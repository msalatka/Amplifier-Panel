"""Read-only SNMP agent, live-value mapping, and warning trap sender."""

import asyncio
import threading
import time
import traceback

from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import config as snmp_config
from pysnmp.entity import engine
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.hlapi.asyncio import *
from pysnmp.proto import rfc1902

from app.core import device_schema, state

OID_BASE_STR = "1.3.6.1.4.1.99999"
TRAP_OID = f"{OID_BASE_STR}.4.1"

# Complete OID-to-Arduino-data-field map.
# .1.1.0        = serial-port connection status
# .2.<n>.0      = amplifier measurements defined by the device schema
STATUS_OID = f"{OID_BASE_STR}.1.1.0"

FIELD_OID_MAP = {
    f"{OID_BASE_STR}.{suffix}": field for suffix, field in device_schema.AMPLIFIER_SNMP_FIELDS
}

FTS_OID_MAP = {
    f"{OID_BASE_STR}.3.1.0": ("profile",),
    f"{OID_BASE_STR}.3.2.0": ("laser", "state"),
    f"{OID_BASE_STR}.3.3.0": ("laser", "optical_frequency"),
    f"{OID_BASE_STR}.3.4.0": ("tec", "temperature_read_c"),
}
for _index in range(0, 8):
    _prefix = f"{OID_BASE_STR}.3.10.{_index}"
    for _suffix, _field in enumerate(
        ("state", "type", "optical_power", "noise_lf", "noise_hf", "jitter"),
        start=1,
    ):
        FTS_OID_MAP[f"{_prefix}.{_suffix}"] = (
            ("uplink", _field) if _index == 0 else ("ports", _index - 1, _field)
        )

# Numeric OID order required for GETNEXT/snmpwalk.
ORDERED_OIDS = [STATUS_OID] + sorted(
    [*FIELD_OID_MAP.keys(), *FTS_OID_MAP.keys()],
    key=lambda oid_str: [int(part) for part in oid_str.split(".")],
)

snmp_thread = None
stop_event = threading.Event()


def _read_value_for_oid(oid_str: str):
    """Return the current OID value as a string, or ``None`` if unsupported."""
    with state.state_lock:
        data = getattr(state, "latest_data", {}) or {}
        connected = getattr(state, "serial_connected", False)
        fts_status = getattr(state, "fts_ls_status", {}) or {}

    if oid_str == STATUS_OID:
        return "CONNECTED" if connected else "DISCONNECTED"

    field = FIELD_OID_MAP.get(oid_str)
    if field is None:
        path = FTS_OID_MAP.get(oid_str)
        if path is None:
            return None
        value = fts_status
        try:
            for part in path:
                value = value[part]
        except (KeyError, IndexError, TypeError):
            return "--"
        return str(value)

    if field not in data:
        return "--"

    return str(data[field])


def _refresh_live_snapshot():
    """Store a complete field-to-value snapshot for the dashboard SNMP view.

    This keeps the web view current without requiring an external SNMP query such
    as ``snmpwalk``.
    """
    snapshot = {}

    with state.state_lock:
        data = getattr(state, "latest_data", {}) or {}
        connected = getattr(state, "serial_connected", False)
        fts_status = getattr(state, "fts_ls_status", {}) or {}

    snapshot["status"] = "CONNECTED" if connected else "DISCONNECTED"

    for field in FIELD_OID_MAP.values():
        snapshot[field] = data.get(field, "--")
    if fts_status:
        snapshot["device_profile"] = fts_status.get("profile", "--")
        snapshot["fts_ls"] = fts_status

    with state.state_lock:
        state.latest_snmp_data = snapshot


class CustomInstrum:
    """Expose application state through the instrumentation API expected by pysnmp."""

    def read_variables(self, *args, **kwargs):
        """Resolve SNMP GET bindings from the latest application state."""

        vars_list = args[0] if len(args) > 0 else []
        results = []

        for varBind in vars_list:
            oid, _old_val = varBind
            oid_str = str(oid)

            value = _read_value_for_oid(oid_str)

            if value is None:
                results.append((oid, rfc1902.NoSuchObject("")))
            else:
                results.append((oid, rfc1902.OctetString(value)))

        # Every GET also refreshes the snapshot displayed by the dashboard.
        _refresh_live_snapshot()

        return results

    def read_next_variables(self, *args, **kwargs):
        """Resolve lexicographically following bindings for GETNEXT and walks."""

        vars_list = args[0] if len(args) > 0 else []
        results = []

        for varBind in vars_list:
            oid, _old_val = varBind
            oid_str = str(oid)

            next_oid_str = None

            for candidate in ORDERED_OIDS:
                candidate_parts = [int(p) for p in candidate.split(".")]
                oid_parts = [int(p) for p in oid_str.split(".")]

                if candidate_parts > oid_parts:
                    next_oid_str = candidate
                    break

            if next_oid_str is None:
                results.append((oid, rfc1902.EndOfMibView()))
                continue

            value = _read_value_for_oid(next_oid_str)
            next_oid = ObjectIdentifier(next_oid_str) if "ObjectIdentifier" in globals() else oid

            results.append((next_oid, rfc1902.OctetString(value if value is not None else "--")))

        _refresh_live_snapshot()

        return results

    def write_variables(self, *args, **kwargs):
        """Reject mutation semantics by returning SET bindings unchanged."""

        # SET is unsupported; return values unchanged.
        vars_list = args[0] if len(args) > 0 else []
        return vars_list


def send_trap(error: dict) -> None:
    """Send one warning as an SNMP trap when trap delivery is enabled."""

    asyncio.run(_async_send_trap(error))


async def _async_send_trap(error: dict):
    with state.state_lock:
        snmp_settings = getattr(state, "snmp_settings", {})
        if not snmp_settings.get("enabled", False):
            return
        community = snmp_settings.get("community", "public")
        trap_host = snmp_settings.get("trap_host", "127.0.0.1")
        trap_port = snmp_settings.get("trap_port", 162)

    error_message = (
        f"ALARM: {error.get('field')} | W: {error.get('value', '--')} "
        f"| T: {error.get('target', '--')}"
    )

    try:
        target = await UdpTransportTarget.create((trap_host, trap_port))
        iterator = send_notification(
            SnmpEngine(),
            CommunityData(community, mpModel=1),
            target,
            ContextData(),
            "trap",
            NotificationType(ObjectIdentity(TRAP_OID)).addVarBinds(
                ("1.3.6.1.2.1.1.3.0", TimeTicks(int(time.time() * 100))),
                ("1.3.6.1.6.3.1.1.4.1.0", ObjectIdentifier(TRAP_OID)),
                (f"{TRAP_OID}.1", OctetString(error_message)),
            ),
        )
        async for errorIndication, _errorStatus, _errorIndex, _varBinds in iterator:
            if errorIndication:
                print(f"[SNMP TRAP FAIL]: {errorIndication}")
    except Exception as e:
        print(f"[SNMP TRAP ERROR]: {e}")


def _snmp_agent_loop():
    with state.state_lock:
        snmp_settings = getattr(state, "snmp_settings", {})
        port = int(snmp_settings.get("port", 1611))
        community = str(snmp_settings.get("community", "public"))

    print(f"\n[SNMP AGENT] Starting on 127.0.0.1:{port} with community '{community}'...")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run_agent():
        """Configure and run the asynchronous pysnmp command responders."""

        agent_refs = {}
        try:
            snmpEngine = engine.SnmpEngine()

            transport = udp.UdpTransport().openServerMode(("0.0.0.0", port))
            snmp_config.addTransport(snmpEngine, udp.domainName + (1,), transport)

            snmp_config.addV1System(snmpEngine, "my-area", community)
            snmp_config.addVacmUser(snmpEngine, 2, "my-area", "noAuthNoPriv", (1, 3, 6))

            snmpContext = context.SnmpContext(snmpEngine)
            snmpContext.unregister_context_name("")
            snmpContext.register_context_name("", CustomInstrum())

            responder = cmdrsp.GetCommandResponder(snmpEngine, snmpContext)
            next_responder = cmdrsp.NextCommandResponder(snmpEngine, snmpContext)

            agent_refs["engine"] = snmpEngine
            agent_refs["transport"] = transport
            agent_refs["context"] = snmpContext
            agent_refs["responder"] = responder
            agent_refs["next_responder"] = next_responder

            print("[SNMP AGENT] Ready; waiting for requests...")

            # Refresh the dashboard snapshot every second, independently of
            # whether an external client queries the agent.
            while not stop_event.is_set():
                _refresh_live_snapshot()
                await asyncio.sleep(1)

        except Exception as e:
            print(f"\n[SNMP AGENT CRITICAL ERROR]: {e}")
            traceback.print_exc()

    loop.run_until_complete(run_agent())
    loop.close()
    print("[SNMP AGENT] Stopped.")


def init_snmp():
    """Start the SNMP agent thread when enabled and not already running."""

    with state.state_lock:
        snmp_settings = getattr(state, "snmp_settings", {})
        if not snmp_settings.get("enabled", False):
            print("[SNMP] Service disabled in settings (enabled=False).")
            return

    global snmp_thread
    stop_event.clear()
    snmp_thread = threading.Thread(target=_snmp_agent_loop, daemon=True)
    snmp_thread.start()


def close_snmp():
    """Request SNMP shutdown and wait briefly for the agent thread."""

    stop_event.set()
    if snmp_thread:
        snmp_thread.join(timeout=2)

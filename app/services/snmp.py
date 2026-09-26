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
from pysnmp.proto import rfc1902, rfc1905

from app.core import config, state

OID_BASE_STR = "1.3.6.1.4.1.99999"
TRAP_OID = f"{OID_BASE_STR}.4.1"

# .1.<n>.0 identifies the connection state of the nth enabled XML device.
# XML fields use their stable mapping ID below the enterprise root, followed by
# the scalar instance suffix .0. For example field 5.1.1.2 becomes
# 1.3.6.1.4.1.99999.5.1.1.2.0.
STATUS_OID = f"{OID_BASE_STR}.1.1.0"

LEGACY_LIVE_ROLES = {
    "input_a": "PiA",
    "output_a": "PoA",
    "input_b": "PiB",
    "output_b": "PoB",
    "gain": "gain_actual",
    "gain_set": "gain_set",
    "temperature": "temperature",
}

snmp_thread = None
stop_event = threading.Event()


def _oid_parts(oid: str) -> tuple[int, ...]:
    """Return a numeric comparison key for one dotted OID."""

    return tuple(int(part) for part in oid.strip(".").split("."))


def _format_value(value: object) -> str:
    """Format JSON values consistently for SNMP OCTET STRING responses."""

    if value is None:
        return "--"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _field_value(snapshot: dict, section_key: str, field: dict) -> object:
    values = snapshot.get("values", {})
    if not isinstance(values, dict):
        return None
    section_values = values.get(section_key, {})
    if not isinstance(section_values, dict):
        return None
    return section_values.get(field.get("key"))


def _live_oid_values() -> dict[str, str]:
    """Build the current OID tree from enabled XML-device snapshots."""

    result = {}
    for index, device_id in enumerate(config.ENABLED_DEVICES, start=1):
        live = state.snapshot_device_live(device_id)
        result[f"{OID_BASE_STR}.1.{index}.0"] = (
            "CONNECTED" if live.get("connected") else "DISCONNECTED"
        )
        snapshot = live.get("data") or {}
        for section in snapshot.get("sections", []):
            if not isinstance(section, dict):
                continue
            section_key = section.get("key")
            if not isinstance(section_key, str):
                continue
            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    continue
                field_id = field.get("id")
                if not isinstance(field_id, str):
                    continue
                try:
                    _oid_parts(field_id)
                except (TypeError, ValueError):
                    continue
                result[f"{OID_BASE_STR}.{field_id}.0"] = _format_value(
                    _field_value(snapshot, section_key, field)
                )
    return result


def _read_value_for_oid(oid_str: str):
    """Return the current OID value as a string, or ``None`` if unsupported."""

    return _live_oid_values().get(oid_str)


def _refresh_live_snapshot():
    """Store a complete field-to-value snapshot for the dashboard SNMP view.

    This keeps the web view current without requiring an external SNMP query such
    as ``snmpwalk``.
    """
    snapshot = {}
    amplifier_snapshots = {}
    for device_id in config.ENABLED_DEVICES:
        live = state.snapshot_device_live(device_id)
        snapshot[f"{device_id}_status"] = "CONNECTED" if live.get("connected") else "DISCONNECTED"
        if device_id in {"oba3", "oba"}:
            amplifier_snapshots[device_id] = live.get("data") or {}
    snapshot["status"] = (
        "CONNECTED"
        if any(value == "CONNECTED" for key, value in snapshot.items() if key.endswith("_status"))
        else "DISCONNECTED"
    )
    # Preserve the existing API keys while sourcing them from the current XML
    # snapshots. OBA3 wins when both amplifier profiles provide the same role.
    for device_id in ("oba3", "oba"):
        amplifier = amplifier_snapshots.get(device_id, {})
        for section in amplifier.get("sections", []):
            if not isinstance(section, dict) or not isinstance(section.get("key"), str):
                continue
            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    continue
                legacy_key = LEGACY_LIVE_ROLES.get(field.get("role"))
                if legacy_key and legacy_key not in snapshot:
                    snapshot[legacy_key] = _format_value(
                        _field_value(amplifier, section["key"], field)
                    )

    with state.state_lock:
        state.latest_snmp_data = snapshot


def _agent_start_message(port: int) -> str:
    """Describe agent startup without including the community secret."""

    return f"[SNMP AGENT] Starting on UDP port {port}..."


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
                results.append((oid, rfc1905.NoSuchObject("")))
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

            values = _live_oid_values()
            try:
                oid_parts = _oid_parts(oid_str)
            except (TypeError, ValueError):
                oid_parts = ()
            next_oid_str = next(
                (
                    candidate
                    for candidate in sorted(values, key=_oid_parts)
                    if _oid_parts(candidate) > oid_parts
                ),
                None,
            )

            if next_oid_str is None:
                results.append((oid, rfc1905.EndOfMibView()))
                continue

            value = values[next_oid_str]
            next_oid = ObjectIdentifier(next_oid_str) if "ObjectIdentifier" in globals() else oid

            results.append((next_oid, rfc1902.OctetString(value if value is not None else "--")))

        _refresh_live_snapshot()

        return results

    def write_variables(self, *args, **kwargs):
        """Reject mutation semantics by returning SET bindings unchanged."""

        # SET is unsupported; return values unchanged.
        vars_list = args[0] if len(args) > 0 else []
        return vars_list


def send_trap(error: dict) -> bool:
    """Send one warning as an SNMP trap when trap delivery is enabled."""

    return asyncio.run(_async_send_trap(error))


async def _async_send_trap(error: dict):
    with state.state_lock:
        snmp_settings = getattr(state, "snmp_settings", {})
        if not snmp_settings.get("enabled", False):
            return False
        community = snmp_settings.get("community", "public")
        trap_host = snmp_settings.get("trap_host", "127.0.0.1")
        trap_port = snmp_settings.get("trap_port", 162)

    error_message = (
        f"ALARM: {error.get('device_id', '--')}:{error.get('field')} "
        f"| W: {error.get('value', '--')} "
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
                return False
        return True
    except Exception as e:
        print(f"[SNMP TRAP ERROR]: {e}")
        return False


def _snmp_agent_loop():
    with state.state_lock:
        snmp_settings = getattr(state, "snmp_settings", {})
        port = int(snmp_settings.get("port", 1611))
        community = str(snmp_settings.get("community", "public"))

    print(f"\n{_agent_start_message(port)}")

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

    global snmp_thread

    with state.state_lock:
        snmp_settings = getattr(state, "snmp_settings", {})
        if not snmp_settings.get("enabled", False):
            print("[SNMP] Service disabled in settings (enabled=False).")
            return
    if snmp_thread is not None and snmp_thread.is_alive():
        return

    stop_event.clear()
    snmp_thread = threading.Thread(target=_snmp_agent_loop, daemon=True)
    snmp_thread.start()


def close_snmp():
    """Request SNMP shutdown and wait briefly for the agent thread."""

    global snmp_thread
    stop_event.set()
    if snmp_thread:
        snmp_thread.join(timeout=2)
        if not snmp_thread.is_alive():
            snmp_thread = None

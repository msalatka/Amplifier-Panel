#!/usr/bin/env python3
import json, pathlib, sqlite3, subprocess, time

STATE_FILE = pathlib.Path("/var/lib/amp-panel/persisted_state.json")
DB_FILE = "/var/lib/amp-panel/measurements.db"
POLL_SECONDS = 10
TRAP_OID = "1.3.6.1.4.1.99999.20.1"
_active_alerts = set()


def load_config():
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return (raw.get("dashboard_settings", {}) or {}), (raw.get("snmp_settings", {}) or {})


def latest_sample():
    connection = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM samples ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    return dict(row) if row else None


def send_trap(host, port, community, message):
    subprocess.run(
        ["snmptrap", "-v2c", "-c", community, f"{host}:{port}", "",
         TRAP_OID, f"{TRAP_OID}.1", "s", message],
        check=False, timeout=5,
    )


def check_field(field, value, limits, host, port, community):
    if value is None:
        return
    bounds = limits.get(field) or {}
    minimum, maximum = bounds.get("min"), bounds.get("max")
    breached = (minimum is not None and value < minimum) or (maximum is not None and value > maximum)
    was_active = field in _active_alerts
    if breached and not was_active:
        _active_alerts.add(field)
        send_trap(host, port, community, f"ACTIVE {field}={value:.2f} outside [{minimum}, {maximum}]")
        print(f"[trap] ACTIVE {field}={value}", flush=True)
    elif not breached and was_active:
        _active_alerts.discard(field)
        send_trap(host, port, community, f"CLEAR {field}={value:.2f} back within [{minimum}, {maximum}]")
        print(f"[trap] CLEAR {field}={value}", flush=True)


def main():
    while True:
        settings, snmp = load_config()
        limits = settings.get("warn_limits", {}) or {}
        host = snmp.get("trap_host", "127.0.0.1")
        port = snmp.get("trap_port", 162)
        community = snmp.get("community", "public")
        sample = latest_sample()
        if sample:
            for field in ("PiA", "PoA", "PiB", "PoB", "temperature"):
                check_field(field, sample.get(field), limits, host, port, community)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

"""Evaluate XML-mapped limits and publish alarm state changes."""

import json
import pathlib
import tempfile
import threading

from app.core import config, state
from app.services import snmp, syslog, xml_status

mapping_write_lock = threading.Lock()


def _key(device_id: str, section: str, field: str, kind: str) -> str:
    return f"{device_id}:{section}:{field}:{kind}"


def evaluate(device_id: str, snapshot: dict, observed_at: str) -> None:
    """Open and clear alarms once as a complete device snapshot changes."""
    current = {}
    for section in snapshot.get("sections", []):
        values = snapshot.get("values", {}).get(section["key"], {})
        for field in section.get("fields", []):
            alarm = field.get("alarm") or {}
            value = values.get(field["key"])
            if not alarm.get("enabled") or not isinstance(value, (int, float)):
                continue
            for kind, boundary in (("minimum", alarm.get("minimum")), ("maximum", alarm.get("maximum"))):
                violated = boundary is not None and (value < boundary if kind == "minimum" else value > boundary)
                if violated:
                    key = _key(device_id, section["key"], field["key"], kind)
                    current[key] = {
                        "key": key,
                        "device_id": device_id,
                        "section": section["key"],
                        "field": field["key"],
                        "label": field.get("label", field["key"]),
                        "unit": field.get("unit", ""),
                        "kind": kind,
                        "value": value,
                        "target": boundary,
                        "message": f"{field.get('label', field['key'])} {'below minimum' if kind == 'minimum' else 'above maximum'} {boundary}",
                    }

    opened, cleared = [], []
    with state.state_lock:
        previous_keys = {key for key, alarm in state.active_alarms.items() if alarm["device_id"] == device_id}
        for key in previous_keys - set(current):
            alarm = state.active_alarms.pop(key)
            cleared.append({**alarm, "event": "CLEARED", "event_time": observed_at})
        for key, alarm in current.items():
            if key in state.active_alarms:
                state.active_alarms[key].update(value=alarm["value"], message=alarm["message"])
            else:
                active = {**alarm, "event": "OPEN", "event_time": observed_at, "opened_at": observed_at}
                state.active_alarms[key] = active
                opened.append(active)
    for alarm in opened:
        syslog.send_warning_event("OPEN", alarm)
        snmp.send_trap(alarm)
    for alarm in cleared:
        syslog.send_warning_event("CLEARED", alarm)


def active(device_id: str) -> list[dict]:
    """Return detached active alarms for one device."""

    with state.state_lock:
        return [dict(alarm) for alarm in state.active_alarms.values() if alarm["device_id"] == device_id]


def update_config(device_id: str, updates: dict[str, dict]) -> dict:
    """Atomically update alarm blocks in the XML mapping."""
    with mapping_write_lock:
        mapping = xml_status.load_mapping()
        fields = {
            f"{section['key']}:{field['key']}": field
            for section in mapping[device_id]["sections"]
            for field in section["fields"]
        }
        unknown = sorted(set(updates) - set(fields))
        if unknown:
            raise ValueError(f"Unknown alarm fields: {', '.join(unknown)}")
        for identifier, alarm in updates.items():
            fields[identifier]["alarm"] = alarm
        xml_status.validate_mapping(mapping)
        path = pathlib.Path(config.XML_MAPPING_FILE).resolve()
        content = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
                output.write(content)
                temporary = pathlib.Path(output.name)
            temporary.chmod(0o640)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return mapping[device_id]

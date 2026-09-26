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
    observed = {}
    for section in snapshot.get("sections", []):
        values = snapshot.get("values", {}).get(section["key"], {})
        for field in section.get("fields", []):
            alarm = field.get("alarm") or {}
            value = values.get(field["key"])
            observed[(section["key"], field["key"])] = value
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
            alarm = state.active_alarms[key]
            if not alarm["condition_active"]:
                continue
            alarm["condition_active"] = False
            alarm["returned_to_normal_at"] = observed_at
            alarm["value"] = observed.get((alarm["section"], alarm["field"]), alarm["value"])
            cleared.append({**alarm, "event": "CLEARED", "event_time": observed_at})
            if alarm["acknowledged"]:
                state.active_alarms.pop(key)
        for key, alarm in current.items():
            if key in state.active_alarms:
                previous = state.active_alarms[key]
                reopened = not previous["condition_active"]
                previous.update(value=alarm["value"], message=alarm["message"], condition_active=True)
                previous.pop("returned_to_normal_at", None)
                if reopened:
                    previous.update(acknowledged=False, opened_at=observed_at, event_time=observed_at)
                    opened.append(dict(previous))
            else:
                active = {
                    **alarm,
                    "event": "OPEN",
                    "event_time": observed_at,
                    "opened_at": observed_at,
                    "condition_active": True,
                    "acknowledged": False,
                }
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


def acknowledge(device_id: str, alarm_key: str) -> dict:
    """Acknowledge one latched alarm and remove it when already normal."""

    with state.state_lock:
        alarm = state.active_alarms.get(alarm_key)
        if alarm is None or alarm["device_id"] != device_id:
            raise ValueError("Alarm no longer exists")
        alarm["acknowledged"] = True
        result = dict(alarm)
        if not alarm["condition_active"]:
            state.active_alarms.pop(alarm_key)
        return result


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
            if not alarm.get("enabled") and not any(
                boundary in alarm for boundary in ("minimum", "maximum")
            ):
                fields[identifier].pop("alarm", None)
            else:
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

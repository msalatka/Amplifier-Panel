"""Read status.xml using editable mappings, without any serial or SNMP operations."""

import datetime
import json
import math
import pathlib
import time
import xml.etree.ElementTree as ET

from app.core import config, state
from app.devices import runtime

MAX_XML_BYTES = 1_000_000


def _automatic_field(param: ET.Element, used_keys: set[str]) -> tuple[dict, float | str] | None:
    """Describe one previously unknown XML parameter without changing the mapping."""
    identifier = (param.get("id") or "").strip()
    name = (param.findtext("name") or "").strip()
    raw = (param.findtext("value") or "").strip()
    if not raw or not (identifier or name):
        return None

    selector = identifier or name
    key = f"auto:{selector}"
    if key in used_keys:
        return None

    try:
        value: float | str = float(raw)
        if not math.isfinite(value):
            return None
        field_type = "number"
    except ValueError:
        value = raw
        field_type = "text"

    label = name or identifier
    used_keys.add(key)
    return (
        {
            "key": key,
            "id": identifier,
            "name": name,
            "label": label,
            "unit": "",
            "group": label,
            "role": "",
            "type": field_type,
            "automatic": True,
        },
        value,
    )


def validate_mapping(mapping: dict) -> dict:
    """Validate and return one complete XML display mapping."""
    if not isinstance(mapping, dict):
        raise ValueError("Mapping root must be a JSON object")
    for device in ("local", "remote", "oba", "oba3"):
        if device not in mapping or not isinstance(mapping[device], dict):
            raise ValueError(f"Missing device mapping: {device}")
        if not isinstance(mapping[device].get("label"), str):
            raise ValueError(f"Invalid device label: {device}")
        sections = mapping[device].get("sections")
        if (
            not isinstance(sections, list)
            or not sections
            or any(not isinstance(section, dict) for section in sections)
            or len({section.get("key") for section in sections}) != len(sections)
        ):
            raise ValueError(f"Invalid sections for {device}")
        for section in sections:
            if not all(
                isinstance(section.get(key), str) and section[key]
                for key in ("key", "xml_section", "label")
            ):
                raise ValueError(f"Invalid section descriptor for {device}")
            fields = section.get("fields")
            if (
                not isinstance(fields, list)
                or not fields
                or any(not isinstance(field, dict) for field in fields)
                or len({field.get("key") for field in fields}) != len(fields)
            ):
                raise ValueError(f"Invalid fields for {device}")
            for field in fields:
                if not isinstance(field.get("key"), str) or not field["key"]:
                    raise ValueError(f"Invalid field key for {device}")
                if not field.get("id") and not field.get("name"):
                    raise ValueError("Each field needs an XML id or name")
                if field.get("type", "number") not in {"number", "text"}:
                    raise ValueError("Field type must be number or text")
                if not isinstance(field.get("writable", False), bool):
                    raise ValueError("Field writable flag must be boolean")
                alarm = field.get("alarm", {"enabled": False})
                if not isinstance(alarm, dict) or not isinstance(alarm.get("enabled", False), bool):
                    raise ValueError("Field alarm must contain a boolean enabled flag")
                for boundary in ("minimum", "maximum"):
                    if boundary in alarm and (
                        isinstance(alarm[boundary], bool)
                        or not isinstance(alarm[boundary], (int, float))
                        or not math.isfinite(alarm[boundary])
                    ):
                        raise ValueError(f"Alarm {boundary} must be finite and numeric")
                if alarm.get("enabled") and not any(key in alarm for key in ("minimum", "maximum")):
                    raise ValueError("Enabled alarm needs a minimum or maximum")
                if "minimum" in alarm and "maximum" in alarm and alarm["minimum"] >= alarm["maximum"]:
                    raise ValueError("Alarm minimum must be lower than maximum")
                for boundary in ("minimum", "maximum"):
                    if boundary in field and (
                        isinstance(field[boundary], bool)
                        or not isinstance(field[boundary], (int, float))
                        or not math.isfinite(field[boundary])
                    ):
                        raise ValueError(f"Field {boundary} must be finite and numeric")
                if (
                    "minimum" in field
                    and "maximum" in field
                    and field["minimum"] > field["maximum"]
                ):
                    raise ValueError("Field minimum must not exceed maximum")
    return mapping


def load_mapping() -> dict:
    """Load display labels and stable field selectors on every poll."""
    mapping = json.loads(pathlib.Path(config.XML_MAPPING_FILE).read_text(encoding="utf-8"))
    return validate_mapping(mapping)


def parse_status(payload: bytes, mapping: dict) -> dict:
    """Keep canonical keys stable when firmware labels change; reject unsafe XML."""
    if len(payload) > MAX_XML_BYTES:
        raise ValueError("XML exceeds 1 MB")
    # Only UTF-8 status documents are supported; reject DTD/entity declarations.
    text = payload.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD and entity declarations are not supported")
    root = ET.fromstring(text)
    if root.tag != "status":
        raise ValueError("Expected <status> root")
    module = root.find("module")
    metadata = (
        {child.tag: child.text for child in module if len(child) == 0} if module is not None else {}
    )
    results = {}
    for device_id, definition in mapping.items():
        values, sections, issues = {}, [], []
        present = False
        for section in definition["sections"]:
            node = root.find(section["xml_section"])
            present |= node is not None
            section_values = {}
            fields = []
            for field in section["fields"]:
                matches = (
                    []
                    if node is None
                    else [
                        p
                        for p in node.findall("param")
                        if (
                            p.get("id") == field["id"]
                            if field.get("id")
                            else p.findtext("name") == field["name"]
                        )
                    ]
                )
                value = None
                if len(matches) == 1:
                    raw = matches[0].findtext("value")
                    try:
                        if raw is None or not raw.strip():
                            raise ValueError("Empty value")
                        field_type = (
                            "boolean"
                            if field["key"] in definition.get("boolean_fields", [])
                            else field.get("type", "number")
                        )
                        if field_type == "text":
                            value = raw
                        elif field_type == "boolean":
                            numeric = float(raw)
                            if numeric not in (0, 1):
                                raise ValueError("Boolean value must be 0 or 1")
                            value = bool(numeric)
                        else:
                            value = float(raw)
                        if isinstance(value, float) and not math.isfinite(value):
                            raise ValueError("Non-finite value")
                    except ValueError:
                        value = None
                        issues.append(f"Invalid value: {section['key']}.{field['key']}")
                elif node is not None:
                    issues.append(f"Missing or duplicate field: {section['key']}.{field['key']}")
                section_values[field["key"]] = value
                fields.append(
                    {
                        "key": field["key"],
                        "id": field.get("id", ""),
                        "name": field.get("name", ""),
                        "label": field.get("label", field["key"]),
                        "unit": field.get("unit", ""),
                        "group": field.get("group", "Measurements"),
                        "role": field.get("role", ""),
                        "type": (
                            "boolean"
                            if field["key"] in definition.get("boolean_fields", [])
                            else field.get("type", "number")
                        ),
                        "writable": field.get("writable", False),
                        "alarm": field.get("alarm", {"enabled": False}),
                        **(
                            {"minimum": field["minimum"]}
                            if "minimum" in field
                            else {}
                        ),
                        **(
                            {"maximum": field["maximum"]}
                            if "maximum" in field
                            else {}
                        ),
                    }
                )
            if node is not None and section.get("discover_unmapped", False):
                mapped_ids = {field.get("id") for field in section["fields"] if field.get("id")}
                mapped_names = {
                    field.get("name") for field in section["fields"] if field.get("name")
                }
                used_keys = set(section_values)
                for param in node.findall("param"):
                    identifier = (param.get("id") or "").strip()
                    name = (param.findtext("name") or "").strip()
                    if identifier in mapped_ids or name in mapped_names:
                        continue
                    automatic = _automatic_field(param, used_keys)
                    if automatic is None:
                        issues.append(
                            f"Invalid automatic field: {section['key']}.{name or identifier}"
                        )
                        continue
                    descriptor, value = automatic
                    fields.append(descriptor)
                    section_values[descriptor["key"]] = value
            values[section["key"]] = section_values
            sections.append(
                {
                    "key": section["key"],
                    "label": section["label"],
                    "present": node is not None,
                    "fields": fields,
                }
            )
        results[device_id] = {
            "values": values,
            "sections": sections,
            "module": metadata,
            "label": definition["label"],
            "present": present,
            "issues": issues,
        }
    return results


def poll_once() -> None:
    """Read one bounded snapshot and isolate missing sections by device."""
    enabled = config.ENABLED_DEVICES
    try:
        mapping = load_mapping()
        path = pathlib.Path(config.XML_STATUS_FILE)
        with path.open("rb") as source:
            modified = path.stat().st_mtime
            payload = source.read(MAX_XML_BYTES + 1)
        if time.time() - modified > config.XML_STALE_SECONDS:
            raise ValueError("XML file is stale: producer has not refreshed it")
        snapshots = parse_status(payload, mapping)
        modified_at = datetime.datetime.fromtimestamp(modified, datetime.timezone.utc).isoformat()
    except (OSError, ValueError, ET.ParseError, KeyError, TypeError, AttributeError) as exc:
        for key in enabled:
            runtime.report_failure(key, f"XML: {exc}")
        return
    for key in enabled:
        snapshot = snapshots[key]
        if not snapshot["present"]:
            state.update_device_live(key, data=snapshot)
            runtime.report_failure(key, "No matching section in status.xml")
        else:
            from app.services import alarms

            alarms.evaluate(key, snapshot, modified_at)
            previous = state.snapshot_device_live(key)
            if previous.get("data", {}).get("values") != snapshot["values"]:
                runtime.publish_snapshot(key, snapshot, timestamp=modified_at)
            else:
                # A fresh, valid XML file confirms that this section is still
                # available, but unchanged measurements are not new history.
                state.update_device_live(
                    key,
                    connected=True,
                    error=None,
                    last_update=modified_at,
                    data=snapshot,
                )
            if snapshot["issues"]:
                state.update_device_live(key, error="; ".join(snapshot["issues"]))


def xml_reader_loop() -> None:
    """Poll a daemon-owned file; failed reads recover on the following poll."""
    while not state.stop_event.is_set():
        poll_once()
        state.stop_event.wait(config.XML_POLL_SECONDS)

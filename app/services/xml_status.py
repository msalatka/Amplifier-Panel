"""Read status.xml using editable mappings, without any serial or SNMP operations."""

import datetime
import json
import math
import pathlib
import time
import xml.etree.ElementTree as ET

from app.core import config, state
from app.devices import runtime
from app.devices.registry import definition_dict

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
    seen_profile_keys = set()
    seen_xml_sections = set()
    seen_snmp_indexes = set()
    for device in mapping:
        if not isinstance(device, str) or not device or not isinstance(mapping[device], dict):
            raise ValueError("Each mapping entry needs a non-empty device key")
        if not isinstance(mapping[device].get("label"), str):
            raise ValueError(f"Invalid device label: {device}")
        for key in ("profile_label", "display_group"):
            if key in mapping[device] and not isinstance(mapping[device][key], str):
                raise ValueError(f"Invalid {key}: {device}")
        if mapping[device].get("view_profile", "station") not in {"station", "amplifier"}:
            raise ValueError(f"Invalid view_profile: {device}")
        sections = mapping[device].get("sections")
        if (
            not isinstance(sections, list)
            or not sections
            or any(not isinstance(section, dict) for section in sections)
            or len({section.get("key") for section in sections}) != len(sections)
        ):
            raise ValueError(f"Invalid sections for {device}")
        if "order" in mapping[device] and (
            isinstance(mapping[device]["order"], bool)
            or not isinstance(mapping[device]["order"], int)
        ):
            raise ValueError(f"Invalid profile order: {device}")
        if "snmp_index" in mapping[device]:
            index = mapping[device]["snmp_index"]
            if isinstance(index, bool) or not isinstance(index, int) or index < 1:
                raise ValueError(f"Invalid SNMP index: {device}")
            if len(sections) != 1:
                raise ValueError(f"Top-level SNMP index requires one section: {device}")
        for section in sections:
            if not all(
                isinstance(section.get(key), str) and section[key]
                for key in ("key", "xml_section", "label")
            ):
                raise ValueError(f"Invalid section descriptor for {device}")
            if section["key"] in seen_profile_keys:
                raise ValueError(f"Duplicate profile key: {section['key']}")
            if section["xml_section"] in seen_xml_sections:
                raise ValueError(f"Duplicate XML section: {section['xml_section']}")
            seen_profile_keys.add(section["key"])
            seen_xml_sections.add(section["xml_section"])
            for key in ("profile_label", "display_group"):
                if key in section and not isinstance(section[key], str):
                    raise ValueError(f"Invalid section {key}: {section['key']}")
            if section.get("view_profile", "station") not in {"station", "amplifier"}:
                raise ValueError(f"Invalid section view_profile: {section['key']}")
            if "order" in section and (
                isinstance(section["order"], bool) or not isinstance(section["order"], int)
            ):
                raise ValueError(f"Invalid profile order: {section['key']}")
            index = section.get("snmp_index", mapping[device].get("snmp_index"))
            if index is None:
                index = definition_dict(section["key"])["snmp_index"]
            if index is not None:
                if isinstance(index, bool) or not isinstance(index, int) or index < 1:
                    raise ValueError(f"Invalid SNMP index: {section['key']}")
                if index in seen_snmp_indexes:
                    raise ValueError(f"Duplicate SNMP index: {index}")
                seen_snmp_indexes.add(index)
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
                if (
                    "minimum" in alarm
                    and "maximum" in alarm
                    and alarm["minimum"] >= alarm["maximum"]
                ):
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


def find_mapping_profile(mapping: dict, profile_id: str) -> tuple[str, dict, dict] | None:
    """Locate the owning mapping entry and section for one runtime profile."""

    for owner_id, definition in mapping.items():
        for section in definition["sections"]:
            if section["key"] == profile_id:
                return owner_id, definition, section
    return None


def _profile_definition(profile_id: str, definition: dict | None, section: dict) -> dict:
    """Combine stable defaults with optional mapping presentation metadata."""

    source = definition or {}
    single_profile_label = source.get("label") if len(source.get("sections", [])) == 1 else None

    def metadata(name: str):
        return section[name] if name in section else source.get(name)

    return definition_dict(
        profile_id,
        label=section.get("profile_label") or source.get("profile_label") or single_profile_label,
        view_profile=section.get("view_profile") or source.get("view_profile"),
        display_group=section.get("display_group") or source.get("display_group"),
        order=metadata("order"),
        snmp_index=metadata("snmp_index"),
    )


def _parse_field_value(raw: str | None, field_type: str) -> float | str | bool:
    """Parse one mapped XML value according to its effective type."""

    if raw is None or not raw.strip():
        raise ValueError("Empty value")
    if field_type == "text":
        return raw
    numeric = float(raw)
    if not math.isfinite(numeric):
        raise ValueError("Non-finite value")
    if field_type == "boolean":
        if numeric not in (0, 1):
            raise ValueError("Boolean value must be 0 or 1")
        return bool(numeric)
    return numeric


def parse_status(payload: bytes, mapping: dict) -> dict:
    """Create one runtime profile for every ``params_*`` section in the XML."""
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
    mapped_sections = {
        section["xml_section"]: (definition, section)
        for definition in mapping.values()
        for section in definition["sections"]
    }
    results = {}
    for node in root:
        if not isinstance(node.tag, str) or not node.tag.startswith("params_"):
            continue
        mapped = mapped_sections.get(node.tag)
        definition, section = mapped if mapped else (None, None)
        profile_id = section["key"] if section else node.tag.removeprefix("params_")
        if not profile_id or profile_id in results:
            raise ValueError(f"Duplicate or invalid XML profile: {profile_id or node.tag}")
        if section is None:
            section = {
                "key": profile_id,
                "xml_section": node.tag,
                "label": profile_id.replace("_", " ").title(),
                "fields": [],
                "discover_unmapped": True,
            }
        boolean_fields = set((definition or {}).get("boolean_fields", []))
        section_values, fields, issues = {}, [], []
        for field in section["fields"]:
            matches = [
                parameter
                for parameter in node.findall("param")
                if (
                    parameter.get("id") == field["id"]
                    if field.get("id")
                    else parameter.findtext("name") == field["name"]
                )
            ]
            field_type = (
                "boolean" if field["key"] in boolean_fields else field.get("type", "number")
            )
            value = None
            if len(matches) == 1:
                try:
                    value = _parse_field_value(matches[0].findtext("value"), field_type)
                except ValueError:
                    issues.append(f"Invalid value: {profile_id}.{field['key']}")
            else:
                issues.append(f"Missing or duplicate field: {profile_id}.{field['key']}")
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
                    "type": field_type,
                    "writable": field.get("writable", False),
                    "alarm": field.get("alarm", {"enabled": False}),
                    **({"minimum": field["minimum"]} if "minimum" in field else {}),
                    **({"maximum": field["maximum"]} if "maximum" in field else {}),
                }
            )
        if section.get("discover_unmapped", False) or mapped is None:
            mapped_ids = {field.get("id") for field in section["fields"] if field.get("id")}
            mapped_names = {field.get("name") for field in section["fields"] if field.get("name")}
            used_keys = set(section_values)
            for parameter in node.findall("param"):
                identifier = (parameter.get("id") or "").strip()
                name = (parameter.findtext("name") or "").strip()
                if identifier in mapped_ids or name in mapped_names:
                    continue
                automatic = _automatic_field(parameter, used_keys)
                if automatic is None:
                    issues.append(f"Invalid automatic field: {profile_id}.{name or identifier}")
                    continue
                descriptor, value = automatic
                fields.append(descriptor)
                section_values[descriptor["key"]] = value
        profile = _profile_definition(profile_id, definition, section)
        results[profile_id] = {
            "values": {profile_id: section_values},
            "sections": [
                {"key": profile_id, "label": section["label"], "present": True, "fields": fields}
            ],
            "module": metadata,
            "label": profile["label"],
            "present": True,
            "issues": issues,
            "profile": profile,
        }
    return results


def poll_once() -> None:
    """Read one bounded snapshot and isolate missing sections by device."""
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
        for key in state.active_device_ids():
            runtime.report_failure(key, f"XML: {exc}")
        return
    state.set_device_inventory([snapshot["profile"] for snapshot in snapshots.values()])
    from app.services import control_requests

    control_requests.reconcile()
    for key, snapshot in snapshots.items():
        from app.services import alarms

        alarms.evaluate(key, snapshot, modified_at)
        previous = state.snapshot_device_live(key)
        if previous.get("data", {}).get("values") != snapshot["values"]:
            runtime.publish_snapshot(key, snapshot, timestamp=modified_at)
        else:
            state.update_device_live(
                key, connected=True, error=None, last_update=modified_at, data=snapshot
            )
        if snapshot["issues"]:
            state.update_device_live(key, error="; ".join(snapshot["issues"]))


def xml_reader_loop() -> None:
    """Poll a daemon-owned file; failed reads recover on the following poll."""
    while not state.stop_event.is_set():
        poll_once()
        state.stop_event.wait(config.XML_POLL_SECONDS)

"""Read status.xml using editable mappings, without any serial or SNMP operations."""

import json
import math
import pathlib
import time
import xml.etree.ElementTree as ET

from app.core import config, state
from app.devices import runtime

MAX_XML_BYTES = 1_000_000


def load_mapping() -> dict:
    """Load display labels and stable field selectors on every poll."""
    mapping = json.loads(pathlib.Path(config.XML_MAPPING_FILE).read_text(encoding="utf-8"))
    for device in ("local", "remote", "oba", "oba3"):
        sections = mapping[device]["sections"]
        if not sections or len({s["key"] for s in sections}) != len(sections):
            raise ValueError(f"Invalid sections for {device}")
        for section in sections:
            fields = section["fields"]
            if not fields or len({f["key"] for f in fields}) != len(fields):
                raise ValueError(f"Invalid fields for {device}")
            for field in fields:
                if not field.get("id") and not field.get("name"):
                    raise ValueError("Each field needs an XML id or name")
                if field.get("type", "number") not in {"number", "text"}:
                    raise ValueError("Field type must be number or text")
    return mapping


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
                        value = raw if field.get("type") == "text" else float(raw)
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
                        "label": field.get("label", field["key"]),
                        "unit": field.get("unit", ""),
                        "group": field.get("group", "Measurements"),
                        "role": field.get("role", ""),
                        "type": field.get("type", "number"),
                    }
                )
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
            runtime.publish_snapshot(key, snapshot)
            if snapshot["issues"]:
                state.update_device_live(key, error="; ".join(snapshot["issues"]))


def xml_reader_loop() -> None:
    """Poll a daemon-owned file; failed reads recover on the following poll."""
    while not state.stop_event.is_set():
        poll_once()
        state.stop_event.wait(config.XML_POLL_SECONDS)

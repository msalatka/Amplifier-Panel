"""Validated, atomic device-control XML exchange."""

import datetime
import math
import os
import pathlib
import threading
import uuid
import xml.etree.ElementTree as ET

from app.core import config
from app.services import xml_status

MAX_CONTROL_BYTES = 256_000
CONTROL_STATES = {"pending", "applied", "rejected", "failed"}
write_lock = threading.Lock()


def _writable_fields(device_id: str) -> dict[str, tuple[dict, dict]]:
    """Return writable fields indexed by stable ``section:key`` identifiers."""

    mapping = xml_status.load_mapping()
    if device_id not in mapping:
        raise ValueError(f"Unknown device: {device_id}")
    result = {}
    for section in mapping[device_id]["sections"]:
        for field in section["fields"]:
            if field.get("writable", False):
                result[f"{section['key']}:{field['key']}"] = (section, field)
    return result


def _validated_value(field: dict, value: object) -> tuple[str, str]:
    """Validate and serialize one value according to its mapping descriptor."""

    field_type = field.get("type", "number")
    if field_type == "number":
        if isinstance(value, bool):
            raise ValueError("Boolean is not a numeric control value")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Control value must be numeric") from exc
        if not math.isfinite(number):
            raise ValueError("Control value must be finite")
        minimum = field.get("minimum")
        maximum = field.get("maximum")
        if minimum is not None and number < minimum:
            raise ValueError(f"Control value must be at least {minimum}")
        if maximum is not None and number > maximum:
            raise ValueError(f"Control value must be at most {maximum}")
        return "number", format(number, ".15g")
    if not isinstance(value, str):
        raise ValueError("Control value must be text")
    if len(value) > 1024 or any(ord(character) < 32 for character in value):
        raise ValueError("Control text is invalid or too long")
    return "text", value


def build_control_xml(
    device_id: str,
    values: dict[str, object],
    *,
    request_id: str | None = None,
    created_at: str | None = None,
) -> tuple[str, bytes]:
    """Build one validated control document and return its request identifier."""

    if not isinstance(values, dict) or not values:
        raise ValueError("At least one control value is required")
    writable = _writable_fields(device_id)
    unknown = sorted(set(values) - set(writable))
    if unknown:
        raise ValueError(f"Fields are unknown or read-only: {', '.join(unknown)}")
    identifier = request_id or str(uuid.uuid4())
    try:
        uuid.UUID(identifier)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid request identifier") from exc
    timestamp = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    root = ET.Element("control", {"version": "1"})
    request = ET.SubElement(root, "request", {"id": identifier, "created_at": timestamp})
    device = ET.SubElement(request, "device", {"id": device_id})
    for field_key, value in values.items():
        section, field = writable[field_key]
        value_type, serialized = _validated_value(field, value)
        attributes = {
            "section": section["key"],
            "key": field["key"],
            "type": value_type,
        }
        if field.get("id"):
            attributes["id"] = field["id"]
        if field.get("name"):
            attributes["name"] = field["name"]
        parameter = ET.SubElement(device, "parameter", attributes)
        ET.SubElement(parameter, "value").text = serialized
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
    if len(payload) > MAX_CONTROL_BYTES:
        raise ValueError("Control XML exceeds 256 KB")
    return identifier, payload


def write_control(device_id: str, values: dict[str, object]) -> dict:
    """Validate and atomically replace the device-facing control document."""

    request_id, payload = build_control_xml(device_id, values)
    path = pathlib.Path(config.XML_CONTROL_FILE)
    temporary = path.with_name(f".{path.name}.{request_id}.tmp")
    with write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o660)
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    return {
        "request_id": request_id,
        "state": "pending",
        "control_file": str(path),
    }


def read_acknowledgement() -> dict:
    """Read the device acknowledgement embedded in the status document."""

    path = pathlib.Path(config.XML_STATUS_FILE)
    try:
        payload = path.read_bytes()
    except OSError:
        return {"request_id": None, "state": "unavailable", "message": "status.xml unavailable"}
    if len(payload) > xml_status.MAX_XML_BYTES:
        return {"request_id": None, "state": "invalid", "message": "status.xml is too large"}
    try:
        text = payload.decode("utf-8-sig")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("unsafe XML")
        root = ET.fromstring(text)
    except (UnicodeDecodeError, ET.ParseError, ValueError) as exc:
        return {"request_id": None, "state": "invalid", "message": str(exc)}
    acknowledgement = root.find("control_status")
    if acknowledgement is None:
        return {"request_id": None, "state": "unsupported", "message": "no acknowledgement"}
    state = (acknowledgement.findtext("state") or "").strip().lower()
    return {
        "request_id": (acknowledgement.findtext("last_request_id") or "").strip() or None,
        "state": state if state in CONTROL_STATES else "invalid",
        "message": (acknowledgement.findtext("message") or "").strip(),
    }


def get_control_status() -> dict:
    """Combine the current request with its acknowledgement and timeout state."""

    acknowledgement = read_acknowledgement()
    path = pathlib.Path(config.XML_CONTROL_FILE)
    try:
        payload = path.read_bytes()
        if len(payload) > MAX_CONTROL_BYTES:
            raise ValueError("control.xml is too large")
        root = ET.fromstring(payload)
        request = root.find("request")
    except (OSError, ET.ParseError, ValueError) as exc:
        return {"request_id": None, "state": "unavailable", "message": str(exc)}
    if request is None:
        return {"request_id": None, "state": "idle", "message": "no control request"}
    request_id = request.get("id")
    if request_id and acknowledgement.get("request_id") == request_id:
        return acknowledgement
    try:
        created = datetime.datetime.fromisoformat(
            (request.get("created_at") or "").replace("Z", "+00:00")
        )
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
    except ValueError:
        return {"request_id": request_id, "state": "invalid", "message": "invalid timestamp"}
    timed_out = age > config.XML_CONTROL_ACK_TIMEOUT_SECONDS
    return {
        "request_id": request_id,
        "state": "timeout" if timed_out else "pending",
        "message": "device acknowledgement timed out" if timed_out else "awaiting acknowledgement",
    }

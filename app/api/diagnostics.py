"""Administrative diagnostics and host-integration HTTP endpoints."""

import asyncio
import datetime
import json
import os
import pathlib
import tempfile
import threading

import fastapi
import fastapi.responses
import pydantic
import starlette.requests

from app.api import security as api_security
from app.core import config, state
from app.services import database as database_service
from app.services import network as network_service
from app.services import ntp as ntp_service
from app.services import snmp as snmp_service
from app.services import syslog as syslog_service
from app.services import xml_control, xml_status

router = fastapi.APIRouter()
heartbeat_settings_changed = asyncio.Event()
xml_mapping_write_lock = threading.Lock()


class NetworkSettingsRequest(pydantic.BaseModel):
    """NetworkManager settings submitted for a guarded network change."""

    interface: str
    mode: str
    ip_address: str = ""
    netmask: str = ""
    gateway: str = ""
    dns: str = ""


class NetworkConfirmationRequest(pydantic.BaseModel):
    """Token confirming that a network change kept the panel reachable."""

    token: str


class ServiceSettingsRequest(pydantic.BaseModel):
    """Runtime service settings editable from the diagnostics page."""

    syslog_heartbeat_seconds: int
    database_max_records: int
    device_id: str = config.ENABLED_DEVICES[0]


class SnmpSettingsUpdateRequest(pydantic.BaseModel):
    """SNMP agent and trap destination settings."""

    enabled: bool
    port: int
    community: str
    trap_host: str
    trap_port: int


class XmlMappingUpdateRequest(pydantic.BaseModel):
    """Complete JSON mapping submitted by an administrator."""

    content: str


class XmlMappingFieldRequest(pydantic.BaseModel):
    """Automatically discovered field that should become a persistent mapping entry."""

    device_id: str
    section: str
    key: str


def _write_xml_mapping(mapping: dict) -> tuple[pathlib.Path, str]:
    """Validate and atomically write a complete mapping document."""

    xml_status.validate_mapping(mapping)
    path = pathlib.Path(config.XML_MAPPING_FILE).resolve()
    content = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
    temporary_path: pathlib.Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = pathlib.Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return path, content


@router.get("/api/xml-mapping")
def get_xml_mapping(
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Return the editable XML mapping and its configured disk location."""

    path = pathlib.Path(config.XML_MAPPING_FILE).resolve()
    try:
        content = path.read_text(encoding="utf-8")
        xml_status.validate_mapping(json.loads(content))
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise fastapi.HTTPException(
            status_code=500, detail=f"Could not read mapping: {exc}"
        ) from exc
    return {"path": str(path), "content": content}


@router.put("/api/xml-mapping")
def update_xml_mapping(
    payload: XmlMappingUpdateRequest,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Validate and atomically replace the complete XML mapping file."""

    try:
        mapping = json.loads(payload.content)
        xml_status.validate_mapping(mapping)
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise fastapi.HTTPException(status_code=400, detail=f"Invalid mapping: {exc}") from exc

    try:
        with xml_mapping_write_lock:
            path, content = _write_xml_mapping(mapping)
    except OSError as exc:
        raise fastapi.HTTPException(
            status_code=500, detail=f"Could not save mapping: {exc}"
        ) from exc

    api_security.audit_event(
        request,
        "xml_mapping_updated",
        current_user["username"],
        f"path={path}",
    )
    return {"status": "ok", "path": str(path), "content": content}


@router.post("/api/xml-mapping/fields")
def add_xml_mapping_field(
    payload: XmlMappingFieldRequest,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Persist one currently visible automatically discovered XML field."""

    if payload.device_id not in config.ENABLED_DEVICES:
        raise fastapi.HTTPException(status_code=404, detail="Device is not enabled")
    live = state.snapshot_device_live(payload.device_id).get("data", {})
    live_section = next(
        (section for section in live.get("sections", []) if section.get("key") == payload.section),
        None,
    )
    live_field = next(
        (
            field
            for field in (live_section or {}).get("fields", [])
            if field.get("key") == payload.key and field.get("automatic") is True
        ),
        None,
    )
    if live_field is None:
        raise fastapi.HTTPException(
            status_code=409, detail="The field is no longer available as an unmapped variable"
        )

    try:
        with xml_mapping_write_lock:
            mapping = xml_status.load_mapping()
            mapping_section = next(
                (
                    section
                    for section in mapping[payload.device_id]["sections"]
                    if section["key"] == payload.section
                ),
                None,
            )
            if mapping_section is None:
                raise fastapi.HTTPException(status_code=404, detail="Mapping section not found")
            if any(field["key"] == payload.key for field in mapping_section["fields"]):
                raise fastapi.HTTPException(status_code=409, detail="Variable is already mapped")
            field = {
                "key": payload.key,
                "id": live_field.get("id", ""),
                "name": live_field.get("name", ""),
                "label": live_field.get("label", payload.key),
                "type": live_field.get("type", "number"),
                "unit": live_field.get("unit", ""),
                "group": live_field.get("label", payload.key),
                "role": "",
            }
            mapping_section["fields"].append(field)
            path, content = _write_xml_mapping(mapping)
    except fastapi.HTTPException:
        raise
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise fastapi.HTTPException(
            status_code=500, detail=f"Could not add variable to mapping: {exc}"
        ) from exc

    api_security.audit_event(
        request,
        "xml_mapping_field_added",
        current_user["username"],
        f"device={payload.device_id}; section={payload.section}; key={payload.key}; path={path}",
    )
    return {"status": "ok", "path": str(path), "content": content, "field": field}


@router.get("/api/service-diagnostics")
def service_diagnostics(
    device: str = config.ENABLED_DEVICES[0],
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Return acquisition, storage, syslog, and service runtime diagnostics."""

    if device not in config.ENABLED_DEVICES:
        raise fastapi.HTTPException(status_code=404, detail="Device is not enabled")
    with state.state_lock:
        settings = state.service_settings.copy()
    storage = database_service.get_storage_status(device)
    live = state.snapshot_device_live(device)
    return {
        "acquisition": {
            "source": "status.xml",
            "file": config.XML_STATUS_FILE,
            "poll_seconds": config.XML_POLL_SECONDS,
            "connected": live["connected"],
            "error": live["error"],
        },
        "xml_control": {
            "file": config.XML_CONTROL_FILE,
            "ack_timeout_seconds": config.XML_CONTROL_ACK_TIMEOUT_SECONDS,
            "acknowledgement": xml_control.get_control_status(),
        },
        "database": {
            **database_service.get_runtime_status(device),
            "file": config.DATABASE_FILE,
            "record_limit": settings["database_max_records"],
            "size_bytes": storage["size_bytes"],
            "filesystem_free_bytes": storage["free_bytes"],
            "discarded_records_since_start": storage["discarded_records_since_start"],
            "sample_rate_per_second": storage["sample_rate_per_second"],
            "estimated_retention_seconds": storage["estimated_retention_seconds"],
            "estimated_seconds_to_limit": storage["estimated_seconds_to_limit"],
            "estimated_seconds_until_disk_full": storage["estimated_seconds_until_disk_full"],
        },
        "syslog": {
            "local_enabled": config.SYSLOG_ENABLED,
            "local_destination": f"{config.SYSLOG_HOST}:{config.SYSLOG_PORT}",
            "remote_enabled": config.REMOTE_SYSLOG_ENABLED,
            "remote_host": config.REMOTE_SYSLOG_HOST,
            "remote_port": config.REMOTE_SYSLOG_PORT,
            "remote_protocol": config.REMOTE_SYSLOG_PROTOCOL,
            "local_file": config.SYSLOG_EXPORT_FILE,
            "heartbeat_seconds": settings["syslog_heartbeat_seconds"],
        },
    }


@router.put("/api/service-diagnostics/settings")
async def update_service_diagnostics_settings(
    request: ServiceSettingsRequest,
    http_request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Validate and apply editable service diagnostics settings."""

    if request.syslog_heartbeat_seconds != 0 and request.syslog_heartbeat_seconds < 10:
        raise fastapi.HTTPException(
            status_code=400, detail="Heartbeat must be 0 or at least 10 seconds"
        )
    if request.syslog_heartbeat_seconds > 86400:
        raise fastapi.HTTPException(status_code=400, detail="Heartbeat cannot exceed 86400 seconds")
    if not 0 <= request.database_max_records <= 10000000:
        raise fastapi.HTTPException(
            status_code=400,
            detail="Database limit must be 0 (unlimited) or between 1 and 10000000 records",
        )
    if request.device_id not in config.ENABLED_DEVICES:
        raise fastapi.HTTPException(status_code=404, detail="Device is not enabled")
    with state.state_lock:
        before = state.service_settings.copy()
        state.service_settings.update(
            {
                "syslog_heartbeat_seconds": request.syslog_heartbeat_seconds,
                "database_max_records": request.database_max_records,
            }
        )
        state.save_persisted_state()
        after = state.service_settings.copy()
    removed_records = database_service.apply_record_limit()
    heartbeat_settings_changed.set()
    api_security.audit_event(
        http_request,
        "service_settings_updated",
        current_user["username"],
        api_security.audit_changes(before, after) + f"; pruned_records={removed_records}",
    )
    return {"status": "ok", "settings": after, "pruned_records": removed_records}


@router.get("/api/network")
def get_network_settings(
    request: starlette.requests.Request,
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Return network state from the restricted host network agent."""

    try:
        return network_service.get_network_state(api_security.get_client_ip(request))
    except network_service.NetworkError as exc:
        raise fastapi.HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/api/network")
def update_network_settings(
    settings: NetworkSettingsRequest,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Apply a guarded network change and return its confirmation token."""

    client_ip = api_security.get_client_ip(request)
    try:
        before = network_service.get_network_state(client_ip)
    except network_service.NetworkError:
        before = {}
    try:
        payload = settings.model_dump()
        payload["_requester_ip"] = client_ip
        result = network_service.apply_network_settings(payload)
    except network_service.NetworkError as exc:
        raise fastapi.HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    api_security.audit_event(
        request,
        "network_settings_pending_confirmation",
        current_user["username"],
        api_security.audit_changes(before, settings.model_dump()),
    )
    return fastapi.responses.JSONResponse(
        content=result,
        headers={"Connection": "close"},
    )


@router.post("/api/network/confirm")
def confirm_network_settings(
    confirmation: NetworkConfirmationRequest,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Confirm a pending network change before its rollback deadline."""

    try:
        result = network_service.confirm_network_settings(
            confirmation.token,
            api_security.get_client_ip(request),
        )
    except network_service.NetworkError as exc:
        raise fastapi.HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    api_security.audit_event(
        request,
        "network_settings_confirmed",
        current_user["username"],
    )
    return result


@router.get("/api/ntp/status")
def get_ntp_status(
    force: bool = False,
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Return cached or freshly queried NTP synchronization diagnostics."""

    return ntp_service.query_ntp_status(force=force)


@router.get("/api/syslog/export.log")
def export_syslog_log(
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Audit and download the locally exported application log."""

    api_security.audit_event(request, "syslog_exported", current_user["username"])
    path = syslog_service.get_syslog_log_path()
    filename = f"amp_syslog_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    if not path.exists():
        return fastapi.responses.Response(
            content="",
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return fastapi.responses.FileResponse(path, media_type="text/plain", filename=filename)


@router.get("/api/snmp/live_data")
def get_snmp_live_data(
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return the values currently exposed by the SNMP agent."""

    with state.state_lock:
        return dict(state.latest_snmp_data)


@router.get("/api/snmp/settings")
def get_snmp_settings(
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Return persisted SNMP agent and trap settings."""

    with state.state_lock:
        return dict(state.snmp_settings)


@router.post("/api/snmp/settings")
def update_snmp_settings(
    settings: SnmpSettingsUpdateRequest,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Validate, persist, audit, and activate SNMP settings."""

    if settings.port != config.SNMP_PORT:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f"SNMP port is fixed by server configuration to {config.SNMP_PORT}",
        )
    if len(settings.community.strip()) < 12:
        raise fastapi.HTTPException(
            status_code=400, detail="SNMP community must contain at least 12 characters"
        )
    with state.state_lock:
        before = dict(state.snmp_settings)
        state.snmp_settings = settings.model_dump()
        state.save_persisted_state()
    snmp_service.close_snmp()
    if settings.enabled:
        snmp_service.init_snmp()
    api_security.audit_event(
        request,
        "snmp_settings_updated",
        current_user["username"],
        api_security.audit_changes(before, state.snmp_settings, redacted={"community"}),
    )
    return state.snmp_settings


@router.post("/api/snmp/test-trap")
def send_test_snmp_trap(
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Send an explicit test notification to the configured trap receiver."""

    if not snmp_service.send_trap(
        {"field": "test", "label": "Amp Panel test", "value": "TEST", "target": "configured receiver"}
    ):
        detail = snmp_service.last_trap_error or "SNMP trap could not be sent"
        raise fastapi.HTTPException(status_code=503, detail=detail)
    api_security.audit_event(request, "snmp_test_trap_sent", current_user["username"], "")
    return {"sent": True}

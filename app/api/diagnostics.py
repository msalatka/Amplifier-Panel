"""Administrative diagnostics and host-integration HTTP endpoints."""

import asyncio
import datetime
import re

import fastapi
import fastapi.responses
import pydantic
import starlette.requests

from app.api import security as api_security
from app.core import config, state
from app.services import database as database_service
from app.services import network as network_service
from app.services import ntp as ntp_service
from app.services import serial as serial_reader
from app.services import snmp as snmp_service
from app.services import syslog as syslog_service

router = fastapi.APIRouter()
heartbeat_settings_changed = asyncio.Event()


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
    serial_port: str


class SnmpSettingsUpdateRequest(pydantic.BaseModel):
    """SNMP agent and trap destination settings."""

    enabled: bool
    port: int
    community: str
    trap_host: str
    trap_port: int


@router.get("/api/service-diagnostics")
def service_diagnostics(
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Return serial, storage, syslog, and service runtime diagnostics."""

    with state.state_lock:
        settings = state.service_settings.copy()
    storage = database_service.get_storage_status()
    return {
        "serial": {
            "port": settings["serial_port"],
            "available_ports": serial_reader.available_serial_ports(),
            "baudrate": config.SERIAL_BAUDRATE,
            "connected": state.serial_connected,
            "error": state.serial_error,
        },
        "database": {
            **database_service.get_runtime_status(),
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
    serial_port = request.serial_port.strip()
    if not re.fullmatch(r"/dev/tty(?:ACM|USB)[0-9]+", serial_port):
        raise fastapi.HTTPException(status_code=400, detail="Select an available USB serial port")
    if serial_port not in serial_reader.available_serial_ports():
        raise fastapi.HTTPException(
            status_code=400, detail="Selected serial port is not currently available"
        )

    with state.state_lock:
        before = state.service_settings.copy()
        state.service_settings.update({**request.model_dump(), "serial_port": serial_port})
        state.save_persisted_state()
        after = state.service_settings.copy()
    removed_records = database_service.apply_record_limit()
    if before["serial_port"] != serial_port:
        serial_reader.reconnect(serial_port)
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

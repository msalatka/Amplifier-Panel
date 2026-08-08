"""Live amplifier data, settings, warnings, and gain-control endpoints."""

import datetime
import json

import fastapi
import pydantic
import starlette.requests

from app.api import security as api_security
from app.core import config, state, validation
from app.services import database as database_service
from app.services import serial as serial_reader
from app.services import syslog as syslog_service

router = fastapi.APIRouter()


class GainSetRequest(pydantic.BaseModel):
    """Requested optical-amplifier gain setpoint."""

    gain_set: float


class DashboardSettingsRequest(pydantic.BaseModel):
    """Editable warning thresholds and gain-tolerance settings."""

    gain_tolerance: float | None = None
    warn_limits: dict[str, dict[str, float | None]] | None = None


@router.get("/api/latest")
def latest(
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return the latest device state and database health summary."""

    database_status = database_service.get_runtime_status()
    system_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with state.state_lock:
        return {
            "device_profile": config.DEVICE_PROFILE,
            "connected": state.serial_connected,
            "error": state.serial_error,
            "last_update": state.last_update,
            "system_time": system_time,
            "last_known_gain_set": state.last_known_gain_set,
            "data": state.latest_data,
            "fts_ls": state.fts_ls_status if config.DEVICE_PROFILE == "fts-ls" else None,
            "database": database_status,
        }


@router.get("/api/settings")
def get_settings(
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return the active dashboard settings and configured gain bounds."""

    with state.state_lock:
        return {
            **json.loads(json.dumps(state.dashboard_settings)),
            "gain_set_limits": {
                "min": config.GAIN_SET_MIN,
                "max": config.GAIN_SET_MAX,
            },
        }


@router.post("/api/settings")
def update_settings(
    request: DashboardSettingsRequest,
    http_request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator", "Operator")),
):
    """Validate, persist, and audit editable dashboard settings."""

    with state.state_lock:
        before = json.loads(json.dumps(state.dashboard_settings))
        try:
            state.dashboard_settings = validation.validated_dashboard_settings(
                state.dashboard_settings,
                request.gain_tolerance,
                request.warn_limits,
            )
        except ValueError as exc:
            raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
        state.save_persisted_dashboard_settings()
        after = json.loads(json.dumps(state.dashboard_settings))
    api_security.audit_event(
        http_request,
        "settings_updated",
        current_user["username"],
        api_security.audit_changes(before, after),
    )
    return {
        **after,
        "gain_set_limits": {
            "min": config.GAIN_SET_MIN,
            "max": config.GAIN_SET_MAX,
        },
    }


@router.get("/api/warnings/active")
def get_active_warnings(
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return the currently active warnings without reading history logs."""

    with state.state_lock:
        warnings = sorted(
            (dict(item) for item in state.active_warnings.values()),
            key=lambda item: item.get("opened_at", ""),
            reverse=True,
        )
        return {"active": warnings}


@router.get("/api/warnings")
def get_warnings(
    range_value: str = fastapi.Query(default="24h", alias="range"),
    start: str = "",
    end: str = "",
    field: str = "",
    status: str = "",
    limit: int = fastapi.Query(default=100, ge=1, le=500),
    offset: int = fastapi.Query(default=0, ge=0, le=10000),
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return filtered warning history together with currently active warnings."""

    now = datetime.datetime.now(datetime.timezone.utc)
    durations = {
        "1h": datetime.timedelta(hours=1),
        "24h": datetime.timedelta(hours=24),
        "7d": datetime.timedelta(days=7),
        "30d": datetime.timedelta(days=30),
    }
    if range_value == "session":
        range_start = datetime.datetime.fromisoformat(state.app_started_at)
    elif range_value in durations:
        range_start = now - durations[range_value]
    elif range_value == "custom":
        try:
            range_start = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError as exc:
            raise fastapi.HTTPException(
                status_code=400, detail="A valid custom start time is required."
            ) from exc
    else:
        raise fastapi.HTTPException(status_code=400, detail="Invalid warning range.")

    try:
        range_end = datetime.datetime.fromisoformat(end.replace("Z", "+00:00")) if end else now
    except ValueError as exc:
        raise fastapi.HTTPException(
            status_code=400, detail="The warning end time is invalid."
        ) from exc
    if range_start.tzinfo is None:
        range_start = range_start.replace(tzinfo=datetime.timezone.utc)
    if range_end.tzinfo is None:
        range_end = range_end.replace(tzinfo=datetime.timezone.utc)
    range_start = range_start.astimezone(datetime.timezone.utc)
    range_end = range_end.astimezone(datetime.timezone.utc)
    if range_start > range_end:
        raise fastapi.HTTPException(
            status_code=400, detail="Warning start time must be before end time."
        )
    normalized_status = status.strip().lower()
    if normalized_status not in {"", "open", "cleared"}:
        raise fastapi.HTTPException(status_code=400, detail="Invalid warning status.")

    history = syslog_service.read_warning_history(
        start=range_start,
        end=range_end,
        field=field.strip(),
        status=normalized_status,
        limit=limit,
        offset=offset,
    )
    with state.state_lock:
        active = sorted(
            (dict(item) for item in state.active_warnings.values()),
            key=lambda item: item.get("opened_at", ""),
            reverse=True,
        )
    return {
        "active": active,
        "history": history["events"],
        "total": history["total"],
        "offset": history["offset"],
        "limit": history["limit"],
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "source_available": history["source_available"],
        "unreadable_files": history["unreadable_files"],
    }


@router.post("/api/warnings/acknowledge")
def acknowledge_warnings(
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator", "Operator")),
):
    """Mark every currently active warning as acknowledged."""

    with state.state_lock:
        keys = set(state.active_warnings)
        state.acknowledged_warning_keys.update(keys)
        for key in keys:
            state.active_warnings[key]["acknowledged"] = True
        acknowledged_count = len(keys)
    api_security.audit_event(
        request,
        "warnings_acknowledged",
        current_user["username"],
        f"count={acknowledged_count}",
    )
    return {"acknowledged": acknowledged_count}


@router.post("/api/set_gain")
def set_gain(
    request: GainSetRequest,
    http_request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator", "Operator")),
):
    """Validate and send a new optical-amplifier gain setpoint."""

    with state.state_lock:
        previous_gain_set = state.last_known_gain_set
    try:
        gain_set = validation.validate_gain_set(
            request.gain_set,
            config.GAIN_SET_MIN,
            config.GAIN_SET_MAX,
        )
        serial_reader.send_gain_set(gain_set)
    except ValueError as exc:
        raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise fastapi.HTTPException(status_code=503, detail=str(exc)) from exc
    api_security.audit_event(
        http_request,
        "gain_setpoint_updated",
        current_user["username"],
        api_security.audit_changes(
            {"gain_set": previous_gain_set},
            {"gain_set": gain_set},
        ),
    )
    return {"status": "ok", "gain_set": gain_set}

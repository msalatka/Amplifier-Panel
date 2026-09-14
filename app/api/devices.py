"""Inventory and live data for independently running devices."""

import datetime

import fastapi

from app.api import security as api_security
from app.api import history as history_api
from app.core import config, state
from app.devices.registry import DEVICES
from app.services import database as database_service

router = fastapi.APIRouter(prefix="/api/devices")
viewer = fastapi.Depends(api_security.require_roles("Administrator", "Operator", "Viewer"))


def require_enabled(device_id: str) -> None:
    if device_id not in config.ENABLED_DEVICES:
        raise fastapi.HTTPException(status_code=404, detail="Device is not enabled")


@router.get("")
def list_devices(_current_user: dict = viewer):
    """List configured devices and their independent connection states."""

    devices = []
    for device_id in config.ENABLED_DEVICES:
        live = state.snapshot_device_live(device_id)
        devices.append({
            "id": device_id,
            "label": DEVICES[device_id].label,
            "profile": DEVICES[device_id].view_profile,
            "connected": live["connected"],
            "error": live["error"],
            "last_update": live["last_update"],
        })
    return {"devices": devices}


@router.get("/{device_id}/latest")
def latest(device_id: str, _current_user: dict = viewer):
    """Return live state and storage health for one selected device."""

    require_enabled(device_id)
    live = state.snapshot_device_live(device_id)
    return {
        "device_id": device_id,
        "device_profile": DEVICES[device_id].view_profile,
        "connected": live["connected"],
        "error": live["error"],
        "last_update": live["last_update"],
        "system_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "last_known_gain_set": state.last_known_gain_set if device_id == "amplifier" else None,
        "data": live["data"],
        "fts_ls": live["data"] if device_id == "fts-ls" else None,
        "database": database_service.get_runtime_status(device_id),
    }


@router.get("/{device_id}/statistics")
def statistics(
    device_id: str,
    range: str = "5m",
    start: str | None = None,
    end: str | None = None,
    _current_user: dict = viewer,
):
    """Return exact selected-device statistics, using hourly buckets where available."""

    require_enabled(device_id)
    range, start, end = history_api.normalize_history_request(range, start, end)
    result = (
        database_service.query_statistics(range, start, end)
        if device_id == "amplifier"
        else database_service.query_device_statistics(device_id, range, start, end)
    )
    if result is None:
        raise fastapi.HTTPException(status_code=503, detail="History database is unavailable")
    return {"device_id": device_id, "range": range, "start": start, "end": end, **result}

"""Inventory and live data for independently running devices."""

import csv
import datetime
import io
import threading

import fastapi

from app.api import history as history_api
from app.api import security as api_security
from app.core import config, state
from app.devices.registry import DEVICES
from app.services import database as database_service
from app.services.device_statistics import scalar_fields

router = fastapi.APIRouter(prefix="/api/devices")
viewer = fastapi.Depends(api_security.require_roles("Administrator", "Operator", "Viewer"))
CSV_EXPORT_LOCK = threading.Lock()


@router.get("/{device_id}/history/export.csv")
def export_device_history(device_id: str, range: str = "5m", _current_user: dict = viewer):
    """Stream complete per-device history without the chart downsampling limit."""
    require_enabled(device_id)
    range, _, _ = history_api.normalize_history_request(range, None, None)
    if not CSV_EXPORT_LOCK.acquire(blocking=False):
        raise fastapi.HTTPException(status_code=429, detail="Another CSV export is in progress")
    points = database_service.stream_device_snapshots(device_id, range)
    if points is None:
        CSV_EXPORT_LOCK.release()
        raise fastapi.HTTPException(status_code=503, detail="History database is unavailable")

    def rows():
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        try:
            writer.writerow(["time", "field", "value"])
            for point in points:
                for key, value in scalar_fields(point["snapshot"].get("values", {})).items():
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                        value = "'" + value
                    writer.writerow([point["time"], key, value])
                if output.tell() >= 65536:
                    yield output.getvalue()
                    output.seek(0)
                    output.truncate(0)
            yield output.getvalue()
        finally:
            points.close()
            CSV_EXPORT_LOCK.release()

    return fastapi.responses.StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{device_id}-history.csv"'},
    )


def require_enabled(device_id: str) -> None:
    """Reject requests for a device outside the configured inventory."""
    if device_id not in config.ENABLED_DEVICES:
        raise fastapi.HTTPException(status_code=404, detail="Device is not enabled")


@router.get("")
def list_devices(_current_user: dict = viewer):
    """List configured devices and their independent connection states."""

    devices = []
    for device_id in config.ENABLED_DEVICES:
        live = state.snapshot_device_live(device_id)
        devices.append(
            {
                "id": device_id,
                "label": DEVICES[device_id].label,
                "profile": DEVICES[device_id].view_profile,
                "connected": live["connected"],
                "error": live["error"],
                "last_update": live["last_update"],
            }
        )
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
        "data": live["data"],
        "database": database_service.get_runtime_status(device_id),
    }


@router.get("/{device_id}/history")
def device_history(
    device_id: str,
    range: str = "5m",
    start: str | None = None,
    end: str | None = None,
    limit: int = fastapi.Query(default=500, ge=1, le=2000),
    _current_user: dict = viewer,
):
    """Return bounded XML observations for charts and inspection."""
    require_enabled(device_id)
    range, start, end = history_api.normalize_history_request(range, start, end)
    points = database_service.query_device_snapshots(device_id, range, start, end, limit)
    if points is None:
        raise fastapi.HTTPException(status_code=503, detail="History database is unavailable")
    return {"points": points}


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
    result = database_service.query_device_statistics(device_id, range, start, end)
    if result is None:
        raise fastapi.HTTPException(status_code=503, detail="History database is unavailable")
    return {"device_id": device_id, "range": range, "start": start, "end": end, **result}

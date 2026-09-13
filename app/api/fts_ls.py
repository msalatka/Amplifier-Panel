"""FTS-LS capabilities, control, status, and history endpoints."""

import csv
import datetime
import io
import threading

import fastapi
import pydantic
import starlette.requests
import starlette.responses

from app.api import history as history_api
from app.api import security as api_security
from app.core import config, state
from app.services import database as database_service

router = fastapi.APIRouter(prefix="/api/fts-ls")

CSV_EXPORT_LOCK = threading.Lock()


class DeviceCommandRequest(pydantic.BaseModel):
    """Validated envelope for an FTS-LS control action."""

    action: str
    parameters: dict = pydantic.Field(default_factory=dict)
    confirmed: bool = False


def require_profile() -> None:
    """Reject an FTS-LS request when another device profile is active."""

    if config.DEVICE_PROFILE != "fts-ls":
        raise fastapi.HTTPException(
            status_code=409,
            detail="The FTS-LS API is unavailable for the amplifier profile.",
        )


@router.get("/capabilities")
def capabilities(
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Describe the read-only FTS-LS profile pending the daemon XML contract."""

    require_profile()
    return {
        "profile": "fts-ls",
        "model": "Frequency Transfer System - Laser Station",
        "ports": 7,
        "module_types": ["Downlink", "Feedback Link", "Beat Detector", "Unequipped"],
        "controls": [],
        "read_only": True,
        "status_source": "daemon-xml-pending",
    }


@router.get("/status")
def get_status(
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return the latest normalized FTS-LS station status."""

    require_profile()
    with state.state_lock:
        return {
            "connected": state.serial_connected,
            "error": state.serial_error,
            "last_update": state.last_update,
            "status": state.fts_ls_status,
        }


@router.post("/command")
def command(
    body: DeviceCommandRequest,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator", "Operator")),
):
    """Reject control until the daemon's XML command contract is defined."""

    require_profile()
    raise fastapi.HTTPException(
        status_code=503,
        detail="FTS-LS control is unavailable until the daemon XML interface is specified.",
    )


def _history(
    range_value: str,
    start: str | None,
    end: str | None,
    limit: int,
) -> tuple[str, str | None, str | None, list[dict]]:
    range_value, start, end = history_api.normalize_history_request(range_value, start, end)
    points = database_service.query_device_snapshots("fts-ls", range_value, start, end, limit)
    if points is None:
        raise fastapi.HTTPException(status_code=503, detail="History database is unavailable.")
    return range_value, start, end, points


@router.get("/history")
def history(
    range_value: str = fastapi.Query(default="5m", alias="range"),
    start: str | None = None,
    end: str | None = None,
    limit: int = fastapi.Query(default=2000, ge=1, le=10000),
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return normalized FTS-LS snapshots from SQLite history."""

    require_profile()
    range_value, start, end, points = _history(range_value, start, end, limit)
    return {
        "source": "sqlite",
        "profile": "fts-ls",
        "range": range_value,
        "start": start,
        "end": end,
        "points": points,
    }


def _flatten(point: dict) -> dict:
    snapshot = point["snapshot"]
    row = {"time": point["time"]}
    for key, value in snapshot.get("laser", {}).items():
        if not isinstance(value, (dict, list)):
            row[f"laser_{key}"] = value
    for key, value in snapshot.get("tec", {}).items():
        if not isinstance(value, (dict, list)):
            row[f"tec_{key}"] = value
    for key, value in snapshot.get("synth", {}).items():
        if not isinstance(value, (dict, list)):
            row[f"synth_{key}"] = value
    for module in [snapshot.get("uplink", {}), *snapshot.get("ports", [])]:
        prefix = str(module.get("name", "module")).lower()
        for key, value in module.items():
            if key != "connectors" and not isinstance(value, (dict, list)):
                row[f"{prefix}_{key}"] = value
    return row


@router.get("/history/export.csv")
def export_history(
    request: starlette.requests.Request,
    range_value: str = fastapi.Query(default="5m", alias="range"),
    start: str | None = None,
    end: str | None = None,
    limit: int = fastapi.Query(default=10000, ge=1, le=10000),
    current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Flatten and export normalized FTS-LS snapshots as CSV."""

    require_profile()
    if not CSV_EXPORT_LOCK.acquire(blocking=False):
        raise fastapi.HTTPException(status_code=429, detail="Another CSV export is in progress")
    try:
        range_value, start, end, points = _history(range_value, start, end, limit)
        rows = [_flatten(point) for point in points]
        fieldnames = ["time"]
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        output = io.StringIO()
        output.write("sep=;\r\n")
        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
            extrasaction="ignore",
            delimiter=";",
            lineterminator="\r\n",
        )
        writer.writeheader()
        writer.writerows(rows)
        api_security.audit_event(
            request,
            "history_csv_exported",
            current_user["username"],
            f"profile=fts-ls; range={range_value}; start={start}; end={end}",
        )
        content = output.getvalue()
    finally:
        CSV_EXPORT_LOCK.release()
    filename = f"fts_ls_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return starlette.responses.Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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
from app.services import fts_ls

router = fastapi.APIRouter(prefix="/api/fts-ls")

TRANSFER_AFFECTING_ACTIONS = {
    "power_reset",
    "factory_default",
    "laser_power",
    "laser_central_frequency",
    "laser_mode",
    "laser_force_relock",
    "optical_power",
}
ADMIN_ONLY_ACTIONS = {"reboot", "power_reset", "factory_default"}
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
    """Describe controls and ranges supported by the FTS-LS profile."""

    require_profile()
    return {
        "profile": "fts-ls",
        "model": "Frequency Transfer System - Laser Station",
        "ports": 7,
        "module_types": ["Downlink", "Feedback Link", "Beat Detector", "Unequipped"],
        "controls": [
            "laser_power",
            "laser_central_frequency",
            "laser_mode",
            "laser_frequency_span",
            "laser_force_relock",
            "tec_power",
            "tec_temperature",
            "external_reference",
            "description",
            "optical_power",
            "downlink_distance",
            "downlink_gain",
            "polarization_control",
            "polarization_speed",
            "polarization_mode",
            "ping",
            "reboot",
            "power_reset",
            "factory_default",
        ],
        "ranges": {
            "laser_central_frequency_ghz": [
                config.FTS_LS_FREQUENCY_MIN_GHZ,
                config.FTS_LS_FREQUENCY_MAX_GHZ,
            ],
            "laser_frequency_span_mhz": [100, 10000],
            "downlink_distance_km": [10, 2000],
            "additional_nc_gain_db": [0, 12, 24],
        },
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
    """Authorize, validate, execute, and audit one FTS-LS action."""

    require_profile()
    action = body.action.strip().lower().replace("-", "_")
    if action in ADMIN_ONLY_ACTIONS and current_user["role"] != "Administrator":
        raise fastapi.HTTPException(status_code=403, detail="Administrator access required.")
    if action in TRANSFER_AFFECTING_ACTIONS and not body.confirmed:
        raise fastapi.HTTPException(
            status_code=409,
            detail="This operation can interrupt frequency transfer and requires confirmation.",
        )
    try:
        result = fts_ls.submit_action(action, body.parameters)
    except ValueError as exc:
        raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise fastapi.HTTPException(status_code=503, detail=str(exc)) from exc
    api_security.audit_event(
        request,
        f"fts_ls_{action}",
        current_user["username"],
        api_security.audit_changes({}, body.parameters),
    )
    return result


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

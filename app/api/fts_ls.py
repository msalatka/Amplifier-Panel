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
from app.services import device_statistics

router = fastapi.APIRouter(prefix="/api/fts-ls")

CSV_EXPORT_LOCK = threading.Lock()


class DeviceCommandRequest(pydantic.BaseModel):
    """Validated envelope for an FTS-LS control action."""

    action: str
    parameters: dict = pydantic.Field(default_factory=dict)
    confirmed: bool = False


def require_profile() -> None:
    """Reject FTS-LS requests only when that device is not configured."""

    if "fts-ls" not in config.ENABLED_DEVICES:
        raise fastapi.HTTPException(
            status_code=409,
            detail="The FTS-LS device is not enabled.",
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
    live = state.snapshot_device_live("fts-ls")
    return {
        "connected": live["connected"],
        "error": live["error"],
        "last_update": live["last_update"],
        "status": live["data"] or state.fts_ls_status,
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


@router.get("/history/export.csv")
def export_history(
    request: starlette.requests.Request,
    range_value: str = fastapi.Query(default="5m", alias="range"),
    start: str | None = None,
    end: str | None = None,
    current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Stream every selected FTS-LS observation as long-form CSV."""

    require_profile()
    range_value, start, end = history_api.normalize_history_request(range_value, start, end)
    if not CSV_EXPORT_LOCK.acquire(blocking=False):
        raise fastapi.HTTPException(status_code=429, detail="Another CSV export is in progress")
    points = database_service.stream_device_snapshots("fts-ls", range_value, start, end)
    if points is None:
        CSV_EXPORT_LOCK.release()
        raise fastapi.HTTPException(status_code=503, detail="History database is unavailable")

    api_security.audit_event(
        request,
        "history_csv_exported",
        current_user["username"],
        f"profile=fts-ls; range={range_value}; start={start}; end={end}; streaming=true",
    )

    def safe_cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    def generate_csv():
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["time", "device_id", "field", "value"],
            delimiter=";",
            lineterminator="\r\n",
        )
        try:
            output.write("sep=;\r\n")
            writer.writeheader()
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
            for point in points:
                fields = device_statistics.scalar_fields(point["snapshot"])
                if not fields:
                    fields = {"snapshot": "{}"}
                for field, value in fields.items():
                    writer.writerow({
                        "time": point["time"],
                        "device_id": "fts-ls",
                        "field": safe_cell(field),
                        "value": safe_cell(value),
                    })
                    if output.tell() >= 64 * 1024:
                        yield output.getvalue()
                        output.seek(0)
                        output.truncate(0)
            if output.tell():
                yield output.getvalue()
        finally:
            points.close()
            CSV_EXPORT_LOCK.release()

    filename = f"fts_ls_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return starlette.responses.StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

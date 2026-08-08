"""Optical-amplifier history, statistics, and CSV export endpoints."""

import csv
import datetime
import io
import threading

import fastapi
import fastapi.responses
import starlette.requests

from app.api import security as api_security
from app.core import device_schema
from app.services import database as database_service

router = fastapi.APIRouter()
ALLOWED_RANGES = {"5m", "1h", "24h", "7d", "30d", "all"}
CSV_FIELDS = device_schema.AMPLIFIER_CSV_FIELDS
CSV_EXPORT_LOCK = threading.Lock()


def _parse_iso_datetime(value: str | None):
    if not value or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def normalize_history_request(
    range_value: str,
    start: str | None,
    end: str | None,
) -> tuple[str, str | None, str | None]:
    """Validate a history range and normalize optional timestamps to UTC."""

    if range_value not in ALLOWED_RANGES:
        raise fastapi.HTTPException(status_code=400, detail="Invalid history range")
    try:
        start_value = _parse_iso_datetime(start)
        end_value = _parse_iso_datetime(end)
    except ValueError as exc:
        raise fastapi.HTTPException(status_code=400, detail="Invalid history timestamp") from exc
    if start_value and end_value and start_value >= end_value:
        raise fastapi.HTTPException(status_code=400, detail="History start must be before end")
    return (
        range_value,
        start_value.isoformat() if start_value else None,
        end_value.isoformat() if end_value else None,
    )


def _read_history(range_value: str, start: str | None, end: str | None) -> dict:
    result = database_service.query_history(range_value, start, end, include_metadata=True)
    if result is None:
        raise fastapi.HTTPException(status_code=503, detail="Local database is unavailable")
    return result


@router.get("/api/history")
def history(
    range: str = "5m",
    start: str | None = None,
    end: str | None = None,
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return amplifier measurements and setpoints from SQLite history."""

    range, start, end = normalize_history_request(range, start, end)
    result = _read_history(range, start, end)
    return {
        "source": "sqlite",
        "range": range,
        "start": start,
        "end": end,
        **result,
    }


@router.get("/api/statistics")
def statistics(
    range: str = "5m",
    start: str | None = None,
    end: str | None = None,
    _current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Return aggregate amplifier statistics for a validated time range."""

    range, start, end = normalize_history_request(range, start, end)
    result = database_service.query_statistics(range, start, end)
    if result is None:
        raise fastapi.HTTPException(status_code=503, detail="Local database is unavailable")
    return {
        "source": "sqlite",
        "range": range,
        "start": start,
        "end": end,
        **result,
    }


@router.get("/api/history/export.csv")
def export_history_csv(
    request: starlette.requests.Request,
    range: str = "5m",
    start: str | None = None,
    end: str | None = None,
    current_user: dict = fastapi.Depends(
        api_security.require_roles("Administrator", "Operator", "Viewer")
    ),
):
    """Stream amplifier measurement history as a semicolon-delimited CSV file."""

    range, start, end = normalize_history_request(range, start, end)
    if not CSV_EXPORT_LOCK.acquire(blocking=False):
        raise fastapi.HTTPException(status_code=429, detail="Another CSV export is in progress")

    points = database_service.stream_raw_history(range, start, end)
    if points is None:
        CSV_EXPORT_LOCK.release()
        raise fastapi.HTTPException(status_code=503, detail="Local database is unavailable")

    api_security.audit_event(
        request,
        "history_csv_exported",
        current_user["username"],
        f"range={range}; start={start}; end={end}; streaming=true",
    )

    def generate_csv():
        """Yield bounded CSV chunks and release the single-export lock."""

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore",
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
                writer.writerow(point)
                if output.tell() >= 64 * 1024:
                    yield output.getvalue()
                    output.seek(0)
                    output.truncate(0)
            if output.tell():
                yield output.getvalue()
        finally:
            points.close()
            CSV_EXPORT_LOCK.release()

    filename = f"amp_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return fastapi.responses.StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

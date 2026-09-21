"""Shared validation of device-history time ranges."""

import datetime

import fastapi

ALLOWED_RANGES = {"5m", "1h", "24h", "7d", "30d", "all"}


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

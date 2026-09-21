"""Transport-neutral publishing API for future device acquisition workers.

An adapter reads its own transport, validates a complete status snapshot and
calls ``publish_snapshot`` once per new observation. The panel then handles
live state and SQLite storage without the adapter knowing either API format.
"""

import copy
import datetime
import json

from app.core import config, state
from app.devices.registry import DEVICES
from app.services import database as database_service


def publish_snapshot(
    device_id: str,
    snapshot: dict,
    timestamp: str | None = None,
) -> bool:
    """Publish a validated device observation to live state and SQLite."""

    if device_id not in config.ENABLED_DEVICES or device_id not in DEVICES:
        raise ValueError(f"Device is not enabled: {device_id}")
    if not isinstance(snapshot, dict):
        raise ValueError("Device snapshot must be a mapping")
    try:
        payload = json.dumps(snapshot, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Device snapshot must be JSON-compatible and finite") from exc
    if len(payload) > 1_000_000:
        raise ValueError("Device snapshot exceeds the 1 MB safety limit")
    try:
        observed_at = (
            datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if timestamp
            else datetime.datetime.now(datetime.timezone.utc)
        )
    except ValueError as exc:
        raise ValueError("Invalid device observation timestamp") from exc
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=datetime.timezone.utc)
    now = observed_at.astimezone(datetime.timezone.utc).isoformat()
    previous = state.snapshot_device_live(device_id)
    stored = (
        database_service.write_device_snapshot(device_id, snapshot, now)
        if previous["last_update"] != now
        else False
    )
    state.update_device_live(
        device_id,
        connected=True,
        error=None,
        last_update=now,
        data=copy.deepcopy(snapshot),
    )
    return stored


def report_failure(device_id: str, error: str) -> None:
    """Mark one acquisition source disconnected without touching other devices."""

    if device_id not in config.ENABLED_DEVICES:
        raise ValueError(f"Device is not enabled: {device_id}")
    state.update_device_live(device_id, connected=False, error=error)

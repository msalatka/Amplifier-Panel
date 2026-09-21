"""Thread-safe persistence facade for measurements, snapshots and history queries."""

import datetime
import json
import logging
import pathlib
import shutil
import sqlite3
import threading
from typing import Any

from app.core import config, state
from app.services import database_schema, device_statistics

logger = logging.getLogger(__name__)
connection = None
database_lock = threading.RLock()
last_error = None
discarded_records = 0
HOUR_MS = 60 * 60 * 1000


def _timestamp_ms(timestamp: str | None) -> int:
    if timestamp:
        normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        parsed = datetime.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        else:
            parsed = parsed.astimezone(datetime.timezone.utc)
    else:
        parsed = datetime.datetime.now(datetime.timezone.utc)
    return round(parsed.timestamp() * 1000)


def _set_error(operation: str, error: Exception) -> None:
    global last_error
    last_error = f"{operation}: {error}"
    logger.warning("SQLite %s failed: %s", operation, error)


def _create_schema(opened_connection: sqlite3.Connection) -> None:
    database_schema.create_schema(opened_connection)


def init_database() -> None:
    """Open SQLite and prepare persistent summaries.

    Initialization is idempotent and serialized because API handlers and the XML
    worker may reach the service concurrently during startup.
    """
    global connection
    global last_error

    if connection is not None:
        return
    with database_lock:
        if connection is not None:
            return
        opened_connection = None
        try:
            database_path = pathlib.Path(config.DATABASE_FILE)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            opened_connection = sqlite3.connect(database_path, check_same_thread=False)
            opened_connection.row_factory = sqlite3.Row
            opened_connection.execute("PRAGMA journal_mode=WAL")
            opened_connection.execute("PRAGMA synchronous=NORMAL")
            with opened_connection:
                _create_schema(opened_connection)
                opened_connection.execute("PRAGMA user_version=6")
            connection = opened_connection
            last_error = None
        except (OSError, sqlite3.Error) as error:
            _set_error("initialization", error)
            if opened_connection is not None:
                opened_connection.close()


def close_database() -> None:
    """Checkpoint pending WAL data and close the shared SQLite connection."""

    global connection
    with database_lock:
        if connection is not None:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.close()
            connection = None


def _prune_device_snapshots(max_records: int, profile: str) -> int:
    global discarded_records
    row_count = connection.execute(
        "SELECT COUNT(*) FROM device_snapshots WHERE profile = ?", (profile,)
    ).fetchone()[0]
    records_to_remove = max(0, int(row_count) - max_records)
    if records_to_remove:
        connection.execute(
            "DELETE FROM device_snapshots WHERE id IN ("
            "SELECT id FROM device_snapshots WHERE profile = ? "
            "ORDER BY id ASC LIMIT ?)",
            (profile, records_to_remove),
        )
        first_remaining = connection.execute(
            "SELECT MIN(timestamp_ms) FROM device_snapshots WHERE profile = ?", (profile,)
        ).fetchone()[0]
        if first_remaining is None:
            connection.execute(
                "DELETE FROM device_hourly_statistics WHERE device_id = ?", (profile,)
            )
        else:
            first_bucket = (int(first_remaining) // HOUR_MS) * HOUR_MS
            connection.execute(
                "DELETE FROM device_hourly_statistics WHERE device_id = ? AND bucket_ms <= ?",
                (profile, first_bucket),
            )
        discarded_records += records_to_remove
    return records_to_remove


def write_device_snapshot(
    profile: str,
    snapshot: dict,
    timestamp: str | None = None,
) -> bool:
    """Persist one complete profile-specific device snapshot as canonical JSON."""
    global last_error
    try:
        timestamp_value = _timestamp_ms(timestamp)
        payload = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        _set_error("device snapshot serialization", error)
        return False
    init_database()
    if connection is None:
        return False
    max_records = max(0, int(state.service_settings["database_max_records"]))
    with database_lock:
        try:
            previous_last_timestamp = connection.execute(
                "SELECT MAX(timestamp_ms) FROM device_snapshots WHERE profile = ?",
                (profile,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO device_snapshots (timestamp_ms, profile, snapshot_json) "
                "VALUES (?, ?, ?)",
                (timestamp_value, profile, payload),
            )
            inserted_bucket = (timestamp_value // HOUR_MS) * HOUR_MS
            if previous_last_timestamp is not None:
                previous_bucket = (int(previous_last_timestamp) // HOUR_MS) * HOUR_MS
                if inserted_bucket > previous_bucket:
                    device_statistics.rebuild_hour(connection, profile, previous_bucket, HOUR_MS)
                elif inserted_bucket <= previous_bucket:
                    existing = connection.execute(
                        "SELECT 1 FROM device_hourly_statistics WHERE device_id = ? AND bucket_ms = ?",
                        (profile, inserted_bucket),
                    ).fetchone()
                    if existing:
                        device_statistics.rebuild_hour(
                            connection, profile, inserted_bucket, HOUR_MS
                        )
            if max_records:
                _prune_device_snapshots(max_records, profile)
            connection.commit()
            last_error = None
            return True
        except (OSError, sqlite3.Error) as error:
            connection.rollback()
            _set_error("device snapshot write", error)
            return False


def get_device_snapshot_count(profile: str | None = None) -> int:
    """Return stored device-snapshot count, optionally for one profile."""

    init_database()
    if connection is None:
        return 0
    with database_lock:
        try:
            if profile:
                return int(
                    connection.execute(
                        "SELECT COUNT(*) FROM device_snapshots WHERE profile = ?",
                        (profile,),
                    ).fetchone()[0]
                )
            return int(
                connection.execute(
                    "SELECT value FROM database_metadata WHERE key = 'device_snapshot_count'"
                ).fetchone()[0]
            )
        except sqlite3.Error as error:
            _set_error("device snapshot status", error)
            return 0


def apply_record_limit() -> int:
    """Apply the configured per-device record cap to every stored device."""

    init_database()
    if connection is None:
        return 0
    with database_lock:
        try:
            max_records = max(0, int(state.service_settings["database_max_records"]))
            removed = 0
            if max_records:
                for row in connection.execute(
                    "SELECT DISTINCT profile FROM device_snapshots"
                ).fetchall():
                    removed += _prune_device_snapshots(max_records, row[0])
            connection.commit()
            return removed
        except sqlite3.Error as error:
            connection.rollback()
            _set_error("record limit update", error)
            return 0


def get_storage_status(device_id: str = config.ENABLED_DEVICES[0]) -> dict:
    """Return database capacity and selected-device retention estimates."""

    database_path = pathlib.Path(config.DATABASE_FILE)
    database_files = (
        database_path,
        pathlib.Path(f"{database_path}-wal"),
        pathlib.Path(f"{database_path}-shm"),
    )
    try:
        size_bytes = sum(path.stat().st_size for path in database_files if path.exists())
        free_bytes = shutil.disk_usage(database_path.parent).free
    except OSError as error:
        _set_error("disk status", error)
        size_bytes = 0
        free_bytes = 0
    sample_rate_per_second = None
    init_database()
    if connection is not None:
        with database_lock:
            try:
                source_table = "device_snapshots"
                source_filter = "WHERE profile = ?"
                source_args = (device_id,)
                latest_timestamp = connection.execute(
                    f"SELECT MAX(timestamp_ms) FROM {source_table} {source_filter}",
                    source_args,
                ).fetchone()[0]
                if latest_timestamp is not None:
                    recent_filter = "profile = ? AND "
                    recent = connection.execute(
                        f"""
                        SELECT COUNT(*) AS sample_count,
                               MIN(timestamp_ms) AS first_ms,
                               MAX(timestamp_ms) AS last_ms
                        FROM {source_table}
                        WHERE {recent_filter}timestamp_ms >= ?
                        """,
                        (*source_args, int(latest_timestamp) - HOUR_MS),
                    ).fetchone()
                    span_seconds = (
                        (int(recent["last_ms"]) - int(recent["first_ms"])) / 1000
                        if recent["sample_count"] >= 2
                        else 0
                    )
                    if span_seconds > 0:
                        sample_rate_per_second = (int(recent["sample_count"]) - 1) / span_seconds
            except sqlite3.Error as error:
                _set_error("storage estimate", error)

    record_limit = max(0, int(state.service_settings["database_max_records"]))
    records = get_device_snapshot_count(device_id)
    estimated_retention_seconds = None
    estimated_seconds_to_limit = None
    estimated_seconds_until_disk_full = None
    if record_limit and sample_rate_per_second:
        estimated_retention_seconds = record_limit / sample_rate_per_second
        estimated_seconds_to_limit = max(0, record_limit - records) / sample_rate_per_second
    if records > 0 and size_bytes > 0 and sample_rate_per_second:
        estimated_bytes_per_record = size_bytes / records
        estimated_seconds_until_disk_full = (
            free_bytes / estimated_bytes_per_record / sample_rate_per_second
        )

    return {
        "size_bytes": size_bytes,
        "free_bytes": free_bytes,
        "discarded_records_since_start": discarded_records,
        "sample_rate_per_second": sample_rate_per_second,
        "estimated_retention_seconds": estimated_retention_seconds,
        "estimated_seconds_to_limit": estimated_seconds_to_limit,
        "estimated_seconds_until_disk_full": estimated_seconds_until_disk_full,
    }


def get_runtime_status(device_id: str = config.ENABLED_DEVICES[0]) -> dict:
    """Return readiness, record counts, retention estimate and the last SQL error."""
    init_database()
    records = get_device_snapshot_count(device_id)
    return {
        "state": "ready" if connection is not None else "error",
        "ready": connection is not None,
        "records": records if connection is not None else 0,
        "error": last_error,
    }


def query_device_snapshots(
    profile: str,
    range_value: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 2000,
) -> list[dict] | None:
    """Return evenly sampled snapshots for a profile and inclusive time range."""
    init_database()
    if connection is None:
        return None
    try:
        start_ms = _parse_boundary(start)
        if start_ms is None:
            range_start = _range_start(range_value)
            start_ms = round(range_start.timestamp() * 1000) if range_start else None
        end_ms = _parse_boundary(end)
        clauses = ["profile = ?"]
        parameters: list[Any] = [profile]
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            parameters.append(start_ms)
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            parameters.append(end_ms)
        point_limit = max(1, min(int(limit), 10_000))
        where_clause = " AND ".join(clauses)
        with database_lock:
            if point_limit == 1:
                rows = connection.execute(
                    "SELECT timestamp_ms, snapshot_json FROM device_snapshots "
                    f"WHERE {where_clause} "
                    "ORDER BY timestamp_ms DESC, id DESC LIMIT 1",
                    parameters,
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    WITH ordered AS (
                        SELECT timestamp_ms,
                               snapshot_json,
                               ROW_NUMBER() OVER (ORDER BY timestamp_ms ASC, id ASC) AS row_nr,
                               COUNT(*) OVER () AS total_rows
                        FROM device_snapshots
                        WHERE {where_clause}
                    )
                    SELECT timestamp_ms, snapshot_json
                    FROM ordered
                    WHERE (row_nr - 1) % MAX(
                        1, (total_rows - 1 + ?) / ?
                    ) = 0
                       OR row_nr = total_rows
                    ORDER BY row_nr ASC
                    LIMIT ?
                    """,
                    [*parameters, point_limit - 2, point_limit - 1, point_limit],
                ).fetchall()
        return [
            {
                "time": datetime.datetime.fromtimestamp(
                    row["timestamp_ms"] / 1000, datetime.timezone.utc
                ).isoformat(),
                "snapshot": json.loads(row["snapshot_json"]),
            }
            for row in rows
        ]
    except (TypeError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        _set_error("device snapshot query", error)
        return None


def query_device_statistics(
    device_id: str,
    range_value: str,
    start: str | None = None,
    end: str | None = None,
) -> dict | None:
    """Merge saved complete-hour summaries with raw boundary observations."""

    init_database()
    if connection is None:
        return None
    try:
        start_ms = _parse_boundary(start)
        if start_ms is None:
            range_start = _range_start(range_value)
            start_ms = round(range_start.timestamp() * 1000) if range_start else None
        end_ms = _parse_boundary(end)
        if end_ms is None:
            end_ms = _timestamp_ms(None)
        with database_lock:
            if start_ms is None:
                start_ms = connection.execute(
                    "SELECT MIN(timestamp_ms) FROM device_snapshots WHERE profile = ?",
                    (device_id,),
                ).fetchone()[0]
            if start_ms is None:
                return {"sample_count": 0, "statistics": {}}
            first_full = ((start_ms + HOUR_MS - 1) // HOUR_MS) * HOUR_MS
            last_full_exclusive = (end_ms // HOUR_MS) * HOUR_MS
            stats = {}
            sample_count = 0
            summaries = connection.execute(
                "SELECT sample_count, statistics_json FROM device_hourly_statistics "
                "WHERE device_id = ? AND bucket_ms >= ? AND bucket_ms < ?",
                (device_id, first_full, last_full_exclusive),
            )
            for summary in summaries:
                sample_count += int(summary["sample_count"])
                device_statistics.merge_stats(stats, json.loads(summary["statistics_json"]))
            raw_rows = connection.execute(
                "SELECT snapshot_json FROM device_snapshots AS d "
                "WHERE d.profile = ? AND d.timestamp_ms >= ? AND d.timestamp_ms <= ? "
                "AND NOT EXISTS (SELECT 1 FROM device_hourly_statistics AS h "
                "WHERE h.device_id = d.profile "
                "AND h.bucket_ms = (d.timestamp_ms / ?) * ? "
                "AND h.bucket_ms >= ? AND h.bucket_ms < ?) "
                "ORDER BY d.timestamp_ms, d.id",
                (device_id, start_ms, end_ms, HOUR_MS, HOUR_MS, first_full, last_full_exclusive),
            )
            for row in raw_rows:
                device_statistics.add_snapshot(stats, json.loads(row["snapshot_json"]))
                sample_count += 1
        return {"sample_count": sample_count, "statistics": device_statistics.finalize(stats)}
    except (TypeError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        _set_error("device statistics query", error)
        return None


def _range_start(range_value: str) -> datetime.datetime | None:
    now = datetime.datetime.now(datetime.timezone.utc)
    durations = {
        "5m": datetime.timedelta(minutes=5),
        "1h": datetime.timedelta(hours=1),
        "24h": datetime.timedelta(hours=24),
        "7d": datetime.timedelta(days=7),
        "30d": datetime.timedelta(days=30),
    }
    duration = durations.get(range_value)
    return now - duration if duration else None


def _parse_boundary(value: str | None) -> int | None:
    return _timestamp_ms(value) if value else None


def stream_device_snapshots(
    device_id: str,
    range_value: str,
    start: str | None = None,
    end: str | None = None,
    batch_size: int = 1000,
):
    """Stream complete device observations without limiting or loading the result set."""

    init_database()
    if connection is None:
        return None
    read_connection = None
    try:
        start_ms = _parse_boundary(start)
        if start_ms is None:
            range_start = _range_start(range_value)
            start_ms = round(range_start.timestamp() * 1000) if range_start else None
        end_ms = _parse_boundary(end)
        clauses = ["profile = ?"]
        parameters = [device_id]
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            parameters.append(start_ms)
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            parameters.append(end_ms)
        database_uri = pathlib.Path(config.DATABASE_FILE).resolve().as_uri() + "?mode=ro"
        read_connection = sqlite3.connect(
            database_uri, uri=True, timeout=5, check_same_thread=False
        )
        read_connection.row_factory = sqlite3.Row
        read_connection.execute("PRAGMA query_only=ON")
        read_connection.execute("PRAGMA busy_timeout=5000")
        cursor = read_connection.execute(
            "SELECT timestamp_ms, snapshot_json FROM device_snapshots "
            f"WHERE {' AND '.join(clauses)} ORDER BY timestamp_ms ASC, id ASC",
            parameters,
        )
    except (OSError, TypeError, ValueError, sqlite3.Error) as error:
        _set_error("device snapshot stream", error)
        if read_connection is not None:
            read_connection.close()
        return None

    def generate_points():
        try:
            while rows := cursor.fetchmany(max(1, batch_size)):
                for row in rows:
                    yield {
                        "time": datetime.datetime.fromtimestamp(
                            row["timestamp_ms"] / 1000, datetime.timezone.utc
                        ).isoformat(),
                        "snapshot": json.loads(row["snapshot_json"]),
                    }
        except (OSError, sqlite3.Error, json.JSONDecodeError) as error:
            _set_error("device snapshot stream", error)
            raise
        finally:
            cursor.close()
            read_connection.close()

    return generate_points()

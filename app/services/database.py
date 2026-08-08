"""Thread-safe persistence facade for measurements, snapshots and history queries."""

import datetime
import json
import logging
import pathlib
import shutil
import sqlite3
import threading
from typing import Any

from app.core import config, device_schema, state
from app.services import database_schema, database_statistics
from app.services import syslog as syslog_service

logger = logging.getLogger(__name__)
connection = None
database_lock = threading.RLock()
last_error = None
discarded_records = 0
HISTORY_FIELDS = device_schema.AMPLIFIER_HISTORY_FIELDS
FIELD_COLUMNS = ", ".join(HISTORY_FIELDS)
FIELD_PLACEHOLDERS = ", ".join("?" for _ in HISTORY_FIELDS)
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


def _rebuild_hourly_bucket(
    opened_connection: sqlite3.Connection,
    bucket_ms: int,
) -> None:
    database_statistics.rebuild_hourly_bucket(
        opened_connection, bucket_ms, HISTORY_FIELDS, FIELD_COLUMNS, HOUR_MS
    )


def _backfill_hourly_statistics(opened_connection: sqlite3.Connection) -> None:
    database_statistics.backfill_hourly_statistics(
        opened_connection, HISTORY_FIELDS, FIELD_COLUMNS, HOUR_MS
    )


def init_database() -> None:
    """Open SQLite and prepare persistent summaries.

    Initialization is idempotent and serialized because API handlers and the serial
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
                schema_version = opened_connection.execute("PRAGMA user_version").fetchone()[0]
                _create_schema(opened_connection)
                if schema_version < 4:
                    opened_connection.execute("DELETE FROM hourly_statistics")
                    _backfill_hourly_statistics(opened_connection)
                opened_connection.execute("PRAGMA user_version=5")
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


def _field_values(fields: dict) -> list[float | None]:
    return [
        float(fields[field])
        if isinstance(fields.get(field), (int, float)) and not isinstance(fields.get(field), bool)
        else None
        for field in HISTORY_FIELDS
    ]


def _prune_to_limit(max_records: int) -> int:
    global discarded_records
    row_count = connection.execute(
        "SELECT value FROM database_metadata WHERE key = 'sample_count'"
    ).fetchone()[0]
    records_to_remove = max(0, row_count - max_records)
    if records_to_remove:
        connection.execute(
            """
            DELETE FROM samples
            WHERE id IN (SELECT id FROM samples ORDER BY id ASC LIMIT ?)
            """,
            (records_to_remove,),
        )
        remaining_bounds = connection.execute(
            "SELECT MIN(timestamp_ms), MAX(timestamp_ms) FROM samples"
        ).fetchone()
        if remaining_bounds[0] is None:
            connection.execute("DELETE FROM hourly_statistics")
        else:
            first_bucket_ms = (int(remaining_bounds[0]) // HOUR_MS) * HOUR_MS
            connection.execute(
                "DELETE FROM hourly_statistics WHERE bucket_ms <= ?",
                (first_bucket_ms,),
            )
        discarded_records += records_to_remove
    return records_to_remove


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
        discarded_records += records_to_remove
    return records_to_remove


def write_measurement(data: dict, timestamp: str | None = None) -> bool:
    """Persist one amplifier sample and update affected summary buckets.

    Returns ``False`` for a payload without supported numeric fields or when the
    database is unavailable. Details remain available in runtime status.
    """
    global last_error
    values = _field_values(data)
    if not any(value is not None for value in values):
        return False
    try:
        timestamp_value = _timestamp_ms(timestamp)
    except (TypeError, ValueError) as error:
        _set_error("timestamp parsing", error)
        return False

    init_database()
    if connection is None:
        return False
    max_records = max(0, int(state.service_settings["database_max_records"]))
    with database_lock:
        try:
            previous_last_timestamp = connection.execute(
                "SELECT MAX(timestamp_ms) FROM samples"
            ).fetchone()[0]
            connection.execute(
                f"INSERT INTO samples (timestamp_ms, {FIELD_COLUMNS}) "
                f"VALUES (?, {FIELD_PLACEHOLDERS})",
                (timestamp_value, *values),
            )
            inserted_bucket_ms = (timestamp_value // HOUR_MS) * HOUR_MS
            if previous_last_timestamp is not None:
                previous_bucket_ms = (int(previous_last_timestamp) // HOUR_MS) * HOUR_MS
                if inserted_bucket_ms > previous_bucket_ms:
                    _rebuild_hourly_bucket(connection, previous_bucket_ms)
                elif inserted_bucket_ms < previous_bucket_ms:
                    # Historical/out-of-order inserts may target a bucket that
                    # is already summarized.
                    summarized = connection.execute(
                        "SELECT 1 FROM hourly_statistics WHERE bucket_ms = ?",
                        (inserted_bucket_ms,),
                    ).fetchone()
                    if summarized:
                        _rebuild_hourly_bucket(connection, inserted_bucket_ms)
            removed = _prune_to_limit(max_records) if max_records else 0
            connection.commit()
            last_error = None
        except (OSError, sqlite3.Error) as error:
            connection.rollback()
            _set_error("write", error)
            return False

    if removed:
        syslog_service.send_warning(
            "database_record_limit_reached; "
            f"discarded_oldest_records={removed}; limit={max_records}"
        )
    return True


def write_setpoint(gain_set: float, timestamp: str | None = None) -> bool:
    """Persist one amplifier gain-setpoint event."""

    global last_error
    try:
        timestamp_value = _timestamp_ms(timestamp)
        gain_value = float(gain_set)
    except (TypeError, ValueError) as error:
        _set_error("setpoint parsing", error)
        return False
    init_database()
    if connection is None:
        return False
    with database_lock:
        try:
            connection.execute(
                "INSERT INTO setpoint_events (timestamp_ms, gain_set) VALUES (?, ?)",
                (timestamp_value, gain_value),
            )
            connection.commit()
            last_error = None
            return True
        except (OSError, sqlite3.Error) as error:
            connection.rollback()
            _set_error("setpoint write", error)
            return False


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
            connection.execute(
                "INSERT INTO device_snapshots (timestamp_ms, profile, snapshot_json) "
                "VALUES (?, ?, ?)",
                (timestamp_value, profile, payload),
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


def get_record_count() -> int:
    """Return the number of stored amplifier measurement samples."""

    init_database()
    if connection is None:
        return 0
    with database_lock:
        try:
            return connection.execute(
                "SELECT value FROM database_metadata WHERE key = 'sample_count'"
            ).fetchone()[0]
        except sqlite3.Error as error:
            _set_error("status", error)
            return 0


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
    """Prune oldest records to the active profile's configured storage limit."""

    init_database()
    if connection is None:
        return 0
    with database_lock:
        try:
            max_records = max(0, int(state.service_settings["database_max_records"]))
            if max_records and config.DEVICE_PROFILE == "fts-ls":
                removed = _prune_device_snapshots(max_records, "fts-ls")
            else:
                removed = _prune_to_limit(max_records) if max_records else 0
            connection.commit()
            return removed
        except sqlite3.Error as error:
            connection.rollback()
            _set_error("record limit update", error)
            return 0


def get_storage_status() -> dict:
    """Return database size, capacity, rate, and retention estimates."""

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
                source_table = (
                    "device_snapshots" if config.DEVICE_PROFILE == "fts-ls" else "samples"
                )
                source_filter = (
                    "WHERE profile = 'fts-ls'" if source_table == "device_snapshots" else ""
                )
                latest_timestamp = connection.execute(
                    f"SELECT MAX(timestamp_ms) FROM {source_table} {source_filter}"
                ).fetchone()[0]
                if latest_timestamp is not None:
                    recent_filter = (
                        "profile = 'fts-ls' AND " if source_table == "device_snapshots" else ""
                    )
                    recent = connection.execute(
                        f"""
                        SELECT COUNT(*) AS sample_count,
                               MIN(timestamp_ms) AS first_ms,
                               MAX(timestamp_ms) AS last_ms
                        FROM {source_table}
                        WHERE {recent_filter}timestamp_ms >= ?
                        """,
                        (int(latest_timestamp) - HOUR_MS,),
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
    records = (
        get_device_snapshot_count("fts-ls")
        if config.DEVICE_PROFILE == "fts-ls"
        else get_record_count()
    )
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


def get_runtime_status() -> dict:
    """Return readiness, record counts, retention estimate and the last SQL error."""
    init_database()
    records = (
        get_device_snapshot_count(config.DEVICE_PROFILE)
        if config.DEVICE_PROFILE == "fts-ls"
        else get_record_count()
    )
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


def _window_seconds(range_value: str) -> int:
    return {
        "5m": 1,
        "1h": 10,
        "24h": 60,
        "7d": 600,
        "30d": 1800,
        "all": 3600,
    }.get(range_value, 1)


def _parse_boundary(value: str | None) -> int | None:
    return _timestamp_ms(value) if value else None


def query_history(
    range_value: str,
    start: str | None = None,
    end: str | None = None,
    include_metadata: bool = False,
):
    """Return bounded amplifier history using dynamic time-window aggregation."""
    init_database()
    if connection is None:
        return None

    try:
        start_ms = _parse_boundary(start)
        if start_ms is None:
            range_start = _range_start(range_value)
            start_ms = round(range_start.timestamp() * 1000) if range_start else None
        end_ms = _parse_boundary(end)
        window_ms = _window_seconds(range_value) * 1000

        clauses = []
        parameters = []
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            parameters.append(start_ms)
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            parameters.append(end_ms)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with database_lock:
            bounds = connection.execute(
                f"SELECT MIN(timestamp_ms) AS first_ms, MAX(timestamp_ms) AS last_ms "
                f"FROM samples {where_clause}",
                parameters,
            ).fetchone()
        if bounds["first_ms"] is None:
            empty_result = {"points": [], "sample_count": 0, "aggregation_seconds": 0}
            return empty_result if include_metadata else []

        first_ms = int(bounds["first_ms"])
        last_ms = int(bounds["last_ms"])
        span_ms = max(1, last_ms - first_ms + 1)
        dynamic_window_ms = (span_ms + config.HISTORY_MAX_POINTS - 1) // config.HISTORY_MAX_POINTS
        window_ms = max(window_ms, dynamic_window_ms)
        query_clauses = list(clauses)
        query_parameters = list(parameters)
        if end_ms is None:
            query_clauses.append("timestamp_ms <= ?")
            query_parameters.append(last_ms)
        query_where_clause = f"WHERE {' AND '.join(query_clauses)}" if query_clauses else ""
        averages = ", ".join(f"AVG({field}) AS {field}" for field in HISTORY_FIELDS)
        sql = f"""
            SELECT ((timestamp_ms - ?) / ?) * ? + ? AS bucket_ms,
                   COUNT(*) AS bucket_sample_count,
                   {averages}
            FROM samples
            {query_where_clause}
            GROUP BY bucket_ms
            ORDER BY bucket_ms ASC
        """
        with database_lock:
            rows = connection.execute(
                sql,
                (first_ms, window_ms, window_ms, first_ms, *query_parameters),
            ).fetchall()
    except (TypeError, ValueError, sqlite3.Error) as error:
        _set_error("history query", error)
        return None

    points = []
    for row in rows:
        point = {
            "time": datetime.datetime.fromtimestamp(
                row["bucket_ms"] / 1000, datetime.timezone.utc
            ).isoformat()
        }
        for field in HISTORY_FIELDS:
            if row[field] is not None:
                point[field] = row[field]
        points.append(point)
    result = {
        "points": points,
        "sample_count": sum(row["bucket_sample_count"] for row in rows),
        "aggregation_seconds": window_ms / 1000,
    }
    return result if include_metadata else points


def _query_raw_statistics_segment(
    opened_connection: sqlite3.Connection,
    start_ms: int,
    end_ms: int,
) -> dict:
    return database_statistics.query_raw_statistics_segment(
        opened_connection, start_ms, end_ms, HISTORY_FIELDS, FIELD_COLUMNS
    )


def _merge_statistics_segments(segments: list[dict]) -> dict:
    return database_statistics.merge_statistics_segments(segments, HISTORY_FIELDS)


def query_statistics(range_value: str, start: str | None = None, end: str | None = None):
    """Calculate exact statistics using hourly summaries plus raw boundary rows."""
    init_database()
    if connection is None:
        return None

    read_connection = None
    try:
        requested_start_ms = _parse_boundary(start)
        if requested_start_ms is None:
            range_start = _range_start(range_value)
            requested_start_ms = round(range_start.timestamp() * 1000) if range_start else None
        requested_end_ms = _parse_boundary(end)

        database_uri = pathlib.Path(config.DATABASE_FILE).resolve().as_uri() + "?mode=ro"
        read_connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=5,
            check_same_thread=False,
        )
        read_connection.row_factory = sqlite3.Row
        read_connection.execute("PRAGMA query_only=ON")
        read_connection.execute("PRAGMA busy_timeout=5000")
        bounds = read_connection.execute(
            "SELECT MIN(timestamp_ms), MAX(timestamp_ms) FROM samples"
        ).fetchone()
        if bounds[0] is None:
            read_connection.close()
            return {"sample_count": 0, "statistics": {}}

        start_ms = max(
            int(bounds[0]),
            requested_start_ms if requested_start_ms is not None else int(bounds[0]),
        )
        end_ms = min(
            int(bounds[1]),
            requested_end_ms if requested_end_ms is not None else int(bounds[1]),
        )
        if start_ms > end_ms:
            read_connection.close()
            return {"sample_count": 0, "statistics": {}}

        first_full_bucket = ((start_ms + HOUR_MS - 1) // HOUR_MS) * HOUR_MS
        last_full_bucket = (((end_ms + 1) // HOUR_MS) - 1) * HOUR_MS
        segments = []
        if first_full_bucket > last_full_bucket:
            segments.append(_query_raw_statistics_segment(read_connection, start_ms, end_ms))
        else:
            if start_ms < first_full_bucket:
                segments.append(
                    _query_raw_statistics_segment(read_connection, start_ms, first_full_bucket - 1)
                )

            summaries = {
                int(row["bucket_ms"]): {
                    "sample_count": int(row["sample_count"]),
                    "statistics": json.loads(row["statistics_json"]),
                }
                for row in read_connection.execute(
                    """
                    SELECT bucket_ms, sample_count, statistics_json
                    FROM hourly_statistics
                    WHERE bucket_ms >= ? AND bucket_ms <= ?
                    ORDER BY bucket_ms ASC
                    """,
                    (first_full_bucket, last_full_bucket),
                )
            }
            bucket_ms = first_full_bucket
            while bucket_ms <= last_full_bucket:
                segment = summaries.get(bucket_ms)
                if segment is None:
                    segment = _query_raw_statistics_segment(
                        read_connection, bucket_ms, bucket_ms + HOUR_MS - 1
                    )
                segments.append(segment)
                bucket_ms += HOUR_MS

            suffix_start_ms = last_full_bucket + HOUR_MS
            if suffix_start_ms <= end_ms:
                segments.append(
                    _query_raw_statistics_segment(read_connection, suffix_start_ms, end_ms)
                )

        result = _merge_statistics_segments(segments)
        read_connection.close()
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        _set_error("statistics query", error)
        if read_connection is not None:
            read_connection.close()
        return None


def query_raw_history(range_value: str, start: str | None = None, end: str | None = None):
    """Return every stored sample in the selected period without aggregation."""
    init_database()
    if connection is None:
        return None

    try:
        start_ms = _parse_boundary(start)
        if start_ms is None:
            range_start = _range_start(range_value)
            start_ms = round(range_start.timestamp() * 1000) if range_start else None
        end_ms = _parse_boundary(end)

        clauses = []
        parameters = []
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            parameters.append(start_ms)
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            parameters.append(end_ms)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT timestamp_ms, {FIELD_COLUMNS}
            FROM samples
            {where_clause}
            ORDER BY timestamp_ms ASC, id ASC
        """
        with database_lock:
            rows = connection.execute(sql, parameters).fetchall()
    except (TypeError, ValueError, sqlite3.Error) as error:
        _set_error("raw history query", error)
        return None

    points = []
    for row in rows:
        point = {
            "time": datetime.datetime.fromtimestamp(
                row["timestamp_ms"] / 1000, datetime.timezone.utc
            ).isoformat()
        }
        for field in HISTORY_FIELDS:
            if row[field] is not None:
                point[field] = row[field]
        points.append(point)
    return points


def stream_raw_history(
    range_value: str,
    start: str | None = None,
    end: str | None = None,
    batch_size: int = 1000,
):
    """Yield raw samples from an independent read-only connection in batches.

    The generator owns and closes its connection, preventing long CSV exports from
    holding the writer lock.
    """
    init_database()
    if connection is None:
        return None

    try:
        start_ms = _parse_boundary(start)
        if start_ms is None:
            range_start = _range_start(range_value)
            start_ms = round(range_start.timestamp() * 1000) if range_start else None
        end_ms = _parse_boundary(end)

        clauses = []
        parameters = []
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            parameters.append(start_ms)
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            parameters.append(end_ms)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT timestamp_ms, {FIELD_COLUMNS}
            FROM samples
            {where_clause}
            ORDER BY timestamp_ms ASC, id ASC
        """

        database_uri = pathlib.Path(config.DATABASE_FILE).resolve().as_uri() + "?mode=ro"
        read_connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=5,
            check_same_thread=False,
        )
        read_connection.row_factory = sqlite3.Row
        read_connection.execute("PRAGMA query_only=ON")
        read_connection.execute("PRAGMA busy_timeout=5000")
        cursor = read_connection.execute(sql, parameters)
    except (OSError, TypeError, ValueError, sqlite3.Error) as error:
        _set_error("raw history stream", error)
        if "read_connection" in locals():
            read_connection.close()
        return None

    def generate_points():
        """Yield raw history rows while holding the database lock safely."""

        try:
            while rows := cursor.fetchmany(max(1, batch_size)):
                for row in rows:
                    point = {
                        "time": datetime.datetime.fromtimestamp(
                            row["timestamp_ms"] / 1000, datetime.timezone.utc
                        ).isoformat()
                    }
                    for field in HISTORY_FIELDS:
                        if row[field] is not None:
                            point[field] = row[field]
                    yield point
        except (OSError, sqlite3.Error) as error:
            _set_error("raw history stream", error)
            raise
        finally:
            cursor.close()
            read_connection.close()

    return generate_points()

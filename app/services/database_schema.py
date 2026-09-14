"""SQLite schema creation for the Amp Panel persistence service."""

import sqlite3

from app.core import device_schema


def create_schema(connection: sqlite3.Connection) -> None:
    """Create current tables, indexes, metadata counters and count triggers.

    The function is idempotent and must run inside the caller's transaction.
    """
    measurement_columns = ",\n            ".join(
        f"{field} REAL" for field in device_schema.AMPLIFIER_HISTORY_FIELDS
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            {measurement_columns}
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_samples_timestamp ON samples (timestamp_ms)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS setpoint_events (
            id INTEGER PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            gain_set REAL NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_setpoints_timestamp ON setpoint_events (timestamp_ms)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS database_metadata (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        ) WITHOUT ROWID
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO database_metadata (key, value)
        VALUES ('sample_count', (SELECT COUNT(*) FROM samples))
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS samples_count_after_insert
        AFTER INSERT ON samples
        BEGIN
            UPDATE database_metadata SET value = value + 1
            WHERE key = 'sample_count';
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS samples_count_after_delete
        AFTER DELETE ON samples
        BEGIN
            UPDATE database_metadata SET value = value - 1
            WHERE key = 'sample_count';
        END
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS hourly_statistics (
            bucket_ms INTEGER PRIMARY KEY,
            sample_count INTEGER NOT NULL,
            statistics_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS device_snapshots (
            id INTEGER PRIMARY KEY,
            timestamp_ms INTEGER NOT NULL,
            profile TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_snapshots_timestamp "
        "ON device_snapshots (timestamp_ms)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_snapshots_device_time "
        "ON device_snapshots (profile, timestamp_ms, id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS device_hourly_statistics (
            device_id TEXT NOT NULL,
            bucket_ms INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            statistics_json TEXT NOT NULL,
            PRIMARY KEY (device_id, bucket_ms)
        )"""
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO database_metadata (key, value)
        VALUES ('device_snapshot_count', (SELECT COUNT(*) FROM device_snapshots))
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS device_snapshots_count_after_insert
        AFTER INSERT ON device_snapshots
        BEGIN
            UPDATE database_metadata SET value = value + 1
            WHERE key = 'device_snapshot_count';
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS device_snapshots_count_after_delete
        AFTER DELETE ON device_snapshots
        BEGIN
            UPDATE database_metadata SET value = value - 1
            WHERE key = 'device_snapshot_count';
        END
        """
    )

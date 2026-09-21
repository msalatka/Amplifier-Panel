"""SQLite schema for XML snapshots. Existing legacy tables remain untouched."""

import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
    """Create XML storage without deleting data from older installations."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS database_metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL) WITHOUT ROWID"
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

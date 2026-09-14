"""Device-independent numeric aggregation for complete JSON observations."""

import json
import math


def scalar_fields(snapshot: dict) -> dict[str, str | int | float | bool | None]:
    """Flatten a snapshot for stable, long-form CSV rows."""

    fields = {}

    def visit(value, prefix: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                name = item.get("name") if isinstance(item, dict) else None
                segment = str(name) if name else str(index)
                visit(item, f"{prefix}.{segment}" if prefix else segment)
        elif prefix:
            fields[prefix] = value

    visit(snapshot, "")
    return fields


def numeric_fields(snapshot: dict) -> dict[str, float]:
    """Flatten finite numeric leaves; named modules keep stable metric keys."""

    fields: dict[str, float] = {}

    def visit(value, prefix: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"connectors", "last_command"}:
                    continue
                visit(item, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                name = item.get("name") if isinstance(item, dict) else None
                segment = str(name) if name else str(index)
                visit(item, f"{prefix}.{segment}" if prefix else segment)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if math.isfinite(numeric) and prefix:
                fields[prefix] = numeric

    visit(snapshot, "")
    return fields


def add_snapshot(stats: dict, snapshot: dict) -> None:
    """Add one observation to count/sum/squared-sum/min/max accumulators."""

    for key, value in numeric_fields(snapshot).items():
        metric = stats.setdefault(
            key,
            {"count": 0, "sum": 0.0, "sum_squares": 0.0, "min": value, "max": value},
        )
        metric["count"] += 1
        metric["sum"] += value
        metric["sum_squares"] += value * value
        metric["min"] = min(metric["min"], value)
        metric["max"] = max(metric["max"], value)


def merge_stats(target: dict, source: dict) -> None:
    """Combine persisted and raw accumulators without losing sample counts."""

    for key, metric in source.items():
        existing = target.get(key)
        if existing is None:
            target[key] = dict(metric)
            continue
        existing["count"] += metric["count"]
        existing["sum"] += metric["sum"]
        existing["sum_squares"] += metric["sum_squares"]
        existing["min"] = min(existing["min"], metric["min"])
        existing["max"] = max(existing["max"], metric["max"])


def finalize(stats: dict) -> dict:
    """Calculate population averages and standard deviations."""

    result = {}
    for key, metric in stats.items():
        count = metric["count"]
        if not count:
            continue
        average = metric["sum"] / count
        variance = max(0.0, metric["sum_squares"] / count - average * average)
        result[key] = {
            "count": count,
            "min": metric["min"],
            "max": metric["max"],
            "average": average,
            "standard_deviation": math.sqrt(variance),
        }
    return result


def rebuild_hour(connection, device_id: str, bucket_ms: int, hour_ms: int) -> None:
    """Recalculate a completed hour from the source observations."""

    stats = {}
    count = 0
    rows = connection.execute(
        "SELECT snapshot_json FROM device_snapshots "
        "WHERE profile = ? AND timestamp_ms >= ? AND timestamp_ms < ? "
        "ORDER BY timestamp_ms, id",
        (device_id, bucket_ms, bucket_ms + hour_ms),
    )
    for row in rows:
        add_snapshot(stats, json.loads(row[0]))
        count += 1
    if count:
        connection.execute(
            "INSERT INTO device_hourly_statistics (device_id, bucket_ms, sample_count, statistics_json) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(device_id, bucket_ms) DO UPDATE SET "
            "sample_count = excluded.sample_count, statistics_json = excluded.statistics_json",
            (device_id, bucket_ms, count, json.dumps(stats, separators=(",", ":"))),
        )
    else:
        connection.execute(
            "DELETE FROM device_hourly_statistics WHERE device_id = ? AND bucket_ms = ?",
            (device_id, bucket_ms),
        )

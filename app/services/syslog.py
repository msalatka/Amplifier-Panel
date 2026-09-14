"""Structured syslog emission, warning-history parsing, and audit events."""

import datetime
import gzip
import json
import pathlib
import socket
import threading
import zoneinfo

from app.core import config

FACILITY_LOCAL0 = config.SYSLOG_FACILITY
SEVERITY_WARNING = 4
SEVERITY_INFO = 6
_warning_file_cache: dict[pathlib.Path, tuple[tuple[int, int], list[dict]]] = {}
_warning_cache_lock = threading.Lock()


def local_now() -> datetime.datetime:
    """Return the current time in the configured syslog timezone."""

    try:
        timezone = zoneinfo.ZoneInfo(config.SYSLOG_TIMEZONE)
    except zoneinfo.ZoneInfoNotFoundError:
        timezone = datetime.timezone.utc
    return datetime.datetime.now(timezone)


def send_syslog(message: str, severity: int) -> None:
    """Send one RFC-style UDP syslog payload when logging is enabled."""

    if not config.SYSLOG_ENABLED:
        return

    priority = FACILITY_LOCAL0 * 8 + severity
    timestamp = local_now().strftime("%b %d %H:%M:%S")
    hostname = socket.gethostname()
    payload = (
        f"<{priority}>{timestamp} {hostname} {config.SYSLOG_APP_NAME}: "
        f"device={config.DEVICE_NAME}; {message}"
    )

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload.encode("utf-8"), (config.SYSLOG_HOST, config.SYSLOG_PORT))
    except OSError as error:
        print(f"Syslog send failed: {error}")


def send_warning(message: str) -> None:
    """Send a plain application warning at warning severity."""

    send_syslog(f"warning; {message}", SEVERITY_WARNING)


def send_warning_event(event: str, warning: dict) -> None:
    """Serialize and send a structured warning-open or warning-clear event."""

    payload = {
        "event": event.upper(),
        "event_time": warning.get("event_time") or warning.get("time"),
        "field": warning.get("field"),
        "kind": warning.get("kind"),
        "label": warning.get("label") or warning.get("field"),
        "value": warning.get("value"),
        "target": warning.get("target"),
        "delta": warning.get("delta"),
        "allowed": warning.get("allowed"),
        "message": warning.get("message", ""),
    }
    if warning.get("duration_seconds") is not None:
        payload["duration_seconds"] = warning["duration_seconds"]
    severity = SEVERITY_WARNING if event.upper() == "OPEN" else SEVERITY_INFO
    send_syslog(
        f"warning; {json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}",
        severity,
    )


def send_lifecycle(event: str, **fields: object) -> None:
    """Send an application lifecycle or heartbeat event at info severity."""

    field_text = "".join(f"; {name}={value}" for name, value in fields.items())
    send_syslog(f"lifecycle; event={event}{field_text}", SEVERITY_INFO)


def get_syslog_log_path() -> pathlib.Path:
    """Return the configured local file used for log export and history."""

    return pathlib.Path(config.SYSLOG_EXPORT_FILE)


def _parse_event_time(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def parse_warning_log_line(line: str) -> dict | None:
    """Parse one structured warning event from a syslog-formatted line."""

    marker = "; warning; "
    if marker not in line:
        return None
    payload = line.split(marker, 1)[1].strip()
    if payload.startswith("{"):
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict) or event.get("event") not in {"OPEN", "CLEARED"}:
            return None
        if _parse_event_time(event.get("event_time")) is None:
            return None
        return event
    return None


def _warning_log_paths(path: pathlib.Path) -> list[pathlib.Path]:
    candidates = [path]
    candidates.extend(path.parent.glob(f"{path.name}.*"))
    return [
        candidate
        for candidate in candidates
        if candidate.is_file()
        and (
            candidate == path
            or candidate.suffix == ".gz"
            or candidate.name[len(path.name) + 1 :].isdigit()
        )
    ]


def read_warning_history(
    start: datetime.datetime | None = None,
    end: datetime.datetime | None = None,
    field: str = "",
    status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Read, filter, sort, and paginate warning events across rotated logs."""

    events = []
    paths = _warning_log_paths(get_syslog_log_path())
    unreadable_files = 0
    active_paths = set(paths)
    with _warning_cache_lock:
        for cached_path in set(_warning_file_cache) - active_paths:
            _warning_file_cache.pop(cached_path, None)

    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            unreadable_files += 1
            continue
        signature = (stat.st_mtime_ns, stat.st_size)
        with _warning_cache_lock:
            cached = _warning_file_cache.get(path)
        if cached is not None and cached[0] == signature:
            file_events = cached[1]
        else:
            file_events = []
            opener = gzip.open if path.suffix == ".gz" else open
            try:
                with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        event = parse_warning_log_line(line)
                        if event is not None:
                            file_events.append(event)
            except (OSError, EOFError):
                unreadable_files += 1
                continue
            with _warning_cache_lock:
                _warning_file_cache[path] = (signature, file_events)

        for event in file_events:
            event_time = _parse_event_time(event.get("event_time"))
            if event_time is None:
                continue
            if start is not None and event_time < start:
                continue
            if end is not None and event_time > end:
                continue
            if field and event.get("field") != field:
                continue
            if status and str(event.get("event", "")).lower() != status:
                continue
            events.append(event)

    events.sort(
        key=lambda event: (
            _parse_event_time(event.get("event_time"))
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        ),
        reverse=True,
    )
    total = len(events)
    return {
        "events": events[offset : offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "source_available": bool(paths),
        "unreadable_files": unreadable_files,
    }


def send_audit(action: str, username: str, ip_address: str, details: str = "") -> None:
    """Send a structured security audit event without logging credentials."""

    detail_text = f"; details={details}" if details else ""
    message = f"audit; user={username}; ip={ip_address}; action={action}{detail_text}"

    send_syslog(
        message,
        SEVERITY_INFO,
    )

"""Shared runtime state and atomic persistence of operator-managed settings."""

import datetime
import json
import pathlib
import threading

from app.core import config, device_schema, validation
from app.core.fts_types import FtsStatus

persist_lock = threading.Lock()


DEFAULT_DASHBOARD_SETTINGS = {
    "gain_tolerance": 0.25,
    "warn_limits": {
        field: {"min": None, "max": None} for field in device_schema.AMPLIFIER_WARNING_FIELDS
    },
}

DEFAULT_SNMP_SETTINGS = {
    "enabled": False,
    "port": config.SNMP_PORT,
    "community": config.SNMP_COMMUNITY,
    "trap_host": "127.0.0.1",
    "trap_port": 162,
}

DEFAULT_SERVICE_SETTINGS = {
    "syslog_heartbeat_seconds": config.SYSLOG_HEARTBEAT_SECONDS,
    "database_max_records": config.DATABASE_MAX_RECORDS,
    "serial_port": config.SERIAL_PORT,
}


def empty_fts_ls_status() -> FtsStatus:
    """Return a complete, independent snapshot for an unpolled FTS-LS station.

    Keeping all seven physical slots in the initial value gives the API and UI a
    stable shape before the first successful serial poll.
    """
    return device_schema.empty_fts_ls_status()


def load_persisted_state() -> dict:
    """Load persisted JSON state, returning defaults after any read failure."""

    path = pathlib.Path(config.PERSISTED_STATE_FILE)

    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def merge_dashboard_settings(saved_settings: dict | None) -> dict:
    """Merge and validate persisted warning settings against current defaults."""

    settings = json.loads(json.dumps(DEFAULT_DASHBOARD_SETTINGS))

    if not isinstance(saved_settings, dict):
        return settings

    if "gain_tolerance" in saved_settings:
        try:
            settings["gain_tolerance"] = float(saved_settings["gain_tolerance"])
        except (TypeError, ValueError, OverflowError):
            pass

    saved_limits = saved_settings.get("warn_limits")
    if isinstance(saved_limits, dict):
        for field, limits in saved_limits.items():
            if field not in settings["warn_limits"] or not isinstance(limits, dict):
                continue

            for side in ("min", "max"):
                if side in limits:
                    value = limits[side]
                    try:
                        settings["warn_limits"][field][side] = (
                            None if value is None else float(value)
                        )
                    except (TypeError, ValueError, OverflowError):
                        pass

    try:
        return validation.validated_dashboard_settings(
            DEFAULT_DASHBOARD_SETTINGS,
            settings["gain_tolerance"],
            settings["warn_limits"],
        )
    except ValueError:
        return json.loads(json.dumps(DEFAULT_DASHBOARD_SETTINGS))


def merge_last_known_gain_set(saved_gain_set: object) -> float:
    """Validate a persisted gain setpoint or choose an in-range fallback."""

    try:
        return validation.validate_gain_set(
            saved_gain_set,
            config.GAIN_SET_MIN,
            config.GAIN_SET_MAX,
        )
    except ValueError:
        fallback = 15.0
        if not config.GAIN_SET_MIN <= fallback <= config.GAIN_SET_MAX:
            fallback = (config.GAIN_SET_MIN + config.GAIN_SET_MAX) / 2.0
        return fallback


def access_user_public(user: dict) -> dict:
    """Return the non-sensitive fields exposed for a local access user."""

    return {
        "username": user["username"],
        "role": user["role"],
        "active": bool(user["active"]),
    }


def merge_access_users(saved_users: list[dict] | None) -> list[dict]:
    """Normalize persisted access users or create the initial administrator."""

    merged_users = []
    seen_usernames = set()

    for user in saved_users if isinstance(saved_users, list) else []:
        if not isinstance(user, dict):
            continue

        username = str(user.get("username", "")).strip()
        if not username or username in seen_usernames:
            continue

        merged_users.append(
            {
                "username": username,
                "role": str(user.get("role", "Operator")).strip() or "Operator",
                "active": bool(user.get("active", True)),
            }
        )
        seen_usernames.add(username)

    if merged_users:
        return merged_users

    return [
        {
            "username": config.INITIAL_ADMIN_USERNAME,
            "role": "Administrator",
            "active": True,
        }
    ]


def merge_snmp_settings(saved_settings: dict | None) -> dict:
    """Merge persisted SNMP values while enforcing server-owned settings."""

    settings = DEFAULT_SNMP_SETTINGS.copy()
    if isinstance(saved_settings, dict):
        settings.update({key: saved_settings[key] for key in settings if key in saved_settings})
    settings["port"] = config.SNMP_PORT
    if settings.get("community") in {"", "public"}:
        settings["community"] = config.SNMP_COMMUNITY
    return settings


def merge_service_settings(saved_settings: dict | None) -> dict:
    """Normalize persisted heartbeat, database, and serial-port settings."""

    settings = DEFAULT_SERVICE_SETTINGS.copy()
    if isinstance(saved_settings, dict):
        for key in ("syslog_heartbeat_seconds", "database_max_records"):
            if key in saved_settings:
                try:
                    settings[key] = int(saved_settings[key])
                except (TypeError, ValueError):
                    pass
        if isinstance(saved_settings.get("serial_port"), str):
            settings["serial_port"] = saved_settings["serial_port"]
    settings["syslog_heartbeat_seconds"] = max(0, settings["syslog_heartbeat_seconds"])
    settings["database_max_records"] = max(0, settings["database_max_records"])
    return settings


persisted_state = load_persisted_state()


def save_persisted_state() -> None:
    """Atomically write all operator-managed settings with restricted permissions."""

    path = pathlib.Path(config.PERSISTED_STATE_FILE)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "last_known_gain_set": float(last_known_gain_set),
        "dashboard_settings": dashboard_settings,
        "access_users": access_users,
        "snmp_settings": snmp_settings,
        "service_settings": service_settings,
    }
    with persist_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.chmod(0o600)
        temporary_path.replace(path)


def save_persisted_gain_set(gain_set: float) -> None:
    """Validate and persist the last amplifier gain setpoint."""

    global last_known_gain_set
    last_known_gain_set = validation.validate_gain_set(
        gain_set,
        config.GAIN_SET_MIN,
        config.GAIN_SET_MAX,
    )
    save_persisted_state()


def save_persisted_dashboard_settings() -> None:
    """Persist the current dashboard warning settings."""

    save_persisted_state()


def save_persisted_access_users() -> None:
    """Persist the current local authorization records."""

    save_persisted_state()


latest_data = {}
latest_snmp_data = {}
fts_ls_status: FtsStatus = empty_fts_ls_status()
serial_connected = False
serial_error = None
last_update = None
last_known_gain_set = merge_last_known_gain_set(persisted_state.get("last_known_gain_set", 15.0))

serial_port = None

state_lock = threading.Lock()
serial_lock = threading.Lock()
stop_event = threading.Event()
serial_reconnect_event = threading.Event()

active_warnings = {}
acknowledged_warning_keys = set()
app_started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
auth_sessions = {}
login_failures = {}

dashboard_settings = merge_dashboard_settings(persisted_state.get("dashboard_settings"))
access_users = merge_access_users(persisted_state.get("access_users"))
snmp_settings = merge_snmp_settings(persisted_state.get("snmp_settings"))
service_settings = merge_service_settings(persisted_state.get("service_settings"))

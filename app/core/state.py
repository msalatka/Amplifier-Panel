"""Shared runtime state and atomic persistence of operator-managed settings."""

import copy
import datetime
import json
import pathlib
import threading

from app.core import config, passwords

persist_lock = threading.Lock()


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
}

DEFAULT_DEVICE_LIVE_FIELDS = {
    "oba": ["oba:gain", "oba:temperature"],
    "oba3": ["oba3:Gain", "oba3:Temp"],
}
DEVICE_LIVE_FIELDS_VERSION = 2


def load_persisted_state() -> dict:
    """Load persisted JSON state, returning defaults after any read failure."""

    path = pathlib.Path(config.PERSISTED_STATE_FILE)

    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def access_user_public(user: dict) -> dict:
    """Return the non-sensitive fields exposed for a local access user."""

    return {
        "username": user["username"],
        "role": user["role"],
        "active": bool(user["active"]),
        "password_set": passwords.password_is_usable(
            user.get("password_hash"), user.get("password_salt")
        ),
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

        merged_user = {
            "username": username,
            "role": str(user.get("role", "Operator")).strip() or "Operator",
            "active": bool(user.get("active", True)),
        }
        if passwords.password_is_usable(user.get("password_hash"), user.get("password_salt")):
            merged_user["password_hash"] = user["password_hash"]
            merged_user["password_salt"] = user["password_salt"]
        merged_users.append(merged_user)
        seen_usernames.add(username)

    initial_user = {
        "username": config.INITIAL_ADMIN_USERNAME,
        "role": "Administrator",
        "active": True,
    }

    def apply_initial_local_password() -> None:
        """Attach the configured bootstrap password hash to the initial administrator."""

        if not passwords.password_is_usable(
            config.INITIAL_ADMIN_PASSWORD_HASH, config.INITIAL_ADMIN_PASSWORD_SALT
        ):
            raise RuntimeError(
                "Local authentication requires an initial administrator password. "
                "Run 'sudo amp-panel configure'."
            )
        initial_user["password_hash"] = config.INITIAL_ADMIN_PASSWORD_HASH
        initial_user["password_salt"] = config.INITIAL_ADMIN_PASSWORD_SALT

    if merged_users:
        if config.AUTH_MODE == "local" and not any(
            passwords.password_is_usable(user.get("password_hash"), user.get("password_salt"))
            for user in merged_users
        ):
            apply_initial_local_password()
            for user in merged_users:
                if user["username"] == config.INITIAL_ADMIN_USERNAME:
                    user.update(initial_user)
                    break
            else:
                merged_users.append(initial_user)
        return merged_users
    if config.AUTH_MODE == "local":
        apply_initial_local_password()
    return [initial_user]


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
    """Normalize persisted heartbeat and database settings."""

    settings = DEFAULT_SERVICE_SETTINGS.copy()
    if isinstance(saved_settings, dict):
        for key in ("syslog_heartbeat_seconds", "database_max_records"):
            if key in saved_settings:
                try:
                    settings[key] = int(saved_settings[key])
                except (TypeError, ValueError):
                    pass
    settings["syslog_heartbeat_seconds"] = max(0, settings["syslog_heartbeat_seconds"])
    settings["database_max_records"] = max(0, settings["database_max_records"])
    return settings


def merge_device_live_fields(
    saved_fields: dict | None, version: int | None = None
) -> dict[str, list[str]]:
    """Normalize globally selected amplifier fields while preserving defaults."""
    result = copy.deepcopy(DEFAULT_DEVICE_LIVE_FIELDS)
    if not isinstance(saved_fields, dict):
        return result
    for device_id in result:
        fields = saved_fields.get(device_id)
        if isinstance(fields, list):
            result[device_id] = list(
                dict.fromkeys(field for field in fields if isinstance(field, str) and field)
            )[:64]
            if version != DEVICE_LIVE_FIELDS_VERSION:
                gain = DEFAULT_DEVICE_LIVE_FIELDS[device_id][0]
                if gain not in result[device_id]:
                    result[device_id].insert(0, gain)
    return result


persisted_state = load_persisted_state()


def save_persisted_state() -> None:
    """Atomically write all operator-managed settings with restricted permissions."""

    path = pathlib.Path(config.PERSISTED_STATE_FILE)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "access_users": access_users,
        "snmp_settings": snmp_settings,
        "service_settings": service_settings,
        "device_live_fields": device_live_fields,
        "device_live_fields_version": DEVICE_LIVE_FIELDS_VERSION,
    }
    with persist_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_path.chmod(0o600)
        temporary_path.replace(path)


def save_persisted_access_users() -> None:
    """Persist the current local authorization records."""

    save_persisted_state()


latest_snmp_data = {}
device_live = {
    device_id: {
        "connected": False,
        "error": None,
        "last_update": None,
        "data": {},
    }
    for device_id in config.ENABLED_DEVICES
}


state_lock = threading.Lock()
stop_event = threading.Event()

app_started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
auth_sessions = {}
login_failures = {}
_UNSET = object()


def update_device_live(
    device_id: str,
    *,
    connected: bool | None = None,
    error: str | None | object = _UNSET,
    last_update: str | None = None,
    data: dict | None = None,
) -> None:
    """Publish one device's status without changing the other devices."""

    with state_lock:
        live = device_live[device_id]
        if connected is not None:
            live["connected"] = connected
        if error is not _UNSET:
            live["error"] = error
        if last_update is not None:
            live["last_update"] = last_update
        if data is not None:
            live["data"] = copy.deepcopy(data)


def snapshot_device_live(device_id: str) -> dict:
    """Return a detached status snapshot for an API response."""

    with state_lock:
        live = device_live[device_id]
        return {**live, "data": copy.deepcopy(live["data"])}


access_users = merge_access_users(persisted_state.get("access_users"))
snmp_settings = merge_snmp_settings(persisted_state.get("snmp_settings"))
service_settings = merge_service_settings(persisted_state.get("service_settings"))
device_live_fields = merge_device_live_fields(
    persisted_state.get("device_live_fields"),
    persisted_state.get("device_live_fields_version"),
)

"""Validated process configuration loaded from environment variables."""

import math
import os
import re


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if math.isfinite(parsed) else default


def _gain_bounds(
    minimum_value: str | None,
    maximum_value: str | None,
) -> tuple[float, float]:
    if minimum_value is None and maximum_value is None:
        # Allows isolated unit tests and direct developer imports. Production
        # configuration should set both variables explicitly.
        return -100.0, 100.0
    if not minimum_value or not maximum_value:
        raise RuntimeError("GAIN_SET_MIN and GAIN_SET_MAX must both be configured.")
    try:
        minimum = float(minimum_value)
        maximum = float(maximum_value)
    except ValueError as exc:
        raise RuntimeError("Gain setpoint limits must be numbers.") from exc
    if not (math.isfinite(minimum) and math.isfinite(maximum) and minimum < maximum):
        raise RuntimeError(
            "Gain setpoint limits must be finite and GAIN_SET_MIN must be lower than GAIN_SET_MAX."
        )
    return minimum, maximum


def _initial_admin_username(value: str | None) -> str:
    username = "admin" if value is None else value.strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,128}", username):
        raise RuntimeError(
            "INITIAL_ADMIN_USERNAME may contain 1-128 letters, digits, dots, underscores, @ or hyphens."
        )
    return username


def _device_profile(value: str | None) -> str:
    profile = (value or "amplifier").strip().lower()
    aliases = {
        "amp": "amplifier",
        "optical-amplifier": "amplifier",
        "fts_ls": "fts-ls",
        "laser-station": "fts-ls",
    }
    profile = aliases.get(profile, profile)
    if profile not in {"amplifier", "fts-ls"}:
        raise RuntimeError("DEVICE_PROFILE must be 'amplifier' or 'fts-ls'.")
    return profile


DEVICE_PROFILE = _device_profile(os.getenv("DEVICE_PROFILE"))
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUDRATE = _env_int(
    "SERIAL_BAUDRATE",
    115200 if DEVICE_PROFILE == "fts-ls" else 9600,
)
FTS_LS_USERNAME = os.getenv("FTS_LS_USERNAME", "appadmin")
FTS_LS_PASSWORD = os.getenv("FTS_LS_PASSWORD", "")
FTS_LS_POLL_SECONDS = max(2, _env_int("FTS_LS_POLL_SECONDS", 10))
FTS_LS_FREQUENCY_MIN_GHZ = _env_float("FTS_LS_FREQUENCY_MIN_GHZ", 194392.6)
FTS_LS_FREQUENCY_MAX_GHZ = _env_float("FTS_LS_FREQUENCY_MAX_GHZ", 194405.6)
if FTS_LS_FREQUENCY_MIN_GHZ >= FTS_LS_FREQUENCY_MAX_GHZ:
    raise RuntimeError("FTS-LS laser frequency minimum must be lower than maximum.")
GAIN_SET_MIN, GAIN_SET_MAX = _gain_bounds(
    os.getenv("GAIN_SET_MIN"),
    os.getenv("GAIN_SET_MAX"),
)

DEVICE_NAME = os.getenv("DEVICE_NAME", "unconfigured-device")
INITIAL_ADMIN_USERNAME = _initial_admin_username(os.getenv("INITIAL_ADMIN_USERNAME"))
INITIAL_ADMIN_PASSWORD_HASH = os.getenv("INITIAL_ADMIN_PASSWORD_HASH", "")
INITIAL_ADMIN_PASSWORD_SALT = os.getenv("INITIAL_ADMIN_PASSWORD_SALT", "")

DATABASE_FILE = os.getenv("DATABASE_FILE", "/var/lib/amp-panel/measurements.db")
DATABASE_MAX_RECORDS = max(0, _env_int("DATABASE_MAX_RECORDS", 0))
HISTORY_MAX_POINTS = max(100, _env_int("HISTORY_MAX_POINTS", 2000))

LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 5)
LOGIN_WINDOW_SECONDS = _env_int("LOGIN_WINDOW_SECONDS", 300)
SESSION_MAX_AGE_SECONDS = _env_int("SESSION_MAX_AGE_SECONDS", 60 * 60 * 12)
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)

PERSISTED_STATE_FILE = os.getenv(
    "PERSISTED_STATE_FILE",
    "/var/lib/amp-panel/persisted_state.json",
)

SYSLOG_ENABLED = _env_bool("SYSLOG_ENABLED", True)
SYSLOG_HOST = os.getenv("SYSLOG_HOST", "127.0.0.1")
SYSLOG_PORT = _env_int("SYSLOG_PORT", 514)
SYSLOG_APP_NAME = os.getenv("SYSLOG_APP_NAME", "amp-panel")
SYSLOG_FACILITY = _env_int("SYSLOG_FACILITY", 16)
SYSLOG_TIMEZONE = os.getenv("SYSLOG_TIMEZONE", "Europe/Warsaw")
SYSLOG_HEARTBEAT_SECONDS = max(0, _env_int("SYSLOG_HEARTBEAT_SECONDS", 300))

SYSLOG_EXPORT_FILE = os.getenv(
    "SYSLOG_EXPORT_FILE",
    "/var/log/amp-panel/amp-panel.log",
)
REMOTE_SYSLOG_ENABLED = _env_bool("REMOTE_SYSLOG_ENABLED", False)
REMOTE_SYSLOG_HOST = os.getenv("REMOTE_SYSLOG_HOST", "")
REMOTE_SYSLOG_PORT = _env_int("REMOTE_SYSLOG_PORT", 514)
REMOTE_SYSLOG_PROTOCOL = os.getenv("REMOTE_SYSLOG_PROTOCOL", "tcp")

SNMP_PORT = _env_int("SNMP_PORT", 1161)
SNMP_COMMUNITY = os.getenv("SNMP_COMMUNITY", "")

# Central Office of Measures (GUM) — Polish national NTP/SNTP time server.
NTP_SERVER = os.getenv("NTP_SERVER", "tempus1.gum.gov.pl")
NTP_SERVER_FALLBACK_IP = os.getenv("NTP_SERVER_FALLBACK_IP", "194.146.251.100")
NTP_PORT = _env_int("NTP_PORT", 123)
NTP_TIMEOUT_SECONDS = _env_int("NTP_TIMEOUT_SECONDS", 3)
NTP_CACHE_SECONDS = _env_int("NTP_CACHE_SECONDS", 15)

RADIUS_SERVER = os.getenv("RADIUS_SERVER", "")
RADIUS_PORT = _env_int("RADIUS_PORT", 1812)
RADIUS_SECRET = os.getenv("RADIUS_SECRET", "")
RADIUS_TIMEOUT_SECONDS = _env_int("RADIUS_TIMEOUT_SECONDS", 3)
RADIUS_RETRIES = _env_int("RADIUS_RETRIES", 1)
RADIUS_NAS_IDENTIFIER = os.getenv("RADIUS_NAS_IDENTIFIER", DEVICE_NAME)


def _auth_mode(value: str | None) -> str:
    """Validate the selected browser authentication backend."""

    mode = (value or "radius").strip().lower()
    if mode not in {"local", "radius"}:
        raise RuntimeError("AUTH_MODE must be 'local' or 'radius'.")
    return mode


AUTH_MODE = _auth_mode(os.getenv("AUTH_MODE"))

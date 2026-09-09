#!/usr/bin/python3
"""System administration CLI for the Amp Panel Debian package.

This module intentionally uses only the Python standard library.  It must be
usable before the private application dependencies have been installed.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import getpass
import hashlib
import html
import math
import os
import pathlib
import re
import secrets
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from typing import Iterable

try:
    import grp
    import pwd
except ImportError:  # pragma: no cover - available on the Debian target
    grp = None
    pwd = None


PRODUCT_NAME = "Amp Panel"
VERSION = "0.1.0"
EXIT_NOT_CONFIGURED = 2

ETC_DIR = pathlib.Path(os.getenv("AMP_PANEL_ETC_DIR", "/etc/amp-panel"))
CONFIG_FILE = ETC_DIR / "amp-panel.env"
DEFAULT_DATA_DIR = pathlib.Path(os.getenv("AMP_PANEL_DEFAULT_DATA_DIR", "/var/lib/amp-panel"))
LOG_DIR = pathlib.Path(os.getenv("AMP_PANEL_LOG_DIR", "/var/log/amp-panel"))
RUN_DIR = pathlib.Path(os.getenv("AMP_PANEL_RUN_DIR", "/run/amp-panel"))
SYSTEMD_OVERRIDE_DIR = pathlib.Path(
    os.getenv(
        "AMP_PANEL_SYSTEMD_OVERRIDE_DIR",
        "/etc/systemd/system/amp-panel.service.d",
    )
)
RSYSLOG_FILE = pathlib.Path(os.getenv("AMP_PANEL_RSYSLOG_FILE", "/etc/rsyslog.d/30-amp-panel.conf"))
LOGROTATE_FILE = pathlib.Path(os.getenv("AMP_PANEL_LOGROTATE_FILE", "/etc/logrotate.d/amp-panel"))
AVAHI_FILE = pathlib.Path(
    os.getenv("AMP_PANEL_AVAHI_FILE", "/etc/avahi/services/amp-panel.service")
)
TIMESYNCD_FILE = pathlib.Path(
    os.getenv(
        "AMP_PANEL_TIMESYNCD_FILE",
        "/etc/systemd/timesyncd.conf.d/amp-panel.conf",
    )
)
VERSION_FILE = pathlib.Path(os.getenv("AMP_PANEL_VERSION_FILE", "/usr/lib/amp-panel/VERSION"))

CURRENT_SERVICE = "amp-panel.service"
NETWORK_AGENT_SERVICE = "amp-panel-network-agent.service"

KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
MDNS_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SERIAL_PATTERN = re.compile(r"^/dev/(?:tty(?:ACM|USB|S|O)[0-9]+|serial/by-id/[A-Za-z0-9._:+-]+)$")

CONFIG_KEYS = (
    "AMP_PANEL_CONFIG_VERSION",
    "AMP_PANEL_PORT",
    "AMP_PANEL_DATA_DIR",
    "DEVICE_PROFILE",
    "SERIAL_PORT",
    "SERIAL_BAUDRATE",
    "FTS_LS_USERNAME",
    "FTS_LS_PASSWORD",
    "FTS_LS_POLL_SECONDS",
    "FTS_LS_FREQUENCY_MIN_GHZ",
    "FTS_LS_FREQUENCY_MAX_GHZ",
    "GAIN_SET_MIN",
    "GAIN_SET_MAX",
    "DEVICE_NAME",
    "MDNS_HOSTNAME",
    "INITIAL_ADMIN_USERNAME",
    "INITIAL_ADMIN_PASSWORD_HASH",
    "INITIAL_ADMIN_PASSWORD_SALT",
    "AUTH_MODE",
    "PERSISTED_STATE_FILE",
    "DATABASE_FILE",
    "DATABASE_MAX_RECORDS",
    "HISTORY_MAX_POINTS",
    "LOGIN_MAX_ATTEMPTS",
    "LOGIN_WINDOW_SECONDS",
    "SESSION_MAX_AGE_SECONDS",
    "SESSION_COOKIE_SECURE",
    "TRUST_PROXY_HEADERS",
    "SYSLOG_ENABLED",
    "SYSLOG_HOST",
    "SYSLOG_PORT",
    "SYSLOG_APP_NAME",
    "SYSLOG_FACILITY",
    "SYSLOG_TIMEZONE",
    "SYSLOG_HEARTBEAT_SECONDS",
    "SYSLOG_EXPORT_FILE",
    "REMOTE_SYSLOG_ENABLED",
    "REMOTE_SYSLOG_HOST",
    "REMOTE_SYSLOG_PORT",
    "REMOTE_SYSLOG_PROTOCOL",
    "TZ",
    "SNMP_PORT",
    "SNMP_COMMUNITY",
    "NTP_SERVER",
    "NTP_SERVER_FALLBACK_IP",
    "NTP_PORT",
    "NTP_TIMEOUT_SECONDS",
    "NTP_CACHE_SECONDS",
    "RADIUS_SERVER",
    "RADIUS_PORT",
    "RADIUS_SECRET",
    "RADIUS_TIMEOUT_SECONDS",
    "RADIUS_RETRIES",
    "RADIUS_NAS_IDENTIFIER",
    "NETWORK_AGENT_SOCKET",
)


class ConfigurationError(RuntimeError):
    """A user-facing validation or system-configuration failure."""

    pass


def _run(
    command: list[str],
    *,
    check: bool = False,
    capture: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    environment = None
    if command and command[0] in {"systemctl", "journalctl"}:
        environment = os.environ.copy()
        environment.update(
            {
                "SYSTEMD_PAGER": "cat",
                "SYSTEMD_PAGERSECURE": "1",
                "PAGER": "cat",
            }
        )
    try:
        return subprocess.run(
            command,
            check=check,
            text=True,
            capture_output=capture,
            env=environment,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        duration = f"{timeout:g}" if timeout is not None else "unknown"
        raise ConfigurationError(
            f"Command did not finish within {duration} seconds: {' '.join(command)}"
        ) from exc


def _configuration_progress(message: str) -> None:
    print(f"[amp-panel] {message}", flush=True)


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _safe_int(value: object, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _safe_float(value: object, name: str) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a number.") from exc
    if not math.isfinite(parsed):
        raise ConfigurationError(f"{name} must be finite.")
    return parsed


def _env_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ConfigurationError("Configuration values may not contain newlines.")
    if re.fullmatch(r"[A-Za-z0-9_./:@%+,=-]*", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    """Read a restricted shell-style environment file without executing it."""

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigurationError(f"Could not read {path}: {exc}") from exc
    for number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"Invalid configuration line {number} in {path}.")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ConfigurationError(f"Invalid configuration key on line {number} in {path}.")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        values[key] = value
    return values


def write_env_file(path: pathlib.Path, values: dict[str, str]) -> None:
    """Atomically write validated configuration values as a protected env file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Managed by amp-panel. Run 'sudo amp-panel configure' to change it.",
        f"# Updated {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
    ]
    for key in CONFIG_KEYS:
        if key in values:
            lines.append(f"{key}={_env_value(str(values[key]))}")
    content = "\n".join(lines) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def edit_configuration(values: dict[str, str]) -> dict[str, str]:
    """Open a temporary complete configuration file and return its validated syntax."""

    temporary = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.edit")
    write_env_file(temporary, values)
    editor_text = os.getenv("VISUAL") or os.getenv("EDITOR") or "editor"
    try:
        editor = shlex.split(editor_text)
    except ValueError as exc:
        temporary.unlink(missing_ok=True)
        raise ConfigurationError("EDITOR contains invalid shell-style quoting.") from exc
    if not editor or shutil.which(editor[0]) is None:
        temporary.unlink(missing_ok=True)
        raise ConfigurationError(f"Configured editor is not available: {editor_text!r}")
    try:
        result = subprocess.run([*editor, str(temporary)], check=False)
        if result.returncode != 0:
            raise ConfigurationError("The editor exited without saving configuration changes.")
        edited = read_env_file(temporary)
        unknown = sorted(set(edited) - set(CONFIG_KEYS))
        if unknown:
            raise ConfigurationError(f"Unknown configuration key: {unknown[0]}")
        return merge_configuration(edited)
    finally:
        temporary.unlink(missing_ok=True)


def _secure_configuration_file(path: pathlib.Path) -> bool:
    if not path.is_file():
        return False
    if os.name != "posix":
        return True
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return False
    return not bool(mode & 0o022)


def discover_configuration() -> pathlib.Path | None:
    """Return the system configuration path only when its permissions are safe."""

    return CONFIG_FILE if _secure_configuration_file(CONFIG_FILE) else None


def _hardware_id() -> str:
    for interface in ("eth0", "end0"):
        path = pathlib.Path(f"/sys/class/net/{interface}/address")
        try:
            value = path.read_text(encoding="ascii").strip().replace(":", "")
        except OSError:
            continue
        if re.fullmatch(r"[0-9A-Fa-f]{12}", value) and value != "000000000000":
            return value[-8:].lower()
    try:
        machine_id = pathlib.Path("/etc/machine-id").read_text(encoding="ascii").strip()
    except OSError:
        machine_id = ""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", machine_id)
    return cleaned[:8].lower() if len(cleaned) >= 8 else secrets.token_hex(4)


def _device_name() -> str:
    prefix = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().lower()).strip("-")
    return f"{prefix or 'amplifier'}-{_hardware_id()}"


def _mdns_hostname() -> str:
    value = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return (value or "amp-panel")[:63].rstrip("-")


def _serial_device() -> str:
    for path in (
        pathlib.Path("/dev/ttyACM0"),
        pathlib.Path("/dev/ttyUSB0"),
        pathlib.Path("/dev/ttyS1"),
        pathlib.Path("/dev/ttyO1"),
    ):
        if path.exists():
            return str(path)
    return "/dev/ttyACM0"


def default_configuration() -> dict[str, str]:
    """Build installation defaults from detected hardware and standard paths."""

    data_dir = DEFAULT_DATA_DIR.resolve()
    device_name = _device_name()
    return {
        "AMP_PANEL_CONFIG_VERSION": "2",
        "AMP_PANEL_PORT": "8000",
        "AMP_PANEL_DATA_DIR": str(data_dir),
        "DEVICE_PROFILE": "amplifier",
        "SERIAL_PORT": _serial_device(),
        "SERIAL_BAUDRATE": "9600",
        "FTS_LS_USERNAME": "appadmin",
        "FTS_LS_PASSWORD": "",
        "FTS_LS_POLL_SECONDS": "10",
        "FTS_LS_FREQUENCY_MIN_GHZ": "194392.6",
        "FTS_LS_FREQUENCY_MAX_GHZ": "194405.6",
        "GAIN_SET_MIN": "",
        "GAIN_SET_MAX": "",
        "DEVICE_NAME": device_name,
        "MDNS_HOSTNAME": _mdns_hostname(),
        "INITIAL_ADMIN_USERNAME": "admin",
        "INITIAL_ADMIN_PASSWORD_HASH": "",
        "INITIAL_ADMIN_PASSWORD_SALT": "",
        "AUTH_MODE": "radius",
        "PERSISTED_STATE_FILE": str(data_dir / "persisted_state.json"),
        "DATABASE_FILE": str(data_dir / "measurements.db"),
        "DATABASE_MAX_RECORDS": "0",
        "HISTORY_MAX_POINTS": "2000",
        "LOGIN_MAX_ATTEMPTS": "5",
        "LOGIN_WINDOW_SECONDS": "300",
        "SESSION_MAX_AGE_SECONDS": "43200",
        "SESSION_COOKIE_SECURE": "false",
        "TRUST_PROXY_HEADERS": "false",
        "SYSLOG_ENABLED": "true",
        "SYSLOG_HOST": "127.0.0.1",
        "SYSLOG_PORT": "514",
        "SYSLOG_APP_NAME": "amp-panel",
        "SYSLOG_FACILITY": "16",
        "SYSLOG_TIMEZONE": "Europe/Warsaw",
        "SYSLOG_HEARTBEAT_SECONDS": "300",
        "SYSLOG_EXPORT_FILE": str(LOG_DIR / "amp-panel.log"),
        "REMOTE_SYSLOG_ENABLED": "false",
        "REMOTE_SYSLOG_HOST": "",
        "REMOTE_SYSLOG_PORT": "514",
        "REMOTE_SYSLOG_PROTOCOL": "tcp",
        "TZ": "Europe/Warsaw",
        "SNMP_PORT": "1161",
        "SNMP_COMMUNITY": secrets.token_urlsafe(24),
        "NTP_SERVER": "tempus1.gum.gov.pl",
        "NTP_SERVER_FALLBACK_IP": "194.146.251.100",
        "NTP_PORT": "123",
        "NTP_TIMEOUT_SECONDS": "3",
        "NTP_CACHE_SECONDS": "15",
        "RADIUS_SERVER": "",
        "RADIUS_PORT": "1812",
        "RADIUS_SECRET": "",
        "RADIUS_TIMEOUT_SECONDS": "3",
        "RADIUS_RETRIES": "1",
        "RADIUS_NAS_IDENTIFIER": device_name,
        "NETWORK_AGENT_SOCKET": str(RUN_DIR / "network-agent.sock"),
    }


def _normalized_data_dir(value: str, source: pathlib.Path | None = None) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        if source is None:
            raise ConfigurationError("The data directory must be an absolute path.")
        path = source.parent / path
    path = path.resolve(strict=False)
    forbidden = {
        pathlib.Path("/"),
        pathlib.Path("/bin"),
        pathlib.Path("/boot"),
        pathlib.Path("/dev"),
        pathlib.Path("/etc"),
        pathlib.Path("/lib"),
        pathlib.Path("/proc"),
        pathlib.Path("/root"),
        pathlib.Path("/run"),
        pathlib.Path("/sbin"),
        pathlib.Path("/sys"),
        pathlib.Path("/usr"),
        pathlib.Path("/var"),
    }
    if path in forbidden:
        raise ConfigurationError(f"Unsafe data directory: {path}")
    application_roots = (pathlib.Path("/var/lib/amp-panel"),)
    external_roots = (
        pathlib.Path("/mnt"),
        pathlib.Path("/media"),
        pathlib.Path("/srv"),
    )
    allowed = any(path == root or root in path.parents for root in application_roots) or any(
        root in path.parents for root in external_roots
    )
    if os.getenv("AMP_PANEL_ALLOW_ANY_DATA_DIR") != "1" and not allowed:
        raise ConfigurationError(
            "The data directory must be /var/lib/amp-panel or below /mnt, /media or /srv."
        )
    return path


def merge_configuration(source_values: dict[str, str]) -> dict[str, str]:
    """Overlay recognized existing values onto current configuration defaults."""

    translated = default_configuration()
    for key in CONFIG_KEYS:
        if key in source_values:
            translated[key] = source_values[key]
    data_dir = _normalized_data_dir(translated["AMP_PANEL_DATA_DIR"])
    translated["AMP_PANEL_DATA_DIR"] = str(data_dir)
    return translated


def validate_configuration(values: dict[str, str]) -> None:
    """Reject unsafe or inconsistent values before writing system files."""

    if not USERNAME_PATTERN.fullmatch(values.get("INITIAL_ADMIN_USERNAME", "")):
        raise ConfigurationError("The Administrator username is invalid.")
    _safe_int(values.get("AMP_PANEL_PORT"), "Web port", 1024, 65535)
    profile = values.get("DEVICE_PROFILE", "amplifier").strip().lower()
    if profile not in {"amplifier", "fts-ls"}:
        raise ConfigurationError("Device profile must be amplifier or fts-ls.")
    _safe_int(values.get("SERIAL_BAUDRATE"), "Serial baud rate", 1, 10_000_000)
    if profile == "amplifier":
        gain_min = _safe_float(values.get("GAIN_SET_MIN"), "Minimum safe gain")
        gain_max = _safe_float(values.get("GAIN_SET_MAX"), "Maximum safe gain")
        if gain_min >= gain_max:
            raise ConfigurationError("Minimum safe gain must be lower than maximum safe gain.")
    else:
        if not USERNAME_PATTERN.fullmatch(values.get("FTS_LS_USERNAME", "")):
            raise ConfigurationError("The FTS-LS console username is invalid.")
        _safe_int(values.get("FTS_LS_POLL_SECONDS"), "FTS-LS poll interval", 2, 3600)
        frequency_min = _safe_float(
            values.get("FTS_LS_FREQUENCY_MIN_GHZ"), "FTS-LS minimum laser frequency"
        )
        frequency_max = _safe_float(
            values.get("FTS_LS_FREQUENCY_MAX_GHZ"), "FTS-LS maximum laser frequency"
        )
        if frequency_min >= frequency_max:
            raise ConfigurationError(
                "FTS-LS minimum laser frequency must be lower than the maximum."
            )
    serial_port = values.get("SERIAL_PORT", "")
    if serial_port != "/dev/null" and not SERIAL_PATTERN.fullmatch(serial_port):
        raise ConfigurationError(
            "Serial device must be a supported /dev/tty* device or /dev/serial/by-id entry."
        )
    data_dir = _normalized_data_dir(values.get("AMP_PANEL_DATA_DIR", ""))
    database_file = pathlib.Path(values.get("DATABASE_FILE", ""))
    state_file = pathlib.Path(values.get("PERSISTED_STATE_FILE", ""))
    for path, label in (
        (database_file, "DATABASE_FILE"),
        (state_file, "PERSISTED_STATE_FILE"),
    ):
        if not path.is_absolute() or data_dir not in path.parents:
            raise ConfigurationError(f"{label} must be inside the data directory.")
    mdns = values.get("MDNS_HOSTNAME", "")
    if not MDNS_PATTERN.fullmatch(mdns):
        raise ConfigurationError("The mDNS hostname is invalid.")
    _safe_int(values.get("SNMP_PORT"), "SNMP port", 1024, 65535)
    if not values.get("SNMP_COMMUNITY"):
        raise ConfigurationError("SNMP community is required.")
    auth_mode = values.get("AUTH_MODE", "").lower()
    if auth_mode not in {"local", "radius"}:
        raise ConfigurationError("Authentication mode must be local or radius.")
    if auth_mode == "local":
        if not values.get("INITIAL_ADMIN_PASSWORD_HASH") or not values.get(
            "INITIAL_ADMIN_PASSWORD_SALT"
        ):
            raise ConfigurationError("A local administrator password is required.")
    else:
        radius_server = values.get("RADIUS_SERVER", "")
        if not radius_server or not HOST_PATTERN.fullmatch(radius_server):
            raise ConfigurationError("A valid remote RADIUS server is required.")
        _safe_int(values.get("RADIUS_PORT"), "RADIUS port", 1, 65535)
        if not values.get("RADIUS_SECRET"):
            raise ConfigurationError("RADIUS shared secret is required.")
    remote_enabled = values.get("REMOTE_SYSLOG_ENABLED", "false").lower()
    if remote_enabled not in {"true", "false"}:
        raise ConfigurationError("REMOTE_SYSLOG_ENABLED must be true or false.")
    if remote_enabled == "true":
        if not HOST_PATTERN.fullmatch(values.get("REMOTE_SYSLOG_HOST", "")):
            raise ConfigurationError("The remote syslog host is invalid.")
        _safe_int(values.get("REMOTE_SYSLOG_PORT"), "Remote syslog port", 1, 65535)
        if values.get("REMOTE_SYSLOG_PROTOCOL") not in {"tcp", "udp"}:
            raise ConfigurationError("Remote syslog protocol must be tcp or udp.")


def _prompt(label: str, default: str = "", *, secret: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    reader = getpass.getpass if secret else input
    value = reader(f"{label}{suffix}: ").strip()
    return value or default


def _set_local_admin_password(values: dict[str, str], password: str) -> None:
    """Hash a local administrator password without retaining its clear-text form."""

    if not 8 <= len(password) <= 256:
        raise ConfigurationError("Local administrator password must contain 8 to 256 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    values["INITIAL_ADMIN_PASSWORD_HASH"] = (
        "pbkdf2_sha256$600000$" + base64.b64encode(digest).decode("ascii")
    )
    values["INITIAL_ADMIN_PASSWORD_SALT"] = base64.b64encode(salt).decode("ascii")


def interactive_configuration(values: dict[str, str]) -> dict[str, str]:
    """Prompt an administrator for profile, serial, host, and service settings."""

    print("\nAmp Panel configuration\n")
    previous_profile = values.get("DEVICE_PROFILE", "amplifier")
    profile = _prompt(
        "Device profile (amplifier/fts-ls)",
        values.get("DEVICE_PROFILE", "amplifier"),
    ).lower()
    profile_aliases = {"amp": "amplifier", "fts_ls": "fts-ls", "laser-station": "fts-ls"}
    values["DEVICE_PROFILE"] = profile_aliases.get(profile, profile)
    if values["DEVICE_PROFILE"] == "amplifier":
        values["SERIAL_BAUDRATE"] = "9600"
        if previous_profile == "fts-ls":
            values["GAIN_SET_MIN"] = ""
            values["GAIN_SET_MAX"] = ""
    values["INITIAL_ADMIN_USERNAME"] = _prompt(
        "Administrator username",
        values["INITIAL_ADMIN_USERNAME"],
    )
    values["AUTH_MODE"] = _prompt(
        "Authentication mode (local/radius)", values.get("AUTH_MODE", "radius")
    ).lower()
    if values["AUTH_MODE"] == "local":
        password = _prompt("Local administrator password", secret=True)
        confirmation = _prompt("Repeat local administrator password", secret=True)
        if password != confirmation:
            raise ConfigurationError("Local administrator passwords do not match.")
        _set_local_admin_password(values, password)
    values["AMP_PANEL_PORT"] = _prompt("Web interface port", values["AMP_PANEL_PORT"])
    values["SERIAL_PORT"] = _prompt("Serial device", values["SERIAL_PORT"])
    if values["DEVICE_PROFILE"] == "fts-ls":
        values["SERIAL_BAUDRATE"] = "115200"
        values["FTS_LS_USERNAME"] = _prompt(
            "FTS-LS ADMIN console username",
            values.get("FTS_LS_USERNAME", "appadmin"),
        )
        values["FTS_LS_PASSWORD"] = _prompt(
            "FTS-LS ADMIN console password",
            values.get("FTS_LS_PASSWORD", ""),
            secret=True,
        )
        values["GAIN_SET_MIN"] = values.get("GAIN_SET_MIN") or "-100"
        values["GAIN_SET_MAX"] = values.get("GAIN_SET_MAX") or "100"
    values["AMP_PANEL_DATA_DIR"] = _prompt(
        "Measurement data directory",
        values["AMP_PANEL_DATA_DIR"],
    )
    data_dir = _normalized_data_dir(values["AMP_PANEL_DATA_DIR"])
    values["AMP_PANEL_DATA_DIR"] = str(data_dir)
    values["DATABASE_FILE"] = str(data_dir / "measurements.db")
    values["PERSISTED_STATE_FILE"] = str(data_dir / "persisted_state.json")
    if values["DEVICE_PROFILE"] == "amplifier":
        values["GAIN_SET_MIN"] = _prompt(
            "Minimum safe gain from the device specification",
            values["GAIN_SET_MIN"],
        )
        values["GAIN_SET_MAX"] = _prompt(
            "Maximum safe gain from the device specification",
            values["GAIN_SET_MAX"],
        )
    if values["AUTH_MODE"] == "radius":
        values["RADIUS_SERVER"] = _prompt("RADIUS server", values["RADIUS_SERVER"])
        values["RADIUS_PORT"] = _prompt("RADIUS UDP port", values["RADIUS_PORT"])
        values["RADIUS_SECRET"] = _prompt(
            "RADIUS shared secret",
            values["RADIUS_SECRET"],
            secret=True,
        )
    values["MDNS_HOSTNAME"] = _prompt(
        "mDNS hostname (without .local)",
        values["MDNS_HOSTNAME"],
    ).lower()
    return values


def _apply_answers(values: dict[str, str], answers: dict[str, str]) -> None:
    mapping = {
        "device_profile": "DEVICE_PROFILE",
        "admin_username": "INITIAL_ADMIN_USERNAME",
        "auth_mode": "AUTH_MODE",
        "port": "AMP_PANEL_PORT",
        "data_dir": "AMP_PANEL_DATA_DIR",
        "serial_port": "SERIAL_PORT",
        "fts_ls_username": "FTS_LS_USERNAME",
        "fts_ls_password": "FTS_LS_PASSWORD",
        "gain_min": "GAIN_SET_MIN",
        "gain_max": "GAIN_SET_MAX",
        "radius_server": "RADIUS_SERVER",
        "radius_port": "RADIUS_PORT",
        "radius_secret": "RADIUS_SECRET",
        "mdns_hostname": "MDNS_HOSTNAME",
    }
    for answer_key, config_key in mapping.items():
        encoded_answer = answers.get(f"{answer_key}_b64")
        if encoded_answer is not None:
            try:
                answer = base64.b64decode(
                    encoded_answer,
                    validate=True,
                ).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise ConfigurationError(f"Invalid encoded installer answer: {answer_key}") from exc
        else:
            answer = answers.get(answer_key)
        if answer is not None and answer != "":
            values[config_key] = answer
    encoded_password = answers.get("local_admin_password_b64")
    local_password = answers.get("local_admin_password")
    if encoded_password is not None:
        try:
            local_password = base64.b64decode(encoded_password, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConfigurationError("Invalid encoded installer answer: local_admin_password") from exc
    if local_password:
        _set_local_admin_password(values, local_password)
    data_dir = _normalized_data_dir(values["AMP_PANEL_DATA_DIR"])
    values["AMP_PANEL_DATA_DIR"] = str(data_dir)
    values["DATABASE_FILE"] = str(data_dir / "measurements.db")
    values["PERSISTED_STATE_FILE"] = str(data_dir / "persisted_state.json")
    if values.get("DEVICE_PROFILE") == "fts-ls":
        values["SERIAL_BAUDRATE"] = "115200"
        values["GAIN_SET_MIN"] = values.get("GAIN_SET_MIN") or "-100"
        values["GAIN_SET_MAX"] = values.get("GAIN_SET_MAX") or "100"


def _lookup_identity() -> tuple[int | None, int | None]:
    if pwd is None or grp is None:
        return None, None
    try:
        user = pwd.getpwnam("amp-panel")
        group = grp.getgrnam("amp-panel")
    except KeyError:
        return None, None
    return user.pw_uid, group.gr_gid


def _chown(path: pathlib.Path, uid: int | None, gid: int | None) -> None:
    if os.name == "posix" and uid is not None and gid is not None:
        os.chown(path, uid, gid)


def prepare_data_directory(values: dict[str, str]) -> None:
    """Create runtime storage with ownership and permissions for the service."""

    data_dir = _normalized_data_dir(values["AMP_PANEL_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    uid, gid = _lookup_identity()
    _chown(data_dir, uid, gid)
    os.chmod(data_dir, 0o750)
    database = pathlib.Path(values["DATABASE_FILE"])
    state_file = pathlib.Path(values["PERSISTED_STATE_FILE"])
    managed_files = (
        database,
        pathlib.Path(f"{database}-wal"),
        pathlib.Path(f"{database}-shm"),
        state_file,
        state_file.with_name(f"{state_file.name}.tmp"),
    )
    for child in managed_files:
        if child.is_file():
            _chown(child, uid, gid)
    if not os.access(data_dir, os.W_OK):
        raise ConfigurationError(f"Data directory is not writable: {data_dir}")


def _write_text(path: pathlib.Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def write_system_configuration(values: dict[str, str]) -> None:
    """Write application, logging, discovery, and time-service configuration."""

    data_dir = pathlib.Path(values["AMP_PANEL_DATA_DIR"])
    _write_text(
        SYSTEMD_OVERRIDE_DIR / "paths.conf",
        f'[Unit]\nRequiresMountsFor="{data_dir}"\n\n[Service]\nReadWritePaths="{data_dir}"\n',
    )
    _write_text(
        AVAHI_FILE,
        '<?xml version="1.0" standalone="no"?>\n'
        '<!DOCTYPE service-group SYSTEM "avahi-service.dtd">\n'
        "<service-group>\n"
        '  <name replace-wildcards="yes">%h Amp Panel</name>\n'
        "  <service>\n"
        "    <type>_http._tcp</type>\n"
        f"    <port>{_safe_int(values['AMP_PANEL_PORT'], 'Web port', 1024, 65535)}</port>\n"
        f"    <txt-record>path=/</txt-record>\n"
        f"    <txt-record>product={html.escape(PRODUCT_NAME)}</txt-record>\n"
        "  </service>\n"
        "</service-group>\n",
    )
    _write_text(
        TIMESYNCD_FILE,
        f"[Time]\nNTP={values['NTP_SERVER']}\nFallbackNTP={values['NTP_SERVER_FALLBACK_IP']}\n",
    )

    remote_action = ""
    if values["REMOTE_SYSLOG_ENABLED"].lower() == "true":
        remote_action = (
            "        action(\n"
            '            type="omfwd"\n'
            f'            target="{values["REMOTE_SYSLOG_HOST"]}"\n'
            f'            port="{values["REMOTE_SYSLOG_PORT"]}"\n'
            f'            protocol="{values["REMOTE_SYSLOG_PROTOCOL"]}"\n'
            '            action.resumeRetryCount="-1"\n'
            '            queue.type="LinkedList"\n'
            '            queue.filename="ampPanelForward"\n'
            '            queue.saveOnShutdown="on"\n'
            "        )\n"
        )
    log_file = LOG_DIR / "amp-panel.log"
    _write_text(
        RSYSLOG_FILE,
        'module(load="imudp")\n'
        "$AllowedSender UDP, 127.0.0.1\n\n"
        'template(name="ampPanelLine" type="string" '
        'string="%timereported:::date-rfc3339% %msg:2:$%\\n")\n\n'
        'ruleset(name="ampPanel") {\n'
        '    if ($programname == "amp-panel") then {\n'
        "        action(\n"
        '            type="omfile"\n'
        f'            file="{log_file}"\n'
        '            fileOwner="root"\n'
        '            fileGroup="adm"\n'
        '            fileCreateMode="0640"\n'
        '            template="ampPanelLine"\n'
        "        )\n"
        f"{remote_action}"
        "        stop\n"
        "    }\n"
        "}\n\n"
        'input(type="imudp" port="514" ruleset="ampPanel")\n',
    )
    _write_text(
        LOGROTATE_FILE,
        f"{log_file} {{\n"
        "    daily\n"
        "    rotate 30\n"
        "    compress\n"
        "    delaycompress\n"
        "    missingok\n"
        "    notifempty\n"
        "    create 0640 root adm\n"
        "    postrotate\n"
        "        systemctl kill -s HUP rsyslog.service >/dev/null 2>&1 || true\n"
        "    endscript\n"
        "}\n",
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file.touch(exist_ok=True)
    try:
        adm_gid = grp.getgrnam("adm").gr_gid if grp is not None else -1
    except KeyError:
        adm_gid = -1
    if os.name == "posix" and os.geteuid() == 0:
        os.chown(log_file, 0, adm_gid)
        os.chown(LOG_DIR, 0, adm_gid)
    os.chmod(LOG_DIR, 0o750)
    os.chmod(log_file, 0o640)


def _service_exists(service: str) -> bool:
    if not _command_exists("systemctl"):
        return False
    result = _run(
        ["systemctl", "show", service, "--property=LoadState", "--value"],
        capture=True,
        timeout=15,
    )
    return result.returncode == 0 and result.stdout.strip() not in {"", "not-found"}


def apply_hostname(values: dict[str, str]) -> None:
    """Apply the configured hostname on a supported systemd host."""

    if (
        os.name != "posix"
        or os.geteuid() != 0
        or ETC_DIR != pathlib.Path("/etc/amp-panel")
        or not _command_exists("hostnamectl")
    ):
        return
    requested = values["MDNS_HOSTNAME"]
    current = socket.gethostname().split(".", 1)[0].lower()
    if current == requested:
        return
    result = _run(
        ["hostnamectl", "set-hostname", requested],
        capture=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ConfigurationError(f"Could not set the mDNS hostname to {requested}: {detail}")


def reload_services(*, start: bool) -> None:
    """Reload systemd and optionally enable and restart panel services."""

    if not _command_exists("systemctl"):
        return
    if _command_exists("rsyslogd"):
        _configuration_progress("Validating the syslog configuration...")
        validation = _run(["rsyslogd", "-N1"], capture=True, timeout=15)
        if validation.returncode != 0:
            detail = validation.stderr.strip() or validation.stdout.strip()
            raise ConfigurationError(f"rsyslog configuration is invalid: {detail}")
    _configuration_progress("Reloading systemd configuration...")
    daemon_reload = _run(["systemctl", "daemon-reload"], capture=True, timeout=30)
    if daemon_reload.returncode != 0:
        detail = daemon_reload.stderr.strip() or daemon_reload.stdout.strip()
        raise ConfigurationError(f"systemd daemon-reload failed: {detail}")
    for service in ("rsyslog.service", "avahi-daemon.service", "systemd-timesyncd.service"):
        if _service_exists(service):
            _configuration_progress(f"Restarting {service}...")
            restarted = _run(["systemctl", "restart", service], capture=True, timeout=30)
            if restarted.returncode != 0:
                detail = restarted.stderr.strip() or restarted.stdout.strip()
                raise ConfigurationError(f"Could not restart {service}: {detail}")
    if start:
        _configuration_progress("Enabling Amp Panel services...")
        enabled = _run(
            [
                "systemctl",
                "enable",
                NETWORK_AGENT_SERVICE,
                CURRENT_SERVICE,
            ],
            capture=True,
            timeout=30,
        )
        if enabled.returncode != 0:
            detail = enabled.stderr.strip() or enabled.stdout.strip()
            raise ConfigurationError(f"Could not enable Amp Panel services: {detail}")
        _configuration_progress("Restarting Amp Panel services...")
        restart = _run(
            ["systemctl", "restart", NETWORK_AGENT_SERVICE, CURRENT_SERVICE],
            capture=True,
            timeout=30,
        )
        if restart.returncode != 0:
            detail = restart.stderr.strip() or restart.stdout.strip()
            raise ConfigurationError(
                f"Could not start Amp Panel services: {detail}. "
                "Inspect: journalctl -u amp-panel.service"
            )


def _configuration_from_source(path: pathlib.Path | None) -> dict[str, str]:
    if path is None:
        return default_configuration()
    return merge_configuration(read_env_file(path))


def configure_command(args: argparse.Namespace) -> int:
    """Validate and atomically apply the requested host configuration."""
    if os.name == "posix" and os.geteuid() != 0:
        print(
            "amp-panel: configuration changes require root; run: sudo amp-panel configure",
            file=sys.stderr,
        )
        return 1
    source = discover_configuration()
    try:
        values = _configuration_from_source(source)
        source_device_profile = values.get("DEVICE_PROFILE", "amplifier")
        answers: dict[str, str] = {}
        if args.answers_file:
            answers = read_env_file(pathlib.Path(args.answers_file))
            answers = {key.lower(): value for key, value in answers.items()}
        _apply_answers(values, answers)
        if args.device_profile:
            values["DEVICE_PROFILE"] = args.device_profile
            if args.device_profile == "fts-ls":
                values["SERIAL_BAUDRATE"] = "115200"
                values["GAIN_SET_MIN"] = values.get("GAIN_SET_MIN") or "-100"
                values["GAIN_SET_MAX"] = values.get("GAIN_SET_MAX") or "100"
            else:
                values["SERIAL_BAUDRATE"] = "9600"
                if source_device_profile == "fts-ls":
                    values["GAIN_SET_MIN"] = ""
                    values["GAIN_SET_MAX"] = ""
        if args.admin_username:
            values["INITIAL_ADMIN_USERNAME"] = args.admin_username
        if args.auth_mode:
            values["AUTH_MODE"] = args.auth_mode
        if args.local_admin_password_stdin:
            _set_local_admin_password(values, sys.stdin.readline().rstrip("\r\n"))
        if args.port:
            values["AMP_PANEL_PORT"] = str(args.port)
        if args.data_dir:
            data_dir = _normalized_data_dir(args.data_dir)
            values["AMP_PANEL_DATA_DIR"] = str(data_dir)
            values["DATABASE_FILE"] = str(data_dir / "measurements.db")
            values["PERSISTED_STATE_FILE"] = str(data_dir / "persisted_state.json")
        if args.serial_port:
            values["SERIAL_PORT"] = args.serial_port
        if args.fts_ls_username:
            values["FTS_LS_USERNAME"] = args.fts_ls_username
        if args.fts_ls_password:
            values["FTS_LS_PASSWORD"] = args.fts_ls_password
        if args.gain_min is not None:
            values["GAIN_SET_MIN"] = str(args.gain_min)
        if args.gain_max is not None:
            values["GAIN_SET_MAX"] = str(args.gain_max)
        if args.radius_server:
            values["RADIUS_SERVER"] = args.radius_server
        if args.radius_port:
            values["RADIUS_PORT"] = str(args.radius_port)
        if args.radius_secret:
            values["RADIUS_SECRET"] = args.radius_secret
        if args.mdns_hostname:
            values["MDNS_HOSTNAME"] = args.mdns_hostname.lower()
        if not args.non_interactive:
            values = interactive_configuration(values) if args.prompt else edit_configuration(values)
        _configuration_progress("Validating settings...")
        validate_configuration(values)
        _configuration_progress("Preparing the measurement data directory...")
        prepare_data_directory(values)
        _configuration_progress("Applying the device hostname...")
        apply_hostname(values)
        _configuration_progress("Writing configuration files...")
        write_env_file(CONFIG_FILE, values)
        write_system_configuration(values)
        _configuration_progress("Applying system services...")
        reload_services(start=not args.no_start)
    except (ConfigurationError, OSError, sqlite3.Error) as exc:
        print(f"amp-panel: configuration incomplete: {exc}", file=sys.stderr)
        return EXIT_NOT_CONFIGURED
    _configuration_progress("Configuration completed successfully.")
    print(f"Configuration: {CONFIG_FILE}")
    print(f"Data directory: {values['AMP_PANEL_DATA_DIR']}")
    print(f"Panel address: http://{values['MDNS_HOSTNAME']}.local:{values['AMP_PANEL_PORT']}")
    return 0


def load_current_configuration() -> dict[str, str]:
    """Load and validate the installed system configuration."""

    if not CONFIG_FILE.is_file():
        raise ConfigurationError("Amp Panel is not configured. Run: sudo amp-panel configure")
    values = read_env_file(CONFIG_FILE)
    validate_configuration(values)
    return values


def paths_command(_args: argparse.Namespace) -> int:
    """Print installed configuration, data, log, and runtime locations."""

    try:
        values = load_current_configuration()
        data_dir = values["AMP_PANEL_DATA_DIR"]
    except ConfigurationError:
        data_dir = str(DEFAULT_DATA_DIR)
    print("Application:   /usr/lib/amp-panel")
    print(f"Configuration: {CONFIG_FILE}")
    print(f"Data:          {data_dir}")
    print(f"Logs:          {LOG_DIR}")
    print(f"Runtime:       {RUN_DIR}")
    return 0


def systemctl_command(action: str) -> int:
    """Run an allowed lifecycle action for both panel systemd services."""

    if not _command_exists("systemctl"):
        print("systemctl is unavailable.", file=sys.stderr)
        return 1
    command = ["systemctl"]
    if action == "status":
        command.extend(["--no-pager", "--full", "--lines=0"])
    command.extend([action, CURRENT_SERVICE])
    return _run(command).returncode


def logs_command(args: argparse.Namespace) -> int:
    """Print a bounded journal sample for panel services."""

    if not _command_exists("journalctl"):
        print("journalctl is unavailable.", file=sys.stderr)
        return 1
    command = [
        "journalctl",
        "--no-pager",
        "-u",
        CURRENT_SERVICE,
        "-n",
        str(args.lines),
    ]
    if args.follow:
        command.append("-f")
    return _run(command).returncode


def _sqlite_integrity(database: pathlib.Path) -> tuple[bool, str]:
    if not database.exists():
        return False, "database does not exist"
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        connection.close()
    except sqlite3.Error as exc:
        return False, str(exc)
    return result == "ok", str(result)


def doctor_command(_args: argparse.Namespace) -> int:
    """Run non-destructive configuration, service and database health checks."""
    failures = 0
    try:
        values = load_current_configuration()
        print(f"[OK] configuration: {CONFIG_FILE}")
    except ConfigurationError as exc:
        print(f"[FAIL] configuration: {exc}")
        return 1
    data_dir = pathlib.Path(values["AMP_PANEL_DATA_DIR"])
    if data_dir.is_dir():
        print(f"[OK] data directory: {data_dir}")
    else:
        print(f"[FAIL] data directory is missing: {data_dir}")
        failures += 1
    database = pathlib.Path(values["DATABASE_FILE"])
    if database.exists():
        valid, detail = _sqlite_integrity(database)
        print(f"[{'OK' if valid else 'FAIL'}] SQLite integrity: {detail}")
        failures += 0 if valid else 1
    else:
        print("[OK] SQLite database will be created on the first measurement.")
    serial_port = pathlib.Path(values["SERIAL_PORT"])
    if serial_port.exists():
        print(f"[OK] serial device: {serial_port}")
    else:
        print(f"[WARN] serial device is not currently connected: {serial_port}")
    for service in (CURRENT_SERVICE, NETWORK_AGENT_SERVICE):
        if _service_exists(service):
            result = _run(["systemctl", "is-active", service], capture=True)
            state = result.stdout.strip() or "unknown"
            print(f"[{'OK' if state == 'active' else 'FAIL'}] {service}: {state}")
            failures += 0 if state == "active" else 1
    return 1 if failures else 0


def _update_data_paths(values: dict[str, str], data_dir: pathlib.Path) -> None:
    values["AMP_PANEL_DATA_DIR"] = str(data_dir)
    values["DATABASE_FILE"] = str(data_dir / "measurements.db")
    values["PERSISTED_STATE_FILE"] = str(data_dir / "persisted_state.json")


def data_dir_command(args: argparse.Namespace) -> int:
    """Move managed persistent data to a validated directory and reload services."""
    try:
        values = load_current_configuration()
    except ConfigurationError as exc:
        print(f"amp-panel: {exc}", file=sys.stderr)
        return 1
    source_dir = pathlib.Path(values["AMP_PANEL_DATA_DIR"])
    if args.data_action == "show":
        print(source_dir)
        return 0
    try:
        destination = _normalized_data_dir(args.path)
        if args.data_action == "use":
            if not destination.is_dir():
                raise ConfigurationError(f"Data directory does not exist: {destination}")
            _update_data_paths(values, destination)
            prepare_data_directory(values)
            write_env_file(CONFIG_FILE, values)
            write_system_configuration(values)
            reload_services(start=True)
            print(f"Amp Panel now uses {destination}")
            return 0

        if destination == source_dir:
            print("Source and destination data directories are the same.")
            return 0
        conflicts = [
            destination / name
            for name in ("measurements.db", "persisted_state.json")
            if (destination / name).exists()
        ]
        if conflicts:
            raise ConfigurationError(
                "The destination already contains Amp Panel data: "
                + ", ".join(str(path) for path in conflicts)
            )
        if systemctl_command("stop") != 0:
            raise ConfigurationError("Could not stop Amp Panel before migration.")
        source_database = pathlib.Path(values["DATABASE_FILE"])
        if source_database.exists():
            connection = sqlite3.connect(source_database, timeout=10)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            connection.close()
            if integrity != "ok":
                raise ConfigurationError(f"Source SQLite integrity check failed: {integrity}")
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("measurements.db", "persisted_state.json"):
            source = source_dir / name
            if source.is_file():
                temporary = destination / f".{name}.migrating"
                shutil.copy2(source, temporary)
                os.replace(temporary, destination / name)
        destination_database = destination / "measurements.db"
        if destination_database.exists():
            valid, detail = _sqlite_integrity(destination_database)
            if not valid:
                raise ConfigurationError(f"Destination SQLite integrity check failed: {detail}")
        _update_data_paths(values, destination)
        prepare_data_directory(values)
        write_env_file(CONFIG_FILE, values)
        write_system_configuration(values)
        reload_services(start=True)
        print(f"Data migrated to {destination}")
        print(f"The previous copy remains in {source_dir}")
        return 0
    except (ConfigurationError, OSError, sqlite3.Error) as exc:
        print(f"amp-panel: data directory change failed: {exc}", file=sys.stderr)
        systemctl_command("start")
        return 1


def version_command(_args: argparse.Namespace) -> int:
    """Print the installed package version or the built-in fallback."""

    try:
        version = VERSION_FILE.read_text(encoding="ascii").strip()
    except OSError:
        version = VERSION
    print(f"amp-panel {version}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser and all supported subcommands."""

    parser = argparse.ArgumentParser(prog="amp-panel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="configure Amp Panel")
    configure.add_argument("--non-interactive", action="store_true")
    configure.add_argument(
        "--prompt",
        action="store_true",
        help="use the previous question-and-answer configuration wizard",
    )
    configure.add_argument("--no-start", action="store_true")
    configure.add_argument("--answers-file")
    configure.add_argument("--admin-username")
    configure.add_argument("--auth-mode", choices=("local", "radius"))
    configure.add_argument(
        "--local-admin-password-stdin",
        action="store_true",
        help="read the local administrator password from standard input",
    )
    configure.add_argument("--port", type=int)
    configure.add_argument("--data-dir")
    configure.add_argument("--device-profile", choices=("amplifier", "fts-ls"))
    configure.add_argument("--serial-port")
    configure.add_argument("--fts-ls-username")
    configure.add_argument("--fts-ls-password")
    configure.add_argument("--gain-min")
    configure.add_argument("--gain-max")
    configure.add_argument("--radius-server")
    configure.add_argument("--radius-port", type=int)
    configure.add_argument("--radius-secret")
    configure.add_argument("--mdns-hostname")
    configure.set_defaults(handler=configure_command)

    for action in ("start", "stop", "restart", "status"):
        command = subparsers.add_parser(action)
        systemd_action = "status" if action == "status" else action
        command.set_defaults(handler=lambda _args, value=systemd_action: systemctl_command(value))

    logs = subparsers.add_parser("logs")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=100)
    logs.set_defaults(handler=logs_command)

    paths = subparsers.add_parser("paths")
    paths.set_defaults(handler=paths_command)
    doctor = subparsers.add_parser("doctor")
    doctor.set_defaults(handler=doctor_command)
    version = subparsers.add_parser("version")
    version.set_defaults(handler=version_command)

    data_dir = subparsers.add_parser("data-dir")
    data_subparsers = data_dir.add_subparsers(dest="data_action", required=True)
    data_show = data_subparsers.add_parser("show")
    data_show.set_defaults(handler=data_dir_command)
    for action in ("use", "migrate"):
        data_command = data_subparsers.add_parser(action)
        data_command.add_argument("path")
        data_command.set_defaults(handler=data_dir_command)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """Parse command-line arguments and dispatch the selected administration task."""

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

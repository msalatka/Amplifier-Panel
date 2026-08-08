"""Minimal cached NTP client used for administrator time diagnostics."""

import datetime
import socket
import struct
import threading
import time

from app.core import config

NTP_EPOCH_OFFSET = (
    2_208_988_800  # seconds between 1900-01-01 (NTP epoch) and 1970-01-01 (Unix epoch)
)
_PACKET = b"\x1b" + 47 * b"\0"  # LI=0, VN=3, Mode=3 (client); remaining fields are zero

LEAP_INDICATOR_LABELS = {
    0: "No warning",
    1: "Last minute has 61 seconds",
    2: "Last minute has 59 seconds",
    3: "Not synchronized (alarm)",
}

_cache_lock = threading.Lock()
_cached_result: dict | None = None
_cached_at = 0.0


def _to_unix(ntp_timestamp: float) -> float:
    return ntp_timestamp - NTP_EPOCH_OFFSET


def _decode_reference_id(stratum: int, raw: bytes) -> str:
    if stratum in (0, 1):
        text = raw.rstrip(b"\x00").decode("ascii", errors="replace")
        return text or "--"

    return ".".join(str(byte) for byte in raw)


def _query_once(host: str) -> dict:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(config.NTP_TIMEOUT_SECONDS)

        t1 = time.time()
        sock.sendto(_PACKET, (host, config.NTP_PORT))
        response, _address = sock.recvfrom(48)
        t4 = time.time()

    if len(response) < 48:
        raise ValueError("Incomplete NTP response")

    unpacked = struct.unpack("!B B b b 11I", response[0:48])

    li_vn_mode = unpacked[0]
    stratum = unpacked[1]
    poll = unpacked[2]
    precision = unpacked[3]
    root_delay_raw = unpacked[4]
    root_dispersion_raw = unpacked[5]
    reference_id_raw = struct.pack("!I", unpacked[6])
    receive_seconds, receive_fraction = unpacked[11], unpacked[12]
    transmit_seconds, transmit_fraction = unpacked[13], unpacked[14]

    leap_indicator = (li_vn_mode >> 6) & 0x3
    version = (li_vn_mode >> 3) & 0x7

    t2 = _to_unix(receive_seconds + receive_fraction / 2**32)
    t3 = _to_unix(transmit_seconds + transmit_fraction / 2**32)

    offset_seconds = ((t2 - t1) + (t3 - t4)) / 2
    round_trip_seconds = (t4 - t1) - (t3 - t2)

    root_delay_signed = struct.unpack("!i", struct.pack("!I", root_delay_raw & 0xFFFFFFFF))[0]

    return {
        "server": host,
        "port": config.NTP_PORT,
        "reachable": True,
        "error": None,
        "leap_indicator": leap_indicator,
        "leap_indicator_label": LEAP_INDICATOR_LABELS.get(leap_indicator, "Unknown"),
        "version": version,
        "stratum": stratum,
        "stratum_label": "Unsynchronized (kiss-o'-death)"
        if stratum == 0
        else ("Primary reference" if stratum == 1 else f"Secondary reference (stratum {stratum})"),
        "poll_interval_seconds": 2**poll if -20 <= poll <= 20 else None,
        "precision_seconds": 2.0**precision if -64 <= precision <= 0 else None,
        "root_delay_ms": round(root_delay_signed / 65536 * 1000, 3),
        "root_dispersion_ms": round(root_dispersion_raw / 65536 * 1000, 3),
        "reference_id": _decode_reference_id(stratum, reference_id_raw),
        "reference_time_utc": datetime.datetime.fromtimestamp(
            t3, tz=datetime.timezone.utc
        ).isoformat(),
        "offset_ms": round(offset_seconds * 1000, 3),
        "round_trip_ms": round(round_trip_seconds * 1000, 3),
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def query_ntp_status(force: bool = False) -> dict:
    """Return cached NTP diagnostics or query configured servers immediately."""

    global _cached_result, _cached_at

    with _cache_lock:
        if (
            not force
            and _cached_result is not None
            and (time.monotonic() - _cached_at) < config.NTP_CACHE_SECONDS
        ):
            return _cached_result

    hosts_to_try = [config.NTP_SERVER]
    if config.NTP_SERVER_FALLBACK_IP and config.NTP_SERVER_FALLBACK_IP != config.NTP_SERVER:
        hosts_to_try.append(config.NTP_SERVER_FALLBACK_IP)

    last_error = None

    for host in hosts_to_try:
        try:
            result = _query_once(host)
            with _cache_lock:
                _cached_result = result
                _cached_at = time.monotonic()
            return result
        except (OSError, ValueError, struct.error) as exc:
            last_error = f"{host}: {exc}"
            continue

    failure = {
        "server": config.NTP_SERVER,
        "port": config.NTP_PORT,
        "reachable": False,
        "error": last_error or "Unknown NTP error",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    with _cache_lock:
        _cached_result = failure
        _cached_at = time.monotonic()

    return failure

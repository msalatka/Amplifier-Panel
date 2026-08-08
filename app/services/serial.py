"""Serial acquisition, amplifier enrichment, and warning-state evaluation."""

import datetime
import pathlib
import time

import serial

from app.core import config, device_schema, state, validation
from app.protocols import amplifier as amplifier_protocol
from app.services import database as database_service
from app.services import snmp as snmp_service
from app.services import syslog as syslog_service

MEASUREMENT_FIELDS = device_schema.AMPLIFIER_MEASUREMENT_FIELDS
FIELD_LABELS = device_schema.AMPLIFIER_FIELD_LABELS


def write_gain_command(ser, gain_set: float) -> None:
    """Validate and write one gain command to an open amplifier serial port."""

    gain_set = validation.validate_gain_set(
        gain_set,
        config.GAIN_SET_MIN,
        config.GAIN_SET_MAX,
    )
    ser.write(amplifier_protocol.build_gain_command(gain_set))
    ser.flush()


def enrich_data(data: dict) -> dict:
    """Add derived gain values to an amplifier measurement when possible."""

    if "gain_set" not in data:
        data["gain_set"] = state.last_known_gain_set

    if "gain_actual" not in data:
        required = ["PiA", "PoA", "PiB", "PoB"]

        if all(key in data for key in required):
            gain_a = data["PoA"] - data["PiA"]
            gain_b = data["PoB"] - data["PiB"]
            data["gain_actual"] = (gain_a + gain_b) / 2.0

    if "gain_delta" not in data:
        if "gain_set" in data and "gain_actual" in data:
            data["gain_delta"] = data["gain_set"] - data["gain_actual"]

    return data


def is_command_response(data: dict) -> bool:
    """Return whether parsed amplifier data represents a command response."""

    return "status" in data and not any(field in data for field in MEASUREMENT_FIELDS)


def build_limit_errors(data: dict, now: str) -> list:
    """Evaluate one amplifier sample against configured warning thresholds."""

    errors = []
    settings = state.dashboard_settings
    warn_limits = settings["warn_limits"]

    if "gain_delta" in data:
        allowed = float(settings["gain_tolerance"])
        delta = float(data["gain_delta"])

        if abs(delta) > allowed:
            errors.append(
                {
                    "time": now,
                    "field": "gain_actual",
                    "kind": "gain_tolerance",
                    "label": FIELD_LABELS["gain_actual"],
                    "value": float(data.get("gain_actual", 0)),
                    "target": float(data.get("gain_set", state.last_known_gain_set)),
                    "delta": delta,
                    "allowed": allowed,
                    "message": "Gain outside tolerance",
                }
            )

    for field, limits in warn_limits.items():
        if field not in data:
            continue

        value = float(data[field])
        min_value = limits.get("min")
        max_value = limits.get("max")

        if min_value is not None and value < float(min_value):
            delta = value - float(min_value)
            errors.append(
                {
                    "time": now,
                    "field": field,
                    "kind": "min",
                    "label": FIELD_LABELS.get(field, field),
                    "value": value,
                    "target": float(min_value),
                    "delta": delta,
                    "allowed": 0,
                    "message": f"{field} below MIN threshold {float(min_value):.2f}",
                }
            )

        if max_value is not None and value > float(max_value):
            delta = value - float(max_value)
            errors.append(
                {
                    "time": now,
                    "field": field,
                    "kind": "max",
                    "label": FIELD_LABELS.get(field, field),
                    "value": value,
                    "target": float(max_value),
                    "delta": delta,
                    "allowed": 0,
                    "message": f"{field} above MAX threshold {float(max_value):.2f}",
                }
            )

    return errors


def warning_key(error: dict) -> tuple:
    """Return the stable field-and-kind identity of one warning."""

    return (error.get("field"), error.get("kind"))


def update_warning_state(
    current_errors: list[dict],
    now: str,
    current_data: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Reconcile evaluated warnings and return newly opened and cleared events."""

    current_by_key = {warning_key(error): error for error in current_errors}
    opened_events = []
    cleared_events = []

    with state.state_lock:
        previous_keys = set(state.active_warnings)
        current_keys = set(current_by_key)

        for key in previous_keys - current_keys:
            previous = dict(state.active_warnings.pop(key))
            state.acknowledged_warning_keys.discard(key)
            field = previous.get("field")
            if current_data is not None and field in current_data:
                previous["value"] = float(current_data[field])
                if previous.get("kind") == "gain_tolerance":
                    previous["delta"] = float(current_data.get("gain_delta", 0))
                elif previous.get("target") is not None:
                    previous["delta"] = previous["value"] - float(previous["target"])
            previous["event"] = "CLEARED"
            previous["event_time"] = now
            previous["cleared_at"] = now
            try:
                opened_at = datetime.datetime.fromisoformat(previous["opened_at"])
                cleared_at = datetime.datetime.fromisoformat(now)
                previous["duration_seconds"] = max(0.0, (cleared_at - opened_at).total_seconds())
            except (KeyError, TypeError, ValueError):
                previous["duration_seconds"] = None
            cleared_events.append(previous)

        for key, error in current_by_key.items():
            if key in state.active_warnings:
                previous = state.active_warnings[key]
                active = {
                    **error,
                    "event": "OPEN",
                    "event_time": previous["event_time"],
                    "opened_at": previous["opened_at"],
                    "acknowledged": key in state.acknowledged_warning_keys,
                }
            else:
                active = {
                    **error,
                    "event": "OPEN",
                    "event_time": now,
                    "opened_at": now,
                    "acknowledged": False,
                }
                opened_events.append(dict(active))
            state.active_warnings[key] = active

    return opened_events, cleared_events


def available_serial_ports() -> list[str]:
    """List serial device nodes and stable by-id links visible on the host."""

    ports = set()
    for base in (pathlib.Path("/dev"),):
        for pattern in ("ttyACM*", "ttyUSB*", "ttyS*", "ttyO*"):
            ports.update(str(path) for path in base.glob(pattern))
        serial_by_id = base / "serial" / "by-id"
        if serial_by_id.is_dir():
            ports.update(str(path) for path in serial_by_id.iterdir())
    return sorted(ports)


def _serial_reader_session(port: str):
    try:
        ser = serial.Serial(port=port, baudrate=config.SERIAL_BAUDRATE, timeout=1)

        with state.serial_lock:
            state.serial_port = ser

        with state.state_lock:
            state.serial_connected = True
            state.serial_error = None

        print(f"Connected to serial port {port}")

        time.sleep(2)

        with state.state_lock:
            restored_gain_set = state.last_known_gain_set

        ser.reset_input_buffer()
        write_gain_command(ser, restored_gain_set)
        print(f"Restored gain_set={restored_gain_set:.2f}")

        while not state.stop_event.is_set():
            line = ser.readline().decode("utf-8", errors="replace").strip()

            if not line:
                continue

            data = amplifier_protocol.parse_line(line)

            if not data:
                continue

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()

            if is_command_response(data):
                with state.state_lock:
                    if "gain_set" in data:
                        try:
                            state.save_persisted_gain_set(data["gain_set"])
                        except ValueError:
                            print("Ignoring out-of-range gain_set in command response.")
                        except OSError as exc:
                            print(f"Persisted state write failed: {exc}")

                    state.serial_connected = True
                    state.serial_error = None

                print("Command response:", data)
                continue

            data = enrich_data(data)

            if "gain_set" in data:
                try:
                    state.save_persisted_gain_set(data["gain_set"])
                except ValueError:
                    print("Ignoring out-of-range gain_set in measurement.")
                except OSError as exc:
                    print(f"Persisted state write failed: {exc}")

            limit_errors = build_limit_errors(data, now)

            with state.state_lock:
                state.latest_data = data
                state.last_update = now
                state.serial_connected = True
                state.serial_error = None

            # The same receive time is used for live state, warnings and SQLite.
            database_service.write_measurement(data, now)

            opened_warnings, cleared_warnings = update_warning_state(
                limit_errors,
                now,
                data,
            )
            for warning in opened_warnings:
                syslog_service.send_warning_event("OPEN", warning)
                try:
                    snmp_service.send_trap(warning)
                except Exception as exc:
                    print("SNMP trap send failed:", exc)
            for warning in cleared_warnings:
                syslog_service.send_warning_event("CLEARED", warning)

    except (serial.SerialException, OSError, TypeError, ValueError) as e:
        with state.state_lock:
            state.serial_connected = False
            state.serial_error = str(e)

        print("Serial port error:", e)

    finally:
        with state.serial_lock:
            if state.serial_port is not None:
                try:
                    state.serial_port.close()
                except serial.SerialException:
                    pass

                state.serial_port = None

        with state.state_lock:
            state.serial_connected = False


def serial_reader_loop():
    """Run the serial worker for the active device profile until shutdown."""

    if config.DEVICE_PROFILE == "fts-ls":
        from app.services import fts_ls

        fts_ls.reader_loop()
        return
    while not state.stop_event.is_set():
        with state.state_lock:
            port = str(state.service_settings["serial_port"])
        _serial_reader_session(port)
        if state.stop_event.is_set():
            break
        state.serial_reconnect_event.wait(timeout=2)
        state.serial_reconnect_event.clear()


def reconnect(port: str) -> None:
    """Interrupt the active serial session so the worker reconnects to a port."""

    with state.state_lock:
        state.serial_connected = False
        state.serial_error = f"Switching to {port}"
    state.serial_reconnect_event.set()
    with state.serial_lock:
        if state.serial_port is not None:
            try:
                state.serial_port.close()
            except (serial.SerialException, OSError):
                pass


def send_gain_set(gain_set: float):
    """Send, persist, and record a validated amplifier gain setpoint."""

    if config.DEVICE_PROFILE != "amplifier":
        raise RuntimeError("Gain setpoint is only available for the amplifier profile")
    gain_set = validation.validate_gain_set(
        gain_set,
        config.GAIN_SET_MIN,
        config.GAIN_SET_MAX,
    )
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with state.serial_lock:
        if state.serial_port is None:
            raise RuntimeError("Serial port is not open")

        write_gain_command(state.serial_port, gain_set)

    with state.state_lock:
        state.last_known_gain_set = float(gain_set)
        state.save_persisted_gain_set(state.last_known_gain_set)

    database_service.write_setpoint(gain_set, timestamp)

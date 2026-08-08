"""Exclusive FTS-LS serial-session worker and synchronous command queue."""

import copy
import dataclasses
import datetime
import queue
import re
import threading
import time
from typing import Any

try:
    import serial
except ModuleNotFoundError:  # Allows parser/unit tests without runtime extras.
    serial = None

from app.core import config, state
from app.protocols import fts_ls as fts_protocol
from app.services import database as database_service


@dataclasses.dataclass
class PendingCommand:
    """One validated station command awaiting execution by the serial worker."""

    command: str
    action: str
    event: threading.Event = dataclasses.field(default_factory=threading.Event)
    output: str = ""
    error: str | None = None


command_queue: queue.Queue[PendingCommand] = queue.Queue(maxsize=32)
SERIAL_ERRORS = (
    (RuntimeError, OSError, ValueError)
    if serial is None
    else (RuntimeError, serial.SerialException, OSError, ValueError)
)


class SerialCliSession:
    """Own one authenticated, prompt-oriented serial session with the station."""

    def __init__(self, port: str):
        if serial is None:
            raise RuntimeError("pyserial is not installed")
        self.serial = serial.Serial(
            port=port,
            baudrate=config.SERIAL_BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.25,
            write_timeout=2,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.exec_mode = False

    def close(self) -> None:
        """Close the physical serial connection owned by this session."""

        self.serial.close()

    def _write_line(self, value: str) -> None:
        self.serial.write((value + "\r\n").encode("utf-8"))
        self.serial.flush()

    def _read_response(self, max_seconds: float = 8.0) -> str:
        chunks = []
        deadline = time.monotonic() + max_seconds
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            chunk = self.serial.readline()
            if chunk:
                chunks.append(chunk.decode("utf-8", errors="replace"))
                last_data = time.monotonic()
                joined = "".join(chunks)
                if fts_protocol.PROMPT_PATTERN.search(joined.replace("\r", "")):
                    break
            elif chunks and time.monotonic() - last_data >= 0.6:
                break
        return "".join(chunks).strip()

    def login(self) -> None:
        """Synchronize with the station prompt and authenticate when requested."""
        self.serial.reset_input_buffer()
        self._write_line("")
        output = self._read_response(3)
        lowered = output.lower()
        if "login:" in lowered or "username:" in lowered:
            self._write_line(config.FTS_LS_USERNAME)
            output += "\n" + self._read_response(3)
            lowered = output.lower()
        if "password:" in lowered:
            if not config.FTS_LS_PASSWORD:
                raise RuntimeError("FTS_LS_PASSWORD is not configured")
            self._write_line(config.FTS_LS_PASSWORD)
            output += "\n" + self._read_response(5)
        if re.search(r"incorrect|authentication failed|login failed", output, re.IGNORECASE):
            raise RuntimeError("FTS-LS console authentication failed")

    def command(self, command: str, *, exec_required: bool = False) -> str:
        """Execute a command and return response text without the trailing prompt.

        Args:
            command: Validated station CLI command.
            exec_required: Enter the privileged EXEC context before sending.

        Raises:
            RuntimeError: If the station rejects the command or changes prompts in
                a way that prevents a complete response from being read.
        """
        if exec_required and not self.exec_mode:
            self._write_line("exec")
            exec_output = self._read_response()
            if re.search(r"busy|denied|not allowed|error", exec_output, re.IGNORECASE):
                raise RuntimeError(exec_output or "FTS-LS EXEC mode is unavailable")
            self.exec_mode = True
        self._write_line(command)
        output = self._read_response(15 if command in fts_protocol.LONG_RESPONSE_COMMANDS else 8)
        if command in fts_protocol.CONSOLE_CONFIRMATION_COMMANDS and re.search(
            r"confirm|are you sure|\[[yY]/[nN]\]|yes/no",
            output,
            re.IGNORECASE,
        ):
            self._write_line("yes")
            output += "\n" + self._read_response(35 if command == "power reset" else 15)
        if re.search(r"unknown command|invalid command|permission denied", output, re.IGNORECASE):
            raise RuntimeError(output)
        return output


def submit_action(action: str, parameters: dict[str, Any], timeout: float = 20) -> dict:
    """Validate and synchronously submit an API action to the serial worker.

    The bounded queue prevents HTTP traffic from creating unbounded work. The
    serial reader remains the only owner of the physical session.
    """
    command = fts_protocol.build_command(action, parameters)
    pending = PendingCommand(command=command, action=action)
    try:
        command_queue.put_nowait(pending)
    except queue.Full as exc:
        raise RuntimeError("The FTS-LS command queue is full.") from exc
    if not pending.event.wait(timeout):
        raise RuntimeError("The FTS-LS device did not answer in time.")
    if pending.error:
        raise RuntimeError(pending.error)
    return {"status": "ok", "action": action, "output": pending.output}


def _process_commands(session: SerialCliSession) -> None:
    while True:
        try:
            pending = command_queue.get_nowait()
        except queue.Empty:
            return
        try:
            pending.output = session.command(
                pending.command,
                exec_required=pending.action != "ping",
            )
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with state.state_lock:
                state.fts_ls_status["last_command"] = {
                    "action": pending.action,
                    "time": now,
                    "output": pending.output,
                }
        except Exception as exc:
            pending.error = str(exc)
        finally:
            if session.exec_mode:
                try:
                    session.command("back")
                except Exception:
                    pass
                session.exec_mode = False
            pending.event.set()
            command_queue.task_done()


def _poll(session: SerialCliSession) -> None:
    with state.state_lock:
        status = copy.deepcopy(state.fts_ls_status)
    summary_output = session.command(fts_protocol.STATUS_COMMAND)
    if not re.search(r"\b(?:Uplink|UL|Port\s*[1-7]|P[1-7])\b", summary_output, re.IGNORECASE):
        raise RuntimeError("FTS-LS did not return a valid 'show status' response")
    status = fts_protocol.parse_show_status(summary_output, status)
    for section in fts_protocol.DETAIL_SECTIONS:
        try:
            fts_protocol.apply_detailed_output(
                status,
                section,
                session.command(f"show {section}"),
            )
        except SERIAL_ERRORS:
            raise
    for name, command in fts_protocol.SYSTEM_COMMANDS:
        output = session.command(command)
        parsed = fts_protocol.parse_key_values(output)
        status["system"][name] = parsed or {"raw": output}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with state.state_lock:
        state.fts_ls_status = status
        state.latest_data = {"device_profile": "fts-ls", "fts_ls": copy.deepcopy(status)}
        state.last_update = now
        state.serial_connected = True
        state.serial_error = None
    database_service.write_device_snapshot("fts-ls", status, now)


def reader_loop() -> None:
    """Maintain the FTS-LS connection until application shutdown.

    Transport and protocol errors close the current session, expose a disconnected
    state to the API and retry with a bounded delay.
    """
    while not state.stop_event.is_set():
        with state.state_lock:
            port = str(state.service_settings["serial_port"])
        session = None
        try:
            session = SerialCliSession(port)
            with state.serial_lock:
                state.serial_port = session.serial
            session.login()
            with state.state_lock:
                state.serial_connected = True
                state.serial_error = None
            next_poll = 0.0
            while not state.stop_event.is_set():
                _process_commands(session)
                if time.monotonic() >= next_poll:
                    _poll(session)
                    next_poll = time.monotonic() + config.FTS_LS_POLL_SECONDS
                state.stop_event.wait(0.2)
        except SERIAL_ERRORS as exc:
            with state.state_lock:
                state.serial_connected = False
                state.serial_error = str(exc)
            print("FTS-LS serial error:", exc)
        finally:
            if session is not None:
                try:
                    session.close()
                except SERIAL_ERRORS:
                    pass
            with state.serial_lock:
                state.serial_port = None
            with state.state_lock:
                state.serial_connected = False
        if not state.stop_event.is_set():
            state.serial_reconnect_event.wait(timeout=2)
            state.serial_reconnect_event.clear()

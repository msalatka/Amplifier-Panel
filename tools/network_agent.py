"""Privileged agent for checkpointed NetworkManager configuration changes."""

from __future__ import annotations

import argparse
import copy
import http.server
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable


class NetworkAgentError(RuntimeError):
    """A validated network request or NetworkManager operation failed."""

    pass


@dataclass
class CommandResult:
    """Captured result of a bounded host command."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CommandResult]
CHECKPOINT_TIMEOUT_SECONDS = 60
CHECKPOINT_DELETE_NEW_CONNECTIONS = 0x02
NETWORK_MANAGER_SERVICE = "org.freedesktop.NetworkManager"
NETWORK_MANAGER_PATH = "/org/freedesktop/NetworkManager"
NETWORK_MANAGER_INTERFACE = "org.freedesktop.NetworkManager"
_pending_checkpoints: dict[str, dict[str, Any]] = {}
_checkpoint_lock = threading.Lock()


def _default_runner(command: list[str]) -> CommandResult:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _run(command: list[str], runner: Runner) -> str:
    try:
        result = runner(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NetworkAgentError(f"Could not run {command[0]}: {exc}") from exc
    if result.returncode != 0:
        raise NetworkAgentError(
            result.stderr.strip() or result.stdout.strip() or "Unknown command error"
        )
    return result.stdout.strip()


def _prefix(mask: str) -> int:
    value = str(mask).strip().removeprefix("/")
    if value.isdigit() and 0 <= int(value) <= 32:
        return int(value)
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{value}").prefixlen
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
        raise NetworkAgentError("Invalid subnet mask.") from exc


def _nmcli(arguments: list[str], runner: Runner) -> str:
    return _run(["nmcli", *arguments], runner).strip()


def _busctl(method: str, signature: str, arguments: list[str], runner: Runner) -> str:
    return _run(
        [
            "busctl",
            "call",
            NETWORK_MANAGER_SERVICE,
            NETWORK_MANAGER_PATH,
            NETWORK_MANAGER_INTERFACE,
            method,
            signature,
            *arguments,
        ],
        runner,
    )


def _device_dbus_path(interface: str, runner: Runner) -> str:
    output = _busctl("GetDeviceByIpIface", "s", [interface], runner)
    parts = shlex.split(output)
    path = parts[1] if len(parts) == 2 and parts[0] == "o" else ""
    if not path.startswith("/org/freedesktop/NetworkManager/Devices/"):
        raise NetworkAgentError("NetworkManager did not return a valid device path.")
    return path


def _create_checkpoint(interface: str, runner: Runner) -> str:
    device_path = _device_dbus_path(interface, runner)
    output = _busctl(
        "CheckpointCreate",
        "aouu",
        [
            "1",
            device_path,
            str(CHECKPOINT_TIMEOUT_SECONDS),
            str(CHECKPOINT_DELETE_NEW_CONNECTIONS),
        ],
        runner,
    )
    parts = shlex.split(output)
    if (
        len(parts) != 2
        or parts[0] != "o"
        or not parts[1].startswith("/org/freedesktop/NetworkManager/Checkpoint/")
    ):
        raise NetworkAgentError("NetworkManager returned an invalid checkpoint.")
    return parts[1]


def _checkpoint_call(method: str, checkpoint_path: str, runner: Runner) -> None:
    _busctl(method, "o", [checkpoint_path], runner)


def _prune_expired_checkpoints() -> None:
    now = time.monotonic()
    with _checkpoint_lock:
        expired = [
            token
            for token, checkpoint in _pending_checkpoints.items()
            if checkpoint["expires_at"] <= now
        ]
        for token in expired:
            _pending_checkpoints.pop(token, None)


def _ensure_no_pending_checkpoint() -> None:
    _prune_expired_checkpoints()
    with _checkpoint_lock:
        if _pending_checkpoints:
            raise NetworkAgentError(
                "A network change is already awaiting confirmation. "
                "Confirm it or wait for automatic rollback."
            )


def _register_checkpoint(
    checkpoint_path: str,
    interface: str,
    state_snapshot: dict[str, Any],
) -> str:
    token = secrets.token_urlsafe(32)
    with _checkpoint_lock:
        _pending_checkpoints[token] = {
            "path": checkpoint_path,
            "interface": interface,
            "expires_at": time.monotonic() + CHECKPOINT_TIMEOUT_SECONDS,
            "state": copy.deepcopy(state_snapshot),
        }
    return token


def _connection_details(interface: str, runner: Runner) -> dict[str, Any]:
    try:
        connection = _nmcli(["-g", "GENERAL.CONNECTION", "device", "show", interface], runner)
        if not connection or connection == "--":
            return {"connection": "", "mode": "unknown", "dns": []}
        method = _nmcli(["-g", "ipv4.method", "connection", "show", connection], runner)
        dns_text = _nmcli(["-g", "IP4.DNS", "device", "show", interface], runner)
        mode = "dhcp" if method == "auto" else "static" if method == "manual" else method
        return {
            "connection": connection,
            "mode": mode,
            "dns": [v.strip() for v in dns_text.splitlines() if v.strip()],
        }
    except NetworkAgentError:
        return {"connection": "", "mode": "unknown", "dns": []}


def _route_interface(client_ip: str, runner: Runner) -> str:
    try:
        address = ipaddress.ip_address(str(client_ip).strip())
    except ValueError as exc:
        raise NetworkAgentError(
            "Could not determine the administrator connection address."
        ) from exc
    if address.is_loopback:
        return ""
    routes = json.loads(_run(["ip", "-j", "route", "get", str(address)], runner) or "[]")
    interface = str(routes[0].get("dev", "")).strip() if routes else ""
    if interface == "lo":
        return ""
    if not interface:
        raise NetworkAgentError(
            "Could not determine which interface carries the administrator connection."
        )
    return interface


def get_network_state(
    runner: Runner | None = None,
    client_ip: str = "",
) -> dict[str, Any]:
    """Read interfaces and identify the route used by the dashboard client."""
    runner = runner or _default_runner
    if runner is _default_runner and not shutil.which("ip"):
        raise NetworkAgentError("The host ip utility is unavailable.")
    addresses = json.loads(_run(["ip", "-j", "address", "show"], runner) or "[]")
    routes = json.loads(_run(["ip", "-j", "route", "show", "default"], runner) or "[]")
    default_routes = {route.get("dev"): route for route in routes if route.get("dev")}
    nmcli_available = runner is not _default_runner or shutil.which("nmcli") is not None
    interfaces = []
    for item in addresses:
        name = str(item.get("ifname", ""))
        if not name or name == "lo":
            continue
        ipv4 = next(
            (entry for entry in item.get("addr_info", []) if entry.get("family") == "inet"), None
        )
        prefix = int(ipv4.get("prefixlen", 0)) if ipv4 else None
        details = (
            _connection_details(name, runner)
            if nmcli_available
            else {"connection": "", "mode": "unknown", "dns": []}
        )
        interfaces.append(
            {
                "name": name,
                "mac": item.get("address", ""),
                "state": str(item.get("operstate", "unknown")).lower(),
                "ip_address": ipv4.get("local", "") if ipv4 else "",
                "prefix": prefix,
                "netmask": str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
                if prefix is not None
                else "",
                "gateway": default_routes.get(name, {}).get("gateway", ""),
                **details,
            }
        )
    selected = next(iter(default_routes), "") or (interfaces[0]["name"] if interfaces else "")
    access_interface = _route_interface(client_ip, runner) if client_ip else ""
    if not nmcli_available:
        backend, message = (
            "read-only",
            "NetworkManager is unavailable on the host; settings are read-only.",
        )
    else:
        backend, message = "NetworkManager", ""
    hostname = socket.gethostname().strip().rstrip(".")
    mdns_hostname = hostname if hostname.lower().endswith(".local") else f"{hostname}.local"
    return {
        "hostname": hostname,
        "mdns_hostname": mdns_hostname,
        "backend": backend,
        "supported": nmcli_available,
        "message": message,
        "selected_interface": selected,
        "access_interface": access_interface,
        "interfaces": interfaces,
    }


def apply_network_settings(payload: dict[str, Any], runner: Runner | None = None) -> dict[str, Any]:
    """Apply settings behind a checkpoint and return a confirmation token."""
    runner = runner or _default_runner
    if runner is _default_runner and not shutil.which("nmcli"):
        raise NetworkAgentError("NetworkManager (nmcli) is not installed on the host.")
    if runner is _default_runner and not shutil.which("busctl"):
        raise NetworkAgentError("The host busctl utility is unavailable.")
    _ensure_no_pending_checkpoint()
    current = get_network_state(runner)
    interface = str(payload.get("interface", "")).strip()
    requester_ip = str(payload.get("_requester_ip", "")).strip()
    mode = str(payload.get("mode", "")).strip().lower()
    known = {item["name"]: item for item in current["interfaces"]}
    if interface not in known:
        raise NetworkAgentError("The selected host interface does not exist.")
    access_interface = _route_interface(requester_ip, runner)
    if access_interface and interface != access_interface:
        raise NetworkAgentError(
            f"This Amp Panel connection uses interface {access_interface}. "
            f"Changing {interface} from this session cannot safely validate reachability."
        )
    if mode not in {"dhcp", "static"}:
        raise NetworkAgentError("Select DHCP or static configuration.")
    connection = known[interface].get("connection", "")
    create_connection = not connection
    if not connection:
        if _nmcli(["-g", "GENERAL.TYPE", "device", "show", interface], runner) != "ethernet":
            raise NetworkAgentError("Only host Ethernet interfaces can be configured.")
        connection = f"amp-{interface}"
    command = ["nmcli", "connection", "modify", connection]
    if mode == "dhcp":
        command.extend(
            ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""]
        )
    else:
        dns_values = payload.get("dns", [])
        if isinstance(dns_values, str):
            dns_values = [v.strip() for v in dns_values.replace(",", " ").split()]
        try:
            address = ipaddress.IPv4Interface(
                f"{str(payload.get('ip_address', '')).strip()}/{_prefix(str(payload.get('netmask', '')))}"
            )
            gateway = ipaddress.IPv4Address(str(payload.get("gateway", "")).strip())
            dns = [str(ipaddress.IPv4Address(str(v).strip())) for v in dns_values if str(v).strip()]
        except ipaddress.AddressValueError as exc:
            raise NetworkAgentError(
                "The IP address, gateway, or DNS has an invalid format."
            ) from exc
        if gateway not in address.network:
            raise NetworkAgentError("The gateway must be in the same subnet as the IP address.")
        command.extend(
            [
                "ipv4.method",
                "manual",
                "ipv4.addresses",
                str(address),
                "ipv4.gateway",
                str(gateway),
                "ipv4.dns",
                ",".join(dns),
            ]
        )
    checkpoint_path = _create_checkpoint(interface, runner)
    try:
        if create_connection:
            _run(
                [
                    "nmcli",
                    "connection",
                    "add",
                    "type",
                    "ethernet",
                    "ifname",
                    interface,
                    "con-name",
                    connection,
                ],
                runner,
            )
        _run(command, runner)
        _run(["nmcli", "connection", "up", connection], runner)
        result = get_network_state(runner, requester_ip)
    except Exception:
        try:
            _checkpoint_call("CheckpointRollback", checkpoint_path, runner)
        except NetworkAgentError:
            pass
        raise

    token = _register_checkpoint(checkpoint_path, interface, result)
    result["confirmation"] = {
        "status": "pending",
        "token": token,
        "expires_in_seconds": CHECKPOINT_TIMEOUT_SECONDS,
    }
    return result


def confirm_network_settings(
    token: str,
    runner: Runner | None = None,
    client_ip: str = "",
) -> dict[str, Any]:
    """Commit a checkpoint after verifying that the confirming client route matches."""
    runner = runner or _default_runner
    _prune_expired_checkpoints()
    with _checkpoint_lock:
        checkpoint = _pending_checkpoints.get(str(token))
    if checkpoint is None:
        raise NetworkAgentError("The network confirmation token is invalid or has expired.")
    access_interface = _route_interface(client_ip, runner)
    if access_interface and access_interface != checkpoint["interface"]:
        raise NetworkAgentError(
            "The confirmation did not arrive through the changed interface. "
            "The previous settings will be restored automatically."
        )
    try:
        _checkpoint_call("CheckpointDestroy", checkpoint["path"], runner)
    except NetworkAgentError:
        raise
    else:
        with _checkpoint_lock:
            _pending_checkpoints.pop(str(token), None)

    result = copy.deepcopy(checkpoint["state"])
    result["confirmation"] = {"status": "confirmed"}
    return result


class _UnixServer(getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    allow_reuse_address = True


class Handler(http.server.BaseHTTPRequestHandler):
    """Serve the restricted network API over a local Unix socket."""

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Return current network state for the supported endpoint."""

        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/v1/network":
            self._send(404, {"detail": "Not found."})
            return
        try:
            query = urllib.parse.parse_qs(parsed.query)
            self._send(
                200,
                get_network_state(client_ip=query.get("client_ip", [""])[0]),
            )
        except NetworkAgentError as exc:
            self._send(503, {"detail": str(exc)})

    def do_POST(self) -> None:
        """Apply or confirm one validated checkpointed network change."""

        if self.path not in {"/v1/network", "/v1/network/confirm"}:
            self._send(404, {"detail": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16384:
                raise NetworkAgentError("Invalid request size.")
            payload = json.loads(self.rfile.read(length))
            if self.path == "/v1/network":
                result = apply_network_settings(payload)
            else:
                token = payload.get("token")
                if not isinstance(token, str) or not token:
                    raise NetworkAgentError("A confirmation token is required.")
                result = confirm_network_settings(
                    token,
                    client_ip=str(payload.get("_requester_ip", "")).strip(),
                )
            self._send(200, result)
        except (NetworkAgentError, json.JSONDecodeError) as exc:
            self._send(400, {"detail": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        """Prefix HTTP request messages for the system service journal."""

        print(f"network-agent: {format % args}", flush=True)


def main() -> None:
    """Create the protected Unix socket and serve network-agent requests."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/amp-panel/network-agent.sock")
    args = parser.parse_args()
    os.makedirs(os.path.dirname(args.socket), mode=0o750, exist_ok=True)
    if os.path.exists(args.socket):
        os.unlink(args.socket)
    with _UnixServer(args.socket, Handler) as server:
        os.chmod(args.socket, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()

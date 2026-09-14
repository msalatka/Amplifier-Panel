"""Client for privileged network operations exposed by the local host agent."""

from __future__ import annotations

import http.client
import json
import os
import socket
from typing import Any


class NetworkError(RuntimeError):
    """An error returned by, or encountered while contacting, the host agent."""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 10):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        """Open the HTTP connection through the configured Unix socket."""

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    socket_path = os.getenv("NETWORK_AGENT_SOCKET", "/run/amp-panel/network-agent.sock")
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    connection = _UnixConnection(socket_path, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
    except (OSError, http.client.HTTPException) as exc:
        raise NetworkError(f"Host network agent is unavailable: {exc}") from exc
    finally:
        connection.close()

    try:
        result = json.loads(raw.decode() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NetworkError("Host network agent returned an invalid response.") from exc
    if response.status >= 400:
        raise NetworkError(str(result.get("detail", "Network operation failed.")), response.status)
    return result


def get_network_state(client_ip: str = "") -> dict[str, Any]:
    """Read host network state and route information for an optional client."""

    path = "/v1/network"
    if client_ip:
        from urllib.parse import quote

        path = f"{path}?client_ip={quote(client_ip, safe='')}"
    return _request("GET", path)


def apply_network_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Request a guarded network change from the privileged host agent."""

    return _request("POST", "/v1/network", payload, timeout=45)


def confirm_network_settings(token: str, client_ip: str = "") -> dict[str, Any]:
    """Confirm reachability after a guarded network change."""

    return _request(
        "POST",
        "/v1/network/confirm",
        {"token": token, "_requester_ip": client_ip},
    )

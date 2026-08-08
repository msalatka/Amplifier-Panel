"""Session authorization and security audit helpers for API endpoints."""

import datetime
import json
import secrets

import fastapi
import starlette.requests

from app.core import config, state
from app.services import syslog as syslog_service


def find_access_user(username: str) -> dict | None:
    """Return the local authorization record for an exact username."""

    return next((user for user in state.access_users if user["username"] == username), None)


def normalize_username(username: str) -> str:
    """Strip a username and reject an empty value as an HTTP request error."""

    value = username.strip()
    if not value:
        raise fastapi.HTTPException(status_code=400, detail="Username is required")
    return value


def count_active_administrators() -> int:
    """Count active local users that retain the Administrator role."""

    return sum(
        1 for user in state.access_users if user["role"] == "Administrator" and user["active"]
    )


def get_client_ip(request: starlette.requests.Request) -> str:
    """Return the audited client address, honoring trusted proxy headers only."""

    forwarded_for = request.headers.get("x-forwarded-for") if config.TRUST_PROXY_HEADERS else None
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client is not None else "unknown"


def audit_event(
    request: starlette.requests.Request,
    action: str,
    username: str,
    details: str = "",
) -> None:
    """Write one authenticated API action to the configured audit log."""

    syslog_service.send_audit(
        action=action,
        username=username,
        ip_address=get_client_ip(request),
        details=details,
    )


def _flatten_audit_values(value: dict, prefix: str = "") -> dict:
    flattened = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(_flatten_audit_values(item, path))
        else:
            flattened[path] = item
    return flattened


def audit_changes(before: dict, after: dict, *, redacted: set[str] | None = None) -> str:
    """Describe changed nested values while redacting named sensitive fields."""

    redacted = redacted or set()
    before_values = _flatten_audit_values(before)
    after_values = _flatten_audit_values(after)
    details = []
    for key in sorted(set(before_values) | set(after_values)):
        old_value = before_values.get(key)
        new_value = after_values.get(key)
        if old_value == new_value:
            continue
        if key in redacted or key.split(".")[0] in redacted:
            old_value = new_value = "[REDACTED]"
        details.append(
            f"{key}.before={json.dumps(old_value, ensure_ascii=False)}; "
            f"{key}.after={json.dumps(new_value, ensure_ascii=False)}"
        )
    return "; ".join(details) if details else "no_effective_changes=true"


def create_session(username: str) -> str:
    """Create an in-memory authenticated session and return its random token."""

    token = secrets.token_urlsafe(32)
    state.auth_sessions[token] = {
        "username": username,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return token


def get_current_user(session_token: str | None = fastapi.Cookie(default=None)) -> dict:
    """Resolve a valid session cookie to an active public user record."""

    if not session_token:
        raise fastapi.HTTPException(status_code=401, detail="Not authenticated")
    with state.state_lock:
        session = state.auth_sessions.get(session_token)
        if session is None:
            raise fastapi.HTTPException(status_code=401, detail="Not authenticated")
        created_at = datetime.datetime.fromisoformat(session["created_at"])
        max_age = datetime.timedelta(seconds=config.SESSION_MAX_AGE_SECONDS)
        if datetime.datetime.now(datetime.timezone.utc) - created_at > max_age:
            state.auth_sessions.pop(session_token, None)
            raise fastapi.HTTPException(status_code=401, detail="Session expired")
        user = find_access_user(session["username"])
        if user is None or not user["active"]:
            state.auth_sessions.pop(session_token, None)
            raise fastapi.HTTPException(status_code=401, detail="Not authenticated")
        return state.access_user_public(user)


def require_roles(*allowed_roles: str):
    """Create a FastAPI dependency that permits only the supplied roles."""

    allowed = set(allowed_roles)

    def dependency(current_user: dict = fastapi.Depends(get_current_user)) -> dict:
        """Return the current user when its role is allowed."""

        if current_user["role"] not in allowed:
            raise fastapi.HTTPException(status_code=403, detail="Not allowed")
        return current_user

    return dependency

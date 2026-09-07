"""Authentication, session, and access-control HTTP endpoints."""

import datetime
import typing

import fastapi
import pydantic
import starlette.requests

from app.api import security as api_security
from app.core import config, passwords, state
from app.services import radius as radius_service

router = fastapi.APIRouter()


class LoginRequest(pydantic.BaseModel):
    """Credentials submitted to the selected authentication backend."""

    username: str
    password: str


class AccessUserCreateRequest(pydantic.BaseModel):
    """User record to create, including a password in local-authentication mode."""

    username: str
    role: typing.Literal["Administrator", "Operator", "Viewer"] = "Operator"
    active: bool = True
    password: str | None = None


class AccessUserUpdateRequest(pydantic.BaseModel):
    """Optional role, activation, and local-password changes for an existing user."""

    role: typing.Literal["Administrator", "Operator", "Viewer"] | None = None
    active: bool | None = None
    password: str | None = None


@router.post("/api/auth/login")
def login(
    login_request: LoginRequest,
    response: fastapi.Response,
    request: starlette.requests.Request,
):
    """Authenticate a panel user through the configured backend and create a session."""

    username = api_security.normalize_username(login_request.username)
    client_ip = api_security.get_client_ip(request)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()

    with state.state_lock:
        failures = state.login_failures.setdefault(client_ip, [])
        cutoff = now - config.LOGIN_WINDOW_SECONDS
        failures[:] = [timestamp for timestamp in failures if timestamp >= cutoff]
        if len(failures) >= config.LOGIN_MAX_ATTEMPTS:
            api_security.audit_event(request, "login_rate_limited", username)
            raise fastapi.HTTPException(
                status_code=429, detail="Too many login attempts. Try again later"
            )
        user = api_security.find_access_user(username)
        if user is None or not user["active"]:
            failures.append(now)
            api_security.audit_event(request, "login_failed", username, "unknown_or_inactive_user")
            raise fastapi.HTTPException(status_code=401, detail="Invalid username or password")

    if config.AUTH_MODE == "local":
        authenticated = passwords.verify_password(
            login_request.password, user.get("password_hash"), user.get("password_salt")
        )
    else:
        try:
            authenticated = radius_service.authenticate(username, login_request.password)
        except radius_service.RadiusUnavailableError as exc:
            api_security.audit_event(request, "login_radius_unavailable", username, str(exc))
            raise fastapi.HTTPException(
                status_code=503,
                detail="Authentication server (RADIUS) is unavailable. Try again later.",
            ) from exc

    with state.state_lock:
        if not authenticated:
            state.login_failures.setdefault(client_ip, []).append(now)
            api_security.audit_event(request, "login_failed", username, f"{config.AUTH_MODE}_reject")
            raise fastapi.HTTPException(status_code=401, detail="Invalid username or password")
        state.login_failures.pop(client_ip, None)
        token = api_security.create_session(username)
        public_user = state.access_user_public(user)

    api_security.audit_event(request, "login_success", username, f"role={public_user['role']}")
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="strict",
        secure=config.SESSION_COOKIE_SECURE,
        max_age=config.SESSION_MAX_AGE_SECONDS,
    )
    return {"user": public_user, "authentication_mode": config.AUTH_MODE}


@router.get("/api/auth/me")
def auth_me(current_user: dict = fastapi.Depends(api_security.get_current_user)):
    """Return the public record of the currently authenticated user."""

    return {"user": current_user}


@router.post("/api/auth/logout")
def logout(
    response: fastapi.Response,
    request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.get_current_user),
    session_token: str | None = fastapi.Cookie(default=None),
):
    """Invalidate the current session and remove its browser cookie."""

    with state.state_lock:
        if session_token:
            state.auth_sessions.pop(session_token, None)
    api_security.audit_event(request, "logout", current_user["username"])
    response.delete_cookie("session_token")
    return {"status": "ok"}


@router.get("/api/access/users")
def get_access_users(
    _current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """List local role assignments visible to administrators."""

    with state.state_lock:
        return {
            "users": [state.access_user_public(user) for user in state.access_users],
            "authentication_mode": config.AUTH_MODE,
        }


@router.post("/api/access/users")
def create_access_user(
    request: AccessUserCreateRequest,
    http_request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Create a local user or a RADIUS role assignment."""

    username = api_security.normalize_username(request.username)
    with state.state_lock:
        if api_security.find_access_user(username) is not None:
            raise fastapi.HTTPException(status_code=409, detail="User already exists")
        user = {
            "username": username,
            "role": request.role.strip() or "Operator",
            "active": bool(request.active),
        }
        if config.AUTH_MODE == "local":
            if request.password is None:
                raise fastapi.HTTPException(status_code=400, detail="Password is required")
            try:
                password = passwords.validate_password(request.password)
            except ValueError as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
            user["password_hash"], user["password_salt"] = passwords.hash_password(password)
        elif request.password is not None:
            raise fastapi.HTTPException(
                status_code=400, detail="Passwords are managed by the RADIUS server"
            )
        state.access_users.append(user)
        state.save_persisted_access_users()
        api_security.audit_event(
            http_request,
            "access_user_created",
            current_user["username"],
            f"target={username}; role={user['role']}; active={user['active']}",
        )
        return state.access_user_public(user)


@router.put("/api/access/users/{username}")
def update_access_user(
    username: str,
    request: AccessUserUpdateRequest,
    http_request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Update a user's role, active state, or local password while preserving an administrator."""

    username = api_security.normalize_username(username)
    with state.state_lock:
        user = api_security.find_access_user(username)
        if user is None:
            raise fastapi.HTTPException(status_code=404, detail="User not found")
        before = state.access_user_public(user)
        next_role = request.role.strip() if request.role is not None else user["role"]
        next_active = bool(request.active) if request.active is not None else bool(user["active"])
        if user["role"] == "Administrator" and user["active"]:
            removing_last_admin = next_role != "Administrator" or not next_active
            if removing_last_admin and api_security.count_active_administrators() <= 1:
                raise fastapi.HTTPException(
                    status_code=400,
                    detail="At least one active administrator is required",
                )
        if request.role is not None:
            user["role"] = next_role or "Operator"
        if request.active is not None:
            user["active"] = next_active
        if request.password is not None:
            if config.AUTH_MODE != "local":
                raise fastapi.HTTPException(
                    status_code=400, detail="Passwords are managed by the RADIUS server"
                )
            try:
                password = passwords.validate_password(request.password)
            except ValueError as exc:
                raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc
            user["password_hash"], user["password_salt"] = passwords.hash_password(password)
        state.save_persisted_access_users()
        api_security.audit_event(
            http_request,
            "access_user_updated",
            current_user["username"],
            f"target={username}; {api_security.audit_changes(before, state.access_user_public(user))}",
        )
        return state.access_user_public(user)


@router.delete("/api/access/users/{username}")
def delete_access_user(
    username: str,
    http_request: starlette.requests.Request,
    current_user: dict = fastapi.Depends(api_security.require_roles("Administrator")),
):
    """Delete a local role assignment while preserving required access."""

    username = api_security.normalize_username(username)
    with state.state_lock:
        user = api_security.find_access_user(username)
        if user is None:
            raise fastapi.HTTPException(status_code=404, detail="User not found")
        if len(state.access_users) == 1:
            raise fastapi.HTTPException(status_code=400, detail="At least one user is required")
        if (
            user["role"] == "Administrator"
            and user["active"]
            and api_security.count_active_administrators() <= 1
        ):
            raise fastapi.HTTPException(
                status_code=400,
                detail="At least one active administrator is required",
            )
        state.access_users.remove(user)
        state.save_persisted_access_users()
    api_security.audit_event(
        http_request,
        "access_user_deleted",
        current_user["username"],
        f"target={username}",
    )
    return {"status": "ok"}

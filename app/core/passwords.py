"""Password hashing helpers for locally authenticated panel users."""

import base64
import hashlib
import secrets

PASSWORD_ITERATIONS = 600_000
LEGACY_PASSWORD_ITERATIONS = 120_000
PASSWORD_SCHEME = "pbkdf2_sha256"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return a PBKDF2 password hash and its independently stored random salt."""

    salt_bytes = secrets.token_bytes(16) if salt is None else base64.b64decode(salt.encode("ascii"))
    encoded_salt = salt or base64.b64encode(salt_bytes).decode("ascii")
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PASSWORD_ITERATIONS
    )
    encoded_digest = base64.b64encode(digest).decode("ascii")
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${encoded_digest}", encoded_salt


def password_is_usable(password_hash: object, password_salt: object) -> bool:
    """Return whether a stored password record has the expected safe shape."""

    if not isinstance(password_hash, str) or not isinstance(password_salt, str):
        return False
    try:
        base64.b64decode(password_salt.encode("ascii"), validate=True)
        if password_hash.startswith(f"{PASSWORD_SCHEME}$"):
            scheme, iterations, digest = password_hash.split("$", 2)
            return scheme == PASSWORD_SCHEME and int(iterations) >= LEGACY_PASSWORD_ITERATIONS and bool(
                base64.b64decode(digest.encode("ascii"), validate=True)
            )
        return bool(base64.b64decode(password_hash.encode("ascii"), validate=True))
    except (ValueError, TypeError):
        return False


def verify_password(password: str, password_hash: object, password_salt: object) -> bool:
    """Verify a password, including records made by the previous local-login release."""

    if not password_is_usable(password_hash, password_salt) or not isinstance(
        password_hash, str
    ) or not isinstance(password_salt, str):
        return False
    try:
        salt_bytes = base64.b64decode(password_salt.encode("ascii"), validate=True)
        if password_hash.startswith(f"{PASSWORD_SCHEME}$"):
            _scheme, iterations, digest = password_hash.split("$", 2)
            expected_hash = base64.b64decode(digest.encode("ascii"), validate=True)
            rounds = int(iterations)
        else:
            expected_hash = base64.b64decode(password_hash.encode("ascii"), validate=True)
            rounds = LEGACY_PASSWORD_ITERATIONS
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, rounds)
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(candidate, expected_hash)


def validate_password(password: str) -> str:
    """Return a valid local password or raise a request-safe validation error."""

    if not isinstance(password, str) or not 8 <= len(password) <= 256:
        raise ValueError("Password must contain 8 to 256 characters")
    return password

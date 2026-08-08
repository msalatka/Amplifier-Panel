"""RADIUS PAP authentication adapter with explicit availability failures."""

import logging
import os

import pyrad.packet
from pyrad.client import Client, Timeout
from pyrad.dictionary import Dictionary

from app.core import config

logger = logging.getLogger(__name__)

_DICTIONARY_PATH = os.path.join(os.path.dirname(__file__), "radius_dictionary")
_dictionary = Dictionary(_DICTIONARY_PATH)


class RadiusUnavailableError(RuntimeError):
    """The RADIUS server did not respond or a communication error occurred."""


def authenticate(username: str, password: str) -> bool:
    """Verify a username and password through RADIUS PAP.

    Return ``True`` for Access-Accept and ``False`` for Access-Reject. Raise
    ``RadiusUnavailableError`` for a timeout or network error so callers can
    distinguish invalid credentials from an unavailable authentication service.
    """
    client = Client(
        server=config.RADIUS_SERVER,
        authport=config.RADIUS_PORT,
        secret=config.RADIUS_SECRET.encode("utf-8"),
        dict=_dictionary,
        timeout=config.RADIUS_TIMEOUT_SECONDS,
        retries=config.RADIUS_RETRIES,
    )

    request = client.CreateAuthPacket(code=pyrad.packet.AccessRequest, User_Name=username)
    request["User-Password"] = request.PwCrypt(password)
    request["NAS-Identifier"] = config.RADIUS_NAS_IDENTIFIER
    # Modern RADIUS servers require Message-Authenticator in Access-Request.
    # pyrad calculates HMAC-MD5 with the shared secret.
    request.add_message_authenticator()

    try:
        reply = client.SendPacket(request)
    except Timeout as exc:
        raise RadiusUnavailableError(
            f"RADIUS server did not respond ({config.RADIUS_SERVER}:{config.RADIUS_PORT})"
        ) from exc
    except OSError as exc:
        raise RadiusUnavailableError(f"RADIUS communication error: {exc}") from exc

    if reply.code == pyrad.packet.AccessAccept:
        return True

    if reply.code == pyrad.packet.AccessReject:
        return False

    logger.warning("Unexpected RADIUS reply code %s for user %s", reply.code, username)
    return False

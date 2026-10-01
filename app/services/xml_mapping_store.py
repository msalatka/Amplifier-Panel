"""Serialize validated reads and atomic updates of ``xml_mapping.json``."""

import hashlib
import json
import os
import pathlib
import tempfile
import threading
from collections.abc import Callable
from typing import TypeVar

from app.core import config

T = TypeVar("T")

_write_lock = threading.Lock()


class MappingConflictError(RuntimeError):
    """The mapping changed after an editor loaded it."""


def path() -> pathlib.Path:
    """Return the configured mapping path as an absolute path."""

    return pathlib.Path(config.XML_MAPPING_FILE).resolve()


def revision(content: str) -> str:
    """Return a stable revision token for one exact mapping document."""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def read() -> tuple[dict, str]:
    """Read and validate the latest mapping and retain its exact representation."""

    from app.services import xml_status

    content = path().read_text(encoding="utf-8")
    mapping = json.loads(content)
    return xml_status.validate_mapping(mapping), content


def load() -> dict:
    """Read and validate the latest mapping document from disk."""

    mapping, _content = read()
    return mapping


def _write(mapping: dict) -> tuple[pathlib.Path, str]:
    """Write a validated mapping while the caller holds ``_write_lock``."""

    from app.services import xml_status

    xml_status.validate_mapping(mapping)
    destination = path()
    content = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
    temporary_path: pathlib.Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            delete=False,
            newline="\n",
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = pathlib.Path(temporary.name)
        temporary_path.chmod(0o640)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination, content


def replace(mapping: dict) -> tuple[pathlib.Path, str]:
    """Atomically replace the complete mapping under the shared writer lock."""

    with _write_lock:
        return _write(mapping)


def replace_if_revision(mapping: dict, expected_revision: str) -> tuple[pathlib.Path, str]:
    """Replace a mapping only if no other writer changed the loaded document."""

    with _write_lock:
        current_content = path().read_text(encoding="utf-8")
        if revision(current_content) != expected_revision:
            raise MappingConflictError(
                "Mapping changed after it was loaded. Reload it and apply the edit again."
            )
        return _write(mapping)


def update(mutator: Callable[[dict], T]) -> tuple[pathlib.Path, str, T]:
    """Read, mutate and write the mapping as one serialized transaction."""

    with _write_lock:
        mapping = load()
        result = mutator(mapping)
        destination, content = _write(mapping)
        return destination, content, result

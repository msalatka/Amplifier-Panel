"""Explicit device registry; add one descriptor for each supported device type.

An adapter owns its acquisition loop. The panel owns authentication, storage,
history, CSV and the device selector. At most one instance of each registered
type is enabled on a host.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceDefinition:
    """Identify a supported view and its optional dedicated acquisition worker."""

    id: str
    label: str
    worker: str | None
    view_profile: str


DEVICES = {
    **{
        key: DeviceDefinition(key, label, None, "xml")
        for key, label in (
            ("local", "Local station / DI"),
            ("remote", "Remote station / DI"),
            ("oba", "EDFA OBA"),
            ("oba3", "EDFA OBA3"),
        )
    },
    "amplifier": DeviceDefinition(
        id="amplifier",
        label="Optical amplifier",
        worker="app.services.serial:serial_reader_loop",
        view_profile="amplifier",
    ),
    "fts-ls": DeviceDefinition(
        id="fts-ls",
        label="FTS-LS laser station",
        worker=None,  # The station daemon's XML contract has not been supplied yet.
        view_profile="fts-ls",
    ),
}


def parse_enabled_devices(value: str | None) -> tuple[str, ...]:
    """Validate a comma-separated set of device IDs in display order."""

    ids = tuple(
        part.strip().lower()
        for part in ("local,remote,oba,oba3" if value is None else value).split(",")
    )
    if not ids or any(not part or part not in DEVICES for part in ids):
        raise ValueError(f"ENABLED_DEVICES must contain registered IDs: {', '.join(DEVICES)}")
    if len(ids) != len(set(ids)):
        raise ValueError("ENABLED_DEVICES must not contain duplicate device IDs")
    return ids

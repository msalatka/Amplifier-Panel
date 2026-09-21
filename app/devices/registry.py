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
    view_profile: str


DEVICES = {
    key: DeviceDefinition(key, label, family)
    for key, label, family in (
        ("local", "Local station / DI", "station"),
        ("remote", "Remote station / DI", "station"),
        ("oba", "EDFA OBA", "amplifier"),
        ("oba3", "EDFA OBA3", "amplifier"),
    )
}


def parse_enabled_devices(value: str | None) -> tuple[str, ...]:
    """Read current device IDs and migrate the two retired profile names."""
    parts = (value if value is not None else "local,remote,oba,oba3").split(",")
    result = []
    migration = {"amplifier": ("oba", "oba3"), "fts-ls": ("local", "remote")}
    for part in parts:
        key = part.strip().lower()
        for device in migration.get(key, (key,)):
            if device not in DEVICES:
                raise ValueError(f"ENABLED_DEVICES must contain: {', '.join(DEVICES)}")
            if device not in result:
                result.append(device)
    return tuple(result)

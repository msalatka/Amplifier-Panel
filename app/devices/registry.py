"""Metadata for XML-discovered device profiles."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DeviceDefinition:
    """Describe one independently displayed XML profile."""

    id: str
    label: str
    view_profile: str = "station"
    display_group: str = "Other devices"
    order: int = 1000
    snmp_index: int | None = None


KNOWN_DEVICES = {
    definition.id: definition
    for definition in (
        DeviceDefinition("local", "Local station", "station", "Local station", 10, 1),
        DeviceDefinition("local_di", "Local DI", "station", "Local station", 20, 2),
        DeviceDefinition("remote", "Remote station", "station", "Remote station", 30, 3),
        DeviceDefinition("remote_di", "Remote DI", "station", "Remote station", 40, 4),
        DeviceDefinition("oba", "EDFA OBA", "amplifier", "Amplifiers", 50, 5),
        DeviceDefinition("oba3", "EDFA OBA3", "amplifier", "Amplifiers", 60, 6),
    )
}


def inferred_definition(device_id: str, label: str | None = None) -> DeviceDefinition:
    """Return stable known metadata or safe defaults for a new XML profile."""

    if device_id in KNOWN_DEVICES:
        return KNOWN_DEVICES[device_id]
    generated_label = label or device_id.replace("_", " ").strip().title() or device_id
    return DeviceDefinition(device_id, generated_label)


def definition_dict(device_id: str, **overrides) -> dict:
    """Return serializable profile metadata with optional mapping overrides."""

    values = asdict(inferred_definition(device_id, overrides.get("label")))
    for key in ("label", "view_profile", "display_group", "order", "snmp_index"):
        if overrides.get(key) is not None:
            values[key] = overrides[key]
    return values

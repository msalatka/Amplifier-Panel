"""Safe presentation defaults for profiles absent from the XML mapping."""

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


def inferred_definition(device_id: str, label: str | None = None) -> DeviceDefinition:
    """Return safe defaults for a newly discovered XML profile."""

    generated_label = label or device_id.replace("_", " ").strip().title() or device_id
    return DeviceDefinition(device_id, generated_label)


def definition_dict(device_id: str, **overrides) -> dict:
    """Return serializable profile metadata with optional mapping overrides."""

    values = asdict(inferred_definition(device_id, overrides.get("label")))
    for key in ("label", "view_profile", "display_group", "order", "snmp_index"):
        if overrides.get(key) is not None:
            values[key] = overrides[key]
    return values

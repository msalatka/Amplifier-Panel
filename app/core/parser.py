KEY_ALIASES = {
    "T": "temperature",
    "p_a_in": "PiA",
    "p_a_out": "PoA",
    "p_b_in": "PiB",
    "p_b_out": "PoB",
}


def parse_value(value: str):
    value = value.strip()

    try:
        numeric_value = float(value)
    except ValueError:
        return value

    if numeric_value == -999:
        return None

    return numeric_value


def parse_semicolon_parts(payload: str, separator: str) -> dict:
    data = {}

    for part in payload.split(";"):
        if separator not in part:
            continue

        key, value = part.split(separator, 1)
        key = KEY_ALIASES.get(key.strip(), key.strip())
        parsed_value = parse_value(value)

        if parsed_value is None:
            continue

        data[key] = parsed_value

    return data


def parse_line(line: str) -> dict:
    line = line.strip()

    if line.startswith("#M:") and line.endswith("*"):
        return parse_semicolon_parts(line[1:-1], ":")

    return parse_semicolon_parts(line, "=")

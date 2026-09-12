#!/usr/bin/env python3
"""pass_persist dla net-snmp: serwuje na żywo konfigurację z GUI amp-panel."""
import json
import pathlib
import sys

BASE = (1, 3, 6, 1, 4, 1, 99999, 10)
STATE_FILE = pathlib.Path("/var/lib/amp-panel/persisted_state.json")


def oid_to_tuple(oid_str):
    return tuple(int(part) for part in oid_str.strip(".").split("."))


def tuple_to_oid(oid_tuple):
    return "." + ".".join(str(part) for part in oid_tuple)


def fmt(value):
    return "unset" if value is None else str(value)


def load_data():
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    settings = raw.get("dashboard_settings", {}) or {}
    limits = settings.get("warn_limits", {}) or {}
    snmp = raw.get("snmp_settings", {}) or {}

    def limit(field, side):
        return fmt((limits.get(field) or {}).get(side))

    return {
        BASE + (1, 0): ("string", fmt(settings.get("gain_tolerance"))),
        BASE + (2, 0): ("string", limit("PiA", "min")),
        BASE + (3, 0): ("string", limit("PiA", "max")),
        BASE + (4, 0): ("string", limit("PoA", "min")),
        BASE + (5, 0): ("string", limit("PoA", "max")),
        BASE + (6, 0): ("string", limit("PiB", "min")),
        BASE + (7, 0): ("string", limit("PiB", "max")),
        BASE + (8, 0): ("string", limit("PoB", "min")),
        BASE + (9, 0): ("string", limit("PoB", "max")),
        BASE + (10, 0): ("string", limit("temperature", "min")),
        BASE + (11, 0): ("string", limit("temperature", "max")),
        BASE + (12, 0): ("string", fmt(snmp.get("enabled"))),
        BASE + (13, 0): ("string", fmt(raw.get("last_known_gain_set"))),
    }


def find_next(after, leaves):
    candidates = sorted(oid for oid in leaves if oid > after)
    return candidates[0] if candidates else None


def main():
    for line in sys.stdin:
        command = line.strip()
        if command == "PING":
            print("PONG", flush=True)
        elif command == "getnext":
            oid_line = sys.stdin.readline().strip()
            leaves = load_data()
            try:
                after = oid_to_tuple(oid_line)
            except ValueError:
                print("NONE", flush=True)
                continue
            next_oid = find_next(after, leaves)
            if next_oid is None:
                print("NONE", flush=True)
            else:
                type_, value = leaves[next_oid]
                print(tuple_to_oid(next_oid), flush=True)
                print(type_, flush=True)
                print(value, flush=True)
        elif command == "get":
            oid_line = sys.stdin.readline().strip()
            leaves = load_data()
            try:
                target = oid_to_tuple(oid_line)
            except ValueError:
                print("NONE", flush=True)
                continue
            if target in leaves:
                type_, value = leaves[target]
                print(tuple_to_oid(target), flush=True)
                print(type_, flush=True)
                print(value, flush=True)
            else:
                print("NONE", flush=True)
        elif command == "set":
            sys.stdin.readline()
            sys.stdin.readline()
            print("not-writable", flush=True)

if __name__ == "__main__":
    main()

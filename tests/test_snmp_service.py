import unittest
from unittest import mock

from pysnmp.proto import rfc1902, rfc1905

from app.services import snmp


def device_snapshot(
    section: str,
    fields: list[dict],
    values: dict,
    *,
    connected: bool = True,
) -> dict:
    return {
        "connected": connected,
        "error": None,
        "last_update": "2026-09-25T12:00:00+00:00",
        "data": {
            "sections": [{"key": section, "fields": fields}],
            "values": {section: values},
        },
    }


class SnmpServiceTests(unittest.TestCase):
    def test_live_oid_tree_uses_enabled_xml_devices_and_mapping_ids(self):
        snapshots = {
            "local": device_snapshot(
                "local",
                [
                    {"key": "LaserLocked", "id": "2.1.1.5", "role": ""},
                    {"key": "automatic", "role": ""},
                ],
                {"LaserLocked": True, "automatic": 12},
            ),
            "oba3": device_snapshot(
                "oba3",
                [{"key": "Gain", "id": "5.1.1.1", "role": "gain"}],
                {"Gain": 30.5},
                connected=False,
            ),
        }
        with (
            mock.patch.object(snmp.config, "ENABLED_DEVICES", ("local", "oba3")),
            mock.patch.object(
                snmp.state,
                "snapshot_device_live",
                side_effect=lambda device_id: snapshots[device_id],
            ),
        ):
            values = snmp._live_oid_values()

        self.assertEqual(values[f"{snmp.OID_BASE_STR}.1.1.0"], "CONNECTED")
        self.assertEqual(values[f"{snmp.OID_BASE_STR}.1.2.0"], "DISCONNECTED")
        self.assertEqual(values[f"{snmp.OID_BASE_STR}.2.1.1.5.0"], "true")
        self.assertEqual(values[f"{snmp.OID_BASE_STR}.5.1.1.1.0"], "30.5")
        self.assertFalse(any("automatic" in oid for oid in values))

    def test_dashboard_snapshot_uses_current_amplifier_roles(self):
        snapshots = {
            "oba": device_snapshot(
                "oba",
                [
                    {"key": "gain", "id": "4.1.1.4", "role": "gain"},
                    {"key": "temperature", "id": "4.1.1.3", "role": "temperature"},
                ],
                {"gain": 14, "temperature": 21},
            ),
            "oba3": device_snapshot(
                "oba3",
                [
                    {"key": "Gain", "id": "5.1.1.1", "role": "gain"},
                    {"key": "Temp", "id": "5.1.1.4", "role": "temperature"},
                ],
                {"Gain": 31, "Temp": 26},
            ),
        }
        with (
            mock.patch.object(snmp.config, "ENABLED_DEVICES", ("oba", "oba3")),
            mock.patch.object(
                snmp.state,
                "snapshot_device_live",
                side_effect=lambda device_id: snapshots[device_id],
            ),
        ):
            snmp._refresh_live_snapshot()

        try:
            self.assertEqual(snmp.state.latest_snmp_data["status"], "CONNECTED")
            self.assertEqual(snmp.state.latest_snmp_data["gain_actual"], "31")
            self.assertEqual(snmp.state.latest_snmp_data["temperature"], "26")
        finally:
            snmp.state.latest_snmp_data = {}

    def test_get_and_getnext_return_current_values_in_numeric_order(self):
        values = {
            f"{snmp.OID_BASE_STR}.2.1.1.10.0": "ten",
            f"{snmp.OID_BASE_STR}.2.1.1.2.0": "two",
        }
        instrumentation = snmp.CustomInstrum()
        requested = rfc1902.ObjectName(f"{snmp.OID_BASE_STR}.2.1.1.2.0")
        with (
            mock.patch.object(snmp, "_live_oid_values", return_value=values),
            mock.patch.object(snmp, "_refresh_live_snapshot"),
        ):
            read = instrumentation.read_variables([(requested, rfc1902.Null(""))])
            following = instrumentation.read_next_variables([(requested, rfc1902.Null(""))])

        self.assertEqual(str(read[0][1]), "two")
        self.assertEqual(str(following[0][0]), f"{snmp.OID_BASE_STR}.2.1.1.10.0")
        self.assertEqual(str(following[0][1]), "ten")

    def test_unknown_get_returns_no_such_object(self):
        instrumentation = snmp.CustomInstrum()
        requested = rfc1902.ObjectName(f"{snmp.OID_BASE_STR}.99.0")
        with (
            mock.patch.object(snmp, "_live_oid_values", return_value={}),
            mock.patch.object(snmp, "_refresh_live_snapshot"),
        ):
            result = instrumentation.read_variables([(requested, rfc1902.Null(""))])

        self.assertIsInstance(result[0][1], rfc1905.NoSuchObject)

    def test_init_does_not_start_a_second_agent_thread(self):
        running_thread = mock.Mock()
        running_thread.is_alive.return_value = True
        original_thread = snmp.snmp_thread
        snmp.snmp_thread = running_thread
        try:
            with (
                mock.patch.object(snmp.state, "snmp_settings", {"enabled": True}),
                mock.patch.object(snmp.threading, "Thread") as thread_class,
            ):
                snmp.init_snmp()
            thread_class.assert_not_called()
        finally:
            snmp.snmp_thread = original_thread

    def test_agent_start_log_does_not_disclose_community(self):
        message = snmp._agent_start_message(1161)
        self.assertIn("1161", message)
        self.assertNotIn("top-secret-community", message)


if __name__ == "__main__":
    unittest.main()

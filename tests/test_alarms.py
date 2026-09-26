import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from app.services import alarms


class AlarmServiceTests(unittest.TestCase):
    def setUp(self):
        alarms.state.active_alarms = {}

    def tearDown(self):
        alarms.state.active_alarms = {}

    @staticmethod
    def snapshot(value: float) -> dict:
        return {
            "sections": [
                {
                    "key": "oba3",
                    "fields": [
                        {
                            "key": "Temp",
                            "label": "Temperature",
                            "unit": "C",
                            "type": "number",
                            "alarm": {"enabled": True, "minimum": 5, "maximum": 50},
                        }
                    ],
                }
            ],
            "values": {"oba3": {"Temp": value}},
        }

    def test_alarm_latches_until_normal_and_acknowledged(self):
        with (
            mock.patch.object(alarms.syslog, "send_warning_event") as send_syslog,
            mock.patch.object(alarms.snmp, "send_trap") as send_trap,
        ):
            alarms.evaluate("oba3", self.snapshot(55), "2026-09-26T10:00:00+00:00")
            alarms.evaluate("oba3", self.snapshot(56), "2026-09-26T10:00:01+00:00")
            self.assertEqual(len(alarms.active("oba3")), 1)
            self.assertEqual(send_trap.call_count, 1)
            send_syslog.assert_called_once()

            alarms.evaluate("oba3", self.snapshot(40), "2026-09-26T10:00:02+00:00")

        latched = alarms.active("oba3")
        self.assertEqual(len(latched), 1)
        self.assertFalse(latched[0]["condition_active"])
        self.assertFalse(latched[0]["acknowledged"])
        self.assertEqual(send_syslog.call_count, 2)
        self.assertEqual(send_syslog.call_args.args[0], "CLEARED")

        alarms.acknowledge("oba3", latched[0]["key"])
        self.assertEqual(alarms.active("oba3"), [])

    def test_acknowledged_active_alarm_remains_until_normal(self):
        with (
            mock.patch.object(alarms.syslog, "send_warning_event"),
            mock.patch.object(alarms.snmp, "send_trap"),
        ):
            alarms.evaluate("oba3", self.snapshot(55), "2026-09-26T10:00:00+00:00")
            key = alarms.active("oba3")[0]["key"]
            alarms.acknowledge("oba3", key)
            self.assertTrue(alarms.active("oba3")[0]["acknowledged"])
            alarms.evaluate("oba3", self.snapshot(40), "2026-09-26T10:00:01+00:00")

        self.assertEqual(alarms.active("oba3"), [])

    def test_empty_disabled_alarm_is_removed_from_mapping(self):
        mapping = alarms.xml_status.load_mapping()
        mapping = copy.deepcopy(mapping)
        field = mapping["oba3"]["sections"][0]["fields"][0]
        field["alarm"] = {"enabled": True, "maximum": 40}
        identifier = f"oba3:{field['key']}"

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "xml_mapping.json"
            path.write_text(json.dumps(mapping), encoding="utf-8")
            with mock.patch.object(alarms.config, "XML_MAPPING_FILE", str(path)):
                alarms.update_config("oba3", {identifier: {"enabled": False}})

            saved = json.loads(path.read_text(encoding="utf-8"))

        saved_field = saved["oba3"]["sections"][0]["fields"][0]
        self.assertNotIn("alarm", saved_field)


if __name__ == "__main__":
    unittest.main()

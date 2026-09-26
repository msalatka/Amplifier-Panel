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

    def test_alarm_opens_once_and_clears_after_value_returns_to_range(self):
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

        self.assertEqual(alarms.active("oba3"), [])
        self.assertEqual(send_syslog.call_count, 2)
        self.assertEqual(send_syslog.call_args.args[0], "CLEARED")


if __name__ == "__main__":
    unittest.main()

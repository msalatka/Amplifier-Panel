import unittest
from unittest import mock

from app.devices import runtime


class DeviceRuntimeTests(unittest.TestCase):
    def test_history_stores_values_without_repeated_display_metadata(self):
        snapshot = {
            "values": {"oba3": {"Gain": 30.0, "Temp": 24.0}},
            "sections": [{"key": "oba3", "fields": [{"key": "Gain", "unit": "dB"}]}],
            "profile": {"id": "oba3", "label": "EDFA OBA3"},
        }
        with (
            mock.patch.object(runtime.state, "is_active_device", return_value=True),
            mock.patch.object(
                runtime.state,
                "snapshot_device_live",
                return_value={"last_update": None},
            ),
            mock.patch.object(runtime.state, "update_device_live") as update_live,
            mock.patch.object(
                runtime.database_service, "write_device_snapshot", return_value=True
            ) as write,
        ):
            stored = runtime.publish_snapshot(
                "oba3", snapshot, "2026-10-01T10:00:00+00:00"
            )

        self.assertTrue(stored)
        write.assert_called_once_with(
            "oba3",
            {"values": {"oba3": {"Gain": 30.0, "Temp": 24.0}}},
            "2026-10-01T10:00:00+00:00",
        )
        self.assertEqual(update_live.call_args.kwargs["data"], snapshot)


if __name__ == "__main__":
    unittest.main()

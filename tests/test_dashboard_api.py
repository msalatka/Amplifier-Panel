import datetime
import unittest
from unittest import mock

from app.api import devices


class DashboardApiTests(unittest.TestCase):
    def test_latest_includes_current_host_system_time(self):
        before = datetime.datetime.now(datetime.timezone.utc)
        with mock.patch.object(
            devices.database_service,
            "get_runtime_status",
            return_value={"state": "ready"},
        ):
            result = devices.latest(devices.config.ENABLED_DEVICES[0], {})
        after = datetime.datetime.now(datetime.timezone.utc)

        system_time = datetime.datetime.fromisoformat(result["system_time"])
        self.assertIsNotNone(system_time.tzinfo)
        self.assertLessEqual(before, system_time)
        self.assertLessEqual(system_time, after)

    def test_operator_can_persist_shared_amplifier_live_fields(self):
        device_id = "oba3"
        previous = list(devices.state.device_live_fields[device_id])
        live = {
            "data": {
                "sections": [
                    {"key": "oba3", "fields": [{"key": "Temp"}, {"key": "PumpI"}]}
                ]
            }
        }
        try:
            with (
                mock.patch.object(devices.config, "ENABLED_DEVICES", (device_id,)),
                mock.patch.object(devices.state, "snapshot_device_live", return_value=live),
                mock.patch.object(devices.state, "save_persisted_state") as save,
                mock.patch.object(devices.api_security, "audit_event") as audit,
            ):
                result = devices.update_live_fields(
                    device_id,
                    devices.LiveFieldsUpdate(fields=["oba3:Temp", "oba3:PumpI"]),
                    mock.Mock(),
                    {"username": "operator", "role": "Operator"},
                )
            self.assertEqual(result["live_fields"], ["oba3:Temp", "oba3:PumpI"])
            save.assert_called_once()
            audit.assert_called_once()
        finally:
            devices.state.device_live_fields[device_id] = previous

    def test_unknown_live_field_is_rejected(self):
        with (
            mock.patch.object(devices.config, "ENABLED_DEVICES", ("oba3",)),
            mock.patch.object(
                devices.state,
                "snapshot_device_live",
                return_value={"data": {"sections": []}},
            ),
            self.assertRaises(Exception) as caught,
        ):
            devices.update_live_fields(
                "oba3",
                devices.LiveFieldsUpdate(fields=["oba3:Unknown"]),
                mock.Mock(),
                {"username": "operator", "role": "Operator"},
            )
        self.assertEqual(caught.exception.status_code, 422)

    def test_operator_can_put_two_series_on_the_same_chart(self):
        device_id = "oba3"
        previous = devices.state.device_chart_layouts.get(device_id)
        live = {"data": {"sections": [{"key": "oba3", "fields": [
            {"key": "Temp", "type": "number"},
            {"key": "PumpI", "type": "number"},
        ]}]}}
        try:
            with (
                mock.patch.object(devices.config, "ENABLED_DEVICES", (device_id,)),
                mock.patch.object(devices.state, "snapshot_device_live", return_value=live),
                mock.patch.object(devices.state, "save_persisted_state") as save,
                mock.patch.object(devices.api_security, "audit_event"),
            ):
                result = devices.update_chart_layout(
                    device_id,
                    devices.ChartLayoutUpdate(charts={"oba3:Temp": 1, "oba3:PumpI": 1}),
                    mock.Mock(),
                    {"username": "operator", "role": "Operator"},
                )
            self.assertEqual(result["chart_layout"], {"oba3:Temp": 1, "oba3:PumpI": 1})
            save.assert_called_once()
        finally:
            if previous is None:
                devices.state.device_chart_layouts.pop(device_id, None)
            else:
                devices.state.device_chart_layouts[device_id] = previous


if __name__ == "__main__":
    unittest.main()

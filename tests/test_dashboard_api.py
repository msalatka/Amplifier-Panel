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


if __name__ == "__main__":
    unittest.main()

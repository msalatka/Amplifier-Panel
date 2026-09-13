import unittest
from unittest import mock

import fastapi

from app.api import fts_ls


class FtsLsApiTests(unittest.TestCase):
    def test_station_capabilities_are_read_only_without_daemon_contract(self):
        with mock.patch.object(fts_ls.config, "DEVICE_PROFILE", "fts-ls"):
            result = fts_ls.capabilities(_current_user={"role": "Viewer"})
        self.assertTrue(result["read_only"])
        self.assertEqual(result["controls"], [])

    def test_station_command_fails_closed_without_daemon_contract(self):
        with mock.patch.object(fts_ls.config, "DEVICE_PROFILE", "fts-ls"):
            with self.assertRaises(fastapi.HTTPException) as raised:
                fts_ls.command(
                    body=fts_ls.DeviceCommandRequest(action="power_reset", confirmed=True),
                    request=mock.MagicMock(),
                    current_user={"username": "admin", "role": "Administrator"},
                )
        self.assertEqual(raised.exception.status_code, 503)

    def test_history_uses_the_common_range_validation(self):
        with self.assertRaisesRegex(fastapi.HTTPException, "Invalid history range"):
            fts_ls._history("invalid", None, None, 2000)
        with self.assertRaisesRegex(fastapi.HTTPException, "History start must be before end"):
            fts_ls._history(
                "all",
                "2026-08-03T13:00:00+00:00",
                "2026-08-03T12:00:00+00:00",
                2000,
            )

    def test_csv_export_uses_the_common_semicolon_format_and_audit_event(self):
        point = {
            "time": "2026-08-03T12:00:00+00:00",
            "snapshot": {
                "laser": {"optical_frequency": 194400.0},
                "tec": {},
                "synth": {},
                "uplink": {},
                "ports": [],
            },
        }
        with (
            mock.patch.object(fts_ls.config, "DEVICE_PROFILE", "fts-ls"),
            mock.patch.object(
                fts_ls,
                "_history",
                return_value=("5m", None, None, [point]),
            ),
            mock.patch.object(fts_ls.api_security, "audit_event") as audit_event,
        ):
            response = fts_ls.export_history(
                request=mock.MagicMock(),
                range_value="5m",
                start=None,
                end=None,
                limit=10000,
                current_user={"username": "viewer", "role": "Viewer"},
            )

        csv_text = response.body.decode("utf-8")
        self.assertTrue(csv_text.startswith("sep=;\r\ntime;laser_optical_frequency\r\n"))
        self.assertIn("2026-08-03T12:00:00+00:00;194400.0", csv_text)
        self.assertIn("fts_ls_history_", response.headers["content-disposition"])
        audit_event.assert_called_once()
        self.assertTrue(fts_ls.CSV_EXPORT_LOCK.acquire(blocking=False))
        fts_ls.CSV_EXPORT_LOCK.release()


if __name__ == "__main__":
    unittest.main()

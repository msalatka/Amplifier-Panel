import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import config, state
from app.services import database as database_service


class DatabaseServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_settings = state.service_settings.copy()
        self.original_discarded = database_service.discarded_records
        database_service.close_database()
        database_service.discarded_records = 0
        self.database_patch = mock.patch.object(
            config,
            "DATABASE_FILE",
            str(Path(self.temp_dir.name) / "measurements.db"),
        )
        self.database_patch.start()
        state.service_settings["database_max_records"] = 100

    def tearDown(self):
        database_service.close_database()
        database_service.discarded_records = self.original_discarded
        state.service_settings.clear()
        state.service_settings.update(self.original_settings)
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_device_statistics_include_completed_hour_and_exact_range_edges(self):
        snapshots = [
            ("2026-07-17T10:00:00+00:00", 100),
            ("2026-07-17T10:15:00+00:00", 2),
            ("2026-07-17T10:45:00+00:00", 6),
            ("2026-07-17T11:00:00+00:00", 4),
        ]
        for timestamp, value in snapshots:
            self.assertTrue(
                database_service.write_device_snapshot(
                    "local", {"laser": {"optical_frequency": value}}, timestamp
                )
            )
        summary = database_service.connection.execute(
            "SELECT sample_count FROM device_hourly_statistics WHERE device_id = 'local'"
        ).fetchone()
        self.assertEqual(summary["sample_count"], 3)

        result = database_service.query_device_statistics(
            "local", "all", "2026-07-17T10:10:00+00:00", "2026-07-17T11:00:00+00:00"
        )
        metric = result["statistics"]["laser.optical_frequency"]
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(metric["min"], 2)
        self.assertEqual(metric["max"], 6)
        self.assertEqual(metric["average"], 4)
        self.assertAlmostEqual(metric["standard_deviation"], (8 / 3) ** 0.5)

    def test_device_statistics_and_retention_are_independent(self):
        state.service_settings["database_max_records"] = 2
        for index in range(3):
            stamp = f"2026-07-17T10:00:0{index}+00:00"
            self.assertTrue(
                database_service.write_device_snapshot(
                    "local", {"laser": {"optical_frequency": index}}, stamp
                )
            )
            self.assertTrue(
                database_service.write_device_snapshot(
                    "oba3", {"values": {"oba3": {"Gain": index}}}, stamp
                )
            )
        self.assertEqual(database_service.get_device_snapshot_count("local"), 2)
        self.assertEqual(database_service.get_device_snapshot_count("oba3"), 2)
        result = database_service.query_device_statistics("local", "all")
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["statistics"]["laser.optical_frequency"]["min"], 1)

    def test_device_snapshot_stream_returns_complete_history_without_writer_lock(self):
        state.service_settings["database_max_records"] = 0
        for index in range(5):
            database_service.write_device_snapshot(
                "local", {"sequence": index}, f"2026-07-17T10:00:0{index}+00:00"
            )
        points = database_service.stream_device_snapshots("local", "all", batch_size=1)
        first = next(points)
        self.assertEqual(first["snapshot"]["sequence"], 0)
        self.assertTrue(
            database_service.write_device_snapshot(
                "local", {"sequence": 5}, "2026-07-17T10:00:05+00:00"
            )
        )
        self.assertEqual([point["snapshot"]["sequence"] for point in points], [1, 2, 3, 4])

    def test_xml_snapshots_are_stored_pruned_and_queried(self):
        state.service_settings["database_max_records"] = 2
        for second in range(3):
            self.assertTrue(
                database_service.write_device_snapshot(
                    "local",
                    {"laser": {"frequency": 194400 + second}},
                    f"2026-07-17T10:15:3{second}+00:00",
                )
            )

        self.assertEqual(database_service.get_device_snapshot_count("local"), 2)
        points = database_service.query_device_snapshots(
            "local",
            "all",
            start="2026-07-17T10:00:00+00:00",
        )
        self.assertEqual(
            [point["snapshot"]["laser"]["frequency"] for point in points],
            [194401, 194402],
        )

    def test_xml_history_is_evenly_downsampled_across_selected_range(self):
        state.service_settings["database_max_records"] = 0
        for second in range(10):
            database_service.write_device_snapshot(
                "local",
                {"sequence": second},
                f"2026-07-17T10:15:{second:02d}+00:00",
            )

        points = database_service.query_device_snapshots("local", "all", limit=3)
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0]["snapshot"]["sequence"], 0)
        self.assertEqual(points[-1]["snapshot"]["sequence"], 9)

    def test_runtime_status_reports_ready_database(self):
        database_service.write_device_snapshot(
            "local", {"values": {"local": {"RecOptPow": 1.0}}}, "2026-07-17T10:15:30+00:00"
        )
        status = database_service.get_runtime_status()
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["ready"])
        self.assertEqual(status["records"], 1)


if __name__ == "__main__":
    unittest.main()

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class DashboardUiTests(unittest.TestCase):
    def test_chart_library_is_served_locally(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        chart_library = ROOT / "static" / "vendor" / "chart.js" / "chart.umd.min.js"

        self.assertIn("/static/vendor/chart.js/chart.umd.min.js", template)
        self.assertNotIn("cdn.jsdelivr.net", template)
        self.assertTrue(chart_library.is_file())
        self.assertIn("Chart.js v4.5.1", chart_library.read_text(encoding="utf-8")[:200])

    def test_system_time_comes_from_latest_api_response(self):
        script = (ROOT / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")

        self.assertIn("formatTime(json.system_time)", script)
        self.assertNotIn(
            "setTextIfExists('status-system-time', new Date().toLocaleTimeString())",
            script,
        )


if __name__ == "__main__":
    unittest.main()

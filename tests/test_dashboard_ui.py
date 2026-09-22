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
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")

        self.assertIn("formatTime(result.system_time)", script)
        self.assertNotIn(
            "setTextIfExists('status-system-time', new Date().toLocaleTimeString())",
            script,
        )

    def test_station_view_has_no_xml_implementation_badge(self):
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")
        live_template = (ROOT / "templates" / "device-live.html").read_text(encoding="utf-8")

        self.assertNotIn("Reported in XML", script)
        self.assertNotIn("device-source-time", live_template)
        self.assertNotIn("systemName", script)
        self.assertIn("fts-station-group", script)

    def test_amplifier_pinned_fields_use_shared_live_row(self):
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")

        self.assertIn("amp-pinned-metrics", script)
        self.assertIn("result.live_fields", script)

    def test_boolean_measurements_are_rendered_as_true_or_false(self):
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")

        self.assertIn("field.type === 'boolean'", script)
        self.assertIn("value ? 'true' : 'false'", script)

    def test_station_layout_has_at_most_four_blocks_per_row(self):
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", stylesheet)

    def test_amplifier_live_fields_are_shared_and_hidden_from_viewers(self):
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "device-live.html").read_text(encoding="utf-8")

        self.assertIn("/live-fields`,", script)
        self.assertIn("xml-metric-selectable", script)
        self.assertIn('aria-pressed="${pinned}"', script)
        self.assertIn('id="amp-pinned-metrics"', template)
        self.assertIn('class="device-measurements" data-operator-only', template)

    def test_each_incomplete_amplifier_metric_row_is_centered(self):
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertIn("flex-wrap: wrap;", stylesheet)
        self.assertIn("justify-content: center;", stylesheet)
        self.assertIn("calc((100% - 72px) / 4)", stylesheet)

    def test_chart_series_can_be_hidden_or_combined_by_operators(self):
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "device-history.html").read_text(encoding="utf-8")

        self.assertIn("/chart-layout`,", script)
        self.assertIn("Hidden</option>", script)
        self.assertIn("xmlChartLayout[fieldIdentifier(field)] === chart", script)
        self.assertIn('id="xml-chart-settings"', template)
        self.assertIn("data-operator-only", template)


if __name__ == "__main__":
    unittest.main()

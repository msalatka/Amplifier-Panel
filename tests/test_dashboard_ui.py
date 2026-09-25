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

    def test_station_blocks_are_dimmed_only_by_the_on_role(self):
        script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertIn("field.role === 'on'", script)
        self.assertIn("is-switched-off", script)
        self.assertNotIn("'locked'", script)
        self.assertNotIn("fts-led", script)
        self.assertIn(".fts-module.is-switched-off", stylesheet)

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
        self.assertIn('<details id="xml-chart-settings"', template)
        self.assertIn("data-operator-only", template)
        self.assertNotIn("showModal()", script)
        self.assertIn("addEventListener('toggle'", script)

    def test_administration_has_complete_xml_mapping_editor(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "dashboard-mapping.js").read_text(encoding="utf-8")

        self.assertIn('data-tab="variable-blocks"', template)
        self.assertIn('data-title="Edit Variables"', template)
        self.assertIn('id="xml-mapping-path"', template)
        self.assertIn('id="xml-mapping-content"', template)
        self.assertIn("/api/xml-mapping", script)
        self.assertIn("beforeunload", script)
        self.assertIn("confirmDiscardXmlMappingChanges", script)

    def test_save_and_secondary_buttons_use_shared_styles(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="save-network-button" class="button-primary"', template)
        self.assertIn('id="refresh-network-button" class="button-secondary"', template)
        self.assertIn('id="download-syslog-button" class="button-primary"', template)
        self.assertIn(">Refresh</button>", template)
        self.assertIn("button.button-primary", stylesheet)
        self.assertIn("button.button-secondary", stylesheet)

    def test_statistics_range_and_csv_export_use_shared_colours(self):
        template = (ROOT / "templates" / "device-history.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="xml-export" class="button button-primary"', template)
        self.assertIn(".monitors-header select", stylesheet)
        self.assertNotIn("#xml-export { color:", stylesheet)

    def test_administrator_can_open_a_live_variable_in_the_mapping_editor(self):
        live_script = (ROOT / "static" / "js" / "dashboard-xml.js").read_text(
            encoding="utf-8"
        )
        mapping_script = (ROOT / "static" / "js" / "dashboard-mapping.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("data-variable-key", live_script)
        self.assertIn("contextmenu", live_script)
        self.assertIn("isAdministrator()", live_script)
        self.assertIn("openXmlVariableEditor", mapping_script)
        self.assertIn("/api/xml-mapping/fields", mapping_script)
        self.assertIn("Add to mapping", live_script)
        self.assertIn("setSelectionRange", mapping_script)

    def test_device_control_is_generated_from_writable_xml_fields(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        control_template = (ROOT / "templates" / "device-control.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "static" / "js" / "dashboard-control.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('data-tab="device-control"', template)
        self.assertIn("dashboard-control.js", template)
        self.assertIn('id="device-control-fields"', control_template)
        self.assertIn("fields.filter((field) => field.writable)", script)
        self.assertIn("for (const identifier of deviceControlDirty)", script)
        self.assertIn("method: 'PUT'", script)
        self.assertIn("/control/status", script)
        self.assertNotIn("/api/set_gain", script)
        self.assertNotIn("gain-set-input", control_template)

    def test_repeated_notifications_are_deduplicated(self):
        script = (ROOT / "static" / "js" / "dashboard-core.js").read_text(
            encoding="utf-8"
        )
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("item.dataset.message === message", script)
        self.assertIn("item.dataset.type === type", script)
        self.assertIn("window.clearTimeout", script)
        self.assertIn("notification-count", script)
        self.assertIn(".notification.repeated", stylesheet)


if __name__ == "__main__":
    unittest.main()

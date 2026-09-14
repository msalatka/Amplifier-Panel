import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def dashboard_scripts() -> str:
    """Read the classic frontend scripts in their browser loading order."""
    names = (
        "dashboard-core.js",
        "dashboard-network.js",
        "dashboard.js",
        "dashboard-history.js",
        "dashboard-fts-ls.js",
        "dashboard-bootstrap.js",
    )
    return "\n".join((ROOT / "static" / "js" / name).read_text(encoding="utf-8") for name in names)


class FtsLsUiTests(unittest.TestCase):
    def test_fts_profile_reuses_monitor_navigation_and_export_toolbar(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("Station History", template)
        self.assertIn('class="nav-link" data-tab="overview"', template)
        self.assertIn('class="nav-link" data-tab="statistics"', template)
        self.assertIn(
            'class="tab-panel profile-fts-only" data-tab="overview"',
            template,
        )
        self.assertIn(
            'class="tab-panel profile-fts-only" data-tab="statistics"',
            template,
        )
        self.assertGreaterEqual(
            template.count(
                '<button type="button" class="export-csv-button">Export selected CSV</button>'
            ),
            4,
        )
        self.assertNotIn("Export 24 h CSV", template)

    def test_fts_settings_use_section_save_and_cancel_controls(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        for form_id in (
            "fts-laser-settings-form",
            "fts-system-settings-form",
            "fts-port-settings-form",
        ):
            self.assertIn(f'id="{form_id}"', template)
        self.assertEqual(template.count('class="fts-save-settings"'), 3)
        self.assertEqual(template.count('class="fts-cancel-settings"'), 3)

    def test_live_view_builds_module_inventory_from_device_data(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = dashboard_scripts()
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="fts-uplink" aria-label="Uplink module"', template)
        self.assertIn('id="fts-modules" class="fts-module-grid" aria-label="Optical port modules"', template)
        self.assertLess(template.index('id="fts-uplink"'), template.index('Internal laser'))
        self.assertLess(template.index('Internal laser'), template.index('10 MHz synthesizer'))
        self.assertLess(template.index('10 MHz synthesizer'), template.index('TEC heater'))
        self.assertLess(template.index('TEC heater'), template.index('id="fts-modules"'))
        self.assertNotIn("fts-power-a", template)
        self.assertNotIn("fts-power-b", template)
        self.assertNotIn("status.power", script)
        self.assertNotIn("UL + 7 modular ports", template)
        self.assertIn("const inventory = [", script)
        self.assertIn("...(status.ports || []).map", script)
        self.assertIn("syncFtsModuleTargets(inventory)", script)
        self.assertIn("data-fts-module-target", script)
        self.assertIn('...(status.uplink ? [{ module: status.uplink, index: 0, uplink: true }]', script)
        self.assertIn('grid-template-columns: repeat(4, minmax(0, 1fr));', stylesheet)
        self.assertIn('function ftsPortState(module)', script)
        self.assertIn("return ['UP', 'LOCKED'].includes(reported) ? 'UP' : 'DOWN'", script)
        self.assertIn('.fts-pluggable-module.down:not(.unequipped)', stylesheet)
        self.assertIn("modules.innerHTML = portPositions.length", script)
        self.assertNotIn('fts-port-connectors', script)
        self.assertNotIn('fts-connector-socket', stylesheet)
        self.assertNotIn('id="fts-warning-count"', template)

    def test_laser_states_are_neutral_and_laser_warnings_are_not_exposed(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = dashboard_scripts()

        self.assertIn("? 'unknown' : 'reported'", script)
        self.assertNotIn("['locked', 'on', 'ok'", script)
        self.assertIn(
            'class="nav-link profile-amplifier-only" data-tab="warnings"',
            template,
        )
        self.assertIn(
            'class="tab-panel profile-amplifier-only" data-tab="warnings"',
            template,
        )
        self.assertNotIn("min: -65", script)
        self.assertNotIn("max: -33", script)
        self.assertNotIn("max: 100", script)
        self.assertNotIn("max: 50", script)

    def test_module_cards_open_the_selected_port_settings(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = dashboard_scripts()
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertNotIn("phase-stabilized optical carrier", template)
        self.assertNotIn("fts-signal-bus", template)
        self.assertNotIn(".fts-signal-bus", stylesheet)
        self.assertNotIn(".fts-pluggable-module:not(.unequipped):hover", stylesheet)
        self.assertNotIn(".fts-optical-rack", stylesheet)
        self.assertNotIn("fts-optical-rack", template)
        station_rules = [rule.split("}", 1)[0] for rule in stylesheet.split(".fts-station {")[1:]]
        self.assertTrue(station_rules)
        self.assertTrue(all("border:" not in rule for rule in station_rules))
        self.assertTrue(all("background:" not in rule for rule in station_rules))
        self.assertIn("setActiveTab('device-settings')", script)
        self.assertIn("uplinkRack?.addEventListener('click', selectModule)", script)
        self.assertIn("getElementById('fts-port-settings-form')", script)
        self.assertIn("scrollIntoView({ behavior: 'smooth', block: 'start' })", script)

    def test_fts_settings_and_service_diagnostics_are_flat_section_panels(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="data-panel fts-settings-panel"', template)
        self.assertIn('class="fts-settings-list"', template)
        self.assertNotIn('class="fts-settings-grid"', template)
        self.assertIn('class="data-panel service-diagnostics-panel"', template)
        self.assertIn('class="service-diagnostics-list"', template)
        self.assertNotIn('class="service-diagnostics-grid"', template)

    def test_front_panel_uses_normalized_units_without_connector_lamps(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = dashboard_scripts()

        self.assertIn("Modular optical station", template)
        self.assertNotIn("Optical module rack", template)
        self.assertIn("function displayMeasurement", script)
        self.assertNotIn("ftsConnectorCode", script)
        self.assertNotIn("ftsConnectorLabel", script)
        self.assertIn("displayMeasurement(firstValue(module, ['jitter']), '%')", script)
        self.assertIn("firstValue(module, ['optical_power_display', 'optical_power'])", script)
        self.assertIn("displayMeasurement(", script)
        self.assertNotIn("'equivalent_distance'", script)
        self.assertNotIn("'temperature_read'", script)
        self.assertNotIn("'current_frequency'", script)

    def test_dirty_state_is_based_on_a_loaded_value_baseline(self):
        script = dashboard_scripts()

        self.assertIn("const ftsInputBaselines = new Map()", script)
        self.assertIn("input.value === ftsInputBaselines.get(input.id)", script)

    def test_administration_tabs_use_service_diagnostics_layout(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

        self.assertEqual(template.count('class="data-panel administration-panel'), 4)
        self.assertEqual(template.count('class="administration-list"'), 4)
        self.assertNotIn('class="access-grid"', template)
        self.assertIn(".administration-section", stylesheet)
        self.assertIn(".administration-panel .network-status-grid", stylesheet)


if __name__ == "__main__":
    unittest.main()

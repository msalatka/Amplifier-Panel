import copy
import datetime
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from app.api import devices
from app.core import config
from app.services import control_requests, xml_status


class XmlStatusTests(unittest.TestCase):
    def setUp(self):
        self.mapping = xml_status.load_mapping()
        self.payload = (
            (pathlib.Path(__file__).parent / "fixtures/status.xml")
            .read_bytes()
            .replace(b"\r\n", b"\n")
        )

    def test_default_mapping_does_not_use_the_retired_locked_role(self):
        roles = {
            field.get("role", "")
            for device in self.mapping.values()
            for section in device["sections"]
            for field in section["fields"]
        }

        self.assertNotIn("locked", roles)

    def test_all_six_xml_sections_become_independent_profiles(self):
        data = xml_status.parse_status(self.payload, self.mapping)
        self.assertEqual(set(data), {"local", "local_di", "remote", "remote_di", "oba", "oba3"})
        self.assertEqual(len(data["local"]["sections"]), 1)
        self.assertEqual(len(data["local_di"]["sections"]), 1)
        self.assertEqual(data["oba3"]["values"]["oba3"]["Gain"], 30)
        self.assertEqual(data["oba"]["values"]["oba"]["mode"], "gain")
        self.assertTrue(all(d["present"] and not d["issues"] for d in data.values()))

    def test_renaming_xml_name_does_not_break_id_mapping(self):
        payload = self.payload.replace(b"<name>Gain</name>", b"<name>Renamed gain</name>")
        data = xml_status.parse_status(payload, self.mapping)
        self.assertEqual(data["oba3"]["values"]["oba3"]["Gain"], 30)

    def test_name_selector_and_display_label_are_editable(self):
        mapping = copy.deepcopy(self.mapping)
        field = mapping["oba3"]["sections"][0]["fields"][0]
        del field["id"]
        field.update(name="NewGain", label="Actual gain")
        payload = self.payload.replace(b"<name>Gain</name>", b"<name>NewGain</name>")
        result = xml_status.parse_status(payload, mapping)["oba3"]
        self.assertEqual(result["values"]["oba3"]["Gain"], 30)
        self.assertEqual(result["sections"][0]["fields"][0]["label"], "Actual gain")

    def test_boolean_fields_are_exposed_as_booleans(self):
        result = xml_status.parse_status(self.payload, self.mapping)
        self.assertIs(result["local"]["values"]["local"]["10MHzInSigDet"], False)
        self.assertIs(result["local"]["values"]["local"]["LaserLocked"], True)
        self.assertIs(result["remote"]["values"]["remote"]["PPSSigDet"], True)

    def test_writable_metadata_is_exposed_to_the_control_view(self):
        result = xml_status.parse_status(self.payload, self.mapping)["oba3"]
        gain_set = next(
            field
            for section in result["sections"]
            for field in section["fields"]
            if field["key"] == "GainSet"
        )

        self.assertIs(gain_set["writable"], True)
        self.assertEqual(gain_set["id"], "5.1.1.2")
        self.assertEqual(gain_set["name"], "GainSet")

    def test_alarm_configuration_is_validated_and_exposed(self):
        mapping = copy.deepcopy(self.mapping)
        field = mapping["oba3"]["sections"][0]["fields"][0]
        field["alarm"] = {"enabled": True, "minimum": 10, "maximum": 40}

        xml_status.validate_mapping(mapping)
        result = xml_status.parse_status(self.payload, mapping)["oba3"]
        descriptor = result["sections"][0]["fields"][0]

        self.assertEqual(descriptor["alarm"], {"enabled": True, "minimum": 10, "maximum": 40})

    def test_boolean_fields_reject_values_other_than_zero_or_one(self):
        payload = self.payload.replace(
            b"<name>LaserLocked</name>\n      <value>1</value>",
            b"<name>LaserLocked</name>\n      <value>2</value>",
            1,
        )
        result = xml_status.parse_status(payload, self.mapping)["local"]
        self.assertIsNone(result["values"]["local"]["LaserLocked"])
        self.assertIn("Invalid value: local.LaserLocked", result["issues"])

    def test_new_local_fields_are_discovered_without_mapping_changes(self):
        payload = self.payload.replace(
            b"</params_local>",
            b'<param id="2.1.1.99"><name>NewDiagnostic</name>'
            b"<value>12.5</value></param></params_local>",
            1,
        )
        result = xml_status.parse_status(payload, self.mapping)["local"]
        section = result["sections"][0]
        automatic = next(field for field in section["fields"] if field.get("automatic"))

        self.assertEqual(automatic["label"], "NewDiagnostic")
        self.assertEqual(automatic["group"], "NewDiagnostic")
        self.assertEqual(result["values"]["local"]["auto:2.1.1.99"], 12.5)

    def test_unmapped_remote_fields_still_require_explicit_configuration(self):
        payload = self.payload.replace(
            b"</params_remote>",
            b'<param id="3.1.1.99"><name>NewRemoteField</name>'
            b"<value>1</value></param></params_remote>",
            1,
        )
        result = xml_status.parse_status(payload, self.mapping)["remote"]

        self.assertNotIn("auto:3.1.1.99", result["values"]["remote"])

    def test_new_amplifier_fields_are_ready_for_live_view_and_history(self):
        payload = self.payload.replace(
            b"</params_oba3>",
            b'<param id="5.1.1.99"><name>NewAmplifierValue</name>'
            b"<value>42.5</value></param></params_oba3>",
            1,
        )
        result = xml_status.parse_status(payload, self.mapping)["oba3"]
        automatic = next(
            field for field in result["sections"][0]["fields"] if field["key"] == "auto:5.1.1.99"
        )

        self.assertEqual(automatic["label"], "NewAmplifierValue")
        self.assertEqual(automatic["id"], "5.1.1.99")
        self.assertEqual(automatic["name"], "NewAmplifierValue")
        self.assertEqual(automatic["type"], "number")
        self.assertEqual(result["values"]["oba3"]["auto:5.1.1.99"], 42.5)

    def test_unknown_xml_section_becomes_a_read_only_profile(self):
        payload = (
            b"<status><params_new_device>"
            b'<param id="9.1.1.1"><name>Temperature</name><value>24.5</value></param>'
            b"</params_new_device></status>"
        )

        result = xml_status.parse_status(payload, self.mapping)["new_device"]
        field = result["sections"][0]["fields"][0]

        self.assertEqual(result["label"], "New Device")
        self.assertEqual(result["profile"]["view_profile"], "station")
        self.assertEqual(result["values"]["new_device"]["auto:9.1.1.1"], 24.5)
        self.assertTrue(field["automatic"])
        self.assertNotIn("writable", field)

    def test_profile_metadata_controls_group_order_layout_and_snmp_index(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["custom"] = {
            "label": "Custom amplifier",
            "view_profile": "amplifier",
            "display_group": "Rack B",
            "order": 15,
            "snmp_index": 7,
            "sections": [
                {
                    "key": "custom",
                    "xml_section": "params_custom",
                    "label": "Custom amplifier",
                    "fields": [{"key": "power", "name": "Power", "type": "number"}],
                }
            ],
        }
        payload = (
            b"<status><params_custom><param><name>Power</name><value>10</value></param>"
            b"</params_custom></status>"
        )

        result = xml_status.parse_status(payload, xml_status.validate_mapping(mapping))["custom"]

        self.assertEqual(result["profile"]["label"], "Custom amplifier")
        self.assertEqual(result["profile"]["view_profile"], "amplifier")
        self.assertEqual(result["profile"]["display_group"], "Rack B")
        self.assertEqual(result["profile"]["order"], 15)
        self.assertEqual(result["profile"]["snmp_index"], 7)

    def test_mapping_rejects_duplicate_effective_snmp_index(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["oba3"]["sections"][0]["snmp_index"] = 1

        with self.assertRaisesRegex(ValueError, "Duplicate SNMP index"):
            xml_status.validate_mapping(mapping)

    def test_valid_poll_replaces_inventory_with_current_xml_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.xml"
            path.write_bytes(
                b'<status><params_new_device><param id="9.1.1.1">'
                b"<name>Value</name><value>1</value></param></params_new_device></status>"
            )
            inventories = []
            with (
                mock.patch.object(config, "XML_STATUS_FILE", str(path)),
                mock.patch.object(
                    xml_status.state,
                    "set_device_inventory",
                    side_effect=lambda definitions: inventories.append(
                        tuple(item["id"] for item in definitions)
                    ),
                ),
                mock.patch.object(
                    xml_status.state,
                    "snapshot_device_live",
                    return_value={"data": {}, "last_update": None},
                ),
                mock.patch.object(xml_status.runtime, "publish_snapshot"),
                mock.patch.object(control_requests, "reconcile") as reconcile,
            ):
                xml_status.poll_once()
                path.write_bytes(self.payload)
                xml_status.poll_once()

        self.assertEqual(inventories[0], ("new_device",))
        self.assertEqual(
            inventories[1],
            ("local", "local_di", "remote", "remote_di", "oba", "oba3"),
        )
        self.assertEqual(reconcile.call_count, 2)

    def test_missing_sections_do_not_invent_zero_values(self):
        result = xml_status.parse_status(b"<status><params_oba3/></status>", self.mapping)
        self.assertNotIn("local", result)
        self.assertIsNone(result["oba3"]["values"]["oba3"]["Gain"])
        self.assertTrue(result["oba3"]["issues"])

    def test_non_finite_values_are_missing_and_reported(self):
        payload = self.payload.replace(b"<value>30</value>", b"<value>NaN</value>")
        result = xml_status.parse_status(payload, self.mapping)["oba3"]
        self.assertIsNone(result["values"]["oba3"]["Gain"])
        self.assertTrue(result["issues"])

    def test_rejects_wrong_root_entities_and_oversized_files(self):
        for payload in (
            b"<snmp/>",
            b'<!DOCTYPE status [<!ENTITY x "abc">]><status/>',
            b" " * 1_000_001,
        ):
            with self.subTest(payload=payload[:60]), self.assertRaises(ValueError):
                xml_status.parse_status(payload, self.mapping)

    def test_poll_failure_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.xml"
            with (
                mock.patch.object(config, "XML_STATUS_FILE", str(path)),
                mock.patch.object(xml_status.state, "active_device_ids", return_value=("oba3",)),
                mock.patch.object(xml_status.runtime, "publish_snapshot") as publish,
                mock.patch.object(xml_status.runtime, "report_failure") as failure,
            ):
                xml_status.poll_once()
                failure.assert_called_once()
                path.write_bytes(self.payload)
                xml_status.poll_once()
                self.assertEqual(publish.call_count, 6)
                expected_mtime = pathlib.Path(path).stat().st_mtime
                published_timestamp = publish.call_args.kwargs["timestamp"]
                self.assertAlmostEqual(
                    datetime.datetime.fromisoformat(published_timestamp).timestamp(),
                    expected_mtime,
                    places=4,
                )
                os.utime(path, (time.time() - 3600, time.time() - 3600))
                xml_status.poll_once()
                self.assertIn("stale", failure.call_args.args[1])
                path.write_bytes(b"<status>")
                xml_status.poll_once()
                self.assertEqual(publish.call_count, 6)

    def test_poll_stores_history_only_for_devices_whose_values_changed(self):
        snapshots = xml_status.parse_status(self.payload, self.mapping)
        previous = {
            key: {"last_update": "earlier", "data": snapshot} for key, snapshot in snapshots.items()
        }
        changed_payload = self.payload.replace(
            b"<name>Gain</name>\n      <value>30</value>",
            b"<name>Gain</name>\n      <value>31</value>",
            1,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "status.xml"
            path.write_bytes(changed_payload)
            with (
                mock.patch.object(config, "XML_STATUS_FILE", str(path)),
                mock.patch.object(xml_status.state, "set_device_inventory"),
                mock.patch.object(
                    xml_status.state,
                    "snapshot_device_live",
                    side_effect=lambda key: previous[key],
                ),
                mock.patch.object(xml_status.state, "update_device_live") as update,
                mock.patch.object(xml_status.runtime, "publish_snapshot") as publish,
            ):
                xml_status.poll_once()

        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0], "oba3")
        self.assertEqual(update.call_count, 5)

    def test_history_rejects_profile_absent_from_xml(self):
        with mock.patch.object(devices.state, "is_active_device", return_value=False):
            with self.assertRaises(Exception) as caught:
                devices.device_history("local", _current_user={})
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

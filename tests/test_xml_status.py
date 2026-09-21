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
from app.services import xml_status


class XmlStatusTests(unittest.TestCase):
    def setUp(self):
        self.mapping = xml_status.load_mapping()
        self.payload = (pathlib.Path(__file__).parent / "fixtures/status.xml").read_bytes()

    def test_all_six_sections_in_four_views(self):
        data = xml_status.parse_status(self.payload, self.mapping)
        self.assertEqual(set(data), {"local", "remote", "oba", "oba3"})
        self.assertEqual(len(data["local"]["sections"]), 2)
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
            b"<param id=\"2.1.1.99\"><name>NewDiagnostic</name>"
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
            b"<param id=\"3.1.1.99\"><name>NewRemoteField</name>"
            b"<value>1</value></param></params_remote>",
            1,
        )
        result = xml_status.parse_status(payload, self.mapping)["remote"]

        self.assertNotIn("auto:3.1.1.99", result["values"]["remote"])

    def test_new_amplifier_fields_are_ready_for_live_view_and_history(self):
        payload = self.payload.replace(
            b"</params_oba3>",
            b"<param id=\"5.1.1.99\"><name>NewAmplifierValue</name>"
            b"<value>42.5</value></param></params_oba3>",
            1,
        )
        result = xml_status.parse_status(payload, self.mapping)["oba3"]
        automatic = next(
            field
            for field in result["sections"][0]["fields"]
            if field["key"] == "auto:5.1.1.99"
        )

        self.assertEqual(automatic["label"], "NewAmplifierValue")
        self.assertEqual(automatic["type"], "number")
        self.assertEqual(result["values"]["oba3"]["auto:5.1.1.99"], 42.5)

    def test_missing_sections_do_not_invent_zero_values(self):
        result = xml_status.parse_status(b"<status><params_oba3/></status>", self.mapping)
        self.assertFalse(result["local"]["present"])
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
                mock.patch.object(config, "ENABLED_DEVICES", ("oba3",)),
                mock.patch.object(xml_status.runtime, "publish_snapshot") as publish,
                mock.patch.object(xml_status.runtime, "report_failure") as failure,
            ):
                xml_status.poll_once()
                failure.assert_called_once()
                path.write_bytes(self.payload)
                xml_status.poll_once()
                publish.assert_called_once()
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
                self.assertEqual(publish.call_count, 1)

    def test_poll_stores_history_only_for_devices_whose_values_changed(self):
        snapshots = xml_status.parse_status(self.payload, self.mapping)
        previous = {
            key: {"last_update": "earlier", "data": snapshot}
            for key, snapshot in snapshots.items()
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
                mock.patch.object(config, "ENABLED_DEVICES", tuple(snapshots)),
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
        self.assertEqual(update.call_count, 3)

    def test_history_rejects_disabled_device(self):
        with mock.patch.object(config, "ENABLED_DEVICES", ("oba3",)):
            with self.assertRaises(Exception) as caught:
                devices.device_history("local", _current_user={})
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

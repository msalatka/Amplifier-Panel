import copy
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
                os.utime(path, (time.time() - 3600, time.time() - 3600))
                xml_status.poll_once()
                self.assertIn("stale", failure.call_args.args[1])
                path.write_bytes(b"<status>")
                xml_status.poll_once()
                self.assertEqual(publish.call_count, 1)

    def test_history_rejects_disabled_device(self):
        with mock.patch.object(config, "ENABLED_DEVICES", ("oba3",)):
            with self.assertRaises(Exception) as caught:
                devices.device_history("local", _current_user={})
            self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

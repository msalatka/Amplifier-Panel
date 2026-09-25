import json
import pathlib
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from app.services import xml_control


def mapping() -> dict:
    devices = {}
    for device_id in ("local", "remote", "oba", "oba3"):
        fields = [
            {
                "key": "setpoint",
                "id": f"{len(devices) + 2}.1.1.1",
                "name": "Setpoint",
                "type": "number",
                "writable": device_id == "oba3",
                "minimum": 0,
                "maximum": 40,
            },
            {
                "key": "measurement",
                "id": f"{len(devices) + 2}.1.1.2",
                "name": "Measurement",
                "type": "number",
            },
        ]
        devices[device_id] = {
            "label": device_id,
            "sections": [
                {
                    "key": device_id,
                    "xml_section": f"params_{device_id}",
                    "label": device_id,
                    "fields": fields,
                }
            ],
        }
    return devices


class XmlControlTests(unittest.TestCase):
    def test_control_is_validated_and_atomically_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            mapping_file = root / "mapping.json"
            control_file = root / "control.xml"
            mapping_file.write_text(json.dumps(mapping()), encoding="utf-8")
            with (
                mock.patch.object(xml_control.config, "XML_MAPPING_FILE", str(mapping_file)),
                mock.patch.object(xml_control.config, "XML_CONTROL_FILE", str(control_file)),
                mock.patch.object(
                    xml_control.uuid,
                    "uuid4",
                    return_value="11111111-1111-4111-8111-111111111111",
                ),
            ):
                result = xml_control.write_control("oba3", {"oba3:setpoint": 28.5})

            document = ET.parse(control_file)
            parameter = document.find("./request/device/parameter")
            self.assertEqual(result["state"], "pending")
            self.assertEqual(parameter.get("id"), "5.1.1.1")
            self.assertEqual(parameter.findtext("value"), "28.5")
            self.assertFalse(list(root.glob("*.tmp")))

    def test_read_only_unknown_and_out_of_range_values_are_rejected(self):
        with mock.patch.object(xml_control.xml_status, "load_mapping", return_value=mapping()):
            for values, message in (
                ({"oba3:measurement": 3}, "read-only"),
                ({"oba3:missing": 3}, "read-only"),
                ({"oba3:setpoint": 41}, "at most 40"),
                ({"oba3:setpoint": float("nan")}, "finite"),
            ):
                with self.subTest(values=values), self.assertRaisesRegex(ValueError, message):
                    xml_control.build_control_xml("oba3", values)

    def test_acknowledgement_is_read_from_status_not_control_file(self):
        payload = b"""<status><control_status>
          <last_request_id>11111111-1111-4111-8111-111111111111</last_request_id>
          <state>applied</state><message>OK</message>
        </control_status></status>"""
        with tempfile.TemporaryDirectory() as directory:
            status_file = pathlib.Path(directory) / "status.xml"
            status_file.write_bytes(payload)
            with mock.patch.object(xml_control.config, "XML_STATUS_FILE", str(status_file)):
                result = xml_control.read_acknowledgement()

        self.assertEqual(result["state"], "applied")
        self.assertEqual(result["request_id"], "11111111-1111-4111-8111-111111111111")

    def test_unsafe_acknowledgement_xml_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            status_file = pathlib.Path(directory) / "status.xml"
            status_file.write_text("<!DOCTYPE status><status/>", encoding="utf-8")
            with mock.patch.object(xml_control.config, "XML_STATUS_FILE", str(status_file)):
                result = xml_control.read_acknowledgement()
        self.assertEqual(result["state"], "invalid")

    def test_unacknowledged_request_times_out(self):
        request_id = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            control_file = root / "control.xml"
            status_file = root / "status.xml"
            control_file.write_text(
                '<?xml version="1.0"?><control version="1"><request '
                f'id="{request_id}" created_at="2020-01-01T00:00:00+00:00" />'
                "</control>",
                encoding="utf-8",
            )
            status_file.write_text("<status/>", encoding="utf-8")
            with (
                mock.patch.object(xml_control.config, "XML_CONTROL_FILE", str(control_file)),
                mock.patch.object(xml_control.config, "XML_STATUS_FILE", str(status_file)),
                mock.patch.object(xml_control.config, "XML_CONTROL_ACK_TIMEOUT_SECONDS", 15),
            ):
                result = xml_control.get_control_status()
        self.assertEqual(result["state"], "timeout")
        self.assertEqual(result["request_id"], request_id)


if __name__ == "__main__":
    unittest.main()

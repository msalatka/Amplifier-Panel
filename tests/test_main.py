import unittest
from unittest import mock

from app import main


class MainPageTests(unittest.TestCase):
    def test_dashboard_shell_is_available_without_telemetry_profiles(self):
        with (
            mock.patch.object(main.state, "active_device_ids", return_value=()),
            mock.patch.object(
                main.templates, "TemplateResponse", return_value="rendered"
            ) as render,
        ):
            result = main.home(mock.Mock())

        self.assertEqual(result, "rendered")
        context = render.call_args.kwargs["context"]
        self.assertFalse(context["has_devices"])
        self.assertEqual(context["selected_device"], "")


if __name__ == "__main__":
    unittest.main()

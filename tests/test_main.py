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


class MainStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_xml_poll_runs_in_a_worker_thread(self):
        with mock.patch.object(main.asyncio, "to_thread", new=mock.AsyncMock()) as to_thread:
            await main.initial_xml_poll()

        to_thread.assert_awaited_once_with(main.poll_once)


if __name__ == "__main__":
    unittest.main()

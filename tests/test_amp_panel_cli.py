import base64
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import amp_panel_cli


class AmpPanelCliTests(unittest.TestCase):
    def test_run_reports_a_timed_out_configuration_command(self):
        with mock.patch.object(
            amp_panel_cli.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["systemctl", "restart", "demo"], 30),
        ):
            with self.assertRaisesRegex(
                amp_panel_cli.ConfigurationError,
                r"within 30 seconds: systemctl restart demo",
            ):
                amp_panel_cli._run(
                    ["systemctl", "restart", "demo"],
                    capture=True,
                    timeout=30,
                )

    def test_status_is_non_interactive_and_omits_journal_lines(self):
        completed = mock.Mock(returncode=0)
        with (
            mock.patch.object(
                amp_panel_cli,
                "_command_exists",
                return_value=True,
            ),
            mock.patch.object(
                amp_panel_cli,
                "_run",
                return_value=completed,
            ) as run,
        ):
            result = amp_panel_cli.systemctl_command("status")

        self.assertEqual(result, 0)
        run.assert_called_once_with(
            [
                "systemctl",
                "--no-pager",
                "--full",
                "--lines=0",
                "status",
                "amp-panel.service",
            ]
        )

    def test_keyboard_interrupt_returns_shell_interrupt_code(self):
        with mock.patch.object(
            amp_panel_cli,
            "systemctl_command",
            side_effect=KeyboardInterrupt,
        ):
            result = amp_panel_cli.main(["status"])

        self.assertEqual(result, 130)

    def test_logs_never_open_a_pager(self):
        completed = mock.Mock(returncode=0)
        arguments = mock.Mock(lines=25, follow=False)
        with (
            mock.patch.object(
                amp_panel_cli,
                "_command_exists",
                return_value=True,
            ),
            mock.patch.object(
                amp_panel_cli,
                "_run",
                return_value=completed,
            ) as run,
        ):
            result = amp_panel_cli.logs_command(arguments)

        self.assertEqual(result, 0)
        run.assert_called_once_with(
            [
                "journalctl",
                "--no-pager",
                "-u",
                "amp-panel.service",
                "-n",
                "25",
            ]
        )

    def test_unprivileged_web_service_rejects_privileged_port(self):
        values = amp_panel_cli.default_configuration()
        values.update(
            {
                "AMP_PANEL_PORT": "80",
                "GAIN_SET_MIN": "0",
                "GAIN_SET_MAX": "20",
                "RADIUS_SERVER": "192.0.2.10",
                "RADIUS_SECRET": "secret",
            }
        )

        with self.assertRaisesRegex(
            amp_panel_cli.ConfigurationError,
            "between 1024 and 65535",
        ):
            amp_panel_cli.validate_configuration(values)

    def test_environment_file_round_trip_preserves_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "amp-panel.env"
            values = {
                "AMP_PANEL_PORT": "8123",
                "RADIUS_SECRET": 'space and "quotes" = # safe',
            }

            amp_panel_cli.write_env_file(path, values)

            self.assertEqual(amp_panel_cli.read_env_file(path), values)

    def test_encoded_installer_answers_preserve_special_characters(self):
        secret = ' radius # "secret" = value '
        values = amp_panel_cli.default_configuration()
        answers = {
            "radius_secret_b64": base64.b64encode(secret.encode()).decode(),
        }

        with mock.patch.object(
            amp_panel_cli,
            "_normalized_data_dir",
            return_value=pathlib.Path(values["AMP_PANEL_DATA_DIR"]),
        ):
            amp_panel_cli._apply_answers(values, answers)

        self.assertEqual(values["RADIUS_SECRET"], secret)

    def test_fts_ls_installer_answers_select_profile_and_serial_defaults(self):
        password = "adm!n with spaces"
        values = amp_panel_cli.default_configuration()
        answers = {
            "device_profile_b64": base64.b64encode(b"fts-ls").decode(),
            "fts_ls_username_b64": base64.b64encode(b"appadmin").decode(),
            "fts_ls_password_b64": base64.b64encode(password.encode()).decode(),
        }
        with mock.patch.object(
            amp_panel_cli,
            "_normalized_data_dir",
            return_value=pathlib.Path(values["AMP_PANEL_DATA_DIR"]),
        ):
            amp_panel_cli._apply_answers(values, answers)

        self.assertEqual(values["DEVICE_PROFILE"], "fts-ls")
        self.assertEqual(values["SERIAL_BAUDRATE"], "115200")
        self.assertEqual(values["FTS_LS_PASSWORD"], password)
        self.assertEqual(values["GAIN_SET_MIN"], "-100")
        self.assertEqual(values["GAIN_SET_MAX"], "100")


if __name__ == "__main__":
    unittest.main()

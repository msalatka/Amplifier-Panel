import json
import pathlib
import tempfile
import unittest
from unittest import mock

from app.core import config, passwords, state


class StateSecurityTests(unittest.TestCase):
    def test_inaccessible_persisted_state_falls_back_to_defaults(self):
        with (
            mock.patch.object(
                config,
                "PERSISTED_STATE_FILE",
                "/inaccessible/persisted_state.json",
            ),
            mock.patch.object(
                pathlib.Path,
                "exists",
                side_effect=PermissionError("access denied"),
            ),
        ):
            self.assertEqual(state.load_persisted_state(), {})

    def test_zero_database_limit_means_unlimited(self):
        settings = state.merge_service_settings({"database_max_records": 0})
        self.assertEqual(settings["database_max_records"], 0)

    def test_temperature_thresholds_are_added_to_existing_settings(self):
        settings = state.merge_dashboard_settings(
            {
                "warn_limits": {
                    "PiA": {"min": -20.0, "max": 5.0},
                },
            }
        )

        self.assertEqual(settings["warn_limits"]["PiA"], {"min": -20.0, "max": 5.0})
        self.assertEqual(
            settings["warn_limits"]["temperature"],
            {"min": None, "max": None},
        )

    def test_unsafe_persisted_dashboard_settings_fall_back_to_defaults(self):
        settings = state.merge_dashboard_settings(
            {
                "gain_tolerance": -1,
                "warn_limits": {
                    "temperature": {"min": 100.0, "max": 10.0},
                },
            }
        )
        self.assertEqual(settings, state.DEFAULT_DASHBOARD_SETTINGS)

    def test_unsafe_persisted_gain_set_is_not_restored_to_device(self):
        self.assertEqual(state.merge_last_known_gain_set(float("nan")), 15.0)
        self.assertEqual(state.merge_last_known_gain_set(1000), 15.0)

    def test_fresh_install_uses_configured_admin_without_password(self):
        with mock.patch.object(
            config,
            "INITIAL_ADMIN_USERNAME",
            "radius-admin@example.com",
        ):
            users = state.merge_access_users(None)
        self.assertEqual(users[0]["username"], "radius-admin@example.com")
        self.assertEqual(users[0]["role"], "Administrator")
        self.assertNotIn("password_hash", users[0])
        self.assertNotIn("password_salt", users[0])

    def test_valid_password_hashes_are_preserved(self):
        password_hash, password_salt = passwords.hash_password("correct-horse-battery")
        users = state.merge_access_users(
            [
                {
                    "username": "operator",
                    "role": "Operator",
                    "active": True,
                    "password_hash": password_hash,
                    "password_salt": password_salt,
                }
            ]
        )
        self.assertEqual(
            users,
            [
                {
                    "username": "operator",
                    "role": "Operator",
                    "active": True,
                    "password_hash": password_hash,
                    "password_salt": password_salt,
                }
            ],
        )

    def test_persisted_state_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "state.json"
            with mock.patch.object(config, "PERSISTED_STATE_FILE", str(path)):
                state.save_persisted_state()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("access_users", payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_local_password_hash_verifies_without_storing_clear_text(self):
        password_hash, password_salt = passwords.hash_password("correct-horse-battery")
        self.assertTrue(passwords.verify_password("correct-horse-battery", password_hash, password_salt))
        self.assertFalse(passwords.verify_password("wrong-password", password_hash, password_salt))
        self.assertNotIn("correct-horse-battery", password_hash)

    def test_switching_to_local_authentication_seeds_an_existing_radius_admin(self):
        password_hash, password_salt = passwords.hash_password("correct-horse-battery")
        with mock.patch.multiple(
            config,
            AUTH_MODE="local",
            INITIAL_ADMIN_USERNAME="admin",
            INITIAL_ADMIN_PASSWORD_HASH=password_hash,
            INITIAL_ADMIN_PASSWORD_SALT=password_salt,
        ):
            users = state.merge_access_users(
                [{"username": "admin", "role": "Administrator", "active": True}]
            )
        self.assertTrue(
            passwords.verify_password(
                "correct-horse-battery",
                users[0]["password_hash"],
                users[0]["password_salt"],
            )
        )


if __name__ == "__main__":
    unittest.main()

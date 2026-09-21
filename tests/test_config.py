import os
import unittest
from unittest import mock

from app.core import config


class ConfigParsingTests(unittest.TestCase):
    def test_env_int_parses_integer_and_uses_default_for_invalid_value(self):
        with mock.patch.dict(os.environ, {"TEST_INTEGER": "42"}):
            self.assertEqual(config._env_int("TEST_INTEGER", 7), 42)
        with mock.patch.dict(os.environ, {"TEST_INTEGER": "not-an-integer"}):
            self.assertEqual(config._env_int("TEST_INTEGER", 7), 7)

    def test_env_float_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf", "invalid"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"TEST_FLOAT": value}):
                self.assertEqual(config._env_float("TEST_FLOAT", 3.5), 3.5)

    def test_initial_admin_username_is_validated(self):
        self.assertEqual(
            config._initial_admin_username("radius-admin@example.com"),
            "radius-admin@example.com",
        )
        for value in ("", "contains space", "admin!", "x" * 129):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                config._initial_admin_username(value)


if __name__ == "__main__":
    unittest.main()

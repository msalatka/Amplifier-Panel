import datetime
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

import fastapi
from starlette.requests import Request

from app.api import auth
from app.core import config, state


class LoginSessionTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(state, "auth_sessions", {}),
            mock.patch.object(state, "login_failures", {}),
            mock.patch.object(
                state,
                "access_users",
                [
                    {"username": name, "role": "Operator", "active": True}
                    for name in ("alice", "bob")
                ],
            ),
            mock.patch.object(auth.radius_service, "authenticate", return_value=True),
            mock.patch.object(auth.api_security, "audit_event"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})

    def login(self, username="alice"):
        response = fastapi.Response()
        auth.login(auth.LoginRequest(username=username, password="test"), response, self.request)
        return response

    def test_second_login_is_rejected_without_invalidating_original(self):
        self.login()
        original = dict(state.auth_sessions)
        with self.assertRaises(fastapi.HTTPException) as error:
            self.login()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(state.auth_sessions, original)
        self.assertEqual(
            auth.api_security.get_current_user(next(iter(original)))["username"], "alice"
        )

    def test_expired_session_allows_login(self):
        state.auth_sessions["expired"] = {
            "username": "alice",
            "created_at": (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(seconds=config.SESSION_MAX_AGE_SECONDS + 1)
            ).isoformat(),
        }
        self.login()
        self.assertNotIn("expired", state.auth_sessions)
        self.assertEqual(len(state.auth_sessions), 1)

    def test_logout_allows_login_again(self):
        self.login()
        token = next(iter(state.auth_sessions))
        auth.logout(fastapi.Response(), self.request, {"username": "alice"}, token)
        self.login()
        self.assertNotIn(token, state.auth_sessions)
        self.assertEqual(len(state.auth_sessions), 1)

    def test_other_account_can_login(self):
        self.login()
        self.login("bob")
        self.assertEqual(len(state.auth_sessions), 2)

    def test_invalid_password_does_not_reveal_existing_session(self):
        self.login()
        with mock.patch.object(auth.radius_service, "authenticate", return_value=False):
            with self.assertRaises(fastapi.HTTPException) as error:
                self.login()
        self.assertEqual(error.exception.status_code, 401)

    def test_simultaneous_logins_create_only_one_session(self):
        barrier = Barrier(2)

        def authenticate(*args):
            barrier.wait(timeout=5)
            return True

        def attempt():
            try:
                self.login()
                return 200
            except fastapi.HTTPException as error:
                return error.status_code

        with mock.patch.object(auth.radius_service, "authenticate", side_effect=authenticate):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(len(state.auth_sessions), 1)

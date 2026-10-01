import unittest
from unittest import mock

from app.services import control_requests


class ControlRequestReconciliationTests(unittest.TestCase):
    def test_background_reconciliation_persists_and_audits_a_transition_once(self):
        status = {
            "request_id": "11111111-1111-4111-8111-111111111111",
            "request_device_id": "oba3",
            "state": "applied",
            "message": "OK",
        }
        with (
            mock.patch.object(
                control_requests.xml_control, "get_control_status", return_value=status
            ),
            mock.patch.object(
                control_requests.database,
                "update_control_request_status",
                side_effect=(True, False),
            ) as update,
            mock.patch.object(control_requests.syslog, "send_audit") as audit,
        ):
            self.assertEqual(control_requests.reconcile(), status)
            self.assertEqual(control_requests.reconcile(), status)

        self.assertEqual(update.call_count, 2)
        audit.assert_called_once()
        self.assertIn("device=oba3", audit.call_args.args[3])

    def test_missing_request_does_not_touch_history(self):
        with (
            mock.patch.object(
                control_requests.xml_control,
                "get_control_status",
                return_value={"request_id": None, "state": "idle", "message": "none"},
            ),
            mock.patch.object(control_requests.database, "update_control_request_status") as update,
        ):
            control_requests.reconcile()

        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()

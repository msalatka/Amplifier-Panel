"""Reconcile device control acknowledgements with the local request history."""

import json

from app.services import database, syslog, xml_control


def reconcile() -> dict:
    """Persist the current control state and audit each transition exactly once."""

    status = xml_control.get_control_status()
    request_id = status.get("request_id")
    device_id = status.get("request_device_id")
    if (
        request_id
        and device_id
        and database.update_control_request_status(request_id, status["state"], status["message"])
    ):
        syslog.send_audit(
            "device_control_status_changed",
            "system",
            "local",
            f"device={device_id}; request_id={request_id}; "
            f"state={status['state']}; "
            f"message={json.dumps(status['message'], ensure_ascii=False)}",
        )
    return status

"""
Smart SMS Payload Parser for Message487 (life.andre.message487 on Android)
and generic webhook formats.
"""

from typing import Any


def parse_sms_payload(
    payload_json: Any | None = None,
    form_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Extract SMS sender, recipient, message body, external message ID, device code,
    device ID, message type, occurred_at timestamp, and source channel.
    """
    result: dict[str, Any] = {
        "provider": "generic",
        "sender": None,
        "recipient": None,
        "message_body": None,
        "external_message_id": None,
        "device_code": None,
        "device_id": None,
        "message_type": None,
        "occurred_at": None,
        "channel_id": None,
        "source_name": None,
    }

    data: dict[str, Any] = {}
    if isinstance(payload_json, dict):
        data.update(payload_json)
    if form_data:
        data.update(form_data)

    if not data:
        return result

    source_val = str(data.get("source") or "").lower()
    source_name_val = str(data.get("source_name") or "").lower()

    # 1. Message487 Detection
    # Has device_code, or source/source_name contains message487, or schema_version is present
    if (
        "message487" in source_val
        or "message487" in source_name_val
        or "device_code" in data
        or "device_id" in data
        or "schema_version" in data
    ):
        result["provider"] = "message487"
        result["source_name"] = data.get("source_name") or "Message487"
        result["device_code"] = str(data.get("device_code")) if data.get("device_code") else None
        result["device_id"] = str(data.get("device_id")) if data.get("device_id") else None
        result["message_type"] = str(data.get("message_type") or data.get("source") or "") or None
        result["occurred_at"] = str(data.get("occurred_at") or data.get("timestamp") or "") or None
        result["channel_id"] = str(data.get("channel_id")) if data.get("channel_id") else None
        result["external_message_id"] = str(data.get("event_id")) if data.get("event_id") else None

        # Text / Message
        result["message_body"] = str(data.get("text") or data.get("message") or data.get("body") or "")

        # Sender & Recipient (for SMS / notifications)
        result["sender"] = str(data.get("sender") or data.get("from") or data.get("originator") or "") or None
        result["recipient"] = str(data.get("recipient") or data.get("to") or data.get("destination") or "") or None

        # If sender is missing but it's a test event
        if not result["sender"] and result["message_type"] == "test":
            result["sender"] = f"Test ({result['device_code'] or 'Android'})"

        return result

    # 2. Generic / Fallback Detection
    sender_keys = ["sender", "from", "from_number", "phone", "phone_number", "originator"]
    recipient_keys = ["recipient", "to", "to_number", "dest", "destination"]
    body_keys = ["text", "message", "body", "msg", "content", "payload"]
    id_keys = ["event_id", "id", "message_id", "msg_id", "sms_id", "uuid"]
    device_keys = ["device_code", "device_id", "device"]
    time_keys = ["occurred_at", "timestamp", "time", "date", "created_at"]

    data_lower = {k.lower(): v for k, v in data.items() if isinstance(k, str)}

    for key in sender_keys:
        if key in data_lower and data_lower[key] is not None:
            result["sender"] = str(data_lower[key])
            break

    for key in recipient_keys:
        if key in data_lower and data_lower[key] is not None:
            result["recipient"] = str(data_lower[key])
            break

    for key in body_keys:
        if key in data_lower and data_lower[key] is not None:
            result["message_body"] = str(data_lower[key])
            break

    for key in id_keys:
        if key in data_lower and data_lower[key] is not None:
            result["external_message_id"] = str(data_lower[key])
            break

    for key in device_keys:
        if key in data_lower and data_lower[key] is not None:
            result["device_code"] = str(data_lower[key])
            break

    for key in time_keys:
        if key in data_lower and data_lower[key] is not None:
            result["occurred_at"] = str(data_lower[key])
            break

    if result["sender"] or result["message_body"]:
        result["provider"] = "sms_app"

    return result

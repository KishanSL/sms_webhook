"""Unit tests for SMS parser logic."""

from app.sms_parser import parse_sms_payload


def test_parse_message487_sms():
    data = {
        "event_id": "evt-123456",
        "device_code": "pixel_7",
        "timestamp": 1718000000000,
        "source": "sms",
        "sender": "+15551234567",
        "recipient": "+15557654321",
        "message": "Your verification code is 449201",
    }
    result = parse_sms_payload(payload_json=data)
    assert result["provider"] == "message487"
    assert result["sender"] == "+15551234567"
    assert result["recipient"] == "+15557654321"
    assert result["message_body"] == "Your verification code is 449201"
    assert result["external_message_id"] == "evt-123456"
    assert result["device_code"] == "pixel_7"


def test_parse_message487_notification():
    data = {
        "event_id": "notif-9988",
        "device_code": "galaxy_s22",
        "timestamp": 1718000000000,
        "source": "notification",
        "sender": "WhatsApp / John",
        "message": "See you at the homelab meeting tomorrow",
    }
    result = parse_sms_payload(payload_json=data)
    assert result["provider"] == "message487"
    assert result["sender"] == "WhatsApp / John"
    assert result["message_body"] == "See you at the homelab meeting tomorrow"
    assert result["external_message_id"] == "notif-9988"


def test_parse_generic_fallback():
    data = {
        "sender": "AlertService",
        "text": "Disk space warning",
        "id": "gen-1",
    }
    result = parse_sms_payload(payload_json=data)
    assert result["sender"] == "AlertService"
    assert result["message_body"] == "Disk space warning"
    assert result["external_message_id"] == "gen-1"

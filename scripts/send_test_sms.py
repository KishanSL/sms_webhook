#!/usr/bin/env python3
"""
Test script to simulate incoming webhooks (Message487 Android, generic form/JSON).
Sends directly to the HTTPS webhook service using self-signed TLS.
"""

import argparse
import time
import uuid

import httpx


def test_message487_sms(base_url: str, secret: str | None = None):
    url = f"{base_url.rstrip('/')}/webhook/message487"
    event_id = str(uuid.uuid4())
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    payload = {
        "event_id": event_id,
        "device_code": "pixel_7_homelab",
        "timestamp": int(time.time() * 1000),
        "source": "sms",
        "sender": "+1-800-555-0199",
        "message": "Your verification security code is 782491. Do not share this code.",
        "extra": {
            "sim_slot": 1,
            "subscription_id": 2,
        },
    }

    print(f"\n[1] Sending Message487 SMS Webhook to {url}...")
    with httpx.Client(verify=False) as client:
        res = client.post(url, json=payload, headers=headers)
        print(f"Status Code: {res.status_code}")
        print(f"Response:    {res.json()}")


def test_message487_notification(base_url: str, secret: str | None = None):
    url = f"{base_url.rstrip('/')}/webhook/message487"
    event_id = str(uuid.uuid4())
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    payload = {
        "event_id": event_id,
        "device_code": "pixel_7_homelab",
        "timestamp": int(time.time() * 1000),
        "source": "notification",
        "sender": "Home Assistant",
        "message": "Motion detected in Driveway at 14:48",
        "title": "Driveway Camera Alert",
    }

    print(f"\n[2] Sending Message487 Notification Webhook to {url}...")
    with httpx.Client(verify=False) as client:
        res = client.post(url, json=payload, headers=headers)
        print(f"Status Code: {res.status_code}")
        print(f"Response:    {res.json()}")


def test_generic_form(base_url: str, secret: str | None = None):
    url = f"{base_url.rstrip('/')}/webhook/sms"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    form_data = {
        "sender": "+12025550143",
        "text": "System reboot completed on Server 01",
        "event_id": f"form-{uuid.uuid4().hex[:12]}",
    }

    print(f"\n[3] Sending generic URL-encoded SMS Webhook to {url}...")
    with httpx.Client(verify=False) as client:
        res = client.post(url, data=form_data, headers=headers)
        print(f"Status Code: {res.status_code}")
        print(f"Response:    {res.json()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send test webhooks to Homelab HTTPS Webhook Service")
    parser.add_argument("--url", default="https://127.0.0.1:8443", help="Base URL of webhook service")
    parser.add_argument("--secret", default=None, help="Bearer token secret if configured")
    args = parser.parse_args()

    print(f"Testing HTTPS Webhook Service at {args.url} (Ignoring self-signed cert warnings for test)")
    test_message487_sms(args.url, args.secret)
    test_message487_notification(args.url, args.secret)
    test_generic_form(args.url, args.secret)
    print("\nAll test webhooks sent successfully!")

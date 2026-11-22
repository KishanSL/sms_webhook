"""Test the exact Message487 payload provided by the user."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "user_payload_test.db")
    monkeypatch.setattr(settings, "DB_PATH", test_db)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", None)
    await init_db(test_db)
    yield


@pytest.mark.anyio
async def test_exact_user_message487_payload():
    user_payload = {
        "schema_version": 1,
        "event_id": "5c90e06a-2cf7-47c4-b23c-9cdc04e78ad4",
        "device_id": "124b5e24-3c4b-436c-87df-5b48da430859",
        "device_code": "moto-edge-70-fusion",
        "message_type": "test",
        "occurred_at": "2026-11-14T09:20:43.419870Z",
        "source": "life.andre.message487",
        "source_name": "Message487",
        "text": "Message487 connection test",
        "channel_id": "test",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Send to /webhook/message487 with Bearer token
        res = await client.post(
            "/webhook/message487",
            json=user_payload,
            headers={"Authorization": "Bearer sample_token_123"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "5c90e06a-2cf7-47c4-b23c-9cdc04e78ad4"

        # Check DB retrieval
        list_res = await client.get("/api/webhooks")
        items = list_res.json()
        assert len(items) == 1
        item = items[0]
        assert item["event_id"] == "5c90e06a-2cf7-47c4-b23c-9cdc04e78ad4"
        assert item["device_code"] == "moto-edge-70-fusion"
        assert item["device_id"] == "124b5e24-3c4b-436c-87df-5b48da430859"
        assert item["message_type"] == "test"
        assert item["message_body"] == "Message487 connection test"
        assert item["occurred_at"] == "2026-11-14T09:20:43.419870Z"
        assert item["channel_id"] == "test"

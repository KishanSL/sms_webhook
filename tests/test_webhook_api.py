"""Integration tests for FastAPI webhook receiver endpoints."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_bank_map, init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "api_test.db")
    monkeypatch.setattr(settings, "DB_PATH", test_db)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", None)
    await init_db(test_db)
    yield


@pytest.mark.anyio
async def test_message487_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {
            "event_id": "m487-evt-001",
            "device_code": "android_phone_1",
            "timestamp": 1718000000000,
            "source": "sms",
            "sender": "+1234567890",
            "message": "OTP verification: 9812",
        }
        res = await client.post("/webhook/message487", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "m487-evt-001"
        assert data["duplicate"] is False

        # Test duplicate event (Message487 retry behavior)
        res_dup = await client.post("/webhook/message487", json=payload)
        assert res_dup.status_code == 200
        data_dup = res_dup.json()
        assert data_dup["status"] == "accepted"
        assert data_dup["event_id"] == "m487-evt-001"
        assert data_dup["duplicate"] is True


@pytest.mark.anyio
async def test_generic_form_post():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        form = {"sender": "+18005550199", "text": "Form webhook text", "event_id": "form-1"}
        res = await client.post(
            "/webhook/sms",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        assert data["event_id"] == "form-1"


@pytest.mark.anyio
async def test_bearer_authentication(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_SECRET", "super_secret_token_123")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {"message": "secure message"}

        # Without token -> 401
        res = await client.post("/webhook/sms", json=payload)
        assert res.status_code == 401

        # With invalid token -> 401
        res = await client.post(
            "/webhook/sms",
            json=payload,
            headers={"Authorization": "Bearer wrong_token"},
        )
        assert res.status_code == 401

        # With correct token -> 200
        res = await client.post(
            "/webhook/sms",
            json=payload,
            headers={"Authorization": "Bearer super_secret_token_123"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"


@pytest.mark.anyio
async def test_api_list_and_stats():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Send 1 message
        await client.post(
            "/webhook/sms",
            json={"sender": "Mom", "message": "Call me later", "id": "sms-100"},
        )

        # Query list
        res = await client.get("/api/webhooks")
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 1
        assert items[0]["sender"] == "Mom"

        # Query stats
        res_stats = await client.get("/api/stats")
        assert res_stats.status_code == 200
        stats = res_stats.json()
        assert stats["total_count"] == 1


@pytest.mark.anyio
async def test_bank_sms_creates_transaction():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        base = {
            "schema_version": 1,
            "device_code": "android_phone_1",
            "message_type": "sms",
            "source": "android",
            "channel_id": "sms",
        }
        spend = {
            **base,
            "event_id": "txn-evt-001",
            "occurred_at": "2026-11-17T23:06:50Z",
            "sender": "BP-ICICIT-S",
            "text": "INR 349.00 spent using ICICI Bank Card XX3333 on 18-Nov-26 on AMAZON PAY IN E. "
                    "Avl Limit: INR 14,79,164.64. If not you, call 1800 2662.",
        }
        toll = {
            **base,
            "event_id": "txn-evt-002",
            "occurred_at": "2026-11-17T19:03:04Z",
            "sender": "VD-IDFCFB-S",
            "text": "INR 120 toll paid from IDFC FIRST Bank Tag 3XXXE000 at Devanahalli Toll Plaza "
                    "on 18/11/2026 00:27. Avbl. Bal.: INR918.00.",
        }
        assert (await client.post("/webhook/message487", json=spend)).status_code == 200
        assert (await client.post("/webhook/message487", json=toll)).status_code == 200
        # Retry of the same event must not create a second transaction
        assert (await client.post("/webhook/message487", json=spend)).status_code == 200

        res = await client.get("/api/transactions")
        assert res.status_code == 200
        txns = res.json()
        assert len(txns) == 1
        assert txns[0]["event_id"] == "txn-evt-001"
        assert txns[0]["bank"] == "ICICI Bank"
        assert txns[0]["amount"] == 349.0
        assert txns[0]["occurred_at"] == "2026-11-17T23:06:50+00:00"

        assert len((await client.get("/api/transactions?bank=ICICIT")).json()) == 1
        assert len((await client.get("/api/transactions?bank=HDFCBK")).json()) == 0

        # Backfill finds nothing new: the spend is recorded and the toll doesn't parse
        res = await client.post("/api/transactions/backfill")
        assert res.json() == {"status": "ok", "added": 0}


@pytest.mark.anyio
async def test_dashboard_pages_render():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/transactions")
        assert res.status_code == 200
        assert '<option value="HDFC Bank">HDFC Bank</option>' in res.text
        assert 'href="/dashboard"' in res.text

        res = await client.get("/dashboard")
        assert res.status_code == 200
        assert 'href="/transactions"' in res.text


@pytest.mark.anyio
async def test_account_names():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {
            "schema_version": 1,
            "event_id": "acct-evt-001",
            "device_code": "android_phone_1",
            "message_type": "sms",
            "occurred_at": "2026-11-16T09:24:22Z",
            "sender": "AD-HDFCBK-S",
            "text": "Spent Rs.641 On HDFC Bank Card 2222 At Swiggy Instamart On 2026-11-16:14:54:18.",
        }
        assert (await client.post("/webhook/message487", json=payload)).status_code == 200

        accounts = (await client.get("/api/accounts")).json()
        assert accounts == [{
            "sender_header": "HDFCBK",
            "bank": "HDFC Bank",
            "instrument": "credit_card",
            "account_last4": "2222",
            "name": None,
            "gnucash_account_id": None,
            "txn_count": 1,
            "last_used_at": "2026-11-16T09:24:22+00:00",
        }]

        res = await client.put("/api/accounts/hdfcbk/2222", json={"name": "  HDFC Swiggy "})
        assert res.status_code == 200
        assert res.json()["name"] == "HDFC Swiggy"
        assert (await client.get("/api/accounts")).json()[0]["name"] == "HDFC Swiggy"
        assert (await client.get("/api/transactions")).json()[0]["account_name"] == "HDFC Swiggy"

        # Empty name clears it
        await client.put("/api/accounts/HDFCBK/2222", json={"name": ""})
        assert (await client.get("/api/transactions")).json()[0]["account_name"] is None

        assert (await client.put("/api/accounts/NOPE00/2222", json={"name": "x"})).status_code == 404


ICICI_SPEND = {
    "schema_version": 1,
    "device_code": "android_phone_1",
    "message_type": "sms",
    "occurred_at": "2026-11-17T23:06:50Z",
    "text": "INR 349.00 spent using ICICI Bank Card XX3333 on 18-Nov-26 on AMAZON PAY IN E. "
            "Avl Limit: INR 14,79,164.64.",
}


@pytest.mark.anyio
async def test_bank_linking_and_backfill():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        banks = {b["sender_header"]: b["name"] for b in (await client.get("/api/banks")).json()}
        assert banks["HDFCBK"] == "HDFC Bank"

        # SMS from an unlinked header is stored but not parsed
        payload = {**ICICI_SPEND, "event_id": "link-evt-001", "sender": "AD-ICICIB-S"}
        assert (await client.post("/webhook/message487", json=payload)).status_code == 200
        assert (await client.get("/api/transactions")).json() == []

        senders = (await client.get("/api/senders")).json()
        assert senders == [{
            "sender_header": "ICICIB",
            "bank": None,
            "message_count": 1,
            "senders": ["AD-ICICIB-S"],
            "last_seen_at": senders[0]["last_seen_at"],
        }]

        # Linking the header parses the stored SMS straight away
        res = await client.put("/api/banks/icicib", json={"name": "ICICI Bank"})
        assert res.json() == {"sender_header": "ICICIB", "name": "ICICI Bank", "transactions_added": 1}
        assert (await client.get("/api/senders")).json()[0]["bank"] == "ICICI Bank"

        # Renaming a bank updates existing transactions
        await client.put("/api/banks/ICICIB", json={"name": "ICICI Amazon"})
        assert (await client.get("/api/transactions")).json()[0]["bank"] == "ICICI Amazon"
        assert len((await client.get("/api/transactions?bank=ICICI Amazon")).json()) == 1

        assert (await client.put("/api/banks/BAD", json={"name": "x"})).status_code == 422
        assert (await client.put("/api/banks/ABCDEF", json={"name": ""})).status_code == 422

        assert (await client.delete("/api/banks/ICICIB")).status_code == 200
        assert (await client.delete("/api/banks/ICICIB")).status_code == 404
        # Transactions are kept after unlinking
        assert len((await client.get("/api/transactions")).json()) == 1


@pytest.mark.anyio
async def test_deleted_default_bank_stays_deleted():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.delete("/api/banks/MYSBIC")).status_code == 200
        await init_db(settings.DB_PATH)
        banks = [b["sender_header"] for b in (await client.get("/api/banks")).json()]
        assert "MYSBIC" not in banks
        b_map = await get_bank_map(settings.DB_PATH)
        assert "MYSBIC" not in b_map


@pytest.mark.anyio
async def test_mark_transaction_read():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {**ICICI_SPEND, "event_id": "read-evt-001", "sender": "BP-ICICIT-S"}
        assert (await client.post("/webhook/message487", json=payload)).status_code == 200

        txn = (await client.get("/api/transactions")).json()[0]
        assert txn["is_read"] == 0
        assert txn["read_at"] is None
        assert len((await client.get("/api/transactions?read=false")).json()) == 1
        assert len((await client.get("/api/transactions?read=true")).json()) == 0

        res = await client.put(f"/api/transactions/{txn['id']}/read", json={"read": True})
        assert res.json() == {"id": txn["id"], "read": True}
        txn = (await client.get("/api/transactions")).json()[0]
        assert txn["is_read"] == 1
        assert txn["read_at"] is not None
        assert len((await client.get("/api/transactions?read=false")).json()) == 0

        # Linked webhook is also marked read
        wh = (await client.get(f"/api/webhooks/event/{txn['event_id']}")).json()
        assert wh["is_read"] == 1
        assert wh["read_at"] is not None

        await client.put(f"/api/transactions/{txn['id']}/read", json={"read": False})
        txn = (await client.get("/api/transactions")).json()[0]
        assert txn["is_read"] == 0
        assert txn["read_at"] is None

        # Linked webhook is also marked unread
        wh = (await client.get(f"/api/webhooks/event/{txn['event_id']}")).json()
        assert wh["is_read"] == 0
        assert wh["read_at"] is None

        assert (await client.put("/api/transactions/999/read", json={"read": True})).status_code == 404


@pytest.mark.anyio
async def test_settings_page_renders():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/settings")
        assert res.status_code == 200
        assert 'href="/transactions"' in res.text
        # The old /banks URL redirects to /settings.
        old = await client.get("/banks")
        assert old.status_code == 308
        assert old.headers["location"] == "/settings"


@pytest.mark.anyio
async def test_concurrent_startup(tmp_path, monkeypatch):
    """run.py starts HTTPS and HTTP servers in one process; both run the lifespan at once."""
    import app.main as main_module

    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "fresh.db"))
    monkeypatch.setattr(settings, "AUTO_GENERATE_CERT", False)
    monkeypatch.setattr(main_module, "_startup_done", False)

    async def run_lifespan():
        async with main_module.lifespan(app):
            pass

    await asyncio.gather(run_lifespan(), run_lifespan())
    assert main_module._startup_done is True


@pytest.mark.anyio
async def test_startup_does_not_touch_certificates(tmp_path, monkeypatch):
    """Certificate handling lives in run.py; the app lifespan must not create or overwrite certs."""
    import app.cert_manager as cert_manager
    import app.main as main_module

    calls: list[str] = []
    monkeypatch.setattr(cert_manager, "ensure_self_signed_cert", lambda *a, **k: calls.append("generated"))
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "fresh.db"))
    monkeypatch.setattr(settings, "AUTO_GENERATE_CERT", True)
    monkeypatch.setattr(settings, "HTTPS_ENABLED", True)
    monkeypatch.setattr(main_module, "_startup_done", False)

    async with main_module.lifespan(app):
        pass

    assert calls == []


@pytest.mark.anyio
async def test_webhook_type_filter_and_detected_type():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Post a transaction SMS
        await client.post(
            "/webhook/sms",
            json={
                "event_id": "api-type-txn-1",
                "sender": "AX-HDFCBK-S",
                "message": "Spent Rs.384 On HDFC Bank Card 9012 At Swiggy On 2026-11-20:22:18:36.",
            },
        )
        # 2. Post an OTP SMS
        await client.post(
            "/webhook/sms",
            json={
                "event_id": "api-type-otp-1",
                "sender": "JM-HDFCBK-S",
                "message": "OTP is 672330 for txn of INR 384.00 at SWIGGY INST on HDFC Bank card ending 9012.",
            },
        )
        # 3. Post a general/other SMS
        await client.post(
            "/webhook/sms",
            json={
                "event_id": "api-type-other-1",
                "sender": "Mom",
                "message": "Call me later tonight",
            },
        )

        # Fetch all webhooks
        res = await client.get("/api/webhooks")
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 3

        types_map = {item["event_id"]: item.get("detected_type") for item in items}
        assert types_map["api-type-txn-1"] == "Transaction"
        assert types_map["api-type-otp-1"] == "OTP"
        assert types_map["api-type-other-1"] == "Others"

        # Filter by type=OTP
        res_otp = await client.get("/api/webhooks?type=OTP")
        assert res_otp.status_code == 200
        otp_items = res_otp.json()
        assert len(otp_items) == 1
        assert otp_items[0]["event_id"] == "api-type-otp-1"
        assert otp_items[0]["detected_type"] == "OTP"

        # Filter by type=Transaction
        res_txn = await client.get("/api/webhooks?type=Transaction")
        assert res_txn.status_code == 200
        txn_items = res_txn.json()
        assert len(txn_items) == 1
        assert txn_items[0]["event_id"] == "api-type-txn-1"

        # Filter by type=Others
        res_other = await client.get("/api/webhooks?type=Others")
        assert res_other.status_code == 200
        other_items = res_other.json()
        assert len(other_items) == 1
        assert other_items[0]["event_id"] == "api-type-other-1"

        # Single webhook endpoint also has detected_type
        res_single = await client.get(f"/api/webhooks/{otp_items[0]['id']}")
        assert res_single.status_code == 200
        assert res_single.json()["detected_type"] == "OTP"


@pytest.mark.anyio
async def test_webhooks_backfill_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.delete("/api/webhooks")

        await client.post(
            "/webhook/sms",
            json={
                "event_id": "bf-otp-1",
                "sender": "TX-GOOGLE",
                "message": "Your verification code is 889900.",
            },
        )
        await client.post(
            "/webhook/message487",
            json={
                "event_id": "bf-txn-1",
                "sender": "BP-HDFCBK-S",
                "text": "Spent Rs.641 On HDFC Bank Card 2222 At Swiggy Instamart On 2026-11-16:14:54:18",
            },
        )

        res = await client.post("/api/webhooks/backfill")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["total_retagged"] == 2
        assert data["by_type"].get("OTP") == 1
        assert data["by_type"].get("Transaction") == 1


@pytest.mark.anyio
async def test_webhook_read_toggle_and_filter():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.delete("/api/webhooks")

        # Ingest two messages
        res1 = await client.post(
            "/webhook/sms",
            json={
                "event_id": "read-evt-1",
                "sender": "HDFCBK",
                "message": "OTP is 1122 for your txn.",
            },
        )
        assert res1.status_code == 200

        res2 = await client.post(
            "/webhook/sms",
            json={
                "event_id": "read-evt-2",
                "sender": "AXISBK",
                "message": "Rs. 250 spent at Tea Stall.",
            },
        )
        assert res2.status_code == 200

        # Retrieve both
        res_all = await client.get("/api/webhooks")
        assert res_all.status_code == 200
        items = res_all.json()
        assert len(items) == 2
        id_map = {item["event_id"]: item["id"] for item in items}
        wh1_id = id_map["read-evt-1"]
        wh2_id = id_map["read-evt-2"]

        # Default state: unread (is_read = 0)
        assert all(item["is_read"] == 0 for item in items)

        # Filter unread only -> both returned
        res_unread = await client.get("/api/webhooks?read=false")
        assert res_unread.status_code == 200
        assert len(res_unread.json()) == 2

        # Filter read only -> 0 returned
        res_read_only = await client.get("/api/webhooks?read=true")
        assert res_read_only.status_code == 200
        assert len(res_read_only.json()) == 0

        # Mark wh1 as read
        res_mark = await client.put(f"/api/webhooks/{wh1_id}/read", json={"read": True})
        assert res_mark.status_code == 200
        assert res_mark.json() == {"id": wh1_id, "read": True}

        # Inspect single webhook
        res_wh1 = await client.get(f"/api/webhooks/{wh1_id}")
        assert res_wh1.status_code == 200
        wh1_data = res_wh1.json()
        assert wh1_data["is_read"] == 1
        assert wh1_data["read_at"] is not None

        # Filter read=true -> only wh1 returned
        res_read_after = await client.get("/api/webhooks?read=true")
        assert res_read_after.status_code == 200
        read_items = res_read_after.json()
        assert len(read_items) == 1
        assert read_items[0]["id"] == wh1_id

        # Filter read=false -> only wh2 returned
        res_unread_after = await client.get("/api/webhooks?read=false")
        assert res_unread_after.status_code == 200
        unread_items = res_unread_after.json()
        assert len(unread_items) == 1
        assert unread_items[0]["id"] == wh2_id

        # Mark wh1 back to unread
        res_unmark = await client.put(f"/api/webhooks/{wh1_id}/read", json={"read": False})
        assert res_unmark.status_code == 200
        assert res_unmark.json() == {"id": wh1_id, "read": False}

        res_wh1_unmarked = await client.get(f"/api/webhooks/{wh1_id}")
        assert res_wh1_unmarked.status_code == 200
        assert res_wh1_unmarked.json()["is_read"] == 0
        assert res_wh1_unmarked.json()["read_at"] is None

        # Test bidirectional sync: marking webhook read/unread updates linked transaction
        res_txn_msg = await client.post(
            "/webhook/message487",
            json={**ICICI_SPEND, "event_id": "wh-sync-txn-1", "sender": "BP-ICICIT-S"},
        )
        assert res_txn_msg.status_code == 200
        wh_item = (await client.get("/api/webhooks/event/wh-sync-txn-1")).json()
        txns = (await client.get("/api/transactions")).json()
        matching_txn = next(t for t in txns if t["event_id"] == "wh-sync-txn-1")
        assert wh_item["is_read"] == 0
        assert matching_txn["is_read"] == 0

        # Mark webhook read -> matching transaction also becomes read
        res_sync_mark = await client.put(f"/api/webhooks/{wh_item['id']}/read", json={"read": True})
        assert res_sync_mark.status_code == 200
        assert (await client.get(f"/api/webhooks/{wh_item['id']}")).json()["is_read"] == 1
        updated_txns = (await client.get("/api/transactions")).json()
        assert next(t for t in updated_txns if t["event_id"] == "wh-sync-txn-1")["is_read"] == 1

        # Mark webhook unread -> matching transaction also becomes unread
        res_sync_unmark = await client.put(f"/api/webhooks/{wh_item['id']}/read", json={"read": False})
        assert res_sync_unmark.status_code == 200
        assert (await client.get(f"/api/webhooks/{wh_item['id']}")).json()["is_read"] == 0
        updated_txns2 = (await client.get("/api/transactions")).json()
        assert next(t for t in updated_txns2 if t["event_id"] == "wh-sync-txn-1")["is_read"] == 0

        # 404 on nonexistent webhook
        res_404 = await client.put("/api/webhooks/9999999/read", json={"read": True})
        assert res_404.status_code == 404


@pytest.mark.anyio
async def test_list_webhooks_negative_limit_is_clamped():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Create multiple webhooks
        for i in range(5):
            await client.post(
                "/webhook/message487",
                json={
                    "event_id": f"evt-clamp-{i}",
                    "message": f"Hello {i}",
                    "sender": "+1234567890",
                    "timestamp": 1718000000000 + i,
                },
            )
        # Passing negative limit must clamp to 1 instead of returning all rows
        res = await client.get("/api/webhooks?limit=-1")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1


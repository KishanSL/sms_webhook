"""Export / import / reset tests, plus the Settings route rename."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import (
    claim_transaction_for_posting,
    clear_all_data,
    export_data,
    get_card_statements,
    get_transactions,
    get_webhooks,
    import_data,
    init_db,
    record_confirmed_rule,
    save_card_statement,
    save_transaction,
    save_webhook,
    set_account_name,
    set_bank,
    set_gnucash_account_map,
)
from app.database.migrations import LATEST_VERSION
from app.main import app


async def _seed(db_path: str) -> None:
    await save_webhook(
        db_path,
        event_id="evt-1",
        source="message487",
        method="POST",
        path="/webhook/message487",
        url="https://x/webhook/message487",
        headers={"content-type": "application/json"},
        query_params=None,
        content_type="application/json",
        payload_raw='{"a":1}',
        payload_json={"a": 1},
        sender="AD-HDFCBK-S",
        recipient=None,
        message_body="Spent Rs.641 On HDFC Bank Card 2222 At Swiggy Instamart",
        external_message_id="evt-1",
    )
    await save_transaction(
        db_path,
        event_id="evt-1",
        webhook_id=1,
        occurred_at="2026-11-16T09:24:22+00:00",
        raw_text="Spent Rs.641 ...",
        bank="HDFC Bank",
        sender_header="HDFCBK",
        direction="debit",
        amount=641.0,
        account_last4="2222",
        counterparty="Swiggy Instamart",
    )
    await set_bank(db_path, "HDFCBK", "HDFC Bank")
    await set_account_name(db_path, "HDFCBK", "2222", "HDFC Swiggy")
    await set_gnucash_account_map(db_path, "HDFCBK", "2222", "acc-1")
    await record_confirmed_rule(db_path, "SWIGGY INSTAMART", "HDFCBK:2222", "acc-2")


@pytest.fixture(autouse=True)
async def setup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "export.db")
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "GNUCASH_URL", None)
    monkeypatch.setattr(settings, "GNUCASH_API_TOKEN", None)
    await init_db(db_path)
    yield db_path


@pytest.mark.anyio
async def test_export_import_replace_round_trip(setup, tmp_path):
    await _seed(setup)
    exported = await export_data(setup)
    assert len(exported["webhooks"]) == 1
    assert len(exported["transactions"]) == 1
    assert len(exported["gnucash_account_map"]) == 1
    assert any(r["merchant_key"] == "SWIGGY INSTAMART" for r in exported["category_rules"])

    fresh = str(tmp_path / "fresh.db")
    await init_db(fresh)
    counts = await import_data(fresh, exported, mode="replace")

    assert counts["webhooks"] == 1
    assert counts["transactions"] == 1
    restored = await export_data(fresh)
    assert [r["event_id"] for r in restored["webhooks"]] == ["evt-1"]
    assert [r["event_id"] for r in restored["transactions"]] == ["evt-1"]
    assert restored["gnucash_account_map"][0]["gnucash_account_id"] == "acc-1"
    assert {r["name"] for r in restored["account_names"]} == {"HDFC Swiggy"}


@pytest.mark.anyio
async def test_merge_skips_duplicates(setup):
    await _seed(setup)
    exported = await export_data(setup)
    counts = await import_data(setup, exported, mode="merge")
    assert all(n == 0 for n in counts.values()), counts


@pytest.mark.anyio
async def test_clear_all_data_wipes_and_reseeds_banks(setup):
    await _seed(setup)
    deleted = await clear_all_data(setup)
    assert deleted["webhooks"] >= 1
    assert deleted["transactions"] >= 1

    after = await export_data(setup)
    assert after["webhooks"] == []
    assert after["transactions"] == []
    assert after["gnucash_account_map"] == []
    assert after["category_rules"] == []
    # A fresh database seeds the default bank codes.
    assert {r["sender_header"] for r in after["banks"]} >= {"HDFCBK", "ICICIT"}


@pytest.mark.anyio
async def test_export_endpoint(setup):
    await _seed(setup)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/export")
    assert res.status_code == 200
    assert "attachment" in res.headers.get("content-disposition", "")
    body = res.json()
    assert body["schema_version"] == LATEST_VERSION
    assert len(body["tables"]["webhooks"]) == 1


@pytest.mark.anyio
async def test_import_endpoint_round_trip(setup, tmp_path):
    await _seed(setup)
    exported = (await export_data(setup))
    envelope = {"schema_version": 1, "tables": exported}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # merge into the same db -> nothing new
        res = await client.post("/api/import?mode=merge", json=envelope)
        assert res.status_code == 200
        assert all(n == 0 for n in res.json()["counts"].values())

        bad = await client.post("/api/import?mode=nope", json=envelope)
        assert bad.status_code == 422
        notjson = await client.post("/api/import", content="not json", headers={"Content-Type": "application/json"})
        assert notjson.status_code == 400


@pytest.mark.anyio
async def test_reset_endpoint(setup):
    await _seed(setup)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.delete("/api/data")
        assert res.status_code == 200
        assert res.json()["status"] == "cleared"
        assert (await export_data(setup))["webhooks"] == []


@pytest.mark.anyio
async def test_settings_route_and_banks_redirect(setup):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        page = await client.get("/settings")
        assert page.status_code == 200
        assert "Settings" in page.text
        assert "Export JSON" in page.text and "Import JSON" in page.text and "Clear all data" in page.text

        old = await client.get("/banks")
        assert old.status_code == 308
        assert old.headers["location"] == "/settings"


@pytest.mark.anyio
async def test_merge_keeps_backup_rows_whose_ids_clash(setup, tmp_path):
    """Merge is add-only and matches on event_id, so a colliding local id can't drop a backup row."""
    local = setup
    await save_webhook(
        local, event_id="local-1", source="sms", method="POST", path="/x", url="u",
        headers={}, query_params=None, content_type="application/json", payload_raw="{}",
        payload_json={}, sender="+1", recipient=None, message_body="local", external_message_id="local-1",
    )
    await save_transaction(
        local, event_id="local-1", webhook_id=1, occurred_at="2026-11-18T00:00:00+00:00",
        raw_text="local", bank="HDFC Bank", sender_header="HDFCBK", direction="debit",
        amount=100.0, account_last4="1111", counterparty="X",
    )

    backup = str(tmp_path / "backup.db")
    await init_db(backup)
    await save_webhook(
        backup, event_id="backup-1", source="sms", method="POST", path="/x", url="u",
        headers={}, query_params=None, content_type="application/json", payload_raw="{}",
        payload_json={}, sender="+2", recipient=None, message_body="backup", external_message_id="backup-1",
    )
    await save_transaction(
        backup, event_id="backup-1", webhook_id=1, occurred_at="2026-11-18T00:00:00+00:00",
        raw_text="backup", bank="HDFC Bank", sender_header="HDFCBK", direction="debit",
        amount=200.0, account_last4="1111", counterparty="Y",
    )

    counts = await import_data(local, await export_data(backup), mode="merge")
    assert counts["webhooks"] == 1
    assert counts["transactions"] == 1

    webhooks = {r["event_id"]: r["id"] for r in await get_webhooks(local, limit=50)}
    assert set(webhooks) == {"local-1", "backup-1"}

    txns = {t["event_id"]: t for t in await get_transactions(local)}
    # The backup transaction points at the local backup webhook, not the clashing id 1.
    assert txns["backup-1"]["webhook_id"] == webhooks["backup-1"]
    assert txns["backup-1"]["webhook_id"] != webhooks["local-1"]
    assert txns["local-1"]["webhook_id"] == webhooks["local-1"]


@pytest.mark.anyio
async def test_import_does_not_restore_a_stuck_posting_claim(setup, tmp_path):
    """A backup taken mid-post must not import a row stuck in `posting`."""
    await _seed(setup)
    assert await claim_transaction_for_posting(setup, 1)  # transaction id 1 -> posting

    payload = await export_data(setup)
    assert payload["transactions"][0]["post_status"] == "posting"

    fresh = str(tmp_path / "fresh.db")
    await init_db(fresh)
    await import_data(fresh, payload, mode="replace")

    txn = (await get_transactions(fresh))[0]
    assert txn["post_status"] == "failed"
    assert txn["post_error"] and "Interrupted" in txn["post_error"]


@pytest.mark.anyio
async def test_merge_relinks_statement_webhook_id(setup, tmp_path):
    """A merged statement's webhook_id points at the local webhook with the same event_id."""
    local = setup
    await save_webhook(
        local, event_id="local-1", source="sms", method="POST", path="/x", url="u",
        headers={}, query_params=None, content_type="application/json", payload_raw="{}",
        payload_json={}, sender="+1", recipient=None, message_body="local", external_message_id="local-1",
    )

    backup = str(tmp_path / "backup2.db")
    await init_db(backup)
    await save_webhook(
        backup, event_id="backup-1", source="sms", method="POST", path="/x", url="u",
        headers={}, query_params=None, content_type="application/json", payload_raw="{}",
        payload_json={}, sender="VM-MYSBIC-S", recipient=None, message_body="stmt", external_message_id="backup-1",
    )
    await save_card_statement(
        backup, event_id="backup-1", webhook_id=1, bank="SBI Card", sender_header="MYSBIC",
        account_last4="24", statement_date="2026-11-16", total_due=18450.0, min_due=369.0,
        due_date="2026-12-06", raw_text="stmt",
    )

    await import_data(local, await export_data(backup), mode="merge")

    webhooks = {r["event_id"]: r["id"] for r in await get_webhooks(local, limit=50)}
    statements = await get_card_statements(local)
    assert len(statements) == 1
    assert statements[0]["webhook_id"] == webhooks["backup-1"]
    assert statements[0]["webhook_id"] != webhooks["local-1"]

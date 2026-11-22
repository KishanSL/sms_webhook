"""Database unit tests."""

import asyncio

import pytest

from app.database import (
    delete_webhook,
    get_stats,
    get_webhook_by_event_id,
    get_webhooks,
    init_db,
    save_webhook,
)


async def _save(db_file, event_id):
    return await save_webhook(
        db_path=db_file,
        event_id=event_id,
        source="message487",
        method="POST",
        path="/webhook/message487",
        url="https://localhost:8443/webhook/message487",
        headers={"content-type": "application/json"},
        query_params={},
        content_type="application/json",
        payload_raw="{}",
        payload_json={},
        sender="+1234567890",
        recipient=None,
        message_body="hi",
        external_message_id=event_id,
    )


@pytest.mark.anyio
async def test_concurrent_duplicate_webhooks_are_not_errors(tmp_path):
    """Five identical events arriving at once must not raise; exactly one is inserted."""
    db_file = str(tmp_path / "concurrent.db")
    await init_db(db_file)

    results = await asyncio.gather(*[_save(db_file, "race-1") for _ in range(5)])

    inserted = [record for record, duplicate in results if not duplicate]
    duplicates = [record for record, duplicate in results if duplicate]
    assert len(inserted) == 1
    assert len(duplicates) == 4
    assert len({record["id"] for record, _duplicate in results}) == 1  # all point at the same row


@pytest.mark.anyio
async def test_database_lifecycle(tmp_path):
    db_file = str(tmp_path / "test_webhooks.db")
    await init_db(db_file)

    # 1. Insert record
    record, is_duplicate = await save_webhook(
        db_path=db_file,
        event_id="test-evt-1",
        source="message487",
        method="POST",
        path="/webhook/message487",
        url="https://localhost:8443/webhook/message487",
        headers={"content-type": "application/json"},
        query_params={},
        content_type="application/json",
        payload_raw='{"message": "Hello Homelab"}',
        payload_json={"message": "Hello Homelab"},
        sender="+1234567890",
        recipient="+1987654321",
        message_body="Hello Homelab",
        external_message_id="test-evt-1",
        device_code="homelab_phone",
        provider="message487",
        client_ip="192.168.1.100",
    )

    assert is_duplicate is False
    assert record["event_id"] == "test-evt-1"
    assert record["sender"] == "+1234567890"

    # 2. Test duplicate event_id handling (idempotency)
    dup_record, is_duplicate2 = await save_webhook(
        db_path=db_file,
        event_id="test-evt-1",
        source="message487",
        method="POST",
        path="/webhook/message487",
        url="https://localhost:8443/webhook/message487",
        headers={"content-type": "application/json"},
        query_params={},
        content_type="application/json",
        payload_raw='{"message": "Hello Homelab"}',
        payload_json={"message": "Hello Homelab"},
        sender="+1234567890",
        recipient="+1987654321",
        message_body="Hello Homelab",
        external_message_id="test-evt-1",
        device_code="homelab_phone",
        provider="message487",
        client_ip="192.168.1.100",
    )
    assert is_duplicate2 is True
    assert dup_record["id"] == record["id"]

    # 3. Query record
    by_event = await get_webhook_by_event_id(db_file, "test-evt-1")
    assert by_event is not None
    assert by_event["id"] == record["id"]

    # 4. Search
    search_results = await get_webhooks(db_file, search="Homelab")
    assert len(search_results) == 1

    # 5. Stats
    stats = await get_stats(db_file)
    assert stats["total_count"] == 1
    assert "message487" in stats["sources"]

    # 6. Delete
    deleted = await delete_webhook(db_file, record["id"])
    assert deleted is True

    empty_stats = await get_stats(db_file)
    assert empty_stats["total_count"] == 0


@pytest.mark.anyio
async def test_delete_and_clear_webhooks_unlink_references(tmp_path):
    from app.database import (
        clear_all_webhooks,
        get_transactions,
        save_card_statement,
        save_transaction,
    )

    db_file = str(tmp_path / "unlink_test.db")
    await init_db(db_file)

    wh1, _ = await _save(db_file, "evt-unlink-1")
    wh2, _ = await _save(db_file, "evt-unlink-2")

    await save_transaction(
        db_file,
        event_id="evt-unlink-1",
        webhook_id=wh1["id"],
        occurred_at="2026-11-20T10:00:00",
        raw_text="Txn 1",
        bank="HDFC",
        sender_header="HDFCBK",
        direction="debit",
        amount=100.0,
    )
    await save_card_statement(
        db_file,
        event_id="evt-unlink-1",
        webhook_id=wh1["id"],
        bank="HDFC",
        sender_header="HDFCBK",
        account_last4="1111",
        statement_date="2026-11-20",
        total_due=100.0,
        min_due=10.0,
        due_date="2026-12-10",
        raw_text="Stmt 1",
    )

    await save_transaction(
        db_file,
        event_id="evt-unlink-2",
        webhook_id=wh2["id"],
        occurred_at="2026-11-20T11:00:00",
        raw_text="Txn 2",
        bank="ICICI",
        sender_header="ICICIT",
        direction="debit",
        amount=200.0,
    )

    # Deleting wh1 unlinks transaction and statement for wh1, but leaves wh2 intact
    assert await delete_webhook(db_file, wh1["id"]) is True
    txns = await get_transactions(db_file)
    t1 = next(t for t in txns if t["event_id"] == "evt-unlink-1")
    t2 = next(t for t in txns if t["event_id"] == "evt-unlink-2")
    assert t1["webhook_id"] is None
    assert t2["webhook_id"] == wh2["id"]

    # Clearing all webhooks unlinks remaining references
    deleted_count = await clear_all_webhooks(db_file)
    assert deleted_count == 1
    txns_after = await get_transactions(db_file)
    t2_after = next(t for t in txns_after if t["event_id"] == "evt-unlink-2")
    assert t2_after["webhook_id"] is None

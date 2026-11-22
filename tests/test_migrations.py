"""Numbered schema migrations."""

import aiosqlite
import pytest

from app.database import init_db
from app.database.migrations import _V1_SQL, LATEST_VERSION


@pytest.mark.anyio
async def test_fresh_database_runs_every_migration(tmp_path):
    db = str(tmp_path / "fresh.db")
    await init_db(db)
    async with aiosqlite.connect(db) as conn:
        rows = await (await conn.execute("SELECT version FROM schema_migrations ORDER BY version")).fetchall()
        assert [r[0] for r in rows] == list(range(1, LATEST_VERSION + 1))
        tx_cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(transactions)")).fetchall()}
        assert {"is_read", "merchant_key", "gnucash_tx_id", "post_status"} <= tx_cols
        # Per-card limits (total limit / available / utilisation) were removed in v8.
        assert (await (await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'card_limits'"
        )).fetchone()) is None


@pytest.mark.anyio
async def test_v8_drops_card_limits_from_an_existing_database(tmp_path):
    db = str(tmp_path / "v7.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL
            );
            CREATE TABLE card_limits (
                sender_header TEXT NOT NULL,
                account_last4 TEXT,
                total_limit REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (sender_header, account_last4)
            );
            INSERT INTO card_limits VALUES ('MYSBIC', '24', 300000, 't');
            """
        )
        await conn.executemany(
            "INSERT INTO schema_migrations VALUES (?, 'x', 't')",
            [(version,) for version in range(1, 8)],
        )
        await conn.commit()

    await init_db(db)

    async with aiosqlite.connect(db) as conn:
        leftover = await (await conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'card_limits'"
        )).fetchone()
        versions = [r[0] for r in await (await conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )).fetchall()]
    assert leftover is None
    assert versions[-1] == LATEST_VERSION


@pytest.mark.anyio
async def test_legacy_v1_database_is_upgraded(tmp_path):
    db = str(tmp_path / "legacy.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(_V1_SQL)
        await conn.execute(
            "INSERT INTO webhooks (event_id, received_at, source, method, path, url, headers) "
            "VALUES ('e1', 't', 'sms', 'POST', '/x', 'u', '{}')"
        )
        await conn.execute(
            "INSERT INTO transactions "
            "(event_id, occurred_at, bank, sender_header, direction, amount, raw_text, created_at, counterparty) "
            "VALUES ('e1', 't', 'HDFC Bank', 'HDFCBK', 'debit', 10, 'raw', 'c', 'AMAZON PAY IN G')"
        )
        await conn.commit()

    await init_db(db)

    async with aiosqlite.connect(db) as conn:
        wh_cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(webhooks)")).fetchall()}
        tx_cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(transactions)")).fetchall()}
        versions = [r[0] for r in await (await conn.execute("SELECT version FROM schema_migrations ORDER BY version")).fetchall()]
        merchant = (await (await conn.execute("SELECT merchant_key FROM transactions WHERE event_id = 'e1'")).fetchone())[0]

    assert {"device_id", "message_type", "occurred_at", "channel_id", "source_name"} <= wh_cols
    assert {"is_read", "read_at", "merchant_key", "post_status", "gnucash_tx_id"} <= tx_cols
    assert versions == list(range(1, LATEST_VERSION + 1))  # baseline detected as 1, then 2..N
    assert merchant == "AMAZON PAY"  # migration 5 backfilled it


@pytest.mark.anyio
async def test_v9_seeds_default_banks_and_backfills_hdfc(tmp_path):
    db = str(tmp_path / "v8.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(_V1_SQL)
        await conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)")
        await conn.executemany("INSERT INTO schema_migrations VALUES (?, 'x', 't')", [(v,) for v in range(1, 9)])
        await conn.execute(
            """
            CREATE TABLE card_statements (
                id INTEGER PRIMARY KEY, event_id TEXT, webhook_id INTEGER, bank TEXT,
                sender_header TEXT, account_last4 TEXT, statement_date TEXT,
                total_due REAL, min_due REAL, due_date TEXT, paid_at TEXT, raw_text TEXT, created_at TEXT
            )
            """
        )
        await conn.execute(
            "INSERT INTO card_statements (id, event_id, sender_header, statement_date) VALUES (1, 'e_hdfc', 'HDFCBK', '2026-11-18')"
        )
        await conn.commit()

    await init_db(db)

    async with aiosqlite.connect(db) as conn:
        banks = {r[0] for r in await (await conn.execute("SELECT sender_header FROM banks")).fetchall()}
        assert {"BOBONE", "BOBCRD"} <= banks
        row = await (await conn.execute("SELECT due_date FROM card_statements WHERE id = 1")).fetchone()
        assert row[0] == "2026-12-08"


@pytest.mark.anyio
async def test_v10_adds_detected_type_and_backfills(tmp_path):
    db = str(tmp_path / "v9.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(_V1_SQL)
        await conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)")
        await conn.executemany("INSERT INTO schema_migrations VALUES (?, 'x', 't')", [(v,) for v in range(1, 10)])
        await conn.execute(
            """
            INSERT INTO webhooks (
                event_id, received_at, source, method, path, url, headers, sender, message_body
            ) VALUES (
                'e_otp', '2026-11-20T10:00:00Z', 'sms', 'POST', '/webhook', 'http://localhost/webhook', '{}',
                'TX-GOOGLE', 'Your Google verification code is 123456. Do not share.'
            )
            """
        )
        await conn.commit()

    await init_db(db)

    async with aiosqlite.connect(db) as conn:
        row = await (await conn.execute("SELECT detected_type FROM webhooks WHERE event_id = 'e_otp'")).fetchone()
        assert row is not None
        assert row[0] == "OTP"


@pytest.mark.anyio
async def test_v11_adds_is_read_and_syncs_transaction_read(tmp_path):
    db = str(tmp_path / "v10.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(_V1_SQL)
        await conn.execute("ALTER TABLE transactions ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0")
        await conn.execute("ALTER TABLE transactions ADD COLUMN read_at TEXT")
        await conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT)")
        await conn.executemany("INSERT INTO schema_migrations VALUES (?, 'x', 't')", [(v,) for v in range(1, 11)])
        await conn.execute(
            """
            INSERT INTO webhooks (
                id, event_id, received_at, source, method, path, url, headers, sender, message_body
            ) VALUES (
                1, 'e_tx_read', '2026-11-20T10:00:00Z', 'sms', 'POST', '/webhook', 'http://localhost/webhook', '{}',
                'HDFCBK', 'Spent Rs 100'
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO webhooks (
                id, event_id, received_at, source, method, path, url, headers, sender, message_body
            ) VALUES (
                2, 'e_tx_unread', '2026-11-20T10:05:00Z', 'sms', 'POST', '/webhook', 'http://localhost/webhook', '{}',
                'HDFCBK', 'Spent Rs 200'
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO transactions (
                id, event_id, occurred_at, bank, sender_header, direction, amount, raw_text, created_at, is_read, read_at
            ) VALUES (
                1, 'e_tx_read', '2026-11-20T10:00:00Z', 'HDFC Bank', 'HDFCBK', 'debit', 100.0, 'Spent Rs 100', '2026-11-20T10:00:00Z', 1, '2026-11-20T11:00:00Z'
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO transactions (
                id, event_id, occurred_at, bank, sender_header, direction, amount, raw_text, created_at, is_read, read_at
            ) VALUES (
                2, 'e_tx_unread', '2026-11-20T10:05:00Z', 'HDFC Bank', 'HDFCBK', 'debit', 200.0, 'Spent Rs 200', '2026-11-20T10:05:00Z', 0, NULL
            )
            """
        )
        await conn.commit()

    await init_db(db)

    async with aiosqlite.connect(db) as conn:
        wh_cols = {r[1] for r in await (await conn.execute("PRAGMA table_info(webhooks)")).fetchall()}
        assert "is_read" in wh_cols
        assert "read_at" in wh_cols

        r1 = await (await conn.execute("SELECT is_read, read_at FROM webhooks WHERE event_id = 'e_tx_read'")).fetchone()
        assert r1[0] == 1
        assert r1[1] == "2026-11-20T11:00:00Z"

        r2 = await (await conn.execute("SELECT is_read, read_at FROM webhooks WHERE event_id = 'e_tx_unread'")).fetchone()
        assert r2[0] == 0
        assert r2[1] is None


@pytest.mark.anyio
async def test_init_db_is_idempotent(tmp_path):
    db = str(tmp_path / "twice.db")
    await init_db(db)
    await init_db(db)  # must not re-run migrations or raise
    async with aiosqlite.connect(db) as conn:
        count = (await (await conn.execute("SELECT COUNT(*) FROM schema_migrations")).fetchone())[0]
    assert count == LATEST_VERSION

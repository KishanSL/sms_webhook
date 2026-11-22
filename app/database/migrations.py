"""
Numbered schema migrations.

Each migration runs once, in order, and is recorded in `schema_migrations`. A database that
predates this system is stamped with a baseline version detected from its columns, so only the
migrations it still needs are applied. This replaces the previous ad-hoc per-column checks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.gnucash import normalise_merchant
from app.txn_parser import DEFAULT_BANKS

Migration = Callable[[aiosqlite.Connection], Awaitable[None]]


async def _exec_script(conn: aiosqlite.Connection, script: str) -> None:
    """Run a multi-statement DDL script without `executescript` (which commits first)."""
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            await conn.execute(statement)


# --- v1: the original schema ---------------------------------------------------------------

_V1_SQL = """
CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL,
    source TEXT NOT NULL,
    provider TEXT,
    device_code TEXT,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    url TEXT NOT NULL,
    headers TEXT NOT NULL,
    query_params TEXT,
    content_type TEXT,
    sender TEXT,
    recipient TEXT,
    message_body TEXT,
    external_message_id TEXT,
    payload_raw TEXT,
    payload_json TEXT,
    client_ip TEXT,
    response_status INTEGER NOT NULL DEFAULT 200
);

CREATE INDEX IF NOT EXISTS idx_webhooks_event_id ON webhooks(event_id);
CREATE INDEX IF NOT EXISTS idx_webhooks_received_at ON webhooks(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhooks_source ON webhooks(source);
CREATE INDEX IF NOT EXISTS idx_webhooks_sender ON webhooks(sender);
CREATE INDEX IF NOT EXISTS idx_webhooks_ext_id ON webhooks(external_message_id);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    webhook_id INTEGER,
    occurred_at TEXT NOT NULL,
    bank TEXT NOT NULL,
    sender_header TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    instrument TEXT,
    channel TEXT,
    account_last4 TEXT,
    counterparty TEXT,
    reference TEXT,
    balance REAL,
    balance_type TEXT,
    raw_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_txn_occurred_at ON transactions(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_txn_bank ON transactions(bank);
CREATE INDEX IF NOT EXISTS idx_txn_account ON transactions(account_last4);

CREATE TABLE IF NOT EXISTS banks (
    sender_header TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_names (
    sender_header TEXT NOT NULL,
    account_last4 TEXT NOT NULL,
    name TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (sender_header, account_last4)
);
"""


async def _migration_1(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V1_SQL)
    now = datetime.now(UTC).isoformat()
    await conn.executemany(
        "INSERT OR IGNORE INTO banks (sender_header, name, updated_at) VALUES (?, ?, ?)",
        [(header, name, now) for header, name in DEFAULT_BANKS.items()],
    )


# --- v2: extra Message487 / SMS fields on webhooks -----------------------------------------

_V2_SQL = """
ALTER TABLE webhooks ADD COLUMN device_id TEXT;
ALTER TABLE webhooks ADD COLUMN message_type TEXT;
ALTER TABLE webhooks ADD COLUMN occurred_at TEXT;
ALTER TABLE webhooks ADD COLUMN channel_id TEXT;
ALTER TABLE webhooks ADD COLUMN source_name TEXT;
CREATE INDEX IF NOT EXISTS idx_webhooks_device_code ON webhooks(device_code);
CREATE INDEX IF NOT EXISTS idx_webhooks_msg_type ON webhooks(message_type);
"""


async def _migration_2(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V2_SQL)


# --- v3: read/unread on transactions -------------------------------------------------------

_V3_SQL = """
ALTER TABLE transactions ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0;
ALTER TABLE transactions ADD COLUMN read_at TEXT;
"""


async def _migration_3(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V3_SQL)


# --- v4: gnucash-web mapping and posting ---------------------------------------------------

_V4_SQL = """
CREATE TABLE IF NOT EXISTS gnucash_account_map (
    sender_header TEXT NOT NULL,
    account_last4 TEXT NOT NULL,
    gnucash_account_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (sender_header, account_last4)
);

CREATE TABLE IF NOT EXISTS category_rules (
    merchant_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'confirmed',
    gnucash_account_id TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    PRIMARY KEY (merchant_key, scope, source, gnucash_account_id)
);

ALTER TABLE transactions ADD COLUMN merchant_key TEXT;
ALTER TABLE transactions ADD COLUMN from_account_id TEXT;
ALTER TABLE transactions ADD COLUMN to_account_id TEXT;
ALTER TABLE transactions ADD COLUMN suggestion_reason TEXT;
ALTER TABLE transactions ADD COLUMN post_status TEXT NOT NULL DEFAULT 'new';
ALTER TABLE transactions ADD COLUMN gnucash_tx_id TEXT;
ALTER TABLE transactions ADD COLUMN post_error TEXT;
ALTER TABLE transactions ADD COLUMN posted_at TEXT;
"""


async def _migration_4(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V4_SQL)


# --- v5: backfill merchant_key for rows that predate the gnucash integration ----------------

async def _migration_5(conn: aiosqlite.Connection) -> None:
    async with conn.execute(
        "SELECT id, counterparty FROM transactions WHERE merchant_key IS NULL OR merchant_key = ''"
    ) as cursor:
        stale = await cursor.fetchall()
    if stale:
        await conn.executemany(
            "UPDATE transactions SET merchant_key = ? WHERE id = ?",
            [(normalise_merchant(row[1]), row[0]) for row in stale],
        )


# --- v6: credit-card statements and per-card limits ----------------------------------------

_V6_SQL = """
CREATE TABLE IF NOT EXISTS card_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    webhook_id INTEGER,
    bank TEXT,
    sender_header TEXT NOT NULL,
    account_last4 TEXT,
    statement_date TEXT,
    total_due REAL,
    min_due REAL,
    due_date TEXT,
    paid_at TEXT,
    raw_text TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_card_statements_card ON card_statements(sender_header, account_last4);

CREATE TABLE IF NOT EXISTS card_limits (
    sender_header TEXT NOT NULL,
    account_last4 TEXT NOT NULL,
    total_limit REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (sender_header, account_last4)
);
"""


async def _migration_6(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V6_SQL)


# --- v7: allow a NULL card number in card_limits (statements with no card ending) -----------

_V7_SQL = """
CREATE TABLE card_limits_new (
    sender_header TEXT NOT NULL,
    account_last4 TEXT,
    total_limit REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (sender_header, account_last4)
);
INSERT INTO card_limits_new (sender_header, account_last4, total_limit, updated_at)
SELECT sender_header, account_last4, total_limit, updated_at FROM card_limits;
DROP TABLE card_limits;
ALTER TABLE card_limits_new RENAME TO card_limits;
"""


async def _migration_7(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V7_SQL)


# --- v8: drop per-card limits (total limit / available / utilisation removed) ----------------

_V8_SQL = """
DROP TABLE IF EXISTS card_limits;
"""


async def _migration_8(conn: aiosqlite.Connection) -> None:
    await _exec_script(conn, _V8_SQL)


# --- v9: seed newly supported default banks and backfill HDFC statement due dates ---------

async def _migration_9(conn: aiosqlite.Connection) -> None:
    if await _table_exists(conn, "banks"):
        now = datetime.now(UTC).isoformat()
        await conn.executemany(
            "INSERT OR IGNORE INTO banks (sender_header, name, updated_at) VALUES (?, ?, ?)",
            [(header, name, now) for header, name in DEFAULT_BANKS.items()],
        )
    if await _table_exists(conn, "card_statements"):
        async with conn.execute(
            "SELECT id, statement_date FROM card_statements "
            "WHERE sender_header = 'HDFCBK' AND due_date IS NULL AND statement_date IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            try:
                d = datetime.fromisoformat(row["statement_date"]).date() + timedelta(days=20)
                await conn.execute("UPDATE card_statements SET due_date = ? WHERE id = ?", (d.isoformat(), row["id"]))
            except (ValueError, TypeError):
                pass


# --- v10: add detected_type column to webhooks and backfill classification ----------------

_V10_SQL = """
ALTER TABLE webhooks ADD COLUMN detected_type TEXT;
CREATE INDEX IF NOT EXISTS idx_webhooks_detected_type ON webhooks(detected_type);
"""


async def _migration_10(conn: aiosqlite.Connection) -> None:
    if not await _table_exists(conn, "webhooks"):
        return
    cols = await _columns(conn, "webhooks")
    if "detected_type" not in cols:
        await _exec_script(conn, _V10_SQL)

    from app.classifier import classify_message_text

    async with conn.execute("SELECT id, sender, message_body FROM webhooks") as cursor:
        rows = await cursor.fetchall()
    if not rows:
        return

    txn_wh_ids: set[int] = set()
    if await _table_exists(conn, "transactions"):
        async with conn.execute("SELECT webhook_id FROM transactions WHERE webhook_id IS NOT NULL") as cursor:
            txn_wh_ids = {r[0] for r in await cursor.fetchall()}

    stmt_wh_ids: set[int] = set()
    if await _table_exists(conn, "card_statements"):
        async with conn.execute("SELECT webhook_id FROM card_statements WHERE webhook_id IS NOT NULL") as cursor:
            stmt_wh_ids = {r[0] for r in await cursor.fetchall()}

    banks_map: dict[str, str] = {}
    if await _table_exists(conn, "banks"):
        async with conn.execute("SELECT sender_header, name FROM banks") as cursor:
            for r in await cursor.fetchall():
                banks_map[r[0]] = r[1]
    if not banks_map:
        banks_map = dict(DEFAULT_BANKS)

    updates = []
    for row in rows:
        wh_id = row["id"]
        sender = row["sender"]
        body = row["message_body"]
        mtype = classify_message_text(
            text=body,
            sender=sender,
            is_transaction=wh_id in txn_wh_ids,
            is_statement=wh_id in stmt_wh_ids,
            banks=banks_map,
        )
        updates.append((mtype, wh_id))

    await conn.executemany("UPDATE webhooks SET detected_type = ? WHERE id = ?", updates)


# --- v11: read/unread on webhooks ----------------------------------------------------------

_V11_SQL = """
ALTER TABLE webhooks ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0;
ALTER TABLE webhooks ADD COLUMN read_at TEXT;
CREATE INDEX IF NOT EXISTS idx_webhooks_is_read ON webhooks(is_read);
"""


async def _migration_11(conn: aiosqlite.Connection) -> None:
    if not await _table_exists(conn, "webhooks"):
        return
    cols = await _columns(conn, "webhooks")
    if "is_read" not in cols:
        await _exec_script(conn, _V11_SQL)
    # If a webhook's transaction was already marked read, mark the webhook read as well
    if await _table_exists(conn, "transactions"):
        txn_cols = await _columns(conn, "transactions")
        if "is_read" in txn_cols:
            await conn.execute(
                """
                UPDATE webhooks
                SET is_read = 1,
                    read_at = (SELECT read_at FROM transactions WHERE transactions.event_id = webhooks.event_id)
                WHERE event_id IN (SELECT event_id FROM transactions WHERE is_read = 1)
                """
            )


MIGRATIONS: list[tuple[int, str, Migration]] = [
    (1, "initial schema", _migration_1),
    (2, "webhook device/message/occurred/channel/source_name columns", _migration_2),
    (3, "transaction is_read/read_at columns", _migration_3),
    (4, "gnucash account map, category rules and transaction posting columns", _migration_4),
    (5, "backfill transaction merchant_key", _migration_5),
    (6, "credit-card statements and per-card limits", _migration_6),
    (7, "allow a NULL card number in card_limits", _migration_7),
    (8, "drop per-card limits", _migration_8),
    (9, "seed default banks and backfill HDFC statement due dates", _migration_9),
    (10, "add detected_type column to webhooks and backfill classification", _migration_10),
    (11, "webhook is_read/read_at columns", _migration_11),
]

LATEST_VERSION = max(version for version, _desc, _fn in MIGRATIONS)


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    async with conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)) as cursor:
        return await cursor.fetchone() is not None


async def _columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cursor:
        return {row[1] for row in await cursor.fetchall()}


async def _detect_baseline(conn: aiosqlite.Connection) -> int:
    """The version a database already at, inferred once for databases created before migrations."""
    if not await _table_exists(conn, "webhooks"):
        return 0
    txn_columns = await _columns(conn, "transactions")
    if await _table_exists(conn, "category_rules") or "gnucash_tx_id" in txn_columns:
        return 4
    if "is_read" in txn_columns:
        return 3
    if "device_id" in await _columns(conn, "webhooks"):
        return 2
    return 1


async def run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply every migration newer than the recorded (or detected) version, atomically."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    async with conn.execute("SELECT MAX(version) FROM schema_migrations") as cursor:
        row = await cursor.fetchone()
    current = row[0] if row and row[0] is not None else None

    if current is None:
        current = await _detect_baseline(conn)
        if current:
            await conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (current, "baseline (detected)", datetime.now(UTC).isoformat()),
            )

    for version, description, migrate in MIGRATIONS:
        if version <= current:
            continue
        # The schema change and its version row commit together, so a failure can't half-apply.
        await conn.execute("BEGIN")
        try:
            await migrate(conn)
            await conn.execute(
                "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                (version, description, datetime.now(UTC).isoformat()),
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

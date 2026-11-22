"""
Database core: connection handling, schema migrations, and JSON backup/restore.

Table-specific queries live in `webhooks.py`, `transactions.py` and `gnucash.py`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.database.migrations import run_migrations
from app.txn_parser import DEFAULT_BANKS


@asynccontextmanager
async def get_db_connection(db_path: str) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager that yields a configured aiosqlite Connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        yield conn


async def init_db(db_path: str) -> None:
    """Create the database and apply any pending numbered migrations."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path, isolation_level=None) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        await run_migrations(conn)


# --- Export / import (JSON backup & restore) ---

# Child/derived tables are exported and imported whole.
EXPORT_TABLES = [
    "banks",
    "account_names",
    "gnucash_account_map",
    "category_rules",
    "webhooks",
    "card_statements",
    "transactions",
]


async def export_data(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """Every row of every table, for a JSON backup."""
    out: dict[str, list[dict[str, Any]]] = {}
    async with get_db_connection(db_path) as conn:
        for table in EXPORT_TABLES:
            async with conn.execute(f"SELECT * FROM {table}") as cursor:
                out[table] = [dict(r) for r in await cursor.fetchall()]
    return out


async def _table_columns(conn: aiosqlite.Connection, table: str) -> list[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cursor:
        return [row[1] for row in await cursor.fetchall()]


async def import_data(db_path: str, tables: dict[str, Any], mode: str = "merge") -> dict[str, int]:
    """
    Restore rows from an export.

    - `mode="replace"` wipes each table first and restores rows with their original ids (an exact
      restore).
    - `mode="merge"` keeps existing rows and adds missing ones. `webhooks` and `transactions` are
      matched on `event_id` (their `id` is left to the database), so a backup row can never be
      dropped just because its id is already used locally for a different record; each imported
      transaction's `webhook_id` is re-linked to the local webhook with the same `event_id`.

    Only known columns present in the payload are written, so older exports still import.
    Returns the number of rows inserted per table.
    """
    counts: dict[str, int] = {}
    event_to_id: dict[str, int] = {}
    async with get_db_connection(db_path) as conn:
        if mode == "replace":
            for table in reversed(EXPORT_TABLES):
                await conn.execute(f"DELETE FROM {table}")

        for table in EXPORT_TABLES:
            rows = tables.get(table) or []
            columns = await _table_columns(conn, table)
            # In merge mode, match webhooks/transactions/statements on event_id, not the local id.
            drop_id = mode == "merge" and table in ("webhooks", "transactions", "card_statements")
            inserted = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                data = dict(row)
                if table == "transactions" and data.get("post_status") == "posting":
                    # A backup taken while a post was in flight must not import a stuck claim.
                    data["post_status"] = "failed"
                    data["post_error"] = "Interrupted before import; press Post again"
                use = [c for c in columns if c in data and not (drop_id and c == "id")]
                if not use:
                    continue
                values = [data[c] for c in use]
                if mode == "merge" and table in ("transactions", "card_statements"):
                    # Point at the local webhook that holds the same event, not the backup's id.
                    local_webhook = event_to_id.get(data.get("event_id") or "")
                    if "webhook_id" in use:
                        values[use.index("webhook_id")] = local_webhook
                    else:
                        use.append("webhook_id")
                        values.append(local_webhook)
                placeholders = ", ".join("?" for _ in use)
                column_list = ", ".join(use)
                cursor = await conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({column_list}) VALUES ({placeholders})",
                    tuple(values),
                )
                inserted += cursor.rowcount
            counts[table] = inserted

            if mode == "merge" and table == "webhooks":
                async with conn.execute("SELECT id, event_id FROM webhooks") as cursor:
                    event_to_id = {r["event_id"]: r["id"] for r in await cursor.fetchall()}

        await conn.commit()
    return counts


async def clear_all_data(db_path: str) -> dict[str, int]:
    """
    Wipe every table and re-seed the default bank sender codes, so the database is back to a
    fresh-install state. Returns the number of rows deleted per table.
    """
    deleted: dict[str, int] = {}
    now = datetime.now(UTC).isoformat()
    async with get_db_connection(db_path) as conn:
        for table in reversed(EXPORT_TABLES):
            cursor = await conn.execute(f"DELETE FROM {table}")
            deleted[table] = cursor.rowcount
        # Reset AUTOINCREMENT counters so ids start from 1 again.
        await conn.execute("DELETE FROM sqlite_sequence")
        # A fresh database seeds the default banks; do the same so parsing still works.
        await conn.executemany(
            "INSERT OR IGNORE INTO banks (sender_header, name, updated_at) VALUES (?, ?, ?)",
            [(header, name, now) for header, name in DEFAULT_BANKS.items()],
        )
        await conn.commit()
    return deleted

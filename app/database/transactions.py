"""Parsed bank transactions, cards/accounts, banks and sender headers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.database.core import get_db_connection
from app.gnucash import normalise_merchant
from app.txn_parser import sender_header


async def save_transaction(
    db_path: str,
    event_id: str,
    webhook_id: int | None,
    occurred_at: str,
    raw_text: str,
    bank: str,
    sender_header: str,
    direction: str,
    amount: float,
    currency: str = "INR",
    instrument: str | None = None,
    channel: str | None = None,
    account_last4: str | None = None,
    counterparty: str | None = None,
    reference: str | None = None,
    balance: float | None = None,
    balance_type: str | None = None,
) -> bool:
    """
    Insert a parsed bank transaction. Ignores rows whose event_id already exists.
    Returns True if a new row was inserted.
    """
    sql = """
    INSERT INTO transactions (
        event_id, webhook_id, occurred_at, bank, sender_header, direction,
        amount, currency, instrument, channel, account_last4, counterparty,
        reference, balance, balance_type, raw_text, created_at, merchant_key
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_id) DO NOTHING;
    """

    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            sql,
            (
                event_id,
                webhook_id,
                occurred_at,
                bank,
                sender_header,
                direction,
                amount,
                currency,
                instrument,
                channel,
                account_last4,
                counterparty,
                reference,
                balance,
                balance_type,
                raw_text,
                datetime.now(UTC).isoformat(),
                normalise_merchant(counterparty),
            ),
        )
        await conn.commit()
        return cursor.rowcount > 0


TRANSACTION_SELECT = """
    SELECT
        t.id, t.event_id, t.webhook_id, t.occurred_at,
        COALESCE(b.name, t.bank) AS bank, t.sender_header, t.direction, t.amount, t.currency,
        t.instrument, t.channel, t.account_last4, a.name AS account_name, t.counterparty,
        t.reference, t.balance, t.balance_type, t.raw_text, t.created_at, t.is_read, t.read_at,
        t.merchant_key, t.from_account_id, t.to_account_id, t.suggestion_reason,
        t.post_status, t.gnucash_tx_id, t.post_error, t.posted_at,
        m.gnucash_account_id AS mapped_from_account_id
    FROM transactions t
    LEFT JOIN banks b ON b.sender_header = t.sender_header
    LEFT JOIN account_names a
      ON a.sender_header = t.sender_header AND a.account_last4 = t.account_last4
    LEFT JOIN gnucash_account_map m
      ON m.sender_header = t.sender_header AND m.account_last4 = t.account_last4
"""


async def get_transactions(
    db_path: str,
    limit: int = 50,
    offset: int = 0,
    bank: str | None = None,
    direction: str | None = None,
    account_last4: str | None = None,
    is_read: bool | None = None,
) -> list[dict[str, Any]]:
    """Retrieve parsed transactions with their account name, newest first, with optional filters."""
    query = TRANSACTION_SELECT + "\n    WHERE 1=1\n"
    params: list[Any] = []

    if bank:
        query += " AND (COALESCE(b.name, t.bank) = ? OR t.sender_header = ?)"
        params.extend([bank, bank.upper()])

    if direction:
        query += " AND t.direction = ?"
        params.append(direction)

    if account_last4:
        query += " AND t.account_last4 = ?"
        params.append(account_last4)

    if is_read is not None:
        query += " AND t.is_read = ?"
        params.append(int(is_read))

    query += " ORDER BY t.occurred_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    async with get_db_connection(db_path) as conn:
        async with conn.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_transaction_by_id(db_path: str, transaction_id: int) -> dict[str, Any] | None:
    """Fetch one parsed transaction with its card name and gnucash From mapping."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            TRANSACTION_SELECT + " WHERE t.id = ?",
            (transaction_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_transactions_by_ids(db_path: str, ids: list[int]) -> list[dict[str, Any]]:
    """Fetch several transactions (by id) with their card name and gnucash mapping."""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            TRANSACTION_SELECT + f" WHERE t.id IN ({placeholders}) ORDER BY t.occurred_at ASC",
            tuple(ids),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def get_accounts(db_path: str) -> list[dict[str, Any]]:
    """List every card / account seen in transactions with its name and transaction count."""
    sql = """
    SELECT
        t.sender_header,
        COALESCE(b.name, t.bank) AS bank,
        t.instrument,
        t.account_last4,
        a.name,
        m.gnucash_account_id,
        COUNT(*) AS txn_count,
        MAX(t.occurred_at) AS last_used_at
    FROM transactions t
    LEFT JOIN banks b ON b.sender_header = t.sender_header
    LEFT JOIN account_names a
      ON a.sender_header = t.sender_header AND a.account_last4 = t.account_last4
    LEFT JOIN gnucash_account_map m
      ON m.sender_header = t.sender_header AND m.account_last4 = t.account_last4
    WHERE t.account_last4 IS NOT NULL
    GROUP BY t.sender_header, t.account_last4
    ORDER BY bank, t.account_last4
    """
    async with get_db_connection(db_path) as conn:
        async with conn.execute(sql) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def set_account_name(db_path: str, sender_header: str, account_last4: str, name: str | None) -> None:
    """Set (or clear, when name is empty) the display name for a card / account."""
    async with get_db_connection(db_path) as conn:
        if name:
            await conn.execute(
                """
                INSERT INTO account_names (sender_header, account_last4, name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sender_header, account_last4)
                DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at
                """,
                (sender_header, account_last4, name, datetime.now(UTC).isoformat()),
            )
        else:
            await conn.execute(
                "DELETE FROM account_names WHERE sender_header = ? AND account_last4 = ?",
                (sender_header, account_last4),
            )
        await conn.commit()


async def get_bank_map(db_path: str) -> dict[str, str]:
    """Return linked sender headers mapped to bank names, for the transaction parser."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute("SELECT sender_header, name FROM banks") as cursor:
            return {r["sender_header"]: r["name"] for r in await cursor.fetchall()}


async def get_banks(db_path: str) -> list[dict[str, Any]]:
    """List linked bank headers with their transaction counts."""
    sql = """
    SELECT b.sender_header, b.name, b.updated_at, COUNT(t.id) AS txn_count
    FROM banks b
    LEFT JOIN transactions t ON t.sender_header = b.sender_header
    GROUP BY b.sender_header
    ORDER BY b.name, b.sender_header
    """
    async with get_db_connection(db_path) as conn:
        async with conn.execute(sql) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def set_bank(db_path: str, sender_header: str, name: str) -> None:
    """Link a sender header to a bank name (insert or rename)."""
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO banks (sender_header, name, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(sender_header) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at
            """,
            (sender_header, name, datetime.now(UTC).isoformat()),
        )
        await conn.commit()


async def delete_bank(db_path: str, sender_header: str) -> bool:
    """Unlink a sender header. Existing transactions are kept."""
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute("DELETE FROM banks WHERE sender_header = ?", (sender_header,))
        await conn.commit()
        return cursor.rowcount > 0


async def get_sender_headers(db_path: str) -> list[dict[str, Any]]:
    """
    Summarise SMS sender headers seen in webhooks ('AD-HDFCBK-S', 'JX-HDFCBK-S' -> HDFCBK),
    with message counts, example sender IDs and the linked bank name if any.
    """
    sql = """
    SELECT sender, COUNT(*) AS count, MAX(received_at) AS last_seen_at
    FROM webhooks
    WHERE sender IS NOT NULL
    GROUP BY sender
    """
    async with get_db_connection(db_path) as conn:
        async with conn.execute(sql) as cursor:
            rows = await cursor.fetchall()
        async with conn.execute("SELECT sender_header, name FROM banks") as cursor:
            banks = {r["sender_header"]: r["name"] for r in await cursor.fetchall()}

    headers: dict[str, dict[str, Any]] = {}
    for r in rows:
        header = sender_header(r["sender"])
        if not header:
            continue
        entry = headers.setdefault(header, {
            "sender_header": header,
            "bank": banks.get(header),
            "message_count": 0,
            "senders": [],
            "last_seen_at": None,
        })
        entry["message_count"] += r["count"]
        entry["senders"].append(r["sender"])
        entry["last_seen_at"] = max(filter(None, [entry["last_seen_at"], r["last_seen_at"]]), default=None)

    for entry in headers.values():
        entry["senders"].sort()
    return sorted(headers.values(), key=lambda e: e["message_count"], reverse=True)


async def set_transaction_read(db_path: str, transaction_id: int, is_read: bool) -> bool:
    """Mark a transaction as read / unread. Also marks matching webhook as read / unread."""
    read_at = datetime.now(UTC).isoformat() if is_read else None
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE transactions SET is_read = ?, read_at = ? WHERE id = ?",
            (int(is_read), read_at, transaction_id),
        )
        if cursor.rowcount > 0:
            await conn.execute(
                """
                UPDATE webhooks
                SET is_read = ?, read_at = ?
                WHERE id = (SELECT webhook_id FROM transactions WHERE id = ?)
                   OR event_id = (SELECT event_id FROM transactions WHERE id = ?)
                """,
                (int(is_read), read_at, transaction_id, transaction_id),
            )
            await conn.commit()
            return True
        return False

"""Credit-card statements and the cards summary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.database.core import get_db_connection
from app.txn_parser import DEFAULT_BANKS


async def save_card_statement(
    db_path: str,
    event_id: str,
    webhook_id: int | None,
    bank: str | None,
    sender_header: str,
    account_last4: str | None,
    statement_date: str | None,
    total_due: float | None,
    min_due: float | None,
    due_date: str | None,
    raw_text: str,
) -> bool:
    """Insert a parsed statement. Ignores rows whose event_id already exists."""
    sql = """
    INSERT INTO card_statements (
        event_id, webhook_id, bank, sender_header, account_last4,
        statement_date, total_due, min_due, due_date, raw_text, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_id) DO NOTHING;
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            sql,
            (
                event_id,
                webhook_id,
                bank,
                sender_header,
                account_last4,
                statement_date,
                total_due,
                min_due,
                due_date,
                raw_text,
                datetime.now(UTC).isoformat(),
            ),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_card_statements(db_path: str) -> list[dict[str, Any]]:
    """All parsed statements, newest first."""
    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            "SELECT * FROM card_statements ORDER BY COALESCE(statement_date, '') DESC, id DESC"
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def set_latest_statement_paid(db_path: str, sender_header: str, account_last4: str | None, paid: bool) -> bool:
    """Mark the card's most recent statement paid (or unpaid). Returns False if there is none."""
    paid_at = datetime.now(UTC).isoformat() if paid else None
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            """
            UPDATE card_statements SET paid_at = ?
            WHERE id = (
                SELECT id FROM card_statements
                WHERE sender_header = ? AND account_last4 IS ?
                ORDER BY COALESCE(statement_date, '') DESC, id DESC LIMIT 1
            )
            """,
            (paid_at, sender_header, account_last4),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_cards(db_path: str) -> list[dict[str, Any]]:
    """
    One row per credit card: its name/bank, total limit and latest available limit, and the
    latest statement's due amounts (the pending payment, unless marked paid).
    """
    async with get_db_connection(db_path) as conn:
        async with conn.execute(
            """
            SELECT t.sender_header, t.account_last4,
                   COALESCE(b.name, t.bank) AS bank, a.name AS account_name,
                   COUNT(*) AS txn_count, MAX(t.occurred_at) AS last_spend_at,
                   MAX(t.webhook_id) AS latest_webhook_id
            FROM transactions t
            LEFT JOIN banks b ON b.sender_header = t.sender_header
            LEFT JOIN account_names a
              ON a.sender_header = t.sender_header AND a.account_last4 = t.account_last4
            WHERE t.instrument = 'credit_card' AND t.account_last4 IS NOT NULL
            GROUP BY t.sender_header, t.account_last4
            """
        ) as cursor:
            accounts = [dict(r) for r in await cursor.fetchall()]

        async with conn.execute(
            "SELECT * FROM card_statements ORDER BY COALESCE(statement_date, '') DESC, id DESC"
        ) as cursor:
            statements: dict[tuple[str, str | None], dict[str, Any]] = {}
            for r in await cursor.fetchall():
                statements.setdefault((r["sender_header"], r["account_last4"]), dict(r))

    cards: dict[tuple[str, str | None], dict[str, Any]] = {}
    for a in accounts:
        cards[(a["sender_header"], a["account_last4"])] = a
    for key, s in statements.items():
        cards.setdefault(key, {
            "sender_header": key[0], "account_last4": key[1], "bank": s.get("bank"),
            "account_name": None, "txn_count": 0, "last_spend_at": None,
        })

    result: list[dict[str, Any]] = []
    for key, card in cards.items():
        sender, last4 = key
        statement = statements.get(key)
        paid = bool(statement and statement.get("paid_at"))
        due_date = statement.get("due_date") if statement else None
        wh_id = (statement.get("webhook_id") if statement else None) or card.get("latest_webhook_id")
        raw_msg = (statement.get("raw_text") if statement else None)
        result.append({
            "sender_header": sender,
            "account_last4": last4,
            "bank": card.get("bank") or (statement.get("bank") if statement else None) or DEFAULT_BANKS.get(sender),
            "name": card.get("account_name"),
            "txn_count": card.get("txn_count", 0),
            "last_spend_at": card.get("last_spend_at"),
            "has_statement": bool(statement),
            "statement_date": statement.get("statement_date") if statement else None,
            "total_due": statement.get("total_due") if statement else None,
            "min_due": statement.get("min_due") if statement else None,
            "due_date": due_date,
            "paid": paid,
            "paid_at": statement.get("paid_at") if statement else None,
            "webhook_id": wh_id,
            "raw_text": raw_msg,
        })

    result.sort(key=lambda c: ((c["bank"] or ""), (c["account_last4"] or "")))
    return result

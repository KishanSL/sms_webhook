"""Shared FastAPI helpers: templates, logging, and webhook/transaction plumbing."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.classifier import classify_message_text
from app.config import settings
from app.database import (
    get_all_webhooks,
    get_bank_map,
    get_db_connection,
    get_webhooks_without_transaction,
    save_card_statement,
    save_transaction,
    update_webhooks_detected_types,
)
from app.gnucash import IST, postable_accounts
from app.gnucash_client import GnucashError
from app.gnucash_post import load_gnucash_accounts
from app.statement_parser import compute_hdfc_due_date, parse_card_statement
from app.txn_parser import parse_transaction, sender_header

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)


def normalize_timestamp(value: str | None) -> str | None:
    """Convert an ISO-8601 or epoch-millisecond timestamp to UTC ISO-8601."""
    if not value:
        return None
    try:
        if value.isdigit():
            dt = datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        else:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def to_local_date(iso_or_utc_str: str | None, tz: tzinfo = IST) -> str | None:
    """Convert an ISO-8601, UTC timestamp, or epoch string into a local calendar date string (YYYY-MM-DD)."""
    if not iso_or_utc_str:
        return None
    normalized = normalize_timestamp(iso_or_utc_str)
    if not normalized:
        return None
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.astimezone(tz).date().isoformat()
    except (ValueError, TypeError):
        return None


async def record_transaction(record: dict[str, Any], banks: dict[str, str] | None = None) -> bool:
    """Parse a stored webhook as a bank transaction and save it. Returns True if a row was added."""
    if banks is None:
        banks = await get_bank_map(settings.DB_PATH)
    txn = parse_transaction(record.get("sender"), record.get("message_body"), banks)
    if not txn:
        return False
    return await save_transaction(
        settings.DB_PATH,
        event_id=record["event_id"],
        webhook_id=record["id"],
        occurred_at=normalize_timestamp(record.get("occurred_at")) or record["received_at"],
        raw_text=record["message_body"],
        **txn,
    )


async def retag_all_webhooks(db_path: str | None = None) -> dict[str, Any]:
    """
    Re-evaluate every stored webhook message against current classification rules
    and bank mappings, updating `detected_type` in SQLite in batch.
    """
    target_db = db_path or settings.DB_PATH
    banks = await get_bank_map(target_db)
    all_webhooks = await get_all_webhooks(target_db)
    if not all_webhooks:
        return {"status": "ok", "total_retagged": 0, "by_type": {}}

    async with get_db_connection(target_db) as conn:
        txn_rows = await (await conn.execute("SELECT webhook_id, event_id FROM transactions")).fetchall()
        txn_wh_ids = {r["webhook_id"] for r in txn_rows if r["webhook_id"]}
        txn_ev_ids = {r["event_id"] for r in txn_rows if r["event_id"]}

        stmt_rows = await (await conn.execute("SELECT webhook_id, event_id FROM card_statements")).fetchall()
        stmt_wh_ids = {r["webhook_id"] for r in stmt_rows if r["webhook_id"]}
        stmt_ev_ids = {r["event_id"] for r in stmt_rows if r["event_id"]}

    updates: list[tuple[str, int]] = []
    type_counts: dict[str, int] = {}

    for w in all_webhooks:
        wh_id = w["id"]
        ev_id = w.get("event_id")
        is_txn = (wh_id in txn_wh_ids) or (ev_id in txn_ev_ids)
        is_stmt = (wh_id in stmt_wh_ids) or (ev_id in stmt_ev_ids)
        new_type = classify_message_text(
            text=w.get("message_body"),
            sender=w.get("sender"),
            is_transaction=is_txn,
            is_statement=is_stmt,
            banks=banks,
        )
        updates.append((new_type, wh_id))
        type_counts[new_type] = type_counts.get(new_type, 0) + 1

    await update_webhooks_detected_types(target_db, updates)
    return {
        "status": "ok",
        "total_retagged": len(updates),
        "by_type": type_counts,
    }


async def backfill_transactions(db_path: str | None = None, retag: bool = True) -> int:
    """Parse stored webhooks that have no transaction row yet, and update detected_types."""
    target_db = db_path or settings.DB_PATH
    added = 0
    banks = await get_bank_map(target_db)
    for record in await get_webhooks_without_transaction(target_db):
        try:
            added += await record_transaction(record, banks)
        except Exception:
            logger.exception("Transaction backfill failed for webhook %s", record.get("id"))
        try:
            await record_card_statement(record, banks)
        except Exception:
            logger.exception("Statement backfill failed for webhook %s", record.get("id"))
    if retag:
        await retag_all_webhooks(target_db)
    return added


async def record_card_statement(record: dict[str, Any], banks: dict[str, str] | None = None) -> bool:
    """Parse a stored webhook as a credit-card statement and save it. Returns True if added."""
    if banks is None:
        banks = await get_bank_map(settings.DB_PATH)
    header = sender_header(record.get("sender"))
    if not header or header not in banks:
        return False

    statement = parse_card_statement(record.get("sender"), record.get("message_body"), banks)
    if not statement:
        return False
    stmt_date = statement.get("statement_date")
    if not stmt_date:
        stmt_date = to_local_date(record.get("occurred_at")) or to_local_date(record.get("received_at"))
    statement["statement_date"] = stmt_date
    if not statement.get("due_date") and statement.get("sender_header") == "HDFCBK" and stmt_date:
        statement["due_date"] = compute_hdfc_due_date(stmt_date)
    return await save_card_statement(
        settings.DB_PATH,
        event_id=record["event_id"],
        webhook_id=record["id"],
        raw_text=record["message_body"],
        **statement,
    )


def verify_authentication(request: Request) -> str | None:
    """
    Verify bearer token or secret header if WEBHOOK_SECRET is set.
    Returns the supplied token if present.
    """
    auth_header = request.headers.get("authorization", "")
    supplied_token = None
    if auth_header.lower().startswith("bearer "):
        supplied_token = auth_header[7:].strip()
    elif request.headers.get("x-webhook-secret"):
        supplied_token = request.headers.get("x-webhook-secret")
    elif request.headers.get("x-api-key"):
        supplied_token = request.headers.get("x-api-key")
    elif request.query_params.get("secret"):
        supplied_token = request.query_params.get("secret")

    # If server has configured WEBHOOK_SECRET, enforce it
    if settings.WEBHOOK_SECRET:
        if supplied_token != settings.WEBHOOK_SECRET:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Missing or invalid Bearer token / secret",
            )

    return supplied_token


def extract_client_ip(request: Request) -> str:
    """Extract real client IP address considering proxy headers."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


async def require_postable(account_id: str) -> None:
    """Reject an account id that is not a postable (leaf) account in gnucash-web."""
    try:
        accounts = await load_gnucash_accounts()
    except GnucashError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if account_id not in {a["id"] for a in postable_accounts(accounts)}:
        raise HTTPException(status_code=400, detail="That account is not a postable gnucash-web account")

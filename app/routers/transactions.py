"""Parsed transactions API and the gnucash posting actions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import (
    decorate_suggestions,
    get_transaction_by_id,
    get_transactions,
    get_transactions_by_ids,
    set_transaction_choice,
    set_transaction_read,
)
from app.deps import backfill_transactions, require_postable
from app.gnucash_post import attempt_post

router = APIRouter()


@router.get("/api/transactions", summary="List parsed bank transactions")
async def list_transactions(
    limit: int = 50,
    offset: int = 0,
    bank: str | None = None,
    direction: str | None = None,
    account: str | None = None,
    read: bool | None = None,
) -> list[dict[str, Any]]:
    """Retrieve parsed bank debits/credits, newest first. `bank` accepts a name or sender header."""
    rows = await get_transactions(
        db_path=settings.DB_PATH,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
        bank=bank,
        direction=direction,
        account_last4=account,
        is_read=read,
    )
    return await decorate_suggestions(settings.DB_PATH, rows)


class ReadState(BaseModel):
    read: bool


@router.put("/api/transactions/{transaction_id}/read", summary="Mark a transaction read / unread")
async def mark_transaction_read(transaction_id: int, body: ReadState) -> dict[str, Any]:
    if not await set_transaction_read(settings.DB_PATH, transaction_id, body.read):
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"id": transaction_id, "read": body.read}


@router.post("/api/transactions/backfill", summary="Parse stored webhooks into transactions")
async def run_transaction_backfill() -> dict[str, Any]:
    """Re-scan stored webhooks without a transaction row, e.g. after adding a new bank format."""
    added = await backfill_transactions()
    return {"status": "ok", "added": added}


class PostRequest(BaseModel):
    to_account_id: str | None = Field(default=None, max_length=64)
    force: bool = False


class BulkPostItem(BaseModel):
    id: int
    to_account_id: str = Field(min_length=1, max_length=64)


class BulkPostRequest(BaseModel):
    posts: list[BulkPostItem] = Field(min_length=1, max_length=200)
    force: bool = False


class ToChoice(BaseModel):
    to_account_id: str = Field(default="", max_length=64)


@router.post("/api/transactions/{transaction_id}/post", summary="Post a transaction to gnucash-web")
async def post_transaction(transaction_id: int, body: PostRequest) -> dict[str, Any]:
    """Validate, claim and post one transaction. A duplicate needs a second click (`force`)."""
    if not settings.gnucash_enabled:
        raise HTTPException(status_code=503, detail="gnucash-web integration is not configured")
    txn = await get_transaction_by_id(settings.DB_PATH, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    result = await attempt_post(txn, body.to_account_id, force=body.force)
    if not result["ok"]:
        if result["code"] == "duplicate":
            raise HTTPException(status_code=409, detail=result["detail"])
        # busy/already-posted are conflicts; gnucash/auth failures are upstream errors; the
        # rest are client/validation problems. Keep the message from the engine.
        status_code = {
            "busy": 409,
            "already_posted": 409,
            "auth": 502,
            "gnucash": 502,
            "validation": 400,
            "no_from": 400,
            "no_to": 400,
            "missing_from": 400,
            "missing_to": 400,
            "unexpected": 500,
        }.get(result["code"], 400)
        raise HTTPException(status_code=status_code, detail=result["error"])
    return result


@router.put("/api/transactions/{transaction_id}/to", summary="Save a transaction's To account without posting")
async def choose_transaction_to(transaction_id: int, body: ToChoice) -> dict[str, Any]:
    """Persist the chosen To account (the UI calls this when the dropdown changes)."""
    txn = await get_transaction_by_id(settings.DB_PATH, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if txn.get("post_status") == "posted" or txn.get("gnucash_tx_id"):
        raise HTTPException(status_code=409, detail="Already posted to gnucash-web; it is final there")
    account_id = body.to_account_id.strip()
    if account_id:
        if not settings.gnucash_enabled:
            raise HTTPException(status_code=503, detail="gnucash-web integration is not configured")
        await require_postable(account_id)
    if not await set_transaction_choice(settings.DB_PATH, transaction_id, account_id or None):
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"id": transaction_id, "to_account_id": account_id or None}


@router.post("/api/transactions/post-reviewed", summary="Post the selected transactions to gnucash-web")
async def post_reviewed(body: BulkPostRequest) -> dict[str, Any]:
    """
    Post an explicit list of {id, to_account_id} pairs. Empty selections are rejected by
    validation, and each row posts the To account the user actually chose.
    """
    if not settings.gnucash_enabled:
        raise HTTPException(status_code=503, detail="gnucash-web integration is not configured")
    rows = await get_transactions_by_ids(settings.DB_PATH, [item.id for item in body.posts])
    by_id = {row["id"]: row for row in rows}
    results = []
    for item in body.posts:
        txn = by_id.get(item.id)
        if not txn:
            results.append({"ok": False, "code": "not_found", "transaction_id": item.id, "error": "Transaction not found"})
            continue
        # attempt_post already refuses posted rows (code "already_posted").
        results.append(await attempt_post(txn, item.to_account_id, force=body.force))
    posted = sum(1 for result in results if result["ok"])
    return {"posted": posted, "total": len(body.posts), "results": results}

"""Cards/accounts, bank sender codes and sender headers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import (
    delete_bank,
    get_accounts,
    get_bank_map,
    get_banks,
    get_sender_headers,
    set_account_name,
    set_bank,
)
from app.deps import backfill_transactions, retag_all_webhooks
from app.txn_parser import HEADER_RE

router = APIRouter()


class AccountName(BaseModel):
    name: str = Field(default="", max_length=100)


@router.get("/api/accounts", summary="List cards / accounts seen in transactions")
async def list_accounts() -> list[dict[str, Any]]:
    return await get_accounts(settings.DB_PATH)


@router.put("/api/accounts/{sender_header}/{account_last4}", summary="Name a card / account")
async def rename_account(sender_header: str, account_last4: str, body: AccountName) -> dict[str, Any]:
    """Set a display name for a card / account. An empty name removes it."""
    header = sender_header.upper()
    if header not in await get_bank_map(settings.DB_PATH):
        raise HTTPException(status_code=404, detail=f"Unknown bank header: {sender_header}")
    name = body.name.strip() or None
    await set_account_name(settings.DB_PATH, header, account_last4, name)
    return {"sender_header": header, "account_last4": account_last4, "name": name}


class BankName(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/api/banks", summary="List sender headers linked to banks")
async def list_banks() -> list[dict[str, Any]]:
    return await get_banks(settings.DB_PATH)


@router.put("/api/banks/{sender_header}", summary="Link a sender header to a bank")
async def link_bank(sender_header: str, body: BankName) -> dict[str, Any]:
    """Add or rename a bank header, then parse any stored SMS from it."""
    header = sender_header.upper()
    if not HEADER_RE.match(header):
        raise HTTPException(status_code=422, detail="Sender header must be 6 letters/digits, e.g. HDFCBK")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Bank name is required")
    await set_bank(settings.DB_PATH, header, name)
    added = await backfill_transactions()
    return {"sender_header": header, "name": name, "transactions_added": added}


@router.delete("/api/banks/{sender_header}", summary="Unlink a sender header")
async def unlink_bank(sender_header: str) -> dict[str, Any]:
    """Stop treating a header as a bank. Existing transactions are kept, and messages are retagged."""
    if not await delete_bank(settings.DB_PATH, sender_header.upper()):
        raise HTTPException(status_code=404, detail="Bank header not found")
    await retag_all_webhooks(settings.DB_PATH)
    return {"status": "deleted", "sender_header": sender_header.upper()}


@router.get("/api/senders", summary="List SMS sender headers seen in messages")
async def list_sender_headers() -> list[dict[str, Any]]:
    return await get_sender_headers(settings.DB_PATH)

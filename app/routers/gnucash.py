"""gnucash-web account list, card mapping and learning."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import (
    get_bank_map,
    get_card_defaults,
    get_category_rules,
    set_gnucash_account_map,
)
from app.deps import require_postable
from app.gnucash import postable_accounts
from app.gnucash_client import GnucashError
from app.gnucash_post import learn_from_history, load_gnucash_accounts

router = APIRouter()


class GnucashMapping(BaseModel):
    gnucash_account_id: str = Field(default="", max_length=64)


@router.get("/api/gnucash/accounts", summary="gnucash-web accounts for the Post pickers")
async def gnucash_accounts(refresh: bool = False) -> dict[str, Any]:
    if not settings.gnucash_enabled:
        return {"enabled": False, "accounts": []}
    try:
        accounts = await load_gnucash_accounts(refresh=refresh)
    except GnucashError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"enabled": True, "accounts": postable_accounts(accounts)}


@router.put("/api/accounts/{sender_header}/{account_last4}/gnucash", summary="Map a card to its gnucash From account")
async def set_account_gnucash(sender_header: str, account_last4: str, body: GnucashMapping) -> dict[str, Any]:
    """Set (or clear, with an empty id) the gnucash account a card posts from."""
    header = sender_header.upper()
    if header not in await get_bank_map(settings.DB_PATH):
        raise HTTPException(status_code=404, detail=f"Unknown bank header: {sender_header}")
    account_id = body.gnucash_account_id.strip()
    if account_id:
        if not settings.gnucash_enabled:
            raise HTTPException(status_code=503, detail="gnucash-web integration is not configured")
        await require_postable(account_id)
    await set_gnucash_account_map(settings.DB_PATH, header, account_last4, account_id or None)
    return {"sender_header": header, "account_last4": account_last4, "gnucash_account_id": account_id or None}


@router.post("/api/gnucash/learn", summary="Learn To-account hints from gnucash-web history")
async def gnucash_learn() -> dict[str, Any]:
    """Read past 2-split gnucash transactions and record merchant → other-account hints."""
    if not settings.gnucash_enabled:
        raise HTTPException(status_code=503, detail="gnucash-web integration is not configured")
    try:
        return await learn_from_history()
    except GnucashError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/gnucash/rules", summary="Confirmed and learned To-account rules")
async def gnucash_rules() -> dict[str, Any]:
    rules = await get_category_rules(settings.DB_PATH)
    defaults = await get_card_defaults(settings.DB_PATH)
    return {"rules": rules, "card_defaults": {k: {"account_id": v[0], "count": v[1]} for k, v in defaults.items()}}

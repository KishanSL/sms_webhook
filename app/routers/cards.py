"""Credit cards: pending statement payments and mark-as-paid."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import get_cards, set_latest_statement_paid

router = APIRouter()


class PaidState(BaseModel):
    paid: bool = True


@router.get("/api/cards", summary="Credit cards with their pending statement payment")
async def list_cards() -> list[dict[str, Any]]:
    return await get_cards(settings.DB_PATH)


@router.put("/api/cards/{sender_header}/{account_last4}/paid", summary="Mark a card's latest statement paid")
async def mark_card_paid(sender_header: str, account_last4: str, body: PaidState) -> dict[str, Any]:
    """Mark the card's most recent statement as paid (or unpaid)."""
    header = sender_header.upper()
    last4 = None if account_last4 in ("", "_") else account_last4
    if not await set_latest_statement_paid(settings.DB_PATH, header, last4, body.paid):
        raise HTTPException(status_code=404, detail="No statement found for this card")
    return {"sender_header": header, "account_last4": last4, "paid": body.paid}
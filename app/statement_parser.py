"""
Credit-card statement SMS parser.

Recognises statement messages (which are not money-movement transactions) and extracts the
amounts due, card ending, and due dates from bank statement SMS:
- SBI Card: "E-statement of SBI Credit Card ending XX24 dated 16/11/2026 ... Total Amt Due Rs 18450; Min Amt Due Rs 369; Payable by 06/12/2026."
- Axis Bank: "Your statement for Axis Bank Credit Card no. XX8642 is generated. Due on: 08-12-26 Total amt: INR Dr. 42310.55 Min amt due: INR Dr. 912.00"
- OneCard: "Your BOBCARD One Credit Card bill of Rs. 0.00 is ready. Pay by 05 Dec, 2026 via the OneCard app"
- HDFC Bank: "Statement Generated: For HDFC Bank Credit Card XX9012."
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from app.txn_parser import AMT, DEFAULT_BANKS, sender_header
from app.txn_parser import to_amount as _amount

logger = logging.getLogger(__name__)

DATE = r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3},?\s*\d{2,4})"


def compute_hdfc_due_date(statement_date: str | None) -> str | None:
    """Calculate HDFC statement due date (statement date + 20 days)."""
    if not statement_date:
        return None
    try:
        return (datetime.fromisoformat(statement_date).date() + timedelta(days=20)).isoformat()
    except (ValueError, TypeError) as e:
        logger.warning("Could not calculate HDFC due date from '%s': %s", statement_date, e)
        return None

DATE_FORMATS = [
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d %b, %Y",
    "%d %b %Y",
    "%d %B, %Y",
    "%d %B %Y",
]


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    normalised = cleaned.replace(".", "/").replace("-", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(normalised, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_card_statement(
    sender: str | None,
    text: str | None,
    banks: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """
    Parse a credit-card statement SMS into due amounts and card details.
    Returns None if the sender isn't a linked bank or the message isn't a statement.
    """
    b_map = banks if banks is not None else DEFAULT_BANKS
    header = sender_header(sender)
    if header not in b_map or not text:
        return None

    bank = b_map[header]
    lower = text.lower()

    # Template 1: Axis Bank direct statement
    # e.g.: Your statement for Axis Bank Credit Card no. XX8642 is generated.
    # Due on: 08-12-26 Total amt: INR Dr. 42310.55 Min amt due: INR Dr. 912.00
    if header == "AXISBK" and ("statement" in lower or "e-statement" in lower):
        ending_m = re.search(r"(?:card\s+no\.?\s*(?:xx|\*+)?|ending\s+(?:xx|\*+)?)\s*(\d{2,4})", text, re.IGNORECASE)
        due_date_m = re.search(rf"(?:due\s+on|due\s+date|payable\s+by)\s*:?\s*{DATE}", text, re.IGNORECASE)
        total_m = re.search(rf"total\s+amt(?:\s+due)?\s*:?\s*(?:inr|rs\.?)?\s*(?:dr\.?)?\s*({AMT})", text, re.IGNORECASE)
        min_m = re.search(rf"min(?:imum)?\s+amt(?:\s+due)?\s*:?\s*(?:inr|rs\.?)?\s*(?:dr\.?)?\s*({AMT})", text, re.IGNORECASE)
        if total_m or due_date_m or ending_m:
            return {
                "bank": bank,
                "sender_header": header,
                "account_last4": ending_m.group(1) if ending_m else None,
                "statement_date": None,
                "total_due": _amount(total_m.group(1)) if total_m else None,
                "min_due": _amount(min_m.group(1)) if min_m else None,
                "due_date": _iso_date(due_date_m.group(1)) if due_date_m else None,
            }

    # Template 2: OneCard / BOBCARD One
    # e.g.: Your BOBCARD One Credit Card bill of Rs. 0.00 is ready. Pay by 05 Dec, 2026 via the OneCard app
    if header in ("BOBONE", "BOBCRD", "ONECRD") and ("bill" in lower or "statement" in lower):
        total_m = re.search(rf"(?:bill\s+of|total\s+amt\s+due)\s*(?:rs\.?|inr)?\s*({AMT})", text, re.IGNORECASE)
        due_date_m = re.search(rf"(?:pay\s+by|due\s+on|due\s+date)\s*:?\s*{DATE}", text, re.IGNORECASE)
        ending_m = re.search(r"(?:card\s*(?:no\.?)?\s*(?:xx|\*+)?|ending\s+(?:xx|\*+)?|(?:xx|\*+))\s*(\d{2,4})", text, re.IGNORECASE)
        if total_m or due_date_m:
            return {
                "bank": bank,
                "sender_header": header,
                "account_last4": ending_m.group(1) if ending_m else None,
                "statement_date": None,
                "total_due": _amount(total_m.group(1)) if total_m else None,
                "min_due": None,
                "due_date": _iso_date(due_date_m.group(1)) if due_date_m else None,
            }

    # Template 3: HDFC Bank direct statement alert
    # e.g.: Statement Generated: For HDFC Bank Credit Card XX9012. View: ... Pay: ...
    if header == "HDFCBK" and "statement generated" in lower:
        ending_m = re.search(r"(?:card\s*(?:no\.?)?\s*(?:xx|\*+)?|ending\s+(?:xx|\*+)?)\s*(\d{2,4})", text, re.IGNORECASE)
        total_m = re.search(rf"total\s+am(?:oun)?t(?:\s+due)?\s*:?\s*(?:rs\.?|inr)?\s*({AMT})", text, re.IGNORECASE)
        due_date_m = re.search(rf"(?:payable\s+by|due\s+(?:date|on))\s*:?\s*{DATE}", text, re.IGNORECASE)
        min_m = re.search(rf"min(?:imum)?\s+am(?:oun)?t\s+due\s*:?\s*(?:rs\.?|inr)?\s*({AMT})", text, re.IGNORECASE)
        stmt_date_m = re.search(rf"(?:dated|statement\s+date)\s*:?\s*{DATE}", text, re.IGNORECASE)
        stmt_date = _iso_date(stmt_date_m.group(1)) if stmt_date_m else None
        due_date = _iso_date(due_date_m.group(1)) if due_date_m else None
        if not due_date and stmt_date:
            due_date = compute_hdfc_due_date(stmt_date)
        return {
            "bank": bank,
            "sender_header": header,
            "account_last4": ending_m.group(1) if ending_m else None,
            "statement_date": stmt_date,
            "total_due": _amount(total_m.group(1)) if total_m else None,
            "min_due": _amount(min_m.group(1)) if min_m else None,
            "due_date": due_date,
        }

    # Template 4: Generic / SBI Card statement
    # e.g.: E-statement of SBI Credit Card ending XX24 dated 16/11/2026 ... Total Amt Due Rs 18450; Min Amt Due Rs 369; Payable by 06/12/2026.
    if "statement" in lower or "due" in lower:
        total_m = re.search(rf"total\s+amt\s+due\s*:?\s*(?:rs\.?|inr)?\s*({AMT})", text, re.IGNORECASE)
        ending_m = re.search(r"ending\s+(?:xx|\*+)?\s*(\d{2,4})", text, re.IGNORECASE)
        stmt_date_m = re.search(rf"dated\s+{DATE}", text, re.IGNORECASE)
        due_date_m = re.search(rf"(?:payable\s+by|due\s+(?:date|on))\s*:?\s*{DATE}", text, re.IGNORECASE)
        min_m = re.search(rf"min(?:imum)?\s+amt\s+due\s*:?\s*(?:rs\.?|inr)?\s*({AMT})", text, re.IGNORECASE)
        if total_m:
            return {
                "bank": bank,
                "sender_header": header,
                "account_last4": ending_m.group(1) if ending_m else None,
                "statement_date": _iso_date(stmt_date_m.group(1)) if stmt_date_m else None,
                "total_due": _amount(total_m.group(1)),
                "min_due": _amount(min_m.group(1)) if min_m else None,
                "due_date": _iso_date(due_date_m.group(1)) if due_date_m else None,
            }

    return None

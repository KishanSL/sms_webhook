"""
SMS message type classification.

Categorises incoming messages into user-facing types:
- Transaction: financial debits/credits (also visible on /transactions)
- OTP: one-time authentication passwords/codes
- Statement: credit card statements and billing alerts (used on /cards)
- Toll / FASTag: toll deductions and FASTag transit debits
- Mandate / Autopay: recurring payment mandates and autopay setups
- Broker / Regulatory: SEBI/NSE/BSE regulatory broker balance disclosures
- Others: general notifications, promotions, and uncategorised messages
"""

from __future__ import annotations

import re
from typing import Any

from app.statement_parser import parse_card_statement
from app.txn_parser import DEFAULT_BANKS, parse_transaction, sender_header

TYPE_TRANSACTION = "Transaction"
TYPE_OTP = "OTP"
TYPE_STATEMENT = "Statement"
TYPE_TOLL = "Toll / FASTag"
TYPE_MANDATE = "Mandate / Autopay"
TYPE_BROKER = "Broker / Regulatory"
TYPE_OTHERS = "Others"

ALL_TYPES = [
    TYPE_TRANSACTION,
    TYPE_OTP,
    TYPE_STATEMENT,
    TYPE_TOLL,
    TYPE_MANDATE,
    TYPE_BROKER,
    TYPE_OTHERS,
]

# Case-insensitive regular expressions for classification
OTP_RE = re.compile(
    r"(\bOTP\b|\bOne[- ]Time Password\b|\bverification code\b|\blogin OTP\b|\blogin code\b|\bSECRET OTP\b|\bPasscode\b)",
    re.IGNORECASE,
)

STATEMENT_RE = re.compile(
    r"(\be-statement\b|\bstatement generated\b|\bstatement for\b|\bcredit card bill\b|\bbill of rs\.?\b|\btotal (?:amt|amount) due\b|\bstatement dated\b|\bcard statement\b)",
    re.IGNORECASE,
)

TOLL_RE = re.compile(
    r"(\btoll paid\b|\bFASTag\b|\bToll Plaza\b|\bTag\s+[0-9a-zA-Z]+.*toll\b)",
    re.IGNORECASE,
)

MANDATE_RE = re.compile(
    r"(\bmandate\b|\bautopay\b|\be-mandate\b|\bsi created\b|\bstanding instruction\b)",
    re.IGNORECASE,
)

REGULATORY_RE = re.compile(
    r"(\breported your Fund bal\b|\bSecurities bal\b|\bbroker-BSE\b|\bbroker-NSE\b|\bCDSL\b|\bNSDL\b)",
    re.IGNORECASE,
)


def classify_message_text(
    text: str | None,
    sender: str | None = None,
    is_transaction: bool = False,
    is_statement: bool = False,
    banks: dict[str, str] | None = None,
) -> str:
    """
    Classify a message by its content, sender, and database status.
    """
    # 1. Transaction: if known transaction record exists or successfully parses as bank transaction
    if is_transaction:
        return TYPE_TRANSACTION

    clean_text = text or ""
    b_map = banks if banks is not None else DEFAULT_BANKS

    header = sender_header(sender) if sender else None
    is_bank_sender = bool(header and header in b_map)

    if is_bank_sender and clean_text and parse_transaction(sender, clean_text, b_map):
        return TYPE_TRANSACTION

    # 2. OTP: check OTP before statement since OTP is an ephemeral action code
    if OTP_RE.search(clean_text):
        return TYPE_OTP

    # 3. Statement: strictly classify as Statement only if it is in Cards (recorded or parseable)
    if is_statement:
        return TYPE_STATEMENT

    if is_bank_sender and clean_text and parse_card_statement(sender, clean_text, b_map):
        return TYPE_STATEMENT

    # 4. Toll / FASTag
    if TOLL_RE.search(clean_text):
        return TYPE_TOLL

    # 5. Mandate / Autopay
    if MANDATE_RE.search(clean_text):
        return TYPE_MANDATE

    # 6. Broker / Regulatory
    if REGULATORY_RE.search(clean_text):
        return TYPE_BROKER

    # 7. Default to Others
    return TYPE_OTHERS


async def enrich_webhooks_with_types(
    webhooks: list[dict[str, Any]],
    banks: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Enrich each webhook dict in-place with its detected_type."""
    if not webhooks:
        return webhooks

    # If all items already have detected_type populated from SQLite, avoid regexes.
    if all(w.get("detected_type") for w in webhooks):
        return webhooks

    for w in webhooks:
        if not w.get("detected_type"):
            w["detected_type"] = classify_message_text(
                text=w.get("message_body"),
                sender=w.get("sender"),
                banks=banks,
            )

    return webhooks

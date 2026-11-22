"""
Bank Transaction SMS Parser.
Identifies money-movement SMS (debits / credits) from known bank sender headers
and extracts amount, account, counterparty, reference and balance.
Messages that don't match a known format (tolls, statements, OTPs) are ignored.
"""

import re
from typing import Any

# Seed data for the `banks` table (DLT sender header -> bank name), inserted when the
# table is first created. Headers are managed from the dashboard after that.
DEFAULT_BANKS: dict[str, str] = {
    "HDFCBK": "HDFC Bank",
    "ICICIT": "ICICI Bank",
    "AXISBK": "Axis Bank",
    "IDFCFB": "IDFC First Bank",
    "MYSBIC": "SBI Card",
    "BOBONE": "OneCard",
    "BOBCRD": "BOBCARD",
}

HEADER_RE = re.compile(r"^[A-Z0-9]{6}$")
SENDER_RE = re.compile(r"^[A-Z]{2}-([A-Z0-9]{6})(?:-[A-Z])?$")

AMT = r"[\d,]+(?:\.\d+)?"


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern.replace("AMT", AMT), re.IGNORECASE)


# Known message formats. Each pattern names its bank in the text, so all patterns are tried
# for any linked header (a bank sending from a new header only needs the header linked).
# Named groups: amount, last4, counterparty, reference, balance.
# The dict holds fixed fields for every match of that pattern.
PATTERNS: list[tuple[re.Pattern[str], dict[str, str]]] = [
    # HDFC: Txn Rs.30.00 On HDFC Bank Card 1111 At *3190@okbizaxi by UPI 111122223333 On 15-11
    (
        _re(r"Txn Rs\.?\s*(?P<amount>AMT)\s+On HDFC Bank Card (?P<last4>\d{4})\s+"
            r"At (?P<counterparty>\S+)\s+by UPI (?P<reference>\d+)"),
        {"direction": "debit", "instrument": "credit_card", "channel": "upi"},
    ),
    # HDFC: Spent Rs.641 On HDFC Bank Card 2222 At Swiggy Instamart On 2026-11-16:14:54:18
    (
        _re(r"Spent Rs\.?\s*(?P<amount>AMT)\s+On HDFC Bank Card (?P<last4>\d{4})\s+"
            r"At (?P<counterparty>.+?)\s+On \d{4}-\d{2}-\d{2}"),
        {"direction": "debit", "instrument": "credit_card", "channel": "card"},
    ),
    # HDFC: Rs.626 spent on HDFC Bank Card x2222 at Swiggy Instamart on 2026-11-22:12:02:23
    (
        _re(r"Rs\.?\s*(?P<amount>AMT)\s+spent on HDFC Bank Card x(?P<last4>\d{4})\s+"
            r"at (?P<counterparty>.+?)\s+on \d{4}-\d{2}-\d{2}"),
        {"direction": "debit", "instrument": "credit_card", "channel": "card"},
    ),
    # ICICI: INR 789.00 spent using ICICI Bank Card XX3333 on 15-Nov-26 on AMAZON PAY IN G. Avl Limit: INR 1,48,126.50
    (
        _re(r"INR (?P<amount>AMT) spent using ICICI Bank Card XX(?P<last4>\d{4}) on \S+ "
            r"on (?P<counterparty>.+?)\.\s+Avl Limit: INR (?P<balance>AMT)"),
        {"direction": "debit", "instrument": "credit_card", "channel": "card", "balance_type": "limit"},
    ),
    # Axis: Spent INR 199 using Axis Bank Credit Card no. XX4444 on 17-11-26 01:01:49 IST at GOOGLE PLAY. Avl limit: INR 245509.40
    (
        _re(r"Spent INR (?P<amount>AMT)\s+using Axis Bank Credit Card no\. XX(?P<last4>\d{4}) "
            r"on .+? at\s+(?P<counterparty>.+?)\.\s+Avl limit: INR (?P<balance>AMT)"),
        {"direction": "debit", "instrument": "credit_card", "channel": "card", "balance_type": "limit"},
    ),
    # Axis: INR 10000.00 debited A/c no. XX5555 17-11-26, 12:48:17 UPI/P2A/123456789012/JOHN DOE ... Axis Bank
    (
        _re(r"INR (?P<amount>AMT) debited\s+A/c no\. XX(?P<last4>\d{4})\s+[^\n]+\s+"
            r"UPI/\w+/(?P<reference>\d+)/(?P<counterparty>[^\n]+)[\s\S]*Axis Bank"),
        {"direction": "debit", "instrument": "account", "channel": "upi"},
    ),
    # IDFC: Your A/c XX6666 debited by Rs. 30.00 on 15/11/26; TEA STALL credited. RRN 123456789012. Available balance Rs. 5,210.35. Team IDFC FIRST Bank
    (
        _re(r"Your A/c XX(?P<last4>\d{4}) debited by Rs\.?\s*(?P<amount>AMT) on \S+; "
            r"(?P<counterparty>.+?) credited\. RRN (?P<reference>\d+)\. "
            r"Available balance Rs\.?\s*(?P<balance>AMT)[\s\S]*IDFC FIRST Bank"),
        {"direction": "debit", "instrument": "account", "balance_type": "balance"},
    ),
]


def sender_header(sender: str | None) -> str | None:
    """Extract the DLT header from a sender ID ('AD-HDFCBK-S' -> 'HDFCBK')."""
    if not sender:
        return None
    match = SENDER_RE.match(sender.strip().upper())
    return match.group(1) if match else None


def to_amount(value: str | None) -> float | None:
    return float(value.replace(",", "")) if value else None


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split()) or None


def parse_transaction(sender: str | None, text: str | None, banks: dict[str, str]) -> dict[str, Any] | None:
    """
    Parse a bank SMS into transaction fields.
    `banks` maps linked sender headers to bank names (the `banks` table).
    Returns None if the sender isn't a linked bank or the text isn't a recognised debit/credit.
    """
    header = sender_header(sender)
    if header not in banks or not text:
        return None

    for pattern, fields in PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        balance = to_amount(groups.get("balance"))
        return {
            "bank": banks[header],
            "sender_header": header,
            "direction": fields["direction"],
            "amount": to_amount(groups["amount"]),
            "currency": "INR",
            "instrument": fields.get("instrument"),
            "channel": fields.get("channel"),
            "account_last4": groups.get("last4"),
            "counterparty": _clean(groups.get("counterparty")),
            "reference": groups.get("reference"),
            "balance": balance,
            "balance_type": fields.get("balance_type") if balance is not None else None,
        }

    return None

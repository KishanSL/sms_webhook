"""
Domain logic for the SMS → gnucash-web bridge: merchant normalising, account paths,
the post payload, and the "To" account suggestion engine.

Everything here is a pure function over plain data, so it can be unit-tested without a
database or network. The HTTP calls live in app/gnucash_client.py, and persistence in
app/database/.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

# India has no DST; a fixed offset avoids depending on tzdata (absent from python:*-slim).
IST = timezone(timedelta(hours=5, minutes=30))

MAX_DESCRIPTION = 255
MAX_NUM = 50


def normalise_merchant(counterparty: str | None) -> str:
    """
    Reduce a counterparty to a stable key so repeats count together:
    - masked UPI payees (`*4521@ybl`) are kept as-is (they rarely repeat)
    - a payment-gateway prefix before `*` is dropped (`RSP*SWIGGY PVT LTD` → `SWIGGY PVT LTD`)
    - upper-cased and whitespace collapsed
    - a trailing `IN <letter>` (ICICI card descriptions) is dropped (`AMAZON PAY IN G` → `AMAZON PAY`)
    """
    if not counterparty:
        return ""
    text = counterparty.strip().upper()
    if "@" in text:
        return " ".join(text.split())
    if "*" in text:
        text = text.split("*", 1)[1]
    parts = text.split()
    if len(parts) > 1 and len(parts[-1]) == 1:
        parts.pop()
    if len(parts) > 1 and parts[-1] == "IN":
        parts.pop()
    return " ".join(parts)


def display_merchant(counterparty: str | None) -> str:
    """Human description for the gnucash transaction: `AMAZON PAY IN G` → `Amazon Pay`."""
    key = normalise_merchant(counterparty)
    if not key:
        return ""
    if "@" in key:
        return f"UPI {key}"
    return key.title()


def ist_date(occurred_at: str | None, fallback: str | None = None) -> str:
    """`occurred_at` (UTC ISO) as an IST calendar date, `YYYY-MM-DD`."""
    value = occurred_at or fallback
    if not value:
        return datetime.now(IST).date().isoformat()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(IST).date().isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST).date().isoformat()


def build_account_paths(raw_accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Add a full `path` (e.g. `Liabilities:Credit Cards:ICICI Amazon Pay`) built from `parent_id`,
    and a `leaf` flag (no account has this account as a parent). Returns the list sorted by path.
    Placeholders and hidden accounts are kept (flagged) so callers can decide; use
    `postable_accounts` for the dropdowns.
    """
    by_id = {a["id"]: a for a in raw_accounts}
    parent_ids = {a.get("parent_id") for a in raw_accounts if a.get("parent_id")}
    result: list[dict[str, Any]] = []
    for account in raw_accounts:
        names = [account.get("name") or "?"]
        parent = account.get("parent_id")
        seen = {account["id"]}
        while parent and parent in by_id and parent not in seen:
            seen.add(parent)
            names.append(by_id[parent].get("name") or "?")
            parent = by_id[parent].get("parent_id")
        result.append(
            {
                "id": account["id"],
                "name": account.get("name"),
                "account_type": account.get("account_type"),
                "currency": account.get("currency"),
                "placeholder": bool(account.get("placeholder")),
                "hidden": bool(account.get("hidden")),
                "leaf": account["id"] not in parent_ids,
                "path": ":".join(reversed(names)),
            }
        )
    result.sort(key=lambda a: a["path"].lower())
    return result


def postable_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Accounts that can receive postings: leaf accounts (no children — their names are unique),
    no placeholders, nothing hidden.
    """
    return [a for a in accounts if a.get("leaf", True) and not a["placeholder"] and not a["hidden"]]


def build_transaction_payload(
    txn: dict[str, Any],
    to_account_id: str,
    from_account_id: str,
    from_memo: str | None = None,
) -> dict[str, Any]:
    """
    Build the gnucash-web POST/PUT body for one parsed transaction.

    Debit SMS: the From (card/account) split is negative, the To split positive.
    Credit SMS: reversed. The From split is marked cleared (`c`) — the bank confirmed it.
    """
    amount = round(float(txn["amount"]), 2)
    is_debit = txn.get("direction") != "credit"
    from_amount = -amount if is_debit else amount
    to_amount = amount if is_debit else -amount

    description = (display_merchant(txn.get("counterparty")) or txn.get("bank") or "Bank transaction")[:MAX_DESCRIPTION]
    reference = (txn.get("reference") or "").strip()
    num = reference[:MAX_NUM] or None

    notes = txn.get("raw_text") or ""
    notes = f"{notes}\nsms-event:{txn['event_id']}"

    return {
        "post_date": ist_date(txn.get("occurred_at")),
        "num": num,
        "description": description,
        "notes": notes,
        "splits": [
            {
                "account_id": from_account_id,
                "amount": f"{from_amount:.2f}",
                "memo": from_memo,
                "reconciled_state": "c",
            },
            {
                "account_id": to_account_id,
                "amount": f"{to_amount:.2f}",
                "reconciled_state": "n",
            },
        ],
    }


def card_scope(sender_header: str | None, account_last4: str | None) -> str:
    return f"{sender_header or '?'}:{account_last4 or '?'}"


def _best_rule(rules: list[dict[str, Any]], merchant_key: str, scopes: list[str], source: str) -> dict[str, Any] | None:
    """Highest-hit rule for a merchant in any of `scopes`, or None."""
    candidates = [
        r for r in rules
        if r["merchant_key"] == merchant_key and r["scope"] in scopes and r["source"] == source
    ]
    if not candidates:
        return None
    # Prefer the more specific scope (earlier in the list), then more hits.
    order = {scope: i for i, scope in enumerate(scopes)}
    candidates.sort(key=lambda r: (order.get(r["scope"], len(scopes)), -int(r["hits"] or 0)))
    return candidates[0]


def choose_suggestion(
    merchant_key: str,
    merchant_label: str,
    scope: str,
    rules: list[dict[str, Any]],
    card_defaults: dict[str, tuple[str, int]],
) -> tuple[str | None, str]:
    """
    Pick a "To" account and the reason to show, in this order:
      1. your confirmed choices for this merchant (this card, then any card)
      2. accounts learned from your existing gnucash-web history for this merchant
      3. this card's usual destination
      4. nothing — the UI shows "Uncategorised" and Post stays disabled
    `rules` rows: {merchant_key, scope, source, gnucash_account_id, hits}.
    `card_defaults` maps a card scope to (account_id, count) of posted transactions.
    `merchant_label` is the human name used in the reason (e.g. `Amazon Pay`).
    """
    label = merchant_label or merchant_key or "this merchant"
    confirmed = _best_rule(rules, merchant_key, [scope, "*"], "confirmed")
    if confirmed and confirmed["hits"]:
        total = sum(
            int(r["hits"] or 0)
            for r in rules
            if r["merchant_key"] == merchant_key
            and r["scope"] == confirmed["scope"]
            and r["source"] == "confirmed"
        )
        reason = f"chosen {confirmed['hits']} of {total} times for {label}"
        return confirmed["gnucash_account_id"], reason

    learned = _best_rule(rules, merchant_key, ["*"], "learned")
    if learned and learned["hits"]:
        reason = f"from your gnucash history ({learned['hits']}× for {label})"
        return learned["gnucash_account_id"], reason

    default = card_defaults.get(scope)
    if default and default[1]:
        return default[0], f"usual for this card ({default[1]}×)"

    return None, "Uncategorised"

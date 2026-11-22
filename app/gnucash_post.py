"""
Posting engine for the SMS → gnucash-web bridge.

Kept separate from the FastAPI routes so it can be tested and reused without the web layer.
It owns the account-list cache, payload submission, duplicate detection and the learn pass;
HTTP failures are typed by app/gnucash_client.py.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, cast

from app.config import settings
from app.database import (
    claim_transaction_for_posting,
    get_gnucash_account_map,
    record_confirmed_rule,
    record_learned_rules,
    set_transaction_post_failed,
    set_transaction_posted,
)
from app.gnucash import (
    build_account_paths,
    build_transaction_payload,
    card_scope,
    ist_date,
    normalise_merchant,
    postable_accounts,
)
from app.gnucash_client import (
    GnucashAuthError,
    GnucashClient,
    GnucashError,
    GnucashTimeout,
    GnucashUnreachable,
    GnucashValidationError,
)

logger = logging.getLogger(__name__)

GNUCASH_ACCOUNT_TTL_SECONDS = 300
_accounts_cache: dict[str, Any] = {"accounts": None, "fetched_at": 0.0}


def gnucash_client() -> GnucashClient:
    if not settings.gnucash_enabled:
        raise GnucashError("gnucash-web integration is not configured")
    return GnucashClient(settings.GNUCASH_URL or "", settings.GNUCASH_API_TOKEN or "")


async def load_gnucash_accounts(refresh: bool = False) -> list[dict[str, Any]]:
    """The gnucash account list, with full paths, cached in memory for a few minutes."""
    now = time.monotonic()
    if refresh or _accounts_cache["accounts"] is None or now - _accounts_cache["fetched_at"] > GNUCASH_ACCOUNT_TTL_SECONDS:
        raw = await gnucash_client().list_accounts()
        _accounts_cache["accounts"] = build_account_paths(raw)
        _accounts_cache["fetched_at"] = now
    return cast(list[dict[str, Any]], _accounts_cache["accounts"])


def reset_account_cache() -> None:
    """Drop the cached account list (used by tests and the Refresh button)."""
    _accounts_cache.update({"accounts": None, "fetched_at": 0.0})


def _from_memo(txn: dict[str, Any]) -> str:
    label = txn.get("account_name") or txn.get("bank") or txn.get("sender_header") or "Account"
    last4 = txn.get("account_last4")
    return f"{label} ••{last4}" if last4 else label


async def find_by_event_tag(client: GnucashClient, event_id: str) -> dict[str, Any] | None:
    """
    Find the gnucash transaction that carries this SMS event's tag, if it exists.
    gnucash's search is a substring match, so confirm the exact `sms-event:<id>` line to
    avoid matching a different event whose id starts with the same digits.
    """
    tag = f"sms-event:{event_id}"
    for tx in await client.list_transactions(search=tag, limit=10):
        notes = tx.get("notes") or ""
        if any(line.strip() == tag for line in notes.splitlines()):
            return tx
    return None


def _accounts_from_adopted(adopted: dict[str, Any], mapped_from_id: str) -> tuple[str, str | None]:
    """
    The accounts actually used by an adopted (already-posted) transaction, so the row records
    the ledger's From/To rather than the retry's choice.
    """
    splits = adopted.get("splits") or []
    if not splits:
        return mapped_from_id, None
    from_split = next((s for s in splits if s.get("account_id") == mapped_from_id), None)
    if from_split is None:
        # Fall back to the most negative split (money leaving the account).
        from_split = min(splits, key=lambda s: float(s.get("amount", 0) or 0))
    to_split = next((s for s in splits if s is not from_split), None)
    return (from_split.get("account_id") or mapped_from_id), (to_split.get("account_id") if to_split else None)


async def _submit_transaction(
    client: GnucashClient, txn: dict[str, Any], from_id: str, to_id: str
) -> tuple[str, str, dict[str, Any] | None]:
    """
    Create the gnucash transaction. Returns (gnucash_id, action, adopted_transaction).
    A posted transaction is final, so this only ever creates; it never updates.
    """
    payload = build_transaction_payload(txn, to_id, from_id, from_memo=_from_memo(txn))

    # Pre-flight: an earlier attempt may have landed even though we recorded a failure.
    # Searching the tag before POSTing makes retries idempotent.
    existing = await find_by_event_tag(client, txn["event_id"])
    if existing:
        return existing["id"], "adopted", existing

    try:
        result = await client.create_transaction(payload)
    except GnucashTimeout:
        # Unknown outcome: verify by tag before reporting failure.
        existing = await find_by_event_tag(client, txn["event_id"])
        if existing:
            return existing["id"], "adopted", existing
        raise
    except GnucashUnreachable:
        # The request never reached gnucash-web; do not search or retry automatically.
        raise
    gnucash_id = result.get("id")
    if not gnucash_id:
        raise GnucashError("gnucash-web accepted the post but returned no id")
    return gnucash_id, "created", None


async def find_possible_duplicate(client: GnucashClient, txn: dict[str, Any], from_id: str) -> dict[str, Any] | None:
    """
    A hand-entered gnucash transaction with the same signed amount (±2 days) on the same account.
    Transactions already tagged by sms_webhook are ignored: they are either this event or another
    SMS that legitimately shares the amount, never a hand-entered duplicate.
    """
    amount = round(float(txn["amount"]), 2)
    if amount <= 0:
        return None
    expected = -amount if (txn.get("direction") or "debit") != "credit" else amount
    target = date.fromisoformat(ist_date(txn.get("occurred_at")))
    from_date = (target - timedelta(days=2)).isoformat()
    to_date = (target + timedelta(days=2)).isoformat()
    offset = 0
    while offset < 1500:
        rows = await client.list_transactions(
            account_id=from_id,
            from_date=from_date,
            to_date=to_date,
            limit=500,
            offset=offset,
        )
        if not rows:
            break
        for candidate in rows:
            if "sms-event:" in (candidate.get("notes") or ""):
                continue
            try:
                post_date = date.fromisoformat(candidate["post_date"])
            except (KeyError, ValueError):
                continue
            if abs((post_date - target).days) > 2:
                continue
            for split in candidate.get("splits") or []:
                if split.get("account_id") != from_id:
                    continue
                split_amount = round(float(split.get("amount", 0)), 2)
                if abs(split_amount - expected) < 0.005:
                    return {
                        "transaction_id": candidate.get("id"),
                        "description": candidate.get("description"),
                        "post_date": candidate.get("post_date"),
                    }
        if len(rows) < 500:
            break
        offset += 500
    return None


async def attempt_post(txn: dict[str, Any], to_id: str | None, force: bool = False) -> dict[str, Any]:
    """
    Post (or re-post) one transaction. Never raises for expected failures; returns a dict:
    {ok, code?, error?, detail?} on failure, {ok, gnucash_tx_id, action, to_account_id} on success.
    The row is always released from the `posting` claim, even on an unexpected error.
    """
    transaction_id = txn["id"]
    # A posted transaction is final: never edit or re-post it.
    if txn.get("gnucash_tx_id") or txn.get("post_status") == "posted":
        return {
            "ok": False,
            "code": "already_posted",
            "transaction_id": transaction_id,
            "error": "Already posted to gnucash-web; it is final there.",
        }
    from_id = txn.get("mapped_from_account_id") or txn.get("from_account_id")
    if not from_id:
        return {"ok": False, "code": "no_from", "error": "No gnucash account mapped for this card. Set it on the Settings page."}
    to_id = to_id or txn.get("to_account_id")
    if not to_id:
        return {"ok": False, "code": "no_to", "error": "Choose a To account before posting (currently Uncategorised)."}

    try:
        client = gnucash_client()
        accounts = await load_gnucash_accounts()
    except GnucashError as exc:
        return {"ok": False, "code": "gnucash", "error": str(exc)}

    valid = {a["id"] for a in postable_accounts(accounts)}
    if from_id not in valid:
        return {"ok": False, "code": "missing_from", "error": "The mapped From account is missing in gnucash-web. Re-map it on the Settings page."}
    if to_id not in valid:
        return {"ok": False, "code": "missing_to", "error": "The chosen To account is missing in gnucash-web."}

    if not force:
        try:
            duplicate = await find_possible_duplicate(client, txn, from_id)
        except GnucashError:
            duplicate = None
        if duplicate:
            return {
                "ok": False,
                "code": "duplicate",
                "detail": duplicate,
                "error": f"Possible duplicate: “{duplicate['description']}” on {duplicate['post_date']}",
            }

    if not await claim_transaction_for_posting(settings.DB_PATH, transaction_id):
        return {"ok": False, "code": "busy", "transaction_id": transaction_id, "error": "This transaction is already being posted."}

    try:
        try:
            gnucash_tx_id, action, adopted = await _submit_transaction(client, txn, from_id, to_id)
        except GnucashAuthError as exc:
            await set_transaction_post_failed(settings.DB_PATH, transaction_id, str(exc))
            return {"ok": False, "code": "auth", "transaction_id": transaction_id, "error": str(exc)}
        except GnucashValidationError as exc:
            await set_transaction_post_failed(settings.DB_PATH, transaction_id, str(exc))
            return {"ok": False, "code": "validation", "transaction_id": transaction_id, "error": str(exc)}
        except GnucashError as exc:
            await set_transaction_post_failed(settings.DB_PATH, transaction_id, str(exc))
            return {"ok": False, "code": "gnucash", "transaction_id": transaction_id, "error": str(exc)}

        if adopted:
            # Record the accounts the ledger actually used, not the retry's choice.
            adopted_from, adopted_to = _accounts_from_adopted(adopted, from_id)
            from_id = adopted_from or from_id
            to_id = adopted_to or to_id

        await set_transaction_posted(settings.DB_PATH, transaction_id, to_id, gnucash_tx_id, from_account_id=from_id)
        await record_confirmed_rule(
            settings.DB_PATH,
            txn.get("merchant_key") or normalise_merchant(txn.get("counterparty")),
            card_scope(txn.get("sender_header"), txn.get("account_last4")),
            to_id,
        )
        return {
            "ok": True,
            "transaction_id": transaction_id,
            "gnucash_tx_id": gnucash_tx_id,
            "action": action,
            "to_account_id": to_id,
        }
    except Exception as exc:  # noqa: BLE001 - never leave a row stuck in `posting`
        logger.exception("Unexpected error posting transaction %s", transaction_id)
        try:
            await set_transaction_post_failed(
                settings.DB_PATH, transaction_id, f"Unexpected error: {exc}"
            )
        except Exception:  # pragma: no cover - last-resort logging
            logger.exception("Could not release the posting claim for transaction %s", transaction_id)
        return {
            "ok": False,
            "code": "unexpected",
            "transaction_id": transaction_id,
            "error": "Unexpected error while posting; the transaction can be retried.",
        }


async def learn_from_history() -> dict[str, Any]:
    """Read past 2-split gnucash transactions and record merchant → other-account hints."""
    mapping = await get_gnucash_account_map(settings.DB_PATH)
    from_ids = set(mapping.values())
    if not from_ids:
        return {"learned": 0, "message": "Map at least one card to a gnucash account first."}

    client = gnucash_client()
    counts: dict[tuple[str, str], int] = {}
    offset = 0
    scanned = 0
    while offset < 10000:
        rows = await client.list_transactions(limit=500, offset=offset)
        if not rows:
            break
        scanned += len(rows)
        for tx in rows:
            # Do not learn from transactions sms_webhook posted itself: they are already recorded
            # as confirmed choices, and re-counting them would inflate the learned hints.
            if "sms-event:" in (tx.get("notes") or ""):
                continue
            splits = tx.get("splits") or []
            if len(splits) != 2:
                continue
            from_split = next((s for s in splits if s.get("account_id") in from_ids), None)
            if not from_split:
                continue
            other = next((s for s in splits if s.get("account_id") != from_split.get("account_id")), None)
            if not other:
                continue
            merchant = normalise_merchant(tx.get("description"))
            if not merchant:
                continue
            key = (merchant, other["account_id"])
            counts[key] = counts.get(key, 0) + 1
        if len(rows) < 500:
            break
        offset += 500

    hints = await record_learned_rules(settings.DB_PATH, counts)
    return {
        "learned": hints,
        "transactions_scanned": scanned,
        "message": f"Recorded {hints} merchant hints from {len(from_ids)} mapped account(s).",
    }

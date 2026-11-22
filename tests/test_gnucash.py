"""Unit tests for the SMS → gnucash-web bridge (no network; httpx.MockTransport)."""

import httpx
import pytest

from app.gnucash import (
    build_account_paths,
    build_transaction_payload,
    card_scope,
    choose_suggestion,
    display_merchant,
    ist_date,
    normalise_merchant,
    postable_accounts,
)
from app.gnucash_client import GnucashClient, GnucashError


@pytest.mark.parametrize(
    "counterparty, expected",
    [
        ("AMAZON PAY IN G", "AMAZON PAY"),
        ("AMAZON PAY IN E", "AMAZON PAY"),
        ("RSP*SWIGGY PVT LTD FOO", "SWIGGY PVT LTD FOO"),
        ("RAZ*Furlenc", "FURLENC"),
        ("*4521@ybl", "*4521@YBL"),
        ("*3190@okbizaxi", "*3190@OKBIZAXI"),
        ("Swiggy Instamart", "SWIGGY INSTAMART"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalise_merchant(counterparty, expected):
    assert normalise_merchant(counterparty) == expected


def test_display_merchant():
    assert display_merchant("AMAZON PAY IN G") == "Amazon Pay"
    assert display_merchant("*4521@ybl") == "UPI *4521@YBL"
    assert display_merchant(None) == ""


def test_ist_date_across_midnight():
    # 20:10 UTC on 17 Nov is 01:40 IST on 18 Nov.
    assert ist_date("2026-11-17T20:10:00Z") == "2026-11-18"
    assert ist_date("2026-11-17T18:29:00+00:00") == "2026-11-17"
    assert ist_date(None, fallback="2026-11-16T10:00:00+00:00") == "2026-11-16"


def test_build_account_paths():
    raw = [
        {"id": "a", "name": "ICICI Amazon Pay", "parent_id": "c", "account_type": "credit", "placeholder": False, "hidden": False},
        {"id": "c", "name": "Credit Cards", "parent_id": "l", "account_type": "liability", "placeholder": False, "hidden": False},
        {"id": "l", "name": "Liabilities", "parent_id": None, "account_type": "liability", "placeholder": False, "hidden": False},
        {"id": "p", "name": "Placeholder", "parent_id": None, "account_type": "expense", "placeholder": True, "hidden": False},
    ]
    accounts = build_account_paths(raw)
    paths = {a["id"]: a["path"] for a in accounts}
    assert paths["a"] == "Liabilities:Credit Cards:ICICI Amazon Pay"
    assert paths["l"] == "Liabilities"
    leaves = {a["id"]: a["leaf"] for a in accounts}
    assert leaves == {"a": True, "c": False, "l": False, "p": True}
    postable = [a["id"] for a in postable_accounts(accounts)]
    # Leaf accounts only, and never a placeholder.
    assert postable == ["a"]


def _sample_txn(direction="debit", amount=349.0):
    return {
        "id": 7,
        "event_id": "544ab1f0-7405-4d07-ad18-36ff0d16b217",
        "occurred_at": "2026-11-17T20:10:00Z",
        "direction": direction,
        "amount": amount,
        "counterparty": "AMAZON PAY IN G",
        "reference": "123456789012",
        "raw_text": "INR 349.00 spent using ICICI Bank Card XX7531 on 18-Nov-26 on AMAZON PAY IN G.",
        "account_last4": "7531",
        "sender_header": "ICICIT",
        "bank": "ICICI Bank",
        "account_name": "ICICI Amazon Pay",
    }


def test_build_payload_debit_signs_and_ist_date():
    payload = build_transaction_payload(_sample_txn("debit"), "to-acc", "from-acc", from_memo="ICICI Amazon Pay ••7531")
    assert payload["post_date"] == "2026-11-18"
    assert payload["description"] == "Amazon Pay"
    assert payload["num"] == "123456789012"
    assert payload["notes"].endswith("sms-event:544ab1f0-7405-4d07-ad18-36ff0d16b217")
    from_split, to_split = payload["splits"]
    assert from_split["account_id"] == "from-acc"
    assert from_split["amount"] == "-349.00"
    assert from_split["reconciled_state"] == "c"
    assert to_split["account_id"] == "to-acc"
    assert to_split["amount"] == "349.00"
    # Splits sum to exactly zero at 2 decimals.
    assert round(float(from_split["amount"]) + float(to_split["amount"]), 2) == 0.0


def test_build_payload_credit_reverses_signs():
    payload = build_transaction_payload(_sample_txn("credit", 100.5), "to-acc", "from-acc")
    from_split, to_split = payload["splits"]
    assert from_split["amount"] == "100.50"
    assert to_split["amount"] == "-100.50"


def test_build_payload_truncates_long_description_and_num():
    txn = _sample_txn()
    txn["counterparty"] = "X" * 400
    txn["reference"] = "9" * 80
    payload = build_transaction_payload(txn, "to", "from")
    assert len(payload["description"]) == 255
    assert len(payload["num"]) == 50


def test_build_payload_missing_reference_is_null():
    txn = _sample_txn()
    txn["reference"] = ""
    assert build_transaction_payload(txn, "to", "from")["num"] is None


def _rule(merchant, scope, source, account, hits):
    return {"merchant_key": merchant, "scope": scope, "source": source, "gnucash_account_id": account, "hits": hits}


def test_suggestion_order():
    scope = card_scope("ICICIT", "7531")
    merchant = "AMAZON PAY"
    label = "Amazon Pay"

    # Step 1: a confirmed choice on this card wins over everything else.
    rules = [
        _rule(merchant, scope, "confirmed", "chosen-on-card", 2),
        _rule(merchant, scope, "confirmed", "other-choice", 1),
        _rule(merchant, "*", "learned", "learned-acc", 9),
    ]
    defaults = {scope: ("usual-acc", 5)}
    assert choose_suggestion(merchant, label, scope, rules, defaults) == (
        "chosen-on-card", "chosen 2 of 3 times for Amazon Pay"
    )

    # Step 1b: a confirmed choice on another card is the "any card" fallback.
    rules = [_rule(merchant, "*", "confirmed", "any-card", 2)]
    assert choose_suggestion(merchant, label, scope, rules, {}) == (
        "any-card", "chosen 2 of 2 times for Amazon Pay"
    )

    # Step 2: no confirmed choice -> learned history is used.
    rules = [_rule(merchant, "*", "learned", "learned-acc", 3)]
    account, reason = choose_suggestion(merchant, label, scope, rules, defaults)
    assert account == "learned-acc"
    assert reason == "from your gnucash history (3× for Amazon Pay)"

    # Step 3: nothing for the merchant -> the card's usual destination.
    assert choose_suggestion(merchant, label, scope, [], defaults) == ("usual-acc", "usual for this card (5×)")

    # Step 4: nothing at all -> Uncategorised (no account).
    account, reason = choose_suggestion(merchant, label, scope, [], {})
    assert account is None
    assert reason == "Uncategorised"


def _client(handler):
    return GnucashClient("http://gnucash.test", "gcw_test", transport=httpx.MockTransport(handler))


def test_client_returns_accounts_and_sends_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[{"id": "a", "name": "Cash"}])

    import asyncio

    accounts = asyncio.run(_client(handler).list_accounts())
    assert accounts == [{"id": "a", "name": "Cash"}]
    assert seen["auth"] == "Bearer gcw_test"


@pytest.mark.parametrize("status", [401, 403])
def test_client_rejects_bad_token(status):
    import asyncio

    def handler(request):
        return httpx.Response(status, json={"error": "nope"})

    with pytest.raises(GnucashError, match="rejected the API token"):
        asyncio.run(_client(handler).list_accounts())


def test_client_surfaces_validation_error():
    import asyncio

    def handler(request):
        return httpx.Response(400, json={"error": "Transaction is not balanced! Sum of splits is 1.00, must be 0.00"})

    with pytest.raises(GnucashError, match="not balanced"):
        asyncio.run(_client(handler).create_transaction({}))


def test_client_reports_not_found():
    import asyncio

    def handler(request):
        return httpx.Response(404, json={"error": "Transaction not found"})

    with pytest.raises(GnucashError, match="may have been deleted"):
        asyncio.run(_client(handler).create_transaction({}))


def test_client_search_passes_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    import asyncio

    asyncio.run(_client(handler).list_transactions(search="sms-event:abc", limit=1))
    assert "search=sms-event%3Aabc" in seen["url"]
    assert "limit=1" in seen["url"]

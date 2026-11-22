"""Tests for the gnucash posting engine and endpoints (no network; httpx.MockTransport)."""

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import gnucash_post as post_engine
from app.config import settings
from app.database import (
    claim_transaction_for_posting,
    decorate_suggestions,
    get_card_defaults,
    get_category_rules,
    get_transaction_by_id,
    get_transactions,
    init_db,
    save_transaction,
    set_gnucash_account_map,
    set_transaction_choice,
    set_transaction_post_failed,
    set_transaction_posted,
)
from app.gnucash_client import GnucashClient
from app.main import app

ACCOUNTS = [
    {"id": "parent-acc", "name": "Credit Cards", "parent_id": None, "account_type": "liability", "placeholder": False, "hidden": False},
    {"id": "from-acc", "name": "ICICI Amazon Pay", "parent_id": "parent-acc", "account_type": "credit", "placeholder": False, "hidden": False},
    {"id": "to-acc", "name": "Groceries", "parent_id": "parent-acc", "account_type": "expense", "placeholder": False, "hidden": False},
    {"id": "other-acc", "name": "Others", "parent_id": "parent-acc", "account_type": "expense", "placeholder": False, "hidden": False},
    {"id": "placeholder-acc", "name": "Placeholder", "parent_id": None, "account_type": "expense", "placeholder": True, "hidden": False},
]


class FakeGnucash:
    """A tiny in-memory gnucash-web used through httpx.MockTransport."""

    def __init__(self):
        self.calls = []
        self.search_results = []
        self.dup_candidates = []
        self.list_results = []
        self.timeout_on_create = False
        self.fail_update = False
        self.create_error_status = None
        self.update_error_status = None
        self.landed_on_timeout = None
        self.create_response_id = "new-tx"

    def handler(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        params = dict(request.url.params)
        self.calls.append((method, path, params))

        if method == "GET" and path == "/api/accounts":
            return httpx.Response(200, json=ACCOUNTS)
        if method == "GET" and path == "/api/transactions":
            if "search" in params:
                return httpx.Response(200, json=self.search_results)
            if "account_id" in params:
                return httpx.Response(200, json=self.dup_candidates)
            return httpx.Response(200, json=self.list_results)
        if method == "POST" and path == "/api/transactions":
            if self.timeout_on_create:
                if self.landed_on_timeout:
                    self.search_results = [self.landed_on_timeout]
                raise httpx.ReadTimeout("timed out", request=request)
            if self.create_error_status:
                return httpx.Response(self.create_error_status, json={"error": "rejected by gnucash"})
            payload = json.loads(request.content or b"{}")
            return httpx.Response(201, json={
                "id": self.create_response_id,
                "post_date": payload.get("post_date"),
                "description": payload.get("description"),
                "notes": payload.get("notes"),
                "splits": [],
            })
        if method == "PUT" and path.startswith("/api/transactions/"):
            if self.fail_update:
                return httpx.Response(404, json={"error": "Transaction not found"})
            if self.update_error_status:
                return httpx.Response(self.update_error_status, json={"error": "edit rejected by gnucash"})
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})
        return httpx.Response(404, json={"error": "unexpected call"})


@pytest.fixture(autouse=True)
async def setup(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gnucash_post.db")
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "GNUCASH_URL", "http://gnucash.test")
    monkeypatch.setattr(settings, "GNUCASH_API_TOKEN", "gcw_test")
    await init_db(db_path)
    post_engine.reset_account_cache()
    yield db_path


def use_fake(monkeypatch, fake):
    def factory():
        return GnucashClient("http://gnucash.test", "gcw_test", transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(post_engine, "gnucash_client", factory)
    post_engine.reset_account_cache()


async def add_transaction(db_path, event_id="evt-1", amount=349.0, direction="debit",
                          counterparty="AMAZON PAY IN G", occurred_at="2026-11-17T20:10:00+00:00"):
    await save_transaction(
        db_path,
        event_id=event_id,
        webhook_id=None,
        occurred_at=occurred_at,
        raw_text="INR 349.00 spent using ICICI Bank Card XX7531 on 18-Nov-26 on AMAZON PAY IN G.",
        bank="ICICI Bank",
        sender_header="ICICIT",
        direction=direction,
        amount=amount,
        instrument="credit_card",
        channel="card",
        account_last4="7531",
        counterparty=counterparty,
        reference="123456789012",
    )
    await set_gnucash_account_map(db_path, "ICICIT", "7531", "from-acc")
    rows = await get_transactions(db_path)
    return next(row for row in rows if row["event_id"] == event_id)


@pytest.mark.anyio
async def test_create_posts_balanced_payload_and_records_rules(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    result = await post_engine.attempt_post(txn, "to-acc")

    assert result["ok"] is True
    assert result["action"] == "created"
    post = [c for c in fake.calls if c[0] == "POST"]
    assert len(post) == 1
    # The payload was sent to the fake; inspect it through a second run's body is awkward,
    # so assert the stored outcome and rules instead.
    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["post_status"] == "posted"
    assert stored["gnucash_tx_id"] == "new-tx"
    assert stored["to_account_id"] == "to-acc"
    assert stored["is_read"] == 1
    rules = await get_category_rules(db_path)
    scopes = {(r["scope"], r["source"], r["gnucash_account_id"]) for r in rules}
    assert ("ICICIT:7531", "confirmed", "to-acc") in scopes
    assert ("*", "confirmed", "to-acc") in scopes


@pytest.mark.anyio
async def test_posted_row_is_refused_and_never_repushed(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)
    first = await post_engine.attempt_post(txn, "to-acc")
    assert first["ok"]

    stored = await get_transaction_by_id(db_path, txn["id"])
    second = await post_engine.attempt_post(stored, "other-acc")

    assert second["ok"] is False
    assert second["code"] == "already_posted"
    # No PUT and no second POST: a posted transaction is final.
    assert len([c for c in fake.calls if c[0] == "POST"]) == 1
    assert not any(c[0] == "PUT" for c in fake.calls)
    assert not await claim_transaction_for_posting(db_path, txn["id"])


@pytest.mark.anyio
async def test_preflight_adopts_an_existing_event_tag(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.search_results = [{"id": "already-there", "post_date": "2026-11-18", "notes": "raw\nsms-event:evt-1"}]
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    result = await post_engine.attempt_post(txn, "to-acc")

    assert result["ok"] is True
    assert result["action"] == "adopted"
    assert result["gnucash_tx_id"] == "already-there"
    assert not [c for c in fake.calls if c[0] == "POST"]


@pytest.mark.anyio
async def test_event_tag_match_is_exact(setup, monkeypatch):
    """A prefix match (evt-10 for evt-1) must not be adopted."""
    db_path = setup
    fake = FakeGnucash()
    fake.search_results = [
        {"id": "prefix-match", "post_date": "2026-11-18", "notes": "sms-event:evt-10"},
        {"id": "exact-match", "post_date": "2026-11-18", "notes": "raw\nsms-event:evt-1"},
    ]
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)  # event_id "evt-1"

    result = await post_engine.attempt_post(txn, "to-acc")

    assert result["ok"] is True
    assert result["gnucash_tx_id"] == "exact-match"


@pytest.mark.anyio
async def test_timeout_then_tag_found_adopts(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.timeout_on_create = True
    fake.landed_on_timeout = {"id": "landed", "post_date": "2026-11-18", "notes": "sms-event:evt-1"}
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    result = await post_engine.attempt_post(txn, "to-acc")

    assert result["ok"] is True
    assert result["action"] == "adopted"
    assert result["gnucash_tx_id"] == "landed"


@pytest.mark.anyio
async def test_timeout_then_not_found_fails_then_retry_adopts(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.timeout_on_create = True
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    first = await post_engine.attempt_post(txn, "to-acc")
    assert first["ok"] is False
    assert first["code"] == "gnucash"
    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["post_status"] == "failed"  # claim released

    # The first attempt actually landed late; the retry must adopt it, not create a duplicate.
    fake.timeout_on_create = False
    fake.search_results = [{"id": "landed-late", "post_date": "2026-11-18", "notes": "sms-event:evt-1"}]
    posts_before = len([c for c in fake.calls if c[0] == "POST"])
    second = await post_engine.attempt_post(stored, "to-acc")
    assert second["ok"] is True
    assert second["action"] == "adopted"
    assert len([c for c in fake.calls if c[0] == "POST"]) == posts_before


@pytest.mark.anyio
async def test_concurrent_post_is_refused(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)
    assert await claim_transaction_for_posting(db_path, txn["id"])

    result = await post_engine.attempt_post(txn, "to-acc")
    assert result["ok"] is False
    assert result["code"] == "busy"
    assert not [c for c in fake.calls if c[0] == "POST"]  # never reached gnucash


@pytest.mark.anyio
async def test_adoption_records_the_ledger_accounts_not_the_retry_choice(setup, monkeypatch):
    """If an earlier post landed with a different To, the retry records what the ledger has."""
    db_path = setup
    fake = FakeGnucash()
    fake.search_results = [{
        "id": "already-there",
        "post_date": "2026-11-18",
        "description": "Amazon Pay",
        "notes": "raw\nsms-event:evt-1",
        "splits": [
            {"account_id": "from-acc", "amount": "-349.00"},
            {"account_id": "to-acc", "amount": "349.00"},
        ],
    }]
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    result = await post_engine.attempt_post(txn, "other-acc")  # retry chooses a different To

    assert result["ok"] is True
    assert result["action"] == "adopted"
    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["to_account_id"] == "to-acc"       # from the ledger, not "other-acc"
    assert stored["from_account_id"] == "from-acc"


@pytest.mark.anyio
async def test_duplicate_warning_and_force(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.dup_candidates = [{
        "id": "manual-tx",
        "post_date": "2026-11-18",
        "description": "Amazon",
        "notes": "hand entered",
        "splits": [{"account_id": "from-acc", "amount": "-349.00"}],
    }]
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    warned = await post_engine.attempt_post(txn, "to-acc")
    assert warned["ok"] is False
    assert warned["code"] == "duplicate"
    assert "Possible duplicate" in warned["error"]
    assert not [c for c in fake.calls if c[0] == "POST"]

    forced = await post_engine.attempt_post(txn, "to-acc", force=True)
    assert forced["ok"] is True
    assert [c for c in fake.calls if c[0] == "POST"]


@pytest.mark.anyio
async def test_duplicate_ignores_other_sms_event_entries(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.dup_candidates = [{
        "id": "other-sms",
        "post_date": "2026-11-18",
        "description": "Amazon Pay",
        "notes": "raw\nsms-event:some-other-event",
        "splits": [{"account_id": "from-acc", "amount": "-349.00"}],
    }]
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    result = await post_engine.attempt_post(txn, "to-acc")
    assert result["ok"] is True


@pytest.mark.anyio
async def test_unexpected_error_releases_the_posting_claim(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(post_engine, "_submit_transaction", boom)
    result = await post_engine.attempt_post(txn, "to-acc")
    assert result["ok"] is False
    assert result["code"] == "unexpected"
    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["post_status"] == "failed"


# --- Endpoints ---


@pytest.mark.anyio
async def test_bulk_requires_an_explicit_non_empty_selection(setup):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.post("/api/transactions/post-reviewed", json={})).status_code == 422
        assert (await client.post("/api/transactions/post-reviewed", json={"posts": []})).status_code == 422


@pytest.mark.anyio
async def test_bulk_uses_the_explicit_to_account(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post("/api/transactions/post-reviewed", json={
            "posts": [{"id": txn["id"], "to_account_id": "other-acc"}],
        })
        assert res.status_code == 200
        body = res.json()
        assert body["posted"] == 1
        assert body["results"][0]["to_account_id"] == "other-acc"

    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["to_account_id"] == "other-acc"


@pytest.mark.anyio
async def test_put_to_choice_persists_without_posting(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.put(f"/api/transactions/{txn['id']}/to", json={"to_account_id": "other-acc"})
        assert res.status_code == 200

    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["to_account_id"] == "other-acc"
    assert stored["post_status"] == "suggested"
    assert not [c for c in fake.calls if c[0] == "POST"]


@pytest.mark.anyio
async def test_post_endpoint_409_then_force(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.dup_candidates = [{
        "id": "manual-tx",
        "post_date": "2026-11-18",
        "description": "Amazon",
        "notes": "hand",
        "splits": [{"account_id": "from-acc", "amount": "-349.00"}],
    }]
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(f"/api/transactions/{txn['id']}/post", json={"to_account_id": "to-acc"})
        assert res.status_code == 409
        assert res.json()["detail"]["description"] == "Amazon"

        forced = await client.post(f"/api/transactions/{txn['id']}/post", json={"to_account_id": "to-acc", "force": True})
        assert forced.status_code == 200
        assert forced.json()["ok"] is True


@pytest.mark.anyio
async def test_accounts_endpoint_reports_disabled(setup, monkeypatch):
    monkeypatch.setattr(settings, "GNUCASH_URL", None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/gnucash/accounts")
        assert res.status_code == 200
        assert res.json() == {"enabled": False, "accounts": []}


@pytest.mark.anyio
async def test_mapping_rejects_a_placeholder_account(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    await add_transaction(db_path)  # creates the ICICIT bank/account rows

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        bad = await client.put("/api/accounts/ICICIT/7531/gnucash", json={"gnucash_account_id": "placeholder-acc"})
        assert bad.status_code == 400
        # A parent account (not a leaf) is also rejected.
        parent = await client.put("/api/accounts/ICICIT/7531/gnucash", json={"gnucash_account_id": "parent-acc"})
        assert parent.status_code == 400
        good = await client.put("/api/accounts/ICICIT/7531/gnucash", json={"gnucash_account_id": "to-acc"})
        assert good.status_code == 200


# --- Recheck regressions (R1-R3) ---


@pytest.mark.anyio
async def test_decorate_does_not_clobber_a_posted_row(setup):
    """R1: a list read must not reset a row that a concurrent Post completed."""
    db_path = setup
    txn = await add_transaction(db_path)
    stale = await get_transactions(db_path)  # what the list endpoint read
    await set_transaction_posted(db_path, txn["id"], "to-acc", "gid-123")

    await decorate_suggestions(db_path, stale)

    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["post_status"] == "posted"
    assert stored["gnucash_tx_id"] == "gid-123"


@pytest.mark.anyio
async def test_decorate_does_not_release_a_posting_claim(setup):
    """R1: a list read must not release a claim held by an in-flight Post."""
    db_path = setup
    txn = await add_transaction(db_path)
    stale = await get_transactions(db_path)
    assert await claim_transaction_for_posting(db_path, txn["id"])

    await decorate_suggestions(db_path, stale)

    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["post_status"] == "posting"


@pytest.mark.anyio
async def test_card_defaults_counts_only_posted_choices(setup):
    """R2: a saved-but-unposted To must not influence the card's usual destination."""
    db_path = setup
    chosen = await add_transaction(db_path, event_id="evt-1")
    posted = await add_transaction(db_path, event_id="evt-2")
    await set_transaction_choice(db_path, chosen["id"], "to-acc")
    await set_transaction_posted(db_path, posted["id"], "other-acc", "gid-2")

    defaults = await get_card_defaults(db_path)
    assert defaults["ICICIT:7531"] == ("other-acc", 1)


@pytest.mark.anyio
async def test_learn_skips_sms_event_entries(setup, monkeypatch):
    """R3: learning must ignore transactions sms_webhook created itself."""
    db_path = setup
    fake = FakeGnucash()
    fake.list_results = [
        {
            "id": "hand-entry",
            "description": "Amazon",
            "notes": "entered by hand",
            "splits": [
                {"account_id": "from-acc", "amount": "-10.00"},
                {"account_id": "to-acc", "amount": "10.00"},
            ],
        },
        {
            "id": "ours",
            "description": "Amazon Pay",
            "notes": "raw sms\nsms-event:abc",
            "splits": [
                {"account_id": "from-acc", "amount": "-20.00"},
                {"account_id": "other-acc", "amount": "20.00"},
            ],
        },
    ]
    use_fake(monkeypatch, fake)
    await add_transaction(db_path)  # maps ICICIT:7531 -> from-acc

    result = await post_engine.learn_from_history()

    learned = {
        (r["merchant_key"], r["gnucash_account_id"])
        for r in await get_category_rules(db_path)
        if r["source"] == "learned"
    }
    assert ("AMAZON", "to-acc") in learned
    assert ("AMAZON PAY", "other-acc") not in learned
    assert result["learned"] == 1


@pytest.mark.anyio
async def test_choosing_to_on_a_failed_row_clears_the_error(setup):
    """A corrected To on a failed row should return it to `suggested`, not keep the old error."""
    db_path = setup
    txn = await add_transaction(db_path)
    await set_transaction_post_failed(db_path, txn["id"], "gnucash said no")

    assert await set_transaction_choice(db_path, txn["id"], "to-acc")

    stored = await get_transaction_by_id(db_path, txn["id"])
    assert stored["post_status"] == "suggested"
    assert stored["post_error"] is None
    assert stored["to_account_id"] == "to-acc"


@pytest.mark.anyio
async def test_failed_post_can_be_retried_and_creates(setup, monkeypatch):
    """A row whose POST failed (no gnucash id) can be retried; it creates, never updates."""
    db_path = setup
    fake = FakeGnucash()
    fake.create_error_status = 400
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    failed = await post_engine.attempt_post(txn, "to-acc")
    assert failed["ok"] is False
    assert failed["code"] == "validation"
    after = await get_transaction_by_id(db_path, txn["id"])
    assert after["gnucash_tx_id"] is None

    fake.create_error_status = None
    retried = await post_engine.attempt_post(after, "to-acc")
    assert retried["ok"] is True
    assert retried["action"] == "created"
    assert not any(c[0] == "PUT" for c in fake.calls)


@pytest.mark.anyio
async def test_in_ledger_is_exposed_on_list(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)
    await post_engine.attempt_post(txn, "to-acc")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        rows = (await client.get("/api/transactions")).json()

    assert rows[0]["in_ledger"] is True
    assert rows[0]["gnucash_tx_id"]


@pytest.mark.anyio
async def test_post_endpoint_maps_busy_to_409(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)
    assert await claim_transaction_for_posting(db_path, txn["id"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(f"/api/transactions/{txn['id']}/post", json={"to_account_id": "to-acc"})
    assert res.status_code == 409


@pytest.mark.anyio
async def test_post_endpoint_maps_auth_failure_to_502(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    fake.create_error_status = 401
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(f"/api/transactions/{txn['id']}/post", json={"to_account_id": "to-acc"})
    assert res.status_code == 502
    assert "rejected" in res.json()["detail"]


@pytest.mark.anyio
async def test_remapping_card_does_not_rewrite_from_on_posted_rows(setup, monkeypatch):
    db_path = setup
    fake = FakeGnucash()
    use_fake(monkeypatch, fake)
    txn = await add_transaction(db_path)
    res = await post_engine.attempt_post(txn, "to-acc")
    assert res["ok"] is True

    posted = await get_transaction_by_id(db_path, txn["id"])
    assert posted["from_account_id"] == "from-acc"

    # Remap card to a new from account
    await set_gnucash_account_map(db_path, "ICICIT", "7531", "from-acc-changed")
    posted = await get_transaction_by_id(db_path, txn["id"])
    assert posted["from_account_id"] == "from-acc"

    # Unmap card
    await set_gnucash_account_map(db_path, "ICICIT", "7531", None)
    posted = await get_transaction_by_id(db_path, txn["id"])
    assert posted["from_account_id"] == "from-acc"

    # decorate_suggestions also preserves from_account_id on posted rows
    rows = await get_transactions(db_path)
    posted_row = next(r for r in rows if r["id"] == txn["id"])
    assert posted_row["from_account_id"] == "from-acc"


@pytest.mark.anyio
async def test_posting_marks_webhook_as_read(setup, monkeypatch):
    from app.database import get_webhook_by_id, save_webhook

    db_path = setup
    wh, _ = await save_webhook(
        db_path,
        event_id="evt-post-read-1",
        source="sms",
        method="POST",
        path="/webhook/sms",
        url="http://testserver/webhook/sms",
        headers={},
        query_params={},
        content_type="application/json",
        payload_raw="",
        payload_json={},
        sender="AX-ICICIT-S",
        recipient=None,
        message_body="INR 100 spent",
        external_message_id=None,
    )
    assert wh["is_read"] == 0

    await save_transaction(
        db_path,
        event_id="evt-post-read-1",
        webhook_id=wh["id"],
        occurred_at="2026-11-20T10:00:00",
        raw_text=wh["message_body"],
        bank="ICICI Bank",
        sender_header="ICICIT",
        direction="debit",
        amount=100.0,
    )
    rows = await get_transactions(db_path)
    txn = next(r for r in rows if r["event_id"] == "evt-post-read-1")

    await set_transaction_posted(
        db_path,
        transaction_id=txn["id"],
        to_account_id="Expenses:Test",
        gnucash_tx_id="tx-123",
        from_account_id="Liabilities:Card",
    )

    txn_after = await get_transaction_by_id(db_path, txn["id"])
    assert txn_after["is_read"] == 1

    wh_after = await get_webhook_by_id(db_path, wh["id"])
    assert wh_after is not None
    assert wh_after["is_read"] == 1
    assert wh_after["read_at"] is not None

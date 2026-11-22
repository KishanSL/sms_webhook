"""Credit-card statement parsing and the Cards API."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import init_db, save_card_statement, save_transaction
from app.main import app
from app.statement_parser import parse_card_statement
from app.txn_parser import DEFAULT_BANKS

SBI_STATEMENT = (
    "E-statement of SBI Credit Card ending XX24 dated 16/11/2026 has been mailed. "
    "If not received, SMS ENRS to 5676791. Total Amt Due Rs 18450; Min Amt Due Rs 369; "
    "Payable by 06/12/2026."
)


def test_parse_card_statement():
    assert parse_card_statement("VM-MYSBIC-S", SBI_STATEMENT, DEFAULT_BANKS) == {
        "bank": "SBI Card",
        "sender_header": "MYSBIC",
        "account_last4": "24",
        "statement_date": "2026-11-16",
        "total_due": 18450.0,
        "min_due": 369.0,
        "due_date": "2026-12-06",
    }
    # Axis Bank
    axis_msg = (
        "Your statement for Axis Bank Credit Card no. XX8642 is generated. "
        "Due on: 08-12-26 Total amt: INR Dr. 42310.55 Min amt due: INR Dr. 912.00"
    )
    assert parse_card_statement("CP-AXISBK-S", axis_msg, DEFAULT_BANKS) == {
        "bank": "Axis Bank",
        "sender_header": "AXISBK",
        "account_last4": "8642",
        "statement_date": None,
        "total_due": 42310.55,
        "min_due": 912.0,
        "due_date": "2026-12-08",
    }
    # OneCard
    onecard_msg = "Your BOBCARD One Credit Card bill of Rs. 0.00 is ready. Pay by 05 Dec, 2026 via the OneCard app"
    assert parse_card_statement("VM-BOBONE-S", onecard_msg, DEFAULT_BANKS) == {
        "bank": "OneCard",
        "sender_header": "BOBONE",
        "account_last4": None,
        "statement_date": None,
        "total_due": 0.0,
        "min_due": None,
        "due_date": "2026-12-05",
    }
    # HDFC Bank
    hdfc_msg = "Statement Generated: For HDFC Bank Credit Card XX9012. View: mycards.hdfcbank.com Pay: hdfcbk.io/k/pAy"
    assert parse_card_statement("BP-HDFCBK-S", hdfc_msg, DEFAULT_BANKS) == {
        "bank": "HDFC Bank",
        "sender_header": "HDFCBK",
        "account_last4": "9012",
        "statement_date": None,
        "total_due": None,
        "min_due": None,
        "due_date": None,
    }
    # not a linked bank / not a statement
    assert parse_card_statement("AD-SWIGGY-S", SBI_STATEMENT, DEFAULT_BANKS) is None
    assert parse_card_statement("VM-MYSBIC-S", "Hello there", DEFAULT_BANKS) is None
    # CRED message must NOT parse as card statement
    cred_msg = "Your Axis credit card bill of Rs 75,837 is due on 08 Dec. Pay now on CRED: https://c-r-e-d.co/pay"
    assert parse_card_statement("AD-CREDIN-S", cred_msg, DEFAULT_BANKS) is None
    # HDFC message with 'statement' but without 'Statement Generated' must NOT parse as card statement
    assert parse_card_statement("BP-HDFCBK-S", "Dear Customer, your account statement is ready.", DEFAULT_BANKS) is None


def test_to_local_date_ist():
    from app.deps import to_local_date

    # Late night UTC (18 Nov 20:30 UTC) is early morning IST (19 Nov 02:00 IST)
    assert to_local_date("2026-11-18T20:30:00Z") == "2026-11-19"
    # Afternoon UTC (18 Nov 14:00 UTC) is evening IST (18 Nov 19:30 IST)
    assert to_local_date("2026-11-18T14:00:00Z") == "2026-11-18"
    assert to_local_date(None) is None
    # Epoch milliseconds string: 1763600000000 is 2025-11-19T22:13:20Z -> in IST (+05:30) is 2025-11-20
    assert to_local_date("1763600000000") == "2025-11-20"
    # Invalid strings return None rather than garbage substrings
    assert to_local_date("21/11/2026 10:00") is None
    assert to_local_date("not-a-date") is None


def test_compute_hdfc_due_date():
    from app.statement_parser import compute_hdfc_due_date

    assert compute_hdfc_due_date("2026-11-18") == "2026-12-08"
    assert compute_hdfc_due_date("2026-11-20") == "2026-12-10"
    assert compute_hdfc_due_date(None) is None
    assert compute_hdfc_due_date("invalid-date") is None


@pytest.fixture
async def cards_db(tmp_path, monkeypatch):
    db = str(tmp_path / "cards.db")
    monkeypatch.setattr(settings, "DB_PATH", db)
    await init_db(db)
    yield db


@pytest.mark.anyio
async def test_cards_endpoint_due_and_paid(cards_db):
    db = cards_db
    await save_card_statement(
        db, event_id="s1", webhook_id=1, bank="SBI Card", sender_header="MYSBIC",
        account_last4="24", statement_date="2026-11-16", total_due=18450.0, min_due=369.0,
        due_date="2026-12-06", raw_text=SBI_STATEMENT,
    )
    await save_transaction(
        db, event_id="t1", webhook_id=2, occurred_at="2026-11-17T00:00:00+00:00",
        raw_text="spend", bank="ICICI Bank", sender_header="ICICIT", direction="debit", amount=100.0,
        instrument="credit_card", channel="card", account_last4="7531", counterparty="X",
        balance=1400000.0, balance_type="limit",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        cards = (await client.get("/api/cards")).json()
        by_key = {f"{c['sender_header']}:{c['account_last4']}": c for c in cards}
        assert by_key["MYSBIC:24"]["total_due"] == 18450.0
        assert by_key["MYSBIC:24"]["min_due"] == 369.0
        assert by_key["MYSBIC:24"]["paid"] is False
        assert "ICICIT:7531" in by_key
        # Limit / available / utilisation are no longer part of the card payload.
        assert "total_limit" not in by_key["MYSBIC:24"]
        assert "available_limit" not in by_key["MYSBIC:24"]
        assert "utilisation" not in by_key["MYSBIC:24"]

        assert (await client.put("/api/cards/MYSBIC/24/paid", json={"paid": True})).status_code == 200

        sbi = next(c for c in (await client.get("/api/cards")).json() if c["sender_header"] == "MYSBIC")
        assert sbi["paid"] is True
        assert sbi["paid_at"] is not None
        assert sbi["total_due"] == 18450.0  # a paid bill still shows its amount

        # unknown card -> 404
        assert (await client.put("/api/cards/MYSBIC/99/paid", json={"paid": True})).status_code == 404


@pytest.mark.anyio
async def test_statement_sms_is_parsed_on_ingest(cards_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {"event_id": "s2", "sender": "VM-MYSBIC-S", "text": SBI_STATEMENT}
        assert (await client.post("/webhook/message487", json=payload)).status_code == 200
        cards = (await client.get("/api/cards")).json()
    assert any(c["sender_header"] == "MYSBIC" and c["total_due"] == 18450.0 for c in cards)


@pytest.mark.anyio
async def test_statement_without_card_number_can_be_marked_paid(cards_db):
    """A statement with no card number uses the '_' sentinel and is still markable as paid."""
    db = cards_db
    await save_card_statement(
        db, event_id="s3", webhook_id=1, bank="SBI Card", sender_header="MYSBIC",
        account_last4=None, statement_date="2026-11-16", total_due=100.0, min_due=10.0,
        due_date=None, raw_text="stmt",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        assert (await client.put("/api/cards/MYSBIC/_/paid", json={"paid": True})).status_code == 200
        card = next(c for c in (await client.get("/api/cards")).json() if c["sender_header"] == "MYSBIC")
    assert card["paid"] is True
    assert card["account_last4"] is None


@pytest.mark.anyio
async def test_axis_onecard_hdfc_statements_ingest_and_cards(cards_db):
    """Test Axis, OneCard, and HDFC statement ingestion and listing on /api/cards."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Ingest Axis statement
        axis_payload = {
            "event_id": "axis_s1",
            "sender": "CP-AXISBK-S",
            "text": "Your statement for Axis Bank Credit Card no. XX8642 is generated. Due on: 08-12-26 Total amt: INR Dr. 42310.55 Min amt due: INR Dr. 912.00",
        }
        res = await client.post("/webhook/message487", json=axis_payload)
        assert res.status_code == 200

        # Ingest OneCard statement
        onecard_payload = {
            "event_id": "onecard_s1",
            "sender": "VM-BOBONE-S",
            "text": "Your BOBCARD One Credit Card bill of Rs. 0.00 is ready. Pay by 05 Dec, 2026 via the OneCard app",
        }
        res = await client.post("/webhook/message487", json=onecard_payload)
        assert res.status_code == 200

        # Ingest HDFC statement
        hdfc_payload = {
            "event_id": "hdfc_s1",
            "sender": "BP-HDFCBK-S",
            "text": "Statement Generated: For HDFC Bank Credit Card XX9012. View: mycards.hdfcbank.com Pay: hdfcbk.io/k/pAy",
            "occurred_at": "2026-11-20T12:00:00+00:00",
        }
        res = await client.post("/webhook/message487", json=hdfc_payload)
        assert res.status_code == 200

        # Query /api/cards
        cards = (await client.get("/api/cards")).json()
        by_header = {c["sender_header"]: c for c in cards}

        # Axis card checks
        assert "AXISBK" in by_header
        axis_card = by_header["AXISBK"]
        assert axis_card["account_last4"] == "8642"
        assert axis_card["total_due"] == 42310.55
        assert axis_card["min_due"] == 912.0
        assert axis_card["due_date"] == "2026-12-08"
        assert axis_card["has_statement"] is True

        # OneCard checks
        assert "BOBONE" in by_header
        one_card = by_header["BOBONE"]
        assert one_card["account_last4"] is None
        assert one_card["total_due"] == 0.0
        assert one_card["due_date"] == "2026-12-05"
        assert one_card["has_statement"] is True

        # HDFC card checks
        assert "HDFCBK" in by_header
        hdfc_card = by_header["HDFCBK"]
        assert hdfc_card["account_last4"] == "9012"
        assert hdfc_card["has_statement"] is True
        assert hdfc_card["statement_date"] == "2026-11-20"
        assert hdfc_card["due_date"] == "2026-12-10"
        assert hdfc_card["total_due"] is None
        assert hdfc_card["webhook_id"] is not None

        # Verify marking HDFC paid works
        assert (await client.put("/api/cards/HDFCBK/9012/paid", json={"paid": True})).status_code == 200
        cards_after = (await client.get("/api/cards")).json()
        hdfc_after = next(c for c in cards_after if c["sender_header"] == "HDFCBK")
        assert hdfc_after["paid"] is True

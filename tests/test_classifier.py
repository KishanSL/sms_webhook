"""Unit tests for the SMS message classifier."""

import pytest

from app.classifier import (
    TYPE_BROKER,
    TYPE_MANDATE,
    TYPE_OTHERS,
    TYPE_OTP,
    TYPE_STATEMENT,
    TYPE_TOLL,
    TYPE_TRANSACTION,
    classify_message_text,
    enrich_webhooks_with_types,
)
from app.database import init_db, save_transaction, save_webhook


def test_classify_transaction():
    # Via debit/credit parser match
    msg = (
        "Spent Rs.384 On HDFC Bank Card 9012 At PYU*Instamart Grocery On 2026-11-20:22:18:36."
        "Not You? To Block+Reissue Call 18002586161/SMS BLOCK CC 9012 to 7308080808"
    )
    assert classify_message_text(msg, sender="AX-HDFCBK-S") == TYPE_TRANSACTION

    # Explicit flag (e.g. from transactions table link)
    assert classify_message_text("Some random text", is_transaction=True) == TYPE_TRANSACTION


def test_classify_otp():
    samples = [
        "OTP is 672330 for txn of INR 384.00 at SWIGGY INST on HDFC Bank card ending 9012. Valid till 10:21.",
        "Hi Your login OTP for Burger King App is 8742.",
        "Your Book An Appointment One Time Password is 875075 Team Rainbow",
        "283003 is One-Time Password for INR 189.00 transaction towards AMAZON using ICICI Bank Credit Card XX7531.",
        "357971 is SECRET OTP for txn of INR 420.00 on Axis Bank card XX8642 at Google on 20-11-26 01:38:08.",
        "Your verification code is 123456.",
    ]
    for s in samples:
        assert classify_message_text(s, sender="TEST") == TYPE_OTP


def test_classify_statement():
    samples = [
        ("VM-MYSBIC-S", "E-statement of SBI Credit Card ending XX24 dated 16/11/2026 has been mailed. Total Amt Due Rs 18450; Min Amt Due Rs 369; Payable by 06/12/2026."),
        ("CP-AXISBK-S", "Your statement for Axis Bank Credit Card no. XX8642 is generated. Due on: 08-12-26 Total amt: INR Dr. 42310.55 Min amt due: INR Dr. 912.00"),
        ("VM-BOBONE-S", "Your BOBCARD One Credit Card bill of Rs. 0.00 is ready. Pay by 05 Dec, 2026 via the OneCard app"),
        ("BP-HDFCBK-S", "Statement Generated: For HDFC Bank Credit Card XX9012. View: https://hdfc.bank.in"),
    ]
    for sender, s in samples:
        assert classify_message_text(s, sender=sender) == TYPE_STATEMENT

    assert classify_message_text("Random statement alert", is_statement=True) == TYPE_STATEMENT

    # Non-card statements (e.g. CRED, restaurant bills, promo) must NOT be classified as statement
    assert classify_message_text(
        "Your Axis credit card bill of Rs 75,837 is due on 08 Dec. Pay now on CRED: https://c-r-e-d.co/pay",
        sender="AD-CREDIN-S",
    ) == TYPE_OTHERS
    assert classify_message_text(
        "Thank you for dining at Burger King! Here is your bill: bk.co/123",
        sender="BK-BRGKNG-S",
    ) == TYPE_OTHERS


def test_classify_toll():
    samples = [
        "INR 120 toll paid from IDFC FIRST Bank Tag 3XXXE980 for vehicle no. AS01FB3450 at Devanahalli Toll Plaza on 18/11/2026 00:27. Avbl. Bal.: INR918.00.",
        "INR 85 toll paid from IDFC FIRST Bank Tag 3XXXE980 for vehicle no. AS01FB3450 at Nalluru Devanahalli on 18/11/2026 01:55. Avbl. Bal.: INR833.00.",
        "Your FASTag account was debited with Rs 150 for toll plaza.",
    ]
    for s in samples:
        assert classify_message_text(s, sender="VD-IDFCFB-S") == TYPE_TOLL


def test_classify_mandate():
    samples = [
        "Your UPI ASPRESENTED mandate has been successfully created towards Anomaly from 18-11-26 to 15-11-36 for INR 15000.00 - Axis Bank",
        "E-mandate registration successful for SIP of Rs 5000.",
        "Autopay has been scheduled for your recurring bill.",
    ]
    for s in samples:
        assert classify_message_text(s, sender="AX-AXISBK-S") == TYPE_MANDATE


def test_classify_broker():
    samples = [
        "ZERODHA BROKING at EOD 30/10/2026 reported your Fund bal Rs -936.750 & Securities bal 0. This excludes your Bank, DP and PMS bal with the broker-BSE",
        "ZERODHABROKINGLIMITED on 14-11-2026 reported your Fund bal Rs.-950.620 & Securities bal 0.000. This excludes your Bank, DP & PMS bal with the broker-NSE",
    ]
    for s in samples:
        assert classify_message_text(s, sender="BT-BSELTD-S") == TYPE_BROKER


def test_classify_others():
    samples = [
        "Dear KRITISHA SEN LASKAR, your appointment with Dr Dr. JAYANTH SUNDAR SAMPATH has been cancelled.",
        "Thank you for ordering with Burger King! Here's your E-bill: ez.easyrz.co/xyz",
        "Hi Customer, your RuPay BOBCARD Credit Card requires your confirmation.",
        "Podman container test SMS",
        "Hello from friend",
    ]
    for s in samples:
        assert classify_message_text(s, sender="PROMO") == TYPE_OTHERS


def test_classify_edge_cases():
    assert classify_message_text(None) == TYPE_OTHERS
    assert classify_message_text("") == TYPE_OTHERS
    assert classify_message_text("   ") == TYPE_OTHERS
    assert classify_message_text(None, sender=None) == TYPE_OTHERS


@pytest.mark.anyio
async def test_enrich_webhooks_with_types(tmp_path):
    test_db = str(tmp_path / "enrich_test.db")
    await init_db(test_db)

    # 1. Webhook that becomes a transaction
    wh1, _ = await save_webhook(
        test_db,
        event_id="evt-txn-1",
        source="sms",
        method="POST",
        path="/webhook/sms",
        url="http://testserver/webhook/sms",
        headers={},
        query_params={},
        content_type="application/json",
        payload_raw="",
        payload_json={},
        sender="AX-HDFCBK-S",
        recipient=None,
        message_body="Spent Rs.500 On HDFC Bank Card 9012 At Grocery On 2026-11-20:10:00:00.",
        external_message_id=None,
    )

    await save_transaction(
        test_db,
        event_id="evt-txn-1",
        webhook_id=wh1["id"],
        occurred_at="2026-11-20T10:00:00",
        raw_text=wh1["message_body"],
        bank="HDFC Bank",
        sender_header="HDFCBK",
        direction="debit",
        amount=500.0,
    )

    # 2. Webhook with OTP
    wh2, _ = await save_webhook(
        test_db,
        event_id="evt-otp-1",
        source="sms",
        method="POST",
        path="/webhook/sms",
        url="http://testserver/webhook/sms",
        headers={},
        query_params={},
        content_type="application/json",
        payload_raw="",
        payload_json={},
        sender="JM-HDFCBK-S",
        recipient=None,
        message_body="OTP is 123456 for txn of Rs 500 at Store.",
        external_message_id=None,
    )

    # 3. Webhook with Statement
    wh3, _ = await save_webhook(
        test_db,
        event_id="evt-stmt-1",
        source="sms",
        method="POST",
        path="/webhook/sms",
        url="http://testserver/webhook/sms",
        headers={},
        query_params={},
        content_type="application/json",
        payload_raw="",
        payload_json={},
        sender="CP-AXISBK-S",
        recipient=None,
        message_body="Your statement for Axis Bank Credit Card no. XX8642 is generated. Total amt: INR 1000",
        external_message_id=None,
    )

    # 4. Webhook with General text
    wh4, _ = await save_webhook(
        test_db,
        event_id="evt-other-1",
        source="sms",
        method="POST",
        path="/webhook/sms",
        url="http://testserver/webhook/sms",
        headers={},
        query_params={},
        content_type="application/json",
        payload_raw="",
        payload_json={},
        sender="Mom",
        recipient=None,
        message_body="Call me when you reach home",
        external_message_id=None,
    )

    items = [wh1, wh2, wh3, wh4]
    enriched = await enrich_webhooks_with_types(items)

    assert enriched[0]["detected_type"] == TYPE_TRANSACTION
    assert enriched[1]["detected_type"] == TYPE_OTP
    assert enriched[2]["detected_type"] == TYPE_STATEMENT
    assert enriched[3]["detected_type"] == TYPE_OTHERS

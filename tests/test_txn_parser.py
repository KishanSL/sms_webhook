"""Bank transaction SMS parser tests (samples anonymised from real bank formats)."""

import pytest

from app.txn_parser import DEFAULT_BANKS, parse_transaction, sender_header


@pytest.mark.parametrize(
    "sender, expected",
    [
        ("AD-HDFCBK-S", "HDFCBK"),
        ("JX-HDFCBK-S", "HDFCBK"),
        ("bp-icicit-s", "ICICIT"),
        ("VM-MYSBIC", "MYSBIC"),
        ("+919876543210", None),
        ("", None),
        (None, None),
    ],
)
def test_sender_header(sender, expected):
    assert sender_header(sender) == expected


def test_hdfc_upi_on_card():
    text = (
        "Txn Rs.1043.00\nOn HDFC Bank Card 1111\nAt *4521@ybl \nby UPI 111122223333\n"
        "On 18-11\nNot You?\nCall 18002586161/SMS BLOCK CC 1111 to 7308080808\n"
    )
    txn = parse_transaction("JX-HDFCBK-S", text, DEFAULT_BANKS)
    assert txn == {
        "bank": "HDFC Bank",
        "sender_header": "HDFCBK",
        "direction": "debit",
        "amount": 1043.0,
        "currency": "INR",
        "instrument": "credit_card",
        "channel": "upi",
        "account_last4": "1111",
        "counterparty": "*4521@ybl",
        "reference": "111122223333",
        "balance": None,
        "balance_type": None,
    }


def test_hdfc_card_spend():
    text = (
        "Spent Rs.316 On HDFC Bank Card 2222 At RSP*SWIGGY PVT LTD FOO On 2026-11-16:22:07:48."
        "Not You? To Block+Reissue Call 18002586161/SMS BLOCK CC 2222 to 7308080808"
    )
    txn = parse_transaction("AD-HDFCBK-S", text, DEFAULT_BANKS)
    assert txn["amount"] == 316.0
    assert txn["account_last4"] == "2222"
    assert txn["counterparty"] == "RSP*SWIGGY PVT LTD FOO"
    assert txn["channel"] == "card"


def test_hdfc_card_spend_amount_first():
    text = (
        "Rs.2227 spent on HDFC Bank Card x2222 at Swiggy Instamart on 2026-11-22:12:56:43."
        "Not U? Call 18002586161/SMS BLOCK DC 2222 to 7308080808"
    )
    txn = parse_transaction("AD-HDFCBK-S", text, DEFAULT_BANKS)
    assert txn["amount"] == 2227.0
    assert txn["account_last4"] == "2222"
    assert txn["counterparty"] == "Swiggy Instamart"
    assert txn["direction"] == "debit"
    assert txn["channel"] == "card"


def test_icici_card_spend():
    text = (
        "INR 2,063.27 spent using ICICI Bank Card XX3333 on 16-Nov-26 on AMAZON PAY IN E. "
        "Avl Limit: INR 14,78,671.15. If not you, call 1800 2662/SMS BLOCK 3333 to 9215676766."
    )
    txn = parse_transaction("BP-ICICIT-S", text, DEFAULT_BANKS)
    assert txn["amount"] == 2063.27
    assert txn["account_last4"] == "3333"
    assert txn["counterparty"] == "AMAZON PAY IN E"
    assert txn["balance"] == 1478671.15
    assert txn["balance_type"] == "limit"


def test_icici_card_spend_variation():
    text = (
        "Rs 25,000.00 spent on ICICI Bank Card XX3333 on 23-Nov-26 at MERCHANT STORE. "
        "Avl Lmt: Rs 1,00,000.00. To dispute, call 18002662/SMS BLOCK 3333 to 9215676766."
    )
    txn = parse_transaction("BP-ICICIT-S", text, DEFAULT_BANKS)
    assert txn == {
        "bank": "ICICI Bank",
        "sender_header": "ICICIT",
        "direction": "debit",
        "amount": 25000.0,
        "currency": "INR",
        "instrument": "credit_card",
        "channel": "card",
        "account_last4": "3333",
        "counterparty": "MERCHANT STORE",
        "reference": None,
        "balance": 100000.0,
        "balance_type": "limit",
    }


def test_axis_card_spend():
    text = (
        "Spent INR 8232.84 \nusing Axis Bank Credit Card no. XX4444 on 17-11-26 12:42:03 IST at \n"
        "RAZ*Furlenc. \nAvl limit: INR 245310.40. Not you? SMS BLOCK 4444 to +919951860002."
    )
    txn = parse_transaction("AD-AXISBK-S", text, DEFAULT_BANKS)
    assert txn["amount"] == 8232.84
    assert txn["account_last4"] == "4444"
    assert txn["counterparty"] == "RAZ*Furlenc"
    assert txn["balance"] == 245310.40


def test_axis_account_upi_debit():
    text = (
        "INR 10000.00 debited\nA/c no. XX5555\n17-11-26, 12:48:17\nUPI/P2A/123456789012/JOHN DOE\n"
        "Not you? SMS BLOCKUPI Cust ID to 919951860002\nAxis Bank"
    )
    txn = parse_transaction("AD-AXISBK-S", text, DEFAULT_BANKS)
    assert txn["amount"] == 10000.0
    assert txn["instrument"] == "account"
    assert txn["channel"] == "upi"
    assert txn["account_last4"] == "5555"
    assert txn["reference"] == "123456789012"
    assert txn["counterparty"] == "JOHN DOE"


def test_idfc_account_debit():
    text = (
        "Your A/c XX6666 debited by Rs. 30.00 on 15/11/26; TEA STALL  CORNER credited. "
        "RRN 123456789012. Available balance Rs. 5,210.35. Team IDFC FIRST Bank"
    )
    txn = parse_transaction("AX-IDFCFB-S", text, DEFAULT_BANKS)
    assert txn["amount"] == 30.0
    assert txn["instrument"] == "account"
    assert txn["counterparty"] == "TEA STALL CORNER"
    assert txn["balance"] == 5210.35
    assert txn["balance_type"] == "balance"


def test_idfc_account_credit():
    text = (
        "Your A/C XXXXX123456 is credited with INR 2,000.00 on 23/11/26 12:51. "
        "Your new balance is INR 6,784.87. Team IDFC FIRST Bank"
    )
    txn = parse_transaction("AD-IDFCFB-S", text, DEFAULT_BANKS)
    assert txn == {
        "bank": "IDFC First Bank",
        "sender_header": "IDFCFB",
        "direction": "credit",
        "amount": 2000.0,
        "currency": "INR",
        "instrument": "account",
        "channel": None,
        "account_last4": "3456",
        "counterparty": None,
        "reference": None,
        "balance": 6784.87,
        "balance_type": "balance",
    }


def test_onecard_spend():
    text = (
        "Superb choice! Rs. 250.00 spent at Coffee House on your BOBCARD One Credit Card xxXX7777. "
        "Reward points added. To dispute, click: https://1crd.in/sample/dispute"
    )
    txn = parse_transaction("AX-BOBONE-S", text, DEFAULT_BANKS)
    assert txn == {
        "bank": "OneCard",
        "sender_header": "BOBONE",
        "direction": "debit",
        "amount": 250.0,
        "currency": "INR",
        "instrument": "credit_card",
        "channel": "card",
        "account_last4": "7777",
        "counterparty": "Coffee House",
        "reference": None,
        "balance": None,
        "balance_type": None,
    }


def test_onecard_spend_variations():
    # Card first variation
    text1 = "Rs. 250.00 spent on your BOBCARD One Credit Card xxXX7777 at Coffee House."
    txn1 = parse_transaction("AX-BOBONE-S", text1, DEFAULT_BANKS)
    assert txn1["amount"] == 250.0
    assert txn1["account_last4"] == "7777"
    assert txn1["counterparty"] == "Coffee House"

    # OneCard ending 1234 with ONECRD sender
    text2 = "Spent Rs. 500 at Amazon on your OneCard ending 1234."
    txn2 = parse_transaction("VM-ONECRD-S", text2, DEFAULT_BANKS)
    assert txn2["bank"] == "OneCard"
    assert txn2["amount"] == 500.0
    assert txn2["account_last4"] == "1234"
    assert txn2["counterparty"] == "Amazon"

    # BOBCRD header
    text3 = "Superb choice! Rs. 10.00 spent at Tea Stall on your BOBCARD One Card 9999."
    txn3 = parse_transaction("BP-BOBCRD-S", text3, DEFAULT_BANKS)
    assert txn3["bank"] == "BOBCARD"
    assert txn3["amount"] == 10.0
    assert txn3["account_last4"] == "9999"
    assert txn3["counterparty"] == "Tea Stall"


@pytest.mark.parametrize(
    "sender, text",
    [
        # FASTag toll
        (
            "VD-IDFCFB-S",
            "INR 120 toll paid from IDFC FIRST Bank Tag 3XXXE000 for vehicle no. XX00XX0000 at "
            "Devanahalli Toll Plaza on 18/11/2026 00:27. Avbl. Bal.: INR918.00.",
        ),
        # Card statement notice
        (
            "VM-MYSBIC-S",
            "E-statement of SBI Credit Card ending XX00 dated 16/11/2026 has been mailed. "
            "Total Amt Due Rs 18450; Min Amt Due Rs 369; Payable by 06/12/2026.",
        ),
        # Unknown sender with a bank-like message
        (
            "AD-SWIGGY-S",
            "Spent Rs.316 On HDFC Bank Card 2222 At SWIGGY On 2026-11-16:22:07:48.",
        ),
        # Personal number
        ("+919876543210", "Sent you Rs.500"),
    ],
)
def test_non_transactions_ignored(sender, text):
    assert parse_transaction(sender, text, DEFAULT_BANKS) is None


def test_new_header_for_known_bank():
    """A bank's formats apply to any header linked to it, and only to linked headers."""
    text = (
        "INR 349.00 spent using ICICI Bank Card XX3333 on 18-Nov-26 on AMAZON PAY IN E. "
        "Avl Limit: INR 14,79,164.64."
    )
    assert parse_transaction("AD-ICICIB-S", text, DEFAULT_BANKS) is None
    txn = parse_transaction("AD-ICICIB-S", text, {**DEFAULT_BANKS, "ICICIB": "ICICI Bank"})
    assert txn["bank"] == "ICICI Bank"
    assert txn["sender_header"] == "ICICIB"

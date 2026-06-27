"""The always-on GENERIC pack (EMAIL / PHONE / CREDIT_CARD / IP_ADDRESS).

A PII firewall must never leak the most common PII even with Presidio disabled, so these
are covered by regex with a Luhn gate on card numbers.
"""

from __future__ import annotations

import pytest

from app.redaction.regex_packs import clear_cache, load_packs


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def _detect(text: str):
    pack = load_packs(["GENERIC"])[0]
    return [(s.entity_type, text[s.start : s.end]) for s in pack.detect(text)]


def test_generic_pack_loads_with_expected_types():
    pack = load_packs(["GENERIC"])[0]
    assert pack.code == "GENERIC"
    for t in ("EMAIL", "PHONE", "CREDIT_CARD", "IP_ADDRESS"):
        assert t in pack.entity_types


@pytest.mark.parametrize(
    "text,value",
    [
        ("write me at jane.doe@example.com today", "jane.doe@example.com"),
        ("billing+inv@sub.domain.co.uk works", "billing+inv@sub.domain.co.uk"),
    ],
)
def test_email_detected(text, value):
    assert ("EMAIL", value) in _detect(text)


def test_email_negative():
    # not an email (no domain TLD)
    assert _detect("the @ handle is just @bob") == []


@pytest.mark.parametrize(
    "card",
    [
        "4111 1111 1111 1111",  # Visa test, valid Luhn
        "4111-1111-1111-1111",
        "5500005555555559",  # MC test, valid Luhn
    ],
)
def test_credit_card_valid_luhn_detected(card):
    got = _detect(f"card on file: {card}")
    assert any(t == "CREDIT_CARD" for t, _ in got)


def test_credit_card_rejects_non_luhn():
    # 16 digits but fails Luhn -> must NOT be flagged as a card
    assert not any(t == "CREDIT_CARD" for t, _ in _detect("ref 1234567812345678"))


@pytest.mark.parametrize(
    "text,value",
    [
        ("call (613) 555-0142 now", "(613) 555-0142"),
        ("ph +1 613-555-0142 ok", "613-555-0142"),
        ("dial 613.555.0142 please", "613.555.0142"),
    ],
)
def test_phone_detected(text, value):
    got = _detect(text)
    assert any(t == "PHONE" and value in v for t, v in got)


def test_phone_does_not_match_bare_10_digit_run():
    # an order/id number with no separators must not be swept up as a phone
    assert not any(t == "PHONE" for t, _ in _detect("order number 6135550142 shipped"))


@pytest.mark.parametrize(
    "ip,ok",
    [("192.168.10.255", True), ("8.8.8.8", True), ("999.1.1.1", False), ("1.2.3", False)],
)
def test_ipv4(ip, ok):
    got = any(t == "IP_ADDRESS" for t, _ in _detect(f"from {ip} last seen"))
    assert got is ok


@pytest.mark.parametrize(
    "ipv6",
    [
        "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
        "fe80::1",
        "::1",
        "2001:db8::1",
    ],
)
def test_ipv6_detected(ipv6):
    got = any(t == "IP_ADDRESS_V6" for t, _ in _detect(f"client {ipv6} connected"))
    assert got, f"expected IPv6 match for {ipv6!r}"


@pytest.mark.parametrize(
    "iban",
    [
        "DE89370400440532013000",
        "GB29 NWBK 6016 1331 9268 19",
        "UA213996220000026007233566001",
        "CA04 CIBC 0010 0234 5678 0",
    ],
)
def test_iban_detected(iban):
    got = any(t == "IBAN" for t, _ in _detect(f"account: {iban} transfer"))
    assert got, f"expected IBAN match for {iban!r}"


@pytest.mark.parametrize(
    "text",
    [
        "born: 1990-05-21",
        "dob: 21/05/1990",
        "birthdate: 05.21.1990",
        "date of birth: 1990-05-21",
        "дата народження: 21.05.1990",
    ],
)
def test_dob_keyword_anchored(text):
    got = any(t == "DATE_OF_BIRTH" for t, _ in _detect(text))
    assert got, f"expected DOB match for {text!r}"


def test_dob_bare_date_not_matched():
    # plain date with no DOB keyword must NOT be flagged
    assert not any(t == "DATE_OF_BIRTH" for t, _ in _detect("report date: 2024-01-15"))

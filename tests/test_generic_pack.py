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

"""Canadian pack additions from Build North (Oct 2026): cue-anchored SIN, health cards,
bank transit/account, postal codes and civic street addresses — positives and negatives."""

from __future__ import annotations

import pytest

from app.redaction.regex_packs import load_packs


def _found(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pack in load_packs(["CA"]):
        for sp in pack.detect(text):
            out.setdefault(sp.entity_type, []).append(sp.text)
    return out


@pytest.mark.parametrize(
    ("text", "etype", "value"),
    [
        ("SIN 046 454 286 on file", "SIN", "046 454 286"),  # fails checksum, has cue
        ("social insurance number: 046-454-286", "SIN", "046-454-286"),
        ("NAS 046454286", "SIN", "046454286"),
        ("OHIP 1234-567-890-AB", "HEALTH_CARD", "1234-567-890-AB"),
        ("card 2345 678 901 XY renewed", "HEALTH_CARD", "2345 678 901 XY"),
        ("health card no. 9876543210", "HEALTH_CARD", "9876543210"),
        ("transit 00412", "BANK_TRANSIT", "00412"),
        ("account 5021234", "BANK_ACCOUNT", "5021234"),
        ("acct # 12-345-67", "BANK_ACCOUNT", "12-345-67"),
        ("Toronto ON M5J 2G2", "POSTAL_CODE", "M5J 2G2"),
        ("lives at 88 Harbour St, Toronto", "ADDRESS", "88 Harbour St"),
        ("office at 1200 Bay Street", "ADDRESS", "1200 Bay Street"),
    ],
)
def test_ca_positive(text, etype, value):
    assert value in _found(text).get(etype, []), _found(text)


@pytest.mark.parametrize(
    "text",
    [
        "T4 income of $84,200 and a T5 of $1,350",
        "invoice 2026-118 total 4512",
        "the account was opened in 2019",  # 4-digit year after 'account' is too short
        "postal code is unknown",
        "we met at 3 pm",
    ],
)
def test_ca_no_false_positive_on_money_years_and_prose(text):
    found = _found(text)
    for etype in ("SIN", "HEALTH_CARD", "BANK_ACCOUNT", "BANK_TRANSIT", "POSTAL_CODE", "ADDRESS"):
        assert etype not in found, (etype, found)

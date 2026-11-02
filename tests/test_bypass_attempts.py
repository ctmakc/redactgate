"""Redaction bypass attempts — every case here is a would-be LEAK of raw PII upstream.

Each test pushes an evasion form through the real ``RedactionPipeline.sanitize_request``
(normalize → detect → tokenize) and asserts the identifying digits/letters never appear
in what would be sent to the provider.
"""

from __future__ import annotations

import re

import pytest

from app.auth import AuthContext
from app.redaction.detect import Detector
from app.redaction.normalize import normalize_text
from app.redaction.pipeline import RedactionPipeline
from app.redaction.store import InMemoryTokenStore
from app.redaction.vault import Vault
from app.schemas.entities import PolicyDecision, PolicyMode


@pytest.fixture
def pipe(master_key, fingerprint_key) -> RedactionPipeline:
    store = InMemoryTokenStore()
    return RedactionPipeline(
        store=store,
        vault=Vault(store, master_key=master_key, fingerprint_key=fingerprint_key),
        detector=Detector(),
        audit_sink=None,
        decision=PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False),
        auth=AuthContext("t", "p", "k"),
        provider=None,  # type: ignore[arg-type]
    )


async def _sent(pipe: RedactionPipeline, text: str) -> str:
    payload = {"model": "m", "messages": [{"role": "user", "content": text}]}
    sanitized, _ = await pipe.sanitize_request(payload, route="t")
    return sanitized["messages"][0]["content"]


def _alnum(s: str) -> str:
    """ASCII-fold + drop separators so a leak can't hide behind formatting."""
    return re.sub(r"[^0-9A-Za-z@]", "", normalize_text(s))


SIN_DIGITS = "193456787"
CARD_DIGITS = "4111111111111111"
SSN_DIGITS = "123456789"

AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")  # Arabic-Indic
DEVA = str.maketrans("0123456789", "०१२३४५६७८९")  # Devanagari
FULL = str.maketrans("0123456789", "０１２３４５６７８９")  # full-width


# ── Unicode look-alike separators and digits ─────────────────────────────────────

UNICODE_CASES = [
    ("en dash", "SIN 193–456–787", SIN_DIGITS),
    ("em dash", "SIN 193—456—787", SIN_DIGITS),
    ("nb hyphen", "SIN 193‑456‑787", SIN_DIGITS),
    ("unicode hyphen", "SIN 193‐456‐787", SIN_DIGITS),
    ("minus sign", "SIN 193−456−787", SIN_DIGITS),
    ("fullwidth hyphen", "SIN 193－456－787", SIN_DIGITS),
    ("nbsp", "SIN 193 456 787", SIN_DIGITS),
    ("narrow nbsp", "SIN 193 456 787", SIN_DIGITS),
    ("figure space", "SIN 193 456 787", SIN_DIGITS),
    ("arabic-indic digits", "SIN " + "193 456 787".translate(AR), SIN_DIGITS),
    ("devanagari digits", "SIN " + "193456787".translate(DEVA), SIN_DIGITS),
    ("fullwidth digits", "SIN " + "193 456 787".translate(FULL), SIN_DIGITS),
    ("card en dash", "card 4111–1111–1111–1111", CARD_DIGITS),
    ("card arabic", "card " + "4111 1111 1111 1111".translate(AR), CARD_DIGITS),
    ("ssn en dash", "SSN 123–45–6789", SSN_DIGITS),
    ("ssn minus", "SSN 123−45−6789", SSN_DIGITS),
]

INVISIBLES = {
    "zwsp": "​",
    "zwnj": "‌",
    "zwj": "‍",
    "word joiner": "⁠",
    "bom": "﻿",
    "soft hyphen": "­",
    "lrm": "‎",
    "rlo": "‮",
    "lro": "‭",
    "lri": "⁦",
    "invisible times": "⁢",
    "invisible separator": "⁣",
    "mongolian vs": "᠎",
    "cgj": "͏",
    "variation selector": "️",
    "tag space": "\U000e0020",
    "hangul filler": "ㅤ",
}


@pytest.mark.parametrize(("name", "text", "digits"), UNICODE_CASES, ids=[c[0] for c in UNICODE_CASES])
async def test_unicode_lookalikes_are_redacted(pipe, name, text, digits):
    sent = await _sent(pipe, text)
    assert digits not in _alnum(sent), f"{name}: leaked -> {sent!r}"
    assert "[[" in sent


@pytest.mark.parametrize("name", list(INVISIBLES), ids=list(INVISIBLES))
async def test_invisible_char_inside_identifier_is_redacted(pipe, name):
    z = INVISIBLES[name]
    text = f"SIN 193{z}456{z}787 and card 4111{z}1111{z}1111{z}1111"
    sent = await _sent(pipe, text)
    folded = _alnum(sent)
    assert SIN_DIGITS not in folded, f"{name}: SIN leaked -> {sent!r}"
    assert CARD_DIGITS not in folded, f"{name}: card leaked -> {sent!r}"


async def test_invisible_char_every_position_in_email(pipe):
    email = "jane.doe@example.org"
    for i in range(1, len(email)):
        text = "mail " + email[:i] + "​" + email[i:]
        sent = await _sent(pipe, text)
        assert email not in normalize_text(sent), f"split at {i}: {sent!r}"


def test_normalize_is_idempotent_on_adversarial_mix():
    s = "e​́ ‮193–456–787 " + "١٢٣" + "️ㅤx"
    once = normalize_text(s)
    assert normalize_text(once) == once
    assert "193-456-787" in once
    assert "123" in once


def test_normalize_keeps_ordinary_multilingual_text():
    for s in ("Привіт, світ", "Bonjour à tous", "日本語のテキスト", "مرحبا بالعالم"):
        assert normalize_text(s) == s


# ── IPv6: compressed forms must be redacted in full (no trailing-group leak) ─────


@pytest.mark.parametrize(
    "ip",
    [
        "2001:db8::1",
        "fe80::1:2",
        "2001:db8::8a2e:370:7334",
        "2001:db8:0:0:1::1",
        "::1",
        "fe80::",
        "1:2:3:4:5:6:7::",
        "::2:3:4:5:6:7:8",
        "FE80::ABCD",
        "::ffff:192.0.2.1",
        "64:ff9b::192.0.2.33",
    ],
)
async def test_ipv6_is_redacted_in_full(pipe, ip):
    sent = await _sent(pipe, f"Login from {ip}, then logout.")
    assert sent.startswith("Login from [[IP_ADDRESS_V6_")
    assert sent.endswith("]], then logout."), f"partial IPv6 redaction leaked: {sent!r}"


@pytest.mark.parametrize("code", ["std::vector<int>", "Foo::Bar::baz()", "C::Foo", "10:30:15"])
async def test_ipv6_does_not_fire_on_code_or_times(pipe, code):
    assert await _sent(pipe, f"see {code} here") == f"see {code} here"


# ── Format variants the default packs must cover ─────────────────────────────────

FORMAT_CASES = [
    ("intl phone uk", "+44 20 7946 0958", "PHONE"),
    ("intl phone ua", "+380 44 123 4567", "PHONE"),
    ("intl phone de", "+49 30 12345678", "PHONE"),
    ("intl phone fr", "+33 1 23 45 67 89", "PHONE"),
    ("intl phone trunk", "+44 (0)20 7946 0958", "PHONE"),
    ("intl phone compact", "+380441234567", "PHONE"),
    ("ssn spaces", "123 45 6789", "SSN"),
    ("sin dots", "193.456.787", "SIN"),
    ("card dots", "4111.1111.1111.1111", "CREDIT_CARD"),
    ("iban lower", "gb82 west 1234 5698 7654 32", "IBAN"),
    ("iban mixed", "De89 3704 0044 0532 0130 00", "IBAN"),
    ("email homoglyph", "jоhn@exаmple.com", "EMAIL"),
    ("email idn", "user@пример.рф", "EMAIL"),
]


@pytest.mark.parametrize(("name", "value", "etype"), FORMAT_CASES, ids=[c[0] for c in FORMAT_CASES])
async def test_format_variants_are_redacted(pipe, name, value, etype):
    sent = await _sent(pipe, f"Details: {value} (end)")
    assert value not in sent
    assert sent == f"Details: [[{etype}_" + sent.split(f"[[{etype}_", 1)[1]
    assert sent.endswith("]] (end)"), f"{name}: partial redaction -> {sent!r}"


@pytest.mark.parametrize(
    "benign",
    ["+100% growth", "score +12 34", "1+2=3", "+5 points", "call me @ noon", "foo@bar"],
)
async def test_format_rules_do_not_fire_on_benign_text(pipe, benign):
    assert await _sent(pipe, benign) == benign


def test_e164_check_bounds():
    from app.redaction.validators import e164_check, get_validator

    assert get_validator("e164_check") is e164_check
    assert e164_check("+44 20 7946 0958")
    assert e164_check("+1 234 567 8")  # 8 digits
    assert not e164_check("+1 234 567")  # 7 digits
    assert not e164_check("+1234567890123456")  # 16 digits


def test_validators_accept_dot_separators():
    from app.redaction.validators import iban_mod97, luhn, sin_check

    assert luhn("4111.1111.1111.1111")
    assert sin_check("193.456.787")
    assert iban_mod97("GB82.WEST.1234.5698.7654.32")


# ── Encoded payloads ─────────────────────────────────────────────────────────────

import base64  # noqa: E402


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


B64_SECRETS = [
    ("sin", "SIN 193 456 787", "SIN"),
    ("email", "jane.doe@example.org", "EMAIL"),
    ("card", "4111111111111111", "CREDIT_CARD"),
    ("iban", "GB82 WEST 1234 5698 7654 32", "IBAN"),
    ("ssn json", '{"ssn": "123-45-6789"}', "SSN"),
]


@pytest.mark.parametrize(("name", "secret", "etype"), B64_SECRETS, ids=[c[0] for c in B64_SECRETS])
@pytest.mark.parametrize("enc", [_b64, _b64url, lambda s: _b64(_b64(s))], ids=["b64", "b64url", "double"])
async def test_base64_wrapped_pii_is_redacted(pipe, name, secret, etype, enc):
    token = enc(secret)
    sent = await _sent(pipe, f"Decode this: {token} and summarise.")
    assert token not in sent, f"{name}: encoded PII leaked -> {sent!r}"
    assert f"[[{etype}_" in sent


async def test_base64_roundtrips_to_the_original_token(pipe):
    token = _b64("SIN 193 456 787")
    payload = {"model": "m", "messages": [{"role": "user", "content": f"x {token} y"}]}
    sanitized, ctx = await pipe.sanitize_request(payload, route="t")
    echoed = sanitized["messages"][0]["content"]
    done = await pipe.reinflate(
        {"choices": [{"message": {"role": "assistant", "content": echoed}}]}, ctx
    )
    assert done["choices"][0]["message"]["content"] == f"x {token} y"


@pytest.mark.parametrize(
    "benign",
    [
        _b64("Hello, this is a perfectly ordinary sentence."),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # sha256
        "550e8400-e29b-41d4-a716-446655440000",  # uuid
        "internationalization",
        "/usr/local/lib/python3.12/site-packages",
        "dGVzdA",  # too short to consider
    ],
)
async def test_base64_pass_ignores_tokens_without_pii(pipe, benign):
    assert await _sent(pipe, f"value {benign} end") == f"value {benign} end"


async def test_base64_pass_tolerates_garbage_and_huge_tokens(pipe):
    junk = "A" * 70_000 + " " + "!!notbase64!!" * 3 + " " + "QUJD" * 5 + "="
    assert await _sent(pipe, junk) == junk

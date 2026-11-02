"""Per-entity detection + redaction coverage through the real regex-only path.

For every regex-detectable PII / financial entity type this proves three things:

1. the default detector (``settings.pack_codes``, zero optional deps) finds it,
2. after ``RedactionPipeline.sanitize_request`` the raw value is ABSENT from the payload
   that would be sent upstream (a leak here is a critical failure), and
3. re-inflation restores the exact original text.

Names / free-text addresses are NOT regex-detectable: they need the optional Presidio or
LLM-NER passes, which are covered (with fakes) in ``test_ner_passes.py``.
"""

from __future__ import annotations

import re

import pytest

from app.auth import AuthContext
from app.config import settings
from app.redaction.detect import Detector
from app.redaction.pipeline import RedactionPipeline
from app.redaction.store import InMemoryTokenStore
from app.redaction.vault import Vault
from app.schemas.entities import PolicyDecision, PolicyMode

PH_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)_[0-9a-f]{4,12}\]\]")

# (entity_type, raw value, surrounding sentence). Every value is a syntactically valid,
# check-digit-passing *test* identifier — none belongs to a real person.
ENTITIES: list[tuple[str, str, str]] = [
    ("EMAIL", "jane.doe+tax@example.org", "Contact {} for the return."),
    ("PHONE", "(613) 555-0142", "Call me at {} tomorrow."),
    ("PHONE", "+1 613-555-0142", "Mobile: {}."),
    ("CREDIT_CARD", "4111 1111 1111 1111", "Charge card {} please."),
    ("CREDIT_CARD", "5555-5555-5555-4444", "Backup card {}."),
    ("CREDIT_CARD", "378282246310005", "Amex {} on file."),
    ("IBAN", "GB82 WEST 1234 5698 7654 32", "Wire to {} today."),
    ("IBAN", "DE89370400440532013000", "Account {} (compact)."),
    ("SIN", "193 456 787", "My SIN is {}."),
    ("SIN", "193-456-787", "NAS {}."),
    ("SSN", "123-45-6789", "SSN {} on the W-2."),
    ("ITIN", "912-70-1234", "ITIN {} for the spouse."),
    ("EIN", "12-3456789", "Employer EIN {}."),
    ("IP_ADDRESS", "203.0.113.42", "Login from {} flagged."),
    ("IP_ADDRESS_V6", "2001:0db8:85a3:0000:0000:8a2e:0370:7334", "IPv6 {} seen."),
    ("IBAN_UA", "UA213223130000026007233566001", "Рахунок {} для оплати."),
]


def _decision() -> PolicyDecision:
    return PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False)


def _pipeline(vault: Vault, store: InMemoryTokenStore) -> RedactionPipeline:
    return RedactionPipeline(
        store=store,
        vault=vault,
        detector=Detector(),
        audit_sink=None,
        decision=_decision(),
        auth=AuthContext("team", "policy", "key"),
        provider=None,  # type: ignore[arg-type]  # not used by sanitize/reinflate
    )


@pytest.fixture
def store() -> InMemoryTokenStore:
    return InMemoryTokenStore()


@pytest.fixture
def pvault(store, master_key, fingerprint_key) -> Vault:
    return Vault(store, master_key=master_key, fingerprint_key=fingerprint_key)


@pytest.mark.parametrize(("etype", "value", "template"), ENTITIES, ids=lambda x: str(x)[:24])
async def test_default_detector_finds_entity(etype, value, template):
    text = template.format(value)
    res = await Detector().detect(text, pack_codes=settings.pack_codes)
    hits = [s for s in res.spans if s.text == value]
    assert hits, f"{etype} not detected in {text!r}: {[(s.entity_type, s.text) for s in res.spans]}"
    # SIN/ITIN and IBAN/IBAN_UA overlap by design; the exact type is secondary to the
    # value being covered, but the primary type must be among the candidates we accept.
    assert hits[0].entity_type in {etype, "IBAN", "IBAN_UA", "SIN", "ITIN", "SSN"}


@pytest.mark.parametrize(("etype", "value", "template"), ENTITIES, ids=lambda x: str(x)[:24])
async def test_sanitized_payload_never_contains_raw_value(etype, value, template, pvault, store):
    text = template.format(value)
    payload = {"model": "gpt", "messages": [{"role": "user", "content": text}]}
    sanitized, ctx = await _pipeline(pvault, store).sanitize_request(payload, route="t")
    sent = sanitized["messages"][0]["content"]
    assert value not in sent
    # The digits/letters of the value must not survive in any separator-stripped form.
    compact = re.sub(r"[\s\-().+]", "", value)
    assert compact not in re.sub(r"[\s\-().+]", "", sent)
    assert PH_RE.search(sent)
    assert sum(ctx.entity_counts.values()) >= 1


@pytest.mark.parametrize(("etype", "value", "template"), ENTITIES, ids=lambda x: str(x)[:24])
async def test_reinflation_restores_original(etype, value, template, pvault, store):
    text = template.format(value)
    pipe = _pipeline(pvault, store)
    sanitized, ctx = await pipe.sanitize_request(
        {"model": "gpt", "messages": [{"role": "user", "content": text}]}, route="t"
    )
    echoed = sanitized["messages"][0]["content"]
    completion = {"choices": [{"index": 0, "message": {"role": "assistant", "content": echoed}}]}
    restored = await pipe.reinflate(completion, ctx)
    assert restored["choices"][0]["message"]["content"] == text


async def test_every_entity_in_one_document_roundtrips(pvault, store):
    doc = " | ".join(t.format(v) for _, v, t in ENTITIES)
    pipe = _pipeline(pvault, store)
    sanitized, ctx = await pipe.sanitize_request(
        {"model": "gpt", "messages": [{"role": "user", "content": doc}]}, route="t"
    )
    sent = sanitized["messages"][0]["content"]
    for _, value, _ in ENTITIES:
        assert value not in sent, f"leak: {value!r}"
    completion = {"choices": [{"index": 0, "message": {"role": "assistant", "content": sent}}]}
    restored = await pipe.reinflate(completion, ctx)
    assert restored["choices"][0]["message"]["content"] == doc


async def test_keyword_anchored_identifiers_redact_only_the_number(pvault, store):
    text = "ІПН 1234567890, ЄДРПОУ 12345678, NEQ 1234 567 890"
    pipe = _pipeline(pvault, store)
    sanitized, _ = await pipe.sanitize_request(
        {"model": "gpt", "messages": [{"role": "user", "content": text}]}, route="t"
    )
    sent = sanitized["messages"][0]["content"]
    for raw in ("1234567890", "12345678", "1234 567 890"):
        assert raw not in sent
    # Cue words are context, not PII — they must survive for the LLM to reason with.
    for cue in ("ІПН", "ЄДРПОУ", "NEQ"):
        assert cue in sent
    types = {m.group(1) for m in PH_RE.finditer(sent)}
    assert {"IPN", "EDRPOU", "NEQ"} <= types


async def test_ircc_pack_is_opt_in_and_detects_uci_when_enabled():
    text = "UCI 1234-5678, application E001234567"
    default = await Detector().detect(text, pack_codes=settings.pack_codes)
    assert "IRCC" not in settings.pack_codes
    assert not [s for s in default.spans if s.jurisdiction == "IRCC"]
    res = await Detector().detect(text, pack_codes=["IRCC"])
    assert {(s.entity_type, s.text) for s in res.spans} == {
        ("UCI", "1234-5678"),
        ("APPLICATION_NUMBER", "E001234567"),
    }


async def test_same_value_in_system_user_and_tool_args_shares_one_placeholder(pvault, store):
    email = "jane.doe@example.org"
    payload = {
        "model": "gpt",
        "messages": [
            {"role": "system", "content": f"Customer email: {email}"},
            {"role": "user", "content": [{"type": "text", "text": f"Email {email} again"}]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "send", "arguments": f'{{"to": "{email}"}}'},
                    }
                ],
            },
        ],
    }
    sanitized, _ = await _pipeline(pvault, store).sanitize_request(payload, route="t")
    blob = repr(sanitized)
    assert email not in blob
    assert len(set(re.findall(r"\[\[EMAIL_[0-9a-f]+\]\]", blob))) == 1


async def test_two_requests_get_independent_sessions(pvault, store):
    pipe = _pipeline(pvault, store)
    body = {"model": "gpt", "messages": [{"role": "user", "content": "SIN 193 456 787"}]}
    s1, c1 = await pipe.sanitize_request(body, route="t")
    s2, c2 = await pipe.sanitize_request(body, route="t")
    assert c1.session_id != c2.session_id
    p1 = s1["messages"][0]["content"]
    p2 = s2["messages"][0]["content"]
    # Placeholders are random per session (no value-derived bits) ...
    assert p1 != p2
    # ... and session B cannot resolve session A's token, even with the same vault keys.
    assert await pvault.detokenize(p1, session_id=c2.session_id) == p1
    assert await pvault.detokenize(p1, session_id=c1.session_id) == "SIN 193 456 787"


@pytest.mark.parametrize(
    "benign",
    [
        "The meeting is at 10:30 in room 204.",
        "Order #12345 shipped on 2024-03-01.",
        "Version 1.2.3 fixes bug 4567.",
        "We grew revenue 15% to $1,250,000 last year.",
    ],
)
async def test_benign_text_is_not_rewritten(benign, pvault, store):
    sanitized, ctx = await _pipeline(pvault, store).sanitize_request(
        {"model": "gpt", "messages": [{"role": "user", "content": benign}]}, route="t"
    )
    assert sanitized["messages"][0]["content"] == benign
    assert ctx.entity_counts == {}


async def test_entity_counts_carry_types_not_values(pvault, store):
    text = "SIN 193 456 787 and card 4111 1111 1111 1111"
    _, ctx = await _pipeline(pvault, store).sanitize_request(
        {"model": "gpt", "messages": [{"role": "user", "content": text}]}, route="t"
    )
    assert ctx.entity_counts == {"SIN": 1, "CREDIT_CARD": 1}
    assert "193" not in repr(ctx.entity_counts)

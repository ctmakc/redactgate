"""Reversible token vault — referential consistency, crypto, and round-trip fidelity.

Uses the ``vault`` fixture (Vault over an InMemoryTokenStore) from conftest. No DB/redis.
"""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from app.redaction.placeholders import PLACEHOLDER_RE
from app.redaction.store import InMemoryTokenStore
from app.redaction.vault import Vault
from app.schemas.entities import EntitySpan

SESSION = "sess-unit-1"


def span_for(text, sub, etype):
    i = text.index(sub)
    return EntitySpan(i, i + len(sub), etype, sub)


def spans_for_all(text, sub, etype):
    """Build one EntitySpan per *distinct* occurrence of ``sub`` in ``text``."""
    out = []
    start = 0
    while True:
        i = text.find(sub, start)
        if i == -1:
            break
        out.append(EntitySpan(i, i + len(sub), etype, sub))
        start = i + len(sub)
    return out


# ── referential consistency ────────────────────────────────────────────────────


async def test_repeated_value_maps_to_identical_placeholder(vault):
    text = "John called. Later John called again about John."
    spans = spans_for_all(text, "John", "PERSON")
    out = await vault.tokenize(text, spans, session_id=SESSION)
    found = PLACEHOLDER_RE.findall(out)
    # All three 'John' occurrences collapse to the SAME placeholder token.
    assert len(found) == 3
    assert len(set(found)) == 1
    assert "John" not in out


async def test_repeated_value_bumps_occurrence_count(vault):
    text = "Email a@b.com twice: a@b.com"
    spans = spans_for_all(text, "a@b.com", "EMAIL")
    await vault.tokenize(text, spans, session_id=SESSION)
    records = await vault._store.all_for_session(SESSION)
    # One stored record for the de-duplicated value, with occurrences == 2.
    email_records = [r for r in records if r.entity_type == "EMAIL"]
    assert len(email_records) == 1
    assert email_records[0].occurrences == 2


async def test_distinct_values_get_distinct_placeholders(vault):
    text = "Alice and Bob"
    spans = [span_for(text, "Alice", "PERSON"), span_for(text, "Bob", "PERSON")]
    out = await vault.tokenize(text, spans, session_id=SESSION)
    found = PLACEHOLDER_RE.findall(out)
    assert len(found) == 2
    assert len(set(found)) == 2


# ── full round-trip ────────────────────────────────────────────────────────────


async def test_tokenize_detokenize_restores_original(vault):
    text = "SIN 046 454 286 for John Smith, email john@x.com."
    spans = [
        span_for(text, "046 454 286", "SIN"),
        span_for(text, "John Smith", "PERSON"),
        span_for(text, "john@x.com", "EMAIL"),
    ]
    redacted = await vault.tokenize(text, spans, session_id=SESSION)
    # Every secret is gone from the redacted text.
    for secret in ("046 454 286", "John Smith", "john@x.com"):
        assert secret not in redacted
    restored = await vault.detokenize(redacted, session_id=SESSION)
    assert restored == text


async def test_roundtrip_with_repeated_and_adjacent_entities(vault):
    text = "Bob met Bob next to bob@x.com and bob@x.com again."
    spans = [
        *spans_for_all(text, "Bob", "PERSON"),
        *spans_for_all(text, "bob@x.com", "EMAIL"),
    ]
    redacted = await vault.tokenize(text, spans, session_id=SESSION)
    assert "Bob" not in redacted and "bob@x.com" not in redacted
    assert await vault.detokenize(redacted, session_id=SESSION) == text


async def test_sample_pii_text_roundtrip(vault, sample_pii_text):
    # Use the shared fixture; redact the two 'John Smith' mentions + the email.
    text = sample_pii_text
    spans = spans_for_all(text, "John Smith", "PERSON")
    spans.append(span_for(text, "john.smith@example.com", "EMAIL"))
    redacted = await vault.tokenize(text, spans, session_id=SESSION)
    assert "John Smith" not in redacted
    assert "john.smith@example.com" not in redacted
    # both 'John Smith' mentions share one placeholder (referential consistency)
    person_phs = [
        m.group(0)
        for m in PLACEHOLDER_RE.finditer(redacted)
        if m.group(1) == "PERSON"
    ]
    assert len(person_phs) == 2 and len(set(person_phs)) == 1
    assert await vault.detokenize(redacted, session_id=SESSION) == text


# ── session isolation ──────────────────────────────────────────────────────────


async def test_different_sessions_have_independent_placeholders(vault):
    text = "Contact John"
    spans = [span_for(text, "John", "PERSON")]
    out_a = await vault.tokenize(text, spans, session_id="A")
    out_b = await vault.tokenize(text, spans, session_id="B")
    ph_a = PLACEHOLDER_RE.search(out_a).group(0)
    ph_b = PLACEHOLDER_RE.search(out_b).group(0)
    # Fingerprint is salted by session_id, so the token differs across sessions.
    assert ph_a != ph_b


async def test_detokenize_is_scoped_to_its_session(vault):
    text = "Call John"
    spans = [span_for(text, "John", "PERSON")]
    redacted_a = await vault.tokenize(text, spans, session_id="A")
    # Detokenizing session A's output under session B leaves the placeholder untouched.
    out_wrong = await vault.detokenize(redacted_a, session_id="B")
    assert out_wrong == redacted_a
    assert "John" not in out_wrong


# ── crypto: ciphertext-in-store must NOT contain plaintext ─────────────────────


async def test_store_ciphertext_does_not_contain_plaintext_bytes(vault):
    secret = "046 454 286"
    text = f"His SIN is {secret}."
    spans = [span_for(text, secret, "SIN")]
    await vault.tokenize(text, spans, session_id=SESSION)
    records = await vault._store.all_for_session(SESSION)
    assert records, "expected a stored token record"
    rec = records[0]
    # The raw value never appears in the stored ciphertext (or its fingerprint).
    assert secret.encode() not in rec.value_ciphertext
    assert secret not in rec.value_fingerprint
    # Ciphertext is nonce(12) + GCM payload, strictly longer than the plaintext.
    assert len(rec.value_ciphertext) > len(secret.encode())


async def test_value_only_recoverable_with_master_key(master_key, fingerprint_key):
    store = InMemoryTokenStore()
    right = Vault(store, master_key=master_key, fingerprint_key=fingerprint_key)
    secret = "john@secret.com"
    text = f"email {secret}"
    spans = [span_for(text, secret, "EMAIL")]
    redacted = await right.tokenize(text, spans, session_id=SESSION)

    # A vault with the WRONG master key cannot decrypt the same store.
    wrong = Vault(
        store, master_key=b"x" * 32, fingerprint_key=fingerprint_key
    )
    with pytest.raises(InvalidTag):
        await wrong.detokenize(redacted, session_id=SESSION)

    # The correct key recovers it.
    assert await right.detokenize(redacted, session_id=SESSION) == text


async def test_fingerprint_changes_with_fingerprint_key(master_key):
    store_a = InMemoryTokenStore()
    store_b = InMemoryTokenStore()
    v_a = Vault(store_a, master_key=master_key, fingerprint_key=b"a" * 32)
    v_b = Vault(store_b, master_key=master_key, fingerprint_key=b"b" * 32)
    text = "Name: John"
    spans = [span_for(text, "John", "PERSON")]
    await v_a.tokenize(text, spans, session_id=SESSION)
    await v_b.tokenize(text, spans, session_id=SESSION)
    fp_a = (await store_a.all_for_session(SESSION))[0].value_fingerprint
    fp_b = (await store_b.all_for_session(SESSION))[0].value_fingerprint
    assert fp_a != fp_b


async def test_nonce_is_randomized_per_encryption(master_key, fingerprint_key):
    # Same value in two different sessions -> different ciphertext (random nonce + DEK).
    store = InMemoryTokenStore()
    vault = Vault(store, master_key=master_key, fingerprint_key=fingerprint_key)
    text = "x a@b.com"
    spans = [span_for(text, "a@b.com", "EMAIL")]
    await vault.tokenize(text, spans, session_id="S1")
    await vault.tokenize(text, spans, session_id="S2")
    ct1 = (await store.all_for_session("S1"))[0].value_ciphertext
    ct2 = (await store.all_for_session("S2"))[0].value_ciphertext
    assert ct1 != ct2


# ── detokenize robustness ──────────────────────────────────────────────────────


async def test_detokenize_leaves_unknown_placeholder_untouched(vault):
    text = "Hello John"
    spans = [span_for(text, "John", "PERSON")]
    redacted = await vault.tokenize(text, spans, session_id=SESSION)
    # Splice in a placeholder that was never stored in this session.
    contaminated = redacted + " and [[SIN_dead99]] unknown"
    restored = await vault.detokenize(contaminated, session_id=SESSION)
    # Known placeholder restored, unknown one left verbatim.
    assert "John" in restored
    assert "[[SIN_dead99]]" in restored


async def test_detokenize_no_placeholder_is_identity(vault):
    plain = "no tokens at all in this string"
    assert await vault.detokenize(plain, session_id=SESSION) == plain


async def test_tokenize_empty_spans_is_identity(vault):
    text = "nothing to redact"
    assert await vault.tokenize(text, [], session_id=SESSION) == text


async def test_mixed_known_and_unknown_in_one_pass(vault):
    text = "A=John B=Jane"
    spans = [span_for(text, "John", "PERSON"), span_for(text, "Jane", "PERSON")]
    redacted = await vault.tokenize(text, spans, session_id=SESSION)
    # corrupt one placeholder's hex so it no longer resolves
    import re as _re

    phs = PLACEHOLDER_RE.findall(redacted)
    assert len(phs) == 2
    # build a guaranteed-unknown placeholder and append
    contaminated = redacted + " C=[[PERSON_999999]]"
    out = await vault.detokenize(contaminated, session_id=SESSION)
    assert "John" in out and "Jane" in out
    assert "[[PERSON_999999]]" in out
    assert not _re.search(r"\[\[PERSON_999999_", out)

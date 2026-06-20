"""Unit tests for the regex jurisdiction packs (zero-dependency detection floor).

For each major entity type we assert at least one POSITIVE case (detected with the
correct type and char offsets) and one NEGATIVE case (a similar-looking but invalid
value that must NOT be detected — especially the validator-gated SIN/IBAN/SSN/etc.).
Every emitted span must carry ``jurisdiction`` (the pack code) and ``source == "regex"``.
"""

from __future__ import annotations

from app.redaction.regex_packs import (
    Pack,
    available_pack_codes,
    load_packs,
)
from app.schemas.entities import EntitySpan

# A valid Ukrainian IBAN (UA + correct mod-97 check digits + 25-char BBAN), grouped
# to match the pack's spacing pattern. Verified mod-97 == 1.
VALID_UA_IBAN = "UA74 3052 9900 0002 6007 2335 66001"


def _pack(code: str) -> Pack:
    packs = load_packs([code])
    assert packs, f"pack {code} failed to load"
    return packs[0]


def _detect(code: str, text: str) -> list[EntitySpan]:
    return _pack(code).detect(text)


def _types(spans: list[EntitySpan]) -> set[str]:
    return {s.entity_type for s in spans}


def _find(spans: list[EntitySpan], entity_type: str) -> EntitySpan:
    matches = [s for s in spans if s.entity_type == entity_type]
    assert matches, f"expected a {entity_type} span, got {[s.entity_type for s in spans]}"
    return matches[0]


def _assert_offsets(span: EntitySpan, text: str) -> None:
    """The span's char offsets must slice exactly to its reported text."""
    assert text[span.start : span.end] == span.text


# ── pack loading ─────────────────────────────────────────────────────────────


class TestLoadPacks:
    def test_all_five_jurisdictions_load(self) -> None:
        codes = ["CA", "US", "EU", "UA", "IRCC"]
        packs = load_packs(codes)
        assert {p.code for p in packs} == set(codes)

    def test_load_is_case_insensitive(self) -> None:
        packs = load_packs(["ca", "Us"])
        assert {p.code for p in packs} == {"CA", "US"}

    def test_unknown_codes_skipped(self) -> None:
        packs = load_packs(["CA", "ZZ", "NOPE"])
        assert {p.code for p in packs} == {"CA"}

    def test_duplicate_codes_dedup(self) -> None:
        packs = load_packs(["CA", "CA", "ca"])
        assert len(packs) == 1

    def test_available_pack_codes(self) -> None:
        codes = available_pack_codes()
        assert {"CA", "US", "EU", "UA", "IRCC"}.issubset(set(codes))

    def test_packs_declare_entity_types(self) -> None:
        assert "SIN" in _pack("CA").entity_types
        assert "SSN" in _pack("US").entity_types
        assert "IBAN" in _pack("EU").entity_types


# ── every span carries jurisdiction + source ─────────────────────────────────


class TestSpanProvenance:
    def test_spans_carry_jurisdiction_and_regex_source(self) -> None:
        spans = _detect("CA", "SIN 193 456 787 and BN 123456789 RC0001")
        assert spans
        for s in spans:
            assert s.source == "regex"
            assert s.jurisdiction == "CA"
            assert 0.0 < s.score <= 1.0

    def test_jurisdiction_matches_pack_code_for_each(self) -> None:
        cases = {
            "US": "SSN 123-45-6789",
            "EU": "IBAN GB82 WEST 1234 5698 7654 32",
            "UA": "ІПН 1234567890",
            "IRCC": "UCI 1234 5678",
        }
        for code, text in cases.items():
            spans = _detect(code, text)
            assert spans, f"{code} produced no spans"
            assert all(s.jurisdiction == code for s in spans)
            assert all(s.source == "regex" for s in spans)


# ── CA: SIN, BUSINESS_NUMBER, GST_HST, NEQ ───────────────────────────────────


class TestCanadaPack:
    def test_sin_positive_with_offsets(self) -> None:
        text = "His SIN is 193 456 787 on file."
        span = _find(_detect("CA", text), "SIN")
        assert span.text == "193 456 787"
        _assert_offsets(span, text)

    def test_sin_negative_bad_checksum(self) -> None:
        # Right shape, wrong Luhn digit -> sin_check rejects, no span.
        assert "SIN" not in _types(_detect("CA", "SIN is 123 456 789"))

    def test_sin_negative_leading_zero(self) -> None:
        # 046 454 286 passes Luhn but a real SIN never starts with 0.
        assert "SIN" not in _types(_detect("CA", "SIN is 046 454 286"))

    def test_business_number_positive(self) -> None:
        text = "BN 123456789 RC0001 active"
        span = _find(_detect("CA", text), "BUSINESS_NUMBER")
        assert span.text == "123456789 RC0001"
        _assert_offsets(span, text)

    def test_business_number_negative_no_program(self) -> None:
        # A bare 9-digit run with no program-account suffix is not a BN.
        assert "BUSINESS_NUMBER" not in _types(_detect("CA", "ref 123456789 only"))

    def test_gst_hst_positive(self) -> None:
        text = "GST 123456789 RT 0001"
        span = _find(_detect("CA", text), "GST_HST")
        _assert_offsets(span, text)

    def test_neq_positive_only_number_redacted(self) -> None:
        # NEQ is keyword-anchored; only the numeric value (named group) is the span.
        text = "NEQ: 1234 567 890 registered"
        span = _find(_detect("CA", text), "NEQ")
        assert span.text == "1234 567 890"
        _assert_offsets(span, text)
        assert "NEQ" not in span.text  # the cue word is excluded

    def test_neq_negative_requires_keyword(self) -> None:
        # A bare 10-digit run without the NEQ cue must not fire.
        assert "NEQ" not in _types(_detect("CA", "the number 1234567890 here"))


# ── US: SSN, EIN, ITIN ───────────────────────────────────────────────────────


class TestUnitedStatesPack:
    def test_ssn_positive(self) -> None:
        text = "SSN 123-45-6789 on record"
        span = _find(_detect("US", text), "SSN")
        assert span.text == "123-45-6789"
        _assert_offsets(span, text)

    def test_ssn_negative_never_issued_area(self) -> None:
        # Area 000 is never issued -> ssn_check rejects.
        assert "SSN" not in _types(_detect("US", "SSN 000-45-6789"))

    def test_ein_positive(self) -> None:
        text = "EIN 12-3456789 filed"
        span = _find(_detect("US", text), "EIN")
        assert span.text == "12-3456789"
        _assert_offsets(span, text)

    def test_ein_negative_bad_prefix(self) -> None:
        # 07 is an invalid IRS campus prefix.
        assert "EIN" not in _types(_detect("US", "EIN 07-1234567"))

    def test_itin_positive(self) -> None:
        text = "ITIN 900-70-0000 here"
        span = _find(_detect("US", text), "ITIN")
        assert span.text == "900-70-0000"
        _assert_offsets(span, text)

    def test_itin_negative_bad_group(self) -> None:
        # group 10 is outside the allocated ITIN ranges.
        assert "ITIN" not in _types(_detect("US", "ITIN 900-10-0000"))


# ── EU: IBAN, VAT ────────────────────────────────────────────────────────────


class TestEuropeanUnionPack:
    def test_iban_positive_gb(self) -> None:
        text = "Wire to GB82 WEST 1234 5698 7654 32 today"
        span = _find(_detect("EU", text), "IBAN")
        assert span.text == "GB82 WEST 1234 5698 7654 32"
        _assert_offsets(span, text)

    def test_iban_negative_corrupted_checksum(self) -> None:
        # A single-character corruption breaks mod-97 -> no IBAN span.
        assert "IBAN" not in _types(_detect("EU", "GB82 WEST 1234 5698 7654 33"))

    def test_vat_positive(self) -> None:
        text = "VAT DE123456789 registered"
        span = _find(_detect("EU", text), "VAT")
        assert span.text == "DE123456789"
        _assert_offsets(span, text)


# ── UA: IBAN_UA, IPN, EDRPOU ─────────────────────────────────────────────────


class TestUkrainePack:
    def test_iban_ua_positive(self) -> None:
        text = f"рахунок {VALID_UA_IBAN} в банку"
        span = _find(_detect("UA", text), "IBAN_UA")
        assert span.text == VALID_UA_IBAN
        _assert_offsets(span, text)

    def test_iban_ua_negative_corrupted(self) -> None:
        # Corrupt the last digit -> mod-97 fails.
        bad = VALID_UA_IBAN[:-1] + ("0" if VALID_UA_IBAN[-1] != "0" else "1")
        assert "IBAN_UA" not in _types(_detect("UA", f"рахунок {bad}"))

    def test_ipn_positive_only_value_redacted(self) -> None:
        text = "ІПН 1234567890 платника"
        span = _find(_detect("UA", text), "IPN")
        assert span.text == "1234567890"
        _assert_offsets(span, text)

    def test_ipn_negative_requires_keyword(self) -> None:
        # A bare 10-digit run (no ІПН/РНОКПП cue) must not be flagged.
        assert "IPN" not in _types(_detect("UA", "замовлення 1234567890"))

    def test_ipn_negative_wrong_length(self) -> None:
        # Keyword present but only 9 digits -> ipn_check rejects.
        assert "IPN" not in _types(_detect("UA", "ІПН 123456789"))

    def test_edrpou_positive(self) -> None:
        text = "ЄДРПОУ 12345678 юрособа"
        span = _find(_detect("UA", text), "EDRPOU")
        assert span.text == "12345678"
        _assert_offsets(span, text)

    def test_edrpou_negative_requires_keyword(self) -> None:
        assert "EDRPOU" not in _types(_detect("UA", "код 12345678 тут"))


# ── IRCC: UCI, APPLICATION_NUMBER ────────────────────────────────────────────


class TestIrccPack:
    def test_uci_positive(self) -> None:
        text = "UCI 1234 5678 assigned"
        span = _find(_detect("IRCC", text), "UCI")
        assert span.text == "1234 5678"
        _assert_offsets(span, text)

    def test_uci_negative_requires_keyword(self) -> None:
        assert "UCI" not in _types(_detect("IRCC", "number 12345678 here"))

    def test_application_number_positive(self) -> None:
        text = "Application E001234567 received"
        span = _find(_detect("IRCC", text), "APPLICATION_NUMBER")
        assert span.text == "E001234567"
        _assert_offsets(span, text)

    def test_application_number_negative_short(self) -> None:
        # Needs 9-10 trailing digits; E12345 is too short.
        assert "APPLICATION_NUMBER" not in _types(_detect("IRCC", "application E12345"))

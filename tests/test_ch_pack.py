"""Swiss (CH) jurisdiction pack: validators + regex detection, de/fr/it cues.

All identifiers below are synthetic: check digits were computed for made-up bodies
(AHV 756.1234.5678.9x, UID CHE-123.456.78x, ...). None belongs to a real person or company.
"""

from __future__ import annotations

import pytest

from app.redaction.detect import Detector
from app.redaction.regex_packs import available_pack_codes, load_packs
from app.redaction.validators import ahv_ean13, ch_iban, ch_phone, ch_uid, get_validator
from app.schemas.entities import EntitySpan

# ── synthetic fixtures (check digits computed, values invented) ───────────────

AHV_VALID = ["756.1234.5678.97", "756.0000.1234.59", "756.9876.5432.17", "7561234567897"]
AHV_INVALID = [
    "756.1234.5678.98",  # wrong check digit
    "755.1234.5678.97",  # wrong country prefix
    "756.1234.5678.9",  # too short
    "756.1234.5678.977",  # too long
]

IBAN_VALID = ["CH76 0900 0000 1234 5678 9", "CH91 8080 8123 4567 8901 2", "LI43 0880 0000 0123 4567 8"]
IBAN_INVALID = [
    "CH77 0900 0000 1234 5678 9",  # wrong check digits
    "CH76 0900 0000 1234 5678",  # truncated (20 chars)
    "DE89 3704 0044 0532 0130 00",  # valid IBAN, but not CH/LI
]

UID_VALID = ["CHE-123.456.788", "CHE-100.000.006", "CHE-271.818.280", "CHE314159269"]
UID_INVALID = [
    "CHE-123.456.789",  # wrong check digit
    "CHE-555.555.556",  # wrong check digit
    "CHE-123.456.78",  # too short
]

PHONE_VALID = [
    "+41 44 123 45 67",
    "+41 (0)79 123 45 67",
    "+41791234567",
    "0041 21 123 45 67",
    "044 123 45 67",
    "091 123 45 67",
]
PHONE_INVALID = [
    "+41 04 123 45 67",  # national number cannot start with 0
    "+41 44 123 45 6",  # 8-digit national number
    "44 123 45 67",  # no prefix at all
    "+49 30 123 45 67",  # German number
]


def _ch_detect(text: str) -> list[EntitySpan]:
    packs = load_packs(["CH"])
    assert packs and packs[0].code == "CH"
    return packs[0].detect(text)


def _only(spans: list[EntitySpan], entity_type: str) -> list[EntitySpan]:
    return [s for s in spans if s.entity_type == entity_type]


# ── validators ────────────────────────────────────────────────────────────────


class TestValidators:
    @pytest.mark.parametrize("value", AHV_VALID)
    def test_ahv_valid(self, value: str) -> None:
        assert ahv_ean13(value)

    @pytest.mark.parametrize("value", AHV_INVALID)
    def test_ahv_invalid(self, value: str) -> None:
        assert not ahv_ean13(value)

    @pytest.mark.parametrize("value", IBAN_VALID)
    def test_ch_iban_valid(self, value: str) -> None:
        assert ch_iban(value)

    @pytest.mark.parametrize("value", IBAN_INVALID)
    def test_ch_iban_invalid(self, value: str) -> None:
        assert not ch_iban(value)

    @pytest.mark.parametrize("value", UID_VALID)
    def test_uid_valid(self, value: str) -> None:
        assert ch_uid(value)

    @pytest.mark.parametrize("value", UID_INVALID)
    def test_uid_invalid(self, value: str) -> None:
        assert not ch_uid(value)

    def test_uid_check_digit_ten_is_never_valid(self) -> None:
        # 11 - (sum % 11) == 10 for this body, so no ninth digit can make it valid.
        body = next(
            b
            for b in (f"{n:08d}" for n in range(10_000_000, 10_001_000))
            if 11 - sum(int(c) * w for c, w in zip(b, (5, 4, 3, 2, 7, 6, 5, 4), strict=True)) % 11
            == 10
        )
        assert not any(ch_uid(f"CHE{body}{d}") for d in range(10))

    @pytest.mark.parametrize("value", PHONE_VALID)
    def test_phone_valid(self, value: str) -> None:
        assert ch_phone(value)

    @pytest.mark.parametrize("value", PHONE_INVALID)
    def test_phone_invalid(self, value: str) -> None:
        assert not ch_phone(value)

    @pytest.mark.parametrize("name", ["ahv_ean13", "ch_iban", "ch_uid", "ch_phone"])
    def test_registered_by_name(self, name: str) -> None:
        assert get_validator(name) is not None


# ── pack loading ──────────────────────────────────────────────────────────────


def test_ch_pack_is_shipped_and_loads() -> None:
    assert "CH" in available_pack_codes()
    pack = load_packs(["ch"])[0]
    assert pack.code == "CH"
    assert {"AHV", "IBAN", "UID", "PHONE", "PASSPORT", "NATIONAL_ID", "DATE_OF_BIRTH"} <= set(
        pack.entity_types
    )
    # every pattern that names a validator must resolve it (no silent typo)
    assert all(p.validator is not None for p in pack.patterns if p.validator_name)


# ── regex detection: positive + negative per type ────────────────────────────


class TestDetection:
    @pytest.mark.parametrize("value", AHV_VALID[:3])
    def test_ahv_detected_with_offsets(self, value: str) -> None:
        text = f"AHV-Nr. {value} gemäss Ausweis."
        spans = _only(_ch_detect(text), "AHV")
        assert len(spans) == 1
        s = spans[0]
        assert s.text == value and text[s.start : s.end] == value
        assert s.jurisdiction == "CH" and s.source == "regex"

    @pytest.mark.parametrize("value", AHV_INVALID[:2])
    def test_ahv_bad_check_digit_not_detected(self, value: str) -> None:
        assert not _only(_ch_detect(f"numéro AVS {value}"), "AHV")

    @pytest.mark.parametrize("value", IBAN_VALID)
    def test_iban_detected(self, value: str) -> None:
        spans = _only(_ch_detect(f"IBAN: {value}, Bankverbindung UBS"), "IBAN")
        assert [s.text for s in spans] == [value]

    def test_iban_bad_checksum_not_detected(self) -> None:
        assert not _only(_ch_detect("conto CH77 0900 0000 1234 5678 9"), "IBAN")

    def test_foreign_iban_left_to_eu_pack(self) -> None:
        assert not _only(_ch_detect("IBAN DE89 3704 0044 0532 0130 00"), "IBAN")

    @pytest.mark.parametrize("value", ["CHE-123.456.788", "CHE-271.818.280"])
    def test_uid_detected_with_suffix(self, value: str) -> None:
        text = f"UID {value} MWST"
        spans = _only(_ch_detect(text), "UID")
        assert [s.text for s in spans] == [value]

    def test_uid_bad_check_digit_not_detected(self) -> None:
        assert not _only(_ch_detect("IDE CHE-123.456.789 TVA"), "UID")

    @pytest.mark.parametrize("value", PHONE_VALID)
    def test_phone_detected(self, value: str) -> None:
        text = f"Tel. {value} (Büro)"
        spans = _only(_ch_detect(text), "PHONE")
        assert [s.text for s in spans] == [value]

    @pytest.mark.parametrize(
        "text",
        [
            "Rechnung 0441234567 vom Mai",  # bare 10-digit run, no separators
            "+41 04 123 45 67",
            "Ref. 44 123 45 67",
        ],
    )
    def test_phone_negative(self, text: str) -> None:
        assert not _only(_ch_detect(text), "PHONE")

    @pytest.mark.parametrize(
        "text,value",
        [
            ("Reisepass Nr. X1234567, gültig bis 2031", "X1234567"),
            ("numéro de passeport : S7654321", "S7654321"),
            ("passaporto n. A0012345", "A0012345"),
        ],
    )
    def test_passport_keyword_anchored(self, text: str, value: str) -> None:
        spans = _only(_ch_detect(text), "PASSPORT")
        assert [s.text for s in spans] == [value]
        # only the number is redacted, the cue word stays
        assert "pass" not in spans[0].text.lower()

    def test_passport_shape_without_keyword_is_ignored(self) -> None:
        assert not _only(_ch_detect("Artikelnummer X1234567 im Lager"), "PASSPORT")

    @pytest.mark.parametrize(
        "text,value",
        [
            ("Identitätskarte C1234567", "C1234567"),
            ("carte d'identité n° E7654321", "E7654321"),
            ("carta d'identità numero B1112223", "B1112223"),
        ],
    )
    def test_identity_card(self, text: str, value: str) -> None:
        assert [s.text for s in _only(_ch_detect(text), "NATIONAL_ID")] == [value]

    @pytest.mark.parametrize(
        "text,value",
        [
            ("Geburtsdatum: 14.03.1981", "14.03.1981"),
            ("geboren am 2. 7. 1975 in Bern", "2. 7. 1975"),
            ("née le 05/11/1990 à Lausanne", "05/11/1990"),
            ("data di nascita 1988-09-30", "1988-09-30"),
            ("nato il 21.12.1969 a Lugano", "21.12.1969"),
        ],
    )
    def test_date_of_birth_de_fr_it(self, text: str, value: str) -> None:
        assert [s.text for s in _only(_ch_detect(text), "DATE_OF_BIRTH")] == [value]

    def test_plain_date_is_not_dob(self) -> None:
        assert not _only(_ch_detect("Rechnung vom 14.03.2026"), "DATE_OF_BIRTH")


# ── end to end through the multi-pass Detector (regex only) ──────────────────

SWISS_DOC = (
    "Mandant: Lea Muster, AHV-Nr. 756.1234.5678.97, geboren am 14.03.1981.\n"
    "Firma: Muster Treuhand GmbH, UID CHE-123.456.788 MWST.\n"
    "IBAN CH76 0900 0000 1234 5678 9 · Tel. +41 79 123 45 67 · lea@example.ch\n"
    "Client : numéro AVS 756.0000.1234.59, téléphone 021 123 45 67.\n"
    "Cliente: passaporto n. A0012345, IBAN LI43 0880 0000 0123 4567 8."
)


async def test_detector_finds_every_swiss_identifier() -> None:
    result = await Detector().detect(SWISS_DOC, pack_codes=["GENERIC", "CH", "EU"])
    found = {(s.entity_type, s.text) for s in result.spans}
    expected = {
        ("AHV", "756.1234.5678.97"),
        ("AHV", "756.0000.1234.59"),
        ("UID", "CHE-123.456.788"),
        ("IBAN", "CH76 0900 0000 1234 5678 9"),
        ("IBAN", "LI43 0880 0000 0123 4567 8"),
        ("PHONE", "+41 79 123 45 67"),
        ("PHONE", "021 123 45 67"),
        ("EMAIL", "lea@example.ch"),
        ("PASSPORT", "A0012345"),
        ("DATE_OF_BIRTH", "14.03.1981"),
    }
    assert expected <= found
    # spans are non-overlapping and slice back to their text
    for s in result.spans:
        assert SWISS_DOC[s.start : s.end] == s.text
    ordered = sorted(result.spans, key=lambda s: s.start)
    assert all(a.end <= b.start for a, b in zip(ordered, ordered[1:], strict=False))


async def test_swiss_doc_roundtrips_through_vault(vault) -> None:
    result = await Detector().detect(SWISS_DOC, pack_codes=["GENERIC", "CH", "EU"])
    redacted = await vault.tokenize(SWISS_DOC, result.spans, session_id="ch-demo")
    for value in ("756.1234.5678.97", "CHE-123.456.788", "CH76 0900 0000 1234 5678 9", "A0012345"):
        assert value not in redacted
    assert await vault.detokenize(redacted, session_id="ch-demo") == SWISS_DOC

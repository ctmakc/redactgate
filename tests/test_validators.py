"""Unit tests for the pure check-digit / format validators.

These run with zero optional dependencies and no DB. Every validator is a pure
``(str) -> bool`` resolved by name from the YAML packs, so correctness here directly
gates the regex-detection layer's precision.
"""

from __future__ import annotations

import pytest

from app.redaction.validators import (
    VALIDATORS,
    edrpou_check,
    ein_check,
    get_validator,
    iban_mod97,
    ipn_check,
    itin_check,
    luhn,
    sin_check,
    ssn_check,
    uci_check,
)


class TestLuhn:
    @pytest.mark.parametrize(
        "value",
        [
            "4111111111111111",  # canonical Visa test number
            "79927398713",  # textbook Luhn-valid example
            "5500005555555559",  # MasterCard test number
            "0000000000000000",  # degenerate but mod-10 == 0
        ],
    )
    def test_known_valid(self, value: str) -> None:
        assert luhn(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "4111111111111112",  # last digit corrupted
            "79927398710",  # check digit changed
            "1234567812345678",  # not mod-10 == 0
        ],
    )
    def test_known_invalid(self, value: str) -> None:
        assert luhn(value) is False

    def test_strips_separators(self) -> None:
        # Spaces / hyphens must be stripped before the check runs.
        assert luhn("4111 1111 1111 1111") is True
        assert luhn("4111-1111-1111-1111") is True

    def test_rejects_non_digits(self) -> None:
        assert luhn("4111abcd11111111") is False

    def test_rejects_too_short(self) -> None:
        # length < 2 after cleaning is rejected.
        assert luhn("0") is False
        assert luhn("") is False


class TestSinCheck:
    def test_known_valid(self) -> None:
        # 193 456 787 starts with a non-0/8 digit and passes Luhn.
        assert sin_check("193 456 787") is True
        assert sin_check("130 692 544") is True

    def test_valid_with_hyphens_and_compact(self) -> None:
        assert sin_check("193-456-787") is True
        assert sin_check("193456787") is True

    def test_wrong_checksum_invalid(self) -> None:
        # Same shape, broken Luhn check digit.
        assert sin_check("193 456 788") is False
        assert sin_check("123 456 789") is False

    def test_rejects_leading_zero_and_eight(self) -> None:
        # Real-allocation rule: a SIN never starts with 0 or 8 even if Luhn passes.
        # 046 454 286 passes Luhn but is rejected on the leading-zero rule.
        assert luhn("046454286") is True
        assert sin_check("046 454 286") is False
        assert sin_check("846 454 286".replace(" ", "")) is False

    def test_rejects_wrong_length(self) -> None:
        assert sin_check("193 456 78") is False  # 8 digits
        assert sin_check("193 456 7870") is False  # 10 digits

    def test_rejects_non_digits(self) -> None:
        assert sin_check("19a 456 787") is False


class TestIbanMod97:
    def test_known_valid_gb(self) -> None:
        # ISO 13616 reference UK IBAN.
        assert iban_mod97("GB82 WEST 1234 5698 7654 32") is True
        assert iban_mod97("GB82WEST12345698765432") is True  # compact form

    def test_known_valid_de(self) -> None:
        assert iban_mod97("DE89 3704 0044 0532 0130 00") is True

    def test_corrupted_is_invalid(self) -> None:
        # Flip a single character in the account part: mod-97 no longer == 1.
        assert iban_mod97("GB82 WEST 1234 5698 7654 33") is False

    def test_bad_check_digits_invalid(self) -> None:
        assert iban_mod97("GB00 WEST 1234 5698 7654 32") is False

    def test_structural_reject(self) -> None:
        # Must start with two letters + two digits.
        assert iban_mod97("1B82 WEST 1234 5698 7654 32") is False
        assert iban_mod97("GBXX WEST 1234 5698 7654 32") is False
        assert iban_mod97("") is False

    def test_case_insensitive(self) -> None:
        assert iban_mod97("gb82 west 1234 5698 7654 32") is True


class TestUsValidators:
    def test_ssn_valid_and_invalid(self) -> None:
        assert ssn_check("123-45-6789") is True
        assert ssn_check("123456789") is True
        # never-issued ranges
        assert ssn_check("000-45-6789") is False  # area 000
        assert ssn_check("666-45-6789") is False  # area 666
        assert ssn_check("900-45-6789") is False  # area 9xx
        assert ssn_check("123-00-6789") is False  # group 00
        assert ssn_check("123-45-0000") is False  # serial 0000
        assert ssn_check("123-45-678") is False  # 8 digits
        assert ssn_check("1234567890") is False  # 10 digits

    def test_ein_valid_and_invalid(self) -> None:
        assert ein_check("12-3456789") is True
        # 07 is an invalid IRS campus prefix.
        assert ein_check("07-1234567") is False
        assert ein_check("89-1234567") is False
        assert ein_check("12-34567") is False  # too short

    def test_itin_valid_and_invalid(self) -> None:
        # Starts with 9, group digit in an allocated range (70..88 here).
        assert itin_check("900-70-0000") is True
        assert itin_check("912-88-1234") is True
        # group 10 is outside any allocated ITIN range.
        assert itin_check("900-10-0000") is False
        # does not start with 9.
        assert itin_check("812-70-0000") is False
        assert itin_check("9007000") is False  # too short


class TestUaAndIrccValidators:
    def test_edrpou(self) -> None:
        assert edrpou_check("12345678") is True
        assert edrpou_check("1234 5678") is True  # separators stripped
        assert edrpou_check("1234567") is False  # 7 digits
        assert edrpou_check("123456789") is False  # 9 digits
        assert edrpou_check("1234567a") is False

    def test_ipn(self) -> None:
        assert ipn_check("1234567890") is True
        assert ipn_check("123456789") is False  # 9 digits
        assert ipn_check("12345678901") is False  # 11 digits

    def test_uci(self) -> None:
        assert uci_check("12345678") is True  # 8 digits
        assert uci_check("1234567890") is True  # 10 digits
        assert uci_check("1234 5678") is True
        assert uci_check("1234567") is False  # 7 digits
        assert uci_check("12345678901") is False  # 11 digits


class TestRegistry:
    def test_registry_resolves_known_names(self) -> None:
        # Every name referenced by a YAML pack must resolve to a callable.
        for name in ("luhn", "sin_check", "iban_mod97", "ssn_check", "ein_check",
                     "itin_check", "edrpou_check", "ipn_check", "uci_check"):
            fn = get_validator(name)
            assert callable(fn)
            assert VALIDATORS[name] is fn

    def test_unknown_validator_is_none(self) -> None:
        assert get_validator("does_not_exist") is None

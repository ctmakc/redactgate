"""Pure check-digit / format validators for regulated identifiers.

Every validator is a pure function ``(str) -> bool``. They are referenced *by name*
from the YAML jurisdiction packs (the ``validator`` key on a pattern) and resolved at
load time by ``regex_packs.py``. A validator receives the raw regex match text; it MUST
strip its own separators (spaces / hyphens) before checking.

SECURITY: validators never log, print, or persist their input.
"""

from __future__ import annotations

import re
from collections.abc import Callable

Validator = Callable[[str], bool]

_NON_DIGIT_RE = re.compile(r"[\s-]")


def _digits(s: str) -> str:
    """Strip spaces and hyphens, returning only what remains (kept as-is otherwise)."""
    return _NON_DIGIT_RE.sub("", s)


def luhn(s: str) -> bool:
    """Luhn (mod-10) check over the digits in ``s``.

    Used for credit cards and (over 9 digits) the Canadian SIN. Returns False unless the
    cleaned string is all digits and length >= 2.
    """
    cleaned = _digits(s)
    if len(cleaned) < 2 or not cleaned.isdigit():
        return False
    total = 0
    # Walk right-to-left; double every second digit.
    for i, ch in enumerate(reversed(cleaned)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def sin_check(s: str) -> bool:
    """Canadian Social Insurance Number: exactly 9 digits passing the Luhn checksum."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    # A SIN never starts with 0 or 8 in the real allocation scheme.
    if cleaned[0] in {"0", "8"}:
        return False
    return luhn(cleaned)


# IBAN: move the 4 leading chars to the end, map letters A..Z -> 10..35, mod-97 == 1.
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$")


def iban_mod97(s: str) -> bool:
    """ISO 13616 IBAN check: structural shape + mod-97 == 1 over the rearranged string."""
    cleaned = _digits(s).upper()
    if not _IBAN_RE.match(cleaned):
        return False
    rearranged = cleaned[4:] + cleaned[:4]
    converted_chars: list[str] = []
    for ch in rearranged:
        if ch.isdigit():
            converted_chars.append(ch)
        else:
            converted_chars.append(str(ord(ch) - 55))  # 'A' (65) -> 10
    try:
        return int("".join(converted_chars)) % 97 == 1
    except ValueError:  # pragma: no cover - guarded by the regex above
        return False


def ssn_check(s: str) -> bool:
    """US SSN format sanity: 9 digits, none of the never-issued area/group/serial codes."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    area, group, serial = cleaned[:3], cleaned[3:5], cleaned[5:]
    if area in {"000", "666"} or area[0] == "9":
        return False
    if group == "00" or serial == "0000":
        return False
    return True


def ein_check(s: str) -> bool:
    """US EIN format sanity: 9 digits with a valid IRS campus prefix (first two digits)."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    invalid_prefixes = {"07", "08", "09", "17", "18", "19", "28", "29", "49", "78", "79", "89"}
    return cleaned[:2] not in invalid_prefixes


def itin_check(s: str) -> bool:
    """US ITIN format sanity: 9 digits starting with 9; group digit in the IRS range."""
    cleaned = _digits(s)
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    if cleaned[0] != "9":
        return False
    group = int(cleaned[3:5])
    # ITIN group ranges historically allocated by the IRS.
    return 50 <= group <= 65 or 70 <= group <= 88 or 90 <= group <= 92 or 94 <= group <= 99


def edrpou_check(s: str) -> bool:
    """Ukrainian ЄДРПОУ (legal-entity code): exactly 8 digits."""
    cleaned = _digits(s)
    return len(cleaned) == 8 and cleaned.isdigit()


def ipn_check(s: str) -> bool:
    """Ukrainian ІПН / РНОКПП (individual tax number): exactly 10 digits."""
    cleaned = _digits(s)
    return len(cleaned) == 10 and cleaned.isdigit()


def uci_check(s: str) -> bool:
    """IRCC UCI / client ID: 8 to 10 digits."""
    cleaned = _digits(s)
    return 8 <= len(cleaned) <= 10 and cleaned.isdigit()


# ── Switzerland (CH pack) ─────────────────────────────────────────────────────


def ahv_ean13(s: str) -> bool:
    """Swiss AHV/AVS social-security number (756.XXXX.XXXX.XC).

    13 digits, country prefix 756, last digit is an EAN-13 check digit: weights 1,3,1,3...
    over the first 12 digits from the left, check = (10 - sum % 10) % 10.
    """
    cleaned = re.sub(r"[\s.\-]", "", s)
    if len(cleaned) != 13 or not cleaned.isdigit() or not cleaned.startswith("756"):
        return False
    total = sum((ord(ch) - 48) * (3 if i % 2 else 1) for i, ch in enumerate(cleaned[:12]))
    return (10 - total % 10) % 10 == ord(cleaned[12]) - 48


def ch_iban(s: str) -> bool:
    """Swiss / Liechtenstein IBAN: CH or LI prefix, exactly 21 chars, ISO 13616 mod-97."""
    cleaned = _digits(s).upper()
    if len(cleaned) != 21 or cleaned[:2] not in {"CH", "LI"}:
        return False
    return iban_mod97(cleaned)


_UID_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)


def ch_uid(s: str) -> bool:
    """Swiss enterprise identification number UID / IDE / IDI (CHE-123.456.789).

    Nine digits; the ninth is a mod-11 check digit over the first eight with weights
    5,4,3,2,7,6,5,4: check = 11 - (sum % 11); 11 maps to 0 and 10 is never issued.
    """
    cleaned = re.sub(r"[\s.\-]", "", s).upper()
    if cleaned.startswith("CHE"):
        cleaned = cleaned[3:]
    if len(cleaned) != 9 or not cleaned.isdigit():
        return False
    total = sum((ord(ch) - 48) * w for ch, w in zip(cleaned[:8], _UID_WEIGHTS, strict=True))
    check = 11 - (total % 11)
    if check == 11:
        check = 0
    if check == 10:
        return False
    return check == ord(cleaned[8]) - 48


def ch_phone(s: str) -> bool:
    """Swiss phone number (+41 / 0041 / 0 trunk prefix + 9-digit national number).

    Accepts the optional ``(0)`` trunk marker used in print (``+41 (0)44 ...``). The
    national significant number must be exactly 9 digits and must not start with 0.
    """
    cleaned = re.sub(r"[\s.\-/]", "", s).replace("(0)", "")
    if cleaned.startswith("+41"):
        national = cleaned[3:]
    elif cleaned.startswith("0041"):
        national = cleaned[4:]
    elif cleaned.startswith("0"):
        national = cleaned[1:]
    else:
        return False
    return len(national) == 9 and national.isdigit() and national[0] != "0"


# Registry consumed by regex_packs.py to resolve a pattern's ``validator`` name.
VALIDATORS: dict[str, Validator] = {
    "luhn": luhn,
    "sin_check": sin_check,
    "iban_mod97": iban_mod97,
    "ssn_check": ssn_check,
    "ein_check": ein_check,
    "itin_check": itin_check,
    "edrpou_check": edrpou_check,
    "ipn_check": ipn_check,
    "uci_check": uci_check,
    "ahv_ean13": ahv_ean13,
    "ch_iban": ch_iban,
    "ch_uid": ch_uid,
    "ch_phone": ch_phone,
}


def get_validator(name: str) -> Validator | None:
    """Resolve a validator function by its pack-referenced name, or None if unknown."""
    return VALIDATORS.get(name)

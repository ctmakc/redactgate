"""Text normalization applied before detection.

SECURITY: without this, an attacker evades the ASCII-oriented regex packs with Unicode
look-alikes — full-width digits (``１３０``), a NBSP/zero-width char inside a SIN/card
number, etc. — and the raw value sails through to the upstream LLM. Normalizing to NFKC
and stripping zero-width/format characters folds those tricks back to the canonical form
the packs match. The normalized text is what gets tokenized and forwarded, so the upstream
never sees the evasion form either.

Beyond NFKC this also:

* drops EVERY Unicode format character (category ``Cf`` — zero-width chars, bidi
  overrides/isolates, invisible math operators, tag characters, ...) plus the invisible
  combining marks/fillers NFKC leaves alone (variation selectors, CGJ, Hangul fillers);
* folds every Unicode dash / minus look-alike to ASCII ``-`` (the packs only accept
  ``[ -]`` separators, so ``193–456–787`` with EN DASHes otherwise escaped detection);
* folds every Unicode decimal digit (``Nd``: Arabic-Indic, Devanagari, Bengali, ...) to
  its ASCII digit. Python's ``\\d`` already *matches* those digits, but the check-digit
  validators do ASCII arithmetic, so e.g. an Arabic-Indic SIN was matched then rejected.
"""

from __future__ import annotations

import unicodedata

# Invisible characters that are NOT category Cf (those are all stripped generically).
_INVISIBLE_EXTRA: frozenset[int] = frozenset(
    {
        0x034F,  # combining grapheme joiner
        0x115F,  # hangul choseong filler
        0x1160,  # hangul jungseong filler
        0x17B4,  # khmer vowel inherent aq
        0x17B5,  # khmer vowel inherent aa
        0x180B,  # mongolian free variation selector one
        0x180C,  # mongolian free variation selector two
        0x180D,  # mongolian free variation selector three
        0x180F,  # mongolian free variation selector four
        0x3164,  # hangul filler
        0xFFA0,  # halfwidth hangul filler
        *range(0xFE00, 0xFE10),  # variation selectors 1-16
        *range(0xE0100, 0xE01F0),  # variation selectors 17-256
    }
)

# Dash / hyphen / minus look-alikes that NFKC does not fold to ASCII '-'.
_DASHES: frozenset[int] = frozenset(
    {
        0x2010,  # hyphen
        0x2011,  # non-breaking hyphen
        0x2012,  # figure dash
        0x2013,  # en dash
        0x2014,  # em dash
        0x2015,  # horizontal bar
        0x2043,  # hyphen bullet
        0x2212,  # minus sign
        0x2796,  # heavy minus sign
        0x2E3A,  # two-em dash
        0x2E3B,  # three-em dash
        0xFE58,  # small em dash
        0xFE63,  # small hyphen-minus
        0xFF0D,  # fullwidth hyphen-minus
    }
)


def _fold_char(ch: str) -> str:
    cp = ord(ch)
    if cp < 0x80:
        return ch
    if cp in _INVISIBLE_EXTRA:
        return ""
    if cp in _DASHES:
        return "-"
    cat = unicodedata.category(ch)
    if cat == "Cf":
        return ""
    if cat == "Nd":
        return str(unicodedata.decimal(ch))
    if cat == "Pd":  # any other dash punctuation
        return "-"
    return ch


def normalize_text(text: str) -> str:
    """NFKC-normalize, strip invisible chars, fold dashes/digits (idempotent, never raises).

    NFKC folds full-width/compatibility forms to ASCII and converts NBSP (U+00A0) and
    other Unicode spaces to a regular space, so the jurisdiction regexes see canonical
    separators. Invisible characters are removed outright.
    """
    if not text:
        return text
    normalized = unicodedata.normalize("NFKC", text)
    if normalized.isascii():
        return normalized
    folded = "".join(_fold_char(ch) for ch in normalized)
    # Removing an invisible char can bring a base char and a combining mark together
    # (``e<ZWSP><U+0301>``); re-compose so the function stays idempotent.
    return unicodedata.normalize("NFKC", folded)

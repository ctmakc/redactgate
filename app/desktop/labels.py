"""Human-readable reversible labels: «Фізособа-1», «Юрособа-2», «Рахунок-3»…

RedactGate's API gateway uses opaque ``[[TYPE_hex]]`` placeholders because a machine reads
them. A person who carries anonymized documents into a chat window needs labels an LLM
(and an auditor) reads as plain words, so the desktop app numbers entities per project and
renders the label in the project's language (uk / ru / en).

Back-mapping is deliberately tolerant: an LLM writing Ukrainian or Russian declines the
label («договір з Фізособою-1», «счёт Юрлица-2»), may switch the label language or write it
in capitals. :func:`find_labels` recognises any of these forms and returns the canonical
``(category, number)`` key; :func:`render` produces the label for a given language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

LANGS = ("uk", "ru", "en")

# Engine entity type (RedactGate packs / desktop rules) -> desktop label category.
CATEGORY_OF: dict[str, str] = {
    "PERSON": "PERSON",
    "ORG": "ORG",
    "IBAN": "ACCOUNT",
    "IBAN_UA": "ACCOUNT",
    "BANK_ACCOUNT": "ACCOUNT",
    "ACCOUNT": "ACCOUNT",
    "CREDIT_CARD": "CARD",
    "CARD": "CARD",
    "EDRPOU": "EDRPOU",
    "IPN": "IPN",
    "SIN": "SIN",
    "SSN": "SSN",
    "EIN": "EIN",
    "ITIN": "ITIN",
    "BUSINESS_NUMBER": "BN",
    "GST_HST": "BN",
    "NEQ": "BN",
    "BN": "BN",
    "VAT": "VAT",
    "UCI": "UCI",
    "PASSPORT": "PASSPORT",
    "ADDRESS": "ADDRESS",
    "LOCATION": "ADDRESS",
    "PHONE": "PHONE",
    "EMAIL": "EMAIL",
    "AMOUNT": "AMOUNT",
    "DATE_OF_BIRTH": "DOB",
    "DOB": "DOB",
    "IP_ADDRESS": "IP",
    "IP_ADDRESS_V6": "IP",
    "IP": "IP",
}

# category -> label word per language
WORDS: dict[str, dict[str, str]] = {
    "PERSON": {"uk": "Фізособа", "ru": "Физлицо", "en": "Person"},
    "ORG": {"uk": "Юрособа", "ru": "Юрлицо", "en": "Company"},
    "ACCOUNT": {"uk": "Рахунок", "ru": "Счёт", "en": "Account"},
    "CARD": {"uk": "Картка", "ru": "Карта", "en": "Card"},
    "EDRPOU": {"uk": "ЄДРПОУ", "ru": "ЕГРПОУ", "en": "EDRPOU"},
    "IPN": {"uk": "ІПН", "ru": "ИНН", "en": "TaxID"},
    "SIN": {"uk": "SIN", "ru": "SIN", "en": "SIN"},
    "SSN": {"uk": "SSN", "ru": "SSN", "en": "SSN"},
    "EIN": {"uk": "EIN", "ru": "EIN", "en": "EIN"},
    "ITIN": {"uk": "ITIN", "ru": "ITIN", "en": "ITIN"},
    "BN": {"uk": "BN", "ru": "BN", "en": "BN"},
    "VAT": {"uk": "ПДВ", "ru": "НДС", "en": "VAT"},
    "UCI": {"uk": "UCI", "ru": "UCI", "en": "UCI"},
    "PASSPORT": {"uk": "Паспорт", "ru": "Паспорт", "en": "Passport"},
    "ADDRESS": {"uk": "Адреса", "ru": "Адрес", "en": "Address"},
    "PHONE": {"uk": "Телефон", "ru": "Телефон", "en": "Phone"},
    "EMAIL": {"uk": "Пошта", "ru": "Почта", "en": "Email"},
    "AMOUNT": {"uk": "Сума", "ru": "Сумма", "en": "Amount"},
    "DOB": {"uk": "ДатаНародження", "ru": "ДатаРождения", "en": "BirthDate"},
    "IP": {"uk": "IP", "ru": "IP", "en": "IP"},
}

# Human names of categories for the UI (labels screen, options).
CATEGORY_TITLES: dict[str, dict[str, str]] = {
    "PERSON": {"uk": "Фізичні особи", "ru": "Физлица", "en": "People"},
    "ORG": {"uk": "Юридичні особи", "ru": "Юрлица", "en": "Companies"},
    "ACCOUNT": {"uk": "Рахунки, IBAN", "ru": "Счета, IBAN", "en": "Accounts, IBAN"},
    "CARD": {"uk": "Банківські картки", "ru": "Банковские карты", "en": "Bank cards"},
    "EDRPOU": {"uk": "ЄДРПОУ", "ru": "ЕГРПОУ", "en": "EDRPOU"},
    "IPN": {"uk": "ІПН / РНОКПП", "ru": "ИНН", "en": "Tax ID (IPN)"},
    "SIN": {"uk": "SIN (Канада)", "ru": "SIN (Канада)", "en": "SIN (Canada)"},
    "SSN": {"uk": "SSN (США)", "ru": "SSN (США)", "en": "SSN (US)"},
    "EIN": {"uk": "EIN (США)", "ru": "EIN (США)", "en": "EIN (US)"},
    "ITIN": {"uk": "ITIN (США)", "ru": "ITIN (США)", "en": "ITIN (US)"},
    "BN": {"uk": "BN / GST (Канада)", "ru": "BN / GST (Канада)", "en": "BN / GST (Canada)"},
    "VAT": {"uk": "ПДВ-номер (ЄС)", "ru": "VAT-номер (ЕС)", "en": "VAT number (EU)"},
    "UCI": {"uk": "UCI (IRCC)", "ru": "UCI (IRCC)", "en": "UCI (IRCC)"},
    "PASSPORT": {"uk": "Паспорти", "ru": "Паспорта", "en": "Passports"},
    "ADDRESS": {"uk": "Адреси", "ru": "Адреса", "en": "Addresses"},
    "PHONE": {"uk": "Телефони", "ru": "Телефоны", "en": "Phones"},
    "EMAIL": {"uk": "Електронна пошта", "ru": "Электронная почта", "en": "Emails"},
    "AMOUNT": {"uk": "Суми", "ru": "Суммы", "en": "Amounts"},
    "DOB": {"uk": "Дати народження", "ru": "Даты рождения", "en": "Dates of birth"},
    "IP": {"uk": "IP-адреси", "ru": "IP-адреса", "en": "IP addresses"},
}

ALL_CATEGORIES: tuple[str, ...] = tuple(WORDS)

# Categories that are OFF unless the user turns them on (an auditor needs the amounts).
DEFAULT_OFF: frozenset[str] = frozenset({"AMOUNT"})

# Stems used to recognise a label in any language and any grammatical case.
# Each entry: (regex stem incl. optional inflection, category).
_STEMS: list[tuple[str, str]] = [
    (r"Фізособ\w{0,3}", "PERSON"),
    (r"Физлиц\w{0,3}", "PERSON"),
    (r"Persons?(?:'s)?", "PERSON"),
    (r"Юрособ\w{0,3}", "ORG"),
    (r"Юрлиц\w{0,3}", "ORG"),
    (r"Compan(?:y|ies)(?:'s)?", "ORG"),
    (r"Рахун\w{0,3}", "ACCOUNT"),
    (r"Сч[её]т\w{0,3}", "ACCOUNT"),
    (r"Accounts?", "ACCOUNT"),
    (r"Карт\w{0,4}", "CARD"),
    (r"Cards?", "CARD"),
    (r"ЄДРПОУ|ЕГРПОУ|ЕДРПОУ|EDRPOU", "EDRPOU"),
    (r"ІПН|ИНН|TaxID", "IPN"),
    (r"SIN", "SIN"),
    (r"SSN", "SSN"),
    (r"EIN", "EIN"),
    (r"ITIN", "ITIN"),
    (r"BN", "BN"),
    (r"ПДВ|НДС|VAT", "VAT"),
    (r"UCI", "UCI"),
    (r"Паспорт\w{0,3}", "PASSPORT"),
    (r"Passports?", "PASSPORT"),
    (r"Адрес\w{0,3}", "ADDRESS"),
    (r"Address(?:es)?", "ADDRESS"),
    (r"Телефон\w{0,3}", "PHONE"),
    (r"Phones?", "PHONE"),
    (r"Пошт\w{0,3}", "EMAIL"),
    (r"Почт\w{0,3}", "EMAIL"),
    (r"E-?mails?", "EMAIL"),
    (r"Сум\w{0,4}", "AMOUNT"),
    (r"Amounts?", "AMOUNT"),
    (r"ДатаНародж\w{0,4}|ДатаРожд\w{0,4}|Дат\w{0,2}Народж\w{0,4}|BirthDates?", "DOB"),
    (r"IP", "IP"),
]

_HYPHEN = "\\-\u2010\u2011\u2012\u2013\u2014"

_LABEL_RE = re.compile(
    r"(?<![\w" + _HYPHEN + r"])(?P<stem>"
    + "|".join(f"(?:{s})" for s, _ in _STEMS)
    + r")[" + _HYPHEN + r"](?P<n>\d{1,6})(?![\d\w])",
    re.IGNORECASE,
)
_STEM_RES = [(re.compile(rf"^(?:{s})$", re.IGNORECASE), c) for s, c in _STEMS]


@dataclass(frozen=True, slots=True)
class LabelHit:
    start: int
    end: int
    category: str
    number: int
    text: str


def category_for(entity_type: str) -> str:
    return CATEGORY_OF.get(entity_type.upper(), entity_type.upper())


def render(category: str, number: int, lang: str = "uk") -> str:
    words = WORDS.get(category)
    word = words.get(lang, words["en"]) if words else category.title()
    return f"{word}-{number}"


def _stem_category(stem: str) -> str | None:
    for rx, cat in _STEM_RES:
        if rx.match(stem):
            return cat
    return None


def find_labels(text: str) -> list[LabelHit]:
    """Every label occurrence in ``text`` (any language, any case ending)."""
    hits: list[LabelHit] = []
    for m in _LABEL_RE.finditer(text):
        cat = _stem_category(m.group("stem"))
        if cat is None:
            continue
        hits.append(LabelHit(m.start(), m.end(), cat, int(m.group("n")), m.group(0)))
    return hits


def is_label(text: str) -> bool:
    hits = find_labels(text.strip())
    return len(hits) == 1 and hits[0].start == 0 and hits[0].end == len(text.strip())

"""Held-out check: hand-written snippets in styles the synthetic generator never produces.

The generator and the rules were written by the same hand, so a 100% score there proves
little. These snippets use other phrasing: names without patronymics, surname-only
mentions, lists of people, Latin transliteration, companies without legal forms, English
contracts, odd spacing. Each snippet lists what must disappear. We report recall per
category AND false positives (things labelled that are not sensitive).

    python eval/desktop/heldout.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.desktop.engine import Anonymizer, Options  # noqa: E402
from app.desktop.vaultdb import LabelVault  # noqa: E402

CASES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Призначення: оплата за консультацію згідно рах. 15, отримувач Гаврилюк Степан, тел. 067 555 12 34",
     [("PERSON", "Гаврилюк Степан"), ("PHONE", "067 555 12 34")]),
    ("Присутні: Іваненко, Петренко та Сидоренко. Протокол вела Коваль О.",
     [("PERSON", "Іваненко"), ("PERSON", "Петренко"), ("PERSON", "Сидоренко"), ("PERSON", "Коваль О.")]),
    ("Передати документи Остапові Вишні до п'ятниці.", [("PERSON", "Остапові Вишні")]),
    ("Платник: PETRENKO IVAN, картка 5168 7573 1234 5674", [("PERSON", "PETRENKO IVAN"), ("CARD", "5168 7573 1234 5674")]),
    ("Компанія Ромашка постачає молоко з 2019 року.", [("ORG", "Ромашка")]),
    ("Між ТОВ \"Зелений Гай Агро\" та АТ «Укрнафта» укладено додаткову угоду.",
     [("ORG", "Зелений Гай Агро"), ("ORG", "Укрнафта")]),
    ("This Agreement is made between Harbour Light Consulting Inc. and Ms. Priya Raman, residing at 44 Elgin Street, Ottawa, ON K2P 1L4.",
     [("ORG", "Harbour Light Consulting"), ("PERSON", "Priya Raman"), ("ADDRESS", "44 Elgin Street, Ottawa, ON K2P 1L4")]),
    ("Contact Kwame Mensah (kwame.m@example.org) or call (416) 555-0199.",
     [("PERSON", "Kwame Mensah"), ("EMAIL", "kwame.m@example.org"), ("PHONE", "(416) 555-0199")]),
    ("Директор ТОВ «Сонячна Енергія» Мирошниченко Валентина Олегівна, РНОКПП 2987654321",
     [("ORG", "Сонячна Енергія"), ("PERSON", "Мирошниченко Валентина Олегівна"), ("IPN", "2987654321")]),
    ("Кошти перераховано на р/р UA90 3052 9900 0002 6006 0108 3729 1 у ПриватБанку.",
     [("ACCOUNT", "UA90 3052 9900 0002 6006 0108 3729 1"), ("ORG", "ПриватБанку")]),
    ("Заявитель: Кузнецова Мария Петровна, паспорт серии МК № 123456, адрес: г. Харьков, ул. Сумская, д. 10, кв. 4",
     [("PERSON", "Кузнецова Мария Петровна"), ("PASSPORT", "МК № 123456"), ("ADDRESS", "г. Харьков, ул. Сумская, д. 10, кв. 4")]),
    ("Звонил Андрей из ООО Вектор, просил перезвонить на +7 912 345-67-89",
     [("PERSON", "Андрей"), ("ORG", "Вектор"), ("PHONE", "+7 912 345-67-89")]),
    ("Employee: O'Connor, Siobhan — SIN 130 692 544, start date 2026-03-01",
     [("PERSON", "O'Connor, Siobhan"), ("SIN", "130 692 544")]),
    ("Рахунок-фактура № 77 від ФОП Шевчук Т.В. на суму 12 400 грн", [("PERSON", "Шевчук Т.В.")]),
    ("У відомості: Бондар Лілія — 15 000; Ткачук Назар — 18 500; Яремчук Христина — 21 000.",
     [("PERSON", "Бондар Лілія"), ("PERSON", "Ткачук Назар"), ("PERSON", "Яремчук Христина")]),
    ("Поставщик: ИП Абрамов Р.С., ИНН 7701234567", [("PERSON", "Абрамов Р.С."), ("IPN", "7701234567")]),
    ("Акт звірки з ПП «Будпостач-Захід» (ЄДРПОУ 35123478) підписано 01.11.2026.",
     [("ORG", "Будпостач-Захід"), ("EDRPOU", "35123478")]),
    ("Пишіть на office@zelenyi-gai.ua або dir.zelenyi@gmail.com", [("EMAIL", "office@zelenyi-gai.ua"), ("EMAIL", "dir.zelenyi@gmail.com")]),
    ("Account holder: Wei Zhang, account 0012-4456789, transit 00102, Bank of Montreal",
     [("PERSON", "Wei Zhang"), ("ACCOUNT", "0012-4456789"), ("ORG", "Bank of Montreal")]),
    ("Від імені клієнта діє адвокат Литвин Богдан Петрович на підставі ордера.",
     [("PERSON", "Литвин Богдан Петрович")]),
    ("Склад комісії: голова — Савченко Ірина, члени — Мельничук Тарас, Гордієнко Аліна.",
     [("PERSON", "Савченко Ірина"), ("PERSON", "Мельничук Тарас"), ("PERSON", "Гордієнко Аліна")]),
    ("Pay to the order of Northern Lights Bakery Ltd., 1200 Bay St, Toronto ON M5R 2A5",
     [("ORG", "Northern Lights Bakery"), ("ADDRESS", "1200 Bay St, Toronto ON M5R 2A5")]),
    ("Оренда: м. Львів, просп. Свободи, 28, оф. 12. Орендодавець Возняк Ігор Романович.",
     [("ADDRESS", "м. Львів, просп. Свободи, 28, оф. 12"), ("PERSON", "Возняк Ігор Романович")]),
    ("Підтверджую, Дмитро Олексієнко, 12.10.2026", [("PERSON", "Дмитро Олексієнко")]),
    ("SSN 123-45-6789 belongs to the claimant John Q. Public of Springfield.", [("SSN", "123-45-6789"), ("PERSON", "John Q. Public")]),
]
# v2: written AFTER the rules were tuned on CASES above; never used for tuning.
CASES_V2: list[tuple[str, list[tuple[str, str]]]] = [
    ("Довіреність видана громадянці Кравченко Ользі Миколаївні на отримання коштів.",
     [("PERSON", "Кравченко Ользі Миколаївні")]),
    ("Отримано від Захарченка 5 000 грн готівкою.", [("PERSON", "Захарченка")]),
    ("ТзОВ «Галицькі Пекарні» сплатило рахунок Юрію Дмитрук.",
     [("ORG", "Галицькі Пекарні"), ("PERSON", "Юрію Дмитрук")]),
    ("Invoice to Maple & Birch Landscaping LLC, attention: Mr. Thomas Leblanc",
     [("ORG", "Maple & Birch Landscaping"), ("PERSON", "Thomas Leblanc")]),
    ("Ваш менеджер — Олена, телефон 093-111-22-33", [("PERSON", "Олена"), ("PHONE", "093-111-22-33")]),
    ("Банк одержувача: АТ «Сенс Банк», IBAN UA31 3805 0500 0002 6007 1234 5678 9",
     [("ORG", "Сенс Банк"), ("ACCOUNT", "UA31 3805 0500 0002 6007 1234 5678 9")]),
    ("Згідно з наказом, бухгалтер Пономаренко має закрити період до 5 числа.", [("PERSON", "Пономаренко")]),
    ("Grantor: Elena Rossi; Grantee: Blue Fjord Holdings Ltd.", [("PERSON", "Elena Rossi"), ("ORG", "Blue Fjord Holdings")]),
    ("Заказчик: Общество с ограниченной ответственностью «ТехСтрой-М», ИНН 7724567890",
     [("ORG", "ТехСтрой-М"), ("IPN", "7724567890")]),
    ("Підписи сторін: _______ С. В. Литвиненко   _______ А. Р. Кушнір",
     [("PERSON", "С. В. Литвиненко"), ("PERSON", "А. Р. Кушнір")]),
    ("Переказ на картку 4149 4991 0000 1234 від Марченко Діани", [("CARD", "4149 4991 0000 1234"), ("PERSON", "Марченко Діани")]),
    ("Адреса доставки: вул. Городоцька 174, Львів 79022", [("ADDRESS", "вул. Городоцька 174, Львів 79022")]),
    ("Lease signed by tenant Ahmed Hassan at 900 Wellington Ave, Winnipeg, MB R3M 1E6",
     [("PERSON", "Ahmed Hassan"), ("ADDRESS", "900 Wellington Ave, Winnipeg, MB R3M 1E6")]),
    ("Від ФОП Гуменюк Р. надійшла претензія щодо поставки.", [("PERSON", "Гуменюк Р.")]),
    ("E-mail бухгалтерії: accounting@budpostach.com.ua, ЄДРПОУ: 41234567",
     [("EMAIL", "accounting@budpostach.com.ua"), ("EDRPOU", "41234567")]),
    ("Зустріч з Андрієм Коваленком перенесено на вівторок.", [("PERSON", "Андрієм Коваленком")]),
    ("Субпідрядник ПрАТ Київбудтрест виконав роботи частково.", [("ORG", "Київбудтрест")]),
    ("Пенсионер Сергеев Николай, СНИЛС не указан, телефон +38 050 765 43 21",
     [("PERSON", "Сергеев Николай"), ("PHONE", "+38 050 765 43 21")]),
    ("The claimant, Ms. Haruka Tanaka, disputes the charge.", [("PERSON", "Haruka Tanaka")]),
    ("Оплата за оренду від Сидорчук Наталії Іванівни, р/р 26000052345678",
     [("PERSON", "Сидорчук Наталії Іванівни"), ("ACCOUNT", "26000052345678")]),
]
# Plain text that must stay untouched (false-positive bait).
NEGATIVE = [
    "Виручка за жовтень 2026 року зросла на 12 % порівняно з вереснем.",
    "Нова Пошта доставила посилку в понеділок.",
    "Слава Україні! Героям слава!",
    "The Board approved the Annual Report for fiscal year 2025.",
    "Податкова декларація з ПДВ подається до 20 числа.",
    "Total Amount Due: see Schedule A.",
    "Київська область, Бориспільський район — зона доставки.",
    "Договір набирає чинності з моменту підписання Сторонами.",
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rg-heldout-"))
    v = LabelVault(tmp / "v.db")
    v.initialize("heldout-1")
    anon = Anonymizer(v)
    caught: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misses = []
    fps = []
    for i, (text, gold) in enumerate(CASES):
        pid = v.create_project(f"case {i}")
        out, _st, reps = anon.anonymize_text(pid, text, lang="uk", options=Options(), record_as=None)
        for cat, val in gold:
            caught[cat][1] += 1
            if val in out:
                misses.append({"case": i, "category": cat, "value": val, "output": out})
            else:
                caught[cat][0] += 1
        gold_spans = []
        for _c, val in gold:
            s = text.find(val)
            if s >= 0:
                gold_spans.append((s, s + len(val)))
        for r in reps:
            if not any(r.start < e and s < r.end for s, e in gold_spans):
                fps.append({"case": i, "labelled": r.original, "as": r.category})
    for j, text in enumerate(NEGATIVE):
        pid = v.create_project(f"neg {j}")
        out, _st, reps = anon.anonymize_text(pid, text, lang="uk", options=Options(), record_as=None)
        for r in reps:
            fps.append({"negative": j, "labelled": r.original, "as": r.category})
    caught2: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misses2 = []
    for i, (text, gold) in enumerate(CASES_V2):
        pid = v.create_project(f"v2 case {i}")
        out, _st, reps = anon.anonymize_text(pid, text, lang="uk", options=Options(), record_as=None)
        for cat, val in gold:
            caught2[cat][1] += 1
            if val in out:
                misses2.append({"case": i, "category": cat, "value": val, "output": out})
            else:
                caught2[cat][0] += 1
        gold_spans = [(text.find(val), text.find(val) + len(val)) for _c, val in gold if val in text]
        for r in reps:
            if not any(r.start < e and s < r.end for s, e in gold_spans):
                fps.append({"v2_case": i, "labelled": r.original, "as": r.category})
    tot_c = sum(c for c, _ in caught.values())
    tot_t = sum(t for _, t in caught.values())
    report = {
        "recall": {k: f"{c}/{t}" for k, (c, t) in sorted(caught.items())},
        "overall_recall": round(tot_c / tot_t, 3),
        "person_org_account_recall": round(
            sum(caught[k][0] for k in ("PERSON", "ORG", "ACCOUNT")) / sum(caught[k][1] for k in ("PERSON", "ORG", "ACCOUNT")), 3),
        "misses": misses, "false_positives": fps,
        "v2_recall": {k: f"{c}/{t}" for k, (c, t) in sorted(caught2.items())},
        "v2_overall_recall": round(sum(c for c, _ in caught2.values()) / sum(t for _, t in caught2.values()), 3),
        "v2_person_org_account_recall": round(
            sum(caught2[k][0] for k in ("PERSON", "ORG", "ACCOUNT")) / sum(caught2[k][1] for k in ("PERSON", "ORG", "ACCOUNT")), 3),
        "v2_misses": misses2,
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    (ROOT / "eval" / "desktop" / "heldout_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

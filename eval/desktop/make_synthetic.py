"""Synthetic auditor case: bank statements, contracts, acts, invoices — all fake.

Every person, company, code and account here is generated (seeded RNG). Nothing comes
from real clients. The generator records each sensitive mention it writes, so the e2e
test can measure recall per category and check that nothing leaks.

Usage:  python eval/desktop/make_synthetic.py OUT_DIR [--seed 7] [--variant demo|eval]
Writes OUT_DIR/*.{csv,xlsx,docx,pdf,txt,png} and OUT_DIR/ground_truth.json.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
from pathlib import Path

SURNAMES_M = """Петренко Коваленко Бондаренко Шевчук Ткаченко Кравчук Олійник Мельник Савчук Гнатюк
Лисенко Мороз Ткач Гончар Кравець Бойко Поліщук Марченко Руденко Литвиненко Зайченко Остапчук
Дорошенко Федорчук Головатий Сидоряк Пилипишин Возняк Тимошенко Яковець Чорновол Гуменюк""".split()
SURNAMES_RU = """Иванов Соколов Кузнецов Попов Лебедев Новиков Морозов Волков Зайцев Павлов""".split()
FIRST_M = "Іван Петро Олександр Андрій Сергій Микола Василь Тарас Богдан Роман Юрій Остап".split()
FIRST_F = "Олена Ірина Наталія Тетяна Оксана Марія Галина Світлана Юлія Катерина Леся Христина".split()
# rarer names — deliberately NOT in the app's gazetteer, to keep the test honest
RARE_M = "Зеновій Ярема Мирон Іларіон Добромир Леонтій Северин Аркадій".split()
RARE_F = "Устина Орися Мирослава Ярина Олеся Божена Стефанія Таїсія".split()
EN_FIRST = "Daniel Sarah Michael Emily Robert Laura James Olivia Peter Grace".split()
EN_LAST = "Thompson MacLeod Fraser Tremblay Gagnon Wilson Bouchard Morrison Campbell Anderson".split()

ROOTS = "Агро Техно Буд Енерго Інвест Логістик Фарм Мет Трансі Дніпро Карпат Поділ Хліб Світ Нафто Еко".split()
TAILS = "Трейд Сервіс Груп Альянс Постач Плюс Інжиніринг Капітал Рітейл Продукт Систем Консалт".split()
EN_ROOTS = "Northwind Bluewater Maplecrest Granite Silverline Harbourview Redwood Cedar Pinnacle".split()
EN_TAILS = "Logistics Solutions Holdings Analytics Foods Consulting Systems".split()
STREETS = "Хрещатик Січових Стрільців Сагайдачного Шевченка Франка Лесі Українки Гончара Велика Васильківська Бандери".split()
CITIES = ["Київ", "Львів", "Харків", "Дніпро", "Одеса", "Вінниця", "Полтава", "Житомир"]
BANKS = ["ПриватБанк", "Ощадбанк", "Укрсиббанк", "Райффайзен Банк", "ПУМБ", "Кредобанк"]
BANK_MFO = ["305299", "300465", "351005", "380805", "334851", "325365"]


def patronymic(first: str, female: bool) -> str:
    base = first
    if base.endswith("й"):
        base = base[:-1]
        return base + ("ївна" if female else "йович")
    if base.endswith("о"):
        base = base[:-1]
    if base.endswith("а"):
        base = base[:-1]
    return base + ("івна" if female else "ович")


def fem_surname(s: str) -> str:
    if s.endswith(("ий",)):
        return s[:-2] + "а"
    if s.endswith(("ов", "ев", "ін", "ин")):
        return s + "а"
    return s


def gen_surname_genitive(s: str, female: bool) -> str:
    if female:
        if s.endswith("а"):
            return s[:-1] + "ої" if s.endswith("ька") or s.endswith("ая") else s[:-1] + "и"
        return s
    if s.endswith("ко"):
        return s[:-1] + "а"
    if s.endswith("ий"):
        return s[:-2] + "ого"
    if s[-1] in "бвгґджзклмнпрстфхцчшщ":
        return s + "а"
    return s


def gen_first_genitive(f: str, female: bool) -> str:
    if female:
        if f.endswith("ія"):
            return f[:-1] + "ї"
        if f.endswith("а"):
            return f[:-1] + ("и" if f[-2] not in "гкхжчшщ" else "и")
        return f
    if f.endswith("о"):
        return f[:-1] + "а"
    if f.endswith("й"):
        return f[:-1] + "я"
    return f + "а"


def gen_patr_genitive(p: str, female: bool) -> str:
    return p[:-1] + "и" if female else p + "а"


def iban_ua(rng: random.Random, mfo: str) -> str:
    acct = "0000026" + "".join(rng.choice("0123456789") for _ in range(12))
    bban = mfo + acct  # 6 + 19 = 25
    num = int(bban + "301000")  # U=30 A=10, check 00
    check = 98 - (num % 97)
    return f"UA{check:02d}{bban}"


def luhn_complete(prefix: str) -> str:
    for d in "0123456789":
        s = prefix + d
        tot = 0
        for i, ch in enumerate(reversed(s)):
            x = int(ch)
            if i % 2 == 1:
                x *= 2
                if x > 9:
                    x -= 9
            tot += x
        if tot % 10 == 0:
            return s
    raise AssertionError


class Case:
    def __init__(self, seed: int, variant: str) -> None:
        self.rng = random.Random(seed)
        self.variant = variant
        self.mentions: dict[str, list[dict]] = {}
        self.used_surn: set[str] = set()
        self.people = [self.person(female=i % 2 == 1, rare=(i % 5 == 4)) for i in range(14)]
        self.companies = [self.company() for _ in range(12)]
        self.client = self.companies[0]
        self.en_people = [{"first": self.rng.choice(EN_FIRST), "last": EN_LAST[i]} for i in range(4)]
        self.en_companies = [f"{EN_ROOTS[i]} {self.rng.choice(EN_TAILS)}" for i in range(3)]

    def person(self, female: bool, rare: bool) -> dict:
        r = self.rng
        while True:
            s = r.choice(SURNAMES_M)
            if s not in self.used_surn:
                self.used_surn.add(s)
                break
        f = r.choice((RARE_F if female else RARE_M) if rare else (FIRST_F if female else FIRST_M))
        father = r.choice(FIRST_M + RARE_M)
        p = patronymic(father, female)
        s = fem_surname(s) if female else s
        return {"s": s, "f": f, "p": p, "female": female,
                "ipn": "".join(r.choice("0123456789") for _ in range(10)),
                "phone": f"+38 (0{r.choice(['50','67','63','93','97','66'])}) {r.randint(100,999)}-{r.randint(10,99)}-{r.randint(10,99)}",
                "email": None}

    def company(self) -> dict:
        r = self.rng
        form = r.choice(["ТОВ", "ТОВ", "ПП", "ПрАТ", "ТОВ"])
        name = f"{r.choice(ROOTS)}{r.choice(TAILS).lower() if r.random() < 0.4 else '-' + r.choice(TAILS)}"
        bank = r.randrange(len(BANKS))
        return {"form": form, "name": name, "edrpou": str(r.randint(30000000, 44999999)),
                "iban": iban_ua(r, BANK_MFO[bank]), "bank": BANKS[bank],
                "addr": f"{r.randint(1, 99999):05d}, м. {r.choice(CITIES)}, вул. {r.choice(STREETS)}, {r.randint(1, 120)}"
                        + (f", оф. {r.randint(1, 40)}" if r.random() < 0.5 else "")}

    # recording helpers
    def m(self, doc: str, cat: str, value: str) -> str:
        self.mentions.setdefault(doc, []).append({"category": cat, "value": value})
        return value

    def pname(self, doc: str, p: dict, form: str = "full") -> str:
        fe = p["female"]
        if form == "full":
            v = f"{p['s']} {p['f']} {p['p']}"
        elif form == "upper":
            v = f"{p['s']} {p['f']} {p['p']}".upper()
        elif form == "init":
            v = f"{p['s']} {p['f'][0]}.{p['p'][0]}."
        elif form == "init_front":
            v = f"{p['f'][0]}. {p['p'][0]}. {p['s']}"
        elif form == "short":
            v = f"{p['f']} {p['s']}"
        elif form == "gen":
            v = f"{gen_surname_genitive(p['s'], fe)} {gen_first_genitive(p['f'], fe)} {gen_patr_genitive(p['p'], fe)}"
        else:
            raise ValueError(form)
        return self.m(doc, "PERSON", v)

    def cname(self, doc: str, c: dict, quoted: bool = True) -> str:
        self.m(doc, "ORG", c["name"])
        return f"{c['form']} «{c['name']}»" if quoted else f"{c['form']} {c['name']}"

    # ── documents ──────────────────────────────────────────────────────────

    def statement_rows(self, doc: str, n: int):
        r = self.rng
        rows = []
        for i in range(n):
            day = 1 + i % 28
            if r.random() < 0.7:
                c = r.choice(self.companies[1:])
                party = self.cname(doc, c, quoted=r.random() < 0.6)
                code = self.m(doc, "EDRPOU", c["edrpou"])
                acc = self.m(doc, "ACCOUNT", c["iban"])
            else:
                p = r.choice(self.people)
                party = "ФОП " + self.pname(doc, p, r.choice(["full", "upper", "init"]))
                code = self.m(doc, "IPN", p["ipn"])
                acc = self.m(doc, "ACCOUNT", iban_ua(r, r.choice(BANK_MFO)))
            amount = f"{r.randint(500, 250000)}.{r.randint(0, 99):02d}"
            purpose = r.choice([
                f"Оплата згідно рахунку № {r.randint(10, 999)} від {day:02d}.10.2026 за товар, у т.ч. ПДВ",
                f"Оплата за послуги згідно договору № {r.randint(1, 99)}/{r.randint(1, 12)} від 01.09.2026",
                "Повернення помилково перерахованих коштів",
                f"Оренда приміщення за жовтень 2026, без ПДВ",
            ])
            debit, credit = (amount, "") if r.random() < 0.5 else ("", amount)
            rows.append([f"{day:02d}.10.2026", str(1000 + i), party, code, acc, purpose, debit, credit])
        return rows

    def write_all(self, out: Path) -> dict:
        out.mkdir(parents=True, exist_ok=True)
        files = {}
        files["01_vypyska_pryvatbank_zhovten.csv"] = self.csv_statement("01_vypyska_pryvatbank_zhovten.csv")
        files["02_vypyska_oschadbank_zhovten.xlsx"] = self.xlsx_statement("02_vypyska_oschadbank_zhovten.xlsx")
        files["03_dogovir_postavky.docx"] = self.docx_contract("03_dogovir_postavky.docx")
        files["04_akt_vykonanykh_robit.pdf"] = self.pdf_act("04_akt_vykonanykh_robit.pdf")
        files["05_invoice_canada.pdf"] = self.pdf_invoice_en("05_invoice_canada.pdf")
        files["06_sluzhebnaya_zapiska.txt"] = self.txt_memo_ru("06_sluzhebnaya_zapiska.txt")
        files["07_lyst_skan.png"] = self.scan_letter("07_lyst_skan.png")
        for name, data in files.items():
            (out / name).write_bytes(data)
        gt = {"seed": None, "documents": self.mentions,
              "client": {"form": self.client["form"], "name": self.client["name"]}}
        (out / "ground_truth.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), "utf-8")
        return gt

    def csv_statement(self, doc: str) -> bytes:
        c = self.client
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow(["Дата", "Номер документа", "Контрагент", "ЄДРПОУ контрагента", "Рахунок контрагента",
                    "Призначення платежу", "Дебет", "Кредит"])
        for row in self.statement_rows(doc, 36):
            w.writerow(row)
        self.m(doc, "ORG", c["name"])
        return ("﻿" + buf.getvalue()).encode("utf-8")

    def xlsx_statement(self, doc: str) -> bytes:
        from openpyxl import Workbook

        c = self.client
        wb = Workbook()
        ws = wb.active
        ws.title = "Виписка"
        ws.append([f"Виписка по рахунку {self.m(doc, 'ACCOUNT', c['iban'])}"])
        ws.append([f"Клієнт: {self.cname(doc, c)}, код ЄДРПОУ {self.m(doc, 'EDRPOU', c['edrpou'])}"])
        ws.append(["Період: 01.10.2026 – 31.10.2026"])
        ws.append([])
        ws.append(["Дата", "№ док.", "Контрагент", "ЄДРПОУ/ІПН", "Рахунок контрагента", "Призначення", "Дебет", "Кредит"])
        for row in self.statement_rows(doc, 30):
            row = row[:6] + [float(x) if x else None for x in row[6:]]
            ws.append(row)
        acc = self.people[3]
        ws.append([])
        ws.append([f"Головний бухгалтер: {self.pname(doc, acc, 'init')}"])
        wb.properties.creator = self.pname(doc, acc, "short")
        bio = io.BytesIO()
        wb.save(bio)
        return bio.getvalue()

    def docx_contract(self, doc: str) -> bytes:
        from docx import Document

        r = self.rng
        a, b = self.client, self.companies[2]
        da, db = self.people[0], self.people[1]
        fop = self.people[2]
        d = Document()
        d.core_properties.author = self.pname(doc, self.people[4], "short")
        d.add_heading("ДОГОВІР ПОСТАВКИ № 27/09-26", 1)
        d.add_paragraph("м. Київ                                                     01 вересня 2026 р.")
        p = d.add_paragraph()
        p.add_run(self.cname(doc, a)).bold = True
        p.add_run(f" (код ЄДРПОУ {self.m(doc, 'EDRPOU', a['edrpou'])}), в особі директора ")
        p.add_run(self.pname(doc, da, "gen"))
        p.add_run(", що діє на підставі Статуту (далі — Покупець), та ")
        p.add_run(self.cname(doc, b)).bold = True
        p.add_run(f" (код ЄДРПОУ {self.m(doc, 'EDRPOU', b['edrpou'])}), в особі генерального директора {self.pname(doc, db, 'gen')} (далі — Постачальник), уклали цей договір.")
        d.add_paragraph("1. Постачальник зобов'язується поставити товар, а Покупець — прийняти та оплатити його.")
        d.add_paragraph(f"2. Загальна вартість договору становить 1 250 000,00 грн, у т.ч. ПДВ 208 333,33 грн.")
        d.add_paragraph(f"3. Доставку здійснює перевізник ФОП {self.pname(doc, fop, 'full')} "
                        f"(РНОКПП {self.m(doc, 'IPN', fop['ipn'])}), тел. {self.m(doc, 'PHONE', fop['phone'])}.")
        d.add_paragraph("4. Реквізити сторін:")
        t = d.add_table(rows=6, cols=2)
        t.style = "Table Grid"
        cells = [
            ("Покупець", "Постачальник"),
            (self.cname(doc, a), self.cname(doc, b)),
            (self.m(doc, "ADDRESS", a["addr"]), self.m(doc, "ADDRESS", b["addr"])),
            (f"IBAN {self.m(doc, 'ACCOUNT', a['iban'])}", f"IBAN {self.m(doc, 'ACCOUNT', b['iban'])}"),
            (f"в АТ «{self.m(doc, 'ORG', a['bank'])}»", f"в АТ «{self.m(doc, 'ORG', b['bank'])}»"),
            (f"Директор ____ {self.pname(doc, da, 'init')}", f"Генеральний директор ____ {self.pname(doc, db, 'init')}"),
        ]
        for i, (x, y) in enumerate(cells):
            t.cell(i, 0).text = x
            t.cell(i, 1).text = y
        # a name split across formatting runs
        p = d.add_paragraph("Контактна особа Покупця: ")
        n = self.people[5]
        p.add_run(n["s"]).bold = True
        p.add_run(" " + n["f"] + " " + n["p"])
        self.m(doc, "PERSON", f"{n['s']} {n['f']} {n['p']}")
        p.add_run(f", e-mail: {self.m(doc, 'EMAIL', 'buh.' + str(r.randint(10, 99)) + '@agro-example.ua')}")
        sec = d.sections[0]
        sec.footer.paragraphs[0].text = f"Договір між {self.cname(doc, a)} та {self.cname(doc, b)}"
        bio = io.BytesIO()
        d.save(bio)
        return bio.getvalue()

    def _pdf(self, lines: list[str], title: str, author: str | None = None) -> bytes:
        import pymupdf

        font = str(Path(__file__).resolve().parents[2] / "app/desktop/assets/fonts/DejaVuSans.ttf")
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        y = 60
        for ln in lines:
            size = 13 if ln.startswith("#") else 10
            txt = ln.lstrip("# ")
            page.insert_text((50, y), txt, fontsize=size, fontname="dv", fontfile=font)
            y += size + 7
            if y > 800:
                page = doc.new_page(width=595, height=842)
                y = 60
        doc.set_metadata({"title": title, "author": author or "", "producer": "synthetic"})
        doc.subset_fonts()
        return doc.tobytes(garbage=3, deflate=True)

    def pdf_act(self, doc: str) -> bytes:
        a, b = self.client, self.companies[3]
        pa, pb = self.people[0], self.people[6]
        lines = [
            "# АКТ № 14 здачі-приймання виконаних робіт (наданих послуг)",
            "м. Київ, 31 жовтня 2026 р.",
            "",
            f"Виконавець: {self.cname(doc, b)}, код ЄДРПОУ {self.m(doc, 'EDRPOU', b['edrpou'])},",
            f"п/р {self.m(doc, 'ACCOUNT', b['iban'])} в АТ «{self.m(doc, 'ORG', b['bank'])}»,",
            f"адреса: {self.m(doc, 'ADDRESS', b['addr'])}.",
            f"Замовник: {self.cname(doc, a)}, код ЄДРПОУ {self.m(doc, 'EDRPOU', a['edrpou'])}.",
            "",
            "Виконавець надав, а Замовник прийняв такі послуги:",
            "1. Консультаційні послуги з бухгалтерського обліку — 48 000,00 грн",
            "2. Підготовка податкової звітності — 12 000,00 грн",
            "Разом: 60 000,00 грн, у т.ч. ПДВ 10 000,00 грн.",
            "",
            f"Від Виконавця: директор {self.pname(doc, pb, 'full')}",
            f"Від Замовника: директор {self.pname(doc, pa, 'init')}",
            f"Відповідальна особа: {self.pname(doc, self.people[7], 'short')}, тел. {self.m(doc, 'PHONE', self.people[7]['phone'])}",
        ]
        return self._pdf(lines, "Акт виконаних робіт", self.pname(doc, pb, "short"))

    def pdf_invoice_en(self, doc: str) -> bytes:
        r = self.rng
        p, q = self.en_people[0], self.en_people[1]
        co, co2 = self.en_companies[0], self.en_companies[1]
        self.m(doc, "ORG", co)
        self.m(doc, "ORG", co2)
        sin = luhn_complete("1" + "".join(r.choice("0123456789") for _ in range(7)))
        lines = [
            f"# INVOICE 2026-118 — {co} Inc.",
            f"Bill to: {co2} Ltd., Attn: {self.m(doc, 'PERSON', p['first'] + ' ' + p['last'])}",
            f"{self.m(doc, 'ADDRESS', str(r.randint(10, 990)) + ' Bank Street, Ottawa, ON K1P 5N7')}",
            f"Phone: {self.m(doc, 'PHONE', '+1 613-555-0' + str(r.randint(100, 199)))}  Email: {self.m(doc, 'EMAIL', p['first'].lower() + '@' + co2.split()[0].lower() + '-example.ca')}",
            "",
            "Services: bookkeeping review for fiscal year 2025 ..... CAD 4,850.00",
            "HST 13% ..... CAD 630.50    Total ..... CAD 5,480.50",
            "",
            f"Contractor: {self.m(doc, 'PERSON', q['first'] + ' ' + q['last'])}, SIN {self.m(doc, 'SIN', sin[:3] + ' ' + sin[3:6] + ' ' + sin[6:])}",
            f"Payment: account {self.m(doc, 'ACCOUNT', '1234-' + str(r.randint(1000000, 9999999)))} at Royal Bank of Canada, transit 00011",
        ]
        self.m(doc, "ORG", "Royal Bank of Canada")
        return self._pdf(lines, f"Invoice {co}", None)

    def txt_memo_ru(self, doc: str) -> bytes:
        rp = {"s": "Соколов", "f": "Дмитрий", "p": "Андреевич", "female": False}
        rp2 = {"s": "Лебедева", "f": "Анна", "p": "Сергеевна", "female": True}
        c = self.companies[4]
        text = (
            "СЛУЖЕБНАЯ ЗАПИСКА\n\n"
            f"Главному бухгалтеру {self.m(doc, 'PERSON', 'Лебедевой А.С.')}\n"
            f"от менеджера {self.m(doc, 'PERSON', 'Соколова Дмитрия Андреевича')}\n\n"
            f"Прошу провести оплату поставщику {self.cname(doc, c)} (ЕДРПОУ {self.m(doc, 'EDRPOU', c['edrpou'])}) "
            f"на счет {self.m(doc, 'ACCOUNT', c['iban'])} по счету-фактуре № 77 от 20.10.2026 на сумму 84 300,00 грн.\n"
            f"Контактное лицо поставщика: {self.pname(doc, self.people[8], 'short')}, тел. {self.m(doc, 'PHONE', self.people[8]['phone'])}.\n\n"
            f"С уважением, {self.m(doc, 'PERSON', rp['s'] + ' ' + rp['f'][0] + '.' + rp['p'][0] + '.')}\n"
        )
        _ = rp2
        return text.encode("utf-8")

    def scan_letter(self, doc: str) -> bytes:
        import pymupdf

        c = self.companies[5]
        p = self.people[9]
        lines = [
            "# ЛИСТ-ПІДТВЕРДЖЕННЯ",
            f"{self.cname(doc, c)} підтверджує заборгованість",
            "перед замовником станом на 31.10.2026 у сумі 312 450,00 грн.",
            f"Код ЄДРПОУ {self.m(doc, 'EDRPOU', c['edrpou'])}",
            f"IBAN {self.m(doc, 'ACCOUNT', c['iban'])}",
            f"Директор {self.pname(doc, p, 'full')}",
        ]
        pdf = pymupdf.open(stream=self._pdf(lines, "scan"), filetype="pdf")
        pix = pdf[0].get_pixmap(dpi=200, clip=pymupdf.Rect(0, 0, 595, 260))
        return pix.tobytes("png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--variant", default="demo")
    a = ap.parse_args(argv)
    case = Case(a.seed, a.variant)
    gt = case.write_all(Path(a.out))
    n = sum(len(v) for v in gt["documents"].values())
    print(f"wrote {len(gt['documents'])} documents, {n} sensitive mentions -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

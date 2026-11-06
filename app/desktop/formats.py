"""Document format handlers: plain text, CSV, DOCX/XLSX/PPTX (OOXML), PDF, scans.

Each handler exposes the same two operations over a document's *text units* (paragraph,
table cell, PDF page, metadata field):

* ``units(data) -> list[Unit]`` — read-only extraction, used by the restorer's pre-pass;
* ``rewrite(data, fn) -> bytes`` — calls ``fn(text, force)`` for every unit in the same
  order and applies the returned :class:`~app.desktop.engine.Replacement` list, keeping
  the rest of the file untouched (other zip parts are copied byte-for-byte; formatting runs
  in DOCX keep their properties; PDF pages keep layout, images and vector art).
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.desktop.engine import Replacement, apply_replacements
from app.desktop.ner_rules import header_category

UnitFn = Callable[[str, "str | None"], list[Replacement]]


@dataclass(slots=True)
class Unit:
    text: str
    force: str | None = None


TEXT_EXT = {".txt", ".md", ".text", ".log", ".json", ".xml", ".html", ".htm", ".rtf", ".eml"}
CSV_EXT = {".csv", ".tsv"}
OOXML_EXT = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_EXT = TEXT_EXT | CSV_EXT | OOXML_EXT | PDF_EXT | IMAGE_EXT


class UnsupportedFormat(ValueError):
    pass


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in CSV_EXT:
        return "csv"
    if ext in OOXML_EXT:
        return ext[1:5]
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT or not ext:
        return "text"
    raise UnsupportedFormat(ext)


def decode_text(data: bytes) -> tuple[str, str, bytes]:
    """Decode bytes keeping the BOM and encoding so the output round-trips byte-for-byte."""
    bom = b""
    if data.startswith(b"\xef\xbb\xbf"):
        bom, data = data[:3], data[3:]
        return data.decode("utf-8"), "utf-8", bom
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16", b""
    try:
        return data.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError:
        pass
    for enc in ("cp1251", "cp1252"):
        try:
            return data.decode(enc), enc, b""
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1", b""


def encode_text(text: str, enc: str, bom: bytes) -> bytes:
    try:
        return bom + text.encode(enc)
    except UnicodeEncodeError:
        # a label in Cyrillic inside a cp1252 file: fall back to UTF-8 with BOM
        return b"\xef\xbb\xbf" + text.encode("utf-8")


# ── plain text ──────────────────────────────────────────────────────────────


class TextHandler:
    def units(self, data: bytes) -> list[Unit]:
        return [Unit(decode_text(data)[0])]

    def rewrite(self, data: bytes, fn: UnitFn) -> bytes:
        text, enc, bom = decode_text(data)
        return encode_text(apply_replacements(text, fn(text, None)), enc, bom)


# ── CSV / TSV (table-aware, byte-preserving) ────────────────────────────────


def _csv_cells(text: str, delim: str) -> list[list[tuple[int, int, bool]]]:
    """Raw spans of every cell: (start, end, quoted) — content inside quotes if quoted."""
    rows: list[list[tuple[int, int, bool]]] = []
    row: list[tuple[int, int, bool]] = []
    i, n = 0, len(text)
    while i <= n:
        if i < n and text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            row.append((i + 1, j, True))
            i = j + 1
            while i < n and text[i] not in (delim, "\n", "\r"):
                i += 1
        else:
            j = i
            while j < n and text[j] not in (delim, "\n", "\r"):
                j += 1
            row.append((i, j, False))
            i = j
        if i >= n:
            rows.append(row)
            break
        if text[i] == delim:
            i += 1
            continue
        # newline
        if text[i] == "\r" and i + 1 < n and text[i + 1] == "\n":
            i += 1
        i += 1
        rows.append(row)
        row = []
        if i == n:
            break
    return rows


class CsvHandler:
    def _prep(self, data: bytes, name: str = ""):
        text, enc, bom = decode_text(data)
        sample = text[:4096]
        delim = "\t" if name.lower().endswith(".tsv") else ","
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            pass
        rows = _csv_cells(text, delim)
        cats = self._header_cats(text, rows)
        return text, enc, bom, rows, cats

    @staticmethod
    def _cell(text: str, c: tuple[int, int, bool]) -> str:
        s = text[c[0] : c[1]]
        return s.replace('""', '"') if c[2] else s

    def _header_cats(self, text, rows) -> tuple[int, dict[int, str]]:
        for ri, row in enumerate(rows[:10]):
            cats = {ci: header_category(self._cell(text, c)) for ci, c in enumerate(row)}
            cats = {k: v for k, v in cats.items() if v}
            if cats and len(row) >= 2:
                return ri, cats
        return -1, {}

    def units(self, data: bytes, name: str = "") -> list[Unit]:
        text, _, _, rows, (hrow, cats) = self._prep(data, name)
        out = []
        for ri, row in enumerate(rows):
            for ci, c in enumerate(row):
                out.append(Unit(self._cell(text, c), cats.get(ci) if ri > hrow >= 0 else None))
        return out

    def rewrite(self, data: bytes, fn: UnitFn, name: str = "") -> bytes:
        text, enc, bom, rows, (hrow, cats) = self._prep(data, name)
        raw_reps: list[Replacement] = []
        for ri, row in enumerate(rows):
            for ci, c in enumerate(row):
                force = cats.get(ci) if ri > hrow >= 0 else None
                val = self._cell(text, c)
                reps = fn(val, force)
                if not reps:
                    continue
                new_val = apply_replacements(val, reps)
                if c[2]:
                    new_raw = new_val.replace('"', '""')
                else:
                    new_raw = new_val
                    if any(ch in new_val for ch in ('"', "\n", "\r")) or ("," in new_val and "," not in val):
                        new_raw = '"' + new_val.replace('"', '""') + '"'
                raw_reps.append(Replacement(c[0], c[1], new_raw))
        return encode_text(apply_replacements(text, raw_reps), enc, bom)


# ── OOXML: DOCX / XLSX / PPTX ───────────────────────────────────────────────

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
CORE_PERSON = {
    "{http://purl.org/dc/elements/1.1/}creator",
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy",
}
CORE_TEXT = {
    "{http://purl.org/dc/elements/1.1/}title",
    "{http://purl.org/dc/elements/1.1/}subject",
    "{http://purl.org/dc/elements/1.1/}description",
    "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}keywords",
}
APP_ORG = {"{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Company",
           "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Manager"}


def _apply_to_nodes(texts: list[str], reps: list[Replacement]) -> list[str]:
    """Apply replacements expressed on ``"".join(texts)`` back onto the separate nodes.

    The new text goes into the node where the replaced span starts; the rest of the span is
    cut from the following nodes, so every run keeps its own formatting. Replacements are
    applied right-to-left, so node offsets left of the current one never shift.
    """
    texts = list(texts)
    for r in sorted(reps, key=lambda r: r.start, reverse=True):
        starts, pos = [], 0
        for t in texts:
            starts.append(pos)
            pos += len(t)
        first = next((i for i, t in enumerate(texts) if starts[i] <= r.start < starts[i] + len(t)), None)
        if first is None:
            continue
        for i in range(first, len(texts)):
            b, t = starts[i], texts[i]
            if b >= r.end:
                break
            s_local = max(r.start - b, 0)
            e_local = min(r.end - b, len(t))
            texts[i] = t[:s_local] + r.new + t[e_local:] if i == first else t[e_local:]
    return texts


class OoxmlHandler:
    def __init__(self, kind: str) -> None:
        self.kind = kind  # docx | xlsx | pptx

    # which parts carry text
    def _parts(self, names: list[str]) -> list[str]:
        out = []
        for n in names:
            if n in ("docProps/core.xml", "docProps/app.xml"):
                out.append(n)
            elif self.kind == "docx" and re.fullmatch(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml", n):
                out.append(n)
            elif self.kind == "xlsx" and (n == "xl/sharedStrings.xml" or re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
                                          or re.fullmatch(r"xl/comments\d*\.xml", n)):
                out.append(n)
            elif self.kind == "pptx" and re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", n):
                out.append(n)
        return sorted(out, key=lambda n: (n.startswith("docProps"), n))

    def _xlsx_context(self, z: zipfile.ZipFile) -> dict[int, str]:
        """shared-string index -> column category, from header rows of every sheet."""
        from lxml import etree

        strings: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = etree.fromstring(z.read("xl/sharedStrings.xml"))
            strings = ["".join(si.itertext()) for si in sst.findall(f"{{{S}}}si")]
        force: dict[int, str] = {}
        self._numeric_force: dict[str, dict[str, str]] = {}
        self._cell_force: dict[str, dict[str, str]] = {}
        for n in z.namelist():
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n):
                continue
            root = etree.fromstring(z.read(n))
            rows = root.findall(f".//{{{S}}}sheetData/{{{S}}}row")
            header_row = None
            cats: dict[str, str] = {}
            for row in rows[:10]:
                found = {}
                cells = row.findall(f"{{{S}}}c")
                for c in cells:
                    col = re.sub(r"\d", "", c.get("r", ""))
                    val = self._cell_text(c, strings)
                    cat = header_category(val) if val else None
                    if cat:
                        found[col] = cat
                if found and len(cells) >= 2:
                    header_row, cats = row, found
                    break
            if header_row is None:
                continue
            hr = int(header_row.get("r", "0") or 0)
            numeric: dict[str, str] = {}
            cellf: dict[str, str] = {}
            for row in rows:
                if int(row.get("r", "0") or 0) <= hr:
                    continue
                for c in row.findall(f"{{{S}}}c"):
                    col = re.sub(r"\d", "", c.get("r", ""))
                    if col not in cats:
                        continue
                    if c.get("t") == "s":
                        v = c.find(f"{{{S}}}v")
                        if v is not None and v.text and v.text.isdigit():
                            force.setdefault(int(v.text), cats[col])
                    elif c.get("t") in (None, "n") and c.find(f"{{{S}}}v") is not None:
                        numeric[c.get("r")] = cats[col]
                    else:
                        cellf[c.get("r")] = cats[col]
            self._numeric_force[n] = numeric
            self._cell_force[n] = cellf
        return force

    @staticmethod
    def _cell_text(c, strings: list[str]) -> str:
        t = c.get("t")
        v = c.find(f"{{{S}}}v")
        if t == "s" and v is not None and v.text and v.text.isdigit():
            i = int(v.text)
            return strings[i] if i < len(strings) else ""
        if t == "inlineStr":
            return "".join(c.find(f"{{{S}}}is").itertext()) if c.find(f"{{{S}}}is") is not None else ""
        return v.text if v is not None and v.text else ""

    def _groups(self, part: str, root, sst_force: dict[int, str]):
        """Yield (nodes, force, kind) groups: kind 'text' = list of text-bearing elements,
        'attr' = (element, attribute name)."""
        if part == "docProps/core.xml":
            for el in root:
                if el.tag in CORE_PERSON and el.text:
                    yield [el], "PERSON", "text"
                elif el.tag in CORE_TEXT and el.text:
                    yield [el], None, "text"
            return
        if part == "docProps/app.xml":
            for el in root:
                if el.tag in APP_ORG and el.text:
                    yield [el], "PARTY", "text"
            return
        if self.kind == "docx":
            for p in root.iter(f"{{{W}}}p"):
                for tag in ("t", "delText", "instrText"):
                    nodes = [t for t in p.iter(f"{{{W}}}{tag}") if self._owner_p(t) is p]
                    if nodes:
                        yield nodes, None, "text"
            for el in root.iter():
                for attr in (f"{{{W}}}author", f"{{{W}}}initials"):
                    if el.get(attr):
                        yield [(el, attr)], "PERSON" if attr.endswith("author") else None, "attr"
            return
        if self.kind == "pptx":
            for p in root.iter(f"{{{A}}}p"):
                nodes = list(p.iter(f"{{{A}}}t"))
                if nodes:
                    yield nodes, None, "text"
            return
        if self.kind == "xlsx":
            if part == "xl/sharedStrings.xml":
                for i, si in enumerate(root.findall(f"{{{S}}}si")):
                    nodes = [t for t in si.iter(f"{{{S}}}t") if t.getparent().tag != f"{{{S}}}rPh"]
                    if nodes:
                        yield nodes, sst_force.get(i), "text"
            elif part.startswith("xl/comments"):
                for t in root.iter(f"{{{S}}}text"):
                    nodes = list(t.iter(f"{{{S}}}t"))
                    if nodes:
                        yield nodes, None, "text"
                for a in root.iter(f"{{{S}}}author"):
                    if a.text:
                        yield [a], "PERSON", "text"
            else:
                numeric = getattr(self, "_numeric_force", {}).get(part, {})
                cellf = getattr(self, "_cell_force", {}).get(part, {})
                for c in root.iter(f"{{{S}}}c"):
                    if c.get("t") == "inlineStr":
                        nodes = list(c.iter(f"{{{S}}}t"))
                        if nodes:
                            yield nodes, cellf.get(c.get("r")), "text"
                    elif c.get("t") == "str":
                        v = c.find(f"{{{S}}}v")
                        if v is not None and v.text:
                            yield [v], cellf.get(c.get("r")), "text"
                    elif c.get("r") in numeric:
                        v = c.find(f"{{{S}}}v")
                        if v is not None and v.text:
                            yield [("numcell", c)], numeric[c.get("r")], "num"

    @staticmethod
    def _owner_p(el):
        p = el.getparent()
        while p is not None and p.tag != f"{{{W}}}p":
            p = p.getparent()
        return p

    @staticmethod
    def _get(nodes, kind) -> list[str]:
        if kind == "attr":
            return [el.get(a) for el, a in nodes]
        if kind == "num":
            c = nodes[0][1]
            return [c.find(f"{{{S}}}v").text]
        return [n.text or "" for n in nodes]

    @staticmethod
    def _set(nodes, kind, texts: list[str]) -> None:
        if kind == "attr":
            for (el, a), t in zip(nodes, texts, strict=True):
                el.set(a, t)
            return
        if kind == "num":
            c = nodes[0][1]
            v = c.find(f"{{{S}}}v")
            c.remove(v)
            c.set("t", "inlineStr")
            from lxml import etree

            is_ = etree.SubElement(c, f"{{{S}}}is")
            t = etree.SubElement(is_, f"{{{S}}}t")
            t.text = texts[0]
            return
        for n, t in zip(nodes, texts, strict=True):
            if (n.text or "") != t:
                n.text = t
                if n.tag in (f"{{{W}}}t", f"{{{W}}}delText", f"{{{S}}}t") and t != t.strip():
                    n.set(XML_SPACE, "preserve")

    def _walk(self, data: bytes, fn: UnitFn | None):
        from lxml import etree

        zin = zipfile.ZipFile(io.BytesIO(data))
        names = zin.namelist()
        sst_force = self._xlsx_context(zin) if self.kind == "xlsx" else {}
        parts = self._parts(names)
        units: list[Unit] = []
        new_parts: dict[str, bytes] = {}
        parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
        for part in parts:
            raw = zin.read(part)
            root = etree.fromstring(raw, parser)
            changed = False
            for nodes, force, kind in list(self._groups(part, root, sst_force)):
                texts = self._get(nodes, kind)
                joined = "".join(texts)
                if not joined.strip():
                    continue
                units.append(Unit(joined, force))
                if fn is None:
                    continue
                reps = fn(joined, force)
                if not reps:
                    continue
                if kind == "num":
                    self._set(nodes, kind, [apply_replacements(joined, reps)])
                else:
                    self._set(nodes, kind, _apply_to_nodes(texts, reps))
                changed = True
            if changed:
                decl = raw[:200].split(b"?>")[0] if raw.startswith(b"<?xml") else b""
                body = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
                new_parts[part] = (decl + b"?>\r\n" if decl else b"") + body
        if fn is None:
            return units, None
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename in new_parts:
                    zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zi.external_attr = info.external_attr
                    zout.writestr(zi, new_parts[info.filename])
                else:
                    zout.writestr(info, zin.read(info.filename))
        return units, out.getvalue()

    def units(self, data: bytes) -> list[Unit]:
        return self._walk(data, None)[0]

    def rewrite(self, data: bytes, fn: UnitFn) -> bytes:
        return self._walk(data, fn)[1]


# ── PDF (PyMuPDF) ───────────────────────────────────────────────────────────


def _res_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "app" / "desktop" if base else Path(__file__).resolve().parent


def font_path() -> str:
    return str(_res_dir() / "assets" / "fonts" / "DejaVuSans.ttf")


def find_tesseract() -> str | None:
    cands = [os.environ.get("REDACTGATE_TESSERACT", "")]
    cands.append(str(_res_dir().parent.parent / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")))
    w = shutil.which("tesseract")
    if w:
        cands.append(w)
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            if base:
                cands.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    else:
        cands += ["/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def tesseract_langs(exe: str) -> str:
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return "eng"
    have = {ln.strip() for ln in out.splitlines()[1:]}
    langs = [x for x in ("ukr", "rus", "eng") if x in have]
    return "+".join(langs) or "eng"


def ocr_words(png: bytes) -> list[tuple[str, tuple[float, float, float, float], int, int, int]]:
    """Run local Tesseract on an image; return words with pixel boxes and line keys."""
    exe = find_tesseract()
    if not exe:
        return []
    with tempfile.TemporaryDirectory(prefix="rg-ocr-") as td:
        img = os.path.join(td, "page.png")
        Path(img).write_bytes(png)
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        proc = subprocess.run([exe, img, "stdout", "-l", tesseract_langs(exe), "--psm", "3", "tsv"],
                              capture_output=True, timeout=300, **kw)
        lines = proc.stdout.decode("utf-8", "replace").splitlines()
    words = []
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) < 12 or not f[11].strip():
            continue
        try:
            conf = float(f[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        left, top, w, h = map(int, f[6:10])
        words.append((f[11], (left, top, left + w, top + h), int(f[2]), int(f[3]), int(f[4])))
    return words


class PdfHandler:
    """Text PDFs: page text is re-typeset span by span at the original positions.

    Pages without findings stay untouched. On a page with findings all text is removed
    (images and vector graphics stay) and every text span is written back at its original
    origin and size — with labels in place of the values. Writing the page's spans back in
    their original order keeps the text layer in reading order, which is what an LLM reads.
    Scanned pages (no text layer, has images) go through local Tesseract OCR: the values are
    painted over in the image pixels, a visible label is drawn, and an invisible text layer
    with the anonymized OCR text is added.
    """

    DPI = 300
    FLAGS = None

    def __init__(self) -> None:
        import pymupdf

        self.FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP

    def _spans(self, page) -> tuple[str, list[dict], list[str]]:
        """Page text + span records + node texts (span texts with "\n" separator nodes)."""
        raw = page.get_text("rawdict", flags=self.FLAGS)
        spans: list[dict] = []
        nodes: list[str] = []
        for b in raw.get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    t = "".join(ch["c"] for ch in sp.get("chars", []))
                    if not t:
                        continue
                    spans.append({"text": t, "origin": sp["origin"], "size": sp["size"], "color": sp.get("color", 0),
                                  "bold": bool(sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower(),
                                  "alpha": sp.get("alpha", 255), "bbox": sp["bbox"], "dir": ln.get("dir", (1, 0))})
                    nodes.append(t)
                nodes.append("\n")
                spans.append(None)
            nodes.append("\n")
            spans.append(None)
        return "".join(nodes), spans, nodes

    @staticmethod
    def _is_scan(page, text: str) -> bool:
        return len(text.strip()) < 20 and bool(page.get_images())

    def _ocr_page(self, page):
        pix = page.get_pixmap(dpi=self.DPI)
        words = ocr_words(pix.tobytes("png"))
        scale = 72.0 / self.DPI
        text: list[str] = []
        info: list[tuple | None] = []
        prev = None
        for w, (x0, y0, x1, y1), blk, par, line in words:
            key = (blk, par, line)
            if prev is not None:
                text.append("\n" if key != prev else " ")
                info.append(None if key != prev else ("sp", key))
            prev = key
            r = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
            cw = (r[2] - r[0]) / max(len(w), 1)
            for k, c in enumerate(w):
                bb = (r[0] + k * cw, r[1], r[0] + (k + 1) * cw, r[3])
                info.append((bb, key))
                text.append(c)
        return "".join(text), info

    def units(self, data: bytes) -> list[Unit]:
        import pymupdf

        doc = pymupdf.open(stream=data, filetype="pdf")
        out = []
        for page in doc:
            t, _, _ = self._spans(page)
            if self._is_scan(page, t) and find_tesseract():
                t, _ = self._ocr_page(page)
            out.append(Unit(t))
        md = doc.metadata or {}
        out += [Unit(md[k], "PERSON" if k == "author" else None) for k in self._md_keys(md)]
        return out

    @staticmethod
    def _md_keys(md: dict) -> list[str]:
        return [k for k in ("author", "creator", "keywords", "subject", "title") if md.get(k)]

    def _fonts(self):
        import pymupdf

        reg = pymupdf.Font(fontfile=font_path())
        bold_p = font_path().replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
        bold = pymupdf.Font(fontfile=bold_p) if os.path.isfile(bold_p) else reg
        return reg, bold

    def rewrite(self, data: bytes, fn: UnitFn, *, scan_input: bool = False) -> bytes:
        import pymupdf

        doc = pymupdf.open(stream=data, filetype="pdf")
        reg, bold = self._fonts()
        for page in doc:
            text, spans, nodes = self._spans(page)
            if scan_input or self._is_scan(page, text):
                if find_tesseract():
                    self._rewrite_scan(page, fn, reg)
                continue
            reps = fn(text, None)
            if not reps:
                continue
            new_nodes = _apply_to_nodes(nodes, reps)
            page.add_redact_annot(page.rect)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            writers: dict[tuple, pymupdf.TextWriter] = {}
            order: list[tuple] = []
            for sp, new in zip(spans, new_nodes, strict=True):
                if sp is None or not new:
                    continue
                font = bold if sp["bold"] else reg
                size = max(float(sp["size"]), 1.0)
                width = font.text_length(new, fontsize=size)
                orig_w = max(sp["bbox"][2] - sp["bbox"][0], 0.5)
                changed = new != sp["text"]
                if width > orig_w * 1.02:
                    size = max(size * orig_w * 1.02 / width, size * (0.55 if changed else 0.75))
                invisible = sp["alpha"] == 0
                key = (sp["color"], invisible)
                if key not in writers:
                    writers[key] = pymupdf.TextWriter(page.rect)
                    order.append(key)
                writers[key].append(pymupdf.Point(*sp["origin"]), new, font=font, fontsize=size)
            for key in order:
                color, invisible = key
                rgb = pymupdf.sRGB_to_pdf(color) if isinstance(color, int) else (0, 0, 0)
                writers[key].write_text(page, color=rgb, render_mode=3 if invisible else 0)
        md = dict(doc.metadata or {})
        changed_md = False
        for k in self._md_keys(md):
            reps = fn(md[k], "PERSON" if k == "author" else None)
            if reps:
                md[k] = apply_replacements(md[k], reps)
                changed_md = True
        if changed_md:
            doc.set_metadata({k: v for k, v in md.items()
                              if k in ("author", "title", "subject", "keywords", "creator", "producer") and v is not None})
        try:
            doc.del_xml_metadata()
        except Exception:  # noqa: BLE001
            pass
        try:
            doc.subset_fonts()
        except Exception:  # noqa: BLE001
            pass
        return doc.tobytes(garbage=3, deflate=True)

    def _rewrite_scan(self, page, fn: UnitFn, font) -> None:
        import pymupdf

        text, info = self._ocr_page(page)
        reps = fn(text, None)
        labels = []
        for r in reps:
            boxes = [info[i] for i in range(r.start, r.end) if info[i] is not None and info[i][0] != "sp"]
            lines: dict = {}
            for bb, key in boxes:
                lines.setdefault(key, []).append(bb)
            for j, bbs in enumerate(lines.values()):
                rect = pymupdf.Rect(min(b[0] for b in bbs), min(b[1] for b in bbs),
                                    max(b[2] for b in bbs), max(b[3] for b in bbs))
                page.add_redact_annot(rect, fill=(1, 1, 1))
                if j == 0:
                    labels.append((rect, r.new))
        if reps:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        vis = pymupdf.TextWriter(page.rect)
        for rect, new in labels:
            size = max(rect.height * 0.8, 4)
            w = font.text_length(new, fontsize=size)
            if w > rect.width * 1.1:
                size = max(size * rect.width * 1.1 / w, size * 0.6)
            vis.append(pymupdf.Point(rect.x0, rect.y1 - rect.height * 0.2), new, font=font, fontsize=size)
        vis.write_text(page, color=(0, 0, 0.55))
        # invisible text layer, one OCR line at a time, in reading order
        anon = apply_replacements(text, reps)
        inv = pymupdf.TextWriter(page.rect)
        line_boxes: list[tuple] = []
        cur: list[tuple] = []
        for it in info:
            if it is None:
                if cur:
                    line_boxes.append(cur)
                cur = []
            elif it[0] != "sp":
                cur.append(it[0])
        if cur:
            line_boxes.append(cur)
        for line, bbs in zip(anon.split("\n"), line_boxes, strict=False):
            if not line.strip() or not bbs:
                continue
            x0, y1 = min(b[0] for b in bbs), max(b[3] for b in bbs)
            h = y1 - min(b[1] for b in bbs)
            inv.append(pymupdf.Point(x0, y1 - h * 0.2), line, font=font, fontsize=max(h * 0.8, 3))
        inv.write_text(page, render_mode=3)


class ImageHandler:
    """A scanned page as an image -> OCR -> anonymized PDF (image + label boxes + text layer)."""

    def _to_pdf(self, data: bytes) -> bytes:
        import pymupdf

        img = pymupdf.open(stream=data)
        pdf = pymupdf.open("pdf", img.convert_to_pdf())
        return pdf.tobytes()

    def units(self, data: bytes) -> list[Unit]:
        pdf = self._to_pdf(data)
        h = PdfHandler()
        import pymupdf

        doc = pymupdf.open(stream=pdf, filetype="pdf")
        return [Unit(h._ocr_page(p)[0]) for p in doc] if find_tesseract() else []

    def rewrite(self, data: bytes, fn: UnitFn) -> bytes:
        return PdfHandler().rewrite(self._to_pdf(data), fn, scan_input=True)


def handler_for(name: str):
    k = kind_of(name)
    if k == "text":
        return TextHandler()
    if k == "csv":
        return CsvHandler()
    if k in ("docx", "xlsx", "pptx", "docm", "xlsm"):
        return OoxmlHandler({"docm": "docx", "xlsm": "xlsx"}.get(k, k))
    if k == "pdf":
        return PdfHandler()
    if k == "image":
        return ImageHandler()
    raise UnsupportedFormat(name)


def output_name(name: str, suffix: str) -> str:
    p = Path(name)
    ext = ".pdf" if kind_of(name) == "image" else p.suffix
    return f"{p.stem}{suffix}{ext}"

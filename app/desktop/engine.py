"""Detection + reversible labelling for the desktop app.

Pipeline for one text unit (a paragraph, a table cell, a PDF page):

1. RedactGate regex packs (``app/packs/*.yaml``: GENERIC, UA, CA, US, EU, IRCC) —
   regulated identifiers with checksum validators;
2. local rules (:mod:`app.desktop.ner_rules`) — people, companies, addresses, accounts,
   phones, passports, optional amounts;
3. the project dictionary — every value already labelled in this project, so a name found
   in a table header column in one file is also caught in free text of another file;
4. a forced span from table context (column «ЄДРПОУ», «Рахунок», «Контрагент»…);
5. RedactGate ``merge_spans`` resolves overlaps.

Each surviving span becomes a project label via :class:`~app.desktop.vaultdb.LabelVault`.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from app.desktop import labels as L
from app.desktop import ner_rules as R
from app.desktop.gazetteer import NOMINATIVE_FIRST, is_first_name, norm_token, stem
from app.desktop.vaultdb import Entity, LabelVault
from app.redaction.merge import merge_spans
from app.redaction.regex_packs import load_packs
from app.schemas.entities import EntitySpan

PACKS = ["GENERIC", "UA", "CA", "US", "EU", "IRCC"]
ID_CATEGORIES = {"ACCOUNT", "CARD", "EDRPOU", "IPN", "SIN", "SSN", "EIN", "ITIN", "BN", "VAT", "UCI", "PASSPORT", "IP"}

_DATE_IN = re.compile(r"\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4}")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\"«»“”„'’ʼ`.,;:()\[\]]")


@dataclass(slots=True)
class Replacement:
    start: int
    end: int
    new: str
    category: str = ""
    n: int = 0
    original: str = ""


@dataclass
class RunState:
    """Accumulates what happened across all text units of one document."""

    occurrences: list[list] = field(default_factory=list)  # [category, n, surface]
    counts: Counter = field(default_factory=Counter)
    units_out: list[str] = field(default_factory=list)
    unknown_labels: list[str] = field(default_factory=list)

    def digest(self) -> str:
        return digest_units(self.units_out)


def digest_units(units: list[str]) -> str:
    h = hashlib.sha256()
    for u in units:
        h.update(u.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def apply_replacements(text: str, reps: list[Replacement]) -> str:
    out = text
    for r in sorted(reps, key=lambda r: r.start, reverse=True):
        out = out[: r.start] + r.new + out[r.end :]
    return out


# ── normalisation (what counts as "the same value") ─────────────────────────


def normalize(category: str, value: str) -> str:
    v = value.strip()
    if category in ID_CATEGORIES:
        return re.sub(r"[^0-9A-Za-zА-ЯІЇЄҐа-яіїєґ]", "", v).upper()
    if category == "PHONE":
        d = re.sub(r"\D", "", v)
        return d[-9:]
    if category in ("AMOUNT", "DOB"):
        return re.sub(r"\D", "", v)
    if category == "EMAIL":
        return v.casefold()
    if category == "PERSON":
        toks = re.findall(r"[^\W\d_]+", v)
        return " ".join(stem(t) for t in toks)
    s = _PUNCT.sub(" ", v.casefold())
    return _WS.sub(" ", s).strip()


_PATR_RX = re.compile(r"(?:ович|евич|івн|ївн|овн|евн|ічн|ичн|ич)[а-яіїєґ]{0,3}$", re.IGNORECASE)


def parse_person(surface: str) -> dict:
    """Split a person mention into surname stem / given / patronymic (for alias linking)."""
    raw = re.findall(r"[^\W\d_]+\.?", surface)
    initials: list[str] = []
    words: list[str] = []
    for t in raw:
        core = t.rstrip(".")
        if len(core) == 1:
            initials.append(core.casefold())
        elif t.endswith(".") and len(core) <= 2:
            initials.append(core[0].casefold())
        else:
            words.append(core)
    surname = given = patr = None
    for w in words:
        if patr is None and _PATR_RX.search(w) and len(words) > 1:
            patr = w
        elif given is None and is_first_name(w) and not (surname is None and len(words) == 1):
            given = w
        elif surname is None:
            surname = w
        elif given is None:
            given = w
    if surname is None and given is not None and len(words) >= 2:
        surname, given = given, None
    g = given[0].casefold() if given else (initials[0] if initials else None)
    p = patr[0].casefold() if patr else (initials[1] if len(initials) > 1 else None)
    return {
        "s": stem(surname) if surname else None,
        "g": g,
        "p": p,
        "gs": stem(given) if given else None,
        "w": len(words),
    }


_PATR_NOM = re.compile(r"(?:ович|евич|івна|ївна|овна|евна|ична|ич)$", re.IGNORECASE)


def nominative_score(surface: str) -> float:
    """How likely a person mention is in the nominative case (best for restoring AI text)."""
    words = [w for w in re.findall(r"[^\W\d_]+", surface) if len(w) > 1]
    initials = len(re.findall(r"(?<![^\W\d_])[^\W\d_]\.", surface))
    score = 0.5 * len(words) + (1.0 if initials else 0.0)
    for w in words:
        if is_first_name(w):
            score += 3 if norm_token(w) in NOMINATIVE_FIRST else -2
        elif _PATR_RX.search(w):
            score += 2 if _PATR_NOM.search(w) else -2
    return score


def label_digest(seq) -> str:
    h = hashlib.sha256()
    for cat, n in seq:
        h.update(f"{cat}:{n}|".encode())
    return h.hexdigest()


def _compatible(a: dict, b: dict) -> bool:
    if not a.get("s") or a.get("s") != b.get("s"):
        return False
    for k in ("g", "p", "gs"):
        if a.get(k) and b.get(k) and a[k] != b[k]:
            return False
    return True


# ── detection ───────────────────────────────────────────────────────────────


@dataclass
class Options:
    categories: set[str] = field(default_factory=lambda: set(L.ALL_CATEGORIES) - set(L.DEFAULT_OFF))
    packs: list[str] = field(default_factory=lambda: list(PACKS))

    @classmethod
    def from_dict(cls, d: dict | None) -> Options:
        o = cls()
        if d:
            if d.get("categories") is not None:
                o.categories = set(d["categories"])
            if d.get("packs"):
                o.packs = list(d["packs"])
        return o


class Detector:
    def __init__(self) -> None:
        self._dict_cache: dict[str, tuple[int, re.Pattern[str] | None, dict[str, Entity]]] = {}

    def invalidate(self, pid: str) -> None:
        self._dict_cache.pop(pid, None)

    def _dictionary(self, vault: LabelVault, pid: str) -> tuple[re.Pattern[str] | None, dict[str, Entity]]:
        ents = vault.entities(pid)
        version = sum(1 + len(e.variants) for e in ents) * 7 + len(ents)
        cached = self._dict_cache.get(pid)
        if cached and cached[0] == version:
            return cached[1], cached[2]
        surfaces: dict[str, Entity] = {}
        for e in ents:
            for s in [e.value, *e.variants.values()]:
                s = s.strip()
                if len(s) >= 3 and not s.isdigit() or (s.isdigit() and len(s) >= 6):
                    surfaces.setdefault(s.casefold(), e)
        if surfaces:
            alts = sorted(surfaces, key=len, reverse=True)
            rx = re.compile(
                r"(?<![\w'’ʼ-])(?:" + "|".join(re.escape(a) for a in alts) + r")(?![\w'’ʼ-])", re.IGNORECASE
            )
        else:
            rx = None
        self._dict_cache[pid] = (version, rx, surfaces)
        return rx, surfaces

    def detect(self, text: str, *, vault: LabelVault | None, pid: str | None, options: Options,
               force: str | None = None) -> list[EntitySpan]:
        if not text or not text.strip():
            return []
        spans: list[EntitySpan] = []
        if force:
            spans.extend(self._forced(text, force, options))
        for pack in load_packs(options.packs):
            spans.extend(pack.detect(text))
        spans.extend(R.detect_all(text, amounts="AMOUNT" in options.categories))
        if vault is not None and pid is not None:
            rx, surfaces = self._dictionary(vault, pid)
            if rx is not None:
                for m in rx.finditer(text):
                    e = surfaces.get(m.group(0).casefold())
                    if e is None:
                        continue
                    et = e.category
                    spans.append(EntitySpan(m.start(), m.end(), et, m.group(0), 0.99, "manual"))
        label_hits = L.find_labels(text)
        out: list[EntitySpan] = []
        for s in spans:
            cat = L.category_for(s.entity_type)
            if cat not in options.categories:
                continue
            if cat == "VAT" and sum(c.isdigit() for c in s.text) < 6:
                continue
            if cat == "DOB":
                m = _DATE_IN.search(s.text)
                if not m:
                    continue
                s = EntitySpan(s.start + m.start(), s.start + m.end(), s.entity_type, m.group(0), s.score, s.source)
            if any(h.start < s.end and s.start < h.end for h in label_hits):
                continue
            # trim whitespace / trailing punctuation
            t = text[s.start : s.end]
            lt = len(t) - len(t.lstrip())
            rt = len(t.rstrip(" \t\r\n,;:")) if cat not in ("PERSON",) else len(t.rstrip(" \t\r\n,;:"))
            ns, ne = s.start + lt, s.start + rt
            if ne <= ns:
                continue
            out.append(EntitySpan(ns, ne, cat, text[ns:ne], s.score, s.source, s.jurisdiction))
        return merge_spans(out)

    def _forced(self, text: str, force: str, options: Options) -> list[EntitySpan]:
        v = text.strip()
        if not v or L.is_label(v):
            return []
        s = text.find(v)
        e = s + len(v)
        cat = force
        if force == "PARTY":
            cat = R.classify_party(v)
            if cat == "ORG":
                # keep legal form outside the label when it is there
                sub = R.detect_companies(v)
                if sub:
                    return [EntitySpan(s + x.start, s + x.end, "ORG", x.text, 0.995, "manual") for x in sub]
            else:
                sub = R._SOLE_TRADER.search(v)
                if sub:
                    return [EntitySpan(s + sub.start("v"), s + sub.end("v"), "PERSON", sub.group("v"), 0.995, "manual")]
        elif force == "TAXCODE":
            d = re.sub(r"\D", "", v)
            if len(d) == 8:
                cat = "EDRPOU"
            elif len(d) == 10:
                cat = "IPN"
            elif len(d) == 9:
                cat = "SIN"
            else:
                return []
        elif force in ("ACCOUNT", "PHONE", "AMOUNT", "DOB", "PASSPORT", "SIN", "SSN"):
            if sum(c.isdigit() for c in v) < (6 if force in ("ACCOUNT", "PASSPORT", "SIN", "SSN") else 1):
                return []
        if L.category_for(cat) not in options.categories:
            return []
        return [EntitySpan(s, e, cat, v, 0.995, "manual")]


# ── anonymizer / restorer ───────────────────────────────────────────────────


class Anonymizer:
    def __init__(self, vault: LabelVault) -> None:
        self.vault = vault
        self.detector = Detector()

    # entity resolution (same value -> same label; alias linking for people)
    def _resolve(self, pid: str, category: str, surface: str, origin: str = "auto") -> Entity:
        v = self.vault
        norm = normalize(category, surface)
        fp = v.fingerprint(pid, category, norm)
        e = v.by_fingerprint(pid, fp)
        if e is not None:
            if surface not in (e.value, *e.variants.values()):
                sfp = v.fingerprint(pid, category, "surface:" + surface)
                v.add_variant(e, sfp, surface)
            return e
        if category == "PERSON":
            meta = parse_person(surface)
            if meta.get("s"):
                cands = [x for x in v.entities(pid) if x.category == "PERSON" and _compatible(meta, x.meta)]
                if len(cands) == 1:
                    e = cands[0]
                    v.add_variant(e, fp, surface)
                    # prefer the most "nominative", fullest spelling as the canonical value
                    if nominative_score(surface) > nominative_score(e.value):
                        merged = {k: (meta.get(k) or e.meta.get(k)) for k in set(meta) | set(e.meta)}
                        merged["w"] = meta["w"]
                        old = e.value
                        v.set_canonical(e, surface, merged)
                        ofp = v.fingerprint(pid, category, "surface:" + old)
                        v.add_variant(e, ofp, old)
                    self.detector.invalidate(pid)
                    return e
            e = v.add_entity(pid, category, surface, fp, meta=meta, origin=origin)
        else:
            e = v.add_entity(pid, category, surface, fp, origin=origin)
        self.detector.invalidate(pid)
        return e

    def add_manual(self, pid: str, category: str, value: str, excluded: bool = False) -> Entity:
        e = self._resolve(pid, category, value.strip(), origin="manual")
        if excluded != e.excluded:
            self.vault.set_excluded(e, excluded)
        self.detector.invalidate(pid)
        return e

    def prescan_unit(self, pid: str, text: str, *, options: Options, force: str | None = None) -> int:
        """Detect and register entities without rewriting (first pass over a batch)."""
        spans = self.detector.detect(text, vault=self.vault, pid=pid, options=options, force=force)
        for s in spans:
            self._resolve(pid, s.entity_type, text[s.start : s.end])
        return len(spans)

    def anonymize_unit(self, pid: str, text: str, state: RunState, *, lang: str, options: Options,
                       force: str | None = None) -> list[Replacement]:
        spans = self.detector.detect(text, vault=self.vault, pid=pid, options=options, force=force)
        reps: list[Replacement] = []
        for s in spans:
            surface = text[s.start : s.end]
            e = self._resolve(pid, s.entity_type, surface)
            if e.excluded:
                continue
            label = L.render(e.category, e.n, lang)
            reps.append(Replacement(s.start, s.end, label, e.category, e.n, surface))
        reps.sort(key=lambda r: r.start)
        for r in reps:
            state.occurrences.append([r.category, r.n, r.original])
            state.counts[(pid, r.category, r.n)] += 1
        state.units_out.append(apply_replacements(text, reps))
        return reps

    def restore_unit(self, pid: str, text: str, state: RunState, exact: list | None,
                     cursor: list[int]) -> list[Replacement]:
        """Replace every known label in ``text`` with its real value.

        ``exact`` is the per-document occurrence list recorded at anonymization time; when the
        document comes back unchanged it restores each mention's original spelling (byte-exact).
        Otherwise the entity's canonical value is used (e.g. for an LLM's answer).
        """
        state.units_out.append(text)
        reps: list[Replacement] = []
        for h in L.find_labels(text):
            e = self.vault.entity(pid, h.category, h.number)
            if e is None:
                state.unknown_labels.append(h.text)
                continue
            value = e.value
            if exact is not None and cursor[0] < len(exact):
                cat, n, surface = exact[cursor[0]]
                if cat == h.category and n == h.number:
                    value = surface
                cursor[0] += 1
            reps.append(Replacement(h.start, h.end, value, h.category, h.number, h.text))
            state.counts[(pid, h.category, h.number)] += 1
        return reps

    def finish_anon(self, state: RunState) -> None:
        self.vault.bump(state.counts)

    def exact_map(self, pid: str, texts: list[str]) -> list | None:
        """The recorded occurrence list of the document these label sequences came from."""
        seq = [(h.category, h.number) for t in texts for h in L.find_labels(t)]
        if not seq:
            return None
        exact = self.vault.restore_map(pid, label_digest(seq))
        if exact is not None and [(c, n) for c, n, _ in exact] != seq:
            return None
        return exact

    # convenience for plain text (the "paste text" box)
    def anonymize_text(self, pid: str, text: str, *, lang: str, options: Options,
                       record_as: str | None = "text") -> tuple[str, RunState, list[Replacement]]:
        st = RunState()
        reps = self.anonymize_unit(pid, text, st, lang=lang, options=options)
        self.finish_anon(st)
        if record_as:
            seq = [(c, n) for c, n, _ in st.occurrences]
            counts: dict[str, int] = {}
            for c, _n, _s in st.occurrences:
                counts[c] = counts.get(c, 0) + 1
            self.vault.record_document(pid, record_as, "text", "anon", label_digest(seq), st.occurrences,
                                       {"counts": counts, "labels": len(set(seq))})
        return apply_replacements(text, reps), st, reps

    def restore_text(self, pid: str, text: str) -> tuple[str, RunState, list[Replacement]]:
        exact = self.exact_map(pid, [text])
        st = RunState()
        reps = self.restore_unit(pid, text, st, exact, [0])
        return apply_replacements(text, reps), st, reps

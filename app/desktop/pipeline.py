"""File-level anonymize / restore built on the format handlers and the engine."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.desktop.engine import Anonymizer, Options, Replacement, RunState, apply_replacements, label_digest
from app.desktop.formats import CsvHandler, handler_for, kind_of, output_name




@dataclass
class FileResult:
    name: str
    out_name: str
    kind: str
    data: bytes
    counts: dict[str, int] = field(default_factory=dict)
    labels: int = 0
    exact: bool | None = None
    unknown: list[str] = field(default_factory=list)
    preview_in: str = ""
    preview_out: str = ""
    marks: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    doc_id: str = ""


def _call(handler, method, data, *args, name=""):
    if isinstance(handler, CsvHandler):
        return getattr(handler, method)(data, *args, name=name)
    return getattr(handler, method)(data, *args)


def _preview(units_in: list[str], units_out: list[str], limit: int = 20000) -> tuple[str, str]:
    a = "\n".join(u for u in units_in if u.strip())
    b = "\n".join(u for u in units_out if u.strip())
    return a[:limit], b[:limit]


def prescan_files(anon: Anonymizer, pid: str, files: list[tuple[str, bytes]], *, options: Options) -> int:
    """First pass over a whole batch: every entity found anywhere joins the project dictionary,
    so a name spotted in a signature of file 3 is also replaced in file 1's free text."""
    found = 0
    for name, data in files:
        handler = handler_for(name)
        for u in _call(handler, "units", data, name=name):
            found += anon.prescan_unit(pid, u.text, options=options, force=u.force)
    return found


def anonymize_file(anon: Anonymizer, pid: str, name: str, data: bytes, *, lang: str,
                   options: Options) -> FileResult:
    t0 = time.time()
    handler = handler_for(name)
    st = RunState()
    units_in: list[str] = []

    def fn(text: str, force: str | None) -> list[Replacement]:
        units_in.append(text)
        return anon.anonymize_unit(pid, text, st, lang=lang, options=options, force=force)

    out = _call(handler, "rewrite", data, fn, name=name)
    anon.finish_anon(st)
    seq = [(c, n) for c, n, _ in st.occurrences]
    counts: dict[str, int] = {}
    for c, _n, _s in st.occurrences:
        counts[c] = counts.get(c, 0) + 1
    pin, pout = _preview(units_in, st.units_out)
    res = FileResult(name=name, out_name=output_name(name, ".anon"), kind=kind_of(name), data=out,
                     counts=counts, labels=len({(c, n) for c, n in seq}), preview_in=pin, preview_out=pout,
                     seconds=round(time.time() - t0, 3))
    res.doc_id = anon.vault.record_document(pid, name, res.kind, "anon", label_digest(seq), st.occurrences,
                                            {"counts": counts, "labels": res.labels})
    return res


def restore_file(anon: Anonymizer, pid: str, name: str, data: bytes) -> FileResult:
    t0 = time.time()
    handler = handler_for(name)
    units = _call(handler, "units", data, name=name)
    exact = anon.exact_map(pid, [u.text for u in units])
    st = RunState()
    cursor = [0]
    units_in: list[str] = []
    units_out: list[str] = []

    def fn(text: str, force: str | None) -> list[Replacement]:
        units_in.append(text)
        reps = anon.restore_unit(pid, text, st, exact, cursor)
        units_out.append(apply_replacements(text, reps))
        return reps

    out = _call(handler, "rewrite", data, fn, name=name)
    counts: dict[str, int] = {}
    for (_p, c, _n), k in st.counts.items():
        counts[c] = counts.get(c, 0) + k
    pin, pout = _preview(units_in, units_out)
    stem_name = name
    for suf in (".anon",):
        stem_name = stem_name.replace(suf + ".", ".")
    res = FileResult(name=name, out_name=output_name(stem_name, ".restored"), kind=kind_of(name), data=out,
                     counts=counts, labels=sum(counts.values()), exact=exact is not None,
                     unknown=sorted(set(st.unknown_labels)), preview_in=pin, preview_out=pout,
                     seconds=round(time.time() - t0, 3))
    res.doc_id = anon.vault.record_document(pid, name, res.kind, "restore", None, None,
                                            {"counts": counts, "exact": res.exact, "unknown": len(res.unknown)})
    return res

"""End-to-end check of the desktop anonymizer on synthetic auditor cases.

For each seed: generate the case -> anonymize every file in one project -> measure, per
category, how many of the generated sensitive values are gone from the output (recall) ->
restore every anonymized file -> compare with the original (byte-exact for TXT/CSV,
unit-by-unit text for DOCX/XLSX, normalised page text for PDF).

    python eval/desktop/e2e.py [--seeds 7,11,23] [--out eval/desktop/out]
Exit code 1 if recall for PERSON/ORG/ACCOUNT < 0.90 or any round trip fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval" / "desktop"))

from make_synthetic import Case  # noqa: E402

from app.desktop.engine import Anonymizer, Options  # noqa: E402
from app.desktop.formats import find_tesseract, handler_for, kind_of  # noqa: E402
from app.desktop.pipeline import anonymize_file, prescan_files, restore_file  # noqa: E402
from app.desktop.vaultdb import LabelVault  # noqa: E402

KEY_CATS = ("PERSON", "ORG", "ACCOUNT")


def full_text(name: str, data: bytes) -> str:
    k = kind_of(name)
    if k in ("pdf", "image"):
        import pymupdf

        doc = pymupdf.open(stream=data, filetype="pdf")
        parts = [v for v in (doc.metadata or {}).values() if v]
        parts += [p.get_text() for p in doc]
        return "\n".join(parts)
    h = handler_for(name)
    units = h.units(data, name=name) if k == "csv" else h.units(data)
    return "\n".join(u.text for u in units)


def ocr_text(pdf: bytes) -> str:
    import pymupdf

    from app.desktop.formats import ocr_words

    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = []
    for p in doc:
        words = ocr_words(p.get_pixmap(dpi=200).tobytes("png"))
        out.append(" ".join(w[0] for w in words))
    return "\n".join(out)


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def run(seeds: list[int], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="rg-e2e-"))
    vault = LabelVault(tmp / "vault.db")
    vault.initialize("e2e-password")
    anon = Anonymizer(vault)
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # cat -> [caught, total]
    leaks: list[dict] = []
    roundtrip: list[dict] = []
    files_report = []
    t0 = time.time()
    for seed in seeds:
        case_dir = tmp / f"case-{seed}"
        gt = Case(seed, "eval").write_all(case_dir)
        pid = vault.create_project(f"Синтетичний кейс {seed}", "uk")
        batch = [(n, (case_dir / n).read_bytes()) for n in sorted(gt["documents"])
                 if kind_of(n) != "image" or find_tesseract()]
        prescan_files(anon, pid, batch, options=Options())
        for name in sorted(gt["documents"]):
            src = (case_dir / name).read_bytes()
            if kind_of(name) == "image" and not find_tesseract():
                continue
            res = anonymize_file(anon, pid, name, src, lang="uk", options=Options())
            (out_dir / f"{seed}-{res.out_name}").write_bytes(res.data)
            out_txt = full_text(res.out_name, res.data)
            if kind_of(name) == "image":
                out_txt += "\n" + ocr_text(res.data)
            seen = set()
            for mtn in gt["documents"][name]:
                key = (mtn["category"], mtn["value"])
                if key in seen:
                    continue
                seen.add(key)
                cat = mtn["category"]
                totals[cat][1] += 1
                if mtn["value"] in out_txt or mtn["value"].casefold() in out_txt.casefold():
                    leaks.append({"seed": seed, "file": name, "category": cat, "value": mtn["value"]})
                else:
                    totals[cat][0] += 1
            # round trip
            back = restore_file(anon, pid, res.out_name, res.data)
            (out_dir / f"{seed}-{back.out_name}").write_bytes(back.data)
            k = kind_of(name)
            if k in ("text", "csv"):
                ok = back.data == src
                how = "bytes"
            elif k in ("docx", "xlsx", "pptx"):
                h = handler_for(name)
                ok = [u.text for u in h.units(src)] == [u.text for u in h.units(back.data)]
                how = "text units"
            elif k == "pdf":
                import pymupdf

                a = [norm_ws(p.get_text()) for p in pymupdf.open(stream=src, filetype="pdf")]
                b = [norm_ws(p.get_text()) for p in pymupdf.open(stream=back.data, filetype="pdf")]
                ok = a == b
                how = "page text (whitespace-normalised)"
                if not ok:
                    # report the first difference
                    for x, y in zip(a, b, strict=False):
                        if x != y:
                            i = next(j for j in range(min(len(x), len(y))) if x[j] != y[j]) if x[:len(y)] != y[:len(x)] else min(len(x), len(y))
                            roundtrip.append({"seed": seed, "file": name, "diff_at": i, "orig": x[max(0, i - 40): i + 40], "back": y[max(0, i - 40): i + 40]})
                            break
            else:
                txt_b = norm_ws(full_text(back.out_name, back.data))
                expected = [m["value"] for m in gt["documents"][name]]
                ok = all(norm_ws(v) in txt_b for v in expected)
                how = "restored values present (OCR scan)"
            roundtrip.append({"seed": seed, "file": name, "ok": ok, "how": how, "exact_map": back.exact,
                              "unknown_labels": back.unknown})
            files_report.append({"seed": seed, "file": name, "labels": res.labels, "counts": res.counts,
                                 "seconds": res.seconds})
    recall = {c: {"caught": v[0], "total": v[1], "recall": round(v[0] / v[1], 4) if v[1] else None}
              for c, v in sorted(totals.items())}
    rt_ok = all(r.get("ok", True) for r in roundtrip if "ok" in r)
    key_ok = all((recall.get(c, {}).get("recall") or 0) >= 0.9 for c in KEY_CATS)
    overall_c = sum(v[0] for v in totals.values())
    overall_t = sum(v[1] for v in totals.values())
    report = {
        "seeds": seeds, "seconds": round(time.time() - t0, 1), "ocr": bool(find_tesseract()),
        "recall": recall, "overall_recall": round(overall_c / overall_t, 4) if overall_t else None,
        "leaks": leaks, "roundtrip": roundtrip, "files": files_report,
        "pass": rt_ok and key_ok,
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    vault.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="7,11,23")
    ap.add_argument("--out", default=str(ROOT / "eval" / "desktop" / "out"))
    a = ap.parse_args()
    rep = run([int(s) for s in a.seeds.split(",")], Path(a.out))
    print(f"{'category':10} caught/total  recall")
    for c, v in rep["recall"].items():
        print(f"{c:10} {v['caught']:>5}/{v['total']:<5}  {v['recall']:.3f}")
    print("overall recall", rep["overall_recall"])
    bad = [r for r in rep["roundtrip"] if r.get("ok") is False or "diff_at" in r]
    print("round trips:", len([r for r in rep["roundtrip"] if r.get("ok")]), "ok,", len(bad), "failed")
    for r in bad[:10]:
        print("  RT", r)
    for lk in rep["leaks"][:40]:
        print("  LEAK", lk)
    print("PASS" if rep["pass"] else "FAIL", f"({rep['seconds']} s)")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Black-box e2e against a RUNNING RedactGate Desktop build (Windows/Linux/macOS).

Talks only to the app's local API (stdlib urllib), exactly like the UI does:
create vault -> project -> upload a synthetic case -> anonymize -> download -> check that no
generated sensitive value survived -> upload the anonymized files back -> restore -> compare
with the originals.

    python eval/desktop/api_roundtrip.py --base http://127.0.0.1:18800 --data-dir DIR --case CASE_DIR --out OUT
(``--data-dir`` is where the app wrote instance.json, to read the session token.)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval" / "desktop"))


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.host = urllib.parse.urlparse(base).netloc

    def call(self, path: str, method: str = "GET", body=None, raw: bytes | None = None, headers=None):
        h = {"X-RG-Token": self.token, "Host": self.host, **(headers or {})}
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + "/api/" + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=600) as r:  # noqa: S310 - loopback
            payload = r.read()
            ctype = r.headers.get("Content-Type", "")
        return json.loads(payload) if ctype.startswith("application/json") else payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--password", default="roundtrip-1")
    a = ap.parse_args()
    from e2e import full_text, norm_ws, ocr_text  # noqa: E402  (pymupdf / lxml based extraction)

    from app.desktop.formats import handler_for, kind_of  # noqa: E402

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    token = json.loads((Path(a.data_dir) / "instance.json").read_text("utf-8"))["token"]
    api = Api(a.base, token)
    st = api.call("state")
    if not st["initialized"]:
        api.call("vault/init", "POST", {"password": a.password})
    elif not st["unlocked"]:
        api.call("vault/unlock", "POST", {"password": a.password})
    ocr = api.call("state")["ocr"]
    case = Path(a.case)
    gt = json.loads((case / "ground_truth.json").read_text("utf-8"))
    names = [n for n in sorted(gt["documents"]) if kind_of(n) != "image" or ocr]
    pid = api.call("projects", "POST", {"name": "API round trip", "label_lang": "uk"})["id"]
    t0 = time.time()
    bid = api.call("batch", "POST")["batch"]
    for n in names:
        api.call(f"batch/{bid}/file", "PUT", raw=(case / n).read_bytes(), headers={"X-File-Name": urllib.parse.quote(n)})
    res = api.call(f"projects/{pid}/anonymize", "POST", {"batch": bid})
    t_anon = time.time() - t0
    totals: dict[str, list[int]] = {}
    leaks = []
    anon_files = {}
    for r in res["results"]:
        if "error" in r:
            leaks.append({"file": r["name"], "error": r["error"]})
            continue
        data = api.call(f"results/{r['id']}")
        (out / r["out_name"]).write_bytes(data)
        anon_files[r["name"]] = (r["out_name"], data)
        txt = full_text(r["out_name"], data)
        if kind_of(r["name"]) == "image":
            txt += "\n" + ocr_text(data)  # what is still visible in the pixels
        seen = set()
        for m in gt["documents"][r["name"]]:
            key = (m["category"], m["value"])
            if key in seen:
                continue
            seen.add(key)
            t = totals.setdefault(m["category"], [0, 0])
            t[1] += 1
            if m["value"].casefold() in txt.casefold():
                leaks.append({"file": r["name"], "category": m["category"], "value": m["value"]})
            else:
                t[0] += 1
    bid = api.call("batch", "POST")["batch"]
    for _n, (on, data) in anon_files.items():
        api.call(f"batch/{bid}/file", "PUT", raw=data, headers={"X-File-Name": urllib.parse.quote(on)})
    back = api.call(f"projects/{pid}/restore", "POST", {"batch": bid})
    rt = []
    by_out = {on: n for n, (on, _d) in anon_files.items()}
    for r in back["results"]:
        orig_name = by_out[r["name"]]
        data = api.call(f"results/{r['id']}")
        (out / r["out_name"]).write_bytes(data)
        src = (case / orig_name).read_bytes()
        k = kind_of(orig_name)
        if k in ("text", "csv"):
            ok = data == src
        elif k in ("docx", "xlsx", "pptx"):
            h = handler_for(orig_name)
            ok = [u.text for u in h.units(src)] == [u.text for u in h.units(data)]
        elif k == "pdf":
            import pymupdf

            ok = [norm_ws(p.get_text()) for p in pymupdf.open(stream=src, filetype="pdf")] == \
                 [norm_ws(p.get_text()) for p in pymupdf.open(stream=data, filetype="pdf")]
        else:
            txt_b = norm_ws(full_text(r["out_name"], data))
            ok = all(norm_ws(m["value"]) in txt_b for m in gt["documents"][orig_name])
        rt.append({"file": orig_name, "ok": ok, "exact": r.get("exact")})
    fail_closed = None
    if not ocr:
        # without OCR a scan must be refused, never passed through looking "anonymized"
        imgs = [n for n in sorted(gt["documents"]) if kind_of(n) == "image"]
        if imgs:
            bid = api.call("batch", "POST")["batch"]
            api.call(f"batch/{bid}/file", "PUT", raw=(case / imgs[0]).read_bytes(), headers={"X-File-Name": urllib.parse.quote(imgs[0])})
            r = api.call(f"projects/{pid}/anonymize", "POST", {"batch": bid})["results"][0]
            fail_closed = r.get("error") == "ocr" and "id" not in r
    check = api.call("selfcheck")
    summary = {
        "app_version": st["version"], "ocr": ocr, "files": len(names), "anonymize_seconds": round(t_anon, 2),
        "recall": {c: {"caught": v[0], "total": v[1], "recall": round(v[0] / v[1], 4)} for c, v in sorted(totals.items())},
        "leaks": leaks, "roundtrip": rt, "roundtrip_ok": all(x["ok"] for x in rt),
        "netguard": check["netguard"], "outbound": check["outbound"], "scan_refused_without_ocr": fail_closed,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "roundtrip"}, ensure_ascii=False))
    print("roundtrip:", [(x["file"], x["ok"]) for x in rt])
    good = summary["roundtrip_ok"] and not leaks and not check["outbound"] and fail_closed is not False
    print("PASS" if good else "FAIL")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())

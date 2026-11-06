"""Local HTTP server for the desktop UI — bound to 127.0.0.1 only, stdlib only.

Every /api call needs the per-launch random token (header ``X-RG-Token`` or ``?t=`` for
downloads). The Host header must be the loopback address we bound (DNS-rebinding guard)
and a cross-site Origin is rejected, so a web page open in the user's browser cannot
drive the app.
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import re
import secrets
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from app.desktop import __version__, netguard
from app.desktop import labels as L
from app.desktop.engine import Anonymizer, Options
from app.desktop.formats import SUPPORTED_EXT, OcrUnavailable, UnsupportedFormat, find_tesseract, kind_of
from app.desktop.pipeline import anonymize_file, prescan_files, restore_file
from app.desktop.vaultdb import BadPassword, LabelVault, VaultLocked

log = logging.getLogger("redactgate.desktop")

MAX_UPLOAD = 200 * 1024 * 1024


def res_dir() -> Path:
    import sys

    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "app" / "desktop" if base else Path(__file__).resolve().parent


class AppState:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.vault = LabelVault(data_dir / "vault.db")
        self.anon = Anonymizer(self.vault)
        self.token = secrets.token_urlsafe(24)
        self.batches: dict[str, list[tuple[str, bytes]]] = {}
        self.results: dict[str, dict] = {}
        self.last_seen = time.time()
        self.seen_ui = False
        self.lock = threading.RLock()
        self.port = 0
        self.ui_lang = "uk"
        settings = data_dir / "settings.json"
        if settings.is_file():
            try:
                self.ui_lang = json.loads(settings.read_text("utf-8")).get("lang", "uk")
            except (ValueError, OSError):
                pass

    def save_settings(self) -> None:
        (self.data_dir / "settings.json").write_text(json.dumps({"lang": self.ui_lang}), "utf-8")

    def clear_memory(self) -> None:
        self.batches.clear()
        self.results.clear()


def _entity_json(e) -> dict:
    return {
        "category": e.category, "n": e.n, "value": e.value, "occurrences": e.occurrences,
        "excluded": e.excluded, "origin": e.origin, "variants": sorted(set(e.variants.values()) - {e.value}),
        "labels": {lg: L.render(e.category, e.n, lg) for lg in L.LANGS},
    }


def _marks(reps) -> list[dict]:
    return [{"start": r.start, "end": r.end, "new": r.new, "category": r.category, "n": r.n} for r in reps]


class Handler(BaseHTTPRequestHandler):
    server_version = "RedactGateDesktop/" + __version__
    app: AppState  # set on the class by make_server

    # quiet, value-free access log
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        log.debug("http %s", self.path.split("?")[0])

    # ── plumbing ────────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _err(self, code: int, msg: str, kind: str = "error") -> None:
        self._json({"error": kind, "message": msg}, code)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        ok_hosts = {f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}"}
        if host not in ok_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower().rstrip("/") not in {f"http://{h}" for h in ok_hosts}:
            return False
        return True

    def _auth(self, query: dict) -> bool:
        tok = self.headers.get("X-RG-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(tok, self.app.token)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("file too large")
        return self.rfile.read(n) if n else b""

    def _jbody(self) -> dict:
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            self._err(403, "bad host/origin", "forbidden")
            return
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if not path.startswith("/api/"):
            if method in ("GET", "HEAD"):
                self._static(path)
            else:
                self._err(405, "method not allowed")
            return
        if not self._auth(query):
            self._err(401, "missing or wrong token", "unauthorized")
            return
        self.app.last_seen = time.time()
        self.app.seen_ui = True
        try:
            self._route(method, path, query)
        except VaultLocked:
            self._err(423, "vault is locked", "locked")
        except BadPassword as exc:
            self._err(403, str(exc), "bad_password")
        except UnsupportedFormat as exc:
            self._err(415, f"unsupported format {exc}", "unsupported")
        except KeyError as exc:
            self._err(404, f"not found: {exc}", "not_found")
        except ValueError as exc:
            self._err(400, str(exc), "bad_request")
        except Exception as exc:  # noqa: BLE001
            log.error("internal error: %s\n%s", type(exc).__name__, traceback.format_exc(limit=6))
            self._err(500, f"{type(exc).__name__}", "internal")

    def _static(self, path: str) -> None:
        ui = res_dir() / "ui"
        rel = "index.html" if path in ("/", "") else unquote(path.lstrip("/"))
        target = (ui / rel).resolve()
        if not str(target).startswith(str(ui.resolve())) or not target.is_file():
            self._err(404, "not found")
            return
        fixed = {".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                 ".woff2": "font/woff2", ".png": "image/png"}
        ctype = fixed.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ── routes ──────────────────────────────────────────────────────────────
    def _route(self, method: str, path: str, query: dict) -> None:
        app = self.app
        v = app.vault
        parts = [p for p in path.split("/") if p][1:]  # drop "api"

        if parts == ["state"]:
            self._json({
                "version": __version__, "initialized": v.initialized, "unlocked": v.unlocked,
                "lang": app.ui_lang, "ocr": bool(find_tesseract()), "netguard": netguard.status(),
                "categories": [{"id": c, "titles": L.CATEGORY_TITLES[c], "default_on": c not in L.DEFAULT_OFF,
                                "sample": {lg: L.render(c, 1, lg) for lg in L.LANGS}} for c in L.ALL_CATEGORIES],
                "extensions": sorted(SUPPORTED_EXT), "data_dir": str(app.data_dir),
            })
            return
        if parts == ["heartbeat"]:
            self._json({"ok": True})
            return
        if parts == ["settings"] and method == "POST":
            b = self._jbody()
            if b.get("lang") in L.LANGS:
                app.ui_lang = b["lang"]
                app.save_settings()
            self._json({"ok": True, "lang": app.ui_lang})
            return
        if parts == ["quit"] and method == "POST":
            self._json({"ok": True})
            threading.Thread(target=self._shutdown_later, daemon=True).start()
            return
        if parts == ["selfcheck"]:
            self._json(selfcheck(app))
            return

        if parts[:1] == ["vault"]:
            b = self._jbody() if method == "POST" else {}
            if parts == ["vault", "init"]:
                v.initialize(str(b.get("password", "")))
            elif parts == ["vault", "unlock"]:
                v.unlock(str(b.get("password", "")))
            elif parts == ["vault", "lock"]:
                v.lock()
                app.clear_memory()
            elif parts == ["vault", "password"]:
                v.change_password(str(b.get("old", "")), str(b.get("new", "")))
            elif parts == ["vault", "export"] and method == "GET":
                v._need()
                buf = io.BytesIO()
                tmp = app.data_dir / f".export-{uuid.uuid4().hex}.db"
                v._db.execute("VACUUM INTO ?", (str(tmp),))
                buf.write(tmp.read_bytes())
                tmp.unlink(missing_ok=True)
                name = time.strftime("redactgate-vault-%Y%m%d-%H%M.rgvault")
                self._send(200, buf.getvalue(), "application/octet-stream",
                           {"Content-Disposition": f"attachment; filename=\"{name}\""})
                return
            else:
                raise KeyError(path)
            self._json({"ok": True, "unlocked": v.unlocked, "initialized": v.initialized})
            return

        if parts == ["projects"]:
            if method == "GET":
                self._json({"projects": v.list_projects()})
            elif method == "POST":
                b = self._jbody()
                name = str(b.get("name") or "").strip() or "Проєкт"
                lang = b.get("label_lang") if b.get("label_lang") in L.LANGS else app.ui_lang
                pid = v.create_project(name, lang, b.get("options") or {})
                self._json(v.get_project(pid), 201)
            return

        if parts[:1] == ["projects"] and len(parts) >= 2:
            pid = parts[1]
            proj = v.get_project(pid)
            rest = parts[2:]
            if not rest:
                if method == "GET":
                    self._json(proj)
                elif method == "PATCH":
                    b = self._jbody()
                    v.update_project(pid, name=b.get("name"), label_lang=b.get("label_lang") if b.get("label_lang") in L.LANGS else None,
                                     options=b.get("options"))
                    self._json(v.get_project(pid))
                elif method == "DELETE":
                    v.delete_project(pid)
                    app.anon.detector.invalidate(pid)
                    self._json({"ok": True})
                return
            if rest == ["entities"]:
                if method == "GET":
                    self._json({"entities": [_entity_json(e) for e in v.entities(pid)]})
                else:
                    b = self._jbody()
                    cat = str(b.get("category", "")).upper()
                    if cat not in L.WORDS or not str(b.get("value", "")).strip():
                        raise ValueError("category and value required")
                    e = app.anon.add_manual(pid, cat, str(b["value"]), bool(b.get("excluded")))
                    self._json(_entity_json(e), 201)
                return
            if rest[:1] == ["entities"] and len(rest) == 3:
                e = v.entity(pid, rest[1].upper(), int(rest[2]))
                if e is None:
                    raise KeyError("entity")
                if method == "PATCH":
                    b = self._jbody()
                    if "value" in b and str(b["value"]).strip():
                        old = e.value
                        v.set_canonical(e, str(b["value"]).strip())
                        v.add_variant(e, v.fingerprint(pid, e.category, "surface:" + old), old)
                    if "excluded" in b:
                        v.set_excluded(e, bool(b["excluded"]))
                    app.anon.detector.invalidate(pid)
                    self._json(_entity_json(e))
                elif method == "DELETE":
                    v.delete_entity(e)
                    app.anon.detector.invalidate(pid)
                    self._json({"ok": True})
                return
            if rest == ["documents"]:
                self._json({"documents": v.documents(pid)})
                return
            if rest == ["anonymize-text"]:
                b = self._jbody()
                opts = Options.from_dict(b.get("options") or proj["options"])
                out, st, reps = app.anon.anonymize_text(pid, str(b.get("text", "")), lang=proj["label_lang"], options=opts)
                self._json({"text": out, "marks": _marks(reps), "counts": dict((c, sum(1 for o in st.occurrences if o[0] == c)) for c in {o[0] for o in st.occurrences})})
                return
            if rest == ["restore-text"]:
                b = self._jbody()
                out, st, reps = app.anon.restore_text(pid, str(b.get("text", "")))
                self._json({"text": out, "marks": _marks(reps), "unknown": sorted(set(st.unknown_labels)),
                            "restored": len(reps)})
                return
            if rest in (["anonymize"], ["restore"]):
                b = self._jbody()
                files = app.batches.pop(str(b.get("batch")), None)
                if not files:
                    raise ValueError("empty batch")
                self._json(run_batch(app, pid, proj, files, rest[0], b.get("options")))
                return
            raise KeyError(path)

        if parts == ["batch"] and method == "POST":
            bid = uuid.uuid4().hex[:12]
            app.batches[bid] = []
            self._json({"batch": bid})
            return
        if parts[:1] == ["batch"] and len(parts) == 3 and parts[2] == "file" and method == "PUT":
            bid = parts[1]
            if bid not in app.batches:
                raise KeyError("batch")
            name = unquote(self.headers.get("X-File-Name") or (query.get("name") or ["file.txt"])[0])
            name = Path(name.replace("\\", "/")).name or "file.txt"
            kind_of(name)  # raises UnsupportedFormat
            app.batches[bid].append((name, self._body()))
            self._json({"ok": True, "files": len(app.batches[bid])})
            return

        if parts[:1] == ["results"] and len(parts) == 2 and method == "GET":
            r = app.results.get(parts[1])
            if r is None:
                raise KeyError("result")
            ctype = mimetypes.guess_type(r["name"])[0] or "application/octet-stream"
            self._send(200, r["data"], ctype, {"Content-Disposition": _disposition(r["name"])})
            return
        if parts[:1] == ["zip"] and len(parts) == 2 and method == "GET":
            ids = parts[1].split(",")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for rid in ids:
                    r = app.results.get(rid)
                    if r:
                        z.writestr(r["name"], r["data"])
            name = (query.get("name") or ["redactgate.zip"])[0]
            self._send(200, buf.getvalue(), "application/zip", {"Content-Disposition": _disposition(name)})
            return

        if parts == ["demo"] and method == "POST":
            self._json(make_demo(app))
            return

        raise KeyError(path)

    def _shutdown_later(self) -> None:
        time.sleep(0.3)
        self.app.vault.lock()
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def _disposition(name: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def run_batch(app: AppState, pid: str, proj: dict, files: list[tuple[str, bytes]], direction: str,
              options: dict | None) -> dict:
    opts = Options.from_dict(options or proj.get("options"))
    results = []
    t0 = time.time()
    if direction == "anonymize":
        try:
            prescan_files(app.anon, pid, files, options=opts)
        except OcrUnavailable:
            # prescan the files that do not need OCR; those that do will report the error below
            prescan_files(app.anon, pid, [f for f in files if kind_of(f[0]) not in ("image", "pdf")], options=opts)
    for name, data in files:
        try:
            if direction == "anonymize":
                res = anonymize_file(app.anon, pid, name, data, lang=proj["label_lang"], options=opts)
            else:
                res = restore_file(app.anon, pid, name, data)
        except UnsupportedFormat:
            results.append({"name": name, "error": "unsupported"})
            continue
        except OcrUnavailable as exc:
            log.warning("ocr unavailable for a file: %s", exc)
            results.append({"name": name, "error": "ocr"})
            continue
        except Exception as exc:  # noqa: BLE001
            log.error("file failed: %s %s", type(exc).__name__, traceback.format_exc(limit=4))
            results.append({"name": name, "error": type(exc).__name__})
            continue
        rid = uuid.uuid4().hex[:16]
        app.results[rid] = {"name": res.out_name, "data": res.data}
        results.append({
            "id": rid, "name": name, "out_name": res.out_name, "kind": res.kind, "size": len(res.data),
            "counts": res.counts, "labels": res.labels, "exact": res.exact, "unknown": res.unknown,
            "preview_in": res.preview_in, "preview_out": res.preview_out, "seconds": res.seconds,
        })
    # keep memory bounded
    while len(app.results) > 400:
        app.results.pop(next(iter(app.results)))
    return {"results": results, "seconds": round(time.time() - t0, 2), "project": app.vault.get_project(pid)}


DEMO_ANSWER = {
    "uk": """АУДИТОРСЬКИЙ ВИСНОВОК (зразок відповіді нейромережі)

Об'єкт перевірки: {client}, операції за жовтень 2026 року за випискою по рахунку {client_acc}.

1. Найбільші контрагенти за обсягом платежів: {top1} (код {top1_code}), {top2} (код {top2_code}).
2. Платежі на користь фізичних осіб-підприємців: {fop1}, {fop2}. Рекомендуємо перевірити наявність договорів і актів наданих послуг.
3. Договір поставки між {client} та {party2} підписано директором {director}; реквізити рахунку {party2_acc} у договорі збігаються з випискою.
4. Акт виконаних робіт від {party3} підписав {signer3}; сума акту відповідає банківському платежу.

Висновок: суттєвих викривлень не виявлено. Відповідальна особа з боку клієнта — {director}.""",
    "ru": """АУДИТОРСКОЕ ЗАКЛЮЧЕНИЕ (образец ответа нейросети)

Объект проверки: {client}, операции за октябрь 2026 года по выписке по счёту {client_acc}.

1. Крупнейшие контрагенты по объёму платежей: {top1} (код {top1_code}), {top2} (код {top2_code}).
2. Платежи в пользу ФЛП: {fop1}, {fop2}. Рекомендуем проверить договоры и акты оказанных услуг.
3. Договор поставки между {client} и {party2} подписан директором {director}; реквизиты счёта {party2_acc} совпадают с выпиской.
4. Акт выполненных работ от {party3} подписал {signer3}; сумма акта соответствует банковскому платежу.

Вывод: существенных искажений не выявлено. Ответственное лицо со стороны клиента — {director}.""",
    "en": """AUDIT OPINION (sample of an AI answer)

Engagement: {client}, October 2026 transactions per the statement of account {client_acc}.

1. Largest counterparties by payment volume: {top1} (code {top1_code}), {top2} (code {top2_code}).
2. Payments to sole proprietors: {fop1}, {fop2}. Check that contracts and acts of services exist.
3. The supply agreement between {client} and {party2} was signed by director {director}; account {party2_acc} in the agreement matches the statement.
4. The act of completed works from {party3} was signed by {signer3}; the amount matches the bank payment.

Conclusion: no material misstatements found. Client's responsible person: {director}.""",
}


def make_demo(app: AppState) -> dict:
    demo_dir = res_dir() / "demo"
    gt = json.loads((demo_dir / "ground_truth.json").read_text("utf-8"))
    lang = app.ui_lang
    name = {"uk": "Демо: аудит (синтетичні дані)", "ru": "Демо: аудит (синтетические данные)",
            "en": "Demo: audit (synthetic data)"}[lang]
    pid = app.vault.create_project(name, lang, {})
    proj = app.vault.get_project(pid)
    files = [(p.name, p.read_bytes()) for p in sorted(demo_dir.iterdir())
             if p.suffix.lower() in SUPPORTED_EXT and p.name != "ground_truth.json"
             and (kind_of(p.name) != "image" or find_tesseract())]
    out = run_batch(app, pid, proj, files, "anonymize", None)

    def label(cat: str, value: str) -> str:
        from app.desktop.engine import normalize

        fp = app.vault.fingerprint(pid, cat, normalize(cat, value))
        e = app.vault.by_fingerprint(pid, fp)
        if e is None:
            for x in app.vault.entities(pid):
                if x.category == cat and (x.value == value or value in x.variants.values()):
                    e = x
                    break
        return L.render(e.category, e.n, lang) if e else "?"

    docs = gt["documents"]
    contract = [m for m in docs["03_dogovir_postavky.docx"]]
    statement = docs["01_vypyska_pryvatbank_zhovten.csv"]
    orgs = [m["value"] for m in statement if m["category"] == "ORG" and m["value"] != gt["client"]["name"]]
    codes = [m["value"] for m in statement if m["category"] == "EDRPOU"]
    persons = [m["value"] for m in statement if m["category"] == "PERSON"]
    from collections import Counter

    top = [o for o, _ in Counter(orgs).most_common(2)]
    top_codes = []
    for o in top:
        idx = next(i for i, m in enumerate(statement) if m["category"] == "ORG" and m["value"] == o)
        top_codes.append(next(m["value"] for m in statement[idx:] if m["category"] == "EDRPOU"))
    _ = codes
    act = docs["04_akt_vykonanykh_robit.pdf"]
    contract_orgs = [m["value"] for m in contract if m["category"] == "ORG"]
    contract_people = [m["value"] for m in contract if m["category"] == "PERSON"]
    contract_acc = [m["value"] for m in contract if m["category"] == "ACCOUNT"]
    act_orgs = [m["value"] for m in act if m["category"] == "ORG"]
    act_people = [m["value"] for m in act if m["category"] == "PERSON"]
    xlsx = docs["02_vypyska_oschadbank_zhovten.xlsx"]
    fill = {
        "client": label("ORG", gt["client"]["name"]),
        "client_acc": label("ACCOUNT", next(m["value"] for m in xlsx if m["category"] == "ACCOUNT")),
        "top1": label("ORG", top[0]), "top1_code": label("EDRPOU", top_codes[0]),
        "top2": label("ORG", top[1] if len(top) > 1 else top[0]), "top2_code": label("EDRPOU", top_codes[-1]),
        "fop1": label("PERSON", persons[0]) if persons else "?",
        "fop2": label("PERSON", persons[1]) if len(persons) > 1 else "?",
        "party2": label("ORG", contract_orgs[1]),
        "director": label("PERSON", contract_people[1]),
        "party2_acc": label("ACCOUNT", contract_acc[-1]),
        "party3": label("ORG", act_orgs[0]),
        "signer3": label("PERSON", act_people[0]),
    }
    out["project_id"] = pid
    out["sample_answer"] = DEMO_ANSWER[lang].format(**fill)
    return out


def selfcheck(app: AppState) -> dict:
    """What the app itself can see about its network behaviour."""
    listening = []
    try:
        import psutil  # optional

        me = psutil.Process()
        for c in me.net_connections(kind="inet"):
            listening.append({"laddr": f"{c.laddr.ip}:{c.laddr.port}",
                              "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "", "status": c.status})
    except Exception:  # noqa: BLE001
        listening = [{"laddr": f"127.0.0.1:{app.port}", "raddr": "", "status": "LISTEN"}]
    outbound = [c for c in listening if c["raddr"] and not c["raddr"].startswith(("127.", "::1"))]
    return {"netguard": netguard.status(), "sockets": listening, "outbound": outbound, "port": app.port}


def make_server(data_dir: Path, port: int = 0) -> tuple[ThreadingHTTPServer, AppState]:
    app = AppState(data_dir)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    app.port = httpd.server_address[1]
    return httpd, app


def wipe_profile(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)

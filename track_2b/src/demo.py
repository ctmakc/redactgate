#!/usr/bin/env python3
"""RedactGate x Apertus: end-to-end demo on a synthetic Swiss client dossier (de/fr/it).

What runs, all on the local machine / inside the compose network:

  client request
    -> RedactGate route POST /v1/chat/completions (the real FastAPI app, in-process)
       1. regex packs GENERIC + CH + EU   (AHV, CH-IBAN, UID, +41 phones, passport, DOB, email)
       2. Apertus LLM-NER pass             (names, companies, addresses) on the LOCAL model
       3. AES-256-GCM token vault          (value -> [[TYPE_hex]], same value -> same token)
    -> Apertus answers the redacted prompt (llama.cpp server, OpenAI-compatible API)
    -> RedactGate re-inflates the answer and writes a hash-chained audit event

The demo then proves the claims instead of asserting them:
  * every byte sent to the answering model is captured and searched for each of the
    26 synthetic sensitive values (regex-class leaks fail the run with exit code 1);
  * placeholders in the model answer are counted and checked for successful restore;
  * the audit chain is verified, then one event is tampered with and verification fails;
  * an egress probe shows whether the process can reach the internet at all.

Usage:
  python track_2b/src/demo.py                      # uses LLM_BASE_URL or http://localhost:8091/v1
  LLM_BASE_URL=http://llm:8080/v1 python demo.py   # inside docker compose
Exit code 0 = no leak of any regex-class identifier and a valid audit chain.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import pathlib
import socket
import sys
import time
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
TRACK = HERE.parent
REPO = TRACK.parent
DATA = TRACK / "data" / "swiss_dossier.json"
OUT_DIR = pathlib.Path(os.environ.get("DEMO_OUT_DIR", TRACK / "out"))

# ── configuration (before importing the app: settings are read at import) ─────────
os.chdir(TRACK)  # keep any developer .env at the repo root out of the demo
sys.path.insert(0, str(REPO))
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("AI_PROVIDER", "apertus")
os.environ.setdefault("ENABLE_LLM_NER", "true")
os.environ.setdefault("LLM_NER_PROVIDER", "apertus")
os.environ.setdefault("DEFAULT_PACK_CODES", "GENERIC,CH,EU")
os.environ.setdefault("REQUIRE_API_KEY", "false")
os.environ.setdefault("UPSTREAM_TIMEOUT_SECONDS", "900")
if not (os.environ.get("LLM_BASE_URL") or os.environ.get("APERTUS_BASE_URL")):
    os.environ["LLM_BASE_URL"] = "http://localhost:8091/v1"
# Fresh, ephemeral vault / fingerprint / audit keys for every run.
for _k in ("VAULT_MASTER_KEY", "FINGERPRINT_HMAC_KEY", "AUDIT_HMAC_KEY"):
    os.environ.setdefault(_k, base64.b64encode(os.urandom(32)).decode())

import httpx  # noqa: E402

import app.gateway  # noqa: E402,F401  (registers adapters)
from app import deps  # noqa: E402
from app.audit import InMemoryAuditSink  # noqa: E402
from app.auth import AuthContext  # noqa: E402
from app.config import settings  # noqa: E402
from app.gateway.apertus import ApertusProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.redaction.placeholders import PLACEHOLDER_RE  # noqa: E402
from app.redaction.store import InMemoryTokenStore  # noqa: E402
from app.schemas.entities import PolicyDecision, PolicyMode  # noqa: E402

TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


def head(title: str) -> None:
    print()
    print(_c("1;36", f"== {title} " + "=" * max(0, 74 - len(title))))


def ok(msg: str) -> None:
    print(_c("32", "  [ok]   ") + msg)


def bad(msg: str) -> None:
    print(_c("31", "  [FAIL] ") + msg)


def info(msg: str) -> None:
    print("         " + msg)


class RecordingApertus(ApertusProvider):
    """The real Apertus adapter plus a tap on the exact JSON body that leaves the gateway."""

    def __init__(self, cfg: Any) -> None:
        super().__init__(cfg)
        self.sent: list[dict[str, Any]] = []
        self.received: list[dict[str, Any]] = []

    def _body(self, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        body = super()._body(payload, stream=stream)
        self.sent.append(copy.deepcopy(body))
        return body

    def _repair(self, result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self.received.append(copy.deepcopy(result))  # the model's answer before repair
        return super()._repair(result, payload)


def build_gateway(provider: Any, store: Any, audit: Any, auth: AuthContext) -> Any:
    """The real RedactGate FastAPI app, wired DB-less: in-memory vault store + audit chain."""
    gateway = create_app()
    gateway.dependency_overrides[deps.get_token_store] = lambda: store
    gateway.dependency_overrides[deps.get_audit_sink] = lambda: audit
    gateway.dependency_overrides[deps.get_policy_decision] = lambda: PolicyDecision(
        mode=PolicyMode.TOKENIZE, blocked=False
    )
    gateway.dependency_overrides[deps.get_active_provider] = lambda: provider
    gateway.dependency_overrides[deps.get_auth] = lambda: auth
    return gateway


def egress_probe() -> dict[str, str]:
    """Try to open TCP connections to the public internet (expected to fail when air-gapped)."""
    out: dict[str, str] = {}
    for host, port in (("huggingface.co", 443), ("1.1.1.1", 443)):
        try:
            socket.create_connection((host, port), timeout=3).close()
            out[f"{host}:{port}"] = "reachable"
        except OSError as exc:
            out[f"{host}:{port}"] = f"blocked ({type(exc).__name__})"
    return out


async def wait_for_model(base_url: str, timeout_s: int = 900) -> str:
    deadline = time.monotonic() + timeout_s
    last = ""
    async with httpx.AsyncClient(timeout=10) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{base_url}/models")
                if r.status_code == 200:
                    data = r.json().get("data") or [{}]
                    return str(data[0].get("id", "?"))
                last = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            await asyncio.sleep(3)
    raise SystemExit(f"model server at {base_url} not ready after {timeout_s}s ({last})")


async def main() -> int:
    dossier = json.loads(DATA.read_text(encoding="utf-8"))
    sensitive: list[dict[str, str]] = dossier["sensitive_values"]
    base_url = settings.apertus_base_url.rstrip("/")

    head("1. Runtime")
    info(f"answering model : {settings.apertus_default_model}")
    info(f"model endpoint  : {base_url}")
    info(f"LLM-NER pass    : {'on' if settings.enable_llm_ner else 'off'}"
         f" (provider: {settings.llm_ner_provider or settings.ai_provider})")
    info(f"regex packs     : {', '.join(settings.pack_codes)}")
    egress = egress_probe()
    for target, state in egress.items():
        info(f"egress {target:<20}: {state}")
    airgapped = all(v.startswith("blocked") for v in egress.values())
    (ok if airgapped else info)(
        "no route to the internet: running air-gapped" if airgapped
        else "internet reachable from this process (run via `make run` for the air-gapped network)"
    )
    served = await wait_for_model(base_url)
    ok(f"model server ready, serving '{served}'")

    # ── the real app, DB-less single-process wiring ──
    store = InMemoryTokenStore()
    audit = InMemoryAuditSink()
    provider = RecordingApertus(settings)
    auth = AuthContext(team_id="team-treuhand-demo", policy_id="policy-default", api_key_id="demo")
    gateway = build_gateway(provider, store, audit, auth)

    head("2. Client request (what the Treuhand employee pastes)")
    print(dossier["document"])
    request = {
        "model": "default",
        "temperature": 0.2,
        "max_tokens": 450,
        "messages": [
            {"role": "system", "content": "Du bist ein sorgfältiger Assistent eines Schweizer Treuhandbüros. Antworte knapp."},
            {"role": "user", "content": dossier["task"] + "\n\n" + dossier["document"]},
        ],
    }

    t0 = time.perf_counter()
    transport = httpx.ASGITransport(app=gateway)
    async with httpx.AsyncClient(transport=transport, base_url="http://redactgate.local", timeout=1800) as client:
        resp = await client.post("/v1/chat/completions", json=request)
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        bad(f"gateway returned HTTP {resp.status_code}: {resp.text[:300]}")
        return 1
    final = resp.json()["choices"][0]["message"]["content"]
    sent = provider.sent[-1]
    sent_text = json.dumps(sent, ensure_ascii=False)
    redacted_doc = sent["messages"][-1]["content"]
    raw_answer = provider.received[-1]["choices"][0]["message"]["content"]

    head("3. What Apertus received (captured on the wire)")
    print(redacted_doc)

    head("4. Apertus answer, as generated (placeholders only)")
    print(raw_answer)

    head("5. Answer returned to the employee (re-inflated by RedactGate)")
    print(final)

    head("6. Checks")
    leaks = [v for v in sensitive if v["text"] in sent_text]
    regex_vals = [v for v in sensitive if v["detector"] == "regex"]
    llm_vals = [v for v in sensitive if v["detector"] == "llm"]
    regex_leaks = [v for v in leaks if v["detector"] == "regex"]
    llm_leaks = [v for v in leaks if v["detector"] == "llm"]
    if regex_leaks:
        bad(f"{len(regex_leaks)} structured identifiers reached the model: "
            + ", ".join(v["type"] for v in regex_leaks))
    else:
        ok(f"{len(regex_vals)}/{len(regex_vals)} structured identifiers (AHV, IBAN, UID, phone, "
           "passport, DOB, email) were replaced before leaving the gateway")
    caught = len(llm_vals) - len(llm_leaks)
    (ok if not llm_leaks else info)(
        f"{caught}/{len(llm_vals)} names, companies and addresses caught by the local Apertus NER pass"
    )
    for v in llm_leaks:
        info(f"  missed by NER: {v['type']}")
    placeholders_out = PLACEHOLDER_RE.findall(raw_answer)
    unresolved = PLACEHOLDER_RE.findall(final)
    repairs = provider.last_repairs
    ok(f"Apertus wrote {len(placeholders_out)} tokens verbatim and {repairs} in a degraded "
       f"form that the adapter repaired; {len(unresolved)} left unresolved in the final answer")
    if unresolved:
        info(f"  {len(unresolved)} placeholder(s) could not be restored (model altered or invented them)")
    restored_values = [v["text"] for v in sensitive if v["text"] in final]
    info(f"real values visible to the employee in the final answer: {len(restored_values)}")
    info(f"end-to-end latency: {elapsed:.1f}s (detection + NER + answer on {served})")

    head("7. Audit trail (hash-chained, counts only)")
    events = audit.events_for(auth.team_id)
    for ev in events:
        shown = {k: ev[k] for k in ("route", "provider", "entity_counts", "blocked",
                                    "prompt_tokens", "completion_tokens", "latency_ms")}
        print("  " + json.dumps(shown, ensure_ascii=False, sort_keys=True))
        info(f"prev_hash  {ev['prev_hash'][:16]}...  event_hash {ev['event_hash'][:16]}...")
    audit_text = json.dumps(events, ensure_ascii=False)
    audit_leaks = [v for v in sensitive if v["text"] in audit_text]
    chain_ok = audit.verify(auth.team_id)
    (ok if chain_ok else bad)(f"audit chain verifies: {chain_ok}")
    (ok if not audit_leaks else bad)(
        f"audit log contains {len(audit_leaks)} raw sensitive values (expected 0)"
    )
    # tamper: lower a count after the fact, as an insider hiding a disclosure would
    team_chain = audit._events[str(auth.team_id)]
    original = copy.deepcopy(team_chain[0])
    counts = dict(team_chain[0]["entity_counts"])
    if counts:
        first = sorted(counts)[0]
        counts[first] = max(0, counts[first] - 1)
    else:
        counts = {"PERSON": 1}
    team_chain[0]["entity_counts"] = counts
    tampered_ok = audit.verify(auth.team_id)
    (ok if not tampered_ok else bad)(f"after editing one audit event, chain verifies: {tampered_ok}")
    team_chain[0] = original

    report = {
        "model": served,
        "endpoint": base_url,
        "egress": egress,
        "airgapped": airgapped,
        "latency_s": round(elapsed, 1),
        "structured_identifiers": len(regex_vals),
        "structured_leaks": len(regex_leaks),
        "ner_values": len(llm_vals),
        "ner_caught": caught,
        "ner_missed_types": [v["type"] for v in llm_leaks],
        "placeholders_in_answer": len(placeholders_out),
        "placeholders_repaired": repairs,
        "placeholders_unresolved": len(unresolved),
        "audit_chain_valid": chain_ok,
        "audit_chain_detects_tamper": not tampered_ok,
        "audit_raw_value_leaks": len(audit_leaks),
        "audit_events": events,
        "redacted_prompt": redacted_doc,
        "model_answer_raw": raw_answer,
        "answer_final_synthetic": final,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "demo_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    head("Done")
    info(f"report: {OUT_DIR / 'demo_report.json'}")
    passed = not regex_leaks and chain_ok and not tampered_ok and not audit_leaks
    (ok if passed else bad)("demo checks passed" if passed else "demo checks failed")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

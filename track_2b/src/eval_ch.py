#!/usr/bin/env python3
"""Evaluation on generated synthetic Swiss client notes (de/fr/it).

For N generated notes (fixed seed) it measures:
  A. leak rate of the sensitive values with regex packs only (GENERIC + CH + EU);
  B. the same with the local Apertus LLM-NER pass added;
  C. task fidelity: Apertus is asked to return the client's name, AHV number and IBAN.
     Baseline = raw note sent straight to Apertus (no gateway, what a user would do with a
     local model). RedactGate = the same request through the gateway; the answer is
     re-inflated before scoring. Score = share of the three values present verbatim.

All data is generated here; nothing real. Writes track_2b/out/eval_report.json and
track_2b/data/eval_set.jsonl (the generated cases + labels, for reproducibility).

Usage:  LLM_BASE_URL=http://localhost:8091/v1 python track_2b/src/eval_ch.py [N]
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from collections import Counter, defaultdict
from typing import Any

import demo  # noqa: F401  (sets env + sys.path for the app before it is imported)
import httpx
from demo import OUT_DIR, TRACK, RecordingApertus, build_gateway, wait_for_model

from app.audit import InMemoryAuditSink
from app.auth import AuthContext
from app.config import settings
from app.redaction.detect import Detector
from app.redaction.placeholders import PLACEHOLDER_RE
from app.redaction.store import InMemoryTokenStore
from app.redaction.vault import Vault

SEED = 7
FIRST = ["Lea", "Noah", "Mia", "Luca", "Elena", "Jonas", "Chiara", "Marco", "Sophie",
         "Nicolas", "Giulia", "Matteo", "Anna", "Julien", "Sara", "David"]
LAST = ["Brunner", "Keller", "Rochat", "Bernasconi", "Favre", "Gerber", "Rossi", "Meier",
        "Huber", "Fontana", "Morel", "Steiner", "Galli", "Bonvin", "Widmer", "Ferrari"]
STREETS = {
    "de": ["Bahnhofstrasse", "Seestrasse", "Dorfstrasse", "Kirchgasse", "Industriestrasse"],
    "fr": ["Rue du Lac", "Avenue de la Gare", "Chemin des Vignes", "Rue du Marché"],
    "it": ["Via Nassa", "Via Cantonale", "Via San Gottardo", "Viale Stazione"],
}
TOWNS = {
    "de": [("8001", "Zürich"), ("3011", "Bern"), ("4051", "Basel"), ("6003", "Luzern")],
    "fr": [("1003", "Lausanne"), ("1201", "Genève"), ("2000", "Neuchâtel"), ("1950", "Sion")],
    "it": [("6900", "Lugano"), ("6500", "Bellinzona"), ("6600", "Locarno"), ("6850", "Mendrisio")],
}
ORG_SUFFIX = {"de": ["AG", "GmbH", "Treuhand AG"], "fr": ["SA", "Sàrl", "Conseil SA"],
              "it": ["SA", "Sagl", "Immobili SA"]}
TEMPLATES = {
    "de": [
        "Mandant: {name}, geboren am {dob}, wohnhaft {street}, {town}. AHV-Nr. {ahv}, Tel. {phone}, "
        "E-Mail {email}. Lohn auf IBAN {iban}. Arbeitgeber: {org}, UID {uid} MWST. "
        "Bitte Steuererklärung 2025 vorbereiten.",
        "Notiz zum Erstgespräch: {name} ({dob}) aus {town}, Adresse {street}. Sozialversicherungsnummer "
        "{ahv}. Konto {iban}. Firma {org} (UID {uid}). Rückruf unter {phone}.",
    ],
    "fr": [
        "Client : {name}, né le {dob}, domicilié {street}, {town}. Numéro AVS {ahv}, téléphone {phone}, "
        "courriel {email}. Compte {iban}. Employeur : {org}, IDE {uid} TVA. Préparer la déclaration 2025.",
        "Note d'entretien : {name}, date de naissance {dob}, adresse {street}, {town}. N° AVS {ahv}. "
        "IBAN {iban}. Société {org} (IDE {uid}). Rappeler au {phone}.",
    ],
    "it": [
        "Cliente: {name}, nato il {dob}, residente in {street}, {town}. Numero AVS {ahv}, telefono {phone}, "
        "e-mail {email}. Conto {iban}. Datore di lavoro: {org}, IDI {uid} IVA. Passaporto n. {passport}.",
        "Appunto: {name}, data di nascita {dob}, indirizzo {street}, {town}. AVS {ahv}. IBAN {iban}. "
        "Ditta {org} (IDI {uid}). Richiamare al {phone}.",
    ],
}
QUESTION = ("Gib aus der folgenden Notiz genau eine Zeile zurück im Format "
            "'Name; AHV-Nummer; IBAN'. Keine weiteren Erklärungen.\n\n")


def _ahv(rng: random.Random) -> str:
    body = "756" + "".join(rng.choice("0123456789") for _ in range(9))
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    v = body + str((10 - total % 10) % 10)
    return f"{v[:3]}.{v[3:7]}.{v[7:11]}.{v[11:]}"


def _iban(rng: random.Random) -> str:
    bban = "".join(rng.choice("0123456789") for _ in range(17))
    n = int("".join(str(int(ch, 36)) for ch in bban + "CH00"))
    v = f"CH{98 - n % 97:02d}{bban}"
    return " ".join(v[i : i + 4] for i in range(0, 21, 4))


def _uid(rng: random.Random) -> str:
    while True:
        b = "".join(rng.choice("0123456789") for _ in range(8))
        c = 11 - sum(int(x) * w for x, w in zip(b, (5, 4, 3, 2, 7, 6, 5, 4), strict=True)) % 11
        c = 0 if c == 11 else c
        if c != 10:
            return f"CHE-{b[:3]}.{b[3:6]}.{b[6:]}{c}"


def generate(n: int, seed: int = SEED) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cases = []
    for i in range(n):
        lang = ("de", "fr", "it")[i % 3]
        first, last = rng.choice(FIRST), rng.choice(LAST)
        zip_, city = rng.choice(TOWNS[lang])
        v = {
            "name": f"{first} {last}",
            "dob": f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(1945, 2004)}",
            "street": f"{rng.choice(STREETS[lang])} {rng.randint(1, 120)}",
            "town": f"{zip_} {city}",
            "ahv": _ahv(rng),
            "phone": rng.choice(["+41 79", "+41 76", "+41 44", "+41 21", "+41 91"])
            + f" {rng.randint(200, 999)} {rng.randint(10, 99)} {rng.randint(10, 99)}",
            "email": f"{first.lower()}.{last.lower()}@example.org",
            "iban": _iban(rng),
            "org": f"{rng.choice(LAST)} {rng.choice(ORG_SUFFIX[lang])}",
            "uid": _uid(rng),
            "passport": f"{rng.choice('XSCE')}{rng.randint(1000000, 9999999)}",
        }
        template = rng.choice(TEMPLATES[lang])
        text = template.format(**v)
        types = {"name": "PERSON", "dob": "DATE_OF_BIRTH", "street": "ADDRESS", "town": "ADDRESS",
                 "ahv": "AHV", "phone": "PHONE", "email": "EMAIL", "iban": "IBAN", "org": "ORG",
                 "uid": "UID", "passport": "PASSPORT"}
        labels = [{"slot": k, "type": types[k], "text": v[k]} for k in v if "{" + k + "}" in template]
        cases.append({"id": i, "lang": lang, "text": text, "labels": labels,
                      "expect": [v["name"], v["ahv"], v["iban"]]})
    return cases


async def leak_check(text: str, labels: list[dict[str, str]], *, ner: bool) -> dict[str, Any]:
    settings.enable_llm_ner = ner
    result = await Detector().detect(text, pack_codes=settings.pack_codes)
    vault = Vault(InMemoryTokenStore(), master_key=b"k" * 32, fingerprint_key=b"f" * 32)
    redacted = await vault.tokenize(text, result.spans, session_id="eval")
    leaked = [lab for lab in labels if lab["text"] in redacted]
    covered_ranges = []
    for lab in labels:
        start = text.find(lab["text"])
        covered_ranges.append((start, start + len(lab["text"])))
    false_pos = [s for s in result.spans
                 if not any(s.start < e and b < s.end for b, e in covered_ranges)]
    return {"leaked": leaked, "false_positives": [(s.entity_type, s.text) for s in false_pos]}


def score(answer: str, expect: list[str]) -> float:
    return sum(1 for v in expect if v in answer) / len(expect)


async def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    base_url = settings.apertus_base_url.rstrip("/")
    model = await wait_for_model(base_url)
    cases = generate(n)
    (TRACK / "data" / "eval_set.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), "utf-8"
    )

    per_type_total: Counter[str] = Counter()
    leaks = {"regex": Counter(), "regex+apertus": Counter()}
    fps = {"regex": 0, "regex+apertus": 0}
    fp_examples: list[Any] = []
    fidelity: dict[str, list[float]] = defaultdict(list)
    repairs = 0
    unresolved = 0
    per_case: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    for case in cases:
        for lab in case["labels"]:
            per_type_total[lab["type"]] += 1
        for mode, ner in (("regex", False), ("regex+apertus", True)):
            r = await leak_check(case["text"], case["labels"], ner=ner)
            for lab in r["leaked"]:
                leaks[mode][lab["type"]] += 1
            fps[mode] += len(r["false_positives"])
            if mode == "regex+apertus":
                fp_examples.extend(r["false_positives"][:2])

        # C. task fidelity, baseline (raw to Apertus) vs through RedactGate (regex + NER)
        settings.enable_llm_ner = True
        request = {"model": "default", "temperature": 0.0, "max_tokens": 120,
                   "messages": [{"role": "user", "content": QUESTION + case["text"]}]}
        async with httpx.AsyncClient(timeout=900) as client:
            raw = (await client.post(f"{base_url}/chat/completions",
                                     json={**request, "model": settings.apertus_default_model})).json()
        fidelity["baseline_raw"].append(score(raw["choices"][0]["message"]["content"], case["expect"]))

        provider = RecordingApertus(settings)
        gateway = build_gateway(provider, InMemoryTokenStore(), InMemoryAuditSink(),
                                AuthContext(team_id="eval", policy_id="eval", api_key_id="eval"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                     base_url="http://redactgate.local", timeout=1800) as client:
            resp = (await client.post("/v1/chat/completions", json=request)).json()
        final = resp["choices"][0]["message"]["content"]
        fidelity["redactgate"].append(score(final, case["expect"]))
        repairs += provider.last_repairs
        left = len(PLACEHOLDER_RE.findall(final))
        unresolved += left
        per_case.append({
            "id": case["id"], "lang": case["lang"],
            "baseline_answer": raw["choices"][0]["message"]["content"],
            "model_answer_redacted": provider.received[-1]["choices"][0]["message"]["content"],
            "final_answer": final,
            "missing": [v for v in case["expect"] if v not in final],
            "unresolved_placeholders": left,
        })
        print(f"case {case['id']:>3} {case['lang']}  baseline={fidelity['baseline_raw'][-1]:.2f}  "
              f"redactgate={fidelity['redactgate'][-1]:.2f}", flush=True)

    total_values = sum(per_type_total.values())
    report = {
        "model": model,
        "cases": n,
        "seed": SEED,
        "values_total": total_values,
        "values_by_type": dict(per_type_total),
        "leak_rate": {m: round(sum(c.values()) / total_values, 4) for m, c in leaks.items()},
        "leaks_by_type": {m: dict(c) for m, c in leaks.items()},
        "false_positive_spans": fps,
        "false_positive_examples": fp_examples[:10],
        "task_fidelity_mean": {k: round(sum(v) / len(v), 4) for k, v in fidelity.items()},
        "placeholders_repaired": repairs,
        "placeholders_unresolved": unresolved,
        "per_case": per_case,
        "wall_time_s": round(time.perf_counter() - t0, 1),
    }
    out = OUT_DIR / "eval_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("false_positive_examples", "per_case")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

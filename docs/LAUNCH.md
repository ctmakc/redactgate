# RedactGate — launch kit

Distribution assets for the first users. The user-acquisition plan: OSS/PLG loop (stars →
NLnet/NGI visibility), the published benchmark as content, and the compliance-officer pitch.
Honest, plain, no hype — let the demo and the benchmark do the work.

---

## Show HN

**Title:** Show HN: RedactGate – a self-hosted redaction firewall so you can use cloud LLMs with client data

**Body:**

I build software for regulated firms (tax/legal/corporate services), and the recurring blocker
is simple: staff want to use ChatGPT/Claude on real client files, and they can't, because the
raw PII would leave the perimeter. The usual answers — "don't," or run a small local model — are
either ignored or not good enough.

RedactGate is a drop-in OpenAI-compatible proxy that sits in front of any cloud LLM. For each
request it detects sensitive entities, **reversibly tokenizes** them into encrypted placeholders
(`[[SIN_7f3a]]`) before the call leaves your network, forwards only the sanitized text, then
re-inflates the real values into the model's answer on the way back. The upstream model never
sees raw data; your user gets a normal answer.

The non-obvious part (and the part I found genuinely hard) is keeping answer quality. Naive
masking destroys the relationships the model needs; naive substitution breaks coreference and
breaks SSE streaming (a placeholder splits across chunks). RedactGate keeps **referential
consistency** (same value → same placeholder across a request, so the model still reasons about
"the same person") and does **stream-safe** re-inflation at placeholder boundaries. There's a
reproducible recall-vs-fidelity benchmark in the repo.

Other bits: per-jurisdiction packs (CA/US/EU/UA/IRCC + generic email/phone/card), a
hash-chained append-only audit log that stores entity *type counts* only (never raw values) so
a compliance officer can hand it to an auditor, one `AI_PROVIDER` switch across
Anthropic/OpenAI/Gemini/Azure/Bedrock/Ollama, and an air-gapped mode (regex + Presidio + local
Ollama, no cloud at all).

It's AGPL-3.0, `docker compose up`, no telemetry. I also ran an adversarial security audit on
it and fixed 13 findings (incl. a couple of real redaction-bypass paths) — writeup in
docs/SECURITY.md.

Repo: https://github.com/ctmakc/redactgate

It's early and I'm solo — I'd love feedback on the tokenization/fidelity approach, the
jurisdiction packs (PRs for your country's identifiers very welcome), and whether the audit
model is what your compliance people would actually accept.

---

## r/selfhosted

**Title:** I built a self-hosted "redaction firewall" so you can use cloud LLMs without leaking PII

**Body:**

`docker compose up` gives you an OpenAI-compatible endpoint. Point any client (Cursor,
LibreChat, the OpenAI SDK) at it. It detects PII, swaps it for reversible encrypted tokens
before calling the upstream model, and swaps the real values back into the answer. Raw data
never leaves your box. There's a full air-gapped mode (Presidio + local Ollama, zero cloud).

AGPL, no telemetry, Postgres + Next.js admin UI, hash-chained audit log (counts only).
Benchmark and a security audit in the repo. Would love feedback + jurisdiction-pack PRs.

https://github.com/ctmakc/redactgate

---

## r/LocalLLaMA

**Title:** Redaction firewall in front of cloud LLMs — reversible tokenization that keeps answer quality (benchmark inside)

**Body:**

The interesting problem here is preserving answer fidelity while redacting. I tokenize entities
reversibly with referential consistency (same value → same placeholder across the prompt, so
coreference survives) and re-inflate stream-safe at placeholder boundaries. There's a
reproducible recall-vs-fidelity harness (LLM-judge compares the redacted round-trip vs a raw
call). Works with a local Ollama as the backend too, fully air-gapped. AGPL, self-hosted.

Curious what this sub thinks of the fidelity tradeoff and the detection coverage.
https://github.com/ctmakc/redactgate

---

## One-pager (for warm outreach to tax/legal/corporate-services contacts)

**Subject:** Let your team use AI on client files — safely

Your associates want to use AI on real files; compliance says no, because the raw data would
leave your control. RedactGate removes the blocker: it sits between your staff and any AI model,
strips and reversibly tokenizes the sensitive data before it leaves your network, and logs every
request for your audit — entity counts only, never the values. The model still does the work;
the client's record never leaves the building.

- Self-hosted (your servers) or fully offline. Open source, no per-seat fee.
- Works with the AI you already use, or a local model.
- A tamper-evident audit log your compliance officer can defend.

Worth a 20-minute look? Demo + repo: https://github.com/ctmakc/redactgate

---

## Where to post (in order)

1. GitHub — make sure README + screenshot + benchmark land well (done).
2. r/selfhosted, r/LocalLLaMA — the natural self-host/LLM audiences.
3. Show HN — Tue–Thu morning ET.
4. Warm outreach — existing crystal.tax / legal.ua / Innova Consult / ShelfVault contacts (the
   buyers who already have this exact blocker).
5. dev.to / a short writeup of the recall-vs-fidelity benchmark as standalone content.

NOTE: posting these is an outward-facing action — review and post under your own accounts.

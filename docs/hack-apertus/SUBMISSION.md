# Hack Apertus — submission package (Track 2B, Own Project)

Submission goes through **https://hackapertus.ch/online-hack/submissions** (Devpost only
hosts the event page). Deadline: **16 Oct 2026, 12:00 CEST** = 06:00 Ottawa.

## Form fields (Track 2B form, checked 12 Oct 2026)

| Field | Value |
|---|---|
| Select track/challenge | `Track 2B - Own Project` |
| First name / Last name | owner fills in |
| Email (used for Devpost sign-up) | owner fills in: the e-mail of the Devpost account that joined Hack Apertus |
| GitHub username | `ctmakc` |
| Hugging Face username | leave empty (optional) |
| Team name | `RedactGate` |
| Team members | owner fills in |
| Project name | `RedactGate × Apertus` |
| Swiss student team | `No` |
| Model | `8B v1.5` |
| Technical report (PDF, max. 6 pages) | `track_2b/RedactGate_Report.pdf` |
| Hugging Face dataset URL | leave empty (optional; the synthetic data is in the repo) |
| GitHub repository URL | `https://github.com/ctmakc/redactgate/tree/hack-apertus/track_2b` |
| Demo video URL (max. 2 min) | URL of the uploaded video (script: `VIDEO_SCRIPT.md`) |
| Your message | text below |
| Terms and Conditions checkbox | owner ticks after reading section 6 (licence) |

**Your message (optional), ready to paste:**

> RedactGate × Apertus runs fully air-gapped: `make run` in `track_2b/` downloads the
> Apertus v1.5 8B GGUF once, then llama.cpp and the gateway run on an internal Docker
> network with no internet route. Apertus is both the answering model and the local PII
> detector. The gateway core (vault, audit chain, proxy) predates the event; everything
> built during Hack Apertus is listed in section 2 of the technical report.

## Project name

RedactGate × Apertus

## Tagline

A self-hosted privacy gateway that lets Swiss fiduciaries use Apertus on client files:
AHV numbers, IBANs and names are swapped for reversible tokens, and nothing leaves the
building.

## Story

### Inspiration

A Swiss fiduciary office runs on documents full of AHV numbers, IBANs, company UIDs, birth
dates and home addresses. The staff would gladly hand the routine part (task lists from a
dossier, summaries, first drafts of letters) to an LLM. The client data cannot go to a US
cloud, and a local model alone gives no record of what was processed. We wanted a setup a
small office can run on one machine: a Swiss open model, a gateway that keeps the identifiers
out of the prompt, and an audit trail a compliance officer can check.

### What it does

RedactGate is an OpenAI-compatible gateway. An employee (or any tool that speaks the OpenAI
API) sends a request; the gateway finds the sensitive values, replaces each one with a
reversible placeholder such as `[[AHV_6985b6]]`, sends the redacted prompt to Apertus, and
puts the real values back into the answer before the employee sees it. The same value always
gets the same placeholder within a request, so the model can still reason about "the client"
and "her IBAN". Every request writes an audit event with entity counts and an HMAC hash
chain; values never enter the log.

Apertus has two jobs in the gateway:

1. **Answering model.** Apertus v1.5 8B on llama.cpp answers the redacted prompt.
2. **Local PII detector.** The same model reads the raw text and returns names, companies and
   addresses as schema-constrained JSON, in German, French and Italian. Because it runs
   inside the perimeter, the raw text never leaves the machine even for this step.

A new Swiss pack covers the structured identifiers with check-digit validation: AHV/AVS
(EAN-13), CH and LI IBAN (mod-97), UID/IDE/IDI (mod-11), Swiss phone numbers, passport and
identity-card numbers next to their keywords, and birth dates with de/fr/it cues.

### How we built it

- Python 3.12, FastAPI gateway with AES-256-GCM token vault and hash-chained audit (existing
  RedactGate core).
- New `apertus` provider for any OpenAI-compatible server; it reads the Hack Apertus
  variables `LLM_BASE_URL`, `LLM_NAME`, `LLM_API_KEY`.
- llama.cpp `llama-server` (build b11561) serving a Q4_K_M GGUF of the Apertus v1.5 8B text
  tower; the chat template comes from the GGUF.
- NER prompt with one worked example and a JSON schema that llama.cpp compiles into a
  grammar, so the 8B model always returns valid JSON.
- Docker Compose with an `internal: true` network for the model and the gateway; only the
  one-time model download has internet access.
- A demo that captures the exact bytes sent to the model and searches them for every
  sensitive value, and an evaluation on 30 generated client notes.
- 93 new unit tests (440 in total, all green).

### Challenges we ran into

- **Apertus rewrote the placeholders.** In the first run it answered `AHV c4857b` instead of
  `[[AHV_c4857b]]`, so nothing could be restored. A system instruction with one correct and
  three wrong examples fixed most cases, and a repair step now rewrites degraded tokens back
  to the canonical form, only for codes the model was actually given. On the 30-note
  evaluation it repaired 76 tokens and left none unresolved.
- **A repetition loop in NER.** On the full dossier the model once listed the same IBAN until
  it hit the token limit; the JSON was cut off and every entity was lost. A `maxItems` cap in
  the schema and a parser that salvages complete items fixed it.
- **Hardware.** On a 6-core CPU the 8B model generates about 5 tokens/s, so the air-gapped
  `make run` takes about six minutes; with an RTX 3060 the same request takes 23 seconds.

### Accomplishments that we're proud of

- The full chain runs with no internet route, and the demo proves it with an egress probe.
- On the synthetic dossier, 0 of 26 sensitive values reached the model and all 9 placeholders
  in the answer were restored.
- On 30 generated notes, the Apertus NER pass cut the share of sensitive values reaching the
  model from 42.5 % (regex packs alone) to 5.8 %, while task fidelity stayed at 95.6 %
  compared with 98.9 % for sending the raw note.
- Editing one audit event after the fact breaks the hash chain, and the demo shows it.

### What we learned

- An 8B model follows a formatting rule much better with a concrete wrong example than with
  a stricter sentence.
- Grammar-constrained decoding turns a small model into a dependable extractor; without it,
  one bad loop costs the whole result.
- Check digits matter more than regex cleverness for Swiss identifiers: AHV, IBAN and UID
  all carry one, and validating them keeps random numbers in the text untouched.

### What's next

- Apertus 70B on a sovereign Swiss endpoint, compared side by side with the local 8B.
- A LoRA adapter for PII spans in Swiss administrative text, trained on generated data.
- Cantonal tax numbers, health-insurance card numbers and QR-bill references in the Swiss
  pack.
- Placeholder repair in streaming mode.
- A pilot with a fiduciary office on consented real documents.

## Built with

Apertus v1.5 8B · llama.cpp · GGUF · Python · FastAPI · httpx · Pydantic · cryptography
(AES-256-GCM, HKDF) · PostgreSQL (production mode) · Docker Compose · pytest · respx

## Links

- Code (Track 2B folder): https://github.com/ctmakc/redactgate/tree/hack-apertus/track_2b
- Technical report: `track_2b/technical_report.md` and `track_2b/RedactGate_Report.pdf`
- Demo transcript of the air-gapped run: `track_2b/docs/results/make_run_transcript.txt`
- Evaluation results: `track_2b/docs/results/eval_report.json`
- Screenshots: `docs/hack-apertus/screenshots/`
- Demo video (93 s): `docs/hack-apertus/video/out/redactgate_apertus_demo.mp4`
- Model weights: https://huggingface.co/Colby/apertus-v1.5-8b-text-Q4_K_M-GGUF
  (from https://huggingface.co/swiss-ai/Apertus-v1.5-8B)
- Live landing page of RedactGate: https://redactgate.pages.dev

## Screenshots

1. `screenshots/01_airgapped_runtime_and_wire_capture.png` — egress blocked, and the prompt
   exactly as Apertus received it.
2. `screenshots/02_apertus_answer_reinflated.png` — Apertus' answer on placeholders and the
   answer the employee gets back.
3. `screenshots/03_checks_and_audit_chain.png` — leak checks, audit event, tamper test.

## Open points for the owner before submitting

1. **Licence.** Event terms (section 6): hackathon code is released under Apache-2.0.
   RedactGate is AGPL-3.0 with a commercial exemption. Decide whether the `hack-apertus`
   additions are released under Apache-2.0 (and how that sits next to the AGPL core), or
   whether to submit at all. The repository licence was left unchanged.
2. **"The project must be new, started within the hackathon period"** (Track 2B README).
   The RedactGate core dates from June 2026. The report says so openly and lists what was
   built during the event. If the jury reads the rule strictly, the entry may be ruled out.
3. **Template repository.** The rules ask for a repo created from
   `HackApertus/project-template` with only `track_2b/`. The branch carries `track_2b/` with
   the template files (`README.md`, `technical_report.md`, `Makefile`, `src/`, `data/`,
   `docs/`). A separate repo from the template can be created if the jury needs it.
4. **Demo video** (max. 2 min): a 93-second cut is ready at
   `docs/hack-apertus/video/out/redactgate_apertus_demo.mp4` (synthetic gTTS voice, captions
   burned in). Upload it (YouTube unlisted or similar), or use the GitHub file URL
   `https://github.com/ctmakc/redactgate/blob/hack-apertus/docs/hack-apertus/video/out/redactgate_apertus_demo.mp4`;
   paste the URL into the form and into `track_2b/technical_report.md` (`{{VIDEO_URL}}`).
5. **Team members** in the report header (`{{TEAM_MEMBERS}}`) and the form, then regenerate
   the PDF.
6. **Model access terms.** The weights came from an ungated community GGUF (Apache-2.0). The
   official swiss-ai repository asks users to accept the Apertus Acceptable Use Policy; doing
   that once on Hugging Face keeps the record clean.

# RedactGate Desktop — offline document anonymizer

A desktop app on the RedactGate engine (regex packs + validators, `merge_spans`, the
`Vault` AES-256-GCM/HKDF/HMAC crypto) for people who carry documents into a chat LLM
themselves: drop PDF/DOCX/XLSX/PPTX/CSV/TXT/scans → get copies where people and companies
read «Фізособа-1», «Юрособа-1» (ru: Физлицо/Юрлицо, en: Person/Company), accounts, ЄДРПОУ,
ІПН, SIN/SSN, addresses, phones, emails (amounts optional) get their own labels → paste the
AI's answer back → real data returns. Same value = same label across the whole project.

Code: `app/desktop/` (engine, vault, formats, server, UI), tests `tests/desktop/`,
eval `eval/desktop/`, packaging `packaging/desktop/`, preview landing `site-desktop/`.

## Run from source
    python -m venv .venv-desktop && .venv-desktop/bin/pip install -r packaging/desktop/requirements-desktop.txt
    PYTHONPATH=. .venv-desktop/bin/python -m app.desktop            # opens the window
    PYTHONPATH=. .venv-desktop/bin/python -m app.desktop --no-browser --port 18731   # headless

## How it stays offline
* server binds 127.0.0.1 only; per-launch token; Host/Origin checks (no DNS rebinding / cross-site use);
* `app/desktop/netguard.py` wraps the socket layer: any non-loopback connect/DNS is refused and counted;
* UI assets (fonts included) are local; CSP `default-src 'self'`;
* vault: scrypt(password) → key-wrap → data key; values AES-256-GCM per project (HKDF), lookups via HMAC;
  project names, file names and per-document restore maps are encrypted too.
* scans: if Tesseract is missing or fails, the file is refused (never passed through unredacted).

## Build
    packaging/desktop/build_windows.sh   # Docker + Wine: PyInstaller + NSIS -> dist-desktop/windows/*.exe
    packaging/desktop/build_linux.sh     # Docker bookworm: AppImage + .deb -> dist-desktop/linux/
    # macOS (and a native Windows cross-check): .github/workflows/desktop-build.yml on GitHub Actions

## Verify
    .venv-desktop/bin/python eval/desktop/e2e.py --seeds 7,11,23,41,58   # engine, 5 synthetic cases
    .venv-desktop/bin/python eval/desktop/heldout.py                       # hand-written snippets
    packaging/desktop/verify_windows.sh   # silent install in Wine, --network none, black-box e2e
    packaging/desktop/verify_deb.sh       # .deb in clean Debian 12, --network none, black-box e2e

## Results (06.11.2026, v0.1.0)
| Check | Result |
|---|---|
| Engine e2e, 5 synthetic auditor cases (822 values) | recall 100% every category, 35/35 round trips |
| Held-out snippets v2 (written after tuning, untuned) | PERSON/ORG/ACCOUNT 21/24 = 87.5%, overall 90.6% → gaps fixed, now 100%, 0 false positives on 8 bait texts |
| Windows build, Wine, no network | install /S ok, demo 6/6, black-box e2e PASS, 0 outbound, scan refused without OCR |
| Windows build, native GitHub runner | black-box e2e PASS |
| macOS arm64 build, GitHub runner | black-box e2e PASS |
| Linux AppImage under strace | e2e PASS incl. OCR scan, 0 AF_INET connect() |
| .deb in clean Debian 12, no network | e2e PASS, scan refused without OCR |
| Unit tests | 357 passed (347 existing + 10 desktop) |

Round trip = TXT/CSV byte-for-byte; DOCX/XLSX text of every paragraph/cell; PDF page text.

## Known limits
* Installers are not code-signed (no paid certificate): SmartScreen / Gatekeeper warnings.
* OCR needs Tesseract installed (Windows/macOS: separate install; .deb: Recommends).
* Images inside DOCX/PDF are not inspected; XLSX numeric cells are labelled only in ID/amount columns.
* Rule-based NER: unusual names without context can be missed — check the copy before sending.
* The window is a Chromium-family browser in app mode (Edge/Chrome); the browser itself is outside the app's network guard.

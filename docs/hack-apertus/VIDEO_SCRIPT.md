# Demo video script — RedactGate × Apertus (max. 2:00)

**A ready cut exists:** `docs/hack-apertus/video/out/redactgate_apertus_demo.mp4`
(1920×1080, 93 s, captions burned in, subtitles in `.srt`). Slides are rendered from the real
`make run` transcript and `eval_report.json`; build with `python3 docs/hack-apertus/video/build.py`,
spoken text in `video/scenes.json`. The voice is gTTS (Google Translate TTS, sped up 1.15×)
because edge-tts returned HTTP 403 on 12 Oct; it sounds synthetic. For a better voice, delete
`video/out/*.mp3` and rebuild once edge-tts works again, or record your own voice over the same
slides. The table below is the script for a manual recording.

Hack Apertus allows **at most 2 minutes**. Screen recording with voice-over; no face needed.
Material: a terminal with `make run` output (or `track_2b/docs/results/make_run_transcript.txt`),
the three screenshots in `screenshots/`, and the repository page.

Recording tips: 1920×1080, terminal font 18 px or larger, dark theme. A native GPU run of
`python track_2b/src/demo.py` finishes in about 25 s and fits the video; the air-gapped
Docker run on CPU takes about 6 minutes, so show its start and cut to the saved transcript.

| Time | On screen | Voice-over (English) |
|---|---|---|
| 0:00–0:12 | Synthetic dossier (section 2 of the demo output): three clients in German, French, Italian | "A Swiss fiduciary office: three client files in German, French and Italian, full of AHV numbers, IBANs, company IDs, birth dates and addresses. All of it synthetic." |
| 0:12–0:25 | `make run` start; section 1: egress to huggingface.co and 1.1.1.1 blocked | "RedactGate runs next to Apertus v1.5 8B on llama.cpp, on a Docker network with no route to the internet. The demo checks that first." |
| 0:25–0:50 | Screenshot 01: the prompt as Apertus received it, placeholders highlighted | "Before anything reaches the model, the Swiss pack finds every AHV, IBAN and UID by check digit, and Apertus itself, running locally, finds the names, companies and addresses. Each value becomes a reversible token. This is exactly what the model receives." |
| 0:50–1:10 | Screenshot 02: Apertus' answer with tokens, then the re-inflated answer | "Apertus writes the task list on tokens only. The gateway puts the real values back before the employee sees the answer." |
| 1:10–1:30 | Screenshot 03: checks and audit trail | "The demo searches every byte sent to the model: zero of 26 values leaked. The audit log keeps counts and a hash chain, never values. Edit one event and the chain breaks." |
| 1:30–1:45 | Evaluation table from the technical report | "On 30 generated client notes, the local Apertus detector cuts what reaches the model from 42.5 percent to 5.8 percent, and the answers stay 95.6 percent correct." |
| 1:45–2:00 | Repository page, `track_2b/` folder, `make run` | "One command, one machine, no cloud. RedactGate with Apertus: open source, built for Swiss client data." |

Word count of the voice-over: about 230 words, which fits 2:00 at a calm pace.

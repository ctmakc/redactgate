# Demo video script (78 s)

Recorded from the live console at https://redactgate.mmix.biz with Playwright's screen
recorder (headless Chromium, 1536x864 upscaled to 1080p), voice by edge-tts
(en-US-AndrewNeural), captions burned in from the generated `.srt`. The wait for Claude's
answer is cut; nothing else is edited. All data is synthetic.

Rebuild: `python docs/build-north/video/build.py` (scenes and narration in
`docs/build-north/video/scenes.json`).

| # | Screen | Narration |
|---|---|---|
| 1 | Sign-in page, file-note specimen sealing its values | Accountants, lawyers and clinics paste client notes into AI chat every day. RedactGate is a self-hosted gateway that lets a firm use Claude without sending Claude its clients. |
| 2 | Firm dashboard, Clerk firm switcher, role stamp | Staff sign in with Clerk. Each firm is a Clerk organization, and the member's role decides what they can do: admin, compliance officer, or member. |
| 3 | Playground: tax sample, "Seal and send to Claude" | Here is a synthetic tax file note with a social insurance number, a health card, a bank account and an address. One click seals every value and sends the note to Claude Opus 5.5. |
| 4 | Three panes: note with tagged values, payload Claude received, answer with restored values | On the left, what stayed inside the firm, each value tagged by type. In the middle, the exact text Claude received: placeholders only. On the right, Claude's answer, with the real values put back on our side of the line. |
| 5 | Audit row written by that request | Each request writes one audit event, chained by hash: entity counts, tokens and latency. The note itself is never stored. |
| 6 | Signed in as compliance officer: audit log, chain verified, read-only policies | A compliance officer signs in to the same firm. She sees the audit trail with the chain re-verified, and read-only policies. She cannot send prompts. |
| 7 | Sign-up page | Clerk sign-in, firm roles, the playground and the Canadian detection fixes were built during Build North. RedactGate is open source. Try it at redactgate.mmix.biz. |

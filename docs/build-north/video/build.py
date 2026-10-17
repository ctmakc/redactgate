#!/usr/bin/env python3
"""Build the Build North demo video from a real screen recording of the live console.

Each scene drives https://redactgate.mmix.biz in headless Chromium with Playwright's video
recorder (no mock-ups), gets an edge-tts voice-over (gTTS fallback), and is cut to the
length of its narration; the playground's wait for Claude is cut out. Captions are burned
in from the generated .srt.

Usage:  python docs/build-north/video/build.py
Needs:  playwright + chromium, ffmpeg/ffprobe with libass, edge-tts at $EDGE_TTS,
        demo accounts in $RG_DEMO_USERS (env file with DEMO_PASSWORD; Clerk test emails).
Output: docs/build-north/video/out/redactgate_build_north.mp4 (+ .srt)
"""

# ruff: noqa: E702  (compact Playwright step scripts: one browser action per statement pair)
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
WORK = OUT / "work"
BASE = os.environ.get("BASE", "https://redactgate.mmix.biz")
EDGE_TTS = os.environ.get("EDGE_TTS", "/data/venvs/edge-tts/bin/edge-tts")
GTTS = os.environ.get("GTTS", "gtts-cli")
CHROME = os.environ.get("CHROME", os.path.expanduser("~/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome"))
USERS_ENV = pathlib.Path(os.environ.get("RG_DEMO_USERS", HERE.parents[2] / ".bn/demo-users.env"))
VOICE, RATE = "en-US-AndrewNeural", "+4%"
VW, VH = 1536, 864  # recorded size; upscaled to 1920x1080
SCENES = json.loads((HERE / "scenes.json").read_text(encoding="utf-8"))
USERS = {
    "admin": "avery.admin+clerk_test@example.com",
    "compliance": "casey.compliance+clerk_test@example.com",
}


def sh(*args: str) -> str:
    return subprocess.run(list(args), check=True, capture_output=True, text=True).stdout


def duration(path: pathlib.Path) -> float:
    return float(sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                    "default=nw=1:nk=1", str(path)).strip())


def tts(text: str, mp3: pathlib.Path) -> str:
    if mp3.exists() and mp3.stat().st_size > 0:
        return "cached"
    for attempt in range(3):
        r = subprocess.run([EDGE_TTS, "--voice", VOICE, f"--rate={RATE}", "--text", text,
                            "--write-media", str(mp3)], capture_output=True, text=True)
        if r.returncode == 0 and mp3.exists() and mp3.stat().st_size > 0:
            return "edge-tts"
        time.sleep(2 + attempt)
    raw = mp3.with_suffix(".gtts.mp3")
    subprocess.run([GTTS, text, "-l", "en", "--tld", "ca", "-o", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "atempo=1.12", str(mp3)], check=True)
    return "gtts"


# ── browser helpers ──────────────────────────────────────────────────────────────


def sign_in(browser, who: str) -> pathlib.Path:
    """Sign in once through the real Clerk <SignIn/> and keep the storage state."""
    state = WORK / f"state-{who}.json"
    if state.exists():
        return state
    env = dict(re.findall(r"^(\w+)=(.*)$", USERS_ENV.read_text(), re.M))
    ctx = browser.new_context(viewport={"width": VW, "height": VH})
    pg = ctx.new_page()
    pg.goto(BASE + "/sign-in"); pg.wait_for_timeout(3000)
    pg.fill('input[name="identifier"]', USERS[who]); pg.click('button:has-text("Continue")')
    pg.wait_for_timeout(2500)
    pg.fill('input[name="password"]', env["DEMO_PASSWORD"]); pg.click('button:has-text("Continue")')
    pg.wait_for_timeout(4000)
    otp = pg.locator('input[autocomplete="one-time-code"], input[inputmode="numeric"]')
    if otp.count():
        otp.first.click(); pg.keyboard.type("424242", delay=80); pg.wait_for_timeout(5000)
    pg.wait_for_url(re.compile(r"^(?!.*sign-in).*$"), timeout=30000)
    ctx.storage_state(path=str(state))
    ctx.close()
    return state


def smooth_scroll(pg, to_y: int, ms: int = 1600) -> None:
    pg.evaluate(
        """([y, ms]) => new Promise(r => { const s = scrollY, d = y - s, t0 = performance.now();
        const f = t => { const k = Math.min(1, (t - t0) / ms); const e = 1 - Math.pow(1 - k, 3);
        scrollTo(0, s + d * e); k < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); })""",
        [to_y, ms],
    )


def y_of(pg, selector: str, offset: int = 80) -> int:
    box = pg.locator(selector).first.bounding_box() or {"y": 0}
    return int(box["y"] + pg.evaluate("scrollY") - offset)


# ── scenes: each returns a list of (start, end) seconds of the raw recording to keep ─


def scene_signin(pg, mark):
    pg.goto(BASE + "/sign-in"); pg.wait_for_load_state("networkidle")
    t = mark(); pg.wait_for_timeout(14000)
    return [(t, mark())]


def scene_dashboard(pg, mark):
    pg.goto(BASE + "/"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(800)
    t = mark()
    pg.hover(".cl-organizationSwitcherTrigger"); pg.wait_for_timeout(1500)
    pg.click(".cl-organizationSwitcherTrigger"); pg.wait_for_timeout(3500)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(1200)
    pg.hover("text=your role"); pg.wait_for_timeout(5000)
    return [(t, mark())]


def scene_run(pg, mark):
    pg.goto(BASE + "/playground"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(800)
    t0 = mark()
    pg.click("text=tax file note"); pg.wait_for_timeout(1500)
    pg.hover("textarea"); pg.wait_for_timeout(5500)
    pg.hover('button:has-text("Seal and send to Claude")'); pg.wait_for_timeout(900)
    pg.click('button:has-text("Seal and send to Claude")')
    pg.wait_for_timeout(2200)
    t1 = mark()  # pending state visible; cut the wait for Claude here
    pg.wait_for_selector("text=what Claude received", timeout=240000)
    pg.wait_for_timeout(300)
    t2 = mark()
    smooth_scroll(pg, y_of(pg, "text=values sealed", 30), 1400)
    pg.wait_for_timeout(5200)
    return [(t0, t1), (t2, mark())]


def scene_panes(pg, mark):
    # continues on the same page as scene_run
    t = mark()
    pg.hover("mark.ent"); pg.wait_for_timeout(6500)
    pg.hover(".tok-seal"); pg.wait_for_timeout(6500)
    pg.hover(".restored"); pg.wait_for_timeout(4000)
    smooth_scroll(pg, y_of(pg, "text=Should you open", 250) if pg.locator("text=Should you open").count() else 900, 2200)
    pg.wait_for_timeout(4500)
    return [(t, mark())]


def scene_audit(pg, mark):
    t = mark()
    smooth_scroll(pg, y_of(pg, "text=what was recorded", 380), 1800)
    pg.wait_for_timeout(10500)
    return [(t, mark())]


def scene_compliance(pg, mark):
    pg.goto(BASE + "/audit"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(800)
    t = mark()
    pg.hover("text=chain verified"); pg.wait_for_timeout(5000)
    pg.hover("nav >> text=policies"); pg.wait_for_timeout(1200)
    pg.click("nav >> text=policies"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(7000)
    return [(t, mark())]


def scene_close(pg, mark):
    pg.goto(BASE + "/sign-up"); pg.wait_for_load_state("networkidle")
    t = mark(); pg.wait_for_timeout(15000)
    return [(t, mark())]


FUNCS = {
    "signin": (None, scene_signin),
    "dashboard": ("admin", scene_dashboard),
    "run": ("admin", scene_run),
    "panes": ("admin", scene_panes),
    "audit": ("admin", scene_audit),
    "compliance": ("compliance", scene_compliance),
    "close": (None, scene_close),
}


def record(browser) -> dict[str, tuple[pathlib.Path, list[tuple[float, float]]]]:
    """Record scenes; consecutive scenes for the same user share one context/video."""
    results: dict[str, tuple[pathlib.Path, list[tuple[float, float]]]] = {}
    groups: list[tuple[str | None, list[dict]]] = []
    for sc in SCENES:
        who = FUNCS[sc["id"]][0]
        if groups and groups[-1][0] == who and who is not None:
            groups[-1][1].append(sc)
        else:
            groups.append((who, [sc]))
    for gi, (who, scs) in enumerate(groups):
        state = sign_in(browser, who) if who else None
        vdir = WORK / f"rec{gi}"
        shutil.rmtree(vdir, ignore_errors=True)
        ctx = browser.new_context(viewport={"width": VW, "height": VH}, record_video_dir=str(vdir),
                                  record_video_size={"width": VW, "height": VH},
                                  storage_state=str(state) if state else None)
        pg = ctx.new_page()
        t0 = time.monotonic()

        def mark(t0: float = t0) -> float:
            return time.monotonic() - t0

        spans = {sc["id"]: FUNCS[sc["id"]][1](pg, mark) for sc in scs}
        video = pathlib.Path(pg.video.path())
        ctx.close()
        for sid, sp in spans.items():
            results[sid] = (video, sp)
        print(f"recorded {[s['id'] for s in scs]} -> {video.name}", flush=True)
    return results


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> int:
    OUT.mkdir(exist_ok=True); WORK.mkdir(exist_ok=True)
    for sc in SCENES:
        print(sc["id"], tts(sc["say"], WORK / f"{sc['id']}.mp3"), flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        rec = record(browser)
        browser.close()

    segs, srt, t = [], [], 0.0
    for i, sc in enumerate(SCENES, 1):
        mp3 = WORK / f"{sc['id']}.mp3"
        need = duration(mp3) + 0.6
        video, spans = rec[sc["id"]]
        parts = []
        for k, (a, b) in enumerate(spans):
            part = WORK / f"{sc['id']}_{k}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(video),
                            "-vf", "scale=1920:1080:flags=lanczos,fps=30", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-crf", "18", str(part)], check=True)
            parts.append(part)
        joined = WORK / f"{sc['id']}_v.mp4"
        (WORK / "parts.txt").write_text("".join(f"file '{x.name}'\n" for x in parts))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(WORK / "parts.txt"),
                        "-c", "copy", str(joined)], check=True)
        have = duration(joined)
        seg = WORK / f"seg_{i:02d}.mp4"
        # trim or hold the last frame so picture length == narration length
        vf = f"tpad=stop_mode=clone:stop_duration={max(0.0, need - have):.3f}"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-i", str(mp3), "-vf", vf,
                        "-af", "apad", "-t", f"{need:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(seg)], check=True)
        segs.append(seg)
        # one caption per sentence, timed by share of characters
        sentences = [x.strip() for x in re.split(r"(?<=[.:!?])\s+", sc["caption"]) if x.strip()]
        total = sum(len(x) for x in sentences)
        at, span = t, need - 0.5
        for sent in sentences:
            d = span * len(sent) / total
            srt.append(f"{len(srt) + 1}\n{srt_time(at)} --> {srt_time(at + d)}\n{sent}\n")
            at += d
        t += need
    (WORK / "segments.txt").write_text("".join(f"file '{s.name}'\n" for s in segs))
    raw = WORK / "raw.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(WORK / "segments.txt"),
                    "-c", "copy", str(raw)], check=True)
    srt_path = OUT / "redactgate_build_north.srt"
    srt_path.write_text("\n".join(srt), encoding="utf-8")
    final = OUT / "redactgate_build_north.mp4"
    style = ("FontName=DejaVu Sans,FontSize=9,PrimaryColour=&H00EEF0F2,OutlineColour=&H00100F0C,"
             "BackColour=&H30100F0C,BorderStyle=4,Outline=0,Shadow=0,MarginV=14,MarginL=60,MarginR=60,Alignment=2")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf",
                    f"subtitles={srt_path}:force_style='{style}'", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", str(final)], check=True)
    print(f"{final}  {duration(final):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

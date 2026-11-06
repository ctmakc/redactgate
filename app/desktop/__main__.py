"""RedactGate Desktop launcher: start the local server, open the app window, exit when closed.

    redactgate-desktop [--data-dir DIR] [--port N] [--no-browser] [--idle-exit SECONDS]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def default_data_dir() -> Path:
    env = os.environ.get("REDACTGATE_DATA")
    if env:
        return Path(env)
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "RedactGate Desktop"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "RedactGate Desktop"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "redactgate-desktop"


_APP_FLAGS = [
    "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--disable-domain-reliability",
    "--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication,InterestFeedContentSuggestions",
    "--disable-client-side-phishing-detection", "--no-pings", "--metrics-recording-only",
    "--window-size=1360,900",
]


def _browser_candidates() -> list[str]:
    system = platform.system()
    if system == "Windows":
        pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        rel = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"BraveSoftware\Brave-Browser\Application\brave.exe", r"Chromium\Application\chrome.exe"]
        return [os.path.join(b, r) for b in pf if b for r in rel]
    if system == "Darwin":
        return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    out = []
    for n in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge", "brave-browser"):
        w = shutil.which(n)
        if w:
            out.append(w)
    return out


def open_window(url: str, data_dir: Path) -> str:
    """Open the UI as an app window of a Chromium-family browser, else the default browser."""
    profile = data_dir / "window-profile"
    for exe in _browser_candidates():
        if os.path.isfile(exe):
            try:
                kw = {}
                if os.name == "nt":
                    kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
                from app.desktop.formats import clean_env

                subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", *_APP_FLAGS],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=clean_env(), **kw)
                return exe
            except OSError:
                continue
    if platform.system() == "Linux":
        from app.desktop.formats import clean_env

        try:
            subprocess.Popen(["xdg-open", url], env=clean_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "xdg-open"
        except OSError:
            pass
    webbrowser.open(url)
    return "default-browser"


def _existing_instance(data_dir: Path) -> str | None:
    f = data_dir / "instance.json"
    try:
        info = json.loads(f.read_text("utf-8"))
        url = f"http://127.0.0.1:{info['port']}/api/heartbeat"
        req = urllib.request.Request(url, headers={"X-RG-Token": info["token"], "Host": f"127.0.0.1:{info['port']}"})
        with urllib.request.urlopen(req, timeout=1.5) as r:  # noqa: S310 - loopback only
            if r.status == 200:
                return f"http://127.0.0.1:{info['port']}/#t={info['token']}"
    except Exception:  # noqa: BLE001
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="redactgate-desktop")
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true", help="do not open a window (tests, servers)")
    ap.add_argument("--idle-exit", type=int, default=180,
                    help="quit this many seconds after the window stops sending heartbeats (0 = never)")
    ap.add_argument("--print-url", action="store_true")
    a = ap.parse_args(argv)

    data_dir = a.data_dir or default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(data_dir / "app.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    from app.desktop import netguard

    netguard.install()  # before anything else can open a socket

    existing = None if a.no_browser else _existing_instance(data_dir)
    if existing:
        open_window(existing, data_dir)
        return 0

    from app.desktop.server import make_server

    httpd, app = make_server(data_dir, a.port)
    url = f"http://127.0.0.1:{app.port}/#t={app.token}"
    (data_dir / "instance.json").write_text(json.dumps({"port": app.port, "token": app.token, "pid": os.getpid()}), "utf-8")
    try:
        os.chmod(data_dir / "instance.json", 0o600)
    except OSError:
        pass
    logging.info("listening on 127.0.0.1:%s", app.port)
    if a.print_url or a.no_browser:
        print(url, flush=True)
    if not a.no_browser:
        threading.Timer(0.4, open_window, args=(url, data_dir)).start()

    if a.idle_exit > 0:
        def watchdog() -> None:
            while True:
                time.sleep(5)
                if app.seen_ui and time.time() - app.last_seen > a.idle_exit:
                    logging.info("window closed; exiting")
                    app.vault.lock()
                    httpd.shutdown()
                    return

        threading.Thread(target=watchdog, daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.vault.lock()
        try:
            (data_dir / "instance.json").unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

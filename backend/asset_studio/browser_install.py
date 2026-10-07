"""A headless Chromium for the forge, the review and the live link — fetched when the PC has none.

On a Windows PC nothing is ever downloaded: Edge is Chromium and is always there, and `_find_chrome`
in workspace.py knows it. This module is for the machine that has neither Chrome nor Edge (a Mac
or Linux box, or a locked-down Windows image): it pulls Google's "Chrome for Testing" build — a
plain zip, no installer, no admin rights — into the data dir, and the finder picks it up.

Never raises. Progress and the last error are readable from `status()`, so the Settings page can
say "downloading, 40%" instead of "Chrome was not found" while it is on its way.
"""
from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from .config import DATA_DIR

_DIR = DATA_DIR / "tools" / "chrome"
_PW = DATA_DIR / "tools" / "ms-playwright"
_CFT = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
_NF = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW

_lock = threading.Lock()
_state: dict = {"installing": False, "progress": 0, "error": "", "started": 0.0}


def _platform() -> str:
    m = platform.machine().lower()
    if sys.platform == "win32":
        return "win64" if m in ("amd64", "x86_64", "arm64") else "win32"
    if sys.platform == "darwin":
        return "mac-arm64" if m in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def local_candidates() -> list[str]:
    """Every place a Studio-fetched or Playwright-fetched Chromium can be, newest first.

    The web tools' `scrapling install` pulls Playwright's Chromium into the data dir; reusing it
    means a PC that has the web fetch gets a forge browser with no second download."""
    out: list[str] = []
    try:
        if _DIR.is_dir():
            for d in sorted(_DIR.glob("chrome-*"), reverse=True):
                out += [str(d / "chrome.exe"),
                        str(d / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"),
                        str(d / "chrome")]
        if _PW.is_dir():
            for d in sorted(_PW.glob("chromium-*"), reverse=True):
                out += [str(d / "chrome-win" / "chrome.exe"),
                        str(d / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"),
                        str(d / "chrome-linux" / "chrome")]
    except OSError:
        pass
    return out


def status() -> dict:
    with _lock:
        s = dict(_state)
    try:
        from .workspace import _find_chrome
        s["exe"] = _find_chrome()
    except Exception:
        s["exe"] = ""
    return s


def ensure_installed(block: bool = False, timeout: float = 1800.0) -> dict:
    """Make sure some Chromium is on this machine; download one if not. Safe to call repeatedly."""
    try:
        from .workspace import _find_chrome
        if _find_chrome():
            return status()
    except Exception:
        pass
    with _lock:
        if _state["installing"]:
            if not block:
                return status()
        else:
            _state.update(installing=True, progress=0, error="", started=time.time())
            threading.Thread(target=_work, name="chrome-install", daemon=True).start()
    if block:
        t0 = time.time()
        while time.time() - t0 < timeout:
            with _lock:
                if not _state["installing"]:
                    break
            time.sleep(1.0)
    return status()


def _work() -> None:
    try:
        plat = _platform()
        with urllib.request.urlopen(_CFT, timeout=60) as r:
            meta = json.loads(r.read().decode("utf-8"))
        downloads = meta["channels"]["Stable"]["downloads"]["chrome"]
        url = next(d["url"] for d in downloads if d.get("platform") == plat)
        _DIR.mkdir(parents=True, exist_ok=True)
        part = _DIR / "chrome.zip.part"
        with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    with _lock:
                        _state["progress"] = int(done * 95 / total)
        with zipfile.ZipFile(part) as z:
            z.extractall(_DIR)
        part.unlink(missing_ok=True)
        # Zip files carry no execute bit; give it back on the platforms that need one.
        if os.name != "nt":
            for c in local_candidates():
                p = Path(c)
                if p.is_file():
                    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        exe = next((c for c in local_candidates() if Path(c).is_file()), "")
        if not exe:
            raise RuntimeError("the archive did not contain a browser executable")
        # Prove it runs the way it will be used: headless, serving the debugging protocol. Chrome
        # writes DevToolsActivePort into the profile the moment it is ready; that file is the proof.
        # (`--version` is not a proof on Windows, where it starts a browser that never returns, and
        # `--dump-dom` can hang under a sandbox complaint about the folder it sits in.)
        import tempfile
        prof = Path(tempfile.mkdtemp(prefix="studio-chrome-check-"))
        proc = subprocess.Popen([exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={prof}",
                                 "--no-first-run", "--no-default-browser-check", "about:blank"],
                                creationflags=_NF, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ready = False
            for _ in range(120):
                if (prof / "DevToolsActivePort").is_file():
                    ready = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.5)
        finally:
            try:
                proc.kill()
            except Exception:
                pass
        if not ready:
            raise RuntimeError("the downloaded browser started but never served the debugging protocol")
        with _lock:
            _state["progress"] = 100
    except Exception as e:                           # noqa: BLE001 — recorded, never raised
        with _lock:
            _state["error"] = str(e)[:300]
    finally:
        with _lock:
            _state["installing"] = False

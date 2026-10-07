"""Read a page that blocks you, and search the web without an API key.

WHY THIS EXISTS. An agent doing research kept hitting pages it could not read. Neither route
available to it worked: the model's own WebFetch is refused by any site that blocks datacentre
traffic, and the Studio's headless browser is plain Playwright with no evasion at all, which
every bot wall recognises on sight. So the research was quietly built out of whatever happened
not to be blocked.

Measured here against real sites before any of this was written:

    plain urllib               g2.com 403   crunchbase.com 403
    scrapling Fetcher          g2.com 403   crunchbase.com 403
    scrapling StealthyFetcher  g2.com 403   crunchbase.com 403   ← got the challenge page
    + solve_cloudflare         demo   200   crunchbase.com 200   ← 15,368 characters of real page

Search is the same wall from the other side: html.duckduckgo.com answers a scraper with a CAPTCHA
("select all squares containing a duck") and mojeek with "your network appears to be sending
automated queries". Getting over that wall is exactly what this module does, so search comes free
with the fetch — no key, no account, no quota.

SHAPE. Scrapling lives in its OWN venv under data/tools with its own browsers, and is driven as a
subprocess — the same arrangement graphify and the headless browser already use, so a bad release
there can never take the backend with it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import pip_attempts, settings, venv_has

_NF = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DATA = Path(__file__).resolve().parent.parent.parent / "data"
_VENV = _DATA / "tools" / "scrapling-venv"
# Where its browsers go - and where the worker must look. The install put Chromium here while the
# worker ran with no PLAYWRIGHT_BROWSERS_PATH at all, so it looked in %LOCALAPPDATA%\ms-playwright:
# on this PC an older copy happened to be there, on a new PC nothing is, and every stealth fetch
# failed with "Executable doesn't exist".
_BROWSERS = _DATA / "tools" / "ms-playwright"
_WORKER = Path(__file__).resolve().parent / "tools" / "web_worker.py"

# Install state, read by the Settings pane the same way graphify's is.
_INSTALL: dict = {"installing": False, "error": ""}
_lock = threading.Lock()


def _venv_py() -> Path:
    return _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _py() -> Optional[str]:
    """The venv's interpreter - only once scrapling is really in it. See `venv_has`."""
    p = _venv_py()
    return str(p) if p.exists() and venv_has(_VENV, "scrapling") else None


def available() -> bool:
    return _py() is not None


def _needed_browsers() -> list:
    """The browser folders the stealth tier needs, read from patchright's own browsers.json.

    ANY Chromium was not enough. The install pulled Playwright's build (chromium-1228) while the
    stealth tier runs on patchright, which wants its own (chromium-1234) - it worked on this PC
    only because an older install had left that build in ms-playwright under %LOCALAPPDATA%."""
    try:
        sites = [_VENV / "Lib" / "site-packages"] + sorted((_VENV / "lib").glob("python3*/site-packages"))
        for sp in sites:
            f = sp / "patchright" / "driver" / "package" / "browsers.json"
            if f.is_file():
                data = json.loads(f.read_text(encoding="utf-8"))
                return ["%s-%s" % (b["name"].replace("-", "_"), b["revision"])
                        for b in data.get("browsers", [])
                        if b.get("installByDefault") and b.get("name") in ("chromium", "chromium-headless-shell")]
    except (OSError, ValueError, KeyError):
        pass
    return []


def browsers_ok() -> bool:
    """Are the browsers the stealth tier needs where the worker looks? Without them the plain fetch
    still works and the stealth fetch - the one that gets past a bot wall - does not."""
    try:
        need = _needed_browsers()
        if need:
            return all((_BROWSERS / d / "INSTALLATION_COMPLETE").is_file() for d in need)
        return any((d / "INSTALLATION_COMPLETE").is_file() for d in _BROWSERS.glob("chromium-*"))
    except OSError:
        return False


def _env() -> dict:
    return dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(_BROWSERS))


def status() -> dict:
    return {"available": available(), "browsers": browsers_ok(),
            "installing": _INSTALL["installing"], "error": _INSTALL["error"],
            "venv": str(_VENV), "browsers_dir": str(_BROWSERS)}


def ensure_installed(block: bool = False, timeout: float = 1800.0) -> dict:
    """Create the venv, install scrapling, and pull its browser. Safe to call repeatedly.

    Each half is checked on its own: a PC that got the packages but lost its connection before
    the browser came down gets only the browser on the next call, not the whole install again.
    Never raises: a failure is recorded so the UI can show it, rather than the feature silently
    doing nothing - which is how a missing install reads to somebody who just wanted a web page.
    """
    with _lock:
        if (available() and browsers_ok()) or _INSTALL["installing"]:
            busy = _INSTALL["installing"]
            start = False
        else:
            _INSTALL["installing"] = True
            _INSTALL["error"] = ""
            busy = start = True
    if not start:
        if busy and block:
            end = time.time() + timeout
            while _INSTALL["installing"] and time.time() < end:
                time.sleep(1.0)
        return status()

    def _work() -> None:
        try:
            if not available():
                _VENV.parent.mkdir(parents=True, exist_ok=True)
                if not _venv_py().exists():
                    subprocess.run([sys.executable, "-m", "venv", str(_VENV)],
                                   timeout=300, creationflags=_NF, capture_output=True)
                py = str(_venv_py())
                if not Path(py).exists():
                    raise RuntimeError("could not create the scrapling venv")
                r = None
                for flags in pip_attempts():            # the bundled wheels first, then PyPI
                    r = subprocess.run([py, "-m", "pip", "install", "--quiet", *flags,
                                        "scrapling[fetchers]"],
                                       timeout=1800, creationflags=_NF, capture_output=True, text=True)
                    if r.returncode == 0:
                        break
                chk = subprocess.run([py, "-c", "from scrapling.fetchers import StealthyFetcher"],
                                     timeout=120, creationflags=_NF, capture_output=True, text=True)
                if chk.returncode != 0:
                    raise RuntimeError(((r.stderr if r else "") or chk.stderr or "").strip()[-400:]
                                       or "install failed")
            py = _py()
            if py and not browsers_ok():
                # The browser the stealth tier runs, into the folder the worker reads: patchright's
                # own build (Chromium plus its headless shell), not Playwright's older one.
                last = subprocess.run([py, "-m", "patchright", "install", "chromium"], timeout=1800,
                                      creationflags=_NF, capture_output=True, text=True, env=_env())
                if not browsers_ok():
                    raise RuntimeError("the Chromium download failed: " + ((last.stderr or last.stdout or "")
                                       .strip()[-300:] if last else "no output"))
        except Exception as e:                      # noqa: BLE001 - recorded, never raised
            _INSTALL["error"] = str(e)[:400]
        finally:
            _INSTALL["installing"] = False

    t = threading.Thread(target=_work, name="scrapling-install", daemon=True)
    t.start()
    if block:
        t.join(timeout=timeout)
    return status()


def _run(req: dict, timeout: float) -> dict:
    py = _py()
    if not py:
        return {"ok": False, "error": "the web tool is not installed yet — turn on cc_web_tools "
                                      "in Settings, or wait for the install to finish"}
    try:
        p = subprocess.run([py, str(_WORKER)], input=json.dumps(req), text=True,
                           capture_output=True, timeout=timeout, creationflags=_NF,
                           encoding="utf-8", errors="replace", env=_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out after %ds" % int(timeout)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    out = (p.stdout or "").strip()
    if not out:
        return {"ok": False, "error": (p.stderr or "the web tool returned nothing").strip()[-300:]}
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"ok": False, "error": "unreadable answer: " + out[:200]}


def fetch(url: str, mode: str = "auto", chars: int = 20000, timeout: float = 120.0) -> dict:
    """One page, as readable text. `mode` is auto | http | stealth.

    `chars` is a TOKEN budget, not a display limit. A long page is 100k characters, which is
    ~25k tokens landing in an agent's context for one read — so the default trims hard and says
    it did. An agent that genuinely needs the rest asks for more.
    """
    if not (url or "").strip().startswith(("http://", "https://")):
        return {"ok": False, "error": "give an http(s) URL"}
    t0 = time.time()
    res = _run({"op": "fetch", "url": url, "mode": mode, "timeout": int(min(timeout, 90))},
               timeout=timeout)
    text = res.get("text") or ""
    if chars and len(text) > chars:
        res["text"] = text[:chars]
        res["truncated_at"] = chars
        res["total_chars"] = len(text)
    res["seconds"] = round(time.time() - t0, 1)
    res["url"] = url
    return res


def search(query: str, n: int = 8, timeout: float = 120.0) -> dict:
    """Web search with no API key. Returns title, url and snippet per hit."""
    if not (query or "").strip():
        return {"ok": False, "error": "give a query"}
    t0 = time.time()
    res = _run({"op": "search", "query": query, "n": int(n), "timeout": int(min(timeout, 90))},
               timeout=timeout)
    res["seconds"] = round(time.time() - t0, 1)
    return res


def enabled() -> bool:
    """The Studio setting, defaulting ON once the tool is installed."""
    return bool(settings.get("cc_web_tools", True)) and available()

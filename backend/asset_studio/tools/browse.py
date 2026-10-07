#!/usr/bin/env python
"""Asset Studio headless browser — the ONE way Claude checks pages/games in any project.

ALWAYS HEADLESS (no window ever appears on the user's desktop — the Studio shows a status
pill instead). Real rendering via Playwright's chromium-headless-shell (the lightest
full-fidelity option: real canvas/WebGL/screenshots, none of Chrome's UI weight).

Standalone + self-bootstrapping: run it with ANY Python; on first use it creates its own
venv under the Studio's data/tools, installs playwright + the headless shell (~90 MB
one-time), then re-execs itself. No effect on any project's own dependencies.

Usage:
  python browse.py URL [--out shot.png] [--html out.html|-] [--full] [--size 1280x720]
                       [--wait MS] [--until CSS] [--js CODE] [--click CSS] [--console]
                       [--timeout MS]
Examples:
  python browse.py http://localhost:8080 --out game.png --console   # screenshot + console errors
  python browse.py http://localhost:5173 --js "window.score" --wait 2000
Prints a final line:  RESULT {"title","url","shot","html","console_errors",...}
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_DATA = _HERE.parents[3] / "data"          # <repo>/backend/asset_studio/tools/browse.py -> <repo>/data
_VENV = _DATA / "tools" / "browser-venv"
_BROWSERS = _DATA / "tools" / "ms-playwright"
_VPY = _VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _out(line: str) -> None:
    """Print without ever dying on the console encoding.

    A Windows console is cp1252 by default, so one arrow or em-dash on the page — in a
    title, a console message, or --js output — raised UnicodeEncodeError and threw away
    the whole result line, screenshot path included. The run had already succeeded."""
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write(line.encode(enc, "replace") + b"\n")
        sys.stdout.flush()


def _ensure_ready() -> None:
    """Create the browser venv and install Chromium if missing. Idempotent."""
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(_BROWSERS)}
    if not _VPY.exists():
        print("[browse] one-time setup: creating browser env…", flush=True)
        _VENV.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(_VENV)], check=True)
        subprocess.run([str(_VPY), "-m", "pip", "install", "--quiet", "--prefer-binary", "playwright"],
                       check=True, env=env)
    if not any(_BROWSERS.glob("chromium-*")):
        print("[browse] one-time setup: downloading headless Chromium…", flush=True)
        subprocess.run([str(_VPY), "-m", "playwright", "install", "chromium"],
                       check=True, env=env)
        print("[browse] setup done.", flush=True)


def _lower_priority() -> None:
    """Drop every browser process we just spawned to BELOW_NORMAL priority (Windows), so a
    heavy WebGL page can't steal cycles from the user's game/work. Best-effort, never fatal."""
    if os.name != "nt":
        return
    try:
        import ctypes
        import ctypes.wintypes as wt
        BELOW_NORMAL, PROCESS_SET_INFORMATION = 0x00004000, 0x0200
        TH32CS_SNAPPROCESS, k32 = 0x00000002, ctypes.windll.kernel32

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                        ("szExeFile", ctypes.c_char * 260)]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        e = PE32(); e.dwSize = ctypes.sizeof(PE32)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            name = e.szExeFile.decode("ascii", "ignore").lower()
            if name.startswith("chrome"):            # chrome.exe / chrome-headless-shell.exe
                h = k32.OpenProcess(PROCESS_SET_INFORMATION, False, e.th32ProcessID)
                if h:
                    k32.SetPriorityClass(h, BELOW_NORMAL)
                    k32.CloseHandle(h)
            ok = k32.Process32Next(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
    except Exception:
        pass


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Asset Studio headless browser (always headless)")
    ap.add_argument("url")
    ap.add_argument("--out", help="screenshot PNG path")
    ap.add_argument("--html", help="dump rendered HTML to a file, or - for stdout")
    ap.add_argument("--full", action="store_true", help="full-page screenshot")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--wait", type=int, default=0, help="extra ms to wait after load")
    ap.add_argument("--until", help="CSS selector to wait for")
    ap.add_argument("--js", help="JS to evaluate; result printed")
    ap.add_argument("--click", help="CSS selector to click before capture")
    ap.add_argument("--console", action="store_true", help="print ALL console messages (errors always shown)")
    ap.add_argument("--timeout", type=int, default=30000)
    ap.add_argument("--no-gpu", action="store_true",
                    help="force software rendering (deterministic pixels, but a WebGL page then burns ALL cores)")
    ap.add_argument("--normal-priority", action="store_true",
                    help="don't de-prioritize the browser (default: below-normal so it never fights your foreground work)")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    w, h = (int(t) for t in a.size.lower().split("x"))
    logs: list[str] = []
    errors: list[str] = []
    out: dict = {"url": a.url}

    with sync_playwright() as pw:
        # GPU BY DEFAULT. The plain headless shell has no GPU, so WebGL/canvas falls back to
        # SwiftShader = CPU rasterization across every core (measured: 76-89% of a 32-core
        # machine on one Three.js page). The full-Chromium "new headless" channel can use the
        # real GPU (measured 53% and rendering on the actual RTX card). Falls back silently
        # if that build isn't present.
        browser = None
        if not a.no_gpu:
            try:
                browser = pw.chromium.launch(headless=True, channel="chromium",
                                             args=["--use-angle=d3d11", "--enable-gpu-rasterization"])
                out["renderer"] = "gpu"
            except Exception:
                browser = None
        if browser is None:
            browser = pw.chromium.launch(headless=True)   # headless is NON-NEGOTIABLE here
            out.setdefault("renderer", "software")
        if not a.normal_priority:
            _lower_priority()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.on("console", lambda m: (logs.append(f"[{m.type}] {m.text}"),
                                      errors.append(m.text) if m.type == "error" else None))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.url, timeout=a.timeout, wait_until="load")
        if a.until:
            page.wait_for_selector(a.until, timeout=a.timeout)
        if a.click:
            page.click(a.click, timeout=a.timeout)
        if a.wait:
            page.wait_for_timeout(a.wait)
        if a.js:
            try:
                out["js_result"] = page.evaluate(a.js)
            except Exception as e:
                out["js_error"] = str(e)[:300]
        out["title"] = page.title()
        out["final_url"] = page.url
        if a.out:
            shot = Path(a.out).resolve()
            shot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot), full_page=a.full)
            out["shot"] = str(shot)
        if a.html:
            html = page.content()
            if a.html == "-":
                print(html)
            else:
                hp = Path(a.html).resolve()
                hp.parent.mkdir(parents=True, exist_ok=True)
                hp.write_text(html, encoding="utf-8")
                out["html"] = str(hp)
        browser.close()

    if a.console and logs:
        _out("--- console ---")
        for ln in logs[:200]:
            _out("  " + ln[:300])
    out["console_errors"] = errors[:50]
    _out("RESULT " + json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_BROWSERS)
    # must run under OUR venv (a system/project playwright would use the wrong browser
    # cache) — bootstrap if needed, then relaunch inside the venv. subprocess, not execv:
    # Windows execv mangles paths with spaces.
    if Path(sys.executable).resolve() != _VPY.resolve():
        _ensure_ready()
        r = subprocess.run([str(_VPY), str(_HERE), *sys.argv[1:]])
        sys.exit(r.returncode)
    _ensure_ready()
    sys.exit(main())

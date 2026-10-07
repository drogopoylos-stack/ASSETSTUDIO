"""Sweep stray automation/AI-spawned windows off the main screen (Windows only).

The Studio's browse tool is truly headless, but project scripts and tools started by a
Claude session can still pop real windows onto the user's main monitor (the blank grey
box). Two layers keep the main screen clean:

1. INSTANT: a WinEvent hook fires the moment ANY top-level window is shown — sweepable
   windows are moved within ~0.1s, so even short-lived ones barely flash.
2. BACKUP: a 2s poll catches anything the hook missed (e.g. windows shown while the
   backend was restarting).

"Sweepable" =
  - a browser process carrying automation flags (--enable-automation / --remote-debugging
    / an ms-playwright path) — a user's normal browser never has these; or
  - any window whose process carries our ASSET_STUDIO_CC=1 marker (i.e. it was spawned by
    a Studio Claude session) and looks like a browser/webview/electron.

Windows are moved ONCE to the LAST monitor (screen 3 on a triple setup; minimized on a
single monitor) and each sweep is LOGGED with the process identity — so if the grey box
ever reappears, the backend log says exactly what it was. Position changes don't affect
Playwright/Puppeteer. Gated on the ``sweep_browser_windows`` setting (default on).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

from .config import settings

_user32 = ctypes.windll.user32 if os.name == "nt" else None
_started = False
_moved: set[int] = set()          # hwnds already swept (don't fight the user dragging one back)

SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
SW_MINIMIZE = 6
EVENT_OBJECT_SHOW = 0x8002
WINEVENT_OUTOFCONTEXT = 0x0000
OBJID_WINDOW = 0

_BROWSERISH = ("chrome", "chromium", "msedge", "firefox", "headless", "electron",
               "msedgewebview2", "webview")


def _monitors() -> list[tuple[int, int, int, int]]:
    out: list[tuple[int, int, int, int]] = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC,
                                         ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(_hmon, _hdc, rect, _lp):
        r = rect.contents
        out.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    _user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(cb), 0)
    out.sort(key=lambda m: m[0])
    return out


_REASON_SEEN: dict = {}        # (pid, when it started) -> the verdict


def _started_at(pid: int) -> float:
    """When this process started. Windows re-uses process ids, so the id alone is not a name."""
    try:
        import psutil
        return float(psutil.Process(pid).create_time())
    except Exception:
        return 0.0


def _sweep_reason(pid: int) -> str:
    """Why this window should be swept — empty string = leave it alone.

    Remembered per process, keyed on its id AND its start time. The verdict reads the process name,
    its command line and, for a browser, its environment block — none of which can change while
    that process lives, and the environment of another process is among the most expensive things
    to read here. This ran for every visible window every two seconds: 4.9% of the whole backend's
    CPU, for an answer that was the same every time.
    """
    from . import perf
    key = (pid, _started_at(pid))
    if perf.fast():
        hit = _REASON_SEEN.get(key)
        if hit is not None:
            return hit
    reason = _sweep_reason_build(pid)
    if len(_REASON_SEEN) > 512:
        _REASON_SEEN.clear()
    _REASON_SEEN[key] = reason
    return reason


def _sweep_reason_build(pid: int) -> str:
    try:
        import psutil
        p = psutil.Process(pid)
        name = p.name().lower()
        cl = " ".join(p.cmdline()).lower()
        if name.startswith(("chrome", "chromium", "msedge", "firefox")) and (
                "--enable-automation" in cl or "--remote-debugging" in cl or "ms-playwright" in cl):
            return f"automation browser ({name})"
        if name.startswith(_BROWSERISH):
            try:
                if p.environ().get("ASSET_STUDIO_CC") == "1":
                    return f"claude-spawned {name}"
            except Exception:
                pass
        return ""
    except Exception:
        return ""


def _sweep_hwnd(hwnd: int, reason: str, pid: int) -> None:
    mons = _monitors()
    target = mons[2] if len(mons) >= 3 else (mons[-1] if len(mons) > 1 else None)
    try:
        if target is not None:
            _user32.SetWindowPos(hwnd, 0, target[0] + 60, target[1] + 60, 0, 0,
                                 SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            where = f"monitor {len(mons) if len(mons) < 3 else 3}"
        else:
            _user32.ShowWindow(hwnd, SW_MINIMIZE)
            where = "minimized"
        _moved.add(hwnd)
        try:
            import psutil
            cl = " ".join(psutil.Process(pid).cmdline())[:110]
        except Exception:
            cl = ""
        print(f"[sweeper] {reason} pid={pid} -> {where}  {cl}", flush=True)   # piped stdout buffers without flush
    except Exception:
        pass


def _maybe_sweep(hwnd: int) -> int:
    if hwnd in _moved or not _user32.IsWindowVisible(hwnd):
        return 0
    r = wt.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    if r.right - r.left < 80 or r.bottom - r.top < 60:   # tooltips/menus — not real windows
        return 0
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    reason = _sweep_reason(pid.value)
    if not reason:
        return 0
    _sweep_hwnd(hwnd, reason, pid.value)
    return 1


def _sweep_once() -> int:
    swept = 0
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)

    def cb(hwnd, _lp):
        nonlocal swept
        try:
            swept += _maybe_sweep(hwnd)
        except Exception:
            pass
        return 1

    _user32.EnumWindows(EnumWindowsProc(cb), 0)
    return swept


_WinEventProc = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND,
                                   ctypes.c_long, ctypes.c_long, wt.DWORD, wt.DWORD)
_hook_cb = None    # keep a reference — GC'ing the callback crashes the hook


def _hook_thread() -> None:
    """INSTANT layer: EVENT_OBJECT_SHOW fires for every newly shown window — sweepable
    ones move within ~0.1s of appearing, so even a short-lived grey box barely flashes."""
    global _hook_cb

    def cb(_hook, _event, hwnd, id_object, _id_child, _tid, _time):
        try:
            if id_object == OBJID_WINDOW and hwnd not in _moved \
                    and settings.get("sweep_browser_windows", True):
                # Two attempts: an INSTANT one (~80ms — moving during Chrome's startup can
                # leave the window never-visible, which is FINE for automation: the user
                # never sees it), and a 1s follow-up for windows that materialize on the
                # main screen anyway. _maybe_sweep is idempotent per hwnd.
                threading.Timer(0.08, _maybe_sweep, args=(hwnd,)).start()
                threading.Timer(1.0, _maybe_sweep, args=(hwnd,)).start()
        except Exception:
            pass

    _hook_cb = _WinEventProc(cb)
    hook = _user32.SetWinEventHook(EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, 0, _hook_cb,
                                   0, 0, WINEVENT_OUTOFCONTEXT)
    if not hook:
        return
    msg = wt.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:   # required message pump
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))


def _poll_loop() -> None:
    while True:
        try:
            time.sleep(2.0)
            if settings.get("sweep_browser_windows", True):
                _sweep_once()
        except Exception:
            pass


def start() -> None:
    """Idempotent; no-op off Windows."""
    global _started
    if _started or os.name != "nt" or _user32 is None:
        return
    _started = True
    threading.Thread(target=_hook_thread, daemon=True).start()
    threading.Thread(target=_poll_loop, daemon=True).start()

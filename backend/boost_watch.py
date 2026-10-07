"""A black box for a session that might freeze the machine.

WHY THIS EXISTS. When the desktop stops answering, the interesting evidence is destroyed by the
very act of asking for it: you cannot open Task Manager on a frozen PC, and a sampling tool that
lives inside the process it watches dies with it. So this runs DETACHED, writes one JSON line
every few seconds to a file that is flushed immediately, and keeps writing through whatever
happens to the Studio. After a freeze, the last line before the gap IS the answer.

WHAT IT RECORDS, and why each field earns its place:
  * commit charge vs commit LIMIT — physical RAM is not the wall. When commit reaches the limit the
    whole desktop stops, and this PC's limit is only ~2 GB above its RAM.
  * every process above a threshold, by working set — so the thing that ate the machine is named,
    not guessed.
  * the backend's own answer to a 4-second request — the Studio being slow to reply is the freeze
    seen from the only side that matters, and a timeout here is the moment it started.
  * graph.json's size and mtime — a code-graph rebuild rewrites a ~45 MB file and can land in the
    middle of a BOOST prefetch.

Usage: boost_watch.py [output.jsonl] [--minutes N]
"""
import ctypes
import json
import os
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

BACKEND = "http://127.0.0.1:8777"
GRAPH = Path(r"D:\Asset Studio\graphify-out\graph.json")
INTERVAL = 5.0
PROBE_TIMEOUT = 4.0


# --- OS commit charge --------------------------------------------------------------------------
class _MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MEMSTATUS)]
_k32.GlobalMemoryStatusEx.restype = wintypes.BOOL
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_psapi = ctypes.WinDLL("psapi", use_last_error=True)


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _PE32(ctypes.Structure):
    # ULONG_PTR MUST be size_t on 64-bit, or every field after it shifts and the names come out as
    # mojibake — a process list you cannot read is not evidence.
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]


_k32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
_k32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
_k32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
                                 ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME)]
_k32.GetProcessTimes.restype = wintypes.BOOL

TH32CS_SNAPPROCESS = 0x2
PROC_QUERY = 0x0410               # QUERY_INFORMATION | VM_READ — what GetProcessMemoryInfo wants
INVALID = ctypes.c_void_p(-1).value


def mem_status():
    m = _MEMSTATUS()
    m.dwLength = ctypes.sizeof(m)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
        raise OSError("GlobalMemoryStatusEx failed")
    return m


def heavy_processes(limit=8):
    """The heaviest processes — PLUS the Studio's own, whatever their size.

    A plain top-N by memory loses exactly the thing this watch exists for: the backend idles around
    180 MB, so on a PC running OBS, Chrome and Discord it never reaches the top eight, and a BOOST
    test whose log cannot show the backend's own growth is a log that answers nothing. Node,
    ComfyUI and Blender come along for the same reason — they are the local generators.
    """
    always = ("python", "pythonw", "comfyui", "blender", "node")
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID:
        return []
    out = []
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(e)
        ok = _k32.Process32First(snap, ctypes.byref(e))
        while ok:
            pid = int(e.th32ProcessID)
            name = e.szExeFile.decode("mbcs", "replace")
            base = name.lower().removesuffix(".exe")
            if pid:
                h = _k32.OpenProcess(PROC_QUERY, False, pid)
                if h:
                    try:
                        c = _PMC()
                        c.cb = ctypes.sizeof(c)
                        if _psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                            ws = int(c.WorkingSetSize)
                            # 20 MB floor: a 4 MB one-thread stub is noise, not a suspect.
                            if ws >= 20e6:
                                cpu = None
                                a, b, cc, d = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
                                if _k32.GetProcessTimes(h, ctypes.byref(a), ctypes.byref(b),
                                                        ctypes.byref(cc), ctypes.byref(d)):
                                    # THE LAST TWO ARE THE CPU ONES. GetProcessTimes is
                                    # (creation, exit, KERNEL, USER) — summing the first two, as this
                                    # did, reports a timestamp since 1601 as if it were CPU seconds.
                                    # 1.3e10 "seconds" of CPU is not a busy process; it is a bug that
                                    # would have made this log useless exactly when it was needed.
                                    cpu = round(((cc.dwHighDateTime << 32 | cc.dwLowDateTime)
                                                 + (d.dwHighDateTime << 32 | d.dwLowDateTime)) / 1e7, 1)
                                out.append({"pid": pid, "name": name,
                                            "ours": base in always, "mb": round(ws / 1e6),
                                            "threads": int(e.cntThreads),
                                            "parent": int(e.th32ParentProcessID), "cpu_s": cpu})
                    finally:
                        _k32.CloseHandle(h)
            ok = _k32.Process32Next(snap, ctypes.byref(e))
    finally:
        _k32.CloseHandle(snap)
    out.sort(key=lambda p: -p["mb"])
    keep = out[:limit]
    have = {p["pid"] for p in keep}
    keep += [p for p in out if p["ours"] and p["pid"] not in have]     # the Studio never gets cut
    return keep


def probe():
    """Ask the backend for its settings. A timeout here IS the freeze, timestamped."""
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(BACKEND + "/api/settings", timeout=PROBE_TIMEOUT) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        ms = round((time.perf_counter() - t) * 1000)
        return {"ok": True, "ms": ms, "boost": bool(body.get("boost")),
                "boost_prefetch": bool(body.get("boost_prefetch")),
                "graphify": bool(body.get("cc_graphify")),
                "min_free_ram_gb": body.get("min_free_ram_gb")}
    except Exception as e:
        return {"ok": False, "ms": round((time.perf_counter() - t) * 1000),
                "err": "%s: %s" % (type(e).__name__, e)}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    minutes = 0.0
    for a in sys.argv[1:]:
        if a.startswith("--minutes"):
            minutes = float(a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1])
    out = Path(args[0]) if args else Path(r"D:\Asset Studio\data\logs") / \
        ("boost-watch-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S"))
    out.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60 if minutes else None

    def line(row):
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())          # a freeze must not take the last line with it

    line({"event": "start", "ts": time.time(), "pid": os.getpid(),
          "note": "black box for a BOOST test; one JSON line per %ss" % INTERVAL,
          "interval_s": INTERVAL, "floor_mb": 20})
    ticks = fails = slow = 0
    while True:
        try:
            m = mem_status()
            g = None
            try:
                st = GRAPH.stat()
                g = {"mb": round(st.st_size / 1e6), "mtime": int(st.st_mtime)}
            except OSError:
                g = {"missing": True}
            p = probe()
            ticks += 1
            if not p["ok"]:
                fails += 1
            elif p["ms"] > 2000:
                slow += 1
            line({"event": "tick", "ts": time.time(), "n": ticks,
                  "ram_total_gb": round(m.ullTotalPhys / 1e9, 1),
                  "ram_free_gb": round(m.ullAvailPhys / 1e9, 1),
                  "commit_used_gb": round((m.ullTotalPageFile - m.ullAvailPageFile) / 1e9, 1),
                  "commit_limit_gb": round(m.ullTotalPageFile / 1e9, 1),
                  "commit_pct": round(100.0 * (m.ullTotalPageFile - m.ullAvailPageFile)
                                      / max(1, m.ullTotalPageFile), 1),
                  "graph": g, "backend": p, "heavy": heavy_processes(),
                  "fails": fails, "slow": slow})
        except Exception as e:                       # never die: the log is the whole point
            line({"event": "error", "ts": time.time(), "err": "%s: %s" % (type(e).__name__, e)})
        if deadline and time.time() >= deadline:
            line({"event": "stop", "ts": time.time(), "ticks": ticks, "probe_fails": fails,
                  "probe_slow": slow})
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

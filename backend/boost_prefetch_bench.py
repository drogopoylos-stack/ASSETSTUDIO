"""What does ONE boost.prefetch() actually cost, on THIS repo's graph?

WHY THIS EXISTS. `_GRAPH_CACHE` in graphify_index.py holds a single parsed graph and re-parses it
whenever (path, mtime, size) changes. The comment above that cache says "graph.json is ~2.4 MB
here" — this repo's is ~43 MB, eighteen times the figure the design was costed against. The
prefetch runs SYNCHRONOUSLY on the send path (boost.prepare -> prefetch -> query, once per
symbol, up to _MAX_PREFETCH_SYMBOLS times), so a cache miss is not a background cost: it is time
the chat spend does not return, on a machine whose commit limit is ~33 GB with the page file.

So: measure it, instead of arguing about it. Cold load, warm query, and the worst case the design
allows — a rebuild landing between two symbol queries, which misses the cache EVERY time.
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = r"D:\Asset Studio"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from asset_studio import boost, graphify_index as gi  # noqa: E402


# --- memory of THIS process, in bytes ----------------------------------------------------------
class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


# ARGTYPES ARE NOT OPTIONAL HERE. Without them ctypes passes the process HANDLE as a 32-bit int,
# the handle is truncated, GetProcessMemoryInfo fails, and this function cheerfully returns 0 —
# a diagnostic that says "the process uses no memory" is worse than no diagnostic at all.
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = wintypes.HANDLE


def _mem():
    c = _PMC()
    c.cb = ctypes.sizeof(c)
    if not _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise OSError("GetProcessMemoryInfo failed: %d" % ctypes.get_last_error())
    return c.WorkingSetSize, c.PeakWorkingSetSize


def gb(n):
    return "%.2f GB" % (n / 1e9)


def mb(n):
    return "%.0f MB" % (n / 1e6)


graph = gi._graph_path(ROOT)
if not graph.is_file():
    print("no graph at %s — nothing to measure" % graph)
    raise SystemExit(0)
st = graph.stat()
print("graph.json: %s  %s  %s" % (mb(st.st_size), time.strftime("%H:%M:%S", time.localtime(st.st_mtime)),
                                  graph))
print("python: %s\n" % sys.executable)

ws0, peak0 = _mem()
print("this process before anything: ws=%s peak=%s\n" % (mb(ws0), mb(peak0)))


def cold_load():
    gi._GRAPH_CACHE["key"] = gi._GRAPH_CACHE["data"] = None
    t = time.perf_counter()
    g = gi._load(ROOT)
    return time.perf_counter() - t, (len(g["nodes"]) if g else 0)


# --- 1. one cold parse of the graph ------------------------------------------------------------
dt, nnodes = cold_load()
ws1, peak1 = _mem()
print("1) COLD _load()            %6.2f s   nodes=%s" % (dt, nnodes))
print("   process now             ws=%s  peak=%s  (+%s ws, +%s peak)\n"
      % (mb(ws1), mb(peak1), mb(ws1 - ws0), mb(peak1 - peak0)))

# --- 2. warm queries: the cache doing its job ---------------------------------------------------
gi._load(ROOT)                                     # make sure it is warm
# WHICH FILES IS THIS GRAPH ACTUALLY ABOUT? The skip list drops node_modules/.venv/site-packages
# by NAME, but the bundled interpreter at runtime/python/Lib is inside the workspace root and
# matches none of those — so if the graph is full of asyncio, the prefetch is answering symbols
# the user never wrote, and the file is 19x the size the design costed.
nodes = gi._load(ROOT)["nodes"]
buckets: dict[str, int] = {}
for n in nodes:
    sf = str(n.get("source_file") or "").replace("\\", "/").lower()
    if not sf:
        top = "(no file)"
    elif sf.startswith("runtime/python"):
        top = "runtime/python (bundled stdlib)"
    elif sf.startswith("data/"):
        top = "data/ (tools, vendored)"
    elif "site-packages" in sf or "/node_modules/" in sf:
        top = "site-packages / node_modules"
    elif sf.startswith("backend/") or sf.startswith("frontend/"):
        top = "the Studio's own code"
    else:
        top = sf.split("/")[0] + "/"
    buckets[top] = buckets.get(top, 0) + 1
print("   graph nodes by origin (total %d):" % len(nodes))
for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])[:8]:
    print("      %-32s %6d  %4.1f%%" % (k, v, 100.0 * v / max(1, len(nodes))))

# Symbols a message about THIS repo would name: our own code, not the bundled interpreter.
symbols, seen = [], set()
for n in nodes:
    lbl = str(n.get("label") or "").strip().rstrip("()")
    sf = str(n.get("source_file") or "").replace("\\", "/").lower()
    if not (lbl and 3 <= len(lbl) <= 60 and "_" in lbl and n.get("source_location")):
        continue
    if not sf.endswith(".py") or sf.startswith(("runtime/", "data/")):
        continue
    if lbl in seen:
        continue
    seen.add(lbl)
    symbols.append(lbl)
    if len(symbols) >= 6:
        break
print("2) symbols picked from OUR OWN code: %s" % ", ".join(symbols))

t = time.perf_counter()
for s in symbols:
    gi.query(ROOT, q=s, limit=3, scan=False)
warm_each = (time.perf_counter() - t) / max(1, len(symbols))
print("   WARM query             %6.3f s each  (x%d = %.2f s)\n"
      % (warm_each, len(symbols), warm_each * len(symbols)))

# --- 3. THE WORST CASE THE DESIGN ALLOWS: a rebuild between two symbols -------------------------
# graph.json is rewritten by a running build, so (mtime, size) changes and EVERY later query
# re-parses the whole file. This is not hypothetical: two rebuilds landed while this was written.
t = time.perf_counter()
for s in symbols:
    dt, _ = cold_load()
    gi.query(ROOT, q=s, limit=3, scan=False)
worst = time.perf_counter() - t
print("3) COLD query x%d          %6.2f s total   (%.2f s each)   <- rebuild lands between symbols"
      % (len(symbols), worst, worst / max(1, len(symbols))))

# --- 4. the call the send path actually makes ---------------------------------------------------
gi._GRAPH_CACHE["key"] = gi._GRAPH_CACHE["data"] = None
msg = "please look at " + ", ".join("`%s`" % s for s in symbols) + " and tell me what calls them"
t = time.perf_counter()
res = boost.prefetch(msg, ROOT)
full = time.perf_counter() - t
ws2, peak2 = _mem()
print("\n4) boost.prefetch() COLD   %6.2f s   lookups=%d  block=%d chars (~%d tokens)"
      % (full, res["lookups"], res["chars"], res["tokens"]))
print("   process after            ws=%s  peak=%s  (+%s ws, +%s peak vs start)"
      % (mb(ws2), mb(peak2), mb(ws2 - ws0), mb(peak2 - peak0)))

t = time.perf_counter()
res_warm = boost.prefetch(msg, ROOT)
print("   boost.prefetch() WARM   %6.2f s   lookups=%d" % (time.perf_counter() - t, res_warm["lookups"]))

print("\nVERDICT")
if full > 2.0:
    print("  A cold prefetch costs %.1f s of the SEND, before the model is even contacted." % full)
if worst > 5.0:
    print("  A rebuild landing mid-prefetch costs %.1f s. That is the freeze, and it is reachable"
          % worst)
    print("  whenever the graph is rebuilding while somebody presses send.")
print("  graph size the design comment assumes: ~2.4 MB. Actual: %s (%.0fx)."
      % (mb(st.st_size), st.st_size / 2.4e6))

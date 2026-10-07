"""Live machine telemetry for the persistent status bar:
CPU, RAM, disk, and per-GPU VRAM/util/temp via ``nvidia-smi``.

Everything degrades gracefully: no GPU, no psutil, or no nvidia-smi all return
sensible empty values instead of raising.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from functools import lru_cache

from . import perf
from .config import DATA_DIR
from .models import DiskStat, GPUStat, SystemStats

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


@lru_cache(maxsize=1)
def _nvidia_smi_path() -> str | None:
    return shutil.which("nvidia-smi")


# nvidia-smi can stall for seconds under GPU load; cache its result briefly so the 2s stats
# broadcaster + the /system endpoints + job preflight never pile up overlapping nvidia-smi calls.
_GPU_CACHE: dict = {"t": 0.0, "v": []}
_GPU_TTL = 1.5


def _gpu_stats() -> list[GPUStat]:
    now = time.monotonic()
    if _GPU_CACHE["t"] and (now - _GPU_CACHE["t"]) < _GPU_TTL:
        return _GPU_CACHE["v"]
    exe = _nvidia_smi_path()
    if not exe:
        _GPU_CACHE.update(t=now, v=[])
        return []
    query = "index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw"
    try:
        out = subprocess.run(
            [exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        # keep the last good reading on a transient stall instead of flickering to empty
        _GPU_CACHE["t"] = now
        return _GPU_CACHE["v"]
    gpus: list[GPUStat] = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue

        def _f(x: str) -> float:
            try:
                return float(x)
            except ValueError:
                return 0.0

        gpus.append(
            GPUStat(
                index=int(_f(parts[0])),
                name=parts[1],
                vram_total_mb=_f(parts[2]),
                vram_used_mb=_f(parts[3]),
                util_percent=_f(parts[4]),
                temperature_c=_f(parts[5]) or None,
                power_w=_f(parts[6]) or None,
            )
        )
    _GPU_CACHE.update(t=now, v=gpus)
    return gpus


_CACHE: dict = {"at": 0.0, "val": None}
_CACHE_TTL = 1.5      # the WS broadcaster ticks every 2s; the HTTP endpoint rides the same result
# The browser count on its own, for longer: a headless browser does not come and go every second,
# and finding one means a walk of every process on the machine.
_BROWSERS_TTL = perf.Ttl(10.0, limit=2)


def collect() -> SystemStats:
    """Cached briefly: the broadcaster and the /api/system/stats endpoint used to each pay the
    full cost, so a visible window did the work twice for numbers that change slowly."""
    now = time.time()
    if _CACHE["val"] is not None and (now - _CACHE["at"]) < _CACHE_TTL:
        return _CACHE["val"]
    val = _collect_uncached()
    _CACHE.update(at=now, val=val)
    return val


def _collect_uncached() -> SystemStats:
    s = SystemStats()
    if psutil:
        s.cpu_percent = psutil.cpu_percent(interval=None)
        s.cpu_cores = psutil.cpu_count(logical=True) or 0
        vm = psutil.virtual_memory()
        s.ram_total_gb = round(vm.total / 1e9, 2)
        s.ram_used_gb = round(vm.used / 1e9, 2)
        s.ram_percent = vm.percent
        try:
            du = psutil.disk_usage(str(DATA_DIR))
            s.disk = DiskStat(
                path=str(DATA_DIR),
                total_gb=round(du.total / 1e9, 1),
                used_gb=round(du.used / 1e9, 1),
                free_gb=round(du.free / 1e9, 1),
                percent=du.percent,
            )
        except Exception:
            pass
    s.gpus = _gpu_stats()
    s.headless_browsers = _headless_browsers()
    return s


def _headless_browsers() -> int:
    """How many automation browsers are running (Playwright/Puppeteer headless shells or
    --headless chromes). Surfaced as a status pill so the user KNOWS a browser is working
    instead of wondering about stray windows. Counts root processes only (browsers fork
    many children)."""
    if not psutil:
        return 0
    return _BROWSERS_TTL.get("n", _headless_browsers_build)


def _headless_browsers_build() -> int:
    n = 0
    try:
        browser_pids = set()
        roots: list[tuple[int, int]] = []
        # Ask for the command line ONLY for processes whose name already looks like a browser.
        # Requesting it for everything costs a per-process Windows API call: measured 1,258 ms
        # across 316 processes versus 25 ms without — and this runs every 2 seconds, so it kept a
        # worker thread busy most of the time for a number that barely changes.
        #
        # The PARENT id is now asked for the same few, for the same reason. `process_iter` fills
        # every field you name for every process on the machine, and ppid was in that list: about
        # 300 extra Windows calls per pass, measured at 8.3% of the whole backend's CPU. The name
        # comes free with the listing; the parent does not.
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = (p.info.get("name") or "").lower()
                if not (name.startswith(("chrome", "chromium", "msedge")) or "headless" in name):
                    continue
                if "headless" in name:
                    cl = ""                      # the name alone already identifies it
                else:
                    cl = " ".join(p.cmdline() or []).lower()
                # matches chrome-headless-shell.exe (Playwright), chrome --headless, ms-playwright installs
                if "--headless" not in cl and "headless" not in name and "ms-playwright" not in cl:
                    continue
                browser_pids.add(p.info["pid"])
                roots.append((p.info["pid"], p.ppid()))
            except Exception:
                continue
        n = sum(1 for _pid, ppid in roots if ppid not in browser_pids)
    except Exception:
        return 0
    return n


# warm psutil's cpu_percent baseline (first call always returns 0.0)
if psutil:
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass

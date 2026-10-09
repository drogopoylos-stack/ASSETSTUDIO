"""Load probe: does /api/health stay fast while the UI's own polling hammers the backend?

The desktop shell (frontend/electron/main.cjs) asks /api/health every 3 s with an 800 ms timeout and
KILLS the backend after ~6 s without an answer. So the number that decides whether a busy project
crashes the app is the health latency UNDER LOAD, not any endpoint's speed on its own.

This replays the UI's cadence for N panes working on one project — feed, live state, sending,
context, live-status, the rail — plus the occasional slow call (/api/providers), and samples
/api/health twice a second. Point it at a SECONDARY backend, never the one you work in:

  ASSET_STUDIO_SECONDARY=1 ASSET_STUDIO_PORT=8790 python -m asset_studio.main
  python load_probe.py --port 8790 --project d--UserFiles-Desktop-KAPOW-BROWLER --panes 2 --seconds 20
"""
import argparse
import statistics
import threading
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8790)
ap.add_argument("--project", required=True)
ap.add_argument("--panes", type=int, default=2)
ap.add_argument("--seconds", type=float, default=20)
a = ap.parse_args()
if a.port == 8777:
    raise SystemExit("refusing to load the backend you work in (8777): the shell would kill it")

BASE = f"http://127.0.0.1:{a.port}"
stop = time.time() + a.seconds
counts: dict = {}
lock = threading.Lock()


def get(path: str, timeout: float = 30.0) -> float:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            r.read()
    except Exception:
        return -1.0
    return time.perf_counter() - t0


def loop(path: str, every: float) -> None:
    while time.time() < stop:
        get(path)
        with lock:
            counts[path] = counts.get(path, 0) + 1
        time.sleep(every)


P = a.project
jobs = []
for pane in range(a.panes):
    feed = P if pane % 2 == 0 else "codex--" + P
    jobs += [(f"/api/mission/projects/{feed}/feed?limit=150", 0.3),     # feed while working
             (f"/api/mission/projects/{feed}/live", 0.2),               # live state while working
             (f"/api/mission/projects/{feed}/sending", 1.0),
             (f"/api/mission/projects/{feed}/context", 6.0),
             (f"/api/mission/projects/{feed}/subagents", 2.5)]
jobs += [("/api/mission/live-status", 3.0), ("/api/mission/agents", 2.0), ("/api/providers", 5.0),
         ("/api/mission/usage?provider=claude", 10.0)]
threads = [threading.Thread(target=loop, args=j, daemon=True) for j in jobs]
for t in threads:
    t.start()

samples = []
while time.time() < stop:
    samples.append(get("/api/health", timeout=10))
    time.sleep(0.5)
for t in threads:
    t.join(timeout=35)

ok = [s for s in samples if s >= 0]
over = sum(1 for s in samples if s < 0 or s > 0.8)
# the shell's rule: a 3 s poll, killed after ~6 s of failures = 3 consecutive misses of 800 ms
run = worst = 0
for s in samples[::6]:                      # every 6th half-second sample ~= the 3 s poll
    run = run + 1 if (s < 0 or s > 0.8) else 0
    worst = max(worst, run)
print(f"health samples {len(samples)}  p50 {statistics.median(ok)*1000:.0f} ms  "
      f"max {max(ok)*1000:.0f} ms  over 800 ms: {over}  "
      f"worst run of missed 3 s polls: {worst} (the shell kills at 3)")
print("requests served:", sum(counts.values()), " by path:",
      {k.split('?')[0].rsplit('/', 1)[-1]: v for k, v in counts.items()})

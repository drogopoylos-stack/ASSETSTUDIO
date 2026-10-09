"""Fill the worker pool with heavy requests, then time /api/health. Secondary backends only.

  python load_probe_saturate.py --port 8790 --project d--UserFiles-Desktop-KAPOW-BROWLER
"""
import argparse
import threading
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8790)
ap.add_argument("--project", required=True)
ap.add_argument("--n", type=int, default=60)
a = ap.parse_args()
if a.port == 8777:
    raise SystemExit("refusing to load the backend you work in (8777)")
BASE = f"http://127.0.0.1:{a.port}"


def get(path, timeout=60.0):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            r.read()
    except Exception:
        return -1.0
    return time.perf_counter() - t0


heavy = f"/api/mission/projects/{a.project}/feed?limit=2000"
ts = [threading.Thread(target=get, args=(heavy,), daemon=True) for _ in range(a.n)]
for t in ts:
    t.start()
time.sleep(0.3)
lat = []
end = time.time() + 8
while time.time() < end:
    lat.append(get("/api/health", timeout=10))
    time.sleep(0.4)
for t in ts:
    t.join(timeout=90)
ok = [x for x in lat if x >= 0] or [float("inf")]   # inf: not one probe answered
print(f"health under {a.n} heavy requests: max {max(ok)*1000:.0f} ms, "
      f"over 800 ms {sum(1 for x in lat if x < 0 or x > 0.8)}/{len(lat)}, "
      f"over 2500 ms {sum(1 for x in lat if x < 0 or x > 2.5)}/{len(lat)}")

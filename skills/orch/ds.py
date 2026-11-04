"""Send one message to the Studio DeepSeek harness, wait, print the reply. usage: ds.py <pid> <path> <model> <msgfile>"""
import json, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
pid, path, model, msgfile = sys.argv[1:5]
import re
if pid == 'auto':
    pid = re.sub(r'[^A-Za-z0-9]', '-', path)
B = f"http://127.0.0.1:8777/api/mission/projects/{pid}"
def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req, timeout=60))
msg = open(msgfile, encoding="utf-8").read()
t0 = time.time()
r = call(B + "/send", {"agent": "deepseek-harness", "model": model, "permission_mode": "full", "path": path,
                       "message": msg, "new_session": True})
if not r.get("ok", True):
    print("SEND FAILED", r); sys.exit(1)
FEED = B.replace(pid, "deepseek-harness--" + pid) + "/feed?limit=30"
time.sleep(3)
while True:
    f = call(FEED)
    if not f.get("working"):
        break
    if time.time() - t0 > 900:
        print("TIMEOUT"); break
    time.sleep(3)
dt = time.time() - t0
lines = f.get("lines") or []
last_user = max((i for i, l in enumerate(lines) if l.get("kind") == "user"), default=-1)
out = [l for l in lines[last_user + 1:] if l.get("kind") in ("assistant", "text", "answer")]
kinds = sorted({l.get("kind") for l in lines[last_user + 1:]})
print(f"[{dt:.1f}s] kinds={kinds}")
print(out[-1].get("text") if out else json.dumps(lines[last_user + 1:])[:1500])

"""Does the feature map describe the code, and does the checker catch a missing route?

FEATURES.md is built from the code by scripts/features/build.py and checked against a running
Studio by scripts/features/verify.py. A map that lists a route the code does not have, or misses
one it has, sends the agent who checks a new installation after the wrong thing. And the map is
committed by a hook and by GitHub Actions, so the same code must give the same bytes on every
machine, or the Action commits a change after every push.

No real backend here: the checker is pointed at a small HTTP server that serves part of a map.
"""
import ast
import contextlib
import io
import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "features"))

import build      # noqa: E402
import verify     # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s %s" % (name, extra))


print("\n-- the map is read from the code --")
d = build.collect()
md, js = build.render_md(d), build.render_json(d)
check("the same code gives the same bytes", build.render_md(build.collect()) == md
      and build.render_json(build.collect()) == js)
check("no path of this machine is in it", str(ROOT) not in md and str(ROOT).replace("\\", "/") not in js
      and not re.search(r"[A-Za-z]:\\\\", js))
routers = sorted(p.stem for p in (ROOT / "backend/asset_studio/routers").glob("*.py") if p.stem != "__init__")
tracked = set(build.source_files())
check("every route group in the code is in the map",
      all(r in d["areas"] for r in routers if "backend/asset_studio/routers/%s.py" % r in tracked),
      [r for r in routers if r not in d["areas"]])
# A second count of the routes, by a plain search, so the parser cannot drop one quietly.
plain = 0
for r in routers:
    rel = "backend/asset_studio/routers/%s.py" % r
    if rel in tracked:
        plain += len(re.findall(r"^@\w+\.(?:get|post|put|delete|patch|websocket)\(",
                                (ROOT / rel).read_text(encoding="utf-8"), re.M))
mapped = sum(len(a["endpoints"]) for k, a in d["areas"].items() if k != "app")
check("the parser finds every route a plain search finds", mapped == plain, (mapped, plain))
live = {e["path"] for e in d["areas"]["live"]["endpoints"]}
check("a known route is where it belongs", "/api/live/bench" in live and "/api/live/forge" in live)
check("the app's own routes are in it", any(e["path"] == "/api/health" for e in d["areas"]["app"]["endpoints"]))
check("the Studio Engine window is a page", any(p["id"] == "engine" and p["file"].endswith("pages/Engine.tsx")
                                                for p in d["pages"]))
check("each tab of the app is a page", {"workspace", "settings", "catalog"} <= {p["id"] for p in d["pages"]})
check("a test is matched to the part it tests",
      "live" in d["tests"]["backend"]["live_test.py"]["areas"]
      and "engine" in d["tests"]["frontend"]["test:edit"]["pages"])
check("the slow suite is named", "keeper_live_test.py" in d["slow_tests"])

print("\n-- the hand-written half --")
check("every feature names its parts, modules or tests",
      all(f["parts"] or f["modules"] or f["claims"] for f in d["features"]))
check("no feature names a part the code does not have", not d["stale"], d["stale"])
check("every part of the code belongs to a feature", not d["undescribed"], d["undescribed"])
smoke = [s for f in d["features"] for s in f["smoke"]]
check("smoke checks are plain reads with no parameter to fill",
      smoke and all(s.startswith("/api/") and "{" not in s and "<" not in s for s in smoke), smoke)
gets = {e["path"] for a in d["areas"].values() for e in a["endpoints"] if e["method"] == "GET"}
# A route group written but not committed yet: its routes are read from disk.
for part in d["pending"]:
    rel = "backend/asset_studio/routers/%s.py" % part
    gets |= {e["path"] for e in build.parse_routes(rel, build.read(rel))["endpoints"] if e["method"] == "GET"}
check("every smoke check is a GET route the code has", all(s.split("?")[0] in gets for s in smoke),
      [s for s in smoke if s.split("?")[0] not in gets])
sec = build.parse_checks("## A\nparts: x, y\nmodules: m\n\nText with `GET /api/a/b` and `POST /api/c`.\n")
check("a section's parts, modules and smoke checks are read",
      sec[0]["parts"] == ["x", "y"] and sec[0]["modules"] == ["m"] and sec[0]["smoke"] == ["/api/a/b"], sec)

print("\n-- the hook --")
check("the hook script tests a Python before it uses it", "sys.version_info" in build.SHIM)
check("the hook never stops a commit", build.SHIM.rstrip().endswith("exit 0") and "|| true" in build.SHIM)
ast.parse((ROOT / "scripts/features/build.py").read_text(encoding="utf-8"))
check("the workflow commits only the map, and skips CI on its own commit",
      "[skip ci]" in (ROOT / ".github/workflows/features.yml").read_text(encoding="utf-8"))

print("\n-- the checker against a backend that misses a route --")
fmap = {"areas": {"demo": {"file": "backend/asset_studio/routers/demo.py", "endpoints": [
            {"method": "GET", "path": "/api/demo/a", "function": "a", "summary": ""},
            {"method": "POST", "path": "/api/demo/b/{x}", "function": "b", "summary": ""},
            {"method": "WS", "path": "/api/demo/ws", "function": "w", "summary": ""}]}},
        "features": [{"id": "demo", "title": "Demo", "parts": ["demo"], "modules": [],
                      "smoke": ["/api/demo/a"], "tests": []}],
        "tests": {"backend": {}, "frontend": {}}, "slow_tests": []}
served = {"/api/demo/a": {"get": {}}}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body, code = b"", 404
        if self.path == "/api/health":
            body, code = b'{"ok": true}', 200
        elif self.path == "/openapi.json":
            body, code = json.dumps({"paths": served}).encode(), 200
        elif self.path in ("/", "/engine"):
            body, code = b'<div id="root"></div>', 200
        elif self.path == "/api/demo/a":
            body, code = b'{"ok": true}', 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
tmp = Path(tempfile.mkdtemp()) / "features.json"
tmp.write_text(json.dumps(fmap), encoding="utf-8")
verify.MAP = tmp
verify.check_programs = lambda base: [{"name": "python", "need": "required", "ok": True, "where": "x", "note": ""}]


def run(*argv):
    out = io.StringIO()
    old = sys.argv
    sys.argv = ["verify.py", "--base", base] + list(argv)
    try:
        with contextlib.redirect_stdout(out):
            code = verify.main()
    finally:
        sys.argv = old
    return code, out.getvalue()


code, text = run()
check("a route the map has and the backend does not serve is a failure",
      code == 1 and "POST /api/demo/b/{x}" in text and "NOT served" in text, text[-600:])
check("a websocket is not looked for in the HTTP routes", "/api/demo/ws" not in text)
check("the smoke check passes", "| 1 | Demo | missing 1 | 1/1 |" in text, text[-400:])
served["/api/demo/b/{x}"] = {"post": {}}
code, text = run()
check("with every route served, it passes", code == 0 and "Every route in the map is served" in text, text[-400:])
srv.shutdown()

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

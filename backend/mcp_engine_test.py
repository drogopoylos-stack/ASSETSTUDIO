"""The Studio engine over MCP: the catalog, the route, the stdio server and the session flag.

    python mcp_engine_test.py

The stdio server is driven exactly as the agent CLI drives it — a child process, JSON-RPC on its
stdin and stdout — against a small fake backend, so the test needs no browser and no real Studio.
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("ASSET_STUDIO_SECONDARY", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS = 0
FAIL: list[str] = []


def ok(name: str, cond: bool, extra: object = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL.append(name)
        print("  FAIL  " + name + ("  <- " + str(extra)[:300] if extra != "" else ""))


from asset_studio import cc_session as cc          # noqa: E402
from asset_studio import mcp_catalog              # noqa: E402
from asset_studio.config import settings          # noqa: E402
from asset_studio.main import app                 # noqa: E402

orig_get = settings.get


def with_settings(**over):
    def get(key, default=None):
        if key in over:
            return over[key]
        return orig_get(key, default)
    return get


# ---------------------------------------------------------------- the catalog
print("The catalog: the app's own routes, the notes' own switches")
cwd = str(HERE.parent)
real = {"forge": cc._forge_on, "live": cc._live_on, "scene": cc._scene_on, "navigate": cc._navigate_on,
        "review": cc._review_on, "animate": cc._animate_on, "debugger": cc._debugger_on}
try:
    cc._forge_on = lambda cwd="": True
    cc._live_on = lambda cwd="": False
    cc._scene_on = lambda cwd="": False
    cc._navigate_on = lambda cwd="": False
    cc._review_on = lambda cwd="": False
    cc._animate_on = lambda cwd="": False
    cc._debugger_on = lambda cwd="": False
    settings.get = with_settings(cc_graphify=True)
    cat = mcp_catalog.catalog(app, cwd)
    names = [t["name"] for t in cat["tools"]]
    ok("the forge family is offered when its note would be", {"forge", "look", "aim", "bench", "export"} <= set(names), names)
    ok("...and the live link is not, when its switch is off", not any(n.startswith("live_") for n in names), names)
    ok("...and the graph follows cc_graphify", "graphify_query" in names)
    forge = next(t for t in cat["tools"] if t["name"] == "forge")
    fs = forge["inputSchema"]
    text = json.dumps(fs)
    ok("forge's schema is its body model's own fields", "js" in fs["properties"] and "views" in fs["properties"], list(fs["properties"])[:12])
    ok("...self-contained: no $ref left", "$ref" not in text)
    ok("...with no titles to pay for", '"title"' not in text)
    ok("...project is not required: the server fills it in", "project" not in (fs.get("required") or []))
    ok("...and forge takes the picture switch", "picture" in fs["properties"])
    bench = next(t for t in cat["tools"] if t["name"] == "bench")
    ok("a GET tool's arguments come from its signature, typed",
       bench["inputSchema"]["properties"].get("project", {}).get("type") == "string", bench["inputSchema"])
    ok("...and bench, which draws nothing, has no picture switch", "picture" not in bench["inputSchema"]["properties"])
    gq = next(t for t in cat["tools"] if t["name"] == "graphify_query")
    ok("the graph's root is filled from the session folder too", gq["fills"] == ["root"], gq["fills"])
    ok("the instructions say the tools are optional and the same as curl",
       "optional" in cat["instructions"] and "curl" in cat["instructions"])
    cc._forge_on = lambda cwd="": False
    settings.get = with_settings(cc_graphify=False)
    from asset_studio import web_tools
    real_web = web_tools.enabled
    web_tools.enabled = lambda: False
    try:
        cat_off = mcp_catalog.catalog(app, cwd)
    finally:
        web_tools.enabled = real_web
    ok("with every switch off, no tool at all", cat_off["tools"] == [], [t["name"] for t in cat_off["tools"]])
finally:
    for k, fn in real.items():
        setattr(cc, "_%s_on" % k, fn)
    settings.get = orig_get

# FastAPI 0.141 (what a new PC's wheels install) keeps each include_router as ONE nested object
# holding the router, where 0.115 (this PC) copied the routes into app.routes. The installed Studio
# then offered 0 tools. The same shape, built by hand, so this PC's FastAPI tests it too.
print("\nRoutes inside nested routers (FastAPI 0.141)")
from types import SimpleNamespace  # noqa: E402
from fastapi import APIRouter  # noqa: E402
_r = APIRouter(prefix="/api/live")


@_r.post("/forge")
def _fake_forge():
    return {}


_inner = APIRouter(prefix="/api/deep")


@_inner.get("/thing")
def _fake_thing():
    return {}


_nested = SimpleNamespace(routes=[SimpleNamespace(original_router=_inner, include_context=SimpleNamespace(prefix="/x"))])
fake_app = SimpleNamespace(routes=[
    SimpleNamespace(original_router=_r, include_context=SimpleNamespace(prefix="")),
    SimpleNamespace(original_router=_nested, include_context=SimpleNamespace(prefix="")),
])
idx = mcp_catalog._route_index(fake_app)
ok("a route inside an included router is found, with its full path", ("POST", "/api/live/forge") in idx, sorted(idx))
ok("...and one nested two deep keeps the include's prefix", ("GET", "/x/api/deep/thing") in idx, sorted(idx))
ok("the real app's index still holds every tool's route", all(
    (m, p) in mcp_catalog._route_index(app) for _, m, p, _, _ in mcp_catalog._TOOLS))
# 0.141 also moved a route's body model: no `type_`, it is at field_info.annotation. Missed, every
# POST tool had an empty schema and `project` was never filled - a 422 on every call.
from pydantic import BaseModel  # noqa: E402


class _FakeBody(BaseModel):
    project: str
    views: list = []


new_style = SimpleNamespace(field_info=SimpleNamespace(annotation=_FakeBody))
ok("a body model is found where FastAPI 0.141 keeps it", mcp_catalog._body_model(new_style) is _FakeBody)
ok("...and where 0.115 keeps it", mcp_catalog._body_model(SimpleNamespace(type_=_FakeBody)) is _FakeBody)
ok("...and none is made up from a field that has no model", mcp_catalog._body_model(SimpleNamespace(type_=str)) is None)

# ---------------------------------------------------------------- the route
print("\nThe route")
from fastapi.testclient import TestClient  # noqa: E402
client = TestClient(app)
r = client.get("/api/mcp/catalog", params={"cwd": cwd})
ok("GET /api/mcp/catalog answers", r.status_code == 200 and r.json().get("ok") is True, r.text[:200])
settings.get = with_settings(cc_mcp=False)
try:
    r2 = client.get("/api/mcp/catalog", params={"cwd": cwd})
    ok("...and lists nothing when Studio tools over MCP is off", r2.json().get("tools") == [] and r2.json().get("off") is True, r2.text[:200])
finally:
    settings.get = orig_get

# ---------------------------------------------------------------- the stdio server
print("\nThe stdio server, driven like the CLI drives it")
tmp = Path(tempfile.mkdtemp(prefix="mcp_test_"))
png = tmp / "sheet.png"
# the smallest valid PNG: 1x1, one pixel
png.write_bytes(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
seen: list = []
CATALOG = {"ok": True, "instructions": "TEST INSTRUCTIONS", "tools": [
    {"name": "look", "description": "look again", "method": "POST", "path": "/api/live/look",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "views": {"type": "array"},
                                                       "picture": {"type": "boolean"}}},
     "fills": ["project"], "picture": True},
    {"name": "bench", "description": "the bench", "method": "GET", "path": "/api/live/bench",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
     "fills": ["project"], "picture": False},
    {"name": "broken", "description": "always 500", "method": "POST", "path": "/api/live/broken",
     "inputSchema": {"type": "object"}, "fills": [], "picture": False},
]}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code: int, obj: object) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        seen.append(("GET", u.path, q))
        if u.path == "/api/mcp/catalog":
            return self._json(200, CATALOG)
        if u.path == "/api/live/bench":
            return self._json(200, {"ok": True, "open": False, "project": q.get("project")})
        return self._json(404, {"ok": False})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        seen.append(("POST", u.path, body))
        if u.path == "/api/live/look":
            if body.get("slow"):
                time.sleep(1.5)
            return self._json(200, {"ok": True, "sheet": str(png), "findings": ["x"], "slow": bool(body.get("slow"))})
        return self._json(500, {"ok": False, "error": "boom"})


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
script = HERE / "asset_studio" / "mcp_engine.py"
env = {**os.environ, "STUDIO_BASE": base, "STUDIO_CWD": str(tmp), "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env)
replies: dict = {}
order: list = []


def reader():
    for raw in proc.stdout:
        try:
            m = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        replies[m.get("id")] = m
        order.append(m.get("id"))


threading.Thread(target=reader, daemon=True).start()


def send(msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
    proc.stdin.flush()


def wait(mid, t=15.0):
    end = time.time() + t
    while time.time() < end:
        if mid in replies:
            return replies[mid]
        time.sleep(0.02)
    return None


send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}}})
init = wait(1)
ok("initialize answers as the studio server", bool(init) and init["result"]["serverInfo"]["name"] == "studio", init)
ok("...echoes the client's protocol version", init and init["result"]["protocolVersion"] == "2025-06-18")
ok("...and hands over the backend's instructions", init and init["result"].get("instructions") == "TEST INSTRUCTIONS")
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
lst = wait(2)
ok("tools/list lists the backend's tools", lst and [t["name"] for t in lst["result"]["tools"]] == ["look", "bench", "broken"], lst)
ok("...each with its input schema", lst and all(t["inputSchema"].get("type") == "object" for t in lst["result"]["tools"]))

send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "look", "arguments": {"views": ["front"]}}})
r3 = wait(3)
c3 = (r3 or {}).get("result", {}).get("content", [])
ok("a call answers with the endpoint's JSON as text", c3 and c3[0]["type"] == "text" and '"findings"' in c3[0]["text"], c3[:1])
ok("...and the answer's sheet attached as an image", len(c3) == 2 and c3[1]["type"] == "image" and c3[1]["mimeType"] == "image/png", [c.get("type") for c in c3])
ok("...the real picture bytes", len(c3) == 2 and base64.b64decode(c3[1]["data"])[:8] == b"\x89PNG\r\n\x1a\n")
post = [s for s in seen if s[0] == "POST" and s[1] == "/api/live/look"]
ok("project left out is filled with the session folder", post and post[-1][2].get("project") == str(tmp), post[-1:] if post else seen)
ok("...and the MCP-only picture switch never reaches the backend", post and "picture" not in post[-1][2])

send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "look", "arguments": {"picture": False}}})
r4 = wait(4)
ok("picture:false returns the text alone", r4 and len(r4["result"]["content"]) == 1)

send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "bench", "arguments": {}}})
r5 = wait(5)
ok("a GET tool goes out as a query, folder filled in",
   r5 and json.loads(r5["result"]["content"][0]["text"]).get("project") == str(tmp), r5)
ok("...and is not an error", r5 and r5["result"]["isError"] is False)

send({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "broken", "arguments": {}}})
r6 = wait(6)
ok("a 500 from the backend is an error result, with its body", r6 and r6["result"]["isError"] is True and "boom" in r6["result"]["content"][0]["text"], r6)
send({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nosuch", "arguments": {}}})
r7 = wait(7)
ok("an unknown tool says so, as an error result", r7 and r7["result"]["isError"] is True)

# Two calls side by side: the slow one first, the fast one must not wait for it.
send({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "look", "arguments": {"slow": True, "picture": False}}})
send({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "bench", "arguments": {}}})
wait(8, 10)
wait(9, 10)
ok("calls run side by side: the fast one answers first", order.index(9) < order.index(8), order)

send({"jsonrpc": "2.0", "id": 10, "method": "ping"})
ok("ping answers", (wait(10) or {}).get("result") == {})
send({"jsonrpc": "2.0", "id": 11, "method": "no/such/method"})
r11 = wait(11)
ok("an unknown method is -32601", r11 and r11.get("error", {}).get("code") == -32601, r11)
proc.stdin.close()
proc.wait(timeout=10)
ok("the server ends cleanly when the CLI closes its stdin", proc.returncode == 0, proc.returncode)

# A client that sends a call and closes its end at once still gets the answer: the server waits
# for the calls in flight before it leaves. The release check lost a `look` this way.
p3 = subprocess.run([sys.executable, str(script)], input=(json.dumps(
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n" + json.dumps(
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
     "params": {"name": "look", "arguments": {"slow": True, "picture": False}}}) + "\n").encode(),
    capture_output=True, env=env, timeout=60)
got3 = {m.get("id"): m for m in (json.loads(x) for x in p3.stdout.decode().splitlines() if x.strip())}
ok("a call still running when stdin closes is answered before the server leaves",
   2 in got3 and got3[2]["result"]["isError"] is False, sorted(got3))

print("\nA backend that is down")
env2 = {**env, "STUDIO_BASE": "http://127.0.0.1:9", "STUDIO_MCP_DOWN_WAIT": "1"}
p2 = subprocess.run([sys.executable, str(script)], input=(json.dumps(
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n" + json.dumps(
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n").encode(), capture_output=True, env=env2, timeout=60)
lines = [json.loads(x) for x in p2.stdout.decode().splitlines() if x.strip()]
ok("it still starts, and says the backend was not reachable",
   lines and "not reachable" in lines[0]["result"]["instructions"], lines[:1])
ok("...and lists no tools rather than wrong ones", len(lines) > 1 and lines[1]["result"]["tools"] == [], lines[1:2])
srv.shutdown()

# ---------------------------------------------------------------- the session flag
print("\nEvery Studio session gets the server")
args = cc._claude_stream_args("claude", None, "default", "default", False, "default", project_id="mcptest", cwd=str(tmp))
ok("--mcp-config is on the command line", "--mcp-config" in args, args[:12])
if "--mcp-config" in args:
    cfgp = Path(args[args.index("--mcp-config") + 1])
    cfg = json.loads(cfgp.read_text(encoding="utf-8"))
    st = cfg["mcpServers"]["studio"]
    ok("...naming this backend's Python and mcp_engine.py", st["command"] == sys.executable and st["args"][0].endswith("mcp_engine.py"), st)
    ok("...with the backend's origin and the session folder", st["env"]["STUDIO_BASE"].startswith("http://") and st["env"]["STUDIO_CWD"] == str(tmp), st["env"])
settings.get = with_settings(cc_mcp=False)
try:
    args_off = cc._claude_stream_args("claude", None, "default", "default", False, "default", project_id="mcptest", cwd=str(tmp))
    ok("...and not when Studio tools over MCP is off", "--mcp-config" not in args_off)
finally:
    settings.get = orig_get
try:
    (cc._MCP_DIR / "mcptest.json").unlink()
except OSError:
    pass

print("\n  %d passed, %d failed" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAIL  " + f)
sys.exit(1 if FAIL else 0)

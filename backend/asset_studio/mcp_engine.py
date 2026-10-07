"""The Studio engine over MCP: a stdio server that forwards every tool to the running backend.

Started by the agent CLI for each Studio session (cc_session writes the --mcp-config), with
STUDIO_BASE (the backend's origin) and STUDIO_CWD (the session's folder) in its environment.

Why it is written this way:
- STANDARD LIBRARY ONLY. It runs as a plain script under the backend's own Python, so a new PC
  needs nothing installed and the installer ships nothing new. The protocol is small: JSON-RPC 2.0,
  one message per line on stdin and stdout.
- THE BACKEND DECIDES WHAT EXISTS. The tool list comes from GET /api/mcp/catalog, built from the
  app's own routes and gated by the same switches as the agent notes. Nothing is listed twice.
- A TOOL IS THE SAME CALL AS CURL. tools/call posts the arguments to the same endpoint; the only
  things added are the session folder where `project` (or the graph's `root`) is left out, and the
  answer's main picture attached as an image, so looking at a render is one step instead of two.
- IT OUTLIVES A BACKEND RESTART. A call that finds the backend down retries for a while, because
  the Studio restarts its backend under running sessions.

Run by hand to see the handshake: `python mcp_engine.py` and type
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (os.environ.get("STUDIO_BASE") or "http://127.0.0.1:8777").rstrip("/")
CWD = os.environ.get("STUDIO_CWD") or os.getcwd()
VERSION = "1.0"
# The picture keys an answer may carry, at its top level or under "look" (edit, place).
PICTURE_KEYS = ("sheet", "detail_sheet", "peak", "shot", "picture", "image")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_TEXT = 80_000            # characters of JSON text per answer; the rest is named, not sent
MAX_IMAGE_BYTES = 6_000_000  # a picture larger than this is named by path instead
DOWN_WAIT_S = float(os.environ.get("STUDIO_MCP_DOWN_WAIT") or 45)  # how long a call waits out a backend restart

_out_lock = threading.Lock()
_catalog: dict | None = None
_catalog_lock = threading.Lock()


def _log(msg: str) -> None:
    try:
        sys.stderr.write("[studio-mcp] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


def _send(msg: dict) -> None:
    data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with _out_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def _http(method: str, path: str, args: dict | None, timeout: float) -> tuple[int, str]:
    """(status, body text). Retries a refused connection for DOWN_WAIT_S: a restart, not an error."""
    url = BASE + path
    data = None
    headers = {"content-type": "application/json"}
    if method == "GET":
        q = {k: ("true" if v is True else "false" if v is False else v)
             for k, v in (args or {}).items() if v is not None}
        if q:
            url += "?" + urllib.parse.urlencode(q, doseq=True)
    else:
        data = json.dumps(args or {}).encode("utf-8")
    deadline = time.time() + DOWN_WAIT_S
    while True:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = str(e)
            return e.code, body
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if time.time() > deadline:
                return 0, "the Studio backend at %s did not answer: %s" % (BASE, e)
            time.sleep(1.5)


def _get_catalog() -> dict:
    """The backend's tool list for this session's folder, fetched once."""
    global _catalog
    with _catalog_lock:
        if _catalog is not None:
            return _catalog
        status, body = _http("GET", "/api/mcp/catalog", {"cwd": CWD}, timeout=30)
        cat: dict = {"tools": [], "instructions": ""}
        if status == 200:
            try:
                cat = json.loads(body)
            except ValueError:
                _log("catalog was not JSON")
        else:
            _log("catalog unavailable (%s): %s" % (status, body[:200]))
            cat["instructions"] = ("The Studio backend was not reachable when this session started, so "
                                   "its tools are not listed; the curl endpoints in your notes still work.")
        _catalog = cat
        return cat


def _pictures(answer: object, want: int) -> list[str]:
    """Paths of the answer's main pictures, main sheet first."""
    found: list[str] = []

    def take(d: dict) -> None:
        for k in PICTURE_KEYS:
            v = d.get(k)
            if isinstance(v, str) and os.path.splitext(v)[1].lower() in IMAGE_TYPES and v not in found:
                found.append(v)

    if isinstance(answer, dict):
        take(answer)
        for sub in ("look", "result"):
            if isinstance(answer.get(sub), dict):
                take(answer[sub])
    return [p for p in found if os.path.isfile(p)][:want]


def _call(name: str, arguments: dict) -> dict:
    cat = _get_catalog()
    tool = next((t for t in cat.get("tools", []) if t.get("name") == name), None)
    if tool is None:
        return {"content": [{"type": "text", "text": "No Studio tool named %r in this session." % name}],
                "isError": True}
    args = dict(arguments or {})
    want_picture = args.pop("picture", True) is not False
    for a in tool.get("fills") or []:
        if not args.get(a):
            args[a] = CWD
    # A forge build can take minutes; everything else answers in seconds.
    timeout = 900 if tool.get("method") == "POST" else 120
    status, body = _http(tool.get("method", "POST"), tool.get("path", ""), args, timeout)
    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    text = body if len(body) <= MAX_TEXT else (
        body[:MAX_TEXT] + "\n... (%d more characters; ask with numbers:true for a short answer)" % (len(body) - MAX_TEXT))
    content: list[dict] = [{"type": "text", "text": text}]
    if answer is not None and tool.get("picture") and want_picture:
        for path in _pictures(answer, 1):
            try:
                size = os.path.getsize(path)
                if size > MAX_IMAGE_BYTES:
                    continue
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                content.append({"type": "image", "data": data,
                                "mimeType": IMAGE_TYPES[os.path.splitext(path)[1].lower()]})
            except OSError as e:
                _log("could not read %s: %s" % (path, e))
    bad = status != 200 or (isinstance(answer, dict) and answer.get("ok") is False)
    return {"content": content, "isError": bool(bad)}


def _handle(msg: dict) -> None:
    mid = msg.get("id")
    method = msg.get("method") or ""
    params = msg.get("params") or {}
    if mid is None:
        return  # a notification: initialized, cancelled, ...
    try:
        if method == "initialize":
            cat = _get_catalog()
            result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "studio", "version": VERSION},
                      "instructions": cat.get("instructions") or ""}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            cat = _get_catalog()
            result = {"tools": [{"name": t["name"], "description": t.get("description", ""),
                                 "inputSchema": t.get("inputSchema") or {"type": "object"}}
                                for t in cat.get("tools", [])]}
        elif method == "tools/call":
            result = _call(params.get("name", ""), params.get("arguments") or {})
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        else:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: " + method}})
            return
        _send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as e:  # a tool must never take the server down
        _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "%s: %s" % (type(e).__name__, e)}})


def main() -> None:
    _log("serving %s for %s" % (BASE, CWD))
    running: list = []
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        # Calls run side by side: a forge build must not hold up a bench read the agent sent with it.
        if isinstance(msg, dict) and msg.get("method") == "tools/call":
            t = threading.Thread(target=_handle, args=(msg,), daemon=True)
            t.start()
            running = [x for x in running if x.is_alive()] + [t]
        elif isinstance(msg, dict):
            _handle(msg)
    # THE END OF STDIN IS NOT "DROP THE WORK". A client that sends its calls and closes its end - a
    # script, a pipe, a person who types a call and ends the input - still reads the answers, and
    # leaving here with a call in flight lost its answer without a word (found by the release
    # check of 2026-09-25). A client that wants the server gone kills it; that is not delayed.
    deadline = time.time() + 900
    for t in running:
        t.join(max(0.0, deadline - time.time()))


if __name__ == "__main__":
    main()

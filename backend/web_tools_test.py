"""Can an agent reach a page that blocks it, and does it get told how?

Two halves. The FETCHER is proven live against a real site, because a stealth fetcher that only
works on a demo page is worth nothing — crunchbase.com answers a plain request with 403 and this
has to come back with the page. That test is skipped, not failed, when there is no network.

The WIRING is proven offline: the hint the agent sees at the moment of use, the note in its system
prompt, and the hook matcher that carries it. Those are what decide whether the fetcher is ever
reached at all, and they were the part that could rot silently.
"""
import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cc      # noqa: E402
from asset_studio import graph_hook as gh      # noqa: E402
from asset_studio import web_tools as wt       # noqa: E402

ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s — %s" % (name, why))


class _Stub(BaseHTTPRequestHandler):
    """Stands in for the backend so the hint can be tested without one running."""
    available = True

    def do_GET(self):
        body = json.dumps({"available": self.available}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve(available: bool):
    _Stub.available = available
    s = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _hint(available: bool, session: str = "") -> str:
    """Run _web_hint against a stub backend and return whatever it printed."""
    srv = _serve(available)
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        spoke = gh._web_hint({"session_id": session}, "127.0.0.1", srv.server_address[1])
    finally:
        sys.stdout = real
        srv.shutdown()
        srv.server_close()
    return buf.getvalue() if spoke else ""


print("The agent is told, at the moment it matters")
out = _hint(True)
check("the hint fires for a web tool", bool(out))
try:
    doc = json.loads(out or "{}")
except Exception:
    doc = {}
msg = ((doc.get("hookSpecificOutput") or {}).get("additionalContext") or "")
check("it is additionalContext, not a block",
      (doc.get("hookSpecificOutput") or {}).get("hookEventName") == "PreToolUse"
      and "permissionDecision" not in (doc.get("hookSpecificOutput") or {}), sorted(doc))
check("it names the fetch endpoint", "/api/web/fetch" in msg)
check("it names the search endpoint", "/api/web/search" in msg)
check("it names the symptoms to watch for",
      "403" in msg and "just a moment" in msg.lower(), msg[:90])

print("\nAdvice you cannot follow is worse than none")
check("silent when the fetcher is not installed", _hint(False) == "")

print("\nSaid once per session, not once per call")
# Firing on every web call put the same ~110 tokens into the conversation for each read —
# uncached, and already said in the system prompt. Once is enough; twice is spam.
import uuid                                            # noqa: E402

sid = "sess-" + uuid.uuid4().hex[:12]
check("the first web call in a session is told", bool(_hint(True, sid)))
check("the second is not", _hint(True, sid) == "")
check("a different session is told once too", bool(_hint(True, "sess-" + uuid.uuid4().hex[:12])))

print("\nOne note, short, for a session and its subagents alike")
note = cc._web_note()
check("it names both endpoints", "/api/web/fetch" in note and "/api/web/search" in note)
# A system prompt already carries the browser rule, the graph, the phases panel, the review
# harness and the output style. Every paragraph here is one the model reads past every turn.
check("it stays under 600 characters", len(note) < 600, len(note))
check("it names the symptom, which a model cannot guess",
      "403" in note and "just a moment" in note.lower())
check("it forbids installing a second browser", "not install" in note.lower(), note[-140:])

print("\nThe same note reaches a session and a subagent")
src_cc = open("asset_studio/cc_session.py", encoding="utf-8").read()
# The indent matters: "sub_notes.append(...)" contains "notes.append(...)" as a substring, so a
# bare count reads 3 and says nothing about which call sites those are.
check("both session spawn paths carry it",
      src_cc.count("\n        notes.append(_web_note())") == 2,
      src_cc.count("\n        notes.append(_web_note())"))
check("and the subagent prompt carries it too",
      "sub_notes.append(_web_note())" in src_cc)

src = open("asset_studio/cc_session.py", encoding="utf-8").read()
check("the hook matcher carries WebFetch", "WebFetch|WebSearch" in src)
check("the hook is registered even with graphify off",
      'settings.get("cc_graphify") or web_tools.enabled()' in src)

print("\nA real page that really blocks")
if not wt.available():
    skipped("crunchbase.com comes back", "scrapling is not installed here")
else:
    r = wt.fetch("https://www.crunchbase.com/organization/openai", chars=3000, timeout=150)
    if not r.get("ok") and "timed out" in str(r.get("error", "")):
        skipped("crunchbase.com comes back", "no network / timed out")
    elif r.get("status") == 404:
        # THE PAGE MOVED, THE FETCHER DID NOT BREAK. This is what happened to the previous
        # target: getting past Cloudflare and being handed a 404 is a pass for the code under
        # test and a failure for the URL, and only one of those is worth a red line.
        skipped("crunchbase.com comes back", "that company page has moved (404)")
    else:
        tried = r.get("tried") or []
        check("the plain tier is refused, as it always is",
              any(t.get("tier") == "http" and t.get("blocked") for t in tried), tried)
        check("the stealth tier gets the page", r.get("status") == 200 and r.get("ok"), r.get("error"))
        check("and the page has real content in it", len(r.get("text") or "") > 1000,
              len(r.get("text") or ""))
        check("the answer says which tier won", r.get("tier") == "stealth", r.get("tier"))

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)

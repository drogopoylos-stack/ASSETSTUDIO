# -*- coding: utf-8 -*-
"""The open path cannot hang, and it says what really went wrong.

`/api/live/open` used to never return. The chain was: a dev server had died, but the registry
still named it, so the tab navigated to a URL nothing was serving and landed on Chrome's own error
page; the shim cannot install there (Chrome forbids it), so an evaluate with `awaitPromise` waited
on a promise that never settled; Chrome never replied; and `_Cdp.call` loops on `recv()` with no
deadline. One dead port took down the forge, the debugger and animation review.

Four things had to be true afterwards, and each is checked here without a browser:

  * a CDP call has a deadline, and the error names the method
  * a URL that answers nothing is not offered as a dev server
  * a tab on an error page says so, instead of failing three steps later
  * the sidecar URL points at THIS backend

Run:  backend/.venv/Scripts/python.exe backend/open_path_test.py
"""
import asyncio
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import live as L                  # noqa: E402
from asset_studio import review                     # noqa: E402

passed = 0
fails = []


def ok(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
        return
    fails.append(name + ("   <- " + str(extra) if extra else ""))
    print("  FAIL  %s   %s" % (name, extra))


# ---------------------------------------------------------------- the deadline
print("A CDP call has a deadline")


class _Silent:
    """A browser that takes the message and never answers — exactly the failure that hung."""

    def __init__(self):
        self.sent = []

    async def send(self, s):
        self.sent.append(s)

    async def recv(self):
        await asyncio.sleep(3600)                    # forever, as far as the caller is concerned


async def silent_case():
    cdp = review._Cdp(_Silent())
    t = time.time()
    try:
        await cdp.call("Page.enable", {}, timeout=1.0)
        return None, time.time() - t
    except TimeoutError as ex:
        return str(ex), time.time() - t


msg, secs = asyncio.run(silent_case())
ok("a silent browser raises instead of hanging", msg is not None)
ok("...promptly", secs < 3.0, "%.1fs" % secs)
ok("...naming the method", msg and "Page.enable" in msg, msg)
ok("...and saying how long it waited", msg and "1s" in msg, msg)
# The message has to point at the real cause, or the next person re-derives it from scratch.
ok("...and pointing at the likely reason", msg and "promise" in msg and "served" in msg, msg)
ok("there is a ceiling by default", review._Cdp.CALL_TIMEOUT > 10)


class _Chatty(_Silent):
    """A browser mid-conversation: events first, then the reply, then more events."""

    def __init__(self):
        super().__init__()
        self.q = ['{"method":"Runtime.consoleAPICalled","params":{}}',
                  '{"method":"Page.frameNavigated","params":{}}',
                  '{"id":1,"result":{"ok":true}}']

    async def recv(self):
        if self.q:
            return self.q.pop(0)
        await asyncio.sleep(3600)


async def chatty_case():
    cdp = review._Cdp(_Chatty())
    return await asyncio.wait_for(cdp.call("Page.enable", {}, timeout=5.0), 6)


got = asyncio.run(chatty_case())
ok("events before the reply are skipped, not fatal", got == {"ok": True}, got)


class _WrongId(_Silent):
    async def recv(self):
        return '{"id":99,"result":{"nope":1}}'


async def wrongid_case():
    cdp = review._Cdp(_WrongId())
    try:
        await cdp.call("Page.enable", {}, timeout=1.0)
        return "returned"
    except TimeoutError:
        return "timed out"


# A reply for somebody else's id must never be mistaken for ours, and must not hang forever
# waiting either. The deadline is what makes the second half true.
ok("another id is not our reply, and still ends", asyncio.run(wrongid_case()) == "timed out")

# ---------------------------------------------------------------- the liveness check
print("\nA dev server has to actually answer")
ok("a dead port is dead", not L._answers("http://127.0.0.1:1"))
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
# A backlog of 1 is a trap here: nothing accepts, so the first probe's connection stays in the
# queue and the second is refused. That is the fixture lying, not the port dying.
srv.listen(8)
port = srv.getsockname()[1]
ok("a listening port is alive", L._answers("http://127.0.0.1:%d" % port))
ok("...with the scheme left off too", L._answers("127.0.0.1:%d" % port))
srv.close()
ok("...and dead again once it closes", not L._answers("http://127.0.0.1:%d" % port))
ok("nonsense does not throw", not L._answers("not a url at all"))
t = time.time()
L._answers("http://10.255.255.1:9")          # a black hole: must not sit on it
ok("an unreachable host gives up quickly", time.time() - t < 3.0, "%.1fs" % (time.time() - t))

# ---------------------------------------------------------------- the landing check
print("\nA tab on an error page says so")


class _Fake:
    def __init__(self, href):
        self.href = href

    async def raw(self, expr, wait=True):
        return self.href


async def landed(href, want):
    try:
        await L._landed(_Fake(href), want)
        return ""
    except RuntimeError as ex:
        return str(ex)


err = asyncio.run(landed("chrome-error://chromewebdata/", "http://127.0.0.1:5173/"))
ok("an error page is caught", bool(err))
# The URL is the actionable part: "something failed" sends you looking in the wrong place.
ok("...naming the URL nobody is serving", "5173" in err, err)
ok("...and what to do about it", "dev server" in err, err)
ok("a real page passes", asyncio.run(landed("http://127.0.0.1:8777/", "http://127.0.0.1:8777/")) == "")
ok("about:blank is not an error page",
   asyncio.run(landed("about:blank", "")) == "")

# ---------------------------------------------------------------- the sidecar URL
print("\nThe sidecar URL points at THIS backend")
src = (Path(__file__).resolve().parent / "asset_studio" / "live.py").read_text(encoding="utf-8")
ok("port 8777 is no longer written into the URL",
   '"http://127.0.0.1:8777/api/workspace/file' not in src)
ok("...the settings are asked instead",
   'settings.get("port")' in src and "api/workspace/file" in src)

# ---------------------------------------------------------------- the bridge
print("\nThe bridge cannot fail the open")
ok("it has a budget", L._BRIDGE_BUDGET > 0)
ok("...and is wrapped in it", "asyncio.wait_for(_bridge(" in src)
# It is an optimisation for Phaser and PixiJS. A page it cannot help is still a page.
ok("...with the failure swallowed and noted", "bridge_note" in src)

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)

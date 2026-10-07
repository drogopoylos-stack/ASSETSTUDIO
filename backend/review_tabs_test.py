# -*- coding: utf-8 -*-
"""What the shared browser is allowed to close.

A leaked page is not idle. A WebGL game keeps rendering, keeps its textures and keeps its share of
the card — twelve copies of one game were found open at once on this machine, 2.5 GB of VRAM and
4.8 GB of RAM for tabs nobody could see. The sweep that fixes that is one instruction away from
being the worst kind of bug, because the tabs it must NOT close belong to other people's agents.
So the ownership rule gets a test of its own, and it needs no browser to run.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from asset_studio import live, review     # noqa: E402

ok = fail = 0


def check(name, cond, got=None):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, repr(got)[:200]))


class FakeCdp:
    """A browser with five pages, one of which is not a page at all."""

    def __init__(self, targets):
        self.targets = list(targets)
        self.closed = []

    async def call(self, method, params=None, sid=None):
        if method == "Target.getTargets":
            return {"targetInfos": self.targets}
        if method == "Target.closeTarget":
            self.closed.append(params["targetId"])
            return {}
        raise AssertionError("unexpected call " + method)


def page(tid, title=""):
    return {"targetId": tid, "type": "page", "title": title}


print("What the sweep is allowed to close")

_tabs_backup, _live_backup, _inflight_backup = dict(review._tabs), dict(live._tabs), set(review._inflight)
try:
    review._tabs.clear()
    live._tabs.clear()
    review._inflight.clear()

    review._tabs["warm-one"] = {"tid": "T-warm", "seen": time.time()}
    review._inflight.add("T-rendering")
    live._tabs["c:/games/rot-haul"] = {"project": "C:/games/rot-haul", "target": "T-live", "url": "x"}

    cdp = FakeCdp([
        page("T-warm", "a warm tab someone may come back to"),
        page("T-rendering", "a render happening right now"),
        page("T-live", "ANOTHER AGENT'S GAME"),
        page("T-orphan-a", "a leaked game"),
        page("T-orphan-b", "another leaked game"),
        {"targetId": "T-worker", "type": "service_worker"},
    ])
    n = asyncio.run(review.sweep_orphan_pages(cdp))

    check("the leaked pages are closed", sorted(cdp.closed) == ["T-orphan-a", "T-orphan-b"], cdp.closed)
    check("...and it says how many", n == 2, n)
    check("ANOTHER AGENT'S LIVE GAME IS NEVER CLOSED", "T-live" not in cdp.closed, cdp.closed)
    check("a render in flight is never closed", "T-rendering" not in cdp.closed, cdp.closed)
    check("a warm tab is never closed", "T-warm" not in cdp.closed, cdp.closed)
    check("...and nothing that is not a page is touched", "T-worker" not in cdp.closed, cdp.closed)

    print("\nWith nothing of our own open, nothing is spared by accident")
    review._tabs.clear()
    live._tabs.clear()
    review._inflight.clear()
    cdp2 = FakeCdp([page("T-1"), page("T-2")])
    asyncio.run(review.sweep_orphan_pages(cdp2, keep={"T-2"}))
    check("a page the caller names is kept", cdp2.closed == ["T-1"], cdp2.closed)

    print("\nA browser that will not answer is not an error")
    class Dead(FakeCdp):
        async def call(self, method, params=None, sid=None):
            raise RuntimeError("socket closed")
    check("a dead browser sweeps nothing and raises nothing",
          asyncio.run(review.sweep_orphan_pages(Dead([]))) == 0)
finally:
    review._tabs.clear(); review._tabs.update(_tabs_backup)
    live._tabs.clear(); live._tabs.update(_live_backup)
    review._inflight.clear(); review._inflight.update(_inflight_backup)

print("\nThe reap runs on every render, not only on the ones that name a session")
src = Path(__file__).resolve().parent / "asset_studio" / "review.py"
text = src.read_text(encoding="utf-8")
tail = text[text.index("        finally:\n            _inflight.discard(tid)"):]
tail = tail[:2000]
check("the idle reap is outside the `if skey:` branch",
      tail.index("do not hoard tabs nobody came back to") > tail.index("else:"), "")
check("...and the orphan sweep runs there too", "sweep_orphan_pages(cdp)" in tail)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

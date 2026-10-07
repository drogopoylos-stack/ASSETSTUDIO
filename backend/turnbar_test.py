# -*- coding: utf-8 -*-
"""The line under a finished answer: what it counted, what it joined, and what it refused to say.

The dangerous half of this feature is not the arithmetic. It is the temptation to fill a gap. A
turn that has not been priced yet, or one from a conversation older than the banking, knows its
tool calls and its duration and nothing else — and a bar that quietly prints `0 tok` there teaches
a person to distrust the number on every other row too. So the rule under test is: a field that is
not known is NOT SENT.

    python turnbar_test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from asset_studio import mission, turns          # noqa: E402

ok = fail = 0


def check(name, cond, got=None):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, repr(got)[:200]))


def ev(kind, at, **kw):
    """A feed event as the parser makes one: a display clock, and the instant as a number."""
    e = {"kind": kind, "ts": time.strftime("%H:%M:%S", time.localtime(at)), "at": at}
    e.update(kw)
    return e


T0 = 1_700_000_000.0
PROJ = "turnbar-test-project"

# ---------------------------------------------------------------- counting what it can see
print("What one turn did, counted from the events themselves")
events = [
    ev("user", T0, text="do the thing"),
    ev("tool", T0 + 2, tool="Bash", title="Bash"),
    ev("tool", T0 + 9, tool="Edit", title="Edit", icon="edit",
       subtitle="C:/p/a.ts", diff={"added": 12, "removed": 3}),
    ev("tool", T0 + 20, tool="Edit", title="Edit", icon="edit",
       subtitle="C:/p/a.ts", diff={"added": 4, "removed": 0}),
    ev("tool", T0 + 25, tool="Write", title="Write", icon="edit",
       subtitle="C:/p/b.ts", diff={"added": 40, "removed": 0}),
    ev("text", T0 + 30, text="done"),
]
rows = [r for r in mission._turn_rows(PROJ, events) if r.get("kind") == "turn"]
check("one finished turn gives one summary", len(rows) == 1, len(rows))
r = rows[0] if rows else {}
check("every tool call is counted", r.get("tools") == 4, r.get("tools"))
check("the same file edited twice is ONE file", r.get("files") == 2, r.get("files"))
check("added and removed lines are summed", (r.get("added"), r.get("removed")) == (56, 3),
      (r.get("added"), r.get("removed")))
check("the duration is first event to last", r.get("wall_s") == 30.0, r.get("wall_s"))
check("...and it says the feed measured it, not the CLI", r.get("wall_from") == "feed", r.get("wall_from"))

print("\nWhat it does NOT know, it does not say")
for absent in ("tokens", "cost", "model", "agents", "agent_tokens"):
    check("an unpriced turn sends no %s" % absent, absent not in r, r.get(absent))

# ---------------------------------------------------------------- the summary goes AFTER the turn
print("\nWhere the line lands")
out = mission._turn_rows(PROJ, events)
kinds = [e["kind"] for e in out]
check("the summary follows the answer it summarises", kinds[-1] == "turn", kinds)
check("...and nothing else was dropped or reordered",
      [k for k in kinds if k != "turn"] == [e["kind"] for e in events], kinds)

print("\nTwo turns get two lines, and the first one closes at the second's prompt")
two = events + [ev("user", T0 + 100, text="and again"),
                ev("tool", T0 + 101, tool="Read", title="Read"),
                ev("text", T0 + 110, text="ok")]
rows2 = [r for r in mission._turn_rows(PROJ, two) if r.get("kind") == "turn"]
check("two turns, two summaries", len(rows2) == 2, len(rows2))
check("the second counts only its own tools", rows2[1].get("tools") == 1, rows2[1].get("tools"))
check("...and its own clock", rows2[1].get("wall_s") == 10.0, rows2[1].get("wall_s"))

print("\nA turn that did nothing gets no line")
quiet = [ev("user", T0, text="hi"), ev("text", T0 + 1, text="hello")]
check("a plain answer with no tools is not decorated",
      not [r for r in mission._turn_rows(PROJ, quiet) if r.get("kind") == "turn"])

# ---------------------------------------------------------------- the priced half
print("\nThe CLI's own numbers, joined on the end of the turn")
turns.record(PROJ, "claude-opus-5", "high", 4321, 30.0, 44.0, 6, 0.1234, False)
priced = [r for r in mission._turn_rows(PROJ, events) if r.get("kind") == "turn"]
check("a banked row is not joined to a turn it does not belong to", not priced or "tokens" not in priced[0],
      priced[0] if priced else None)

now = time.time()
live_events = [
    ev("user", now - 40, text="go"),
    ev("tool", now - 30, tool="Bash", title="Bash"),
    ev("text", now - 5, text="done"),
]
turns.record(PROJ, "claude-opus-5", "high", 4321, 30.0, 44.0, 6, 0.1234, False)
got = [r for r in mission._turn_rows(PROJ, live_events) if r.get("kind") == "turn"]
check("a turn priced inside its own window is joined", got and got[0].get("tokens") == 4321,
      got[0] if got else None)
g = got[0] if got else {}
check("...with the CLI's cost", abs(float(g.get("cost") or 0) - 0.1234) < 1e-9, g.get("cost"))
check("...and the CLI's wall clock, not the feed's", g.get("wall_s") == 44.0 and g.get("wall_from") == "cli",
      (g.get("wall_s"), g.get("wall_from")))
check("...and the model that ran it", g.get("model") == "claude-opus-5", g.get("model"))

# ---------------------------------------------------------------- the store itself
print("\nThe store keeps every turn, including the small ones")
tiny = turns.record(PROJ, "m", "default", 3, 0.1, 0.4, 1, 0.0001, False)
check("a turn far too small to be a speed sample is still banked",
      any(x.get("ts") == tiny["ts"] for x in turns.for_project(PROJ)), tiny)
check("a window with nothing in it finds nothing", turns.at(PROJ, 1, 2) is None)
check("another project's turns are not this project's", turns.for_project("someone-else") == [])

# ---------------------------------------------------------------- one answer, drawn once
#
# The feed is served from a thread pool, and two panes poll the same transcript at once. Both
# extended the shared line index from the same offset, so a new answer was indexed twice and drawn
# twice — same text, same timestamp — until the backend restarted.
print("\nAn answer is drawn once, however many polls read it at the same moment")
import json as _json         # noqa: E402
import shutil as _shutil     # noqa: E402
import tempfile as _tf       # noqa: E402
import threading as _th      # noqa: E402

_dir = Path(_tf.mkdtemp(prefix="feedrace-"))
_p = _dir / "race.jsonl"


def _line(i):
    return _json.dumps({"type": "assistant", "uuid": "u%d" % i,
                        "message": {"content": [{"type": "text", "text": "answer %d " % i + "x" * 200}]}}) + "\n"


def _race(first, more, threads):
    with open(_p, "w", encoding="utf-8") as f:
        f.writelines(_line(i) for i in range(first))
    mission._LINE_IDX.pop(str(_p), None)
    mission._useful_lines(_p, _p.stat().st_size)
    with open(_p, "a", encoding="utf-8") as f:
        f.writelines(_line(i) for i in range(first, first + more))
    size = _p.stat().st_size
    bar = _th.Barrier(threads)

    def go():
        bar.wait()
        mission._useful_lines(_p, size)
    ts = [_th.Thread(target=go) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return [o for o, _n in mission._LINE_IDX[str(_p)]["lines"]]


offs = _race(10, 20000, 8)
check("eight polls on 20,000 new lines index each line once", len(offs) == 20010, len(offs))
check("...in file order", all(a < b for a, b in zip(offs, offs[1:])))
twice = 0
for _ in range(40):          # the case that was seen: a thinking block and its answer, two panes
    o2 = _race(10, 2, 2)
    twice += len(o2) - len(set(o2))
check("two polls on a two-line answer, forty times: never twice", twice == 0, twice)
_st = mission._LINE_IDX[str(_p)]
_st["lines"] = _st["lines"] + _st["lines"][-2:]      # the index the running server was holding
_uu = [e.get("uuid") for e in mission._tail_entries(_p, kb=8)]
check("an index that already repeats a line still reads it once", _uu and len(_uu) == len(set(_uu)), _uu)
mission._LINE_IDX.pop(str(_p), None)
_shutil.rmtree(_dir, ignore_errors=True)

print("\n  %d passed, %d failed" % (ok, fail))
raise SystemExit(0 if not fail else 2)

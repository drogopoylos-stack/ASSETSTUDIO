# -*- coding: utf-8 -*-
"""The instant-window caches: each one is exact, and each one can be switched off.

These caches exist because the UI was measured (2026-09-20) waiting on work the backend had
already done: a rescan of 52 project folders behind `/workspace/tree` (662 ms) and
`/workspace/changes` (680 ms), a PATH search behind `/mission/agents` (796 ms), a re-read of a
386 MB transcript behind `/context` (1,343 ms), a glob of every forge record behind
`/engine/state` (316-552 ms).

A cache that answers wrongly would be worse than the wait, so every claim is checked here:

  * `Memo` answers again only while the stamp is identical, and `Ttl` only inside its window
  * `perf_fast = false` takes both out of the way completely
  * the spawn count reads each byte once, and still counts correctly when the file is appended to,
    truncated, or truncated AND regrown with different content (an edit-message rewind)
  * a half-written last line is not counted until it is complete
  * the project index finds a project, and `fresh=True` sees one that appeared a moment ago
  * the forge-record list notices a new record

Run:  backend/.venv/Scripts/python.exe backend/perf_test.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# POINT THE STUDIO'S DATA DIR AT A THROWAWAY **BEFORE** THE PACKAGE IS IMPORTED.
#
# Without this the test reads the REAL settings.json, so it checks whatever the user last chose in
# Settings rather than the code under it. That is not hypothetical: on 2026-10-06 `perf_fast` was
# switched off on this machine, every cache was therefore bypassed correctly, and NINE assertions
# failed — "a Memo answers from memory while the stamp is the same", "the switch is back on by
# default" — as though the caches were broken. The one real finding was that the test could not tell
# a switched-off feature from a bug, and a suite that cries wolf when a feature is off is a suite
# people learn to ignore. The other caches' tests (boost_test, queue_guard_test, deepseek_test) have
# isolated their data dir all along; this one had not.
os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="studio-perf-data-")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import perf                       # noqa: E402
from asset_studio import mission                    # noqa: E402

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


TMP = Path(tempfile.mkdtemp(prefix="studio-perf-"))


def line(n_agents=0, text="hello"):
    """One transcript line; `n_agents` Agent tool_use blocks in it."""
    blocks = [{"type": "text", "text": text}]
    for i in range(n_agents):
        blocks.append({"type": "tool_use", "name": "Agent", "input": {"description": "d%d" % i}})
    return json.dumps({"message": {"role": "assistant", "content": blocks}}) + "\n"


# --- Memo: the same stamp answers again, a new stamp rebuilds --------------------------------
calls = {"n": 0}


def build():
    calls["n"] += 1
    return calls["n"]


m = perf.Memo()
a1 = m.get("k", ("stamp", 1), build)
a2 = m.get("k", ("stamp", 1), build)
a3 = m.get("k", ("stamp", 2), build)
ok("a Memo answers from memory while the stamp is the same", a1 == a2 == 1, (a1, a2))
ok("...and rebuilds when the stamp changes", a3 == 2, a3)

t = perf.Ttl(5.0)
calls["n"] = 0
b1 = t.get("k", build)
b2 = t.get("k", build)
ok("a Ttl answers from memory inside its window", b1 == b2 == 1, (b1, b2))
t.drop()
ok("...and drop() forgets it", t.get("k", build) == 2)


# --- the switch ------------------------------------------------------------------------------
class _FakeSettings:
    def __init__(self, on=True):
        self.on = on

    def get(self, key, default=None):
        if key == "perf_fast":
            return self.on
        return default


real_settings = perf.settings
try:
    perf.settings = _FakeSettings(False)
    calls["n"] = 0
    m2 = perf.Memo()
    m2.get("k", "s", build)
    m2.get("k", "s", build)
    ok("perf_fast = false: a Memo does the work every time", calls["n"] == 2, calls["n"])
    t2 = perf.Ttl(60.0)
    calls["n"] = 0
    t2.get("k", build)
    t2.get("k", build)
    ok("perf_fast = false: a Ttl does the work every time", calls["n"] == 2, calls["n"])
    ok("perf.fast() reports the switch", perf.fast() is False)
finally:
    perf.settings = real_settings
ok("...and the switch is back on by default", perf.fast() is True)


# --- the spawn count -------------------------------------------------------------------------
f = TMP / "session.jsonl"
f.write_text(line(1) + line(0) + line(2), encoding="utf-8")
n1 = mission._count_spawns(f)
ok("counts the spawns in a fresh transcript", n1 == 3, n1)

n2 = mission._count_spawns(f)
ok("...the same answer when nothing was written", n2 == 3, n2)

with open(f, "a", encoding="utf-8") as fh:
    fh.write(line(2))
n3 = mission._count_spawns(f)
ok("...and adds only what was appended", n3 == 5, n3)

# a half-written line must not be counted until it ends
with open(f, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Agent"}]}})[:-3])
n4 = mission._count_spawns(f)
ok("a half-written last line is not counted yet", n4 == 5, n4)
with open(f, "a", encoding="utf-8") as fh:
    fh.write("]}}\n")           # completes the line cut three characters short above
n5 = mission._count_spawns(f)
ok("...and is counted once it is complete", n5 == 6, n5)

# truncation: an edit-message rewind cuts the file back
f.write_text(line(1), encoding="utf-8")
n6 = mission._count_spawns(f)
ok("a shorter file is counted again from zero", n6 == 1, n6)

# truncate, then regrow LONGER with different content: the boundary check must catch it
f.write_text(line(1) + line(0) + line(2), encoding="utf-8")
mission._count_spawns(f)                                   # remember the offset
f.write_text(line(0) + line(0) + line(0) + line(0), encoding="utf-8")
n7 = mission._count_spawns(f)
ok("a rewritten file of the same or greater length is not trusted", n7 == 0, n7)

# with the switch off there is no memory at all, and the answer is still right
try:
    perf.settings = _FakeSettings(False)
    f.write_text(line(3), encoding="utf-8")
    ok("perf_fast = false still counts correctly", mission._count_spawns(f) == 3)
finally:
    perf.settings = real_settings


# --- the tail of a transcript, read backwards ---------------------------------------------------
#
# The feed used to ask for an index of the WHOLE file and then throw away all but the end. That is
# why the first chat opened after a restart was slow: measured 6.6 s on a project holding a 1 GB
# transcript, and 1,418 ms for the index of that file alone. Reading backwards costs the tail only
# — but it must pick EXACTLY the same lines, so it is checked against the old index here, with the
# chunk size cut to a few bytes so that every line crosses a boundary.
def old_want(p, size, budget):
    idx = mission._useful_lines(p, size)
    want, total = [], 0
    for off, n in reversed(idx):
        if want and off >= want[-1][0]:
            continue
        want.append((off, n))
        total += n
        if total >= budget:
            break
    want.reverse()
    return want


tail_f = TMP / "tail.jsonl"
big = "x" * (mission._MAX_ENTRY_BYTES + 10)                      # oversized...
img = json.dumps({"t": "img", "d": "data:image/png;base64," + big}) + "\n"   # ...and an image: ballast
body = "".join(json.dumps({"i": i, "pad": "p" * (i * 7)}) + "\n" for i in range(40))
tail_f.write_text(body[:len(body) // 2] + img + body[len(body) // 2:], encoding="utf-8")
with open(tail_f, "a", encoding="utf-8") as fh:
    fh.write('{"half": "written"')                               # no newline: still being written

real_chunk = mission._TAIL_CHUNK
same_all, sizes = True, []
try:
    for chunk in (7, 37, 512, 1 << 20):                          # every line crosses a boundary
        mission._TAIL_CHUNK = chunk
        for kb in (1, 2, 8, 64):
            size = tail_f.stat().st_size
            got = mission._tail_useful(tail_f, size, kb * 1024)
            mission._LINE_IDX.pop(str(tail_f), None)
            want = old_want(tail_f, size, kb * 1024)
            if got != want:
                same_all = False
                print("      chunk=%s kb=%s got %s want %s" % (chunk, kb, got[:3], want[:3]))
            sizes.append(len(got))
finally:
    mission._TAIL_CHUNK = real_chunk
ok("the backward tail picks exactly the lines the whole-file index picked", same_all, sizes[:4])
ok("...and it skips the ballast and the half-written last line",
   max(sizes) == 40 and all(s <= 40 for s in sizes), sizes)
ok("an empty file gives nothing", mission._tail_useful(TMP / "nope.jsonl", 0, 1024) == [])


# --- what survives a restart ---------------------------------------------------------------------
#
# The first click into a project after a restart was the slow one: 6.7 s on a workspace holding
# 2.5 GB across 144 transcripts, because every count, bill and scan position died with the process.
# These two keep them. A new INSTANCE stands in for a new process — it shares nothing in memory.
from asset_studio import config                     # noqa: E402

real_data = config.DATA_DIR
try:
    config.DATA_DIR = TMP
    built = {"n": 0}

    def slow():
        built["n"] += 1
        return {"bill": 42, "tools": ["Read", "Edit"]}

    d1 = perf.DiskMemo("t_ledgers", save_every=0.0)
    v1 = d1.get("file|10|99", slow)
    d1.save(force=True)
    d2 = perf.DiskMemo("t_ledgers", save_every=0.0)          # a "restart"
    v2 = d2.get("file|10|99", slow)
    ok("a DiskMemo answers after a restart without doing the work", built["n"] == 1 and v1 == v2, built["n"])
    d2.get("file|11|99", slow)
    ok("...and a changed file (a new key) is work again", built["n"] == 2, built["n"])

    cur_f = TMP / "parent.jsonl"
    cur_f.write_text("".join('{"i": %d}\n' % i for i in range(50)), encoding="utf-8")
    size = cur_f.stat().st_size
    c1 = perf.Cursor("t_scan", save_every=0.0)
    c1.put(str(cur_f), size, [{"agent": "a1"}])
    c1.save(force=True)
    c2 = perf.Cursor("t_scan", save_every=0.0)               # a "restart"
    got = c2.get(str(cur_f), size)
    ok("a Cursor returns the saved place after a restart", got == (size, [{"agent": "a1"}]), got)

    with open(cur_f, "a", encoding="utf-8") as fh:           # the file grew: the place still holds
        fh.write('{"i": 50}\n')
    grown = cur_f.stat().st_size
    ok("...and it still holds when the file only grew",
       (perf.Cursor("t_scan").get(str(cur_f), grown) or ("", ""))[0] == size)

    cur_f.write_text('{"i": 0}\n', encoding="utf-8")          # truncated
    ok("a shorter file is not continued", perf.Cursor("t_scan").get(str(cur_f), cur_f.stat().st_size) is None)

    cur_f.write_text("".join('{"x": %d}\n' % i for i in range(80)), encoding="utf-8")   # rewritten, longer
    ok("a rewritten file of greater length is not continued either",
       perf.Cursor("t_scan").get(str(cur_f), cur_f.stat().st_size) is None)

    perf.settings = _FakeSettings(False)
    built["n"] = 0
    d3 = perf.DiskMemo("t_ledgers", save_every=0.0)
    d3.get("file|10|99", slow)
    ok("perf_fast = false ignores what was saved", built["n"] == 1, built["n"])
    ok("...and a Cursor offers nothing", perf.Cursor("t_scan").get(str(cur_f), 10) is None)
finally:
    perf.settings = real_settings
    config.DATA_DIR = real_data


# --- the light project index -----------------------------------------------------------------
home = TMP / "projects"
(home / "c--tmp-demo").mkdir(parents=True)
(home / "c--tmp-demo" / "s1.jsonl").write_text(
    json.dumps({"cwd": str(TMP), "type": "user"}) + "\n", encoding="utf-8")

real_dir = mission.claude_projects_dir
try:
    mission.claude_projects_dir = lambda: home            # type: ignore[assignment]
    mission._IDX_ALL.drop()
    mission._IDX_ONE.drop()
    rows = mission.project_index()
    ok("the index finds a project folder", any(r["id"] == "c--tmp-demo" for r in rows), rows)
    row = [r for r in rows if r["id"] == "c--tmp-demo"][0]
    ok("...with the working folder from the transcript head", row["path"].lower() == str(TMP).lower(), row)

    (home / "c--tmp-second").mkdir()
    (home / "c--tmp-second" / "s1.jsonl").write_text(
        json.dumps({"cwd": str(TMP), "type": "user"}) + "\n", encoding="utf-8")
    # STRICTLY NEWER. Both folders name the same working folder, so the index keeps the newer
    # one - and two writes inside one clock tick (15.6 ms on Windows) tie, and the first stays.
    # That made this check fail about one run in three.
    _t2 = (home / "c--tmp-demo" / "s1.jsonl").stat().st_mtime + 2
    os.utime(home / "c--tmp-second" / "s1.jsonl", (_t2, _t2))
    cached = mission.project_index()
    ok("a folder made a moment ago is not in the cached list", len(cached) == len(rows), len(cached))
    now = mission.project_index(fresh=True)
    ok("...and fresh=True sees it, which is what the path check uses",
       any(r["id"] == "c--tmp-second" for r in now), [r["id"] for r in now])
finally:
    mission.claude_projects_dir = real_dir                # type: ignore[assignment]
    mission._IDX_ALL.drop()
    mission._IDX_ONE.drop()


# --- the forge-record list ---------------------------------------------------------------------
from asset_studio import engine                      # noqa: E402

live_dir = TMP / "live"
(live_dir / "proj-1").mkdir(parents=True)
(live_dir / "proj-1" / "forge-1.json").write_text("{}", encoding="utf-8")
real_live = engine._LIVE_DIR
try:
    engine._LIVE_DIR = live_dir                       # type: ignore[assignment]
    engine._RECORDS_MEMO.drop()
    ok("the record list finds one record", len(engine._forge_records()) == 1)
    time.sleep(0.02)
    (live_dir / "proj-1" / "forge-2.json").write_text("{}", encoding="utf-8")
    ok("...and notices a new record in a folder it already listed",
       len(engine._forge_records()) == 2, len(engine._forge_records()))
finally:
    engine._LIVE_DIR = real_live                      # type: ignore[assignment]
    engine._RECORDS_MEMO.drop()

shutil.rmtree(TMP, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, len(fails)))
for f_ in fails:
    print("  - " + f_)
sys.exit(1 if fails else 0)

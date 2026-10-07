# -*- coding: utf-8 -*-
"""Jobs, the test runner, and touching code safely.

The riskiest part of this module is not running a subprocess — it is READING what came back. The
thirty-one suites in this repository print their results in four different dialects, and a runner
that silently mis-parses one of them reports a green run over a red suite, which is worse than not
having a runner. So most of what follows is real output from each dialect, verbatim.

The second risk is `edit`, which writes to the user's source. Every refusal it makes is checked
here, including the one that matters most: a change that breaks the parse is put back.

Run:  backend/.venv/Scripts/python.exe backend/agent_tools_test.py
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import agent_tools as T          # noqa: E402

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


def eq(name, got, want):
    ok(name, got == want, "got %r, wanted %r" % (got, want))


# ---------------------------------------------------------------------------- the four dialects
print("Four dialects, one shape")

# C — the newer frontend suites AND every backend suite.
r = T._parse_suite("\n  119 passed, 0 failed\n", "", 0)
eq("C: passed", r["passed"], 119)
eq("C: failed", r["failed"], 0)
ok("C: verdict", r["ok"])

r = T._parse_suite("\n  40 passed, 2 failed\n  FAIL  a thing\n  FAIL  another\n", "", 1)
eq("C: counts a failure", (r["passed"], r["failed"]), (40, 2))
eq("C: and names them", r["failures"], ["a thing", "another"])
ok("C: verdict is red", not r["ok"])

# Five backend suites add a skipped count; four of them exit 2 rather than 1.
r = T._parse_suite("\n  146 passed, 0 failed, 0 skipped\n", "", 0)
eq("C: skipped is read", (r["passed"], r["failed"], r["skipped"]), (146, 0, 0))
ok("exit 2 is a failure, not a crash", not T._parse_suite("\n  1 passed, 1 failed\n", "", 2)["ok"])

# A — kit, ops, model. No per-check output; one summary line, a different wording.
r = T._parse_suite("129 checks passed\n", "", 0)
eq("A: passed", r["passed"], 129)
eq("A: no failures", r["failed"], 0)
r = T._parse_suite("48 checks passed, 3 FAILED\n  FAIL one\n", "", 1)
eq("A: counts the failures", (r["passed"], r["failed"]), (48, 3))
# Dialect A puts ONE space after FAIL where the others put two.
eq("A: one space after FAIL still parses", r["failures"], ["one"])

# B — the odd one out. Nothing on stdout when it fails, a total rather than a pass count, and
# `x` instead of `FAIL`. Miss this and four suites report as unparseable.
r = T._parse_suite("", "\nFAILED 3 of 21\n  x the first\n  x the second\n", 1)
eq("B: derives passed from the total", (r["passed"], r["failed"]), (18, 3))
eq("B: reads the failing lines off STDERR", r["failures"], ["the first", "the second"])
ok("B: verdict is red", not r["ok"])
r = T._parse_suite("shading: 26 checks pass\n", "", 0)
eq("B: the success line is a different sentence", (r["passed"], r["failed"]), (26, 0))
r = T._parse_suite("drop folder: 38 checks pass\n", "", 0)
eq("B: ...whatever the label is", r["passed"], 38)

# D — per-check lines with a C-shaped summary. The summary wins; the PASS lines are noise.
r = T._parse_suite("  PASS  a\n  PASS  b\n  FAIL  c\n\n  2 passed, 1 failed\n", "", 1)
eq("D: the summary is authoritative", (r["passed"], r["failed"]), (2, 1))
eq("D: only the failure is carried back", r["failures"], ["c"])

# A suite that prints nothing recognisable must not be reported as zero passes — that reads as a
# green empty run. `None` says "it did not tell me".
r = T._parse_suite("SKIP - no three.js build here.\n", "", 0)
eq("silence is not zero", r["passed"], None)
ok("...but the exit code still decides", r["ok"])

# ---------------------------------------------------------------------------- what can be run
print("\nIt knows what there is to run")
s = T.suites()
ok("frontend suites found", len(s["frontend"]) >= 12, len(s["frontend"]))
ok("backend suites found", len(s["backend"]) >= 15, len(s["backend"]))
ok("every frontend entry is an npm script", all(n.startswith("test:") for n in s["frontend"]))
ok("every backend entry is a file", all(n.endswith("_test.py") for n in s["backend"]))
# It drives the real CLI and takes minutes; a default "run everything" that waits for it is a
# runner nobody uses twice.
eq("the slow one is excluded by name", s["excluded"], ["keeper_live_test.py"])
ok("...and it is still listed as runnable", "keeper_live_test.py" in s["backend"])
bad = T.run_tests("no_such_test.py")
ok("an unknown suite is refused", not bad["ok"] and "no such suite" in bad["error"])
ok("...and it says what there is instead", "frontend" in bad and "backend" in bad)

# ---------------------------------------------------------------------------- jobs
print("\nA job answers at once and finishes later")
started = T.start_job("demo", "a demo", lambda jid: (T.append(jid, "working\n"), {"ok": True, "n": 7})[1])
jid = started["job"]
ok("an id comes back immediately", bool(jid) and started["ok"])
ok("...with somewhere to poll", started["poll"].endswith(jid))
for _ in range(100):
    if not T.job(jid)["running"]:
        break
    time.sleep(0.02)
j = T.job(jid)
ok("it finished", not j["running"])
eq("...and kept the result", (j["result"] or {}).get("n"), 7)
ok("the log is not in the way by default", "log" not in j and j["log_bytes"] > 0)
ok("...but can be asked for", "working" in T.job(jid, log=True)["log"])

# A job that throws must record it. A job that dies silently is worse than one that reports.
died = T.start_job("demo", "a bad one", lambda jid: (_ for _ in ()).throw(ValueError("boom")))
for _ in range(100):
    if not T.job(died["job"])["running"]:
        break
    time.sleep(0.02)
d = T.job(died["job"])
ok("a thrown job is recorded, not lost", not d["ok"] and "boom" in d["error"])
ok("an unknown job says so", not T.job("nope-123")["ok"])
ok("the list shows them", any(x["id"] == jid for x in T.jobs()["jobs"]))

# ---------------------------------------------------------------------------- sha
print("\nA fingerprint, without the contents")
me = "backend/agent_tools_test.py"
h = T.sha(me)
ok("it reads a real file", h["ok"] and len(h["sha256"]) == 64)
ok("...and reports the shape", h["bytes"] > 100 and h["lines"] > 10)
ok("the contents are NOT in the answer", "content" not in h and "text" not in h)
eq("twice is the same", T.sha(me)["sha256"], h["sha256"])
ok("a missing file says so", not T.sha("backend/nope.py")["ok"])
# The fence. An endpoint that reads and writes files needs one.
ok("outside the repository is refused", not T.sha("../../../../etc/passwd")["ok"])
ok("...and an absolute path outside too", not T.sha(str(Path(tempfile.gettempdir()) / "x.txt"))["ok"])

# ---------------------------------------------------------------------------- validate
print("\nDoes it still hold together")
tmpdir = Path(T.ROOT) / "data" / "tmp"
tmpdir.mkdir(parents=True, exist_ok=True)
good = tmpdir / "t_valid.py"
bad_py = tmpdir / "t_broken.py"
good_json = tmpdir / "t_valid.json"
bad_json = tmpdir / "t_broken.json"
io.open(str(good), "w", encoding="utf-8").write("x = 1\n")
io.open(str(bad_py), "w", encoding="utf-8").write("def f(:\n")
io.open(str(good_json), "w", encoding="utf-8").write('{"a": 1}')
io.open(str(bad_json), "w", encoding="utf-8").write('{"a": }')
rel = lambda p: str(p.relative_to(T.ROOT)).replace("\\", "/")     # noqa: E731

ok("valid python", T.validate(rel(good))["ok"])
v = T.validate(rel(bad_py))
ok("broken python is caught", not v["ok"] and v["diagnostics"])
ok("...with a line number", any(":1:" in d or ":2:" in d for d in v["diagnostics"]), v["diagnostics"])
ok("valid json", T.validate(rel(good_json))["ok"])
ok("broken json is caught", not T.validate(rel(bad_json))["ok"])
u = T.validate("README.md")
ok("an unchecked kind says so plainly", u["ok"] and u["checker"] == "none" and "no checker" in u["note"])

# ---------------------------------------------------------------------------- edit
print("\nA narrow edit, and every way it refuses")
target = tmpdir / "t_edit.py"
ORIG = "a = 1\nb = 2\nc = 1\n"
io.open(str(target), "w", encoding="utf-8").write(ORIG)
t = rel(target)

r = T.edit(t, "b = 2", "b = 99")
ok("a unique anchor is replaced", r["ok"])
eq("...exactly once", io.open(str(target), encoding="utf-8").read(), "a = 1\nb = 99\nc = 1\n")
ok("...and it hands back the new sha", len(r["sha256"]) == 64)
ok("...having checked it still parses", r["validate"]["ok"])

# The anchor that appears twice is the dangerous one: a blind replace would pick the first and
# look like it worked.
r = T.edit(t, "= 1", "= 5")
ok("an ambiguous anchor is refused", not r["ok"] and r["occurrences"] == 2)
eq("...and nothing was written", io.open(str(target), encoding="utf-8").read(), "a = 1\nb = 99\nc = 1\n")
r = T.edit(t, "nowhere", "x")
ok("a missing anchor is refused", not r["ok"] and r["occurrences"] == 0)
r = T.edit(t, "", "x")
ok("an empty anchor is refused", not r["ok"])

now = T.sha(t)["sha256"]
ok("the right sha is accepted", T.edit(t, "b = 99", "b = 100", expect_sha=now)["ok"])
r = T.edit(t, "b = 100", "b = 101", expect_sha="0" * 64)
ok("a stale sha is refused", not r["ok"] and "changed since you read it" in r["error"])
eq("...and nothing was written", io.open(str(target), encoding="utf-8").read(), "a = 1\nb = 100\nc = 1\n")

# THE ONE THAT MATTERS. An edit that breaks the file is put back, because the alternative is
# handing the user a source tree that no longer parses and a report that says "ok".
r = T.edit(t, "b = 100", "def f(:")
ok("an edit that breaks the parse fails", not r["ok"] and r.get("rolled_back"))
eq("...and the file is exactly as it was", io.open(str(target), encoding="utf-8").read(),
   "a = 1\nb = 100\nc = 1\n")
ok("outside the repository is refused", not T.edit("../../x.py", "a", "b")["ok"])

# ---------------------------------------------------------------------------- the sidecar
print("\nA saved document, read with nothing open")
side = tmpdir / "t_asset.edits.json"
io.open(str(side), "w", encoding="utf-8").write(json.dumps({
    "version": 1, "asset": "rock", "params": {"bumps": 3}, "bones": [], "clips": [],
    "parts": {"hull": {"pos": [0, 1, 0]}, "fin": {"hidden": True}},
    "mods": [{"op": "subsurf", "target": "hull"}, {"op": "weld", "off": True}],
    "verts": [{"mesh": "hull", "at": [0, 1, 0], "to": [0, 2, 0]},
              {"mesh": "hull", "at": [1, 1, 0], "to": [1, 2, 0]},
              {"mesh": "fin", "at": [0, 0, 1], "to": [0, 0, 2]}],
}))
d = T.sidecar_read(rel(side))
ok("it reads", d["ok"])
eq("parts", d["parts"], 2)
eq("...and how many are hidden", d["hidden"], 1)
eq("vertex edits", d["verts"], 3)
# Per mesh, because "which part did I edit" is the question actually being asked.
eq("...grouped by mesh", d["verts_by_mesh"], {"hull": 2, "fin": 1})
eq("modifiers, with the switched-off one marked", [m["off"] for m in d["mods"]], [False, True])
eq("...and the whole-asset one named", d["mods"][1]["target"], "(whole asset)")
ok("the raw document is NOT returned", "verts_raw" not in d and not isinstance(d.get("mods"), str))
ok("a file that is not JSON says so", not T.sidecar_read(rel(bad_json))["ok"])
ok("a missing sidecar says so", not T.sidecar_read("data/tmp/nope.edits.json")["ok"])

for f in (good, bad_py, good_json, bad_json, target, side):
    try:
        os.unlink(str(f))
    except OSError:
        pass

# ---------------------------------------------------------------------------- the switches
print("\nOff is off")
from asset_studio.config import DEFAULT_SETTINGS      # noqa: E402
eq("the debugger is off on a fresh machine", DEFAULT_SETTINGS.get("cc_debugger"), False)
eq("...and so are the code tools", DEFAULT_SETTINGS.get("cc_code_tools"), False)
eq("...and edit mode", DEFAULT_SETTINGS.get("cc_vertedit"), False)

from asset_studio import cc_session as C              # noqa: E402
ok("the debugger note exists and is short", 60 < len(C._debugger_note()) // 4 < 400)
ok("the code-tools note exists and is short", 60 < len(C._code_tools_note()) // 4 < 500)
cat = C.agent_notes_catalog(str(T.ROOT))
keys = {r["key"] for r in cat["notes"]}
for k in ("cc_debugger", "cc_code_tools", "cc_vertedit"):
    ok("%s has a row in Settings" % k, k in keys)
    row = [r for r in cat["notes"] if r["key"] == k][0]
    ok("...priced" % (), row["tokens"] > 0, row["tokens"])
    ok("...explained", len(row["why"]) > 40)
    ok("...and off by default", row["default"] is False)

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)

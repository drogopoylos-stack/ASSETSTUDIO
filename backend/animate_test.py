# -*- coding: utf-8 -*-
"""The gait report: which foot, when, and by how much.

The pictures are the easy half. The half that has to be right is the reading — because the whole
claim of this feature is that it names the fault before you open the sheet, and a report that
cries wolf is worse than no report at all.

The case that decides the design is the LAST one here: in a cycle played in place the world does
not move, so a planted foot MUST slide backwards — that is what makes it read as walking. A tool
that flagged that would be wrong on every in-place animation ever made, which is most of them. So
the question "did the root travel" is asked first and everything else depends on the answer.

Run:  backend/.venv/Scripts/python.exe backend/animate_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import live_animate as A          # noqa: E402

passed = 0
fails = []
TOL = 0.02


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


def has(name, findings, needle):
    hit = [f for f in findings if needle in f]
    ok(name, bool(hit), "no finding contains %r; got %r" % (needle, findings))
    return hit[0] if hit else ""


def none(name, findings, needle):
    ok(name, not [f for f in findings if needle in f],
       "a finding wrongly contains %r: %r" % (needle, findings))


def foot(gap, at):
    """One tracked part at one moment. `down` is computed the way the page computes it."""
    return {"meshes": 1, "bottom": gap, "gap": gap, "down": -TOL <= gap <= TOL, "at": list(at)}


def row(parts, root):
    return {"ground": 0.0, "tolerance": TOL, "parts": parts, "root": list(root), "missing": []}


# ---------------------------------------------------------------- which moments to sample
print("Choosing the moments")
eq("an explicit list is honoured", A._times([0, 0.5, 1.0], 0, 0), [0.0, 0.5, 1.0])
eq("...sorted and de-duplicated", A._times([1.0, 0, 0.5, 0], 0, 0), [0.0, 0.5, 1.0])
t8 = A._times(None, 8, 1.0)
eq("a count spreads over the duration", len(t8), 8)
eq("...starting at zero", t8[0], 0.0)
# A loop's first and last pose are the same one. Showing it twice wastes a panel and reads as a
# stutter in the strip.
ok("...and stopping BEFORE the end, not on it", t8[-1] < 1.0, t8[-1])
eq("...evenly", [round(x, 3) for x in t8[:3]], [0.0, 0.125, 0.25])
ok("a silly count is clamped", len(A._times(None, 500, 1.0)) <= 24, len(A._times(None, 500, 1.0)))
ok("...at both ends", len(A._times(None, 1, 1.0)) >= 2)

print("\nFinding the feet by name")
parts = [{"name": "head"}, {"name": "footL"}, {"name": "footR"}, {"name": "tail"},
         {"name": "toeL_1"}, {"name": "body"}]
got = A._auto_feet(parts)
ok("it finds the feet", "footL" in got and "footR" in got, got)
ok("...and a toe", "toeL_1" in got, got)
ok("...and leaves the head alone", "head" not in got and "body" not in got, got)
eq("nothing to find is nothing", A._auto_feet([{"name": "cube"}]), [])

# ---------------------------------------------------------------- a walk with root motion
print("\nA walk that travels: a planted foot must stay put")
clean = [
    row({"footL": foot(0.00, (0, 0)), "footR": foot(0.30, (0, 0.5))}, (0, 1, 0.0)),
    row({"footL": foot(0.00, (0, 0)), "footR": foot(0.00, (0, 1.0))}, (0, 1, 0.5)),
    row({"footL": foot(0.30, (0, 1.5)), "footR": foot(0.00, (0, 1.0))}, (0, 1, 1.0)),
    row({"footL": foot(0.00, (0, 2.0)), "footR": foot(0.00, (0, 1.0))}, (0, 1, 1.5)),
]
r = A._gait(clean, [0, 0.25, 0.5, 0.75], ["footL", "footR"], TOL)
ok("the root travelled", r["root"]["travel"] > 1.0, r["root"])
eq("...so it is not an in-place cycle", r["root"]["in_place"], False)
none("no foot is called floating", r["findings"], "FLOATING")
none("...or sinking", r["findings"], "SINKING")
# footR is down at 0.25, 0.5 and 0.75 and never moves: 1.0 all three times. Correct.
none("...or sliding", r["findings"], "FOOT SLIDE")
eq("footR was down three times", r["feet"]["footR"]["frames_down"], 3)
eq("...and travelled nothing while it was", r["feet"]["footR"]["slide"], 0.0)

print("\nThe same walk, with the back foot skating")
skate = [
    row({"footL": foot(0.00, (0, 0)), "footR": foot(0.00, (0, 1.0))}, (0, 1, 0.0)),
    row({"footL": foot(0.00, (0, 0)), "footR": foot(0.00, (0, 1.2))}, (0, 1, 0.5)),
    row({"footL": foot(0.00, (0, 0)), "footR": foot(0.00, (0, 1.4))}, (0, 1, 1.0)),
]
r = A._gait(skate, [0, 0.3, 0.6], ["footL", "footR"], TOL)
f = has("the slide is named", r["findings"], "FOOT SLIDE")
ok("...it names the foot", "footR" in f, f)
ok("...and how far", "0.400" in f, f)
ok("...and between which two times", "0.00" in f and "0.60" in f, f)
eq("the number is on the record too", r["feet"]["footR"]["slide"], 0.4)
eq("...with the window", r["feet"]["footR"]["slide_between"], [0, 0.6])
none("the planted foot is not accused", r["findings"], "footL travels")

# ---------------------------------------------------------------- floating and sinking
print("\nA foot that never lands, and one that goes through the floor")
air = [row({"footL": foot(0.042, (0, 0)), "footR": foot(0.0, (0, 0))}, (0, 1, i * 0.5))
       for i in range(4)]
r = A._gait(air, [0, 0.25, 0.5, 0.75], ["footL", "footR"], TOL)
f = has("floating is named", r["findings"], "FLOATING")
ok("...with the foot", "footL" in f, f)
ok("...and the gap", "0.042" in f, f)
eq("...and it is on the record", r["feet"]["footL"]["closest"], 0.042)
eq("...having never been down", r["feet"]["footL"]["frames_down"], 0)

sink = [row({"footR": foot(0.0, (0, 0))}, (0, 1, 0)),
        row({"footR": foot(-0.055, (0, 0))}, (0, 1, 0.5)),
        row({"footR": foot(0.0, (0, 0))}, (0, 1, 1.0))]
r = A._gait(sink, [0, 0.4, 0.8], ["footR"], TOL)
f = has("sinking is named", r["findings"], "SINKING")
ok("...with the depth", "0.055" in f, f)
ok("...and the exact moment", "t=0.40" in f, f)

# A DIP INSIDE THE TOLERANCE IS CONTACT, NOT A FAULT. The tolerance exists because a foot resting
# on the floor lands a hair either side of it; a report that cried about every one of those would
# be ignored within a day, which costs more than the report is worth.
graze = [row({"footR": foot(-0.018, (0, 0))}, (0, 1, 0))]
r2 = A._gait(graze, [0], ["footR"], TOL)
none("a dip within tolerance is left alone", r2["findings"], "SINKING")
ok("...and still counts as touching", r2["feet"]["footR"]["frames_down"] == 1)

# ---------------------------------------------------------------- support
print("\nWhen nothing is holding it up")
hop = [row({"footL": foot(0.0, (0, 0)), "footR": foot(0.0, (0, 0))}, (0, 1, 0)),
       row({"footL": foot(0.4, (0, 0)), "footR": foot(0.4, (0, 0))}, (0, 1, 0.5)),
       row({"footL": foot(0.0, (0, 0)), "footR": foot(0.0, (0, 0))}, (0, 1, 1.0))]
r = A._gait(hop, [0, 0.3, 0.6], ["footL", "footR"], TOL)
f = has("an airborne frame is named", r["findings"], "NO SUPPORT")
ok("...at the right moment", "0.30" in f, f)
# A run HAS airborne frames. Reporting it as a fault would be wrong, so it is reported as a fact.
ok("...and it does not call it a fault", "only a fault if" in f, f)
eq("the record agrees", r["support"]["frames_with_nothing_down"], [0.3])

print("\nWhen the ground is simply in the wrong place")
adrift = [row({"footL": foot(1.4, (0, 0))}, (0, 1, i)) for i in range(3)]
r = A._gait(adrift, [0, 0.3, 0.6], ["footL"], TOL)
# THE FIRST LINE, because every other number below it is then meaningless. An agent that reads
# "floating by 1.4" and starts adjusting the ankle has been sent the wrong way.
ok("it goes first", "NOTHING EVER TOUCHES" in r["findings"][0], r["findings"][:2])
ok("...and blames the ground before the asset", "ground` is where you think" in r["findings"][0]
   or "ground" in r["findings"][0], r["findings"][0])

# ---------------------------------------------------------------- the case that decides it all
print("\nAn in-place cycle: sliding feet are the POINT, not the bug")
treadmill = [
    row({"footL": foot(0.0, (0, 0.0)), "footR": foot(0.4, (0, 0))}, (0, 1, 0)),
    row({"footL": foot(0.0, (0, -0.3)), "footR": foot(0.4, (0, 0))}, (0, 1, 0)),
    row({"footL": foot(0.0, (0, -0.6)), "footR": foot(0.4, (0, 0))}, (0, 1, 0)),
]
r = A._gait(treadmill, [0, 0.3, 0.6], ["footL", "footR"], TOL)
eq("the root did not travel", r["root"]["in_place"], True)
# The whole design turns on this line. A tool that flagged a treadmill foot would be wrong on
# nearly every walk cycle ever authored.
none("so the slide is NOT called a fault", r["findings"], "FOOT SLIDE")
f = has("...and it says why", r["findings"], "IN PLACE")
ok("...naming what to do instead", "Drive the root" in f, f)
eq("the slide is still measured", r["feet"]["footL"]["slide"], 0.6)

print("\n...but planted feet still have to agree with each other")
skew = [
    row({"footL": foot(0.0, (0, 0.0)), "footR": foot(0.0, (0, 0.0))}, (0, 1, 0)),
    row({"footL": foot(0.0, (0, -0.6)), "footR": foot(0.0, (0, -0.1))}, (0, 1, 0)),
]
r = A._gait(skew, [0, 0.4], ["footL", "footR"], TOL)
f = has("disagreeing feet are named", r["findings"], "FEET DISAGREE")
ok("...the fast one", "footL" in f, f)
ok("...and the slow one", "footR" in f, f)
ok("...and it says why it matters", "skates" in f, f)

# ---------------------------------------------------------------- a name that is not there
print("\nA part that does not exist")
r = A._gait([row({"footL": foot(0.0, (0, 0))}, (0, 1, 0))], [0], ["footL", "footZ"], TOL)
f = has("it says so", r["findings"], "NOT FOUND")
ok("...naming it", "footZ" in f, f)
ok("...and saying what to do", "track" in f, f)
eq("...and marks it on the record", r["feet"]["footZ"]["found"], False)
ok("the real foot is still measured", r["feet"]["footL"]["found"])

print("\nNothing at all does not throw")
eq("no rows", A._gait([], [], [], TOL)["findings"], [])
eq("no names", A._gait([row({}, (0, 0, 0))], [0], [], TOL)["findings"], [])

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)

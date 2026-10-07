# -*- coding: utf-8 -*-
"""The API reflection: what forge-ops.js exports, read out of the source.

The value of this endpoint is entirely that it cannot lie. A hand-written catalogue drifts the
first time somebody adds a tool and forgets, and a drifted catalogue is worse than none — it
teaches the agent a function that is not there. So these checks are about the parser telling the
truth against the REAL modules, not against a fixture: real signatures, real parameter names, real
defaults, and a cross-check that every name it advertises is in the shipped bundle.

Run:  backend/.venv/Scripts/python.exe backend/apireflect_test.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import apireflect as A          # noqa: E402

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


print("It reads the real modules")
d = A.reflect()
ok("ops is there", "ops" in d)
ok("...with a lot of functions", len(d["ops"]["functions"]) > 40, len(d["ops"]["functions"]))
ok("vertedit is there too", "vertedit" in d)
# The editor's own module is NOT in forge-ops.js. Saying so stops an agent importing it over HTTP
# and getting a 404 it cannot explain.
ok("...and says it is not served over HTTP", "not served" in d["vertedit"]["served_as"])

print("\nThe default answer is cheap")
size = len(json.dumps(d)) // 4
ok("under 800 tokens", size < 800, "%d tokens" % size)
ok("...and carries no signatures", "params" not in json.dumps(d))
ok("it says how to get more", "?name=" in d["how"])
ok("it hands over a paste-able import", "forge-ops.js" in d["import"])

print("\nA name gives the real signature")
e = A.reflect(name="applyVertEdits")["entries"][0]
eq("the name", e["name"], "applyVertEdits")
eq("the kind", e["kind"], "function")
# Real parameter names and real defaults, which is the whole reason to parse the source rather
# than enumerate the bundle: `fn.length` would give 2 and tell you nothing.
ok("real parameter names", "pos: Float32Array" in e["params"] and "verts: VertEdit[]" in e["params"])
ok("...including the defaults", "tol = VERT_TOL" in e["params"] and "faces = -1" in e["params"])
ok("...and the optional marker", "rest?:" in e["params"])
ok("a multi-line signature is read whole", "\n" not in e["params"])
ok("the return type survives being an object literal",
   e["returns"].startswith("{") and "orphans" in e["returns"], e["returns"][:60])
ok("the doc is one sentence", e["doc"].endswith(".") and len(e["doc"]) < 240, e["doc"][:80])

print("\nOps is the contract of makeOps(THREE)")
ops = A.reflect(name="Ops")["entries"][0]
eq("it is an interface", ops["kind"], "interface")
ok("it has the modelling tools", len(ops["members"]) > 25, len(ops["members"]))
body = " ".join(ops["members"])
for tool in ("merge(", "weld(", "skin(", "subsurf(", "bevel(", "check("):
    ok("...%s" % tool, tool in body)
ok("the default answer points at it", "makeOps" in d["note"])

# FOUND BY RUNNING IT, NOT BY READING IT. TypeScript erases an interface at build, so checking
# `Ops` against the JavaScript exports reported the single most useful entry in the answer as
# "not in the bundle" — which would teach an agent to distrust the one thing it should trust.
ok("asking for a TYPE does not report it missing",
   not (A.reflect(name="Ops").get("bundle") or {}).get("not_in_bundle"),
   A.reflect(name="Ops").get("bundle"))
ok("...and a real function is still checked",
   "not_in_bundle" not in (A.reflect(name="applyVerts").get("bundle") or {}))

print("\nSearch finds a tool by what it is called")
q = A.reflect(q="weld")
names = [x["name"] for x in q["entries"]]
ok("the free functions", "weldArrays" in names)
# THE ONE THAT WAS WRONG FIRST. `weld` is not a top-level export; it is a member of Ops. A search
# that only read names answered "no such tool" about a tool that exists.
ok("...and the Ops member", "Ops" in names, names)
opshit = [x for x in q["entries"] if x["name"] == "Ops"][0]
ok("...narrowed to the matching members", all("weld" in m.lower() for m in opshit["members"]),
   opshit["members"])
ok("a miss says so", A.reflect(name="nosuchthing")["found"] == 0)
ok("...and offers near misses", "did_you_mean" in A.reflect(name="applyVert"))

print("\nIt cannot advertise something that was never shipped")
b = d["bundle"]
ok("the bundle was read", isinstance(b, dict) and b.get("exports", 0) > 30, b)
ok("...and nothing is missing from it", not b.get("not_in_bundle"), b.get("not_in_bundle"))
# Every function the parser found in ops.ts must really be exported by forge-ops.js. This is the
# check that makes the endpoint trustworthy rather than merely plausible.
shipped = A._bundle_names()
# The default answer lists NAMES, which is the whole point of it being cheap.
parsed = set(d["ops"]["functions"]) | set(d["ops"]["consts"])
missing = sorted(parsed - (shipped or set()))
ok("every parsed ops export is in the bundle", not missing, missing)

print("\nThe parser survives the shapes the real file uses")
m = A.parse_module(A.SRC / "ops.ts")
by = {f["name"]: f for f in m["functions"]}
ok("a plain function", "faceNormal" in by)
ok("a generic-free multi-return", "weldArrays" in by and "merged" in by["weldArrays"]["returns"])
ok("a function taking a callback type",
   "isosurfaceArrays" in by and "=>" in by["isosurfaceArrays"]["params"])
ok("an arrow const is read as a function", any(f["name"] == "vertKey" for f in m["functions"])
   or "vertKey" in by)
ok("a plain const keeps its value", any(c["name"] == "VERT_TOL" and "1e-3" in c["value"]
                                        for c in m["consts"]),
   [c for c in m["consts"]])
ok("no export is nameless", all(f["name"] for f in m["functions"]))
ok("no signature leaked a comment", not any("//" in f["params"] for f in m["functions"]))
ok("no signature leaked a brace from the body",
   not any(f["params"].count("{") != f["params"].count("}") for f in m["functions"]))

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)

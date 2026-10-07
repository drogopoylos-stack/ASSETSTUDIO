# -*- coding: utf-8 -*-
"""What the code graph is allowed to be ABOUT.

Why this exists, measured on this machine before the fix: of 39,815 nodes, 27,820 (69.9%) were
`runtime/python/Lib` - the bundled CPython stdlib, idlelib included - and a further ~1,100 came
from `data/backups` and `data/tmp`, which are old copies of this very code. graph.json was 45 MB
instead of the ~2.4 MB the comment above `_GRAPH_CACHE` assumes, a cold parse held ~111 MB of the
backend resident, and BOOST's prefetch could answer with `_LoopBoundMixin` instead of the symbol the
user named, charge tokens for it, and report that it had saved a grep.

The cause is not a missing name in a list. `_git_ignored` asks GIT what a project calls output, and
`D:\\Asset Studio` is not a git repo - so it returns None and the walk falls back to names alone.
No name list can know that `runtime/` and `data/` here are the Studio's own install, because
`runtime` is a perfectly plausible name for a game's own source. PowderPeaks keeps its whole game in
`build/js/`, and this codebase already paid once for judging a folder by its name.

So the parent names its own trees (`_own_top_dirs` -> cfg `skip_top`), matched at the TOP LEVEL
only. What is checked here:

  * the parent only claims a top-level tree when it really is inside the workspace
  * the build's own _skip honours it: runtime/ and data/ out, backend/ and frontend/ in
  * a FILE named build.py is still source - a suffix-tolerant directory rule must not eat it
  * `dist.old` is deferred to the evidence check, not skipped on its name
  * `.graphifyignore` alone achieves the same, which is what makes the fix apply to a build started
    by a server that still holds the OLD build script in memory
  * `_collect` records what it refused in `excluded`, so the text scan agrees with the graph
  * the parent's skip lists and the embedded script's fallback copies have not drifted apart

Run:  backend/.venv/Scripts/python.exe backend/graph_scope_test.py
"""
import ast
import json
import sys
import tempfile
import types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asset_studio import graphify_index as gi                       # noqa: E402
from asset_studio.config import ROOT_DIR                            # noqa: E402

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


# ---------------------------------------------------------------------------
# The embedded build script imports graphify, which lives in the graphify venv, not this one. Stand
# the package up as modules whose every attribute is a no-op callable, so those imports resolve and
# this test does not have to track graphify's API.
# ---------------------------------------------------------------------------
class _Any(types.ModuleType):
    def __getattr__(self, name):
        return lambda *a, **k: None


for _name in ("graphify", "graphify.detect", "graphify.extract", "graphify.build",
              "graphify.cluster", "graphify.analyze", "graphify.report", "graphify.export"):
    sys.modules[_name] = _Any(_name)

# Everything before the graph is actually built. Exec'ing this gives the real _skip/_collect, so the
# rules are exercised as code rather than grepped for as text.
_HEAD = gi._BUILD_SRC.split("_SRC_EXT = _parse_exts()")[0]
_REAL_ARGV = sys.argv


def load_build(skip_top, argv_cfg=None):
    """Exec the build script's head with a given cfg, and hand back its namespace."""
    ns = {}
    sys.argv = ["-c", str(ROOT_DIR),
                argv_cfg if argv_cfg is not None else json.dumps(
                    {"always": sorted(gi._ALWAYS_SKIP), "maybe": sorted(gi._MAYBE_BUILT),
                     "skip_top": skip_top})]
    try:
        exec(compile(_HEAD, "<build>", "exec"), ns)
    finally:
        sys.argv = _REAL_ARGV
    return ns


print("\n-- the parent names its own trees, and only inside the workspace --")

own = gi._own_top_dirs(str(ROOT_DIR))
ok("the Studio root reports data/ and runtime/ as ours",
   set(own) == {"data", "runtime"}, own)

foreign = tempfile.mkdtemp(prefix="graph-scope-")
ok("an unrelated project reports nothing as ours", gi._own_top_dirs(foreign) == [], foreign)
ok("a SUBfolder of the Studio reports nothing as ours",
   gi._own_top_dirs(str(ROOT_DIR / "backend")) == [], gi._own_top_dirs(str(ROOT_DIR / "backend")))

ns = load_build(own)
ok("the cfg carries skip_top through to the build",
   ns["_SKIP_TOP"] == {"data", "runtime"}, ns["_SKIP_TOP"])

print("\n-- the build's own _skip, with the real rules --")
skip = ns["_skip"]


def skipped(rel):
    return skip(ROOT_DIR / rel, ROOT_DIR, [], None, ())


cases = [
    (r"runtime\python\Lib\asyncio\locks.py", True, "the bundled interpreter's stdlib"),
    (r"runtime\python\include\Python.h", True, "the bundled headers"),
    (r"data\backups\deepseek-panes-20261004\app.js", True, "an old copy of this code"),
    (r"data\tmp\deepseek-panes-dist\index.js", True, "a stale build under data/tmp"),
    (r"backend\asset_studio\boost.py", False, "THE SOURCE WE WANT"),
    (r"frontend\src\components\CostPanel.tsx", False, "THE SOURCE WE WANT"),
]
for rel, want, why in cases:
    ok("%-46s %s" % (rel.rsplit("\\", 1)[-1], "skipped" if want else "kept"), skipped(rel) == want,
       "wanted %s for %s" % (want, why))

# A suffix-tolerant DIRECTORY rule must not reach a file. `build.py` is somebody's source, and the
# first version of this rule would have skipped it.
ok("a file named build.py is still source",
   skipped(r"skills\img2threejs\scripts\build.py") is False)
ok("a folder named build-old is treated as maybe-built (deferred, not skipped outright)",
   ns["_maybe_built"](["build-old"]) is True)
ok("a folder named dist.old likewise", ns["_maybe_built"](["dist.old"]) is True)
ok("...but 'runtime' is not a build name, it is skipped for a different reason",
   ns["_maybe_built"](["runtime"]) is False)

print("\n-- .graphifyignore alone, which is what makes this work before a restart --")
# THE POINT: a server that has not been restarted still holds the old build script, which knows
# nothing about skip_top. It does read .graphifyignore. If this passes, the graph comes out clean
# even for builds started by the running server.
ign_path = ROOT_DIR / ".graphifyignore"
ok("the workspace has a .graphifyignore", ign_path.is_file(), ign_path)
if ign_path.is_file():
    ns_ign = load_build([])                      # skip_top deliberately EMPTY
    ignore = ns_ign["_load_ignore"](ROOT_DIR)
    ok("it parses to a non-empty pattern list", len(ignore) >= 2, ignore)
    for rel, why in ((r"runtime\python\Lib\asyncio\locks.py", "the bundled stdlib"),
                     (r"data\backups\thing\app.js", "an old copy under data/")):
        ok("with skip_top EMPTY, %s is still skipped" % rel.rsplit("\\", 1)[-1],
           ns_ign["_skip"](ROOT_DIR / rel, ROOT_DIR, ignore, None, ()) is True, why)
    ok("...and the real source is untouched by it",
       ns_ign["_skip"](ROOT_DIR / r"backend\asset_studio\boost.py", ROOT_DIR, ignore, None, ()) is False)

print("\n-- what the build refuses, the text scan must know it refused --")
# `_scan_files` reads coverage.json's `excluded` rather than working it out again, so a tree the
# build skipped but the scan still walks reports "the graph missed this file" about 27,000 stdlib
# symbols. This is that list being written.
tree = Path(tempfile.mkdtemp(prefix="graph-collect-"))
for rel in (r"runtime\x.py", r"data\y.py", r"src\z.py", r"frontend\dist.old\bundle.js"):
    p = tree / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x = 1\n", encoding="utf-8")
excluded, deferred = set(), []
found = ns["_collect"](tree, tree, {".py", ".js"}, None, excluded, deferred)
rels = {str(p.relative_to(tree)).replace("\\", "/") for p in found}
ok("collect found the real source", "src/z.py" in rels, rels)
ok("collect never walked runtime/", not any(r.startswith("runtime/") for r in rels), rels)
ok("collect never walked data/", not any(r.startswith("data/") for r in rels), rels)
ok("...and recorded both as excluded, so the scan skips them too",
   {"runtime", "data"} <= excluded, sorted(excluded))
ok("dist.old was DEFERRED to the evidence check rather than dropped blind",
   any(d[0] == "frontend/dist.old" for d in deferred), deferred)

print("\n-- the two copies of the skip lists have not drifted --")
# The parent passes its pair to the build, and the script keeps literals as a fallback for a
# hand-run. They are meant to be the same rule; nothing but this notices when they stop being.
ns_fb = load_build(None, argv_cfg="not json at all")     # forces the fallback branch
ok("the embedded fallback _ALWAYS_SKIP equals the parent's",
   ns_fb["_ALWAYS_SKIP"] == gi._ALWAYS_SKIP,
   sorted(ns_fb["_ALWAYS_SKIP"] ^ gi._ALWAYS_SKIP))
ok("the embedded fallback _MAYBE_BUILT equals the parent's",
   ns_fb["_MAYBE_BUILT"] == gi._MAYBE_BUILT,
   sorted(ns_fb["_MAYBE_BUILT"] ^ gi._MAYBE_BUILT))

print("\n-- the module and the embedded script are both valid source --")
ok("graphify_index.py parses",
   bool(ast.parse((Path(gi.__file__)).read_text(encoding="utf-8"))))
ok("_BUILD_SRC parses", bool(ast.parse(gi._BUILD_SRC)))

print("\n%d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAILED: %s" % f)
sys.exit(1 if fails else 0)

# -*- coding: utf-8 -*-
"""The tools an agent needs that are not about geometry: jobs, tests, and touching code safely.

Three ideas taken from the editor MCPs, each because it removes a specific waste:

  JOBS        Unity's `run_tests` returns a job id immediately and you poll it. Anything that
              takes longer than a few seconds should not hold an HTTP connection, and the backend
              must never block its event loop — so the work runs on a thread and the caller asks
              how it is going. The pattern is `setup_installer.py`'s, generalised.

  TESTS       Both Unity and Godot let an agent run the suite. We have thirty-one suites and no
              way for an agent to ask. The hard part is not running them, it is that they print
              in FOUR different dialects — so this parses all four into one shape and returns the
              failing lines rather than the whole log.

  CODE        Unity's `get_sha` is the quietly clever one: confirm a file has not changed without
              spending tokens reading it. Paired with a narrow edit that refuses when the sha has
              moved, it turns "read the whole file, edit it, hope" into something checkable.

Nothing here is on by default. Every entry point is gated by the caller.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = BACKEND / ".venv" / "bin" / "python"

# ---------------------------------------------------------------------------
# Jobs
#
# In memory on purpose. A job is a thing an agent started in this session and will poll within a
# minute or two; persisting it to SQLite would buy nothing and the real queue in jobs/queue.py is
# tied to a closed StageType enum that has nothing to do with running a test suite.

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_KEEP = 40                     # the last N, so a long session cannot grow the dict forever
_LOG_MAX = 24_000              # the trick from setup_installer: keep the TAIL, drop the middle


def _trim() -> None:
    """Oldest finished jobs first. A running job is never dropped."""
    with _LOCK:
        if len(_JOBS) <= _KEEP:
            return
        done = sorted((j for j in _JOBS.values() if not j["running"]),
                      key=lambda j: j.get("finished") or 0)
        for j in done[:len(_JOBS) - _KEEP]:
            _JOBS.pop(j["id"], None)


def append(job_id: str, text: str) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return
        j["log"] = (j["log"] + text)[-_LOG_MAX:]


def start_job(kind: str, label: str, fn: Callable[[str], dict]) -> dict:
    """Run `fn(job_id)` on a thread and hand back the id at once.

    `fn` returns the dict that becomes `result`. Anything it raises is recorded rather than lost:
    a job that dies silently is worse than one that reports a traceback.
    """
    job_id = kind + "-" + uuid.uuid4().hex[:8]
    rec = {"id": job_id, "kind": kind, "label": label, "running": True, "ok": None,
           "log": "", "result": None, "error": "", "started": time.time(), "finished": 0.0}
    with _LOCK:
        _JOBS[job_id] = rec

    def go() -> None:
        try:
            out = fn(job_id)
            with _LOCK:
                rec["result"] = out
                rec["ok"] = bool(out.get("ok", True)) if isinstance(out, dict) else True
        except Exception as ex:                              # noqa: BLE001 - reported, not raised
            with _LOCK:
                rec["ok"] = False
                rec["error"] = "%s: %s" % (type(ex).__name__, ex)
        finally:
            with _LOCK:
                rec["running"] = False
                rec["finished"] = time.time()
            _trim()

    threading.Thread(target=go, name=job_id, daemon=True).start()
    return {"ok": True, "job": job_id, "kind": kind, "label": label,
            "poll": "/api/tools/job/" + job_id}


def job(job_id: str, log: bool = False) -> dict:
    """How a job is going. The log is left OUT unless asked for — it is the bulky part and the
    parsed result is what a caller usually wants."""
    with _LOCK:
        rec = _JOBS.get(job_id)
        if not rec:
            return {"ok": False, "error": "no such job: " + job_id}
        out = {k: v for k, v in rec.items() if k != "log"}
    out["ok"] = True if rec["running"] else bool(rec["ok"])
    out["seconds"] = round((rec["finished"] or time.time()) - rec["started"], 2)
    if log:
        out["log"] = rec["log"]
    else:
        out["log_bytes"] = len(rec["log"])
    return out


def jobs(limit: int = 20) -> dict:
    with _LOCK:
        rows = sorted(_JOBS.values(), key=lambda j: j["started"], reverse=True)[:max(1, limit)]
        return {"ok": True, "jobs": [{"id": r["id"], "kind": r["kind"], "label": r["label"],
                                      "running": r["running"], "ok": r["ok"],
                                      "seconds": round((r["finished"] or time.time()) - r["started"], 2)}
                                     for r in rows]}


# ---------------------------------------------------------------------------
# Tests
#
# THE SUITES PRINT IN FOUR DIALECTS. Not a complaint — they were written at different times by
# different hands and each is readable on its own. But an agent asking "did it pass" should not
# have to know that, so all four are parsed into one shape here.
#
#   A  "%d checks passed"                     kit, ops, model
#   B  "FAILED %d of %d" on STDERR, and "<label>: %d checks pass" on success   shading, forgescript,
#                                                                              feedscroll, dropfolder
#   C  "  %d passed, %d failed"               the newer frontend suites, and every backend suite
#   D  per-check "  PASS  " lines plus a C-shaped summary                      openasset, backend
#
# The exit code is reliable everywhere — 0 pass, 1 or 2 fail — so it is the verdict, and the
# parsed counts are the detail.

_RE_C = re.compile(r"^\s*(\d+) passed, (\d+) failed(?:, (\d+) skipped)?", re.M)
_RE_A = re.compile(r"^(\d+) checks passed(?:, (\d+) FAILED)?", re.M)
_RE_B_FAIL = re.compile(r"^FAILED (\d+) of (\d+)\s*$", re.M)
_RE_B_OK = re.compile(r"^[\w ]+: (\d+) checks pass\s*$", re.M)
_RE_FAILLINE = re.compile(r"^ {2}(?:FAIL {1,2}|x )(.+)$", re.M)

# Excluded from a normal run because it drives the real Claude CLI and takes minutes; its own
# docstring says so. Ask for it by name if you really want it.
SLOW = {"keeper_live_test.py"}


def _parse_suite(out: str, err: str, rc: int) -> dict:
    """One shape out of four dialects. `passed` is None when a suite prints no count at all."""
    both = out + "\n" + err
    passed = failed = skipped = None

    m = _RE_C.search(both)
    if m:
        passed, failed = int(m.group(1)), int(m.group(2))
        skipped = int(m.group(3)) if m.group(3) else 0
    else:
        m = _RE_A.search(both)
        if m:
            passed = int(m.group(1))
            failed = int(m.group(2) or 0)
        else:
            m = _RE_B_FAIL.search(both)
            if m:
                failed, total = int(m.group(1)), int(m.group(2))
                passed = total - failed
            else:
                m = _RE_B_OK.search(both)
                if m:
                    passed, failed = int(m.group(1)), 0

    # The failing lines, which is the only part of a log worth carrying back.
    fails = [f.strip() for f in _RE_FAILLINE.findall(both)][:25]
    return {"ok": rc == 0, "exit": rc, "passed": passed, "failed": failed,
            "skipped": skipped, "failures": fails}


def suites() -> dict:
    """Every suite that can be run, without running one."""
    front = []
    try:
        pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
        front = sorted(k for k in (pkg.get("scripts") or {}) if k.startswith("test:"))
    except Exception:                                        # noqa: BLE001
        pass
    back = sorted(p.name for p in BACKEND.glob("*_test.py"))
    return {"ok": True,
            "frontend": front,
            "backend": back,
            "excluded": sorted(SLOW),
            "why_excluded": "drives the real CLI and takes minutes; name it explicitly to run it",
            "how": "POST /api/tools/tests {\"which\": \"frontend\" | \"backend\" | \"all\" | "
                   "\"test:verts\" | \"skills_test.py\"}"}


def _run_one(name: str, timeout: float) -> dict:
    """One suite, as a subprocess, with its own working directory."""
    t0 = time.time()
    if name.startswith("test:"):
        cwd, argv = FRONTEND, ["npm", "run", name]
    elif name.endswith("_test.py"):
        # Every backend suite does sys.path.insert(0, ".") and so must run FROM backend/.
        cwd, argv = BACKEND, [str(VENV_PY), name]
    else:
        return {"suite": name, "ok": False, "exit": -1, "error": "not a suite name"}
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, shell=(os.name == "nt"))
        got = _parse_suite(p.stdout or "", p.stderr or "", p.returncode)
    except subprocess.TimeoutExpired:
        got = {"ok": False, "exit": -1, "passed": None, "failed": None, "skipped": None,
               "failures": ["timed out after %ds" % int(timeout)]}
    except Exception as ex:                                  # noqa: BLE001
        got = {"ok": False, "exit": -1, "passed": None, "failed": None, "skipped": None,
               "failures": ["%s: %s" % (type(ex).__name__, ex)]}
    got["suite"] = name
    got["seconds"] = round(time.time() - t0, 1)
    return got


def run_tests(which: str = "all", timeout: float = 300.0, job_id: str = "") -> dict:
    """Run one suite, one side, or everything, and report counts rather than a log."""
    have = suites()
    names: list[str]
    w = (which or "all").strip()
    if w == "frontend":
        names = have["frontend"]
    elif w == "backend":
        names = [n for n in have["backend"] if n not in SLOW]
    elif w in ("all", ""):
        names = have["frontend"] + [n for n in have["backend"] if n not in SLOW]
    else:
        names = [n.strip() for n in w.split(",") if n.strip()]
        unknown = [n for n in names if n not in have["frontend"] and n not in have["backend"]]
        if unknown:
            return {"ok": False, "error": "no such suite: " + ", ".join(unknown),
                    "frontend": have["frontend"], "backend": have["backend"]}

    rows = []
    for n in names:
        if job_id:
            append(job_id, "running %s\n" % n)
        r = _run_one(n, timeout)
        rows.append(r)
        if job_id:
            append(job_id, "  %s  %s  (%ss)\n" % ("ok" if r["ok"] else "FAILED", n, r["seconds"]))

    bad = [r for r in rows if not r["ok"]]
    total_pass = sum(r["passed"] or 0 for r in rows)
    total_fail = sum(r["failed"] or 0 for r in rows)
    return {
        "ok": not bad,
        "suites": len(rows),
        "passed": total_pass,
        "failed": total_fail,
        "seconds": round(sum(r["seconds"] for r in rows), 1),
        # Only the ones that failed come back in full. A green run is one line.
        "green": [r["suite"] for r in rows if r["ok"]],
        "red": [{"suite": r["suite"], "exit": r["exit"], "failed": r["failed"],
                 "failures": r["failures"]} for r in bad],
    }


def tests_async(which: str = "all", timeout: float = 300.0) -> dict:
    return start_job("tests", "tests: " + (which or "all"),
                     lambda jid: run_tests(which, timeout, jid))


# ---------------------------------------------------------------------------
# Code: know it is unchanged, check it is valid, change it narrowly

def _resolve(path: str) -> Optional[Path]:
    """Inside the repo, or nothing. An endpoint that will read and write files needs a fence."""
    try:
        p = (ROOT / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    except Exception:                                        # noqa: BLE001
        return None
    try:
        p.relative_to(ROOT)
    except ValueError:
        return None
    return p


def sha(path: str) -> dict:
    """A file's fingerprint and shape, without its contents.

    Unity's `get_sha`. Worth having because "is this still what I read ten minutes ago" is a
    question an agent asks constantly and currently answers by re-reading the whole file.
    """
    p = _resolve(path)
    if not p:
        return {"ok": False, "error": "path is outside the repository: " + path}
    if not p.is_file():
        return {"ok": False, "error": "no such file: " + path}
    data = p.read_bytes()
    return {"ok": True, "path": str(p.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "lines": data.count(b"\n") + (0 if data.endswith(b"\n") else 1),
            "mtime": round(p.stat().st_mtime, 3)}


def validate(path: str, timeout: float = 120.0) -> dict:
    """Does this file still hold together? Python by parse, TypeScript by the project's own tsc.

    The repo has no eslint, no ruff and no mypy — so this reports exactly what is really available
    rather than pretending to a check nobody configured.
    """
    p = _resolve(path)
    if not p:
        return {"ok": False, "error": "path is outside the repository: " + path}
    if not p.is_file():
        return {"ok": False, "error": "no such file: " + path}
    suffix = p.suffix.lower()

    if suffix == ".py":
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            return {"ok": True, "checker": "python ast.parse", "diagnostics": []}
        except SyntaxError as ex:
            return {"ok": False, "checker": "python ast.parse",
                    "diagnostics": ["%s:%s: %s" % (p.name, ex.lineno, ex.msg)]}

    if suffix == ".json":
        try:
            json.loads(p.read_text(encoding="utf-8"))
            return {"ok": True, "checker": "json.loads", "diagnostics": []}
        except ValueError as ex:
            return {"ok": False, "checker": "json.loads", "diagnostics": [str(ex)]}

    if suffix in (".ts", ".tsx"):
        # THE WHOLE PROJECT, not the one file. tsconfig has `include: ["src"]`, so `tsc <file>`
        # would ignore the config, lose `strict`, and re-resolve every import from scratch —
        # slower AND weaker. The project's own check is `npm run typecheck`.
        tsc = FRONTEND / "node_modules" / ".bin" / ("tsc.cmd" if os.name == "nt" else "tsc")
        if not tsc.exists():
            return {"ok": False, "checker": "tsc", "diagnostics": ["typescript is not installed"]}
        try:
            r = subprocess.run([str(tsc), "--noEmit", "-p", "tsconfig.json"], cwd=str(FRONTEND),
                               capture_output=True, text=True, timeout=timeout,
                               shell=(os.name == "nt"))
        except subprocess.TimeoutExpired:
            return {"ok": False, "checker": "tsc", "diagnostics": ["timed out"]}
        lines = [ln.strip() for ln in (r.stdout + r.stderr).splitlines() if "error TS" in ln]
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        # In `src`, this file's own errors first; outside `src`, tsconfig does not see it and
        # saying so is more honest than reporting a clean pass.
        mine = [ln for ln in lines if p.name in ln]
        covered = "/src/" in "/" + rel
        return {"ok": r.returncode == 0, "checker": "tsc --noEmit -p tsconfig.json",
                "covers_this_file": covered,
                "note": "" if covered else "tsconfig includes only src/, so this file was not checked",
                "diagnostics": (mine + [ln for ln in lines if ln not in mine])[:40],
                "errors": len(lines)}

    return {"ok": True, "checker": "none", "diagnostics": [],
            "note": "no checker for %s files in this repository" % (suffix or "extensionless")}


def edit(path: str, old: str, new: str, expect_sha: str = "", check: bool = True) -> dict:
    """Replace one exact, unique span, refusing anything ambiguous.

    Three refusals, each of which is a way a blind edit goes wrong:
      - the file changed since you looked      (`expect_sha` does not match)
      - the anchor appears twice, or not at all (which line did you mean?)
      - the result no longer parses             (rolled back, with the diagnostics)

    Written to a temp file and moved into place, never opened for writing — an open("w") that
    then fails on its arguments has already truncated the file.
    """
    p = _resolve(path)
    if not p:
        return {"ok": False, "error": "path is outside the repository: " + path}
    if not p.is_file():
        return {"ok": False, "error": "no such file: " + path}
    if not old:
        return {"ok": False, "error": "`old` is empty — that would match everywhere"}

    before = p.read_text(encoding="utf-8")
    if expect_sha:
        now = hashlib.sha256(p.read_bytes()).hexdigest()
        if now != expect_sha:
            return {"ok": False, "error": "the file changed since you read it",
                    "sha256": now, "expected": expect_sha}

    n = before.count(old)
    if n == 0:
        return {"ok": False, "error": "the anchor is not in the file", "occurrences": 0}
    if n > 1:
        return {"ok": False, "error": "the anchor appears %d times — make it unique" % n,
                "occurrences": n}

    after = before.replace(old, new, 1)
    tmp = str(p) + ".tmp"
    io.open(tmp, "w", encoding="utf-8", newline="").write(after)
    os.replace(tmp, str(p))

    out = {"ok": True, "path": str(p.relative_to(ROOT)).replace("\\", "/"),
           "sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
           "lines_before": before.count("\n"), "lines_after": after.count("\n")}
    if check:
        v = validate(path)
        out["validate"] = v
        if not v.get("ok") and p.suffix.lower() in (".py", ".json"):
            # A syntax error is unambiguous and always this edit's fault, so put it back. A tsc
            # failure is not — the project may have been red before — so that one is reported and
            # left alone rather than reverted on a guess.
            io.open(tmp, "w", encoding="utf-8", newline="").write(before)
            os.replace(tmp, str(p))
            out["ok"] = False
            out["rolled_back"] = True
            out["error"] = "the edit did not parse; the file is back as it was"
    return out


# ---------------------------------------------------------------------------
# The sidecar, with nothing open
#
# Godot's MCP parses scene files on disk and only uses the live editor when it happens to be
# running. Blender MCP dies without Blender on :9876; Unity's dies without the Editor. Ours does
# not have to: `applyEdits` and `applyVerts` are pure JavaScript, so the same document the editor
# writes can be read, applied and reported on in node with no browser anywhere.
#
# Two levels, because they fail differently. Reading a sidecar always works. APPLYING one has to
# run the project's own builder, which may not import outside a bundler — so that half says why
# it could not rather than pretending.

NODE_MODULES = FRONTEND / "node_modules"
THREE_MJS = NODE_MODULES / "three" / "build" / "three.module.js"
OPS_BUNDLE = FRONTEND / "dist" / "forge-ops.js"


def sidecar_read(path: str) -> dict:
    """What a saved document holds, counted. No browser, no build, no project.

    The summary rather than the file: a sidecar with four hundred vertex edits is a big JSON blob
    and "how many, on which meshes" is the question actually being asked.
    """
    p = _resolve(path)
    if not p:
        return {"ok": False, "error": "path is outside the repository: " + path}
    if not p.is_file():
        return {"ok": False, "error": "no such file: " + path}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as ex:
        return {"ok": False, "error": "not readable as JSON: %s" % ex}
    if not isinstance(d, dict):
        return {"ok": False, "error": "a sidecar is an object; this is a %s" % type(d).__name__}

    verts = d.get("verts") or []
    by_mesh: dict = {}
    for v in verts:
        if isinstance(v, dict):
            by_mesh[v.get("mesh") or "?"] = by_mesh.get(v.get("mesh") or "?", 0) + 1
    mods = [m for m in (d.get("mods") or []) if isinstance(m, dict)]
    return {
        "ok": True,
        "path": str(p.relative_to(ROOT)).replace("\\", "/"),
        "version": d.get("version"),
        "asset": d.get("asset", ""),
        "params": len(d.get("params") or {}),
        "parts": len(d.get("parts") or {}),
        "hidden": sum(1 for o in (d.get("parts") or {}).values()
                      if isinstance(o, dict) and o.get("hidden")),
        "mods": [{"op": m.get("op"), "target": m.get("target") or "(whole asset)",
                  "off": bool(m.get("off"))} for m in mods],
        "verts": len(verts),
        "verts_by_mesh": by_mesh,
        "bones": len(d.get("bones") or []),
        "clips": len(d.get("clips") or []),
        "placed": len(d.get("placed") or []),
        "world": bool(d.get("world")),
        "bakes": len(d.get("bakes") or []),
        "updated": d.get("updated"),
    }


_APPLY_JS = """
// Generated. Builds the asset in node and applies the sidecar to it, with no browser involved.
import * as THREE from %(three)s;
const ops = await import(%(bundle)s);
const mod = await import(%(module)s);
const build = mod[%(export)s] ?? mod.default;
if (typeof build !== "function") {
  console.log(JSON.stringify({ok:false, error:"%(export_plain)s is not a function in that module",
                              exports:Object.keys(mod).slice(0,40)}));
  process.exit(0);
}
const edits = JSON.parse(%(edits)s);
let root;
try { root = await build(THREE, ...JSON.parse(%(args)s)); }
catch (e) { console.log(JSON.stringify({ok:false, error:"the builder threw: "+(e&&e.message||e)})); process.exit(0); }
if (!root || !root.traverse) {
  console.log(JSON.stringify({ok:false, error:"the builder returned no Object3D"}));
  process.exit(0);
}
let meshes = 0, tris = 0;
root.traverse((o) => { if (o.isMesh && o.geometry) { meshes++;
  const g = o.geometry; tris += (g.index ? g.index.count : (g.attributes.position?.count||0)) / 3; } });
const report = ops.applyEdits(root, edits, ops.makeOps(THREE), THREE);
console.log(JSON.stringify({ok: !report.errors.length, meshes, triangles: Math.round(tris),
                            applied: report}));
"""


def sidecar_apply(sidecar: str, module: str, export: str = "", args: Optional[list] = None,
                  timeout: float = 90.0) -> dict:
    """Build the asset in node and put the saved document on it. Reports what bound and what did not.

    This is the check that matters after a parameter change: did my hand edits survive, or did the
    code move out from under them? Answerable with nothing running.
    """
    sp = _resolve(sidecar)
    mp = _resolve(module)
    if not sp or not sp.is_file():
        return {"ok": False, "error": "no such sidecar: " + sidecar}
    if not mp or not mp.is_file():
        return {"ok": False, "error": "no such module: " + module}
    if not THREE_MJS.exists():
        return {"ok": False, "error": "three is not installed under frontend/node_modules"}
    if not OPS_BUNDLE.exists():
        return {"ok": False, "error": "forge-ops.js is not built — run `npm run build:ops`"}
    try:
        edits = json.loads(sp.read_text(encoding="utf-8"))
    except ValueError as ex:
        return {"ok": False, "error": "the sidecar is not readable as JSON: %s" % ex}

    def url(p: Path) -> str:
        return json.dumps(p.resolve().as_uri())

    src = _APPLY_JS % {
        "three": url(THREE_MJS), "bundle": url(OPS_BUNDLE), "module": url(mp),
        "export": json.dumps(export or "default"), "export_plain": (export or "default"),
        "edits": json.dumps(json.dumps(edits)), "args": json.dumps(json.dumps(args or [])),
    }
    tmp = ROOT / "data" / "tmp" / ("sidecar-apply-%s.mjs" % uuid.uuid4().hex[:8])
    tmp.parent.mkdir(parents=True, exist_ok=True)
    io.open(str(tmp), "w", encoding="utf-8", newline="").write(src)
    try:
        r = subprocess.run(["node", str(tmp)], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout, shell=(os.name == "nt"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "node timed out after %ds" % int(timeout)}
    except FileNotFoundError:
        return {"ok": False, "error": "node is not on PATH"}
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    line = (r.stdout or "").strip().splitlines()
    for ln in reversed(line):
        try:
            return json.loads(ln)
        except ValueError:
            continue
    return {"ok": False, "error": "node printed nothing readable",
            "stderr": (r.stderr or "")[-1200:], "exit": r.returncode}

#!/usr/bin/env python3
"""Check an installed Asset Studio against FEATURES.md.

Run it on a new machine after the setup, with the Studio open:

    python scripts/features/verify.py                  programs, backend, every route, the UI, smoke checks
    python scripts/features/verify.py --tests all      ...and every test suite (minutes)
    python scripts/features/verify.py --tests engine_test.py,test:edit
    python scripts/features/verify.py --json report.json

WHAT IT CAN PROVE BY ITSELF:
- the programs the Studio needs are on this machine;
- the backend answers, and serves EVERY route the code has (a route group that failed to load
  leaves a whole window dead with no error anywhere - this is the check that finds it);
- the built UI is served;
- each feature's smoke checks (every `GET /api/...` written in docs/features/checks.md) answer;
- the test suites pass, when asked.

What it cannot prove - that a picture looks right, that an agent's answer is good - is in the
"How to check" steps of each feature in FEATURES.md. The report ends with the list of them.

Standard library only, so any Python 3.10+ runs it. Exit code 0 when nothing required failed.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "docs" / "features" / "features.json"

# THE ONE LIST OF WHAT MUST BE INSTALLED. build.py prints it in FEATURES.md, this file checks it.
# `need`: required (the Studio does not work without it), recommended (a feature is lost), or
# optional (only for the feature named in `for`).
PROGRAMS = [
    {"name": "python", "need": "required",
     "for": "The backend. Its own venv in backend/.venv, Python 3.10 or newer (the installer carries 3.12).",
     "from": "setup.ps1 installs Python 3.12 with winget; the installer carries its own in runtime/python."},
    {"name": "node", "need": "required",
     "for": "Builds the UI, runs the dev servers of web games, installs gltf-transform.",
     "from": "setup.ps1 installs Node.js LTS with winget."},
    {"name": "npm", "need": "required", "for": "Comes with Node.js; runs the frontend test suites.",
     "from": "Node.js."},
    {"name": "git", "need": "required",
     "for": "Workspaces, worktrees and the source control panel.",
     "from": "Git for Windows. Install it by hand when it is missing."},
    {"name": "claude", "need": "required", "for": "Every Claude chat agent runs in Claude Code.",
     "from": "setup.ps1 and the installer run Anthropic's installer. Log in once: `claude`."},
    {"name": "browser", "need": "required",
     "for": "The forge, the live game link, the reviews and the web tools draw in a headless Chrome or Edge.",
     "from": "Edge is on every Windows PC; setup downloads a headless Chrome only when neither is there."},
    {"name": "graphify", "need": "recommended", "for": "The code graph that agents search first.",
     "from": "setup.ps1 installs it in its own venv and puts `graphify` in bin/ on PATH."},
    {"name": "gltf-transform", "need": "recommended",
     "for": "Opens compressed models (Draco, meshopt) and runs the Optimize stage.",
     "from": "setup.ps1: npm install -g @gltf-transform/cli."},
    {"name": "codex", "need": "optional", "for": "Codex chats, and GPT images made through the Codex CLI.",
     "from": "setup.ps1 installs the Codex CLI."},
    {"name": "blender", "need": "optional",
     "for": "Rigging, human references, Poly Pizza measuring, and the Blender MCP.",
     "from": "Install Blender by hand. Set Settings -> tools.blender_path when it is not on PATH."},
    {"name": "comfyui", "need": "optional", "for": "The local 2D and 3D generators.",
     "from": "Install AI Pack.ps1, or Settings -> Local generators. It must be running to be found."},
]


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------
def _venv_python() -> str:
    for p in (ROOT / "backend" / ".venv" / "Scripts" / "python.exe", ROOT / "backend" / ".venv" / "bin" / "python"):
        if p.is_file():
            return str(p)
    return ""


def _version(argv: list) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                           shell=(os.name == "nt" and argv[0].lower().endswith(".cmd")))
        out = (r.stdout or r.stderr or "").strip().splitlines()
        return out[0][:80] if out else ""
    except Exception:
        return ""


def _browser() -> str:
    env = os.environ
    cands = []
    for base in (env.get("PROGRAMFILES", r"C:\Program Files"), env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 env.get("LOCALAPPDATA", "")):
        if base:
            cands += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                      os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    for c in cands:
        if os.path.isfile(c):
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "msedge", "chrome"):
        w = shutil.which(name)
        if w:
            return w
    return ""


def _blender() -> str:
    w = shutil.which("blender")
    if w:
        return w
    for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),):
        hits = sorted(glob.glob(os.path.join(base, "Blender Foundation", "*", "blender.exe")))
        if hits:
            return hits[-1]
    return ""


def check_programs(base: str) -> list:
    rows = []
    for p in PROGRAMS:
        name, where, note = p["name"], "", ""
        if name == "python":
            where = _venv_python()
            note = _version([where, "--version"]) if where else "backend/.venv is missing - run Setup.bat"
        elif name == "browser":
            where = _browser()
        elif name == "blender":
            where = _blender()
        elif name == "comfyui":
            url = "http://127.0.0.1:8188"
            try:
                s = _get_json(base + "/api/settings")[1] or {}
                url = ((s.get("settings") or s).get("tools") or {}).get("comfyui_url") or url
            except Exception:
                pass
            code, _ = _get(url.rstrip("/") + "/system_stats", timeout=3)
            where = url if code == 200 else ""
            note = "" if where else "not running at %s" % url
        else:
            where = shutil.which(name) or ""
            if not where and name == "graphify":
                for c in (ROOT / "bin" / "graphify.cmd", ROOT / "bin" / "graphify"):
                    if c.is_file():
                        where = str(c)
            if where and name in ("node", "npm", "git", "claude", "codex", "gltf-transform"):
                note = _version([where, "--version"])
        rows.append({"name": name, "need": p["need"], "ok": bool(where), "where": where, "note": note})
    return rows


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get(url: str, timeout: float = 20.0):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "studio-verify"}),
                                    timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read() if hasattr(ex, "read") else b""
    except Exception as ex:
        return 0, str(ex).encode("utf-8", "replace")


def _get_json(url: str, timeout: float = 20.0):
    code, body = _get(url, timeout)
    try:
        return code, json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return code, None


def _norm(path: str) -> str:
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def run_suite(name: str, timeout: float) -> dict:
    t0 = time.time()
    if name.endswith("_test.py"):
        py = _venv_python() or sys.executable
        argv, cwd, shell = [py, name], ROOT / "backend", False
    else:
        argv, cwd, shell = "npm run " + name, ROOT / "frontend", True
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, shell=shell)
        tail = [ln for ln in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines() if ln.strip()]
        fails = [ln.strip() for ln in tail if re.search(r"\bFAIL|Error|Traceback|AssertionError", ln)][:8]
        ok = r.returncode == 0
        return {"suite": name, "ok": ok, "seconds": round(time.time() - t0, 1),
                "detail": [] if ok else (fails or tail[-8:])}
    except subprocess.TimeoutExpired:
        return {"suite": name, "ok": False, "seconds": round(time.time() - t0, 1),
                "detail": ["timed out after %ds" % timeout]}


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Check an installed Asset Studio against FEATURES.md.")
    ap.add_argument("--base", default=os.environ.get("STUDIO_BASE", "http://127.0.0.1:8777"),
                    help="where the backend answers (default http://127.0.0.1:8777)")
    ap.add_argument("--tests", default="none",
                    help="none (default), backend, frontend, all, or suite names separated by commas")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds per test suite")
    ap.add_argument("--json", default="", help="also write the whole result to this file")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not MAP.is_file():
        print("No %s. Run: python scripts/features/build.py" % MAP.relative_to(ROOT))
        return 2
    fmap = json.loads(MAP.read_text(encoding="utf-8"))
    result: dict = {"base": base, "programs": [], "backend": {}, "ui": {}, "features": [], "tests": []}
    required_failed = []

    # 1. Programs
    result["programs"] = check_programs(base)
    for p in result["programs"]:
        if p["need"] == "required" and not p["ok"]:
            required_failed.append("program %s" % p["name"])

    # 2. The backend, and every route the code has
    code, _ = _get(base + "/api/health", timeout=10)
    result["backend"]["health"] = code == 200
    missing: dict = {}
    extra: list = []
    if code != 200:
        required_failed.append("backend not answering at %s/api/health" % base)
    else:
        _, spec = _get_json(base + "/openapi.json", timeout=60)
        served = {}
        for path, ops in ((spec or {}).get("paths") or {}).items():
            served[_norm(path)] = {m.upper() for m in ops}
        mapped = set()
        for aid, area in fmap["areas"].items():
            for e in area["endpoints"]:
                if e["method"] == "WS" or e["path"].endswith("{full_path:path}"):
                    continue
                p = _norm(e["path"])
                mapped.add(p)
                if e["method"] not in served.get(p, set()):
                    missing.setdefault(aid, []).append("%s %s" % (e["method"], e["path"]))
        extra = sorted(p for p in served if p not in mapped)
        result["backend"].update(routes_served=len(served), missing=missing, not_in_map=extra)
        if missing:
            required_failed.append("routes not served: %d" % sum(len(v) for v in missing.values()))

    # 3. The UI
    for path in ("/", "/engine"):
        c, body = _get(base + path, timeout=20)
        ok = c == 200 and b'id="root"' in body
        result["ui"][path] = ok
        if not ok and result["backend"].get("health"):
            required_failed.append("UI not served at %s (run npm run build in frontend/)" % path)

    # 4. Smoke checks, per feature
    for f in fmap["features"]:
        smoke = []
        for path in f["smoke"]:
            c, data = _get_json(base + path, timeout=30)
            ok = c == 200 and not (isinstance(data, dict) and data.get("ok") is False)
            why = "" if ok else ("HTTP %s" % c if c != 200 else str((data or {}).get("error") or "ok: false")[:160])
            smoke.append({"path": path, "ok": ok, "why": why})
        routes_missing = [m for part in f["parts"] for m in missing.get(part, [])]
        result["features"].append({"id": f["id"], "title": f["title"], "smoke": smoke,
                                   "routes_missing": routes_missing, "tests": []})

    # 5. Test suites
    want = a.tests.strip()
    if want and want != "none":
        back = sorted(fmap["tests"]["backend"])
        front = sorted(fmap["tests"]["frontend"])
        slow = set(fmap.get("slow_tests") or [])
        if want == "backend":
            names = [n for n in back if n not in slow]
        elif want == "frontend":
            names = front
        elif want == "all":
            names = front + [n for n in back if n not in slow]
        else:
            names = [n.strip() for n in want.split(",") if n.strip()]
        for n in names:
            print("running %s ..." % n, file=sys.stderr, flush=True)
            result["tests"].append(run_suite(n, a.timeout))
        ran = {t["suite"]: t for t in result["tests"]}
        for f, spec in zip(result["features"], fmap["features"]):
            f["tests"] = [{"suite": n, "ok": ran[n]["ok"]} for n in spec.get("tests", []) if n in ran]

    # The report
    out = ["# Asset Studio check", "", "Backend: %s" % base, ""]
    out += ["## Programs", "", "| Program | Needed | Found | Note |", "| --- | --- | --- | --- |"]
    for p in result["programs"]:
        out.append("| %s | %s | %s | %s |" % (p["name"], p["need"], "yes" if p["ok"] else "**NO**",
                                             (p["note"] or p["where"]).replace("|", "/")))
    out += ["", "## Backend", ""]
    if not result["backend"].get("health"):
        out.append("- **The backend does not answer at %s.** Start the Studio, then run this again." % base)
    else:
        n_missing = sum(len(v) for v in missing.values())
        out.append("- Health: OK")
        out.append("- Routes: %d served. %s" % (result["backend"]["routes_served"],
                                                "Every route in the map is served." if not n_missing
                                                else "**%d routes in the map are NOT served:**" % n_missing))
        for aid, rows in sorted(missing.items()):
            out.append("  - `%s` (%s): %s" % (aid, fmap["areas"][aid]["file"], ", ".join(rows[:6])
                                              + (" ..." if len(rows) > 6 else "")))
        if extra:
            out.append("- Served but not in the map (the map is older than this backend): "
                       + ", ".join("`%s`" % p for p in extra[:12]) + (" ..." if len(extra) > 12 else ""))
        for path, ok in result["ui"].items():
            out.append("- UI at `%s`: %s" % (path, "OK" if ok else "**NOT SERVED**"))
    out += ["", "## Features", "", "| # | Feature | Routes | Smoke checks | Tests | Result |",
            "| --- | --- | --- | --- | --- | --- |"]
    for i, f in enumerate(result["features"], 1):
        s_ok = sum(1 for s in f["smoke"] if s["ok"])
        t_ok = sum(1 for t in f["tests"] if t["ok"])
        bad = bool(f["routes_missing"]) or s_ok < len(f["smoke"]) or t_ok < len(f["tests"])
        f["ok"] = not bad
        out.append("| %d | %s | %s | %s | %s | %s |" % (
            i, f["title"], "missing %d" % len(f["routes_missing"]) if f["routes_missing"] else "all served",
            "%d/%d" % (s_ok, len(f["smoke"])) if f["smoke"] else "-",
            "%d/%d" % (t_ok, len(f["tests"])) if f["tests"] else "-",
            "**FAIL**" if bad else "pass"))
    fails = [(f, s) for f in result["features"] for s in f["smoke"] if not s["ok"]]
    if fails:
        out += ["", "### Smoke checks that failed", ""]
        for f, s in fails:
            out.append("- %s: `GET %s` - %s" % (f["title"], s["path"], s["why"]))
    bad_tests = [t for t in result["tests"] if not t["ok"]]
    if result["tests"]:
        out += ["", "## Test suites", "", "%d of %d passed." % (len(result["tests"]) - len(bad_tests), len(result["tests"]))]
        for t in bad_tests:
            out.append("- **%s** failed (%ss):" % (t["suite"], t["seconds"]))
            for ln in t["detail"]:
                out.append("    " + ln[:200])
    out += ["", "## Left for you", "",
            "This script cannot see pictures or judge an agent's answer. Do the \"How to check\" steps "
            "of each feature in FEATURES.md that it could not run, and add them to this report."]
    if required_failed:
        out += ["", "## Result: FAIL", ""] + ["- " + r for r in required_failed]
    elif any(not f["ok"] for f in result["features"]) or bad_tests:
        out += ["", "## Result: the Studio runs, and some features fail (above)"]
    else:
        out += ["", "## Result: everything this script can check passes"]
    print("\n".join(out))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 1 if required_failed or bad_tests or any(not f["ok"] for f in result["features"]) else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build FEATURES.md and docs/features/features.json from the code.

WHAT THE STUDIO HAS is read from the source itself: every route, every page, every setting,
every program it calls and every test. So the map cannot fall behind the code - a route added
today is in it the next time this runs, with the first sentence of its docstring.

WHAT EACH PART MUST DO, and how to check it, is written by hand in docs/features/checks.md and
placed beside the parts it names. A part that no section names is listed under "Parts no
feature describes yet", and a section that names a part which no longer exists is listed too,
so the drift is visible in both directions.

    python scripts/features/build.py                 write both files
    python scripts/features/build.py --check         exit 1 when either file is out of date
    python scripts/features/build.py --hook          the pre-commit hook: write, stage what changed
    python scripts/features/build.py --install-hook  put that hook into this clone

Standard library only: it runs in GitHub Actions with nothing installed, and inside a git hook.
The output depends on the tracked files alone - no dates, no commit ids, no absolute paths - so
the same commit gives the same bytes on every machine, and the Action commits only a real change.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT_MD = "FEATURES.md"
OUT_JSON = "docs/features/features.json"
CHECKS = "docs/features/checks.md"
PKG = "backend/asset_studio"
ROUTERS = PKG + "/routers"
WEB = "frontend/src"
METHODS = ("get", "post", "put", "delete", "patch", "websocket")
# Folders a walk must not enter when there is no git to list the files (an installed copy).
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "dist-electron", "data", "runtime",
             "bin", "__pycache__", "tmp", "output", "tools", "graphify-out", "third_party"}
# A module imported by more routers than this is plumbing (config, db, events), not a part's own.
OWN_MAX = 2
HOOK_MARK = "asset-studio-features"

sys.path.insert(0, str(HERE))
import verify  # noqa: E402  - PROGRAMS: the one list of what must be installed


# ---------------------------------------------------------------------------
# Reading the tree
# ---------------------------------------------------------------------------
def source_files() -> list[str]:
    """The files of the program, as repository-relative posix paths.

    `git ls-files` when this is a clone, so a scratch file nobody committed cannot change the map;
    a walk otherwise, which is what an installed copy (no .git) has."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                             check=True, timeout=60).stdout
        files = [f for f in out.decode("utf-8", "replace").split("\0") if f]
        if files:
            return sorted(f for f in files if (ROOT / f).is_file())
    except Exception:
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel = Path(dirpath).relative_to(ROOT).as_posix()
        for f in filenames:
            found.append(f if rel == "." else rel + "/" + f)
    return sorted(found)


def read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def first_sentence(doc: str, limit: int = 170) -> str:
    """The first sentence of a docstring, on one line, short enough for a table cell."""
    if not doc:
        return ""
    para = " ".join(doc.strip().split("\n\n")[0].split())
    m = re.match(r"(.+?[.!?])(\s|$)", para)
    s = m.group(1) if m else para
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + "…"
    return s


def first_paragraph(doc: str, limit: int = 600) -> str:
    if not doc:
        return ""
    para = " ".join(doc.strip().split("\n\n")[0].split())
    return para if len(para) <= limit else para[:limit - 1].rstrip() + "…"


def cell(s: str) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


# ---------------------------------------------------------------------------
# The backend: route groups
# ---------------------------------------------------------------------------
def _call_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def parse_routes(rel: str, text: str, app_var: str = "") -> dict:
    """One file's routes, the modules it imports, and its own docstring."""
    try:
        tree = ast.parse(text)
    except SyntaxError as ex:
        return {"error": "could not be parsed: %s" % ex.msg, "endpoints": [], "imports": [],
                "prefixes": [], "purpose": ""}
    prefixes: dict[str, str] = {app_var: ""} if app_var else {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and _call_name(node.value.func) == "APIRouter":
            prefix = ""
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefix = str(kw.value.value)
            for t in node.targets:
                if isinstance(t, ast.Name):
                    prefixes[t.id] = prefix
    endpoints = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            owner, verb = dec.func.value, dec.func.attr
            if not (isinstance(owner, ast.Name) and owner.id in prefixes and verb in METHODS):
                continue
            path = ""
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = str(dec.args[0].value)
            for kw in dec.keywords:
                if kw.arg == "path" and isinstance(kw.value, ast.Constant):
                    path = str(kw.value.value)
            endpoints.append({"method": "WS" if verb == "websocket" else verb.upper(),
                              "path": prefixes[owner.id] + path, "function": node.name,
                              "line": node.lineno,
                              "summary": first_sentence(ast.get_docstring(node) or "")})
    endpoints.sort(key=lambda e: e["line"])
    # Relative imports: `from .. import live` and `from ..engine import x` are the package's
    # modules; `from . import x` inside routers/ is another route group.
    depth_pkg = 2 if rel.startswith(ROUTERS + "/") else 1
    imports = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.level):
            continue
        names = [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
        if node.level == depth_pkg:
            if node.module == "routers" or (node.module or "").startswith("routers."):
                continue
            imports.update(names)
        elif node.level == depth_pkg - 1:
            imports.update("routers." + n for n in names)
    return {"endpoints": endpoints, "imports": sorted(imports),
            "prefixes": sorted(set(prefixes.values()) - {""}) if not app_var else [],
            "purpose": first_paragraph(ast.get_docstring(tree) or "")}


def endpoint_matcher(endpoints: list) -> list:
    out = []
    for e in endpoints:
        # The UI's catch-all (`/{full_path:path}`) answers every path; it says nothing about which
        # group a call belongs to.
        if re.fullmatch(r"/\{[^}]+:path\}", e["path"]):
            continue
        pat = re.sub(r"\{[^}:]+:path\}", ".*", e["path"])
        pat = re.sub(r"\{[^}]+\}", "[^/]+", pat)
        out.append((re.compile("^" + pat + "$"), e))
    return out


# ---------------------------------------------------------------------------
# The frontend: pages, the API client, imports
# ---------------------------------------------------------------------------
IMPORT_RE = re.compile(r"""(?:\bfrom\s*["']([^"']+)["'])|(?:\bimport\(\s*["']([^"']+)["']\s*\))|(?:^\s*import\s+["']([^"']+)["'])""",
                       re.M)
API_LITERAL = re.compile(r"""["'`](/api/[^"'`?\s#]*)""")


def resolve_import(from_rel: str, spec: str, files: set) -> str:
    if not spec.startswith("."):
        return ""
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), spec))
    for cand in (base, base + ".ts", base + ".tsx", base + ".js", base + ".jsx",
                 base + "/index.ts", base + "/index.tsx"):
        if cand in files:
            return cand
    return ""


def client_functions(text: str) -> dict:
    """`api.<name>` -> the first /api path its body names."""
    out = {}
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"^  (\w+)\s*:", text, re.M)]
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        m = API_LITERAL.search(text, pos, end)
        if m and name not in out:
            out[name] = re.sub(r"\$\{[^}]*\}", "X", m.group(1))
    return out


def page_table(files: set) -> list:
    app = read(WEB + "/App.tsx")
    nav = read(WEB + "/components/TopNav.tsx")
    labels = dict(re.findall(r'\{\s*id:\s*"(\w+)",\s*label:\s*"([^"]+)"', nav))
    pages = []
    for tab, mod in re.findall(r'(\w+):\s*lazy\(\(\)\s*=>\s*import\("\./pages/(\w+)"\)\)', app):
        rel = resolve_import(WEB + "/App.tsx", "./pages/" + mod, files)
        if rel:
            pages.append({"id": tab, "label": labels.get(tab, mod), "file": rel, "window": "tab"})
    if re.search(r'const\s+EnginePage\s*=\s*lazy\(\(\)\s*=>\s*import\("\./pages/Engine"\)\)', app):
        rel = resolve_import(WEB + "/App.tsx", "./pages/Engine", files)
        if rel:
            pages.append({"id": "engine", "label": "Studio Engine", "file": rel,
                          "window": "its own window, /engine"})
    return pages


def closure(start: str, files: set, stop: set) -> dict:
    """Every source file a page loads, with how many imports away it is. It does not enter
    another page, the API client or the store."""
    depth, todo = {start: 0}, [start]
    while todo:
        cur = todo.pop(0)
        for m in IMPORT_RE.finditer(read(cur)):
            spec = m.group(1) or m.group(2) or m.group(3)
            dep = resolve_import(cur, spec, files)
            if not dep or dep in depth or dep in stop:
                continue
            if dep.startswith((WEB + "/api/", WEB + "/store/")):
                continue
            depth[dep] = depth[cur] + 1
            todo.append(dep)
    return depth


# ---------------------------------------------------------------------------
# Settings, programs, skills, tests
# ---------------------------------------------------------------------------
SETTING_RE = re.compile(r"""settings\.get\(\s*["']([\w.]+)["']\s*(?:,\s*([^)\n]{0,60}))?\)""")
PROGRAM_RE = re.compile(r"""(?:shutil\.which|\b_has|\bwhich)\(\s*["']([A-Za-z0-9_.+-]+)["']""")


def settings_labels(files: list) -> dict:
    out = {}
    for rel in files:
        if rel.startswith(WEB + "/pages/Settings") and rel.endswith(".tsx"):
            for key, label in re.findall(r'\{\s*key:\s*"([\w.]+)",\s*label:\s*"([^"]+)"', read(rel)):
                out.setdefault(key, label)
    return out


def skills_table(files: list) -> list:
    out = []
    for rel in files:
        m = re.match(r"^skills/([^/]+)/SKILL\.md$", rel)
        if not m:
            continue
        text = read(rel)
        fm = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
        meta = fm.group(1) if fm else ""
        name = re.search(r"^name:\s*(.+)$", meta, re.M)
        desc = re.search(r"^description:\s*(.+)$", meta, re.M)
        d = (desc.group(1).strip().strip("\"'>|") if desc else "")
        out.append({"name": (name.group(1).strip().strip("\"'") if name else m.group(1)),
                    "folder": m.group(1), "summary": first_sentence(d, 200)})
    return sorted(out, key=lambda s: s["folder"])


def py_test_refs(text: str) -> set:
    """The backend modules a test file reaches: imports, and paths it names as text."""
    mods = set()
    for m in re.finditer(r"from\s+asset_studio((?:\.\w+)*)\s+import\s+(\([^)]*\)|[^\n]+)", text):
        sub = [p for p in m.group(1).split(".") if p]
        names = [i.strip().split()[0] for i in m.group(2).split("#")[0].strip("() \n").replace("\n", " ").split(",")
                 if i.strip() and i.strip()[0].isalpha()]
        if not sub:
            mods.update(names)
        elif sub == ["routers"]:
            mods.update("routers." + n for n in names)
        elif sub[0] == "routers" and len(sub) > 1:
            mods.add("routers." + sub[1])
        else:
            mods.add(sub[0])
    for m in re.finditer(r"import\s+asset_studio\.(routers\.)?(\w+)", text):
        mods.add((m.group(1) or "") + m.group(2))
    for m in re.finditer(r"asset_studio[/\\](routers[/\\])?(\w+)\.py", text):
        mods.add(("routers." if m.group(1) else "") + m.group(2))
    return mods


def web_test_refs(text: str, rel: str, files: set) -> set:
    """The frontend source files a test file reaches, by import or by reading them as text."""
    out = set()
    for m in IMPORT_RE.finditer(text):
        dep = resolve_import(rel, m.group(1) or m.group(2) or m.group(3) or "", files)
        if dep.startswith(WEB + "/"):
            out.add(dep)
    # join(process.cwd(), "src", "components", "engine", "Stage2D.tsx")
    joined = ["/".join(re.findall(r"""["']([^"']+)["']""", m))
              for m in re.findall(r"""["']src["']((?:\s*,\s*["'][^"']+["'])+)""", text)]
    for spec in re.findall(r"""\bsrc\(\s*["']([^"']+)["']""", text) + joined + \
            re.findall(r"""["'](?:\./)?src/([\w./-]+\.(?:tsx?|jsx?))["']""", text):
        cand = WEB + "/" + spec.lstrip("./")
        if cand in files:
            out.add(cand)
    return out


# ---------------------------------------------------------------------------
# The hand-written half
# ---------------------------------------------------------------------------
def parse_checks(text: str) -> list:
    sections, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            cur = {"title": m.group(1).strip(), "parts": [], "modules": [], "claims": [], "body": []}
            sections.append(cur)
            continue
        if cur is None:
            continue
        # `parts:` names route groups and pages. `modules:` names backend modules no route group
        # owns alone (the session code every chat route uses), so the tests of those find a home.
        # `tests:` names a suite that reaches neither, by its name.
        pm = re.match(r"^(parts|modules|tests):\s*(.*)$", line, re.I)
        if pm and not "".join(cur["body"]).strip():
            key = {"tests": "claims"}.get(pm.group(1).lower(), pm.group(1).lower())
            cur[key] = [p.strip() for p in pm.group(2).split(",") if p.strip()]
            continue
        cur["body"].append(line)
    for s in sections:
        s["body"] = "\n".join(s["body"]).strip()
        s["id"] = re.sub(r"[^a-z0-9]+", "-", s["title"].lower()).strip("-")
        # The checks a script can run by itself: every `GET /api/...` written in backticks.
        s["smoke"] = sorted(set(re.findall(r"`GET (/api/[^`\s]+)`", s["body"])))
    return sections


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
def collect() -> dict:
    files = source_files()
    fileset = set(files)

    # Route groups: one per file in routers/, and the app's own routes in main.py.
    areas: dict = {}
    for rel in files:
        if rel.startswith(ROUTERS + "/") and rel.endswith(".py") and not rel.endswith("__init__.py"):
            aid = Path(rel).stem
            areas[aid] = dict(parse_routes(rel, read(rel)), id=aid, file=rel)
    if PKG + "/main.py" in fileset:
        main = parse_routes(PKG + "/main.py", read(PKG + "/main.py"), app_var="app")
        areas["app"] = dict(main, id="app", file=PKG + "/main.py", imports=[],
                            purpose="The app's own routes: health, and the built UI served for every other path.")
    all_eps = [dict(e, area=a["id"]) for a in areas.values() for e in a["endpoints"]]
    matchers = endpoint_matcher(all_eps)

    def areas_of_path(path: str) -> set:
        hit = {e["area"] for rx, e in matchers if rx.match(path)}
        if hit:
            return hit
        best = ""
        for a in areas.values():
            for p in a["prefixes"]:
                if p != "/api" and (path == p or path.startswith(p + "/")) and len(p) > len(best):
                    best, hit = p, {a["id"]}
        return hit

    # Which modules belong to which group: a module imported by few routers is theirs.
    importers: dict = {}
    for a in areas.values():
        for m in a["imports"]:
            importers.setdefault(m, set()).add(a["id"])
    owned = {aid: {aid, "routers." + aid} for aid in areas}
    for m, users in importers.items():
        if len(users) <= OWN_MAX:
            for u in users:
                owned[u].add(m)

    def areas_of_module(mod: str) -> set:
        return {aid for aid, mods in owned.items() if mod in mods}

    # Settings and programs, found in every backend module.
    labels = settings_labels(files)
    settings: dict = {}
    programs: dict = {}
    for rel in files:
        if not (rel.startswith(PKG + "/") and rel.endswith(".py")):
            continue
        mod = rel[len(PKG) + 1:-3].replace("/", ".")
        mod = mod if mod.startswith("routers.") else mod.split(".")[0]
        text = read(rel)
        for key, default in SETTING_RE.findall(text):
            s = settings.setdefault(key, {"key": key, "defaults": set(), "modules": set()})
            if default.strip():
                s["defaults"].add(default.strip())
            s["modules"].add(mod)
        for prog in PROGRAM_RE.findall(text):
            programs.setdefault(prog, set()).add(mod)
    for s in settings.values():
        s["label"] = labels.get(s["key"], "")
        s["areas"] = sorted({a for m in s["modules"] for a in areas_of_module(m)})
        s["defaults"] = sorted(s["defaults"])
        s["modules"] = sorted(s["modules"])

    # The API client and the pages.
    client = client_functions(read(WEB + "/api/client.ts"))
    client_areas = {fn: sorted(areas_of_path(p)) for fn, p in client.items()}
    pages = page_table(fileset)
    # The frame around every page: the top bar, the status bar, the login prompt, the toasts.
    if WEB + "/App.tsx" in fileset:
        pages.append({"id": "shell", "label": "App shell", "file": WEB + "/App.tsx",
                      "window": "around every page"})
    page_files = {p["file"] for p in pages}
    page_closure = {}
    page_depth: dict = {}
    for p in pages:
        depth = closure(p["file"], fileset, page_files - {p["file"]})
        cl = sorted(depth)
        page_closure[p["id"]] = set(cl)
        for rel, n in depth.items():
            page_depth.setdefault(rel, {})[p["id"]] = n
        used = set()
        for rel in cl:
            for fn in re.findall(r"\bapi\.(\w+)\b", read(rel)):
                used.update(client_areas.get(fn, []))
        head = re.match(r"\s*((?://[^\n]*\n)+)", read(p["file"]))
        p["purpose"] = first_paragraph(" ".join(l.strip().lstrip("/").strip()
                                                for l in head.group(1).splitlines())) if head else ""
        p["areas"] = sorted(used)
        p["source_files"] = len(cl)
        if p["id"] == "shell" and not p["purpose"]:
            p["purpose"] = ("The frame around every page: the top bar of tabs, the status bar, the "
                            "Claude login prompt, the update banner and the notifications.")
    # WHICH PAGE A FILE BELONGS TO: the one that loads it in the fewest imports. Settings loads
    # the engine's edit kit four imports down, and that does not make the kit's tests Settings
    # tests. A file most pages load at the same distance (ui.tsx, types.ts) belongs to none.
    page_of_file = {p["file"]: p["id"] for p in pages}

    def pages_of_file(rel: str) -> set:
        if rel in page_of_file:
            return {page_of_file[rel]}
        d = page_depth.get(rel) or {}
        if not d:
            return set()
        near = min(d.values())
        owners = {pid for pid, n in d.items() if n == near}
        return owners if len(owners) <= OWN_MAX else set()

    # Tests: what each suite reaches.
    tests = {"backend": {}, "frontend": {}}
    for rel in files:
        if re.match(r"^backend/[^/]+_test\.py$", rel):
            text = read(rel)
            hit = set()
            mods = py_test_refs(text)
            for mod in mods:
                hit |= areas_of_module(mod)
            for path in API_LITERAL.findall(text):
                hit |= areas_of_path(re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "X", path))
            doc = ""
            try:
                doc = ast.get_docstring(ast.parse(text)) or ""
            except SyntaxError:
                pass
            # Some backend suites bundle the editor's own TypeScript and run it: those name it.
            pg = set()
            for f in web_test_refs(text, rel, fileset):
                pg |= pages_of_file(f)
            tests["backend"][Path(rel).name] = {"areas": sorted(hit), "pages": sorted(pg),
                                                "modules": sorted(mods), "summary": first_sentence(doc)}
    pkg = {}
    try:
        pkg = json.loads(read("frontend/package.json") or "{}")
    except ValueError:
        pass
    for name, cmd in sorted((pkg.get("scripts") or {}).items()):
        if not name.startswith("test:"):
            continue
        hit_pages, hit_areas, summary = set(), set(), ""
        for t in re.findall(r"\btests/[\w.-]+\.(?:tsx?|jsx?|mjs|cjs)\b", cmd):
            rel = "frontend/" + t
            text = read(rel)
            if not text:
                continue
            for f in web_test_refs(text, rel, fileset):
                hit_pages |= pages_of_file(f)
            for fn in re.findall(r"\bapi\.(\w+)\b", text):
                hit_areas.update(client_areas.get(fn, []))
            for path in API_LITERAL.findall(text):
                hit_areas |= areas_of_path(re.sub(r"\$\{[^}]*\}", "X", path))
            if not summary:
                c = re.match(r"\s*(?:/\*\*?([\s\S]*?)\*/|((?://[^\n]*\n)+))", text)
                if c:
                    raw = c.group(1) or c.group(2) or ""
                    summary = first_sentence(" ".join(l.strip().lstrip("*/").strip() for l in raw.splitlines()))
        tests["frontend"][name] = {"areas": sorted(hit_areas), "pages": sorted(hit_pages),
                                   "summary": summary}
    m = re.search(r"^SLOW\s*=\s*\{([^}]*)\}", read(PKG + "/agent_tools.py"), re.M)
    slow = sorted(re.findall(r"""["']([\w.]+)["']""", m.group(1))) if m else []

    for a in areas.values():
        aid = a["id"]
        a["settings"] = sorted(k for k, s in settings.items() if aid in s["areas"])
        a["programs"] = sorted(p for p, mods in programs.items()
                               if any(aid in areas_of_module(md) for md in mods))
        a["tests"] = sorted(n for side in ("backend", "frontend") for n, t in tests[side].items()
                            if aid in t["areas"])
        a["pages"] = sorted(p["id"] for p in pages if aid in p["areas"])
    for p in pages:
        p["tests"] = sorted(n for side in ("backend", "frontend") for n, t in tests[side].items()
                            if p["id"] in t["pages"])

    # The hand-written half, matched to the parts.
    checks = parse_checks(read(CHECKS))
    features = [c for c in checks if not c["title"].startswith("_")]
    special = {c["title"]: c for c in checks if c["title"].startswith("_")}
    known = set(areas) | {"page:" + p["id"] for p in pages}
    named = {p for f in features for p in f["parts"]}
    by_page = {p["id"]: p for p in pages}
    # A route group written but not committed yet is not stale: it is on disk and git does not list
    # it. It stays out of the map (the map describes what git holds) and is reported separately.
    pending = sorted({p for f in features for p in f["parts"]
                      if p not in known and (ROOT / ROUTERS / (p + ".py")).is_file()})
    stale = [(p, f["title"]) for f in features for p in f["parts"] if p not in known and p not in pending]
    # A module is there when its file or its package is, committed yet or not.
    stale += [("module " + m, f["title"]) for f in features for m in f["modules"]
              if not ((ROOT / PKG / (m + ".py")).is_file() or (ROOT / PKG / m / "__init__.py").is_file())]
    for f in features:
        # The suites that check this feature: those of its parts, and those that reach the
        # modules it names.
        ts = set()
        for part in f["parts"]:
            if part in areas:
                ts.update(areas[part]["tests"])
            elif part.startswith("page:") and part[5:] in by_page:
                ts.update(by_page[part[5:]]["tests"])
        for side in ("backend", "frontend"):
            for n, t in tests[side].items():
                if set(t.get("modules") or []) & set(f["modules"]):
                    ts.add(n)
        all_suites = set(tests["backend"]) | set(tests["frontend"])
        ts.update(n for n in f["claims"] if n in all_suites)
        stale.extend(("test " + n, f["title"]) for n in f["claims"]
                     if n not in all_suites and not (ROOT / "backend" / n).is_file())
        f["tests"] = sorted(ts)
    covered = {n for f in features for n in f["tests"]}
    return {
        "areas": areas, "pages": pages, "tests": tests, "slow_tests": slow,
        "settings": settings, "programs": programs, "skills": skills_table(files),
        "features": features, "special": special,
        "undescribed": sorted(known - named),
        "stale": sorted(stale),
        "pending": pending,
        "orphan_tests": sorted(n for side in ("backend", "frontend") for n in tests[side]
                               if n not in covered) if features else [],
        "endpoint_count": len(all_eps),
    }


# ---------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------
def render_area(a: dict, out: list) -> None:
    pre = ", ".join("`%s`" % p for p in a["prefixes"]) or "the app itself"
    out.append("#### API `%s` - %s, %d endpoints" % (a["id"], pre, len(a["endpoints"])))
    out.append("")
    out.append("Code: `%s`" % a["file"])
    if a.get("error"):
        out.append("")
        out.append("**This file %s** The map cannot list its routes." % a["error"])
    if a["purpose"]:
        out.append("")
        out.append("> " + a["purpose"])
    if a["endpoints"]:
        out.append("")
        out.append("| Method | Path | What it does |")
        out.append("| --- | --- | --- |")
        for e in a["endpoints"]:
            out.append("| %s | `%s` | %s |" % (e["method"], e["path"], cell(e["summary"])))
    out.append("")
    for label, key, fmt in (("Settings", "settings", "`%s`"), ("Programs it calls", "programs", "`%s`"),
                            ("Pages that use it", "pages", "`page:%s`"), ("Tests", "tests", "`%s`")):
        if a[key]:
            out.append("- %s: %s" % (label, ", ".join(fmt % x for x in a[key])))
    if not a["tests"]:
        out.append("- Tests: **none reach this part.**")
    out.append("")


def render_page(p: dict, out: list) -> None:
    out.append("#### UI `page:%s` - %s (%s)" % (p["id"], p["label"], p["window"]))
    out.append("")
    out.append("Code: `%s` and %d files it loads" % (p["file"], p["source_files"] - 1))
    if p["purpose"]:
        out.append("")
        out.append("> " + p["purpose"])
    out.append("")
    if p["areas"]:
        out.append("- API groups it calls: " + ", ".join("`%s`" % a for a in p["areas"]))
    out.append("- Tests: " + (", ".join("`%s`" % t for t in p["tests"]) if p["tests"]
                              else "**none reach this page.**"))
    out.append("")


def render_md(d: dict) -> str:
    areas, pages = d["areas"], {p["id"]: p for p in d["pages"]}
    n_tests = len(d["tests"]["backend"]) + len(d["tests"]["frontend"])
    out = [
        "# Asset Studio - what must work, and how to check it",
        "",
        "<!-- GENERATED by scripts/features/build.py. Do not edit this file: edit "
        "docs/features/checks.md (what each feature must do) or the code. -->",
        "",
        "This file is made from the code. A git hook builds it again on each commit, and GitHub "
        "Actions builds it again on each push to `main`, so it always describes the code beside it.",
        "",
        "- What each feature must do and how to check it is written by hand in "
        "`docs/features/checks.md`.",
        "- Everything else - every route, page, setting, program and test - is read from the code.",
        "- `scripts/features/verify.py` checks a running installation against this file.",
        "",
        "**Size:** %d features, %d API groups with %d endpoints, %d UI pages, %d settings, %d test suites."
        % (len(d["features"]), len(areas), d["endpoint_count"], len(pages), len(d["settings"]), n_tests),
        "",
    ]
    for title in ("_how-to-check", "_install"):
        sec = d["special"].get(title)
        if sec:
            out.append("## " + {"_how-to-check": "How to check an installation",
                                 "_install": "What must be installed"}[title])
            out.append("")
            out.append(sec["body"])
            out.append("")
            if title == "_install":
                out.append("| Program | Needed | What for | Where it comes from |")
                out.append("| --- | --- | --- | --- |")
                for p in verify.PROGRAMS:
                    out.append("| `%s` | %s | %s | %s |" % (p["name"], p["need"], cell(p["for"]), cell(p["from"])))
                out.append("")
                found = sorted(d["programs"])
                out.append("Programs the backend looks for on `PATH` (read from the code): "
                           + ", ".join("`%s`" % p for p in found) + ".")
                out.append("")

    out.append("## Features")
    out.append("")
    for i, f in enumerate(d["features"], 1):
        out.append("%d. [%s](#%d-%s)" % (i, f["title"], i, f["id"]))
    out.append("")
    shown: dict = {}                     # a part two features share is listed in full once
    for i, f in enumerate(d["features"], 1):
        out.append("### %d. %s" % (i, f["title"]))
        out.append("")
        out.append(f["body"])
        out.append("")
        if f["parts"]:
            out.append("**Parts:** " + ", ".join("`%s`" % p for p in f["parts"]))
            out.append("")
        if f["modules"]:
            out.append("**Backend modules:** " + ", ".join("`%s`" % m for m in f["modules"]))
            out.append("")
        out.append("**Test suites for this feature:** " + (", ".join("`%s`" % t for t in f["tests"])
                                                           or "**none.**"))
        out.append("")
        for part in f["parts"]:
            if part in shown:
                out.append("#### `%s` - listed in full under feature %d." % (part, shown[part]))
                out.append("")
                continue
            shown[part] = i
            if part.startswith("page:") and part[5:] in pages:
                render_page(pages[part[5:]], out)
            elif part in areas:
                render_area(areas[part], out)
            else:
                out.append("#### `%s` - **no such part in the code.** Fix `docs/features/checks.md`." % part)
                out.append("")

    out.append("## Parts no feature describes yet")
    out.append("")
    if d["undescribed"]:
        out.append("The code has these parts, and no section of `docs/features/checks.md` names them. "
                   "They must work too. Add them to a feature in that file.")
        out.append("")
        for part in d["undescribed"]:
            if part.startswith("page:"):
                render_page(pages[part[5:]], out)
            else:
                render_area(areas[part], out)
    else:
        out.append("None. Each part of the code belongs to a feature above.")
        out.append("")
    if d["stale"]:
        out.append("## Written checks for parts that do not exist")
        out.append("")
        for part, title in d["stale"]:
            out.append("- `%s`, named by \"%s\"" % (part, title))
        out.append("")
    if d["orphan_tests"]:
        out.append("## Test suites no feature claims")
        out.append("")
        out.append("They run with `--tests all` like the others. Name their part or module in a "
                   "feature of `docs/features/checks.md` so a failure points at a feature.")
        out.append("")
        out.append(", ".join("`%s`" % n for n in d["orphan_tests"]))
        out.append("")

    out.append("## All test suites")
    out.append("")
    out.append("Run one backend suite from `backend/` with the backend's Python: "
               "`.venv/Scripts/python.exe <name>`. Run one frontend suite from `frontend/`: "
               "`npm run <name>`. `scripts/features/verify.py --tests all` runs all of them.")
    if d["slow_tests"]:
        out.append("")
        out.append("Slow, and left out of \"all\": " + ", ".join("`%s`" % s for s in d["slow_tests"])
                   + ". Run it by name.")
    out.append("")
    out.append("| Suite | Side | Reaches | What it checks |")
    out.append("| --- | --- | --- | --- |")
    for side in ("backend", "frontend"):
        for name, t in sorted(d["tests"][side].items()):
            reach = ", ".join(["`%s`" % a for a in t["areas"]] + ["`page:%s`" % p for p in t["pages"]])
            out.append("| `%s` | %s | %s | %s |" % (name, side, reach or "-", cell(t["summary"])))
    out.append("")

    out.append("## All settings")
    out.append("")
    out.append("Read from `settings.get(...)` in the backend. The label is the one the Settings page shows.")
    out.append("")
    out.append("| Key | Label in Settings | Default in the code | Used by |")
    out.append("| --- | --- | --- | --- |")
    for key, s in sorted(d["settings"].items()):
        out.append("| `%s` | %s | %s | %s |" % (
            key, cell(s["label"]) or "-",
            cell(" / ".join("`%s`" % x for x in s["defaults"])) or "-",
            ", ".join("`%s`" % a for a in s["areas"]) or ", ".join("`%s`" % m for m in s["modules"])))
    out.append("")

    out.append("## Skills shipped with the Studio")
    out.append("")
    out.append("| Skill | What it is for |")
    out.append("| --- | --- |")
    for s in d["skills"]:
        out.append("| `%s` | %s |" % (s["name"], cell(s["summary"])))
    out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_json(d: dict) -> str:
    data = {
        "schema": 1,
        "features": [{k: f[k] for k in ("id", "title", "parts", "modules", "smoke", "tests")}
                     for f in d["features"]],
        "areas": {aid: {k: a[k] for k in ("file", "prefixes", "purpose", "settings", "programs", "tests", "pages")}
                  | {"endpoints": [{k: e[k] for k in ("method", "path", "function", "summary")} for e in a["endpoints"]]}
                  for aid, a in sorted(d["areas"].items())},
        "pages": {p["id"]: {k: p[k] for k in ("label", "file", "window", "areas", "tests")} for p in d["pages"]},
        "tests": d["tests"],
        "slow_tests": d["slow_tests"],
        "settings": {k: {"label": s["label"], "defaults": s["defaults"], "areas": s["areas"]}
                     for k, s in sorted(d["settings"].items())},
        "undescribed": d["undescribed"],
    }
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def outputs() -> dict:
    d = collect()
    return {OUT_MD: render_md(d), OUT_JSON: render_json(d), "_report": d}


def _same(rel: str, text: str) -> bool:
    try:
        return (ROOT / rel).read_bytes().decode("utf-8").replace("\r\n", "\n") == text
    except (OSError, UnicodeDecodeError):
        return False


def write_all(quiet: bool = False) -> list:
    got = outputs()
    changed = []
    for rel in (OUT_MD, OUT_JSON):
        if not _same(rel, got[rel]):
            p = ROOT / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(got[rel].encode("utf-8"))
            changed.append(rel)
    d = got["_report"]
    if not quiet:
        print("%s: %d features, %d API groups, %d endpoints, %d pages, %d settings; %s"
              % (OUT_MD, len(d["features"]), len(d["areas"]), d["endpoint_count"], len(d["pages"]),
                 len(d["settings"]), ("updated " + ", ".join(changed)) if changed else "no change"))
    if d["undescribed"]:
        print("  not described in %s yet: %s" % (CHECKS, ", ".join(d["undescribed"])))
    if d["stale"]:
        print("  %s names parts that do not exist: %s"
              % (CHECKS, ", ".join(sorted({p for p, _ in d["stale"]}))))
    if d["orphan_tests"]:
        print("  test suites no feature claims: %s" % ", ".join(d["orphan_tests"]))
    if d["pending"]:
        print("  on disk but not in git yet (the map lists them once committed): %s" % ", ".join(d["pending"]))
    return changed


# ---------------------------------------------------------------------------
# The git hook
# ---------------------------------------------------------------------------
SHIM = """#!/bin/sh
# %s: build FEATURES.md again from the code, and add it to this commit when it changed.
# Installed by scripts/features/build.py --install-hook. It never stops a commit.
root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
script="$root/scripts/features/build.py"
[ -f "$script" ] || exit 0
for py in "$root/backend/.venv/Scripts/python.exe" "$root/backend/.venv/bin/python" \\
          "$root/runtime/python/python.exe" python3 python py; do
  case "$py" in
    /*|?:*) [ -x "$py" ] || continue ;;
    *) command -v "$py" >/dev/null 2>&1 || continue ;;
  esac
  # On Windows `python3` can be the Store's stub, which only prints a message: test it first.
  "$py" -c "import sys; sys.exit(sys.version_info < (3, 9))" >/dev/null 2>&1 || continue
  "$py" "$script" --hook || true
  exit 0
done
exit 0
""" % HOOK_MARK


def hook() -> int:
    """Write the map, and stage it when it changed. Any failure is reported and ignored."""
    try:
        changed = write_all(quiet=True)
        if changed:
            subprocess.run(["git", "add", "--"] + changed, cwd=ROOT, check=False, timeout=60)
            print("feature map: updated %s" % ", ".join(changed))
    except Exception as ex:                                  # never stop a commit
        print("feature map: not updated (%s: %s)" % (type(ex).__name__, ex), file=sys.stderr)
    return 0


def install_hook() -> int:
    try:
        hooks = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT,
                               capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    except Exception:
        print("no git here: the hook is only for a clone of the repository")
        return 0
    target = (ROOT / hooks / "pre-commit").resolve()
    if target.is_file() and HOOK_MARK not in target.read_text(encoding="utf-8", errors="replace"):
        print("%s already holds another pre-commit hook. Add this line to it:\n"
              "  python scripts/features/build.py --hook" % target)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SHIM.encode("utf-8"))
    try:
        target.chmod(0o755)
    except OSError:
        pass
    print("installed %s" % target)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="exit 1 when FEATURES.md is out of date")
    g.add_argument("--hook", action="store_true", help="the pre-commit hook")
    g.add_argument("--install-hook", action="store_true", help="install the pre-commit hook")
    a = ap.parse_args()
    if a.hook:
        return hook()
    if a.install_hook:
        return install_hook()
    if a.check:
        got = outputs()
        stale = [rel for rel in (OUT_MD, OUT_JSON) if not _same(rel, got[rel])]
        if stale:
            print("out of date: %s - run python scripts/features/build.py" % ", ".join(stale))
            return 1
        print("FEATURES.md is in step with the code")
        return 0
    write_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())

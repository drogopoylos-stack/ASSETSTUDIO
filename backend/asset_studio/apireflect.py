# -*- coding: utf-8 -*-
"""What the modelling library actually exports, read out of the source.

Unity's MCP has `unity_reflect`: the agent asks the live API what it exposes instead of guessing
and finding out when it throws. This is that, for our own library — and it matters more here,
because `forge-ops.js` is a module an agent imports into code it is writing blind.

READ FROM THE SOURCE, NOT FROM A LIST. A hand-written catalogue of "the tools we have" drifts the
first time somebody adds one and forgets, and a drifted catalogue is worse than none: it teaches
the agent a function that is not there. So every answer here is parsed out of the .ts files, and
then cross-checked against the built bundle. If the parser claims something the shipped bundle
does not contain, the answer says so rather than quietly passing it on.

CHEAP BY DEFAULT. The whole point is to save tokens, so the default answer is names grouped by
subject and nothing else — a few hundred tokens. Ask by name or by search term to get signatures,
defaults and the one-line doc for just the entries you care about.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
SRC = FRONTEND / "src" / "components" / "engine" / "edit"
BUNDLE = FRONTEND / "dist" / "forge-ops.js"

# The modules that make up what an agent imports. `ops.ts` is the bundle's entry; `vertedit.ts` is
# the editor's own half, reachable in the app but not in forge-ops.js, and it is labelled as such
# so nobody imports it over HTTP and wonders why it 404s.
MODULES = [
    ("ops", "ops.ts", "forge-ops.js", True),
    ("vertedit", "vertedit.ts", "", False),
    # Bundled into forge-ops.js by ops.ts's `export *`. model.ts was bundled and never listed, so its
    # bakes and remesh were invisible here; the rest are the goblin A/B's tools (2026-09-24). A file
    # not written yet is skipped, not an error.
    ("model", "model.ts", "forge-ops.js", True),
    ("material", "material.ts", "forge-ops.js", True),
    ("atlas", "atlas.ts", "forge-ops.js", True),
    ("sculpt", "sculpt.ts", "forge-ops.js", True),
    ("decimate", "decimate.ts", "forge-ops.js", True),
    ("sweep", "sweep.ts", "forge-ops.js", True),
    ("hands", "hands.ts", "forge-ops.js", True),
]

_IDENT = re.compile(r"[A-Za-z0-9_$]")


def _strip_comments_for_scan(s: str) -> str:
    """A copy with comments and string bodies blanked, for structural scanning only.

    Positions are preserved exactly, so an offset found in the blanked copy indexes the original.
    Without this a `//` inside a doc line or a brace inside a string throws the depth counting off.
    """
    out = list(s)
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            while i < n and s[i] != "\n":
                out[i] = " "
                i += 1
        elif c == "/" and nxt == "*":
            while i < n and not (s[i] == "*" and i + 1 < n and s[i + 1] == "/"):
                if s[i] != "\n":
                    out[i] = " "
                i += 1
            for k in range(i, min(i + 2, n)):
                out[k] = " "
            i += 2
        elif c in "\"'`":
            q = c
            i += 1
            while i < n and s[i] != q:
                if s[i] == "\\":
                    out[i] = " "
                    i += 1
                if i < n and s[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
            i += 1
        else:
            i += 1
    return "".join(out)


def _match_paren(scan: str, open_at: int) -> int:
    """Index of the `)` closing the `(` at `open_at`, or -1."""
    depth = 0
    for i in range(open_at, len(scan)):
        if scan[i] == "(":
            depth += 1
        elif scan[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _prev_meaningful(scan: str, i: int) -> str:
    j = i - 1
    while j >= 0 and scan[j].isspace():
        j -= 1
    return scan[j] if j >= 0 else ""


def _body_start(scan: str, after: int) -> int:
    """Where a function's BODY opens, skipping a return type that is itself an object literal.

    `): number {` and `): { pos: Float32Array; moved: number } {` both have to work. The rule that
    separates them: a `{` that follows `:` `|` `&` `,` `<` or `(` is part of a TYPE; a `{` that
    follows a complete type — an identifier, `}`, `]`, `>` or `)` — opens the body.
    """
    depth = 0
    i = after
    while i < len(scan):
        c = scan[i]
        if c == "{":
            if depth == 0 and _prev_meaningful(scan, i) not in (":", "|", "&", ",", "<", "("):
                return i
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == ";" and depth == 0:
            return -1          # a declaration with no body (an overload or an ambient signature)
        i += 1
    return -1


def _tidy(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(",")


def _doc_before(src: str, at: int) -> str:
    """The first sentence of the `/** ... */` immediately above a declaration."""
    head = src[:at].rstrip()
    if not head.endswith("*/"):
        return ""
    start = head.rfind("/**")
    if start < 0:
        return ""
    body = head[start + 3:-2]
    lines = []
    for ln in body.splitlines():
        ln = ln.strip().lstrip("*").strip()
        if not ln:
            if lines:
                break
            continue
        if ln.startswith("@"):
            break
        lines.append(ln)
    one = " ".join(lines).strip()
    # One sentence is enough for a picker; the rest is in the file for anyone who opens it.
    m = re.search(r"^(.{0,220}?[.!?])(\s|$)", one)
    return (m.group(1) if m else one[:220]).strip()


def parse_module(path: Path) -> dict:
    """Every export in one TypeScript module: functions with real parameter names, and the
    interfaces that describe what they take and return."""
    src = path.read_text(encoding="utf-8")
    scan = _strip_comments_for_scan(src)
    fns: list[dict] = []
    consts: list[dict] = []
    types: list[dict] = []

    for m in re.finditer(r"\bexport\s+(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*(?:<[^>(]*>)?\s*\(", scan):
        name = m.group(1)
        op = scan.index("(", m.end() - 1)
        cp = _match_paren(scan, op)
        if cp < 0:
            continue
        body = _body_start(scan, cp + 1)
        ret = src[cp + 1:body] if body > 0 else ""
        ret = _tidy(ret.lstrip().lstrip(":")) if ret.strip().startswith(":") else ""
        fns.append({
            "name": name,
            "params": _tidy(src[op + 1:cp]),
            "returns": ret,
            "doc": _doc_before(src, m.start()),
        })

    for m in re.finditer(r"\bexport\s+const\s+([A-Za-z0-9_$]+)(?:\s*:\s*[^=]+?)?\s*=\s*", scan):
        name = m.group(1)
        line_end = scan.find("\n", m.end())
        rest = src[m.end():line_end if line_end > 0 else len(src)]
        arrow = re.match(r"\s*(?:<[^>]*>)?\s*\(", rest)
        if arrow:
            op = scan.index("(", m.end())
            cp = _match_paren(scan, op)
            if cp > 0:
                fns.append({
                    "name": name, "params": _tidy(src[op + 1:cp]), "returns": "",
                    "doc": _doc_before(src, m.start()),
                })
                continue
        consts.append({
            "name": name, "value": _tidy(rest)[:80].rstrip(";"),
            "doc": _doc_before(src, m.start()),
        })

    for m in re.finditer(r"\bexport\s+(interface|type)\s+([A-Za-z0-9_$]+)", scan):
        kind, name = m.group(1), m.group(2)
        members: list[str] = []
        if kind == "interface":
            ob = scan.find("{", m.end())
            if ob > 0:
                depth, i = 0, ob
                while i < len(scan):
                    if scan[i] == "{":
                        depth += 1
                    elif scan[i] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                for ln in src[ob + 1:i].splitlines():
                    ln = ln.strip()
                    if not ln or ln.startswith("//") or ln.startswith("*") or ln.startswith("/*"):
                        continue
                    members.append(ln.rstrip(";,"))
        types.append({"name": name, "kind": kind, "members": members,
                      "doc": _doc_before(src, m.start())})

    fns.sort(key=lambda f: f["name"])
    return {"file": path.name, "functions": fns, "consts": consts, "types": types}


def _bundle_names() -> Optional[set]:
    """What the SHIPPED bundle really exports. None when it has not been built.

    The cross-check that stops this endpoint teaching an agent a function that only exists in a
    source tree nobody rebuilt.
    """
    if not BUNDLE.exists():
        return None
    text = BUNDLE.read_text(encoding="utf-8", errors="replace")
    tail = text[text.rfind("export {"):] if "export {" in text else ""
    names: set = set()
    for part in re.split(r"[{},\n]", tail):
        part = part.strip()
        if not part or part == "export":
            continue
        # esbuild writes `localName as exportedName`; the exported half is what an importer types.
        m = re.match(r"^(?:[A-Za-z0-9_$]+\s+as\s+)?([A-Za-z0-9_$]+)$", part)
        if m:
            names.add(m.group(1))
    return names or None


def _entries(mod: dict) -> Iterable[dict]:
    for f in mod["functions"]:
        yield {"kind": "function", **f}
    for c in mod["consts"]:
        yield {"kind": "const", **c}
    for t in mod["types"]:
        yield {"kind": t["kind"], "name": t["name"], "doc": t["doc"], "members": t["members"]}


def _base() -> str:
    """This Studio's own origin, so the `import` line in the answer can be pasted as-is."""
    try:
        from .config import settings
        host = str(settings.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return "http://%s:%s" % (host, settings.get("port") or 8777)
    except Exception:
        return "http://127.0.0.1:8777"


def _field_type(ann) -> str:
    """A type an agent can act on, not a repr of a typing object."""
    s = str(ann)
    s = s.replace("typing.", "").replace("<class '", "").replace("'>", "")
    s = s.replace("Optional[", "").rstrip("]") if s.startswith("Optional[") else s
    return s.strip() or "any"


def bodies() -> dict:
    """Every field of every live request body, with its type and default.

    READ OFF THE MODELS, NOT WRITTEN DOWN. The agent note has a hard character ceiling, so a new
    field could only be announced by taking a sentence away from something else - which is how a
    documented API stops matching the one that is running. FastAPI already holds all of it.
    """
    out: dict = {"ok": True, "note": "every field of every POST /api/live/* body, from the "
                                     "models themselves. Anything absent here does not exist."}
    try:
        from .routers import live as _routes
    except Exception as ex:                                 # pragma: no cover - import guard
        return {"ok": False, "error": "could not read the live routes: %s" % str(ex)[:200]}

    used: dict = {}
    try:
        for r in getattr(_routes.router, "routes", []):
            fn = getattr(r, "endpoint", None)
            for nm, ann in getattr(fn, "__annotations__", {}).items():
                # `from __future__ import annotations` makes every one of these a STRING, so the
                # class it names has to be read off the text. Taking `ann.__name__` matched
                # nothing and left every route unattributed.
                cls = ann if isinstance(ann, str) else getattr(ann, "__name__", "")
                cls = str(cls).strip().strip("'\"")
                if cls.endswith("Body"):
                    used.setdefault(cls, []).append(getattr(r, "path", ""))
    except Exception:
        pass

    rows = []
    for nm in sorted(vars(_routes)):
        cls = getattr(_routes, nm)
        if not isinstance(cls, type) or not nm.endswith("Body"):
            continue
        fields = getattr(cls, "model_fields", None)            # pydantic v2
        if fields is None:
            fields = getattr(cls, "__fields__", None)           # pydantic v1
        if not fields:
            continue
        items = []
        for fname, f in fields.items():
            ann = getattr(f, "annotation", None) or getattr(f, "outer_type_", None)
            dflt = getattr(f, "default", None)
            try:
                if dflt is not None and type(dflt).__name__ == "PydanticUndefinedType":
                    dflt = "(required)"
            except Exception:
                pass
            items.append({"name": fname, "type": _field_type(ann), "default": dflt})
        rows.append({"body": nm, "used_by": sorted(set(used.get(nm, []))), "fields": items})
    out["bodies"] = rows
    out["count"] = sum(len(r["fields"]) for r in rows)
    return out


def reflect(q: str = "", name: str = "", base: str = "") -> dict:
    """The answer the endpoint returns.

    No arguments: names only, grouped, plus the import line — a few hundred tokens.
    `q`: every entry whose name or doc matches, in full.
    `name`: one entry in full, including an interface's members. `name=Ops` is the one an agent
    wants most: it is the exact contract of `makeOps(THREE)`.
    """
    base = base or _base()
    mods = []
    for key, fname, served, over_http in MODULES:
        p = SRC / fname
        if not p.exists():
            continue
        m = parse_module(p)
        m["module"] = key
        m["served_as"] = served
        m["over_http"] = over_http
        mods.append(m)

    shipped = _bundle_names()
    out: dict = {
        "ok": True,
        "import": ("const m = await import('%s/forge-ops.js');" % base.rstrip("/")) if base else
                  "const m = await import('<studio>/forge-ops.js');",
        "note": "makeOps(THREE) returns the Ops interface — ask name=Ops for its exact members.",
    }

    want_name = (name or "").strip()
    want_q = (q or "").strip().lower()

    # `name=bodies` is the OTHER half of reflection: not what the library exports, but what every
    # HTTP call will accept. A field added to a request body used to be invisible unless somebody
    # also spent characters of the agent note on it.
    if want_name.lower() in ("bodies", "body", "fields"):
        got = bodies()
        got["import"] = out["import"]
        return got

    if want_name:
        hits = [e for m in mods for e in _entries(m) if e["name"] == want_name]
        out["entries"] = hits
        out["found"] = len(hits)
        if not hits:
            near = sorted({e["name"] for m in mods for e in _entries(m)
                           if want_name.lower() in e["name"].lower()})[:12]
            out["did_you_mean"] = near
        return _mark_shipped(out, shipped)

    if want_q:
        hits = []
        for m in mods:
            for e in _entries(m):
                named = want_q in e["name"].lower() or want_q in (e.get("doc") or "").lower()
                # SEARCH THE MEMBERS TOO. `weld` is not a top-level export — it is a member of the
                # `Ops` interface, which is what `makeOps(THREE)` hands back. A search that only
                # looked at names would answer "no such tool" about a tool that exists.
                inner = [ln for ln in (e.get("members") or []) if want_q in ln.lower()]
                if not named and not inner:
                    continue
                e = dict(e)
                if inner and not named:
                    e["members"] = inner
                    e["from"] = "a member of " + e["name"] + " — reached as makeOps(THREE)." \
                        + inner[0].split("(")[0].split(":")[0].strip() \
                        if e["name"] == "Ops" else "a member of " + e["name"]
                elif e.get("members") and len(e["members"]) > 14:
                    # An interface's full body is the bulky part; a search wants the shape.
                    e["members"] = e["members"][:14] + ["… %d more, ask name=%s" %
                                                        (len(e["members"]) - 14, e["name"])]
                hits.append(e)
        out["entries"] = hits
        out["found"] = len(hits)
        return _mark_shipped(out, shipped)

    bundled: dict = {}
    for m in mods:
        if m["module"] not in ("ops", "vertedit"):
            # The modules ops.ts re-exports: NAMES ONLY, so the default answer stays cheap. Their
            # types and signatures are one ?name= away, like everything else.
            bundled[m["module"]] = [f["name"] for f in m["functions"]] + [c["name"] for c in m["consts"]]
            continue
        out[m["module"]] = {
            "file": m["file"],
            "served_as": m["served_as"] or "not served over HTTP — it is the editor's own module",
            "functions": [f["name"] for f in m["functions"]],
            "consts": [c["name"] for c in m["consts"]],
            "types": [t["name"] for t in m["types"]],
        }
    if bundled:
        out["also_in_forge_ops"] = bundled
    out["how"] = "Add ?name=<X> for one entry in full, or ?q=<word> to search names and docs."
    return _mark_shipped(out, shipped)


def _mark_shipped(out: dict, shipped: Optional[set]) -> dict:
    """Say plainly whether the built bundle agrees with the source that was parsed."""
    if shipped is None:
        out["bundle"] = "not built — run `npm run build:ops`; these names come from the source only"
        return out
    named = []
    for e in out.get("entries", []) or []:
        # A TYPE IS NOT IN THE BUNDLE AND NEVER CAN BE. TypeScript erases interfaces and type
        # aliases at build, so checking `Ops` against the JavaScript exports reported the most
        # useful entry in the whole answer as missing.
        if e.get("kind") in ("interface", "type"):
            continue
        named.append(e["name"])
    if not named:
        for key in ("ops",):
            named += (out.get(key) or {}).get("functions", []) + (out.get(key) or {}).get("consts", [])
    missing = [n for n in named if n not in shipped]
    out["bundle"] = {"exports": len(shipped),
                     "not_in_bundle": missing[:20]} if missing else {"exports": len(shipped)}
    if missing:
        out["bundle"]["why"] = ("in the source but not in the built forge-ops.js — rebuild, or it is "
                                "from a module that is not the bundle's entry")
    return out

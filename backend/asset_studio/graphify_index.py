"""Background code knowledge-graph indexing for the Graphify toggle.

When ``cc_graphify`` is on, the Studio keeps each workspace's code graph
(``<workspace>/graphify-out/graph.json``) fresh so Claude can query it (explain /
path / query) instead of grepping. It is **code-only** — pure tree-sitter AST,
so **zero tokens, no API key** — and ``extract()`` caches per file, so a re-run
only re-parses the files that changed (the "only the new code" requirement).

It is fire-and-forget: ``refresh()`` returns instantly and the build runs in a
daemon thread, debounced + rate-limited so it never blocks a send and never
piles up. graphify's own detector skips ``node_modules`` / ``.venv`` / ``data`` /
``dist`` / ``.git``, so pointing it at the workspace root indexes only real source.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR, claude_home, pip_attempts, venv_has

_LOCK = threading.Lock()
# workspace -> {"running", "pending": a build is in flight / queued
#               "last":     when the last build finished
#               "covers":   when the last SUCCESSFUL build started. The graph describes the code
#                           as of that moment, so a change after it is not in the graph yet
#               "changed":  when a source file last changed
#               "changed_files": the ones no build has picked up yet}
_state: dict[str, dict] = {}
_GPY: Optional[str] = None
# Studio-owned, self-contained graphify install — NO pipx, NO PATH dependency — so the Graphify
# toggle actually works on a fresh PC where setup's pipx install failed. Lives under the data dir.
_VENV_DIR = DATA_DIR / "tools" / "graphify-venv"
_INSTALL: dict = {"installing": False, "done": False, "error": "", "started": 0.0}
_MIN_INTERVAL = 12.0                 # never rebuild one workspace more often than this
_DEBOUNCE = 1.2                      # coalesce a burst of triggers/edits before building
_NF = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _ws_key(p: str) -> str:
    """One spelling of a workspace path, so the watcher and a query hit the same state record."""
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def _st(ws: str) -> dict:
    """The state record for a workspace. Call with _LOCK held."""
    return _state.setdefault(ws, {"running": False, "pending": False, "last": 0.0,
                                  "covers": 0.0, "changed": 0.0, "changed_files": set()})


def _st_read(root: str) -> dict:
    """A snapshot of that record for a reader.

    Windows hands us the same workspace under different letter case — one spelling from a query
    URL, another from the watcher — so fall back to a case-insensitive match. Without it a query
    would find no record and report a stale graph as fresh, which is the one wrong answer this
    whole mechanism exists to prevent."""
    ws = _ws_key(root)
    with _LOCK:
        st = _state.get(ws)
        if st is None:
            low = ws.lower()
            st = next((v for k, v in _state.items() if k.lower() == low), None)
        if st is None:
            return {}
        snap = dict(st)
        snap["changed_files"] = set(st.get("changed_files") or ())
        return snap


def note_change(root: str, paths: Any = ()) -> None:
    """Record that source files changed — the input to the staleness note on every answer.

    The filesystem watcher is the only caller, because it is the only signal that means "the code
    moved". refresh() is NOT that signal: it also fires when a chat message is sent, so driving
    staleness from it would mark a perfectly current graph stale on every turn."""
    ws = _ws_key(root)
    with _LOCK:
        st = _st(ws)
        st["changed"] = time.time()
        files = st["changed_files"]
        for p in paths:
            if len(files) >= 200:            # a hint for the reader, not a changelog
                break
            try:
                files.add(str(Path(p).resolve().relative_to(Path(ws))).replace("\\", "/"))
            except (OSError, ValueError):
                pass

# The whole code-only pipeline as a single script run by the graphifyy interpreter:
# detect -> AST extract (cached) -> build -> cluster -> graph.json + GRAPH_REPORT.md.
#
# NO TRIPLE QUOTES IN HERE. This is one raw string, so a `"""` anywhere inside it ends the literal
# and dumps the rest of the script into this module at the top level — the failure surfaces as a
# syntax error about whatever character happens to sit a few lines further down, which is a long
# way from the actual mistake. Use `#` comments in the embedded script, never a docstring.
_BUILD_SRC = r"""
import sys, os, re, json, time, fnmatch, hashlib, subprocess
from pathlib import Path
from graphify.detect import detect
from graphify.extract import _get_extractor
from graphify.extract import collect_files, extract
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json

# Keep the graph about the USER'S code, not vendored / minified / built junk. graphify's own
# detect() only skips node_modules/.venv/.git/dist/data, so a checked-in vendor/ folder or an
# 8 MB built bundle can be 90% of the nodes and make the god-nodes meaningless (all pdf.js
# internals). We drop third-party dirs, *.min.js, oversized files, and minified files (huge
# average line length). A workspace .graphifyignore (gitignore-ish globs) can add more.
# The two lists arrive from the parent process (sys.argv[2]) so there is exactly one copy of
# this rule in the codebase. The literals below are only a fallback for a hand-run script.
_ALWAYS_SKIP = {"node_modules", "bower_components", "jspm_packages", "site-packages",
                "__pycache__", ".git", "graphify-out", "venv", ".venv",
                # somebody else's source. Real code, not ours, and bulky: three.module.js alone
                # is 2263 symbols and would out-vote every file in a small game.
                "vendor", "vendored", "third_party", "third-party", "thirdparty",
                "external", "externals",
                # names a TOOL owns. No human writes their own code into target/ (cargo, maven)
                # or .next/ — and a build tree is full of hand-written-looking files that pass
                # any "does this look generated?" test: cargo's target/ held three copies of
                # aws-lc's openssl headers, and they read as source because they are.
                "target", ".next", ".nuxt", ".svelte-kit", ".output", "coverage",
                "win-unpacked", "_site", "release", "minified"}
# MAYBE built. The NAME suggests compiler output, and usually it is — searching a real symbol
# once returned "P", "N", "I" out of deploy/public/.../brainrots-BfRVxtfb.js instead of the
# source. But PowderPeaks keeps its entire game in build/js/, so this same rule gave that project
# a graph of its tests with no game code in it at all, and nothing anywhere said so.
#
# A name is a hint, not a verdict. Every git project already states which paths are output, in
# .gitignore, and maintains that list for its own sake. Ask git and both directions come out
# right at once: PowderPeaks keeps build/ because git tracks it, and drops PLANNER-main/,
# brainrot-brawl/, art/work/ and deploy/public/ because git ignores them. This repo drops data/,
# frontend/dist/ and backend/third_party/ for the same reason. No list of ours has to keep up
# with anybody's layout. With no git to ask, the name is all there is, so it is used.
# The only four names a person plausibly gives their OWN source. Everything else that smells
# of output is now simply skipped; these four get looked inside, because PowderPeaks keeps its
# whole game in build/js and calling that "output" on the name cost a day.
_MAYBE_BUILT = {"dist", "build", "out", "deploy"}
# THE APP'S OWN TREES, named by the parent rather than guessed from a name here.
#
# Measured on this machine: of 39,815 nodes, 27,820 (69.9%) were `runtime/python/Lib` — the bundled
# CPython stdlib, idlelib included — and a further ~1,100 were `data/backups` and `data/tmp`, old
# copies of this very code. The graph was ~75% junk, so a prefetch could answer a question about
# `_LoopBoundMixin` instead of the symbol the user named, charge tokens for it, and report that it
# had saved a grep.
#
# The reason is not a missing name in the list below. `_git_ignored` asks git what a project calls
# output — and D:\Asset Studio is NOT a git repo, so it returns None and the walk falls back to
# names alone. Nothing in a name list can know that `runtime/` and `data/` here are the Studio's
# own install, because `runtime` is a perfectly plausible name for a game's own source. So the
# parent, which knows ROOT_DIR and DATA_DIR, says which top-level folders are ours; it is matched
# at the TOP LEVEL ONLY, and only when they actually sit inside this workspace.
_SKIP_TOP = set()
try:
    _cfg = json.loads(sys.argv[2])
    _ALWAYS_SKIP = set(_cfg["always"])
    _MAYBE_BUILT = set(_cfg["maybe"])
    _SKIP_TOP = {str(x).lower() for x in (_cfg.get("skip_top") or ())}
except Exception:
    pass


# The paths this project calls output. None when there is no git to ask.
def _git_ignored(root):
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--ignored",
                            "--exclude-standard", "--directory"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None
        return {ln.strip().rstrip("/").lower().replace(chr(92), "/")
                for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:
        return None


def _under(low, dirs):
    return any(low == d or low.startswith(d + "/") for d in dirs)


def _maybe_built(segs):
    # Is a DIRECTORY segment named like build output, with a suffix or not?
    #
    # `frontend/dist.old` held 329 nodes of a stale bundled copy of this very code, and an exact
    # match against {"dist", "build", "out", "deploy"} never sees it. This only widens what gets
    # DEFERRED to the evidence check below, never what gets skipped outright: `dist.old` full of
    # hashed bundles still reads as built, and a `build.source/` full of hand-written files still
    # reads as source. Callers must pass DIRECTORY segments only - `build.py` is somebody's source,
    # and splitting it on the dot is right for a folder and wrong for a file.
    #
    # COMMENTS, NOT A DOCSTRING. This function lives inside the raw string that carries the build
    # script, so a triple quote here would end the enclosing literal and turn the rest of the
    # script into module code at the top level. That is exactly what happened: the module stopped
    # importing, and the error pointed at an em-dash "outside a string" three lines further down.
    for s in segs:
        if re.split(r"[.\-_]", s, 1)[0] in _MAYBE_BUILT:
            return True
    return False


# The extensions graphify can actually parse, asked of graphify itself rather than guessed.
# Probed instead of hardcoded so this keeps up with the package: a list of ours would quietly
# stop covering a language the day they add one. Documents stay out — detect() already
# classifies those, and this walk is about code.
def _parse_exts():
    ext = set()
    for e in (".py .js .jsx .mjs .cjs .ts .tsx .mts .cts .vue .svelte .c .h .cc .cpp .cxx .hpp "
              ".hh .cs .go .rs .java .kt .kts .rb .php .swift .lua .sql .sh .bash .scala .dart "
              ".ex .exs .hs .ml .jl .zig .nim .f90 .xaml .pl .r").split():
        try:
            if _get_extractor(Path("probe" + e)) is not None:
                ext.add(e)
        except Exception:
            pass
    return ext
_SKIP_NAME = re.compile(r"\.(min|bundle|chunk|vendor)\.(js|mjs|cjs|css)$"
                        # Vite/Rollup/webpack emit a content hash: brainrots-BfRVxtfb.js
                        r"|-[A-Za-z0-9_-]{8,}\.(js|mjs|cjs|css)$", re.I)

def _load_ignore(root):
    pats = []
    try:
        f = root / ".graphifyignore"
        if f.is_file():
            for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip().replace(chr(92), "/").rstrip("/")
                if ln and not ln.startswith("#"):
                    pats.append(ln)
    except OSError:
        pass
    return pats

def _minified(p):
    # minified / generated code has an enormous average line length; sample the head only.
    try:
        with open(p, "rb") as fh:
            chunk = fh.read(262144)
    except OSError:
        return False
    if not chunk:
        return False
    nl = chunk.count(b"\n")
    return nl == 0 or (len(chunk) / (nl + 1)) > 400.0

def _skip(p, root, ignore, gitign=None, kept=()):
    try:
        rel = p.resolve().relative_to(root)   # only judge segments BELOW the workspace root
    except Exception:
        rel = Path(p.name)
    parts = [seg.lower() for seg in rel.parts]
    low = rel.as_posix().lower()
    if _ALWAYS_SKIP.intersection(parts):
        return True
    if _SKIP_TOP and parts and parts[0] in _SKIP_TOP:
        return True                               # the app's own install, named by the parent
    if gitign is None:
        # The name says output. _walk_source has already looked inside and may have judged it
        # source anyway; honour that, or the walk keeps a folder and this throws it straight
        # back out - which is exactly what happened, and left the file unindexed with a
        # coverage record cheerfully reporting the folder as kept.
        # DIRECTORIES ONLY: the last segment is the file itself, and a file named `build.py` is
        # source. Suffix-tolerant, so `dist.old` is judged too.
        if _maybe_built(parts[:-1]) and not _under(low, kept):
            return True
    elif _under(low, gitign):
        return True                           # the project itself calls this output
    if _SKIP_NAME.search(p.name):
        return True
    if ignore:
        rp = rel.as_posix()
        low = "/" + rp.lower() + "/"
        for pat in ignore:
            if fnmatch.fnmatch(rp, pat) or fnmatch.fnmatch(p.name, pat) or ("/" + pat.lower() + "/") in low:
                return True
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if size > 1500000:                        # a >1.5 MB single source file is a built/bundled artifact
        return True
    if size > 8000 and _minified(p):
        return True
    return False

# Every parseable file under root, with excluded folders pruned as we go.
#
# We do this walk ourselves because graphify's own detect() carries a directory blacklist with
# "build" in it. PowderPeaks keeps its whole game in build/js/, so detect() handed back 26 files
# and not one of them was the game. That graph then read as healthy at 117 nodes while it held
# no game code at all. Coverage can only be promised by whoever picks the files.
# Is this directory compiler output, or source that happens to be called "build"?
#
# Only asked when there is no git to ask, and answered on evidence rather than on the name:
# sample the parseable files and look for the marks of generated code — a content hash in the
# file name, a .min.js, an enormous file, lines too long to have been typed. A rollup dist/
# carries those marks. PowderPeaks' build/js does not: it is hand-written, so it reads as source
# and gets indexed. When the evidence is thin the answer is "source", because a missing file is
# silent and a duplicated one is visible — and _rank already sorts a dist/ copy below the real
# one, so the cost of being wrong that way is small.
def _dir_looks_built(d, exts, seen_hashes):
    sample = []
    for dp, dns, fns in os.walk(d):
        dns[:] = [x for x in dns if x.lower() not in _ALWAYS_SKIP and not x.startswith(".")]
        for fn in fns:
            if Path(fn).suffix.lower() in exts:
                sample.append(Path(dp) / fn)
        if len(sample) >= 25:
            break
    sample = sample[:25]
    if not sample:
        return True                       # no code in there — nothing to lose
    # CACHEDIR.TAG is the cross-tool standard for "this directory is generated, do not back it
    # up". cargo writes one. When a build tool has already said so, believe it and stop.
    if (Path(d) / "CACHEDIR.TAG").exists():
        return True
    marks = dupes = 0
    local = set()
    for q in sample:
        try:
            size = q.stat().st_size
            body = q.read_bytes()
        except OSError:
            continue
        if _SKIP_NAME.search(q.name) or size > 1500000 or (size > 8000 and _minified(q)):
            marks += 1
            continue
        h = hashlib.sha1(body).hexdigest()
        if h in seen_hashes:
            # byte-identical to a file we already have. A deploy/ or dist/ that is a COPY of the
            # source adds every symbol a second time, and a query then answers from the copy.
            dupes += 1
        elif h in local:
            dupes += 1                    # the same file several times WITHIN the folder, which
        local.add(h)                      # is what a per-profile build tree looks like
    n = len(sample)
    return marks * 2 >= n or (marks + dupes) * 10 >= n * 7


def _collect(root, start, exts, gitign, excluded, deferred):
    found = []
    for dirpath, dirnames, filenames in os.walk(start):
        keep = []
        for d in dirnames:
            dl = d.lower()
            full = Path(dirpath, d)
            try:
                rl = full.resolve().relative_to(root).as_posix().lower()
            except Exception:
                rl = dl
            if dl in _ALWAYS_SKIP or d.startswith("."):
                excluded.add(rl)
                continue
            if _SKIP_TOP and "/" not in rl and dl in _SKIP_TOP:
                # RECORDED as excluded, not merely skipped. The text scan reads this list back out
                # of coverage.json instead of working it out again, so a tree the build refused to
                # index but the scan still walks would report "the graph missed this file" about
                # 27,000 stdlib symbols — the disagreement that comment on _write_coverage exists
                # to prevent.
                excluded.add(rl)
                continue
            if gitign is None:
                if _maybe_built([dl]) and deferred is not None:
                    deferred.append((rl, full))   # decide later, on evidence
                    continue
            elif _under(rl, gitign):
                excluded.add(rl)
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if Path(fn).suffix.lower() in exts:
                found.append(Path(dirpath) / fn)
    return found


# Two passes, because judging an ambiguous folder needs to know what the project already has.
# Pass one takes the folders nobody argues about. Pass two asks of each "build"-shaped folder:
# does this read as generated, or is it a copy of what pass one already found? PowderPeaks'
# build/js is neither, so it is indexed. Only reached when there is no git to ask.
def _walk_source(root, exts, gitign, excluded, kept_built):
    deferred = [] if gitign is None else None
    found = _collect(root, root, exts, gitign, excluded, deferred)
    if not deferred:
        return found
    seen = set()
    for q in found[:3000]:
        try:
            if q.stat().st_size <= 400000:
                seen.add(hashlib.sha1(q.read_bytes()).hexdigest())
        except OSError:
            pass
    for rl, full in deferred:
        if _dir_looks_built(full, exts, seen):
            excluded.add(rl)
            continue
        kept_built.append(rl)
        found.extend(_collect(root, full, exts, gitign, excluded, None))
    return found


# State what this build saw, measured against what git says the project contains.
#
# A silent gap is the reason this exists. The PowderPeaks graph looked fine at 117 nodes while it
# held none of the game, and no figure anywhere said otherwise — so a wrong answer read exactly
# like a right one. Now every build writes down its own coverage and the map query reports it.
def _write_coverage(root, out, code, dropped, ignore, gitign, src_ext, excluded, kept_built):
    cov = {"built_at": time.time(), "indexed": len(code), "dropped": dropped,
           "git": gitign is not None,
           # The folders this build did NOT look in. The text scan reads this list instead of
           # working it out again, so the scan and the graph cannot end up disagreeing about
           # what the project is — which is exactly how "no file contains that word" got said
           # about a function sitting at build/js/draw.js:446.
           "excluded": sorted(excluded)[:400]}
    if kept_built:
        cov["indexed_despite_name"] = sorted(kept_built)[:20]
    try:
        idx = set()
        for p in code:
            try:
                idx.add(p.resolve().relative_to(root).as_posix().lower())
            except Exception:
                pass
        r = subprocess.run(["git", "-C", str(root), "ls-files"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            want = [f for f in (ln.strip() for ln in r.stdout.splitlines())
                    if f and Path(f).suffix.lower() in src_ext
                    and not _skip(root / f, root, ignore, gitign, kept_built)]
            missing = sorted(f for f in want if f.lower() not in idx)
            cov["tracked_source"] = len(want)
            cov["missing"] = len(missing)
            cov["missing_sample"] = missing[:15]
            cov["percent"] = (round(100.0 * (len(want) - len(missing)) / len(want), 1)
                              if want else 100.0)
    except Exception as e:
        cov["error"] = str(e)[:200]
    try:
        (out / "coverage.json").write_text(json.dumps(cov, indent=1), encoding="utf-8")
    except OSError:
        pass


_SRC_EXT = _parse_exts()
root = Path(sys.argv[1]).resolve()
out = root / "graphify-out"
out.mkdir(parents=True, exist_ok=True)
det = detect(root)
ignore = _load_ignore(root)
gitign = _git_ignored(root)
_excluded = set()
_kept_built = []
code = []
for f in det.get("files", {}).get("code", []):
    p = Path(f)
    code.extend(collect_files(p) if p.is_dir() else [p])
# UNION, never a replacement. detect() still contributes whatever it classifies as code
# (package.json and the like), and the walk adds the folders it wrongly dropped. This can only
# widen coverage, so no project that worked before loses anything.
_seen = set()
_found = []
for p in code + _walk_source(root, _SRC_EXT, gitign, _excluded, _kept_built):
    try:
        k = str(p.resolve()).lower()
    except Exception:
        k = str(p).lower()
    if k not in _seen:
        _seen.add(k)
        _found.append(p)
code = [p for p in _found if not _skip(p, root, ignore, gitign, _kept_built)]
dropped = len(_found) - len(code)
_write_coverage(root, out, code, dropped, ignore, gitign, _SRC_EXT, _excluded, _kept_built)
if not code:
    print("no-code"); raise SystemExit(0)
ast = extract(code, cache_root=root)  # cached: only changed files re-parsed
G = build_from_json({"nodes": ast["nodes"], "edges": ast["edges"], "hyperedges": [],
                     "input_tokens": 0, "output_tokens": 0}, root=str(root), directed=False)
if G.number_of_nodes() == 0:
    print("empty"); raise SystemExit(0)
comm = cluster(G); coh = score_all(G, comm)
gods = god_nodes(G); surp = surprising_connections(G, comm)
labels = {c: "Community " + str(c) for c in comm}
qs = suggest_questions(G, comm, labels)
to_json(G, comm, str(out / "graph.json"), force=True)  # deterministic full rebuild; intentional shrink when we drop vendor/built files (graphify still keeps a dated backup)
rep = generate(G, comm, coh, labels, gods, surp, det, {"input": 0, "output": 0},
               str(root), suggested_questions=qs)
(out / "GRAPH_REPORT.md").write_text(rep, encoding="utf-8")
print("ok", G.number_of_nodes(), "nodes", G.number_of_edges(), "edges", "dropped", dropped, "vendored/built")
"""


# ---------------------------------------------------------------------------
# Querying the graph — the part that was missing
# ---------------------------------------------------------------------------
# The graph is only useful if ASKING it is cheap. graph.json is ~12 MB here, which is ~2.4M tokens:
# reading it costs more than a whole context window, so "read graph.json" was the most expensive
# action available and grep beat it every time. Loading it SERVER-side costs nothing, and the
# answer to a real question ("where is find_entries, what calls it") is a few hundred tokens.
#
# THE NUMBER IN THIS COMMENT WAS WRONG BY 19x, AND THAT IS WHY THE BUG HID. It said ~2.4 MB while
# the file was 45 MB, so a graph that was 70% bundled CPython stdlib looked like the size the design
# had been costed against. It is 12 MB now that the Studio's own `runtime/` and `data/` are excluded
# (see _own_top_dirs and the workspace .graphifyignore), a cold parse costs 0.11 s and ~31 MB of the
# backend. If a change here moves any of those figures by an order of magnitude, that is the signal
# that something is being indexed that should not be — not a detail to round off.
_GRAPH_CACHE: dict[str, Any] = {"key": None, "data": None}


def _graph_path(root: str) -> Path:
    return Path(root) / "graphify-out" / "graph.json"


def _load(root: str) -> Optional[dict]:
    p = _graph_path(root)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime, st.st_size)
    except OSError:
        return None
    if _GRAPH_CACHE["key"] == key:
        return _GRAPH_CACHE["data"]        # type: ignore[return-value]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    nodes = data.get("nodes") or []
    # networkx node-link format calls edges "links" — reading data["edges"] returns nothing and
    # makes a perfectly good graph look empty.
    links = data.get("links") or data.get("edges") or []
    by_id = {str(n.get("id")): n for n in nodes}
    out_e: dict[str, list] = {}
    in_e: dict[str, list] = {}
    for l in links:
        s, t = str(l.get("source")), str(l.get("target"))
        out_e.setdefault(s, []).append(l)
        in_e.setdefault(t, []).append(l)
    # Every name this repo declares, plus every module stem. The text scan uses it to tell a
    # qualified call apart from a coincidence: `graphify_index.refresh(...)` names a module this
    # project owns, `p.mkdir(...)` does not, and only the first refers to our symbol.
    names: set = set()
    for n in nodes:
        lbl = str(n.get("label") or "").strip().rstrip("()").lower()
        if lbl:
            names.add(lbl)
        srcf = str(n.get("source_file") or "")
        if srcf:
            names.add(Path(srcf).stem.lower())
    data = {"nodes": nodes, "links": links, "by_id": by_id, "out": out_e, "in": in_e,
            "built": st.st_mtime, "names": names}
    _GRAPH_CACHE["key"], _GRAPH_CACHE["data"] = key, data
    return data


def _brief(n: dict) -> dict:
    """A node in the fewest tokens that still lets you open the code."""
    return {"name": n.get("label") or n.get("id"),
            "at": f"{n.get('source_file', '')}:{str(n.get('source_location', '')).lstrip('L')}".rstrip(":"),
            "id": n.get("id")}


# A project usually holds an older copy of itself. Ranking by name alone made `buildClouds`
# resolve to rot-haul-LEGACY/src/render/world.ts ahead of the live rot-rush file — so the answer
# pointed confidently at code the user abandoned, which is worse than no answer.
_STALE_PATH = ("legacy", "backup", "archive", "deprecated", "old", "vendor", "third_party",
               "node_modules", "dist", "build", "demo", "sample", "prototype", "graybox",
               "wip", "sandbox", "scratch", "attempt", "unused", "copy", "bak")


def _rank(n: dict, root: str, ql: str) -> tuple:
    """Sort key for a match: exactness first, then the LIVE tree over a parked copy, then the
    file most recently worked on."""
    rel = str(n.get("source_file") or "").replace("\\", "/")
    segs = rel.lower().split("/")
    label = str(n.get("label") or "").lower().rstrip("()")
    score = 0
    if label == ql:
        score += 60
    elif label.startswith(ql):
        score += 25
    for s in segs[:-1]:
        if any(w in s for w in _STALE_PATH):
            score -= 45
        if s.startswith("_"):
            score -= 15
    score -= len(segs)                       # shallower slightly preferred
    try:
        mtime = (Path(root) / rel).stat().st_mtime
    except OSError:
        mtime = 0.0
    return (-score, -mtime, len(str(n.get("label") or "")))


# ---------------------------------------------------------------------------
# The other half of an answer: a text scan, complete where the graph is precise
# ---------------------------------------------------------------------------
# The graph is AST-derived, so an absence in it reads exactly like a real absence — and it is not
# always one. It cannot follow a namespace import, a dispatch table keyed by string, or a
# re-export it failed to resolve, and it collapses parallel call edges. Acting on "nothing else
# uses this" when something does is the one way a cheap lookup makes an edit WORSE than the grep
# it replaced. So a symbol answer carries a plain text scan beside it and says where the two
# disagree. Nobody has to ask for it — that is the point. A separate check-it-for-me command only
# ever helps the agent that remembers to run it.
#
# ripgrep is not an option: it is not on the PATH this backend runs under (shutil.which finds
# nothing) and the Studio installs nothing globally, so the walk is here. The extension filter
# keeps it to one language family, which is what makes it cheap AND quiet — a whole-word match in
# another language is nearly always a different symbol that happens to share a name (`cls` is a
# Python convention and a TSX helper in this very repo). Shout on those and the warning gets
# ignored, which is worse than not having one.
_SCAN_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,63}$")
_SCAN_MAX_FILES = 4000
_SCAN_MAX_BYTES = 1500000
_SCAN_LIST = 8
# THE list — the build script is handed this same pair, so the scan and the graph can never
# disagree about what the project is. It has to match: a file the BUILD skipped is not "missing
# from the graph", and a built bundle holds every symbol name, so reporting one on every query
# would drown the real signal.
#
# Never source, whatever a project says.
_ALWAYS_SKIP = {"node_modules", "bower_components", "jspm_packages", "site-packages",
                "__pycache__", ".git", "graphify-out", "venv", ".venv",
                # somebody else's source. Real code, not ours, and bulky: three.module.js alone
                # is 2263 symbols and would out-vote every file in a small game.
                "vendor", "vendored", "third_party", "third-party", "thirdparty",
                "external", "externals",
                # names a TOOL owns. No human writes their own code into target/ (cargo, maven)
                # or .next/ — and a build tree is full of hand-written-looking files that pass
                # any "does this look generated?" test: cargo's target/ held three copies of
                # aws-lc's openssl headers, and they read as source because they are.
                "target", ".next", ".nuxt", ".svelte-kit", ".output", "coverage",
                "win-unpacked", "_site", "release", "minified"}
# Named like output. Only skipped when git agrees, or when there is no git to ask. "build" is in
# here and PowderPeaks keeps its whole game in build/js — on the name alone the scan answered
# "no file contains that word" about a function sitting at build/js/draw.js:446.
# The only four names a person plausibly gives their OWN source. Everything else that smells
# of output is now simply skipped; these four get looked inside, because PowderPeaks keeps its
# whole game in build/js and calling that "output" on the name cost a day.
_MAYBE_BUILT = {"dist", "build", "out", "deploy"}
_GITIGN: dict = {}          # root -> (expires_at, ignored set | None)
_GITIGN_TTL = 60.0


def _git_ignored(root: str):
    """What this project calls output, straight from git. None when there is no git to ask.

    Cached for a minute: it costs about 45 ms and a query sits in front of a tool call."""
    now = time.time()
    hit = _GITIGN.get(root)
    if hit and hit[0] > now:
        return hit[1]
    val = None
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "--others", "--ignored",
                            "--exclude-standard", "--directory"],
                           capture_output=True, text=True, timeout=20, creationflags=_NF)
        if r.returncode == 0:
            val = {ln.strip().rstrip("/").lower().replace("\\", "/")
                   for ln in r.stdout.splitlines() if ln.strip()}
    except Exception:
        val = None
    _GITIGN[root] = (now + _GITIGN_TTL, val)
    return val
_SCAN_SKIP_NAME = re.compile(r"\.(min|bundle|chunk|vendor)\.(js|mjs|cjs|css)$"
                             r"|-[A-Za-z0-9_-]{8,}\.(js|mjs|cjs|css)$", re.I)
_FAM_GROUPS = (
    {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte"},
    {".py", ".pyi"},
    {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"},
    {".java", ".kt", ".kts"}, {".cs"}, {".go"}, {".rs"}, {".rb"}, {".php"}, {".swift"},
    {".lua"}, {".sh", ".bash"}, {".sql"},
)
_FAMILY = {e: g for g in _FAM_GROUPS for e in g}
_ALL_SOURCE_EXT = set().union(*_FAM_GROUPS)
_CASELESS_PATHS = os.name == "nt"


def _norm(p: Any) -> str:
    v = str(p or "").replace("\\", "/")
    return v.lower() if _CASELESS_PATHS else v


def _family_for(hits: list, ql: str) -> set:
    """The extensions worth scanning: the language the ANSWER is about.

    From the exact matches, not from every hit. The union over all hits made a `.tsx` symbol scan
    Python as well, because some unrelated `.py` node happened to hold the same substring — and
    then reported a wall of Python files as missing from the graph."""
    fam: set = set()
    for n in ([h for h in hits if _is_exact(h, ql)] or hits[:1]):
        e = Path(str(n.get("source_file") or "")).suffix.lower()
        if e:
            fam |= _FAMILY.get(e, {e})
    return fam or _ALL_SOURCE_EXT


# A comment carries the word without referring to the symbol ("so a refresh stays on that tab").
# Blanking comments and docstrings costs one regex per file and removed most of the noise. String
# literals stay: a name in a string is often a real dispatch key the AST pass could not follow.
_PY_LIKE = {".py", ".pyi", ".sh", ".bash"}
_BLANK_PY = re.compile(rb"#[^\n]*"
                       rb"|\x22\x22\x22[\s\S]*?\x22\x22\x22"
                       rb"|\x27\x27\x27[\s\S]*?\x27\x27\x27")
# The lookbehind keeps a URL and a doubled path separator out of it: a real // comment
# follows whitespace or punctuation, never a word character or another slash.
_BLANK_C = re.compile(rb"(?<![:/\w])//[^\n]*|/\*[\s\S]*?\*/")
# A qualifier that always names the enclosing object, so it can never be a coincidence.
_SELF_QUAL = {"self", "cls", "this"}


def _blank(m) -> bytes:
    """Same length, same line breaks, no words — so offsets and line structure survive."""
    return re.sub(rb"[^\n]", b" ", m.group(0))


def _strip_comments(blob: bytes, ext: str) -> bytes:
    try:
        return (_BLANK_PY if ext in _PY_LIKE else _BLANK_C).sub(_blank, blob)
    except Exception:
        return blob


def _is_reference(blob: bytes, at: int, names: set) -> bool:
    """Does this occurrence use OUR symbol, or somebody else's with the same name?

    A bare `refresh(` counts. A qualified `x.refresh(` counts only when `x` is a name this repo
    declares. That single rule removes the largest class of false alarm there is: `p.mkdir()`,
    `fp.stat()`, `shutil.copy()` — standard-library methods that share a name with something
    declared here, and so matched in every file that ever touched a path."""
    if blob[at - 1:at] == b"/" and at > 0:
        return False                                 # "/api/health" — a path segment in a string
    j = at - 1
    while j >= 0 and blob[j:j + 1] in b" \t":
        j -= 1
    if j < 0 or blob[j:j + 1] != b".":
        return True                                  # not qualified — take it
    k = j - 1
    while k >= 0 and blob[k:k + 1] in b" \t\r\n":
        k -= 1
    end = k + 1
    while k >= 0 and (blob[k:k + 1].isalnum() or blob[k:k + 1] == b"_"):
        k -= 1
    qual = blob[k + 1:end].decode("utf-8", "ignore").lower()
    return bool(qual) and (qual in _SELF_QUAL or qual in names)


def _excluded_dirs(root: str):
    """The folders the last BUILD decided not to look in, straight from its own record.

    The scan follows the build rather than work it out again. Two copies of that decision is how
    they come to disagree, and a disagreement here is not cosmetic: the scan is the half that is
    allowed to say a symbol does not exist. None when no build has run."""
    try:
        d = json.loads((Path(root) / "graphify-out" / "coverage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ex = d.get("excluded")
    return set(ex) if isinstance(ex, list) else None


def _keep_dir(dirpath: str, d: str, rootp: Path, excluded) -> bool:
    """Is this directory part of the project, or is it output?"""
    dl = d.lower()
    if d.startswith(".") or dl in _ALWAYS_SKIP:
        return False
    if excluded is None:
        return dl not in _MAYBE_BUILT         # nothing to go on — the name is all there is
    try:
        rl = (Path(dirpath, d).resolve().relative_to(rootp)).as_posix().lower()
    except Exception:
        return dl not in _MAYBE_BUILT
    return not any(rl == g or rl.startswith(g + "/") for g in excluded)


def _scan_files(root: str, sym: str, exts: set, names: set) -> tuple:
    """(repo-relative files that REFER to ``sym`` in code, hit-the-cap flag)."""
    try:
        rootp = Path(root)
        # the build's own record first; git only when this project has never been built
        excluded = _excluded_dirs(root)
        if excluded is None:
            g = _git_ignored(root)
            excluded = g if g is not None else None
        pat = re.compile(rb"\b" + re.escape(sym.encode("utf-8")) + rb"\b")
        hits: list[str] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(rootp):
            dirnames[:] = [d for d in dirnames if _keep_dir(dirpath, d, rootp, excluded)]
            for fn in filenames:
                if Path(fn).suffix.lower() not in exts or _SCAN_SKIP_NAME.search(fn):
                    continue
                if scanned >= _SCAN_MAX_FILES:
                    return hits, True
                fp = Path(dirpath) / fn
                try:
                    if fp.stat().st_size > _SCAN_MAX_BYTES:
                        continue
                    blob = fp.read_bytes()
                except OSError:
                    continue
                scanned += 1
                blob = _strip_comments(blob, Path(fn).suffix.lower())
                if any(_is_reference(blob, m.start(), names) for m in pat.finditer(blob)):
                    hits.append(str(fp.relative_to(rootp)).replace("\\", "/"))
        return hits, False
    except Exception:
        return None, False


def _referencing_files(hits: list, by_id: dict, in_e: dict) -> set:
    """Every file the GRAPH ties to any of the matched symbols.

    From the FULL in-edge list, not from the ``used_by`` in the reply: that one is cut to
    ``limit``, and comparing a scan against a truncated list would report every caller past the
    cut as a miss. Every hit counts, not only the exact ones — a file that calls
    ``_refresh_alive`` contains the text "refresh", and blaming the graph for that is noise."""
    files = {_norm(n.get("source_file")) for n in hits}
    for n in hits:
        for l in in_e.get(str(n.get("id")), []):
            src = by_id.get(str(l.get("source")))
            if src:
                files.add(_norm(src.get("source_file")))
    files.discard("")
    return files


def _scan_report(root: str, q: str, ql: str, hits: list, by_id: dict, in_e: dict,
                 names: set) -> Any:
    """``"agrees"``, or the files the scan found and the graph did not.

    Never silent, even when it agrees: an absent field could not be told apart from a scan that
    never ran, and a safety check you cannot tell has run is not one."""
    if not _SCAN_SYMBOL.match(q):
        return "not run: only a whole symbol is comparable"
    found, capped = _scan_files(root, q, _family_for(hits, ql), names)
    if found is None:
        return "unavailable"
    known = _referencing_files(hits, by_id, in_e)
    extra = sorted(f for f in found if _norm(f) not in known)
    if not extra:
        return "agrees (partial: hit the file cap)" if capped else "agrees"
    rep: dict = {"count": len(extra), "files_not_in_graph": extra[:_SCAN_LIST],
                 "note": "a whole-word text scan matched these files too. The graph holds declared "
                         "symbols, so a match here can be a comment, a string or a different "
                         "symbol of the same name — but the graph can also miss a real reference "
                         "it could not resolve. Read them before you edit call sites."}
    if len(extra) > _SCAN_LIST:
        rep["shown"] = _SCAN_LIST
    if capped:
        rep["incomplete"] = "the scan hit its file cap; there may be more"
    return rep


def _scan_only(root: str, q: str, names: set) -> Any:
    """For a symbol the graph never heard of: where the text appears anyway.

    Turns a dead end into an answer. "Not in the graph" was true and useless — the name is often
    right there, in a file the AST pass could not resolve or in a language it skipped."""
    if not _SCAN_SYMBOL.match(q):
        return None
    found, capped = _scan_files(root, q, _ALL_SOURCE_EXT, names)
    if found is None:
        return "unavailable"
    if not found:
        # Say what was searched. The bare version claimed "no file contains that word" about a
        # function at build/js/draw.js:446, because build/ was skipped on its name. A negative
        # is only worth stating together with its boundary.
        return ("no source file contains that word either (folders the project's .gitignore "
                "excludes were not searched)")
    rep: dict = {"count": len(found), "text_matches": sorted(found)[:_SCAN_LIST],
                 "note": "not a declared symbol in the graph, but the text appears here"}
    if capped:
        rep["incomplete"] = "the scan hit its file cap; there may be more"
    return rep


def _coverage(root: str) -> Optional[dict]:
    """What the last build indexed, against what git says the project holds.

    Written by every build. Reported here because a gap that nobody can see is a gap nobody
    fixes: the PowderPeaks graph sat at 117 nodes with none of the game in it, and read as
    perfectly healthy, because no figure anywhere disagreed."""
    try:
        d = json.loads((Path(root) / "graphify-out" / "coverage.json")
                       .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = {k: d[k] for k in ("percent", "indexed", "tracked_source", "missing") if k in d}
    if d.get("missing"):
        out["missing_sample"] = d.get("missing_sample") or []
        out["note"] = ("git tracks source files this graph does not hold — grep those, and tell "
                       "the user the graph is incomplete for this project")
    if not d.get("git"):
        kept = d.get("indexed_despite_name") or []
        out["note"] = ("no git in this project, so folders named build/dist/out were judged on "
                       "their contents instead" + (" — %s read as source and were indexed"
                                                   % ", ".join(kept[:3]) if kept else ""))
    return out or None


def _is_exact(n: dict, ql: str) -> bool:
    """The same name, not merely one containing it. graphify labels a callable ``name()``."""
    return str(n.get("label") or "").strip().lower().rstrip("()") == ql


def _staleness(root: str, built: float) -> Optional[dict]:
    """Present only when source files changed after the graph was built.

    Aimed at the caller lists on purpose. A stale LOCATION costs one wasted Read — the agent opens
    the file and sees the truth. A stale CALLER LIST is different: the agent edits the three call
    sites it was handed, misses the fourth another agent added a minute ago, and that one does not
    correct itself."""
    st = _st_read(root)
    changed = float(st.get("changed") or 0.0)
    # covers = when the last successful build STARTED, which is the conservative end. Falls back
    # to the graph file's own mtime, so a graph built before this process started still dates its
    # answers instead of silently claiming to be current.
    covers = float(st.get("covers") or 0.0) or float(built or 0.0)
    if not changed or not covers or changed <= covers:
        return None
    files = sorted(st.get("changed_files") or ())
    out: dict = {"seconds_behind": max(1, int(time.time() - covers)),
                 "note": "source files changed after this graph was built, so 'uses' and "
                         "'used_by' can be incomplete — grep before you edit call sites"}
    if files:
        out["changed_since"] = files[:_SCAN_LIST]
        if len(files) > _SCAN_LIST:
            out["more"] = len(files) - _SCAN_LIST
    if st.get("running") or st.get("pending"):
        out["rebuilding"] = True
    return out


def query(root: str, q: str = "", to: str = "", limit: int = 12, path: str = "",
          scan: bool = True) -> dict:
    """Answer one question about the code graph, small enough to paste into a prompt.

    ``q`` alone      → where that symbol is, what it uses, what uses it.
    ``q`` + ``to``   → a shortest path between two symbols (how A reaches B).
    ``path``         → restrict to a subtree ("rot-rush/src"), for a repo holding several apps.
    no ``q``         → a map of the codebase: size plus the most connected nodes.
    ``scan=False``   → skip the text cross-check. Only the Grep hook passes this: the grep it is
                       sitting in front of IS the scan, and running it twice helps nobody.
    """
    g = _load(root)
    if g is None:
        # Self-healing: asking a project that has never been indexed STARTS the build instead of
        # just refusing. "Graphify should always work in every project" only holds if the first
        # question triggers it — a workspace the user never opened in the Studio would otherwise
        # answer "no graph" forever. Fire-and-forget; it is debounced and rate-limited.
        started = False
        try:
            from .config import settings
            if settings.get("cc_graphify", True) and Path(root).is_dir():
                started = bool(refresh(root))
        except Exception:
            pass
        why = absurd_root(root)
        if why:
            return {"ok": False, "building": False,
                    "error": f"not indexing {why}. Point the Studio at a project folder instead."}
        return {"ok": False, "building": started,
                "error": ("no graph for this project yet — a build has just been started, try again "
                          "in a minute; grep in the meantime" if started else
                          "no graph yet for this project")}
    nodes, by_id, out_e, in_e = g["nodes"], g["by_id"], g["out"], g["in"]
    if not q:
        deg = sorted(nodes, key=lambda n: len(out_e.get(str(n.get("id")), []))
                     + len(in_e.get(str(n.get("id")), [])), reverse=True)
        files: dict[str, int] = {}
        for n in nodes:
            f = str(n.get("source_file") or "")
            if f:
                files[f] = files.get(f, 0) + 1
        cov = _coverage(root)
        return {"ok": True, "nodes": len(nodes), "links": len(g["links"]),
                "files": len(files),
                **({"coverage": cov} if cov else {}),
                "biggest_files": [{"file": f, "symbols": c} for f, c in
                                  sorted(files.items(), key=lambda kv: -kv[1])[:10]],
                "most_connected": [_brief(n) for n in deg[:12]]}

    ql = q.lower().strip()
    hits = [n for n in nodes
            if ql in str(n.get("label", "")).lower() or ql in str(n.get("id", "")).lower()]
    if not hits:                                   # fall back to file-path matching
        hits = [n for n in nodes if ql in str(n.get("source_file", "")).lower()][:limit]
    if path:
        pl = path.replace("\\", "/").lower()
        hits = [n for n in hits if pl in str(n.get("source_file", "")).replace("\\", "/").lower()]
    if not hits:
        res: dict = {"ok": True, "q": q, "matches": [],
                     # the boundary is worth stating: it is AST-derived, so it holds declarations,
                     # not locals inside a function, not CSS classes, not dynamic dispatch
                     "hint": "not in the graph — it indexes declared symbols (functions, classes, "
                             "methods, exports), NOT local variables inside a function, CSS "
                             "classes or strings. Grep for those."}
        if scan:
            sc = _scan_only(root, q, g.get("names") or set())
            if sc is not None:
                res["scan"] = sc
        return res
    hits.sort(key=lambda n: _rank(n, root, ql))

    if to:
        tl = to.lower().strip()
        goals = {str(n.get("id")) for n in nodes
                 if tl in str(n.get("label", "")).lower() or tl in str(n.get("id", "")).lower()}
        if not goals:
            return {"ok": True, "q": q, "to": to, "path": [], "hint": f"no node matching {to!r}"}
        from collections import deque
        start = str(hits[0].get("id"))
        prev: dict[str, Optional[str]] = {start: None}
        dq = deque([start])
        found = None
        while dq:
            cur = dq.popleft()
            if cur in goals:
                found = cur
                break
            for l in out_e.get(cur, []) + in_e.get(cur, []):
                for nxt in (str(l.get("target")), str(l.get("source"))):
                    if nxt not in prev:
                        prev[nxt] = cur
                        dq.append(nxt)
        if not found:
            return {"ok": True, "q": q, "to": to, "path": [], "hint": "no connection found"}
        chain, cur = [], found
        while cur is not None:
            chain.append(cur)
            cur = prev[cur]
        chain.reverse()
        return {"ok": True, "q": q, "to": to,
                "path": [_brief(by_id[c]) for c in chain if c in by_id]}

    top_node = hits[0]
    top = str(top_node.get("id"))
    out_all, in_all = out_e.get(top, []), in_e.get(top, [])
    uses = [by_id[str(l.get("target"))] for l in out_all[:limit] if str(l.get("target")) in by_id]
    used_by = [by_id[str(l.get("source"))] for l in in_all[:limit] if str(l.get("source")) in by_id]
    same_file = [n for n in nodes
                 if n.get("source_file") == top_node.get("source_file")
                 and n is not top_node][:limit]
    out: dict = {"ok": True, "q": q,
                 # WHICH declaration the two lists below describe. Without this the answer was
                 # silently partial: `refresh` matches 7 nodes in this repo, `used_by` covered
                 # exactly one of them, and nothing in the reply said so.
                 "for": _brief(top_node),
                 "matches": [_brief(n) for n in hits[:limit]],
                 "uses": [_brief(n) for n in uses],
                 "used_by": [_brief(n) for n in used_by],
                 "same_file": [_brief(n) for n in same_file]}
    # The same silence one layer down: a caller list cut at `limit` read exactly like a whole one.
    if len(in_all) > limit:
        out["used_by_total"] = len(in_all)
    if len(out_all) > limit:
        out["uses_total"] = len(out_all)
    twins = [n for n in hits[1:] if _is_exact(n, ql)]
    if twins:
        out["also_declared"] = [_brief(n) for n in twins[:6]]
        out["note"] = (f"{len(twins) + 1} declarations carry the name {q!r}; 'uses' and 'used_by' "
                       f"describe the one under 'for'. Add path=<subdir> to ask about another.")
    stale = _staleness(root, float(g.get("built") or 0.0))
    if stale:
        out["stale"] = stale
    if scan:
        out["scan"] = _scan_report(root, q, ql, hits, by_id, in_e, g.get("names") or set())
    return out


def _pipx_venvs_dir() -> Optional[Path]:
    """Ask pipx where it keeps its venvs on THIS machine — the location varies by OS / pipx
    version / install method, so a hardcoded path breaks on other PCs."""
    nf = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    for cmd in (["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"],
                ["python", "-m", "pipx", "environment", "--value", "PIPX_LOCAL_VENVS"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, creationflags=nf)
            p = (r.stdout or "").strip()
            if p and Path(p).exists():
                return Path(p)
        except Exception:
            pass
    return None


def _graphify_python() -> Optional[str]:
    """Locate an interpreter that can ``import graphify`` (the pipx venv). Cached."""
    global _GPY
    if _GPY and Path(_GPY).exists():
        return _GPY
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", "") or (home / "AppData" / "Local"))
    cands: list[Path] = [
        _VENV_DIR / "Scripts" / "python.exe", _VENV_DIR / "bin" / "python",  # Studio-owned venv first
    ]
    vd = _pipx_venvs_dir()                       # dynamic: wherever pipx actually put it here
    if vd:
        cands += [vd / "graphifyy" / "Scripts" / "python.exe", vd / "graphifyy" / "bin" / "python"]
    cands += [
        home / "pipx" / "venvs" / "graphifyy" / "Scripts" / "python.exe",
        home / ".local" / "pipx" / "venvs" / "graphifyy" / "Scripts" / "python.exe",
        local / "pipx" / "pipx" / "venvs" / "graphifyy" / "Scripts" / "python.exe",
        local / "pipx" / "venvs" / "graphifyy" / "Scripts" / "python.exe",
        home / "pipx" / "venvs" / "graphifyy" / "bin" / "python",
        home / ".local" / "pipx" / "venvs" / "graphifyy" / "bin" / "python",
    ]
    studio_own = {_VENV_DIR / "Scripts" / "python.exe", _VENV_DIR / "bin" / "python"}
    for c in cands:
        try:
            # THE STUDIO'S OWN VENV COUNTS ONLY WITH graphify IN IT. The folder exists the moment
            # `python -m venv` runs; an install that stopped after that (no internet, a restart)
            # used to read as installed for good, and every build then failed in silence.
            if c in studio_own and not venv_has(_VENV_DIR, "graphify"):
                continue
            if c.exists():
                _GPY = str(c)
                return _GPY
        except OSError:
            pass
    # last resort: a `graphify` shim on PATH → walk to its pipx venv's python
    g = shutil.which("graphify")
    if g:
        gp = Path(g).resolve()
        for venv_py in (gp.parent / ("python.exe" if os.name == "nt" else "python"),
                        gp.parent.parent / "venvs" / "graphifyy" / "Scripts" / "python.exe",
                        gp.parent.parent / "venvs" / "graphifyy" / "bin" / "python"):
            if venv_py.exists():
                _GPY = str(venv_py)
                return _GPY
    return None


def is_available() -> bool:
    return _graphify_python() is not None


def install_status() -> dict:
    """For the UI / Graphify toggle: is graphify usable, and is a background install running / failed."""
    return {
        "available": is_available(),
        "installing": bool(_INSTALL["installing"]),
        "error": _INSTALL["error"],
        "venv": str(_VENV_DIR),
    }


def graphify_exe() -> Optional[str]:
    """The `graphify` command that belongs to the interpreter the Studio uses."""
    py = _graphify_python()
    if py:
        for name in ("graphify.exe", "graphify"):
            c = Path(py).parent / name
            if c.exists():
                return str(c)
    return shutil.which("graphify")


def skill_installed() -> bool:
    return (claude_home() / "skills" / "graphify" / "SKILL.md").is_file()


def ensure_skill() -> bool:
    """Write the /graphify Claude skill when this PC does not have it yet.

    It used to be written only at the end of the Studio's own install - so a PC where graphify
    arrived another way (pipx, a copy on PATH) had the package and never the skill, and /graphify
    in Claude Code did nothing. Found on a fresh profile: graphify answered, the skill was absent."""
    if skill_installed():
        return True
    exe = graphify_exe()
    if not exe:
        return False
    try:
        subprocess.run([exe, "install", "--platform", "claude"], timeout=120,
                       creationflags=_NF, capture_output=True)
    except Exception:
        return False
    return skill_installed()


def ensure_installed(block: bool = False, timeout: float = 900.0) -> bool:
    """Make graphifyy importable by a Studio-controlled interpreter. If it isn't, install it into a
    dedicated venv under the data dir — no pipx, no PATH — so the toggle works on a fresh PC where
    setup couldn't install it. Idempotent; runs in the background by default."""
    if is_available():
        _INSTALL["done"] = True
        if not skill_installed():
            if block:
                ensure_skill()
            else:
                threading.Thread(target=ensure_skill, name="graphify-skill", daemon=True).start()
        return True
    start_new = False
    with _LOCK:
        if not _INSTALL["installing"]:
            _INSTALL.update(installing=True, error="", started=time.time())
            start_new = True
    if start_new:
        t = threading.Thread(target=_do_install, daemon=True)
        t.start()
        if block:
            t.join(timeout=timeout)
    elif block:
        end = time.time() + timeout
        while _INSTALL["installing"] and time.time() < end:
            time.sleep(1.0)
    return is_available()


def _do_install() -> None:
    """Create data/tools/graphify-venv and pip-install graphifyy into it (prefer wheels — the target
    may have no C/C++ compiler). Then wire the /graphify Claude skill from that venv's shim. Any
    failure is recorded in _INSTALL['error'] so the UI can show it instead of silently doing nothing."""
    global _GPY
    try:
        py = _VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not py.exists():
            _VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)],
                           timeout=180, creationflags=_NF, capture_output=True)
        if not py.exists():
            raise RuntimeError("could not create the graphify venv")
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
                       timeout=180, creationflags=_NF, capture_output=True)
        # The bundled wheels first (an installer .exe ships them), then PyPI.
        for flags in pip_attempts():
            r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", *flags, "graphifyy"],
                               timeout=900, creationflags=_NF, capture_output=True, text=True)
            if r.returncode == 0:
                break
        chk = subprocess.run([str(py), "-c", "import graphify"], timeout=60,
                             creationflags=_NF, capture_output=True, text=True)
        if chk.returncode != 0:
            raise RuntimeError((r.stderr or chk.stderr or "").strip()[:400] or "graphify install failed")
        _GPY = str(py)
        shim = _VENV_DIR / ("Scripts/graphify.exe" if os.name == "nt" else "bin/graphify")
        if shim.exists():   # best-effort: wire the /graphify skill; never blocks success
            try:
                subprocess.run([str(shim), "install", "--platform", "claude"],
                               timeout=120, creationflags=_NF, capture_output=True)
            except Exception:
                pass
        _INSTALL.update(done=True, error="")
    except Exception as e:
        _INSTALL["error"] = str(e)[:400] or "graphify install failed"
    finally:
        _INSTALL["installing"] = False


def _own_top_dirs(ws: str) -> list:
    """Top-level folders under ``ws`` that are the Studio's OWN, not the project's.

    The build cannot work this out for itself: it is handed a workspace path and a name list, and
    `runtime` is as plausible a name for a game's own source as it is for our bundled interpreter.
    The parent knows, because it has ROOT_DIR and DATA_DIR, so it says so — and only when the folder
    really sits directly under this workspace, so a graph of somebody else's project is untouched.
    """
    from .config import DATA_DIR, ROOT_DIR
    out = set()
    try:
        root = Path(ws).resolve()
    except OSError:
        return []
    for d in (ROOT_DIR / "runtime", DATA_DIR):
        try:
            rel = Path(d).resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if len(rel.parts) == 1:            # directly under the root, so it is a top-level tree
            out.add(rel.parts[0])
    return sorted(out)


def build_now(ws: str) -> tuple:
    """Build ``ws``'s graph to completion, here and now. Returns ``(ok, tail of output)``.

    `_run` wraps this for the background path. It is separate so a build can also be run
    deliberately — a one-off rebuild after changing the rules above, which is how a fix takes
    effect without restarting the server that still holds the old script in memory.
    """
    py = _graphify_python()
    if not py:
        return False, "graphify is not installed"
    cfg = json.dumps({"always": sorted(_ALWAYS_SKIP), "maybe": sorted(_MAYBE_BUILT),
                      "skip_top": _own_top_dirs(ws)})
    r = subprocess.run([py, "-c", _BUILD_SRC, ws, cfg], capture_output=True,
                       timeout=300, creationflags=_NF)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    lines = [ln for ln in (out + "\n" + err).splitlines() if ln.strip()]
    return r.returncode == 0, " | ".join(lines[-2:])


def _run(ws: str) -> None:
    ok = False
    started = time.time()
    covered: set = set()
    try:
        time.sleep(_DEBOUNCE)  # coalesce a burst (e.g. Claude just edited several files)
        started = time.time()
        with _LOCK:
            # What this build is about to read. Anything that changes DURING the build stays in
            # changed_files and keeps reporting stale, which is the safe direction to be wrong in.
            covered = set(_st(ws)["changed_files"])
        ok, _tail = build_now(ws)
    except Exception:
        pass
    finally:
        with _LOCK:
            st = _st(ws)
            st["running"] = False
            st["last"] = time.time()
            if ok:
                # Only a build that FINISHED clears the backlog. Clearing it after a failed build
                # would leave the graph old and the answers claiming to be current.
                st["changed_files"] -= covered
                st["covers"] = started


def absurd_root(ws: str) -> str:
    """Why this path must never be indexed, or "".

    This exists because the hook now builds a graph for any project it is asked about. That is
    what makes a never-opened project work — and it also means one careless registered folder
    could start a walk over everything the user owns. The home folder is registered as a
    workspace on this machine, so the case is not hypothetical."""
    try:
        p = Path(ws).resolve()
    except OSError:
        return ""
    if p.parent == p:
        return "a drive root"
    try:
        if p == Path.home().resolve():
            return "your home folder — every project inside it would be indexed as one"
    except OSError:
        pass
    return ""


def refresh(workspace_path: str) -> bool:
    """Fire-and-forget: (re)build the workspace's code graph in the background.

    Returns True if a build was scheduled. No-op (returns False) if graphify
    isn't installed, a build for this workspace is already running, or one ran
    within the last ``_MIN_INTERVAL`` seconds.
    """
    if not workspace_path:
        return False
    try:
        ws = _ws_key(workspace_path)
    except Exception:
        return False
    if not Path(ws).is_dir():
        return False
    if absurd_root(ws):
        return False
    if not is_available():
        ensure_installed()   # fresh PC / never-installed: kick off the self-contained install now;
        return False         # this build is skipped — the next refresh after install completes builds
    now = time.time()
    with _LOCK:
        st = _st(ws)
        if st["running"] or (now - st["last"]) < _MIN_INTERVAL:
            # The rate limit used to DROP the request, so the last change in a burst never got
            # built: deleting a file right after creating one left the deleted symbol in the graph
            # until something unrelated triggered a rebuild. A graph that confidently points at a
            # file that no longer exists is worse than a slightly late one, so the request is
            # remembered and run once the interval passes. One follow-up, not a queue.
            if not st.get("pending"):
                st["pending"] = True
                delay = max(0.5, _MIN_INTERVAL - (now - st["last"]) + 0.5)
                threading.Timer(delay, _run_pending, args=(ws,)).start()
            return False
        st["running"] = True
        st["pending"] = False
    threading.Thread(target=_run, args=(ws,), daemon=True).start()
    return True


def _run_pending(ws: str) -> None:
    """Run the build that the rate limit deferred, if nothing else has run it meanwhile."""
    with _LOCK:
        st = _st(ws)
        st["pending"] = False
        if st["running"]:
            return
        st["running"] = True
    threading.Thread(target=_run, args=(ws,), daemon=True).start()

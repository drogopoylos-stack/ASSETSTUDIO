"""Which line of code made this object.

`find`, the scene API and the Edit tab hand an agent a NAME - `pillar-2`, `label.003`,
`JobJobJobSahur` - and the next thing it needs is the line that gave the object that name,
because that is where a change has to go. Grep for the name is what an agent does today, and it
fails on the commonest case of all: nothing in the proof game says `pillar-2`. The game wrote
`p.name = 'pillar-' + (i + 1)`, and the group itself was made by `buildPillar`, whose own name
for it is `'pillar'`. So a hint is searched for in the order a person would look:

    1. the name exactly, as a string literal           'player'
    2. the name without its trailing index             'pillar-'   `pillar-${i}`   'label'
       and without the separator that led to it        'pillar'
    3. the name as an identifier: a declaration of it, a builder whose name contains it
       (`buildPillar`), then any use of it              const player = ...

Each hint is `"<relative path>:<line>: <trimmed source line>"`, at most 160 characters, and the
search is bounded the way the asset index is: the same folders skipped, files over 800 KB left
out, 2,000 files at most. The text is kept between calls and re-read only when a file's mtime or
size changes.

HOW IT STAYS FAST. The first version ran one regex per tier over every file, and a warm call on
the brainrot workspace (128 files, 4.6 MB) took 205 ms for five names - nearly the whole budget,
none of it the walk. Now the files are held as ONE string and each tier is a plain `str.find`
for the text it must contain; every hit is then judged by the few characters around it. A scan
of 4.6 MB is a couple of milliseconds in C, and Python only ever looks at the hits.
"""
from __future__ import annotations

import os
import re
import threading
import time
from bisect import bisect_right
from collections import deque
from pathlib import Path

try:
    from .assets_index import _skip_dir as _index_skip
except Exception:                                    # pragma: no cover - defensive
    _index_skip = None

CODE_EXT = (".js", ".mjs", ".ts", ".tsx", ".jsx")
MAX_FILE_BYTES = 800_000
MAX_FILES = 2000
MAX_DEPTH = 10
HINT_CHARS = 160
# Candidates kept per tier before ranking. A name like `player` sits on hundreds of lines of a real
# game; ranking all of them to hand back three is work nobody asked for.
_TIER_CAP = 60
_MAX_NAMES = 400
# What the asset index skips, and then these whatever that list says: they are never the game's
# own source, and `.studio` is where the Studio keeps its own files inside a game.
_EXTRA_SKIP = {"node_modules", "dist", "build", "deploy", ".studio"}
# A burst of calls - one per row of a scene listing - walks the tree once. Longer than this and an
# agent that has just edited a file could be handed the line numbers from before the edit.
_WALK_TTL = 1.0
_KEEP_PROJECTS = 8
# Between two files in the corpus: a newline so no line spans two files, and a NUL so no literal
# (which never contains one) can match across the join.
_SEP = "\n\x00\n"
_NL = re.compile("\n")
_QUOTES = "'\"`"
_IDCH = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


class _File:
    __slots__ = ("rel", "mtime", "size", "text", "rank", "_nl")

    def __init__(self, rel: str, mtime: int, size: int, text: str):
        self.rel = rel
        self.mtime = mtime
        self.size = size
        self.text = text
        self._nl = None
        # Tests and fixtures name things too, but the game's own file is the one to edit.
        self.rank = 1 if re.search(r"(^|/)(__tests__|tests?|spec|fixtures?|mocks?)/|\.(test|spec)\.",
                                   rel.lower()) else 0

    def line_at(self, i: int) -> tuple[int, str, int]:
        """(1-based line number, the line, the column of `i` in it)."""
        if self._nl is None:
            self._nl = [m.start() for m in _NL.finditer(self.text)]
        n = bisect_right(self._nl, i - 1)
        start = self._nl[n - 1] + 1 if n else 0
        end = self._nl[n] if n < len(self._nl) else len(self.text)
        return n + 1, self.text[start:end], i - start


_lock = threading.Lock()
_index: dict[str, dict] = {}


def _skip(name: str) -> bool:
    low = name.lower()
    if low in _EXTRA_SKIP:
        return True
    if _index_skip is not None:
        try:
            return bool(_index_skip(name))
        except Exception:
            return False
    return low.startswith(".")


def _key(project: str) -> str:
    try:
        return str(Path(project).resolve()).lower()
    except OSError:
        return str(project).lower()


def _walk(root: Path) -> tuple[list[tuple[str, str, int, int]], bool]:
    """(absolute path, relative path, mtime_ns, size) for each code file, shallowest first.

    Breadth first, so when the 2,000-file ceiling is reached it is the deepest files that are
    dropped and not the game's own `src/`. `DirEntry.stat()` on Windows comes from the directory
    listing itself, so the walk costs one call per folder rather than one per file.
    """
    out: list[tuple[str, str, int, int]] = []
    base = str(root)
    todo: deque = deque([(base, 0)])
    while todo:
        folder, depth = todo.popleft()
        try:
            with os.scandir(folder) as it:
                entries = sorted(it, key=lambda d: d.name.lower())
        except OSError:
            continue
        for ent in entries:
            name = ent.name
            try:
                if ent.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and not _skip(name):
                        todo.append((ent.path, depth + 1))
                    continue
            except OSError:
                continue
            low = name.lower()
            if not low.endswith(CODE_EXT) or low.endswith(".d.ts") or ".min." in low:
                continue
            try:
                st = ent.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            rel = os.path.relpath(ent.path, base).replace("\\", "/")
            out.append((ent.path, rel, int(st.st_mtime_ns), int(st.st_size)))
            if len(out) >= MAX_FILES:
                return out, True
    return out, False


def _read(path: str):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _files(project: str) -> dict:
    """The project's code as one searchable string, kept, and re-read per file when it changes."""
    root = Path(project)
    k = _key(project)
    now = time.monotonic()
    with _lock:
        idx = _index.get(k)
        if idx is not None:
            idx["used"] = now
            if now - idx["walked"] < _WALK_TTL:
                return idx
    if not root.is_dir():
        return {"files": [], "starts": [], "corpus": "", "low": "", "truncated": False,
                "walked": now, "used": now, "reread": 0}
    listing, truncated = _walk(root)
    old = {f.rel: f for f in (idx or {}).get("files") or []}
    files: list[_File] = []
    reread = 0
    for path, rel, mtime, size in listing:
        f = old.get(rel)
        if f is None or f.mtime != mtime or f.size != size:
            text = _read(path)
            if text is None:
                continue
            f = _File(rel, mtime, size, text)
            reread += 1
        files.append(f)
    same = idx is not None and not reread and [f.rel for f in files] == [f.rel for f in idx["files"]]
    if same:
        corpus, low, starts = idx["corpus"], idx["low"], idx["starts"]
    else:
        corpus = _SEP.join(f.text for f in files)
        starts, off = [], 0
        for f in files:
            starts.append(off)
            off += len(f.text) + len(_SEP)
        low = corpus.lower()
        # `lower()` can change a string's LENGTH (one capital becomes two letters), and every
        # position found in the lowercase copy is used on the original. If it moved, the
        # case-blind half of the search is simply switched off rather than allowed to lie.
        if len(low) != len(corpus):
            low = ""
    new = {"files": files, "starts": starts, "corpus": corpus, "low": low, "truncated": truncated,
           "walked": time.monotonic(), "used": now, "reread": reread}
    with _lock:
        _index[k] = new
        if len(_index) > _KEEP_PROJECTS:
            order = sorted(_index, key=lambda x: _index[x].get("used", 0))
            for stale in order[:len(_index) - _KEEP_PROJECTS]:
                if stale != k:
                    _index.pop(stale, None)
    return new


def invalidate(project: str = "") -> None:
    """Forget what was read, for one project or for all of them."""
    with _lock:
        if project:
            _index.pop(_key(project), None)
        else:
            _index.clear()


def stats(project: str) -> dict:
    """How much of the project the hints can see."""
    idx = _files(project)
    fl = idx.get("files") or []
    return {"files": len(fl), "bytes": sum(f.size for f in fl), "truncated": bool(idx.get("truncated")),
            "reread": int(idx.get("reread") or 0)}


# ---------------------------------------------------------------------------
# What to look for
# ---------------------------------------------------------------------------
# A piece of a merged mesh (`base-props~t40-52`, see pieces.ts) is named after the mesh it came from.
_PIECE = re.compile(r"~t\d+-\d+$")
# `label.003`: stableKeys' suffix for a repeated name, and Blender's. The dot belongs to the suffix.
_DUP = re.compile(r"^(.+?)\.\d{3,}$")
_PAREN = re.compile(r"^(.+?)\s*\(\d+\)$")
# `pillar-2`, `crystal_03`, `Box12`: the digits go, the separator stays - the code that numbered
# them wrote `'pillar-' + i`, so the separator is part of what it wrote.
_DIGITS = re.compile(r"^(.*?\D)\d+$")
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_DECL_BEFORE = re.compile(r"(?:\bfunction\s*\*?|\bclass|\bconst|\blet|\bvar)\s+$")
_FN_BEFORE = re.compile(r"(?:\bfunction\s*\*?|\bclass)\s+$")
_VAR_BEFORE = re.compile(r"(?:\bconst|\blet|\bvar)\s+$")
# `const buildX = (opts) => …`, `const makeY = function (…)`, `const z = async p => …`
_FN_AFTER = re.compile(r"\s*(?::[^=\n]{0,80})?=\s*(?:async\b\s*)?(?:function\b|\(|[A-Za-z_$][\w$]*\s*=>)")
# A class method: `buildPillar(opts) {` alone on its line, modifiers allowed in front.
_METHOD_LINE = re.compile(r"^\s*(?:(?:public|private|protected|static|async|override|readonly|export|default)\s+)*"
                          r"[A-Za-z_$][\w$]*\s*(?:<[^>]*>)?\s*\([^)]*\)?")
# WHAT A BUILDER IS CALLED. `buildPillar`, `createPlayer`, `pillarMesh`, `class Runner` - and not
# `playerFootY`, which the first version handed back for `player` because it merely starts with the
# word. The name, less underscores, has to be the word with at most a verb before it and a noun of
# made-things after it.
_VERB = ("build|make|create|spawn|add|new|gen|generate|construct|assemble|init|setup|place|load|"
         "forge|draw|render|mk")
_MADE = "mesh|model|geometry|geo|group|entity|factory|builder|asset|prefab|node|object|obj|actor|rig"
# The line that NAMES a thing outranks one that merely mentions it: `x.name = …`, `name: …`,
# `new pc.Entity('…')`. That is the line the object's name came from.
_NAMING = re.compile(r"\bname\s*[:=]|\.name\b\s*=|setName\s*\(|new\s+(?:pc\.)?Entity\s*\(|"
                     r"\b(?:id|key|label|title)\s*:|this\.emit\(")


def _quoted(corpus: str, p: int, n: int) -> bool:
    """Is corpus[p:p+n] a whole string literal - the same quote right before and right after?"""
    if p < 1 or p + n >= len(corpus):
        return False
    q = corpus[p - 1]
    return q in _QUOTES and corpus[p + n] == q


def _template_head(corpus: str, p: int, n: int) -> bool:
    """Is corpus[p:p+n] the fixed start of a template literal: `` `pillar-${ ``?"""
    return p >= 1 and corpus[p - 1] == "`" and corpus.startswith("${", p + n)


def _template_tail(corpus: str, p: int, n: int) -> bool:
    """Is corpus[p:p+n] the fixed end of a template literal: `` }-still` ``?"""
    return p >= 1 and corpus[p - 1] == "}" and p + n < len(corpus) and corpus[p + n] == "`"


def _ident_span(corpus: str, p: int, n: int) -> tuple[int, int]:
    a, b = p, p + n
    while a > 0 and corpus[a - 1] in _IDCH:
        a -= 1
    L = len(corpus)
    while b < L and corpus[b] in _IDCH:
        b += 1
    return a, b


def _plan(name: str) -> list[tuple]:
    """[(tier, anchor, case_blind, accept(corpus, p) -> bool)] for one name, best tier first.

    Every anchor is a piece of text the hit MUST contain, so the scan is a plain `find`. The tiers
    are strict: 1 the exact literal, 2 the numbered stem (`'pillar-'`, what really named
    `pillar-2`), 3 the bare stem, 4 a declaration of it, 5 a builder whose name holds it, 6 a use."""
    raw = _PIECE.sub("", str(name or "").strip())
    if not raw or raw.startswith("/") or len(raw) > 200:
        return []                        # an unnamed object's key is its child path: nothing to find
    plan: list[tuple] = []
    n_raw = len(raw)
    plan.append((1, raw, False, lambda c, p, n=n_raw: _quoted(c, p, n)))

    stem = bare = ""
    m = _DUP.match(raw) or _PAREN.match(raw)
    if m:
        stem = bare = m.group(1)
    else:
        m = _DIGITS.match(raw)
        if m and m.group(1).strip(" -_.#:"):
            stem = m.group(1)
            bare = stem.rstrip(" -_.#:")
    if stem:
        ns = len(stem)
        plan.append((2, stem, False, lambda c, p, n=ns: _quoted(c, p, n) or _template_head(c, p, n)))
        if bare and bare != stem:
            nb = len(bare)
            plan.append((3, bare, False, lambda c, p, n=nb: _quoted(c, p, n)))
    else:
        # `face-left` made by `` `face-${side}` ``: the name without its last word is a template head.
        cut = max(raw.rfind(ch) for ch in "-_.")
        if cut >= 2:
            head = raw[:cut + 1]
            nh = len(head)
            plan.append((2, head, False, lambda c, p, n=nh: _template_head(c, p, n)))
        # ...and `tungtungsahur-still` made by `` `${a.key}-still` `` (rot-rush, brainrots.ts:1181):
        # there the fixed part is the TAIL - a template's last piece, or a literal added on.
        if 0 < cut < len(raw) - 2:
            tail = raw[cut:]
            nt = len(tail)
            plan.append((2, tail, False, lambda c, p, n=nt: _template_tail(c, p, n) or _quoted(c, p, n)))
        # A node that came out of a model file - `TungTungSahur_Armature` - is named by nothing in
        # the code, but the name before its last word usually is: the spec row that loads it.
        if cut >= 3:
            front = raw[:cut]
            nf = len(front)
            plan.append((3, front, False, lambda c, p, n=nf: _quoted(c, p, n)))

    core = bare or raw
    words = [w for w in re.split(r"[^A-Za-z0-9]+", core) if w]
    ident_core = bool(_IDENT.match(core))
    if ident_core:
        nc = len(core)

        def decl_exact(c, p, n=nc):
            a, b = _ident_span(c, p, n)
            return a == p and b == p + n and bool(_DECL_BEFORE.search(c, max(0, p - 24), p))
        plan.append((4, core, False, decl_exact))
    joined = "".join(words).lower()
    if len(joined) >= 4:
        variants = [joined] + (["_".join(w.lower() for w in words)] if len(words) > 1 else [])
        for v in variants:
            nv = len(v)

            shape = re.compile(r"^(?:%s)?%s(?:%s)?$" % (_VERB, re.escape(joined), _MADE))

            def builder(c, p, n=nv, exact=core, shape=shape):
                a, b = _ident_span(c, p, n)
                ident = c[a:b]
                if ident == exact:
                    return False                       # that is the exact tier's, not this one's
                if re.search(r"class\s+$", c[max(0, a - 12):a]):
                    return True                        # `class Runner` for a `runner`: always
                if not shape.match(ident.replace("_", "").lower()):
                    return False
                if _FN_BEFORE.search(c, max(0, a - 24), a):
                    return True
                if _VAR_BEFORE.search(c, max(0, a - 24), a):
                    return bool(_FN_AFTER.match(c, b))
                ls = c.rfind("\n", 0, a) + 1
                le = c.find("\n", b)
                line = c[ls:le if le >= 0 else len(c)]
                return (bool(_METHOD_LINE.match(line)) and c[ls:a].strip() in ("", "async", "static", "public",
                                                                                   "private", "protected")
                        and line.rstrip().endswith("{"))
            plan.append((5, v, True, builder))
    if ident_core and len(core) >= 3:
        nc2 = len(core)

        def use(c, p, n=nc2):
            a, b = _ident_span(c, p, n)
            return a == p and b == p + n and (p == 0 or c[p - 1] != ".")
        plan.append((6, core, False, use))
    return plan


def _format(rel: str, line_no: int, line: str, col: int) -> str:
    head = "%s:%d: " % (rel, line_no)
    room = max(20, HINT_CHARS - len(head))
    body = line.rstrip("\r")
    lead = len(body) - len(body.lstrip())
    body = body.strip()
    col = max(0, col - lead)
    if len(body) > room:
        # A long line is cut AROUND the match, not from its start: a table row with the name at
        # column 300 is useless trimmed to its first 150 characters.
        start = max(0, min(col - 30, len(body) - room + 1))
        body = ("…" if start else "") + body[start:start + room - (2 if start else 1)] + "…"
    return (head + body)[:HINT_CHARS]


def _search(idx: dict, name: str, per_name: int, prefer: str) -> list[tuple]:
    plan = _plan(name)
    files = idx.get("files") or []
    corpus = idx.get("corpus") or ""
    if not plan or not files:
        return []
    starts = idx["starts"]
    low = idx.get("low") or ""
    pref = prefer.strip("/").lower() + "/" if prefer and prefer.strip("/") else ""
    seen: set = set()
    picked: list[tuple] = []
    # A hit on a comment line: `runner` in a JSDoc sentence matched the literal tier as though it
    # were a string. It is a lead, but a weaker one than any line of code, so it waits until
    # every tier has had its turn.
    remarks: list[tuple] = []
    tier_now, found = 0, []

    def flush():
        found.sort(key=lambda t: t[:8])
        picked.extend(found)
        found.clear()

    for tier_no, anchor, blind, accept in plan:
        if tier_no != tier_now:
            flush()
            if len(picked) >= per_name:
                break
            tier_now = tier_no
        hay = low if blind else corpus
        if blind and not hay:
            continue
        a = anchor.lower() if blind else anchor
        pos = hay.find(a)
        while pos >= 0 and len(found) < _TIER_CAP:
            if accept(corpus, pos):
                fi = bisect_right(starts, pos) - 1
                f = files[fi]
                line_no, line, col = f.line_at(pos - starts[fi])
                k = (fi, line_no)
                if k not in seen:
                    seen.add(k)
                    away = 0 if not pref or f.rel.lower().startswith(pref) else 1
                    said = 1 if line.lstrip().startswith(("//", "*", "/*")) else 0
                    (remarks if said else found).append(
                        (tier_no, said, 0 if _NAMING.search(line) else 1, away, f.rank,
                         f.rel.count("/"), f.rel.lower(), line_no, f.rel, line, col))
            pos = hay.find(a, pos + 1)
    flush()
    if len(picked) < per_name:
        picked.extend(sorted(remarks, key=lambda t: t[:8]))
    return picked[:per_name]


def hints_for(project: str, names: list, per_name: int = 3, prefer: str = "") -> dict:
    """{name: ["<relative path>:<line>: <trimmed source line>", ...]} for each name asked.

    `prefer` is a project-relative folder whose files win a tie: the game a workspace's tab is
    serving, when the workspace holds several games that reuse each other's names.
    """
    per = max(1, min(20, int(per_name or 3)))
    out: dict[str, list[str]] = {}
    wanted: list[str] = []
    for n in names or []:
        s = str(n if n is not None else "")
        if s in out:
            continue
        out[s] = []
        wanted.append(s)
        if len(wanted) >= _MAX_NAMES:
            break
    if not wanted:
        return out
    idx = _files(project)
    for n in wanted:
        out[n] = [_format(t[8], t[7], t[9], t[10]) for t in _search(idx, n, per, prefer)]
    return out

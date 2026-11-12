"""The model files an agent writes, shown in the Engine window while it works.

THE BENCH ONLY KNEW WHAT CAME THROUGH THE FORGE. An agent that edits a GLB with a script of its
own — numpy over the buffers, Blender in the background, a Playwright page it started itself —
never calls /api/live/glb. Measured on one such job: the agent wrote shape1.glb and shape2.glb
beside its input, rendered them to PNGs nobody saw, and the Engine window said "live · empty"
over an empty grid for the whole of it, because the one thing it had sent the forge was a probe
that returned an empty group.

So the session hook hands every tool call here BEFORE it runs. A .glb or .gltf named in it is
noted with that time. A noted file whose date is LATER than the note was written by the call
that named it (or by something that call ran), and the newest of those is the agent's model:
the window loads it, and loads it again each time the file is written again.

Two halves, because the hook is a separate process that must never fail a turn:

- `record()` runs inside the hook (btw_hook.py). Standard library only, no Studio imports, one
  appended line per tool call that names a model and nothing at all for the rest.
- `latest()` and `bench()` run in the backend. They read the appended lines and look at the
  files themselves; nothing the hook wrote is believed about a file, only that it was named.
"""
import json
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Optional

FEED_NAME = "agent_models.jsonl"

# A model file, by its extension at the end of a token.
_MODEL = re.compile(r"\.(?:glb|gltf)$", re.I)
# A quoted string may hold spaces ("D:\Asset Studio\out.glb"), so quoted ones are read first.
_QUOTED = re.compile(r"""(["'`])([^"'`\r\n]{1,500}?)\1""")
# Everything a shell or a Python call puts between two paths.
_SPLIT = re.compile(r"""[\s"'`<>|;&(){}\[\],=*?]+""")
# The folders a command moves into before it names a file relative to them.
_CD = re.compile(r"""(?:^|[;&|(\n]\s*)(?:cd|pushd|Set-Location|sl)\s+(?:/d\s+)?("[^"\r\n]+"|'[^'\r\n]+'|[^\s;&|)]+)""",
                 re.I)
# Git Bash writes C:\tmp as /c/tmp.
_GITBASH = re.compile(r"^/([A-Za-z])(/.*)?$")
# `D=/g/out; cp a.glb $D/b.glb`: a folder named once and used after, in the same command.
_ASSIGN = re.compile(r"""(?:^|[;&|\n]\s*)(?:export\s+)?([A-Za-z_]\w*)=("[^"\r\n]*"|'[^'\r\n]*'|[^\s;&|]+)""")
_MAX_TEXT = 200_000          # of one tool call's input, read for names
_MAX_PATHS = 12              # noted per tool call


# ---------------------------------------------------------------------------
# The hook's half: which model files does this tool call name?
# ---------------------------------------------------------------------------
def _strings(value, out: list, budget: list) -> None:
    """Every string in a tool's input, the input's own nesting flattened, to a budget."""
    if budget[0] <= 0:
        return
    if isinstance(value, str):
        out.append(value[:budget[0]])
        budget[0] -= len(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out, budget)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out, budget)


def _drive(p: str) -> str:
    """A Git Bash path as Windows writes it; anything else unchanged."""
    m = _GITBASH.match(p)
    if m and os.name == "nt":
        return m.group(1).upper() + ":" + (m.group(2) or "/")
    return p


def _is_abs(p: str) -> bool:
    if os.name == "nt":
        return bool(re.match(r"^[A-Za-z]:[\\/]", p)) or p.startswith("\\\\")
    return p.startswith("/")


def _expand(text: str) -> str:
    """The shell variables a command sets, put in where it uses them."""
    names = {}
    for m in _ASSIGN.finditer(text):
        names[m.group(1)] = m.group(2).strip("\"'")
    for k in sorted(names, key=len, reverse=True):
        text = text.replace("${%s}" % k, names[k]).replace("$" + k, names[k])
    return text


def _named(text: str) -> list:
    """The model files a piece of text names, as written."""
    text = _expand(text)
    found = []
    for m in _QUOTED.finditer(text):
        s = m.group(2).strip()
        # A quoted string with a space is a path only when it is a whole one: "D:/My Models/a.glb"
        # is, "cp a.glb b.glb" is a command quoted inside a script.
        if _MODEL.search(s) and not (" " in s and (not _is_abs(_drive(s)) or any(c in s for c in "&|;"))):
            found.append(s)
    for tok in _SPLIT.split(text):
        tok = tok.rstrip(".:")
        if _MODEL.search(tok):
            found.append(tok)
    keep = []
    for s in found:
        stem = re.split(r"[\\/]", s)[-1].rsplit(".", 1)[0]
        # A glob, a URL, a shell or Python variable, or a bare extension names no one file.
        if not stem or "://" in s or any(c in s for c in "$%{}*?<>"):
            continue
        if s not in keep:
            keep.append(s)
    return keep


def paths_in(tool_input, cwd: str = "") -> list:
    """The absolute paths of the model files one tool call names.

    A relative name is resolved against every folder the command moves into, newest first, and
    then the session's own folder. A wrong guess costs nothing: a path that is never written is
    never shown."""
    texts: list = []
    _strings(tool_input, texts, [_MAX_TEXT])
    text = "\n".join(t for t in texts if ".gl" in t.lower())
    if not text:
        return []
    bases = []
    for m in _CD.finditer(text):
        d = _drive(os.path.expanduser(m.group(1).strip("\"'")))
        if not _is_abs(d) and cwd:
            d = os.path.join(cwd, d)
        if _is_abs(d) and d not in bases:
            bases.insert(0, d)
    if cwd:
        bases.append(cwd)
    out: list = []
    for s in _named(text):
        p = _drive(os.path.expanduser(s))
        if _is_abs(p):
            cands = [p]
        elif p.startswith(("/", "\\")):
            continue                      # rooted with no drive: which drive is anybody's guess
        else:
            cands = [os.path.join(b, p) for b in bases[:4]]
        for c in cands:
            c = os.path.normpath(c)
            if "node_modules" in c.replace("\\", "/").split("/"):
                continue
            if c not in out:
                out.append(c)
        if len(out) >= _MAX_PATHS:
            break
    return out[:_MAX_PATHS]


def feed_file(data_dir) -> Path:
    return Path(data_dir) / "hooks" / FEED_NAME


def record(payload: dict, data_dir) -> None:
    """Note the model files a tool call is about to touch. Called by the hook; never raises."""
    try:
        event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "")
        if event != "PreToolUse":
            return
        cwd = str(payload.get("cwd") or "")
        paths = paths_in(payload.get("tool_input"), cwd)
        if not paths:
            return
        line = json.dumps({"ts": time.time(), "cwd": cwd, "tool": str(payload.get("tool_name") or ""),
                           "paths": paths}, ensure_ascii=False)
        f = feed_file(data_dir)
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The backend's half: which of those files did the agent write, newest first?
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_seen: dict = {}             # normcase path -> {"path", "cwd", "noted"}; noted = FIRST time named
_offset = [-1]               # how far the feed has been read; -1 = not yet, start near the end
_counts_cache: dict = {}     # (path, mtime_ns, size) -> triangle counts
_SEEN_MAX = 400
_TAIL = 256 * 1024           # read on the first call: the last few hundred notes survive a restart
_TRIM_AT = 1024 * 1024
_SLACK = 2.0                 # a note can land a moment after the tool started
_SETTLE = 0.8                # a file written in the last moment may still be half written
_FRESH = 2 * 3600.0          # an older file is not what anybody is working on now


def _data_dir() -> Path:
    from .config import DATA_DIR
    return DATA_DIR


def _read_feed() -> None:
    f = feed_file(_data_dir())
    try:
        size = f.stat().st_size
    except OSError:
        return
    with _lock:
        start = _offset[0]
        initial = start < 0
        if initial:
            start = max(0, size - _TAIL)
        elif size < start:
            start = 0                      # trimmed under us: read what is there now
        if size == start:
            _offset[0] = start
            return
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
        except OSError:
            return
        # A half-written last line waits for the next read.
        end = data.rfind(b"\n")
        if end < 0:
            _offset[0] = start
            return
        _offset[0] = start + end + 1
        lines = data[:end].split(b"\n")
        if initial and start > 0:
            lines = lines[1:]               # the cut landed inside a line
        for raw in lines:
            try:
                row = json.loads(raw)
            except Exception:
                continue
            ts = float(row.get("ts") or 0)
            for p in row.get("paths") or []:
                k = os.path.normcase(str(p))
                cur = _seen.get(k)
                if cur is None:
                    _seen[k] = {"path": str(p), "cwd": str(row.get("cwd") or ""), "noted": ts}
                else:
                    cur["cwd"] = str(row.get("cwd") or cur["cwd"])
        if len(_seen) > _SEEN_MAX:
            for k, _ in sorted(_seen.items(), key=lambda kv: kv[1]["noted"])[:len(_seen) - _SEEN_MAX]:
                _seen.pop(k, None)
    if size > _TRIM_AT:
        _trim(f)


def _trim(f: Path) -> None:
    """Keep the tail. A hook appending in the same instant can lose one line, which costs one
    file that is noted again the next time it is named."""
    try:
        data = f.read_bytes()
        cut = data[-_TAIL:]
        nl = cut.find(b"\n")
        f.write_bytes(cut[nl + 1:] if nl >= 0 else cut)
        with _lock:
            _offset[0] = -1
    except OSError:
        pass


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p)).rstrip("\\/")


def _under(child: str, parent: str) -> bool:
    if not child or not parent:
        return False
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p + os.sep)


def counts(path: str) -> dict:
    """Triangles, meshes and materials, read from the file's JSON alone. No buffer is opened."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_mtime_ns, st.st_size)
    hit = _counts_cache.get(key)
    if hit is not None:
        return hit
    out: dict = {}
    try:
        with open(path, "rb") as fh:
            head = fh.read(20)
            if head[:4] == b"glTF":
                jlen, jtype = struct.unpack("<I4s", head[12:20])
                if jtype != b"JSON" or jlen > 64 * 1024 * 1024:
                    raise ValueError("no JSON chunk")
                j = json.loads(fh.read(jlen))
            elif st.st_size < 64 * 1024 * 1024:
                j = json.loads(head + fh.read())
            else:
                raise ValueError("too big")
        acc = j.get("accessors") or []

        def tris(prim: dict) -> int:
            mode = prim.get("mode", 4)
            i = prim.get("indices")
            if not isinstance(i, int):
                i = (prim.get("attributes") or {}).get("POSITION")
            n = int(acc[i].get("count") or 0) if isinstance(i, int) and 0 <= i < len(acc) else 0
            return n // 3 if mode == 4 else max(0, n - 2) if mode in (5, 6) else 0

        per_mesh = [sum(tris(p) for p in (m.get("primitives") or [])) for m in (j.get("meshes") or [])]
        users = [n["mesh"] for n in (j.get("nodes") or [])
                 if isinstance(n.get("mesh"), int) and 0 <= n["mesh"] < len(per_mesh)]
        out = {"triangles": sum(per_mesh[m] for m in users) if users else sum(per_mesh),
               "meshes": len(users) or len(per_mesh),
               "materials": len(j.get("materials") or [])}
    except Exception:
        out = {}
    if len(_counts_cache) > 64:
        _counts_cache.clear()
    _counts_cache[key] = out
    return out


def latest(project: str = "") -> Optional[dict]:
    """The newest model file an agent wrote, or None.

    With a project, only a file inside it, or one written by an agent working in it (or in a
    folder inside it, which is how a workspace holds its games)."""
    _read_feed()
    now = time.time()
    with _lock:
        rows = [dict(r) for r in _seen.values()]
    best = None
    for r in rows:
        if project and not (_under(r["path"], project) or _under(r["cwd"], project)
                            or _under(project, r["cwd"])):
            continue
        try:
            st = os.stat(r["path"])
        except OSError:
            continue
        mt = st.st_mtime
        if mt < r["noted"] - _SLACK or now - mt > _FRESH or now - mt < _SETTLE or st.st_size < 32:
            continue
        if best is None or mt > best[0]:
            best = (mt, r, st)
    if not best:
        return None
    mt, r, st = best
    return {"path": r["path"], "name": Path(r["path"]).name, "cwd": r["cwd"], "mtime": mt,
            "size": st.st_size, "v": st.st_mtime_ns // 1_000_000}


def bench(project: str = "") -> Optional[dict]:
    """The newest model file an agent wrote, in the shape /api/live/bench answers with.

    The code is the same comment /glb leaves, so the window treats it exactly as it treats a GLB
    on the bench: it loads the file itself, and `v` makes a rewritten file a new scene."""
    m = latest(project)
    if not m:
        return None
    c = counts(m["path"])
    return {
        "ok": True, "open": True,
        "project": m["cwd"] or str(Path(m["path"]).parent),
        "code": "/* GLB: %s */" % m["name"],
        "models": [{"name": m["name"], "path": m["path"], "v": m["v"]}],
        "sig": "file:%d:%d" % (m["v"], m["size"]),
        "at": {},
        "stats": {"triangles": c.get("triangles"), "meshes": c.get("meshes"),
                  "materials": c.get("materials")},
        "engine": "three",
        "label": Path(m["name"]).stem,
        "ts": m["mtime"],
        # Not a forge bench: a file on disk, which the window says, with where it is.
        "source": "file",
        "file": m["path"],
    }

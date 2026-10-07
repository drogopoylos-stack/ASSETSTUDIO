"""Workspace file APIs — a VSCode-style explorer/editor for Claude Code projects.

SECURITY: every path is gated. Browsing/reading/writing is restricted to inside a
known Claude Code project root (from the Mission Control scan). Creating new
folders/projects is allowed inside those roots or inside the user's standard base
dirs (home / Downloads / Desktop / Documents). Anything else is refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import fsutil
from . import mission
from . import dev_server
from .config import DATA_DIR, claude_home, claude_json, settings

# directories we never expand/list (huge / noise)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist",
              ".turbo", ".cache", "build", ".idea", ".pytest_cache"}
_TEXT_MAX = 1_500_000  # 1.5 MB text cap
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
# what <model-viewer> can actually render in the editor pane (see read_file)
_MODEL_EXT = {".glb", ".gltf"}
_MESH_EXT = {".obj", ".stl", ".ply", ".off"}   # converted to GLB on the fly so they're viewable
_HTML_EXT = {".html", ".htm"}


def _project_roots(fresh: bool = False) -> list[Path]:
    """The folders of every Claude project, for the path checks.

    Reads `mission.project_index()`, NOT `mission.overview()`. The overview is the rich answer —
    git, summaries, phases — it costs 0.5-1.1 s for 52 projects, and its cache is three seconds
    old, so asking it here meant the explorer's one-second change poll kept a full rescan of every
    project running for as long as a window was open. Measured on the real UI: `/workspace/tree`
    662 ms and `/workspace/changes` 680 ms during a workspace switch, for a list of folders that
    changes when you open one.
    """
    roots = []
    for p in mission.project_index(fresh=fresh):
        path = p.get("path")
        if path:
            try:
                rp = Path(path).resolve()
                if rp.exists():
                    roots.append(rp)
            except Exception:
                continue
    return roots


@lru_cache(maxsize=4096)
def _claude_id(path: str) -> str:
    """Encode a folder the way Claude Code names its ~/.claude/projects/<id> dir:
    EVERY non-alphanumeric char in the absolute path becomes '-' (so spaces, dots,
    parentheses, ':' '\\' '/' all collapse to '-', and runs like ' - ' become '---').
    Drive letter is lowercased; Windows' case-insensitive filesystem reconciles any
    remaining case difference. Critically this means a folder whose NAME has spaces
    (e.g. "WOLT - EFOOD automatic upload") binds to the correct session/feed/transcript
    dir — otherwise the working indicators poll an id with literal spaces that matches
    no dir, so a running turn looks dead."""
    p = str(Path(path).resolve())
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + p[1:]
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def _extra_roots() -> list[Path]:
    """Folders the user explicitly opened in the workspace (persisted in settings)."""
    out = []
    for s in (settings.get("workspace_roots") or []):
        try:
            rp = Path(s).resolve()
            if rp.exists() and rp.is_dir():
                out.append(rp)
        except Exception:
            pass
    return out


_ROOTS_CACHE: dict = {"at": 0.0, "val": []}
_ROOTS_TTL = 5.0


def invalidate_roots() -> None:
    """Called when the open-folder set changes, so a new root is usable immediately."""
    _ROOTS_CACHE["at"] = 0.0


def _browse_roots(fresh: bool = False) -> list[Path]:
    """The open project/folder roots, cached for a few seconds.

    Every path check goes through here — the explorer's 1-second change poll, every read, every
    thumbnail — and each call resolved ~69 paths against the filesystem. Cheap once, wasteful
    sixty times a minute. Correctness is preserved by the callers that DENY on a miss: they retry
    with ``fresh=True``, so a folder opened one second ago is never rejected for being new."""
    now = time.time()
    if not fresh and _ROOTS_CACHE["val"] and (now - _ROOTS_CACHE["at"]) < _ROOTS_TTL:
        return _ROOTS_CACHE["val"]          # type: ignore[return-value]
    # `fresh` goes all the way down: a project opened one second ago must not be refused because
    # the index behind this list is a few seconds old.
    val = _project_roots(fresh=fresh) + _extra_roots()
    _ROOTS_CACHE.update(at=now, val=val)
    return val


def _create_bases() -> list[Path]:
    home = Path.home()
    bases = [home]
    for sub in ("Downloads", "Desktop", "Documents", "Projects", "dev", "code"):
        bases.append(home / sub)
    # also allow making siblings next to existing projects / opened folders
    for r in _project_roots() + _extra_roots():
        bases.append(r.parent)
        bases.append(r)
    out = []
    for b in bases:
        try:
            out.append(b.resolve())
        except Exception:
            pass
    return out


def _within(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.resolve()
    except Exception:
        return False
    for r in roots:
        if rp == r or r in rp.parents:
            return True
    return False


def _check_browse(path: str) -> Path:
    p = Path(path)
    if not _within(p, _browse_roots()):
        # a folder opened seconds ago must not be refused just because the list is cached
        if not _within(p, _browse_roots(fresh=True)):
            raise PermissionError("path is outside any open project/folder")
    rp = p.resolve()
    if not rp.exists():
        raise FileNotFoundError(str(rp))
    return rp


def _check_create(parent: str) -> Path:
    p = Path(parent).resolve()
    if not (_within(p, _project_roots()) or _within(p, _create_bases())):
        raise PermissionError("location not allowed (must be a project or a standard folder)")
    return p


# ---------------------------------------------------------------------------
def roots() -> list[dict]:
    out = []
    seen: set[str] = set()
    for p in mission.project_index():
        path = p.get("path")
        if not path:
            continue
        try:
            rp = Path(path).resolve()
        except Exception:
            continue
        if rp.exists() and str(rp).lower() not in seen:
            seen.add(str(rp).lower())
            # CANONICAL id — always _claude_id(path), never the on-disk transcript-dir name.
            # Claude Code creates those dirs with the drive letter in either case ("C--…planner"
            # vs "c--…STUDIO"); handing both spellings to the UI meant every case-SENSITIVE
            # in-memory map (_live, _last_agent, the frontend's ccLive) filed a project's live
            # state under one key while the feed read the other — so a project's working/live
            # state could show as another's (or vanish). Path lookups stay correct because the
            # Windows filesystem resolves the transcript dir case-insensitively.
            out.append({"id": _claude_id(str(rp)), "name": p.get("name") or rp.name, "path": str(rp)})
    # folders the user opened that aren't (yet) Claude Code projects
    for rp in _extra_roots():
        if str(rp).lower() in seen:
            continue
        seen.add(str(rp).lower())
        out.append({"id": _claude_id(str(rp)), "name": rp.name, "path": str(rp), "opened": True})
    return out


# ---------------------------------------------------------------------------
# Finding the user's projects on a PC the Studio has never seen
# ---------------------------------------------------------------------------
# Claude Code's own registry first — every folder the person ever ran `claude` in, with its exact
# path — then the folders people keep projects in, one level deep, judged by what a project leaves
# behind (a git repo, a package.json, a page, a CLAUDE.md). Names are the folder names: that is
# what the person calls them everywhere else.
_PROJECT_MARKS = (".git", "package.json", "index.html", "CLAUDE.md", ".claude", "pyproject.toml",
                  "project.godot", "Cargo.toml", "go.mod")
_DISCOVER_DIRS = ("Desktop", "Documents", "Downloads", "Projects", "projects", "dev", "code", "src",
                  "repos", "git", "work", "games", "OneDrive/Desktop", "OneDrive/Documents")


def _claude_registry_projects() -> list[Path]:
    out: list[Path] = []
    try:
        cfg = claude_json()
        if cfg.is_file():
            data = json.loads(cfg.read_text(encoding="utf-8", errors="replace"))
            for k in (data.get("projects") or {}).keys():
                try:
                    p = Path(k)
                    if p.is_dir():
                        out.append(p.resolve())
                except OSError:
                    continue
    except Exception:
        pass
    return out


def discover_projects(limit: int = 60) -> list[dict]:
    """Folders on this machine that look like projects and are not open in the Studio yet."""
    have = {str(p).lower() for p in _project_roots() + _extra_roots()}
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        k = str(p).lower()
        if k in have or k in seen:
            return
        seen.add(k)
        found.append(p)

    for p in _claude_registry_projects():
        add(p)
    home = Path.home()
    for sub in _DISCOVER_DIRS:
        base = home / sub
        try:
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir(), key=lambda c: c.name.lower()):
                if len(found) >= limit:
                    break
                try:
                    if not child.is_dir() or child.name.startswith(".") or child.name.lower() in _ENTRY_SKIP:
                        continue
                    if any((child / m).exists() for m in _PROJECT_MARKS):
                        add(child.resolve())
                except OSError:
                    continue
        except OSError:
            continue
    return [{"path": str(p), "name": p.name} for p in found[:limit]]


def add_roots(paths: list[str]) -> int:
    """Open these folders in the Studio (persisted), skipping any already open. Returns how many were new."""
    cur = [str(s) for s in (settings.get("workspace_roots") or [])]
    have = {str(Path(s).resolve()).lower() for s in cur if s}
    added = 0
    for s in paths:
        try:
            p = Path(s).resolve()
        except OSError:
            continue
        if not p.is_dir() or str(p).lower() in have:
            continue
        cur.append(str(p))
        have.add(str(p).lower())
        added += 1
    if added:
        settings.update({"workspace_roots": cur})
        invalidate_roots()
    return added


def autodiscover_roots() -> int:
    """The first run on a new PC: nothing open, no Claude Code project yet — open what is theirs.

    Only when the list is EMPTY, so an existing setup is never padded with folders nobody asked
    for; and only when the setting allows it. Returns how many folders were opened."""
    if not settings.get("workspace_autodiscover", True):
        return 0
    if roots():
        return 0
    return add_roots([d["path"] for d in discover_projects()])


def list_dir(path: str) -> dict:
    rp = _check_browse(path)
    if not rp.is_dir():
        raise NotADirectoryError(str(rp))
    entries = []
    try:
        items = sorted(rp.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        items = []
    for child in items[:800]:
        try:
            is_dir = child.is_dir()
            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": is_dir,
                "size": (child.stat().st_size if not is_dir else 0),
                "skip": child.name in _SKIP_DIRS,
            })
        except OSError:
            continue
    return {"path": str(rp), "entries": entries}


def read_file(path: str) -> dict:
    rp = _check_browse(path)
    if rp.is_dir():
        raise IsADirectoryError(str(rp))
    ext = rp.suffix.lower()
    st = rp.stat()
    size, mtime = st.st_size, st.st_mtime
    if ext in _IMG_EXT:
        return {"path": str(rp), "kind": "image", "size": size, "mtime": mtime}
    # 3D before the size gate: a GLB is binary AND usually over _TEXT_MAX, so it would otherwise
    # report "too large" / "binary" and never reach the viewer. Only the two formats
    # <model-viewer> actually renders — .obj/.fbx/.stl stay binary rather than open to a blank box.
    if ext in _MODEL_EXT:
        return {"path": str(rp), "kind": "model", "size": size, "mtime": mtime}
    # Other meshes (.obj/.stl/.ply) aren't glTF, so the viewer can't read them directly —
    # but trimesh (already a dependency) converts them to GLB on demand, so they open in the
    # same 3D view instead of dead-ending as "binary file". `convert` tells the UI to fetch
    # the converted bytes from /api/workspace/model rather than the raw file.
    if ext in _MESH_EXT:
        return {"path": str(rp), "kind": "model", "size": size, "mtime": mtime, "convert": True}
    if size > _TEXT_MAX:
        # Still previewable when it is HTML: the preview serves the file over http and never reads
        # it into the editor, so the text cap has nothing to do with it. A big generated page was
        # exactly the case that showed "File too large to open here" and offered no way to look at it.
        return {"path": str(rp), "kind": "toobig", "size": size, "mtime": mtime,
                "previewable": ext in _HTML_EXT}
    try:
        text = rp.read_text(encoding="utf-8")
        # `previewable` = the editor can offer a live Preview toggle beside the code. HTML stays a
        # TEXT tab on purpose: you must still be able to edit and save it, unlike an image or a GLB.
        return {"path": str(rp), "kind": "text", "text": text, "size": size, "mtime": mtime,
                "ext": ext, "previewable": ext in _HTML_EXT}
    except (UnicodeDecodeError, OSError):
        return {"path": str(rp), "kind": "binary", "size": size, "mtime": mtime}


def preview_url(path: str) -> dict:
    """Localhost URL that serves this file's FOLDER, for the in-app preview iframe.

    An ES-module page (`import './foo.js'`, import maps) is blocked by CORS from a `file://`
    document, so a preview must go over http. This reuses the same throwaway static server that
    "Run" already uses — one per folder, reaped when idle."""
    rp = _check_browse(path)
    if rp.is_dir():
        raise IsADirectoryError(str(rp))
    from . import preview_server
    return {"url": preview_server.serve_file(rp)}


def stat_file(path: str) -> dict:
    """Cheap mtime/size check for live-sync (detect external edits)."""
    rp = _check_browse(path)
    st = rp.stat()
    return {"path": str(rp), "mtime": st.st_mtime, "size": st.st_size}


def write_file(path: str, text: str) -> dict:
    p = Path(path)
    # allow writing to a file within any open project or opened folder
    if not _within(p, _browse_roots()):
        raise PermissionError("path is outside any open project/folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    return {"path": str(p.resolve()), "size": st.st_size, "mtime": st.st_mtime, "ok": True}


def mkdir(path: str) -> dict:
    p = Path(path)
    parent = p.parent
    _check_create(str(parent))
    p.mkdir(parents=True, exist_ok=True)
    return {"path": str(p.resolve()), "ok": True}


def delete(path: str) -> dict:
    rp = _check_browse(path)
    # never delete a root folder itself
    if rp in _browse_roots():
        raise PermissionError("refusing to delete a root folder")
    if rp.is_dir():
        shutil.rmtree(rp, ignore_errors=True)
    else:
        rp.unlink(missing_ok=True)
    return {"ok": True}


def rename(path: str, new_name: str) -> dict:
    """Rename a file/folder in place (stays inside its parent)."""
    rp = _check_browse(path)
    if rp in _browse_roots():
        raise PermissionError("refusing to rename a root folder")
    name = (new_name or "").strip()
    if not name or name in (".", "..") or re.search(r"[\\/]", name) or any(c in name for c in '<>:"|?*'):
        raise ValueError("invalid name")
    dest = rp.parent / name
    if dest.resolve() == rp.resolve():
        return {"ok": True, "path": str(rp.resolve()), "name": rp.name}
    if dest.exists():
        raise FileExistsError(f"'{name}' already exists")
    rp.rename(dest)
    return {"ok": True, "path": str(dest.resolve()), "name": name}


def _dest_dir(dest_dir: str) -> Path:
    d = Path(dest_dir)
    if not _within(d, _browse_roots()):
        raise PermissionError("destination is outside any open project/folder")
    dr = d.resolve()
    if not dr.exists():
        raise FileNotFoundError(str(dr))
    if not dr.is_dir():
        raise NotADirectoryError(str(dr))
    return dr


def _free_path(dr: Path, name: str) -> Path:
    """A non-clobbering destination path inside ``dr`` (appends ' (n)' if taken)."""
    dest = dr / name
    if not dest.exists():
        return dest
    stem, suf = Path(name).stem, Path(name).suffix
    i = 1
    while True:
        i += 1
        cand = dr / f"{stem} ({i}){suf}"
        if not cand.exists():
            return cand


def move(path: str, dest_dir: str) -> dict:
    """Move (cut+paste) a file/folder into another folder."""
    rp = _check_browse(path)
    if rp in _browse_roots():
        raise PermissionError("refusing to move a root folder")
    dr = _dest_dir(dest_dir)
    if rp.parent.resolve() == dr:
        return {"ok": True, "path": str(rp.resolve()), "name": rp.name}  # already there
    if rp.is_dir() and (dr == rp.resolve() or rp.resolve() in dr.parents):
        raise ValueError("can't move a folder into itself")
    dest = dr / rp.name
    if dest.exists():
        raise FileExistsError(f"'{rp.name}' already exists in the destination")
    shutil.move(str(rp), str(dest))
    return {"ok": True, "path": str(dest.resolve()), "name": rp.name}


def copy(path: str, dest_dir: str) -> dict:
    """Copy a file/folder into another folder (or duplicate within the same one)."""
    rp = _check_browse(path)
    dr = _dest_dir(dest_dir)
    if rp.is_dir() and (dr == rp.resolve() or rp.resolve() in dr.parents):
        raise ValueError("can't copy a folder into itself")
    dest = _free_path(dr, rp.name)
    if rp.is_dir():
        shutil.copytree(rp, dest)
    else:
        shutil.copy2(rp, dest)
    return {"ok": True, "path": str(dest.resolve()), "name": dest.name}


# Files named in chat ("run inspect.bat") frequently live in a SUBFOLDER of the project, not its
# root — so an exact-path check misses them. _locate falls back to a bounded basename search under
# the owning browse root (skipping junk dirs, preferring the shallowest match).
_SKIP_DIRS = {".venv", "venv", "env", "node_modules", ".git", "__pycache__", "dist", "build",
              ".next", ".cache", ".idea", ".vscode", "site-packages", ".mypy_cache", ".pytest_cache"}
# For VIEWING (reveal/open) we must NOT skip dist/build/.next — rendered image OUTPUTS live there
# (e.g. dist/etsy/showcase-*.png). Only the truly-never-user-content dirs are skipped.
_VIEW_SKIP_DIRS = {".venv", "venv", "env", "node_modules", ".git", "__pycache__",
                   ".cache", ".idea", ".vscode", "site-packages", ".mypy_cache", ".pytest_cache"}


def _find_by_name(root: Path, name: str, cap: int = 60, maxdepth: int = 7,
                  skip: "set[str] | None" = None) -> list[Path]:
    skipset = skip if skip is not None else _SKIP_DIRS
    root = root.resolve()
    base = len(root.parts)
    target = name.lower()
    hits: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if len(Path(dirpath).parts) - base >= maxdepth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d.lower() not in skipset and not d.startswith(".")]
        for fn in filenames:
            if fn.lower() == target:
                hits.append(Path(dirpath) / fn)
                if len(hits) >= cap:
                    return hits
    return hits


def _try_relative(p: Path, roots: "list[Path]") -> "Optional[Path]":
    """Join a RELATIVE path onto each open root and return the first one that exists.

    Chat links are relative far more often than absolute — `budget-studio/BudgetStudio.html`, or
    `catalog/.../scripts/build.cjs` printed inside a command that had already `cd`-ed somewhere.
    Resolving those against the backend's own working directory is meaningless, which is why
    clicking them did nothing. Trying them under every open root needs no per-project setup, so it
    works in an existing workspace and a brand-new one alike."""
    if p.is_absolute():
        return None
    parts = [x for x in p.parts if x not in (".", "")]
    if not parts:
        return None
    for r in roots:
        try:
            cand = r.joinpath(*parts)
            if cand.exists():
                return cand.resolve()
        except OSError:
            continue
    return None


def _tail_score(hit: Path, wanted: Path) -> int:
    """How many trailing segments of the requested path this hit matches. A link that says
    `scripts/build.cjs` must beat an unrelated `build.cjs` sitting somewhere else in the tree."""
    a = [x.lower() for x in hit.parts]
    b = [x.lower() for x in wanted.parts]
    n = 0
    while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n


def _locate(path: str) -> Path:
    """Resolve a UI-supplied path to a real file: exact, then relative to an open root, then a
    basename search for files living in a subfolder. Stays inside browse roots (security preserved)."""
    p = Path(path)
    roots = [r for r in _browse_roots() if r.exists()]
    rel = _try_relative(p, roots)
    if rel is not None:
        return rel
    if not _within(p, _browse_roots()) and not _within(p, _browse_roots(fresh=True)):
        raise PermissionError("path is outside any open project/folder")
    rp = p.resolve()
    if rp.exists():
        return rp
    name = p.name
    if not name or "." not in name:
        raise FileNotFoundError(str(rp))
    owner = next((r for r in roots if r == rp.parent or r in rp.parents), None)
    for r in ([owner] if owner else roots):
        hits = _find_by_name(r, name)
        if hits:
            # closest match to what was actually written, then shallowest
            hits.sort(key=lambda h: (-_tail_score(h, p), len(h.parts)))
            return hits[0].resolve()
    raise FileNotFoundError(f"couldn't find {name} in {owner.name if owner else 'the open folders'}")


def _extra_view_roots() -> "list[Path]":
    """Extra places a chat-mentioned file legitimately lives, beyond the open projects: the
    Studio's own data dir (job outputs, uploads) and the local ComfyUI output tree. Widening
    the SEARCH is safe because this resolver is view-only — `run` still uses _locate()."""
    cands: list[Path] = []
    try:
        from .config import DATA_DIR, settings
        cands.append(Path(DATA_DIR))
        for key in ("comfy_output_dir", "comfy_dir", "comfy_path"):
            v = str(settings.get(key, "") or "").strip()
            if v:
                cands.append(Path(v))
                cands.append(Path(v) / "output")
    except Exception:
        pass
    cands.append(Path.home() / "ComfyUI" / "output")
    out: list[Path] = []
    seen: set[str] = set()
    for c in cands:
        try:
            if c.is_dir():
                r = c.resolve()
                if str(r).lower() not in seen:
                    seen.add(str(r).lower())
                    out.append(r)
        except Exception:
            continue
    return out


def _view_search_roots(prefer: str = "") -> "list[Path]":
    """Where to look for a chat-mentioned file, most likely first.

    ``prefer`` is the workspace root of the chat that named the file. It matters because a chat
    link is often just a bare name. Without it, ``final.png`` resolved to whichever OPEN PROJECT
    came first and happened to contain that name — so an inline thumbnail could show a same-named
    image from an unrelated project while its label, and the click-through, were both right.
    The click never had this bug: it resolves against its own project first, and this makes the
    thumbnail agree with it.

    Only a real open root may jump the queue, so this cannot be used to search elsewhere."""
    out: "list[Path]" = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        key = str(rp).lower()
        if key not in seen and rp.exists():
            seen.add(key)
            out.append(rp)

    if prefer:
        pp = Path(prefer)
        if _within(pp, _browse_roots()):
            add(pp)
    for r in (*_browse_roots(), *_extra_view_roots()):
        add(r)
    return out


def _best_by_name(root: Path, p: Path) -> "Optional[Path]":
    """The best match for this file NAME inside one root: closest to the path as written, then
    shallowest, then newest."""
    name = p.name
    if not name or "." not in name:
        return None
    hits = _find_by_name(root, name, skip=_VIEW_SKIP_DIRS)     # searches dist/build too
    if not hits:
        return None
    hits.sort(key=lambda h: (-_tail_score(h, p), len(h.parts), -h.stat().st_mtime))
    return hits[0].resolve()


def _locate_for_view(path: str, prefer: str = "") -> Path:
    """Resolver for REVEAL / OPEN (viewing) — NOT for run. Viewing a file the assistant put in the
    chat is safe even when it lives OUTSIDE any project (renders, output/ dirs, temp shots, sibling
    folders), so an existing absolute path is allowed as-is. A relative or slightly-off path still
    resolves by basename search WITHIN the browse roots (we never wander the whole disk).

    ``prefer`` names the workspace the link came from, so an ambiguous name resolves inside its
    own project instead of the first project that happens to hold that name."""
    p = Path(path)
    if p.is_absolute():
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        if rp.exists():
            return rp                   # any existing absolute path is fine to reveal/open
    roots = _view_search_roots(prefer)
    home = roots[0] if (prefer and roots and _within(Path(prefer), [roots[0]])) else None
    rest = roots[1:] if home is not None else roots
    # The OWNING project gets BOTH attempts — join, then search — before any other root is
    # considered. Interleaving them (all roots joined, then all roots searched) let a top-level
    # `final.png` in an unrelated project beat the nested one in the project that named it.
    if home is not None:
        rel = _try_relative(p, [home])
        if rel is not None:
            return rel
        hit = _best_by_name(home, p)
        if hit is not None:
            return hit
    # relative first: joining onto the open roots is far more likely to be right than resolving
    # against the backend's own working directory, which is never what a chat link meant
    rel = _try_relative(p, rest)
    if rel is not None:
        return rel
    try:
        rp = p.resolve()
    except Exception:
        rp = p
    if rp.exists():
        return rp
    # Then the Studio's own output areas — a file named by bare filename very often lives outside
    # every project (renders and meshes under data/jobs, ComfyUI's output tree), which is why
    # searching only the projects meant "it opens only when the full path is given".
    #
    # When the caller SAID which project the link came from, the search stops there. Wandering on
    # into other projects is what made a chat show a confidently wrong image: STUDIO has no
    # favicon.ico, so the thumbnail happily served WOLT's. Showing nothing is correct — the UI
    # hides an image it cannot load, and "not found" is the truth.
    tail = rest if home is None else [r for r in _extra_view_roots() if r.exists()]
    for r in tail:
        hit = _best_by_name(r, p)
        if hit is not None:
            return hit
    raise FileNotFoundError(f"couldn't find {p.name or path}"
                            + (f" in {Path(prefer).name}" if home is not None else ""))


def open_file(path: str, root: str = "") -> dict:
    """Open a chat-referenced file in the OS default app (image viewer, PDF reader, …). Safe for any
    existing file — the user explicitly clicked it. Images/renders often live outside the project.

    ``root`` is the workspace the link came from, so a name several projects share opens the one
    from THIS chat — the same scoping the inline thumbnail already uses."""
    rp = _locate_for_view(path, root)
    if rp.is_dir():
        return reveal(str(rp))
    import subprocess
    import sys as _sys
    try:
        if os.name == "nt":
            os.startfile(str(rp))  # type: ignore[attr-defined]
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", str(rp)])
        else:
            subprocess.Popen(["xdg-open", str(rp)])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"could not open {rp.name}: {e}")
    return {"ok": True, "path": str(rp)}


def reveal(path: str, root: str = "") -> dict:
    """Open the path in the OS file explorer (a folder opens; a file is revealed/selected).
    ``root`` scopes an ambiguous name to the project the link came from."""
    rp = _locate_for_view(path, root)
    import subprocess
    import sys as _sys
    try:
        if os.name == "nt":
            if rp.is_dir():
                os.startfile(str(rp))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", "/select,", str(rp)])
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", str(rp)] if rp.is_dir() else ["open", "-R", str(rp)])
        else:
            subprocess.Popen(["xdg-open", str(rp if rp.is_dir() else rp.parent)])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"could not open file explorer: {e}")
    return {"ok": True}


# file types "Run" knows how to launch (the UI only offers Run for these)
RUNNABLE_EXTS = {".bat", ".cmd", ".ps1", ".py", ".pyw", ".exe", ".msi", ".com", ".lnk", ".html", ".htm", ".sh"}


def _py_exe() -> str:
    """Prefer the user's own python (their script's deps live there), fall back to the venv."""
    import shutil
    import sys as _sys
    return shutil.which("python") or shutil.which("py") or _sys.executable


def _open_url(url: str) -> None:
    import subprocess
    import sys as _sys
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]  # default browser (a real window, not automation)
    elif _sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])


def _find_chrome() -> str:
    """Path to chrome.exe, or '' to fall back to the default browser.

    The ``chrome_path`` setting wins and may point at either the exe or the folder holding it —
    people copy the folder out of the address bar, so accept both. Otherwise probe the usual
    installs, including a non-system drive (Chrome is often not on C:)."""
    cand: list[str] = []
    raw = str(settings.get("chrome_path", "") or "").strip().strip('"')
    if raw:
        cand += [raw, str(Path(raw) / "chrome.exe")]
    if os.name == "nt":
        # Windows keeps every installed browser's real path in the registry ("App Paths"), whatever
        # drive or folder it went to. Ask there first: a Chrome on E: is found on the first try.
        try:
            import winreg
            for exe in ("chrome.exe", "msedge.exe"):
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe) as k:
                            v = str(winreg.QueryValueEx(k, None)[0] or "").strip().strip('"')
                            if v:
                                cand.append(v)
                    except OSError:
                        continue
        except Exception:
            pass
        for drive in ("C:", "D:", "E:", "F:"):
            cand.append(rf"{drive}\Program Files\Google\Chrome\Application\chrome.exe")
            cand.append(rf"{drive}\Program Files (x86)\Google\Chrome\Application\chrome.exe")
        cand.append(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"))
    else:
        cand += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 shutil.which("google-chrome") or "", shutil.which("chromium") or ""]
    # A copy the Studio fetched itself, or the one the web tools' Playwright pulled: the fresh-PC path.
    try:
        from . import browser_install
        cand += browser_install.local_candidates()
    except Exception:
        pass
    if os.name == "nt":
        # Last: Edge is Chromium and is on every Windows 10/11 PC, so the forge, the review and the
        # live link work on day one with nothing to download.
        for drive in ("C:", "D:"):
            cand.append(rf"{drive}\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
            cand.append(rf"{drive}\Program Files\Microsoft\Edge\Application\msedge.exe")
    for c in cand:
        try:
            if c and Path(c).is_file():
                return c
        except OSError:
            continue
    return ""


# folders that never hold the thing you want to play, and page names that usually do
_ENTRY_SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist-electron", "coverage",
               ".next", ".turbo", ".cache", "graphify-out", "test", "tests", "docs", "doc",
               "example", "examples", "backup", "backups", "old", "reference", "references"}
_ENTRY_HINTS = (("game", 18), ("dist", 14), ("public", 12), ("build", 10), ("web", 8),
                ("www", 8), ("client", 6), ("app", 6), ("src", 3))


_ENTRY_STALE = {"legacy", "template", "templates", "demo", "sample", "samples", "graybox",
                "prototype", "proto", "archive", "old", "wip", "sandbox", "scratch", "attempt",
                "backup", "deprecated", "unused"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _entry_score(rel: Path, project: str = "") -> int:
    parts = [p.lower() for p in rel.parts]
    name, folders = parts[-1], parts[:-1]
    score = 100 - (len(parts) - 1) * 12          # shallower wins
    if name == "index.html":
        score += 40
    elif name in ("game.html", "play.html", "main.html", "demo.html"):
        score += 25
    for hint, bonus in _ENTRY_HINTS:
        if hint in folders:
            score += bonus
    proj = _norm(project)
    for f in folders:
        # "_something" is the universal "parked / not the live one" convention, and
        # legacy/template/demo folders are exactly what people DON'T want to open
        if f.startswith("_"):
            score -= 30
        if any(w in f for w in _ENTRY_STALE):
            score -= 25
        # a folder named after the project is almost always the real game (games/hordefall/)
        if proj and len(proj) > 3 and (_norm(f) == proj or proj in _norm(f)):
            score += 20
    return score


def find_entry(folder) -> Optional[Path]:
    """The page that IS the game/app in this project — what the localhost button should open.

    Serving a project folder used to land on a bare directory listing, so a game living in
    ``game/index.html`` (or ``dist/``) needed manual clicking to reach. Candidates are ranked by
    depth plus folder hints, skipping vendor/test/doc noise."""
    root = Path(folder)
    cands: list[tuple[int, float, Path]] = []
    stack = [(root, 0)]
    while stack and len(cands) < 500:
        d, depth = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir():
                    if depth < 3 and not e.name.startswith(".") and e.name.lower() not in _ENTRY_SKIP:
                        stack.append((e, depth + 1))
                elif e.suffix.lower() in (".html", ".htm"):
                    # ties are common (several games in one repo) — break them by mtime, i.e.
                    # the one actually being worked on
                    cands.append((_entry_score(e.relative_to(root), root.name), e.stat().st_mtime, e))
            except OSError:
                continue
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return cands[0][2]


# Folders that hold BUILT output rather than source. A page in one of these is runnable as a
# static file (that is why it outranks the source page), but only while the build is current.
_BUILD_DIRS = {"dist", "build", "out", "_site"}
_SRC_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".css", ".scss",
            ".html", ".htm", ".glsl", ".frag", ".vert", ".json", ".wgsl"}


def _entry_project(entry: Path, root: Path) -> Path:
    """The project folder a page belongs to — its parent, stepped up out of any build dir."""
    d = entry.parent
    while d != root and d != d.parent and d.name.lower() in _BUILD_DIRS:
        d = d.parent
    return d


def _newest_source(project: Path, skip: Optional[Path] = None, cap: int = 4000) -> float:
    """Newest source-file mtime under `project`, ignoring its build output. Bounded so a huge
    project cannot make the localhost button slow."""
    newest, seen = 0.0, 0
    stack = [project]
    while stack and seen < cap:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir():
                    if e == skip or e.name.startswith(".") or e.name.lower() in _ENTRY_SKIP:
                        continue
                    stack.append(e)
                elif e.suffix.lower() in _SRC_EXT:
                    seen += 1
                    m = e.stat().st_mtime
                    if m > newest:
                        newest = m
            except OSError:
                continue
    return newest


def _build_age(entry: Path, root: Path) -> tuple[bool, float]:
    """(is this a BUILT page whose source has changed since, newest source mtime).

    This is the difference between "open my game" and "open a snapshot of my game from three days
    ago". A dist/ page scores higher than the source page next to it — correctly, it is the one a
    static server can actually run — but when the build is older than the code it is the wrong
    answer, and nothing said so."""
    build_dir = None
    for parent in entry.parents:
        if parent == root.parent:
            break
        if parent.name.lower() in _BUILD_DIRS:
            build_dir = parent
        if parent == root:
            break
    if build_dir is None:
        return False, 0.0
    project = build_dir.parent
    newest = _newest_source(project, skip=build_dir)
    try:
        return newest > entry.stat().st_mtime, newest
    except OSError:
        return False, newest


def _live_entries(root: Path) -> list[dict]:
    """The servers actually running for this project, as menu entries.

    Shaped exactly like a file entry so the menu renders them with no special case: `rel` is what
    the row shows, and `dev.url` is what opening it uses. A server's page TITLE is the label,
    because three servers rooted in one folder are told apart by what they serve, not by their
    port — "Powder Peaks" against "Directory listing for /"."""
    try:
        servers = dev_server.running_servers(root)
    except Exception:
        return []
    now = time.time()
    out: list[dict] = []
    for srv in servers:
        host = srv["url"].replace("http://", "").rstrip("/")
        out.append({
            "path": srv["url"],                  # unique key, and what open_url receives
            "rel": srv.get("title") or host,
            "project": str(root),
            "project_name": root.name,
            "score": 100000 - srv["port"],
            "mtime": now,
            "built": False,
            "stale": False,
            "source_mtime": 0.0,
            "live": True,
            "dev": {"url": srv["url"], "script": "", "proc": srv.get("proc", ""),
                    "title": srv.get("title", "")},
        })
    return out


def find_entries(folder, limit: int = 10) -> list[dict]:
    """Every page in this project that could BE the app, best first.

    One project often holds several (rot-rush, rot-haul, a viewer, an old prototype), so the
    localhost button offers the list and opens the best by default. Each entry carries what the
    user needs to judge it: where it lives, how old it is, whether it is a built bundle, and
    whether that bundle has gone stale against its own source."""
    root = Path(folder)
    cands: list[tuple[int, float, Path]] = []
    stack = [(root, 0)]
    while stack and len(cands) < 500:
        d, depth = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir():
                    if depth < 3 and not e.name.startswith(".") and e.name.lower() not in _ENTRY_SKIP:
                        stack.append((e, depth + 1))
                elif e.suffix.lower() in (".html", ".htm"):
                    cands.append((_entry_score(e.relative_to(root), root.name), e.stat().st_mtime, e))
            except OSError:
                continue
    # NB: `live` is already used further down for a different thing, hence the name.
    live_rows = _live_entries(root)
    if not cands:
        return live_rows
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)

    out: list[dict] = []
    seen_projects: set[str] = set()          # a dev server belongs to the PROJECT, so it is
    for score, mtime, p in cands[: max(1, limit)]:   # reported once — on that project's best page
        project = _entry_project(p, root)
        built = any(par.name.lower() in _BUILD_DIRS for par in p.parents if par != root.parent)
        stale, newest_src = (_build_age(p, root) if built else (False, 0.0))
        pkey = str(project).lower()
        dev = dev_server.describe(project) if pkey not in seen_projects else {}
        # A running server is its OWN row now, at the top. Leaving the url on the file entry too
        # made build/index.html claim to be the live dev server as well — the same address
        # offered twice, once truthfully and once not. Keep only what this row can still do:
        # offer to START one.
        dev = {"script": dev.get("script", "")} if dev.get("script") else {}
        seen_projects.add(pkey)
        out.append({
            "path": str(p),
            "rel": str(p.relative_to(root)).replace("\\", "/"),
            "project": str(project),
            "project_name": project.name if project != root else root.name,
            "score": score,
            "mtime": mtime,
            "built": built,
            "stale": stale,
            "source_mtime": newest_src,
            "dev": dev,                          # a live dev server for this project, if any
        })
    # A stale build must not be the default when the same project has something better: a running
    # dev server IS the latest, so it wins outright. Ordering only ever changes WITHIN a project,
    # so which project the button picks is unchanged.
    top = out[0]
    if top["stale"] or top["dev"].get("url"):
        same = [e for e in out if e["project"] == top["project"]]
        live = next((e for e in same if e["dev"].get("url")), None)
        if live is not None and live is not top:
            out.remove(live)
            out.insert(0, live)
        elif top["stale"]:
            fresh = next((e for e in same if not e["built"] and e["mtime"] > top["mtime"]), None)
            if fresh is not None and dev_server.script_for(Path(top["project"])) is None:
                # no dev server to run it, so the newer hand-written page is the better answer
                out.remove(fresh)
                out.insert(0, fresh)
    # A RUNNING server is the live truth and outranks every file on disk. It used to be invisible
    # here: the menu only ever listed .html files to serve through the Studio's own port, so a
    # game already being served on 127.0.0.1:8080 was nowhere in the list and the user was
    # offered a two-day-old build instead.
    return live_rows + out


def open_in_browser(path: str) -> dict:
    """Serve a file (or a whole folder) over http and open it in a real browser window.

    This is the "see it for real" button. It always goes through the local static server, never
    ``file://`` — an ES-module page is blocked by CORS from file://, which is the failure people
    hit and cannot explain. Chrome is preferred because that is what the user tests in; the OS
    default is the fallback. The server is started on demand and reused per folder."""
    rp = _locate(path)
    from . import preview_server
    entry = None
    info: dict = {}
    if rp.is_dir():
        # open the GAME, not a directory listing: find the project's real entry page
        picks = find_entries(rp)
        info = picks[0] if picks else {}
        live = (info.get("dev") or {}).get("url") if info else ""
        if live:
            url = live                       # a running dev server IS the latest — prefer it
        elif info:
            entry = Path(info["path"])
            url = preview_server.serve_file(entry)
        else:
            port = preview_server.serve_dir(str(rp))
            url = f"http://127.0.0.1:{port}/"
    else:
        url = preview_server.serve_file(rp)
    exe = _find_chrome()
    try:
        if exe:
            import subprocess
            subprocess.Popen([exe, url], close_fds=True)
        else:
            _open_url(url)
    except Exception as e:  # noqa: BLE001 — report the url even when the launch fails
        return {"ok": False, "url": url, "browser": exe or "default", "error": str(e)}
    rel = ""
    if entry is not None:
        try:
            rel = str(entry.relative_to(rp)).replace("\\", "/")
        except ValueError:
            rel = entry.name
    return {"ok": True, "url": url, "browser": exe or "default", "entry": rel,
            # so the UI can say "this is a build from 3 days ago" instead of silently
            # showing an old version of the game
            "stale": bool(info.get("stale")), "live": bool((info.get("dev") or {}).get("url")),
            "dev_script": (info.get("dev") or {}).get("script", "")}


def open_browser_url(url: str) -> dict:
    """Open a LOCAL url in the browser — the running-dev-server case, where there is no file to
    serve. Loopback only, so this can never be used to launch an arbitrary site."""
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in (
            "127.0.0.1", "localhost", "::1", "0.0.0.0"):
        raise ValueError("only a local (127.0.0.1 / localhost) URL can be opened here")
    exe = _find_chrome()
    try:
        if exe:
            import subprocess
            subprocess.Popen([exe, url], close_fds=True)
        else:
            _open_url(url)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "url": url, "browser": exe or "default", "error": str(e)}
    return {"ok": True, "url": url, "browser": exe or "default"}


def run(path: str) -> dict:
    """Launch a file the way a double-click would, but type-smart:
      • .html/.htm → SERVE the folder and open http://127.0.0.1 (so ES-module imports work —
        file:// would break them; self-contained pages work through it too)
      • .ps1 → PowerShell in a new console (kept open so output/errors are visible)
      • .py   → the user's python in a new console
      • .bat/.cmd/.exe/.msi/.com/.lnk (+ anything else) → OS double-click
      • a directory → reveal it in the explorer
    Gated to browse roots; a bare filename from chat is located even if it's in a subfolder."""
    rp = _locate(path)
    if rp.is_dir():
        return reveal(str(rp))
    ext = rp.suffix.lower()
    folder = str(rp.parent)
    import subprocess
    import sys as _sys
    try:
        if ext in (".html", ".htm"):
            from . import preview_server
            url = preview_server.serve_file(rp)
            _open_url(url)
            return {"ok": True, "action": "served", "url": url, "ext": ext}
        if os.name == "nt":
            new_console = 0x00000010  # subprocess.CREATE_NEW_CONSOLE
            if ext == ".ps1":
                subprocess.Popen(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", str(rp)],
                    cwd=folder, creationflags=new_console)
            elif ext in (".py", ".pyw"):
                subprocess.Popen([_py_exe(), str(rp)], cwd=folder, creationflags=new_console)
            else:
                os.startfile(str(rp))  # type: ignore[attr-defined]
        elif _sys.platform == "darwin":
            if ext == ".py":
                subprocess.Popen([_py_exe(), str(rp)], cwd=folder)
            elif ext == ".sh":
                subprocess.Popen(["bash", str(rp)], cwd=folder)
            else:
                subprocess.Popen(["open", str(rp)])
        else:
            if ext == ".py":
                subprocess.Popen([_py_exe(), str(rp)], cwd=folder)
            elif ext == ".sh":
                subprocess.Popen(["bash", str(rp)], cwd=folder)
            else:
                subprocess.Popen(["xdg-open", str(rp)])
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"could not run {rp.name}: {e}")
    return {"ok": True, "action": "ran", "ext": ext}


# Folders that are never somebody's project, and opening one as a workspace only ever goes wrong.
_NEVER_OPEN = {"windows", "winnt", "program files", "program files (x86)", "programdata",
               "$recycle.bin", "system volume information", "recovery", "perflogs", "$windows.~ws"}


def openable(p: Path) -> bool:
    """May this folder be opened as a workspace?

    Anything the person names, on any drive. A project moved to `E:\\claude\\…` is still their
    project, and the old rule — home, Downloads, Desktop, Documents, or beside an open project —
    answered that with "location not allowed" and nothing else to try.

    Two things are still refused, because neither is ever a project: a drive root (opening `C:\\`
    means walking the whole machine) and Windows' own folders.
    """
    parts = [x.strip("\\/").lower() for x in p.parts]
    if len(parts) <= 1:
        return False
    return not any(seg in _NEVER_OPEN for seg in parts)


def add_root(path: str) -> dict:
    """Open an existing folder in the workspace (persist it as a root so it's browsable/editable)."""
    p = Path(path).resolve()
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError(str(p))
    # The old allowances stay, so nothing that opened before stops opening.
    if not (openable(p) or _within(p, _project_roots()) or _within(p, _create_bases())):
        raise PermissionError("that folder cannot be opened as a workspace — it is a drive root or "
                              "one of Windows' own folders. Open the project folder itself.")
    sp = str(p)
    cur = list(settings.get("workspace_roots") or [])
    if sp not in cur:
        cur.append(sp)
        invalidate_roots(); settings.update({"workspace_roots": cur})
    return {"id": _claude_id(sp), "name": p.name, "path": sp, "opened": True, "ok": True}


def remove_root(path: str) -> dict:
    sp = str(Path(path).resolve())
    cur = [x for x in (settings.get("workspace_roots") or []) if str(Path(x).resolve()) != sp]
    invalidate_roots(); settings.update({"workspace_roots": cur})
    return {"ok": True}


def mesh_as_glb(path: str) -> Path:
    """Convert a non-glTF mesh (.obj/.stl/.ply/.off) to GLB so the built-in 3D view can show
    it. Cached under data/cache/mesh by path+mtime+size, so re-opening a tab is instant and a
    re-exported file reconverts automatically."""
    rp = _check_browse(path)
    if rp.suffix.lower() not in _MESH_EXT:
        raise ValueError(f"not a convertible mesh: {rp.name}")
    st = rp.stat()
    key = hashlib.sha1(f"{rp}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8", "ignore")).hexdigest()[:16]
    out = DATA_DIR / "cache" / "mesh" / f"{rp.stem}-{key}.glb"
    if out.exists():
        return out
    import trimesh
    scene = trimesh.load(str(rp), force="scene")   # keeps sub-meshes + any .mtl materials
    data = scene.export(file_type="glb")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.write_bytes(data)
    fsutil.replace(tmp, out)
    return out


def rename_root(path: str, new_name: str) -> dict:
    """Rename a project folder on disk AND keep Claude's context: the CLI keys transcripts
    to ~/.claude/projects/<slug-of-cwd>, so a bare folder rename would orphan the whole
    conversation history. Order matters: kill the project's live session (its cwd is about
    to vanish), pause the fs watcher (it holds a handle that blocks os.rename on Windows),
    rename the folder, rename the matching transcript dir(s) to the new slug, and update
    the opened-roots setting. The next send resumes the SAME conversation via --resume."""
    old = Path(path).resolve()
    if not old.exists() or not old.is_dir():
        raise FileNotFoundError(str(old))
    safe = "".join(c for c in (new_name or "") if c.isalnum() or c in " ._-()&").strip()
    if not safe:
        raise ValueError("give the project a real name")
    if safe == old.name:
        return {"ok": True, "id": _claude_id(str(old)), "name": old.name, "path": str(old),
                "old_path": str(old), "migrated_sessions": 0}
    new = old.parent / safe
    if str(new).lower() != str(old).lower() and new.exists():
        raise FileExistsError(f"'{safe}' already exists next to it")
    old_id = _claude_id(str(old))
    # A rename kills the live session (its cwd is about to move) and every background agent in
    # it. Refuse instead: the folder will still be renameable when they are done.
    try:
        from . import cc_session
        blocked = cc_session._agents_block(old_id, "Renaming the folder stops this project's "
                                                   "session, which")
        if blocked:
            raise PermissionError(blocked)
    except PermissionError:
        raise
    except Exception:
        pass
    try:                                   # 1) live claude stream on the old cwd
        live = cc_session._live.pop(old_id, None)
        if live:
            cc_session._kill_live(live)
    except Exception:
        pass
    try:                                   # 2) watcher handle would block the rename
        from . import fswatch
        fswatch.pause(str(old))
    except Exception:
        pass
    # 2b) processes parked INSIDE the folder hold cwd handles → os.rename = WinError 5.
    # Leftover claude-spawned ones (dev servers, shells) carry our env marker — kill those;
    # anything foreign (user's own terminal/editor) is only REPORTED, never killed.
    low = str(old).lower()
    foreign: list = []
    try:
        import psutil
        killed = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if not (p.cwd() or "").lower().startswith(low):
                    continue
                if p.environ().get("ASSET_STUDIO_CC") == "1":
                    p.kill()
                    killed.append(p)
                else:
                    foreign.append(p)
            except Exception:
                continue
        if killed:
            psutil.wait_procs(killed, timeout=4)
    except Exception:
        pass
    # 3) the folder itself — with retries: handles take a moment to release after kills
    last_err: Optional[Exception] = None
    for _ in range(6):
        try:
            os.rename(old, new)
            last_err = None
            break
        except PermissionError as e:
            last_err = e
            time.sleep(0.5)
    if last_err is not None:
        names = ", ".join(f"{b.name()} (pid {b.pid})" for b in foreign[:5]) if foreign else ""
        raise PermissionError(
            "the folder is in use" + (f" by {names}" if names else "")
            + " — close terminals/servers/editors running inside it and rename again")
    migrated = 0                           # 4) Claude Code transcript dir(s) → new slug
    new_id = _claude_id(str(new))
    proj = claude_home() / "projects"
    if proj.is_dir():
        for d in proj.iterdir():
            try:
                if d.is_dir() and d.name.lower() == old_id.lower():
                    target = proj / new_id
                    if not target.exists():
                        os.rename(d, target)
                        migrated += 1
            except OSError:
                continue
    cur = [str(new) if str(Path(x).resolve()).lower() == str(old).lower() else x
           for x in (settings.get("workspace_roots") or [])]
    # ALWAYS register the new path as an opened root. A detected-only project (never
    # explicitly opened) is mapped through its transcripts' cwd fields — which still
    # say the OLD path after a rename — so without this it vanishes from the list
    # until the first new message rewrites the mapping.
    if not any(str(Path(x).resolve()).lower() == str(new).lower() for x in cur):
        cur.append(str(new))
    invalidate_roots(); settings.update({"workspace_roots": cur})
    return {"ok": True, "id": new_id, "name": new.name, "path": str(new),
            "old_path": str(old), "migrated_sessions": migrated}


def new_project(parent: str, name: str) -> dict:
    base = _check_create(parent)
    safe = "".join(c for c in name if c.isalnum() or c in " ._-").strip() or "new-project"
    folder = base / safe
    folder.mkdir(parents=True, exist_ok=True)
    return add_root(str(folder))   # open it in the workspace right away


def search(root: str, query: str, limit: int = 40) -> dict:
    """Fuzzy-ish filename search under a project root for @-mention pickers."""
    rp = _check_browse(root)
    if not rp.is_dir():
        rp = rp.parent
    q = (query or "").lower().strip()
    results: list[dict] = []
    scanned = 0
    for cur, dirs, files in os.walk(rp):
        # dot-dirs are skipped (.git etc.) EXCEPT .studio-uploads — that's the Studio's own
        # chat-uploads/results folder, exactly where file links in the conversation point
        # (pasted screenshots, generated before/afters), so search MUST see inside it.
        dirs[:] = [d for d in dirs
                   if d == ".studio-uploads" or (d not in _SKIP_DIRS and not d.startswith("."))]
        for f in files:
            scanned += 1
            if scanned > 20000:
                break
            if q and q not in f.lower() and q not in os.path.join(cur, f).lower():
                continue
            full = Path(cur) / f
            try:
                rel = str(full.relative_to(rp))
            except ValueError:
                rel = f
            results.append({"name": f, "path": str(full), "rel": rel.replace("\\", "/")})
            if len(results) >= limit:
                break
        if len(results) >= limit or scanned > 20000:
            break
    # shorter relative paths first (closer to root = usually more relevant)
    results.sort(key=lambda r: (len(r["rel"]), r["rel"].lower()))
    return {"root": str(rp), "entries": results[:limit]}


def save_upload(project_path: str, data: bytes, ext: str = ".png") -> dict:
    """Save a pasted/uploaded image under the project's .studio-uploads/ folder."""
    base = _check_create(project_path)
    up = base / ".studio-uploads"
    up.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    dest = up / f"paste-{ts}{ext if ext.startswith('.') else '.' + ext}"
    dest.write_bytes(data)
    return {"path": str(dest.resolve()), "ok": True}


def import_external(src: str, dest_dir: str) -> dict:
    """Copy a file/folder from anywhere on disk INTO an open project folder. Used by
    drag-and-drop from Windows Explorer (Electron exposes the dragged item's real path)."""
    sp = Path(src)
    try:
        spr = sp.resolve()
    except Exception:
        raise FileNotFoundError(str(src))
    if not spr.exists():
        raise FileNotFoundError(str(spr))
    dr = _dest_dir(dest_dir)
    if spr.is_dir() and (dr == spr or spr in dr.parents):
        raise ValueError("can't import a folder into itself")
    dest = _free_path(dr, spr.name)
    if spr.is_dir():
        shutil.copytree(spr, dest)
    else:
        shutil.copy2(spr, dest)
    return {"ok": True, "path": str(dest.resolve()), "name": dest.name}


def upload_file(dest_dir: str, filename: str, data: bytes) -> dict:
    """Write uploaded bytes as a file inside an open project folder (drag-drop fallback
    for browser mode, where the dragged item's source path isn't available)."""
    dr = _dest_dir(dest_dir)
    safe = Path(filename or "file").name
    safe = "".join(c for c in safe if c not in '<>:"|?*').strip() or "file"
    dest = _free_path(dr, safe)
    dest.write_bytes(data)
    return {"ok": True, "path": str(dest.resolve()), "name": dest.name}

"""Pre-turn checkpoints for AI edits — Cline-style "see what changed & revert".

Before an agent turn (and on demand) we snapshot the project's text files, so you
can later review exactly what the agent changed and roll any of it back. Snapshots
are bounded (skip noise dirs, per-file & total caps), pruned to the last few per
project, and stored gzipped under ``DATA_DIR/checkpoints/<project_id>/``.

EVERY ENGINE, ONE STORE. A checkpoint frames the FOLDER's files, not one engine's conversation, so
Claude, Codex, DeepSeek and the alternate providers share one list — keyed by the bare folder id
whatever prefix the caller happens to carry. Getting there took two fixes: the folder lookup
compared ids as exact strings, so it never matched the Workspace's lowercased drive letter on ANY
engine, and the snapshot itself was only taken inside the Claude and Codex branches — the DeepSeek
adapter returns from `send()` before Claude's line, so it never took one at all. The snapshot now
sits above the engine dispatch in `cc_session.send`, and each row records which engine was about to
write.

Restore is intentionally non-destructive: it overwrites snapshotted files back to
their captured content but never deletes files the agent created.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .config import DATA_DIR
from . import engines, fsutil, mission, workspace

CKPT_DIR = (DATA_DIR / "checkpoints").resolve()
CKPT_DIR.mkdir(parents=True, exist_ok=True)

_SKIP = workspace._SKIP_DIRS
_MAX_FILE = 512 * 1024            # per-file cap
_MAX_TOTAL = 30 * 1024 * 1024    # total snapshot cap
_MAX_FILES = 4000
_KEEP = 12                       # checkpoints retained per project
_BIN_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip", ".gz",
    ".exe", ".dll", ".so", ".bin", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4",
    ".mov", ".wasm", ".db", ".sqlite", ".pyc", ".class", ".jar", ".7z", ".rar", ".tar",
}


def _bare_id(project_id: str) -> str:
    """The folder id without an engine prefix — "codex--d--x" and "d--x" are the same folder.

    Asked of the engine registry, which knows every engine's prefix and the alternate engines'.
    This used to strip Codex by name (its prefix is not in `mission.alt_homes()`), and relying on
    `alt_prefix` alone had left Codex unable to find its own project folder.
    """
    return engines.bare_folder(project_id)


def _norm_folder_id(project_id: str) -> str:
    """The folder id as the Workspace writes it: drive letter lowercased
    (`workspace._claude_id`), so one project cannot end up with two checkpoint directories on a
    case-sensitive filesystem. Both spellings appear — the id proper (`D:\\…`, from a resolved
    path) and Claude's slug for it (`D--…`, from the project index)."""
    bare = _bare_id(project_id)
    if len(bare) >= 2 and bare[1] == ":":                 # a real path: "D:\UserFiles\…"
        return bare[0].lower() + bare[1:]
    m = re.match(r"^([A-Za-z])--", bare)                  # Claude's slug: "D--UserFiles-…"
    return m.group(1).lower() + bare[1:] if m else bare


def _proj_root(project_id: str) -> Optional[Path]:
    """The folder a session is about, whichever engine is driving it.

    THIS IS WHY THE CHECKPOINTS PANEL WAS ALWAYS EMPTY. The id that arrives here is the
    WORKSPACE's, and the workspace lowcases the drive letter (`workspace._claude_id`); the index
    below keeps the casing Claude Code wrote (`D--UserFiles-…`). The lookup used to be an exact
    string compare, so on Windows the two never met, `create()` returned "no project folder for
    this session", and the failure was silent — a background thread whose only output was a return
    value nobody read. Measured 2026-10-05: `data/checkpoints/` had no project directory at all,
    for any engine, after weeks of turns. So resolve the way the filesystem already does: compare
    case-folded, and fall back to the Studio's own folder list, so a project that only Codex or
    DeepSeek has ever touched still snapshots.
    """
    want = _norm_folder_id(project_id).lower()

    def _ok(path) -> Optional[Path]:
        if not path:
            return None
        try:
            rp = Path(path).resolve()
            if rp.exists() and rp.is_dir():
                return rp
        except Exception:
            return None
        return None

    # The light index, not the overview: this only needs id -> folder, and the overview costs a
    # full scan of every project when its three-second cache has expired.
    for p in mission.project_index():
        if str(p.get("id") or "").lower() == want:
            rp = _ok(p.get("path"))
            if rp:
                return rp
    # Not in the Claude index — a folder that only Codex or DeepSeek has run in. The workspace's
    # own root list knows it, and it is the list the id came from in the first place.
    try:
        from . import workspace as _workspace
        for r in _workspace.roots():
            if str(r.get("id") or "").lower() == want:
                rp = _ok(r.get("path"))
                if rp:
                    return rp
    except Exception:
        pass
    # Last resort: ask the session layer, which can read the cwd out of a transcript head even for
    # a folder no index has seen yet.
    try:
        from . import cc_session
        cwd, _ = cc_session._resolve(_norm_folder_id(project_id))
        return _ok(cwd)
    except Exception:
        return None


# HOW A CHECKPOINT IS STORED, and why it changed (2026-10-07).
#
# Version 1 copied the WHOLE project — every text file, up to 30 MB — into one gzip per turn, and
# read every one of those files from disk to do it. Every message did that, including a steer sent
# into a running turn and a send that was then refused. Twelve kept per folder came to 151 MB here
# for three projects, and listing them decompressed every one in full just to read its label.
#
# Version 2 stores each file's content ONCE, under its hash, in `<folder>/blobs/`. A checkpoint is
# a small map of path -> hash, so a turn that changed three files adds three blobs. A file whose
# size and modification time match the last scan is not read again — its hash is reused. A
# checkpoint identical to the newest one is not written at all. Version 1 files still load.
_BLOB_DIR = "blobs"
_stat_cache: dict[str, dict[str, tuple[int, int, str]]] = {}   # root -> rel -> (mtime_ns, size, sha)
_lock = threading.RLock()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _scan(root: Path) -> tuple[dict[str, str], dict[str, str], bool]:
    """(path -> content hash, path -> text for the files actually read this time, partial).

    Same rules as before for what counts — noise directories, binary extensions, the per-file,
    total and count caps — but a file unchanged since the last scan (same size, same mtime) is not
    opened. A file is only ever stored if it decodes as UTF-8, so a cached hash is always text."""
    key = str(root)
    with _lock:
        old = dict(_stat_cache.get(key) or {})
    refs: dict[str, str] = {}
    fresh: dict[str, str] = {}
    seen: dict[str, tuple[int, int, str]] = {}
    total = 0
    partial = False
    for cur, dirs, fs in os.walk(root):
        # prune noise dirs and our own checkpoint store
        dirs[:] = [d for d in dirs if d not in _SKIP and not d.startswith(".")
                   and (Path(cur) / d).resolve() != CKPT_DIR]
        for f in fs:
            if Path(f).suffix.lower() in _BIN_EXT:
                continue
            full = Path(cur) / f
            try:
                st = full.stat()
            except OSError:
                continue
            sz = st.st_size
            if sz > _MAX_FILE:
                continue
            if len(refs) >= _MAX_FILES or total + sz > _MAX_TOTAL:
                partial = True
                break
            rel = str(full.relative_to(root)).replace("\\", "/")
            hit = old.get(rel)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == sz:
                sha = hit[2]
            else:
                try:
                    text = full.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                sha = _sha(text)
                fresh[rel] = text
            refs[rel] = sha
            seen[rel] = (st.st_mtime_ns, sz, sha)
            total += sz
        if partial:
            break
    with _lock:
        _stat_cache[key] = seen
    return refs, fresh, partial


def _snapshot_files(root: Path) -> tuple[dict, bool]:
    """The whole text of the folder, path -> text. Only the version-1 diff still needs this."""
    refs, fresh, partial = _scan(root)
    files: dict[str, str] = {}
    for rel in refs:
        text = fresh.get(rel)
        if text is None:
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        files[rel] = text
    return files, partial


def _blob_path(d: Path, sha: str) -> Path:
    return d / _BLOB_DIR / sha[:2] / (sha + ".gz")


def _write_blob(d: Path, sha: str, text: str) -> None:
    bp = _blob_path(d, sha)
    if bp.exists():
        return
    bp.parent.mkdir(parents=True, exist_ok=True)
    tmp = bp.with_name("%s.%d.%d.tmp" % (bp.name, os.getpid(), threading.get_ident()))
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.write(text)
    fsutil.replace(tmp, bp)


def _read_blob(d: Path, sha: str) -> Optional[str]:
    try:
        with gzip.open(_blob_path(d, sha), "rt", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, EOFError):
        return None


def _gc_blobs(d: Path) -> None:
    """Delete blobs no kept checkpoint points at. Runs only after a prune removed something."""
    keep: set[str] = set()
    for f in d.glob("*.json.gz"):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                keep.update((json.load(fh).get("refs") or {}).values())
        except (OSError, ValueError, EOFError):
            return                     # cannot be sure what is referenced: delete nothing
    for bp in (d / _BLOB_DIR).glob("*/*.gz"):
        if bp.name[:-3] not in keep:
            try:
                bp.unlink()
            except OSError:
                pass


def _dir(project_id: str) -> Path:
    """Where this folder's snapshots live.

    Keyed by the BARE folder id, so every spelling of the same project reaches one directory: the
    Workspace's lowercased id, Mission Control's id, and the "<engine>--" feed id the chat sends
    through. Keying on what the caller happened to have meant a Codex or DeepSeek rewind looked in
    `deepseek-harness--<slug>` for snapshots that were written under `<slug>` — a store that exists
    but is always empty reads exactly like a feature that does not work.
    """
    safe = "".join(c for c in _norm_folder_id(project_id) if c.isalnum() or c in "-_. ")
    return CKPT_DIR / (safe or "x")


def _prune(d: Path) -> None:
    removed = False
    for old in sorted(d.glob("*.json.gz"), key=lambda f: f.stat().st_mtime, reverse=True)[_KEEP:]:
        try:
            old.unlink()
            removed = True
        except OSError:
            pass
    if removed and (d / _BLOB_DIR).is_dir():
        _gc_blobs(d)


def _newest(d: Path) -> Optional[dict]:
    files = sorted(d.glob("*.json.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        with gzip.open(files[0], "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return None


def create(project_id: str, label: str = "", kind: str = "manual", agent: str = "") -> dict:
    root = _proj_root(project_id)
    if not root:
        return {"ok": False, "error": "no project folder for this session"}
    with _lock:                          # two engines in one folder may snapshot at once
        refs, fresh, partial = _scan(root)
        d = _dir(project_id)
        d.mkdir(parents=True, exist_ok=True)
        # NOTHING CHANGED SINCE THE NEWEST CHECKPOINT: that one already frames this turn. A refused
        # send, a retry and a second message before any edit used to each write a full copy.
        last = _newest(d)
        if last and last.get("refs") == refs and str(last.get("root")) == str(root):
            return {"ok": True, "id": last["id"], "file_count": len(refs), "partial": partial,
                    "unchanged": True}
        for rel, sha in list(refs.items()):
            if _blob_path(d, sha).exists():
                continue
            text = fresh.get(rel)
            if text is None:                 # hash from the stat cache, blob since pruned
                try:
                    text = (root / rel).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    refs.pop(rel, None)
                    continue
                sha = _sha(text)
                refs[rel] = sha
            try:
                _write_blob(d, sha, text)
            except OSError as e:
                return {"ok": False, "error": str(e)}
        ts = time.time()
        cid = str(int(ts * 1000))
        while (d / f"{cid}.json.gz").exists():
            cid = str(int(cid) + 1)
        payload = {"v": 2, "id": cid, "ts": ts, "label": (label or "").strip()[:200], "kind": kind,
                   # WHICH engine was about to write. Every engine snapshots through the same door,
                   # so the panel can say whose turn a checkpoint frames instead of assuming Claude.
                   "agent": (agent or "").strip()[:40],
                   "root": str(root), "partial": partial, "file_count": len(refs), "refs": refs}
        try:
            tmp = d / f"{cid}.json.gz.tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh)
            fsutil.replace(tmp, d / f"{cid}.json.gz")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        _prune(d)
    return {"ok": True, "id": cid, "file_count": len(refs), "partial": partial}


def _load(project_id: str, cid: str) -> Optional[dict]:
    f = _dir(project_id) / f"{os.path.basename(cid)}.json.gz"
    if not f.exists():
        return None
    try:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            ck = json.load(fh)
    except (OSError, json.JSONDecodeError, EOFError):
        return None
    if "refs" in ck and "files" not in ck:
        # Version 2: the text lives in blobs. Restore and the legacy diff want path -> text.
        d = _dir(project_id)
        files = {}
        for rel, sha in ck["refs"].items():
            text = _read_blob(d, sha)
            if text is not None:
                files[rel] = text
        ck["files"] = files
    return ck


def list_checkpoints(project_id: str) -> list[dict]:
    d = _dir(project_id)
    out = []
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json.gz"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                p = json.load(fh)
            out.append({"id": p["id"], "ts": p["ts"], "label": p.get("label", ""),
                        "kind": p.get("kind", ""), "agent": p.get("agent", ""),
                        "file_count": p.get("file_count", 0),
                        "partial": p.get("partial", False)})
        except Exception:
            continue
    return out


def diff(project_id: str, cid: str) -> dict:
    ck = _load(project_id, cid)
    if not ck:
        return {"ok": False, "error": "checkpoint not found"}
    root = Path(ck["root"])
    if "refs" in ck:
        return _diff_v2(project_id, cid, ck, root)
    cur, _ = _snapshot_files(root)
    old = ck["files"]
    changes = []
    for rel, oldtext in old.items():
        newtext = cur.get(rel)
        if newtext is None:
            changes.append({"path": rel, "status": "deleted", "added": 0,
                            "removed": oldtext.count("\n") + 1, "hunks": []})
        elif newtext != oldtext:
            a, r, hunks = mission._diff_stat(oldtext, newtext, cap=40)
            changes.append({"path": rel, "status": "modified", "added": a, "removed": r, "hunks": hunks})
    for rel, newtext in cur.items():
        if rel not in old:
            changes.append({"path": rel, "status": "added", "added": newtext.count("\n") + 1,
                            "removed": 0, "hunks": []})
    changes.sort(key=lambda c: (c["status"] != "modified", c["path"]))
    return {"ok": True, "id": cid, "ts": ck["ts"], "label": ck.get("label", ""),
            "changed": len(changes), "changes": changes}


def _diff_v2(project_id: str, cid: str, ck: dict, root: Path) -> dict:
    """The same answer as the version-1 diff, but only the files whose hash differs are read."""
    d = _dir(project_id)
    old_refs: dict = ck["refs"]
    cur_refs, fresh, _ = _scan(root)

    def cur_text(rel: str) -> str:
        t = fresh.get(rel)
        if t is None:
            try:
                t = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                t = ""
        return t

    changes = []
    for rel, sha in old_refs.items():
        now = cur_refs.get(rel)
        if now == sha:
            continue
        oldtext = _read_blob(d, sha) or ""
        if now is None:
            changes.append({"path": rel, "status": "deleted", "added": 0,
                            "removed": oldtext.count("\n") + 1, "hunks": []})
        else:
            a, r, hunks = mission._diff_stat(oldtext, cur_text(rel), cap=40)
            changes.append({"path": rel, "status": "modified", "added": a, "removed": r, "hunks": hunks})
    for rel in cur_refs:
        if rel not in old_refs:
            changes.append({"path": rel, "status": "added", "added": cur_text(rel).count("\n") + 1,
                            "removed": 0, "hunks": []})
    changes.sort(key=lambda c: (c["status"] != "modified", c["path"]))
    return {"ok": True, "id": cid, "ts": ck["ts"], "label": ck.get("label", ""),
            "changed": len(changes), "changes": changes}


def restore(project_id: str, cid: str, only: Optional[list[str]] = None) -> dict:
    ck = _load(project_id, cid)
    if not ck:
        return {"ok": False, "error": "checkpoint not found"}
    root = Path(ck["root"]).resolve()
    if not workspace._within(root, workspace._project_roots()):
        return {"ok": False, "error": "checkpoint root is outside any known project"}
    sel = set(only) if only else None
    restored = []
    for rel, text in ck["files"].items():
        if sel is not None and rel not in sel:
            continue
        dest = (root / rel).resolve()
        if root != dest and root not in dest.parents:   # guard against path escape
            continue
        try:
            try:
                if dest.read_text(encoding="utf-8") == text:
                    continue  # unchanged — skip
            except (OSError, UnicodeDecodeError):
                pass
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            restored.append(rel)
        except OSError:
            continue
    return {"ok": True, "restored": len(restored), "files": restored}

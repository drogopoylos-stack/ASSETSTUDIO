"""Shrink the screenshots a session READS, before the CLI puts them into the conversation.

WHY. The CLI sends every image in the conversation with every request, as base64. When the images
in one request pass about 25 MB, it removes the OLDEST ones (measured on the KAPOW session: the
first strip at 24.3-24.9 MB after a compaction, then one for each further 10-11 MB, which is about
30 screenshots). A strip edits old history. On Opus 5.5 the API then drops every thinking block
after the edit (`thinking_drop`, `prefix_mismatch`: 129 blocks at 14:37 on 2026-11-12), and the
prompt cache from the edit on is gone, so the whole conversation is written again: $4 to $6.50
each time, 8 times in one week. The API's own guidance for this case: size an image when it
enters the conversation, never afterwards.

WHAT. A PreToolUse hook on Read (called from btw_hook.py). An image file of 150 KB or more is
re-encoded as WebP at quality 90 with the SAME pixels (the long edge is capped at 2000, which the
CLI does anyway), and the Read is pointed at the copy. Image tokens follow the pixels, not the
bytes, so the model sees the same picture at the same token cost; the request carries about 56%
of the bytes (measured on 60 KAPOW screenshots), so the strip comes about half as often.

SAFE. Only in bypassPermissions mode, where reading the copy asks nobody anything the original
would have asked. Never when a settings file has a Read deny rule. Never for an animation. Never
when the copy is not at least 20% smaller. On any error the Read goes ahead untouched.

Standard library and Pillow only: the hook runs under the backend's Python without the app.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MIN_BYTES = 150_000
MAX_SIDE = 2000
QUALITY = 90
KEEP_S = 7 * 24 * 3600.0
_FORMAT = "1"               # part of the cache key: a change of encoder settings makes new copies


def _setting(data_dir: Path, key: str, default):
    try:
        d = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
        return d.get(key, default) if isinstance(d, dict) else default
    except Exception:
        return default


def _deny_files(cwd: str) -> list[Path]:
    files = [Path.home() / ".claude" / "settings.json"]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    if os.name == "nt":
        files.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode" / "managed-settings.json")
    return files


def has_read_deny(cwd: str) -> bool:
    """Any Read deny rule anywhere in reach. The copy is read under ANOTHER path, so a rule that
    protects the original would not see it; rather than re-implement the rule matcher, a folder
    with any such rule is left alone."""
    for f in _deny_files(cwd):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        deny = ((d if isinstance(d, dict) else {}).get("permissions") or {}).get("deny") or []
        if any(str(r).strip().startswith("Read") for r in deny):
            return True
    return False


def _inside(p: Path, root: Path) -> bool:
    try:
        return os.path.normcase(str(p.resolve())).startswith(os.path.normcase(str(root.resolve())) + os.sep)
    except OSError:
        return False


def sweep(cache: Path) -> None:
    """Copies older than a week go, at most once an hour."""
    stamp = cache / ".swept"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 3600:
            return
        cache.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        cut = time.time() - KEEP_S
        for f in cache.iterdir():
            if f.name.startswith("."):
                continue
            try:
                if f.stat().st_mtime < cut:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def shrink(src: Path, cache: Path) -> Optional[Path]:
    """The WebP copy of `src`, made once per version of the file; None when it is not worth it."""
    st = src.stat()
    key = hashlib.sha1(("%s|%s|%d|%d" % (_FORMAT, os.path.normcase(str(src.resolve())),
                                         st.st_mtime_ns, st.st_size)).encode("utf-8")).hexdigest()[:24]
    out = cache / (key + ".webp")
    skip = cache / (key + ".skip")
    if out.exists():
        try:
            os.utime(out)       # still in use: the weekly sweep keeps it
        except OSError:
            pass
        return out
    if skip.exists():
        return None
    from PIL import Image        # only here: most Reads are not images and never pay the import
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / (key + ".tmp")
    with Image.open(src) as im:
        if getattr(im, "is_animated", False) and int(getattr(im, "n_frames", 1) or 1) > 1:
            skip.touch()
            return None
        im.load()
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        img = im.convert("RGBA" if alpha else "RGB")
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        img.save(tmp, "WEBP", quality=QUALITY, method=4)
    if tmp.stat().st_size > 0.8 * st.st_size:
        try:
            tmp.unlink()
        except OSError:
            pass
        skip.touch()
        return None
    os.replace(tmp, out)
    return out


def redirect(payload: dict, data_dir: Path) -> Optional[dict]:
    """The Read's new input, pointed at the smaller copy, or None to leave the Read alone."""
    if str(payload.get("tool_name") or "") != "Read":
        return None
    if str(payload.get("permission_mode") or "") != "bypassPermissions":
        return None
    if not _setting(data_dir, "cc_image_shrink", True):
        return None
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") if isinstance(ti, dict) else None
    if not isinstance(fp, str) or not fp.strip():
        return None
    p = Path(fp)
    if not p.is_absolute():
        cwd = str(payload.get("cwd") or "")
        if not cwd:
            return None
        p = Path(cwd) / p
    if p.suffix.lower() not in EXTS:
        return None
    try:
        if not p.is_file() or p.stat().st_size < MIN_BYTES:
            return None
    except OSError:
        return None
    cache = data_dir / "read-cache"
    if _inside(p, cache):
        return None
    if has_read_deny(str(payload.get("cwd") or "")):
        return None
    out = shrink(p, cache)
    sweep(cache)
    if out is None:
        return None
    new = dict(ti)
    new["file_path"] = str(out)
    return new

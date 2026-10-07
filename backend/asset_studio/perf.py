"""The caches that keep the window instant, in one place.

MEASURED, not guessed (2026-09-20, py-spy on the running backend plus the real UI driven by real
clicks): the frontend was never the delay. A workspace switch waited on `/workspace/tree` 662 ms,
`/workspace/changes` 680 ms and `/mission/agents` 796 ms; an idle poll of `/context` reached
1,343 ms. Every one of those numbers was the same work done again on data that had not changed —
a rescan of 52 project folders, a re-read of a 386 MB transcript, a PATH search, a glob of every
forge record. So the fix is not a faster language; it is to stop repeating the work.

TWO SHAPES, AND NO OTHERS, so that every cache in the Studio can be reasoned about the same way:

  `Memo` — keyed on a STAMP you compute from the thing itself (a file's size and mtime, a folder's
           dates). Same stamp, same answer, no work. Any write changes the stamp, so a stale answer
           is not possible; it is exact, not approximate.
  `Ttl`  — keyed on time alone, for things with no cheap stamp: a PATH search, a process list. Say
           out loud how old the answer may be. Nothing here is older than a minute.

ONE SWITCH. `perf_fast` in Settings (default on) takes every cache in this module out of the way,
so a wrong answer is never a dead end: turn it off and the Studio is back on the slow path that
always reads from disk. Anything that cannot be switched off does not belong here.
"""
from __future__ import annotations

import atexit
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import fsutil
from .config import settings

_DISKS: list = []          # every DiskMemo, so all of them are written out at shutdown


def fast() -> bool:
    """False = read from disk every time (Settings → "Instant window")."""
    try:
        return settings.get("perf_fast", True) is not False
    except Exception:
        return True


class Memo:
    """Answer from memory while the stamp is unchanged.

    `build` runs OUTSIDE the lock on purpose: two callers may build the same value at once, which
    costs a little work twice, while holding the lock across a file read would let one slow build
    block every other cache user — the exact shape of freeze this module exists to remove.
    """

    def __init__(self, limit: int = 256):
        self._lock = threading.Lock()
        self._v: dict = {}
        self._limit = max(1, limit)

    def get(self, key: Any, stamp: Any, build: Callable[[], Any]) -> Any:
        if not fast():
            return build()
        with self._lock:
            hit = self._v.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        val = build()
        with self._lock:
            if len(self._v) >= self._limit:
                self._v.clear()
            self._v[key] = (stamp, val)
        return val

    def drop(self, key: Any = None) -> None:
        with self._lock:
            if key is None:
                self._v.clear()
            else:
                self._v.pop(key, None)


class Ttl:
    """Answer from memory for `seconds`, for work with no cheap stamp."""

    def __init__(self, seconds: float, limit: int = 64):
        self.seconds = float(seconds)
        self._lock = threading.Lock()
        self._v: dict = {}
        self._limit = max(1, limit)

    def get(self, key: Any, build: Callable[[], Any]) -> Any:
        if not fast():
            return build()
        now = time.time()
        with self._lock:
            hit = self._v.get(key)
        if hit is not None and (now - hit[0]) < self.seconds:
            return hit[1]
        val = build()
        with self._lock:
            if len(self._v) >= self._limit:
                self._v.clear()
            self._v[key] = (now, val)
        return val

    def drop(self, key: Any = None) -> None:
        with self._lock:
            if key is None:
                self._v.clear()
            else:
                self._v.pop(key, None)


class DiskMemo:
    """A Memo whose answers survive a restart.

    For work that costs seconds, whose answer can never change, and whose cost lands exactly when
    somebody is waiting: the bill for one subagent's transcript, the spawn count of a session. The
    first click into a project after a restart used to re-read gigabytes for numbers the Studio had
    already worked out — measured at 6.5 s on a workspace holding 2.5 GB in 144 files.

    THE STAMP GOES IN THE KEY. Put the file's size and date in the key string yourself; then a
    changed file simply misses, and there is no type to compare (JSON turns a tuple into a list,
    which is exactly the kind of quiet mismatch that makes a cache never hit).

    Values must be JSON-safe. Losing or deleting the file costs one slow call and nothing else.
    """

    def __init__(self, name: str, limit: int = 4000, save_every: float = 20.0):
        self.name = name
        self.limit = max(16, limit)
        self.save_every = float(save_every)
        self._lock = threading.Lock()
        self._v: Optional[dict] = None
        self._dirty = False
        self._saved = 0.0
        _DISKS.append(self)

    @property
    def path(self) -> Path:
        from .config import DATA_DIR
        return Path(DATA_DIR) / "cache" / (self.name + ".json")

    def _load_locked(self) -> None:
        if self._v is not None:
            return
        self._v = {}
        if not fast():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._v = raw
        except (OSError, ValueError):
            pass

    def get(self, key: str, build: Callable[[], Any]) -> Any:
        if not fast():
            return build()
        with self._lock:
            self._load_locked()
            hit = self._v.get(key)            # type: ignore[union-attr]
        if hit is not None:
            return hit
        val = build()
        try:
            json.dumps(val)
        except (TypeError, ValueError):
            return val                        # not storable — still a correct answer
        with self._lock:
            self._v[key] = val                # type: ignore[index]
            if len(self._v) > self.limit:     # type: ignore[arg-type]
                for k in list(self._v)[: len(self._v) - self.limit]:   # type: ignore[arg-type]
                    self._v.pop(k, None)      # type: ignore[union-attr]
            self._dirty = True
        self.save()
        return val

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty or self._v is None:
                return
            now = time.time()
            if not force and (now - self._saved) < self.save_every:
                return
            data = dict(self._v)
            self._saved = now
            self._dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            fsutil.replace(tmp, self.path)
        except OSError:
            pass

    def drop(self) -> None:
        with self._lock:
            self._v = {}
            self._dirty = True


class Cursor:
    """How far a file was read, and what was read up to there — kept across restarts.

    For an append-only file that is scanned for records. The scanner already remembers its place
    WITHIN a run; this makes the place survive the process, which is what the first click into a
    project after a restart was paying for: 1.6 s to re-scan a 1 GB transcript for records it had
    already extracted, 6.5 s for the agent bills beside it.

    Trusted only when the file is at least as long as the offset AND the 64 bytes ending at the
    offset are the same ones as last time — a rewind truncates and then writes different content,
    and that must be re-read from zero, not appended to.
    """

    EDGE = 64

    def __init__(self, name: str, save_every: float = 20.0):
        self.name = name
        self.save_every = float(save_every)
        self._lock = threading.Lock()
        self._mem: dict = {}          # path -> {"off": int, "edge": bytes, "rows": Any, "saved": float}
        _DISKS.append(self)

    def _file(self, path: str) -> Path:
        import hashlib
        from .config import DATA_DIR
        h = hashlib.sha1(path.lower().encode("utf-8", "replace")).hexdigest()[:16]
        return Path(DATA_DIR) / "cache" / self.name / (h + ".json")

    @staticmethod
    def _edge_of(path: str, off: int) -> bytes:
        if off <= 0:
            return b""
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, off - Cursor.EDGE))
                return fh.read(min(Cursor.EDGE, off))
        except OSError:
            return b""

    def get(self, path: str, size: int):
        """(offset, rows) to continue from, or None to start at zero."""
        if not fast():
            return None
        with self._lock:
            st = self._mem.get(path)
        if st is None:
            try:
                raw = json.loads(self._file(path).read_text(encoding="utf-8"))
                st = {"off": int(raw["off"]), "edge": bytes.fromhex(raw["edge"]), "rows": raw["rows"],
                      "saved": time.time()}
            except (OSError, ValueError, KeyError, TypeError):
                return None
            with self._lock:
                self._mem[path] = st
        if not (0 < st["off"] <= size):
            return None
        if self._edge_of(path, st["off"]) != st["edge"]:
            return None
        return st["off"], st["rows"]

    def put(self, path: str, off: int, rows) -> None:
        if not fast():
            return
        with self._lock:
            st = self._mem.get(path) or {}
            st.update(off=off, edge=self._edge_of(path, off), rows=rows)
            st.setdefault("saved", 0.0)
            self._mem[path] = st
            due = (time.time() - st["saved"]) >= self.save_every
            if due:
                st["saved"] = time.time()
        if due:
            self._write(path)

    def _write(self, path: str) -> None:
        with self._lock:
            st = self._mem.get(path)
            if not st:
                return
            data = {"off": st["off"], "edge": st["edge"].hex(), "rows": st["rows"]}
        try:
            f = self._file(path)
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            fsutil.replace(tmp, f)
        except (OSError, TypeError, ValueError):
            pass

    def save(self, force: bool = False) -> None:
        for path in list(self._mem):
            self._write(path)

    def drop(self) -> None:
        with self._lock:
            self._mem.clear()


def file_stamp(p) -> tuple:
    """(size, mtime_ns) — changes on any write. (0, 0) when the file is not there."""
    try:
        st = Path(p).stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return (0, 0)


def dir_stamp(p) -> tuple:
    """(how many entries, the newest entry's mtime_ns) for ONE level of a folder.

    `os.scandir` is used on purpose: on Windows the listing already carries each entry's dates, so
    this is one directory read rather than one system call per file. That matters where it is used
    — a task folder here holds 400 files and this runs on a poll.

    It notices a CHANGED file, not only a new one, which `Path.stat()` on the folder alone would
    miss: a folder's own date does not move when a file inside it is rewritten in place.
    """
    n, newest = 0, 0
    try:
        import os as _os
        with _os.scandir(str(p)) as it:
            for e in it:
                n += 1
                try:
                    # A FOLDER IS ASKED DIRECTLY. On Windows the listing's dates come from the
                    # parent's index, and a folder's entry there is NOT refreshed when a file is
                    # created inside it — a new forge record was invisible until something else
                    # touched the folder. A file's entry is written when its handle closes, so
                    # for files the listing is exact and free.
                    if e.is_dir(follow_symlinks=False):
                        mt = _os.stat(e.path).st_mtime_ns
                    else:
                        mt = e.stat(follow_symlinks=False).st_mtime_ns
                except OSError:
                    continue
                if mt > newest:
                    newest = mt
    except OSError:
        return (0, 0)
    return (n, newest)


def stamps(paths) -> tuple:
    """One stamp for several files — for a value built from more than one of them."""
    return tuple(file_stamp(p) for p in paths)


@atexit.register
def _save_all() -> None:
    """Write every DiskMemo out on the way down, so a restart starts warm."""
    for d in list(_DISKS):
        try:
            d.save(force=True)
        except Exception:
            pass

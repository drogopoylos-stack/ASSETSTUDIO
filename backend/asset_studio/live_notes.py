"""Small durable mailbox shared by the backend and the standalone Claude hook.

One atomic file per send: concurrent sends cannot overwrite each other, and hooks,
turn-end fallback and recovery can each claim a note only once. Legacy .note files
are still readable after an update.
"""
import os
import time
import uuid
from pathlib import Path


def _files(path: Path) -> list[Path]:
    folder = Path(str(path) + ".d")
    return ([path] if path.is_file() else []) + sorted(
        p for p in folder.glob("*") if p.name.endswith(".note") or ".consuming-" in p.name
    )


def put(path: Path, text: str) -> None:
    folder = Path(str(path) + ".d")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{time.time_ns():020d}-{uuid.uuid4().hex}.note"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def peek(path: Path) -> str:
    notes = []
    for item in _files(path):
        try:
            notes.append(item.read_text(encoding="utf-8").strip())
        except OSError:
            pass  # another reader claimed it
    return "\n\n".join(n for n in notes if n)


def claim(path: Path, max_age: float | None = None) -> str:
    # Windows can open the same rename source in two callers before either move
    # completes. Serialize claims with an OS lock, released even if a hook crashes.
    # Writers still publish their separate files independently.
    folder = Path(str(path) + ".d")
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "claim.lock").open("a+b") as lock:
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return ""  # another boundary is consuming this mailbox
        try:
            return _claim_unlocked(path, max_age)
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _claim_unlocked(path: Path, max_age: float | None) -> str:
    notes = []
    for item in _files(path):
        tmp = item.with_suffix(f".consuming-{uuid.uuid4().hex}")
        try:
            os.replace(item, tmp)
        except OSError:
            continue  # another consumer won
        try:
            if max_age is None or time.time() - tmp.stat().st_mtime <= max_age:
                notes.append(tmp.read_text(encoding="utf-8").strip())
            tmp.unlink(missing_ok=True)
        except OSError:
            if tmp.exists():
                os.replace(tmp, item)  # keep unread mail for recovery
    return "\n\n".join(n for n in notes if n)

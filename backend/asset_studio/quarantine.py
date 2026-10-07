"""Keep unreadable state instead of silently replacing it.

``config.Settings._load`` used to swallow a corrupt ``settings.json``::

    except (json.JSONDecodeError, OSError):
        data = {}

Defaults were then merged over the empty dict and the next ``update()`` wrote the result
straight back to disk — so a single truncated write destroyed every setting the user had,
left no trace, and there was nothing to recover from.

This module keeps the bad bytes. The file is copied into ``<parent>/quarantine/`` under a
timestamped name and recorded in a small SQLite ledger with a reason code; the caller still
gets its fallback, so start-up never blocks on a damaged file.

Two rules this module lives by:

* **Stdlib only, and it must stay that way.** ``config`` imports this, so importing
  ``config`` here — or anything that reaches it, such as ``db`` — is a circular import.
* **Never raise into the caller.** A fault in the rescue path must not stop the Studio
  from booting. Every entry point swallows its own errors and degrades to the fallback.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

#: Reason codes are a closed set — the ledger's CHECK constraint enforces the same list,
#: so adding one here without adding it there is rejected by SQLite rather than stored.
REASONS = ("parse_error", "read_error", "wrong_type")

_TABLE_STRICT = """
CREATE TABLE IF NOT EXISTS quarantined_state (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path            TEXT    NOT NULL CHECK (length(source_path) BETWEEN 1 AND 1024),
    saved_as               TEXT    NOT NULL CHECK (length(saved_as)    BETWEEN 1 AND 1024),
    reason_code            TEXT    NOT NULL CHECK (reason_code IN ('parse_error','read_error','wrong_type')),
    detail                 TEXT    NOT NULL CHECK (length(detail) <= 4000),
    content_sha256         TEXT    NOT NULL CHECK (length(content_sha256) = 64),
    size_bytes             INTEGER NOT NULL CHECK (size_bytes >= 0),
    quarantined_at_unix_ms INTEGER NOT NULL CHECK (quarantined_at_unix_ms > 0)
) STRICT
"""
# STRICT needs SQLite 3.37+ (the bundled one here is 3.40). On anything older the CREATE
# fails, and a missing ledger must not cost the user their rescued file — so fall back to
# the same table without the keyword and carry on.
_TABLE_PLAIN = _TABLE_STRICT.replace(") STRICT", ")")
_INDEX = ("CREATE INDEX IF NOT EXISTS quarantined_state_recent_idx "
          "ON quarantined_state(quarantined_at_unix_ms DESC, id)")


def quarantine_dir(path: Path) -> Path:
    """Where rescued copies of *path* live. Derived from the file itself so this module
    needs nothing from ``config`` (see the circular-import note in the module docstring)."""
    return Path(path).parent / "quarantine"


def _ledger(qdir: Path) -> Optional[sqlite3.Connection]:
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(qdir / "quarantine.db"))
        try:
            conn.execute(_TABLE_STRICT)
        except sqlite3.DatabaseError:
            conn.execute(_TABLE_PLAIN)
        conn.execute(_INDEX)
        conn.commit()
        return conn
    except Exception:
        return None


def _already_held(conn: sqlite3.Connection, source: str, digest: str) -> bool:
    """True if these exact bytes from this exact path are already quarantined.

    Without this, a file that stays broken is copied again on every reload — each start-up,
    each settings read — until the disk fills with identical rescues.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM quarantined_state WHERE source_path = ? AND content_sha256 = ? LIMIT 1",
            (source, digest),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def quarantine_file(path: Path, reason_code: str, detail: str = "") -> Optional[Path]:
    """Copy *path* aside and record why. Returns the saved copy, or None if nothing was done.

    The original is left in place: callers rewrite it with defaults, and that rewrite is the
    whole reason a copy has to exist first.
    """
    path = Path(path)
    if reason_code not in REASONS:          # keep python and the CHECK constraint honest
        reason_code = "read_error"
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    qdir = quarantine_dir(path)
    conn = _ledger(qdir)
    if conn is not None and _already_held(conn, str(path), digest):
        conn.close()
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    saved = qdir / f"{path.name}.{stamp}.{digest[:8]}"
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, saved)        # copy, never move — the caller still owns the original
    except OSError:
        if conn is not None:
            conn.close()
        return None
    if conn is not None:
        try:
            conn.execute(
                """INSERT INTO quarantined_state
                   (source_path, saved_as, reason_code, detail, content_sha256,
                    size_bytes, quarantined_at_unix_ms)
                   VALUES (?,?,?,?,?,?,?)""",
                (str(path), str(saved), reason_code, str(detail)[:4000], digest,
                 len(raw), int(time.time() * 1000)),
            )
            conn.commit()
        except Exception:
            pass                            # the copy is what matters; the ledger is bookkeeping
        finally:
            conn.close()
    return saved


def safe_load_json(path: Path, default: Any, *, expect: type = dict) -> Any:
    """Read JSON from *path*, or quarantine it and hand back a copy of *default*.

    ``expect`` guards the shape as well as the syntax: a file holding valid JSON of the
    wrong type (``null`` where a dict belongs, say) is just as unusable as a truncated one,
    and silently accepting it pushes the failure somewhere far less obvious.
    """
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        quarantine_file(path, "read_error", f"{type(e).__name__}: {e}")
        return copy.deepcopy(default)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        quarantine_file(path, "parse_error", f"{type(e).__name__}: {e}")
        return copy.deepcopy(default)
    if expect is not None and not isinstance(data, expect):
        quarantine_file(path, "wrong_type",
                        f"expected {expect.__name__}, found {type(data).__name__}")
        return copy.deepcopy(default)
    return data


def list_quarantined(where: Path, limit: int = 50) -> list[dict]:
    """Recent rescues, newest first — for a settings pane or a support question.

    Accepts whichever handle the caller happens to have: the data directory, a file that
    lives in it, or the quarantine directory itself. Taking ``.parent`` unconditionally
    meant passing the data directory looked one level too high and quietly returned
    nothing, which reads exactly like "no incidents" — the one answer this must never
    give by mistake.
    """
    p = Path(where)
    if p.name == "quarantine":
        qdir = p
    elif p.is_dir():
        qdir = p / "quarantine"
    else:
        qdir = quarantine_dir(p)
    db = qdir / "quarantine.db"
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT source_path, saved_as, reason_code, detail, size_bytes,
                      quarantined_at_unix_ms
                 FROM quarantined_state
                ORDER BY quarantined_at_unix_ms DESC, id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

"""SQLite-backed catalog + job history.

Uses the stdlib ``sqlite3`` driver with a single shared connection guarded by a
lock — simple, dependency-free, and plenty fast for a local single-user studio.
Rows store the full Pydantic JSON plus a few indexed columns for filtering.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from typing import Any, Optional

from .config import DB_PATH
from .models import Asset, Job, JobStatus

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _migrate(_conn)
    return _conn


# ---------------------------------------------------------------------------
# Schema versioning
#
# ``PRAGMA user_version`` is the schema's version number, and it is 0 on a database that has
# never been migrated — including the catalog.db already sitting in data/. Migration 1 is
# therefore a *baseline*: it recreates today's tables with IF NOT EXISTS, which is a no-op on
# an existing catalog and the correct first step on a fresh one.
#
# Adding a migration:
#   * append to _MIGRATIONS; never renumber or edit an entry that has shipped, because
#     databases in the wild have already recorded that number as done;
#   * make it idempotent. The version is bumped only after the migration returns, so a crash
#     halfway through means it runs again from the top next launch;
#   * give any NEW table STRICT and CHECK constraints — see quarantine.py for the pattern.
#     The tables below predate that rule and cannot take STRICT without a full rebuild,
#     which is not worth risking on live rows.
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 1

#: Set when the file was written by a build newer than this one. Start-up continues — refusing
#: to boot would strand the user with no way back — but writes may fail against columns this
#: build does not know about, and this flag is why.
schema_is_from_the_future = False


def _migration_1_baseline(conn: sqlite3.Connection) -> None:
    # One statement per execute(), deliberately. Connection.executescript() COMMITs any open
    # transaction before it runs (Python 3.10 behaviour), which would silently end the
    # transaction _migrate() opened around this call and make the following COMMIT raise
    # "cannot commit - no transaction is active". Migrations must not commit; see _migrate().
    for statement in (
        """CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            name TEXT,
            stage TEXT,
            type TEXT,
            provider_id TEXT,
            target_game TEXT,
            commercial_ok INTEGER,
            cost REAL,
            created_at REAL,
            tags TEXT,
            json TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_assets_stage ON assets(stage)",
        "CREATE INDEX IF NOT EXISTS idx_assets_game ON assets(target_game)",
        "CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at)",
        """CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            stage TEXT,
            provider_id TEXT,
            status TEXT,
            cost REAL,
            created_at REAL,
            json TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
    ):
        conn.execute(statement)


_MIGRATIONS = {
    1: _migration_1_baseline,
}


def _migrate(conn: sqlite3.Connection) -> None:
    global schema_is_from_the_future
    have = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if have > SCHEMA_VERSION:
        # Downgrade, or a half-finished update. Don't touch the schema and don't crash: the
        # rows are JSON blobs behind named columns, so reads keep working and a write that
        # genuinely conflicts will fail loudly at the point it happens, not at import time.
        schema_is_from_the_future = True
        print(f"[db] catalog.db is at schema v{have}, this build knows v{SCHEMA_VERSION}. "
              f"Leaving it alone; update the Studio if something looks wrong.", file=sys.stderr)
        return
    # Each migration runs inside an explicit transaction together with its version bump, so the
    # two can never disagree: either both land or neither does. SQLite makes DDL transactional,
    # which means a migration that throws half-way leaves no partial table behind and the retry
    # starts from clean ground. Idempotence is still the rule for anything shipped, but it is no
    # longer the only thing standing between a crash and a wedged database.
    prior_isolation = conn.isolation_level
    conn.isolation_level = None          # take manual control of BEGIN/COMMIT
    try:
        for version in range(have + 1, SCHEMA_VERSION + 1):
            conn.execute("BEGIN")
            try:
                _MIGRATIONS[version](conn)
                # PRAGMA cannot take a bound parameter; int() is what makes this safe.
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.isolation_level = prior_isolation


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
def save_asset(asset: Asset) -> Asset:
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT OR REPLACE INTO assets
               (id,name,stage,type,provider_id,target_game,commercial_ok,cost,created_at,tags,json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                asset.id,
                asset.name,
                asset.stage.value,
                asset.type.value,
                asset.provider_id,
                asset.target_game,
                None if asset.commercial_ok is None else int(asset.commercial_ok),
                asset.cost,
                asset.created_at,
                json.dumps(asset.tags),
                asset.model_dump_json(),
            ),
        )
        conn.commit()
    return asset


def get_asset(asset_id: str) -> Optional[Asset]:
    with _lock:
        row = _connect().execute("SELECT json FROM assets WHERE id=?", (asset_id,)).fetchone()
    return Asset.model_validate_json(row["json"]) if row else None


def list_assets(
    stage: Optional[str] = None,
    target_game: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
) -> list[Asset]:
    q = "SELECT json FROM assets WHERE 1=1"
    args: list[Any] = []
    if stage:
        q += " AND stage=?"
        args.append(stage)
    if target_game:
        q += " AND target_game=?"
        args.append(target_game)
    if tag:
        q += " AND tags LIKE ?"
        args.append(f'%"{tag}"%')
    if search:
        q += " AND (name LIKE ? OR json LIKE ?)"
        args += [f"%{search}%", f"%{search}%"]
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _lock:
        rows = _connect().execute(q, args).fetchall()
    return [Asset.model_validate_json(r["json"]) for r in rows]


def delete_asset(asset_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def save_job(job: Job) -> Job:
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT OR REPLACE INTO jobs (id,stage,provider_id,status,cost,created_at,json)
               VALUES (?,?,?,?,?,?,?)""",
            (
                job.id,
                job.stage.value,
                job.provider_id,
                job.status.value,
                job.cost,
                job.created_at,
                job.model_dump_json(),
            ),
        )
        conn.commit()
    return job


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        row = _connect().execute("SELECT json FROM jobs WHERE id=?", (job_id,)).fetchone()
    return Job.model_validate_json(row["json"]) if row else None


def list_jobs(status: Optional[str] = None, limit: int = 200, offset: int = 0) -> list[Job]:
    q = "SELECT json FROM jobs WHERE 1=1"
    args: list[Any] = []
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _lock:
        rows = _connect().execute(q, args).fetchall()
    return [Job.model_validate_json(r["json"]) for r in rows]


# ---------------------------------------------------------------------------
# Aggregates (dashboard / cost tracking)
# ---------------------------------------------------------------------------
def month_to_date_cost() -> float:
    import datetime

    now = datetime.datetime.now()
    month_start = datetime.datetime(now.year, now.month, 1).timestamp()
    with _lock:
        row = _connect().execute(
            "SELECT COALESCE(SUM(cost),0) s FROM jobs WHERE created_at>=?", (month_start,)
        ).fetchone()
    return float(row["s"] or 0.0)


def reconcile_orphans(active_ids: set[str]) -> int:
    """Mark jobs left 'running'/'queued' in the DB (from a previous process) as
    failed — they can't resume after a restart. Returns how many were fixed."""
    fixed = 0
    for job in list_jobs(limit=1000):
        if job.status.value in ("running", "queued") and job.id not in active_ids:
            job.status = JobStatus.failed
            job.error = "interrupted by a backend restart"
            job.error_hint = "This job was interrupted when the studio restarted. Re-run it."
            save_job(job)
            fixed += 1
    return fixed


def stats_summary() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        total_assets = conn.execute("SELECT COUNT(*) c FROM assets").fetchone()["c"]
        total_cost = conn.execute("SELECT COALESCE(SUM(cost),0) s FROM assets").fetchone()["s"]
        by_stage = {
            r["stage"]: r["c"]
            for r in conn.execute("SELECT stage, COUNT(*) c FROM assets GROUP BY stage").fetchall()
        }
        by_provider_cost = {
            r["provider_id"]: r["s"]
            for r in conn.execute(
                "SELECT provider_id, COALESCE(SUM(cost),0) s FROM assets GROUP BY provider_id"
            ).fetchall()
        }
        non_commercial = conn.execute(
            "SELECT COUNT(*) c FROM assets WHERE commercial_ok=0"
        ).fetchone()["c"]
        jobs_running = conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status IN ('queued','running')"
        ).fetchone()["c"]
    return {
        "total_assets": total_assets,
        "total_cost": round(total_cost, 4),
        "by_stage": by_stage,
        "by_provider_cost": {k: round(v, 4) for k, v in by_provider_cost.items()},
        "non_commercial_assets": non_commercial,
        "jobs_running": jobs_running,
        "generated_at": time.time(),
    }

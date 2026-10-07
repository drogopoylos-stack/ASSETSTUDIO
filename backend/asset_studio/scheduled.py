"""Scheduled (delayed) messages to Claude Code sessions.

The user writes a prompt in the composer, picks a delay + target workspace, and the studio fires
it via :func:`cc_session.send` at that moment. Jobs persist to ``DATA_DIR/scheduled.json`` so they
survive a backend restart, and a single daemon thread checks every few seconds for due jobs.
"""
from __future__ import annotations

import datetime
import json
import threading
import time
import uuid
from typing import Optional

from . import fsutil
from .config import DATA_DIR, settings
from . import quarantine

_LOCK = threading.Lock()
_FILE = DATA_DIR / "scheduled.json"
_started = False
_KEEP_FINISHED = 24 * 3600.0   # keep sent/failed records this long for the UI, then prune


#: Monday is 0, matching ``datetime.weekday()``. A job with an empty set is one-shot, which is
#: what every job written before this existed looks like — so old rows keep their old behaviour
#: without a migration.
WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)


def _clean_weekdays(raw) -> list[int]:
    """Normalise whatever the caller sent into a sorted, de-duplicated set of 0-6."""
    out: set[int] = set()
    for v in (raw or []):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n in WEEKDAYS:
            out.add(n)
    return sorted(out)


def _next_occurrence(weekdays: list[int], hhmm: str, after: float) -> Optional[float]:
    """Epoch seconds of the next *local* ``hhmm`` on one of ``weekdays``, strictly after ``after``.

    Strictly, deliberately: the caller reschedules a job the moment it fires, and a boundary of
    ">=" would land on the same instant and fire again immediately, and again, forever.

    Returns None if the weekday set is empty (a one-shot job) or the time is unparseable, and the
    caller then treats the job as finished rather than guessing at a schedule.
    """
    if not weekdays:
        return None
    try:
        hh, mm = (int(x) for x in str(hhmm).split(":", 1))
    except (ValueError, TypeError):
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    start = datetime.datetime.fromtimestamp(after)
    # 0..7 rather than 0..6: when today is the only chosen weekday and its time has already
    # passed, the answer is the same weekday NEXT week, which is offset 7.
    for offset in range(8):
        day = (start + datetime.timedelta(days=offset)).date()
        if day.weekday() not in weekdays:
            continue
        cand = datetime.datetime.combine(day, datetime.time(hh, mm))
        ts = cand.timestamp()
        if ts > after:
            return ts
    return None


def _load() -> list[dict]:
    # An unreadable file used to mean "no jobs", and the next _save() wrote that emptiness
    # back — every pending and recurring job dropped without a word. Keep the bytes.
    return quarantine.safe_load_json(_FILE, [], expect=list)


def _save(jobs: list[dict]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")
        fsutil.replace(tmp, _FILE)
    except Exception:
        pass


def list_jobs() -> list[dict]:
    with _LOCK:
        jobs = _load()
    jobs.sort(key=lambda j: (j.get("status") != "pending", j.get("send_at", 0)))
    return jobs


def create_job(data: dict) -> dict:
    now = time.time()
    weekdays = _clean_weekdays(data.get("repeat_weekdays"))
    repeat_time = str(data.get("repeat_time") or "")
    send_at = float(data.get("send_at") or now)
    if weekdays:
        # A repeating job is defined by its weekday set and time of day, so derive the first
        # run from those rather than trusting a send_at the UI may not have computed. Falling
        # back to the clock of send_at keeps the call usable without a repeat_time.
        if not repeat_time:
            repeat_time = datetime.datetime.fromtimestamp(send_at).strftime("%H:%M")
        first = _next_occurrence(weekdays, repeat_time, now - 1)
        if first is not None:
            send_at = first
        else:
            weekdays = []          # unparseable time — degrade to the one-shot it already was
    job = {
        "id": uuid.uuid4().hex[:12],
        "project_id": data.get("project_id", ""),
        "path": data.get("path", ""),
        "root_name": data.get("root_name", ""),
        "message": data.get("message", ""),
        "send_at": send_at,
        "created_at": now,
        "repeat_weekdays": weekdays,   # [] = fire once, exactly as before this existed
        "repeat_time": repeat_time if weekdays else "",
        "last_run_at": None,
        "run_count": 0,
        "model": data.get("model", "default"),
        "permission_mode": data.get("permission_mode", "acceptEdits"),
        "effort": data.get("effort", "default"),
        "fork": bool(data.get("fork", False)),
        "thinking": bool(data.get("thinking", False)),
        "agent": data.get("agent", "claude"),
        "new_session": bool(data.get("new_session", False)),
        "session": data.get("session", ""),
        "images": list(data.get("images") or []),
        "status": "pending",
        "error": "",
        "sent_at": None,
    }
    with _LOCK:
        jobs = _load()
        jobs.append(job)
        _save(jobs)
    _ensure_started()
    return job


def cancel_job(job_id: str) -> bool:
    """Cancel a pending job (or clear a finished record) by id."""
    with _LOCK:
        jobs = _load()
        kept = [j for j in jobs if j.get("id") != job_id]
        if len(kept) == len(jobs):
            return False
        _save(kept)
        return True


def _fire(job: dict) -> None:
    from . import cc_session
    try:
        res = cc_session.send(
            job["project_id"], job["message"],
            model=job.get("model", "default"),
            permission_mode=job.get("permission_mode", "acceptEdits"),
            fork=bool(job.get("fork", False)),
            images=list(job.get("images") or []) or None,
            effort=job.get("effort", "default"),
            agent=job.get("agent", "claude"),
            new_session=bool(job.get("new_session", False)),
            session=job.get("session", ""),
            path=job.get("path", ""),
            thinking=bool(job.get("thinking", False)),
        )
        ok = bool(res.get("ok"))
        job["status"] = "sent" if ok else "failed"
        job["error"] = "" if ok else (res.get("error") or "send failed")
    except Exception as e:  # never let one bad job kill the loop
        job["status"] = "failed"
        job["error"] = str(e)
    job["sent_at"] = time.time()


def paused() -> bool:
    """The one switch for every scheduled and recurring message.

    A scheduled message is the only thing in the Studio that spends tokens with nobody watching,
    and a recurring one re-arms itself forever. There used to be no way to stop them all — only
    each job by hand. While paused, due jobs stay pending; they fire when the switch is turned
    back on."""
    return bool(settings.get("scheduled_paused", False))


def set_paused(on: bool) -> bool:
    settings.update({"scheduled_paused": bool(on)})
    return paused()


def _loop() -> None:
    while True:
        time.sleep(5.0)
        if paused():
            continue
        now = time.time()
        due: list[dict] = []
        # claim due jobs under the lock so we never double-fire, even if the file is edited
        with _LOCK:
            jobs = _load()
            changed = False
            for j in jobs:
                if j.get("status") == "pending" and float(j.get("send_at", 0)) <= now:
                    j["status"] = "sending"
                    due.append(dict(j))
                    changed = True
            if changed:
                _save(jobs)
        if not due:
            continue
        for j in due:        # fire OUTSIDE the lock (send() launches a process)
            _fire(j)
        # write results back + prune old finished records
        with _LOCK:
            jobs = _load()
            res_by_id = {j["id"]: j for j in due}
            for j in jobs:
                r = res_by_id.get(j.get("id"))
                if r is None:
                    continue
                j.update(status=r["status"], error=r["error"], sent_at=r["sent_at"])
                j["last_run_at"] = r["sent_at"]
                j["run_count"] = int(j.get("run_count") or 0) + 1
                # A repeating job goes straight back to pending on its next occurrence instead
                # of retiring. _next_occurrence is strictly-after, so the new time can never be
                # the one just fired — that is what stops it re-firing on the very next sweep.
                weekdays = _clean_weekdays(j.get("repeat_weekdays"))
                if not weekdays:
                    continue
                nxt = _next_occurrence(weekdays, j.get("repeat_time") or "", time.time())
                if nxt is None:
                    continue        # unusable schedule — leave it finished rather than spin
                j["send_at"] = nxt
                j["status"] = "pending"
                j["sent_at"] = None   # keeps the pruner from ageing out a live recurring job
            cutoff = time.time() - _KEEP_FINISHED
            jobs = [j for j in jobs if j.get("status") in ("pending", "sending") or (j.get("sent_at") or 0) > cutoff]
            _save(jobs)


def _ensure_started() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()


# Start on import so jobs persisted before a restart still fire on time.
_ensure_started()

"""Mission Control — a live command center for all your Claude Code projects.

Reads Claude Code's on-disk session data (``~/.claude/projects/*``) READ-ONLY and
inspects OS processes/ports to answer:
  * which projects exist, when each was last worked on, and is it active now
  * how many agents are running right now and what each is doing
  * the latest "upgrades" (git branch + last commit + dirty file count)
  * each project's running localhost dev server (mapped by process cwd)

Everything is best-effort and heavily guarded — Claude Code's transcript format is
internal, so parsing never raises; missing data just degrades gracefully. Parsing
is head/tail-bounded so multi-hundred-MB transcripts stay cheap, results are
computed in parallel, and the whole overview is cached for a few seconds.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from . import fsutil
from . import perf
from . import engines
from .coalesce import coalesced
from .config import DATA_DIR, settings

PROJECT_ACTIVE_WINDOW = 600   # s — project counted "active" if touched within
AGENT_ACTIVE_WINDOW = 180     # s — agent transcript counted "running" if written within
WORKING_WINDOW = 14           # s — transcript written this recently → likely generating now
_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL = 3.0
# Scanning every project takes ~2s here (30+ folders, each read for its cwd, summary and todos).
# With a plain 3s TTL practically every workspace switch paid that in full, because a person takes
# longer than 3s between clicks — which is exactly what "changing workspaces is slow" was.
# So a stale result is SERVED IMMEDIATELY and refreshed in the background: the switch is instant
# and the data catches up within one scan. Only the very first call after boot blocks.
_CACHE_HARD_TTL = 90.0            # older than this and we would rather wait than mislead
_refresh_lock = threading.Lock()
_build_lock = threading.Lock()   # one cold overview at a time, however many ask
_refreshing = False


def _refresh_overview_async() -> None:
    """Recompute the overview off the request path. Single-flight: extra callers do nothing."""
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    def work() -> None:
        global _refreshing
        try:
            overview(force=True)
        except Exception:
            pass
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CCProject(BaseModel):
    id: str
    path: str = ""
    name: str
    exists: bool = False
    last_activity: float = 0.0
    active: bool = False
    sessions: int = 0
    size_bytes: int = 0
    cc_version: str = ""
    git_branch: str = ""
    git_last_commit: str = ""
    git_last_commit_rel: str = ""
    git_dirty: int = 0
    last_summary: str = ""
    awaiting_input: bool = False     # session ended on a question, waiting for the user
    working: bool = False            # transcript is actively being written → "Generating…" now
    model: str = ""
    ctx_used: int = 0                # tokens in the current context window
    ctx_max: int = 0                 # model context window size
    ctx_pct: float = 0.0             # % of context window used
    ctx_remaining: float = 0.0       # % of headroom left until auto-compact
    just_compacted: bool = False     # ctx is an estimate right after /compact (no real turn yet)
    # prompt-cache split of the last turn's billed input (CLI 2.1.251 shows the same in /cost)
    cache_read: int = 0              # input served from the cache — the cheap part
    cache_write: int = 0             # input written INTO the cache this turn
    fresh_in: int = 0                # input neither read nor cached
    cache_pct: float = 0.0           # cache_read as a % of the billed input
    todos_total: int = 0
    todo_done: int = 0
    todo_in_progress: str = ""
    agents_active: int = 0
    agents_top: int = 0        # spawns made by the main session (what you actually asked for)
    agents_nested: int = 0     # spawns made BY those agents (children spawning their own)
    port: Optional[int] = None
    url: Optional[str] = None


class CCAgent(BaseModel):
    project_id: str
    project_name: str
    session: str
    agent_id: str
    last_update: float
    age_seconds: float
    label: str = ""


class CCPort(BaseModel):
    port: int
    pid: int = 0
    process: str = ""
    cwd: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    is_self: bool = False
    url: str


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def claude_projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    home = Path(base) if base else (Path.home() / ".claude")
    return home / "projects"


# --- Alternate engines: parallel Claude-Code universes ------------------------
# Kimi K3 and Qwen run through the SAME claude CLI (each vendor's Anthropic-compatible
# endpoint) with their own CLAUDE_CONFIG_DIR, so their sessions/context/usage are fully
# isolated from the real Claude ones AND from each other. An "<engine>--" project-id prefix
# routes every feed/live/context/rewind lookup into that universe — which is exactly what
# makes an alternate engine's chat render identically to a Claude chat.
# Providers the user adds in Settings → Models join this map at runtime, so a custom
# engine gets the same isolation without a single extra line in the feed code.
KIMI_PREFIX = "kimi--"
QWEN_PREFIX = "qwen--"
BUILTIN_ALT_HOMES = {KIMI_PREFIX: "kimi-home", QWEN_PREFIX: "qwen-home", "deepseek-harness--": "deepseek-harness-home"}
ALT_HOMES = BUILTIN_ALT_HOMES      # kept for callers that only need the built-ins


def alt_homes() -> dict[str, str]:
    """{prefix: home dir name} for every alternate engine — built-in and user-added."""
    out = dict(BUILTIN_ALT_HOMES)
    try:
        from . import chat_providers
        for p in chat_providers.all_providers():
            pid = str(p.get("id", ""))
            if pid:
                out[chat_providers.prefix(pid)] = chat_providers.home_dirname(pid)
    except Exception:
        pass
    return out


def _alt_home(dirname: str) -> Path:
    from .config import DATA_DIR
    return DATA_DIR / dirname


def kimi_home() -> Path:
    return _alt_home(BUILTIN_ALT_HOMES[KIMI_PREFIX])


def qwen_home() -> Path:
    return _alt_home(BUILTIN_ALT_HOMES[QWEN_PREFIX])


def alt_prefix(project_id: str) -> str:
    """The alternate-engine prefix carried by this id ('' for a normal Claude project).
    Longest match wins so an id like 'glm-air--' can never be swallowed by 'glm--'."""
    best = ""
    for p in alt_homes():
        if project_id.startswith(p) and len(p) > len(best):
            best = p
    return best


def last_agent_for(path: str) -> dict:
    """Which engine most recently ran in this folder.

    The workspace already carries the answer and nobody was reading it: Claude Code files its
    transcripts under <engine home>/projects/<slug>, one home per engine, so the newest transcript
    names the engine last used there. That is what lets per-workspace agent memory work on the
    very first switch, instead of only after the user has re-picked in every workspace they own."""
    try:
        from .workspace import _claude_id
        slug = _claude_id(path)
    except Exception:
        return {"agent": "", "at": 0.0}
    if not slug:
        return {"agent": "", "at": 0.0}

    cands = [("claude", claude_projects_dir() / slug)]
    for pfx, dirname in alt_homes().items():
        cands.append((pfx[:-2], _alt_home(dirname) / "projects" / slug))

    # Two engines can share one session universe on purpose, and the folder cannot say which of
    # them wrote it. Let the engine that OWNS the home answer for it; picking the other would
    # switch the workspace to an agent that was never used there.
    picked: dict = {}
    for name, d in cands:
        if str(d) not in picked or d.parent.parent.name == f"{name}-home":
            picked[str(d)] = (name, d)
    cands = list(picked.values())

    best, best_at = "", 0.0
    for name, d in cands:
        try:
            if not d.is_dir():
                continue
            at = max((f.stat().st_mtime for f in d.glob("*.jsonl")), default=0.0)
        except OSError:
            continue
        if at > best_at:
            best, best_at = name, at
    # Codex keeps its conversations in its own home; the Studio's index of them says when it ran
    try:
        from .config import DATA_DIR
        at = (DATA_DIR / "codex" / "projects" / f"{slug}.json").stat().st_mtime
        if at > best_at:
            best, best_at = "codex", at
    except OSError:
        pass
    return {"agent": best, "at": best_at}


def project_dir(project_id: str) -> tuple[Path, Path, str]:
    """(projects_root, this_project_dir, bare_id) — prefix-aware. The bare id is the dir
    name inside whichever engine universe (claude / kimi / qwen / a custom one) the
    prefix selects."""
    p = alt_prefix(project_id)
    if p:
        root = _alt_home(alt_homes()[p]) / "projects"
        bare = project_id[len(p):]
        return root, root / bare, bare
    root = claude_projects_dir()
    return root, root / project_id, project_id


# ---------------------------------------------------------------------------
# Transcript parsing (bounded)
# ---------------------------------------------------------------------------
def _iter_head(jsonl: Path, maxlines: int = 400):
    try:
        with open(jsonl, encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i > maxlines:
                    break
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
    except OSError:
        return


# Weight limits for one transcript line. A big line is almost always a tool_result carrying a
# base64 screenshot the agent Read: the feed renders NOTHING for those (``_result_text`` returns
# "" for an image block), so they are pure weight. Measured on a real project: 128 such lines held
# 125.7 MB of a 133.5 MB transcript — 94% — and the remaining 6% carried every prompt and answer.
# Size ALONE is not the test, because a large Read of a real file is genuine content the feed does
# show. So a big line is only dropped when it actually looks like an image payload.
_MAX_ENTRY_BYTES = 192 * 1024            # under this, always keep
_HARD_ENTRY_BYTES = 4 * 1024 * 1024      # over this, drop whatever it is (nothing renders it)
_IMG_MARKS = (b'"type": "image"', b'"type":"image"', b"base64")


def _is_ballast(raw: bytes) -> bool:
    """Is this oversized line an embedded image rather than readable content?
    A substring scan over a megabyte is a C-level memchr — cheap next to parsing it."""
    n = len(raw)
    if n <= _MAX_ENTRY_BYTES:
        return False
    if n > _HARD_ENTRY_BYTES:
        return True
    return any(m in raw for m in _IMG_MARKS)

_LINE_IDX: dict[str, dict] = {}      # path -> {"at": bytes indexed, "lines": [(offset, length)]}
# Evict ONE, oldest first — never wipe the lot. With 45 workspaces on this machine the old cap of
# 8 plus a full `.clear()` meant that touching a ninth transcript threw away the index for every
# other one, including the 100 MB session being polled twice a second, which then had to be
# rebuilt from nothing. An index is small (101 MB of transcript indexes to 15,225 useful lines),
# so holding more of them costs almost nothing and holding fewer costs a rescan.
_LINE_IDX_MAX = 64
# One lock per transcript. The feed is a sync route, so it is served from a thread pool, and two
# panes (or the rail and a pane) poll the same transcript at once. Both read `at`, both scanned
# the same new bytes, and both appended them: an answer drawn twice with the same timestamp, until
# the backend restarted, because the index never forgets a line. Eight threads on 20,010 new lines
# indexed 160,010. Per file, so the first scan of a 500 MB transcript holds nobody else up.
_LINE_LOCKS: dict[str, threading.Lock] = {}
_LINE_LOCKS_GUARD = threading.Lock()


def _line_lock(key: str) -> threading.Lock:
    with _LINE_LOCKS_GUARD:
        lk = _LINE_LOCKS.get(key)
        if lk is None:
            lk = _LINE_LOCKS[key] = threading.Lock()
        return lk


def _useful_lines(p: Path, size: int) -> list[tuple[int, int]]:
    """(offset, length) of every line worth parsing, built ONCE per file and then extended.

    Only the bytes appended since the last call are scanned, so an active session costs the size
    of its new messages, not the size of its transcript.

    NO LONGER ON THE REQUEST PATH. Building it costs the whole file once — 1,418 ms for a 1 GB
    transcript — and that bill came due again after every restart, which is what made the first
    chat opened after one feel slow. `_tail_useful` reads the end instead. This is kept as the
    reference the test compares that reader against, line for line.
    """
    key = str(p)
    with _line_lock(key):
        st = _LINE_IDX.get(key)
        if st is None or st["at"] > size:            # new file, or rotated/truncated → start over
            st = {"at": 0, "lines": []}
        if size > st["at"]:
            try:
                with open(p, "rb") as fh:
                    fh.seek(st["at"])
                    pos = st["at"]
                    for raw in fh:
                        if not raw.endswith(b"\n"):
                            break                    # half-written final line — re-read it next time
                        n = len(raw)
                        if raw.strip() and not _is_ballast(raw):
                            st["lines"].append((pos, n))
                        pos += n
                    st["at"] = pos
            except OSError:
                return st["lines"]
        with _LINE_LOCKS_GUARD:
            if key not in _LINE_IDX and len(_LINE_IDX) >= _LINE_IDX_MAX:
                _LINE_IDX.pop(next(iter(_LINE_IDX)), None)     # oldest out, the rest keep their index
            _LINE_IDX.pop(key, None)                           # re-insert so this one is now the newest
            _LINE_IDX[key] = st
        return st["lines"]


_TAIL_CHUNK = 1 << 20          # how much is read per step when walking backwards


def _tail_useful(p: Path, size: int, budget: int) -> list[tuple[int, int]]:
    """(offset, length) of the newest useful lines, read BACKWARDS until the budget is met.

    This used to ask `_useful_lines`, which indexes the WHOLE file and then throws away everything
    but the end. That is fine while the index lives, and it is why the chat felt instant after the
    first look — but the index dies with the process. Measured after a restart: the first feed of a
    project holding a 1 GB transcript took 6.6 s, because 1 GB was read to find the last 150 lines.
    Reading from the end costs the tail and nothing else: the same answer, in milliseconds.

    A half-written final line is skipped, exactly as the forward scan skipped it, so a line is only
    ever shown once it is complete.
    """
    out: list[tuple[int, int]] = []
    if size <= 0:
        return out
    total = 0
    try:
        with open(p, "rb") as fh:
            end = size
            tail = b""              # bytes already read that belong to a line not yet complete
            tail_at = size          # the offset `tail` starts at
            while end > 0:
                start = max(0, end - _TAIL_CHUNK)
                fh.seek(start)
                buf = fh.read(end - start) + tail
                tail_at = start
                end = start
                # Every complete line inside `buf`, newest first. `buf[:cut]` is the head of a line
                # that began before this chunk: it is carried into the next (earlier) read. With no
                # newline at all, the WHOLE chunk is such a head — cut must be its length, or the
                # fragment would be read as a line of its own and its real start would be lost.
                if start > 0:
                    nl0 = buf.find(b"\n")
                    cut = (nl0 + 1) if nl0 >= 0 else len(buf)
                else:
                    cut = 0
                pos = len(buf)
                while pos > cut:
                    nl = buf.rfind(b"\n", cut, pos - 1)
                    line_start = nl + 1 if nl >= 0 else cut
                    raw = buf[line_start:pos]
                    if raw.endswith(b"\n"):     # a line without its newline is still being written
                        if raw.strip() and not _is_ballast(raw):
                            out.append((tail_at + line_start, len(raw)))
                            total += len(raw)
                            if total >= budget:
                                out.reverse()
                                return out
                    pos = line_start
                tail = buf[:cut]
    except OSError:
        pass
    out.reverse()
    return out


def _tail_entries(jsonl: Path, kb: int = 96) -> list[dict]:
    """The newest entries, budgeting on USEFUL bytes instead of raw file bytes.

    Seeking back `kb` raw kilobytes assumed a transcript is mostly conversation. It is not: one
    screenshot the agent Read is a single ~1.2 MB line, and a project with a few dozen of them is
    almost entirely image payload. The old budget therefore bought almost no history — a 133 MB
    transcript holding 10 prompts showed ONE, and the rest looked deleted. Skipping the oversized
    lines (which display as nothing anyway) makes the same budget reach far further back."""
    try:
        size = jsonl.stat().st_size
    except OSError:
        return []
    want = _tail_useful(jsonl, size, kb * 1024)
    out: list[dict] = []
    try:
        with open(jsonl, "rb") as fh:
            for off, n in want:
                fh.seek(off)
                line = fh.read(n).strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return out


def _text_of(o: dict) -> str:
    m = o.get("message") or {}
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                return b.get("text", "")
    return ""


def _meta_from_head(jsonl: Path) -> dict:
    out: dict = {}
    for o in _iter_head(jsonl):
        if o.get("cwd") and "cwd" not in out:
            out["cwd"] = o["cwd"]
            out["gitBranch"] = o.get("gitBranch", "")
            out["version"] = o.get("version", "")
            break
    return out


def _summary_and_todos(entries: list[dict], project_id: str = "",
                       session_ids: Optional[set] = None) -> dict:
    """Card summary + the phase counter. The counter goes through the same resolver the Phases
    panel uses, so a card and the panel it opens can never disagree."""
    res = {"summary": "", "todos_total": 0, "todo_done": 0, "todo_in_progress": ""}
    for o in reversed(entries):
        t = _text_of(o)
        if t and o.get("type") in ("assistant", "user"):
            res["summary"] = " ".join(t.split())[:160]
            break
    ph = resolve_phases(project_id, entries, session_ids or set())
    res["todos_total"] = ph["total"]
    res["todo_done"] = ph["done"]
    ip = [x for x in ph["todos"] if x["status"] == "in_progress"]
    if ip:
        res["todo_in_progress"] = (ip[0]["active_form"] or ip[0]["content"])[:120]
    return res


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
def _git(path: str, args: list[str], timeout: float = 4.0) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# --- which conversation is "the" one for a project ---------------------------
# The Studio used to continue whichever transcript was touched LAST. That means any
# other tool working in the same folder (e.g. the VS Code Claude panel used to rescue
# the app after a crash) silently hijacks which conversation the Studio resumes — the
# user then lands in an old thread with none of their recent history. So we pin the
# conversation the STUDIO itself is using and prefer it; newest is only the fallback
# (first ever run, or the pinned conversation was deleted).
_ACTIVE_F = DATA_DIR / "sessions" / "active_session.json"
_active_lock = threading.Lock()
_active_cache: dict = {"mtime": -1.0, "map": {}}


def _load_active() -> dict:
    try:
        mt = _ACTIVE_F.stat().st_mtime
        if mt != _active_cache["mtime"]:
            data = json.loads(_ACTIVE_F.read_text(encoding="utf-8"))
            _active_cache["map"] = data if isinstance(data, dict) else {}
            _active_cache["mtime"] = mt
    except Exception:
        pass
    return _active_cache["map"]


def remember_active_session(project_id: str, session_id: str) -> None:
    """Pin the conversation the Studio is actually running for this project."""
    if not project_id or not session_id:
        return
    with _active_lock:
        m = dict(_load_active())
        if m.get(project_id) == session_id:
            return
        m[project_id] = session_id
        try:
            _ACTIVE_F.parent.mkdir(parents=True, exist_ok=True)
            tmp = _ACTIVE_F.with_suffix(".tmp")
            tmp.write_text(json.dumps(m), encoding="utf-8")
            fsutil.replace(tmp, _ACTIVE_F)
            _active_cache["map"] = m
            _active_cache["mtime"] = _ACTIVE_F.stat().st_mtime
        except OSError:
            pass


def active_session_id(project_id: str) -> Optional[str]:
    """The pinned conversation id — only if its transcript still exists."""
    sid = _load_active().get(project_id)
    if not sid:
        return None
    _, pdir, _ = project_dir(project_id)
    return sid if (pdir / f"{sid}.jsonl").exists() else None


def active_transcript(project_id: str, jsonls: list) -> Optional[Path]:
    """The transcript the Studio should read/continue: pinned when valid, else newest."""
    if not jsonls:
        return None
    sid = active_session_id(project_id)
    if sid:
        for f in jsonls:
            if f.stem == sid:
                return f
    return max(jsonls, key=lambda f: f.stat().st_mtime)


_GIT_TTL = perf.Ttl(15.0, limit=128)


def _git_info(path: str) -> dict:
    """Branch, last commit and dirty count for one repo — three `git` processes.

    Kept for 15 seconds per repo. The overview scan asks for every project it lists, so this was
    three processes per repo per scan; the column it feeds does not change faster than that. The
    source-control panel does its own, always-fresh, git calls — this is only the summary line.
    """
    return _GIT_TTL.get(str(path), lambda: _git_info_build(path))


def _git_info_build(path: str) -> dict:
    if not path or not Path(path).exists() or not (Path(path) / ".git").exists():
        return {}
    branch = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    last = _git(path, ["log", "-1", "--format=%h|%s|%cr"])
    dirty = _git(path, ["status", "--porcelain"])
    info: dict = {"git_branch": branch}
    if last and "|" in last:
        h, msg, rel = (last.split("|", 2) + ["", "", ""])[:3]
        info["git_last_commit"] = f"{h} {msg}"[:120]
        info["git_last_commit_rel"] = rel
    info["git_dirty"] = len([l for l in dirty.splitlines() if l.strip()]) if dirty else 0
    return info


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def repair_cwd(project_id: str, cwd: str) -> str:
    """A renamed project's transcripts carry the OLD cwd in their heads FOREVER (resumed
    sessions only append; the head never changes). When the recorded cwd is gone, recover
    the real folder from the opened-roots setting by matching the transcript-dir slug —
    rename_root migrates the transcript dir to the new slug and registers the new path."""
    if cwd and Path(cwd).exists():
        return cwd
    try:
        from .config import settings
        from .workspace import _claude_id
        for r in (settings.get("workspace_roots") or []):
            if _claude_id(str(r)).lower() == (project_id or "").lower() and Path(r).exists():
                return str(r)
    except Exception:
        pass
    return cwd


# --- the light index: which folders exist, and where each one lives ---------------------------
#
# WHY THIS EXISTS. `overview()` is the rich answer — git, summaries, phases, context — and it costs
# 0.5-1.1 s for 52 projects. Three callers never wanted any of that: the explorer's path check, the
# workspace list, and the graph watcher only need id -> folder. They called `overview()` anyway, its
# cache is three seconds old, and so a full scan of every project ran about every three seconds for
# as long as a window was open. Measured on the real UI, that is what made `/workspace/tree` take
# 662 ms and `/workspace/changes` 680 ms during a workspace switch.
#
# This builds the same list with one directory read and a stat per transcript. The head of a
# transcript — the only expensive part — is read once per project and re-read only when that
# project's newest transcript changes.
_IDX_ONE = perf.Memo(limit=512)
_IDX_ALL = perf.Ttl(3.0)


def _roots_stamp() -> int:
    """Changes when the opened-folder list changes, so a renamed project is repaired, not cached."""
    try:
        return hash(tuple(settings.get("workspace_roots") or []))
    except Exception:
        return 0


def _index_one(pdir: Path) -> Optional[dict]:
    try:
        jsonls = list(pdir.glob("*.jsonl"))
    except OSError:
        return None
    last, newest = 0.0, None
    for f in jsonls:
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        if mt > last:
            last, newest = mt, f
    stamp = (newest.name if newest else "", int(last * 1000), len(jsonls), _roots_stamp())

    def build() -> dict:
        meta = _meta_from_head(newest) if newest else {}
        cwd = repair_cwd(pdir.name, meta.get("cwd", ""))
        return {"id": pdir.name, "path": cwd, "name": (Path(cwd).name if cwd else pdir.name)}

    row = dict(_IDX_ONE.get(str(pdir), stamp, build))
    row["last_activity"] = last
    row["sessions"] = len(jsonls)
    return row


def project_index(fresh: bool = False) -> list[dict]:
    """Every Claude project folder as ``{id, path, name, last_activity, sessions}``, newest first.

    The cheap half of :func:`overview`: no git, no transcript tails, no phases, no context. Callers
    that only need to know WHERE a project is use this, so that no request can start a full scan.
    `fresh=True` rebuilds now — used by the path check before it refuses a folder, so a project
    opened one second ago is never called unknown.
    """
    def build() -> list[dict]:
        root = claude_projects_dir()
        rows: list[dict] = []
        try:
            pdirs = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            pdirs = []
        for d in pdirs:
            row = _index_one(d)
            if row:
                rows.append(row)
        # Same dedupe rule as the overview: one folder, the liveliest record of it.
        by: dict[str, dict] = {}
        for r in rows:
            k = (r["path"] or r["id"]).lower()
            cur = by.get(k)
            if cur is None or r["last_activity"] > cur["last_activity"]:
                by[k] = r
        return sorted(by.values(), key=lambda r: r["last_activity"], reverse=True)

    if fresh:
        _IDX_ALL.drop("all")
    return _IDX_ALL.get("all", build)


def _scan_one_project(pdir: Path) -> Optional[CCProject]:
    jsonls = list(pdir.glob("*.jsonl"))
    sess_dirs = [d for d in pdir.iterdir() if d.is_dir() and d.name != "memory"]

    # discover real cwd (from main jsonl head, else any agent transcript)
    meta: dict = {}
    newest = max(jsonls, key=lambda f: f.stat().st_mtime) if jsonls else None
    if newest:
        meta = _meta_from_head(newest)
    if "cwd" not in meta:
        for sd in sess_dirs:
            for ag in sd.rglob("agent-*.jsonl"):
                meta = _meta_from_head(ag)
                if "cwd" in meta:
                    break
            if "cwd" in meta:
                break

    cwd = repair_cwd(pdir.name, meta.get("cwd", ""))
    last_mtime = max((f.stat().st_mtime for f in jsonls), default=0.0)
    # also consider session-dir activity (agents) for last_activity
    for sd in sess_dirs:
        try:
            last_mtime = max(last_mtime, sd.stat().st_mtime)
        except OSError:
            pass

    name = Path(cwd).name if cwd else pdir.name
    proj = CCProject(
        id=pdir.name,
        path=cwd,
        name=name or pdir.name,
        exists=bool(cwd) and Path(cwd).exists(),
        last_activity=last_mtime,
        active=(time.time() - last_mtime) < PROJECT_ACTIVE_WINDOW,
        sessions=len(jsonls),
        size_bytes=sum((f.stat().st_size for f in jsonls), 0),
        cc_version=meta.get("version", ""),
    )

    if newest:
        # The cached reader: keyed on the transcript's own size and mtime, so an unchanged project
        # is not re-read and re-parsed on every scan. Any write changes the key.
        tail = _tail_entries_cached(newest, kb=96)
        st = _summary_and_todos(tail, proj.id, {f.stem for f in jsonls})
        proj.last_summary = st["summary"]
        proj.todos_total = st["todos_total"]
        proj.todo_done = st["todo_done"]
        proj.todo_in_progress = st["todo_in_progress"]
        proj.awaiting_input = _awaiting_input(tail)
        proj.working = _is_working(tail, last_mtime, proj.awaiting_input, proj.id)
        for k, v in _context_info(tail).items():
            setattr(proj, k, v)

    if proj.exists:
        for k, v in _git_info(cwd).items():
            setattr(proj, k, v)

    return proj


def _scan_agents(pdirs: list[Path], proj_by_id: dict[str, CCProject]) -> list[CCAgent]:
    now = time.time()
    agents: list[CCAgent] = []
    for pdir in pdirs:
        # The shared definition, so Mission Control agrees with the rail, the bar and the chat.
        # One call per project, and it costs a scandir when nothing is running - which is the
        # answer almost every time.
        try:
            from . import subagents
            live_ids = {a.get("agent_id") for a in subagents.working(pdir.name)}
        except Exception:
            live_ids = None
        for sd in pdir.iterdir():
            if not sd.is_dir() or sd.name == "memory":
                continue
            for ag in sd.rglob("agent-*.jsonl"):
                try:
                    mt = ag.stat().st_mtime
                except OSError:
                    continue
                if live_ids is not None:
                    if ag.stem[len("agent-"):] not in live_ids:
                        continue
                elif now - mt > AGENT_ACTIVE_WINDOW:
                    continue
                # The name it was given, from the .meta.json beside it. Its first prompt is the
                # fallback, and for a Workflow agent that is the harness preamble, not a name.
                try:
                    label = " ".join(str(subagents.agent_meta(ag).get("description") or "").split())[:120]
                except Exception:
                    label = ""
                if not label:
                    for o in _iter_head(ag, 30):
                        if o.get("type") == "user":
                            label = " ".join(_text_of(o).split())[:120]
                            if label:
                                break
                pj = proj_by_id.get(pdir.name)
                agents.append(CCAgent(
                    project_id=pdir.name,
                    project_name=pj.name if pj else pdir.name,
                    session=sd.name[:8],
                    agent_id=ag.stem.replace("agent-", "")[:12],
                    last_update=mt,
                    age_seconds=now - mt,
                    label=label,
                ))
    agents.sort(key=lambda a: a.last_update, reverse=True)
    return agents


_DEV_SERVER_HINTS = (
    "node", "python", "deno", "bun", "php", "ruby", "dotnet", "go.exe",
    "cargo", "caddy", "nginx", "http-server", "vite", "next", "uvicorn", "gunicorn",
)


def _broad_dirs() -> set[Path]:
    h = Path.home()
    dirs = {h, Path(h.anchor)} if h.anchor else {h}
    for sub in ("Downloads", "Desktop", "Documents", "Music", "Pictures", "Videos"):
        dirs.add(h / sub)
    return {d.resolve() for d in dirs}


def _scan_ports(projects: list[CCProject], self_port: int = 8777) -> list[CCPort]:
    try:
        import psutil
    except Exception:
        return []
    broad = _broad_dirs()
    resolved = []
    for p in projects:
        if p.path:
            try:
                rp = Path(p.path).resolve()
                resolved.append((p, rp, rp in broad))
            except Exception:
                pass

    def match(cwd: Optional[str]) -> Optional[CCProject]:
        if not cwd:
            return None
        try:
            c = Path(cwd).resolve()
        except Exception:
            return None
        best, best_depth = None, -1
        for p, pp, is_broad in resolved:
            ok = c == pp or (not is_broad and pp in c.parents and _depth_below(pp, c) <= 2)
            if ok and len(pp.parts) > best_depth:  # deepest (most specific) root wins
                best, best_depth = p, len(pp.parts)
        return best

    out: list[CCPort] = []
    seen: set[int] = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception:
        return []
    for c in conns:
        if c.status != "LISTEN" or not c.laddr:
            continue
        port = c.laddr.port
        if port <= 1024 or port in seen:
            continue
        proc_name = ""
        cwd = None
        if c.pid:
            try:
                pr = psutil.Process(c.pid)
                proc_name = pr.name()
                cwd = pr.cwd()
            except Exception:
                pass
        pj = match(cwd)
        looks_dev = any(h in proc_name.lower() for h in _DEV_SERVER_HINTS)
        # keep project-matched servers + the studio itself + likely dev servers; drop the rest as noise
        if pj is None and port != self_port and not looks_dev:
            continue
        seen.add(port)
        out.append(CCPort(
            port=port, pid=c.pid or 0, process=proc_name, cwd=cwd,
            project_id=pj.id if pj else None,
            project_name=pj.name if pj else None,
            is_self=(port == self_port),
            url=f"http://localhost:{port}",
        ))
    out.sort(key=lambda p: (p.project_id is None, p.port))
    return out


def _depth_below(ancestor: Path, child: Path) -> int:
    try:
        return len(child.relative_to(ancestor).parts)
    except Exception:
        return 999


def overview(force: bool = False) -> dict:
    if not force and _CACHE["data"]:
        age = time.time() - _CACHE["ts"]
        if age < _CACHE_TTL:
            return _CACHE["data"]
        if age < _CACHE_HARD_TTL:
            _refresh_overview_async()      # serve what we have NOW, catch up in the background
            return _CACHE["data"]

    # Single-flight the COLD build as well.
    #
    # The warm path has been single-flight for a while (_refresh_overview_async), but with an
    # EMPTY cache every caller fell straight through and ran the whole scan itself. On a freshly
    # started backend three of them arrive within a breath of each other — the startup warm-up,
    # the workspace list the window is waiting on, and the worktree map — so the same scan of
    # fifty project directories ran three times at once, 16 threads each, on a machine already
    # busy with agents. That is the minute of "pick a project".
    #
    # One computes; the rest wait on this lock and are handed the same result.
    with _build_lock:
        if not force and _CACHE["data"] and time.time() - _CACHE["ts"] < _CACHE_TTL:
            return _CACHE["data"]
        return _overview_build()


def _overview_build() -> dict:
    root = claude_projects_dir()
    if not root.exists():
        data = {"projects": [], "agents": [], "ports": [], "counts": {
            "projects": 0, "active_projects": 0, "active_agents": 0, "running_servers": 0}}
        _CACHE.update(ts=time.time(), data=data)
        return data

    pdirs = [d for d in root.iterdir() if d.is_dir()]
    with ThreadPoolExecutor(max_workers=min(16, len(pdirs) or 1)) as ex:
        scanned = [p for p in ex.map(_scan_one_project, pdirs) if p]

    # dedupe by resolved real path (capital/lowercase drive encodings collide)
    by_key: dict[str, CCProject] = {}
    for p in scanned:
        key = p.path.lower() if p.path else p.id.lower()
        cur = by_key.get(key)
        if cur is None or p.last_activity > cur.last_activity:
            if cur:
                p.sessions += cur.sessions
            by_key[key] = p
        else:
            cur.sessions += p.sessions
    projects = sorted(by_key.values(), key=lambda p: p.last_activity, reverse=True)

    proj_by_id = {p.id: p for p in projects}
    agents = _scan_agents(pdirs, proj_by_id)
    # attach active-agent counts to projects
    for a in agents:
        if a.project_id in proj_by_id:
            proj_by_id[a.project_id].agents_active += 1

    # drop empty orphan dirs (no resolvable path, no sessions, no live agents)
    projects = [p for p in projects if p.path or p.sessions > 0 or p.agents_active > 0]

    ports = _scan_ports(projects)
    for pt in ports:
        if pt.project_id and pt.project_id in proj_by_id and not proj_by_id[pt.project_id].port:
            proj_by_id[pt.project_id].port = pt.port
            proj_by_id[pt.project_id].url = pt.url

    data = {
        "projects": [p.model_dump() for p in projects],
        "agents": [a.model_dump() for a in agents],
        "ports": [pt.model_dump() for pt in ports],
        "counts": {
            "projects": len(projects),
            "active_projects": sum(1 for p in projects if p.active),
            "active_agents": len(agents),
            "running_servers": sum(1 for pt in ports if pt.project_id),
        },
        "generated_at": time.time(),
    }
    _CACHE.update(ts=time.time(), data=data)
    return data


# ---------------------------------------------------------------------------
# Who needs you
# ---------------------------------------------------------------------------
# Three states and no more, because a fourth one would have to be guessed. BLOCKED is the only
# one that is a claim about the user: the last turn ended on a question, so nothing moves until
# somebody answers. WORKING is the transcript being written, or a subagent of this project still
# running. Everything else is IDLE — finished, or never started. "Just finished, come and look"
# was left out on purpose: without a record of what you have already read it is indistinguishable
# from "finished an hour ago", and a status line that cries wolf is worse than no status line.
ATTENTION_STATES = ("blocked", "working", "idle")

_ATTN = perf.Ttl(4.0, limit=2)


def _attention_build() -> dict:
    ov = overview()
    now = time.time()
    rows: list[dict] = []
    idle = 0
    agents = 0
    for p in ov.get("projects") or []:
        if not p.get("path"):
            continue                       # a transcript folder whose project is gone
        a = int(p.get("agents_active") or 0)
        agents += a
        if p.get("awaiting_input"):
            state = "blocked"
        elif p.get("working") or a > 0:
            state = "working"
        else:
            idle += 1
            continue                       # the quiet ones are a number, not a list
        rows.append({
            "id": p.get("id", ""),
            "path": p.get("path", ""),
            "name": p.get("name") or "",
            "state": state,
            "agents": a,
            "since": max(0.0, now - float(p.get("last_activity") or now)),
            "why": _oneline(p.get("last_summary") or "", 160),
            "todo": _oneline(p.get("todo_in_progress") or "", 90),
        })
    # Blocked first, then whichever has been waiting longest — the order you would work through
    # them. Sorting by name instead would put the same project at the top every day.
    rows.sort(key=lambda r: (r["state"] != "blocked", -r["since"]))
    return {
        "ok": True,
        "at": now,
        "counts": {
            "blocked": sum(1 for r in rows if r["state"] == "blocked"),
            "working": sum(1 for r in rows if r["state"] == "working"),
            "idle": idle,
            "agents": agents,
        },
        "rows": rows[:24],
    }


def attention() -> dict:
    """Who is blocked, who is working, and how many are quiet — across every project.

    Built from the overview the Studio already keeps, so this starts NO scan of its own: it reads
    the same cached answer Mission Control reads, and `overview()` serves that answer immediately
    and catches up in the background. Its own four-second cache is what stops two windows, or two
    panes, paying for the same walk twice.
    """
    return _ATTN.get("all", _attention_build)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _oneline(text: str, n: int = 280) -> str:
    return " ".join((text or "").split())[:n]


def _clean(text: str, cap: int = 8000) -> str:
    """Full message text: keep line breaks, collapse only runs of spaces/blank lines."""
    t = re.sub(r"[ \t]+", " ", (text or "").strip())
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t[:cap]


# CSI / OSC escapes + stray control chars. Without this, coloured CLI output (npm, pip, git)
# reaches the feed as literal "[32m…[39m" garbage next to an unprintable box glyph.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_out(text: str, cap: int = 4000) -> str:
    """Cleaner for tool OUTPUT (bash stdout, file reads, grep hits).

    Deliberately NOT `_clean`: that collapses every run of spaces/tabs, which flattens the
    indentation of any code it passes through — the reason file reads looked shapeless in the
    feed. Here indentation is preserved and only ANSI/control noise is removed."""
    t = _ANSI_RE.sub("", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)          # trailing whitespace only
    t = re.sub(r"\n{3,}", "\n\n", t).strip("\n")
    return t[:cap]


def _clean_md(text: str, cap: int = 24000) -> str:
    """Cleaner for MESSAGE text (mine and yours).

    Two reasons this is not `_clean`:
      * `_clean` collapses every run of spaces/tabs, which flattens fenced code blocks and the
        leading indentation that nested bullets are detected from — the markdown arrives shapeless.
      * its 8000-char cap silently swallowed the tail of long answers (19k-char replies were cut
        with no indication, so the answer merely *looked* incomplete).
    Here whitespace is preserved and an over-cap cut is announced instead of hidden."""
    t = _ANSI_RE.sub("", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{4,}", "\n\n\n", t).strip("\n")
    if len(t) > cap:
        t = t[:cap].rstrip() + "\n\n… (truncated for display — the full text is in the transcript)"
    return t


def _tool_brief(block: dict) -> str:
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    for key in ("command", "file_path", "path", "pattern", "prompt", "query", "url", "description"):
        if isinstance(inp.get(key), str) and inp[key].strip():
            return f"{name}: {_oneline(inp[key], 400)}"
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return f"{name}: {_oneline(v, 400)}"
    return name


def _diff_stat(old: str, new: str, cap: int = 36, ctx: int = 3):
    """Unified-style rows: changed lines PLUS up to `ctx` unchanged lines around each change,
    so an edit reads in place instead of a wall of red followed by a wall of green.

    Every row carries line numbers — `n` = line in the OLD file, `m` = line in the NEW file —
    and a {"t": "gap"} row marks unchanged lines that were elided between hunks. `added` /
    `removed` remain exact totals even when the row list is capped."""
    import difflib

    ol = (old or "").split("\n")
    nl = (new or "").split("\n")
    added = removed = 0
    rows: list[dict] = []

    def push(row: dict) -> None:
        if len(rows) < cap:
            rows.append(row)

    ops = difflib.SequenceMatcher(a=ol, b=nl).get_opcodes()
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            seg = ol[i1:i2]
            n = len(seg)
            first, last = k == 0, k == len(ops) - 1
            if first and last:
                continue                       # identical text — nothing to show

            def emit(rng) -> None:             # old/new indices advance together in an equal run
                for t in rng:
                    push({"t": "ctx", "s": seg[t][:260], "n": i1 + t + 1, "m": j1 + t + 1})

            if first:                          # only the tail of the run leads into the change
                start = max(0, n - ctx)
                if start > 0:
                    push({"t": "gap"})
                emit(range(start, n))
            elif last:                         # only the head of the run trails the change
                emit(range(0, min(ctx, n)))
                if n > ctx:
                    push({"t": "gap"})
            elif n > ctx * 2:                  # between two changes — keep both edges
                emit(range(0, ctx))
                push({"t": "gap"})
                emit(range(n - ctx, n))
            else:
                emit(range(0, n))
            continue
        if tag in ("replace", "delete"):
            for t, ln in enumerate(ol[i1:i2]):
                removed += 1
                push({"t": "del", "s": ln[:260], "n": i1 + t + 1})
        if tag in ("replace", "insert"):
            for t, ln in enumerate(nl[j1:j2]):
                added += 1
                push({"t": "add", "s": ln[:260], "m": j1 + t + 1})
    return added, removed, rows


def _tool_event(b: dict, ts: str) -> dict:
    name = b.get("name", "tool")
    inp = b.get("input") or {}
    ev: dict = {"kind": "tool", "ts": ts, "tool": name, "title": name, "icon": "tool"}
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        ev["title"] = "Write" if name == "Write" else "Edit"
        ev["subtitle"] = inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or ""
        ev["icon"] = "edit"
        if name == "Write":
            lines = (inp.get("content", "") or "").split("\n")
            ev["diff"] = {"added": len(lines), "removed": 0,
                          "hunks": [{"t": "add", "s": l[:260], "m": i + 1}
                                    for i, l in enumerate(lines[:24])]}
        elif name == "MultiEdit":
            a = r = 0
            hunks: list[dict] = []
            for e in inp.get("edits", []) or []:
                da, dr, hh = _diff_stat(e.get("old_string", ""), e.get("new_string", ""))
                a += da
                r += dr
                for h in hh:
                    if len(hunks) < 20:
                        hunks.append(h)
            ev["diff"] = {"added": a, "removed": r, "hunks": hunks}
        else:
            da, dr, hh = _diff_stat(inp.get("old_string", ""), inp.get("new_string", ""))
            ev["diff"] = {"added": da, "removed": dr, "hunks": hh}
    elif name in ("Bash", "PowerShell"):
        ev["title"] = name
        ev["icon"] = "terminal"
        ev["command"] = _clean(inp.get("command", ""), 4000)
        ev["subtitle"] = _clean(inp.get("description", ""), 160)
        # A ten-minute sweep looked exactly like `ls`, and a backgrounded job never visibly
        # ended. Both are one field in the tool input that nothing was reading.
        if inp.get("run_in_background"):
            ev["bg"] = True
        try:
            ms = int(inp.get("timeout") or 0)
        except (TypeError, ValueError):
            ms = 0
        if ms >= 120000:
            ev["slow"] = max(1, ms // 60000)
    elif name in ("Read", "Grep", "Glob"):
        ev["title"] = name
        ev["icon"] = "search"
        ev["subtitle"] = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
    elif name in ("TodoWrite", "todo_write"):
        # `todo_write` is the DeepSeek Harness runtime's phase tool (`{todos: [{content, status}]}`,
        # the same shape as Claude's TodoWrite), and its pane showed an empty Phases panel because
        # only the Claude spelling was listed. Read out of the installed runtime's tool catalog
        # rather than guessed: ask_user_question, bash, cordis_*, edit, glob, grep, present, read,
        # read_image, skill, todo_write, web_fetch, web_search, write.
        ev["title"] = "Phases"
        ev["icon"] = "check"
        ev["todos"] = [{"content": t.get("content", ""), "status": t.get("status", "")}
                       for t in (inp.get("todos") or [])][:14]
    elif name in ("TaskCreate", "TaskUpdate"):
        # One phase per call, so this is a line rather than the whole checklist TodoWrite drew.
        ev["title"] = "Phase" if name == "TaskCreate" else "Phase update"
        ev["icon"] = "check"
        status = str(inp.get("status") or "")
        subject = str(inp.get("subject") or inp.get("activeForm") or "")
        if not subject and inp.get("taskId"):
            subject = "#" + str(inp["taskId"])
        ev["subtitle"] = _clean((subject + (" - " + status if status else "")).strip(), 200)
    elif name in ("Task", "Agent"):
        ev["title"] = "Task"
        ev["icon"] = "bot"
        ev["subtitle"] = _clean(inp.get("description") or inp.get("prompt", ""), 220)
    elif name in ("WebFetch", "WebSearch"):
        ev["title"] = name
        ev["icon"] = "globe"
        ev["subtitle"] = inp.get("url") or inp.get("query") or ""
    elif name in ("TaskStop", "TaskOutput", "KillShell", "BashOutput"):
        ev["title"] = {"TaskStop": "Stop task", "TaskOutput": "Task output",
                       "KillShell": "Kill shell", "BashOutput": "Shell output"}[name]
        ev["icon"] = "terminal"
        ev["subtitle"] = _clean(str(inp.get("task_id") or inp.get("shell_id")
                                    or inp.get("bash_id") or ""), 80)
    elif name == "ToolSearch":
        ev["title"] = "Tool search"
        ev["icon"] = "search"
        ev["subtitle"] = _clean(str(inp.get("query") or ""), 120)
    else:
        ev["subtitle"] = _tool_brief(b)
    return ev


def _question_event(b: dict, ts: str) -> dict:
    """Render AskUserQuestion / ExitPlanMode as a first-class 'question' event."""
    name = b.get("name", "")
    inp = b.get("input") or {}
    if name == "ExitPlanMode":
        return {"kind": "question", "ts": ts, "tool": name,
                "text": _clean(inp.get("plan", "") or "Plan ready — approve to proceed?", 2000),
                "options": [{"label": "Approve", "description": "proceed with the plan"},
                            {"label": "Keep planning", "description": "revise the plan"}]}
    parts: list[str] = []
    options: list[dict] = []
    for q in inp.get("questions", []) or []:
        if isinstance(q, dict):
            if q.get("question"):
                parts.append(q["question"])
            for o in q.get("options", []) or []:
                if isinstance(o, dict):
                    options.append({"label": o.get("label", ""), "description": o.get("description", "")})
                elif isinstance(o, str):
                    options.append({"label": o, "description": ""})
    return {"kind": "question", "ts": ts, "tool": name,
            "text": _clean("  •  ".join(p for p in parts if p) or "(question)", 1200),
            "options": options[:8]}


def _awaiting_input(entries: list[dict]) -> bool:
    """True if the latest conversational turn is an unanswered question."""
    last = None
    for o in entries:
        typ = o.get("type")
        c = (o.get("message") or {}).get("content")
        if typ == "user":
            last = "user"  # a real answer or a tool_result both clear the wait
        elif typ == "assistant":
            if isinstance(c, list):
                if any(isinstance(x, dict) and x.get("type") == "tool_use"
                       and x.get("name") in ("AskUserQuestion", "ExitPlanMode") for x in c):
                    last = "question"
                else:
                    txts = [x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"]
                    if txts and txts[-1].strip().endswith("?"):
                        last = "question"
                    elif txts or any(isinstance(x, dict) and x.get("type") == "tool_use" for x in c):
                        last = "assistant"
            elif isinstance(c, str) and c.strip():
                last = "question" if c.strip().endswith("?") else "assistant"
    return last == "question"


def _is_working(entries: list[dict], last_mtime: float, awaiting: bool, project_id: str = "") -> bool:
    """Best-effort 'generating right now' signal.

    Uses the *shape* of the latest turn rather than only file mtime, so the
    indicator doesn't flicker off during a long think or a slow tool call:
      • a turn we launched from the Studio → definitely working
      • last entry is an assistant ``tool_use`` → a tool is running
      • last entry is a user row (tool_result or a fresh prompt) → assistant is replying
      • last entry is an assistant ``end_turn`` → just finished (brief tail only)
    A session parked on a question is *not* working (it's waiting on the user)."""
    if project_id:
        try:
            from . import cc_session
            if cc_session.is_sending(project_id):
                return True
        except Exception:
            pass
    if awaiting:
        return False
    age = time.time() - last_mtime
    if age > 180:  # nothing written in 3 min → idle
        return False
    # last *conversational* entry — skip metadata rows (ai-title, queue-operation…)
    last = None
    for o in reversed(entries):
        if isinstance(o, dict) and o.get("type") in ("user", "assistant"):
            last = o
            break
    if last is not None:
        typ = last.get("type")
        if typ == "assistant":
            sr = (last.get("message") or {}).get("stop_reason")
            if sr == "end_turn":
                return age < 6         # finished — keep it on only for a brief tail
            return True                # tool_use / mid-stream → still generating
        if typ == "user":
            return True                # assistant is generating its reply
    return age < WORKING_WINDOW


def _turn_tokens(entries: list[dict]) -> int:
    """Output tokens generated in the current/last turn (sum of assistant message
    usage back to the user prompt that started it) — a live-ish counter like the
    VS Code extension's 'N tokens'.

    One figure per API CALL: every block of an answer is its own line, each line repeats the call's
    usage, and the early lines carry the output count as it stood then (see agent_usage). The
    largest count of a call is its real one."""
    from . import agent_usage
    per_call: dict = {}
    for o in reversed(entries):
        typ = o.get("type")
        m = o.get("message") or {}
        if typ == "assistant":
            key = agent_usage.call_key(o) or str(id(o))
            out = int((m.get("usage") or {}).get("output_tokens", 0) or 0)
            per_call[key] = max(per_call.get(key, 0), out)
        elif typ == "user":
            c = m.get("content")
            is_tr = isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
            if not is_tr:
                break  # reached the user prompt that began this turn
    return sum(per_call.values())


_PROV_WIN: tuple[float, dict[str, int]] = (0.0, {})


def _provider_window(model: str) -> int:
    """The context window a user-added provider declares for this model id, or 0.

    A third-party model name matches none of the built-in patterns below, so without this the
    meter falls back to 200k and reads as nearly full while the agent still has room. The provider
    already carries the real number — the same one the CLI is started with."""
    global _PROV_WIN
    if not model:
        return 0
    age, table = _PROV_WIN
    if time.time() - age > 5.0:      # cheap: the file is read once per scan, not once per project
        table = {}
        try:
            from . import chat_providers
            for p in chat_providers.list_for_ui():
                w = int(p.get("context_window") or 0)
                if w <= 0:
                    continue
                for m in {str(p.get("default_model") or ""), *(str(x) for x in p.get("models") or [])}:
                    if m:
                        table[m] = w
        except Exception:
            table = {}
        _PROV_WIN = (time.time(), table)
    return int(table.get(model, 0))


def model_window(model: str, used: int = 0) -> int:
    """The context window this model is running with, in tokens.

    Pulled out of ``_context_info`` so that everything which shows a fill — the session meter, a
    subagent card, the note the model is told about its own window — asks ONE function. The
    number the user reads and the number the model believes cannot then disagree, which is the
    invariant ``cc_session._context_window_note`` was written to protect.

    `used` is optional and only widens the answer: a turn that already billed more than 200k
    input proves the big window regardless of what the id looks like.
    """
    ml = (model or "").lower()
    cw = int(settings.get("context_window", 0) or 0)
    if cw <= 0:
        cw = _provider_window(model)   # a custom provider knows its own window
    if cw > 0:
        return cw
    # auto-detect: Fable/Mythos are 1M-native (the max IS the default, no suffix); Opus reaches
    # 1M via the [1m] suffix when cc_1m is on; a [1m] suffix or >200k usage also imply it.
    on_1m = (
        ("1m" in ml)
        or ("fable" in ml) or ("mythos" in ml)     # 1M-native — always the big window
        or ("kimi-k3" in ml)                       # Kimi K3 is 1M-context too
        or (used > 200_000)
        or (bool(settings.get("cc_1m", True)) and "opus" in ml)
    )
    return 1_000_000 if on_1m else 200_000


def _context_info(entries: list[dict]) -> dict:
    """Context-window fill from the latest assistant turn's token usage.

    Compaction-aware: right after a /compact the newest *assistant* turn in the
    transcript is still the pre-compact one (huge), while the live context has
    been reset to ~the summary size. Claude Code marks the boundary with a
    `isCompactSummary` user entry. So we only trust assistant usage recorded
    *after* the most recent boundary; if none exists yet (compact just ran, no
    new turn), we estimate the fresh fill from the summary + re-read attachments
    + a base overhead so the meter drops immediately instead of showing the
    stale high-water mark until the user happens to send another message."""
    # most recent compaction boundary, if any (a user entry flagged compact-summary)
    boundary = -1
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("isCompactSummary"):
            boundary = i
            break
    model = ""
    used = 0
    cache: dict[str, int] = {}
    # newest assistant usage that lands *after* the boundary (boundary=-1 ⇒ all)
    for i in range(len(entries) - 1, boundary, -1):
        o = entries[i]
        if o.get("type") != "assistant":
            continue
        m = o.get("message") or {}
        u = m.get("usage") or {}
        if u:
            fresh = int(u.get("input_tokens", 0) or 0)
            read = int(u.get("cache_read_input_tokens", 0) or 0)
            write = int(u.get("cache_creation_input_tokens", 0) or 0)
            used = fresh + read + write
            # How much of this turn's input came from the prompt cache. CLI 2.1.251 added the
            # same figure to `/cost`; it is computed here from usage the transcript already
            # carries, so the meter costs nothing to show. A cold turn reads 0 and writes the
            # lot; a warm one is the reverse, and a run of cold turns means something in the
            # prompt prefix is moving between requests.
            cache = {"cache_read": read, "cache_write": write, "fresh_in": fresh}
            model = m.get("model", "") or ""
            break
    just_compacted = False
    if used <= 0 and boundary >= 0:
        # compaction just ran and no real turn has reported usage yet — estimate
        # the post-compact fill (summary + re-read files + fixed system/tools base)
        just_compacted = True
        base = int(settings.get("compact_base_tokens", 25000) or 25000)
        body = 0
        for o in entries[boundary:]:
            if o.get("type") == "assistant":
                continue
            try:
                body += len(json.dumps(o, ensure_ascii=False))
            except Exception:
                pass
        used = base + body // 4
        for o in reversed(entries):  # best-effort model for the window sizing below
            mm = o.get("message") or {}
            if mm.get("model"):
                model = mm["model"]
                break
    if used <= 0:
        return {}
    mx = model_window(model, used)
    compact_at = float(settings.get("auto_compact_at", 0.92) or 0.92)
    frac = used / mx
    out = {
        "model": model, "ctx_used": used, "ctx_max": mx,
        "ctx_pct": round(100 * frac, 1),
        "ctx_remaining": round(max(0.0, 100 * (compact_at - frac) / compact_at), 1),
        "just_compacted": just_compacted,
    }
    if cache:
        billed = cache["cache_read"] + cache["cache_write"] + cache["fresh_in"]
        out.update(cache)
        out["cache_pct"] = round(100 * cache["cache_read"] / billed, 1) if billed else 0.0
    return out


# ---------------------------------------------------------------------------
# Build phases
# ---------------------------------------------------------------------------
# Claude Code renamed this feature, so reading one tool name shows an empty panel. `TodoWrite`
# (one call carrying the whole list) became `TaskCreate`/`TaskUpdate` (one call per phase,
# mirrored to <home>/tasks/<session id>/N.json).
#
# This used to say the CLI hands the Task tools to Haiku only, and that Opus 5 and Sonnet 5 get no
# phase tool at all. That is no longer true, and was checked on 2.1.257: an Opus 5 session in this
# Studio holds TaskCreate/TaskGet/TaskList/TaskOutput/TaskStop/TaskUpdate, and its own folder held
# 105 phase files. Every source is still read, because the sources genuinely differ — a folder can
# be cleaned, a transcript predates the rename, and a user-added engine has no phase tool at all —
# and the Studio's own file is what fills the panel in that last case.
_PHASE_STATUSES = ("pending", "in_progress", "completed")
# The whole-list phase tool, in both spellings: Claude Code's and the DeepSeek Harness runtime's
# (`todo_write`). One constant, so the feed renderer, the transcript replay and the deep backwards
# scan cannot disagree about which tool carries the plan.
_PHASE_LIST_TOOLS = ("TodoWrite", "todo_write")


def claude_home(project_id: str = "") -> Path:
    """The config home of whichever engine this project belongs to (claude / kimi / qwen / ...)."""
    p = alt_prefix(project_id)
    if p:
        return _alt_home(alt_homes()[p])
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) if base else (Path.home() / ".claude")


def studio_phases_file(project_id: str) -> Path:
    """Where the agent writes its phase list when the CLI gives it no phase tool.

    Kept in the Studio's data dir rather than the project, so a workspace the user version-controls
    never gains a file it did not ask for."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "unknown")
    return DATA_DIR / "phases" / (safe + ".json")


def _norm_phase(subject, status, active_form="", detail="") -> dict:
    st = str(status or "pending").strip().lower()
    if st in ("done", "complete", "finished"):
        st = "completed"
    elif st in ("active", "running", "in-progress", "inprogress"):
        st = "in_progress"
    if st not in _PHASE_STATUSES:
        st = "pending"
    return {"content": str(subject or "").strip()[:300],
            "status": st,
            "active_form": str(active_form or "").strip()[:200],
            "detail": str(detail or "").strip()[:400]}


def _created_at(st) -> float:
    """When the file was made, where the platform records that.

    Windows and the BSDs keep a birth time. Linux's ``st_ctime`` is the inode change time, which
    a later status update overwrites, so it cannot tell one run of phases from the next. Returning
    0 there means "unknown", and the caller then keeps every phase rather than cutting the list on
    a number that does not mean what it looks like."""
    bt = getattr(st, "st_birthtime", None)
    if bt:
        return float(bt)
    return float(st.st_ctime) if os.name == "nt" else 0.0


# A phase written this long after the one before it belongs to a new run. Measured against real
# sessions: phases created in one batch land within seconds of each other, and the pause between
# one piece of work and the next is tens of minutes at least.
_RUN_GAP = 20 * 60


def _run_starts(rows: list) -> list:
    """The index each run begins at, oldest first. Always starts with 0.

    A session runs for days, and the task folder keeps every phase list written in it, so without
    this the panel stacks Monday's finished build on top of tonight's. Two things end a run:

      * everything in THAT RUN is finished — the next phase created opens the next one, and
      * a long gap since the previous phase was created.

    The gap rule is what makes this survive a forgotten phase. The finished test used to look at
    every row from the start of the session rather than from the start of the current run, so a
    single item left pending in August welded eight days of work into one list of 76 — which is
    exactly what it did.

    `rows` is (created, updated, phase) in id order."""
    starts = [0]
    for i, (created, _updated, _phase) in enumerate(rows):
        if not i or not created:
            continue
        prev = rows[i - 1][0]
        gap = (created - prev) if prev else 0.0
        run = rows[starts[-1]:i]
        done = bool(run) and all(p["status"] == "completed" and u <= created for _c, u, p in run)
        if (gap > _RUN_GAP or done) and i != starts[-1]:
            starts.append(i)
    return starts


def _run_start(rows: list) -> int:
    """Index of the first phase of the LATEST run."""
    return _run_starts(rows)[-1]


def _runs_from_rows(rows: list, starts: list) -> list:
    """Every run as its own set, oldest first — what the panel groups the history by."""
    out = []
    for a, b in zip(starts, starts[1:] + [len(rows)]):
        chunk = rows[a:b]
        if not chunk:
            continue
        phases = [p for _c, _u, p in chunk]
        out.append({
            "started": chunk[0][0],
            "ended": max((u for _c, u, _p in chunk), default=0.0),
            "total": len(phases),
            "done": sum(1 for p in phases if p["status"] == "completed"),
            "phases": phases,
        })
    return out


def _phase_dirs(project_id: str, session_ids: set) -> list:
    """Task folders belonging to this project, newest first. The folder is keyed by session id,
    and --fork-session starts a fresh one, so the active session's folder can be empty while the
    phases the user is watching live under the id it was forked from."""
    base = claude_home(project_id) / "tasks"
    try:
        dirs = [d for d in base.iterdir() if d.is_dir() and d.name in session_ids]
    except OSError:
        return []
    return sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)


_PHASES_MEMO = perf.Memo(limit=64)


def _phases_from_store(d: Path, hook_ids: Optional[set] = None) -> tuple:
    """The phase folder, parsed once per change.

    Measured: this workspace's task folder holds more than 400 files, every one of them opened and
    parsed on every call — and it is called by the phase list, by the header counter and by every
    project of the overview scan. It was 13.9% of the backend's CPU on its own.

    The stamp is the folder's own listing: how many files, and the newest date among them. Any
    write to any task file moves it, so a stale answer is not possible. Callers treat the result as
    read-only (they only read the rows); nothing here may be mutated in place.
    """
    key = (str(d), tuple(sorted(hook_ids)) if hook_ids else ())
    return _PHASES_MEMO.get(key, perf.dir_stamp(d), lambda: _phases_from_store_build(d, hook_ids))


def _phases_from_store_build(d: Path, hook_ids: Optional[set] = None) -> tuple:
    """<home>/tasks/<session>/N.json -- the live state the Task tools keep on disk.

    Only the latest run is returned; see :func:`_run_start`. The count of everything older is
    returned too, so the panel can say that it is hiding something instead of quietly dropping it.
    """
    try:
        files = [f for f in d.iterdir() if f.suffix == ".json"]
        ts = d.stat().st_mtime
    except OSError:
        return [], 0.0, 0, []

    def order(f: Path):
        try:
            return (0, int(f.stem))
        except ValueError:
            return (1, 0)

    rows = []
    ids = []            # the task id each row came from, in the same order
    for f in sorted(files, key=order):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
            st = f.stat()
        except (OSError, ValueError):
            continue
        if isinstance(o, dict) and o.get("subject"):
            rows.append((_created_at(st), st.st_mtime,
                         _norm_phase(o.get("subject"), o.get("status"),
                                     o.get("activeForm"), o.get("description"))))
            ids.append(f.stem)
    starts = _run_starts(rows)
    cut = starts[-1]
    # Where the hook recorded this run, its ids win over the file timestamps.
    #
    # Not because the timestamps are wrong here — on Windows `_created_at` reads a real birth
    # time and the grouping above is correct, measured against a 105-phase folder. They are wrong
    # on Linux, where `st_ctime` is the inode change time and a status update overwrites it, so
    # `_created_at` returns 0 and the panel falls back to showing every phase ever written. The
    # TaskCreated record is immutable and platform-independent, so it fixes that case properly.
    #
    # It is only used when it covers every id it claims: a session older than the hook, or one
    # whose ledger has been trimmed, keeps the heuristic rather than getting a half-applied cut.
    if hook_ids:
        idx = [i for i, t in enumerate(ids) if t in hook_ids]
        if idx and len(idx) == len(hook_ids):
            cut = idx[0]
            starts = [s for s in starts if s < cut] + [cut]
    # The runs go back with it, so the panel can offer the earlier sets instead of only saying
    # how many phases it is hiding.
    return [p for _c, _u, p in rows[cut:]], ts, cut, _runs_from_rows(rows, starts)


def _phases_from_entries(entries: list) -> tuple:
    """Replay TaskCreate/TaskUpdate from the transcript, else the last TodoWrite.

    This covers a session whose task folder was cleaned, and every transcript written before the
    rename. TaskCreate carries no id -- the id is in the tool RESULT ("Task #3 created") -- so the
    creation order is the id, which is exactly how the CLI assigns them."""
    tasks: dict = {}
    order: list = []
    made = 0
    start = 0            # first index of the latest run; see _run_start for the same rule
    ts = ""
    for o in entries:
        m = o.get("message") or {}
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name")
            inp = b.get("input") or {}
            if name == "TaskCreate":
                made += 1
                tid = str(inp.get("taskId") or inp.get("id") or made)
                # A run is over once everything in it is finished. Replaying gives the status as
                # it stood at this moment, which is exactly the test _run_start approximates from
                # file times. The next phase created after that point opens a new run.
                if order and all(tasks[t]["status"] == "completed" for t in order):
                    start = len(order)
                if tid not in tasks:
                    order.append(tid)
                tasks[tid] = {"subject": inp.get("subject", ""), "status": "pending",
                              "activeForm": inp.get("activeForm", ""),
                              "description": inp.get("description", "")}
                ts = o.get("timestamp", "") or ts
            elif name == "TaskUpdate":
                key = str(inp.get("taskId") or inp.get("id") or "")
                t = tasks.get(key)
                if t is not None:
                    ts = o.get("timestamp", "") or ts
                    if str(inp.get("status") or "").strip().lower() == "deleted":
                        # A deleted phase leaves the panel. Left in, it reads as "pending" —
                        # a step that will never happen, shown as one still to come.
                        i = order.index(key)
                        order.pop(i)
                        tasks.pop(key, None)
                        if i < start:
                            start -= 1
                        continue
                    for k in ("status", "subject", "activeForm", "description"):
                        if inp.get(k):
                            t[k] = inp[k]
    if tasks:
        return [_norm_phase(tasks[t]["subject"], tasks[t]["status"],
                            tasks[t]["activeForm"], tasks[t]["description"])
                for t in order[start:]], ts, start
    for o in reversed(entries):                     # legacy: one call held the whole list
        rows = _phases_from_block((o.get("message") or {}).get("content"))
        if rows is not None:
            return rows, o.get("timestamp", ""), 0
    return [], "", 0


def _phases_from_block(blocks) -> Optional[list]:
    """The whole phase list from ONE assistant message's content blocks, or None if it has none.

    Two spellings, one shape: `TodoWrite` is Claude's (`{todos: [{content, status, activeForm}]}`)
    and `todo_write` is the DeepSeek Harness runtime's, read out of the installed runtime's own
    tool catalog rather than guessed (ask_user_question, bash, cordis_*, edit, glob, grep, present,
    read, read_image, skill, todo_write, web_fetch, web_search, write). Listing only the first is
    why a DeepSeek pane showed an empty Phases panel while its transcript held 14 phase writes.
    """
    if not isinstance(blocks, list):
        return None
    for b in blocks:
        if (isinstance(b, dict) and b.get("type") == "tool_use"
                and b.get("name") in _PHASE_LIST_TOOLS):
            items = (b.get("input") or {}).get("todos") or []
            return [_norm_phase(t.get("content"), t.get("status"), t.get("activeForm"))
                    for t in items if isinstance(t, dict)]
    return None


_PHASE_SCAN_BYTES = 12 * 1024 * 1024


def _last_phase_call(jsonl: Path, budget: int = _PHASE_SCAN_BYTES) -> tuple:
    """(rows, iso) for the LAST whole-list phase call in a transcript, however far back it is.

    `_tail_entries` budgets bytes from the END, which is the right shape for a feed and the wrong
    one for a phase list: the list is written once and the turn keeps appending after it, so in a
    long session the last `todo_write` sits megabytes behind the tail. Measured here 2026-10-05: a
    DeepSeek session was 4.4 MB and held 14 `todo_write` calls, not one of them inside the 512 KB
    tail `project_todos` reads — so the Phases button was empty for a build whose plan had been on
    screen minutes earlier. This scans back over a byte budget and parses only the lines that
    mention the tool, so the cost is one read and a substring scan, not a full JSON pass.
    """
    try:
        size = jsonl.stat().st_size
    except OSError:
        return [], ""
    start = max(0, size - max(1, budget))
    try:
        with open(jsonl, "rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return [], ""
    lines = blob.splitlines()
    if start:
        lines = lines[1:]          # the first line is almost certainly cut in half
    needles = []
    for t in _PHASE_LIST_TOOLS:
        needles.append(('"name": "%s"' % t).encode())
        needles.append(('"name":"%s"' % t).encode())
    for raw in reversed(lines):
        if not any(n in raw for n in needles):
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        rows = _phases_from_block((o.get("message") or {}).get("content"))
        if rows:
            return rows, o.get("timestamp", "") or ""
    return [], ""


def _phases_from_studio(project_id: str) -> tuple:
    """The file the agent writes when it has no phase tool. Deliberately forgiving about shape --
    a list of strings, or of objects under any of the names the three generations of this feature
    have used, all read the same."""
    f = studio_phases_file(project_id)
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        ts = f.stat().st_mtime
    except (OSError, ValueError):
        return [], 0.0
    items = raw.get("phases") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return [], 0.0
    out = []
    for t in items[:60]:
        if isinstance(t, str):
            out.append(_norm_phase(t, "pending"))
        elif isinstance(t, dict):
            out.append(_norm_phase(t.get("subject") or t.get("content") or t.get("title"),
                                   t.get("status"),
                                   t.get("activeForm") or t.get("active_form"),
                                   t.get("description") or t.get("detail")))
    phases = [p for p in out if p["content"]]
    if phases:
        _record_studio_run(project_id, phases, ts)
    return phases, ts


# --- phase history ---------------------------------------------------------
# The agent rewrites its phase file WHOLE, so the previous set is gone the instant a new one is
# written — a finished build's steps disappear at the exact moment you might want to look back at
# what it actually did. This keeps them.
#
# A "run" is one set of phases. The subjects are its identity: same subjects means the same run
# with statuses moving, different subjects means a new run has started and the old one is closed.
# Recorded on READ, because the agent writes that file directly and the Studio never sees the
# write — but only when something actually changed, so a poll that finds nothing new writes
# nothing.
_RUN_LIMIT = 40


def phase_runs_file(project_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "unknown")
    return DATA_DIR / "phases" / (safe + ".runs.json")


def _sig(phases: list) -> list:
    return [p["content"] for p in phases]


def _record_studio_run(project_id: str, phases: list, ts: float) -> None:
    f = phase_runs_file(project_id)
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
        runs = doc.get("runs") if isinstance(doc, dict) else None
        runs = runs if isinstance(runs, list) else []
    except (OSError, ValueError):
        runs = []
    sig = _sig(phases)
    last = runs[-1] if runs else None
    if last and last.get("sig") == sig:
        if last.get("phases") == phases:
            return                                   # nothing moved — do not touch the disk
        last["phases"] = phases
        last["ended"] = ts
    else:
        runs.append({"started": ts, "ended": ts, "sig": sig, "phases": phases})
        runs[:] = runs[-_RUN_LIMIT:]
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"runs": runs}), encoding="utf-8")
        fsutil.replace(tmp, f)
    except OSError:
        pass                                          # history is a convenience, never a blocker


def _file_runs(project_id: str) -> list:
    """The project's history file (phase_runs_file): runs moved out of the task folder by
    archive_finished_phase_runs, and runs a CLI without the phase tools wrote."""
    try:
        runs = json.loads(phase_runs_file(project_id).read_text(encoding="utf-8")).get("runs") or []
    except (OSError, ValueError, AttributeError):
        return []
    return runs if isinstance(runs, list) else []


def _merge_runs(store: list, filed: list) -> list:
    """Store runs and history-file runs as ONE list, oldest first, each set once. Two runs are the
    same set when their phase subjects are the same. The store's own rows are never mutated
    (_phases_from_store memoises them)."""
    out = list(store)
    seen = {tuple(p.get("content") for p in (r.get("phases") or [])) for r in store}
    for r in filed:
        ph = r.get("phases") or []
        key = tuple(p.get("content") for p in ph)
        if not ph or key in seen:
            continue
        seen.add(key)
        out.append({"started": r.get("started", 0), "ended": r.get("ended", 0), "total": len(ph),
                    "done": sum(1 for p in ph if p.get("status") == "completed"), "phases": ph})
    out.sort(key=lambda r: r.get("started", 0))
    return out


# A finished run leaves the task list this long after its last change: long enough that nothing
# the agent is still looking at moves, short enough that the next job's reminders are its own.
_ARCHIVE_AFTER_S = 30 * 60


def _store_runs_with_files(d: Path) -> list:
    """The folder's runs, oldest first, each with the task files it came from. The grouping is
    the Phases panel's own (_run_starts over the same rows _phases_from_store_build reads)."""
    def order(f: Path):
        try:
            return (0, int(f.stem))
        except ValueError:
            return (1, 0)

    rows, files = [], []
    for f in sorted((f for f in d.iterdir() if f.suffix == ".json"), key=order):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
            st = f.stat()
        except (OSError, ValueError):
            continue
        if isinstance(o, dict) and o.get("subject"):
            rows.append((_created_at(st), st.st_mtime,
                         _norm_phase(o.get("subject"), o.get("status"),
                                     o.get("activeForm"), o.get("description"))))
            files.append(f)
    out = []
    starts = _run_starts(rows) if rows else []
    for a, b in zip(starts, starts[1:] + [len(rows)]):
        if rows[a:b]:
            out.append({"started": rows[a][0], "ended": max(u for _c, u, _p in rows[a:b]),
                        "phases": [p for _c, _u, p in rows[a:b]], "files": files[a:b]})
    return out


def archive_finished_phase_runs(project_id: str, session_id: str, now: Optional[float] = None) -> int:
    """Move a session's FINISHED phase runs out of the CLI's task folder. Returns tasks moved.

    The CLI repeats the whole task list in a reminder every few tool calls, and in a long session
    that list only grows: one STUDIO session reached 554 finished tasks, about 10k tokens each
    time, 1,290 times in its record. Nothing is deleted. The runs go to the project's history
    file first (phase_history reads it), then the files move to data/phases/archive/. The CLI
    reads the folder live, so the next reminder is short.

    Kept in the list: the newest run always, a run with any phase not completed, and a run
    changed in the last half hour. Off: Settings -> Planning & review -> "Keep the phase list short"."""
    if not session_id or not settings.get("cc_phase_archive", True):
        return 0
    d = claude_home(project_id) / "tasks" / session_id
    try:
        runs = _store_runs_with_files(d)
    except OSError:
        return 0
    now = time.time() if now is None else now
    done = [r for r in runs[:-1]
            if r["phases"] and all(p["status"] == "completed" for p in r["phases"])
            and now - r["ended"] >= _ARCHIVE_AFTER_S]
    if not done:
        return 0
    # The history first: if it cannot be written, the tasks stay where they are.
    have = _file_runs(project_id)
    seen = {tuple(r.get("sig") or [p.get("content") for p in (r.get("phases") or [])]) for r in have}
    add = [{"started": r["started"], "ended": r["ended"], "sig": _sig(r["phases"]), "phases": r["phases"]}
           for r in done if tuple(_sig(r["phases"])) not in seen]
    merged = sorted(have + add, key=lambda r: r.get("started", 0))[-_RUN_LIMIT:]
    f = phase_runs_file(project_id)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"runs": merged}), encoding="utf-8")
        fsutil.replace(tmp, f)
    except OSError:
        return 0
    dest = (DATA_DIR / "phases" / "archive" / session_id[:8]
            / time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
    moved = 0
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    for r in done:
        for tf in r["files"]:
            try:
                fsutil.replace(tf, dest / tf.name)
                moved += 1
            except OSError:
                pass                                  # a file the CLI holds stays; next round
    return moved


def phase_history(project_id: str) -> dict:
    """Every phase set, newest first. Index 0 is the one on screen now.

    The task store and the history file together: finished runs are moved out of the store into
    that file (archive_finished_phase_runs), and a CLI without the phase tools writes only there.
    """
    store: list = []
    try:
        root, pdir, _ = project_dir(project_id)
        pdir = pdir.resolve()
        if root.resolve() in pdir.parents:
            ids = {f.stem for f in pdir.glob("*.jsonl")}
            for d in _phase_dirs(project_id, ids)[:6]:
                _cur, _ts, _earlier, runs = _phases_from_store(d)
                if runs:
                    store = list(runs)
                    break
    except Exception:                                # history is a convenience, never a blocker
        pass
    return {"runs": list(reversed(_merge_runs(store, _file_runs(project_id))))}   # newest first


def _iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return ""


def resolve_phases(project_id: str, entries: list, session_ids: set,
                   transcript: Optional[Path] = None) -> dict:
    """The phase list for a project, from whichever source wrote most recently.

    `transcript` is the conversation file the `entries` were tailed from. Given it, a phase call
    that has scrolled out of the byte budget is still found — see `_last_phase_call`.
    """
    cands = []
    store_list: list = []
    for d in _phase_dirs(project_id, session_ids)[:6]:
        try:
            from . import hook_events
            hook_ids = hook_events.latest_run_ids(project_id, d.name)
        except Exception:
            hook_ids = set()
        rows, ts, earlier, runs = _phases_from_store(d, hook_ids)
        if rows:
            store_list = runs
            cands.append((ts, "tasks", rows, _iso(ts), earlier))
            break                                   # newest folder wins; older ones are history
    rows, iso, earlier = _phases_from_entries(entries or [])
    if rows:
        cands.append((_epoch(iso), "transcript", rows, iso, earlier))
    elif transcript is not None:
        # Not in the tail. Read further back rather than reporting "no plan" for a plan the agent
        # wrote — the tail budget is a feed's, and a phase list outlives it in any long session.
        deep_rows, deep_iso = _last_phase_call(transcript)
        if deep_rows:
            cands.append((_epoch(deep_iso), "transcript", deep_rows, deep_iso, 0))
    rows, ts = _phases_from_studio(project_id)
    if rows:
        # The agent rewrites this file whole, so it is already just the run in hand.
        cands.append((ts, "studio", rows, _iso(ts), 0))
    if not cands:
        return {"todos": [], "done": 0, "total": 0, "source": "", "ts": "", "earlier": 0,
                "finished": False, "runs": 0}
    _ts, source, todos, iso, earlier = max(cands, key=lambda c: c[0])
    done = sum(1 for t in todos if t["status"] == "completed")
    if source == "tasks" and store_list:
        runs = len(_merge_runs(store_list, _file_runs(project_id)))
    else:
        runs = len(_file_runs(project_id))
    return {"todos": todos, "ts": iso, "source": source, "earlier": earlier,
            # `finished` is what lets the panel stop looking like work in progress. A set that is
            # entirely ticked is a record of something done, not a plan — it should read that way,
            # and step aside for the next set instead of sitting there as if it were still live.
            "finished": bool(todos) and done == len(todos),
            "runs": runs, "done": done, "total": len(todos)}


def _epoch(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def project_todos(project_id: str) -> dict:
    """The CURRENT phase list for a project, complete.

    The feed renders a phase event as it happens, but it scrolls away, so "3/9" in the header had
    nothing behind it. This is the standing list."""
    if engines.native(project_id):
        return engines.native(project_id).todos(project_id)
    root, pdir, _ = project_dir(project_id)
    empty = {"todos": [], "done": 0, "total": 0, "source": "", "ts": "", "earlier": 0}
    try:
        pdir = pdir.resolve()
        if root.resolve() not in pdir.parents:
            return empty
    except Exception:
        return empty
    jsonls = list(pdir.glob("*.jsonl")) if pdir.exists() else []
    newest = active_transcript(project_id, jsonls)
    # A bigger tail than the context meter reads: the phase calls are often many tools back.
    entries = _tail_entries_cached(newest, kb=512) if newest else []
    return resolve_phases(project_id, entries, {f.stem for f in jsonls}, transcript=newest)

@coalesced(ttl=0)            # one parse serves every pane that asks while it runs
def project_context(project_id: str) -> dict:
    if engines.native(project_id):
        return engines.native(project_id).context(project_id)
    root, pdir, _ = project_dir(project_id)
    try:
        pdir = pdir.resolve()
        if root.resolve() not in pdir.parents:
            return {}
    except Exception:
        return {}
    jsonls = list(pdir.glob("*.jsonl")) if pdir.exists() else []
    if not jsonls:
        return {}
    newest = active_transcript(project_id, jsonls)   # context meter follows the pinned chat
    tail = _tail_entries_cached(newest, kb=128)
    info = _context_info(tail)
    awaiting = _awaiting_input(tail)
    info["awaiting_input"] = awaiting
    info["working"] = _is_working(tail, newest.stat().st_mtime, awaiting, project_id)
    info["agents_active"] = _active_agent_count(pdir, project_id)
    info["agents_top"], info["agents_nested"] = _spawn_origin(pdir)
    info["tokens"] = _turn_tokens(tail)
    try:   # lazy (cc_session imports mission) — flag a /compact in progress for the meter
        from . import cc_session
        info["compacting"] = bool(cc_session.live_state(project_id).get("compacting"))
    except Exception:
        info["compacting"] = False
    _apply_compact_hook(info, project_id)
    # The standing instructions this session took on. Billed on every request and visible
    # nowhere else — a CLAUDE.md that imports two more is three files nobody chose to load.
    try:
        from . import hook_events
        ins = hook_events.instructions(project_id)
        if ins.get("count"):
            info["instructions"] = ins
    except Exception:
        pass
    return info


# How long after a compaction ends the hook's own figure is trusted over the transcript. The
# summary entry is normally written within a second or two; this only has to cover that.
_COMPACT_GAP = 180.0


def _apply_compact_hook(info: dict, project_id: str) -> None:
    """Correct the meter from the PreCompact/PostCompact record, where there is one.

    Three things the transcript alone gets wrong, and one it does not:

    * ``compacting`` was read from the Studio's own streaming process, so a /compact run in the
      terminal pane — a real CLI session the Studio did not spawn — showed nothing at all.
      The hook fires for any session started with these settings, so both are covered now.
    * PostCompact fires the moment compaction ends; the transcript's summary entry is written
      after it. In that gap the newest assistant usage is still the PRE-compact turn, so the
      meter kept showing the old high-water mark. The hook carries the summary itself, which is
      the only copy that exists during the gap.
    * ``trigger`` says whether the user asked for it or the window filled up.

    What it does NOT improve is the arithmetic once the transcript has caught up: that entry
    holds the same summary text, so the existing estimate already measures it. This only closes
    the gap before it lands.
    """
    try:
        from . import hook_events
        cs = hook_events.compact_state(project_id)
    except Exception:
        return
    if not cs:
        return
    if cs.get("trigger"):
        info["compact_trigger"] = cs["trigger"]
    if cs.get("running"):
        info["compacting"] = True
        return
    ended = float(cs.get("ended") or 0)
    summary = int(cs.get("summary_tokens") or 0)
    if not ended or not summary or info.get("just_compacted"):
        return
    if time.time() - ended > _COMPACT_GAP:
        return
    # The summary IS the new context, plus the fixed system/tools overhead that every session
    # carries. Anything read since lands on the next real turn, which supersedes this.
    used = int(settings.get("compact_base_tokens", 25000) or 25000) + summary
    if used >= int(info.get("ctx_used") or 0):
        return                     # nothing stale to correct — leave the real figure alone
    mx = int(info.get("ctx_max") or 0) or model_window(str(info.get("model") or ""))
    compact_at = float(settings.get("auto_compact_at", 0.92) or 0.92)
    frac = used / mx if mx else 0.0
    info.update({
        "ctx_used": used, "ctx_max": mx,
        "ctx_pct": round(100 * frac, 1),
        "ctx_remaining": round(max(0.0, 100 * (compact_at - frac) / compact_at), 1),
        "just_compacted": True,
    })


# ---------------------------------------------------------------------------
# Slash commands — built-ins + custom commands discovered on disk
# ---------------------------------------------------------------------------
_BUILTIN_SLASH = [
    ("/clear", "Clear the conversation history"),
    ("/compact", "Summarize & compact the context now"),
    ("/model", "Switch the model"),
    ("/config", "Open settings"),
    ("/cost", "Show token cost for this session"),
    ("/context", "Show context-window usage"),
    ("/review", "Review the current changes"),
    ("/agents", "Manage subagents"),
    ("/memory", "Edit CLAUDE.md memory"),
    ("/init", "Generate a CLAUDE.md for the project"),
    ("/help", "List commands"),
]


def _commands_in(dir_path: Path, scope: str) -> list[dict]:
    out: list[dict] = []
    if not dir_path.exists():
        return out
    try:
        for md in sorted(dir_path.rglob("*.md")):
            rel = md.relative_to(dir_path).with_suffix("")
            name = "/" + ":".join(rel.parts)
            desc = ""
            try:
                for line in md.read_text(encoding="utf-8", errors="ignore").splitlines()[:8]:
                    s = line.strip()
                    if s.lower().startswith("description:"):
                        desc = s.split(":", 1)[1].strip().strip("\"'")
                        break
                    if s and not s.startswith(("---", "#")):
                        desc = s[:80]
                        break
            except OSError:
                pass
            out.append({"name": name, "desc": desc, "scope": scope})
    except OSError:
        pass
    return out


def slash_commands(project_id: str = "") -> list[dict]:
    """Built-in slash commands + custom commands from ~/.claude/commands and the
    project's .claude/commands. Note: in headless sends, custom (skill) commands
    run; some built-ins are interactive-only."""
    cmds = [{"name": n, "desc": d, "scope": "builtin"} for n, d in _BUILTIN_SLASH]
    home = os.environ.get("CLAUDE_CONFIG_DIR")
    cc_home = Path(home) if home else (Path.home() / ".claude")
    cmds += _commands_in(cc_home / "commands", "personal")
    # project-scoped commands live in <cwd>/.claude/commands
    try:
        root, pdir, _ = project_dir(project_id)
        if project_id and pdir.exists():
            cwd = _meta_from_head(max(pdir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)).get("cwd")
            if cwd:
                cmds += _commands_in(Path(cwd) / ".claude" / "commands", "project")
    except (ValueError, OSError):
        pass
    return cmds


def list_sessions(project_id: str) -> list[dict]:
    """All conversations (transcripts) for a project, newest first — so the user can
    clear to a fresh one and reopen previous ones. The active (newest) is flagged."""
    if engines.native(project_id):
        return engines.native(project_id).sessions(project_id)
    root, pdir, _ = project_dir(project_id)
    try:
        pdir = pdir.resolve()
        if pdir != root.resolve() and root.resolve() not in pdir.parents:
            return []
    except Exception:
        return []
    if not pdir.exists():
        return []
    jsonls = sorted(pdir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for jl in jsonls[:50]:
        try:
            st = jl.stat()
        except OSError:
            continue
        title = ""
        for o in _iter_head(jl, 80):
            if o.get("type") == "user":
                t = _text_of(o)
                if t and not t.lstrip().startswith(("<", "Caveat:")):
                    title = " ".join(t.split())[:90]
                    break
        out.append({"id": jl.stem, "ts": st.st_mtime, "size": st.st_size,
                    "title": title or "(empty conversation)", "active": False})
    # flag the conversation the Studio is actually continuing (pinned), not just the
    # most recently touched file — those differ when another tool wrote to this folder
    sid = active_session_id(project_id)
    marked = False
    if sid:
        for o in out:
            if o["id"] == sid:
                o["active"] = marked = True
                break
    if out and not marked:
        out[0]["active"] = True
    return out


_GRAPH_MARK = "[code graph]"
_GRAPH_RE = re.compile(r"\[code graph\] '([^']+)' is declared at: (.*?)\. That came from", re.S)


def _graph_from_hook(entry: dict) -> dict:
    """The graph's answer out of a PreToolUse hook attachment, or {}.

    The hook injects this into the model's context and the transcript records it, but nothing
    ever showed it to the person watching — so the graph could answer a hundred times and look
    exactly like it had never run. The marker is a contract with graph_hook.py."""
    att = entry.get("attachment") or {}
    if att.get("hookEvent") != "PreToolUse":
        return {}
    c = att.get("content")
    parts = c if isinstance(c, list) else [c]
    for part in parts:
        t = part if isinstance(part, str) else (part.get("text") if isinstance(part, dict) else "")
        if not isinstance(t, str) or _GRAPH_MARK not in t:
            continue
        m = _GRAPH_RE.search(t)
        if m:
            return {"symbol": m.group(1), "where": " ".join(m.group(2).split())[:300]}
        return {"symbol": "", "where": " ".join(t.split())[:300]}
    return {}


def _result_text(block: dict):
    c = block.get("content")
    ok = not block.get("is_error", False)
    if isinstance(c, str):
        return c, ok
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"), ok
    return "", ok


def _active_agent_count(pdir: Path, project_id: str = "") -> int:
    """How many subagents are working for this project right now.

    ONE DEFINITION, SHARED. There used to be three, and the user saw all three at once: this one
    counted any agent file touched in the last 180 seconds (so a finished agent still counted, and
    a quiet one stopped counting), the left rail counted 90 seconds with an outcome check, and the
    bottom-bar pill counted 90 seconds plus a 30-minute "dispatched and never reported" window.
    The same fan-out therefore showed as 2 under the chat, 0 in the rail and 1 in the bar, and no
    reading of the screen could tell you which was true.

    So the question is asked once, in `subagents`, and everything reads that answer: an agent
    counts while it is writing, and it goes on counting while it is quiet if it was dispatched in
    the background and has never reported an outcome. The mtime scan below is kept only for the
    caller that has no project id."""
    if project_id:
        try:
            from . import subagents
            return len(subagents.working(project_id))
        except Exception:
            pass
    now = time.time()
    n = 0
    try:
        for sd in pdir.iterdir():
            if not sd.is_dir() or sd.name == "memory":
                continue
            for ag in sd.rglob("agent-*.jsonl"):
                try:
                    if now - ag.stat().st_mtime <= AGENT_ACTIVE_WINDOW:
                        n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def _spawns_in_line(raw: bytes) -> int:
    """Agent/Task spawn calls in ONE transcript line. Pre-filters on raw bytes so only the handful
    of lines that could hold a tool_use are ever JSON-parsed."""
    if b'"tool_use"' not in raw or (b'"Agent"' not in raw and b'"Task"' not in raw):
        return 0
    try:
        o = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return 0
    c = (o.get("message") or {}).get("content")
    if not isinstance(c, list):
        return 0
    return sum(1 for b in c if isinstance(b, dict) and b.get("type") == "tool_use"
               and b.get("name") in ("Agent", "Task"))


_SPAWN_LOCK = threading.Lock()
_SPAWN_READ: dict = {}     # path -> (bytes counted, the 64 bytes before that point, the count)
_SPAWN_EDGE = 64           # how much of the boundary is remembered; see below

# THE COUNTS SURVIVE A RESTART, because the first click after one was the slow one.
#
# Measured on the real window: the first time a project is opened after the backend starts, the
# context meter counts spawns across every transcript that project owns. One workspace here holds
# 2.5 GB in 144 files (a 1 GB session plus ~140 agent transcripts), and reading them took about
# five seconds — once, and then everything was instant. Keeping the offsets on disk means that
# five seconds is paid once ever, not once per restart: after a restart only the bytes written
# since the last save are read.
#
# Losing or corrupting this file costs one slow call, nothing else: every entry is still checked
# against the file's length and the 64 bytes at its boundary before it is trusted.
_SPAWN_FILE = DATA_DIR / "cache" / "spawn_counts.json"
_SPAWN_MAX = 600           # entries kept; the oldest are dropped when a save runs
_spawn_loaded = False
_spawn_dirty = False
_spawn_saved = 0.0
_SPAWN_SAVE_EVERY = 20.0   # seconds between saves at most; the file is a few tens of KB


def _spawn_load() -> None:
    """Read the saved offsets once. Called with _SPAWN_LOCK held."""
    global _spawn_loaded
    if _spawn_loaded:
        return
    _spawn_loaded = True
    if not perf.fast():
        return
    try:
        raw = json.loads(_SPAWN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    for path, row in raw.items():
        try:
            off, edge_hex, count = row
            if Path(path).exists():
                _SPAWN_READ[path] = (int(off), bytes.fromhex(str(edge_hex)), int(count))
        except (TypeError, ValueError, OSError):
            continue


def _spawn_save(force: bool = False) -> None:
    """Write the offsets out, at most every _SPAWN_SAVE_EVERY seconds. Never raises."""
    global _spawn_dirty, _spawn_saved
    with _SPAWN_LOCK:
        if not _spawn_dirty:
            return
        now = time.time()
        if not force and (now - _spawn_saved) < _SPAWN_SAVE_EVERY:
            return
        rows = list(_SPAWN_READ.items())[-_SPAWN_MAX:]
        _spawn_saved = now
        _spawn_dirty = False
    data = {p: [off, edge.hex(), n] for p, (off, edge, n) in rows}
    try:
        _SPAWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SPAWN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        fsutil.replace(tmp, _SPAWN_FILE)
    except OSError:
        pass


atexit.register(lambda: _spawn_save(force=True))


def _edge_bytes(path: Path, off: int, n: int = _SPAWN_EDGE) -> bytes:
    """The n bytes that end at `off` — the proof that the file we counted is still this file."""
    if off <= 0:
        return b""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, off - n))
            return fh.read(min(n, off))
    except OSError:
        return b""


def _count_spawns(path: Path) -> int:
    """Agent/Task spawn calls recorded in one transcript, counting every byte ONCE.

    A transcript is append-only, so the count is kept beside the offset it was counted to and only
    the NEW bytes are read. Measured before this: one call on this workspace's 386 MB transcript
    took 0.5 s, the context meter asked for it again after every message, and the whole backend —
    one interpreter, one lock — stopped for that half second. `/context` was seen taking 1,343 ms.

    Three things make an incremental count safe here:
      * only COMPLETE lines are counted; a half-written last line is left for the next call;
      * a file SHORTER than the offset is counted again from zero (a rewind truncates);
      * the 64 bytes before the offset are remembered and compared. An edit-message rewind
        truncates and then writes different content, so the file can pass the length test with a
        different history; a changed boundary means count again from zero.
    """
    key = str(path)
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    start, count = 0, 0
    if perf.fast():
        with _SPAWN_LOCK:
            _spawn_load()
            prev = _SPAWN_READ.get(key)
        if prev and 0 < prev[0] <= size and _edge_bytes(path, prev[0]) == prev[1]:
            start, count = prev[0], prev[2]
    consumed = start
    try:
        with open(path, "rb") as fh:
            if start:
                fh.seek(start)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break                      # still being written — count it next time
                consumed += len(raw)
                count += _spawns_in_line(raw)
    except OSError:
        return count
    global _spawn_dirty
    edge = _edge_bytes(path, consumed)
    with _SPAWN_LOCK:
        if len(_SPAWN_READ) > _SPAWN_MAX * 2:
            _SPAWN_READ.clear()
        prev = _SPAWN_READ.get(key)
        _SPAWN_READ[key] = (consumed, edge, count)
        _spawn_dirty = _spawn_dirty or prev != (consumed, edge, count)
    _spawn_save()
    return count


_SPAWN_CACHE: dict = {}


def _spawn_origin(pdir: Path) -> "tuple[int, int]":
    """(top_level, nested) agent spawns for this project's newest session.

    Transcripts carry NO parent->child link — an agent file records its own agentId and its TYPE
    ("general-purpose"), never who spawned it — so attribution is done by ORIGIN: spawn calls made
    by the main session are top-level; spawn calls made from inside an agent transcript are nested.
    That is what explains "I asked for 5 agents and 16 appeared": 5 top-level, and those 5 spawned
    11 more between them. Flat counting makes obedient fan-out look like a runaway."""
    try:
        sessions = list(pdir.glob("*.jsonl"))
        if not sessions:
            return 0, 0
        newest = max(sessions, key=lambda f: f.stat().st_mtime)
        sdir = pdir / newest.stem
        agents = list(sdir.rglob("agent-*.jsonl")) if sdir.is_dir() else []
        key = (str(newest), newest.stat().st_mtime_ns, len(agents),
               max((a.stat().st_mtime_ns for a in agents), default=0))
    except OSError:
        return 0, 0
    hit = _SPAWN_CACHE.get(key)
    if hit is not None:
        return hit
    val = (_count_spawns(newest), sum(_count_spawns(a) for a in agents))
    if len(_SPAWN_CACHE) > 8:
        _SPAWN_CACHE.clear()
    _SPAWN_CACHE[key] = val
    return val


_EMPTY_FEED = {"lines": [], "working": False, "tokens": 0, "agents_active": 0, "agent": "", "agent_log": ""}


_TAIL_CACHE: dict = {}          # (path, mtime_ns, size, kb) -> parsed entries
_TAIL_CACHE_MAX = 32


def _tail_entries_cached(p: Path, kb: int) -> list:
    """`_tail_entries` memoised on the file's exact identity (mtime_ns + size). The feed polls
    every ~300-900ms while a turn runs; between two polls the transcript is usually byte-identical,
    and re-reading + json-parsing megabytes of tail for an unchanged file is pure waste. Any write
    changes mtime_ns or size, so a stale hit is not possible."""
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size, kb)
    except OSError:
        return _tail_entries(p, kb=kb)
    hit = _TAIL_CACHE.get(key)
    if hit is not None:
        return hit
    val = _tail_entries(p, kb=kb)
    if len(_TAIL_CACHE) >= _TAIL_CACHE_MAX:
        _TAIL_CACHE.pop(next(iter(_TAIL_CACHE)), None)   # oldest out, not everything
    _TAIL_CACHE[key] = val
    return val


# the first line btw_hook.py writes into `additionalContext`; matched case-insensitively because
# the marker has been retyped more than once and a case slip silently hides every note again
_BTW_HOOK_MARK = "live side-note from the user"


def _btw_from_hook(entry: dict) -> str:
    """The user's own words out of a delivered /btw hook attachment, or ''.

    Shape (verified against a real transcript — the hook fields are NESTED, not top level):
        {"type":"attachment", "attachment":{"hookEvent":"PostToolUse", "toolUseID":…,
          "stdout":"{\\"hookSpecificOutput\\":{\\"additionalContext\\":\\"↪ LIVE side-note …\\"}}"}}
    The wrapper text around the note is instruction for the model, not something to show, so
    only the middle — between the marker line and the trailing "— Take it into account" — is kept."""
    att = entry.get("attachment")
    if not isinstance(att, dict) or str(att.get("hookEvent") or "") not in ("PreToolUse", "PostToolUse"):
        return ""
    raw = att.get("stdout")
    marks = (_BTW_HOOK_MARK, "live update from the user")
    if not isinstance(raw, str) or not any(mark in raw.lower() for mark in marks):
        return ""
    try:
        ctx = ((json.loads(raw) or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""
    except (json.JSONDecodeError, TypeError):
        return ""
    if not any(mark in ctx.lower() for mark in marks):
        return ""
    body = ctx.split("\n", 1)[1] if "\n" in ctx else ""
    cut = body.find("\n— Take it into account")
    if cut >= 0:
        body = body[:cut]
    return body.strip()


def _agent_event(b: dict, ts: str) -> dict:
    """A subagent, from the call that started it — upgraded in place when its result lands.

    ONE event, not two. A Task tool_use followed later by its tool_result is the same subagent
    seen twice, and drawing it twice makes a feed in which every delegation appears to happen
    again for no reason.
    """
    inp = b.get("input") or {}
    return {
        "kind": "agent", "ts": ts, "icon": "bot",
        "agent": {
            "agent_id": "",
            "description": _clean(str(inp.get("description") or ""), 200),
            "agent_type": str(inp.get("subagent_type") or ""),
            "prompt": _clean(str(inp.get("prompt") or ""), 6000),
            "background": bool(inp.get("run_in_background")),
            "running": True,
        },
    }


def _subagent_record(o: dict):
    """The parent's record of a subagent, if this entry carries one.

    Imported here rather than at the top: `subagents` reads this module for its event builders,
    so importing it up there would be a cycle. By the time a feed is built both are loaded.
    """
    if not isinstance(o.get("toolUseResult"), dict):
        return None
    try:
        from . import subagents
        return subagents._record(o)
    except Exception:
        return None


def _filter_kinds(events: list[dict], kinds: str) -> list[dict]:
    """Optional kind filter for the feed.

    The Prompts panel needs only `user` and `text` lines, but asked for the whole timeline —
    a 1.4 MB response every few seconds to display ~50 KB. Filtering server-side keeps the
    read window (and therefore how far back it reaches) exactly the same."""
    if not kinds:
        return events
    want = {k.strip() for k in kinds.split(",") if k.strip()}
    return [e for e in events if e.get("kind") in want] if want else events


def _iso_epoch(iso: str) -> float:
    """`2026-09-09T02:16:40.123Z` -> epoch seconds. 0.0 when there is nothing to read."""
    if not iso:
        return 0.0
    try:
        from datetime import datetime, timezone
        t = iso.replace("Z", "+00:00")
        d = datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return 0.0


# How far after the last visible event the CLI's own `result` may still land. The answer is
# written, then the turn is priced and closed; on a long reply that gap is seconds, not minutes.
_TURN_SLACK = 90.0


def _turn_rows(project_id: str, events: list) -> list:
    """One summary event after each finished turn, from what is known rather than what is guessed.

    A turn begins at a user message and ends where the next one begins. Everything countable comes
    from the events themselves — tool calls, the files an edit named, the lines it added and
    removed, the wall time between the first and last of them. Everything priced comes from the
    CLI, banked by `turns` at the moment it reported `result`, and joined here on the END time,
    which is the reliable half: a turn can start minutes before its first visible event while the
    model thinks, but `result` is stamped when the answer is complete.

    A FIELD THAT IS NOT KNOWN IS NOT SENT. The running turn has not been priced yet, and an old
    conversation predates the banking entirely; both still get their tools and their duration, and
    the bar simply says less. Inventing the rest would make the one number a person actually
    checks the one number they cannot trust.
    """
    try:
        from . import turns as _turns
    except Exception:
        return events
    banked = _turns.for_project(project_id)
    if not banked and not events:
        return events

    subs: list = []
    try:
        from . import subagents as _subs
        subs = list((_subs.list_for(project_id) or {}).get("agents") or [])
    except Exception:
        subs = []

    out: list = []
    turn: list = []

    def close(bucket: list) -> None:
        """Emit the summary for one finished turn, if it did anything worth summarising."""
        body = [e for e in bucket if e.get("kind") != "user"]
        if not body:
            return
        first = float(bucket[0].get("at") or 0)
        last = float(body[-1].get("at") or 0)
        tools = sum(1 for e in body if e.get("kind") == "tool")
        files, added, removed = set(), 0, 0
        for e in body:
            if e.get("icon") == "edit":
                if e.get("subtitle"):
                    files.add(str(e["subtitle"]))
                d = e.get("diff") or {}
                added += int(d.get("added") or 0)
                removed += int(d.get("removed") or 0)
        # A turn whose events predate the epoch stamp can still be counted, just not timed.
        span = round(max(0.0, last - first), 1) if (first and last) else 0.0
        row = {"kind": "turn", "ts": body[-1].get("ts") or "", "at": last,
               "tools": tools, "files": len(files),
               "added": added, "removed": removed,
               "wall_s": span, "wall_from": "feed"}

        priced = None
        for b in banked:
            ts = float(b.get("ts") or 0)
            if first <= ts <= last + _TURN_SLACK and (priced is None or ts > float(priced["ts"])):
                priced = b
        if priced:
            row.update({"tokens": int(priced.get("tokens") or 0),
                        "cost": float(priced.get("cost") or 0),
                        "model": priced.get("model") or "",
                        "effort": priced.get("effort") or "",
                        "gen_s": float(priced.get("gen_s") or 0),
                        "wall_s": round(float(priced.get("wall_s") or row["wall_s"]), 1),
                        "wall_from": "cli"})
            if priced.get("compacting"):
                row["compacting"] = True

        # The subagents this turn spawned. Their tokens are a SLICE of the turn's cost, never an
        # addition to it: the CLI prices the whole turn and a Task is part of that turn.
        a_n = a_tok = a_tools = 0
        a_ms = 0.0
        for a in subs:
            # A subagent row stamps its start as ISO, not epoch - the same two clocks the feed
            # itself had to be taught apart.
            raw = a.get("ts")
            ts = float(raw) if isinstance(raw, (int, float)) else _iso_epoch(str(raw or ""))
            if first <= ts <= last + _TURN_SLACK:
                a_n += 1
                a_tok += int(a.get("tokens") or 0)
                a_tools += int(a.get("tools") or 0)
                a_ms += float(a.get("ms") or 0)
        if a_n:
            row.update({"agents": a_n, "agent_tokens": a_tok, "agent_tools": a_tools,
                        "agent_s": round(a_ms / 1000.0, 1)})

        if tools or priced or a_n:
            out.append(row)

    for e in events:
        if e.get("kind") == "user" and not e.get("btw"):
            close(turn)
            turn = [e]
        elif turn:
            turn.append(e)
        else:
            turn = [e]           # a feed window that begins mid-turn still gets summarised
        out.append(e)
    close(turn)
    return out


def project_subagents(project_id: str, session: str = "", with_files: bool = False) -> dict:
    """Every agent this project has delegated to, whichever engine was driving it.

    The three consumers of this — the bottom-bar pill, the "who is running" list and the agent
    pane — all speak the shape `subagents.list_for` returns. A Codex conversation keeps the same
    material on disk, under its own thread ids, so it is served the same structure instead of a
    second one every consumer would have to learn.

    This dispatch is the whole reason a Codex fan-out used to be invisible: the endpoints behind
    the pill, the card and the pane called the Claude module directly, which reads Claude's
    transcript layout and nothing else.
    """
    if engines.native(project_id):
        return engines.native(project_id).subagents(project_id)
    from . import subagents
    return subagents.list_for(project_id, session=session, with_files=with_files)


def project_subagent(project_id: str, agent_id: str, limit: int = 400) -> dict:
    """One agent in full: its own timeline, and what it read, ran and changed."""
    if engines.native(project_id):
        return engines.native(project_id).subagent_detail(project_id, agent_id, limit=limit)
    from . import subagents
    return subagents.detail(project_id, agent_id, limit=limit)


def project_subagent_collisions(project_id: str, root: str = "") -> list:
    """Files that more than one RUNNING agent has edited — parallel work about to go wrong.

    Claude's version joins each agent's own tool calls against the code graph, so it needs the
    Claude transcript layout. Codex agents do report the paths they changed (see
    `codex_app._decorate_sub`), but there is no graph-joined clash report for them yet, so an
    empty list is the honest answer rather than a guessed one.
    """
    if engines.native(project_id):
        return []
    from . import subagents
    return subagents.collisions(project_id, root)


# Coalesced: the panes, the rail and the context meter poll this together; see coalesce.py.
@coalesced(ttl=0)
def project_feed(project_id: str, limit: int = 150, session: str = "", kinds: str = "") -> dict:
    """Rich, Claude-Code-style event timeline from a project transcript: thinking
    (with token counts), messages, edits (with diffs), commands, todos, and tool
    results — plus live status (working now / active subagents) and any non-claude
    agent's stdout. ``session`` selects a specific conversation; default = newest."""
    if engines.native(project_id):
        return engines.native(project_id).feed(project_id, limit, session, kinds)
    root, pdir, _ = project_dir(project_id)
    try:
        pdir = pdir.resolve()
        if root.resolve() not in pdir.parents:
            return dict(_EMPTY_FEED)
    except Exception:
        return dict(_EMPTY_FEED)
    if not pdir.exists():
        return dict(_EMPTY_FEED)
    jsonls = list(pdir.glob("*.jsonl"))
    if not jsonls:
        return dict(_EMPTY_FEED)
    newest = None
    if session:
        cand = pdir / f"{os.path.basename(session)}.jsonl"
        if cand.exists() and cand.parent == pdir:
            newest = cand
    if newest is None:
        newest = active_transcript(project_id, jsonls)   # pinned conversation, not "last touched"
    kb = min(24576, 200 + limit * 8)  # up to ~24MB tail so "full history" gets the whole convo
    entries = _tail_entries_cached(newest, kb)
    events: list[dict] = []
    # tool_use id -> the agent card it created, so the result can be folded into the card
    pending_agents: dict[str, dict] = {}
    def _stamp(from_i: int, at: float) -> None:
        for e in events[from_i:]:
            if at and "at" not in e:
                e["at"] = at

    _prev = (0, 0.0)
    for o in entries:
        _stamp(_prev[0], _prev[1])          # whatever the LAST pass appended, however it exited
        typ = o.get("type")
        ts = (o.get("timestamp") or "")[11:19]
        # ...and the same instant as a number, stamped onto whatever this entry appends. See
        # `_turn_rows`: a display clock cannot be compared with the CLI's, or with a subagent's.
        _at = _iso_epoch(o.get("timestamp") or "")
        _prev = (len(events), _at)
        # A LIVE /btw is delivered by the PostToolUse hook, so the transcript records it as an
        # `attachment` carrying the hook's stdout — NOT as a `user` message. Every consumer that
        # filters on kind=="user" (the prompt history, above all) therefore dropped it forever.
        # Unwrap it back into a user line so the note appears where the user looks for it.
        if typ == "attachment":
            note = _btw_from_hook(o)
            if note:
                # Hook context is not a rewindable user turn. Separate each update
                # so its framing stays out of the visible text.
                updates = re.split(r"\n\n(?=↪ (?:Steering update|Side-note))", note)
                for update in updates:
                    events.append({"kind": "user", "ts": ts, "text": update,
                                   "steer": update.startswith("↪ Steering update"), "btw": True})
                continue
            g = _graph_from_hook(o)
            if g:
                events.append({"kind": "graph", "ts": ts,
                               "symbol": g["symbol"], "text": g["where"]})
            continue
        m = o.get("message") or {}
        c = m.get("content")
        usage = m.get("usage") or {}
        out_tok = int(usage.get("output_tokens", 0) or 0)
        if typ == "user":
            uid = o.get("uuid") or ""   # lets the UI rewind the conversation to this message
            if isinstance(c, str) and c.strip():
                events.append({"kind": "user", "ts": ts, "text": _clean(c), "id": uid})
            elif isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and (b.get("text") or "").strip():
                        events.append({"kind": "user", "ts": ts, "text": _clean_md(b["text"]), "id": uid})
                    elif b.get("type") == "tool_result":
                        # A subagent's result belongs on the card that announced it, not in a
                        # wall of text under it — the raw result is the same paragraph again.
                        rec = _subagent_record(o)
                        if rec:
                            card = pending_agents.pop(str(b.get("tool_use_id") or ""), None)
                            if card is None:
                                for k, ev in list(pending_agents.items()):
                                    if not ev["agent"].get("agent_id"):
                                        card = pending_agents.pop(k)
                                        break
                            if card is not None:
                                card["agent"].update(rec)
                                # A background agent is recorded the moment it is LAUNCHED, so a
                                # record alone does not mean it finished.
                                card["agent"]["running"] = (
                                    rec.get("background") and str(rec.get("status") or "") != "completed"
                                )
                            continue
                        txt, ok = _result_text(b)
                        if txt.strip():
                            events.append({"kind": "result", "ts": ts, "ok": ok, "text": _clean_out(txt)})
        elif typ == "assistant" and isinstance(c, list):
            thought = False
            for b in c:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt in ("thinking", "redacted_thinking"):
                    txt = _clean(b.get("thinking") or "")
                    # Claude redacts its chain of thought, so its blocks arrive EMPTY — collapse
                    # those into ONE "Thinking…" marker per turn (otherwise the feed fills with
                    # blank markers). Engines that DO return reasoning text (Qwen via DashScope)
                    # get every block through, or only the first slice would ever be visible.
                    if txt or not thought:
                        thought = True
                        ev = {"kind": "thinking", "ts": ts, "text": txt}
                        if out_tok:
                            ev["tokens"] = out_tok
                        events.append(ev)
                elif bt == "text" and (b.get("text") or "").strip():
                    ev = {"kind": "text", "ts": ts, "text": _clean_md(b["text"])}
                    # the MODEL stopped at its output limit — surface it, otherwise a genuinely
                    # cut-off answer is indistinguishable from a Studio display problem
                    if (m.get("stop_reason") or "") == "max_tokens":
                        ev["cut"] = True
                    events.append(ev)
                elif bt == "tool_use":
                    if b.get("name") in ("AskUserQuestion", "ExitPlanMode"):
                        events.append(_question_event(b, ts))
                    elif b.get("name") in ("Task", "Agent"):
                        ev = _agent_event(b, ts)
                        events.append(ev)
                        pending_agents[str(b.get("id") or "")] = ev
                    else:
                        events.append(_tool_event(b, ts))
        elif typ == "assistant" and isinstance(c, str) and c.strip():
            events.append({"kind": "text", "ts": ts, "text": _clean_md(c)})

    # Give every card its bill and its true state.
    #
    # A background agent is written down the instant it is LAUNCHED, so its record has a prompt
    # and a model and nothing else — no tokens, no tools, and a status that still says "started"
    # weeks later. Its own transcript is the only thing that knows what it did and when it
    # stopped, so the card is finished off from there.
    if any(e.get("kind") == "agent" for e in events):
        try:
            from . import subagents
            rows = {r["agent_id"]: r for r in subagents.list_for(project_id)["agents"]}
            for e in events:
                if e.get("kind") != "agent":
                    continue
                r = rows.get(e["agent"].get("agent_id"))
                if not r:
                    e["agent"]["running"] = False      # no transcript of its own = long gone
                    continue
                e["agent"] = {**e["agent"],
                              **{k: v for k, v in r.items() if v not in (None, "", 0, {}, [])},
                              "running": bool(r.get("running"))}
        except Exception:
            pass

    _stamp(_prev[0], _prev[1])              # the last entry has no next pass to stamp it
    awaiting = _awaiting_input(entries)
    try:
        from . import cc_session
        last_ag = cc_session.last_agent(project_id)
        agent_log = cc_session.log_tail(project_id) if (last_ag and last_ag != "claude") else ""
        companions = cc_session.companion_state(project_id)
    except Exception:
        last_ag, agent_log, companions = "", "", {}
    # A /btw written but not yet consumed by the hook. Surfacing it makes the note visible the
    # INSTANT it is sent, instead of only at the next tool boundary. It disappears from here the
    # moment it is delivered, and reappears above as a real feed line — never both at once.
    btw_pending = ""
    try:
        from . import cc_session
        nf = cc_session._btw_note_file(project_id)
        btw_pending = re.sub(r"(?m)^↪ (?:Steering update|Side-note)[^\n]*\n\n", "", cc_session.live_notes.peek(nf))
    except Exception:
        btw_pending = ""
    return {
        "btw_pending": btw_pending,
        # The summary bar goes on AFTER the kind filter, so hiding tool rows does not also hide
        # the line that says how many there were.
        "lines": _turn_rows(project_id, _filter_kinds(events, kinds))[-limit:],
        "working": (cc_session.is_sending(project_id) if engines.for_feed(project_id).reports_working
                    else _is_working(entries, newest.stat().st_mtime, awaiting, project_id)),
        "tokens": _turn_tokens(entries),
        "agents_active": _active_agent_count(pdir, project_id),
        "agent": last_ag,
        "agent_log": agent_log,
        "companions": companions,
    }


def open_in_editor(path: str, target: str = "vscode") -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"path does not exist: {path}"}
    try:
        if target == "folder":
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys_is_mac():
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return {"ok": True, "opened": "folder"}
        # vscode
        import shutil
        code = shutil.which("code") or shutil.which("code.cmd")
        if not code and os.name == "nt":
            cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
            if cand.exists():
                code = str(cand)
        if not code:
            # fall back to opening the folder
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            return {"ok": True, "opened": "folder", "note": "VS Code 'code' CLI not found; opened folder instead"}
        subprocess.Popen([code, str(p)], shell=(os.name == "nt"))
        return {"ok": True, "opened": "vscode"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sys_is_mac() -> bool:
    import sys
    return sys.platform == "darwin"

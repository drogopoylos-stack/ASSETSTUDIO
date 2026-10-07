"""Plans written in plan mode.

In plan mode the agent may read but not write code, and it saves what it worked out to
``~/.claude/plans/<name>.md`` before asking for approval. Those files are the build
phases — they just had nowhere to be seen. This exposes them, newest first, and matches
each one to a workspace by the file paths it names, so a plan can be sent straight to
that project's chat to build.

Alternate engines (Kimi, Qwen, a user-added provider) keep their own config dir, so
their plans are collected from there too and tagged with the engine that wrote them.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import mission

router = APIRouter(prefix="/api/plans", tags=["plans"])

MAX_BYTES = 400_000        # a plan is prose; anything larger is not one
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s`\"'*|<>]+")
_HEADING = re.compile(r"^\s{0,3}(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_STEP = re.compile(r"^\s{0,3}(?:\d+[.)]|[-*])\s+\*\*(.+?)\*\*", re.MULTILINE)


def _plan_dirs() -> list[tuple[str, Path]]:
    """(engine label, plans dir) for the real Claude config and every alternate engine."""
    out: list[tuple[str, Path]] = []
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    out.append(("claude", (Path(base) if base else Path.home() / ".claude") / "plans"))
    for prefix, dirname in mission.alt_homes().items():
        out.append((prefix[:-2], mission._alt_home(dirname) / "plans"))
    return out


def _title(text: str, fallback: str) -> str:
    m = _HEADING.search(text)
    if m:
        t = m.group(2).strip()
        return re.sub(r"^Plan:\s*", "", t)[:120]
    return fallback


def _phases(text: str) -> list[str]:
    """The build phases, as the plan lists them: '## ' sections, or the bold lead-in of
    each numbered step when the plan is one flat list."""
    heads = [m.group(2).strip() for m in _HEADING.finditer(text) if len(m.group(1)) == 2]
    heads = [h for h in heads if h.lower() not in ("context", "summary", "notes", "background")]
    if len(heads) >= 2:
        return heads[:14]
    return [m.group(1).strip() for m in _STEP.finditer(text)][:14]


def _paths_in(text: str) -> list[str]:
    seen, out = set(), []
    for m in _WIN_PATH.finditer(text):
        p = m.group(0).rstrip(".,);:")
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out[:40]


def _match_root(paths: list[str]) -> str:
    """The workspace root this plan is about — the open root that most of its paths sit under."""
    try:
        from .. import workspace
        roots = [r["path"] for r in workspace.roots() if r.get("path")]
    except Exception:
        return ""
    best, best_n = "", 0
    for r in roots:
        rl = r.lower().rstrip("\\/")
        n = sum(1 for p in paths if p.lower().startswith(rl))
        if n > best_n:
            best, best_n = r, n
    return best


def _entries() -> list[dict]:
    out: list[dict] = []
    for engine, d in _plan_dirs():
        try:
            files = list(d.glob("*.md"))
        except OSError:
            continue
        for f in files:
            try:
                st = f.stat()
                if st.st_size > MAX_BYTES:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            paths = _paths_in(text)
            out.append({
                "id": f"{engine}:{f.name}",
                "engine": engine,
                "file": str(f),
                "name": f.stem,
                "title": _title(text, f.stem.replace("plan-", "").replace("-", " ")),
                "phases": _phases(text),
                "mtime": st.st_mtime,
                "size": st.st_size,
                "root": _match_root(paths),
                "words": len(text.split()),
            })
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


@router.get("")
def list_plans(limit: int = 60):
    return {"plans": _entries()[: max(1, limit)]}


def _resolve(file: str) -> Path:
    p = Path(file)
    for _engine, d in _plan_dirs():
        try:
            if p.resolve().parent == d.resolve():
                return p
        except OSError:
            continue
    raise HTTPException(403, "that file is not in a plans folder")


@router.get("/file")
def read_plan(file: str = Query(...)):
    p = _resolve(file)
    if not p.exists():
        raise HTTPException(404, "plan not found")
    return {"file": str(p), "text": p.read_text(encoding="utf-8", errors="replace")}


class PathBody(BaseModel):
    file: str


@router.post("/delete")
def delete_plan(body: PathBody):
    p = _resolve(body.file)
    p.unlink(missing_ok=True)
    return {"ok": True}

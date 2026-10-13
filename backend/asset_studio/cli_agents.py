"""Another model, launched from the shell — shown as the subagent it is.

WHY THIS FILE EXISTS. Ask the agent for a comic panel and it does not call the Studio's job
queue: the `/studio` skill runs

    codex exec --skip-git-repo-check -s read-only -m gpt-6.1-sol -- "<prompt>"

from a Bash tool call, and Codex draws the panel with its own image tool. That is a delegation by
every measure the Studio cares about — another model, its own prompt, its own bill, two minutes of
wall clock in which the parent is doing nothing at all — and it was the one kind the Studio could
not see. `delegates` covers work handed to the job queue; `subagents` covers the Task tool. A CLI
the agent launched itself fell between the two, so the panel said "No agents running" while GPT
was drawing, and the only trace was a Bash line in the feed that read like any other shell call.

HOW IT IS FOUND. From the parent transcript, which already records every tool call: an assistant
`tool_use` for Bash whose command launches a known model CLI is the start, and the `tool_result`
that carries its id is the end. So the state is EXACT rather than a time window — a card spins
while the call has no result and stops the moment one arrives, which is the one thing a heartbeat
could never get right for a tool call that prints nothing for two minutes.

ONE CALL IS NOT ONE IMAGE. Measured against a real comic run, a single Bash call launches six
`gen.sh` in parallel and waits for all of them:

    cd "<project>" && G=~/.claude/skills/studio/tools/gen.sh && P=$PWD/prompts &&
    ( bash $G $PWD p3-v1-edit $P/edit-3-kegs.txt raw/p3-v1.png & ... & wait )

so the command is parsed rather than pattern-matched: the shell variables are resolved, every
invocation is found, and each panel gets its own card with its own prompt and its own PNG. While
the call is in flight each card asks the disk whether its own image has landed yet, which is how
"4 of 6 drawn" can be shown for work that reports nothing at all until the last one finishes.

It reads the tail of the transcript, the same window the rest of the panel's data comes from, so
this is the work in flight and the recent history, not the whole session.

WHAT IT IS NOT. Nothing is launched, counted twice, or guessed here: a Task agent writes its own
`agent-*.jsonl` and is `subagents`' to report, a queue job is `delegates`', and this is only the
third case.
"""
from __future__ import annotations

import re
import shlex
import time
from pathlib import Path
from typing import Optional

from . import engines, mission

# No result after this long and the call did not survive — the PC slept, the CLI was killed, or
# the backend outlived the shell. A card that spins forever is worse than one that says it stopped.
_RUN_CAP = 1800.0

# How much transcript to read. One record is one message, so this is hundreds of tool calls: the
# running call is always the last few, and the rest is the history the cards show underneath.
_RECORDS = 400

# (pattern, engine id, engine name, what the work is). Order matters: the wrapper is matched
# before the CLI it wraps, because its arguments say far more than the generic form.
_KINDS: tuple[tuple[re.Pattern, str, str, str], ...] = (
    (re.compile(r"gen\.sh\b"), "codex", "GPT", "image"),
    (re.compile(r"\bcodex\s+exec\b"), "codex", "GPT", "task"),
    (re.compile(r"h3_render\.py\b"), "minimax-h3", "MiniMax H3", "video"),
    (re.compile(r"\bgemini\b[^|&]*\s(?:-p|--prompt)\b"), "gemini", "Gemini", "task"),
    (re.compile(r"\bclaude\s+-p\b"), "claude", "Claude", "task"),
)

# A command that only READS the text is not a launch. Searching this repo for "codex exec" must
# not post a card saying GPT is drawing.
_READERS = re.compile(r"^\s*(?:grep|rg|cat|sed|awk|head|tail|echo|ls|find|wc|git)\b")

# Nor is asking the CLI about itself. `codex exec --help | grep image` ran here while the agent
# worked out what the tool could do, and it posted a card that said GPT was drawing.
_NOT_A_RUN = re.compile(r"(?:^|\s)(?:--help|-h|--version|-V)(?:\s|$)")

_MODEL = re.compile(r"(?:^|\s)(?:-m|--model)[=\s]+([A-Za-z0-9._:/-]+)")

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def _kind(cmd: str) -> Optional[tuple[str, str, str]]:
    if _READERS.search(cmd or "") or _NOT_A_RUN.search(cmd or ""):
        return None
    for pat, eng, name, work in _KINDS:
        if pat.search(cmd):
            return eng, name, work
    return None


# ---------------------------------------------------------------------------
# Reading the command the agent actually ran
# ---------------------------------------------------------------------------
def _winpath(p: str) -> str:
    """A Git-Bash path as Windows sees it: `/d/VIDEOS/x` is `D:/VIDEOS/x`, `~` is the home.

    The agent's shell is POSIX and the Studio's filesystem is not, so a path copied straight out
    of the command never exists and every card lost its image.
    """
    s = (p or "").strip().strip('"').strip("'")
    if s.startswith("~"):
        s = str(Path.home()) + s[1:]
    m = re.match(r"^/([A-Za-z])/(.*)$", s)
    if m:
        s = f"{m.group(1).upper()}:/{m.group(2)}"
    return s


def _shell_vars(parts: list[str]) -> dict[str, str]:
    """The variables the command set itself, plus the directory it changed to.

    `$PWD` is the whole point: every `gen.sh` call names its project folder as `$PWD`, set by a
    `cd` at the head of the same line.
    """
    out: dict[str, str] = {}
    for i, tok in enumerate(parts):
        if tok == "cd" and i + 1 < len(parts):
            out["PWD"] = _winpath(_expand(parts[i + 1], out))
            continue
        m = _ASSIGN.match(tok)
        if m and m.group(2):
            out[m.group(1)] = _expand(m.group(2), out)
    return out


def _expand(tok: str, vars: dict[str, str]) -> str:
    """`$P/edit-3.txt` with `P=$PWD/prompts` and `PWD=D:/VIDEOS/x`. Three passes is plenty for a
    command line; an unknown name is left alone rather than blanked, so it stays recognisable."""
    out = tok or ""
    for _ in range(3):
        m = _VAR.search(out)
        if not m or m.group(1) not in vars:
            break
        out = out[:m.start()] + vars[m.group(1)] + out[m.end():]
    return out


def _invocations(parts: list[str], vars: dict[str, str]) -> list[tuple[str, str, str]]:
    """Every `gen.sh <project dir> <label> <prompt file>` in one command line.

    Matched on the EXPANDED token, because the script is usually reached through a variable
    (`bash $G ...`): counting arguments forward from the literal text `gen.sh` produced a label
    of "&&" on every real command.
    """
    out: list[tuple[str, str, str]] = []
    for i, tok in enumerate(parts):
        if not _expand(tok, vars).endswith("gen.sh") or i + 3 >= len(parts):
            continue
        base = _winpath(_expand(parts[i + 1], vars))
        label = _expand(parts[i + 2], vars)
        pfile = _winpath(_expand(parts[i + 3], vars))
        if not label or any(ch in label for ch in "/\\=\"'&$"):
            continue
        out.append((base, label, pfile))
    return out


def _launched(eng: str, work: str, parts: list[str]) -> bool:
    """Is the CLI a COMMAND here, or only mentioned in text that the shell passes on?

    `python -c "...codex exec..."` is ONE quoted argument, so the launcher is not a token of its
    own — which is exactly how a script that talks ABOUT the CLI posted three cards saying GPT
    was drawing. A launch is a bare token in command position; a mention never is.
    """
    toks = [t.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for t in parts]
    if work == "video":
        return "h3_render.py" in toks
    if eng == "codex":
        return any(t == "codex" and i + 1 < len(toks) and toks[i + 1] == "exec"
                   for i, t in enumerate(toks))
    return eng in toks


def _text_of(content) -> str:
    """A tool_result's content, which is a string or a list of blocks depending on the tool."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(str(b.get("text") or ""))
            elif isinstance(b, str):
                out.append(b)
        return "\n".join(out)
    return ""


def _read_prompt(pfile: str) -> str:
    try:
        p = Path(pfile)
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        pass
    return ""


def _landed(path: str, after: float) -> float:
    """When this file was written, if it was written by this call. 0 means not yet."""
    try:
        st = Path(path).stat()
    except OSError:
        return 0.0
    # Written BEFORE the call started is a previous version of the same panel, not this result.
    return st.st_mtime if st.st_mtime >= after - 2 else 0.0


# ---------------------------------------------------------------------------
# One shell delegation as a subagent card
# ---------------------------------------------------------------------------
def _row(agent_id: str, eng: str, eng_name: str, work: str, model: str, label: str,
         prompt: str, cmd: str, started: float, finished: float, status: str, live: bool,
         result: str, out_file: str, log_file: str, session: str) -> dict:
    short = cmd if len(cmd) <= 140 else cmd[:137] + "…"
    desc = f"{work} · {eng_name}" + (f" ({model})" if model else "") + (f" · {label}" if label else "")
    return {
        "agent_id": agent_id,
        "description": desc,
        "agent_type": eng,
        "model": model or eng,
        # Neither a Task agent nor a queue job: a CLI this conversation launched itself.
        "source": "cli",
        "status": status,
        "running": bool(live),
        "open": bool(live),
        "prompt": prompt or short,
        "result": (result or "")[:4000],
        "output_file": out_file,
        "ms": int(1000 * ((finished or time.time()) - started)) if started else 0,
        "tools": 0,
        "tokens": 0,
        "cost": 0.0,
        "ts": started,
        "session": session,
        "updated": finished or started,
        "idle_s": int(time.time() - (finished or started)),
        "activity": ({"tool": "shell", "detail": label or short, "phase": "tool"} if live else None),
        "command": short,
        "log_file": log_file,
    }


def _cards_for(use: dict, started: float, res: Optional[tuple[str, bool, float]],
               session: str) -> list[dict]:
    """One Bash tool call as the cards it deserves — one per model invocation inside it."""
    cmd = str((use.get("input") or {}).get("command") or "")
    k = _kind(cmd)
    if k is None:
        return []
    eng, eng_name, work = k
    uid = str(use.get("id") or "")
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        parts = cmd.split()

    model = ""
    m = _MODEL.search(cmd)
    if m:
        model = m.group(1)

    text, is_err, finished = (res or ("", False, 0.0))
    call_live = res is None and (time.time() - started) < _RUN_CAP
    if res is None:
        call_status = "running" if call_live else "stopped"
    else:
        call_status = "failed" if is_err else "succeeded"

    out: list[dict] = []
    if work == "image":
        # The invocations ARE the check for this one: a command that names `gen.sh` without
        # calling it — a test, a comment, a path being copied — launches nothing.
        for base, label, pfile in _invocations(parts, _shell_vars(parts)):
            png = str(Path(base) / "raw" / f"{label}.png") if base else ""
            log = str(Path(base) / "logs" / f"{label}.log") if base else ""
            at = _landed(png, started) if png else 0.0
            # ITS OWN image, not the call's: six panels in one call finish minutes apart, and the
            # shell says nothing until the last. The file landing is the only honest progress.
            if call_live:
                live = not at
                status = "running" if live else "succeeded"
            else:
                live, status = False, call_status
            out.append(_row(f"cli-{uid}-{label}", eng, eng_name, work, model, label,
                            _read_prompt(pfile), cmd, started, at or finished, status, live,
                            text if not call_live else "", png if at else "", log, session))
    if out:
        return out
    if work == "image" or not _launched(eng, work, parts):
        return []
    return [_row("cli-" + uid, eng, eng_name, work, model, "", "", cmd, started, finished,
                 call_status, call_live, text, "", "", session)]


def _session_file(project_id: str, session: str = "") -> Optional[Path]:
    """The parent transcript to read: the named conversation, else the newest."""
    try:
        root, pdir, _ = mission.project_dir(project_id)
        pdir = pdir.resolve()
        if root.resolve() not in pdir.parents:
            return None
    except Exception:
        return None
    if session:
        f = pdir / f"{session}.jsonl"
        return f if f.is_file() else None
    best: Optional[Path] = None
    best_at = -1.0
    try:
        for e in pdir.iterdir():
            if e.suffix == ".jsonl" and e.is_file():
                at = e.stat().st_mtime
                if at > best_at:
                    best, best_at = e, at
    except OSError:
        return None
    return best


def fresh(project_id: str, window: float = _RUN_CAP) -> bool:
    """Has this conversation written recently enough to be worth reading at all?

    THE SWEEP ASKS THIS FOR EVERY FOLDER ON DISK, so it must stay one stat. A shell delegation
    can only be in flight if the transcript that launched it was touched inside the window: the
    launching `tool_use` is itself the last thing written while the call runs.
    """
    f = _session_file(project_id)
    if f is None:
        return False
    try:
        return time.time() - f.stat().st_mtime < window
    except OSError:
        return False


def cards(project_id: str, session: str = "") -> list[dict]:
    """This conversation's shell delegations as subagent cards, newest first."""
    if engines.native(project_id):
        # A Codex conversation records its shell calls in its own format; its reader owns them.
        return []
    f = _session_file(project_id, session)
    if f is None:
        return []
    from . import subagents
    try:
        entries = subagents._entries(f, _RECORDS)
    except Exception:
        return []

    uses: list[tuple[dict, float]] = []
    results: dict[str, tuple[str, bool, float]] = {}
    for e in entries:
        at = subagents._epoch(str(e.get("timestamp") or ""))
        content = ((e.get("message") or {}).get("content"))
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                uses.append((b, at))
            elif b.get("type") == "tool_result":
                tid = str(b.get("tool_use_id") or "")
                if tid:
                    results[tid] = (_text_of(b.get("content")), bool(b.get("is_error")), at)

    sess = f.stem
    out: list[dict] = []
    for use, at in uses:
        try:
            out += _cards_for(use, at, results.get(str(use.get("id") or "")), sess)
        except Exception:
            continue
    out.sort(key=lambda c: c.get("ts") or 0, reverse=True)
    return out


def merge(project_id: str, data: dict, session: str = "") -> dict:
    """Add this conversation's shell delegations to a subagents answer, totals included."""
    try:
        cs = cards(project_id, session)
    except Exception:
        return data
    if not cs:
        return data
    agents = list(data.get("agents") or []) + cs
    agents.sort(key=lambda a: a.get("ts") or 0, reverse=True)
    out = dict(data)
    out["agents"] = agents
    out["running"] = int(data.get("running") or 0) + sum(1 for c in cs if c.get("running"))
    out["open"] = int(data.get("open") or 0) + sum(1 for c in cs if c.get("open"))
    out["shelled"] = len(cs)
    return out


def detail(project_id: str, agent_id: str, limit: int = 400) -> Optional[dict]:
    """The pane view of one shell delegation: the card, the command, and the CLI's own log.

    `None` when the id is not one of these, so the caller falls through to the engine's reader.
    """
    if not str(agent_id).startswith("cli-"):
        return None
    card = next((c for c in cards(project_id) if c.get("agent_id") == agent_id), None)
    if card is None:
        return None
    lines = [{"kind": "tool", "ts": _hms(card.get("ts") or 0), "at": card.get("ts") or 0,
              "text": card.get("command") or ""}]
    if card.get("prompt") and card.get("prompt") != card.get("command"):
        lines.append({"kind": "user", "ts": "", "at": 0, "text": card["prompt"]})
    # The CLI's own transcript, when the wrapper kept one. This is the only place the other
    # model's reasoning is visible at all, which is the reason to open the pane.
    log = card.get("log_file") or ""
    if log:
        try:
            tail = Path(log).read_text(encoding="utf-8", errors="replace")[-40_000:]
            for chunk in tail.splitlines()[-limit:]:
                if chunk.strip():
                    lines.append({"kind": "tool", "ts": "", "at": 0, "text": chunk})
        except OSError:
            pass
    if card.get("result"):
        lines.append({"kind": "result", "ts": "", "at": 0, "text": card["result"]})
    if card.get("output_file"):
        lines.append({"kind": "text", "ts": "", "at": 0, "text": "Wrote " + card["output_file"]})
    return {"ok": True, "agent": card, "lines": lines[-limit:], "source": "cli"}


def _hms(at: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(at))
    except (ValueError, OSError):
        return ""


_COUNT_TTL = 2.0
_count_cache: dict[str, tuple[float, int]] = {}


def running_count(project_id: str) -> int:
    """How many shell delegations this conversation has in flight.

    THE LIVE POLL ASKS THIS EVERY 1.5 SECONDS, so it is memoed twice over. The transcript tail
    behind it is cached on the file's own mtime and size, and that cache HOLDS for exactly the
    case that matters: a parent blocked on a two-minute `codex exec` writes nothing at all, so
    the file does not move and the answer is a dictionary lookup.
    """
    now = time.time()
    hit = _count_cache.get(project_id)
    if hit and now - hit[0] < _COUNT_TTL:
        return hit[1]
    try:
        n = sum(1 for c in cards(project_id) if c.get("running"))
    except Exception:
        n = 0
    if len(_count_cache) > 64:
        _count_cache.clear()
    _count_cache[project_id] = (now, n)
    return n

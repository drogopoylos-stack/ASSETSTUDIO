"""Real terminals, so another agent's CLI keeps its own face.

Claude Code streams into our chat because we parse its ``stream-json``. Every other coding CLI
draws its own full-screen interface instead: Codex, OpenCode, Gemini and the rest paint boxes,
spinners and colour with escape codes. Re-rendering that inside our message list would mean
reimplementing each one, and then breaking on their next release. So they do not get our look.
They get a real pseudo-terminal and draw themselves, exactly as they do in a console.

That is also what makes this the safest thing to add: nothing in here touches ``cc_session``,
the chat, or the agent picker. A terminal that breaks takes down a terminal.

The blocking read lives on a reader thread and NEVER on the event loop. ``pty.read()`` waits
until bytes arrive — one call of it on the loop would freeze the whole backend, which is the
one failure this app is least allowed to have.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Optional

# 256 KB of scrollback per terminal. Enough to redraw a full-screen TUI after you switch tabs
# and come back, small enough that ten forgotten terminals cannot eat real memory.
_SCROLLBACK = 256 * 1024
_IDLE_KILL = 60 * 60.0      # nobody has looked at it for an hour — close it
_REAP_TICK = 60.0

_LOCK = threading.Lock()
_TERMS: dict[str, "_Term"] = {}
_reaper_started = False


def available() -> tuple[bool, str]:
    """Can this machine give us a pty at all? (message explains why not)."""
    if os.name == "nt":
        try:
            import winpty  # noqa: F401
            return True, ""
        except Exception as e:
            return False, f"pywinpty is not installed ({e}). pip install pywinpty"
    try:
        import pty  # noqa: F401
        return True, ""
    except Exception as e:
        return False, str(e)


class _Term:
    __slots__ = ("id", "proc", "cmd", "cwd", "buf", "pending", "lock", "seen",
                 "started", "exited", "label", "size", "owns_project")

    def __init__(self, tid: str, proc, cmd: list, cwd: str, label: str):
        self.id = tid
        self.proc = proc
        self.cmd = cmd
        self.cwd = cwd
        self.label = label
        self.buf = ""            # scrollback, for a client that reconnects
        self.pending = ""        # not yet handed to the socket
        self.lock = threading.Lock()
        self.seen = time.time()
        self.started = time.time()
        self.exited: Optional[int] = None
        self.size = (0, 0)       # last size we set — a reconnect asking for the SAME one
                                 # is a no-op to the pty, and a no-op repaints nothing
        # Set when this terminal holds a Studio project's CONVERSATION (claude --resume). Such
        # a terminal is one of the two possible owners of that transcript, so the chat has to
        # be able to find it and close it before taking the conversation back.
        self.owns_project = ""

    # -- reader thread -------------------------------------------------------
    def _pump(self) -> None:
        try:
            while True:
                data = self.proc.read(8192)
                if not data:
                    break
                with self.lock:
                    # Both are capped. `pending` is drained every 30 ms by an attached client,
                    # so it only grows when NOBODY is watching — and a TUI left redrawing with
                    # no client would otherwise grow it without limit, forever.
                    self.pending = (self.pending + data)[-_SCROLLBACK:]
                    self.buf = (self.buf + data)[-_SCROLLBACK:]
        except EOFError:
            pass
        except Exception as e:                       # a dead pty must not kill the thread quietly
            with self.lock:
                self.pending += f"\r\n\x1b[31m[terminal error: {e}]\x1b[0m\r\n"
        finally:
            code = None
            try:
                code = self.proc.exitstatus
            except Exception:
                pass
            with self.lock:
                self.exited = 0 if code is None else int(code)
                self.pending += f"\r\n\x1b[90m[process exited{'' if code is None else f' ({code})'}]\x1b[0m\r\n"


def _spawn(cmd: list, cwd: str, cols: int, rows: int, env: Optional[dict] = None):
    # env=None means inherit, on both backends — so every existing caller is unchanged.
    if os.name == "nt":
        import winpty
        return winpty.PtyProcess.spawn(cmd, cwd=cwd or None, env=env, dimensions=(rows, cols))
    from ptyprocess import PtyProcess          # type: ignore
    p = PtyProcess.spawn(cmd, cwd=cwd or None, env=env, dimensions=(rows, cols))
    return p


def create(cmd: list, cwd: str = "", cols: int = 100, rows: int = 30, label: str = "",
           env: Optional[dict] = None, owns_project: str = "") -> dict:
    """Start one terminal. Returns {ok, id} or {ok: False, error}."""
    ok, why = available()
    if not ok:
        return {"ok": False, "error": why}
    if not cmd:
        return {"ok": False, "error": "no command"}
    try:
        proc = _spawn(list(cmd), cwd, max(20, int(cols)), max(5, int(rows)), env)
    except Exception as e:
        return {"ok": False, "error": f"could not start {cmd[0]}: {e}"}
    tid = uuid.uuid4().hex[:12]
    t = _Term(tid, proc, list(cmd), cwd, label or cmd[0])
    # Record what the pty was actually born with, so the client's first resize — which asks
    # for exactly these numbers — is recognised as "I attached, please redraw".
    t.size = (max(20, int(cols)), max(5, int(rows)))
    t.owns_project = owns_project or ""
    threading.Thread(target=t._pump, daemon=True, name=f"pty-{tid}").start()
    with _LOCK:
        _TERMS[tid] = t
    _start_reaper()
    return {"ok": True, "id": tid, "label": t.label}


def drain(tid: str) -> Optional[str]:
    """Everything written since the last call. None when the terminal is gone."""
    with _LOCK:
        t = _TERMS.get(tid)
    if t is None:
        return None
    with t.lock:
        out, t.pending = t.pending, ""
    t.seen = time.time()
    return out


def scrollback(tid: str) -> str:
    """What to replay into a client that just attached.

    The buffer is a ring: once it wraps, its first bytes are the TAIL of an escape sequence
    whose ESC was thrown away, and a terminal fed that renders nothing you can read. It is not
    hypothetical — `claude --resume` on a long conversation replays megabytes, so the ring has
    always wrapped by the time you look. Start at the first ESC instead: everything before it
    is by definition the remains of a cut sequence.
    """
    with _LOCK:
        t = _TERMS.get(tid)
    if t is None:
        return ""
    with t.lock:
        buf = t.buf
    if len(buf) < _SCROLLBACK:          # never wrapped — it is whole, send it as it is
        return buf
    i = buf.find("\x1b")
    return buf[i:] if i >= 0 else ""


def attach(tid: str) -> Optional[str]:
    """What a client that just connected should draw — and nothing it will be handed twice.

    `pending` is the not-yet-delivered TAIL of the same bytes already in `buf`, so replaying the
    scrollback and then draining pending would draw that tail a second time, over a screen a
    full-screen program had just finished painting. Take one, drop the other, under one lock.
    """
    with _LOCK:
        t = _TERMS.get(tid)
    if t is None:
        return None
    with t.lock:
        t.pending = ""
        buf = t.buf
    t.seen = time.time()
    if len(buf) < _SCROLLBACK:          # never wrapped — it is whole, send it as it is
        return buf
    i = buf.find("\x1b")
    return buf[i:] if i >= 0 else ""


def for_project(project_id: str) -> Optional[str]:
    """The id of the live terminal holding this project's conversation, if there is one."""
    if not project_id:
        return None
    with _LOCK:
        for t in _TERMS.values():
            if t.owns_project == project_id and t.exited is None:
                return t.id
    return None


def write(tid: str, data: str) -> bool:
    with _LOCK:
        t = _TERMS.get(tid)
    if t is None or t.exited is not None:
        return False
    try:
        t.proc.write(data)
        t.seen = time.time()
        return True
    except Exception:
        return False


def resize(tid: str, cols: int, rows: int) -> bool:
    """Set the pty size — and when the size does not change, make the app repaint anyway.

    A full-screen program owns the screen and only redraws when something tells it to. A
    client that reattaches sends the size it already has, the pty shrugs, and the pane stays
    blank however healthy the process is. A size change is the one portable way to ask for a
    redraw, so nudge it by a column and put it straight back. Measured: a same-size resize
    produced nothing, a changed one produced a complete clean frame.
    """
    with _LOCK:
        t = _TERMS.get(tid)
    if t is None:
        return False
    c, r = max(20, int(cols)), max(5, int(rows))
    try:
        if (c, r) == t.size:
            t.proc.setwinsize(r, max(20, c - 1))
        t.proc.setwinsize(r, c)
        t.size = (c, r)
        return True
    except Exception:
        return False


def kill(tid: str) -> bool:
    with _LOCK:
        t = _TERMS.pop(tid, None)
    if t is None:
        return False
    try:
        t.proc.terminate(force=True)
    except Exception:
        pass
    return True


def close_for_project(project_id: str) -> int:
    """Close any terminal holding this project's conversation. Returns how many.

    The other half of the one-owner rule. A terminal deliberately outlives its socket, which is
    right for an agent running its own thing and wrong for this one: leave the pane without
    pressing "Back to chat" and the TUI keeps the conversation open while the chat resumes it
    too — two processes appending to one transcript. The chat calls this before it takes over.
    """
    if not project_id:
        return 0
    with _LOCK:
        doomed = [t for t in _TERMS.values() if t.owns_project == project_id]
        for t in doomed:
            _TERMS.pop(t.id, None)
    for t in doomed:
        try:
            t.proc.terminate(force=True)
        except Exception:
            pass
    return len(doomed)


def info(tid: str) -> dict:
    with _LOCK:
        t = _TERMS.get(tid)
    if t is None:
        return {"ok": False, "error": "no such terminal"}
    return {"ok": True, "id": t.id, "label": t.label, "cwd": t.cwd,
            "cmd": t.cmd, "exited": t.exited, "started": t.started}


def listing() -> list[dict]:
    with _LOCK:
        terms = list(_TERMS.values())
    return [{"id": t.id, "label": t.label, "cwd": t.cwd, "exited": t.exited,
             "started": t.started} for t in terms]


def _start_reaper() -> None:
    """Close terminals nobody is watching. A forgotten agent CLI is a process holding a GPU
    lock, a file lock and a token budget, so leaving them to accumulate is not neutral."""
    global _reaper_started
    with _LOCK:
        if _reaper_started:
            return
        _reaper_started = True

    def loop() -> None:
        while True:
            time.sleep(_REAP_TICK)
            try:
                now = time.time()
                with _LOCK:
                    stale = [k for k, t in _TERMS.items()
                             if now - t.seen > _IDLE_KILL
                             or (t.exited is not None and now - t.seen > 300)]
                for k in stale:
                    kill(k)
            except Exception:               # a background keeper must never die
                pass

    threading.Thread(target=loop, daemon=True, name="pty-reaper").start()


# ---------------------------------------------------------------------------
# What you can launch
# ---------------------------------------------------------------------------
# Deliberately a SEPARATE list from agents.AGENTS. That one feeds the chat, where every entry
# has to stream into our message list; adding a terminal-only CLI there would put an option in
# the chat picker that the chat cannot render. These never touch that path.
#
# Only CLIs that were checked to exist and install on this platform are listed. A button that
# installs nothing is worse than no button — it looks ready and is not.
_NPM = "npm install -g "

AGENT_CLIS: list[dict] = [
    {"id": "claude", "name": "Claude Code", "color": "#d97757", "bin": "claude",
     "install": _NPM + "@anthropic-ai/claude-code",
     "note": "The same agent as the chat, in its own full terminal interface."},
    {"id": "codex", "name": "Codex", "color": "#10a37f", "bin": "codex",
     "install": _NPM + "@openai/codex",
     "note": "OpenAI's coding agent. Sign in inside the terminal on first run."},
    {"id": "gemini", "name": "Gemini CLI", "color": "#4285f4", "bin": "gemini",
     "install": _NPM + "@google/gemini-cli",
     "note": "Google's coding agent. Free tier with a Google account."},
    {"id": "opencode", "name": "OpenCode", "color": "#f59e0b", "bin": "opencode",
     "install": _NPM + "opencode-ai",
     "note": "Open-source agent; brings its own model picker (Anthropic, OpenAI, local)."},
    {"id": "cursor", "name": "Cursor Agent", "color": "#9ca3af", "bin": "cursor-agent",
     "install": "curl https://cursor.com/install -fsS | bash",
     "note": "Cursor's CLI agent. The installer needs bash — Git Bash works."},
    {"id": "shell", "name": "Terminal", "color": "#7c8aa5", "bin": "",
     "install": "", "note": "A plain shell in the project folder."},
]


def _shell_cmd() -> list:
    if os.name == "nt":
        return [os.environ.get("COMSPEC") or "cmd.exe"]
    return [os.environ.get("SHELL") or "/bin/bash"]


def _resolve(binary: str) -> Optional[str]:
    """Absolute path to a CLI, preferring a form Windows can actually execute.

    npm writes THREE files for one command: `codex` (a `#!/bin/sh` script, for Git Bash),
    `codex.cmd` and `codex.ps1`. Python's `shutil.which` tries the bare name FIRST on Windows,
    so it returned the shell script — and spawning that gives

        %1 is not a valid Win32 application

    which is how Codex, Gemini and OpenCode all reported themselves as installed and then
    refused to open. Measured, not guessed: the bare path fails to spawn and the .cmd runs.
    So the runnable extensions are tried first, and the bare name is only a fallback for a
    platform where it means something.
    """
    if not binary:
        return None
    if os.name == "nt":
        for ext in (".cmd", ".exe", ".bat", ".com", ".ps1"):
            p = shutil.which(binary + ext)
            if p:
                return p
    return shutil.which(binary)


def agents() -> list[dict]:
    """The launcher list, with live detection."""
    out = []
    for a in AGENT_CLIS:
        path = _shell_cmd()[0] if a["id"] == "shell" else _resolve(a["bin"])
        out.append({**{k: a[k] for k in ("id", "name", "color", "install", "note")},
                    "installed": bool(path), "path": path or ""})
    return out


_MODEL_ID = re.compile(r"[A-Za-z0-9._:/\[\]-]{1,80}")


def launch(agent_id: str, cwd: str = "", cols: int = 100, rows: int = 30,
           model: str = "", effort: str = "") -> dict:
    """Start one of the known CLIs in its own terminal.

    Codex takes the chat box's model and effort for this folder, so the terminal answers on the
    same model the chat would. The model id goes onto a command line, so anything that is not
    shaped like a model id is refused rather than passed through."""
    a = next((x for x in AGENT_CLIS if x["id"] == agent_id), None)
    if a is None:
        return {"ok": False, "error": f"unknown agent {agent_id!r}"}
    if a["id"] == "shell":
        return create(_shell_cmd(), cwd, cols, rows, label=a["name"])
    path = _resolve(a["bin"])
    if not path:
        return {"ok": False, "error": f"{a['name']} is not installed", "install": a["install"]}
    cmd = _runnable(path)
    if a["id"] == "codex":
        m = (model or "").strip()
        if m and m != "default":
            if not _MODEL_ID.fullmatch(m):
                return {"ok": False, "error": f"not a model id: {m!r}"}
            cmd += ["-m", m]
        from . import agents
        eff = agents._codex_effort(effort)
        if eff:
            cmd += ["-c", f'model_reasoning_effort="{eff}"']
    return create(cmd, cwd, cols, rows, label=a["name"])


def _runnable(path: str) -> list:
    """The argv that actually starts this file. A shim is a script, not a program."""
    low = (path or "").lower()
    if os.name != "nt":
        return [path]
    if low.endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC") or "cmd.exe", "/c", path]
    if low.endswith(".ps1"):
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    return [path]


def install(agent_id: str, cwd: str = "", cols: int = 100, rows: int = 30) -> dict:
    """Run the install in a terminal, so you watch it happen instead of waiting on a spinner
    that cannot tell you which of npm's twelve failure modes you hit."""
    a = next((x for x in AGENT_CLIS if x["id"] == agent_id), None)
    if a is None or not a.get("install"):
        return {"ok": False, "error": "nothing to install"}
    if os.name == "nt":
        cmd = [os.environ.get("COMSPEC") or "cmd.exe", "/k", a["install"]]
    else:
        cmd = [os.environ.get("SHELL") or "/bin/bash", "-lc", a["install"] + "; exec $SHELL"]
    return create(cmd, cwd, cols, rows, label=f"install {a['name']}")

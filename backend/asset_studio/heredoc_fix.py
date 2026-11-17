"""Move a long heredoc body out of a Bash command, before the CLI runs it.

WHY. On this Windows machine the CLI cannot carry a long Bash command intact. Measured over every
transcript on 2026-11-17 (6,944 Bash commands with a heredoc), by command length:

    under 4,000 chars    6,392 runs     0.1% failed
    4,000 - 8,000          438 runs     3.2% failed
    8,000 - 12,000          71 runs    80.3% failed
    over 12,000             44 runs    84.1% failed

The failure reads `unexpected EOF while looking for matching `''`, and most of the "passes" above
8,000 were broken too: `ENAMETOOLONG`, or `here-document ... delimited by end-of-file`, which means
the body was CUT and a file may have been written short without any error. Each failure makes the
agent generate and send the whole file again, often 300 lines.

WHAT. A PreToolUse hook on Bash (called from btw_hook.py). When the command is long, each heredoc
with a QUOTED delimiter (`<<'EOF'`, `<<"EOF"`, `<<-'EOF'`) has its body written to a file, and the
operator is replaced with `< 'that file'`. A quoted delimiter means bash reads the body literally,
with no expansion, so feeding the same bytes from a file is the same input. The command that
reaches the CLI is then short.

SAFE. Only in bypassPermissions mode, where the rewritten command asks nobody anything the
original would have asked. Never when a command has an UNQUOTED heredoc (its body is expanded, so a
file is not the same thing). Never when a body has no terminator line. On any doubt, or any error,
the command goes ahead untouched.

Standard library only: the hook runs under the backend's Python without the app.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

MIN_CHARS = 4000            # below this the measured failure rate is 0.1%: leave it alone
KEEP_S = 24 * 3600.0
_QUOTED = re.compile(r"(?<![<\w])<<(-?)[ \t]*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2")
_UNQUOTED = re.compile(r"(?<![<\w])<<-?[ \t]*[A-Za-z_\\]")


def _dir() -> Path:
    return Path(tempfile.gettempdir()) / "studio-heredoc"


def _sweep(d: Path) -> None:
    now = time.time()
    try:
        for f in d.iterdir():
            try:
                if now - f.stat().st_mtime > KEEP_S:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _write(body: str) -> Optional[str]:
    """The body as a file, named by its content; the path as bash should see it, or None."""
    data = body.encode("utf-8")
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / (hashlib.sha256(data).hexdigest()[:24] + ".txt")
    if not (f.exists() and f.stat().st_size == len(data)):
        tmp = f.with_suffix(".part%d" % os.getpid())
        tmp.write_bytes(data)
        os.replace(tmp, f)
    else:
        os.utime(f)              # keep a reused file from the sweep
    path = str(f).replace("\\", "/")
    return None if "'" in path else path


def rewrite_command(cmd: str) -> Optional[str]:
    """The same command with every quoted heredoc body moved to a file, or None to leave it."""
    if len(cmd) < MIN_CHARS or "<<" not in cmd:
        return None
    lines = cmd.split("\n")
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        ops = list(_QUOTED.finditer(line))
        if _UNQUOTED.search(_QUOTED.sub("", line)):
            return None                      # an expanded body: a file would not be the same
        if not ops:
            out.append(line)
            i += 1
            continue
        j = i + 1
        reps = []
        for m in ops:                        # bodies follow in the order the operators appear
            strip_tabs = m.group(1) == "-"
            term = m.group(3)
            body: list[str] = []
            while j < len(lines):
                cur = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if cur == term:
                    break
                body.append(cur)
                j += 1
            else:
                return None                  # no terminator: bash's own reading is unclear
            j += 1
            path = _write("".join(b + "\n" for b in body))
            if not path:
                return None
            reps.append((m.span(), "< '%s'" % path))
        for (a, b), rep in reversed(reps):
            line = line[:a] + rep + line[b:]
        out.append(line)
        changed = True
        i = j
    new = "\n".join(out)
    if not changed or len(new) >= len(cmd):
        return None                          # nothing moved, or only tiny bodies: no gain
    _sweep(_dir())
    return new


def redirect(payload: dict) -> Optional[dict]:
    """The Bash tool input with long heredocs moved to files, or None (run it as it is)."""
    try:
        if str(payload.get("tool_name") or "") != "Bash":
            return None
        if str(payload.get("permission_mode") or "") != "bypassPermissions":
            return None
        ti = payload.get("tool_input") or {}
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return None
        new = rewrite_command(cmd)
        if not new:
            return None
        return {**ti, "command": new}
    except Exception:
        return None

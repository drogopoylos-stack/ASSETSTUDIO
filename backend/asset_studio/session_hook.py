"""Claude Code hook: record the two session events the transcript reports too late.

COMPACTION. After a `/compact` the newest assistant turn in the transcript is still the
PRE-compact one — huge — while the live context has been reset to about the summary. The meter
therefore had to guess, and it guessed with a constant (`compact_base_tokens`, 25000). The
`PostCompact` payload carries the actual `compact_summary`: the text that BECOMES the new
context. That turns the guess into a measurement. `PreCompact` marks the start, which is what
makes "compacting…" exact instead of inferred from whether the process looks busy.

PHASES. The Task tools mirror each phase to `<home>/tasks/<session>/N.json`, and the Studio reads
those files. But a file is REWRITTEN every time its phase changes status, so its timestamps say
when the phase was last touched, not when the run that created it began. Grouping runs off those
times is what welded eleven days of phases into one list. `TaskCreated` fires once, at creation,
and never again — an immutable record of when each run really started.

One JSON line is appended per event. Append-only on purpose: several hook processes can be alive
at once (a subagent fires session hooks too), and a read-modify-write would drop events. Standalone
— no package imports, runs under any Python.
"""
import json
import os
import sys
import time

# Enough of the summary to show what a compaction kept, without copying a novel into the ledger
# on every compact. The LENGTH is recorded in full and separately; this is only for display.
_SUMMARY_KEEP = 600
# A ledger that grows without bound would be read on every poll. Compaction is rare and phases
# are per run, so this holds many sessions' worth; the backend trims it when it passes this.
_MAX_BYTES = 512 * 1024

_EVENTS = ("PreCompact", "PostCompact", "TaskCreated", "TaskCompleted", "InstructionsLoaded")

# NOT registered, on purpose: WorktreeCreate and WorktreeRemove.
#
# They read as notifications and are not. The CLI takes the hook's STDOUT as the path of the
# worktree it should use, and refuses the whole operation without one — "WorktreeCreate hook
# failed: hook succeeded but returned no worktree path (command: echo the path to stdout)". They
# exist so a team can substitute its own worktree manager. Registering a recorder on them would
# make every `claude --worktree` and `/worktree` throw. The rail follows worktrees by re-reading
# git instead, which costs a subprocess and cannot break anything.


def _row(p: dict) -> dict:
    """The one line this event is worth keeping, or {} for an event we did not ask for."""
    ev = str(p.get("hook_event_name") or p.get("hookEventName") or "")
    if ev not in _EVENTS:
        return {}
    row = {
        "at": round(time.time(), 3),
        "event": ev,
        "session": str(p.get("session_id") or ""),
        # A subagent fires session hooks too. Recording which agent it was lets the panel keep
        # the main session's phases separate from a Task agent's, rather than dropping either.
        "agent": str(p.get("agent_id") or ""),
    }
    if ev in ("PreCompact", "PostCompact"):
        row["trigger"] = str(p.get("trigger") or "")
    if ev == "PostCompact":
        s = p.get("compact_summary")
        s = s if isinstance(s, str) else ""
        row["summary_chars"] = len(s)
        row["summary"] = s[:_SUMMARY_KEEP]
    if ev == "InstructionsLoaded":
        # Every CLAUDE.md, memory file and imported fragment a session took on, with the reason
        # it was taken on. The token weight is worked out on the reading side from the file
        # itself — the hook stays a short write that cannot slow a session start down.
        row["file_path"] = str(p.get("file_path") or "")
        row["memory_type"] = str(p.get("memory_type") or "")
        row["load_reason"] = str(p.get("load_reason") or "")
        row["parent"] = str(p.get("parent_file_path") or "")
        row["trigger"] = str(p.get("trigger_file_path") or "")
    if ev in ("TaskCreated", "TaskCompleted"):
        row["task_id"] = str(p.get("task_id") or "")
        row["subject"] = str(p.get("task_subject") or "")[:200]
        if ev == "TaskCreated":
            row["description"] = str(p.get("task_description") or "")[:400]
    return row


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    row = _row(payload if isinstance(payload, dict) else {})
    if not row:
        return 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # One short line, opened in append mode: the OS keeps concurrent appends whole, so two
        # hook processes finishing together cannot interleave or overwrite each other.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    # A hook must never fail a turn. Anything unexpected exits quietly with 0.
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

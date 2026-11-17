"""Claude Code hook: deliver pending steering and /btw updates into a running turn.

Registered on BOTH PreToolUse and PostToolUse. PostToolUse alone loses a race that matters:
a note written while the model is deciding its next action sits on disk through that whole
action and is only read afterwards — measured at 9.1s, with the note already waiting 2.1s
before the tool even started. For "stop, I saw something I don't like", after is too late.
PreToolUse delivers it BEFORE the next action instead. Whichever boundary comes first wins
the atomic claim below, so registering both can never deliver the note twice.

Writing to the CLI's stdin mid-turn only QUEUES a message for the NEXT turn — the running
agentic loop never sees it. Hooks are the real mid-turn channel: this fires after every
tool call, and its `additionalContext` lands in the model's context at that step boundary.

The Studio publishes one file per update in a per-project mailbox. The shared
standard-library reader locks and claims entries against other hooks and turn-end
fallback, then emits them as additionalContext. The script runs directly under the
backend's Python, without importing the application or its dependencies.
"""
import json
import os
import sys
from pathlib import Path

if __package__:
    from . import agent_glb, heredoc_fix, live_notes, read_shrink
else:
    import agent_glb
    import heredoc_fix
    import live_notes
    import read_shrink


def _note_context(p: str, payload: dict) -> str:
    """The pending steering or /btw text as additionalContext, or "" (and the note stays put)."""
    if not p or not live_notes.peek(Path(p)):
        return ""
    # SUBAGENTS run session hooks too (Task/Workflow agents fire PostToolUse constantly during
    # long orchestrated turns). A subagent consuming the note would inject it into ITS context —
    # the MAIN assistant would never see it, which is exactly the reported "my /btw got lost".
    # Detect the subagent transcript and leave the note for the main loop's next boundary.
    tp = str(payload.get("transcript_path") or "")
    if os.path.basename(tp).lower().startswith("agent-") or "subagent" in tp.lower().replace("\\", "/"):
        return ""
    try:
        note = live_notes.claim(Path(p))
    except OSError:
        return ""
    if not note:
        return ""
    if note.startswith(("↪ Steering update", "↪ Side-note")):
        # Each entry has its own instructions: steer may reprioritize, /btw stays FYI.
        return ("↪ LIVE update from the user:\n" + note
                + "\n— Take it into account from this step onward. Acknowledge the update briefly.")
    return ("↪ LIVE side-note from the user (sent with /btw while you work — they want you to see it "
            "NOW, mid-task, not after you finish):\n" + note +
            "\n— Take it into account from this step onward. Acknowledge it in ONE short line in your "
            "final answer. Do NOT derail the current task unless the note itself asks you to. "
            "(If you are a SUBAGENT on a delegated task: don't act on it beyond noting it — include "
            "this side-note VERBATIM at the top of your final report so the main assistant sees it.)")


def main() -> int:
    p = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        # The CLI writes UTF-8; a pipe read as text is the locale's code page, which mangles a
        # path like D:\Modèles.
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        payload = {}
    # THE MODEL FILES THIS CALL NAMES, noted before it runs, for the Engine window. An agent that
    # writes its GLB with a script of its own never reaches the forge's bench, and the person
    # watching saw an empty grid for the whole job. Every agent and subagent passes through this
    # hook, so this is the one place that sees all of them. The note file is DATA_DIR/btw/<id>.note.
    if p:
        agent_glb.record(payload, Path(p).parent.parent)
    # echo back the event we were actually called for — the same script serves Pre and Post,
    # and a mismatched hookEventName is not a shape the CLI is guaranteed to accept
    event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "PostToolUse")
    if event not in ("PreToolUse", "PostToolUse"):
        event = "PostToolUse"
    out: dict = {}
    # A SCREENSHOT IS SIZED BEFORE IT ENTERS THE CONVERSATION, never after: see read_shrink.py.
    # Subagents too: their own requests carry their images the same way.
    if p and event == "PreToolUse":
        try:
            updated = read_shrink.redirect(payload, Path(p).parent.parent)
        except Exception:
            updated = None
        if updated:
            out["permissionDecision"] = "allow"     # bypassPermissions only: nothing was asked anyway
            out["permissionDecisionReason"] = "Studio: the same picture re-encoded as WebP, fewer bytes"
            out["updatedInput"] = updated
    # A LONG HEREDOC IS MOVED TO A FILE before the CLI runs it: see heredoc_fix.py. Above about
    # 8,000 characters the command does not survive the trip on Windows (80% failed, measured), and
    # a quoted heredoc read from a file is the same input. bypassPermissions only, like the above.
    if event == "PreToolUse" and "updatedInput" not in out:
        fixed = heredoc_fix.redirect(payload)
        if fixed:
            out["permissionDecision"] = "allow"
            out["permissionDecisionReason"] = "Studio: long heredoc body moved to a file, same input"
            out["updatedInput"] = fixed
    ctx = _note_context(p, payload)
    if ctx:
        out["additionalContext"] = ctx
    if out:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **out}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

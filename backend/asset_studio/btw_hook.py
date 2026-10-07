"""Claude Code hook: deliver a pending /btw side-note INTO the running turn.

Registered on BOTH PreToolUse and PostToolUse. PostToolUse alone loses a race that matters:
a note written while the model is deciding its next action sits on disk through that whole
action and is only read afterwards — measured at 9.1s, with the note already waiting 2.1s
before the tool even started. For "stop, I saw something I don't like", after is too late.
PreToolUse delivers it BEFORE the next action instead. Whichever boundary comes first wins
the atomic claim below, so registering both can never deliver the note twice.

Writing to the CLI's stdin mid-turn only QUEUES a message for the NEXT turn — the running
agentic loop never sees it. Hooks are the real mid-turn channel: this fires after every
tool call, and its `additionalContext` lands in the model's context at that step boundary.

The Studio writes the raw note to a per-project file; this script atomically claims it
(os.replace beats the turn-end fallback in cc_session, so the note is delivered exactly
once) and emits it as additionalContext. Standalone on purpose: no package imports, runs
with any Python. The settings JSON wraps it in `cmd /c if exist ...` so the per-tool-call
cost when there is NO note is one cmd spawn, no Python.
"""
import json
import os
import sys


def main() -> int:
    p = sys.argv[1] if len(sys.argv) > 1 else ""
    if not p or not os.path.exists(p):
        return 0
    # SUBAGENTS run session hooks too (Task/Workflow agents fire PostToolUse constantly during
    # long orchestrated turns). A subagent consuming the note would inject it into ITS context —
    # the MAIN assistant would never see it, which is exactly the reported "my /btw got lost".
    # Detect the subagent transcript and leave the note for the main loop's next boundary.
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    # echo back the event we were actually called for — the same script serves Pre and Post,
    # and a mismatched hookEventName is not a shape the CLI is guaranteed to accept
    event = str(payload.get("hook_event_name") or payload.get("hookEventName") or "PostToolUse")
    if event not in ("PreToolUse", "PostToolUse"):
        event = "PostToolUse"
    tp = str(payload.get("transcript_path") or "")
    if os.path.basename(tp).lower().startswith("agent-") or "subagent" in tp.lower().replace("\\", "/"):
        return 0
    tmp = p + ".consuming"
    try:
        os.replace(p, tmp)          # atomic claim — the fallback can no longer double-send it
        with open(tmp, "r", encoding="utf-8") as fh:
            note = fh.read().strip()
        os.remove(tmp)
    except OSError:
        return 0
    if not note:
        return 0
    ctx = ("↪ LIVE side-note from the user (sent with /btw while you work — they want you to see it "
           "NOW, mid-task, not after you finish):\n" + note +
           "\n— Take it into account from this step onward. Acknowledge it in ONE short line in your "
           "final answer. Do NOT derail the current task unless the note itself asks you to. "
           "(If you are a SUBAGENT on a delegated task: don't act on it beyond noting it — include "
           "this side-note VERBATIM at the top of your final report so the main assistant sees it.)")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

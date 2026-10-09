# Steering a running agent

While Claude or Codex is working, type a correction and press **Steer** beside the
prompt. The button is available in Workspace panes and Mission Control cards.
You can also send `/steer <correction>` with Enter.

Claude receives the update through its PreToolUse/PostToolUse hooks at the next
tool boundary. Codex receives it through `turn/steer` for the current thread and
turn. Steering preserves the running session and does not launch companions,
take another checkpoint, or apply changed model/planner settings.

An action already executing finishes before the next step can see the update.
If Claude finishes without another tool boundary, the saved update is delivered
as a follow-up turn. `/btw` retains its gentler side-note framing.

Steer is disabled when idle. If the turn ends before delivery, or delivery fails,
the composer reports the error and restores the correction for normal **Send**.

Validation, with no vendor calls:

```powershell
backend/.venv/Scripts/python.exe backend/steer_test.py
cd frontend
npm.cmd run test:steer
```

---
name: claude-code-headless-sdk
description: Use when building an app/tool that drives Claude Code headlessly (the `claude` CLI) — print mode, streaming stream-json I/O, key flags, and the message-coalescing gotcha.
metadata:
  category: general
  updated: 2026-06-19
  confidence: verified
  source: experience building on Claude Code
disable-model-invocation: true
---

# Driving Claude Code Headlessly (`claude -p`)

Run Claude Code non-interactively to power your own app.

## One-shot
`claude -p "<prompt>" --output-format text` (or `json`). Add `--model`, `--permission-mode`, `--effort`, `--append-system-prompt`, `--resume <id>`, `--fork-session`.

`--output-format json` returns a rich object: `result`, `session_id`, `usage`, and `modelUsage` (per-model token + `contextWindow`).

## Persistent streaming session (fire messages without waiting)
```
claude -p --input-format stream-json --output-format stream-json --verbose [--resume <id>]
```
- Write each user turn to **stdin** as one JSON line:
  `{"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}\n`
- Read **stdout** lines: `{"type":"system","subtype":"init","session_id":...}` (once), `{"type":"assistant",...}` (carries `usage.output_tokens`), `{"type":"result","subtype":"success",...}` (one per turn), plus `rate_limit_event`.

## ⚠️ The message-coalescing gotcha
If you write several stdin messages while a turn is still active, Claude Code **merges them into ONE turn** — you get **fewer `result` lines than messages**. So never count results per message. Track busy state as `turn_active` (set on the first activity line, cleared on `result`) plus an `outstanding` counter — don't assume one result per message.

## Robust busy-tracking (timestamps beat a counter)
An `outstanding` counter gets **stuck**: coalescing zeroes it on first output, a missed/odd
`result` leaves it >0, and a slow turn that hasn't emitted output yet reads idle. A
self-clearing timestamp model is more reliable:
- Track `last_write` (when you wrote to stdin) and `last_result` (when the last `result` arrived).
- **`busy = turn_active OR last_write > last_result`.**
- On `result` → set `last_result = now`, so busy clears even if a counter desynced; a write
  after the last result reads busy even *before* the first output line (covers slow
  pre-output thinking). No timeouts, coalescing-proof.

## Respawn discipline (don't drop or kill in-flight turns)
A streaming process's settings (model/effort/permission/`--append-system-prompt`) are **fixed
for its life**, so changing them requires a respawn. But:
- **Never respawn while busy** — killing mid-turn aborts the in-flight answer. Respawn only
  when idle; apply the new settings on the next idle send. (Gate the respawn on the busy check above.)
- **On a failed stdin write** (the child died, e.g. user switched projects), respawn with
  `--resume <session>` and **retry the write once** so the queued message isn't silently lost.

## Mid-turn messages = next-boundary steering
A message written to stdin **while a turn is active** is queued and consumed at the **next turn
boundary** — Claude cannot read it mid-tool-call. To make it act as a *correction*, prepend a
marker telling Claude it's a steering update ("if this corrects/refines my previous request,
adjust course and prioritize it"); strip that marker from your UI's echo of the message. For an
**immediate** stop instead of next-boundary, send an interrupt/cancel (it aborts the turn).

## Computing context-window fill
From any assistant turn's `usage`, tokens currently in context =
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`. After a `/compact`, the
boundary is a `user` transcript entry flagged `"isCompactSummary": true`; the compaction itself
emits no fresh assistant `usage`, so a meter reading "newest assistant turn" shows the **stale
pre-compact** number until the next real turn — only trust usage recorded *after* the boundary,
or estimate from the summary size meanwhile. See `claude-code-1m-context`.

## Useful flags
- `--model opus[1m]` — model + 1M context (see `claude-code-1m-context`).
- `--append-system-prompt "<text>"` — inject behavior. A streaming process's system prompt is **fixed for its life**, so to change it you must **respawn** the process (`--resume` keeps the conversation).
- `--effort low|medium|high|xhigh|max` — thinking budget. There is **no separate thinking flag** (the user typing `ultrathink` = max budget).
- `--permission-mode default|plan|acceptEdits|bypassPermissions`.

## Transcripts
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, one JSON line per event (`uuid`, `parentUuid`, `type`, `message`, `timestamp`).

**cwd → dir-name encoding (get this EXACTLY right or your app binds to the wrong/empty dir):**
replace **every non-alphanumeric character** with `-` — i.e. `re.sub(r"[^A-Za-z0-9]", "-", abspath)`.
Not just `:` `\` `/`: **spaces, dots, parentheses, underscores, hyphens** all become `-`, so a
run like `" - "` collapses to `---`. The drive-letter **case is preserved** (whatever case the
cwd was given), but Windows' filesystem is case-insensitive so `C--…`/`c--…` resolve to the same
dir. Examples: `C:\Users\me\My App` → `C--Users-me-My-App`; `…\WOLT - EFOOD upload` →
`…-WOLT---EFOOD-upload`. ⚠️ Encoding only `:\/` (a common mistake) leaves literal spaces in the
id, which matches no dir — so a folder with spaces in its name silently fails to bind to its
transcript/feed/working-state, and a running turn looks dead.

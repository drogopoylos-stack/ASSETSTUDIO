---
name: studio-integrations
description: Validated recipes for integrating third-party services, APIs, SDKs, and features — accumulated across projects (auth, endpoints, request shapes, gotchas). Auto-captured when Auto-learn is on.
metadata:
  category: integrations
  updated: 2026-06-19
  auto: true
disable-model-invocation: true
---

# Studio · Feature & Service Integrations

Validated integration recipes — each a dated, sourced `## entry` you can edit or remove.
Auto-captured when **Auto-learn** (the 🎓 toggle in the chat box) is on.

<!-- learnings are appended below -->

## Claude Code context-meter must be compaction-aware (2026-06-19)

**Problem:** A context-fill % computed from "the newest assistant turn's token usage" in the
transcript stays stuck at the pre-compact high-water mark (e.g. 88.8%) right after a `/compact`,
and only drops once the user happens to send another message.

**Why:** `/compact` writes a summary entry but does **not** itself emit a fresh assistant `usage`
record. So walking `reversed(entries)` for the last `type=="assistant"` with `usage` lands on the
huge pre-compact turn. The true post-compact context (~summary size) is unknown until the next
real turn runs.

**Fix (read transcript, compaction-aware):**
- Claude Code marks the boundary with a `user` entry flagged `"isCompactSummary": true`.
- Find the most recent boundary; only trust assistant `usage` recorded **after** it.
- If none exists yet (compact just ran, no new turn) → **estimate** the fresh fill:
  `base_overhead + (chars of summary + re-read attachments)/4`, and flag `just_compacted: true`.
  `base_overhead` (~25k, settings-configurable) covers the Claude Code system prompt + tool/skill
  schemas, which are NOT in the transcript — a transcript-only char/4 estimate under-counts badly
  (measured: ~11k visible vs ~39k actual first post-compact turn).
- Context total per turn = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
- **Gotcha:** `mission._scan_one_project` does `setattr(proj, k, v)` for EVERY key returned by
  `_context_info`, and `CCProject` is a pydantic model that rejects unknown fields. So any new key
  added to the `_context_info` return dict (e.g. `just_compacted`) MUST also be declared as a field
  on `CCProject` — otherwise `overview` 500s and the whole project list vanishes ("pick a project").

**Frontend:** show the estimate honestly (`~3.6% · compacted`, green), poll the meter ~6s (not 12s),
and fire an immediate refetch burst (e.g. 0.2/2.5/6/11/18s) right after sending `/compact` since the
summary takes a few seconds to be written. **Source:** STUDIO `mission._context_info`, `ContextMeter.tsx`.

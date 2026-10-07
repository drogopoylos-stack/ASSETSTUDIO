---
name: claude-code-1m-context
description: Use when configuring Claude's 1M-token context window in Claude Code — the [1m] model suffix, plan gating, enabling/disabling, and verifying.
metadata:
  category: general
  updated: 2026-06-17
  confidence: verified
  source: code.claude.com/docs/en/model-config + live test
disable-model-invocation: true
---

# Claude Code — 1M-Token Context Window

Claude's default context is **200K tokens**; the **1M window is 5×** that. Same model weights — it changes how much it can *hold*, not how it *reasons*. Main benefit: it defers Claude Code's lossy `/compact` auto-summarization, so long sessions keep verbatim history instead of a summary.

## Enable — append `[1m]` to the model
- In session: `/model opus[1m]`
- Launch: `claude --model opus[1m]`
- Settings (`~/.claude/settings.json`): `"model": "opus[1m]"`
- Env: `ANTHROPIC_MODEL=opus[1m]`
- Works on full ids too: `claude-opus-4-8[1m]`.

Disable entirely: env `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`.

## Plan gating
- **Max / Team / Enterprise:** Opus **auto-upgrades** to 1M, no extra cost. (The $100/mo tier = Max 5×.)
- **Pro:** needs usage credits (`/extra-usage`).
- **API / pay-as-you-go:** available at **standard** token rates (no premium).

## Verify it's active
`claude -p ... --output-format json` → `modelUsage` is keyed by the resolved model, e.g.
`"claude-opus-4-8[1m]": { "contextWindow": 1000000 }`. Or `/status` / the `/model` picker shows the `[1m]` tag.

## Caveat
More context ≠ better attention — very full windows degrade ("lost in the middle"). 1M is **headroom** for long sessions / big files, not a "fill it for better answers" button. Keep context relevant.

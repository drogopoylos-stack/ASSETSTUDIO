---
name: ai-token-routing
description: AI model routing & token-efficiency policy — which Claude model + effort level to use per kind of work (lead vs subagents vs sweeps), plus the cache/subagent mechanics that decide real spend. Use when orchestrating multi-agent work, choosing a model/effort for a task or Workflow, or when the user asks to reduce token cost without losing quality.
category: studio-general
disable-model-invocation: true
metadata:
  created: 2026-07-02
  updated: 2026-07-02
---

# AI routing & token efficiency — dated policy (verify facts before trusting after ~3 months)

> **Facts as of 2026-07-02** (official Anthropic docs). Model names/prices/effort semantics go
> stale FAST. If today is >3 months past that date, re-verify before relying on the table.

## The invariant (never violate)
**Token savings come from removing WASTE — re-discovery, redundant reads, unscoped agents,
model-eyeballing, barrier-stalls — NEVER from downgrading judgment work.** Never downgrade a
launch-critical surface when unsure; never upgrade a wide, verifiable fan-out.

## Routing table (2026-07)
| Work | Model | Effort | Why |
|---|---|---|---|
| Orchestration, architecture, design, synthesis, adjudication, balance math, creative-feel surfaces, gnarly correctness | Fable 5 (lead) | high | Judgment work IS the product; official: effort is Fable's primary knob |
| Scoped verification with concrete evidence; implementing decided + spec'd modules; review passes | Opus 4.8 | high/medium | Half Fable's price; strong on scoped tasks |
| Retrieval research, search/Explore fan-outs, mechanical gate-checked sweeps | Sonnet 5 | medium | "Best speed+intelligence combo" (official) |
| Trivial formatting/bookkeeping | Haiku 4.5 | low | 200k ctx only — keep prompts small |
| Structured mid-stakes work on the lead | Fable 5 | medium/low | Official: "lower effort on Fable still often exceeds prior models' xhigh" |

Fast mode is **Opus-only** — not a Fable option; effort is Fable's only speed/cost control.

## Mechanics that decide real spend
- **Prompt cache:** 5-min TTL, hits 0.1×. Identical prefix required — don't churn tool definitions or
  system content mid-session. Long waits >5 min = one cache miss; don't poll in the 300s dead zone.
- **Model switches are cache resets:** caches are model-scoped — flipping opus↔fable mid-conversation
  re-reads the whole context at full price. Pick the model per session and stay on it.
- **Subagent hygiene:** scoped prompt (exact files/seams, never "explore the repo"), schema output,
  pipelines over barriers. **Agent-artifact rule:** any agent report >15 lines goes to a FILE; the agent
  returns verdict + path + surprises only.
- **Adjudicate cheap-tier findings before acting** — verified false-positive rate on confident HIGH
  findings from cheap tiers is real (3 of 4 in one audit).
- **Re-discovery is the #1 sink:** keep a current seam/architecture map in the repo (e.g.
  docs/seam-map.md) and read it instead of re-grepping. Ground truth (pinned engine source, the
  fixture) beats memory.
- **CLAUDE.md loads every session** — keep it ≈60 lines; everything else on-demand (this skill is
  on-demand for a reason).
- **Don't compact early:** compaction spends tokens and loses detail; on a 1M-window model at low
  fill it's pure waste.

## Second-mind (Codex) tiering
Codex burns OpenAI tokens (a separate budget). Use it for design-convergence on new core bets +
hard subsystems; a codex review at change-set checkpoints; a fresh compliance pass before a
submission. Never per-edit.

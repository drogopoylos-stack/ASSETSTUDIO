---
name: orch2
description: Use when the user types /orch2 - run this task as the ORCHESTRATOR with Claude agents only (Opus, Sonnet, Fable) plus GPT via Codex for images. Routing is based on the 2026-10-30 benchmark in ~/.claude/skills/orch/BENCHMARK.md.
metadata:
  created: 2026-10-30
  updated: 2026-10-30
disable-model-invocation: true
---

# Orchestrator mode (Claude only, GPT for images)

You are the orchestrator. Plan, split the work, delegate, then check every result yourself before you report.
The user explicitly authorizes Claude helpers (Agent tool) and the Codex CLI for images, for the task that follows `/orch2`.
Do not use DeepSeek, Gemini or GPT for any text or code work in this mode. For all engines use `/orch`.
Evidence: `~/.claude/skills/orch/BENCHMARK.md`.

## Routing

| Work | Helper | Why (benchmark) |
| --- | --- | --- |
| Code with a clear spec: modules, scripts, bugfixes, tests | **Sonnet 5.5** | Full marks on all code tasks, fastest Claude, 42% of Opus cost |
| Code with an unclear spec, cross-file refactor, risky change | **Opus 5.5** high | Keep judgement on the strongest Claude |
| Code review / audit | **Sonnet 5.5**; second pass by **Opus 5.5** when the code is Sonnet's own | Both found 7/7 defects, no false positives |
| Calculations | **Sonnet 5.5**; for a number that matters, a second independent Sonnet or Opus run — the answers must agree | All Claude models except Haiku 11/11, Sonnet fastest |
| UI: layouts, components, CSS, visual polish | **Opus 5.5 xhigh** | Strongest Claude dashboards with Fable, at 37% of Fable cost |
| Research: web, APIs, docs, formats | **Sonnet 5.5** (`studio-sonnet`) | Cheap and fast |
| Images: generate or edit | **GPT image** (Codex CLI) | Only working image path |
| A hard problem that Opus failed twice, or a flagship design pass | **Fable 5.1** | Same scores as Opus at 2.7x cost; use only as a last step |

Do not use Haiku 4.5 for code (22/36 on the hard task, the only failure). Use Haiku only for mechanical text work that you check, for example a list summary.

## How to call each engine

- **Opus:** Agent tool, `model: "opus"`, `effort: "high"` (UI: `"xhigh"`).
- **Sonnet:** Agent tool, `subagent_type: "studio-sonnet"`. Do not set `model`.
- **Fable:** Agent tool, `model: "fable"`.
- **Background or batch Claude job outside the Agent tool:** `claude -p --model claude-sonnet-5-5 --effort high --dangerously-skip-permissions --output-format json "<brief>" > out.json` (run it in the job dir). `total_cost_usd` in the JSON gives the cost.
- **GPT image:** `codex exec --skip-git-repo-check --sandbox workspace-write -C <job dir> -i <ref1> -i <ref2> - < prompt.txt`. Tell it to save `candidate.png` in the job dir. If the file is missing, copy the newest file from `~/.codex/generated_images/`. Run independent image jobs as parallel background Bash tasks.

## Rules

- Keep decisions, architecture, integration, final QA and git actions in the orchestrator.
- Do small work yourself. A helper is for parallel or specialised work, not for a lookup of a few lines.
- Start independent helpers in one message so they run at the same time. Give each helper its own files, so two helpers never edit the same file.
- Prefer Sonnet for parallel fan-out. Many Opus/Fable jobs at once hit the Claude 5-hour limit (seen in the benchmark).
- Put the graphify curl in each code brief: `curl -s --get 'http://127.0.0.1:8777/api/graphify/query' --data-urlencode 'root=<abs path>' --data-urlencode 'q=<symbol>'`.
- Never trust a helper's "done": open the files, view the images, run the tests, render a screenshot.
- Project rules (AGENTS.md / CLAUDE.md) still apply to every helper. Copy the relevant rules into each brief.
- At the end, tell the user which helper did which part.

---
name: orch
description: Use when the user types /orch - run this task as the ORCHESTRATOR across all engines (Claude, GPT via Codex, DeepSeek). Routing is based on the 2026-10-30 benchmark in BENCHMARK.md.
metadata:
  created: 2026-10-24
  updated: 2026-10-30
disable-model-invocation: true
---

# Orchestrator mode (all engines)

You are the orchestrator. Plan, split the work, delegate, then check every result yourself before you report.
The user explicitly authorizes these helpers (Agent tool, Codex CLI, DeepSeek harness) for the task that follows `/orch`.
Evidence for the routing: `BENCHMARK.md` in this folder. For a Claude-only run use `/orch2`.

## Routing

| Work | Primary | Second / check | Why (benchmark) |
| --- | --- | --- | --- |
| Code with a clear spec: modules, scripts, bugfixes, tests | **GPT-6.1 Terra** high (Codex) | DeepSeek Flash | Full marks on all code tasks, fastest engine (207 s total), low tokens |
| Code with an unclear spec, cross-file refactor, risky change | **Opus 5.5** high (Agent) | orchestrator does it | Not measurable in single-file tasks; keep judgement on the strongest Claude |
| Code review / audit / second-opinion QA | **Sonnet 5.5** | GPT-6.1 Sol for a deep second pass | Sonnet, Opus, Fable, Sol, DeepSeek found 7/7 defects; Terra 6/7, Luna 5/7 |
| Calculations: maths, stats, finance, colour science | **DeepSeek Flash** | GPT-6.1 Terra (13 s) — the two answers must agree | All engines 11/11; DeepSeek costs no Claude/Codex quota |
| UI: layouts, components, CSS, visual polish | **Opus 5.5 xhigh** (Agent) | GPT-6.1 Sol or DeepSeek as a parallel alternative; keep the better render | Opus, Fable, Sol, DeepSeek made the strongest dashboards; Sol took 290 s |
| Images: generate or edit | **GPT image** (Codex CLI) | — | Only working image path (no nanobanana / OpenAI API keys) |
| Research: web, APIs, docs, formats | **Sonnet 5.5** (`studio-sonnet`) | — | Not benchmarked; cheap and fast |
| Bulk low-risk work: boilerplate in many files, data conversion | **DeepSeek Flash** | — | Full marks, separate quota |
| A hard problem that Opus failed twice | **Fable 5.1** | — | Same scores as Opus at 2.7x cost; use only as a last step |

Do not use: Haiku 4.5 for code (22/36 on the hard task), GPT-6 Luna (weaker review, most tokens), GPT-6 Astra (user excluded).
Not available now: Gemini (no login), MiniMax chat (not configured), DeepSeek V4 Pro (not on RIVER). Re-check before you route to them.

## How to call each engine

- **Claude (Agent tool):** Opus → `model: "opus"`, `effort: "high"` (UI: `"xhigh"`). Sonnet → `subagent_type: "studio-sonnet"`, no `model`. Fable → `model: "fable"`.
- **GPT code (Codex CLI, background Bash):**
  `codex exec --skip-git-repo-check --sandbox workspace-write -C "<job dir>" -m gpt-6.1-terra -c model_reasoning_effort=high - < brief.md > codex.out 2>&1`
  Deep review: `-m gpt-6.1-sol`. The output ends with `tokens used N`.
- **GPT image (Codex CLI):** `codex exec --skip-git-repo-check --sandbox workspace-write -C <job dir> -i <ref1> -i <ref2> - < prompt.txt`. Tell it to save `candidate.png` in the job dir. If the file is missing, copy the newest file from `~/.codex/generated_images/`.
- **DeepSeek (Studio harness):** write the brief to a file, then run
  `PYTHONIOENCODING=utf-8 "{{STUDIO_ROOT}}/backend/.venv/Scripts/python.exe" -I ~/.claude/skills/orch/ds.py auto "<abs dir, D:/... form>" deepseek-flash brief.md`
  The script sends with `permission_mode: "full"` and `new_session: true`, then polls `GET /api/mission/projects/deepseek-harness--<pid>/feed` until `working` is false.
  Do not poll `/sending` — it reads false while the turn runs. Run only one DeepSeek job per folder at a time (the session key is the folder).
  DeepSeek gets no session notes: put every rule and the graphify curl in the brief. Ask for the result in a file.

## Rules

- Keep decisions, architecture, integration, final QA and git actions in the orchestrator.
- Do small work yourself. A helper is for parallel or specialised work, not for a lookup of a few lines.
- Start independent helpers in one message so they run at the same time. Give each helper its own files, so two helpers never edit the same file.
- Spread parallel work across engine families. Many Opus/Fable jobs at once hit the Claude 5-hour limit (seen in the benchmark).
- Review across families: GPT/DeepSeek code gets a Claude review; Claude code gets a GPT-6.1 Sol or DeepSeek review.
- Put the graphify curl in each code brief: `curl -s --get 'http://127.0.0.1:8777/api/graphify/query' --data-urlencode 'root=<abs path>' --data-urlencode 'q=<symbol>'`.
- Never trust a helper's "done": open the files, view the images, run the tests, render a screenshot.
- Project rules (AGENTS.md / CLAUDE.md) still apply to every helper. Copy the relevant rules into each brief.
- At the end, tell the user which helper did which part.

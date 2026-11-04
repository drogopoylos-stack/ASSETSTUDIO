# Engine benchmark — 2026-10-30

Harness and graders: `{{STUDIO_ROOT}}/tmp/orch-bench/` (`tools/lane.sh <engine> [tasks…]`, `grade/grade_all.py -v`).
All engines at effort `high` (DeepSeek has no effort knob). One run per task, all lanes in parallel,
so times include contention and are a rough guide only.

| Task | What it measures | Grader |
| --- | --- | --- |
| t1 code | write a duration parser from a tight spec | 41 hidden cases |
| t2 bugfix | fix 6 planted bugs in a TTL cache | 10 hidden tests |
| t3 calc | 6 maths/finance/stats problems | 11 numbers vs key |
| t4 UI | one-file dark dashboard, 1280 px and 390 px | rendered and judged by eye |
| t5 review | find 7 planted defects in orders.py | hits / false positives |
| t6 hard code | spreadsheet evaluator (lazy IF, cycles, error order) | 36 hidden cases |

| Engine | t1 | t2 | t3 | t4 UI | t5 | t6 | total time | cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-fable-5-1 | 41/41 | 10/10 | 11/11 | very good | 7/7 | 36/36 | 622 s | $5.67 |
| claude-opus-5-5 | 41/41 | 10/10 | 11/11 | very good | 7/7 | 36/36 | 450 s | $2.11 |
| claude-sonnet-5-5 | 41/41 | 10/10 | 11/11 | good | 7/7 | 36/36 | 296 s | $0.88 |
| claude-haiku-4-5 | 39/41 | 10/10 | 11/11 | plain | 6/7 | **22/36** | 472 s | $0.82 |
| gpt-6.1-sol | 41/41 | 10/10 | 11/11 | very good (290 s) | 7/7 | 36/36 | 719 s | 159k tok |
| gpt-6.1-terra | 41/41 | 10/10 | 11/11 | good | 6/7 | 36/36 | **207 s** | 103k tok |
| gpt-6-luna | 41/41 | 10/10 | 11/11 | good | 5/7 | 36/36 | 298 s | 218k tok |
| deepseek-flash (RIVER) | 41/41 | 10/10 | 11/11 | very good | 7/7 | 36/36 | 435 s | RIVER key, no Claude/Codex quota |

Claude cost = `total_cost_usd` from `claude -p --output-format json` (API-equivalent; on a plan it is quota).
The Fable lane hit the Claude 5-hour limit once during the run.

Findings:
- Single-file tasks with a clear spec saturate: every engine except Haiku got full marks on code, bugfix and maths.
  The benchmark does NOT measure long multi-step projects, ambiguous specs or architecture.
- Haiku 4.5 is the only engine that failed the hard task (error order, cycles, range order). Do not give it code.
- Fable 5.1 scored the same as Opus 5.5 at 2.7x the cost and 1.4x the time. No measured gain on these tasks.
- Sonnet 5.5 is the best Claude value: full marks, fastest Claude, 42% of Opus cost.
- GPT-6.1 Terra is the fastest engine overall, full marks on code, but missed 1 of 7 review defects (empty-list ZeroDivision).
- GPT-6.1 Sol is thorough (7/7 review, best-looking UI with a sidebar) but the slowest engine.
- GPT-6 Luna missed 2 review defects and is token-heavy. No reason to choose it.
- DeepSeek Flash got full marks everywhere and a strong UI. It costs no Claude or Codex quota.
- Review: Fable, Opus, Sonnet, Sol and DeepSeek found 7/7 with no false positives.

Health on 2026-10-30:
- OK: all 4 Claude models, all GPT models except Astra (not tested), DeepSeek V4.1 Flash on RIVER.
- Not available: DeepSeek V4 Pro (RIVER does not serve it); Gemini CLI (no login);
  MiniMax chat (no provider configured; only MiniMax video providers exist); nanobanana and OpenAI image API (no keys).

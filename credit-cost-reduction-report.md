# Cutting Claude Code / Codex / DeepSeek credit cost by offloading to the local PC

Scope note: prices and model names below were read from vendor docs in this session (dated 2026-09/10 in the sources). Third-party figures are marked *secondary*. Anything I could not confirm is marked **unverified**.

## Ranked techniques

| # | Technique | What it does | Realistic credit saving | Local HW needed | Risk / caveats | Breaks prompt caching? |
|---|---|---|---|---|---|---|
| 1 | **Provider prompt-cache discipline** | Keep `tools → system → static context → [breakpoint] → volatile` byte-identical across turns | Anthropic: cache hits billed **0.1×** input for most models (Opus 5.5 **0.05×**, Fable/Mythos 5.1 **0.025×**); writes cost 1.25× (5 min) / 2× (1 h). DeepSeek: cache-hit input is **$0.003 vs $0.15** per 1M off-peak on flash (~50×); v4-pro **$0.022 vs $0.66** (~30×) — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) | None | 5-min TTL expires between turns; below the minimum cacheable prefix (512–4096 tokens depending on model) caching is silently skipped | No — this *is* caching |
| 2 | **Tool-output truncation hooks** | PreToolUse/PostToolUse wrapper pipes `pytest`/`npm test`/logs through `grep`/`head` so 10k lines become 100 | Large and direct (input tokens re-sent every subsequent turn). Codex exposes `tool_output_token_limit` (default 12000) | CPU only | Over-filtering hides the failing assertion | No (output is below the breakpoint) |
| 3 | **Context hygiene** | `/compact`, `/clear`, thin `CLAUDE.md`/`AGENTS.md`, disable unused MCP servers | Anthropic docs/blog and vendor guides report cached-token ratios of 70–85% in steady sessions; Codex `auto_compact_token_limit` | None | Compaction itself costs an API call; over-compaction loses task state | Compaction **invalidates** old cached prefix — but the prefix is being discarded anyway |
| 4 | **Model tiering + cheap subagents** | Route mechanical work to Haiku/mini; keep the expensive model only as orchestrator | Vendor-reported 30–60% (*secondary*, [ofox](https://ofox.ai/zh/blog/claude-code-token-optimization-5-strategies-2026/)); price gaps are 2.5–5× per token | None | Subagents re-send context; multi-agent systems use far more tokens overall | No |
| 5 | **Tool-definition slimming / lazy schema disclosure** | Compact summary pool always in prefix; promote full JSON schemas only for gated top-k tools | A *simulated* 120-tool benchmark measured 47.3k→2.4k tokens/turn (**95%**), but task-success/cost were **projections, not live runs** ([Tool Attention, arXiv 2604.21816](https://ar5iv.labs.arxiv.org/html/2604.21816)) | CPU (MiniLM embeddings, FAISS) | Hallucinated tool calls; needs a rejection gate. Community-reported MCP "tools tax" is ~15k–60k tokens/turn | **Yes, if you change tool defs per turn** — that busts everything downstream. Keep summaries stable above the breakpoint, volatile schemas below it |
| 6 | **Output-side caps** | `max_tokens`, `model_verbosity=low`, `model_reasoning_summary=none`, `model_reasoning_effort=low`, diff-only output | Reasoning tokens bill as output. Vendor guide claims 30–50% output-token cut from the Codex "three knobs" (*secondary*) | None | Lower effort increases missed edits | No |
| 7 | **Local repo map / symbol graph (tree-sitter + PageRank)** | Agent asks for a token-fitted symbol map instead of grepping and reading whole files | Illustrative example: **87 tokens** for a map vs ~12,000 reading all files (*not a benchmark*) — [repo map pattern](https://raw.githubusercontent.com/agentpatterns-ai/website/refs/heads/main/context-engineering/repository-map-pattern.md), [aider repomap](https://aider.chat/docs/repomap.html) | CPU (tree-sitter; PageRank); GPU optional | Stale index on fast-moving repos; metaprogramming breaks the AST. Claude Code deliberately uses agentic search instead | Only if injected into the cached prefix; inject below the breakpoint |
| 8 | **Local query routing (Ollama/llama.cpp/RouteLLM/FrugalGPT)** | Weak model answers easy turns; escalate hard ones | RouteLLM reports **up to 85% cost cut at 95% GPT-4 quality on MT-Bench**, >40% cheaper at parity — **chat benchmarks, not agentic coding** ([RouteLLM](https://github.com/lm-sys/RouteLLM)) | 8–32 GB RAM / GPU for a usable local coder model | Escalated turns still ship the full context; local models need the same big context locally | No |
| 9 | **Response / semantic caching (GPTCache, Redis)** | Hash-equal or embedding-near-equal prompts return a stored answer | MeanCache: only ~**31%** of production queries repeat — that is the ceiling. A 67% hit rate came from one **NL-to-code analytics** workload (*secondary*) ([pattern page](https://www.agentpatterns.ai/patterns/multi-agent/semantic-caching-multi-agent/)) | CPU for embeddings; FAISS/SQLite | Stale code answers are actively harmful; needs file-hash invalidation; every request pays embedding+lookup | No (sits in front of the API) |
| 10 | **Local prompt compression (LLMLingua-2 / LongLLMLingua / Ollama summarization)** | Token-classify or summarize the prompt before sending | LLMLingua-2 reports 2×–5× compression, 3×–6× faster, 1.6×–2.9× lower end-to-end latency — measured on MeetingBank/LongBench/GSM8K/**BBH**, not coding agents ([ACL 2024](https://aclanthology.org/2024.findings-acl.57/)) | Small encoder on CPU (XLM-RoBERTa-large) | Can drop identifiers, stack traces, exact error strings; adds a local preprocessing step | **Yes — rewriting the prefix guarantees a cache miss and reprices the whole prefix at 1×** |

## Myths

- **"Compress the system prompt every turn to save money."** If that prefix is cached you already pay 0.1× (or less). Rewriting it converts a cache hit into a full-price cache miss. Compression only pays on tokens that were going to be cache-miss anyway.
- **"Prompt compression is a free 2–5× cut."** The 2×–5× numbers are QA/summarization/classification benchmarks. No verified measurement on agentic coding exists in what I found. **Unverified for coding agents.**
- **"Semantic caching cuts agent bills by 67%."** That number is one NL-to-code analytics deployment; the general repeat-query ceiling is ~31%, and multi-turn coding sessions with changing file state are near-unique.
- **"Local RAG always saves tokens."** Retrieval has its own cost, agents often need implementation bodies rather than signatures, and the aider repo-map "87 vs 12,000 tokens" is an illustration, not a benchmark.
- **"Codex `hide_agent_reasoning=true` saves tokens."** Display-only TUI switch; reasoning tokens are still billed. The real knobs are `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity` ([source](https://ofox.ai/zh/blog/codex-cli-token-saving-tips-2026/)).
- **"RouteLLM's 85% applies to Claude Code."** It is measured on MT-Bench/GSM8K/MMLU-style single-turn queries.

## Where to hook each technique in a local-first desktop app

The hard constraint: **Claude Code CLI and the Codex app-server own their own prompt assembly.** You cannot rewrite their internal prefixes without invalidating cache — so most savings must come from *inputs you control*, not prompt rewriting.

**Stage 0 — on-disk policy, before spawn (Python `spawn` layer)**
Write/limit `CLAUDE.md` and `AGENTS.md` (Codex default cap `project_doc_max_bytes = 32768`); generate the output-style / verbosity config; set `MAX_THINKING_TOKENS`, `model_reasoning_effort`, `model_reasoning_summary`, `tool_output_token_limit`; disable unused MCP servers in `.mcp.json`.

**Stage 1 — PreToolUse / PostToolUse hook scripts (external, not in-process)**
This is the single best local offload: shell/Python filters that truncate command output before it enters the transcript. Claude Code runs these via `settings.json`; Codex equivalents go in its config. Zero API tokens, zero cache risk.

**Stage 2 — FastAPI "context broker"**
Local MCP servers the CLIs connect to: (a) tree-sitter symbol index + PageRank repo map, (b) local embeddings + RAG over the repo, (c) local embedding-based semantic cache keyed on `(task_text, sorted file hashes, model, repo HEAD)`. The cache must live *outside* the CLI: hash-match → return stored answer and never spawn; near-match → return a scaffold to a local model. Do **not** echo a rewritten prompt back into Claude Code.

**Stage 3 — routing**
FastAPI `/route` endpoint classifies the request. Trivial (format, commit message, log triage, docstring) → Ollama/llama.cpp. Otherwise choose the paid tier (Haiku/mini subagent vs frontier) and spawn Claude Code CLI / Codex app-server with that model flag.

**Stage 4 — local API proxy (only for the session you own)**
For DeepSeek (and any OpenAI/Anthropic-compatible session the app itself drives), point the client at a local FastAPI proxy that injects `max_tokens`, logs `prompt_cache_hit_tokens` / `cache_read_input_tokens`, and enforces stable prefix ordering. For Claude Code, `ANTHROPIC_BASE_URL` proxying is possible but the proxy must pass the prefix through **byte-identically** — reordering or trimming it is exactly what breaks caching.

**Stage 5 — post-processing**
Extract diffs, strip narration, summarize CLI output *locally* before writing to your UI/DB so it is not fed back into the next turn.

**Practical priority order:** caching discipline → tool-output hooks → context hygiene → model tiering → output caps. Apply local compression/router/cache only to single-shot calls you own, never inside a live cached session.

# Asset Studio — credit-cost audit: the "Plan usage" bar and every other paid path

## Plan usage bar

**Verdict: FREE. It is a quota/read path (HTTP GETs + local file reads). It never runs a model turn and never spawns the Claude CLI in print/exec mode.**

Call chain:

- UI: `frontend/src/components/UsageBar.tsx:173` (`UsageLimitsBar`); poll at `UsageBar.tsx:192-201` → `api.missionUsage(provider, force)`.
- Client: `frontend/src/api/client.ts:626-629` → `GET /api/mission/usage`, `GET /api/mission/usage/accounts`.
- Router: `backend/asset_studio/routers/mission.py:41-67` (`/usage`), `mission.py:34-38` (`/usage/accounts`).
- Claude: `routers/mission.py:49-54` → `usage.compute()` (`backend/asset_studio/usage.py:73-177`) → `httpx.get("https://api.anthropic.com/api/oauth/usage")` (`usage.py:22`, call at `usage.py:95`) using the OAuth `accessToken` read off `~/.claude/.credentials.json` (`usage.py:42-47`). One GET, parsed; no CLI, no messages API.
- Codex: `usage_accounts._codex()` (`backend/asset_studio/usage_accounts.py:195-246`) → `account/rateLimits/read` JSON-RPC (`usage_accounts.py:211`).
- Balance accounts: DeepSeek `GET https://api.deepseek.com/user/balance` (`usage_accounts.py:254`); Kimi `GET /v1/users/me/balance` (`usage_accounts.py:276`); OpenRouter `GET /api/v1/key` (`usage_accounts.py:296`) and `/api/v1/credits` (`usage_accounts.py:308`).
- `claude_auth.usage_limits()` (`backend/asset_studio/claude_auth.py:46-58`) is just a wrapper around `usage.compute()` — no second poller.

Poll interval / caching:

- Frontend: `60000 ms` per mounted bar (`UsageBar.tsx:201`); the bar is mounted at `frontend/src/pages/Workspace.tsx:2050` and `frontend/src/pages/MissionControl.tsx:459`.
- `pollWhileVisible` skips every tick while `document.hidden` and never overlaps an in-flight request (`frontend/src/components/ui.tsx:84-100`) — so ~0 requests when the app is minimised/tray.
- Backend Claude cache `_TTL = 90.0` (`usage.py:26`, guard at `usage.py:75`), 429 backoff 120-900 s (`usage.py:98-107`), last-good persisted to `data/usage_cache.json` (`usage.py:29`, `usage.py:167-171`). Balance/Codex TTL 60-120 s (`usage_accounts.py:31`, `usage_accounts.py:133-150`).
- Net effect: at most ~1 Anthropic usage GET per 90 s while a bar is visible; two mounted bars still produce ~1 because of the shared backend cache.

Side effects (none of which bill model tokens, but they are not pure reads):

1. **Codex app-server is started by the usage bar.** `usage_accounts.py:91-100` / `:195-202` call `codex_app.server()`, which does `subprocess.Popen(self.argv + ["app-server"], ...)` when no server is running (`backend/asset_studio/codex_app.py:454-481`, spawn at `codex_app.py:301`). `GET /api/mission/usage/accounts` hits this through `_connected("codex")` (`usage_accounts.py:103-111`) — so simply opening the account picker can start a Codex app-server process. No thread/turn is started.
2. **On HTTP 401 from the usage endpoint**, `usage.py:108-117` calls `claude_auth.report_401` → `cli_logged_in()` → `claude auth status` subprocess (`claude_auth.py:83-84`). That call is documented as "instant, non-interactive, costs no model tokens" (`claude_auth.py:65-70`). If signed in, `_kick_silent_refresh` runs it once more to rewrite `.credentials.json` (`claude_auth.py:115-130`).
3. **`/usage/accounts` can trigger one extra usage GET**: `_claude_connected()` → `claude_auth.status()` (`usage_accounts.py:83-88`); when the stored token is expired by timestamp, `status()` starts a background `usage.compute(force=True)` (`claude_auth.py:213-215`).
4. Reads the OS keychain for DeepSeek/Kimi/OpenRouter keys (`usage_accounts.py:40-59`) — local, keys never returned.

`GET /api/auth/claude/limits` (`routers/auth.py:29-32`) exists but has no caller: `api.claudeLimits` is defined (`frontend/src/api/client.ts:422-425`) and never used.

## Features that cost credits

| Feature | Trigger | Evidence (file:line) | How to stop it |
|---|---|---|---|
| Send a message to a Claude Code session | user | `cc_session.py:1818` (`[exe,"-p",message,...]`), `cc_session.py:4332` (Popen); streaming path `cc_session.py:4304-4307` | `settings.json: "monthly_spend_cap_usd": >0` (checked `cc_session.py:4168-4174` via `spend.over_cap`, `spend.py:157-162`); otherwise just don't send |
| **Co-agents / companions (dual-agent review)** | user picks them, then **automatic after every primary turn** | passes `companions` (`routers/mission.py:214`, `routers/mission.py:219-222`); `cc_session.py:4282-4293` starts the thread; each runs `codex_app.run_once` (`cc_session.py:4007`) or `claude --print`/`gemini -p`/`cursor -p` (`cc_session.py:4032-4051`) | No settings key. Remove co-agents from the composer (`companions` defaults to `[]`, `routers/mission.py:214`); add `monthly_spend_cap_usd` |
| **Scheduled + recurring messages** | user creates, **fires unattended** (one-shot or weekly) | `scheduled.py:158-173` (`_fire` → `cc_session.send`), loop `scheduled.py:183-202`, auto-start on import `scheduled.py:239-240` | No global key. Cancel each job: `DELETE /api/mission/scheduled/{job_id}` (`routers/mission.py:358-361`) |
| Codex turn (app-server thread) | user | `codex_app.py:1166` (send), `codex_app.py:1271` (`_start_turn`) | `monthly_spend_cap_usd` — guard sits above the Codex dispatch (`cc_session.py:4168-4202`) |
| DeepSeek Harness chat (pay-per-token SDK) | user | `cc_session.py:4194-4198`; SDK spawn `deepseek_session.py:395-435`; key gate `deepseek_session.py:399-404` | `monthly_spend_cap_usd`; remove the DeepSeek key / don't pick the engine |
| "Ask AI" tab (OpenRouter and any OpenAI-compatible provider) | user | `llm_chat.py:207-219`, POST `llm_chat.py:238`; providers `chat_providers.py:44-90` | Remove the key; **not covered by `monthly_spend_cap_usd`** |
| Voice dictation with the Groq engine | user | `voice.py:366-367` → `_groq_transcribe`; `voice.py:278`, POST `voice.py:320` | `settings.json: "voice_engine": "local"` (`config.py:231`), or `"voice_enabled": false` (`config.py:366`) |
| Autonomous agent / Compare (generation jobs) | user | `routers/agent.py:16-24`, `routers/agent.py:49-52`; jobs `agent/loop.py:98-104` | `monthly_spend_cap_usd` blocks API jobs (`jobs/queue.py:184-190`); pick `kind=local` providers |
| **Vision judge (OpenAI gpt-4o-mini / Gemini 2.0 Flash)** | **automatic on every agent-loop iteration once a key exists** | `agent/loop.py:115`; auto-select `judge.py:175-180`; OpenAI call `judge.py:101-117`; Gemini call `judge.py:141-153` | Set `goal.judge = "heuristic"` (`models.py:218`, branch `judge.py:179-180`); or remove the `openai`/`gemini` keychain keys |
| Asset generation with `kind=api` providers (Tripo, Meshy, OpenAI images, NanoBanana/Gemini, MiniMax H3 Max on fal, Mixamo, custom endpoints) | user | `jobs/queue.py:184-190`; e.g. `providers/threed/tripo.py:42`, `providers/threed/meshy.py:45`, `providers/texture/meshy_texture.py:36`, `providers/image/openai_image.py:21`, `providers/image/nanobanana.py:21`, `providers/video/minimax_h3_max.py:40`, `providers/rig/mixamo.py:32` | `monthly_spend_cap_usd`; or choose the local defaults (`config.py:152-161`: placeholder/rembg/blender-rig/gltf-transform/turntable, ComfyUI/Trellis/Hunyuan are `kind=local`) |
| Alternate "write" engines in a pane (Kimi, Qwen, Gemini, Cursor, custom) | user | `cc_session.py:4194-4202`, `cc_session.py:4319-4320`; commands built `agents.py:277-300` (`codex exec`, `gemini -y -p`, `cursor-agent -p --force`) | `monthly_spend_cap_usd`; don't select that engine |
| Letting the agent call the paid generators at all | automatic prompt note, when on | `cc_session.py:1050-1051`, `cc_session.py:1831-1833` | `settings.json: "studio_tools_prompt": false` (`config.py:185`, **already the default**) |

## Free (local) work

- Plan usage / balance reads, all of the above (`usage.py:95`; `usage_accounts.py:254,276,296,308`) — and `claude auth status` (`claude_auth.py:83`).
- Codex rate-limit read (`usage_accounts.py:211`) — free RPC (but see the app-server spawn above).
- Graphify code graph / query: AST-only, "zero tokens, no API key" (`graphify_index.py:6`, build `graphify_index.py:435`); background rebuild loop `fswatch.py:54-84`, gated by `cc_graphify` (`config.py:244`).
- Auto-learn: transcript harvester + **local** Qwen3-4B distiller; Claude tokens are never spent (`autolearn.py:1-3`, worker `autolearn.py:512`), gated by `cc_autolearn` (`config.py:207`, default off).
- Mission summaries, card text, phases, spend roll-ups: sliced from local transcripts (`mission.py:470-479`, `subagents.py:815`, `workflows.py:783`).
- Chat conversation titles: truncated user message, no model call (`llm_chat.py:222-225`).
- Plans tab: reads `~/.claude/plans/*.md` (`routers/plans.py:85-114`).
- Checkpoints / diffs / restore: local git+file snapshots (`checkpoints.py`, no network).
- Review pipeline (contact sheets, actions, targets, browser control): deterministic headless Chrome, no model call (`routers/review.py:16-91`; `review.py` contains no provider HTTP).
- Forge / live link render, engine notes: local headless Chrome + prompt text only (`cc_session.py:1049-1099`).
- Web fetch/search: keyless, self-hosted (`web_tools.py:232`; `mcp_catalog.py:83`), gated by `cc_web_tools` (`config.py:248`).
- Voice with the default engine: local faster-whisper (`voice.py:368-403`).
- BOOST message compression, filesystem watcher, window sweeper, session keeper: local (`boost.py`, `fswatch.py`, `window_sweeper.py`, `session_keeper.py`).

## The 5 findings that matter most

1. **The Plan usage bar is free — do not fear it.** It is a single cached `GET https://api.anthropic.com/api/oauth/usage` (`usage.py:22`, `usage.py:95`) plus free balance GETs, at most one per 90 s while visible (`usage.py:26`) and none while the window is hidden (`ui.tsx:97`). No inference, no CLI print run.
2. **The one thing that spends money while you are away is a scheduled/recurring message.** `scheduled.py:158-173` fires `cc_session.send`, the loop starts on import (`scheduled.py:239-240`), and repeat jobs re-arm themselves forever (`scheduled.py:217-225`). There is **no global off-switch** — only per-job cancellation at `routers/mission.py:358`.
3. **Co-agents are N extra full billed turns per message.** They run automatically after the primary turn (`cc_session.py:4062-4075`, `cc_session.py:4289-4293`) and each is its own model run (`cc_session.py:4007`, `cc_session.py:4050`). The code itself records that a refused primary send used to still buy a full billed turn per co-agent (`cc_session.py:4276-4281`). No settings key disables them.
4. **The vision judge silently adds a paid API call on top of every agent-loop iteration.** With an OpenAI or Gemini key present, `auto` picks it (`judge.py:175-178`) and `agent/loop.py:115` calls it per iteration; the free heuristic is only reached if both are absent (`judge.py:179-180`). Per-request fix: `goal.judge = "heuristic"` (`models.py:218`).
5. **`monthly_spend_cap_usd` is the only real brake, it defaults to off, and it does not cover everything.** Default `0.0` = disabled (`config.py:420`); it now blocks agent sends across Claude/Codex/DeepSeek (`cc_session.py:4161-4174`) and API generation jobs (`jobs/queue.py:184-190`). It does **not** cover the Ask AI tab (direct `httpx.stream` POST, `llm_chat.py:238`), Groq dictation (`voice.py:320`), or the (free) usage bar. `spend.py:128-135` documents that agent turns were previously uncapped entirely.

Bonus flag: opening the Plan usage account picker can **start a Codex app-server process** (`usage_accounts.py:91-100` → `codex_app.py:301`) — a process spawn and credential read, not a billed call.

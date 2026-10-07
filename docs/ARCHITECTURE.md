# Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Electron window  ──  React + TS + Tailwind  (frontend/src)            │
│   TopNav · pages/* · StatusBar (GPU/CPU/RAM/disk) · live WS feed      │
└───────────────▲───────────────────────────────────┬──────────────────┘
                │ REST /api/*  +  WebSocket /ws       │
┌───────────────┴───────────────────────────────────▼──────────────────┐
│ FastAPI backend  (backend/asset_studio)                               │
│                                                                       │
│  routers/        system · providers · jobs(+pipeline) · catalog ·     │
│                  settings(+keys) · agent · ws                         │
│  jobs/queue.py   async queue + worker pool → runs providers in threads│
│  events.py       in-proc event bus → fans ProgressEvents to WS clients│
│  providers/      base.Provider + registry + adapters per stage        │
│  agent/          loop (generate→judge→iterate) + judge (vision/heur.) │
│  render/         numpy software renderer (headless turntable, no GPU) │
│  db.py           SQLite catalog + job history                         │
│  keychain.py     OS keychain (fallback: encoded file)                 │
│  system_stats.py psutil + nvidia-smi                                  │
└───────┬───────────────┬───────────────┬──────────────┬───────────────┘
        │               │               │              │
   ComfyUI/TRELLIS  Blender headless  gltf-transform   Cloud APIs
   Hunyuan (HTTP)   (subprocess)      (CLI)            (Tripo/Meshy/Gemini/OpenAI)
```

## Request lifecycle (a job)
1. UI/CLI/MCP `POST /api/jobs` with `{stage, provider_id, params, inputs}`.
2. `JobQueue.submit` persists a `queued` Job and enqueues it; returns immediately.
3. A worker resolves `inputs` (asset ids → file paths), builds a `JobContext`, and runs
   `provider.run(ctx)` in a thread pool (providers block on subprocess/HTTP/PIL).
4. The provider streams `ctx.progress(frac, "step")` → `ProgressEvent` → event bus → **all WS clients**
   (live % + ETA + step). ETA is auto-estimated from elapsed/progress when not supplied.
5. Returned `Asset`s are written to disk + SQLite; `asset_created` events fire; the Job is marked
   `succeeded`/`failed` with cost + logs.

## Provider contract
A provider is a subclass of `providers.base.Provider` with class attributes (`id`, `name`, `stage`,
`kind`, `requires_key`, `key_name`, `params`, license/cost metadata), an `is_available() -> (bool, reason)`
check, and `run(ctx) -> list[Asset]`. The `JobContext` exposes inputs, params, progress/logging, cost
accounting, the job workdir, tool/endpoint settings, and `make_asset(...)` (auto-fills image dims /
mesh poly counts / thumbnails). Adapters import heavy deps **lazily** so a missing tool only makes the
provider *unavailable* — it never breaks startup (the registry guards every import).

See [ADDING_PROVIDERS.md](ADDING_PROVIDERS.md).

## Live progress & stats
`events.EventBus` keeps a ring buffer (so new WS clients catch up) and fans out to per-client async
queues. A 2s broadcaster pushes `SystemStats` (CPU/RAM/disk via psutil, per-GPU VRAM/util/temp via
`nvidia-smi`) to the status bar.

## Catalog & licensing
Every asset row records provider, prompt, seed, file info, cost, and a `commercial_ok` flag +
`license` note inherited from the provider. The Catalog filters on "commercial-safe only" and the
Dashboard surfaces a warning count, so a monetized build stays clean.

## Headless surfaces
- **HTTP**: full REST (`/docs` for OpenAPI) + `/ws`.
- **CLI**: `asset_studio.cli` (Typer) — talks to the running server.
- **MCP**: `asset_studio.mcp_server` (FastMCP) — thin client over the HTTP API.
All three reuse the same queue/providers, so the agent loop, Compare, and pipelines behave identically
whether driven by a human or an AI.

## Frontend
Vite + React 18 + Tailwind. `store/useStore.ts` (zustand) holds live job/stat/toast state fed by
`api/ws.ts`. `StageRunner` is the shared generate-surface reused by the 2D/3D/Texture/Rig tabs;
distinct pages (Dashboard, Pipeline, Catalog, Jobs, Compare, Settings) compose the same primitives.
`<model-viewer>` renders GLB/GLTF previews. In dev, Vite proxies `/api` + `/ws` to the backend; in
prod the backend serves the built SPA so everything is one origin.

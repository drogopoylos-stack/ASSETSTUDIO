# Asset Studio

**A local-first desktop studio for running coding agents on your own machine — and for making the
game assets they need.**

One window that holds every project you work on, the agents working in them, the files they are
changing, the git branches they are changing them on, the running game they are changing them for,
and a 3D studio for building the assets that go in it. Nothing leaves the machine except the model
calls you choose to make.

It began as a 2D/3D asset pipeline (that half is still here, [further down](#the-asset-pipeline)).
Most of what it is now grew around the other problem: an agent is fast, and almost all of the time
that used to be spent typing is now spent **watching, checking and correcting** — so the tools that
make watching cheap are the ones that matter.

```
Python FastAPI backend  ·  React + TypeScript + Tailwind UI  ·  Electron shell
209 HTTP routes  ·  1,169 checks across 24 test files  ·  no account, telemetry off by default
```

---

## Contents

1. [The chat](#1-the-chat) — the agent, and everything you need to see while it works
2. [Many agents, many windows](#2-many-agents-many-windows) — panes, and knowing what each one is doing
3. [Workspaces, worktrees and git](#3-workspaces-worktrees-and-git)
4. [localhost](#4-localhost) — the running game, and what an agent may do to it
5. [graphify](#5-graphify) — the code knowledge graph
6. [The Studio Engine](#6-the-studio-engine) — the forge, the editor, the library
7. [What agents are told](#7-what-agents-are-told) — every note, with a switch and a token price
8. [The asset pipeline](#the-asset-pipeline) — 2D and 3D generation
9. [Install](#install)
10. [Under the hood](#under-the-hood)

---

## 1. The chat

The composer drives a real **Claude Code** session in the project folder — the same CLI, the same
transcript on disk, the same sessions you can pick up in a terminal. What the Studio adds is
everything around it.

| | |
|---|---|
| **Live streaming** | A persistent `stream-json` process per project. Tool calls, thinking, diffs and results arrive as they happen, not at the end. |
| **Edit a past prompt** | claude.ai-style rewind: change something you said ten turns ago and restart from there. The transcript is truncated and the session resumed. |
| **Model and effort** | Pick the model per project and the reasoning effort per model. The list is **learned from the turns your account actually runs**, so it never offers you something you cannot use. |
| **Context meter** | How full the window is, and how far from a compaction — read from the session's own hook events, not guessed from the transcript. |
| **Plan usage** | Session (5h) and Weekly (7d) buckets against your real plan limits, each model's bucket separately. |
| **What a turn cost** | Banked per turn: tokens in and out, cache reads, dollars. And tokens/sec per workspace, so you can see which model is actually fast here. |
| **`/btw` side notes** | Say something to a turn that is already running. It is delivered **into** the turn rather than queued behind it. |
| **Scheduled messages** | Send a message in 20 minutes, or every weekday at 09:00. |
| **Voice input** | On-device speech to text in the composer. Free, private, no key. A Groq cloud engine is available if you want it. |
| **Checkpoints** | A checkpoint before every turn: see exactly what the agent changed, and revert it. |
| **Skills** | Every installed Claude skill listed, each one switchable, with when it was added. |
| **Auto-learn** | Off by default. Mines finished transcripts for validated build patterns and banks them as reusable skills — with a local model doing the polishing, so it costs no tokens. |
| **Output styles** | The Studio's own writing styles for the agent, on a switch. |
| **Sessions survive a restart** | Session stdin is held outside the backend. Restart the backend, close the window — the turn keeps running and reattaches. |

### More than one engine

Every agent below runs **in the project folder**, and the first three stream into the same live
transcript the feed already tails — so they get the full chat, context meter, effort and permission
controls, with their own sessions and their own context.

| Agent | How it runs | Needs |
|---|---|---|
| **Claude Code** | Primary agent, full live transcript | `npm i -g @anthropic-ai/claude-code` |
| **Kimi K3** (1M) | The same engine pointed at Moonshot's Anthropic-compatible API | a Moonshot key |
| **Qwen** | The same engine pointed at Alibaba DashScope | a DashScope key |
| **OpenAI Codex** | its own conversation per folder, streamed into the feed through `codex app-server`; sign in from the chat box (ChatGPT, a code, or an API key); models and efforts come from Codex itself | the chat box's Install button, or `npm i -g @openai/codex` |
| **Gemini CLI** | `gemini -p`, headless | `npm i -g @google/gemini-cli` |
| **Cursor Agent** | `cursor-agent -p`, headless | `curl https://cursor.com/install \| bash` |

And **any other model you like**: add a chat engine in Settings and it appears in the composer like
the rest. One-click starting points for DeepSeek, Z.ai GLM, MiniMax, OpenRouter, Ollama, LM Studio,
Groq, xAI Grok, Together and Mistral — Anthropic-protocol ones go straight through, OpenAI-protocol
ones through a built-in Messages ⇄ chat-completions bridge.

There is also a **real terminal** (a pty, not a pipe) for any CLI that draws its own full-screen
interface, so an agent's own TUI keeps its own face inside the Studio.

---

## 2. Many agents, many windows

The workspace is a **grid of panes**, not a fixed layout. Every pane is a cell on one grid — the
seams *are* the grid tracks — so nothing can fall out of line. Drag a pane onto another to trade
places, blow one up to the whole window and put it back, and it keeps its scroll, its running turn
and whatever you had half-typed.

- **A pane is a chat, an agent, a file, an image, a 3D model or a rendered page.** Layout presets,
  draggable seams, per-pane tabs, and a tab can be dragged into another pane.
- **Each pane has its own project and its own conversation.** Two projects side by side, each with
  its own agent, is the normal case rather than a trick.
- **Drag a project onto a pane** to open it there. Drop a folder from Windows onto the workspaces
  list and it becomes a workspace.

### Seeing what an agent is actually doing

| | |
|---|---|
| **Running agents** | A live count in the bottom bar, and a dot on every project in the rail that has a turn in flight — with the colour of the agent that is running. |
| **Subagents** | What the Task tool *actually did*, not just what it reported: the card carries the prompt, the tools, what it inherited, how full its context got, and what it cost. Open one in its own pane and continue it from there. |
| **Build phases** | A phase list beside the chat that the agent keeps current as it works, so a long job is legible without reading the transcript. |
| **Who changed what** | File changes are attributed to the agent that was running, and you are warned when two agents touch the same code. |
| **Mission Control** | One command centre across *every* Claude Code project on the machine, not just the ones open here. |
| **Workflows** | A live dashboard of the Workflow tool's multi-agent runs — phases and agents as they go. |
| **Bottom bar** | CPU, RAM, GPU, VRAM, temperature, disk, the Claude CLI version, the running-agent count, and whether the headless browser is up. Choose what it shows. |
| **Notifications** | An OS toast and a taskbar flash when a turn finishes, on a switch. |
| **Stray windows** | A sweeper keeps automation-spawned windows off your main screen (Windows only). |

---

## 3. Workspaces, worktrees and git

**A folder is a workspace, and a workspace is a session.** The Studio opens the projects Claude
Code already knows, plus project folders it finds on the Desktop, in Documents and in Downloads —
on the first run, by itself. "Find my projects…" does it again later, and dragging a folder in from
Windows opens it on the spot.

The explorer is a VS Code-shaped file tree with a Monaco editor, search, rename/move/copy/delete,
a live filesystem watcher (so an agent's edit shows up immediately rather than on a slow poll), and
drag-and-drop import from the OS.

### Worktrees

Several branches of one repository, open **side by side, one agent each**. Create a worktree from
the dialog and you land in it; it nests under its parent project in the rail and gets its own
session, its own context and its own pane. A scoped subfolder can be its own workspace too.

### Source control

A git panel per workspace or worktree: status, stage, **commit, push, pull**, branch, and the whole
thing driven from the UI — including from a worktree, on its own branch. The rail shows each
project's branch beside its name.

---

## 4. localhost

Games are the point, so the running page is a first-class object.

**Find the dev server, don't guess it.** The Studio never guesses a port. It asks the OS which
ports are listening and which of those belong to a process running *inside this project*; to start
one it reads the URL the tool itself prints (Vite, Next and CRA all print one). A built `dist/` is
a snapshot — the dev server is the live answer, and it is preferred for that reason. Every game
inside a workspace is found separately, each with its own server.

**A headless browser is shared.** One Chrome for the whole Studio: the review harness, the live
link and the forge all use it. A pill in the bottom bar says which project it is for and opens or
closes it. If the PC has no Chrome it uses Edge; if it has neither it fetches a headless Chromium
itself. It is never left leaking — twelve copies of one WebGL game were once found open at once,
2.5 GB of VRAM for tabs nobody could see, and the sweep that fixes that knows which tabs belong to
other people's agents and must not be touched.

### What an agent can do with the running game

Two tools, both off by default, both switchable in Settings.

**Visual review** — judge art, effects and UI from a **contact sheet**, never a single screenshot.
One screenshot is the wrong frame nearly every time: an effect peaks for 200 ms, a page load shows
the menu, a 40 px asset is invisible at 720p, and two runs never match. The harness drives the page
clock by hand, so you get exactly the frames you ask for, identical every run, plus the same row
again at the size the player sees it, plus numbers — `findings` and `metrics` catch a dead or
static effect for **no image tokens at all**.

**Live game link** — ask the running game a question and change it while it runs. The scene tree
with its materials, lights and cameras; the console including failed asset loads; frame cost, draw
calls, triangles and GPU objects; and synthetic input — keys, mouse, wheel, taps, swipes, with a
phone layout on request. It reads *and writes*: set a value, look, and only then put it in the file.
PlayCanvas, three.js, Babylon, Phaser, PixiJS, Cocos and plain canvas 2D, with no change to the
project. It finds the engine object even in a bundled game with no globals, and when it cannot, it
names the one line that would fix it.

---

## 5. graphify

A **live code knowledge graph** per project, kept current in the background by the Studio, that
answers *where is X*, *what calls X*, *what does X use* and *how does A reach B* with exact
`file:line`. One cheap HTTP call, a few hundred tokens, milliseconds.

```bash
curl -s --get 'http://127.0.0.1:8777/api/graphify/query' \
  --data-urlencode 'root=<project>' --data-urlencode 'q=<symbol>'
```

- **It installs itself.** No pipx, no PATH: a dedicated venv under `data/`, built on demand. On a
  bare machine — nothing on PATH, no pipx venv — install to first answer measured at ~30 seconds,
  including building a 4,669-node graph.
- **It is enforced, not advised.** A `PreToolUse` hook answers a symbol-shaped `Grep` from the
  graph automatically, and refuses a read of the multi-megabyte `graph.json`.
- **It self-heals.** Asking about a project that has never been indexed *starts* the build, so a
  workspace you never opened here still works.
- **Grep is still right for strings** — an error message, a TODO, a config value. The graph indexes
  symbols.

---

## 6. The Studio Engine

A second window: **a Blender-shaped 3D editor, a library of the project's own assets, a live mirror
of the running game, and the forge an agent builds assets in.** Four tabs.

### The forge

Assets written in code, then *looked at* — Blender's loop for assets that are typed instead of
modelled. The agent's JavaScript runs inside the project's own page and its own engine, so
`import()` reaches the game's real `buildX()` rather than a copy.

The loop, in the order that costs least:

1. **Aim.** Sweep 36 angles, score every silhouette against a reference photo, and get the best
   angle and where the proportions differ back **as numbers**. No image, no image tokens.
2. **Measure.** Findings, every part's size in pixels, what touches the ground, how many surfaces
   the body is, the nodes that can be moved — hundreds of tokens instead of thousands, after every
   edit. With a reference you also get, free: the outline overlap score, where each named part
   landed as 0..1 coordinates (and the reference panel is ruled the same way, so a correction is a
   subtraction), and **what moved or vanished since your last shot under the same label** — the only
   line that catches a detail broken by accident.
3. **Look**, when the numbers stop moving. Any angle, any zoom, presets, turntables, silhouette /
   wireframe / normals passes, parameter variants side by side, and `focus` to frame *one part* so
   a 12 px detail becomes 500.
4. **Move it, with no rebuild.** Grab a part by name and rotate, move, scale or hide it; children
   come with it. The transforms come back so the ones worth keeping go into the builder.

**The bench keeps the scene between calls.** Measured on one chest: build 3.0 s, four more angles
0.6 s, an orbit 0.34 s, one part framed 0.19 s. Judging a whole asset from a single angle now costs
*more* than not. `clear:false` builds an asset a part at a time — and **the Engine window follows
the bench live**, so a person watches it happen instead of seeing it arrive finished, and can stand
where the agent is standing.

Every generation is recorded with its code, so it can be replayed, and tagged with what it *is* and
what it *depicts*, so it can be found again.

### The editor

A Blender-shaped edit tab over the project's real code: viewport navigation and overlays, a
navigation gizmo, selection and a transform gizmo, declared parameters with live rebuild,
armatures and pose mode, keyframes and playback, lights and cameras as first-class objects (and a
letterboxed look through a scene camera), and a round-trip that writes edits back to the code.

The modelling library agents import over HTTP: `weld`, `skin`, `check`, convex hull, boolean CSG,
isosurface, IK, Catmull-Clark subdivision with creases, bevel, UV unwrap with seams and packing,
texture baking, heat-diffusion skin weights, voxel remesh and relax — plus `applyEdits` and
`sceneReport` so a sidecar written in the editor does the same thing inside the game.

### The library

What a project already *has* — builder functions found in its code, model files, images — on
shelves with thumbnails, so the Engine window shows a game's own creatures and props and not only
what the Studio made. Every asset opens in the editor from its own game, with the arguments its
builder actually asks for. Compressed models are no obstacle: Draco is decoded on the way through.

---

## 7. What agents are told

Every capability above that an agent can reach is a **note in its system prompt**, and every note
has a switch and a measured token price, listed in Settings. Nothing is hidden, and nothing is on
that you did not agree to.

| Note | Default | What it buys |
|---|---|---|
| Code graph | **on** | Where a symbol is and what calls it, from the graph instead of grep |
| Blocked pages & search | **on** | A stealth fetch for sites that refuse robots, and a keyless web search |
| Build phases panel | **on** | The phase list beside the chat |
| Forge | **on** | Render asset code in a lit studio, with numbers and a reference |
| Mesh ops | **on** | The modelling library |
| 1M context | **on** | Tells the agent its window is a million tokens, so it does not compact early |
| Memory | **on** | Off adds a note that memory is unavailable |
| Visual review | off | Contact sheets of the running game |
| Live game link | off | Question and change the running game |
| Studio generators | off | The 2D/3D generators, callable by curl |
| Auto-learn | off | Bank reusable patterns as skills while working |
| Blender kiln | off | The Blender MCP pipeline |
| Force plan mode | off | Start every conversation in plan mode |

A **browser rule** is always sent and cannot be switched off: never kill every browser on the
machine. `taskkill /IM chrome.exe` takes out the user's browser and every other agent's too, so the
hook refuses it and says what to do instead.

Notes that describe local tools **look before they speak** — Blender, `gltf-transform` and the
Hunyuan3D weights are discovered on the machine, and named as missing when they are missing. A tool
that is announced and then absent is worse than one never mentioned: the plan is built around it
before it fails.

---

## The asset pipeline

The original half, still here and still working. Pick a free/local model or a paid API **per
stage**, with live previews, progress %, ETA and current step.

| Stage | Free / local | Paid API |
|---|---|---|
| **2D image** | ComfyUI (SDXL/Flux/Qwen), local Diffusers, **procedural (no key)** | Gemini 2.5 Flash Image, OpenAI `gpt-image-1` |
| **2D processing** | Background removal, upscale, sprite slicer, atlas pack, PNG→WebP | — |
| **3D generation** | TRELLIS / TRELLIS2, Hunyuan3D-2, **procedural mesh (no key)** | Tripo, Meshy |
| **Texturing** | TRELLIS2 projection, Hunyuan paint | Tripo, Meshy |
| **Rigging** | Blender headless (auto-weights / Rigify), UniRig | Tripo auto-rig, Mixamo handoff |
| **Optimise** | `gltf-transform` (Draco + KTX2), web size budget | — |
| **QA** | Headless turntable render (montage PNG + GIF, no GPU) | — |

Every provider is swappable from a dropdown with a free⇄API toggle, and a **brand-new AI can be
added from Settings** (endpoint + templated request + response mapping — no code). Unavailable
providers say exactly what to do rather than failing quietly.

The **Catalog** stores every asset with its preview, tags, target game, provider + prompt + seed,
file info, cost, and a **commercial-use flag** — with a Dashboard warning when non-commercial
assets exist, so a monetised build stays clean. **Pipeline** chains stages in one click and
**Compare** runs two providers on the same input, side by side.

Keys live in the **OS credential manager**, never in a file in this repository.

---

## Install

Copy the folder anywhere and double-click **`Asset Studio.vbs`**. The first run installs
everything; every run after opens instantly and lives in the system tray.

`setup.ps1` is idempotent and self-healing, and can be run by hand at any time. It:

- installs **Python 3.12, Node LTS and Claude Code with winget** when they are missing;
- builds and *verifies* the backend venv — it refuses if any of the app's routers failed to
  register, because a router that quietly did not load leaves a whole window dead at runtime;
- installs the UI dependencies and builds the bundle;
- copies the bundled skills to `~/.claude/skills` (disabled by default);
- installs **graphify** into its own venv and wires the `/graphify` skill;
- installs the Codex CLI, and offers the optional local-AI pack.

Then re-enter your API keys in Settings — they live in the OS credential manager, which does not
travel with the folder.

**Prerequisites** are installed for you when winget is available; otherwise Python 3.10+ and
Node.js LTS by hand. **No Chrome needed** — Edge is used when Chrome is absent, and a headless
Chromium is fetched on a PC with neither. See
[READ ME - New PC Setup.txt](READ%20ME%20-%20New%20PC%20Setup.txt).

Run the pieces yourself if you prefer:

```powershell
cd backend  ; ./.venv/Scripts/python.exe -m asset_studio.main   # http://127.0.0.1:8777
cd frontend ; npm run dev                                       # http://localhost:5173
cd frontend ; npm run app                                       # Electron on the dev server
```

The tray icon gives you: open · open in browser · **allow access from my network (LAN)** ·
start when I log in · restart backend · quit.

---

## Under the hood

```
backend/asset_studio/     FastAPI app, job queue, WebSocket progress, SQLite catalog,
                          OS-keychain secrets, provider adapters, and one module per feature
  routers/                27 route groups — workspace, worktrees, git, live, engine, review,
                          mission, chat, terminal, skills, web, voice, workflows, …
  vendor/                 three.js loader modules, served rewritten to the project's own three
frontend/src/             React UI — pages/, components/, api/, store/
frontend/electron/        the shell: supervises the backend, restarts it, native notifications
skills/                   Claude skills shipped with the Studio (installed disabled)
data/                     settings, catalog.db, assets, transcripts, caches   (gitignored)
```

**Everything is local.** The backend binds `127.0.0.1` by default; LAN access is one tray click and
off until you ask. There is no account and no sign-in; telemetry is a switch that is **off by
default**, and nothing leaves the machine while it is off. `data/` — which holds your
keys' metadata, your transcripts and copies of your other projects — is git-ignored on purpose.

**The backend must never block.** Nothing slow runs on the asyncio loop, every request has a
deadline, polls cannot stack up on the connection pool, and Electron supervises the backend and
self-heals it. Unreadable JSON state is quarantined rather than lost.

### API and headless use

- Interactive docs: `http://127.0.0.1:8777/docs` · live events: `ws://127.0.0.1:8777/ws`
- **CLI**: `python -m asset_studio.cli` — providers, gen, agent, compare, qa, pipeline
- **MCP server**: `python -m asset_studio.mcp_server` — see [docs/MCP.md](docs/MCP.md)
- **Forge from the terminal**: `python backend/forge_cli.py asset.js --views 3q --numbers`

### Tests

No mocks where a real thing will do: real repos for git, the real three.js build for the editor,
the real engine builds for the scene adapters, a real blocking site for the fetcher.

```powershell
cd backend  ; foreach ($t in Get-ChildItem *_test.py) { ./.venv/Scripts/python.exe $t.Name }
cd frontend ; npm test
```

665 checks across 16 backend files · 504 across 8 frontend files. Anything that needs a fixture the
repository does not carry **skips and says so** rather than failing — a test that only passes at one
desk is a test nobody trusts.

### More

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/ADDING_PROVIDERS.md](docs/ADDING_PROVIDERS.md) ·
[docs/MCP.md](docs/MCP.md) ·
[docs/visual-review.md](docs/visual-review.md)

---

## Licensing of what you make

Asset Studio is your tooling. **The licence of generated content depends on the model or provider
you chose for it.** The Catalog tracks a `commercial_ok` flag per asset and the Dashboard warns
when non-commercial assets exist, so a monetised build can be kept clean — but always check each
provider's own terms before shipping.

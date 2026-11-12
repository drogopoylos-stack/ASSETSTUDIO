# What each feature must do, and how to check it

This is the hand-written half of `FEATURES.md`. `scripts/features/build.py` reads it, and puts
each section beside the parts of the code that it names. Edit THIS file, not `FEATURES.md`.

How a section is written:

- `## <title>` starts a feature. A title that starts with `_` is a special section.
- `parts:` names the route groups (a file in `backend/asset_studio/routers/`, for example
  `engine`) and the UI pages (`page:<tab id>`, for example `page:engine`) of the feature.
- `modules:` (optional) names backend modules that no route group owns alone, for example
  `cc_session`. The test suites that reach them are then counted for this feature.
- The text after those lines is free Markdown. Write what must work and how to check it.
- Each `GET /api/...` written in backticks, for example `GET /api/health`, is a smoke check.
  `scripts/features/verify.py` calls it, and it passes when the answer is HTTP 200 and does not
  hold `"ok": false`. Write a call this way only when it reads and changes nothing, needs no
  parameter, and answers `ok` on every good installation. A call that depends on an optional
  program or on a switch (for example `/api/git/github` without `gh`) is written with no `GET`.

A part that no feature names is listed in `FEATURES.md` under "Parts no feature describes yet".
Add it to a feature here.

## _how-to-check

You are an agent on a machine where Asset Studio was installed. Do these steps in this order.

1. Make sure that the Studio runs. Start it from the Start menu, from the desktop shortcut or
   with `Asset Studio.vbs`. `GET /api/health` must answer at `http://127.0.0.1:8777`.
2. Run the automatic checks from the Studio folder:
   `python scripts/features/verify.py`
   Any Python 3.10 or newer runs it. The backend's own Python also runs it:
   `backend\.venv\Scripts\python.exe scripts\features\verify.py`.
   The script checks the programs, that the backend serves EVERY route in this file, that the
   UI is served, and the smoke checks of each feature.
3. Run all the test suites. This takes many minutes:
   `python scripts/features/verify.py --tests all --json data/verify-report.json`
4. For each feature below, do the steps under "How to check" that the script cannot do. These
   are the steps in the app. When you cannot see the screen, photograph a page with the shared
   browser: `POST /api/review/render` with `{"project": "<the Studio folder>", "url":
   "http://127.0.0.1:8777/<tab id>", "times": [3000], "size": [1440, 900], "scale": 1}`, then
   read the `peak` picture it returns. `/engine` is the Studio Engine window.
5. Fix a failure when its cause is the installation: a missing program, a key that is not
   entered, a setting that is off. Do not change the code to make a check pass. When the cause
   is in the code, report it with the output that failed.
6. Report one table: feature, result (pass, fail, needs a key, not installed, not checked),
   and what you saw.

Rules for this check:

- API keys do not move with an installation: they are in the Windows Credential Manager. A
  feature that needs a key (a paid provider, Poly Pizza, Ask AI, a chat engine) answers "no
  key" until the user enters it in Settings. Report it as "needs a key", not as a failure.
- Blender, ComfyUI and Codex are optional. Report a feature that needs one of them as "not
  installed" when the program is missing, not as a failure.
- Do not kill browsers by name. Other agents use the shared headless browser.
- Do not restart the backend while an agent works in another chat, unless the user agrees.

## _install

`Setup.bat` (or `setup.ps1`) installs everything that is missing, and it can run again at any
time: it only repairs what is missing. The installer `.exe` carries the app, the built UI, its
own Python and every Python package. Read `READ ME - New PC Setup.txt` for the two ways to
install. After the installation, enter the API keys again in Settings.

## Start, stay up and update
parts: app, system, ws, setup, page:shell, page:dashboard

**What it is.** The backend (FastAPI on `127.0.0.1:8777`), the window (Electron), the tray
icon, the top bar of tabs, the status bar and the Dashboard.

**Must work:**
- The Studio opens from the Start menu, the desktop shortcut or `Asset Studio.vbs`. The first
  start on a new machine runs the setup, then opens the window.
- The backend answers `GET /api/health` in less than one second, also while it is busy.
- The status bar shows CPU, RAM, GPU, VRAM, disk, the Claude CLI version and the count of
  running agents.
- A restart of the backend (tray icon, `Restart Studio Backend.bat`, or `POST
  /api/system/restart`) brings the window back by itself. Chat turns that run continue.
- When the UI is built again, the window shows an update banner (`GET /api/system/version`
  changes).
- Settings -> setup lists the optional tools with their state (`GET /api/setup/tasks`), and
  installs one on a click.
- The Dashboard shows the catalog summary, the system stats and the spend.

**How to check:**
1. `GET /api/health`, `GET /api/system/version`, `GET /api/system/info`,
   `GET /api/system/stats` and `GET /api/setup/tasks` answer.
2. Open the app. The top bar shows the tabs. The status bar shows numbers that change.
3. Restart the backend from the tray icon. The window says that it reconnects, then works.

## Chat with Claude Code agents
parts: mission, auth, autolearn, voice, plans, page:mission, page:plans
modules: cc_session, session_keeper, live_notes, btw_hook, session_hook, hook_events, checkpoints, scheduled, output_styles, model_access, turns, spend

**What it is.** The chat box drives a real Claude Code session in the project folder: one
`stream-json` process for each project. Mission Control is one view of every Claude Code
project on the machine.

**Must work:**
- A message sent from the chat box starts a turn. Tool calls, thinking and text show while
  they happen, not at the end.
- The model and the effort can be chosen for each project. The list holds only models that
  the account can use.
- The context meter shows how full the context is. Plan usage shows the 5-hour and the
  weekly buckets (`GET /api/auth/claude/limits`).
- Each turn records its cost and its tokens (`GET /api/mission/spend`).
- A `/btw` note written during a turn arrives in that turn (the session hook delivers it).
- A checkpoint is made before each turn. It shows the diff and can restore the folder.
- A scheduled message is sent at its time (`GET /api/mission/scheduled`).
- Editing a past prompt rewinds the conversation to that point.
- A session survives a backend restart: a session keeper holds its input, and the turn
  continues.
- When Claude Code is not logged in, the app shows the login prompt (`GET /api/auth/claude`).
- Voice input turns speech into text in the chat box, after it is enabled.
- Plan mode writes plans that the Plans tab lists (`GET /api/plans`).
- Mission Control lists every project, its agents, and the dev servers that run.

**How to check:**
1. `GET /api/mission/overview`, `GET /api/mission/live-status`, `GET /api/mission/attention`,
   `GET /api/mission/agents`, `GET /api/mission/slash-commands`, `GET /api/mission/scheduled`,
   `GET /api/auth/claude`, `GET /api/autolearn/status`, `GET /api/voice/status` and
   `GET /api/plans` answer.
2. Open a small test folder as a workspace. Send "reply with the word ready". The reply
   streams into the chat, and the context meter moves.
3. Send a second message during a long turn with `/btw`. The agent mentions it in that turn.
4. Open the checkpoint of that turn. It shows what changed.

## The prompt cache guard
modules: cache_guard, read_shrink
tests: cache_guard_test.py

**What it is.** A Claude session's prompt cache lives one hour after its last request. When the
cache is lost, the next request writes the whole conversation again at the write price ($8 per
million tokens on Opus 5.5, against $0.20 to read it). The guard stops the three causes that
the Studio controls. It is in Settings -> Cost -> Prompt cache (`/settings?pane=cost`).

**Must work:**
- An idle session with more than 150k tokens gets `/compact` about 50 minutes after its last
  request, while its cache is still warm. The backend log says `[cache-guard] ... compacting
  while it is warm`. The reaper does not stop such a session before that.
- A session is never compacted while a turn runs, while a background agent is owed a result,
  or when its cache lives only 5 minutes.
- A warm, large session that resumes after a CLI update starts on the build that it started on
  (`~/.local/share/claude/versions/<old>`). The backend log says `[cache-guard] ... resumed on`.
- In bypass-permissions mode, a `Read` of an image of 150 KB or more gets a WebP copy with the
  same pixels (`data/read-cache`). The tool result is `image/webp`.
- A result with no tokens and no message records no cost in the ledgers.
- While the guard is on, subagents are told that their cache lives 5 minutes, and that a job
  longer than 4 minutes runs in the background with checks inside 4 minutes (`PROMPT CACHE` in
  their prompt).

**How to check:**
1. `GET /api/mission/cache-guard` answers with `enabled`, `min_tokens`, `after_s` and one row
   for each live Claude session (its cached tokens, idle time, expiry and next step).
2. Run `python cache_guard_test.py` in `backend`. All checks pass.
3. Open `/settings?pane=cost`. The Prompt cache section shows the two switches, the compaction
   window and the live sessions.

## Other agent engines and terminals
parts: codex, chat_models, terminal, deepseek_creator
modules: codex_app, codex_runs, deepseek_session, cli_agents

**What it is.** Codex, DeepSeek, Kimi, Qwen and any chat engine added in Settings run in the
same chat box. A real terminal (a pty) runs any CLI that draws its own screen.

**Must work:**
- The chat box shows Codex as installed or not, and signed in or not (`GET /api/codex/status`).
  Its Install button installs it. Sign-in works with ChatGPT, a code or an API key.
- A chat engine added in Settings answers a test call (`POST /api/chat-providers/{pid}/test`).
  OpenAI-protocol engines go through the built-in bridge (`/api/bridge/{pid}/v1/messages`).
- The terminal lists the agent CLIs that are installed (`GET /api/terminal/agents`), and starts
  one in a new terminal pane.
- DeepSeek Creator mode: the DeepSeek settings → **Creator mode** installs the official DeepSeek
  Harness app once (`POST /api/deepseek/creator/install`, about 870 MB, with its own pnpm) and opens it
  over the window for the pane's folder (`POST /api/deepseek/creator/open`). New sessions start in
  Creator mode, the folder is the only workspace, and the model is the engine's own (a RIVER.AI key
  goes through the app's OpenAI-compatible route). Stop ends the app (`POST /api/deepseek/creator/stop`).
  `GET /api/deepseek/creator/status` says what is installed and what runs.

**How to check:**
1. `GET /api/codex/status`, `GET /api/chat-providers`, `GET /api/terminal/agents` and
   `GET /api/deepseek/creator/status` answer.
2. When Codex is installed and signed in, switch the chat box to Codex and send a short
   message. The reply streams into the same feed.
3. Open a terminal pane. Type `claude --version`. The version shows.
4. With a DeepSeek or RIVER.AI key: in a DeepSeek pane, open the settings and click **Creator mode**.
   After the install, the app opens; the session shows "Creator mode", the folder's name, and the
   engine's model. Send "Reply with the single word ready". Stop the app with **stop**.

## Workspaces, panes and files
parts: workspace, page:workspace
modules: fswatch

**What it is.** A folder is a workspace, and a workspace is a session. The Workspace tab is a
grid of panes: chats, files, images, 3D models and pages. The explorer is a file tree with an
editor.

**Must work:**
- On the first start, the Studio opens the projects that Claude Code knows, and project folders
  on the Desktop, in Documents and in Downloads. "Find my projects..." does it again.
- A folder dragged in from Windows becomes a workspace.
- The explorer shows the tree, opens a file in the editor, saves it, searches, and renames,
  moves, copies and deletes files.
- A file that an agent changes shows the change at once (the file watcher,
  `GET /api/workspace/changes`).
- Panes can be split, swapped by drag, made full size and put back. Each pane keeps its own
  project and conversation.
- The localhost menu finds the dev server of a game, and starts it with
  `POST /api/workspace/dev-server`.
- "New game" makes a game that the live link, the forge and the review can use.

**How to check:**
1. `GET /api/workspace/roots` answers and lists the workspaces.
2. Open the Workspace tab. Open a file, change one line, save. Look at the file on disk.
3. Split a pane and open a second project in it. Both chats work.

## Worktrees and git
parts: worktrees, git

**What it is.** Several branches of one repository, side by side, with one agent each. A git
panel for each workspace or worktree.

**Must work:**
- The rail nests each worktree under its project, and shows the branch beside the name
  (`GET /api/worktrees/map`).
- The worktree dialog shows where a new worktree goes, then makes it
  (`POST /api/worktrees/create`). It gets its own session and pane.
- The git panel shows the status, and does commit, push and pull (`pull` is fast-forward only,
  with a clean tree).
- The GitHub state shows whether `gh` is installed and signed in (`/api/git/github`). `gh` is
  optional: without it the answer says so, and the rest of git works.

**How to check:**
1. `GET /api/worktrees/map` answers.
2. In a test repository, make a worktree from the dialog. It opens in its own pane.
3. Change a file in it, and commit it from the git panel. `git log` shows the commit.

## Watching agents: subagents and workflows
parts: workflows, page:workflows
modules: subagents

**What it is.** What each agent and subagent does while it works, and the Workflow tool's
multi-agent runs.

**Must work:**
- A subagent card shows its prompt, its tools, its context and its cost. It can open in its
  own pane.
- The rail shows a dot on each project that has a turn running.
- Two running subagents that change the same file are reported
  (`/api/mission/projects/{project_id}/subagents/collisions`).
- The Workflows tab lists the runs, live runs first, with phases and agents (`GET /api/workflows`).

**How to check:**
1. `GET /api/workflows` and `GET /api/mission/agents/running` answer.
2. Ask an agent to use a subagent for a small task. The subagent card shows while it works.

## The code graph (graphify)
modules: graphify_index, graph_hook, graph_watch

**What it is.** A live code knowledge graph for each project. It answers where a symbol is,
what calls it and what it uses, with `file:line`. Its routes are in the `settings` group
(`/api/graphify/...`).

**Must work:**
- graphify installs itself into its own venv. `GET /api/graphify/status` says if it is usable.
- `/api/graphify/query?root=<project>&q=<symbol>` answers in milliseconds. A project that has
  no graph yet starts a build on the first question.
- The graph is rebuilt in the background when code changes.
- The `PreToolUse` hook answers a symbol-shaped Grep from the graph, and refuses a read of
  `graph.json`.

**How to check:**
1. `GET /api/graphify/status` answers.
2. Ask the graph about the Studio itself:
   `/api/graphify/query?root=<the Studio folder>&q=bench`. It lists symbols with
   `file:line`. On a new machine the first answer can say that a build started: ask again
   after one minute.

## The web an agent can reach
parts: web
modules: web_tools

**What it is.** A fetch that gets past pages that block robots (Scrapling), and a web search
with no API key.

**Must work:**
- `POST /api/web/fetch {"url": "<url>"}` returns the page as text, also for a page that shows
  "verify you are human" to a plain request.
- `/api/web/search?q=<words>` returns results with no key.
- `GET /api/web/status` says if the web tools are installed. `POST /api/web/install` installs
  them.

**How to check:**
1. `GET /api/web/status` answers.
2. Search for "three.js GLTFLoader" with `/api/web/search`. Results come back.
3. Fetch `https://example.com` with `POST /api/web/fetch`. The text holds "Example Domain".

## The running game: dev servers, live link and visual review
parts: live, live_stream, review
modules: dev_server

**What it is.** The Studio finds the dev server of each game. One shared headless browser holds
the game for the live link, the review and the forge.

**Must work:**
- The live link opens a game (`POST /api/live/open`) and finds the engine object: PlayCanvas,
  three.js, Babylon, Phaser, PixiJS, Cocos or a plain canvas.
- It reads the scene, the console and the frame cost, and runs code in the page
  (`POST /api/live/eval`). It sends keys, mouse, taps and swipes (`POST /api/live/input`).
- Changes made in the running game can be saved to `studio.edits.json`
  (`POST /api/live/edit` with `save`), and `/api/live/edits?project=<game>` reads them back.
- The visual review (`POST /api/review/render`) returns a contact sheet of exact frames,
  plus numbers (`findings`, `metrics`).
- The browser pill in the status bar says which project the browser is for
  (`GET /api/review/browsers`). The Studio closes browser tabs that nobody uses.
- The Game tab of the Studio Engine shows the live tab as a stream (`/api/live/stream`).

**How to check:**
1. `GET /api/live/status`, `GET /api/review/status` and `GET /api/review/browsers` answer.
2. Make a test game with "New game" (three.js), and start its dev server.
3. `POST /api/live/open {"project": "<game folder>"}`. The answer names the engine, and
   `reachable` is true.
4. `POST /api/review/render {"project": "<game folder>", "times": [0, 1000]}`. Read the sheet.
   It shows the game.

## Studio Engine
parts: engine, gamemodel, polyhaven, polypizza, refs, page:engine, live
modules: agent_glb, glb, live_forge, library, assets_index, engine_tags

**What it is.** A second window: a Blender-shaped 3D editor, a 2D editor, the library of the
project's own assets, a live view of the running game, and the forge in which an agent builds
assets in code.

**Must work:**
- The window opens at `/engine`, with the tabs Runs, Edit, Game and Assets.
- The forge (`POST /api/live/forge`) builds asset code in a lit studio and returns numbers and a
  picture. `look`, `aim`, `edits`, `glb`, `export` and `compare` work on the same bench.
- With "watching" on, the Edit tab shows what the agent builds, while it builds it
  (`GET /api/live/bench`). A GLB file that an agent writes with its own tools shows too, and
  loads again each time the agent writes it.
- The Runs tab lists every generation, and a run can be replayed (`GET /api/engine/history`).
- The Assets tab lists the game's own builders, models and pictures (`/api/engine/assets`).
  A compressed model (Draco, meshopt) opens, because gltf-transform decodes it.
- The editor: select, move, turn and scale with the gizmo; edit mode, sculpt, shape keys,
  weight paint, bone constraints, curves, modifiers and materials; the graph editor; export
  GLB and OBJ; import OBJ and FBX. Edits are saved to `studio.edits.json`.
- The 2D editor shows a Phaser, PixiJS or HTML game with its objects, and the sprite editor
  saves a PNG and an atlas.
- Poly Haven search and download (`/api/polyhaven/search`), Poly Pizza search with a key
  (`GET /api/polypizza/status`), and human references made in Blender (`GET /api/refs/status`).
- `/gamemodel` publishes the bench into the game and the Catalog
  (`POST /api/gamemodel/publish`).

**How to check:**
1. `GET /api/engine/state`, `GET /api/engine/projects`, `GET /api/engine/history`,
   `GET /api/live/bench`, `GET /api/live/api`, `GET /api/polypizza/status` and
   `GET /api/refs/status` answer.
2. Open the Studio Engine window. Photograph `/engine` with the review render if you cannot see
   the screen.
3. Run a small forge build on the test game:
   `POST /api/live/forge {"project": "<game folder>", "js": "add(new THREE.Mesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshStandardMaterial()))", "numbers": true}`.
   The answer has `ok: true` and 12 triangles. The Edit tab shows the box.
4. Open a `.glb` from the Assets tab. It shows in the Edit tab.

## Generate 2D and 3D assets
parts: jobs, providers, services, uploads, page:image, page:video, page:studio2d, page:studio3d, page:texture, page:rig, page:pipeline, page:jobs, page:servers

**What it is.** The asset pipeline: 2D images, 2D processing, 3D generation, texturing,
rigging, optimisation and QA. Each stage uses a free local model or a paid API.

**Must work:**
- The providers list shows each provider, and says what to do when one is not available
  (`GET /api/providers`, `GET /api/providers/errors`).
- A job starts, shows its progress, and can be cancelled (`POST /api/jobs`,
  `GET /api/jobs/active`). The procedural providers work with no key and no GPU.
- Pipeline runs stages one after the other (`POST /api/pipeline`).
- Servers starts and stops the local model servers, and shows their logs (`GET /api/services`).
- A new AI can be added from Settings with no code (`POST /api/providers/custom`).
- Settings -> Local generators lists the ComfyUI models with their state
  (`GET /api/providers/comfy-catalog`).

**How to check:**
1. `GET /api/jobs`, `GET /api/jobs/active`, `GET /api/providers`, `GET /api/providers/errors`,
   `GET /api/services` and `GET /api/providers/comfy-catalog` answer.
2. In the Image tab, make an image with the procedural provider (no key). The job finishes,
   and the image shows.
3. In the 3D Studio tab, make a procedural mesh. The model shows in the viewer.

## Catalog and Compare
parts: catalog, agent, page:catalog, page:compare

**What it is.** The Catalog keeps every asset with its preview, tags, provider, prompt, cost
and a commercial-use flag. Compare runs two providers on the same input.

**Must work:**
- The Catalog lists and filters assets, shows the shelves with their counts, and edits the
  metadata (`GET /api/assets`, `GET /api/catalog/categories`).
- A 3D model made outside a job can be imported (`POST /api/library/import`).
- The Dashboard warns when there are assets that are not for commercial use.
- Compare runs two providers side by side (`POST /api/agent/compare`).

**How to check:**
1. `GET /api/assets`, `GET /api/catalog/summary`, `GET /api/catalog/categories` and
   `GET /api/agent/runs` answer.
2. Open the Catalog tab. The asset made in the pipeline check is there, with its preview.

## Ask AI
parts: chat, page:chat

**What it is.** A general chat with many models (OpenRouter), with saved conversations. It is
not a coding agent.

**Must work:**
- With a key, a message streams a reply (`POST /api/chat/conversations/{cid}/send`).
- Conversations are saved, and can be renamed and deleted.
- An image or a text file can be attached.

**How to check:**
1. `GET /api/chat/models` and `GET /api/chat/conversations` answer.
2. With an OpenRouter key, send "hello". A reply streams. Without a key: "needs a key".

## Settings, keys, skills and what agents are told
parts: settings, skills, mcp, tools, page:settings
modules: keychain, skill_sync

**What it is.** The Settings tab. API keys in the OS keychain. The skills that agents get.
Every note that an agent is told, each with a switch and its token cost. The Studio's own MCP
tools and the agent tools (tests, edits, file checks).

**Must work:**
- Settings are saved, and stay after a restart (`GET /api/settings`).
- A key is stored in the OS keychain, never in a file, and never returned in full
  (`GET /api/keys` shows only whether each key is set).
- Each agent note has a switch and a token cost (`GET /api/settings/agent-notes`). A switch
  changes what a new session is told.
- The skills list shows each installed skill, and a skill can be turned on and off
  (`GET /api/skills`). Skills can be exported and imported as a pack.
- The plugins list shows each optional part and whether it is on (`GET /api/plugins`).
- The MCP catalog lists the Studio tools for the stdio MCP server (`GET /api/mcp/catalog`).
- With Settings -> Studio engine -> Code tools on, the agent tools list the test suites and
  run them (`/api/tools/suites`, `POST /api/tools/tests`).

**How to check:**
1. `GET /api/settings`, `GET /api/plugins`, `GET /api/settings/agent-notes`, `GET /api/keys`,
   `GET /api/skills`, `GET /api/output-styles` and `GET /api/mcp/catalog` answer.
2. Open Settings. Change one switch, restart the backend, and open Settings again. The switch
   kept its value.
3. Enter a test key, then look at `GET /api/keys`. The key shows as set, and its value is not
   in the answer.

## This map and the installation check
tests: features_map_test.py

**What it is.** `FEATURES.md` (this file), made by `scripts/features/build.py` from the code and
from `docs/features/checks.md`, and `scripts/features/verify.py`, which checks a running
installation against it.

**Must work:**
- The git pre-commit hook builds the map again in each commit, and adds it to the commit when
  it changed. It never stops a commit. `Setup.bat` installs it in a clone;
  `python scripts/features/build.py --install-hook` installs it by hand.
- GitHub Actions builds the map again on each push to `main`, and commits it only when it
  changed (`.github/workflows/features.yml`).
- The same code gives the same map on every machine: no dates, no commit ids, no local paths.
- `verify.py` finds a route that the map has and the backend does not serve.

**How to check:**
1. `python scripts/features/build.py --check` says that the map is in step with the code. (In
   an installed copy, with no `.git`, a difference is normal: it reads the folder, not git.)
2. `python scripts/features/verify.py` prints the report and ends with a result line.

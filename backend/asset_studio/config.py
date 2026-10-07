"""Central configuration & path management for Asset Studio.

All runtime configuration lives in a single JSON file under the data directory so
the whole app is portable and local-first. API keys are *not* stored here — they go
in the OS keychain (see :mod:`asset_studio.keychain`).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Stdlib-only by design, so importing it from here cannot create a cycle — see quarantine.py.
from . import quarantine
# Stdlib-only too: the settings file is replaced with a retry (see fsutil.py).
from . import fsutil

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Repo root = .../STUDIO ; backend pkg = .../STUDIO/backend/asset_studio
PKG_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PKG_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

DATA_DIR = Path(os.environ.get("ASSET_STUDIO_DATA", ROOT_DIR / "data")).resolve()
ASSETS_DIR = DATA_DIR / "assets"
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "catalog.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
# A second backend can serve a test build of the UI (ASSET_STUDIO_DIST=<folder>) while the running
# one keeps serving frontend/dist to the window that is open.
FRONTEND_DIST = (Path(os.environ["ASSET_STUDIO_DIST"]) if os.environ.get("ASSET_STUDIO_DIST")
                 else ROOT_DIR / "frontend" / "dist")


def claude_home() -> Path:
    """Claude Code's own folder on THIS PC: CLAUDE_CONFIG_DIR when it is set, else ~/.claude.

    One answer for every module. Half of them read CLAUDE_CONFIG_DIR and half assumed ~/.claude,
    so on a PC where the variable is set the credentials, the usage meter, autolearn and the
    project finder all looked in a folder Claude Code does not use."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base).expanduser() if base else Path.home() / ".claude"


def claude_json() -> Path:
    """Claude Code's global state file: inside CLAUDE_CONFIG_DIR when that is set and holds one,
    else ~/.claude.json - the project registry the Studio reads to find a new PC's projects."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    if base:
        p = Path(base).expanduser() / ".claude.json"
        if p.is_file():
            return p
    return Path.home() / ".claude.json"


def wheelhouse() -> "Path | None":
    """A folder of prebuilt wheels to install from with no internet, or None.

    The installer .exe ships one (runtime/wheelhouse) so a new PC installs every Python package
    offline; ASSET_STUDIO_WHEELHOUSE points anywhere else. pip tries it first, then PyPI."""
    env = os.environ.get("ASSET_STUDIO_WHEELHOUSE")
    for cand in ([Path(env)] if env else []) + [ROOT_DIR / "runtime" / "wheelhouse"]:
        try:
            if cand.is_dir() and any(cand.glob("*.whl")):
                return cand
        except OSError:
            continue
    return None


def pip_attempts() -> "list[list[str]]":
    """The pip install flags to try, in order: the bundled wheels alone, then PyPI (with the
    wheels still preferred). A PC with no internet gets everything from the first; a wheelhouse
    that lacks one package falls through to the second instead of failing."""
    wh = wheelhouse()
    if not wh:
        return [["--prefer-binary"]]
    return [["--no-index", "--find-links", str(wh)], ["--prefer-binary", "--find-links", str(wh)]]


def venv_has(venv: Path, package: str) -> bool:
    """Is `package` really installed in this venv? The venv folder exists the moment
    `python -m venv` runs, long before pip has put anything in it - so a half-finished install
    used to count as installed forever and was never retried."""
    try:
        sites = [venv / "Lib" / "site-packages"] + sorted((venv / "lib").glob("python3*/site-packages"))
        return any((sp / package / "__init__.py").is_file() for sp in sites)
    except OSError:
        return False


def registry_path() -> "list[str]":
    """The PATH Windows holds NOW - the machine's, then the user's - with %VARIABLES% expanded."""
    if os.name != "nt":
        return []
    import winreg
    out: list[str] = []
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                raw = str(winreg.QueryValueEx(k, "Path")[0] or "")
        except OSError:
            continue
        for part in raw.split(";"):
            part = winreg.ExpandEnvironmentStrings(part.strip()) if part.strip() else ""
            if part:
                out.append(part)
    return out


def refresh_path() -> "list[str]":
    """Add to this process's PATH every folder Windows now has on PATH and this process lacks.

    A process keeps the PATH it was started with. The installer starts the app from its last page,
    before its own PATH changes reach anyone, and a restart from inside the app inherits the
    launcher's old PATH - so a tool installed a minute ago (claude, graphify, gltf-transform, Node)
    was invisible to the Studio and to every agent it started, until Windows was signed out of.
    Appended, so nothing already on PATH changes its order. ASSET_STUDIO_REGISTRY_PATH=0 turns it
    off (the fresh-PC rig does, because its registry PATH is this PC's)."""
    if os.name != "nt" or os.environ.get("ASSET_STUDIO_REGISTRY_PATH") == "0":
        return []
    cur = os.environ.get("PATH", "")
    have = {p.strip().rstrip("\\/").lower() for p in cur.split(os.pathsep) if p.strip()}
    added: list[str] = []
    try:
        for p in registry_path():
            k = p.rstrip("\\/").lower()
            if k not in have and os.path.isdir(p):
                have.add(k)
                added.append(p)
    except Exception:
        return []
    if added:
        os.environ["PATH"] = cur.rstrip(os.pathsep) + os.pathsep + os.pathsep.join(added)
    return added

for _p in (DATA_DIR, ASSETS_DIR, JOBS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Defaults — written to settings.json on first run; user-editable from the UI.
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8777,
    "default_providers": {
        "video": "minimax-h3",
        "image2d": "placeholder-image",
        "process2d": "rembg",
        "gen3d": "placeholder-3d",
        "texture": "tripo-texture",
        "rig": "blender-rig",
        "optimize": "gltf-transform",
        "qa": "turntable",
    },
    # local tool locations / endpoints — override to point at your installs
    "tools": {
        "comfyui_url": "http://127.0.0.1:8188",
        "blender_path": "blender",            # on PATH, or absolute path to blender.exe
        "gltf_transform_cmd": "gltf-transform",  # npm i -g @gltf-transform/cli
        "trellis_url": "http://127.0.0.1:7860",
        "trellis_space": "",   # HuggingFace TRELLIS Space id for hosted mode, e.g. "JeffreyXiang/TRELLIS"
        "hunyuan_url": "http://127.0.0.1:8080",
        "unirig_path": "",
        # Blank = auto-detect: the `claude` on PATH first (the copy `claude update` keeps
        # current), then the newest VSCode extension as a fallback. Set it only to PIN a
        # version on purpose — an explicit path stops tracking updates.
        "claude_path": "",
    },
    # defaults for sending messages to Claude Code sessions from Mission Control
    "session_defaults": {"model": "default", "permission_mode": "acceptEdits", "fork": False},
    # extra folders opened in the Workspace that aren't Claude Code projects (yet)
    "workspace_roots": [],
    # First run on a new PC, nothing open yet: open the folders that look like their projects
    # (Claude Code's own registry first, then Desktop/Documents/Downloads/dev…). Only when EMPTY.
    "workspace_autodiscover": True,
    # tell Claude (on studio sends) it can call the 2D/3D generator API. OFF by default: a fresh
    # PC's agents are told about the generators only when somebody turns this on.
    "studio_tools_prompt": False,
    # keep one live `claude` process per project so messages stream in instantly
    # (fire several back-to-back without waiting, like the VS Code extension)
    "cc_streaming": True,
    # when the session model is Fable, append a compact token-efficiency directive to the system
    # prompt (act-don't-resurvey, outcome-first, no unrequested refactors, route subagents cheaply
    # via the ai-token-routing skill). Style-only — it never caps Fable's reasoning power.
    "cc_fable_efficient": True,
    # CLI 2.1.257's --system-prompt-snapshot: record the system prompt once per conversation and
    # replay it verbatim, so the prompt-cache prefix stops moving between launches. OFF by default
    # because the Studio always sends --append-system-prompt, and a recorded prompt makes a later
    # launch's DIFFERENT notes be ignored until the next compaction. cc_session._snapshot_flag
    # fingerprints the notes and sends "off" for the one launch that changes them, so a toggle
    # still takes effect — see its docstring for what was measured.
    "cc_prompt_snapshot": False,
    # always request Claude's 1M-token context window via the `[1m]` model suffix.
    # Opus 1M is included on Max/Team/Enterprise at no extra cost (Sonnet 1M costs
    # usage credits, so only Opus is auto-upgraded). Set false for the standard 200k window.
    "cc_1m": True,
    # auto-capture reusable build knowledge as personal skills (studio-ui/game/backend/integrations/
    # general) while you work — OFF by default (it is the biggest note, ~1900 tokens a turn);
    # toggle on from the chat box (🎓) or Settings.
    "cc_autolearn": False,
    # inject the persistent auto-memory (MEMORY.md) on sends; OFF = tell Claude not to read/use/update it.
    "cc_memory": True,
    # Fast mode: Claude Opus with faster output (it does NOT drop to a smaller model). Applies to
    # the Opus models only — Fable/Sonnet/Haiku ignore it. Headless sessions must opt in, so the
    # Studio writes the opt-in into the per-session settings file it already passes.
    "cc_fast_mode": False,
    # Codex's native collaboration Plan mode, selected in the chat's model settings.
    "codex_planner": False,
    # Native OS toast + taskbar flash when a workspace finishes a turn. A run can take ten
    # minutes and the point of the app is that you go and do something else meanwhile; the
    # in-app toasts only exist while you are already looking at the window. Fires for ANY
    # workspace, not just the open one, and stays quiet while you are watching that workspace.
    "notify_turn_end": True,
    # blender-kiln: a 3D asset pipeline skill that pilots Blender over MCP (brief -> source ->
    # import -> cleanup -> texture -> optimize -> export GLB). OFF by default because it only
    # works once Blender is running with the MCP addon on :9876 AND a `blender` MCP server is
    # registered with the CLI — telling Claude about a skill it cannot actually drive is worse
    # than saying nothing, because it will try and fail mid-task.
    "cc_blender_kiln": False,
    # Which engine turns speech into text. "local" = faster-whisper on this PC (private, offline,
    # but ~3GB of VRAM competing with ComfyUI on an 8GB card). "groq" = the same Whisper family
    # hosted, so dictation costs the GPU nothing — exactly when it matters, because the GPU is
    # busy generating assets. Local stays the default: it needs no key and no network.
    "voice_engine": "local",
    # whisper-large-v3 on purpose, NOT -turbo. Turbo is a distilled model and Groq's translations
    # endpoint does not accept it at all — and voice_task defaults to "translate", so turbo would
    # break the one thing this is used for.
    "voice_groq_model": "whisper-large-v3",
    # active output style — a system-prompt overlay that changes HOW Claude writes, never what it may
    # do. Claude Code's own ~/.claude/output-styles/<id>.md format, so a style set here is the same one
    # `/output-style` uses in a terminal. "" = off (Claude's default voice). Bundled: "asd-ste100".
    "cc_output_style": "",
    # surface graphify: when ON, Claude is told a queryable knowledge graph exists for the project and to
    # prefer `graphify query` over broad grep/read (self-heals: installs graphifyy on first use if missing).
    # ON by default — re-discovery (re-grepping what's already mapped) is the #1 token sink on code
    # projects; toggle off per taste from the chat box.
    "cc_graphify": True,
    # Read a page that refuses robots, and search without an API key. ON by default: a blocked
    # page is invisible otherwise — the agent writes up whatever was not blocked and nothing
    # says a source was missing. Costs one short note in the prompt and nothing when unused.
    "cc_web_tools": True,
    # tell the agent that the workspace has a Phases panel and how to fill it. Claude Code hands
    # the TaskCreate/TaskUpdate phase tools to Haiku only right now (2.1.235) — on Opus and Sonnet
    # the model has no phase tool at all, so the note also gives it a file to write instead. OFF =
    # no note, and the panel then fills only for a model that happens to have the Task tools.
    "cc_phases": True,
    # move FINISHED phase lists out of the session's task folder half an hour after their last
    # change, into data/phases (the Phases panel's history still shows them). The CLI repeats the
    # whole task list in a reminder every few tool calls: one STUDIO session had grown to 554
    # finished tasks, about 10k tokens a reminder. OFF = the list keeps everything, as before.
    "cc_phase_archive": True,
    # tell the agent that deterministic visual review exists (contact sheets it can judge art
    # from, instead of one arbitrary screenshot of a running game). OFF by default: it is ~700
    # tokens, and it was going to every project on the machine because the only gate was "is
    # Chrome installed" — which says nothing about whether the project has a page. Even when ON
    # it is now withheld from a project with nothing a browser could open.
    "cc_review": False,
    # ...and to SUBAGENTS as well. Separate, and off, because a subagent prompt is not shared
    # with the parent's cache: measured at ~697 tokens EACH, so a ten-agent fan-out paid ~7k for
    # a harness most of them never touch. Turn it on when the agents you fan out judge art.
    "cc_review_subagents": False,
    # ...and the ENGINE notes to subagents (live link, forge, navigate, scene edit): ~11k chars
    # EACH, not shared with the parent's cache. Off: a subagent gets one line naming
    # /api/settings/engine-notes, where it reads the full notes when its task needs the engine.
    "cc_engine_subagents": False,
    # default sheet detail: draft (~800 image tokens) | normal (~2200) | high (~4300). A single
    # request can still override it; this is what the agent gets when it does not say.
    "cc_review_quality": "normal",
    # let the review browser use the GPU. Measured here: headless Chrome picks the real GPU on its
    # own (ANGLE/NVIDIA) and a forced-software path is ~11x slower to read a frame back. The only
    # honest reason to turn this off is to leave the GPU alone while a local model is loaded.
    "cc_review_gpu": True,
    # a live tab of the running game that an agent can question and change while it runs:
    # /api/live/eval returns the VALUE of an expression, so "why is the mountain black" is
    # answered with `material.diffuse === '#000000'` instead of guessed from a picture. ON by
    # default, unlike the review harness, for two reasons: the note is ~440 tokens rather than
    # ~680, and it costs nothing until an agent calls it — no tab opens, no browser starts. It
    # rides the same headless Chrome the review harness uses; one browser, one more tab.
    # OFF on a fresh install: a new PC's agents know the code graph, the web fetch and the forge,
    # and nothing else until it is switched on (Settings → Studio engine, or Planning & review).
    "cc_live": False,
    # The forge (asset code rendered in a lit studio, with numbers) and the mesh-ops library. Each
    # is its own note with its own switch; off means the agent is never told it exists, and the
    # forge endpoint refuses. ON: this is what the Studio is for.
    "cc_forge": True,
    "cc_ops": True,
    # THE SAME ENGINE OVER MCP. Every Studio session is handed the Studio's own stdio MCP server
    # (mcp_engine.py): the forge, the live link, scene edit and the rest as tools, gated by the
    # same switches as the notes above. It adds nothing an agent is not already told about, and
    # it never replaces curl — a tool is the same call. ON: typed arguments, no shell quoting of
    # JavaScript, and a render's picture in the same answer.
    "cc_mcp": True,
    # EDIT MODE. The editor's third mode: the vertices themselves, keyed by where the code
    # put them so a hand move survives the next parameter change. Its own note and its own
    # switch, because an agent that is only writing builder code never needs to be told.
    "cc_vertedit": False,
    # CODE TOOLS. A file's sha without its contents, a typecheck, a narrow edit that refuses when
    # the file moved under you, and the test suites as a job. Off by default: an agent writing
    # asset code never needs them, and they are the group with the widest reach into the repo.
    "cc_code_tools": False,
    # THE DEBUGGER. Stop asset code where it threw and read the variables in scope. Off by
    # default because it pauses the shared page for a moment, which nothing else does.
    "cc_debugger": False,
    # ANIMATION REVIEW. Run a cycle and photograph it on one fixed camera, with the feet measured
    # against a ground that does not move. Off by default: most assets do not animate, and an
    # agent building a static prop never needs to be told this exists.
    "cc_animate": False,
    # Navigate: go to a place in the RUNNING game and look at it — a camera the Studio owns, a
    # "where am I" with the named things in view, and `locate`, which turns a screenshot the user
    # pasted into a camera pose by rendering candidate views and scoring them against it. Off by
    # default like every other extra: an agent building an asset in the forge never needs it.
    "cc_navigate": False,
    # SCENE EDIT: the game's own objects as an agent's tools — list them with their keys, world
    # bounds and the line of code that made them; move, turn, scale, hide, place and undo in the
    # running game; save a change to studio.edits.json so the Edit tab and the next reload see it.
    # Built because an agent could only move a thing with an `eval` it wrote itself, gone at the
    # next reload. ON: it rides the live link, so a PC with the live link off never hears of it.
    "cc_scene_edit": True,
    # In a folder with no game yet, a short note that says a Studio-ready game can be started.
    "cc_new_game": True,
    # A NEW GAME, Studio-ready from the first minute: `window.__game`, the saved edits applied at
    # start, review targets, builders with their feet on y = 0, a zero-dependency dev server. The
    # last game started from an empty folder had sixty ad-hoc screenshots in its root — its agent
    # had built its own tools. Engine, where it goes ("" = beside the current project) and whether
    # to run `npm install` straight away.
    "new_game_engine": "three",
    "new_game_parent": "",
    "new_game_install": True,
    # The resolution the game is judged at, shared by the engine window and the agents.
    #
    # A game built for 1920x1080 that an agent looks at in a 900px pane is not the game: the HUD
    # reflows, text that fits stops fitting, and a layout fault the player would hit never appears
    # — or one appears that does not exist. Both sides read this, so what you see in the window
    # and what an agent judges are the same frame by construction. Empty = 1280x720.
    "cc_engine_view": {"w": 1280, "h": 720, "label": "720p"},
    # force every Claude session to start in plan mode, whatever the composer's per-message
    # selector says. OFF by default -- plan mode is a deliberate choice, not a house rule, and
    # forcing it on someone who wanted a one-line edit wastes a turn. ON = research and propose
    # first, write nothing until approved, on any model.
    "cc_force_plan": False,
    # optional token budgets (per rolling window) to turn usage into a % gauge
    "usage_budgets": {},  # e.g. {"session": 1500000, "daily": 5000000, "weekly": 20000000}
    # fraction of the model context window at which Claude Code auto-compacts
    "auto_compact_at": 0.92,
    # context window in tokens; 0 = auto (200k, or 1M if a session exceeds 200k)
    "context_window": 0,
    # browser used by the workspace "open in browser" button. Blank = auto-detect Chrome on any
    # drive, then fall back to the OS default. May be the exe or the folder that holds it.
    "chrome_path": "",
    # feature toggles from Settings → Plugins: {tab_id: bool}. Absent = on, so a tab added by a
    # newer build never disappears because an older settings file has not heard of it.
    # Background helpers keep their own keys (cc_graphify, cc_autolearn…) and are listed there too.
    # The asset-generation and system tabs start hidden: a fresh PC is a Claude workspace with a
    # code graph, a web fetch and the forge. Switch a tab on in Settings → Plugins when wanted.
    "plugins": {"compare": False, "texture": False, "image": False, "studio2d": False, "studio3d": False,
                "rig": False, "pipeline": False, "catalog": False, "jobs": False, "servers": False, "chat": False},
    # microphone button in the chat box (local Whisper). Off = the button is hidden and the
    # model is never loaded, so it holds no GPU memory.
    "voice_enabled": True,
    # move automation browser windows that open themselves off the main screen (the sweeper
    # re-reads this every tick, so the toggle takes effect without a restart)
    "sweep_browser_windows": True,
    # answer from memory while the files behind an answer have not changed (asset_studio/perf.py).
    # On: the project list, the phase files, the spawn count, the installed CLIs, the engine state
    # and the browser count are each remembered until the thing they describe changes. Off: every
    # request reads from disk again, which is slower but can never be stale. Read at each call, so
    # the switch takes effect immediately, with no restart.
    "perf_fast": True,
    # One line at the top of the workspace: who is blocked on a question, who is working, and how
    # many are quiet — across every project, not only the open one. Reads the overview the Studio
    # already keeps, so it starts no scan of its own. Off: the line is not drawn and not polled.
    "needs_you": True,
    # Click an element in the running page and send what it is to the agent: the HTML, the
    # computed CSS that applies and a cropped picture. Costs nothing until the Inspect tab is
    # opened; needs the live game link, which has its own switch.
    "live_pick": True,
    # Write a comment on a line of a diff — in the feed or in a checkpoint — collect them, and
    # hand the batch to the agent with the file and the code quoted. Costs nothing until used.
    "diff_notes": True,
    # cost tracking ledger settings
    "currency": "USD",
    "web_size_budget_kb": 5000,
    "telemetry": False,
    # drop cached GPU models after this many idle seconds so idle VRAM ~= 0 (0 = never release)
    "gpu_idle_release_seconds": 90,
    # local model servers Asset Studio can launch on demand (user fills in commands)
    "auto_start_services": True,
    "services": {},          # per-service overrides: {id: {command, cwd, autostart, health_url, port}}
    # voice input (local Whisper via faster-whisper — installed self-contained on first use).
    # voice_task: "translate" = any spoken language → English (incl. Greek); "transcribe" = keep spoken.
    "voice_model": "large-v3",     # best accuracy; switchable (small/medium/large-v3) — bigger = slower on CPU
    "voice_task": "translate",
    "voice_idle_unload_seconds": 180,   # free the Whisper model's GPU VRAM after this idle; 0 = keep loaded
    # BOOST — the ON/OFF button beside the prompt that makes a turn cheaper by using THIS
    # PC first. OFF by default: it changes which notes an agent gets and it moves switches
    # of its own, so it is a mode you turn on, not one you discover. When it is on, the
    # message you send is compressed on the CPU (ANSI noise, folded log runs, blank runs —
    # never a reworded sentence), the project's own code graph is asked about the symbols
    # you named before the agent has to, one constant note tells it to work that way, and
    # the profile below is applied. What it replaced is remembered in data/boost_saved.json
    # and put back the moment it is switched off. See asset_studio/boost.py for why each
    # part is where it is, and for what BOOST deliberately does NOT do.
    "boost": False,
    "boost_compress": True,         # rewrite the outgoing message on this PC
    "boost_prefetch": True,         # send the local graph's answer for symbols you named
    "boost_directive": True,        # one constant "budget mode" note in the system prompt
    # "safe" never touches a fenced code block or a base64 payload; "hard" also replaces a
    # large data: URI or base64 blob with its size. Safe is the default: a pasted blob is
    # usually something the agent was asked to decode, and folding it would break the task.
    "boost_level": "safe",
    # safety guards
    "min_free_gb": 3.0,             # block jobs when disk free below this
    # ...and the two that keep the PC usable while the GPU is busy.
    #
    # A LOCAL JOB IS REFUSED WHEN FREE SYSTEM RAM IS BELOW THIS. Not a nicety: one MiniMax H3 clip
    # leaves about 30.8 GB private working set on this machine (measured, comfy_common.py:265), and
    # a second heavyweight job on top of that does not fail — it pushes the rest into the page file,
    # and every process on the PC, including the chat, stops answering. A refused job with a
    # sentence explaining it is recoverable; a frozen desktop is not. 0 = no floor.
    "min_free_ram_gb": 4.0,
    # ONE HEAVY LOCAL JOB AT A TIME. The two workers exist so cheap API jobs overlap; two LOCAL
    # jobs share one GPU, and the second makes ComfyUI offload the first model into host RAM.
    # Serializing them costs nothing (the card was the bottleneck anyway) and removes the overlap
    # that froze the machine. Off is for someone splitting two GPUs by hand.
    "serialize_local_jobs": True,
    "monthly_spend_cap_usd": 0.0,   # 0 = disabled; blocks paid jobs past the cap
}


def first_run_seed() -> dict[str, Any]:
    """Settings carried from another PC: runtime/seed-settings.json, which the installer .exe
    ships (or ASSET_STUDIO_SEED_SETTINGS). Read ONLY when this PC has no settings.json yet, so a
    new PC starts with the settings of the PC it was built on and every later change is its own."""
    env = os.environ.get("ASSET_STUDIO_SEED_SETTINGS")
    for cand in ([Path(env)] if env else []) + [ROOT_DIR / "runtime" / "seed-settings.json"]:
        try:
            if cand.is_file():
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            continue
    return {}


class Settings:
    """Lazy JSON-backed settings store. Reloads from disk; writes atomically."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self.path = path
        self._cache: dict[str, Any] | None = None
        # Two threads saving at once shared one ".tmp" file: one write was lost, and the second
        # replace failed because the first had already moved the file away.
        self._write_lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        # THE FIRST START ON A NEW PC takes the installer's seed, once, and writes it down - so
        # the seed is never read again and every later change belongs to this PC.
        if not self.path.exists():
            seed = first_run_seed()
            if seed:
                merged = _deep_merge(DEFAULT_SETTINGS, seed)
                try:
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
                    fsutil.replace(tmp, self.path)
                except OSError:
                    pass
                return merged
        # A damaged settings.json used to be swallowed into {} right here, and the very next
        # update() wrote the merged defaults straight back over it — every key the user had
        # set, gone, with no trace and nothing to restore from. Keep the bad bytes aside
        # first; only then is falling back to defaults a safe thing to do.
        data = quarantine.safe_load_json(self.path, {}, expect=dict)
        # deep-merge defaults so new keys appear after upgrades
        merged = _deep_merge(DEFAULT_SETTINGS, data)
        return merged

    def all(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = self._load()
        return self._cache

    def get(self, key: str, default: Any = None) -> Any:
        return self.all().get(key, default)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._write_lock:
            cur = self.all()
            merged = _deep_merge(cur, patch)
            self._cache = merged
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            fsutil.replace(tmp, self.path)
            return merged

    def reload(self) -> dict[str, Any]:
        self._cache = None
        return self.all()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


settings = Settings()

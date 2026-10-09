"""Send a message to a Claude Code session from inside the Studio.

Uses the bundled ``claude`` CLI in headless mode (``-p/--print``) to run one turn
in a project, resuming its most-recent session so the message continues that
conversation. The reply lands in the project's transcript, which Mission Control
already tails — so it streams into the card's live feed. Model / permission-mode /
fork are selectable per send.

This drives the user's own authenticated CLI on their own projects (exactly what
typing in the integrated terminal would do); nothing leaves the machine.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import engines, fsutil, live_notes
from .config import DATA_DIR, claude_home, settings
from . import perf
from . import mission
from . import plugins
from . import claude_auth
from . import model_access
from . import hook_events
from . import web_tools

SESS_DIR = DATA_DIR / "sessions"
SESS_DIR.mkdir(parents=True, exist_ok=True)
# One small json per live session: enough for a NEW backend to recognise a process the OLD one
# started, and to know where in its log the last completed turn ended. Written at spawn, updated
# at each turn boundary, deleted on a clean stop.
LIVE_DIR = DATA_DIR / "live"
LIVE_DIR.mkdir(parents=True, exist_ok=True)
# Windows kills a child with its parent only when a job object says so; breaking away makes the
# session genuinely independent. Harmless when there is no job, ACCESS_DENIED when a job forbids
# it, which is why the spawn retries without it.
_BREAKAWAY = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0) if os.name == "nt" else 0

_procs: dict[str, subprocess.Popen] = {}
_logfiles: dict[str, object] = {}      # keep the stdout handle so we can close it on reap
_last_agent: dict[str, str] = {}       # which agent last ran in a project (for the feed)

# Persistent streaming `claude` sessions (one long-lived process per project).
# Messages are written to the process's stdin as they're typed — so you can fire
# several in a row and Claude works through them without a respawn, exactly like
# the VS Code extension. The reply still lands in the transcript the feed tails.
_live: dict[str, "_Live"] = {}
_live_lock = threading.Lock()
_IDLE_TIMEOUT = 900.0   # reap a persistent session idle this long (seconds)
_reaper_started = False


_CALL_SEQ = [0]


def _next_call_id() -> int:
    """A number that goes up for every CLI process the Studio starts.

    Both ledgers were missing this, and without it a `turns.jsonl` row cannot be attributed to the
    run that produced it — which is exactly what let a 2.34x over-count go unnoticed for so long:
    the only way to find where one CLI call ended and the next began was to watch the cost column
    move backwards.
    """
    _CALL_SEQ[0] += 1
    return _CALL_SEQ[0]


class _Live:
    """A long-lived `claude -p --input-format stream-json` process for a project.

    Busy-tracking is coalescing-proof: ``outstanding`` counts messages written but
    not yet picked up by a turn; ``turn_active`` is True while a turn produces output.
    A turn consumes ALL queued messages at once (Claude may merge them), so the first
    activity of a turn zeroes ``outstanding``. Busy = turn_active or outstanding > 0 —
    no timeouts, so it survives both message-coalescing and slow turns."""
    __slots__ = ("project_id", "proc", "logf", "sig", "session_id",
                 # the log is the stream now, not a pipe: where it lives, how far we have read,
                 # and whether this session was inherited rather than started by us
                 "log_path", "read_from", "adopted",
                 # its stdin is held by a KEEPER process, not by us: the folder a message is
                 # dropped into and the pid holding the pipe open (see session_keeper.py)
                 "inbox", "keeper_pid",
                 "outstanding", "turn_active", "lock", "alive", "last_write", "last_result",
                 # live progress (from --include-partial-messages): real-time tokens + activity
                 "turn_committed", "cur_tokens", "cur_chars", "activity", "turn_started",
                 # generation-only timing: tokens/second means nothing measured over a turn
                 # that spent two minutes inside a tool, so only message windows are clocked
                 "gen_s", "msg_started", "turn_model", "turn_msgs",
                 # live streaming of the visible answer + the per-block token baseline + block kind
                 "cur_text", "cur_block_base", "cur_kind",
                 # True while the current turn is a /compact (so the UI shows "Compacting…")
                 "compacting",
                 # WS push throttle: last publish ts + "a message just completed" flag
                 "last_pub", "msg_done",
                 # a /btw note file is waiting for the hook — turn end queues it if unconsumed
                 "btw_pending",
                 # COST ACCOUNTING. `call_cost` is the CLI's running `total_cost_usd` for THIS
                 # process, so a turn can take its delta instead of the raw figure (see the result
                 # handler). `call_seq` names the process, so a ledger row can be attributed to the
                 # run that produced it — the one thing both ledgers were missing.
                 "call_cost", "call_seq")

    def __init__(self, project_id: str, proc: subprocess.Popen, logf, sig: tuple,
                 session_id: Optional[str]):
        self.project_id = project_id
        self.proc = proc
        self.logf = logf
        self.log_path = SESS_DIR / f"{project_id}.log"
        self.read_from = 0
        self.adopted = False
        self.inbox = ""              # set when a keeper holds stdin; "" = legacy direct pipe
        self.keeper_pid = 0
        self.sig = sig
        self.session_id = session_id
        self.outstanding = 0
        self.turn_active = False
        self.lock = threading.Lock()
        self.alive = True
        self.last_write = time.time()
        self.last_result = 0.0
        self.turn_committed = 0      # output tokens from completed messages this turn
        self.cur_tokens = 0          # running output tokens of the message generating right now
        self.cur_chars = 0           # chars streamed this block (live token estimate = chars//4)
        self.activity = ""           # what Claude is doing this instant (Thinking / Editing …)
        self.turn_started = 0.0      # epoch when the current turn began (for a live elapsed timer)
        self.gen_s = 0.0             # seconds this turn spent actually generating
        self.msg_started = 0.0       # epoch of the message being generated right now
        self.turn_model = ""         # the model that ACTUALLY served this turn
        self.turn_msgs = 0
        self.cur_text = ""           # the visible answer text streaming RIGHT NOW (text_delta)
        self.cur_block_base = 0      # cur_tokens banked when this content block started
        self.cur_kind = ""           # current content-block type: thinking / text / tool_use
        self.compacting = False      # this turn is a /compact — show "Compacting…" not "Working…"
        self.last_pub = 0.0          # last cc_live WS publish (throttles the push to ~16/s)
        self.msg_done = False        # a message completed since the last publish → push NOW
        self.btw_pending = False     # a /btw note awaits the mid-turn hook (or turn-end fallback)
        self.call_cost = 0.0         # the CLI's running total_cost_usd for THIS process
        self.call_seq = _next_call_id()

# Pinned ids first, then the aliases. `claude-fable-5-1` arrived with CLI 2.1.257 and
# `claude-opus-5-5` with 2.1.280; both roll out per account, so either can be listed here and still
# be refused — model_access learns that from the turn and the picker greys it, rather than the pick
# silently costing a turn. Opus 5.5 needs nothing else from this file: `_apply_1m` gives any id
# holding "opus" the [1m] suffix (its catalog entry sets supports_1m_suffix), fast mode is a config
# flag rather than a per-model argument, and --effort takes the same five levels.
MODELS = ["default", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "opus",
          "claude-fable-5-1", "claude-fable-5", "sonnet", "haiku"]
PERMISSION_MODES = ["default", "plan", "acceptEdits", "bypassPermissions"]


@lru_cache(maxsize=1)
def _ext_root() -> Path:
    return Path.home() / ".vscode" / "extensions"


_CLAUDE_TTL = perf.Ttl(30.0, limit=8)


def find_claude() -> Optional[str]:
    """Locate the claude executable: explicit setting → PATH install → newest VSCode extension.

    The extension used to win, and that quietly pinned every session to whatever binary VSCode
    last shipped. Measured here: the extension held 2.1.233 while the installed CLI was already
    2.1.250 — seventeen versions behind, for weeks, with nothing anywhere saying so. Worse, it
    is invisible: `claude --version` in a terminal reports the NEW one, so the machine looks up
    to date while every Studio session runs the old one.

    The PATH install is the copy `claude update` maintains and the one that updates itself, so
    it goes first. The extension stays as the fallback for a machine with the editor but no CLI
    on PATH. `tools.claude_path` still overrides both, for pinning a version on purpose.
    """
    # Remembered for 30 seconds, keyed on the override setting, because this runs on several polls
    # and each call is a PATH search plus a walk of the extension folder. `invalidate_claude()`
    # forgets it the moment the Studio installs or updates the CLI.
    override = settings.get("tools", {}).get("claude_path")
    return _CLAUDE_TTL.get(str(override or ""), lambda: _find_claude_build(override))


def invalidate_claude() -> None:
    """Forget the resolved binary — after an install, an update or a change of the pinned path."""
    _CLAUDE_TTL.drop()


def _find_claude_build(override: Optional[str]) -> Optional[str]:
    if override and Path(override).exists():
        return override
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    # Claude Code's own installer puts the binary in ~/.local/bin and, on Windows, does NOT put
    # that folder on PATH - it tells the user to do it by hand. A Studio started before that (or
    # from an Explorer that has not seen the change) then found no claude at all on a fresh PC.
    native = Path.home() / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
    if native.is_file():
        return str(native)
    best: tuple[tuple[int, int, int], str] | None = None
    root = _ext_root()
    if root.exists():
        for d in root.glob("anthropic.claude-code-*"):
            for rel in ("resources/native-binary/claude.exe", "resources/native-binary/claude"):
                exe = d / rel
                if exe.exists():
                    m = re.search(r"(\d+)\.(\d+)\.(\d+)", d.name)
                    ver = tuple(int(x) for x in m.groups()) if m else (0, 0, 0)  # type: ignore
                    if best is None or ver > best[0]:
                        best = (ver, str(exe))  # type: ignore
    if best:
        return best[1]
    return shutil.which("claude")


def status() -> dict:
    from . import agents
    exe = find_claude()
    return {"available": bool(exe), "path": exe or "", "models": MODELS,
            "modes": PERMISSION_MODES, "efforts": ["default", *EFFORT_LEVELS, "ultracode"],
            # models this account was refused on a real turn — the picker greys these
            "model_access": model_access.blocked(),
            "agents": agents.list_agents()}


def _resolve(project_id: str) -> tuple[Optional[str], Optional[str]]:
    """Return (cwd, session_id) for a project id (the project dir name).
    A 'kimi--' prefix resolves inside the Kimi engine's isolated session home."""
    root, pdir, bare = mission.project_dir(project_id)
    full_id = project_id          # keep the prefixed id — the pin is per engine (claude/kimi/…)
    project_id = bare
    if not pdir.exists():
        return None, None
    jsonls = list(pdir.glob("*.jsonl"))
    # continue the conversation the STUDIO pinned, not merely the last-touched transcript
    # (another tool writing in this folder must not hijack which chat we resume)
    chosen = mission.active_transcript(full_id, jsonls)
    session_id = chosen.stem if chosen else None
    meta = {}
    if session_id:
        meta = mission._meta_from_head(pdir / f"{session_id}.jsonl")
    if "cwd" not in meta:
        for jl in jsonls:
            meta = mission._meta_from_head(jl)
            if "cwd" in meta:
                break
    # renamed project → the head cwd points at the old (gone) folder; recover the real one
    return mission.repair_cwd(project_id, meta.get("cwd") or "") or None, session_id


def _transcripts_key(project_id: str) -> tuple:
    """(session home, bare project id). Two ids with the same pair write the same files."""
    pfx = mission.alt_prefix(project_id)
    home = mission.alt_homes().get(pfx, pfx) if pfx else ""
    return (home, project_id[len(pfx):] if pfx else project_id)


def _stop_shared(project_id: str) -> str:
    """Stop another engine's live session on the SAME transcripts, and say which.

    Providers can be pointed at one session universe so a chat survives switching between them.
    Only one CLI may hold it: two processes appending to a single transcript interleave their
    writes. Switching agent mid-answer therefore ends that answer, which is what switching
    means -- the alternative is a corrupted history."""
    mine = _transcripts_key(project_id)
    stopped = ""
    for other in [k for k in list(_live) if k != project_id]:
        if _transcripts_key(other) != mine:
            continue
        live = _live.get(other)
        if live is None:
            continue
        # Never silently kill a peer session that still has a background agent working. Two
        # engines on one transcript is the thing this function prevents, but a fan-out mid-flight
        # outranks that: the caller can wait, an interrupted agent cannot be recovered.
        if _open_background(other):
            continue
        _kill_live(live)
        _live.pop(other, None)
        stopped = other
    return stopped


def _reap(project_id: str) -> None:
    lf = _logfiles.pop(project_id, None)
    if lf is not None:
        try:
            lf.close()
        except Exception:
            pass
    _procs.pop(project_id, None)


def is_sending(project_id: str) -> bool:
    """Is a turn running in this feed — whichever engine it belongs to (see engines.py)."""
    return engines.for_feed(project_id).is_sending(project_id)


def _claude_is_sending(project_id: str) -> bool:
    """Claude's own answer: the streaming session, or a one-shot process. Also the alternate
    engines (Kimi, Qwen…), which run through this same plumbing under their prefix."""
    live = _live.get(project_id)
    if live is not None:
        if live.alive and live.proc.poll() is None:
            return _stream_busy(live)
        with _live_lock:  # process exited → reap
            if _live.get(project_id) is live:
                _live.pop(project_id, None)
    p = _procs.get(project_id)
    if p is None:
        return False
    if p.poll() is None:
        return True
    _reap(project_id)  # finished → release the process + its log handle
    return False


def pending(project_id: str) -> int:
    """1 while the live session is busy, else 0. (Coalescing makes an exact queue length
    meaningless; the UI tracks its own sent-message chips and clears them when idle.)"""
    return 1 if is_sending(project_id) else 0


def live_status() -> dict:
    """Busy state per project, and which engine is doing the work.

    Reported under the BARE folder id as well as the prefixed one. An alternate engine files its
    session under an `<engine>--` prefix, so a workspace list looking up the plain folder id
    matched nothing and the working indicator stayed dark for every agent except Claude.

    A folder can hold a session for more than one engine. The busy one owns the indicator, so
    the colour shown is the colour of the agent actually working.
    """
    out: dict[str, bool] = {}
    who: dict[str, str] = {}
    # How many subagents are in flight per project. The rail already polls this endpoint every
    # 1.5s for the working spinner, so the count rides along rather than opening a second poller.
    #
    # It once counted only for a project the stream already called busy, because the answer meant
    # reading every parent transcript the project owned — 523 MB on one real workspace here, six
    # to ten seconds inside the backend, on a poll that fires every 1.5 seconds. That is what
    # filled the browser's connection pool and left the chat blank. `subagents.running` is now
    # mtimes first and opens nothing unless something is genuinely running, so the count is honest
    # for every live session and costs about a millisecond.
    fanout: dict[str, int] = {}
    waiting: dict[str, int] = {}      # of those, the ones dispatched and quiet rather than typing
    total_agents = 0
    for pid in list(_live.keys()):
        try:
            stream = is_sending(pid)
        except Exception:
            stream = False
        # A PARENT WAITING ON ITS SUBAGENTS IS STILL WORKING.
        #
        # It hands the task out and then goes quiet — no tokens, no stream writes — until they
        # come back. The reaper has always known this and refuses to reap a session whose agents
        # are still writing; the indicator did not, so the busiest workspace on the machine went
        # dark. The count vanished with it, because the count was only computed for a project the
        # stream already called busy, which is the one case where it adds nothing.
        #
        # This is asked for EVERY live session now, not only busy ones. It used to mean reading
        # every transcript the project owns; it is now mtimes, about a millisecond.
        #
        # AND A BACKGROUND AGENT THAT HAS GONE QUIET HAS NOT STOPPED.
        #
        # `subagents.running` defaults to a 90-second window, which is the right question for "is
        # it moving". It is the wrong question for "is it there": a background agent sits inside
        # one long tool call for five minutes at a time, and at ninety seconds it vanished from
        # the rail and the dashboard while the card under the chat still showed it. Two places
        # said dead, one said working, and the user had no way to tell which was lying.
        #
        # So ask once with the WIDE window and split the answer: moving (wrote in the last 90s)
        # and quiet-but-owed (dispatched in the background, no outcome recorded). Both are on
        # screen; only their wording differs. One pass, because the wide question costs the same
        # as the narrow one - mtimes, and a cursor-cached parent scan.
        moving = quiet = 0
        try:
            from . import subagents
            for a in subagents.working(pid):
                if a.get("moving"):
                    moving += 1
                else:
                    quiet += 1
        except Exception:
            pass
        n = moving + quiet
        busy = stream or n > 0
        out[pid] = busy
        pfx = mission.alt_prefix(pid)
        bare = pid[len(pfx):] if pfx else pid
        if bare != pid:
            out[bare] = out.get(bare, False) or busy
        if busy or bare not in who:
            who[bare] = pfx[:-2] if pfx else "claude"
        if n:
            fanout[pid] = n
            fanout[bare] = n
            total_agents += n
        if quiet:
            waiting[pid] = quiet
            waiting[bare] = quiet
    # Codex and DeepSeek conversations do not live in `_live`; each engine reports its own, under
    # "<prefix><folder>" and the bare folder id, the same way.
    for eng in engines.ALL:
        if eng is engines.CLAUDE:
            continue
        try:
            rows = eng.live_rows()
        except Exception:
            continue
        for bare, busy, n in rows:
            fid = eng.feed_id(bare)
            out[fid] = busy
            out[bare] = out.get(bare, False) or busy
            if busy or bare not in who:
                who[bare] = eng.id
            if n:
                fanout[fid] = n
                fanout[bare] = fanout.get(bare, 0) + n
                total_agents += n
    # The map is keyed by BOTH ids so either lookup works, which makes summing its values wrong
    # for an alternate engine. The honest total is counted here, where the prefix is known.
    return {"statuses": out, "agents": who, "running_agents": fanout,
            "quiet_agents": waiting, "running_agents_total": total_agents}


def last_agent(project_id: str) -> str:
    return _last_agent.get(project_id, "")


def log_tail(project_id: str, lines: int = 80) -> str:
    f = SESS_DIR / f"{project_id}.log"
    if not f.exists():
        return ""
    try:
        return "\n".join(f.read_bytes()[-16384:].decode("utf-8", "ignore").splitlines()[-lines:])
    except OSError:
        return ""


EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]


def _resolve_node_shim(exe: str) -> Optional[list[str]]:
    """Map an npm ``.cmd``/``.bat`` shim to ``[node, <script>.js]`` so we bypass
    cmd.exe entirely. Routing a prompt through a batch shim corrupts it on Windows:
    cmd.exe truncates the text at the first newline, expands ``%VAR%`` tokens, and
    breaks on spaced paths. Invoking the underlying Node script directly passes argv
    verbatim via CreateProcess. Returns ``None`` for real ``.exe`` binaries or if the
    script can't be resolved (caller then launches the exe as-is)."""
    if os.name != "nt" or not exe.lower().endswith((".cmd", ".bat")):
        return None
    try:
        text = Path(exe).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = re.search(r'(?:%~dp0|%dp0%)[\\/]?([^"%\r\n]+?\.js)', text)
    if not m:
        return None
    node = shutil.which("node")
    if not node:
        return None
    rel = m.group(1).strip().replace("/", os.sep).replace("\\", os.sep)
    js = (Path(exe).parent / rel).resolve()
    if not js.exists():
        return None
    return [node, str(js)]


def _studio_note() -> str:
    """System-prompt note so Claude knows it can drive the studio's 2D/3D generators."""
    port = int(settings.get("port", 8777) or 8777)
    base = f"http://127.0.0.1:{port}"
    return (
        f"You're running inside Asset Studio (a local creative app) at {base}. Besides coding, you can "
        f"GENERATE 2D images and 3D models on demand by calling its local HTTP API with curl: "
        f"GET {base}/api/providers?stage=image2d (also stages gen3d, process2d, texture, rig, optimize, qa) "
        f"lists the engines and which are available; "
        f'POST {base}/api/jobs with JSON {{"stage":"image2d","provider_id":"<id>","params":{{"prompt":"..."}},'
        f'"inputs":["<absolute input file path>"]}} starts a generation; '
        f"GET {base}/api/jobs/<id> until \"status\":\"succeeded\" — the result file is outputs[0].path. "
        f"Prefer providers with available=true (e.g. the ComfyUI presets for local 2D/3D). "
        # OPT-IN, and firmly. The old wording ended "...or game assets", which in a game project
        # matches almost every task: "make the mountains look better" reads as an asset request,
        # and generation got reached for roughly nine times in ten when the user wanted code. It
        # also silently overrode a preference the user had already recorded — procedural Three.js
        # assets over generated files — because a system-prompt line is present in every session
        # and a memory note only surfaces when it is relevant.
        f"USE IT ONLY WHEN EXPLICITLY ASKED for a generated picture or 3D model — \"generate a "
        f"sprite\", \"make me a texture\", \"an image of X\", \"a 3D model of Y\". A task that "
        f"merely TOUCHES art or a game is NOT such a request: \"make the mountains look better\", "
        f"\"add a penguin\", \"the slope needs snow\" are CODE tasks, and this user's standing "
        f"preference is procedural code assets (Three.js) over generated files. If you think "
        f"generation is the right answer but were not asked for it, say so in ONE line and let "
        f"them decide — do not start a job. "
        f"When you need the user to decide between options, ASK IN PLAIN TEXT and list the options "
        f"clearly so they can answer by typing — do NOT call the AskUserQuestion tool, which cannot "
        f"be answered from this chat. "
        f"HEADLESS BROWSER (checking pages/games/UIs): run "
        f'`python "{Path(__file__).resolve().parent / "tools" / "browse.py"}" <url> --out shot.png --console` '
        f"— renders for real (canvas/WebGL), screenshots, and reports console errors; options: --js, --click, "
        f"--until, --html, --full, --wait. First run self-installs (~90 MB). ALWAYS use this instead of opening "
        f"a visible browser: never launch with headless:false, never `start http://…` — stray browser windows "
        f"on the user's desktop are a bug; the Studio shows them a 'headless browser active' pill instead. "
        f"FILE PATHS IN CHAT: always write the FULL absolute path (e.g. "
        f"`C:\\Users\\me\\proj\\src\\app.ts`, or `C:\\Users\\me\\proj\\src\\app.ts:42` for a line). The Studio "
        f"turns a path in your answer into a click that opens or previews that file, and a bare file name "
        f"often cannot be resolved — the click then does nothing. Never shorten a path to just the file name, "
        f"and never write a path relative to a cwd the reader cannot see."
    )


def _skills_inventory() -> str:
    """A compact list of the user's existing personal skills (id · category · gist) so
    auto-learn can EXTEND the right one instead of duplicating. Critical now that most
    skills are disabled (`disable-model-invocation`) and therefore NOT auto-loaded — without
    this the model can't see what already exists when it decides create-new vs edit."""
    try:
        from . import skills as _skills
        items = _skills._scan_dir(_skills._claude_home() / "skills", "personal")
    except Exception:
        return ""
    lines: list[str] = []
    for it in sorted(items, key=lambda x: x.get("id", "")):
        desc = (it.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:90] + "…"
        cat = it.get("category") or ""
        lines.append(f"- {it.get('id')}" + (f" [{cat}]" if cat else "") + (f": {desc}" if desc else ""))
        if len(lines) >= 60:
            break
    return "\n".join(lines)


def _autolearn_note() -> str:
    """System-prompt note injected when cc_autolearn is on: auto-capture reusable build knowledge as skills."""
    note = (
        "AUTO-LEARN is ON. As we work, AUTO-SAVE reusable build knowledge into the matching personal "
        "skill (a SKILL.md under ~/.claude/skills/, create if missing): UI/component recipes -> studio-ui; "
        "game patterns -> studio-game; servers/APIs/data -> studio-backend; third-party/service/API "
        "integrations -> studio-integrations; anything else -> studio-general. SAVE ESPECIALLY when (a) "
        "the user signals they like or approve something we built ('i like this', 'perfect', 'keep this') "
        "-> bank the REUSABLE recipe behind it (the approach, pattern, or snippet) written generically "
        "enough to reuse on a future project, NOT the project-specific instance; or (b) you looked up or "
        "verified an API fact, model id, endpoint, or non-obvious fix. Write each as a dated, sourced "
        "'## <topic>' entry; merge into existing entries instead of duplicating. If the user later "
        "corrects something you saved, find that entry and fix or delete it. Do NOT ask first -- just "
        "save, then mention it in one short line. Skip secrets, project-specific trivia, and things "
        "already 100% known.\n"
        "ITERATIVE REFINEMENT (capture the CONVERGED result, never the dead-ends): when the user keeps "
        "adjusting the SAME thing across turns (e.g. tweaking a smoke effect again and again), do NOT save "
        "the in-progress or rejected versions -- HOLD OFF, and track what they REJECT vs ACCEPT as it "
        "evolves. Treat it as converged ONLY when they either (a) approve it ('good now', 'perfect', 'keep "
        "it', 'leave it'), or (b) stop bringing it up and move on to a different task for a few turns. At "
        "that point save/merge ONE entry: the FINAL validated recipe PLUS the preference it encodes -- what "
        "they wanted, what they rejected, and the change that fixed it -- written so it's right the FIRST "
        "time next project (e.g. 'smoke: soft, slow-rising, low-opacity, grey-blue; NOT fast/dense/white; "
        "fix was additive blend at low alpha + ~3s lifetime'). For a long back-and-forth you MAY keep a "
        "running draft in the target skill (it's disabled = zero context cost) and clean it up at "
        "convergence; if they revisit it later, update that SAME entry and bump `updated`.\n"
        "MAKE IT REPRODUCIBLE (a drop-in kit, not a vibe): for a concrete buildable thing (effect/shader/"
        "component/config), save the EXACT final code and PARAMETER VALUES verbatim, the engine/stack it "
        "assumes (e.g. 'Unity URP particle system', 'Three.js Points + additive blend', '2D canvas'), WHERE "
        "it hooks in, and any ASSET it needs -- bundle a small file in the skill folder, or record the exact "
        "generation prompt/seed if it was AI-made. Then enabling the skill in a same-stack project reproduces "
        "it faithfully (near-identical); in a different stack you keep the look/preferences and adapt. Vague "
        "prose reproduces vaguely -- be exact.\n"
        "WRITE GREAT SKILLS: lead with the compact concept (leading words); keep one source of truth per "
        "fact; prune anything that doesn't change behavior vs the default (the 'no-op test'); give a clear "
        "'Use when ...' description as the trigger. NEW skills start DISABLED so the user opts in from the "
        "Skills tab: when you CREATE a new skill file, put `disable-model-invocation: true` in its "
        "frontmatter and add `metadata.created` AND `metadata.updated` set to today's date. When you EDIT "
        "an existing skill, bump `metadata.updated`, keep `created`, and do NOT re-add the disable flag "
        "(respect the user's enable choice)."
    )
    inv = _skills_inventory()
    if inv:
        note += (
            "\nYOUR EXISTING SKILLS (most are disabled so you won't see their bodies — this is the index). "
            "Before saving, MATCH the learning to the closest one and EXTEND it (open that SKILL.md, merge "
            "your entry, bump `updated`); only create a NEW skill folder when nothing here fits, and name it "
            "precisely (e.g. a game smoke effect -> extend `studio-game` or a `game-vfx-*` skill, NOT a vague "
            "name). The index:\n" + inv
        )
    return note


def _phases_note(project_id: str) -> str:
    """System-prompt note injected when cc_phases is on.

    The workspace has a Phases panel, and it can only show what the session writes down. Claude
    Code has moved this feature twice: `TodoWrite` became `TaskCreate`/`TaskUpdate`, and as of
    2.1.235 the CLI gives the Task tools to Haiku ONLY — an Opus or Sonnet session is handed no
    phase tool whatsoever. So the note covers both cases: use the tools when they exist, and
    otherwise write a file the Studio reads. The path is baked in per project, so the instruction
    is runnable exactly as written and never depends on the agent guessing a location."""
    f = mission.studio_phases_file(project_id)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return (
        "BUILD PHASES: this workspace shows a Phases panel beside the chat. It is empty unless you "
        "fill it, and the user reads it to follow a long job.\n"
        "When a task needs more than about three steps, write the phase list BEFORE you start the "
        "first step, then keep it current as you work.\n"
        "If you have the TaskCreate and TaskUpdate tools, use those and ignore the rest of this note.\n"
        "If you do NOT have them, write this file instead:\n"
        f"  {f}\n"
        '  {"phases": [{"subject": "short phase title", "status": "pending", '
        '"description": "one line"}]}\n'
        "Rewrite the whole file when a phase starts (status \"in_progress\") and when a phase "
        "finishes (status \"completed\"). Use only these statuses: pending, in_progress, completed. "
        "Keep at most one phase in_progress.\n"
        "Do not describe this file to the user. The panel is what they read."
    )


def _live_note(cwd: str = "") -> str:
    """The one thing a contact sheet can never say: why.

    A sheet reports that the mountain is black. It cannot report that `material.diffuse` is
    `#000000`, or that the GLB 404'd, or that the scene runs at 22fps — so an agent handed only
    pixels edits a file, rebuilds, re-screenshots and guesses again. This note exists because the
    alternative is undiscoverable: nothing in the project mentions the endpoint, and a model that
    does not know it can question the running game will not invent the idea.

    Measured at ~440 tokens against the review note's ~680. Every line here is one a model cannot
    derive: that `eval` returns the VALUE, that it also WRITES, and that `open` comes first."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}/api/live"
    # Forward slashes: the path goes inside JSON, where a Windows backslash is an escape.
    proj = (cwd or "<the workspace folder>").replace("\\", "/")
    return (
        "LIVE GAME LINK — ask the running game a question, and change it while it runs. A contact "
        "sheet shows what the game LOOKS like; this says WHY. Open once; after that every path "
        "below is on the same host and \"project\" is always this same folder.\n"
        f"  curl -s -X POST {base}/open -H 'content-type: application/json' "
        f"-d '{{\"project\":\"{proj}\"}}'\n"
        "It answers with `engine`, `engine_version` and `found_at` — the path the engine's app "
        "object sits at. USE `found_at`. A bundled game has no `window.pc` and no `window.THREE`, "
        "so this hunts the object down and lands on paths like `__game.renderer.app`; if "
        "`reachable` is false the answer names the one line that would fix it.\n"
        f"  curl -s -X POST {base}/eval -H 'content-type: application/json' "
        "-d '{\"project\":\"…\",\"js\":\"<found_at>.scene.ambientLight\"}'\n"
        "`eval` returns the VALUE, flattened. It also WRITES: set it, look, and only then put it "
        "in the file. Seconds, against minutes of edit-rebuild-screenshot.\n"
        "  /api/live/scene?project=…            the tree, with materials, lights and cameras\n"
        "  /api/live/find?project=…&q=player    where a thing is, by name\n"
        "  /api/live/console?project=…          errors, warnings, failed asset loads\n"
        "  /api/live/perf?project=…             frame cost, draw calls, triangles, GPU objects\n"
        "  POST /api/live/input  {\"project\":\"…\",\"events\":[{\"type\":\"tap\",\"x\":200,"
        "\"y\":600},{\"type\":\"key\",\"key\":\" \",\"code\":\"Space\",\"keyCode\":32}]} — keys, "
        "mouse, wheel, taps, swipes; \"device\":\"phone\" on /open tests a touch layout.\n"
        "PlayCanvas, three.js, Babylon, Phaser, PixiJS, Cocos and plain canvas 2D, with no change "
        "to the project. It uses your dev server, so your edits show in the tab. Same headless "
        "Chrome as the review — never start your own."
    )


def _forge_note() -> str:
    """The forge, as a note of its own with its own switch (cc_forge).

    It used to ride inside the live note. Two reasons it stands alone now: a person may want the
    forge with the live link off (it is how an asset gets MADE; the live link is how a running game
    gets questioned), and "off" has to mean off — the switch removes this whole note, so an agent is
    never told the forge exists, and the endpoint refuses as well."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}"
    return (
        "FORGE — build an asset in code, then look at it, move it and measure it in a lit"
        " studio. Blender's loop, for assets that are written instead of modelled.\n"
        f"  curl -s -X POST {base}/api/live/forge -H 'content-type: application/json'"
        " -d '{\"project\":\"<absolute project path>\",\"js\":\"…add(thing)…\",\"views\":[\"3q\"]}'"
        " — `import()` works inside js, so preview the game's real buildX(), not a copy."
        " three.js and PlayCanvas.\n"
        "  1. AIM. POST /api/live/aim, no js once it is built: 36 angles swept, every silhouette"
        " scored against your reference, the best angle and where your proportions differ returned"
        " AS NUMBERS, no image. Set the target once with \"ref\":\"<image path>\"; it is"
        " remembered; \"ref\":\"none\" leaves it out of ONE call."
        " A SHEET of figures needs \"ref_crop\":\"x0,y0,x1,y1\" (0..1, or pixels)"
        " so one is scored; clothes the colour of the backdrop take \"ref_mask\":\"<png>\"."
        # A silhouette is the same from the front and the back, and a sweep picked the wrong
        # side of the goblin twice. Three named points on the reference fix the camera exactly.
        " A silhouette cannot tell front from back: \"anchors\":[{\"at\":{\"part\":\"head\","
        "\"where\":\"top\"},\"px\":[200,40]},…] (3+, reference pixels) SOLVES the camera, and"
        " `snap` gives each part the move that puts it on its pixel.\n"
        "  2. MEASURE. \"numbers\":true on /forge — findings, every part's size in pixels, what"
        " touches the ground, how many surfaces the body is, the nodes you can move, and no sheet."
        " Do it after every edit. With a \"ref\" you also"
        " get `score.overlap`, `placement.parts` (0..1, y down), SINCE YOUR LAST SHOT — and, for a"
        " character, per part: COLOUR, SIZE, TONE (never gets dark = separate strands merge into"
        " one mass), BUSY / PLAIN (a seam the reference has not; a pocket it has), OFFSET (how far"
        " from where the reference puts it, in its own pixels), FEATURES (the eyes and mouth"
        " printed on a face), MESH (no normals, no-area faces, a vertex tint painted on before any"
        " lamp), UPSIDE DOWN / MIRRORED / COVERED.\n"
        "  3. THE STUDIO IS YOURS. \"ortho\":true for a turnaround reference — a perspective"
        " render scores against a different projection."
        " \"light\":\"studio\"|\"flat\"|\"reference\"|\"hard\", plus \"env\","
        " \"ambient\" and \"exposure\" as numbers and \"lights\":{\"key\":1,\"fill\":0.4,"
        "\"rim\":1.2}. Near-black cloth needs \"light\":\"reference\": the default rig renders"
        " pure black as #2c2e30. \"sweep\":3 on /look moves the key light three times, which is"
        " the only way a gloss shows. What you leave out stays as you set it, backdrop and size"
        " included.\n"
        "  4. LOOK when the numbers stop moving. \"views\" takes a preset (3q front back side"
        " left top bottom low hero back3q) or any angle of your own — \"az=35,el=12\", with"
        " \"zoom=2\". \"focus\":[\"claw\"] frames ONE part. \"detail\":[\"face\",\"hair\"]"
        " shoots each part from four sides AND alone, beside the same crop of your reference; a"
        " character with a reference gets it unasked — read `detail_sheet`. Also \"passes\","
        " \"turntable\":8, \"variants\":[{…}] (your js gets `params`).\n"
        "  4b. WALK ROUND IT: POST /api/live/look, the same words, no js and no rebuild."
        " \"orbit\":[40,15] turns from where you are.\n"
        "  5. MOVE IT, no rebuild: \"edits\":[{\"target\":\"wing\",\"rotate\":[0,-20,0]},"
        "{\"target\":\"head\",\"move\":[0,0.1,0]},{\"target\":\"tail\",\"scale\":1.2},"
        "{\"target\":\"backSpike\",\"visible\":false}] — grabbed by name, exact first,"
        " children come with it; \"edits\":\"reset\" puts every one of them back. Write the"
        " transforms you keep into your builder; nothing is saved for you.\n"
        "  6. SOMEBODY ELSE'S ASSET: POST /api/live/glb {\"path\":\"x.glb\"} puts a GLB on the"
        " bench, so Blender's output is judged by the same camera and the same numbers;"
        " /api/live/compare {\"a\":\"ours.glb\",\"b\":\"theirs.glb\",\"ref\":\"<image>\"}"
        " shoots both with ONE camera and scores both; /api/live/export {\"path\":\"out.glb\"}"
        " writes what the bench holds.\n"
        # Terrain is the one thing on this bench a person cannot do faster by hand, and the
        # one thing Unity, Blender and Godot cannot be asked for from a command line at all.
        # Two lines, because the endpoint prints its own shapes: a wrong action answers with
        # the table, so the note never has to carry twelve action bodies.
        "  7. GROUND: POST /api/live/terrain — make, brush a LIST of strokes per call,"
        " layers, scatter, look, glb, code. plan searches strokes against a goal (walkable, relief, coverage); a \"flow\" view shows drainage. `report` is the default: numbers, no picture. A"
        " wrong action prints the shapes.\n"
        "  READ `findings` FIRST. NOT CHECKED names the parts too small to have been judged."
        " BUILD counts your surfaces and the ones that interpenetrate — nothing is forbidden, but"
        " one soft skin is usually one implicit surface or a weld.\n"
        "  \"clear\":false ADDS to the bench instead of replacing it, so an asset can be built a"
        " part at a time and looked at as it grows — and the Engine window follows the bench live.\n"
        "  Label every run, and \"tags\" its subject (character, creature, prop, building, …),"
        " so the Engine window can shelve it. One shared headless Chrome"
        " — never start your own.\n"
        "  `POST %s/api/live/batch {calls:[…]}` runs several of these in ONE request.\n"
        "  `curl %s/api/live/api` says what the library exports; `?name=Ops` is the exact"
        " contract of makeOps(THREE), `?name=bodies` every field of every call here with its"
        " default. A throw returns a real stack and console, errors first."
        % (base, base)
    )


def _forge_on(cwd: str = "") -> bool:
    """Wanted, a browser to render in, and a project with something to build for.

    Independent of the live link on purpose (see _forge_note). The browser check is the same one
    the review and the live link make, so all three agree on what this machine can do."""
    if not settings.get("cc_forge", True):
        return False
    try:
        from . import live
        if not live.available()[0]:
            return False
    except Exception:
        return False
    return _has_renderable(cwd)


def _ops_note() -> str:
    """The modelling toolbox, as its own note with its own switch (cc_ops).

    It used to ride inside the live note, which made it vanish whenever the live link was off —
    but the library needs no browser and no running game: an agent writing asset code imports it
    straight into that code. So it stands alone, and it stays short on purpose: the catalogue of
    which tool to use when is one curl away, paid for only by a turn that wants it."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}"
    return (
        f"MESH OPS — `import('{base}/forge-ops.js')` → makeOps(THREE) or makeOpsPc(pc, device):"
        " `skin(object)` (merge+weld+smooth: a pile of primitives becomes ONE surface), subsurf"
        " (Catmull-Clark, crease angle), bevel, unwrap, bakeAO/bakeNormalMap, heatWeights, remesh,"
        " relax, boolean, hull, blobs, weld, mirror, solidify. `check(object)` returns the defects"
        " as NUMBERS."
        # The goblin A/B against Blender (data/ab/goblin/): what Blender had and the forge did not
        # was this chain. An agent that is not told the chain exists writes its own, badly.
        # OPTIONAL, said in so many words: the user's rule is that an agent picks what it thinks
        # best, and plain code is a fine answer. The tools are there for when they save work.
        " All optional: plain three.js code is always fine. Blender's chain, if you want it:"
        " densify → sculpt(strokes) → decimate → unwrap →"
        " bakeMaterial(low, \"rustySteel\", 1024, {high}) → applyMaps; bakeAtlas gives many parts"
        " ONE texture set. hand + grip: a whole fist ON its handle. sweep, rim, reach."
        f" Which to use when: `curl {base}/forge-ops.md`."
        f"\n  No browser needed: `{base}/api/tools/sidecar` reads a saved document,"
        f" `/sidecar/apply` puts it on a fresh build in node and says what bound."
    )


def _base_url() -> str:
    """This Studio's origin. Four notes were each rebuilding it; now there is one of it."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return "http://%s:%s" % (host, settings.get("port") or 8777)


def _animate_note(cwd: str = "") -> str:
    """The time axis. Short, because the whole idea is one paragraph."""
    base = _base_url()
    return (
        "ANIMATION REVIEW — a gait bug is invisible in a still.\n"
        "`POST %s/api/live/animate {project, js, view:\"side\", frames:8, ground:0,"
        " track:[\"footL\",\"footR\"]}`. It runs the cycle, photographs every frame on ONE fixed"
        " camera framed over the whole sequence, and reports the feet as NUMBERS first: which one"
        " floats and by how much, which one slides while planted and between which two times,"
        " which frames have nothing touching the ground.\n"
        "  Why it is not just a screenshot loop: `view` re-frames on the current bounds and slides"
        " the ground plane under whatever it framed, so frame-by-frame the subject re-centres and"
        " the floor follows the feet — the fault cancels itself out. Here both are pinned.\n"
        "  DRIVING IT: `drive:\"auto\"` finds an AnimationMixer clip or root.update/tick/animate."
        " `drive:\"fn\"` with `fn:\"root.pose(t)\"` runs your own expression with `t` in scope."
        " Say `mode:\"delta\"` if your update takes the time since the LAST frame rather than"
        " since the start — passing one to the other does not throw, it just animates wrongly.\n"
        "  `numbers:true` gives the report with no sheet at all." % base
    )


def _animate_on(cwd: str = "") -> bool:
    return bool(settings.get("cc_animate", False)) and _has_renderable(cwd)


def _navigate_note(cwd: str = "") -> str:
    """Go to the place, then look. One paragraph, because the endpoints answer the rest."""
    base = _base_url()
    return (
        "NAVIGATE — go to the place in the RUNNING game, then look at it larger than any screenshot.\n"
        "`POST %s/api/live/goto {project, name:\"treadmill\"}` — or `at:[x,y,z]`, `look_at:[x,y,z]`,"
        " `yaw`, `pitch`, `distance`, `step:[dx,dy,dz]`, `orbit:[dYaw,dPitch]`, `pose:\"last\"|\"game\"`."
        " It puts a camera the STUDIO owns where you ask, so the game's follow-camera cannot fight it,"
        " and answers with `shot` (a picture), `pose`, and `in_view`: the named things on screen with"
        " their distance and screen position. A target the game has switched off — a platform the"
        " player has not reached — is switched on while you look (`revealed_now` says so) and put"
        " back by `release:true`, which also hands the view back to the game.\n"
        "`GET %s/api/live/where?project=…` — the same answer without moving.\n"
        "`POST %s/api/live/locate {project, image:\"<path the user pasted>\", hint:\"platform 8 treadmills\"}`"
        " — candidate views are rendered and scored against that screenshot; the best is taken and"
        " the top three come back with scores and pictures. START THE GAME FIRST (`/api/live/input`,"
        " a tap or Space): a title card scores as itself. A score under 0.5 is a guess: say so.\n"
        "  After you change code and the dev server reloads, `goto {pose:\"last\"}` returns to the"
        " same view and reports `changed_pct` against the previous picture — a correction is proved"
        " that way, not by eye. It reaches the app in a bundled game by itself; if it still says"
        " unreachable, the one dev line `window.__game = { app }` where the game is made fixes it."
        % (base, base, base)
    )


def _navigate_on(cwd: str = "") -> bool:
    return bool(settings.get("cc_navigate", False))


def _scene_note(cwd: str = "") -> str:
    """The game's own objects as an agent's tools.

    Written after building a new game with only what an agent is told: `find` gave a name and a
    LOCAL position, and moving `pillar-2` took an `eval` the agent wrote itself — gone at the next
    reload, with no word about which line of code had made the pillar. The last new game built
    before this had sixty ad-hoc screenshots in its root. Everything below answers one of those."""
    base = _base_url()
    proj = (cwd or "<the workspace folder>").replace("\\", "/")
    return (
        "SCENE EDIT — the running game's objects as YOUR tools: see them, move them, place the game's"
        " own assets, keep a change. No eval of your own. three.js and PlayCanvas; open the game with"
        " /api/live/open first.\n"
        f"  curl -s --get {base}/api/live/objects --data-urlencode 'project={proj}' -d q=pillar"
        " -d in_view=1 -d code=1 — rows of KEY (what saved edits use; a shared name reads name.000),"
        " world min/max, in_view, screen 0..1, src (its model file), code (the line that named it)."
        " pick=0.5,0.5 names what is under that point; near=<key>&radius=2 what is around it.\n"
        f"  POST {base}/api/live/edit {{project, target:\"<key>\", move:[x,y,z] (world m) | pos |"
        " rotate:[0,45,0] | rot (DEGREES) | scale | visible | drop:true} — applied at once; the answer"
        " has before/after, ground {support, gap: + floats, - sinks} and overlaps [{key, share}]."
        " Look, then repeat with save:true to keep it in <project>/studio.edits.json. {undo:true} takes"
        " the last call back, live and on disk; {edits:[…]} does several as one; {clear:[keys]|\"all\"}"
        f" takes saved entries out. GET {base}/api/live/edits?project=… is what is saved.\n"
        f"  POST {base}/api/live/place {{project, asset, near:\"<key>\" | at:[x,y,z], rot, scale, drop,"
        " save} — asset: {\"builder\":\"src/assets.js#buildX\",\"args\":[{…}]} | {\"model\":\"<.glb>\"}"
        " | {\"clone\":\"<key>\"} | {\"primitive\":\"box\",\"color\":\"#ff8800\"} | {\"id\":\"<asset"
        " id>\"}. Without save it is a try the next reload removes; {remove:\"<id>\"} takes one out."
        f" {base}/api/live/batch runs objects/edit/place/shot/watch in one call.\n"
        "  SEE IT. Every edit and place answers with `look`: a before|after sheet (amber before, green"
        " after) from ONE Studio camera framed on what changed — read look.sheet, not only the"
        " numbers. look:\"front\"|\"side\"|\"back\"|\"top\"|\"player\"|false (default \"3q\"; a batch"
        " takes one only when asked). look.drift = the game's own code put it straight back: change"
        f" that code. POST {base}/api/live/shot {{project, target, angle}} frames one thing now; POST"
        f" {base}/api/live/watch {{project, seconds, frames, input:[events], at_ms, target}} is real time:"
        " frames plus findings (STILL, NO RESPONSE, moved in box …). `stale` on any answer = the page"
        " runs older code than your files: POST /api/live/open {reload:true}. The person can watch your"
        " tab live in the Engine window (Game tab → Agent's view).\n"
        "  A saved edit applies in the Studio's tab. For the SHIPPED game, change the line `code`"
        f" names, or copy {base}/studio-runtime.js beside it and run `await applyStudioEdits(sceneOrApp,"
        " await loadStudioEdits())` after the world is built.\n"
        f"  {base}/api/engine/assets?project=… lists the builders, table entries and model files;"
        f" {base}/api/engine/asset-sheet?project=…&q=…&limit=12 draws them as ONE numbered picture,"
        " a failed tile saying why."
    )


def _scene_on(cwd: str = "") -> bool:
    """Wanted, and the live link it rides on is on and has a game to open."""
    return bool(settings.get("cc_scene_edit", True)) and _live_on(cwd)


def _new_game_note(cwd: str = "") -> str:
    """A game started from an empty folder, wired for the Studio before its first line."""
    base = _base_url()
    parent = str(Path(cwd).parent).replace("\\", "/") if cwd else "<a folder>"
    return (
        "NEW GAME — this folder has no game yet. Start one that every Studio tool works on from the"
        f" first minute: POST {base}/api/workspace/new-game {{\"name\":\"…\",\"engine\":\"three\"|"
        f"\"playcanvas\",\"parent\":\"{parent}\"}} → {{path, files, dev, next}}. npm install runs in"
        f" the background: poll GET {base}/api/workspace/new-game/status?path=<path> until done. It has"
        " window.__game, saved edits applied before the first frame (src/studio-runtime.js), a review"
        " action and targets, and serve.mjs; /api/live/open starts it. Build in src/assets.js: export"
        " buildThing() returning a named group whose lowest point is y = 0; give every object a unique"
        " name. A code edit reloads the open page by itself, so save:true the tries you keep first."
        " Read its README.md."
    )


def _new_game_on(cwd: str = "") -> bool:
    """Only where a game is likely to be started: a folder with no page and little else in it.

    A research repo or an automation project has no page either, and has no use for the note; a
    new project folder has a handful of files at most."""
    if not settings.get("cc_new_game", True) or not cwd:
        return False
    try:
        entries = [e for e in Path(cwd).iterdir() if not e.name.startswith(".")]
    except OSError:
        return False
    return len(entries) <= 12 and not _has_renderable(cwd)


def _code_tools_note() -> str:
    """The code half: know a file is unchanged, check it, change it narrowly, run the suites."""
    base = _base_url()
    return (
        "CODE TOOLS — check before you look, and before you trust.\n"
        "  `curl '%s/api/tools/sha?path=<f>'` a file's sha256, size and line count WITHOUT its"
        " contents: the cheap way to confirm what you read is still there.\n"
        "  `curl '%s/api/tools/validate?path=<f>'` does it still hold together — Python by parse,"
        " TypeScript by the project's own `tsc --noEmit -p tsconfig.json`.\n"
        "  `POST %s/api/tools/edit {path, old, new, expect_sha}` replaces ONE exact unique span and"
        " says at once whether it still parses. It refuses an anchor that appears twice or none,"
        " refuses when the sha has moved under you, and rolls back a change that broke the parse.\n"
        "  `POST %s/api/tools/tests {\"which\":\"all\"|\"frontend\"|\"backend\"|\"test:verts\"}`"
        " runs the suites as a JOB and hands back an id; poll `/api/tools/job/<id>`. You get pass"
        " and fail counts and the failing lines, never the whole log. `GET /api/tools/suites`"
        " lists them." % (base, base, base, base)
    )


def _code_tools_on(cwd: str = "") -> bool:
    return bool(settings.get("cc_code_tools", False))


def _debugger_note(cwd: str = "") -> str:
    """The one thing the Blender and Unity integrations structurally cannot offer."""
    base = _base_url()
    return (
        "DEBUGGER — stop where it went wrong and read the variables.\n"
        "`POST %s/api/live/debug {project, js, pause_on, lines, steps, depth}`. `pause_on`:"
        " \"uncaught\" (default), \"all\", or \"none\" for breakpoints only. `lines` are 1-based"
        " line numbers in YOUR OWN code. `steps` steps over that many statements after the first"
        " stop, reporting each. You get the stack with real line numbers and, for the frame that"
        " stopped, every LOCAL variable and its value — which is the answer to \"why is this vertex"
        " NaN\" without adding a single log line and running again. The whole stop-and-inspect"
        " happens inside the one request and the page is always resumed." % base
    )


def _debugger_on(cwd: str = "") -> bool:
    return bool(settings.get("cc_debugger", False)) and _has_renderable(cwd)


def _vertedit_note(cwd: str = "") -> str:
    """The one thing Blender cannot do: a hand-moved vertex that survives the code re-running.

    Short on purpose. The whole idea is one paragraph, and the detail is in forge-ops.md where it
    costs nothing until an agent asks for it.
    """
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}"
    return (
        "EDIT MODE — move the VERTICES, and keep the move when the code runs again.\n"
        "The Edit tab has a third mode beside Object and Pose: press Tab. Pick vertices, edges or"
        " faces (1/2/3), drag with the gizmo. Moves are saved in the sidecar under TWO keys, never"
        " an index: where the code put the vertex, and which corner it was plus a corners:faces"
        " signature. A change elsewhere matches the place and restores it exactly; a change that"
        " re-displaces that very vertex matches the signature instead and carries your offset onto"
        " the code's new position; a change that alters the mesh's shape orphans the edit and says"
        " so. Blender cannot do any of this — its mesh is stored, so it has nothing to rebuild"
        " against.\n"
        "  In code: `import('%s/forge-ops.js')` gives `applyVerts(root, edits.verts, report)`, plus"
        " `vertKey`, `groupVerts`, `topoSig` and `restBuffer` to write moves yourself. Run it"
        " BEFORE the modifier stack — hand-moved vertices are the base mesh, and a subdivide over"
        " them is Blender's order too.\n"
        "  A corner is not a vertex. Generated geometry duplicates: a box is 24 vertices for 8"
        " corners, and moving one of three tears the surface. Everything within %s of a place"
        " moves together, which is what `groupVerts` does for you. Detail: `curl %s/forge-ops.md`."
        % (base, "1e-3", base)
    )


def _vertedit_on(cwd: str = "") -> bool:
    """Wanted, and this project has something with a mesh in it."""
    return bool(settings.get("cc_vertedit", False)) and _has_renderable(cwd)


def _ops_on(cwd: str = "") -> bool:
    """Wanted, and this project has something to model. No browser needed, so no machine check."""
    return bool(settings.get("cc_ops", True)) and _has_renderable(cwd)


def agent_notes_catalog(cwd: str = "") -> dict:
    """Every note a session can be told, with its switch, its default, and what it costs per turn.

    Measured live rather than remembered, so the Settings page shows the number a turn actually
    pays today. `on` is the switch alone; `needs` says what else has to be true for the note to be
    sent at all (a browser, a page to render, a generator installed). `default` is read from the
    real defaults, so a fresh PC and this page can never disagree. `group` separates what the Studio
    always sent from the forge things, which arrived later and have switches of their own."""
    from .config import DEFAULT_SETTINGS as _D

    def size(fn) -> int:
        try:
            return len(fn() or "") // 4
        except Exception:
            return 0

    def dflt(key: str, fb: bool) -> bool:
        return bool(_D.get(key, fb))

    rows = [
        {"key": "studio_tools_prompt", "label": "Studio generators", "group": "studio", "default": dflt("studio_tools_prompt", False),
         "tokens": size(_studio_note), "why": "The 2D and 3D generators behind the tabs, callable by curl.", "needs": "a generator tab switched on"},
        {"key": "cc_web_tools", "label": "Blocked web pages and search", "group": "studio", "default": dflt("cc_web_tools", True),
         "tokens": size(_web_note), "why": "A stealth fetch for pages that refuse robots, and a keyless search."},
        {"key": "cc_graphify", "label": "Code graph", "group": "studio", "default": dflt("cc_graphify", True),
         "tokens": size(_graphify_note), "why": "Where a symbol is and what calls it, from the live graph instead of grep."},
        {"key": "cc_phases", "label": "Build phases panel", "group": "studio", "default": dflt("cc_phases", True),
         "tokens": size(lambda: _phases_note("x")), "why": "The phase list beside the chat, kept current by the agent."},
        {"key": "cc_review", "label": "Visual review", "group": "studio", "default": dflt("cc_review", False),
         "tokens": size(lambda: _review_note(cwd)), "why": "Contact sheets of the running game at the size the player sees.", "needs": "a page a browser can open"},
        {"key": "cc_live", "label": "Live game link", "group": "studio", "default": dflt("cc_live", False),
         "tokens": size(lambda: _live_note(cwd)), "why": "Question and change the running game: the scene tree, console, perf, input.", "needs": "a page a browser can open"},
        {"key": "cc_autolearn", "label": "Auto-learn skills", "group": "studio", "default": dflt("cc_autolearn", False),
         "tokens": size(_autolearn_note), "why": "Bank reusable build patterns as skills while the agent works."},
        {"key": "cc_blender_kiln", "label": "Blender kiln", "group": "studio", "default": dflt("cc_blender_kiln", False),
         "tokens": size(_blender_kiln_note), "why": "The Blender MCP pipeline: source, generate, clean, texture, export."},
        {"key": "cc_1m", "label": "1M context", "group": "studio", "default": dflt("cc_1m", True),
         "tokens": size(lambda: _context_window_note("claude-fable-5-1[1m]")), "why": "Allows 1M context. Off limits new Claude sessions to 200k, including native 1M models."},
        {"key": "cc_fable_efficient", "label": "Fable token efficiency", "group": "studio", "default": dflt("cc_fable_efficient", True),
         "tokens": size(_fable_efficiency_note), "why": "Act on what is known, do not re-derive, lead with the outcome. Fable models only."},
        {"key": "cc_force_plan", "label": "Force plan mode", "group": "studio", "default": dflt("cc_force_plan", False),
         "tokens": size(_plan_note), "why": "Starts every conversation in plan mode. The plan note is sent while a session is in plan mode."},
        {"key": "cc_memory", "label": "Memory", "group": "studio", "default": dflt("cc_memory", True),
         "tokens": size(_memory_off_note), "why": "Off adds a short note that memory is not available; on costs nothing here."},
        {"key": "cc_forge", "label": "Forge", "group": "forge", "default": dflt("cc_forge", True),
         "tokens": size(_forge_note), "why": "Render asset code in a lit studio with numbers and a reference. Its own note: off, and the agent is never told it exists; the endpoint refuses too.",
         "needs": "a browser, and a page to build for"},
        {"key": "cc_animate", "label": "Animation review", "group": "forge", "default": dflt("cc_animate", False),
         "tokens": size(lambda: _animate_note(cwd)), "why": "Run a cycle and photograph every frame on one fixed camera, with the feet measured against a ground that does not move. Says which foot floats or slides while planted, and when. Nothing else has this: Blender, Unity and Godot all hand back a screenshot and leave the judging to your eyes.",
         "needs": "a page a browser can open"},
        {"key": "cc_scene_edit", "label": "Scene edit for agents", "group": "forge", "default": dflt("cc_scene_edit", True),
         "tokens": size(lambda: _scene_note(cwd)), "why": "The running game's objects as an agent's own tools: each object's key, world size and the code line that named it; move, turn, scale, hide and place the game's own assets, undo, and save a change to studio.edits.json. With the answer: does it float, sink or overlap. Without it an agent writes its own eval, and the move is gone at the next reload.",
         "needs": "the live link, and a game a browser can open"},
        {"key": "cc_new_game", "label": "New game template", "group": "forge", "default": dflt("cc_new_game", True),
         "tokens": size(lambda: _new_game_note(cwd or "C:/x/new")), "why": "In a folder with no game yet, tells the agent it can start one that is Studio-ready from the first minute, instead of wiring its own screenshots and test pages.",
         "needs": "a folder with no game in it yet"},
        {"key": "cc_navigate", "label": "Navigate the running game", "group": "forge", "default": dflt("cc_navigate", False),
         "tokens": size(lambda: _navigate_note(cwd)), "why": "Go to a place in the running game and look at it: a camera the Studio owns that the game's follow-camera cannot fight, the named things in view with distances, and locate — a screenshot the user pasted turned into a camera pose by rendering candidate views and scoring them. Blender, Unity and Godot have a free camera in their editor; none can be told 'go where this picture was taken'.",
         "needs": "a game a browser can open"},
        {"key": "cc_debugger", "label": "Debugger", "group": "forge", "default": dflt("cc_debugger", False),
         "tokens": size(lambda: _debugger_note(cwd)), "why": "Stop asset code where it threw and read every local variable, with the real line numbers. Godot's MCP is the only other one with a debugger and it needs the Godot editor open; Blender's and Unity's cannot have one at all.",
         "needs": "a page a browser can open"},
        {"key": "cc_code_tools", "label": "Code tools and tests", "group": "studio", "default": dflt("cc_code_tools", False),
         "tokens": size(_code_tools_note), "why": "A file's sha without reading it, a typecheck, a narrow edit that refuses when the file moved under you, and the test suites as a job with pass and fail counts."},
        {"key": "cc_vertedit", "label": "Edit mode: the vertices", "group": "forge", "default": dflt("cc_vertedit", False),
         "tokens": size(lambda: _vertedit_note(cwd)), "why": "Move vertices, edges and faces by hand, and keep the move when the code runs again. Blender cannot do this: its mesh is stored, so it has nothing to rebuild against.",
         "needs": "a project with a mesh in it"},
        {"key": "cc_ops", "label": "Mesh ops and modelling tools", "group": "forge", "default": dflt("cc_ops", True),
         "tokens": size(_ops_note), "why": "The library: skin, subsurf, bevel, unwrap, bakes, heat weights, remesh, booleans, check(); Blender's chain in code (sculpt, decimate, smart materials in one texture set), whole hands on a grip, sweeps and rims."},
    ]
    total = 0
    for r in rows:
        r["on"] = bool(settings.get(r["key"], r["default"]))
        # Memory is the one inverted row: the note is sent when the switch is OFF.
        sent = (not r["on"]) if r["key"] == "cc_memory" else r["on"]
        if sent:
            total += int(r["tokens"])
    always = [{"key": "browser_rule", "label": "Browser rule", "tokens": size(_browser_rule_note),
               "why": "Never kill every browser on the machine. Always sent; it is a safety rule."}]
    total += always[0]["tokens"]
    style = size(_output_style_note)
    return {"notes": rows, "always": always, "style_tokens": style, "total": total + style}


def _live_on(cwd: str = "") -> bool:
    """On, possible, and worth sending — the same three questions the review harness asks."""
    if not settings.get("cc_live", True):
        return False
    try:
        from . import live
        if not live.available()[0]:
            return False
    except Exception:
        return False
    return _has_renderable(cwd)


def _review_note(cwd: str = "") -> str:
    """System-prompt note injected when cc_review is on AND a browser is actually available.

    An agent asked to judge a spell reaches for a headless browser and takes one screenshot. That
    frame is wrong nearly every time — the effect peaks for ~200ms, the page load shows the menu,
    the asset is 40px inside a 720p shot — and two runs never match, so nothing can be compared.
    The note exists because the agent cannot discover the alternative on its own, and because
    every agent that does not know about it silently spawns its own Chrome.

    Gated on the browser being present for the same reason the generator note is gated on a
    generator being switched on: describing a tool that cannot run wastes tokens and invites the
    agent to try anyway."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}/api/review"
    # Forward slashes: the path is going inside JSON, where a Windows backslash is an escape.
    proj = (cwd or "<the workspace folder>").replace("\\", "/")
    return (
        "VISUAL REVIEW: judge art, effects, characters and UI from a contact sheet — never from a "
        "single screenshot.\n"
        "One screenshot of a running game is the wrong frame nearly every time: an effect peaks "
        "for about 200ms, a page load shows the menu, a 40px asset is invisible in a 720p shot, "
        "and two runs never match so nothing can be compared. The Studio drives the page clock by "
        "hand instead, so you get exactly the frames you ask for, identical every run, plus the "
        "same row again at the size the player sees it, plus numbers.\n"
        f"  curl -s -X POST {base}/render -H 'content-type: application/json' \\\n"
        f"    -d '{{\"project\":\"{proj}\",\"mode\":\"scene\",\"label\":\"boss fight\","
        "\"warmup_ms\":2500,\"times\":[0,200,500,900,1400]}'\n"
        "Read `findings` and `metrics` FIRST — they catch a dead or static effect for no image "
        "tokens at all. Then Read the `sheet` path as an image. `peak` is the busiest frame at "
        "full resolution; open it only when the sheet raises a question about craft.\n"
        "Two coverage numbers, and picking the wrong one is the usual mistake. `fill` is how much "
        "of the frame is DRAWN, measured against the page's own backdrop \u2014 read it to judge a "
        "scene, a character or a layout. `ink` is how much CHANGED against a plate \u2014 read it "
        "to judge an effect. On a still camera `ink` is near zero however good the picture "
        "is, because the plate absorbs the whole world; a `STILL SCENE` finding says exactly "
        "that, and it is not a bug in the game.\n"
        "mode \"scene\" loads the real game and needs no setup — use it for \"does this read in "
        "play\". To get past a title screen add "
        "\"input\":[{\"at\":-400,\"type\":\"key\",\"key\":\" \",\"code\":\"Space\","
        "\"keyCode\":32}] — a negative \"at\" fires during warm-up.\n"
        f"mode \"isolate\" judges ONE asset on a clean backdrop. It reads {proj}/review/targets.js:\n"
        "  export const targets = [{ name: 'frostbolt', mount(root, ctx) { /* draw into root at "
        "ctx.width x ctx.height, honour ctx.pixelRatio */ } }]\n"
        "Write that file once; every later review reuses it. GET /api/review/targets lists them.\n"
        "An effect that only plays when something triggers it needs firing FIRST, or you review an "
        "empty scene: \"js\":\"pc.app.fire('vfx:cast')\" runs any expression and needs nothing from "
        "the game, or \"action\":\"fireball\" calls a name the game registered on "
        "window.__review.actions (GET /api/review/actions lists them). Add \"auto_times\":true to "
        "find the peak instead of guessing it, \"auto_plate\":true for honest coverage numbers, "
        "\"clip\":true for a video the USER can watch, and \"session\":\"<name>\" to keep the tab "
        "warm while you iterate (0.4s a run instead of 1.4s).\n"
        "\"quality\" is \"draft\" | \"normal\" | \"high\".\n"
        "Do not launch your own browser and do not take ad-hoc screenshots to judge art — one "
        "headless Chrome is already running and shared."
    )


# "Does this project have anything a browser could open?" — cached, because a session
# respawns often and this must never be felt as a pause before a turn.
_PAGE_TTL = 120.0
_page_cache: dict[str, tuple[float, bool]] = {}


def _has_renderable(cwd: str) -> bool:
    """Is there a page in here at all?

    A backend, a research folder or a notes repo can never use the review harness, and the note
    is ~700 tokens — measured. It used to be sent to every project on the machine because the
    only gate was "is Chrome installed", which says nothing about the project.

    Deliberately bounded and early-exiting: the FIRST .html wins, directories are read at most
    two deep, and the scan gives up long before a big tree could be felt at spawn time.
    """
    if not cwd:
        return True                     # nothing to judge on — do not silently withhold it
    now = time.time()
    hit = _page_cache.get(cwd)
    if hit and now - hit[0] < _PAGE_TTL:
        return hit[1]
    found = False
    try:
        root = Path(cwd)
        from . import dev_server
        from .workspace import _ENTRY_SKIP
        if dev_server.script_for(root):          # an npm dev script serves SOMETHING
            found = True
        else:
            reads = 0
            stack = [(root, 0)]
            while stack and reads < 60 and not found:
                d, depth = stack.pop()
                reads += 1
                try:
                    for e in d.iterdir():
                        if e.is_dir():
                            if depth < 2 and not e.name.startswith(".") and e.name.lower() not in _ENTRY_SKIP:
                                stack.append((e, depth + 1))
                        elif e.suffix.lower() in (".html", ".htm"):
                            found = True
                            break
                except OSError:
                    continue
    except Exception:
        found = True                    # never let a probe failure remove a working feature
    _page_cache[cwd] = (now, found)
    return found


def _review_on(cwd: str = "") -> bool:
    """Three halves now: the user wants it, this machine can render it, and this PROJECT has
    something to render. The third is what stops a backend repo paying for a games tool."""
    if not settings.get("cc_review", False):
        return False
    try:
        from . import review
        if not review.available()[0]:
            return False
    except Exception:
        return False
    return _has_renderable(cwd)


def _blender_kiln_note() -> str:
    """System-prompt note injected only when cc_blender_kiln is on.

    The skill lives at ~/.claude/skills/blender-kiln and Claude Code loads it on its own; what
    this note adds is the local truth the skill cannot know — which of its backends actually
    exist on THIS machine. Without that it assumes a HuggingFace Space and re-downloads models
    that are already here.

    Off by default on purpose. The Blender half of the skill is driven through mcp__blender__*
    tools, which do not exist unless Blender is open with the MCP addon listening on :9876 and a
    `blender` MCP server is registered with the CLI. Announcing a skill Claude cannot drive is
    worse than silence: it plans around tools that will not resolve and fails mid-task.
    """
    if not _kiln_skill():
        # The skill is a separate download, not part of the Studio. Announcing a pipeline whose
        # instructions are not on the machine sends the agent after a skill that will never
        # resolve, so say the one useful thing instead.
        return (
            "BLENDER-KILN: the `kiln` skill is switched on but is NOT installed on this PC. "
            "Install it into ~/.claude/skills/blender-kiln, or turn the switch off in "
            "Settings -> Planning & review. Until then use the forge and gltf-transform for 3D "
            "work, and say the kiln is unavailable rather than pretending it ran."
        )
    return (
        "BLENDER-KILN is installed (skill name: `kiln`) — a 3D asset pipeline: brief, source, "
        "import, cleanup, texture, optimize, export GLB. Reach for it for 3D asset production, "
        "not for ordinary code work.\n"
        "What is on THIS machine, checked just now — prefer these over the skill's cloud "
        "defaults, and do not plan around the ones marked missing:\n"
        + "".join("  - %s\n" % line for line in _kiln_facts()) +
        "The mcp__blender__* tools only exist while Blender is open with the MCP addon on :9876. "
        "Check before you plan around them; if they are missing, say so and use the Bash/"
        "gltf-transform path instead of pretending the pipeline ran."
    )


_KILN_TTL = 120.0
_kiln_cache: tuple = (0.0, [])


def _kiln_skill() -> bool:
    """Is the kiln skill on this PC? It is a separate download, not part of the Studio."""
    try:
        return (claude_home() / "skills" / "blender-kiln" / "SKILL.md").is_file()
    except OSError:
        return False


def _blender_exe() -> str:
    """Blender, wherever this PC put it. Settings first, then PATH, then the usual folders."""
    want = ((settings.get("tools") or {}).get("blender_path") or "blender").strip()
    if want and Path(want).is_file():
        return want
    found = shutil.which(want or "blender")
    if found:
        return found
    best = ""
    for base in ("C:/Program Files/Blender Foundation", "C:/Program Files (x86)/Blender Foundation",
                 str(Path.home() / "Applications"), "/Applications", "/usr/share/blender"):
        try:
            d = Path(base)
            if not d.is_dir():
                continue
            for sub in sorted(d.iterdir(), reverse=True):     # newest version folder first
                exe = sub / ("blender.exe" if os.name == "nt" else "blender")
                if exe.is_file():
                    best = str(exe)
                    break
        except OSError:
            continue
        if best:
            break
    return best


def _hunyuan_models() -> list:
    """Hunyuan3D weights sitting in the local ComfyUI, by file name. Empty means: not downloaded."""
    try:
        from .services import _detect_comfyui
        _cmd, where = _detect_comfyui(None)
    except Exception:
        where = ""
    if not where:
        return []
    out = []
    try:
        models = Path(where) / "models"
        for sub in ("checkpoints", "diffusion_models", "unet", "diffusers"):
            d = models / sub
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if "hunyuan3d" in f.name.lower():
                    out.append(f.name)
    except OSError:
        pass
    return sorted(set(out))[:4]


def _kiln_facts() -> list:
    """One line per backend, saying what is here rather than what was here when this was written.

    THE NOTE USED TO ASSERT ALL THREE. It named a Blender version, a CLI and two checkpoint files
    that happened to be on the machine it was written on, and every agent on every other machine
    was told the same thing. A tool that is announced and then absent is worse than one that was
    never mentioned: the plan is already built around it by the time it fails.
    """
    global _kiln_cache
    now = time.time()
    if _kiln_cache[1] and now - _kiln_cache[0] < _KILN_TTL:
        return _kiln_cache[1]
    out = []

    weights = _hunyuan_models()
    if weights:
        out.append("Hunyuan3D locally through ComfyUI (%s). Do NOT fall back to a HuggingFace "
                   "Space and do NOT re-download weights." % ", ".join(weights))
    else:
        out.append("Hunyuan3D weights: NOT on this PC. Local image-to-3D needs ComfyUI with the "
                   "Hunyuan3D models installed — say so rather than downloading gigabytes.")

    gltf = ((settings.get("tools") or {}).get("gltf_transform_cmd") or "gltf-transform").strip()
    if shutil.which(gltf):
        out.append("gltf-transform CLI for decimation and web-shrinking (`%s`)." % gltf)
    else:
        out.append("gltf-transform: NOT installed (`npm i -g @gltf-transform/cli` adds it).")
    if shutil.which("gltfpack"):
        out.append("gltfpack is installed.")

    exe = _blender_exe()
    out.append(("Blender at '%s'." % exe) if exe
               else "Blender: NOT found on this PC, so the mcp__blender__* half cannot run.")

    _kiln_cache = (now, out)
    return out


def _web_note() -> str:
    """One short note, the same for a session and for a subagent.

    It began as two — a long form for the session and a short one for agents — and the long form
    was 1,618 characters of prose about a curl the model either uses or does not. A system prompt
    already carries the browser rule, the graph, the phases panel, the review harness and the
    output style; every paragraph added here is one the model reads past on every turn. The short
    form says the same thing in a quarter of the space, and having ONE means the two cannot drift.

    What the model actually needs: the symptom, the endpoint, and not to give up. Everything else
    (which tier won, how `tried` reads, the token cost of a big page) is in the response itself.
    """
    base = "http://127.0.0.1:8777/api/web"
    return (
        "BLOCKED WEB PAGE? A 403, or a page whose text is 'Just a moment…' / 'verify you are "
        "human', means the site refuses robots — not that it is down. The Studio has a stealth "
        "fetcher that gets past it, and a keyless search:\n"
        f"  curl -s -X POST {base}/fetch -H 'content-type: application/json' "
        "-d '{\"url\":\"<url>\",\"chars\":20000}'\n"
        f"  curl -s --get {base}/search --data-urlencode 'q=<query>'\n"
        "Never report a page as unreachable without trying it. Do not install scrapling or a "
        "browser yourself — one is already running."
    )


def _graphify_note() -> str:
    """System-prompt note injected when cc_graphify is on. The STUDIO owns building the graph and
    installing graphify (background, zero-token, no pipx/PATH) — so this note steers Claude to USE
    the graph (reading graph.json needs no CLI at all), NOT to install anything itself (that pipx
    self-install was the thing that silently failed on fresh PCs)."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}/api/graphify/query"
    return (
        "GRAPHIFY — USE IT FIRST, BEFORE ANY GREP OR GLOB, TO LOCATE CODE.\n"
        "This project has a live code knowledge-graph that the Studio keeps current in the background. "
        "Querying it is a single cheap curl; it answers 'where is X', 'what calls X', 'what does X use', "
        "and 'how does A reach B' with exact file:line.\n"
        f"  curl -s --get '{base}' --data-urlencode 'root=<ABSOLUTE WORKSPACE PATH>' "
        "--data-urlencode 'q=<symbol or file>'\n"
        f"  curl -s --get '{base}' --data-urlencode 'root=<PATH>' --data-urlencode 'q=<A>' "
        "--data-urlencode 'to=<B>'      # shortest path between two symbols\n"
        f"  curl -s --get '{base}' --data-urlencode 'root=<PATH>'                      "
        "# a map: size, biggest files, most-connected symbols\n"
        "A query costs a few hundred tokens and returns in milliseconds.\n"
        "DO NOT read `graphify-out/graph.json` yourself — it is MEGABYTES (hundreds of thousands of "
        "tokens) and reading it is far more expensive than the grep you were avoiding. That file is the "
        "server's data, not something to open. Query the endpoint instead.\n"
        "Use it for: finding a function/class/component, seeing a symbol's callers before changing it, "
        "and orienting in an unfamiliar area. Grep is still right for string/content searches (an error "
        "message, a TODO, a config value) — the graph indexes SYMBOLS, not arbitrary text.\n"
        "If a query returns no graph yet, the Studio is still building it (first build takes ~a minute); "
        "grep meanwhile and switch over once it answers. Do NOT pip/pipx-install graphify yourself.\n"
        "This is ENFORCED, not just advised: a PreToolUse hook answers a symbol-shaped Grep from the "
        "graph automatically, and refuses a Read of graph.json. The hook is wired per session via "
        "`--settings` (data/btw/<project>.settings.json) — `~/.claude/settings.json` will show "
        "`hooks: {}` and that is NOT evidence the hook is missing.\n"
        "PROPAGATE TO AGENTS: subagents and Workflow agents do NOT inherit this note — put the curl "
        "command above in each agent's prompt when you delegate. A fan-out of N agents each re-discovering "
        "the repo by grep is N× the waste, and it is the single biggest token sink on a large codebase."
    )


def _graphify_subagent_note(cwd: str = "") -> str:
    """Appended to EVERY Task-tool subagent's system prompt.

    A subagent does not inherit the main session's system prompt, so it began every task knowing
    nothing about the graph and re-discovered the repo by grep — and a fan-out of N agents is N
    times that waste. Asking the main agent to pass the instruction along only worked when it
    remembered, which is exactly what kept failing. The CLI can append a system prompt to every
    subagent (--append-subagent-system-prompt), so this is enforced rather than requested.

    Deliberately short: it rides on every subagent. The workspace path is baked in so the command
    is runnable as written instead of a template the agent has to fill in."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}/api/graphify/query"
    root = cwd or "<the absolute path of the project you are working in>"
    return (
        "LOCATING CODE: this project has a code knowledge-graph. Query it BEFORE grepping or "
        "globbing for a symbol — it returns exact file:line for a few hundred tokens.\n"
        f"  curl -s --get '{base}' --data-urlencode 'root={root}' --data-urlencode 'q=<symbol>'\n"
        "  add --data-urlencode 'to=<other symbol>' for how one reaches another; omit q for a map "
        "of the codebase.\n"
        "Do NOT read `graphify-out/graph.json` — it is megabytes and would blow your context. "
        "Grep is still correct for literal text (an error string, a TODO, a config value); the "
        "graph indexes SYMBOLS. If the query reports no graph yet, grep instead."
    )


def _engine_subagent_notes(cwd: str = "") -> list:
    """The engine notes a subagent of this workspace could be given, in the order they are sent,
    each only when its own switch is on and the workspace can use it."""
    out = []
    if _live_on(cwd):
        out.append(_live_note(cwd))
    if _forge_on(cwd):
        out.append(_forge_note())
    if _ops_on(cwd):
        out.append(_ops_note())
    if _vertedit_on(cwd):
        out.append(_vertedit_note(cwd))
    if _debugger_on(cwd):
        out.append(_debugger_note(cwd))
    if _animate_on(cwd):
        out.append(_animate_note(cwd))
    if _navigate_on(cwd):
        out.append(_navigate_note(cwd))
    # A subagent sent to lay out a level is exactly the one that must not write its own eval.
    if _scene_on(cwd):
        out.append(_scene_note(cwd))
    if _code_tools_on(cwd):
        out.append(_code_tools_note())
    return out


def _engine_pointer_note(cwd: str = "") -> str:
    """What a subagent gets in place of the engine notes (cc_engine_subagents off): where they are.

    A subagent's prompt is not shared with the parent's cache, so every note in it is paid in full
    by every agent of a fan-out. The engine notes were ~11k of the 13k chars a STUDIO subagent got,
    and most subagents search code or read files. This line costs ~70 tokens; the one agent that
    needs the engine reads the full notes with one call."""
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    base = f"http://{host}:{settings.get('port') or 8777}"
    proj = (cwd or "").replace("\\", "/")
    tools = " The same calls are also the mcp__studio__* tools (ToolSearch loads them)." if _mcp_on() else ""
    return ("STUDIO ENGINE: this workspace has the Studio engine (the live game link, the forge and "
            "scene edit). If your task needs the running game or an asset built or rendered, read "
            f"its notes first: curl -s --get {base}/api/settings/engine-notes --data-urlencode "
            f"'cwd={proj}'. Never start your own browser for it.{tools}")


def _browser_rule_note() -> str:
    """Two facts every agent needs about browsers, and nothing else.

    This exists because the opposite was tried. The full visual-review note is ~697 tokens and a
    subagent prompt is not shared with the parent's cache, so a ten-agent fan-out paid ~7k for a
    harness most of them never used — and it was switched off for subagents to save exactly that.

    The saving cost more than it saved. Not knowing a shared browser existed, three agents each
    built their own screenshot pipeline, and one of them opened with `taskkill /IM chrome.exe`,
    which kills every Chrome on the machine. They then killed each other's browsers, and the
    user's, about twenty times in an afternoon, and re-took the failed shots each time.

    So this is the 139-token version: the rule, and the one-line alternative that makes the rule
    easy to follow. Sent to every subagent unconditionally, because a fan-out is exactly the
    situation in which it matters and exactly the one where nobody remembers to pass it on.
    """
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = settings.get("port") or 8777
    return (
        "SHARED BROWSER: a headless Chrome is already running on this machine and other agents "
        "may be using it right now. NEVER kill browsers by name — taskkill /IM, pkill, killall, "
        "Stop-Process -Name — that kills the user's own browser with every tab they had open, "
        "and every other agent's mid-screenshot. Stop only what you started, by pid. "
        # Measured on this machine: forcing software cost 25 of 32 cores rendering one 3D scene,
        # with the GPU idle. `--enable-unsafe-swiftshader` alone is the right flag — it PERMITS
        # a fallback when no GPU is usable, which is what stops a 3D shot coming back black.
        "If you launch a browser yourself, never pass --use-angle=swiftshader: it FORCES software "
        "rendering (measured here at 11x slower, and 25 of 32 cores on one 3D page). Pass only "
        "--enable-unsafe-swiftshader, which permits a fallback without demanding one. For a "
        "screenshot use the shared browser, which needs nothing launched or cleaned up:\n"
        f"  curl -s -X POST http://{host}:{port}/api/review/render -H 'content-type: application/json' "
        "-d '{\"project\":\"<abs path>\",\"mode\":\"scene\",\"times\":[0,500,1200]}'"
    )


def _memory_off_note() -> str:
    """Injected when cc_memory is OFF: don't read/use/update the persistent auto-memory."""
    return (
        "PERSISTENT MEMORY IS DISABLED this session: do NOT read, rely on, or update the auto-memory "
        "(MEMORY.md / the memory directory). Treat it as off and don't save new memories."
    )


def _output_style_note() -> str:
    """The active output style (chat settings → Output style), injected as a system-prompt
    overlay. Styles live in Claude Code's own ~/.claude/output-styles/<id>.md, so the one
    picked here is the same one `/output-style` applies in a terminal. '' when off."""
    sid = (settings.get("cc_output_style") or "").strip()
    if not sid:
        return ""
    try:
        from . import output_styles
        text = output_styles.body(sid)
    except Exception:
        return ""
    if not text:
        return ""
    return (
        "OUTPUT STYLE ACTIVE — this replaces your default writing voice for every reply in this "
        "session. It governs HOW you write only: it never changes what you may do, which tools you "
        "use, or how carefully you work. Follow it for chat replies; keep code, commands and file "
        "contents exactly as they must be.\n\n" + text
    )


def _fable_efficiency_note() -> str:
    """Injected when the session model is Fable (and cc_fable_efficient is on): trims Fable's
    output/deliberation overhead WITHOUT capping its reasoning — per Anthropic's own Fable
    prompting guidance. Also points at the bundled ai-token-routing skill for delegation."""
    return (
        "TOKEN EFFICIENCY (style, not a capability cap): when you have enough information to act, act — "
        "don't re-derive established facts, re-litigate settled decisions, or survey options you won't "
        "pursue. Lead with the outcome; be selectively concise (drop details that don't change what the "
        "reader does next). Don't add features, refactors, abstractions, or error handling beyond what "
        "the task requires. Prefer reading a repo's existing docs/maps over re-grepping what's already "
        "mapped. When delegating to subagents or Workflows, route each task to the cheapest model+effort "
        "that preserves judgment quality — the `ai-token-routing` skill has the routing table."
    )


def _apply_1m(model: str) -> str:
    """Request extended context for an explicit Opus pick; preserve the CLI default.

    Native 1M models also need the environment gate below when the switch is off.
    """
    if not settings.get("cc_1m", True):
        return model
    m = (model or "").strip()
    if "[" in m:  # already carries a context/output selector, e.g. opus[1m]
        return m
    low = m.lower()
    if m in ("", "default"):
        return m
    if "opus" in low:
        return f"{m}[1m]"
    return m


def _claude_context_env() -> dict[str, str]:
    """Bound native 1M models as well as older explicit [1m] variants."""
    return {} if settings.get("cc_1m", True) else {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}


_PROMPT_FP = DATA_DIR / "prompt_fingerprints.json"


def _snapshot_flag(project_id: str, notes: list[str], resuming: bool) -> list[str]:
    """Decide `--system-prompt-snapshot` for this launch (CLI 2.1.257).

    The flag records the whole system prompt once per conversation and replays it verbatim
    on every later request and resume, so the prompt-cache prefix stops moving. It is off by
    default for anyone passing ``--append-system-prompt`` — which the Studio always does —
    because a recorded prompt makes a LATER launch's different append text be ignored until
    the next compaction. That is the whole footgun: turn off a Chat-helpers toggle and the
    note it controls would still be in the prompt.

    So the flag is not passed blind. The assembled notes are fingerprinted per project:

      * notes unchanged  -> ``on``  — replay the record, keep the cache prefix stable
      * notes changed AND we are resuming -> ``off`` — the new text must apply NOW
      * fresh conversation -> ``on`` — there is no record to ignore, so this writes one

    Measured on this machine: the flag is accepted but currently inert (2.1.257 records
    nothing in the transcript, matching the help's "no effect where system-prompt recording
    is not yet enabled"). It costs nothing to send and starts working when recording does.
    """
    if not settings.get("cc_prompt_snapshot", False):
        return []
    import hashlib
    fp = hashlib.sha256("\n\n".join(notes).encode("utf-8", "replace")).hexdigest()[:16]
    key = project_id or "default"
    try:
        store = json.loads(_PROMPT_FP.read_text(encoding="utf-8"))
        if not isinstance(store, dict):
            store = {}
    except Exception:
        store = {}
    changed = store.get(key) != fp
    if changed:
        store[key] = fp
        try:
            _PROMPT_FP.parent.mkdir(parents=True, exist_ok=True)
            _PROMPT_FP.write_text(json.dumps(store, indent=1), encoding="utf-8")
        except OSError:
            pass
    return ["--system-prompt-snapshot", "off" if (changed and resuming) else "on"]


def _context_window_note(model: str) -> str:
    """Tell the session how big its context window actually IS.

    Nothing in the harness states the window size, so a model falls back on the 200k it was
    trained around and misreads its own fill by 5x. Measured here: a session recommended a
    fresh window at 365,792 tokens used — 36% of the 1M it actually had, but 183% of 200k.
    The plumbing was never wrong (`--model ...[1m]` is sent, and the probe confirms
    contextWindow=1000000); only the session's belief about it was.

    The 1M test matches ``mission._context_info`` on purpose, so the meter the user reads and
    the number the model believes can never disagree. Returns '' when 1M is not in play —
    better to say nothing than to assert a window we did not verify."""
    ml = (model or "").lower()
    on_1m = mission.model_window(model) == 1_000_000
    if not on_1m:
        return ""
    head = ("CONTEXT WINDOW: this session holds 1,000,000 tokens, not the 200k default. Convert "
            "before you judge how full you are — 200k used is 20% of the window, 500k is half. ")
    try:
        from . import usage as _usage
        tier = _usage.plan_tier()
    except Exception:
        tier = ""
    if tier == "pro" and (not ml or any(x in ml for x in ("claude", "opus", "sonnet", "haiku", "fable", "mythos"))):
        return head + (
            "Long conversations can consume more of this plan's usage quota even when there "
            "is context space left. Mid-task, keep going — do not interrupt work to suggest "
            "`/clear`. When the user turns to an UNRELATED task, say in one line that a fresh "
            "conversation there costs less, and let them decide."
        )
    return head + (
        "Do NOT recommend `/clear`, `/compact`, a fresh session, or a handoff document because the "
        "conversation feels long; recommend it only when you are genuinely near the limit or the "
        "user asks. Long is normal here. Keep working."
    )


# ---------------------------------------------------------------------------
# THE COMMAND LINE HAS A CEILING, AND THE NOTES REACHED IT.
#
# Windows starts a process with ONE command-line string of at most 32,767 characters, and every
# agent note rides in it: `--append-system-prompt` and `--append-subagent-system-prompt` carry the
# text itself. On 2026-09-23 two new notes took the line to 34,210 characters, and every workspace
# that needed a NEW process got "[WinError 206] The filename or extension is too long". Nothing
# could be sent there at all; the brainrot workspace was the first to find it.
#
# The CLI also reads either prompt from a file (`--append-system-prompt-file`,
# `--append-subagent-system-prompt-file`, each read once at startup, 2.1.280). The two are NOT
# equal, and the difference is in the binary: a session whose MAIN prompt came from a file is
# marked fork-restricted, so `/fork`, a renderer switch and a self-restart in the terminal refuse
# — a fork replays the prompt TEXT and cannot replay a file. The subagent file carries no mark.
# So:
#   - under the ceiling, nothing changes: both texts stay on the line, exactly as before;
#   - over it, the SUBAGENT text moves to a file first (11,700 characters today, and free);
#   - only if the line is still too long does the main text move too. A session that starts
#     without /fork beats a session that does not start.
# A batch-file shim (npm's claude.cmd) runs through cmd.exe, whose ceiling is 8,191.
_CMDLINE_MAX = 30_000          # the hard limit is 32,767; the margin absorbs a note that grows
_CMDLINE_MAX_CMD = 8_000       # cmd.exe, for a .cmd / .bat shim
_CMDLINE_HARD = 32_766
_CMDLINE_HARD_CMD = 8_191
_PROMPT_DIR = DATA_DIR / "prompts"
_PROMPT_KEEP = 7 * 24 * 3600.0  # the CLI reads its file once, at startup; a week is generous
_PROMPT_FILE_FLAGS = (("--append-subagent-system-prompt", "--append-subagent-system-prompt-file"),
                      ("--append-system-prompt", "--append-system-prompt-file"))
_prompt_swept = [0.0]


def _is_batch_shim(args: list) -> bool:
    return bool(args) and str(args[0]).lower().endswith((".cmd", ".bat"))


def _cmdline_len(args: list) -> int:
    """The length of the ONE string Windows is handed for these args — what Popen builds."""
    return len(subprocess.list2cmdline([str(a) for a in args]))


def _prompt_file(text: str) -> Path:
    """The prompt in a file named by its content, written once.

    Named by a hash and not by project, so two launches never race over one file: the same notes
    are the same file, and a changed note is a NEW file rather than an edit under a process that
    may be reading the old one. Bytes, not text mode — text mode on Windows would turn every
    newline into CRLF and hand the agent a different prompt from the one on the line."""
    import hashlib
    data = text.encode("utf-8")
    _PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    path = _PROMPT_DIR / (hashlib.sha256(data).hexdigest()[:24] + ".md")
    try:
        if path.is_file() and path.stat().st_size == len(data):
            os.utime(path)                     # in use again: keep it out of the sweep
            return path
    except OSError:
        pass
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    tmp.write_bytes(data)
    fsutil.replace(tmp, path)
    _sweep_prompt_files()
    return path


def _sweep_prompt_files() -> None:
    """Remove prompt files nobody has used for a week. At most once an hour."""
    now = time.time()
    if now - _prompt_swept[0] < 3600:
        return
    _prompt_swept[0] = now
    try:
        for f in _PROMPT_DIR.iterdir():
            try:
                if now - f.stat().st_mtime > _PROMPT_KEEP:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _fit_command_line(args: list) -> list:
    """These args with prompt text moved into files until the command line fits.

    Unchanged when it already fits, so a session that started before starts the same way now."""
    limit = _CMDLINE_MAX_CMD if _is_batch_shim(args) else _CMDLINE_MAX
    if os.name != "nt" or _cmdline_len(args) <= limit:
        return args
    out = list(args)
    for flag, file_flag in _PROMPT_FILE_FLAGS:
        if _cmdline_len(out) <= limit:
            break
        if flag not in out:
            continue
        i = out.index(flag)
        if i + 1 >= len(out):
            continue
        try:
            path = _prompt_file(str(out[i + 1]))
        except OSError:
            continue                           # _launch_problem reports what is left
        out[i:i + 2] = [file_flag, str(path)]
    return out


def _launch_problem(args: list) -> str:
    """Why this launch cannot succeed, found BEFORE anything is stopped; "" when it can.

    The old process used to be killed first and the new one launched second, so a launch that
    was bound to fail took the working session down with it and left the workspace with no
    process at all — every later send failed the same way."""
    if os.name != "nt":
        return ""
    n = _cmdline_len(args)
    hard = _CMDLINE_HARD_CMD if _is_batch_shim(args) else _CMDLINE_HARD
    if n > hard:
        return ("The agent notes do not fit the Windows command line (%d characters; the limit is "
                "%d). Turn some notes off in Settings, then send again." % (n, hard))
    return ""


def _claude_args(exe: str, message: str, session_id: Optional[str], model: str,
                 permission_mode: str, fork: bool, effort: str) -> list[str]:
    args: list[str] = [exe, "-p", message, "--output-format", "text"]
    if session_id:
        args += ["--resume", session_id]
        if fork:
            args += ["--fork-session"]
    if model and model != "default":
        args += ["--model", model]
    if permission_mode and permission_mode != "default":
        args += ["--permission-mode", permission_mode]
    notes: list[str] = []
    # Two conditions, and the second is the one that was missing: the note only exists when a
    # generator is actually switched on. Turning the tabs off used to hide the buttons while
    # still telling the agent the API was there.
    if settings.get("studio_tools_prompt", True) and plugins.generation_available():
        notes.append(_studio_note())
    if web_tools.enabled():
        notes.append(_web_note())
    if settings.get("cc_autolearn"):
        notes.append(_autolearn_note())
    if settings.get("cc_graphify"):
        notes.append(_graphify_note())
    if settings.get("cc_blender_kiln"):
        notes.append(_blender_kiln_note())
    if not settings.get("cc_memory", True):
        notes.append(_memory_off_note())
    win_note = _context_window_note(model)   # model already carries [1m] — _apply_1m runs before this
    if win_note:
        notes.append(win_note)
    if settings.get("cc_fable_efficient", True) and "fable" in (model or "").lower():
        notes.append(_fable_efficiency_note())
    # BOOST's one note. Appended in BOTH arg builders, and byte-identical on every turn on
    # purpose: a note that is re-worded between turns moves the cached prefix, which reprices
    # the whole conversation and costs more than the note saves. See boost.py.
    try:
        from . import boost as _boost
        if _boost.enabled():
            _bd = _boost.directive()
            if _bd:
                notes.append(_bd)
    except Exception:
        pass                       # a saving is never worth a failed send
    style_note = _output_style_note()   # last style word wins — keep it after the other style notes
    if style_note:
        notes.append(style_note)
    eff = (effort or "default").lower()
    if eff == "ultracode":
        args += ["--effort", "xhigh"]
        notes.append("Ultracode mode: optimize for the most exhaustive, correct answer (not the fastest or "
                     "cheapest). Use the Workflow tool to orchestrate substantive tasks; adversarially verify.")
    elif eff in EFFORT_LEVELS:
        args += ["--effort", eff]
    # Subagents do not inherit the session's system prompt — without this, every Task-tool agent
    # starts blind and re-greps the repo. Enabled by CLAUDE_CODE_ENABLE_APPEND_SUBAGENT_PROMPT,
    # which the spawn env sets.
    if settings.get("cc_graphify"):
        args += ["--append-subagent-system-prompt", _graphify_subagent_note()]
    if notes:
        args += ["--append-system-prompt", "\n\n".join(notes)]
    # One-shot send: it has no project of its own, so the fingerprint lives under a shared key.
    args += _snapshot_flag("", notes, bool(session_id))
    return _fit_command_line(args)


def _claude_stream_args(exe: str, session_id: Optional[str], model: str,
                        permission_mode: str, fork: bool, effort: str,
                        project_id: str = "", cwd: str = "") -> list[str]:
    """Process args for a persistent streaming session. The user message is NOT here —
    it's written to stdin as stream-json. Everything else (model/effort/permission/
    system-prompt/resume) is fixed for the life of the process."""
    args: list[str] = [exe, "-p",
                       "--input-format", "stream-json",
                       "--output-format", "stream-json",
                       "--include-partial-messages",  # live token + activity deltas as Claude generates
                       "--verbose"]
    if project_id:
        # mid-turn /btw injection: a PostToolUse hook that feeds a pending side-note into
        # the RUNNING turn at the next tool boundary (stdin only queues for the next turn)
        try:
            args += ["--settings", str(_btw_settings_file(project_id))]
        except Exception:
            pass
    # The Studio engine as MCP tools, beside the notes (see mcp_engine.py). One small file per
    # project names the stdio server; the CLI starts it for this session and every subagent.
    if project_id and _mcp_on():
        try:
            args += ["--mcp-config", str(_mcp_config_file(project_id, cwd))]
        except Exception:
            pass
    if session_id:
        args += ["--resume", session_id]
        if fork:
            args += ["--fork-session"]
    # Kimi engine: model selection travels via ANTHROPIC_MODEL in the spawn env (the CLI's
    # --model validates against Anthropic ids and would reject kimi-k3), so skip the flag.
    if model and model != "default" and not mission.alt_prefix(project_id):
        args += ["--model", model]
    if permission_mode and permission_mode != "default":
        args += ["--permission-mode", permission_mode]
    notes: list[str] = []
    # Two conditions, and the second is the one that was missing: the note only exists when a
    # generator is actually switched on. Turning the tabs off used to hide the buttons while
    # still telling the agent the API was there.
    if settings.get("studio_tools_prompt", True) and plugins.generation_available():
        notes.append(_studio_note())
    if web_tools.enabled():
        notes.append(_web_note())
    if settings.get("cc_autolearn"):
        notes.append(_autolearn_note())
    if settings.get("cc_graphify"):
        notes.append(_graphify_note())
    if settings.get("cc_blender_kiln"):
        notes.append(_blender_kiln_note())
    if settings.get("cc_phases", True) and project_id:
        notes.append(_phases_note(project_id))
    if _review_on(cwd):
        notes.append(_review_note(cwd))
    else:
        # The full note already carries the rule and the endpoint. Where it is NOT sent — a
        # project with no page to render — the two-line version still goes, because the danger
        # is not about reviewing art: it is about one process killing every browser on the box.
        notes.append(_browser_rule_note())
    # Independent of the review harness on purpose. They answer different questions — one shows
    # the picture, the other says why — and turning off the expensive one should not remove the
    # cheap one.
    if _live_on(cwd):
        notes.append(_live_note(cwd))
    # The forge is a note of its own with its own switch: off means an agent is never told it exists.
    if _forge_on(cwd):
        notes.append(_forge_note())
    # The toolbox stands apart from the live link: it needs no browser, only asset code to write.
    if _ops_on(cwd):
        notes.append(_ops_note())
    if _vertedit_on(cwd):
        notes.append(_vertedit_note(cwd))
    if _debugger_on(cwd):
        notes.append(_debugger_note(cwd))
    if _animate_on(cwd):
        notes.append(_animate_note(cwd))
    if _navigate_on(cwd):
        notes.append(_navigate_note(cwd))
    if _scene_on(cwd):
        notes.append(_scene_note(cwd))
    if _new_game_on(cwd):
        notes.append(_new_game_note(cwd))
    if _code_tools_on(cwd):
        notes.append(_code_tools_note())
    if permission_mode == "plan":
        notes.append(_plan_note())
    if not settings.get("cc_memory", True):
        notes.append(_memory_off_note())
    win_note = _context_window_note(model)   # model already carries [1m] — _apply_1m runs before this
    if win_note:
        notes.append(win_note)
    if settings.get("cc_fable_efficient", True) and "fable" in (model or "").lower():
        notes.append(_fable_efficiency_note())
    # BOOST's one note. Appended in BOTH arg builders, and byte-identical on every turn on
    # purpose: a note that is re-worded between turns moves the cached prefix, which reprices
    # the whole conversation and costs more than the note saves. See boost.py.
    try:
        from . import boost as _boost
        if _boost.enabled():
            _bd = _boost.directive()
            if _bd:
                notes.append(_bd)
    except Exception:
        pass                       # a saving is never worth a failed send
    style_note = _output_style_note()   # last style word wins — keep it after the other style notes
    if style_note:
        notes.append(style_note)
    eff = (effort or "default").lower()
    if eff == "ultracode":
        args += ["--effort", "xhigh"]
        notes.append("Ultracode mode: optimize for the most exhaustive, correct answer (not the fastest or "
                     "cheapest). Use the Workflow tool to orchestrate substantive tasks; adversarially verify.")
    elif eff in EFFORT_LEVELS:
        args += ["--effort", eff]
    # Alternate engine (Kimi / Qwen): tell the model it is NOT Claude, or it answers "I am Claude"
    # straight from the harness's built-in prompt and you can't tell which engine actually replied.
    alt_pfx = mission.alt_prefix(project_id)
    if alt_pfx:
        notes.append(_alt_identity_note(alt_pfx, model))
    elif model and model.lower().startswith("claude-"):
        # an explicitly PINNED model id — tell the session what it is actually running on
        notes.append(_pinned_model_note(model))
    # Same for this session's subagents; the workspace path is baked in so the command they get
    # is runnable as written.
    # Measured: graphify costs a subagent ~166 tokens, the review note ~697 — and a subagent
    # prompt is NOT shared with the parent's cache, so a ten-agent fan-out paid ~8.6k for it.
    # An agent sent to grep for a symbol has no use for a contact-sheet harness, so review is
    # off here unless you ask for it (Settings -> Planning & review). Graphify stays: it is the
    # one that stops an agent re-discovering the repo, which costs far more than it does.
    # 139 tokens, unconditional. The full review note stays opt-in below; this is only the rule
    # and its one-line alternative, which is what a fan-out actually needs — see the docstring
    # on _browser_rule_note for what leaving it out cost.
    sub_notes = [_browser_rule_note()]
    if settings.get("cc_graphify"):
        sub_notes.append(_graphify_subagent_note(cwd))
    # A research fan-out is exactly where a blocked page costs most: N agents each writing up
    # "could not access" instead of reading the page.
    if web_tools.enabled():
        sub_notes.append(_web_note())
    if settings.get("cc_review_subagents") and _review_on(cwd):
        sub_notes.append(_review_note(cwd))
    # The engine notes were the heavy part of every subagent's prompt: live link, forge, navigate
    # and scene edit alone were ~11k of the 13k chars each subagent paid, and most subagents grep
    # and read. Off by default: one line says what is there and where the full notes are, and the
    # agent that needs the engine reads them (GET /api/settings/engine-notes). On: the full notes
    # again. Settings -> Planning & review -> "...and give subagents the engine notes".
    eng = _engine_subagent_notes(cwd)
    if eng:
        sub_notes.extend(eng if settings.get("cc_engine_subagents", False) else [_engine_pointer_note(cwd)])
    if sub_notes:
        args += ["--append-subagent-system-prompt", "\n\n".join(sub_notes)]
    if notes:
        args += ["--append-system-prompt", "\n\n".join(notes)]
    args += _snapshot_flag(project_id, notes, bool(session_id))
    # Every note above rides on ONE command line, and Windows caps it. See _fit_command_line.
    return _fit_command_line(args)


# --- persistent streaming session machinery --------------------------------

# Friendly verbs for the live activity line ("Editing Workspace.tsx", "Running: npm build").
_TOOL_VERB = {
    "Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing", "NotebookEdit": "Editing notebook",
    "Read": "Reading", "Bash": "Running command", "PowerShell": "Running command",
    "Grep": "Searching", "Glob": "Finding files", "Task": "Delegating to a subagent",
    "Agent": "Delegating to a subagent", "WebFetch": "Reading the web", "WebSearch": "Searching the web",
    "TodoWrite": "Planning", "Skill": "Running a skill", "ExitPlanMode": "Finishing the plan",
}


def _activity_for_block(cb: dict) -> str:
    """Live activity the instant a content block STARTS (name only — input streams later)."""
    t = cb.get("type")
    if t == "thinking":
        return "Thinking"
    if t == "text":
        return "Writing"
    if t == "tool_use":
        return _TOOL_VERB.get(cb.get("name") or "", f"Using {cb.get('name') or 'a tool'}")
    return "Working"


def _activity_from_content(content) -> str:
    """A richer activity from a COMPLETED assistant message (enriched with file/command)."""
    if not isinstance(content, list):
        return ""
    for b in reversed(content):
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "tool_use":
            name = b.get("name") or "tool"
            verb = _TOOL_VERB.get(name, f"Using {name}")
            inp = b.get("input") or {}
            if name in ("Bash", "PowerShell"):
                cmd = inp.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    return f"Running: {cmd.strip().splitlines()[0][:42]}"
            detail = inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("notebook_path")
            if isinstance(detail, str) and detail:
                base = os.path.basename(detail.rstrip("/\\")) or detail
                return f"{verb} {base}"[:52]
            return verb
        if bt == "thinking":
            return "Thinking"
        if bt == "text":
            return "Writing"
    return ""


def _live_progress(live: "_Live", typ: str, ev: dict) -> None:
    """Update the live token count, the streaming answer text, and the current activity from
    one stream event (lock held).

    Tokens — the per-message FINAL count lives in `message_delta.usage.output_tokens` (the
    `assistant` event's usage is only a partial snapshot), so we track `cur_tokens` live and BANK
    it into `turn_committed` on `message_stop` — accumulating across a multi-message turn
    (think → tool → text …). LIVE estimates while a block streams:
      • thinking — the CLI REDACTS the prose (`thinking` is ""), but every `thinking_delta`
        carries `estimated_tokens` (a running estimate for the block), so the counter ticks up
        DURING the think instead of jumping only at the end.
      • text / tool input — estimate as chars//4 from the streamed bytes.
    `cur_block_base` locks in earlier blocks' tokens so each block's estimate ADDS on top.

    Text — `text_delta` carries the real visible answer; we append it to `cur_text` so the UI can
    show the answer being written live (thinking stays hidden because the CLI doesn't expose it)."""
    try:
        if typ == "assistant":
            act = _activity_from_content((ev.get("message") or {}).get("content"))
            if act:
                live.activity = act          # use the assistant entry for ACTIVITY only
        elif typ == "stream_event":
            e = ev.get("event") or {}
            et = e.get("type")
            if et == "message_start":
                live.msg_started = time.time()          # a generation window opens
                m = (e.get("message") or {}).get("model")
                if m:
                    live.turn_model = str(m)            # what the endpoint really served
                live.cur_chars = 0
                live.cur_text = ""
                live.cur_kind = ""
                live.cur_tokens = int(((e.get("message") or {}).get("usage") or {}).get("output_tokens", 0) or 0)
                live.cur_block_base = live.cur_tokens
            elif et == "content_block_start":
                cb = e.get("content_block") or {}
                live.cur_kind = cb.get("type") or ""
                live.activity = _activity_for_block(cb)
                live.cur_block_base = live.cur_tokens   # subsequent estimates add on top of prior blocks
                live.cur_chars = 0
            elif et == "content_block_delta":
                d = e.get("delta") or {}
                dt = d.get("type")
                if dt == "thinking_delta":
                    # thinking prose is REDACTED in headless (`thinking` is ""), but each delta carries
                    # a PER-DELTA `estimated_tokens` (~50–150), so SUM them for a live thinking-token
                    # count that climbs as it reasons (≈ the exact count, snapped at message_delta).
                    est = d.get("estimated_tokens")
                    if est:
                        live.cur_tokens += int(est)
                    th = d.get("thinking") or ""         # usually "" in headless; stream it if ever present
                    if th:
                        live.cur_text += th
                elif dt == "text_delta":
                    t = d.get("text") or ""
                    if t:
                        live.cur_text += t               # the visible answer, live
                        live.cur_chars += len(t)
                        live.cur_tokens = max(live.cur_tokens, live.cur_block_base + live.cur_chars // 4)
                elif dt == "input_json_delta":
                    pj = d.get("partial_json") or ""     # tool args (counts toward tokens, not shown)
                    if pj:
                        live.cur_chars += len(pj)
                        live.cur_tokens = max(live.cur_tokens, live.cur_block_base + live.cur_chars // 4)
            elif et == "message_delta":
                u = e.get("usage") or {}
                if u.get("output_tokens") is not None:
                    live.cur_tokens = int(u.get("output_tokens") or 0)   # snap to the EXACT count
            elif et == "message_stop":
                if live.msg_started:
                    live.gen_s += time.time() - live.msg_started
                    live.msg_started = 0.0
                live.turn_msgs += 1
                live.turn_committed += live.cur_tokens   # message done → bank the exact count
                live.cur_tokens = 0
                live.cur_chars = 0
                live.cur_block_base = 0
                live.cur_text = ""    # the completed message now lands in the transcript the feed tails
                live.cur_kind = ""
                live.msg_done = True  # → force the next WS push so the feed pulls the transcript NOW
    except Exception:
        pass


def live_state(project_id: str) -> dict:
    """Instant live progress (real-time tokens + current activity + elapsed) read straight
    from the live session — no transcript parse, so it updates the moment the engine does."""
    return engines.for_feed(project_id).live_state(project_id)


def _claude_live_state(project_id: str) -> dict:
    live = _live.get(project_id)
    if live is None or not live.alive:
        return {"working": False, "tokens": 0, "activity": "", "elapsed": 0.0, "text": "", "kind": ""}
    with live.lock:
        busy = bool(live.turn_active or live.last_write > live.last_result)
        compacting = bool(live.compacting) and busy
        tokens = live.turn_committed + live.cur_tokens
        # while compacting, the label is fixed ("Compacting…") — it's one long internal op, not the
        # usual Thinking/Editing/Writing steps — so the UI can show a clear "this isn't frozen" state
        activity = ("Compacting the conversation" if compacting else live.activity) if busy else ""
        elapsed = (time.time() - live.turn_started) if (busy and live.turn_started) else 0.0
        # the answer being typed right now — cap to the tail so a long reply stays a small payload
        # (the full text lands in the transcript when the message completes)
        text = "" if compacting else (live.cur_text[-6000:] if busy else "")
        kind = live.cur_kind if busy else ""
        # Generation-only seconds: banked windows plus the one open right now. Dividing by
        # `elapsed` instead would rate a model by how slow its TOOLS were.
        gen = live.gen_s + ((time.time() - live.msg_started) if live.msg_started else 0.0)
        model = live.turn_model
    tps = round(tokens / gen, 1) if (busy and gen > 0.4 and tokens > 0) else 0.0
    return {"working": busy, "tokens": int(tokens), "activity": activity, "compacting": compacting,
            "elapsed": round(elapsed, 1), "text": text, "kind": kind,
            # live output speed — what the model is actually producing per second right now
            "tps": tps, "gen_s": round(gen, 1), "model": model}


def note_graph_read(root: str, symbol: str) -> None:
    """Put "Reading the code graph" on the live indicator, where Thinking/Writing/Running go.

    Every other label comes from the stream, because every other activity IS the stream. The
    graph is read by a PreToolUse hook — a separate process that runs BEFORE the tool and never
    appears in the stream at all — so nothing in the normal path could ever know it happened.
    The hook's own query is therefore what reports it, and the label lands where a person is
    already watching instead of only in the transcript afterwards.

    It is deliberately only set mid-turn. Announcing activity on an idle session would be
    inventing one, and the pulse is trusted precisely because it never does that."""
    try:
        want = str(Path(root).resolve()).lower()
    except (OSError, ValueError):
        return
    for live in list(_live.values()):
        sig = getattr(live, "sig", ())
        if len(sig) < 2:
            continue
        try:
            if str(Path(str(sig[1])).resolve()).lower() != want:
                continue
        except (OSError, ValueError):
            continue
        with live.lock:
            if not (live.turn_active or live.last_write > live.last_result):
                return                       # idle — say nothing
            live.activity = ("Reading the code graph · " + symbol)[:64]
        _publish_live(live, force=True)      # push it now; the pulse polls, but this is instant
        return


def _publish_live(live: "_Live", force: bool = False) -> None:
    """Push this session's live state to WS clients so the chat updates the instant Claude
    does — no polling latency. Throttled to ~7/s per project (forced on turn start/end and
    message completion). MUST be called OUTSIDE live.lock: live_state() re-acquires it.
    Nobody connected → skip; the HTTP polling fallback still works."""
    now = time.time()
    if not force and (now - live.last_pub) < 0.06:   # ~16/s: smooth token streaming, still light
        return
    live.last_pub = now
    try:
        from .events import bus
        from .models import ProgressEvent, WSEventType
        if bus.subscriber_count() > 0:
            bus.publish_threadsafe(ProgressEvent(
                type=WSEventType.cc_live,
                data={"project_id": live.project_id, **live_state(live.project_id)}))
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).is_running()
    except Exception:
        return False


class _Adopted:
    """Stands in for Popen when we inherited a session from a previous backend.

    Windows will not hand a new parent the stdin of a child it did not create, so `stdin` here is
    always None. It used to follow that an adopted session could be watched and stopped but not
    written to, and that the next message had to respawn it with `--resume`. That reads like a
    fair trade until you remember that background agents live INSIDE the process being replaced,
    and go with it.

    The pipe is not reached through this object at all any more. A keeper process holds it and
    takes messages through a folder (`_inbox_put`), which ANY backend can write to. So this class
    is now only what its name says: a handle for watching and, when asked, stopping."""
    __slots__ = ("pid", "stdin", "stdout", "returncode")

    def __init__(self, pid: int):
        self.pid = int(pid)
        self.stdin = None            # always; messages go to the keeper (see the docstring)
        self.stdout = None
        self.returncode = None

    def poll(self):
        if self.returncode is None and not _pid_alive(self.pid):
            self.returncode = 0
        return self.returncode

    def wait(self, timeout: float = 0.0):
        end = time.time() + (timeout or 0)
        while _pid_alive(self.pid) and time.time() < end:
            time.sleep(0.05)
        return self.poll()

    def terminate(self):
        _force_kill_tree(self.pid)

    kill = terminate


def _reg_path(project_id: str) -> Path:
    return LIVE_DIR / (re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "x") + ".json")


def _register(live: "_Live", cwd: str) -> None:
    try:
        _reg_path(live.project_id).write_text(json.dumps({
            "project_id": live.project_id, "pid": getattr(live.proc, "pid", 0),
            "session_id": live.session_id or "", "cwd": cwd,
            "log": str(live.log_path), "offset": 0,
            # how the NEXT backend talks to this session without restarting it
            "inbox": live.inbox, "keeper_pid": live.keeper_pid,
            "sig": list(live.sig or ()), "started": time.time(),
        }), encoding="utf-8")
    except Exception:
        pass


def _reg_checkpoint(live: "_Live") -> None:
    """Called at a turn boundary — the natural place to say 'a new backend may resume here'."""
    p = _reg_path(live.project_id)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    d["offset"] = int(live.read_from)
    if live.session_id:
        d["session_id"] = live.session_id
    try:
        p.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def _unregister(project_id: str) -> None:
    try:
        _reg_path(project_id).unlink()
    except OSError:
        pass


def _tail_lines(live: "_Live"):
    """Yield complete lines from the session log as the child appends them.

    The child writes stdout straight to this file, so there is no pipe to break when the backend
    dies: the process keeps running and keeps appending, and the next backend resumes the tail
    from the last checkpoint. Node writes to a regular file synchronously, so the file is as
    live as the pipe was."""
    try:
        f = open(live.log_path, "rb")
    except OSError:
        return
    try:
        f.seek(live.read_from)
        buf = b""
        gone = 0
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                if live.proc.poll() is not None:
                    gone += 1
                    if gone > 4:            # exited, and the tail is drained
                        break
                else:
                    gone = 0
                time.sleep(0.04)
                continue
            gone = 0
            buf += chunk
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                yield buf[:nl]
                buf = buf[nl + 1:]
            live.read_from = f.tell() - len(buf)
        if buf.strip():
            yield buf
    finally:
        try:
            f.close()
        except Exception:
            pass


def _live_reader(live: "_Live") -> None:
    """Drain the process's stream-json stdout: capture the real session id and track
    turn boundaries (``result`` ends a turn; the first activity line starts one) to
    keep the busy state accurate even when Claude coalesces queued messages."""
    try:
        for raw in _tail_lines(live):
            line = (raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray))
                    else str(raw)).strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            typ = ev.get("type")
            if typ == "system" and ev.get("subtype") == "init":
                sid = ev.get("session_id")
                if sid:
                    live.session_id = sid
                    # pin it: this is the conversation the Studio is really running, so a
                    # restart resumes THIS one even if something else touched the folder
                    mission.remember_active_session(live.project_id, sid)
            elif typ == "result":
                _reg_checkpoint(live)          # a new backend may safely resume from here
                with live.lock:
                    live.turn_active = False   # turn finished
                    live.last_result = time.time()   # busy clears unless a newer write is pending
                    live.turn_committed += live.cur_tokens   # bank any uncommitted live tokens
                    if live.msg_started:                     # a window left open by an aborted message
                        live.gen_s += time.time() - live.msg_started
                        live.msg_started = 0.0
                    # WHAT `total_cost_usd` ACTUALLY MEANS, AND THE 2.34x IT USED TO COST.
                    #
                    # The Studio drives ONE long-lived `claude -p --input-format stream-json`
                    # process per project. In streaming-input mode this event carries the RUNNING
                    # TOTAL FOR THE WHOLE PROCESS, not the price of the turn that just finished —
                    # so banking it per result charged the same dollars again on every turn. On
                    # this machine: $379.73 banked, against $162.51 the CLI's own rate card had
                    # actually reported. 2.34x over. The SDK's cost-tracking page says it in one
                    # line: "read the latest result for call totals rather than summing across
                    # results".
                    #
                    # So a turn's price is its DELTA against this process's high-water mark. Both
                    # branches below matter. `max` keeps the mark from going DOWN, because a
                    # synthetic `task-notification` result reports 0 in the middle of a call and a
                    # plain assignment would let the next real result bank the whole call again.
                    # And if a value ever comes back lower on a real turn, this CLI is reporting
                    # per-turn figures after all — then the raw value IS the turn's price. Either
                    # behaviour is correct here, so a CLI that switches between them cannot
                    # silently halve or double the bill a second time.
                    running = float(ev.get("total_cost_usd") or 0)
                    prev = float(live.call_cost or 0.0)
                    turn_cost = (running - prev) if running >= prev else running
                    live.call_cost = max(prev, running)
                    turn = {"tokens": live.turn_committed, "gen_s": live.gen_s,
                            "wall_s": max(0.0, live.last_result - live.turn_started),
                            "model": live.turn_model or (live.sig[2] if len(live.sig) > 2 else ""),
                            "effort": (live.sig[5] if len(live.sig) > 5 else "default"),
                            "msgs": live.turn_msgs, "compacting": bool(live.compacting),
                            # The CLI prices the turn itself, on its own rate card, and includes
                            # every subagent the turn spawned. Banked rather than discarded — see
                            # spend.py for why the Dashboard's cost card was the smallest number
                            # on the screen.
                            "cost": max(0.0, turn_cost),
                            "call": int(live.call_seq)}
                    live.cur_tokens = 0
                    live.cur_text = ""
                    live.cur_kind = ""
                    live.compacting = False
                    live.activity = ""
                # A RESULT THAT DID NO WORK IS A NOTIFICATION, NOT A TURN. The CLI emits one per
                # background agent (`num_turns: 0`, `origin.kind: "task-notification"`, no tokens),
                # and banking those wrote 14 rows on this machine that carried the PREVIOUS turn's
                # cost a second time. Nothing about a notification belongs in a ledger of what the
                # work cost.
                no_work = (int(turn["tokens"] or 0) <= 0 and float(turn["cost"] or 0) <= 0
                           and int(ev.get("num_turns") or 0) <= 0)
                # how fast this model/effort really is — recorded per turn so the numbers can be
                # compared later. A /compact is excluded: it is one long internal op, not a reply.
                if not turn["compacting"] and not no_work:
                    try:
                        from . import speed
                        speed.record(live.project_id, str(turn["model"]), str(turn["effort"]),
                                     int(turn["tokens"]), float(turn["gen_s"]),
                                     float(turn["wall_s"]), int(turn["msgs"]))
                    except Exception:
                        pass
                # A /compact IS billed, so unlike the speed record this one counts it.
                try:
                    from . import spend
                    if not no_work:
                        spend.record(live.project_id, str(turn["model"]), float(turn["cost"]),
                                     int(turn["tokens"]), billed=_claude_billed(live.project_id))
                except Exception:
                    pass
                # ...and the third consumer, the only one that keeps EVERY turn. `speed` drops a
                # turn too small to be a useful sample and `spend` only accumulates, so neither
                # can answer "what did the answer I am looking at cost". This can.
                try:
                    from . import turns as _turns
                    if not no_work:
                        _turns.record(live.project_id, str(turn["model"]), str(turn["effort"]),
                                      int(turn["tokens"]), float(turn["gen_s"]), float(turn["wall_s"]),
                                      int(turn["msgs"]), float(turn["cost"]), bool(turn["compacting"]),
                                      int(turn["call"]))
                except Exception:
                    pass
                # A turn error MIGHT be auth — but it's just as likely a 429, a tool failure, a refusal,
                # or code the user is writing that merely mentions "401"/"unauthorized". NEVER pop the
                # login modal from turn text; if it looks auth-ish, VERIFY against the real account and
                # let only a genuine 401 surface the popup. A clean result means auth works → clear flag.
                try:
                    if ev.get("is_error") or str(ev.get("subtype") or "").startswith("error"):
                        blob = json.dumps(ev.get("result") or ev.get("errors") or ev.get("error") or "")
                        if claude_auth.AUTH_ERR_RE.search(blob):
                            claude_auth.verify_soon("session")
                    else:
                        claude_auth.report_ok("session")
                except Exception:
                    pass
                # Did this turn prove anything about the model? A refusal ("issue with the
                # selected model") greys that entry in the picker; a clean turn clears a stale
                # one. Reads the text already in hand — no probe, no extra call.
                try:
                    model_access.note_turn(
                        str(turn["model"]) or (live.sig[2] if len(live.sig) > 2 else ""),
                        str(ev.get("result") or ""))
                except Exception:
                    pass
                _publish_live(live, force=True)   # turn ended → clients update instantly
                # /btw turn-end fallback: the turn finished before any tool boundary consumed the
                # note (e.g. it was pure text generation) → deliver it now as a queued message.
                # The hook claims the file atomically (os.replace), so this can't double-send.
                if live.btw_pending:
                    live.btw_pending = False
                    try:
                        nf = _btw_note_file(live.project_id)
                        note = live_notes.claim(nf)
                        if note and not _write_msg(live, _btw_wrap(note)):
                            live_notes.put(nf, note)
                    except OSError:
                        pass
            elif typ in ("assistant", "user", "tool_use", "stream_event"):
                with live.lock:
                    if not live.turn_active:    # first output of a turn → it consumed the queue
                        live.turn_active = True
                        live.outstanding = 0    # (live token/timer counters were set at write time)
                    _live_progress(live, typ, ev)
                    push_now = live.msg_done
                    live.msg_done = False
                _publish_live(live, force=push_now)
    except Exception:
        pass
    finally:
        with live.lock:
            live.alive = False
            live.outstanding = 0
            live.turn_active = False
        try:
            if live.logf is not None:
                live.logf.close()
        except Exception:
            pass


# Alternate engines: the SAME claude CLI pointed at a vendor's Anthropic-compatible endpoint.
# Adding one is data, not code — the streaming/feed/live/btw pipeline is reused verbatim.
_ALT_ENGINES = {
    mission.KIMI_PREFIX: {
        "key": "moonshot", "base": "https://api.moonshot.ai/anthropic", "vendor": "Moonshot AI",
        "setting": "kimi_model", "default_model": "kimi-k3", "home": mission.kimi_home,
    },
    mission.QWEN_PREFIX: {
        "key": "dashscope", "base": "https://dashscope-intl.aliyuncs.com/apps/anthropic",
        "vendor": "Alibaba Cloud", "setting": "qwen_model",
        "default_model": "qwen3.8-max", "home": mission.qwen_home,
    },
}


def _alt_engine(prefix: str) -> dict:
    """Engine spec for a prefix — built-in first, then whatever the user added in
    Settings → Models. Raises KeyError for an unknown prefix, like the dict it replaced."""
    spec = _ALT_ENGINES.get(prefix)
    if spec:
        return spec
    from . import chat_providers
    spec = chat_providers.engines().get(prefix)
    if not spec:
        raise KeyError(prefix)
    dirname = str(spec["home_dirname"])
    return {**spec, "home": lambda d=dirname: mission._alt_home(d)}


def _alt_model(prefix: str, model: str) -> str:
    spec = _alt_engine(prefix)
    return model if (model and model != "default") else str(settings.get(spec["setting"], spec["default_model"]))


def _pinned_model_note(model: str) -> str:
    """State which model is actually serving the session.

    Bundled references are dated snapshots — the claude-api skill's model table is cached, and a
    model that shipped after that date simply is not in it. Asked "is <that model> released?", a
    session consults the table (correctly, it is the designated authority), fails to find it, and
    answers "not released" — while running ON it. Stating the runtime fact heads off that one
    wrong answer without asserting anything else about other models."""
    m = (model or "").split("[")[0].strip()      # drop any [1m] context selector
    return (f"RUNTIME FACT: this session is served by the model `{m}`. Bundled and cached "
            f"references — including the claude-api skill's model table, which is a dated "
            f"snapshot — may not list it. Absence from a cached table is NOT evidence that a "
            f"model is unreleased. When asked which model is running, or whether `{m}` exists, "
            f"trust this runtime fact. To check any OTHER model's availability, query the live "
            f"models endpoint or run `claude --model <id>` rather than citing a cached table.")


def _alt_identity_note(prefix: str, model: str) -> str:
    """Claude Code's built-in system prompt states "You are Claude Code" — an alternate engine
    dutifully repeats it and tells the user it is Claude, which is exactly how a Qwen answer gets
    mistaken for a Claude one. Correct ONLY the identity; everything else about the harness is true."""
    spec = _alt_engine(prefix)
    m = _alt_model(prefix, model)
    return (f"IDENTITY CORRECTION: you are NOT Claude and NOT made by Anthropic. This CLI harness is "
            f"Anthropic's Claude Code, but the model actually serving this conversation is {m}, made by "
            f"{spec['vendor']} and reached through its Anthropic-compatible API. The harness's default "
            f"'You are Claude Code' identity text is inaccurate here: ignore it for any question about "
            f"who or what you are, and identify yourself accurately as {m}. Everything else the harness "
            f"says about your tools, permissions and file access is correct and still applies.")


def _alt_env(prefix: str, model: str) -> dict:
    """Env that turns the claude CLI into an alternate engine (Kimi / Qwen): that vendor's
    Anthropic-compatible endpoint + API-key auth + an ISOLATED CLAUDE_CONFIG_DIR so the
    engine's sessions/context/usage never mix with the real Claude ones. The model rides
    ANTHROPIC_MODEL because the CLI's --model validates against Anthropic ids."""
    from . import keychain
    spec = _alt_engine(prefix)
    key = keychain.get_key(str(spec["key"])) or ""
    home = spec["home"]()          # type: ignore[operator]
    home.mkdir(parents=True, exist_ok=True)
    m = _alt_model(prefix, model)
    # A local engine (Ollama, LM Studio) has no key, but the CLI refuses to start without
    # some token — send a placeholder. The bridge ignores it and uses the stored key, if any.
    if not key and str(spec.get("protocol", "")) == "openai":
        key = "studio-local"
    env = {
        "CLAUDE_CONFIG_DIR": str(home),
        "ANTHROPIC_BASE_URL": str(spec["base"]),
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_API_KEY": key,
        "ANTHROPIC_MODEL": m,
        "ANTHROPIC_SMALL_FAST_MODEL": m,
    }
    # The CLI does not recognise a third-party model id, so it assumes a 200k window and
    # auto-compacts there — throwing away context a 1M-window model could still hold.
    # Telling it the real number is the documented fix for exactly this warning.
    window = int(spec.get("context_window") or 0)
    if window > 0:
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(window)
    return env


def _kimi_env(model: str) -> dict:   # kept for existing call sites
    return _alt_env(mission.KIMI_PREFIX, model)


# chat-agent id -> (session-universe prefix, keychain key, message when that key is missing)
_ALT_AGENTS = {
    "kimi": (mission.KIMI_PREFIX, "moonshot",
             "Kimi K3 needs a Moonshot API key — add it in Settings → API Keys → "
             "'Moonshot / Kimi' (get one at platform.moonshot.ai)."),
    "qwen": (mission.QWEN_PREFIX, "dashscope",
             "Qwen needs a DashScope API key — add it in Settings → API Keys → "
             "'Alibaba DashScope / Qwen' (get one at dashscope-intl.console.aliyun.com)."),
}


def alt_agent(agent_id: str) -> Optional[tuple[str, str, str]]:
    """(session-universe prefix, keychain key, 'add a key' message) for an alternate
    engine — built-in or user-added — or None if this id is not one.

    A local OpenAI-protocol engine needs no key at all, so it reports no requirement.
    """
    built = _ALT_AGENTS.get(agent_id)
    if built:
        return built
    from . import chat_providers
    p = chat_providers.get(agent_id)
    if not p:
        return None
    local = str(p.get("base_url", "")).startswith(("http://127.0.0.1", "http://localhost", "http://0.0.0.0"))
    need = "" if local else (
        f"{p.get('name') or agent_id} needs an API key — add it in Settings → Models → "
        f"{p.get('name') or agent_id}" + (f" (get one at {p['key_url']})" if p.get("key_url") else "."))
    return (chat_providers.prefix(agent_id), chat_providers.key_name(agent_id), need)


_BTW_CARRY_MAX_AGE = 1800.0   # don't resurrect a note older than 30 min (stale crash leftover)


def _claim_pending_btw(project_id: str) -> str:
    """Take (and clear) a /btw note that was written but never consumed.

    A pending note sits in a file until the PostToolUse hook injects it mid-turn, or the turn-end
    fallback queues it. If the session respawns inside that window (settings change, idle reap,
    write-retry) the note used to be DELETED here — the user's side-note silently vanished. Now we
    claim it so the caller can re-deliver it to the new process. A genuinely stale note (crash
    leftover older than _BTW_CARRY_MAX_AGE) is still dropped so it can't fire into an unrelated turn."""
    note = ""
    try:
        nf = _btw_note_file(project_id)
        note = live_notes.claim(nf, max_age=_BTW_CARRY_MAX_AGE)
    except OSError:
        pass
    return note


# --- the session's stdin, held outside the backend ---------------------------
#
# MEASURED, not assumed: a `claude -p --input-format stream-json` process EXITS ONE SECOND AFTER
# ITS STDIN CLOSES. The backend owned the write end of that pipe, so stopping the backend closed
# it, and the session - with every background agent living inside it - was gone before the
# replacement backend had finished starting. No guard in this file could ever have prevented
# that: by the time the new backend runs, there is nothing left to guard.
#
# So the pipe is held by a KEEPER process instead (session_keeper.py), and a message is a file
# dropped into a folder rather than a write down a pipe only one backend can reach. Any backend,
# now or later, can send to a session it did not start - which is what makes adoption real
# instead of a respawn wearing its name.

_WRITE_ACK = 5.0                     # wait this long for the keeper's receipt before judging
_msg_seq = itertools.count()


def _new_inbox(project_id: str) -> Path:
    """A fresh mailbox for ONE session instance.

    Per instance and not per project, deliberately. A respawn's incoming keeper and the outgoing
    one overlap for a moment, and if they shared a folder the dying keeper could pick up a message
    meant for the new session, write it into a closed pipe, and delete it - lost, with nothing
    logged. A folder no other process has ever seen cannot be read by the wrong one."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "x")
    return LIVE_DIR / ("%s.%d.inbox" % (safe, time.time_ns()))


def _keeper_python() -> str:
    """The interpreter that runs the keeper, or "" if there is not one to hand.

    A frozen build ships no python. Then there is no keeper and the old direct pipe is used:
    degraded - a restart still ends that session - but never broken."""
    exe = sys.executable or ""
    if exe and not getattr(sys, "frozen", False):
        return exe
    cand = Path(exe).parent / ("python.exe" if os.name == "nt" else "python")
    return str(cand) if cand.exists() else ""


def _start_keeper(project_id: str):
    """(read fd for the CLI, inbox, keeper Popen) - or (None, None, None) for a direct pipe.

    The keeper starts BEFORE the CLI, and the order is not cosmetic: the write end must already be
    held when the CLI opens the read end, or the CLI sees EOF and exits within the second."""
    py = _keeper_python()
    if not py:
        return None, None, None
    inbox = _new_inbox(project_id)
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        r_fd, w_fd = os.pipe()
    except OSError:
        return None, None, None
    flags = 0
    if os.name == "nt":
        flags = (subprocess.CREATE_NO_WINDOW |             # type: ignore[attr-defined]
                 subprocess.CREATE_NEW_PROCESS_GROUP)      # type: ignore[attr-defined]
    root = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, "ASSET_STUDIO_KEEPER": project_id, "PYTHONPATH": root}
    # The last two are how the keeper eventually lets go: a backend heartbeat and this session's
    # own log. Half an hour with neither means the app is closed and nothing is in flight.
    argv = [py, "-u", "-m", "asset_studio.session_keeper", str(inbox), str(inbox / "child.pid"),
            str(BACKEND_BEAT), str(SESS_DIR / ("%s.log" % project_id))]

    def _go(extra: int):
        return subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=w_fd, stderr=subprocess.DEVNULL,
                                creationflags=flags | extra, close_fds=True)

    try:
        try:
            kp = _go(_BREAKAWAY)
        except OSError:
            kp = _go(0)
    except Exception:
        for fd in (r_fd, w_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        return None, None, None
    try:
        os.close(w_fd)               # the keeper holds it now, and nobody else may
    except OSError:
        pass
    return r_fd, inbox, kp


def _inbox_put(live: "_Live", data: bytes) -> bool:
    """Hand a message to the keeper. False ONLY when the session itself is gone.

    The inbox is a queue on disk, which is stronger than a pipe buffer: a message the keeper is
    slow to pick up is still delivered, and it outlives this backend. So a slow keeper is never
    reported as a dead session. That distinction is not pedantry - "the write failed" is what
    triggers a respawn, and the respawn is what used to kill the background agents."""
    inbox = Path(live.inbox)
    final = inbox / ("%020d-%03d.msg" % (time.time_ns(), next(_msg_seq) % 1000))
    tmp = final.with_suffix(".tmp")
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        fsutil.replace(tmp, final)       # atomic: the keeper never reads half a message
    except OSError:
        return False
    end = time.time() + _WRITE_ACK
    while time.time() < end:
        if not final.exists():       # the keeper deleted it - the CLI has the bytes
            return True
        if live.proc.poll() is not None:
            try:
                final.unlink()
            except OSError:
                pass
            return False
        time.sleep(0.02)
    return live.proc.poll() is None  # still running: the message stays queued, not lost


def keeper_alive(inbox: str) -> bool:
    """Is a keeper holding this session's stdin? Read from its heartbeat, so the answer is true
    for a session this backend never started."""
    if not inbox:
        return False
    try:
        beat = Path(inbox) / "keeper.beat"
        return beat.is_file() and (time.time() - beat.stat().st_mtime) < 30.0
    except OSError:
        return False


def _stop_keeper(inbox: str) -> None:
    """Only ever after the session it served is already dead. The keeper notices on its own within
    two polls; removing the mailbox just means no message written a moment too late is delivered
    into a session that no longer exists."""
    if not inbox:
        return
    try:
        shutil.rmtree(inbox, ignore_errors=True)
    except Exception:
        pass


def sweep_keeper_mail() -> int:
    """Drop mailboxes no registered session refers to. Called at startup, after adoption, so a
    crash cannot leave the live folder filling up with dead sessions' mail."""
    keep = {str(r.get("inbox") or "") for r in _read_registry()}
    gone = 0
    try:
        for d in LIVE_DIR.glob("*.inbox"):
            if str(d) in keep:
                continue
            if keeper_alive(str(d)):      # a keeper we have no record of is still a live session
                continue
            shutil.rmtree(d, ignore_errors=True)
            gone += 1
    except OSError:
        pass
    return gone


def _spawn_live(project_id: str, args: list[str], cwd: str, sig: tuple) -> "_Live":
    # A /btw note still pending from the OLD process must survive the respawn (it used to be
    # deleted here and lost). Claim it now, re-deliver it into the new session below.
    carried_btw = _claim_pending_btw(project_id)
    node_cmd = _resolve_node_shim(args[0])
    if node_cmd:
        args = [*node_cmd, *args[1:]]
    logf = open(SESS_DIR / f"{project_id}.log", "wb", buffering=0)
    flags = 0
    if os.name == "nt":
        # A new process group as well, so a console signal aimed at the backend does not travel
        # down to a session that is meant to outlive it.
        flags = (subprocess.CREATE_NO_WINDOW |            # type: ignore[attr-defined]
                 subprocess.CREATE_NEW_PROCESS_GROUP)      # type: ignore[attr-defined]
    # Private marker so adopt_orphans() can recognise OUR live streams (and never the user's IDE
    # or hand-run `claude`) and reap ones left behind by a crashed/killed previous backend.
    # --append-subagent-system-prompt is gated behind this flag in the CLI; without it the
    # subagent instruction is silently ignored.
    env = {**os.environ, "ASSET_STUDIO_CC": "1", "CLAUDE_CODE_ENABLE_APPEND_SUBAGENT_PROMPT": "1"}
    # A forge build through the Studio's MCP tool can take minutes; the CLI's own tool timeout
    # would cut it off first. Only when the MCP server is on, and never over the user's own value.
    if _mcp_on() and "MCP_TOOL_TIMEOUT" not in env:
        env["MCP_TOOL_TIMEOUT"] = "900000"
    # The phase tools (TaskCreate/TaskUpdate) ship to Haiku only by default — measured on 2.1.235,
    # an Opus 5 or Sonnet 5 session is handed no phase tool of any kind, which is why the Phases
    # panel sat empty on every workspace. This flag restores them for every model (verified: opus
    # goes 29 tools -> 33 with TaskCreate present). Off by settings.cc_phases, same switch as the
    # note, so one toggle governs the whole feature.
    if settings.get("cc_phases", True):
        env["CLAUDE_CODE_ENABLE_TODO_TOOLS"] = "1"
    alt = mission.alt_prefix(project_id)
    if alt:
        env.update(_alt_env(alt, str(sig[2]) if len(sig) > 2 else "default"))
    else:
        env.update(_claude_context_env())
    # stdout goes to the LOG, not a pipe. A pipe dies with this process and takes the session
    # with it; a file does not, so the session survives a restart or a crash and the next backend
    # picks the tail up where this one stopped.
    #
    # stdin is the same problem in the other direction, and it is the one that was still open: a
    # stream-json session exits ONE SECOND after its stdin closes, so while the backend held the
    # write end, stopping the backend ended the session. The keeper holds it now.
    r_fd, inbox, keeper = _start_keeper(project_id)

    def _start(extra: int):
        return subprocess.Popen(
            args, cwd=cwd, env=env,
            stdin=(r_fd if r_fd is not None else subprocess.PIPE), stdout=logf,
            stderr=subprocess.STDOUT, creationflags=flags | extra, bufsize=0,
        )

    try:
        try:
            proc = _start(_BREAKAWAY)
        except OSError:
            proc = _start(0)          # a job object forbade breakaway; still better than a pipe
    except Exception:
        if keeper is not None:        # no session to serve -> the keeper has no reason to live
            try:
                keeper.terminate()
            except Exception:
                pass
        if r_fd is not None:
            try:
                os.close(r_fd)
            except OSError:
                pass
        raise
    if r_fd is not None:
        try:
            os.close(r_fd)            # the CLI owns the read end now; nobody else may
        except OSError:
            pass
    live = _Live(project_id, proc, logf, sig, None)
    if keeper is not None and inbox is not None:
        live.inbox = str(inbox)
        live.keeper_pid = keeper.pid
        # The keeper forwards nothing until it is told which process it serves, and gives up if it
        # is never told - so a backend that dies mid-spawn leaves no stray keeper behind.
        try:
            (inbox / "child.pid").write_text(str(proc.pid), encoding="utf-8")
        except OSError:
            pass
    _register(live, cwd)
    threading.Thread(target=_live_reader, args=(live,), daemon=True).start()
    _start_reaper()
    if carried_btw:
        # first thing the new session sees — the note the old process never got to deliver
        if not _write_msg(live, _btw_wrap(carried_btw)):
            # the fresh process died before it could take it → put the note BACK so the next
            # spawn (or the next turn's hook) still delivers it. A note is never dropped silently.
            try:
                live_notes.put(_btw_note_file(project_id), carried_btw)
            except OSError:
                pass
    return live


def _is_compact_cmd(message: str) -> bool:
    """The studio runs /compact by sending it as a normal message, so a turn whose prompt is
    /compact is a compaction — flag it so the UI can show 'Compacting…' (it can take minutes)."""
    return (message or "").strip().lower().startswith("/compact")


def _write_msg(live: "_Live", text: str, compact: bool = False) -> bool:
    """Queue a user message for the live session. False means the SESSION IS GONE.

    Nothing else may return False, and the difference is the whole point. The caller treats False
    as "respawn", a respawn kills the process, and background agents live inside the process — so
    a slow keeper reported as a dead session costs the user their agents. A message handed to the
    keeper is a file on disk: if it is picked up late it is still delivered, and it survives this
    backend entirely.
    """
    payload = {"type": "user", "message": {"role": "user",
               "content": [{"type": "text", "text": text}]}}
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with live.lock:
            if not live.alive or live.proc.poll() is not None:
                live.alive = False
                return False
            inbox = live.inbox
            if not inbox and live.proc.stdin is None:
                # An inherited session from before the keeper existed: alive, watchable, but with
                # nobody holding its stdin. Say so honestly rather than marking a running process
                # dead — the caller decides, and it knows about the agents.
                return False
        if inbox:
            # Outside the lock: delivery can wait on another process, and the reader thread needs
            # this lock to keep the feed moving while it does.
            if not _inbox_put(live, data):
                with live.lock:
                    live.alive = False
                return False
        else:
            with live.lock:
                if not live.alive or live.proc.stdin is None or live.proc.poll() is not None:
                    live.alive = False
                    return False
                live.proc.stdin.write(data)
                live.proc.stdin.flush()
        with live.lock:
            live.outstanding += 1
            live.last_write = time.time()
            if not live.turn_active:                 # starting a turn -> begin the live timer NOW,
                live.turn_started = live.last_write   # so "Thinking... 3s" shows during the pre-output
                live.activity = "Thinking"            # load phase, before any tokens stream
                live.turn_committed = 0
                live.gen_s = 0.0
                live.msg_started = 0.0
                live.turn_model = ""
                live.turn_msgs = 0
                live.cur_tokens = 0
                live.cur_chars = 0
                live.cur_block_base = 0
                live.cur_text = ""
                live.cur_kind = ""
                live.compacting = compact             # show "Compacting..." for the whole /compact turn
        _publish_live(live, force=True)   # turn start ("Thinking...") shows the instant it is queued
        return True
    except Exception:
        with live.lock:
            live.alive = live.proc.poll() is None     # only a gone process is a dead session
        return False


def _stream_busy(live: "_Live") -> bool:
    """Busy iff a turn is producing output, OR a message was written after the last
    `result` (a turn is pending/in-progress). Self-clearing: when the result arrives,
    last_result advances past last_write so it reads idle — no stuck 'working', while
    slow pre-output thinking still reads busy. Coalescing-proof."""
    if not live.alive or live.proc.poll() is not None:
        return False
    return live.turn_active or live.last_write > live.last_result


def _force_kill_tree(pid: int) -> None:
    """Kill a process AND its whole child tree WITHOUT depending on psutil. On Windows
    `taskkill /F /T` walks the tree natively, so the real `claude`/node workers die — not just
    the launcher. On POSIX, kill the process group. This is the guarantee that Stop actually
    stops: an install missing psutil would otherwise kill only the top process and leave Claude
    generating (and billing) in the background — which is how repeated send/stop can pile up
    several orphaned turns running in parallel."""
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
        else:
            import signal as _sig
            try:
                os.killpg(os.getpgid(pid), _sig.SIGKILL)
            except Exception:
                os.kill(pid, _sig.SIGKILL)
    except Exception:
        pass


def _kill_proc_tree(proc) -> None:
    """Best-effort clean kill via psutil (if present) ALWAYS followed by a native tree-kill,
    so the entire Claude process tree dies regardless of environment or a child-enumeration race."""
    pid = getattr(proc, "pid", 0) or 0
    try:
        import psutil
        pr = psutil.Process(pid)
        for ch in pr.children(recursive=True):
            try:
                ch.kill()
            except Exception:
                pass
        pr.kill()
    except Exception:
        pass
    _force_kill_tree(pid)            # the guarantee — native, no dependency
    try:
        proc.terminate()
    except Exception:
        pass


def _kill_live(live: "_Live") -> None:
    with live.lock:
        live.alive = False
        live.outstanding = 0
        live.turn_active = False
    proc = live.proc
    try:
        if proc and proc.poll() is None:
            _kill_proc_tree(proc)
    finally:
        try:
            if live.logf is not None:
                live.logf.close()
        except Exception:
            pass
        # The keeper exists to serve THIS process; with it gone the keeper would notice within
        # two polls anyway. Clearing the inbox now just means no message written a moment too
        # late is delivered into a session that no longer exists.
        if live.inbox:
            _stop_keeper(live.inbox)


def _has_recent_agent_activity(project_id: str, window: float = _IDLE_TIMEOUT) -> bool:
    """True if a subagent/workflow journal for this project was written within `window` seconds —
    i.e. a background fan-out (the Workflow tool, Task subagents) is STILL running even though the
    foreground chat turn ended and the stream went quiet. The reaper and the settings-respawn use
    this so long background work is never killed just because the chat looks idle. Cheap in the
    common case: it's only consulted for a process that ALREADY tripped the 15-min idle check.

    The window matches the idle timeout on purpose. It was 300s while the timeout was 900s, so a
    session that had been quiet for fifteen minutes was reaped if its agents had been quiet for
    six — and a background agent that thinks for six minutes is not a stopped one. The two
    numbers now agree, so the guard covers the whole period it is guarding."""
    cutoff = time.time() - window

    def _fresh(root: Path, pattern: str) -> bool:
        try:
            if not root.is_dir():
                return False
            for f in root.rglob(pattern):
                try:
                    if f.is_file() and f.stat().st_mtime >= cutoff:
                        return True
                except OSError:
                    continue
        except Exception:
            pass
        return False

    # 1) subagent + Workflow journals live beside the transcript
    try:
        from . import mission
        _root, pdir, _bare = mission.project_dir(project_id)
        if pdir.exists():
            for sd in pdir.iterdir():
                if sd.is_dir() and sd.name != "memory" and _fresh(sd / "subagents", "*.jsonl"):
                    return True
    except Exception:
        pass
    # 2) BACKGROUND TASKS live somewhere else entirely: %TEMP%/claude/<slug>/<session>/tasks/*.output
    # A long-running background task/agent writes ONLY there — never into subagents/*.jsonl — so
    # checking (1) alone still let the reaper call the session "idle" and kill it mid-run (which is
    # how agents ended up dead with 0-byte .output files). Checked independently of (1).
    try:
        import tempfile
        if _fresh(Path(tempfile.gettempdir()) / "claude" / project_id, "*"):
            return True
    except Exception:
        pass
    return False


def _open_background(project_id: str) -> list:
    """Background agents that were dispatched and never reported an outcome — the authoritative
    "do not kill this process" signal.

    A background agent (the Agent tool, a Workflow fan-out) runs as a CHILD of the chat's own CLI
    process. So ANY respawn of that process orphans it, and an orphaned in-process child cannot be
    adopted by the replacement — only lost, with no result recorded. That is precisely how a pair
    of agents ended up "stopped, no completion record found" after a model switch.

    `subagents.open_background` reads the recorded task-notifications, so it knows an agent is still
    owed a result even when its transcript has been quiet for a while — more reliable than a file
    mtime. `_has_recent_agent_activity` (journal mtimes) is kept as the fast fallback. Either being
    positive means a respawn or reap must be DEFERRED, never forced: the settings/model change
    lands on the next send once the agents are done, which costs nothing, while killing them costs
    the whole task.
    """
    try:
        from . import subagents
        bg = subagents.open_background(project_id)
        if bg:
            return bg
    except Exception:
        pass
    if _has_recent_agent_activity(project_id):
        return [{"agent_id": "?", "description": "a background fan-out"}]
    return []


def _owed_background(project_id: str) -> list:
    """Background agents owed a result, asked STRICTLY: only a dispatch with no recorded outcome.

    The difference from `_open_background` is which way being wrong hurts. An INVOLUNTARY stop
    (the reaper, a respawn nobody asked for) should be prevented on the faintest sign of life, so
    that one uses the generous question and counts journal mtimes too. A refusal shown to the user
    is the opposite: it must never fire because an agent finished four minutes ago and its file is
    still warm. So this one asks only the question with a real answer in it.
    """
    try:
        from . import subagents
        return subagents.open_background(project_id)
    except Exception:
        return []


def _agents_block(project_id: str, what: str) -> str:
    """The sentence to refuse with, or "" when nothing is at risk.

    One wording in one place, because five paths need it and each of them, phrased separately,
    used to give the user a different account of the same event."""
    owed = _owed_background(project_id)
    if not owed:
        return ""
    names = ", ".join(str(a.get("description") or a.get("agent_id") or "")[:44] for a in owed[:3])
    more = "" if len(owed) <= 3 else " and %d more" % (len(owed) - 3)
    return ("%d background agent%s still working (%s%s). %s would stop them mid-task with no "
            "result recorded. Wait for them to report back, or stop them first."
            % (len(owed), "s" if len(owed) > 1 else "", names, more, what))


def _start_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def loop() -> None:
        n = 0
        while True:
            _beat()                       # tell every keeper a backend is still here
            time.sleep(60.0)
            n += 1
            if n % 10 == 0:               # every ten minutes, not every one: it reads /proc
                try:
                    sweep_untracked()
                except Exception:
                    pass
                # Finished phase lists out of each live session's task folder: the CLI repeats
                # that whole list in a reminder every few tool calls, and it only grew.
                for lv in list(_live.values()):
                    try:
                        mission.archive_finished_phase_runs(lv.project_id, lv.session_id or "")
                    except Exception:
                        pass
            now = time.time()
            for pid, live in list(_live.items()):
                dead = (not live.alive) or live.proc.poll() is not None
                idle = (not _stream_busy(live)) and (now - live.last_write) > _IDLE_TIMEOUT
                if dead or idle:
                    # A Workflow/subagent fan-out keeps running after the chat turn ends and never
                    # touches the stream — reaping the "idle" process would kill the whole workflow
                    # (this is what killed an 84-agent deep-research run). Keep it alive while its
                    # journals are still being written.
                    if idle and not dead and _open_background(live.project_id):
                        continue
                    with _live_lock:
                        if _live.get(pid) is live:
                            _live.pop(pid, None)
                    if not dead:
                        _kill_live(live)

    threading.Thread(target=loop, daemon=True).start()


BACKEND_BEAT = LIVE_DIR / "backend.beat"
_ORPHAN_GRACE = 1800.0        # 30 min with no backend AND no output = nobody is waiting


def _beat() -> None:
    """Say a backend is running. The keepers read this, and it is the ONLY thing that stops a
    session living for ever after the app is closed: stdin is now held outside the backend, so
    nothing else would ever close it."""
    try:
        BACKEND_BEAT.write_text("%.0f" % time.time(), encoding="utf-8")
    except OSError:
        pass


def sweep_untracked() -> int:
    """Stop a live CLI that no backend is tracking and nothing is waiting on.

    Deliberately hard to satisfy, because being wrong here is the exact mistake this whole file
    is about. All four must hold: it carries our marker, no `_live` entry claims it, no registry
    record claims it (another backend might), no background agent is owed a result, and its log
    has not been written for longer than the idle timeout."""
    try:
        procs = _our_live_processes()
    except Exception:
        return 0
    if not procs:
        return 0
    mine = {getattr(l.proc, "pid", 0) for l in _live.values()}
    claimed = {int(r.get("pid") or 0) for r in _read_registry()}
    now = time.time()
    stopped = 0
    for pr in procs:
        pid = int(pr.get("pid") or 0)
        if pid in mine or pid in claimed:
            continue
        proj = _project_of_cmdline(str(pr.get("cmdline") or ""))
        if not proj:
            continue                 # cannot say whose it is, so cannot say it is finished
        if _open_background(proj):
            continue
        # Positive evidence of staleness, not the absence of evidence. Falling through to the
        # kill when the log could not be read would have made an unreadable file a death
        # sentence, which is the opposite of how every other decision in this file is made.
        try:
            log = SESS_DIR / ("%s.log" % proj)
            if not log.exists() or (now - log.stat().st_mtime) < _IDLE_TIMEOUT:
                continue
        except OSError:
            continue
        _force_kill_tree(pid)
        stopped += 1
    return stopped


def shutdown_all() -> None:
    """Let go of the live sessions as the backend stops.

    A session that is mid-turn, or that has a subagent fan-out still running, is LEFT RUNNING on
    purpose. It was spawned detached, its output goes to a log file rather than to a pipe of
    ours, and its registry entry lets the next backend adopt it — so a restart costs nothing
    instead of killing the work. Closing every session here undid all of that, and it is why a
    restart used to stop the agents: the survival machinery only ever protected against a crash,
    while an orderly shutdown still shot them.

    NOTHING IS KILLED HERE ANY MORE. Not even a session that looks idle.

    It used to close the ones that read idle, on the reasoning that an idle session has nothing
    in flight to lose. The reasoning was sound and the READING was not. "Idle" is
    `_stream_busy`, which is false in several states that are not idle at all: a parent waiting
    on background agents writes nothing to the stream; a message can be queued and not yet
    started; and an ADOPTED session reads its predecessor's final `result` out of the log, which
    moves `last_result` past `last_write` and makes a perfectly live session look finished. The
    only guard was `_has_recent_agent_activity`, and a background agent that thinks for longer
    than its window loses even that.

    Each wrong reading cost a real conversation, because `_kill_live` is a TREE kill and the
    CLI's background agents live inside the process it kills. When a supervisor loop restarted
    the backend thirty-six times in a row, the reading only had to be wrong once.

    So the rule is now unconditional and needs no reading at all: THE BACKEND STOPPING NEVER
    STOPS A SESSION. Every one is checkpointed — the next backend resumes tailing exactly where
    this one stopped — and left running.

    Idle CLIs still get collected: the 15-minute reaper takes them while a backend is up, and the
    next start-up adopts them so they are managed rather than orphaned. The worst case is an idle
    process living until the app is opened again, which costs some memory. Being wrong the other
    way costs the user their work.
    """
    for _pid, live in list(_live.items()):
        try:
            _reg_checkpoint(live)             # where the next backend picks the log up
        except Exception:                     # never let a shutdown hang on a bad checkpoint
            continue


def _classify_orphans(procs: list, reg: list) -> tuple:
    """(adopt, kill) for a set of candidate processes and registry entries.

    Pure on purpose: the real thing enumerates every process on the machine, so the decision it
    makes has to be testable without one real session anywhere near it.

    A process we can match to a registry entry is a session THIS backend can take over. Anything
    else carrying our marker is an untracked duplicate, and a duplicate stream on one project
    doubles its API calls -- which is exactly what burned the rate limit and broke a workspace
    after a restart. So the old protection is kept in full; what changes is that a session we can
    identify is resumed rather than killed."""
    by_pid = {int(r.get("pid") or 0): r for r in reg if r.get("pid")}
    adopt, kill = [], []
    unmatched = []
    taken: set = set()
    for p in procs:
        pid = int(p.get("pid") or 0)
        r = by_pid.get(pid)
        if r and p.get("marker"):
            adopt.append((p, r))
            taken.add(str(r.get("project_id") or ""))
        else:
            unmatched.append(p)

    # A process we could not match is not automatically a duplicate. It used to be treated as
    # one and tree-killed, which meant that losing a registry record — a file write that did not
    # land, a backend that died between spawn and register, competing backends rewriting it
    # during a restart loop — ended somebody's running agents. The rate-limit protection is only
    # needed for a REAL duplicate: a second stream on a project whose session we have already
    # adopted. Anything else is left running, and the next checkpoint picks it back up.
    for p in unmatched:
        proj = _project_of_cmdline(str(p.get("cmdline") or ""))
        if proj and proj in taken:
            kill.append(p)
    return adopt, kill


_SETTINGS_RE = re.compile(r"[\\/]btw[\\/]([^\\/\"]+)\.settings\.json")


def _project_of_cmdline(cmdline: str) -> str:
    """Which project a live stream belongs to, read off its own command line.

    Every session is launched with `--settings <data>/btw/<project_id>.settings.json`, so the id
    is there even when our bookkeeping for it is gone. That is what lets a duplicate be told from
    a session we merely lost track of.
    """
    m = _SETTINGS_RE.search(cmdline or "")
    return m.group(1) if m else ""


def _read_registry() -> list:
    out = []
    try:
        files = list(LIVE_DIR.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            try:
                f.unlink()
            except OSError:
                pass
            continue
        if isinstance(d, dict) and d.get("project_id"):
            out.append(d)
    return out


def _our_live_processes() -> list:
    """Every claude live-stream process started by a Studio backend -- ours or a previous one.

    Matched on the private env marker so the user's own IDE or a hand-run `claude` is never a
    candidate, which is the invariant that made reaping safe in the first place."""
    found = []
    try:
        import psutil
    except Exception:
        return found
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.pid == me:
                continue
            cl = " ".join(p.info.get("cmdline") or [])
            if "stream-json" not in cl:        # only the persistent live-stream processes
                continue
            found.append({"pid": p.pid, "cmdline": cl,
                          "marker": p.environ().get("ASSET_STUDIO_CC") == "1"})
        except Exception:
            continue
    return [p for p in found if p["marker"]]


def _adopt_one(rec: dict) -> bool:
    """Rebuild a _Live around a process we inherited, and resume tailing its log."""
    pid = int(rec.get("pid") or 0)
    project_id = str(rec.get("project_id") or "")
    if not pid or not project_id:
        return False
    live = _Live(project_id, _Adopted(pid), None, tuple(rec.get("sig") or ()),
                 rec.get("session_id") or None)
    log = rec.get("log")
    if log:
        live.log_path = Path(log)
    # Resume from the last completed turn, not from byte zero: replaying finished turns would
    # re-run their side effects, and starting at the end would lose whatever the old backend
    # never got to read.
    live.read_from = int(rec.get("offset") or 0)
    live.adopted = True
    # THE POINT OF THE KEEPER. Before it, an adopted session could be watched and stopped but not
    # written to, so the next message respawned the process -- and a background agent lives INSIDE
    # the process. The inbox belongs to the SESSION, not to whichever backend started it, so an
    # inherited session takes messages exactly like one this backend launched itself.
    live.inbox = str(rec.get("inbox") or "")
    live.keeper_pid = int(rec.get("keeper_pid") or 0)
    if live.inbox and not keeper_alive(live.inbox):
        live.inbox = ""          # no keeper: a write there would go nowhere and look like it worked
    with _live_lock:
        _live[project_id] = live
    threading.Thread(target=_live_reader, args=(live,), daemon=True).start()
    _start_reaper()
    return True


def adopt_orphans() -> dict:
    """Take over live sessions a previous backend left running; kill only what cannot be taken.

    Called once on startup, before any new session is spawned. Returns a small report so the
    startup log says what actually happened rather than a bare count."""
    reg = _read_registry()
    procs = _our_live_processes()
    adopt, kill = _classify_orphans(procs, reg)

    adopted = 0
    for _p, rec in adopt:
        try:
            if _adopt_one(rec):
                adopted += 1
        except Exception:
            continue

    killed = 0
    for p in kill:
        try:
            _force_kill_tree(int(p["pid"]))
            killed += 1
        except Exception:
            continue

    # Registry entries whose process is gone are stale bookkeeping, not sessions.
    stale = 0
    live_pids = {int(p["pid"]) for p in procs}
    for rec in reg:
        if int(rec.get("pid") or 0) not in live_pids:
            _unregister(str(rec.get("project_id") or ""))
            stale += 1

    if adopted or killed:
        print(f"[cc_session] adopted {adopted} live session(s) from a previous backend, "
              f"killed {killed} untracked, cleared {stale} stale record(s)")
    return {"adopted": adopted, "killed": killed, "stale": stale}


def cleanup_orphans() -> int:
    """Kept for callers that predate adoption; the count is what they expected."""
    return adopt_orphans()["killed"]


# Marker prepended to a message that arrives WHILE Claude is mid-turn. When Claude
# consumes it at the next step boundary it knows the user is steering/correcting and
# should adjust course instead of blindly finishing the old plan. The frontend strips
# this leading line from the visible bubble so the user's transcript stays clean —
# keep STEER_PREFIX byte-identical with the copy in SessionFeed.tsx.
STEER_PREFIX = "↪ Steering update (sent while you were working)"


def _steer_wrap(message: str) -> str:
    note = (f"{STEER_PREFIX} — if it corrects or refines my previous request, adjust "
            "course now and prioritize it; otherwise fold it into what you're doing.")
    return note + "\n\n" + message


# "/btw <note>" — a live SIDE-NOTE sent while Claude works. Unlike a bare mid-turn message (treated
# as a course-correction above), this is FYI/awareness: keep the main task going, just be aware.
# Frontend strips this leading line from the visible bubble — keep BTW_PREFIX byte-identical there.
BTW_PREFIX = "↪ Side-note (by the way — sent while you work)"


def _btw_wrap(message: str) -> str:
    # Mailbox entries already carry their own framing, including steering updates.
    if message.startswith((STEER_PREFIX, BTW_PREFIX)):
        return message
    note = (f"{BTW_PREFIX} — FYI/awareness while you keep going. Note it, and answer briefly if it's "
            "a question, but do NOT drop, restart, or reprioritize the main task unless this directly "
            "blocks it.")
    return note + "\n\n" + message


# --- LIVE /btw delivery (hook injection) -------------------------------------
# Writing to the CLI's stdin mid-turn only QUEUES for the NEXT turn. To make /btw visible
# WHILE Claude works, each session is spawned with --settings registering a PostToolUse
# hook: after every tool call it checks a per-project note file and, if present, injects
# the note into the RUNNING turn as additionalContext (btw_hook.py). Cost with no note:
# one `cmd /c if exist` per tool call — no Python spawn.
_BTW_DIR = DATA_DIR / "btw"
_MCP_DIR = DATA_DIR / "mcp"


def _mcp_on() -> bool:
    """Settings -> Studio engine -> "Studio tools over MCP". On unless switched off."""
    return settings.get("cc_mcp", True) is not False


def _mcp_config_file(project_id: str, cwd: str = "") -> Path:
    """(Re)write the --mcp-config file naming the Studio's own stdio server for one project.

    The server is a plain script under this backend's Python (standard library only), told where
    the backend is and which folder the session works in. What it lists is decided by the backend
    at /api/mcp/catalog, with the same switches as the notes, so this file never changes shape."""
    _MCP_DIR.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parent / "mcp_engine.py"
    cfg = {"mcpServers": {"studio": {
        "type": "stdio", "command": sys.executable, "args": [str(script)],
        "env": {"STUDIO_BASE": _base_url(), "STUDIO_CWD": str(cwd or ""), "PYTHONIOENCODING": "utf-8"},
    }}}
    path = _MCP_DIR / f"{project_id}.json"
    text = json.dumps(cfg, indent=1)
    try:
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    fsutil.replace(tmp, path)
    return path


def _btw_note_file(project_id: str) -> Path:
    return _BTW_DIR / f"{project_id}.note"


def _btw_settings_file(project_id: str) -> Path:
    """(Re)write the per-project session settings JSON wiring the mid-turn hook."""
    _BTW_DIR.mkdir(parents=True, exist_ok=True)
    note = _btw_note_file(project_id)
    hook_py = Path(__file__).resolve().parent / "btw_hook.py"
    # direct python invocation, shell-agnostic (a `cmd /c if exist` wrapper broke under the
    # CLI's hook runner); matcher must be "" — "*" is parsed as a REGEX and never matches
    cmd = f'"{sys.executable}" "{hook_py}" "{note}"'
    # BOTH events. PostToolUse alone means a note written while the model decides its next action
    # waits out that whole action (measured: 9.1s, note already on disk 2.1s before the tool began)
    # — so a "stop, do it this way" note only lands AFTER the thing it was meant to prevent.
    # PreToolUse delivers it first instead. btw_hook.py claims the note file with an atomic
    # os.replace, so exactly one of the two boundaries can ever win it.
    entry = [{"matcher": "", "hooks": [{"type": "command", "command": cmd, "timeout": 20}]}]
    pre = list(entry)
    # Grep for a SYMBOL → hand back what the graph already knows, before the grep runs. This is
    # the enforcement the system-prompt note could not provide: the note is advice the model may
    # skip, this is executed by the harness. It never blocks — the graph misses local closures,
    # CSS and dynamic dispatch, where grep is the correct tool — and it stays silent unless it
    # has a real answer. Subagents fire hooks too, so they get it as well.
    # Registered when EITHER is on. The hook carries three guards that are independent of each
    # other — the code graph, the browser-massacre refusal, and the blocked-page hint — and
    # gating the whole process on graphify alone silently disabled the other two.
    if settings.get("cc_graphify") or web_tools.enabled():
        gh = Path(__file__).resolve().parent / "graph_hook.py"
        # Bash is in the matcher because leaving it out silently disabled the whole thing: a
        # session told to search with `grep` in Bash made 283 shell calls and 0 Grep calls, so
        # the hook fired zero times while looking perfectly installed. The hook returns in
        # microseconds for a command that is not a search, so the cost is one process spawn.
        pre.append({"matcher": "Grep|Read|Glob|Bash|PowerShell|WebFetch|WebSearch", "hooks": [
            {"type": "command", "command": f'"{sys.executable}" "{gh}"', "timeout": 8}]})
    cfg: dict = {"hooks": {"PreToolUse": pre, "PostToolUse": entry}}
    # Compaction and phase events. Both answer a question the transcript answers too late: after a
    # /compact its newest assistant turn is still the PRE-compact one, and the task store's files
    # are rewritten on every status change so their timestamps cannot say when a run began. These
    # fire a handful of times per session, not per tool call, so the cost is nothing.
    sh = Path(__file__).resolve().parent / "session_hook.py"
    scmd = f'"{sys.executable}" "{sh}" "{hook_events.file_for(project_id)}"'
    shook = [{"matcher": "", "hooks": [{"type": "command", "command": scmd, "timeout": 10}]}]
    # InstructionsLoaded names every CLAUDE.md and memory file the session took on. See
    # session_hook._EVENTS for why the two Worktree events are deliberately NOT in this list.
    for ev in ("PreCompact", "PostCompact", "TaskCreated", "TaskCompleted", "InstructionsLoaded"):
        cfg["hooks"][ev] = shook
    # Fast mode. A headless/SDK session is refused with "sdk_opt_in_required" unless
    # flagSettings.fastMode is set, and BOTH keys are needed — verified against the CLI:
    # flagSettings alone still reports off, the pair reports fast_mode_state="on".
    if settings.get("cc_fast_mode"):
        cfg["flagSettings"] = {"fastMode": True}
        cfg["fastMode"] = True
    # BOOST: a long command output stays out of the context. The CLI's `bashOutputMaxChars`
    # (default 30000, clamped 4000-128000) saves everything past the limit to a FILE and gives the
    # model a preview plus the path, so nothing is lost — the agent reads the rest only if it needs
    # it. A test run or a build log is the biggest thing re-sent on every later turn, and this sits
    # below the cache breakpoint, so it cannot move the cached prefix. Applies from the next launch.
    try:
        from . import boost as _boost
        n = _boost.bash_output_chars()
        if n:
            cfg["bashOutputMaxChars"] = n
    except Exception:
        pass
    sf = _BTW_DIR / f"{project_id}.settings.json"
    sf.write_text(json.dumps(cfg), encoding="utf-8")
    return sf


_BTW_SWEEP_SECS = 20.0
_btw_sweeper: Optional[threading.Thread] = None


def start_btw_sweeper() -> None:
    """Last line of defence so a /btw is never lost.

    The hook delivers it at the next tool boundary, and the turn-end fallback catches a turn that
    finished without one. Neither survives a BACKEND restart mid-turn: the note file is then left
    with nobody watching, and it waits for the user's next message — which reads as "my note
    vanished". This sweeps every _BTW_SWEEP_SECS and delivers any note left sitting on an IDLE
    session. It claims the file with the same atomic os.replace btw_hook.py uses, so the hook and
    the sweeper can never both send the same note."""
    global _btw_sweeper
    if _btw_sweeper is not None:
        return

    def loop() -> None:
        while True:
            time.sleep(_BTW_SWEEP_SECS)
            try:
                _btw_sweep_once()
            except Exception:  # noqa: BLE001 — a sweeper must never die
                pass

    _btw_sweeper = threading.Thread(target=loop, name="btw-sweeper", daemon=True)
    _btw_sweeper.start()


def _btw_sweep_once() -> None:
    if not _BTW_DIR.exists():
        return
    now = time.time()
    paths = set(_BTW_DIR.glob("*.note"))
    paths.update(Path(str(p)[:-2]) for p in _BTW_DIR.glob("*.note.d"))
    for nf in paths:
        try:
            mailbox = Path(str(nf) + ".d")
            if now - (mailbox if mailbox.exists() else nf).stat().st_mtime < _BTW_SWEEP_SECS:
                continue                      # just written — let the hook have it first
        except OSError:
            continue
        project_id = nf.name[:-len(".note")]  # not .stem: a project id may contain a dot
        with _live_lock:
            live = _live.get(project_id)
        if live is None or not live.alive or live.proc.poll() is not None:
            continue                          # no session: _claim_pending_btw carries it on respawn
        if _stream_busy(live):
            continue                          # a turn owns it (hook / turn-end fallback)
        try:
            note = live_notes.claim(nf)
        except OSError:
            continue
        if not note:
            continue
        if _write_msg(live, _btw_wrap(note)):
            with live.lock:
                live.btw_pending = False
        else:
            try:                              # write failed — put it back, never drop it
                live_notes.put(nf, note)
            except OSError:
                pass


def _claude_billed(project_id: str = "") -> bool:
    """Include API-backed sessions in the pay-as-you-go estimate.

    OAuth plan turns retain their comparison price separately. This is a local estimate,
    not an invoice or a measurement of subscription extra-usage charges.
    """
    if mission.alt_prefix(project_id):
        return True
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                      "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                                      "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
        return True
    try:
        from . import usage
        return not usage.on_subscription()
    except Exception:
        return True


def _note_sig() -> tuple:
    """Every setting that decides WHICH notes go into the appended system prompt.

    The rule, and the reason this is a list rather than a hash of the prompt: the appended prompt
    is fixed for the life of the CLI process, so a switch that changes it has to respawn the
    session or it does nothing until something else happens to respawn. Turning the forge off, for
    instance, stopped the endpoint at once — but a session already running still carried the
    paragraph telling it the forge existed, so it would try, be refused, and waste a call.

    The prompt is NOT hashed, deliberately. The phases note carries the live phase list, which
    changes several times a minute; hashing it would respawn the session constantly. What is
    stable is the set of switches, so that is what is compared.
    """
    keys = ("cc_forge", "cc_live", "cc_ops", "cc_review", "cc_graphify", "cc_web_tools",
            "cc_phases", "cc_memory", "cc_blender_kiln", "cc_fable_efficient",
            "studio_tools_prompt", "cc_1m")
    # BOOST IS NOT IN THIS LIST, ON PURPOSE, and it used to be. Its note is a set of HABITS
    # ("use the local index, do not re-read a file"), not a capability: a live session that never
    # hears it loses a saving and nothing else. Forcing a respawn to deliver it did the opposite
    # of what the button promises — a respawn re-sends the whole conversation under a NEW request
    # prefix, which is the cache READ at ~0.1x turning into a cache WRITE at ~1.25x, and on a long
    # session that one line costs more than every token the note could ever save. So the directive
    # rides the next natural respawn (new conversation, model change, idle reap) and BOOST itself
    # never forces one. The switches BOOST moves are in boost._PROFILE, which is now made only of
    # keys that are not in this tuple either.
    return tuple(bool(settings.get(k, True)) for k in keys)


def _send_streaming(project_id: str, message: str, cwd: str, model: str,
                    permission_mode: str, fork: bool, effort: str, exe: str,
                    new_session: bool, session: str, newest_session: Optional[str], steer: bool = False) -> dict:
    want_sig = (exe, cwd, model or "default", permission_mode or "default",
                bool(fork), (effort or "default").lower(), bool(settings.get("cc_autolearn")),
                # the style rides the system prompt, which is fixed for the life of the process —
                # so changing it has to respawn (at the next idle send, never mid-turn)
                (settings.get("cc_output_style") or "").strip(),
                # fast mode is read from the settings file at startup, so the same applies:
                # without this the toggle would appear to do nothing until something else respawned
                bool(settings.get("cc_fast_mode")),
                # ...and so does every switch that adds or removes a note.
                _note_sig())
    # /compact INTERRUPTS THE TURN, AND A BACKGROUND AGENT DOES NOT SURVIVE THAT.
    #
    # Compaction is not a quiet bookkeeping step: the CLI stops what it is doing to make room.
    # Any background agent dispatched in that turn is interrupted with it, and — because an
    # interrupt leaves no transcript marker — no completion record is ever written. The next
    # session can then only report it as "stopped, no completion record found", which is exactly
    # what happened here: two agents, both ending `[Request interrupted by user]` in the same
    # second, with their work half done.
    #
    # Nothing else in this backend interrupts a session, so this is the one place it can be
    # prevented. Refuse while background agents are open and say which ones.
    if _is_compact_cmd(message) and not new_session:
        current = _live.get(project_id)
        if current is not None and current.alive and current.proc.poll() is None and _stream_busy(current):
            return {"ok": False, "busy": True, "error": "A turn is still running. Wait for it to finish before compacting."}
        blocked = _agents_block(project_id, "/compact interrupts the current turn, which")
        if blocked:
            return {"ok": False, "error": blocked}

    # STARTING A NEW CONVERSATION, OR SWITCHING TO ANOTHER ONE, REPLACES THE PROCESS.
    #
    # Everything else on this path was made to defer instead of kill - a model change waits for
    # the next send, the reaper skips a session with an agent open. These two cannot defer,
    # because the user asked for a different conversation and there is only one process per
    # project. So they refuse and say what is at stake, exactly as /compact does. Refusing is
    # recoverable; the agents are not.
    if (new_session or (session and session != getattr(_live.get(project_id), "session_id", ""))):
        blocked = _agents_block(project_id, "Changing conversation restarts the session, which")
        if blocked and _live.get(project_id) is not None:
            return {"ok": False, "error": blocked}
        # ...and a RUNNING TURN is not a thing to throw away silently either. There is one process
        # per project and engine, so with two panes on one folder, "new chat" in the second pane
        # killed the first pane's answer mid-turn, with no word in either pane. Stopping is one
        # click and says what it does; this refuses and points at it.
        cur = _live.get(project_id)
        if cur is not None and cur.alive and cur.proc.poll() is None and _stream_busy(cur):
            return {"ok": False, "busy": True, "error": (
                "A turn is still running in this project's current conversation (maybe in another "
                "pane). Starting or switching conversation would kill it. Press Stop first, or "
                "wait for it to finish.")}

    spawned = False
    resume: Optional[str] = None
    with _live_lock:
        live = _live.get(project_id)
        if live is not None and (not live.alive or live.proc.poll() is not None):
            _live.pop(project_id, None)
            live = None
        if steer and (live is None or not _stream_busy(live)):
            return {"ok": False, "error": "The turn has finished. Use Send to start another turn."}
        # WHY the process is being replaced, so the send can say so. A SIG change re-sends the whole
        # conversation under a DIFFERENT request prefix, which is a prompt-cache miss and bills at
        # cache-write instead of cache-read — 5x to 50x on the line a long agent spends most of its
        # money. `_note_sig` and the note switches are in `want_sig`, so toggling a helper mid-chat
        # does it too. Nothing on screen used to mention that.
        sig_changed = bool(live is not None and live.sig != want_sig)
        need_new = (
            live is None
            or new_session
            or (bool(session) and session != (live.session_id or ""))
            # a settings (sig) change respawns — but NOT mid-turn, so an in-progress answer
            # is never killed (the new settings apply on the next idle send). Also NOT while a
            # background workflow/subagent fan-out is running — respawning would kill it.
            # A settings or MODEL change respawns — but never mid-turn, never while a background
            # fan-out is running, and never while a background agent is still owed a result. The
            # last of those is the one that bit: switching the chat model killed the process that
            # hosted two background agents. The new model simply applies on the next send once
            # they finish; the change is deferred, not lost.
            or (sig_changed and not _stream_busy(live)
                and not _has_recent_agent_activity(project_id)
                and not _open_background(project_id))
        )
        if need_new:
            if new_session:
                resume = None
            elif session:
                resume = session
            elif live is not None and live.session_id:
                resume = live.session_id
            else:
                resume = newest_session
            # BUILT AND CHECKED BEFORE THE OLD PROCESS IS STOPPED. A launch that cannot succeed
            # must not cost the session it was going to replace; see _launch_problem.
            args = _claude_stream_args(exe, resume, model, permission_mode, fork, effort, project_id, cwd)
            problem = _launch_problem(args)
            if problem:
                return {"ok": False, "error": problem}
            if live is not None:
                _kill_live(live)
                _live.pop(project_id, None)
            _stop_shared(project_id)          # another engine on these very transcripts
            # ...and a terminal still holding this conversation. It outlives its pane on
            # purpose, so leaving the pane WITHOUT pressing "Back to chat" would otherwise
            # leave two processes appending to one transcript.
            try:
                from . import terminal as _term
                _term.close_for_project(project_id)
            except Exception:
                pass
            try:
                live = _spawn_live(project_id, args, cwd, want_sig)
            except Exception as e:
                return {"ok": False, "error": f"failed to launch claude: {e}"}
            _live[project_id] = live
            _last_agent[project_id] = "claude"
            spawned = True

    # "/btw <note>" = a live side-note (see it while working, keep the main task going). Detect and
    # strip the prefix here so both the steering and idle paths carry the clean text.
    ms = message.strip()
    btw = ms.lower().startswith("/btw") and (len(ms) == 4 or ms[4] in " \t")
    core = ms[4:].lstrip() if btw else message
    # If this lands while a turn is already running, inject it live: /btw uses the soft "just be aware,
    # keep going" framing; a bare mid-turn message uses the "adjust course / prioritize" one.
    steering = (not spawned) and _stream_busy(live)
    if steering and (btw or steer) and core:
        # TRUE mid-turn delivery: stdin would only queue this for the NEXT turn. Write the note
        # file instead — the session's PostToolUse hook injects it into the RUNNING turn at the
        # very next tool boundary. If the turn ends first, the reader queues it normally
        # (turn-end fallback below), so it's delivered exactly once either way.
        try:
            _BTW_DIR.mkdir(parents=True, exist_ok=True)
            nf = _btw_note_file(project_id)
            live_notes.put(nf, _steer_wrap(core) if steer else _btw_wrap(core))
            with live.lock:
                live.btw_pending = True
            return {
                "ok": True, "agent": "claude", "streams": True, "session_id": live.session_id,
                "forked": False, "model": model, "permission_mode": permission_mode,
                "pid": live.proc.pid, "cwd": cwd, "steering": True,
                "steer_live": steer, "steer_mode": "live" if steer else "",
                "btw_live": btw, "btw": btw, "btw_mode": "live" if btw else "",
                "pending": live.outstanding + (1 if live.turn_active else 0), "respawned": False,
            }
        except OSError:
            if steer:
                return {"ok": False, "agent": "claude", "error": "Could not deliver the steering update. Use Send to queue it instead."}
            pass    # can't write the note file — fall through to the queued path
    if steering:
        msg_out = _btw_wrap(core) if btw else _steer_wrap(core)
    else:
        # idle: nothing to run alongside — but a /btw is still a SIDE-NOTE, so keep its framing.
        # (Without this it arrived as an ordinary instruction and read like a fresh task, which is
        # why an idle /btw looked like it "wasn't noted".)
        msg_out = _btw_wrap(core) if btw else core
    is_compact = _is_compact_cmd(core) and not steering

    if not _write_msg(live, msg_out, compact=is_compact):
        # THE WRITE FAILED. That is not the same as "the session is dead", and treating it as if
        # it were is what used to kill the agents on a backend restart: an inherited session had
        # no reachable stdin, the write failed, and this line tree-killed a perfectly healthy
        # process - with a fan-out inside it - to make room for a replacement.
        #
        # A session with a keeper cannot reach here while it lives, because a keeper takes
        # messages from any backend. What can still reach here is one started before the keeper
        # existed. So: kill only what is already dead, and if it is alive with agents owed a
        # result, refuse rather than take the decision on the user's behalf.
        alive_now = live.proc.poll() is None
        if alive_now:
            blocked = _agents_block(project_id, "Restarting this session to deliver the message")
            if blocked:
                return {"ok": False, "error": blocked}
        old_sid = live.session_id
        resume2 = session or old_sid or newest_session
        r_args = _claude_stream_args(exe, resume2, model, permission_mode, fork, effort, project_id, cwd)
        problem = _launch_problem(r_args)
        if problem and alive_now:
            return {"ok": False, "error": problem}     # a live process is kept, not traded for nothing
        if not alive_now:
            _force_kill_tree(getattr(live.proc, "pid", 0))   # make sure a dying one cannot linger
        else:
            _kill_live(live)                                 # orderly: closes the log, drops the mail
        with _live_lock:
            if _live.get(project_id) is live:
                _live.pop(project_id, None)
            try:
                live = _spawn_live(project_id, r_args, cwd, want_sig)
            except Exception as e:
                return {"ok": False, "error": f"failed to relaunch claude: {e}"}
            _live[project_id] = live
            spawned = True
        # fresh process ⇒ nothing in-flight to steer; send the clean (prefix-stripped) message
        if not _write_msg(live, core, compact=_is_compact_cmd(core)):
            with _live_lock:
                if _live.get(project_id) is live:
                    _live.pop(project_id, None)
            return {"ok": False, "error": "claude session restarting — send again"}
        steering = False

    return {
        "ok": True, "agent": "claude", "streams": True, "session_id": live.session_id,
        "forked": bool(fork and resume), "model": model, "permission_mode": permission_mode,
        "pid": live.proc.pid, "cwd": cwd, "steering": steering,
        "steer_mode": "queued" if steer else "",
        # a /btw that went down the queued path (idle chat, or the hook file couldn't be written)
        "btw": bool(btw and core), "btw_mode": ("queued" if (btw and core) else ""),
        "pending": live.outstanding + (1 if live.turn_active else 0), "respawned": spawned,
        # "settings" means the context was re-sent because a setting changed the request prefix —
        # the one respawn the user can actually avoid, and the one nothing used to mention.
        "respawn_reason": ("settings" if (spawned and sig_changed) else ""),
    }


# --- multi-agent ("both agents in one chat") companions ----------------------
_companion_out: dict[str, dict] = {}   # project_id -> {agent_id: {"text","ts","running"}}


def set_companion(project_id: str, agent: str, text: str, running: bool) -> None:
    _companion_out.setdefault(project_id, {})[agent] = {"text": text, "ts": time.time(), "running": running}


def companion_state(project_id: str) -> dict:
    """Latest output of each co-agent (Codex/…) for this project — shown in the feed."""
    return _companion_out.get(project_id, {})


def _companion_context(project_id: str, companion_ids: list[str]) -> str:
    """The co-agents' last outputs, appended to the primary's prompt so it 'sees' them."""
    prev = companion_state(project_id)
    chunks = []
    for c in companion_ids:
        t = ((prev.get(c) or {}).get("text") or "").strip()
        if t and not t.startswith("("):
            chunks.append(f"\n\n[{c} (your co-agent) wrote last turn — take it into account]:\n{t[:4000]}")
    return "".join(chunks)


def _last_assistant_text(project_id: str) -> str:
    try:
        from . import mission
        fb = mission.project_feed(project_id, limit=40)
        for e in reversed(fb.get("lines", []) or []):
            if e.get("kind") == "text" and (e.get("text") or "").strip():
                return e["text"]
    except Exception:
        pass
    return ""


def _companion_prompt(user_message: str, peer_resp: str, comp: str) -> str:
    parts = [f"You are {comp}, collaborating with Claude in the SAME workspace and chat — you are peers "
             f"(not a fallback). The user's message:\n{user_message}\n"]
    if peer_resp:
        parts.append(f"\nClaude (your co-agent) just responded:\n---\n{peer_resp[:6000]}\n---")
    parts.append("\nReview Claude's response and the task: confirm what's correct, catch mistakes or gaps, and "
                 "add your own perspective or a better approach. Be concise and complementary — build on it, "
                 "don't just repeat it.")
    return "".join(parts)


def _run_one_companion(project_id: str, cwd: str, user_message: str, peer_resp: str,
                       comp: str, model: str, effort: str, permission_mode: str) -> None:
    from . import agents
    set_companion(project_id, comp, "(reviewing…)", running=True)
    if comp == "codex":
        # through the Codex app-server: signed-in, current models, a read-only throwaway thread
        from . import codex_app
        text = codex_app.run_once(cwd, _companion_prompt(user_message, peer_resp, comp), model, effort)
        set_companion(project_id, comp, text or "[no output]", running=False)
        return
    # An alternate engine (Kimi, Qwen, a user-added provider) IS the claude CLI, pointed at
    # another endpoint — build_args has no entry for it, so route it explicitly.
    alt = alt_agent(comp)
    env = None
    if alt:
        prefix, keyname, need_key = alt
        from . import keychain
        if need_key and not keychain.has_key(keyname):
            set_companion(project_id, comp, f"[{need_key}]", running=False)
            return
        exe = agents.detect("claude")
        env = {**os.environ, **_alt_env(prefix, model)}
    else:
        exe = agents.detect(comp)
    if not exe:
        sp = agents.spec(comp) or {}
        set_companion(project_id, comp,
                      f"[{sp.get('name', comp)} isn't installed/logged-in here. Install it to enable review: "
                      f"{sp.get('install_cmd', '')}]", running=False)
        return
    prompt = _companion_prompt(user_message, peer_resp, comp)
    if alt:
        args = [exe, "--print", "--permission-mode", permission_mode or "default"]
        # A co-agent used to get NO system-prompt notes at all, so it grepped the repo blind while
        # the primary agent had the graph. Give it the same instruction.
        if settings.get("cc_graphify"):
            args += ["--append-system-prompt", _graphify_note()]
        args += [prompt]
    else:
        # codex / gemini / cursor run a different harness with no system-prompt flag — the only
        # channel is the prompt itself, so the instruction rides in front of it.
        if settings.get("cc_graphify"):
            prompt = _graphify_note() + "\n\n---\n\n" + prompt
        args = agents.build_args(comp, exe, prompt, cwd, model=model, effort=effort,
                                 permission_mode=permission_mode)
    nf = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        # stdin MUST be closed: codex reads the prompt from stdin when one is attached
        # ("Reading additional input from stdin..."), so an inherited stdin hangs the
        # co-run until the 15-minute timeout instead of answering.
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=900,
                           stdin=subprocess.DEVNULL, creationflags=nf, env=env)
        text = (r.stdout or "").strip()
        if r.returncode != 0 and (r.stderr or "").strip():
            text = (text + "\n\n[stderr] " + r.stderr.strip()).strip()
        set_companion(project_id, comp, text or "[no output]", running=False)
    except subprocess.TimeoutExpired:
        set_companion(project_id, comp, "[timed out after 15 min]", running=False)
    except Exception as e:  # noqa: BLE001
        set_companion(project_id, comp, f"[error running {comp}: {e}]", running=False)


def _run_companions(project_id: str, cwd: str, user_message: str, companions: list[dict]) -> None:
    """After Claude finishes its turn, run each co-agent to review/complement that turn."""
    t0 = time.time()
    while not is_sending(project_id) and time.time() - t0 < 8:
        time.sleep(0.3)                       # wait for Claude's turn to actually start
    t0 = time.time()
    while is_sending(project_id) and time.time() - t0 < 900:
        time.sleep(0.5)                       # …then for it to finish
    time.sleep(1.0)                           # let the transcript flush
    peer = _last_assistant_text(project_id)
    for comp in companions:
        cid = comp.get("id")
        if not cid or cid == "claude":
            continue
        _run_one_companion(project_id, cwd, user_message, peer, cid,
                           comp.get("model", "default"), comp.get("effort", "default"),
                           comp.get("permission_mode", "default"))


def _plan_note() -> str:
    """Told to a session that starts in plan mode.

    A headless session does not get `ExitPlanMode`, so the agent cannot leave plan mode by
    itself. Without being told, it hunts for the tool, fails, and then gives the advice that
    fits a terminal -- press Shift+Tab -- which does nothing in the Studio, where the mode
    lives in the composer. One session burned four tool searches and a turn on exactly
    that."""
    return "\n".join((
        "PLAN MODE IS ON, and you cannot turn it off from in here.",
        "There is no ExitPlanMode tool in this session -- do not search for it, and do not "
        "tell the user to press Shift+Tab. That is terminal advice and this is not a "
        "terminal.",
        "Every write is refused while this lasts: Edit, Write, NotebookEdit, and any Bash "
        "that changes a file. Reading, searching and read-only commands all work.",
        "So: research the task, write the phase list, and set out the plan you would carry "
        "out. Then stop, and say plainly that the plan is ready and that the user leaves "
        "plan mode with the mode control under the chat box, next to the send button. The "
        "change takes effect on the next message. Do not start the work until it has.",
    ))


def forced_mode(mode: str, agent: str = "claude") -> str:
    """Plan mode, when Settings says every session must start there.

    Applied in the backend rather than the composer because a message can arrive from the chat, a
    scheduled send or a rewind, and a rule the user set once should hold for all three. Only the
    Claude Code engine understands "plan" -- Codex and the other terminal CLIs have their own
    approval vocabulary, so they keep whatever was sent."""
    if not settings.get("cc_force_plan"):
        return mode
    return "plan" if (agent == "claude" or alt_agent(agent)) else mode


def _snapshot_before_turn(project_id: str, message: str, agent: str = "",
                          has_images: bool = False) -> None:
    """Snapshot the project before an agent turn — at ONE door, for EVERY engine.

    The snapshot used to be taken inside the Claude branch and, separately, inside the Codex one,
    so DeepSeek never took one at all: its adapter returns from `send()` before the line that
    Claude's path reaches (see `deepseek_session.send`). Its pane therefore had a Checkpoints tab
    with nothing in it, forever. A feature that is per-engine by accident is the same bug class as
    a button that only works for Claude, so the call lives here, above the dispatch, and the two
    old call sites are gone.

    Background thread: the scan must never delay the send. Failures are swallowed on purpose — a
    checkpoint that cannot be taken must not refuse the turn.
    """
    text = (message or "").strip()
    if not text and not has_images:
        return
    try:
        from . import checkpoints
        threading.Thread(target=checkpoints.create, args=(project_id,),
                         kwargs={"label": (text[:80] or "attached image"), "kind": "turn",
                                 "agent": agent or ""}, daemon=True).start()
    except Exception:
        pass


def _engine_busy(project_id: str, agent: str) -> bool:
    """Is a turn already running for THIS engine on this project (so a new message steers it)?"""
    try:
        alt = alt_agent(agent)
        fid = (alt[0] + project_id) if alt else engines.for_agent(agent).feed_id(project_id)
        return is_sending(fid)
    except Exception:
        return False


def send(
    project_id: str,
    message: str,
    model: str = "default",
    permission_mode: str = "acceptEdits",
    fork: bool = False,
    images: Optional[list[str]] = None,
    effort: str = "default",
    agent: str = "claude",
    new_session: bool = False,
    session: str = "",
    path: str = "",
    thinking: bool = False,
    companions: Optional[list[dict]] = None,
    steer: bool = False,
) -> dict:
    from . import agents
    # WHICH ENGINE IS ABOUT TO WRITE, read before the alt remap below renames it to "claude". The
    # turn's checkpoint is labelled with this, not with the engine the plumbing ends up running.
    engine = agent
    # /steer and the button use the same path. Validate before snapshots/BOOST/companions.
    command = re.match(r"^\s*/steer(?:\s+|$)", message or "", flags=re.I)
    if command:
        steer = True
        message = message[command.end():]
    if steer:
        if agent not in ("claude", "codex"):
            return {"ok": False, "error": "Steer is available for Claude and Codex.", "agent": agent}
        if not (message or "").strip() and not images:
            return {"ok": False, "error": "empty steering update", "agent": agent}
        if new_session or fork or not _engine_busy(project_id, agent):
            return {"ok": False, "error": "Steer needs a running turn in this conversation. Use Send instead.", "agent": agent}
        if agent == "claude" and not settings.get("cc_streaming", True):
            return {"ok": False, "error": "Claude steering requires streaming mode.", "agent": agent}

    # THE MONEY GUARD, and it now covers what its own comment always claimed. Nothing used to stop
    # an agent turn on cost: the app's only cap, `monthly_spend_cap_usd`, was checked in
    # `jobs/queue._preflight` for paid ASSET-generation jobs only, and agent turns — the larger
    # number by two orders of magnitude here — were covered by nothing at all. It was then put at
    # "this one door ... every engine reaches", but the Codex and DeepSeek branches below RETURN
    # from this function, so the guard sat under them and neither engine was ever checked. Moved
    # above the dispatch so the sentence is true.
    try:
        from . import spend
        over = spend.over_cap()
        if over:
            return {"ok": False, "error": over, "agent": agent}
    except Exception:
        pass

    # ...and the pre-turn snapshot, for the same reason and by the same move. It is handed the
    # message AS THE USER WROTE IT — a checkpoint label is about their words, not about what
    # BOOST removed from the copy that actually leaves the machine.
    # Not for a message that STEERS a turn already running on this engine — that turn's checkpoint
    # was taken when it started, and the files are mid-edit now — and not for /compact, which edits
    # nothing. (An unchanged folder is also not written twice: see checkpoints.create.)
    if not steer and not _engine_busy(project_id, agent) and not _is_compact_cmd(message or ""):
        _snapshot_before_turn(project_id, message, engine, has_images=bool(images))

    # BOOST, at the one door every engine comes through — the same reason the money guard above
    # was moved here. The compression and the local-index lookup apply to the OUTGOING copy only,
    # and both are cache-safe by construction: a user message sits BELOW the provider's cache
    # breakpoint, so rewriting it cannot invalidate the cached prefix above it.
    boost_info: dict = {"applied": False, "saved_tokens": 0}
    try:
        from . import boost as _boost
        if _boost.enabled():
            boost_info = _boost.prepare(project_id, message, engine=engine, cwd=path or "")
            message = boost_info.get("message") or message
    except Exception:
        boost_info = {"applied": False, "saved_tokens": 0}   # a saving never fails a send

    # Every engine but Claude starts its turn through its adapter (engines.py). Everything above —
    # the money guard, the checkpoint, BOOST — has already run for all of them.
    eng = engines.for_agent(agent)
    if eng is not engines.CLAUDE:
        res = eng.start_turn(project_id, message, model=model, permission_mode=permission_mode,
                             images=images, effort=effort, new_session=new_session,
                             session=session, path=path, steer=steer)
        return {**res, "boost": boost_info} if isinstance(res, dict) else res
    if images:
        refs = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(images))
        message = (message.strip() + "\n\nAttached screenshot(s) — please Read these image files:\n" + refs).strip()
    if thinking and not steer and (agent == "claude" or alt_agent(agent)):
        message = (message.strip() + "\n\nultrathink").strip()  # max extended-thinking budget
    if not message.strip():
        return {"ok": False, "error": "empty message"}

    permission_mode = forced_mode(permission_mode, agent)

    # Kimi K3 / Qwen = the SAME Claude Code engine pointed at that vendor's Anthropic-compatible
    # API, in an isolated session universe (the "<engine>--" id prefix). After this remap the whole
    # streaming/feed/live/effort/btw pipeline below is reused verbatim — which is why an alternate
    # engine's messages render in the feed exactly like Claude's.
    alt = alt_agent(agent)
    if alt:
        from . import keychain
        prefix, keyname, need_key_err = alt
        if need_key_err and not keychain.has_key(keyname):
            return {"ok": False, "needs_install": True, "agent": agent, "error": need_key_err}
        project_id = prefix + project_id
        agent = "claude"

    sp = agents.spec(agent)
    if sp is None:
        return {"ok": False, "error": f"unknown agent {agent!r}", "agent": agent}
    exe = agents.detect(agent)
    if not exe:
        return {"ok": False, "needs_install": True, "agent": agent,
                "error": f"{sp['name']} not found. Install it: {sp['install_cmd']}"}

    if agent == "claude" and not mission.alt_prefix(project_id):
        model = _apply_1m(model)  # request the 1M context window (Opus on Max) when cc_1m is on
                                  # (kimi/qwen: model rides ANTHROPIC_MODEL in the spawn env instead)

    resolved_cwd, newest_session = _resolve(project_id)
    if resolved_cwd and not Path(resolved_cwd).exists() and path and Path(path).exists():
        resolved_cwd = path   # renamed project: the UI's root path is the live truth
    # for a freshly-opened folder with no Claude project yet, start a session in it
    cwd = resolved_cwd or (path if (path and Path(path).exists()) else None)
    if not cwd or not Path(cwd).exists():
        return {"ok": False, "error": "could not resolve this project's folder on disk"}
    if not resolved_cwd:
        # brand-new folder → begin a fresh Claude session here, BUT if we already have a
        # live session for it, keep streaming into that (the transcript just hasn't been
        # written yet) instead of respawning and dropping the in-flight queue
        live_now = _live.get(project_id)
        if not (live_now is not None and live_now.alive):
            new_session = True

    # which conversation to continue: new (none), a chosen one, or the pinned one
    session_id = None if new_session else (session or newest_session)
    if session_id and not new_session:
        # the user picking a conversation (or us resuming the pinned one) makes it THE
        # active chat for this project, so later plain sends stay on it
        mission.remember_active_session(project_id, session_id)

    # (the pre-turn snapshot is taken at the top of `send()`, for every engine — see
    # `_snapshot_before_turn`. It used to live here, which is why DeepSeek never had one.)

    # Graphify toggle: keep this workspace's code knowledge-graph fresh in the background so
    # Claude can query it instead of grepping. Code-only (AST) → zero tokens/Claude usage;
    # debounced + rate-limited + non-blocking, so it never delays the send.
    if agent == "claude" and settings.get("cc_graphify"):
        try:
            from . import graphify_index
            graphify_index.refresh(cwd)
        except Exception:
            pass

    # dual-agent: run the enabled co-agents too. Prepend their last outputs so the primary
    # 'sees' them, then after the primary's turn run each co-agent to review/complement it.
    #
    # THE THREAD STARTS ONLY ONCE THE SEND IS ACCEPTED. It used to start right here, ABOVE the
    # send, and nothing ever looked at whether the send happened. `_send_streaming` has several
    # ways to refuse — a launch that cannot succeed, an agent still owed a result, a restart it
    # declined, a busy session — and every one of them still bought a full billed turn per
    # co-agent, for a primary turn that never ran. Each companion is its own model run, so a pane
    # with two co-agents paid twice for nothing, repeatedly.
    comps = [c for c in (companions or []) if not steer and isinstance(c, dict) and c.get("id") and c.get("id") != agent]
    user_msg = message
    if agent == "claude" and comps:
        ctx = _companion_context(project_id, [c["id"] for c in comps])
        if ctx:
            message = message + ctx

    def _start_companions(out: dict) -> dict:
        """Co-agents follow a primary turn that actually started — never a refused send."""
        if comps and isinstance(out, dict) and out.get("ok"):
            threading.Thread(target=_run_companions, args=(project_id, cwd, user_msg, comps),
                             daemon=True).start()
        # BOOST's own report rides EVERY accepted send through here, whatever path produced it.
        # The streaming session is the common one and returns from its own branch, so attaching
        # this at only one return left the saving invisible on the path users actually take —
        # and a saving nobody can see is indistinguishable from one that never happened.
        if isinstance(out, dict) and boost_info.get("applied"):
            out = {**out, "boost": boost_info}
        return out

    # Claude: stream the message into a persistent session so several can be fired
    # back-to-back without a respawn (VS Code-style). Other CLIs run one-shot.
    if agent == "claude" and settings.get("cc_streaming", True):
        return _start_companions(
            _send_streaming(project_id, message, cwd, model, permission_mode, fork,
                            effort, exe, new_session, session, newest_session, steer=steer))

    # one-shot path (non-claude agents, or streaming disabled) — reject if busy
    if is_sending(project_id):
        return {"ok": False, "error": "a turn is already running for this project"}

    if agent == "claude":
        args = _claude_args(exe, message, session_id, model, permission_mode, fork, effort)
    else:
        # Codex / Gemini / Cursor have no --append-system-prompt, so the graph instruction has to
        # ride in the message. Without this, switching agent silently dropped it and that agent
        # re-discovered the repo by grep — the exact waste the graph exists to remove.
        msg = (_graphify_note() + "\n\n---\n\n" + message) if settings.get("cc_graphify") else message
        args = agents.build_args(agent, exe, msg, cwd, model=model, effort=effort, permission_mode=permission_mode)

    # Bypass npm .cmd batch shims on Windows (they mangle newlines/%VARS%/spaces).
    node_cmd = _resolve_node_shim(args[0])
    if node_cmd:
        args = [*node_cmd, *args[1:]]

    logf = open(SESS_DIR / f"{project_id}.log", "wb", buffering=0)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        proc = subprocess.Popen(
            args, cwd=cwd, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags,
            # same gate as the streaming path, or the subagent instruction is dropped here
            env={**os.environ, "CLAUDE_CODE_ENABLE_APPEND_SUBAGENT_PROMPT": "1",
                 **(_claude_context_env() if agent == "claude" else {}),
                 **({"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"} if settings.get("cc_phases", True) else {})},
        )
    except Exception as e:
        try:
            logf.close()
        except Exception:
            pass
        return {"ok": False, "error": f"failed to launch {sp['name']}: {e}"}
    _procs[project_id] = proc
    _logfiles[project_id] = logf
    _last_agent[project_id] = agent
    return _start_companions({
        "ok": True, "agent": agent, "streams": sp.get("streams", False),
        "session_id": session_id, "forked": bool(fork and session_id),
        "model": model, "permission_mode": permission_mode, "pid": proc.pid, "cwd": cwd,
    })


def _send_codex(project_id: str, message: str, model: str, permission_mode: str,
                images: Optional[list[str]], effort: str, new_session: bool, session: str,
                path: str, steer: bool = False) -> dict:
    """Codex goes through its own app-server (codex_app.py): signed in there, its own models,
    one conversation per folder, streamed into the feed under "codex--<folder>". Pictures travel
    as pictures, not as a list of paths to read."""
    from . import codex_app
    project_id = codex_app.bare(project_id)
    cwd = path if (path and Path(path).is_dir()) else None
    if not cwd:
        cwd = codex_app.known_cwd(project_id) or None
    if not cwd:
        rc, _ = _resolve(project_id)
        cwd = rc if (rc and Path(rc).is_dir()) else None
    if not cwd:
        return {"ok": False, "agent": "codex", "error": "could not resolve this project's folder on disk"}
    # "/btw" is Claude's side-note channel; a message sent while Codex works steers the turn anyway,
    # so the word itself would only reach Codex as text
    message = re.sub(r"^\s*/btw\b[ \t]*", "", message or "", flags=re.I)
    if not (message or "").strip() and not images:
        return {"ok": False, "error": "empty message"}
    # (the pre-turn snapshot is taken at the top of `send()`, for every engine.)
    if settings.get("cc_graphify"):
        try:
            from . import graphify_index
            graphify_index.refresh(cwd)
        except Exception:
            pass
    return codex_app.send(project_id, message, cwd, model=model, effort=effort, mode=permission_mode,
                          images=images, new_session=new_session, session=session,
                          fast=bool(settings.get("codex_fast")), planner=bool(settings.get("codex_planner")),
                          steer=steer)


def cancel(project_id: str) -> dict:
    """Stop the turn running in this feed, whichever engine it belongs to."""
    return engines.for_feed(project_id).cancel(project_id)


def _claude_cancel(project_id: str) -> dict:
    live = _live.pop(project_id, None)
    if live is not None:
        _kill_live(live)   # killing ends the turn; next send resumes the same conversation
        return {"ok": True, "killed": "live"}
    p = _procs.get(project_id)
    if p and p.poll() is None:
        _kill_proc_tree(p)
    _reap(project_id)
    return {"ok": True}


# --- handing the conversation to a real terminal ---------------------------
#
# The chat and the terminal are not two conversations. Both resume the same session id and
# both append to the same transcript on disk, which is also exactly why only ONE of them may
# be alive at a time -- see _stop_shared for why interleaved writes corrupt a history.
#
# So this is a handoff, not a split screen: stop the streaming session, start the TUI on the
# id it was holding. Close the terminal and the next send resumes that same id, with
# everything typed in the terminal already sitting in the feed.

# Flags that only mean something to a headless session. The terminal's args ARE the streaming
# args with these removed, so the model, the effort, the permission mode and every system note
# are identical to what the chat would have been given. Hand-building a second list is how the
# two drift apart, and a terminal that behaves differently from the chat is worse than none.
_HEADLESS_FLAGS = {"-p", "--include-partial-messages", "--verbose"}
_HEADLESS_PAIRS = {"--input-format", "--output-format"}


def _tui_args(stream_args: list[str]) -> list[str]:
    out: list[str] = []
    drop_next = False
    for a in stream_args:
        if drop_next:
            drop_next = False
            continue
        if a in _HEADLESS_PAIRS:
            drop_next = True          # the flag AND its value
            continue
        if a in _HEADLESS_FLAGS:
            continue
        out.append(a)
    return out


def become_terminal(project_id: str, cols: int = 100, rows: int = 30, model: str = "default",
                    permission_mode: str = "default", effort: str = "default",
                    session: str = "", fresh: bool = False, cwd_hint: str = "") -> dict:
    """Stop this project's streaming session and open its conversation in a real terminal.

    `fresh` and `session` mirror the chat's own conversation picker. Without them the terminal
    always reopened the pinned conversation — so asking for a NEW chat and then opening the
    terminal handed you the old one back, and on a long history that replay is what left the
    pane blank.
    """
    from . import terminal          # local: terminal.py depends on nothing here, keep it that way

    ok, why = terminal.available()
    if not ok:
        return {"ok": False, "error": why}

    # Already open? Hand back the SAME terminal.
    #
    # Leaving the tab unmounts the pane, which loses the terminal id, so coming back asked for a
    # terminal again — and got a second CLI resumed on the same conversation. Two of them then
    # append to one transcript and the turn you were waiting on stops making sense. Reattaching
    # is also what you want anyway: the work carried on while you were away, and the scrollback
    # replays it. "Restart" in the pane header is the way to a genuinely new one.
    running = terminal.for_project(project_id)
    if running:
        return {"ok": True, "id": running, "label": "Claude Code", "reused": True}

    if mission.alt_prefix(project_id):
        return {"ok": False, "error": "This opens the Claude Code terminal. Switch the chat back "
                                      "to Claude first, then hand the conversation over."}
    # Guard, not a courtesy: stopping mid-turn throws away the answer being written, and the
    # transcript would carry a half-finished turn into the terminal.
    # Same reason, different door: the handoff STOPS the streaming session, and the agents are
    # inside it. The terminal will still be there in a minute.
    blocked = _agents_block(project_id, "Handing the conversation to a terminal stops this "
                                        "session, which")
    if blocked:
        return {"ok": False, "error": blocked}
    if is_sending(project_id):
        return {"ok": False, "error": "A turn is running. Wait for it to finish, or press Stop "
                                      "first — switching now would lose the answer being written."}
    exe = find_claude()
    if not exe:
        return {"ok": False, "error": "claude was not found on this machine."}
    cwd, session_id = _resolve(project_id)
    # A folder the chat has never run in has no transcript to read a cwd from, and that is the
    # ordinary case for "new project, straight to the terminal". The workspace knows the path.
    cwd = cwd or cwd_hint
    if not cwd:
        return {"ok": False, "error": "This project has no folder on disk yet."}
    if fresh:
        session_id = None          # a new conversation means new — not the last one, resumed
    elif session:
        session_id = session       # the conversation picked in the chat's own dropdown

    # One owner. Ours, any other engine pointed at the same transcripts, and any terminal that
    # exited or was left behind (for_project above only matches a LIVE one).
    cancel(project_id)
    _stop_shared(project_id)
    terminal.close_for_project(project_id)

    args = _tui_args(_claude_stream_args(exe, session_id, _apply_1m(model), permission_mode,
                                         False, effort, project_id, cwd))
    # A .cmd shim is a batch file, so it needs its interpreter; a real .exe is spawned directly.
    if os.name == "nt" and str(args[0]).lower().endswith((".cmd", ".bat")):
        args = [os.environ.get("COMSPEC") or "cmd.exe", "/c", *args]

    # Measured, not assumed: a claude spawned from inside another claude session inherits
    # CLAUDE_CODE_CHILD_SESSION and then runs with transcript saving OFF. The transcript IS the
    # handoff, so that one inherited variable would make everything typed here vanish on the way
    # back to the chat -- silently, with the terminal looking perfectly healthy.
    # ASSET_STUDIO_CC is stripped, not merely left unset: the backend does not carry it, but
    # anything the backend was itself launched from might, and it would then be INHERITED here.
    # It marks "a Studio live-stream process" and is the invariant reaping relies on.
    _drop = {"CLAUDE_CODE_CHILD_SESSION", "ASSET_STUDIO_CC"}
    env = {k: v for k, v in os.environ.items() if k not in _drop}
    # Everything _spawn_live sets, because a note in the system prompt is only half of a feature
    # and the other half rides here. Subagents need the first key to inherit their prompt; the
    # Phases panel needs the second, or the terminal is TOLD about a panel it has no tool to fill.
    env["CLAUDE_CODE_ENABLE_APPEND_SUBAGENT_PROMPT"] = "1"
    env.update(_claude_context_env())
    if settings.get("cc_phases", True):
        env["CLAUDE_CODE_ENABLE_TODO_TOOLS"] = "1"
    # Deliberately NOT ASSET_STUDIO_CC=1: that marker means "a Studio live-stream process", and
    # it is what makes reaping and adoption safe. A terminal is neither.

    r = terminal.create(args, cwd, cols, rows, label="Claude Code", env=env,
                        owns_project=project_id)
    if r.get("ok"):
        r["session_id"] = session_id or ""
        r["cwd"] = cwd
    return r


def _parse_iso_epoch(s: str) -> Optional[float]:
    """ISO-8601 → epoch seconds, tolerant of the 'Z' suffix and fractional seconds that
    Python 3.10's strict fromisoformat rejects."""
    import datetime
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", s)   # drop fractional secs / odd tz
    if m:
        try:
            return datetime.datetime.fromisoformat(m.group(1)).replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except Exception:
            return None
    return None


def engine_name(feed_id: str) -> str:
    """A person's name for the engine behind a feed id: "Claude", "Codex", "DeepSeek", "kimi"…"""
    return engines.name_of(feed_id)


def folder_busy(project_id: str, exclude: str = "") -> list[str]:
    """Every feed with a turn RUNNING in this project's folder, whichever engine it is.

    Two agents can work in one folder — Claude in one pane, Codex or DeepSeek in the other — and
    a file restore does not know whose edits it is overwriting. A checkpoint frames the FOLDER, so
    restoring one while the other agent writes puts that agent's files back under it, mid-turn.
    The restore paths ask this first. `exclude` is the caller's own feed, which it has already
    stopped."""
    return engines.busy_feeds(project_id, exclude=exclude)


def _revert_to_before(project_id: str, msg_ts_iso: str) -> Optional[dict]:
    """Restore the studio checkpoint taken just before the given message's turn, so
    the files go back to how they were when that prompt was originally asked."""
    from . import checkpoints
    epoch = _parse_iso_epoch(msg_ts_iso)
    if epoch is None:
        return None
    best = None
    for ck in checkpoints.list_checkpoints(project_id):   # newest-first
        if float(ck.get("ts", 0) or 0) <= epoch + 2:      # first one at/just-before the turn
            best = ck
            break
    if not best:
        return None
    res = checkpoints.restore(project_id, best["id"])
    return {"checkpoint": best["id"], "label": best.get("label", ""), **res}


def rewind(project_id: str, msg_uuid: str, message: str, model: str = "default",
           permission_mode: str = "acceptEdits", fork: bool = False, effort: str = "default",
           thinking: bool = False, session: str = "", path: str = "",
           restore_files: bool = False) -> dict:
    """Rewind the conversation to a past user message and resume from an edited version
    of it — everything after that message is removed (like editing a message on the web).
    The original transcript is backed up first; optionally also revert file changes."""
    if engines.native(project_id):
        return {"ok": False, "error": "Editing a sent message is not available for %s yet. "
                                      "Send the corrected message as a new one." % engine_name(project_id)}
    if not message.strip():
        return {"ok": False, "error": "empty message"}
    # Putting the FILES back is a folder-wide act. If another engine is mid-turn in this folder,
    # the restore would overwrite what it is writing. Refuse before anything is stopped or cut.
    if restore_files:
        others = folder_busy(project_id, exclude=project_id)
        if others:
            return {"ok": False, "busy": others, "error": (
                "%s is working in this folder right now. Restoring files would overwrite its "
                "edits. Wait for it to finish, or edit the message without restoring files."
                % ", ".join(engine_name(f) for f in others))}
    # WHICH ENGINE OWNS THIS CONVERSATION, and it is not always Claude. The resend below used to
    # name agent="claude" unconditionally. For Kimi/Qwen that is harmless — the "<engine>--" prefix
    # on the id is what selects their environment (see `_alt_env`) — but a "deepseek-harness--" id
    # is NOT a Claude conversation wearing a prefix: `send()` hands that prefix to DeepSeek's own
    # adapter, which rebuilds its thread from this very transcript. Naming Claude tried to run the
    # Claude CLI against DeepSeek's session home, so editing a DeepSeek prompt either failed or
    # started a second, unrelated conversation.
    resend_agent = engines.for_feed(project_id).id
    permission_mode = forced_mode(permission_mode)
    _, pdir, _ = mission.project_dir(project_id)
    if not pdir.exists():
        return {"ok": False, "error": "no conversation to rewind for this project"}
    jsonls = list(pdir.glob("*.jsonl"))
    if not jsonls:
        return {"ok": False, "error": "no conversation to rewind"}
    target = None
    if session:
        cand = pdir / f"{os.path.basename(session)}.jsonl"
        if cand.exists() and cand.parent == pdir:
            target = cand
    if target is None:
        target = max(jsonls, key=lambda f: f.stat().st_mtime)

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return {"ok": False, "error": f"could not read transcript: {e}"}
    cut, msg_ts = None, None
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("uuid") == msg_uuid and o.get("type") == "user":
            cut, msg_ts = i, o.get("timestamp")
            break
    if cut is None:
        return {"ok": False, "error": "could not find that message in the transcript"}

    # Rewinding respawns the session to re-read the truncated file, which kills the process and
    # any background agent living inside it. Refuse while one is open, the same rule /compact
    # follows, so the edit never silently strands a fan-out.
    blocked = _agents_block(project_id, "Editing a past message restarts the session, which")
    if blocked:
        return {"ok": False, "error": blocked}

    # Drop the engine's in-memory conversation so the next send re-reads the truncated transcript:
    # Claude's live process is killed and respawns on it; DeepSeek's runtime is closed and the
    # thread rebuilt from it. `reset`, not `cancel` — cancel only stops a RUNNING turn, and an edit
    # is made while idle, so an idle DeepSeek runtime used to keep the removed messages.
    engines.for_feed(project_id).reset(project_id)

    def _is_turn(s: str) -> bool:
        try:
            return json.loads(s).get("type") in ("user", "assistant")
        except Exception:
            return False

    prefix = lines[:cut]
    has_context = any(_is_turn(s) for s in prefix if s.strip())
    reverted = _revert_to_before(project_id, msg_ts) if (restore_files and msg_ts) else None

    if has_context:
        # real conversation precedes this message → back up, truncate, and resume from here
        try:
            bdir = DATA_DIR / "rewind_backups" / project_id
            bdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, bdir / f"{target.stem}-{int(time.time())}.jsonl")
            target.write_text("\n".join(prefix) + "\n", encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": f"failed to rewind transcript: {e}"}
        res = send(project_id, message, model=model, permission_mode=permission_mode, fork=fork,
                   images=None, effort=effort, agent=resend_agent, new_session=False,
                   session=session, path=path, thinking=thinking)
    else:
        # editing the very first message → nothing to resume; start a fresh conversation
        # (non-destructive: the original session is left intact as an earlier branch)
        res = send(project_id, message, model=model, permission_mode=permission_mode, fork=fork,
                   images=None, effort=effort, agent=resend_agent, new_session=True,
                   session="", path=path, thinking=thinking)

    res["rewound"] = True
    res["removed_from_line"] = cut
    res["reverted_files"] = reverted
    return res

"""Native DeepSeek Harness SDK, isolated from Claude and Codex conversations.

DSH owns its durable session log; a small Claude-compatible transcript lets the
Studio's existing timeline, conversation picker and context readers display it.
Credentials stay in the backend and are injected only into the child runtime.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR

AGENT = "deepseek-harness"
PREFIX = AGENT + "--"
# The four rows the runtime's DeepSeek adapter declares, in the order the picker shows them.
# `deepseek-flash` is first because it is the one that can SEE — see the note above `_image_blocks`.
#
# `deepseek-flash` IS DeepSeek V4.1 Flash, and that is a fact read off two sources rather than a
# guess. The adapter's own catalog (`@deepseek-ai/dsh-llm-deepseek`, `DEFAULT_MODELS`) names that row
# `"DeepSeek-V41-Flash"`; the release notice of 2026-09-10 says "Set your model to `deepseek-flash`",
# so V4.1-Flash has no id of its own on the API — `deepseek-flash` IS its id. It only READ as an
# older, unversioned model here because this dict called it "DeepSeek Flash".
#
# V4-Flash and V4-Flash-Vision-Exp were retired on 2026-09-10; their ids are aliases that are served
# AND billed as V4.1-Flash. `deepseek-v4-pro` is NOT an alias: the pricing page (read 2026-10-07)
# lists it as its own model, DeepSeek-V4-Pro-0813, at about four times the Flash price, and the
# runtime's catalogue names it "DeepSeek V4 Pro". Calling it "V4.1 Flash (Pro id)" hid the price.
# Which rows can SEE is the runtime's declaration — the runtime strips an image for any row it
# catalogues as text-only, so the picker keeps saying which rows those are.
MODELS = ("deepseek-flash", "deepseek-v4-flash-vision-exp", "deepseek-v4-flash", "deepseek-v4-pro")
MODEL_LABELS = {"deepseek-flash": "DeepSeek V4.1 Flash",
                "deepseek-v4-flash-vision-exp": "DeepSeek V4.1 Flash (legacy vision id)",
                "deepseek-v4-flash": "DeepSeek V4.1 Flash (legacy id)",
                "deepseek-v4-pro": "DeepSeek V4 Pro"}
HOME = DATA_DIR / "deepseek-harness-home"
_lock = threading.RLock()
_live: dict[str, dict] = {}
_runtimes: dict[tuple[str, str], dict] = {}


def bare(pid: str) -> str:
    return pid.removeprefix(PREFIX)


def installed() -> str | None:
    try:
        from deepseek_harness_runtime import bundled_runtime_path
        return str(bundled_runtime_path())
    except (ImportError, FileNotFoundError):
        return None


def api_key() -> str:
    from . import keychain
    return keychain.get_key("deepseek") or keychain.get_key("chat:deepseek") or os.environ.get("DEEPSEEK_API_KEY", "")


def _close(harness) -> None:
    # SDK 0.1.5 reaps the runtime but leaves its stdout/stderr wrappers open.
    proc = getattr(harness.client, "_proc", None)
    harness.close()
    if proc:
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()


def _append(state: dict, role: str, content, usage: dict | None = None) -> None:
    import json
    message = {"role": role, "content": content, "model": state["model"]}
    if usage:
        message["usage"] = usage
    row = {"type": role, "uuid": uuid.uuid4().hex, "sessionId": state["session"],
           "cwd": state["cwd"], "timestamp": datetime.now(timezone.utc).isoformat(), "message": message}
    with state["transcript"].open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def runtime_home(pid: str) -> Path:
    """The SDK's per-project runtime directory. One runtime per project, so this is also the
    `dshHome` whose `skills` subdirectory the runtime scans (rank 400) — which is why the shared
    library has to arrive as a `customSkillDirs` root instead."""
    return HOME / "runtime" / hashlib.sha256(bare(pid).encode()).hexdigest()[:24]


def _skill_dirs() -> list[str]:
    """The skill roots the Studio hands to DeepSeek, in the order the runtime ranks them.

    READ OUT OF THE INSTALLED RUNTIME, not guessed. `@deepseek-ai/dsh-skill-filesystem` discovers
    `<root>/<name>/SKILL.md` (one level deep) from, in rank order:

        100  project-dsh     <projectRoot>/.dsh/skills
        200  project-agents  <projectRoot>/.agents/skills
        300  custom          Config.customSkillDirs
        400  user-dsh        <dshHome>/skills
        500  user-agents     <agentsHome>/skills
        600  bundled         $DSH_BUNDLED_SKILL_DIR

    The Studio already owns a skill library at `~/.claude/skills`, and its on/off switch is
    `disable-model-invocation` in the SKILL.md frontmatter — a key the DSH runtime reads too (it
    surfaces as `modelInvocable` on `skills/list`). So the library is handed over as a custom root
    instead of being copied: one set of files, one switch, and both engines see the same skill.

    `dshHome` cannot carry it: it is the per-project runtime directory (see `send`), so the
    user-level root can only arrive through `customSkillDirs`. Project roots need no help — they are
    default roots, which is why a `<project>/.dsh/skills` just works.
    """
    out: list[str] = []
    try:
        from . import skills as _skills
        shared = _skills._claude_home() / "skills"
        if shared.is_dir():
            out.append(str(shared))
    except Exception:
        pass
    return out


def _patch_text() -> str:
    """The `--patch` overlay this adapter always passes.

    The format is an id-keyed row list: a row names `id` and `config`, and the launcher merges it
    over the profile's own row of that id (see @deepseek-ai/dsh-app-boot/profile). Two rows:

    * `session-log-deepseek` — keep optional session-log uploads off.
    * `skill-filesystem` — add the Studio's shared skill library as a custom root, so the Skills
      button is not a Claude-only control.
    """
    lines = ["- id: session-log-deepseek", "  config:", "    enabled: false"]
    dirs = _skill_dirs()
    if dirs:
        lines += ["- id: skill-filesystem", "  name: '@deepseek-ai/dsh-skill-filesystem'", "  config:",
                  "    customSkillDirs:"]
        # SINGLE quotes: YAML does not process escapes inside them, so a Windows path with
        # backslashes survives as written. Only a literal quote would need doubling.
        for d in dirs:
            lines.append("      - '" + d.replace("'", "''") + "'")
    return "\n".join(lines) + "\n"


def _publish(state: dict, force: bool = False) -> None:
    """Push this turn's live state over the WebSocket, the way Codex and Claude already do.

    Without it every open DeepSeek pane asked `/live` five times a second for the whole turn, and
    twice that with two panes. With a push arriving, the pane's poll stands down (SessionFeed
    `wsAt`). At most one push per 60 ms unless `force`."""
    now = time.time()
    if not force and now - float(state.get("last_pub") or 0.0) < 0.06:
        return
    state["last_pub"] = now
    try:
        from .events import bus
        from .models import ProgressEvent, WSEventType
        if bus.subscriber_count() > 0:
            fid = PREFIX + str(state.get("pid") or "")
            bus.publish_threadsafe(ProgressEvent(type=WSEventType.cc_live,
                                                 data={"project_id": fid, **live_state(fid)}))
    except Exception:
        pass


def _notification(state: dict, note) -> None:
    if note.method != "session.event" or note.payload.get("sessionId") != state["sdk_session"]:
        return
    # Anything at all from the runtime counts as "it is alive": the stall watch only ever fires
    # for a turn that has produced NOTHING, which is the one failure with no other symptom.
    state["last_note"] = time.time()
    state["seen"] = int(state.get("seen") or 0) + 1
    event = note.payload.get("event") or {}
    typ, data = event.get("type", ""), event.get("data") or {}
    if typ == "assistant/message":
        msg = data.get("message") or data
        blocks = msg.get("content") or []
        # tool/call owns tool cards, so do not duplicate them in assistant/message.
        visible = [b for b in blocks if b.get("type") in ("text", "thinking", "reasoning")]
        visible = [{"type": "thinking", "thinking": b.get("text", b.get("thinking", ""))}
                   if b.get("type") == "reasoning" else b for b in visible]
        use = data.get("usage") or msg.get("usage") or {}
        # The harness reports DISJOINT counts: `inputTokens` is the cache MISS part only, and the
        # hits arrive as `cacheReadTokens` (omitted when zero). Reading only the first made a warm
        # turn look almost free to the context meter and to the ledger.
        usage = {"input_tokens": int(use.get("inputTokens", use.get("input_tokens", 0)) or 0),
                 "output_tokens": int(use.get("outputTokens", use.get("output_tokens", 0)) or 0),
                 "cache_read_input_tokens": int(use.get("cacheReadTokens",
                                                        use.get("cache_read_input_tokens", 0)) or 0)}
        if visible:
            _append(state, "assistant", visible, usage)
        # ACCUMULATE FOR THE LEDGER. One turn is many assistant messages, each its own billed
        # request, so the turn's cost is their sum — see `_bank_turn`, which spends it at turn/end.
        with _lock:
            state["turn_in"] = int(state.get("turn_in") or 0) + usage["input_tokens"]
            state["turn_out"] = int(state.get("turn_out") or 0) + usage["output_tokens"]
            state["turn_cache"] = int(state.get("turn_cache") or 0) + usage["cache_read_input_tokens"]
        state["text"] = ""
        state["activity"] = "Working"
    elif typ == "tool/call":
        import json
        state["activity"] = "Running " + str(data.get("name", "tool"))
        name = {"pwsh": "PowerShell", "bash": "Bash", "read": "Read", "write": "Write", "edit": "Edit"}.get(data.get("name"), data.get("name", "tool"))
        arguments = data.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {"raw": arguments}
        _append(state, "assistant", [{"type": "tool_use", "id": data.get("callId"),
                "name": name, "input": arguments}])
    elif typ == "tool/result":
        msg = data.get("message") or {}
        blocks = msg.get("content") or []
        if blocks:
            _append(state, "user", [{"type": "tool_result", "tool_use_id": msg.get("callId"),
                    "content": blocks, "is_error": msg.get("isError", False)}])
    elif typ == "turn/end":
        reason = data.get("reason") or {}
        if reason.get("kind") not in ("completed", None):
            _append(state, "assistant", [{"type": "text", "text": "DeepSeek turn ended: " + str(reason)}])
        _bank_turn(state)
    _publish(state, force=(typ == "turn/end"))


# A TURN THAT SAYS NOTHING AT ALL IS THE ONE FAILURE WITH NO OTHER SYMPTOM.
#
# What it looked like on this PC, 2026-10-06: the send returned 200, the user's message was in the
# transcript, and then NOTHING for fifteen minutes — no assistant message, no tool call, no error,
# and not even a row in `data/turns.jsonl`. The pane simply kept spinning, so the only thing left
# to try was to restart the backend, which lost the live conversation. The numbers below are a
# PLAIN-LANGUAGE line for the pane, not a kill switch: a slow model call and a stuck one are
# indistinguishable from here, and killing a slow answer is worse than waiting for it.
_STALL_WARN_S = 180.0     # no output at all for this long: say so
_STALL_POLL_S = 15.0      # how often the watch looks


def _start_stall_watch(state: dict) -> None:
    """Replace the activity line with the truth when a turn has produced nothing.

    It stops as soon as ANY notification arrives, so a long quiet stretch later in a turn (a slow
    tool, a big edit) can never re-label a session that is demonstrably working."""
    def work() -> None:
        while True:
            time.sleep(_STALL_POLL_S)
            with _lock:
                if not state.get("working") or state.get("harness") is None:
                    return
                if int(state.get("seen") or 0) > 0:
                    return                      # it IS talking; it just went quiet
                waited = time.time() - float(state.get("sent") or time.time())
                if waited >= _STALL_WARN_S:
                    state["activity"] = (
                        "No output from the harness for %d minutes — it has not answered anything "
                        "at all. Press Stop and send again: the retry rebuilds the thread from the "
                        "transcript, and waiting longer has not produced a token."
                        % int(waited // 60))
                    return
    threading.Thread(target=work, name="dsh-stall", daemon=True).start()


def _bank_turn(state: dict) -> None:
    """Bank one finished DeepSeek turn into the ledgers Claude and Codex also write to.

    Before this the adapter wrote a transcript and nothing else, so a DeepSeek pane did not appear
    on the Dashboard at all — not its tokens, not its cost — even though it is one of the three
    engines the Studio drives.

    The price comes from DeepSeek's own rate card (`pricing._DEEPSEEK`, peak and off-peak), so the
    turn is banked in dollars and `monthly_spend_cap_usd` can stop this engine too. Before that the
    cost was always 0 and the cap never saw DeepSeek at all. A rate in `pricing_overrides`
    (settings) still wins. Cache misses and cache hits are priced apart, because a hit costs about a
    fiftieth of a miss.

    BANKED UNDER THE FEED ID, not the bare folder. The ledger is read back per CONVERSATION —
    `mission._turn_rows` (and so the summary bar under every answer) asks `turns.for_project` for
    the id the feed polls, which for this engine is `deepseek-harness--<slug>`. Writing the bare
    slug here meant the row was banked and never matched: a DeepSeek answer had no tokens, no
    model and no cost bar under it, while `data/turns.jsonl` grew with every turn. The feed's own
    id is the key both sides already agreed on; this was the one side not using it.
    """
    from . import pricing, spend, turns
    with _lock:
        tin = int(state.get("turn_in") or 0)
        tout = int(state.get("turn_out") or 0)
        tcache = int(state.get("turn_cache") or 0)
        state["turn_in"] = 0
        state["turn_out"] = 0
        state["turn_cache"] = 0
    pid = str(state.get("pid") or "")
    if not pid or not (tin or tout or tcache):
        return
    feed = PREFIX + pid
    model = str(state.get("model") or "")
    cost, basis = pricing.cost_with_basis(model, {"input": tin, "output": tout,
                                                  "cache_read": tcache, "cache_write": 0})
    try:
        turns.record(feed, model, "default", tout, 0.0,
                     max(0.0, time.time() - float(state.get("started") or time.time())),
                     0, cost, False, 0, basis)
        if cost > 0:
            spend.record(feed, model, cost, tout)
    except Exception:
        pass


# --- image attachments -------------------------------------------------------
#
# THE PAPERCLIP WAS NEVER BROKEN. THE MODEL LIST WAS.
#
# The DSH runtime maps the `image` block this module builds straight onto the provider's shape:
# `toChatMessages(messages, supportsImages)` turns `{type:'image', mimeType, data}` into
# `{type:'image_url', imageUrl:'data:<mime>;base64,<data>'}`, and that one boolean is the row's own
# declaration — `model.input.includes("image")`. So everything depended on WHICH ROW was selected.
#
# The runtime's DeepSeek adapter (`@deepseek-ai/dsh-llm-deepseek`) declares four default rows, and
# its own documentation says exactly that: "Omitted `models` advertises the text- and image-capable
# `deepseek-flash` and `deepseek-v4-flash-vision-exp` alongside the text-only `deepseek-v4-flash`
# and `deepseek-v4-pro`."
#
#     deepseek-flash                 inputModalities: ["text","image"]   ← vision, and V4.1 Flash
#     deepseek-v4-flash-vision-exp   inputModalities: ["text","image"]   ← vision (legacy name)
#     deepseek-v4-flash              (text only)
#     deepseek-v4-pro                (text only)
#
# The Studio offered the last two ONLY — precisely the rows that cannot see. That was the whole bug:
# correct plumbing, pointed at the one pair of models that would drop the picture.
#
# WHICH ROW SEES IS NOT THE SAME QUESTION AS WHICH MODEL ANSWERS. Since 2026-09-10 all four ids are
# served by V4.1-Flash upstream (see the note above `MODELS`), so every row here is the same model —
# but the runtime still drops an image for a row its catalog marks text-only. Hence both facts are
# kept: the NAME says V4.1 Flash, because that is what answers, and VISION_MODELS says which ROWS
# can carry a picture, because that is what the runtime will actually send.
#
# Measured blind on 2026-10-04: one image, inlined, run in an EMPTY directory with no copy on disk
# and no path named, and no tool used — so nothing but vision could have produced the answer:
#
#     deepseek-flash               → "A blue square and a red circle, with the number 42 …"   ✔
#     deepseek-v4-flash-vision-exp → "A blue square, a red circle, and the number 42."        ✔
#     deepseek-v4-flash            → "it was omitted because this model accepts text only"
#     deepseek-v4-pro              → "it was omitted and I can only see text"
#
# (An EARLIER run LOOKED like Pro could see — it described the picture exactly. It had cheated: the
# probe image was sitting in the working directory and the agent decoded it with PIL. Hence the
# blind re-test above, and hence this note: a right answer is not evidence of vision.)
#
# So the default row is now `deepseek-flash` — V4.1 Flash — and an attached picture simply works.
# VISION_MODELS is the single place the capability is recorded — add a row here if the runtime
# declares another.
VISION_MODELS: tuple[str, ...] = ("deepseek-flash", "deepseek-v4-flash-vision-exp")
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
_MAX_IMAGE = 32 * 1024 * 1024    # DeepSeek: one image, inline
_MAX_TOTAL = 48 * 1024 * 1024    # DeepSeek: the whole request body


def _image_blocks(paths: list) -> tuple[list[dict], list[str], str]:
    """Read attached images into SDK content blocks. Returns (blocks, paths, error).

    The blocks are the runtime's own shape — what it builds internally when it hands an image to
    the provider — so nothing about the file has to be understood here beyond its type and size.
    """
    import base64
    blocks: list[dict] = []
    refs: list[str] = []
    total = 0
    for raw in paths:
        p = Path(str(raw).strip().strip('"'))
        if not p.is_file():
            return [], [], f"Attached image not found: {raw}"
        mime = _IMAGE_MIME.get(p.suffix.lower())
        if not mime:
            return [], [], (f"{p.name} is not an image DeepSeek can read — "
                            "PNG, JPEG, GIF or WebP.")
        size = p.stat().st_size
        if size > _MAX_IMAGE:
            return [], [], f"{p.name} is larger than DeepSeek's 32 MiB per-image limit."
        total += size
        if total > _MAX_TOTAL:
            return [], [], "The attached images are larger than DeepSeek's 48 MiB request limit."
        blocks.append({"type": "image", "mimeType": mime,
                       "data": base64.b64encode(p.read_bytes()).decode()})
        refs.append(str(p))
    return blocks, refs, ""


def send(project_id: str, message: str, *, path: str = "", model: str = "default",
         effort: str = "default", permission_mode: str = "full", new_session: bool = False,
         session: str = "", images=None, **_ignored) -> dict:
    from . import mission, cc_session
    pid = bare(project_id)
    if not pid or any(c in pid for c in "/\\") or pid in (".", ".."):
        return {"ok": False, "error": "Invalid project id"}
    shot_blocks, shot_refs, shot_err = _image_blocks(list(images or []))
    if shot_err:
        return {"ok": False, "error": shot_err}
    # An image on its own is a message — "look at this" is implied by attaching it.
    if not message.strip() and not shot_blocks:
        return {"ok": False, "error": "empty message"}
    # What the TRANSCRIPT keeps: the prompt, plus where the pictures are. The bytes go to the
    # model and nowhere else — a base64 image in the .jsonl would be re-read by every feed poll
    # and by the context meter, for a picture the pane can already draw from its own file.
    shown = message.strip()
    if shot_refs:
        shown = (shown + "\n\nAttached image" + ("s" if len(shot_refs) > 1 else "")
                 + " — reading " + ", ".join(shot_refs)).strip()
    if model == "default":
        model = MODELS[0]
    if model not in MODELS:
        return {"ok": False, "error": f"Unsupported DeepSeek model: {model}"}
    if shot_blocks and model not in VISION_MODELS:
        # Refused BEFORE the turn, not sent. The runtime strips the picture for a text-only row and
        # hands the model a "[image omitted …]" placeholder; we watched one then burn a whole turn
        # globbing, reading the probe script and hand-writing a PNG decoder to guess at pixels. A
        # sentence pointing at the vision row is cheaper than that turn.
        return {"ok": False, "error": (
            f"{MODEL_LABELS.get(model, model)} takes text only, so an attached image would be "
            "dropped before the request. Pick the DeepSeek V4.1 Flash vision row (the model button "
            "beside the prompt) to send pictures — it has vision. To have this row work on the file "
            "itself, send the prompt without the attachment and name the path: it can read it with "
            "its tools.")}
    if permission_mode not in ("full", "bypassPermissions", "danger-full-access"):
        return {"ok": False, "error": "The DeepSeek SDK connection supports Full access. Select it in the agent settings before sending."}
    if not installed():
        return {"ok": False, "agent": AGENT, "needs_install": True, "error": "DeepSeek Harness SDK is not installed"}
    key = api_key()
    if not key:
        return {"ok": False, "agent": AGENT, "needs_install": True,
                "error": "Add the DeepSeek API key in Settings > API Keys > DeepSeek Harness"}
    cwd = path if path and Path(path).is_dir() else cc_session._resolve(pid)[0]
    if not cwd or not Path(cwd).is_dir():
        return {"ok": False, "error": "could not resolve this project's folder on disk"}
    feed = PREFIX + pid
    if session and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session):
        return {"ok": False, "error": "Invalid session id"}
    # Keep the code graph fresh for the note this engine is given, the same way the Claude and Codex
    # paths do. Debounced and non-blocking in graphify_index, so it never delays the send.
    try:
        from .config import settings
        if settings.get("cc_graphify"):
            from . import graphify_index
            graphify_index.refresh(cwd)
    except Exception:
        pass
    with _lock:
        if _live.get(pid, {}).get("working"):
            return {"ok": False, "error": "DeepSeek is already working in this workspace; wait or stop the current turn."}
        _, latest = cc_session._resolve(feed)
        # `/compact` is a COMMAND, not a prompt — see `_compact`. Handled here, after the busy check
        # (a compaction cannot run inside a live turn) and before a runtime is spawned for it.
        if message.strip().lower() == "/compact" and not new_session and not shot_blocks:
            return _compact(pid, feed, session or latest or "", cwd)
        sid = uuid.uuid4().hex if new_session else session or latest or uuid.uuid4().hex
        # ONE RUNTIME PER PROJECT. Runtimes are keyed by (project, conversation), and nothing used
        # to close the one a conversation left behind: every "new chat" and every switch to an older
        # conversation kept the previous runtime alive (100-185 MB each) until the backend restarted.
        # Only one DeepSeek turn per project can run (the busy check above), so the others are idle
        # and can go. Closed in the worker, because a close waits on the process.
        stale = [_runtimes.pop(k) for k in list(_runtimes) if k[0] == pid and k[1] != sid]
        pdir = HOME / "projects" / pid
        pdir.mkdir(parents=True, exist_ok=True)
        transcript = pdir / (sid + ".jsonl")
        history = _history(transcript)
        state = {"working": True, "session": sid, "cwd": str(Path(cwd).resolve()),
                 "model": model, "transcript": transcript, "started": time.time(),
                 "activity": "Starting DeepSeek Harness", "text": "", "harness": None, "cancelled": False, "ready": False,
                 # The stall watch's two facts: when this turn was handed to the runtime, and
                 # whether the runtime has said ANYTHING yet (see `_start_stall_watch`).
                 "sent": time.time(), "seen": 0, "last_note": 0.0,
                 # The ledgers are keyed by project, and the token counters are per TURN — one turn
                 # is many assistant messages, and the bill is per request.
                 "pid": pid, "turn_in": 0, "turn_out": 0}
        _live[pid] = state
        mission.remember_active_session(feed, sid)
        _append(state, "user", shown)
    _start_reaper()

    def run():
        for old in stale:
            try:
                _close(old["harness"])
            except Exception:
                pass
        try:
            from deepseek_harness import DeepSeekHarness
            runtime_home = HOME / "runtime" / hashlib.sha256(pid.encode()).hexdigest()[:24]
            # The runtime home is per-project (the SDK keeps one live runtime per project), so the
            # shared skill library is mounted as a custom root rather than placed inside it.
            runtime_home.mkdir(parents=True, exist_ok=True)
            patch = runtime_home / "studio.patch.yml"
            patch.write_text(_patch_text(), encoding="utf-8")
            sig = (state["cwd"], model, effort, hashlib.sha256(key.encode()).hexdigest())
            rec = _runtimes.get((pid, sid))
            if rec and rec["sig"] != sig:
                _close(rec["harness"])
                _runtimes.pop((pid, sid), None)
                rec = None
            if not rec:
                h = DeepSeekHarness(cwd=state["cwd"], dsh_home=str(runtime_home), model=model,
                        api_key=key, reasoning_effort=None if effort == "default" else effort,
                        patches=(str(patch),), env={"DSH_PERMISSION_MODE": "danger-full-access", "DSH_TELEMETRY_DISABLED": "1"})
                # SDK 0.1.5 cannot attach a previously created id after process exit.
                # Keep a runtime alive between turns; after restart/model switch,
                # restore the visible conversation explicitly into a fresh SDK id.
                rec = {"harness": h, "sdk_session": uuid.uuid4().hex, "sig": sig, "used": time.time()}
                prompt = ("Previous conversation restored by Asset Studio after restarting the harness:\n"
                          + history + "\n\nCurrent user message:\n" + message) if history else message
                # The Studio's notes — the code-graph command and BOOST's directive. This SDK
                # exposes no system-prompt parameter, so they ride the FIRST message of a runtime
                # and then stay in the conversation for the rest of it: paid for once, not once per
                # turn. Deliberately not re-sent on later turns, which would also move the prefix
                # DeepSeek's own context cache keys on.
                #
                # The graph note was missing before, so DeepSeek — the one engine billed per token —
                # was the one that found code by grep and whole-file reads.
                notes = _first_message_notes()
                if notes and isinstance(prompt, str):
                    prompt = notes + "\n\n" + prompt
            else:
                h, prompt = rec["harness"], message
            state["sdk_session"] = rec["sdk_session"]
            with _lock:
                if state["cancelled"]:
                    return
                state["harness"] = h
            h.start()
            with _lock:
                state["ready"] = True
                if state["cancelled"]:
                    _close(h)
                    return
                _runtimes[(pid, sid)] = rec
            state["activity"] = "Thinking"

            def wire(text: str):
                """The prompt as the SDK takes it. A LIST goes through as content blocks verbatim
                (see `normalize_input`), so images ride beside the text instead of inside it."""
                return [{"type": "text", "text": text}, *shot_blocks] if shot_blocks else text

            _start_stall_watch(state)
            h.run(wire(prompt), session_id=state["sdk_session"], on_notification=lambda n: _notification(state, n))
        except Exception as exc:
            _runtimes.pop((pid, sid), None)
            if state["harness"]:
                _close(state["harness"])
            if not state["cancelled"]:
                # Runtime diagnostics may contain request data. Never echo API keys.
                err = str(exc).replace(key, "[redacted]")
                _append(state, "assistant", [{"type": "text", "text": "DeepSeek Harness error: " + err[:2000]}])
        finally:
            with _lock:
                kept = _runtimes.get((pid, sid))
                if kept is not None:
                    kept["used"] = time.time()      # the idle clock starts when the turn ends
            state["working"] = False
            state["activity"] = ""
            state["harness"] = None
            _publish(state, force=True)          # the pane stops spinning now, not at its next poll
    threading.Thread(target=run, name="deepseek-" + pid[:24], daemon=True).start()
    return {"ok": True, "agent": AGENT, "session": sid, "streaming": True}


# HOW MUCH OF A CONVERSATION IS WORTH RE-SENDING, IN CHARACTERS.
#
# The restore path re-sends the visible conversation as ONE text message, and it had no bound at
# all. Measured on this machine, 2026-10-05: the reconstructed text for one project was 1,162,843
# characters — about 290,000 tokens in a single message — and it is re-sent on every change to
# (cwd, model, effort, key) AND on every Stop, because stopping closes the runtime and SDK 0.1.5
# cannot re-attach a session id after its process exits. A session stopped and resumed a few times
# paid for its entire history each time.
#
# 120,000 characters (~30k tokens) is roughly the last dozen exchanges with their tool results:
# enough that the agent keeps the thread, small enough that a restore is no longer the most
# expensive thing the adapter does.
_HISTORY_CHARS = 120_000
_HISTORY_ROW_CHARS = 4_000      # a single tool result must not eat the budget on its own


def _history(path: Path) -> str:
    """The visible conversation as text, BOUNDED — see ``_HISTORY_CHARS``.

    Built newest-first, so what survives a cut is the part the next message is most likely about.
    A single enormous row (a test log, a whole file handed back) is clipped rather than allowed to
    consume the budget, and the clip SAYS it was clipped so the model is not left believing it
    read the whole thing.
    """
    import json
    if not path.exists():
        return ""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            content = row.get("message", {}).get("content", "")
            if isinstance(content, str):
                text = content
            else:
                # Retain observable messages/tools, without replaying reasoning.
                text = "\n".join(b.get("text", "") if b.get("type") == "text" else
                                  json.dumps(b, ensure_ascii=False) if b.get("type") in ("tool_use", "tool_result") else ""
                                  for b in content)
            if text.strip():
                rows.append(row.get("type", "message").upper() + ": " + text)
        except (ValueError, TypeError, AttributeError):
            continue
    kept: list[str] = []
    used = 0
    for text in reversed(rows):                       # newest first
        if len(text) > _HISTORY_ROW_CHARS:
            text = text[:_HISTORY_ROW_CHARS] + " …[truncated]"
        if kept and used + len(text) > _HISTORY_CHARS:
            break
        kept.append(text)
        used += len(text)
    kept.reverse()
    dropped = len(rows) - len(kept)
    head = (f"[{dropped} earlier message(s) omitted from this restore to keep it small]\n\n"
            if dropped else "")
    return head + "\n\n".join(kept)


def _compact(pid: str, feed: str, sid: str, cwd: str) -> dict:
    """`/compact` for DeepSeek, done with the one lever the SDK actually offers.

    The runtime HAS a real compaction — `@deepseek-ai/dsh-command-compact` runs
    `ctx.compaction.compactNow`, a model-written summary that replaces the history — but it is a
    CLIENT slash command. The SDK plane this adapter talks to exposes `session/prompt` and little
    else (`skills/list` is already "unknown method"), so there is no way to call it from here, and
    sending the words "/compact" as a prompt just asks the model what /compact means. That is what
    the Context meter's button used to do on a DeepSeek pane.

    What this adapter CAN do is exactly what it already does on a model switch or a Stop: close the
    runtime and let the next send rebuild the thread from `_history`, which is bounded at
    `_HISTORY_CHARS` (~30k tokens). That is the half of compaction the bill notices — the next
    request carries the tail instead of the whole session. So this is named for what it does, it
    says the number, and it leaves a line in the transcript; a "/compact" that quietly kept the
    whole history would be a switch that does nothing.
    """
    pdir = HOME / "projects" / pid
    transcript = pdir / (sid + ".jsonl") if sid else None
    before_bytes = 0
    if transcript is not None:
        try:
            before_bytes = transcript.stat().st_size
        except OSError:
            before_bytes = 0
    with _lock:
        rec = _runtimes.pop((pid, sid), None) if sid else None
    if rec is not None:
        _close(rec["harness"])
    # What the NEXT send will carry, measured the same way `_history` builds it.
    history = _history(transcript) if transcript is not None else ""
    note = (f"DeepSeek context compacted. The live conversation is closed; the next message "
            f"re-sends the last {len(history):,} characters of it (~{max(1, len(history) // 4):,} "
            f"tokens) instead of the whole session ({before_bytes:,} bytes on disk). The full "
            f"transcript is untouched — pick an earlier conversation from the header to reread it.")
    if transcript is not None:
        state = {"session": sid, "cwd": cwd, "model": MODELS[0], "transcript": transcript}
        try:
            _append(state, "assistant", [{"type": "text", "text": note}])
        except Exception:
            pass
    return {"ok": True, "agent": AGENT, "session": sid, "compacted": True,
            "rebuilt_chars": len(history), "transcript_bytes": before_bytes}


def is_sending(pid: str) -> bool:
    with _lock:
        return bool(_live.get(bare(pid), {}).get("working"))


def live_rows() -> list[tuple[str, bool]]:
    with _lock:
        return [(pid, bool(s["working"])) for pid, s in _live.items()]


def live_state(pid: str) -> dict:
    with _lock:
        state = _live.get(bare(pid), {})
        busy = bool(state.get("working"))
        return {"working": busy, "tokens": 0, "activity": state.get("activity", "") if busy else "",
                "elapsed": round(time.time() - state["started"], 1) if busy else 0,
                "text": state.get("text", "") if busy else "", "kind": "", "model": state.get("model", "")}


def cancel(pid: str) -> dict:
    with _lock:
        state = _live.get(bare(pid))
        if not state or not state.get("working"):
            return {"ok": True}
        state["cancelled"] = True
        h = state.get("harness") if state.get("ready") else None
        _runtimes.pop((bare(pid), state["session"]), None)
    if h:
        _close(h)
    # The worker clears working when shutdown actually finishes, so another send
    # cannot race an old runtime still holding this durable session.
    #
    # THIS IS WHY STOP COSTS A RESTORE. SDK 0.1.5 cannot re-attach a durable session id once its
    # process has exited, so ending the process throws away the one thing that made the next send
    # cheap — the live conversation. The next send therefore reconstructs the visible thread from
    # the transcript as text. That used to be unbounded (1.16M characters, ~290k tokens, measured
    # here on 2026-10-05) and is now capped by ``_history`` at ~30k tokens. Closing is not
    # optional: the turn is in flight inside that process and there is no other way to stop it.
    return {"ok": True}


def reset(pid: str) -> dict:
    """Close every runtime of this project, idle or not, so the next send rebuilds the thread from
    the transcript on disk.

    `cancel` is not enough for that: it only acts on a turn that is RUNNING. An edit-and-resend
    (`cc_session.rewind`) truncates the transcript while DeepSeek is idle, and `cancel` returned
    early, so the next send reused the live runtime — and the model still remembered every
    message the edit had just removed."""
    cancel(pid)
    p = bare(pid)
    with _lock:
        recs = [_runtimes.pop(k) for k in list(_runtimes) if k[0] == p]
    for rec in recs:
        try:
            _close(rec["harness"])
        except Exception:
            pass
    return {"ok": True, "closed": len(recs)}


def _first_message_notes() -> str:
    """What a new DeepSeek runtime is told about the Studio: the code-graph command when the graph
    switch is on, and BOOST's directive when BOOST is on.

    The SAME text Codex gets as its developer instructions (`codex_app._developer_notes`), so the two
    engines cannot drift apart on how to reach the graph. A note never fails a send."""
    try:
        from .codex_app import _developer_notes
        return _developer_notes()
    except Exception:
        return ""


# AN IDLE RUNTIME IS CLOSED AFTER THIS LONG. Each one is a process of 100-185 MB, and nothing used to
# close one at all. The price of closing is small: the next send rebuilds the thread from the
# transcript, bounded by `_HISTORY_CHARS` (~30k tokens, about one US cent at the Flash peak rate).
# `deepseek_idle_minutes` in settings changes it; 0 keeps runtimes open until the backend stops.
_IDLE_MINUTES_DEFAULT = 30
_REAP_EVERY_S = 60.0
_reaper_on = False


def _idle_limit_s() -> float:
    try:
        from .config import settings
        minutes = float(settings.get("deepseek_idle_minutes", _IDLE_MINUTES_DEFAULT))
    except (TypeError, ValueError):
        minutes = float(_IDLE_MINUTES_DEFAULT)
    return max(0.0, minutes) * 60.0


def reap_idle(now: float | None = None) -> int:
    """Close runtimes that have done nothing for the idle limit. Never one whose project has a turn
    running. Returns how many were closed."""
    limit = _idle_limit_s()
    if limit <= 0:
        return 0
    now = time.time() if now is None else now
    with _lock:
        busy = {p for p, s in _live.items() if s.get("working")}
        old = [k for k, r in _runtimes.items()
               if k[0] not in busy and now - float(r.get("used") or now) >= limit]
        recs = [_runtimes.pop(k) for k in old]
    for rec in recs:
        try:
            _close(rec["harness"])
        except Exception:
            pass
    return len(recs)


def _start_reaper() -> None:
    global _reaper_on
    with _lock:
        if _reaper_on:
            return
        _reaper_on = True

    def loop() -> None:
        while True:
            time.sleep(_REAP_EVERY_S)
            try:
                reap_idle()
            except Exception:
                pass
    threading.Thread(target=loop, name="dsh-reaper", daemon=True).start()


def shutdown() -> None:
    for pid, busy in live_rows():
        if busy:
            cancel(pid)
    with _lock:
        runtimes = list(_runtimes.values())
        _runtimes.clear()
    for rec in runtimes:
        _close(rec["harness"])

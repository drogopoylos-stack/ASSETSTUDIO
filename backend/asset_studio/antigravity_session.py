"""Google Antigravity CLI (`agy`) as a Studio engine, beside Claude, Codex and DeepSeek.

WHY THIS ENGINE EXISTS. Since 2026-06-18 the Gemini CLI refuses personal Google accounts
("This client is no longer supported for Gemini Code Assist for individuals") and Google moved
them to Antigravity. `agy` signs in with the Google account (no API key), offers Gemini, Claude
and GPT-OSS models (`agy models`), and its agent has a native image generator (Nano Banana) —
which is how a Studio agent gets Nano Banana without a Gemini API key. See `generate_image`.

HOW A TURN RUNS. One `agy -p <prompt> --output-format stream-json` process per turn, in the
project folder. The first turn of a conversation reports agy's `conversation_id` in its `init`
event; later turns pass `--conversation <id>`, so agy keeps the real history and nothing is
replayed from here. Read off agy 1.5.9 on 2026-11-15:

    {"event":"init","conversation_id":…,"init":{"cwd","tools":[…],"model","permission_mode"}}
    {"event":"step_update","step_update":{"step_index":2,"state":"ACTIVE","step_type":"tool",
        "tool_name":"read_file","tool_info":{"name","parameters":{…}}}}
    … the same step "DONE" with tool_info.output …
    {"event":"step_update","step_update":{"step_type":"agent_response","state":"ACTIVE",
        "text_delta":"…"}}   (many; the last is "DONE" with usage)
    {"event":"result","result":{"status":"SUCCESS"|"ERROR","response","error","usage",…}}

THE TRANSCRIPT is the same small Claude-format .jsonl DeepSeek writes (see deepseek_session),
under the "antigravity--" alternate home, so the feed, the conversation picker and the context
readers show it with no code of their own.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

AGENT = "antigravity"
PREFIX = AGENT + "--"
HOME = DATA_DIR / "antigravity-home"
BRAIN = Path.home() / ".gemini" / "antigravity-cli" / "brain"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_lock = threading.RLock()
_live: dict[str, dict] = {}
_models_cache: dict = {"at": 0.0, "rows": []}
_MODELS_TTL_S = 600.0
# Windows caps a whole command line at 32,767 characters. A longer prompt goes to a file that the
# agent is told to read first, instead of failing the spawn.
_ARGV_LIMIT = 24_000
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def bare(pid: str) -> str:
    return pid.removeprefix(PREFIX)


def find_agy() -> Optional[str]:
    """agy.exe. The installer puts it in %LOCALAPPDATA%\\agy\\bin and adds that to the USER PATH —
    which a backend started before the install does not see, so the folder is checked directly."""
    p = shutil.which("agy")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        c = Path(local) / "agy" / "bin" / ("agy.exe" if os.name == "nt" else "agy")
        if c.is_file():
            return str(c)
    c = Path.home() / ".local" / "bin" / "agy"
    return str(c) if c.is_file() else None


def _flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def _read_models() -> list[list[str]]:
    exe = find_agy()
    rows: list[list[str]] = [["default", "Antigravity default"]]
    if not exe:
        return rows
    try:
        out = subprocess.run([exe, "models"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60, creationflags=_flags()).stdout
        for line in out.splitlines():
            parts = line.strip().split("	")
            if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", parts[0].strip()):
                rows.append([parts[0].strip(), parts[1].strip()])
    except Exception:
        pass
    return rows


def _refresh_models() -> list[list[str]]:
    rows = _read_models()
    if len(rows) > 1:
        with _lock:
            _models_cache.update(at=time.time(), rows=rows, busy=False)
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            (HOME / "models.json").write_text(json.dumps(rows), encoding="utf-8")
        except Exception:
            pass
    else:
        with _lock:
            _models_cache["busy"] = False
    return rows


def models(refresh: bool = False) -> list[list[str]]:
    """[[id, label], …] from `agy models` — the models THIS account can use.

    The list depends on the account and changes as Google rolls models out, so it is read from
    the CLI and never written down here. "default" (agy's own choice) is always first.

    `agy models` asks Google and takes seconds, and the agent list that carries this is read by
    four parts of the window. So the last list is kept on disk and returned at once; a list older
    than 10 minutes is refreshed in the background. Only the very first read ever waits."""
    if refresh:
        return _refresh_models()
    with _lock:
        rows = _models_cache.get("rows") or []
        if not rows:
            try:
                rows = json.loads((HOME / "models.json").read_text(encoding="utf-8"))
                _models_cache.update(rows=rows, at=0.0)
            except Exception:
                rows = []
        stale = time.time() - float(_models_cache.get("at") or 0.0) > _MODELS_TTL_S
        start = bool(rows) and stale and not _models_cache.get("busy")
        if start:
            _models_cache["busy"] = True
    if not rows:
        return _refresh_models()
    if start:
        threading.Thread(target=_refresh_models, name="agy-models", daemon=True).start()
    return rows


# --- transcript --------------------------------------------------------------------------------

def _append(state: dict, role: str, content, usage: Optional[dict] = None) -> None:
    message = {"role": role, "content": content, "model": state.get("model_shown") or state["model"]}
    if usage:
        message["usage"] = usage
    row = {"type": role, "uuid": uuid.uuid4().hex, "sessionId": state["session"],
           "cwd": state["cwd"], "timestamp": datetime.now(timezone.utc).isoformat(), "message": message}
    with state["transcript"].open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _meta_path(pid: str, sid: str) -> Path:
    return HOME / "projects" / pid / (sid + ".agy.json")


def _conversation_of(pid: str, sid: str) -> str:
    try:
        return str(json.loads(_meta_path(pid, sid).read_text(encoding="utf-8")).get("conversation") or "")
    except Exception:
        return ""


def _remember_conversation(pid: str, sid: str, cid: str) -> None:
    if not cid:
        return
    try:
        _meta_path(pid, sid).write_text(json.dumps({"conversation": cid}), encoding="utf-8")
    except Exception:
        pass


def _publish(state: dict, force: bool = False) -> None:
    """Push live state over the WebSocket (see deepseek_session._publish)."""
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


_TOOL_NAMES = {"read_file": "Read", "write_to_file": "Write", "replace_file_content": "Edit",
               "multi_replace_file_content": "Edit", "run_command": "PowerShell" if os.name == "nt" else "Bash",
               "grep_search": "Grep", "find_by_name": "Glob", "list_dir": "LS", "view_file": "Read",
               "search_web": "WebSearch", "read_url_content": "WebFetch", "invoke_subagent": "Task"}


def _usage(u: dict) -> dict:
    u = u or {}
    return {"input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0) + int(u.get("thinking_tokens") or 0),
            "cache_read_input_tokens": int(u.get("cache_read_tokens") or 0)}


def _on_event(state: dict, ev: dict) -> None:
    state["seen"] = int(state.get("seen") or 0) + 1
    kind = ev.get("event")
    if kind == "init":
        cid = str(ev.get("conversation_id") or "")
        if cid and not state.get("conversation"):
            state["conversation"] = cid
            _remember_conversation(state["pid"], state["session"], cid)
        init = ev.get("init") or {}
        if init.get("model"):
            state["model_shown"] = init["model"]
        state["activity"] = "Thinking"
    elif kind == "step_update":
        s = ev.get("step_update") or {}
        typ, st, idx = s.get("step_type"), s.get("state"), s.get("step_index")
        if typ == "agent_response":
            state["text"] = (state.get("text") or "") + str(s.get("text_delta") or "")
            state["activity"] = "Writing"
            if st == "DONE":
                text = state.get("text") or ""
                usage = _usage(s.get("usage") or {})
                if text.strip():
                    _append(state, "assistant", [{"type": "text", "text": text}], usage)
                with _lock:
                    state["turn_in"] = int(state.get("turn_in") or 0) + usage["input_tokens"]
                    state["turn_out"] = int(state.get("turn_out") or 0) + usage["output_tokens"]
                    state["turn_cache"] = int(state.get("turn_cache") or 0) + usage["cache_read_input_tokens"]
                state["text"] = ""
                state["activity"] = "Working"
        elif typ in ("tool", "subagent"):
            info = s.get("tool_info") or {}
            raw = info.get("name") or s.get("tool_name") or "tool"
            name = _TOOL_NAMES.get(raw, raw)
            call_id = f"agy-{state.get('conversation') or 'x'}-{state.get('turn_no', 0)}-{idx}"
            params = info.get("parameters") if typ == "tool" else s.get("subagent_info")
            if not isinstance(params, dict):
                params = {"value": params}
            opened = state.setdefault("open_tools", set())
            if call_id not in opened:
                opened.add(call_id)
                _append(state, "assistant", [{"type": "tool_use", "id": call_id, "name": name, "input": params}])
            state["activity"] = "Running " + name
            if st == "DONE":
                if typ == "tool":
                    out = info.get("output")
                else:
                    out = json.dumps(s.get("subagent_info") or {}, ensure_ascii=False)
                text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                _append(state, "user", [{"type": "tool_result", "tool_use_id": call_id,
                                         "content": [{"type": "text", "text": (text or "done")[:20000]}],
                                         "is_error": False}])
                state["activity"] = "Working"
    elif kind == "result":
        r = ev.get("result") or {}
        state["result"] = r
        if r.get("conversation_id") and not state.get("conversation"):
            state["conversation"] = r["conversation_id"]
            _remember_conversation(state["pid"], state["session"], r["conversation_id"])
        if str(r.get("status")).upper() != "SUCCESS" and r.get("error"):
            _append(state, "assistant", [{"type": "text", "text": "Antigravity: " + str(r["error"])[:2000]}])
    _publish(state, force=(kind == "result"))


def _bank_turn(state: dict) -> None:
    """Bank the turn's tokens in the ledger. Cost 0: the turn runs on the Google sign-in, so there
    is no per-token price to charge (`basis` says so)."""
    from . import turns
    with _lock:
        tin, tout = int(state.get("turn_in") or 0), int(state.get("turn_out") or 0)
        state["turn_in"] = state["turn_out"] = state["turn_cache"] = 0
    if not (tin or tout):
        return
    try:
        turns.record(PREFIX + state["pid"], str(state.get("model_shown") or state["model"]), "default",
                     tout, 0.0, max(0.0, time.time() - float(state.get("started") or time.time())),
                     0, 0.0, False, 0, "subscription")
    except Exception:
        pass


def _permission_args(mode: str) -> list[str]:
    """The chat box's permission mode as agy flags. Headless agy auto-DENIES any tool that asks
    for permission (a shell command, for one), so the Studio's default here is full access."""
    m = (mode or "").lower()
    if m == "plan":
        return ["--mode", "plan"]
    if m in ("acceptedits", "accept-edits", "default"):
        return ["--mode", "accept-edits"]
    return ["--dangerously-skip-permissions"]


def _first_message_notes(pid: str) -> str:
    """The Studio's standing notes (code-graph command, BOOST), as Codex and DeepSeek get them."""
    try:
        from .codex_app import _developer_notes
        return _developer_notes(pid)
    except Exception:
        return ""


def _kill(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           timeout=10, creationflags=_flags())
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def send(project_id: str, message: str, *, path: str = "", model: str = "default",
         effort: str = "default", permission_mode: str = "full", new_session: bool = False,
         session: str = "", images=None, **_ignored) -> dict:
    from . import mission, cc_session
    pid = bare(project_id)
    if not pid or any(c in pid for c in "/\\") or pid in (".", ".."):
        return {"ok": False, "error": "Invalid project id"}
    shots = [str(p).strip().strip('"') for p in (images or []) if str(p).strip()]
    for s in shots:
        if not Path(s).is_file():
            return {"ok": False, "error": f"Attached image not found: {s}"}
    if not (message or "").strip() and not shots:
        return {"ok": False, "error": "empty message"}
    exe = find_agy()
    if not exe:
        return {"ok": False, "agent": AGENT, "needs_install": True,
                "error": "Antigravity CLI is not installed. In PowerShell: irm https://antigravity.google/cli/install.ps1 | iex"}
    if model != "default" and not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model or ""):
        return {"ok": False, "error": f"not a model id: {model!r}"}
    if session and not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session):
        return {"ok": False, "error": "Invalid session id"}
    cwd = path if path and Path(path).is_dir() else cc_session._resolve(pid)[0]
    if not cwd or not Path(cwd).is_dir():
        return {"ok": False, "error": "could not resolve this project's folder on disk"}
    feed = PREFIX + pid
    try:
        from .config import settings
        if settings.get("cc_graphify"):
            from . import graphify_index
            graphify_index.refresh(cwd)
    except Exception:
        pass

    shown = (message or "").strip()
    if shots:
        shown = (shown + "\n\nAttached image" + ("s" if len(shots) > 1 else "") + " — "
                 + ", ".join(shots)).strip()
    with _lock:
        if _live.get(pid, {}).get("working"):
            return {"ok": False, "error": "Antigravity is already working in this workspace; wait or stop the current turn."}
        _, latest = cc_session._resolve(feed)
        sid = uuid.uuid4().hex if new_session else (session or latest or uuid.uuid4().hex)
        pdir = HOME / "projects" / pid
        pdir.mkdir(parents=True, exist_ok=True)
        transcript = pdir / (sid + ".jsonl")
        conversation = "" if new_session else _conversation_of(pid, sid)
        state = {"working": True, "session": sid, "cwd": str(Path(cwd).resolve()), "pid": pid,
                 "model": model, "transcript": transcript, "started": time.time(), "sent": time.time(),
                 "activity": "Starting Antigravity", "text": "", "proc": None, "cancelled": False,
                 "conversation": conversation, "turn_in": 0, "turn_out": 0, "turn_cache": 0, "seen": 0,
                 "turn_no": int(time.time())}
        _live[pid] = state
        mission.remember_active_session(feed, sid)
        _append(state, "user", shown)

    prompt = (message or "").strip()
    if shots:
        prompt += ("\n\nThe user attached image file(s). Look at them before you answer:\n"
                   + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(shots)))
    if not conversation:
        notes = _first_message_notes(pid)
        if notes:
            prompt = notes + "\n\n---\n\n" + prompt
    if len(prompt) > _ARGV_LIMIT:
        big = pdir / (sid + f".prompt-{int(time.time())}.md")
        big.write_text(prompt, encoding="utf-8")
        prompt = (f"The full message is too long for the command line. Read the file {big} first, "
                  f"and treat its content as the user's message.")

    args = [exe, "-p", prompt, "--output-format", "stream-json"]
    if model and model != "default":
        args += ["--model", model]
    if (effort or "").lower() in EFFORTS:
        args += ["--effort", effort.lower()]
    if conversation:
        args += ["--conversation", conversation]
    args += _permission_args(permission_mode)

    def run() -> None:
        errf = None
        try:
            errf = open(pdir / (sid + ".stderr.log"), "wb")
            proc = subprocess.Popen(args, cwd=state["cwd"], stdout=subprocess.PIPE, stderr=errf,
                                    stdin=subprocess.DEVNULL, creationflags=_flags())
            with _lock:
                state["proc"] = proc
                if state["cancelled"]:
                    _kill(proc)
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                try:
                    _on_event(state, ev)
                except Exception:
                    pass
            proc.wait()
            if state.get("text", "").strip():   # a response cut off before its DONE step
                _append(state, "assistant", [{"type": "text", "text": state["text"]}])
                state["text"] = ""
            if not state.get("result") and not state["cancelled"]:
                tail = ""
                try:
                    errf.flush()
                    tail = (pdir / (sid + ".stderr.log")).read_text(encoding="utf-8", errors="replace")[-1500:]
                except Exception:
                    pass
                hint = (" Sign in again: open a terminal and run `agy` once."
                        if "auth" in tail.lower() else "")
                _append(state, "assistant", [{"type": "text", "text":
                         f"Antigravity stopped without an answer (exit {proc.returncode}).{hint}\n\n{tail.strip()}"}])
            _bank_turn(state)
        except Exception as exc:
            if not state["cancelled"]:
                _append(state, "assistant", [{"type": "text", "text": "Antigravity error: " + str(exc)[:2000]}])
        finally:
            if errf:
                try:
                    errf.close()
                except Exception:
                    pass
            with _lock:
                state["working"] = False
                state["activity"] = ""
                state["proc"] = None
            _publish(state, force=True)

    threading.Thread(target=run, name="antigravity-" + pid[:24], daemon=True).start()
    return {"ok": True, "agent": AGENT, "session": sid, "streaming": True}


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
                "text": state.get("text", "") if busy else "", "kind": "",
                "model": state.get("model_shown") or state.get("model", "")}


def cancel(pid: str) -> dict:
    with _lock:
        state = _live.get(bare(pid))
        if not state or not state.get("working"):
            return {"ok": True}
        state["cancelled"] = True
        proc = state.get("proc")
    _kill(proc)
    return {"ok": True}


def reset(pid: str) -> dict:
    """After an edit-and-resend the transcript no longer matches agy's own history, so the next
    send starts a fresh agy conversation (the meta file is dropped) instead of resuming one that
    still remembers the removed messages."""
    cancel(pid)
    p = bare(pid)
    try:
        for f in (HOME / "projects" / p).glob("*.agy.json"):
            f.unlink()
    except Exception:
        pass
    return {"ok": True}


def shutdown() -> None:
    for pid, busy in live_rows():
        if busy:
            cancel(pid)


# --- images (Nano Banana through the agent's own image generator) ------------------------------

def _images_in(text: str) -> list[Path]:
    out = []
    for m in re.finditer(r"file:///([^)\s\]]+?\.(?:png|jpe?g|webp|gif))", text or "", flags=re.I):
        p = Path(m.group(1).replace("%20", " "))
        if p.is_file():
            out.append(p)
    return out


def generate_image(prompt: str, out: str = "", refs: Optional[list[str]] = None,
                   model: str = "default", timeout_s: int = 300) -> dict:
    """Make one image with Antigravity's image generator and copy it to `out`.

    Runs in an empty scratch folder, so the agent has nothing to wander into. agy writes the
    image under ~/.gemini/antigravity-cli/brain/<conversation>/ and links it in the answer; the
    newest image there is the fallback when the answer has no link."""
    exe = find_agy()
    if not exe:
        return {"ok": False, "error": "Antigravity CLI is not installed"}
    if not (prompt or "").strip():
        return {"ok": False, "error": "empty prompt"}
    work = HOME / "image-jobs" / uuid.uuid4().hex[:12]
    work.mkdir(parents=True, exist_ok=True)
    ref_names = []
    for i, r in enumerate(refs or []):
        src = Path(str(r).strip().strip('"'))
        if not src.is_file():
            return {"ok": False, "error": f"reference image not found: {r}"}
        dst = work / f"reference_{i + 1}{src.suffix.lower()}"
        shutil.copyfile(src, dst)
        ref_names.append(str(dst))
    ask = "Generate an image of " + prompt.strip() if not re.match(r"(?i)\s*(generate|create|make|draw)\b", prompt) else prompt.strip()
    if ref_names:
        ask += ("\n\nUse these reference image(s) as input for the image generator: "
                + ", ".join(ref_names))
    ask += "\n\nGenerate exactly one image. Do not ask questions. Reply with the image file link only."
    args = [exe, "-p", ask, "--output-format", "json", "--print-timeout", f"{int(timeout_s)}s"]
    if model and model != "default" and re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", model):
        args += ["--model", model]
    started = time.time()
    try:
        r = subprocess.run(args, cwd=str(work), capture_output=True, timeout=timeout_s + 30,
                           stdin=subprocess.DEVNULL, creationflags=_flags())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Antigravity did not finish in {timeout_s} s"}
    try:
        res = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": "no answer from agy: " + r.stderr.decode("utf-8", "replace")[-800:]}
    cid = str(res.get("conversation_id") or "")
    found = _images_in(res.get("response") or "")
    if not found and cid and (BRAIN / cid).is_dir():
        found = sorted((p for p in (BRAIN / cid).rglob("*") if p.suffix.lower() in _IMAGE_EXT
                        and p.stat().st_mtime >= started - 5 and ".user_uploaded" not in p.parts),
                       key=lambda p: p.stat().st_mtime)
    if not found:
        return {"ok": False, "status": res.get("status"), "conversation_id": cid,
                "error": res.get("error") or "the agent answered without an image",
                "response": (res.get("response") or "")[:1500]}
    img = found[-1]
    if out:
        dst = Path(out)
        if dst.suffix.lower() not in _IMAGE_EXT or dst.is_dir():
            dst = (dst if dst.is_dir() else dst.parent) / (dst.stem if dst.suffix else img.stem)
        # the real format wins over the asked-for extension (agy returns JPEG today)
        dst = dst.with_suffix(img.suffix.lower())
    else:
        dst = DATA_DIR / "antigravity-images" / img.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(img, dst)
    try:
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass
    size = None
    try:
        from PIL import Image
        with Image.open(dst) as im:
            size = list(im.size)
    except Exception:
        pass
    return {"ok": True, "path": str(dst), "source": str(img), "size": size,
            "conversation_id": cid, "seconds": round(time.time() - started, 1)}

"""REMOTE — the Studio on your phone, over Discord.

One switch (``settings["remote"]``, the button above BOOST). ON means the PC can be left
working while you are not at it:

1. OUT — when a turn in any conversation FINISHES, a message goes to your Discord channel:
   the project, the engine that did the work, how long it took, and the last thing the agent
   said. A turn that produced pictures attaches them (the files the agent named in its answer,
   plus the output of any job it delegated to another engine).
2. ASK — when an agent STOPS to ask something (a Codex approval, a question), that goes out
   too, so a turn does not sit blocked for four hours while you are out.
3. BACK — you REPLY to that Discord message on the phone, and the text is delivered to the
   agent that sent it. A turn still running is STEERED (Claude and Codex can take a mid-turn
   message); an idle conversation gets a normal turn. Photos attached to your reply are saved
   and handed over as images.

WHY DISCORD AND NOT WHATSAPP
----------------------------
WhatsApp cannot do this without a business account: the Cloud API needs a verified Meta
business, a registered number that is no longer usable in the normal app, message templates
approved by Meta, and it only lets a service answer inside a 24-hour window after the user
writes. Discord needs a bot token and a channel id, has no template review, no reply window,
and takes file attachments up to the channel's limit. Same phone, same push notification,
none of the review. If WhatsApp is ever wanted, this module is the seam: `_post` and `_pull`
are the only two places that know the transport.

HOW THE ROUTING IS CORRECT
--------------------------
Every message sent out records which conversation it is about (``{"m": <discord message id>,
"feed": ..., "bare": ..., "agent": ...}``). A reply carries ``message_reference.message_id``,
so the answer goes to the conversation that asked — not to "the last one", which is wrong the
moment two agents work in parallel. A reply that is NOT a reply to anything (just typed into
the channel) goes to the most recent conversation, and the Studio says out loud which one it
picked. A reply can also be addressed by hand: start it with ``@codex`` or ``@claude`` to pick
the engine in the most recent project.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No gateway websocket, no discord.py, no new dependency: a bot token over plain HTTPS with
``urllib``, one GET every few seconds. No mirroring of the whole conversation — the phone gets
the OUTCOME of a turn, not every tool call, because the point is to be away from the screen.
Nothing is sent while the switch is off, and nothing at all is sent until a token and a channel
are set.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

from . import engines
from .config import DATA_DIR, settings

API = "https://discord.com/api/v10"
STATE_PATH = DATA_DIR / "remote.json"
INBOX = DATA_DIR / "remote" / "inbox"          # photos that arrived from the phone

# Defaults. Each is a settings key, so they can be tuned without a code change.
POLL_S = 5                  # how often replies are read (one small GET)
TICK_S = 2                  # how often busy state is sampled (local, no network)
MIN_TURN_S = 20             # a turn shorter than this is not worth a push notification
MAX_IMAGES = 4
MAX_BYTES = 9 * 1024 * 1024         # a non-boosted Discord channel takes 10 MB per message
BODY_CHARS = 1400                   # Discord's hard limit is 2000 for the whole message
ROUTES_KEPT = 60

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_state: dict = {}
_busy: dict[str, bool] = {}          # feed id -> was it working on the last tick
_start: dict[str, float] = {}        # feed id -> when this turn started
_asked: dict[str, float] = {}        # feed id -> when "needs your answer" was last sent
_owed: dict[str, float] = {}         # feed id -> when the PHONE started this turn
_IMG = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s\"'`<>|*?\n]+\.(?:png|jpe?g|webp|gif|bmp)",
                  re.IGNORECASE)


# ---------------------------------------------------------------------------
# settings + state
# ---------------------------------------------------------------------------

def enabled() -> bool:
    return bool(settings.get("remote", False))


def _cfg() -> tuple[str, str]:
    """(bot token, channel id). Both live in data/settings.json, which is never committed."""
    return (str(settings.get("remote_token", "") or "").strip(),
            str(settings.get("remote_channel", "") or "").strip())


def ready() -> bool:
    tok, ch = _cfg()
    return bool(tok and ch)


def _num(key: str, default: int) -> int:
    try:
        n = int(settings.get(key, default) or default)
    except Exception:
        return default
    return n if n > 0 else default


def _load() -> dict:
    global _state
    if _state:
        return _state
    try:
        _state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _state = {}
    _state.setdefault("after", "")        # the newest Discord message already seen
    _state.setdefault("routes", [])       # [{m, feed, bare, agent, name, ts}]
    _state.setdefault("sent", 0)
    _state.setdefault("replies", 0)
    _state.setdefault("last_error", "")
    _state.setdefault("last_sent", 0.0)
    _state.setdefault("last_reply", 0.0)
    return _state


def _save() -> None:
    st = _load()
    st["routes"] = (st.get("routes") or [])[-ROUTES_KEPT:]
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def _fail(msg: str) -> None:
    st = _load()
    st["last_error"] = str(msg)[:400]
    _save()


# ---------------------------------------------------------------------------
# the transport: Discord over plain HTTPS
# ---------------------------------------------------------------------------

def _call(method: str, path: str, body: Any = None, files: Optional[list[tuple[str, bytes]]] = None,
          timeout: float = 25.0) -> tuple[int, Any]:
    """One Discord REST call. Returns (status, parsed json or text). Never raises."""
    tok, _ = _cfg()
    if not tok:
        return 0, {"error": "no bot token"}
    url = API + path
    data: Optional[bytes] = None
    headers = {"Authorization": "Bot " + tok, "User-Agent": "AssetStudio (remote, 1.0)"}
    if files:
        boundary = "----studio" + uuid.uuid4().hex
        parts: list[bytes] = []
        payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        parts.append(('--%s\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                      'Content-Type: application/json\r\n\r\n' % boundary).encode("utf-8"))
        parts.append(payload + b"\r\n")
        for i, (name, blob) in enumerate(files):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="files[%d]"; filename="%s"\r\n'
                          'Content-Type: application/octet-stream\r\n\r\n' % (boundary, i, name)).encode("utf-8"))
            parts.append(blob + b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
        data = b"".join(parts)
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        payload: Any = raw
        try:
            payload = json.loads(raw)
        except Exception:
            pass
        # Discord asks for a wait rather than refusing; honour it once and let the caller retry.
        if e.code == 429 and isinstance(payload, dict):
            time.sleep(min(float(payload.get("retry_after") or 1.0), 5.0))
        return e.code, payload
    except Exception as e:                      # offline, DNS, timeout: not an error worth a crash
        return 0, {"error": str(e)[:200]}


def _post(text: str, files: Optional[list[tuple[str, bytes]]] = None,
          reply_to: str = "") -> Optional[str]:
    """Put one message in the channel. Returns its Discord id, so a reply can be routed back."""
    _, ch = _cfg()
    if not ch:
        return None
    body: dict = {"content": text[:1990], "allowed_mentions": {"parse": []}}
    if reply_to:
        body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
    code, res = _call("POST", "/channels/%s/messages" % ch, body, files)
    if code == 429:                                             # rate limited: one retry
        code, res = _call("POST", "/channels/%s/messages" % ch, body, files)
    if code // 100 != 2:
        # An attachment that the channel refuses must not cost the message itself.
        if files:
            return _post(text + "\n_(the images were too large for this channel)_")
        _fail("send failed: %s %s" % (code, res))
        return None
    st = _load()
    st["sent"] = int(st.get("sent") or 0) + 1
    st["last_sent"] = time.time()
    st["last_error"] = ""
    _save()
    return str((res or {}).get("id") or "") if isinstance(res, dict) else None


def _react(mid: str, emoji: str = "✅") -> None:
    """Tick the reply, so the phone shows it was taken without another message in the channel."""
    _, ch = _cfg()
    if ch and mid:
        _call("PUT", "/channels/%s/messages/%s/reactions/%s/@me"
              % (ch, mid, urllib.parse.quote(emoji, safe="")), timeout=10.0)


def _pull() -> list[dict]:
    """The messages written since the last look, oldest first. People only, never a bot."""
    _, ch = _cfg()
    if not ch:
        return []
    st = _load()
    after = str(st.get("after") or "")
    # The FIRST look asks for one message only: that stores where the channel is now, so the
    # history written before REMOTE was switched on is never replayed as instructions.
    q = ("?limit=25&after=" + after) if after else "?limit=1"
    code, res = _call("GET", "/channels/%s/messages%s" % (ch, q), timeout=20.0)
    if code // 100 != 2 or not isinstance(res, list):
        if code:
            _fail("read failed: %s %s" % (code, res))
        return []
    msgs = list(reversed(res))
    if msgs:
        st["after"] = str(msgs[-1].get("id") or after)
        _save()
    if not after:
        return []       # first look: remember where the channel is, never replay its history
    return [m for m in msgs if not (m.get("author") or {}).get("bot")]


# ---------------------------------------------------------------------------
# who a message is about
# ---------------------------------------------------------------------------

def _agent_for(feed: str) -> str:
    """The agent id the chat box would send for this conversation: claude, codex, kimi…"""
    e = engines.for_feed(feed)
    if e is not engines.CLAUDE:
        return e.id
    try:
        from . import mission
        p = mission.alt_prefix(feed) or ""
    except Exception:
        p = ""
    return p[:-2] if p else "claude"


def _project_name(bare: str) -> str:
    try:
        from . import workspace
        for r in workspace.roots():
            if r.get("id") == bare:
                return str(r.get("name") or bare)
    except Exception:
        pass
    return bare


def _target(feed: str) -> dict:
    """Everything needed to talk to this conversation later. `send` wants the BARE folder id plus
    the agent id — an engine prefixes the feed itself — so both are stored, not just the feed."""
    bare = engines.bare_folder(feed)
    return {"feed": feed, "bare": bare, "agent": _agent_for(feed),
            "engine": engines.name_of(feed), "name": _project_name(bare)}


def _remember(mid: str, tgt: dict) -> None:
    if not mid:
        return
    st = _load()
    st.setdefault("routes", []).append({"m": str(mid), "ts": time.time(), **tgt})
    _save()


def _route(msg: dict) -> tuple[Optional[dict], str]:
    """(where this reply goes, why). The reference first, then an @engine, then the newest."""
    st = _load()
    routes = list(st.get("routes") or [])
    if not routes:
        # Nothing has been sent yet, so there is no reply to follow - but a message that NAMES a
        # project and an engine is a whole instruction on its own, and starts the turn.
        named, _clean = _address(str(msg.get("content") or ""))
        return (named, "named") if named else (None, "nothing has been sent yet")
    ref = str(((msg.get("message_reference") or {}).get("message_id")) or "")
    if ref:
        for r in reversed(routes):
            if r.get("m") == ref:
                return r, "reply"
    text = str(msg.get("content") or "").strip()
    # A PROJECT NAMED BY HAND WINS over "the conversation that engine last had": writing the
    # project is the one way to say which folder, and it would be useless if the last route
    # could override it.
    named, _clean = _address(text)
    if named and _flat(str(named.get("name") or "")) in _flat(text):
        return named, "named"
    m = re.match(r"^@([A-Za-z0-9_-]+)\b", text)
    if m:
        want = m.group(1).lower()
        for r in reversed(routes):
            if str(r.get("agent") or "").lower() == want or str(r.get("engine") or "").lower() == want:
                return r, "@" + want
    if named:
        return named, "named"
    return routes[-1], "newest"


def _strip_address(text: str) -> str:
    return re.sub(r"^@[A-Za-z0-9_-]+[\s,:]*", "", text or "", count=1)


def _engine_words() -> dict[str, str]:
    """{the word a person writes: the agent id the chat box sends}. Every engine, plus the
    alternate ones (Kimi, Qwen, a chat provider), so a message can ask for any of them by name."""
    out: dict[str, str] = {}
    for e in engines.ALL:
        out[e.id.lower()] = e.id
        out[e.name.lower()] = e.id
    out["deepseek"] = engines.DEEPSEEK.id        # nobody types "deepseek-harness" on a phone
    out["dsh"] = engines.DEEPSEEK.id
    try:
        from . import mission
        for pfx in mission.alt_homes():
            out[pfx[:-2].lower()] = pfx[:-2]
    except Exception:
        pass
    return out


def _flat(name: str) -> str:
    """A name as a person types it: lower case, dashes and underscores as spaces."""
    return " ".join(re.sub(r"[-_]+", " ", str(name or "")).lower().split())


def _engine_target(agent: str, bare: str) -> dict:
    """The conversation to talk to, named by engine and folder rather than by a message. An
    alternate engine has a session universe of its own, so its prefix is put back on here."""
    e = engines.for_agent(agent)
    feed = e.feed_id(bare)
    if e is engines.CLAUDE and agent != "claude":
        try:
            from . import mission
            for pfx in mission.alt_homes():
                if pfx[:-2] == agent:
                    feed = pfx + bare
                    break
        except Exception:
            pass
    return {"feed": feed, "bare": bare, "agent": agent,
            "engine": engines.name_of(feed), "name": _project_name(bare)}


def _address(text: str) -> tuple[Optional[dict], str]:
    """A message that NAMES where it goes, so a turn can be STARTED from the phone without
    replying to anything: "@codex add a jump" and "in Asset Studio ask deepseek for the weather"
    both land. The project is matched against the names of the open workspaces.

    It only wins when it is unambiguous: a project is named, or an engine is written with an "@".
    A message that merely mentions an engine in passing still follows the reply it answers, which
    is why "yes, claude, go on" cannot start a second conversation by accident."""
    body = str(text or "")
    low = _flat(body)
    words = _engine_words()
    agent = at = ""
    m = re.search(r"@([A-Za-z0-9_-]+)", body)
    if m and m.group(1).lower() in words:
        agent, at = words[m.group(1).lower()], m.group(0)
    if not agent:
        for w in sorted(words, key=len, reverse=True):
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(_flat(w)), low):
                agent = words[w]
                break
    bare = ""
    try:
        from . import workspace
        best = 0
        for r in workspace.roots():
            nm = _flat(r.get("name") or "")
            if nm and len(nm) > best and nm in low:
                bare, best = str(r.get("id") or ""), len(nm)
    except Exception:
        pass
    if not bare and not at:
        return None, body                   # nothing certain was named: follow the reply instead
    routes = list(_load().get("routes") or [])
    if not bare:                            # "@codex do x": the project it last wrote about...
        for r in reversed(routes):
            if (not agent) or str(r.get("agent") or "") == agent:
                bare = str(r.get("bare") or "")
                break
    if not bare and routes:                 # ...or, for an engine that has not written yet, the
        bare = str(routes[-1].get("bare") or "")    # folder the last message was about, which is
                                            # the one the person is thinking about
    if not bare:                            # ...or the only workspace there is
        try:
            from . import workspace
            rs = workspace.roots()
            if len(rs) == 1:
                bare = str(rs[0].get("id") or "")
        except Exception:
            pass
    if not bare:
        return None, body
    if not agent:                           # a project named with no engine: the one already
        for r in reversed(routes):          # talking about it, else Claude
            if str(r.get("bare") or "") == bare:
                agent = str(r.get("agent") or "")
                break
        agent = agent or "claude"
    clean = body.replace(at, "", 1).strip() if at else body
    clean = re.sub(r"(?<![A-Za-z0-9])#([A-Za-z0-9_-]+)", r"\1", clean).strip()
    return _engine_target(agent, bare), clean


_HELP = (
    "**REMOTE** - what you can write here:\n"
    "• **reply** to one of my messages: that same agent gets your text, and a turn that is "
    "still running is steered\n"
    "• `@codex add a jump to the player`: that engine, in the project it last wrote about\n"
    "• `in <project> ask deepseek for the weather tonight`: names the project and the engine, "
    "and starts the turn even when nothing is running\n"
    "• a photo on your message is given to the agent as an image\n"
    "• `/projects` the workspaces · `/status` what is working · `/stop` cancel the turn you "
    "reply to · `/help` this\n"
    "Any other slash word is passed to the agent, so `/compact` still works.")


def _command(word: str, tgt: Optional[dict]) -> bool:
    """The few words answered HERE instead of being sent to an agent. Anything else is not one of
    them and goes through, so an engine's own slash commands keep working from the phone."""
    if word in ("/help", "/h", "/?"):
        _post(_HELP)
        return True
    if word in ("/projects", "/p", "/ls"):
        try:
            from . import workspace
            rows = ["• %s" % (r.get("name") or r.get("id")) for r in workspace.roots()[:20]]
        except Exception:
            rows = []
        _post("**Workspaces**\n" + ("\n".join(rows) or "none are open")
              + "\n_write: in <name> <what to do>, and name @claude, @codex or @deepseek_")
        return True
    if word in ("/status", "/s"):
        rows = []
        for feed, busy in sorted(_busy_now().items()):
            if busy:
                t = _target(feed)
                rows.append("• %s · %s · %s" % (
                    t["name"], t["engine"], _dur(time.time() - _start.get(feed, time.time()))))
        _post("**Working now**\n" + ("\n".join(rows) or "nothing is working"))
        return True
    if word == "/stop":
        if not tgt:
            _post("⚠️ Reply to the message of the agent you want to stop.")
            return True
        feed = str(tgt.get("feed") or "")
        try:
            engines.for_feed(feed).cancel(engines.bare_folder(feed))
            _owed.pop(feed, None)
            _post("⏹ %s · %s stopped." % (tgt.get("engine"), tgt.get("name")))
        except Exception as e:
            _post("⚠️ could not stop it: %s" % str(e)[:120])
        return True
    return False


# ---------------------------------------------------------------------------
# what the agent said, and the pictures it made
# ---------------------------------------------------------------------------

def _last_text(feed: str) -> str:
    """The last thing the agent actually said in this conversation, for every engine: the feed
    reader already speaks Claude transcripts, Codex threads and DeepSeek logs behind one call."""
    try:
        from . import mission
        fd = mission.project_feed(feed, 40, "", "text") or {}
    except Exception:
        return ""
    for ln in reversed(fd.get("lines") or []):
        if ln.get("kind") == "text" and str(ln.get("text") or "").strip():
            return str(ln["text"]).strip()
    return ""


def _shrink(path: Path) -> Optional[bytes]:
    """A picture too big for the channel, re-encoded small enough to arrive. Pillow if it is
    installed; otherwise the file is named in the text instead of attached."""
    try:
        from PIL import Image                      # noqa: PLC0415 — optional, not a dependency
        import io
        im = Image.open(path)
        im.thumbnail((1600, 1600))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
        blob = buf.getvalue()
        return blob if len(blob) <= MAX_BYTES else None
    except Exception:
        return None


def _images(feed: str, text: str, since: float) -> tuple[list[tuple[str, bytes]], list[str]]:
    """(files to attach, the ones that could not be). Two honest sources: a file the agent named
    in its answer, and the output of a job it delegated to another engine during this turn."""
    if not bool(settings.get("remote_images", True)):
        return [], []
    paths: list[str] = []
    try:
        from . import delegates
        for c in delegates.cards(feed):
            out = str(c.get("output_file") or "")
            if out and not c.get("running") and float(c.get("ts") or 0) >= since - 10:
                paths.append(out)
    except Exception:
        pass
    paths += _IMG.findall(text or "")
    files: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    total = 0
    for p in paths:
        if len(files) >= MAX_IMAGES:
            break
        try:
            fp = Path(p.strip().strip("'\"()[]"))
            key = str(fp).lower()
            if key in seen or not fp.is_file():
                continue
            seen.add(key)
            size = fp.stat().st_size
            blob = fp.read_bytes() if size <= MAX_BYTES else (_shrink(fp) or b"")
            if not blob or total + len(blob) > MAX_BYTES:
                skipped.append(fp.name)
                continue
            total += len(blob)
            files.append((fp.name, blob))
        except Exception:
            continue
    return files, skipped


def _dur(sec: float) -> str:
    sec = int(max(sec, 0))
    return "%dm %02ds" % (sec // 60, sec % 60) if sec >= 60 else "%ds" % sec


# ---------------------------------------------------------------------------
# out: a turn finished, or an agent is waiting for you
# ---------------------------------------------------------------------------

def _notify_done(feed: str, took: float) -> None:
    tgt = _target(feed)
    text = _last_text(feed)
    files, skipped = _images(feed, text, time.time() - max(took, 60))
    body = " ".join(str(text).split())[:BODY_CHARS] or "(no closing message)"
    head = "✅ **%s** · %s · %s" % (tgt["name"], tgt["engine"], _dur(took))
    if skipped:
        body += "\n_too large to attach: %s_" % ", ".join(skipped[:3])
    mid = _post("%s\n%s\n_reply to this message → %s_" % (head, body, tgt["engine"]), files)
    _remember(mid or "", tgt)


def _notify_ask(feed: str, question: str) -> None:
    tgt = _target(feed)
    body = " ".join(str(question or "").split())[:BODY_CHARS] or "waiting for your answer"
    mid = _post("❓ **%s** · %s needs you\n%s\n_reply to this message → %s_"
                % (tgt["name"], tgt["engine"], body, tgt["engine"]), None)
    _remember(mid or "", tgt)


# ---------------------------------------------------------------------------
# back: your reply reaches the agent that wrote to you
# ---------------------------------------------------------------------------

def _save_attachments(msg: dict) -> list[str]:
    """Photos sent from the phone, on disk, so an engine can read them as images."""
    out: list[str] = []
    for a in (msg.get("attachments") or [])[:3]:
        url = str(a.get("url") or "")
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(a.get("filename") or "photo.jpg"))[:80]
        if not url or int(a.get("size") or 0) > 20 * 1024 * 1024:
            continue
        try:
            INBOX.mkdir(parents=True, exist_ok=True)
            dest = INBOX / ("%d-%s" % (int(time.time()), name))
            with urllib.request.urlopen(url, timeout=30) as r:
                dest.write_bytes(r.read())
            out.append(str(dest))
        except Exception:
            continue
    return out


def _deliver(msg: dict) -> None:
    raw = str(msg.get("content") or "").strip()
    tgt, why = _route(msg)
    # The handful of words REMOTE answers itself (/help, /projects, /status, /stop). Everything
    # else that starts with a slash belongs to the agent, so /compact and /steer still work.
    word = raw.split()[0].lower() if raw.startswith("/") else ""
    if word and _command(word, tgt):
        _react(str(msg.get("id") or ""))
        return
    text = raw
    if why.startswith("@"):
        text = _strip_address(raw)
    elif why == "named":
        _again, text = _address(raw)        # the same parse, with the @engine / #project removed
    text = text.strip()
    images = _save_attachments(msg)
    if not tgt:
        _post("⚠️ %s. Start a turn in the Studio, then reply here." % why)
        return
    if not text and not images:
        return
    feed, bare, agent = str(tgt.get("feed") or ""), str(tgt.get("bare") or ""), str(tgt.get("agent") or "claude")
    busy = _busy_now().get(feed, False)
    # A running turn is STEERED rather than queued, which is what a person on a phone means by
    # "no, do it the other way". Only Claude and Codex can take a message mid-turn.
    steer = bool(busy and agent in ("claude", "codex"))
    if busy and not steer:
        _post("⚠️ %s is still working and cannot take a message mid-turn. Try again when it stops."
              % tgt.get("engine"))
        return
    if not feed:
        _post("⚠️ that conversation is gone. Write: in <project> <what to do>.")
        return
    try:
        from . import cc_session
        res = cc_session.send(bare, text or "(see the attached image)", agent=agent,
                              images=images or None, steer=steer)
    except Exception as e:
        res = {"ok": False, "error": str(e)[:200]}
    if isinstance(res, dict) and res.get("ok") is False:
        _post("⚠️ %s: %s" % (tgt.get("engine"), res.get("error") or "the message was refused"))
        return
    st = _load()
    st["replies"] = int(st.get("replies") or 0) + 1
    st["last_reply"] = time.time()
    _save()
    # A TURN THE PHONE STARTED IS ALWAYS REPORTED BACK, however short it was. The length floor
    # exists so working at the PC does not buzz the phone for every ten-second turn; a question
    # asked FROM the phone is the opposite case - an answer that never arrives is the one failure
    # a remote user cannot work around.
    _owed[feed] = time.time()
    _react(str(msg.get("id") or ""), "✅" if not steer else "⚡")
    # Only say where it went when the choice was not obvious from the reply itself.
    if why != "reply":
        _post("→ %s · %s%s" % (tgt.get("engine"), tgt.get("name"),
                                         " (steered)" if steer else ""))


# ---------------------------------------------------------------------------
# the watcher
# ---------------------------------------------------------------------------

def _busy_now() -> dict[str, bool]:
    """feed id -> is that conversation working, for EVERY engine, keyed by the feed id only.

    `live_status()` answers under the prefixed id and the bare one, which would count an
    alternate engine's conversation twice; the live feed ids are collected here instead, and the
    busy flag is read for those keys. A parent waiting on subagents, or on a job it delegated to
    another engine, still counts as working — that is when the pictures arrive."""
    from . import cc_session
    try:
        st = cc_session.live_status() or {}
    except Exception:
        return {}
    stat = st.get("statuses") or {}
    fan = st.get("running_agents") or {}
    feeds: set[str] = set(cc_session._live.keys())
    for eng in engines.ALL:
        if eng is engines.CLAUDE:
            continue
        try:
            for bare, _b, _n in eng.live_rows():
                feeds.add(eng.feed_id(bare))
        except Exception:
            continue
    return {f: bool(stat.get(f)) or int(fan.get(f) or 0) > 0 for f in feeds}


def _awaiting(feed: str) -> str:
    """The question an engine is blocked on, or "" when it is not blocked. Codex reports it; the
    others have no such state, so they are silent here rather than guessed at."""
    try:
        state = engines.for_feed(feed).live_state(engines.bare_folder(feed)) or {}
    except Exception:
        return ""
    if not state.get("awaiting_input"):
        return ""
    return str(state.get("text") or "").strip() or "waiting for your answer"


def tick() -> None:
    """One pass: who stopped working since the last look, and who is stuck waiting.

    The switch is checked HERE and not only in the loop, so "nothing is sent while REMOTE is off"
    holds for every caller, including a thread that is on its way out."""
    if not (enabled() and ready()):
        return
    now = _busy_now()
    min_turn = _num("remote_min_turn_s", MIN_TURN_S)
    for feed, busy in now.items():
        was = _busy.get(feed, False)
        if busy and not was:
            _start[feed] = time.time()
        elif was and not busy:
            took = time.time() - _start.pop(feed, time.time())
            _asked.pop(feed, None)
            if took >= min_turn or _owed.pop(feed, 0):
                _notify_done(feed, took)
        if busy:
            q = _awaiting(feed)
            if q and not _asked.get(feed):
                _asked[feed] = time.time()
                _notify_ask(feed, q)
            elif not q:
                _asked.pop(feed, None)
    # A conversation can LEAVE the live list instead of going idle in it: a reaped Claude session,
    # a Codex thread that stopped. Gone is finished.
    for feed in [f for f, b in _busy.items() if b and f not in now]:
        took = time.time() - _start.pop(feed, time.time())
        _asked.pop(feed, None)
        if took >= min_turn or _owed.pop(feed, 0):
            _notify_done(feed, took)
    # ...and a turn the phone started can begin AND finish between two looks (a short question, a
    # cached answer, a refusal). Nothing above would ever see it as busy, so it is swept here.
    for feed, at in list(_owed.items()):
        if now.get(feed) or _busy.get(feed):
            continue
        if time.time() - at >= 8:
            _owed.pop(feed, None)
            _notify_done(feed, time.time() - at)
    _busy.clear()
    _busy.update(now)


def _loop() -> None:
    last_pull = 0.0
    while enabled():
        try:
            if ready():
                tick()
                if time.time() - last_pull >= _num("remote_poll_s", POLL_S):
                    last_pull = time.time()
                    for m in _pull():
                        _deliver(m)
        except Exception as e:
            _fail(str(e))
        time.sleep(TICK_S)
    with _lock:
        global _thread
        _thread = None


def _start_thread() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="remote-watch", daemon=True)
        _thread.start()


# ---------------------------------------------------------------------------
# the button
# ---------------------------------------------------------------------------

def status() -> dict:
    """What the REMOTE button shows. The token is never returned — only that one is set."""
    st = _load()
    tok, ch = _cfg()
    return {"on": enabled(), "ready": ready(), "transport": "discord",
            "token_set": bool(tok), "channel": ch,
            "bot": str(settings.get("remote_bot", "") or ""),
            "channel_name": str(settings.get("remote_channel_name", "") or ""),
            "images": bool(settings.get("remote_images", True)),
            "poll_s": _num("remote_poll_s", POLL_S),
            "min_turn_s": _num("remote_min_turn_s", MIN_TURN_S),
            "sent": int(st.get("sent") or 0), "replies": int(st.get("replies") or 0),
            "last_sent": float(st.get("last_sent") or 0), "last_reply": float(st.get("last_reply") or 0),
            "last_error": str(st.get("last_error") or ""),
            "watching": sum(1 for b in _busy.values() if b),
            "running": bool(_thread is not None and _thread.is_alive())}


def set_enabled(on: bool) -> dict:
    """The ON/OFF button, idempotent. ON primes the busy map from what is running RIGHT NOW, so a
    turn left running when the user walks away is the first thing reported when it ends."""
    on = bool(on)
    if on == enabled() and (not on or (_thread is not None and _thread.is_alive())):
        return status()
    settings.update({"remote": on})
    if on:
        _busy.clear()
        _start.clear()
        _asked.clear()
        try:
            for feed, busy in _busy_now().items():
                _busy[feed] = busy
                if busy:
                    _start[feed] = time.time()
        except Exception:
            pass
        _start_thread()
    return status()


def resume() -> dict:
    """Called once by the backend at startup: REMOTE survives a restart of the Studio."""
    if enabled() and ready():
        set_enabled(True)
    return status()


def configure(token: str = "", channel: str = "", images: Optional[bool] = None,
              min_turn_s: int = 0, poll_s: int = 0) -> dict:
    """Store the bot token and channel, after checking that both actually work."""
    patch: dict = {}
    if token:
        patch["remote_token"] = token.strip()
    if channel:
        patch["remote_channel"] = re.sub(r"[^0-9]", "", channel)
    if images is not None:
        patch["remote_images"] = bool(images)
    if min_turn_s:
        patch["remote_min_turn_s"] = int(min_turn_s)
    if poll_s:
        patch["remote_poll_s"] = int(poll_s)
    if patch:
        settings.update(patch)
    tok, ch = _cfg()
    if not tok:
        return {**status(), "ok": False, "error": "A Discord bot token is needed."}
    code, me = _call("GET", "/users/@me", timeout=15.0)
    if code // 100 != 2 or not isinstance(me, dict):
        return {**status(), "ok": False, "error": "Discord refused the token (%s)." % code}
    settings.update({"remote_bot": str(me.get("username") or "bot")})
    if not ch:
        return {**status(), "ok": False, "error": "A channel id is needed."}
    code, chan = _call("GET", "/channels/%s" % ch, timeout=15.0)
    if code // 100 != 2 or not isinstance(chan, dict):
        return {**status(), "ok": False,
                "error": "The bot cannot see that channel (%s). Invite it, or check the id." % code}
    settings.update({"remote_channel_name": str(chan.get("name") or ch)})
    _load()["last_error"] = ""
    _save()
    return {**status(), "ok": True}


def test() -> dict:
    """Send one message now, so the phone proves it works before the user walks away."""
    if not ready():
        return {"ok": False, "error": "Set the bot token and the channel first."}
    mid = _post("\U0001f4e1 **Asset Studio** is connected.\n"
                "You get a message when a turn finishes, with any pictures it made. "
                "Reply to that message and your answer goes to that agent.\n"
                "You can also start work from here: `in <project> ask deepseek for the weather`. "
                "Write `/help` for the rest.")
    if not mid:
        return {"ok": False, "error": _load().get("last_error") or "Discord did not take the message."}
    return {"ok": True, "id": mid, **status()}


def forget() -> dict:
    """Reset the counters and the routing table (the token and channel are kept)."""
    global _state
    _state = {}
    try:
        STATE_PATH.unlink()
    except Exception:
        pass
    _load()
    _save()
    return status()

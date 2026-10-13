# -*- coding: utf-8 -*-
"""REMOTE — the phone gets the right turn, and the reply reaches the right agent.

The three things that have to hold before someone can leave the PC working and walk away:

  1. silence     nothing is sent while the switch is off, or before Discord is connected
  2. out         a turn that FINISHES is reported once, for every engine, with its pictures
  3. back        a reply is delivered to the conversation that wrote, steered when it is busy

Discord is never called: `_post`, `_react` and `_pull` are replaced, because what is under test
is the detection and the routing, not the HTTP. No turn is started either: `cc_session.send` is
recorded instead of run.

Run:  backend/.venv/Scripts/python.exe backend/remote_test.py
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_data = Path(tempfile.mkdtemp(prefix="remote-data-"))
(_data / "tmp").mkdir(parents=True, exist_ok=True)
os.environ["ASSET_STUDIO_DATA"] = str(_data)

from asset_studio import cc_session                  # noqa: E402
from asset_studio import delegates as dg             # noqa: E402
from asset_studio import remote as rm                # noqa: E402
from asset_studio import workspace as ws             # noqa: E402
from asset_studio.config import settings             # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


# --- the project, and the two conversations in it ---------------------------------------------
PROJ = Path(tempfile.mkdtemp(prefix="remote-proj-"))
(PROJ / "main.py").write_text("print('hi')\n", encoding="utf-8")
ws.add_roots([str(PROJ)])
BARE = next(r["id"] for r in ws.roots() if Path(r["path"]).name == PROJ.name)
CX = "codex--" + BARE                       # the Codex conversation in the same folder

# --- Discord, stubbed -------------------------------------------------------------------------
SENT: list[dict] = []
REACTS: list[tuple[str, str]] = []
_mid = [1000]


def fake_post(text, files=None, reply_to=""):
    _mid[0] += 1
    SENT.append({"id": str(_mid[0]), "text": text, "files": list(files or [])})
    return str(_mid[0])


rm._post = fake_post
rm._react = lambda mid, emoji="x": REACTS.append((mid, emoji))
REAL_PULL = rm._pull            # kept, so the real channel reader can be tested too
rm._pull = lambda: []

# --- the engines, stubbed: a busy map and a closing message, no processes ----------------------
BUSY: dict = {}
TEXT: dict = {}
rm._busy_now = lambda: dict(BUSY)
rm._last_text = lambda feed: TEXT.get(feed, "")
rm._awaiting = lambda feed: ""

SEND: list[dict] = []


def fake_send(project_id, message, **kw):
    SEND.append({"project_id": project_id, "message": message, **kw})
    return {"ok": True}


cc_session.send = fake_send

settings.update({"remote_token": "zzsecretzz", "remote_channel": "4242", "remote_min_turn_s": 20,
                 "remote_images": True})

print("\n[1] Silence — the switch and the connection both have to be real")
check("1a off by default", rm.enabled() is False)
check("1b a token and a channel means ready to send", rm.status()["ready"] is True, rm.status())
settings.update({"remote_token": ""})
check("1c no token, not ready", rm.ready() is False and rm.status()["ready"] is False)
settings.update({"remote_token": "zzsecretzz"})
st = rm.status()
check("1d the token never reaches the UI, only that one is set",
      "zzsecretzz" not in repr(st) and st["token_set"] is True, st)
BUSY[BARE] = True
rm.tick()
BUSY[BARE] = False
rm._start[BARE] = time.time() - 300        # a long turn, so only the switch can hold it back
rm.tick()
check("1e a finished turn sends nothing while REMOTE is off", SENT == [], SENT)

print("\n[2] Out — a turn that finishes, once, for either engine")
settings.update({"remote": True})
SENT.clear()
TEXT[BARE] = "Added the exporter and fixed the panel."
BUSY.clear()
BUSY[BARE] = True
rm.tick()
check("2a a turn that STARTS says nothing", SENT == [], SENT)
rm._start[BARE] = time.time() - 222        # 3m 42s
BUSY[BARE] = False
rm.tick()
check("2b one message when it ends", len(SENT) == 1, SENT)
body = SENT[0]["text"] if SENT else ""
check("2c it names the project, the engine and the time",
      PROJ.name in body and "Claude" in body and "3m 42s" in body, body)
check("2d and what the agent said", "fixed the panel" in body, body)
check("2e it says how to answer", "reply to this message" in body.lower(), body)
rm.tick()
check("2f and never a second time for the same turn", len(SENT) == 1, SENT)

SENT.clear()
TEXT[CX] = "Codex finished the refactor."
BUSY[CX] = True
rm.tick()
rm._start[CX] = time.time() - 100
BUSY[CX] = False
rm.tick()
check("2g a CODEX conversation is reported the same way",
      len(SENT) == 1 and "Codex" in SENT[0]["text"], SENT)

SENT.clear()
BUSY[CX] = True
rm.tick()
rm._start[CX] = time.time() - 100
del BUSY[CX]                                # the conversation left the live list: it is finished
rm.tick()
check("2h a conversation that disappears counts as finished", len(SENT) == 1, SENT)

SENT.clear()
BUSY[BARE] = True
rm.tick()
rm._start[BARE] = time.time() - 3           # three seconds
BUSY[BARE] = False
rm.tick()
check("2i a three-second turn is not a push notification", SENT == [], SENT)

print("\n[3] The pictures ride along")
png = PROJ / "hero.png"
png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)
big = PROJ / "huge.png"
big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (rm.MAX_BYTES + 10))
rm._shrink = lambda p: None                 # no Pillow in the test: too big means named, not sent
files, skipped = rm._images(BARE, "I saved it to %s and %s" % (png, big), time.time() - 60)
check("3a a file the agent named is attached", [n for n, _ in files] == ["hero.png"], files)
check("3b one too large for the channel is named instead", skipped == ["huge.png"], skipped)


class FakeJob:
    """A delegated image job, as much of it as a card reads."""

    def __init__(self):
        self.id = "j9"
        self.stage = "image2d"
        self.provider_id = "openai-image"
        self.label = "leopard"
        self.params = {"prompt": "a snow leopard", "model": "gpt-image-1"}
        self.created_at = time.time() - 30
        self.started_at = time.time() - 28
        self.finished_at = time.time()
        self.status = type("S", (), {"value": "succeeded"})()
        self.outputs = [type("A", (), {"path": str(png)})()]
        self.error = ""
        self.cost = 0.04
        self.logs = []
        self.step = ""
        self.progress = 1.0


dg._job = lambda jid: FakeJob()
dg.note_job(BARE, FakeJob())
files, _ = rm._images(BARE, "done", time.time() - 60)
check("3c a picture a job made for this turn is attached too",
      [n for n, _ in files] == ["hero.png"], files)
settings.update({"remote_images": False})
check("3d and nothing is attached when images are switched off",
      rm._images(BARE, str(png), time.time() - 60) == ([], []))
settings.update({"remote_images": True})

print("\n[4] Back — the reply goes to the conversation that wrote")
rm.forget()
SENT.clear()
TEXT[BARE], TEXT[CX] = "claude done", "codex done"
for feed in (BARE, CX):
    BUSY[feed] = True
    rm.tick()
    rm._start[feed] = time.time() - 120
    BUSY[feed] = False
    rm.tick()
check("4a two conversations, two messages, two routes", len(SENT) == 2, SENT)
claude_msg, codex_msg = SENT[0]["id"], SENT[1]["id"]
tgt, why = rm._route({"content": "yes do it", "message_reference": {"message_id": codex_msg}})
check("4b a reply goes to the agent it replies to",
      why == "reply" and tgt["agent"] == "codex" and tgt["bare"] == BARE, (why, tgt))
tgt, why = rm._route({"content": "no, revert", "message_reference": {"message_id": claude_msg}})
check("4c and not to the newest one", tgt["agent"] == "claude", tgt)
tgt, why = rm._route({"content": "@codex try again"})
check("4d an engine can be named by hand", why == "@codex" and tgt["agent"] == "codex", (why, tgt))
tgt, why = rm._route({"content": "ok"})
check("4e a plain message goes to the newest", why == "newest" and tgt["m"] == codex_msg, (why, tgt))
check("4f and the address is removed from what the agent is told",
      rm._strip_address("@codex try again") == "try again")

print("\n[5] Back — how it is delivered")
SEND.clear()
SENT.clear()
BUSY.clear()
rm._deliver({"id": "m1", "content": "stop and do the other thing",
             "message_reference": {"message_id": codex_msg}})
check("5a an idle conversation gets a normal turn",
      len(SEND) == 1 and SEND[0]["steer"] is False, SEND)
check("5b sent to the FOLDER id with the engine's agent id — what the chat box itself sends",
      SEND and SEND[0]["project_id"] == BARE and SEND[0]["agent"] == "codex", SEND)
check("5c the reply is ticked, not answered with another message", REACTS and SENT == [], (REACTS, SENT))
SEND.clear()
BUSY[CX] = True
rm._deliver({"id": "m2", "content": "no, use the other folder",
             "message_reference": {"message_id": codex_msg}})
check("5d a RUNNING turn is steered instead of queued",
      len(SEND) == 1 and SEND[0]["steer"] is True, SEND)
SEND.clear()
BUSY.clear()
BUSY[BARE] = True
rm._deliver({"id": "m3", "content": "and add a test",
             "message_reference": {"message_id": claude_msg}})
check("5e Claude is steered the same way", SEND and SEND[0]["steer"] is True, SEND)
SEND.clear()
SENT.clear()
rm._deliver({"id": "m4", "content": "ok"})
check("5f a guessed target is said out loud", any("→" in m["text"] for m in SENT), SENT)
SEND.clear()
SENT.clear()
cc_session.send = lambda *a, **k: {"ok": False, "error": "Steer needs a running turn"}
rm._deliver({"id": "m5", "content": "x", "message_reference": {"message_id": claude_msg}})
# ...and a refusal is not counted as a delivered reply: four went through, this one did not.
check("5g a refused message is reported to the phone, not swallowed",
      any("Steer needs" in m["text"] for m in SENT) and rm.status()["replies"] == 4,
      (SENT, rm.status()["replies"]))
cc_session.send = fake_send
rm.forget()
SENT.clear()
rm._deliver({"id": "m6", "content": "hello?"})
check("5h with nothing sent yet, the phone is told what to do, and no turn is started",
      SEND == [] and SENT and "Start a turn" in SENT[0]["text"], (SEND, SENT))

print("\n[6] Reading the channel - the cursor, and whose messages count")
rm.forget()
CALLS: list[str] = []
INBOX_MSGS: list[dict] = [{"id": "500", "content": "written before REMOTE was on",
                           "author": {"username": "me"}}]


def fake_call(method, path, body=None, files=None, timeout=25.0):
    """Discord's REST layer, replaced: the question here is which query is asked."""
    CALLS.append(path)
    return 200, list(INBOX_MSGS)


rm._call = fake_call
check("6a the first look asks for ONE message, and replays nothing",
      REAL_PULL() == [] and CALLS[-1].endswith("?limit=1"), CALLS)
INBOX_MSGS = [{"id": "501", "content": "do it", "author": {"username": "me"}},
              {"id": "502", "content": "ignore me", "author": {"username": "studio", "bot": True}}]
msgs = REAL_PULL()
check("6b then only what was written after that point", "after=500" in CALLS[-1], CALLS)
check("6c and never a bot's own message, which would be a loop",
      [m["id"] for m in msgs] == ["501"], msgs)

print("\n[7] Speaking from the phone - a turn STARTED there, not only answered")
rm.forget()
SENT.clear()
SEND.clear()
BUSY.clear()
GREEK = ("στο workspace %s βαλε εναν deepseek agent "
         "να μου πει τι καιρο "
         "θα εχει το βραδυ" % PROJ.name)
tgt, why = rm._route({"content": GREEK})
check("7a a sentence that names the project and the engine is a whole instruction",
      why == "named" and tgt and tgt["agent"] == "deepseek-harness" and tgt["bare"] == BARE,
      (why, tgt))
check("7b even with nothing sent yet, so the first word of the day can be work",
      rm._load().get("routes") == [], rm._load().get("routes"))
rm._deliver({"id": "p1", "content": GREEK})
check("7c the turn is started on that engine, in that folder",
      len(SEND) == 1 and SEND[0]["project_id"] == BARE
      and SEND[0]["agent"] == "deepseek-harness" and SEND[0]["steer"] is False, SEND)
check("7d and the phone is told where it went", any("→" in m["text"] for m in SENT), SENT)
DS = "deepseek-harness--" + BARE               # DeepSeek files its own conversation
check("7e the answer is OWED, whatever the turn's length", DS in rm._owed, dict(rm._owed))

SENT.clear()
BUSY[DS] = True
rm.tick()
rm._start[DS] = time.time() - 4            # four seconds: under the floor for a turn started here
BUSY[DS] = False
rm.tick()
check("7f a short turn the PHONE started is still reported", len(SENT) == 1, SENT)
check("7g and the debt is cleared", DS not in rm._owed, dict(rm._owed))

SENT.clear()
BUSY.clear()
rm._owed[DS] = time.time() - 9             # it began and ended between two looks
rm.tick()
check("7h a turn too fast to be seen working is reported too", len(SENT) == 1, SENT)
check("7i and only once", (rm.tick() or len(SENT)) == 1, SENT)

SEND.clear()
SENT.clear()
tgt, why = rm._route({"content": "@codex add a jump to the player"})
check("7j an engine alone is enough when it has written before",
      tgt and tgt["agent"] == "codex", (why, tgt))
addr, clean = rm._address("@codex #%s add a jump" % PROJ.name)
check("7k the address is not part of what the agent is told",
      clean == "%s add a jump" % PROJ.name and addr["agent"] == "codex", (clean, addr))
rm._remember("900", rm._target(BARE))      # a Claude message to reply to
tgt, why = rm._route({"content": "yes claude, go on", "message_reference": {"message_id": "900"}})
check("7l a reply still wins over an engine named in passing", why == "reply", (why, tgt))
tgt, why = rm._route({"content": "yes claude, go on"})
check("7m and a mention with no project starts nothing new", why == "newest", (why, tgt))

print("\n[8] The words REMOTE answers itself")
SENT.clear()
SEND.clear()
rm._deliver({"id": "c1", "content": "/help"})
check("8a /help answers on the phone and starts no turn",
      SEND == [] and len(SENT) == 1 and "reply" in SENT[0]["text"], (SEND, SENT))
SENT.clear()
rm._deliver({"id": "c2", "content": "/projects"})
check("8b /projects names the workspaces", SEND == [] and PROJ.name in SENT[0]["text"], SENT)
SENT.clear()
BUSY.clear()
rm._deliver({"id": "c3", "content": "/status"})
check("8c /status is honest when nothing is working",
      "nothing is working" in SENT[0]["text"], SENT)
SENT.clear()
STOPPED: list[str] = []
from asset_studio import engines as eng_mod               # noqa: E402
eng_mod.CODEX.cancel = lambda pid: STOPPED.append(pid)
rm._remember("950", rm._target(CX))                  # a Codex message to reply to
rm._deliver({"id": "c4", "content": "/stop", "message_reference": {"message_id": "950"}})
check("8d /stop cancels the turn you replied to", STOPPED == [BARE], (STOPPED, SENT))
SENT.clear()
SEND.clear()
rm._deliver({"id": "c5", "content": "/compact", "message_reference": {"message_id": "900"}})
check("8e an engine's own slash command is passed through, not swallowed",
      len(SEND) == 1 and SEND[0]["message"] == "/compact", (SEND, SENT))

print("\n[9] The agent behind a feed id")
check("9a a bare folder is Claude", rm._agent_for(BARE) == "claude")
check("9b a codex-- feed is Codex", rm._agent_for(CX) == "codex")
check("9c and the folder id is the same for both",
      rm._target(CX)["bare"] == rm._target(BARE)["bare"] == BARE)
check("9d the project is named as the user named it", rm._target(CX)["name"] == PROJ.name)

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

"""The prompt-cache guard, the screenshot shrink and the no-work ledger rows.

Measured on the KAPOW session (2026-11-05..12): the conversation's cache was thrown away 16 times
by expiry, 8 times by the CLI stripping old screenshots, and once by a resume on a new CLI build,
and the ledger banked 45 results that did no work ($373.58). Each part of the fix is driven here
with a clock, a fake process and a prepared log: no CLI, no network, no browser.

Run: python cache_guard_test.py   (from backend/)
"""
import io
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_data = tempfile.TemporaryDirectory(prefix="studio-cache-guard-")
os.environ["ASSET_STUDIO_DATA"] = _data.name

from asset_studio import cache_guard as cg      # noqa: E402
from asset_studio import cc_session as cc       # noqa: E402
from asset_studio import ledger_repair, read_shrink, spend   # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ok   " + name)
    else:
        fail += 1
        print("  FAIL " + name + ("  <- " + str(extra) if extra else ""))


def section(t):
    print("\n" + t)


# --- 1. the decision ---------------------------------------------------------------------------
section("decide: compact a large idle session while its cache is warm")
T0 = 1_800_000_000.0
base = dict(enabled=True, alt=False, alive=True, busy=False, ctx=800_000, ttl=3600.0,
            last_api=T0, guard_at=0.0, min_tok=150_000, after=3000.0)
check("fires at 50 minutes", cg.decide(**base, now=T0 + 3001) == "compact")
check("not at 49 minutes", cg.decide(**base, now=T0 + 2940) == "early")
check("not after the cache is about to expire", cg.decide(**base, now=T0 + 3600 - 200) == "too_late")
check("not for a small session", cg.decide(**{**base, "ctx": 90_000}, now=T0 + 3001) == "small")
check("not for a 5-minute cache", cg.decide(**{**base, "ttl": 300.0}, now=T0 + 3001) == "short_ttl")
check("not while a turn runs", cg.decide(**{**base, "busy": True}, now=T0 + 3001) == "busy")
check("not twice for one quiet spell", cg.decide(**{**base, "guard_at": T0 + 3001}, now=T0 + 3100) == "done")
check("re-armed by the next request", cg.decide(**{**base, "guard_at": T0 + 3001, "last_api": T0 + 4000},
                                                now=T0 + 4000 + 3001) == "compact")
check("not for another vendor's engine", cg.decide(**{**base, "alt": True}, now=T0 + 3001) == "other_engine")
check("not when switched off", cg.decide(**{**base, "enabled": False}, now=T0 + 3001) == "off")
check("not before any request was seen", cg.decide(**{**base, "last_api": 0.0}, now=T0 + 3001) == "no_request_seen")
asked = []
check("not while an agent is owed a result",
      cg.decide(**base, now=T0 + 3001, background=lambda: asked.append(1) or True) == "background")
asked.clear()
cg.decide(**base, now=T0 + 100, background=lambda: asked.append(1) or True)
check("the file-reading question is not asked on an early tick", not asked)

section("hold_reap: keep the process until the guard has compacted")
hb = dict(enabled=True, alt=False, ctx=800_000, ttl=3600.0, last_api=T0, guard_at=0.0, min_tok=150_000)
check("held at 20 minutes idle", cg.hold_reap(**hb, now=T0 + 1200))
check("released once compacted", not cg.hold_reap(**{**hb, "guard_at": T0 + 3001}, now=T0 + 3100))
check("released once the cache is cold", not cg.hold_reap(**hb, now=T0 + 3500))
check("never held for a small session", not cg.hold_reap(**{**hb, "ctx": 50_000}, now=T0 + 1200))
check("never held for a 5-minute cache", not cg.hold_reap(**{**hb, "ttl": 300.0}, now=T0 + 1200))


# --- 2. the tick on a live session ---------------------------------------------------------------
section("the reaper's tick sends /compact into the session, once")
sent = []
real_write, real_bg = cc._write_msg, cc._open_background
cc._write_msg = lambda live, text, compact=False: sent.append((text, compact)) or True
cc._open_background = lambda pid: []
try:
    proc = SimpleNamespace(poll=lambda: None, pid=0)
    lv = cc._Live("guard-proj", proc, None, ("exe",), "sess-g")
    lv.last_write = lv.last_result = time.time() - 3100
    lv.last_api, lv.ctx_tokens, lv.cache_ttl = time.time() - 3010, 820_000, 3600.0
    check("held from the reap before it fires", cc._cache_guard_holds(lv, time.time()))
    r1 = cc._cache_guard_tick(lv, time.time())
    check("the tick decides to compact", r1 == "compact", r1)
    check("it sends /compact as a compaction", sent == [("/compact", True)], sent)
    check("and remembers it did", lv.guard_at > 0 and cg.record("guard-proj").get("guard_at") == lv.guard_at)
    r2 = cc._cache_guard_tick(lv, time.time() + 60)
    check("the next tick does nothing", r2 == "done" and len(sent) == 1, r2)
    check("and the reap is no longer held", not cc._cache_guard_holds(lv, time.time() + 60))
    lv2 = cc._Live("guard-small", proc, None, ("exe",), "sess-s")
    lv2.last_api, lv2.ctx_tokens, lv2.cache_ttl = time.time() - 3010, 40_000, 3600.0
    check("a small session is left alone", cc._cache_guard_tick(lv2, time.time()) == "small" and len(sent) == 1)
finally:
    cc._write_msg, cc._open_background = real_write, real_bg


# --- 3. the stream reader ------------------------------------------------------------------------
section("the reader: cache facts from message_start, and no bank for a no-work result")
logp = Path(_data.name) / "reader.log"
events = [
    {"type": "system", "subtype": "init", "session_id": "sess-r", "claude_code_version": "2.1.322"},
    # the first result of a process the Studio did not see start: no message, a carried total
    {"type": "result", "subtype": "success", "total_cost_usd": 58.744998, "num_turns": 0},
    {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_start", "message": {
        "model": "claude-opus-5-5", "usage": {"input_tokens": 2, "cache_read_input_tokens": 700000,
        "cache_creation_input_tokens": 5000, "output_tokens": 1,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 5000}}}}},
    # a subagent's request must not move the conversation's numbers
    {"type": "stream_event", "parent_tool_use_id": "toolu_x", "event": {"type": "message_start", "message": {
        "model": "claude-sonnet-5-5", "usage": {"input_tokens": 9, "cache_read_input_tokens": 12000,
        "cache_creation_input_tokens": 0, "output_tokens": 1}}}},
    {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_delta", "usage": {"output_tokens": 50}}},
    {"type": "stream_event", "parent_tool_use_id": None, "event": {"type": "message_stop"}},
    {"type": "result", "subtype": "success", "total_cost_usd": 60.0, "num_turns": 1},
]
logp.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
dead = SimpleNamespace(poll=lambda: 0, pid=0)
lr = cc._Live("reader-proj", dead, None, ("exe", "cwd", "claude-opus-5-5", "bypassPermissions", False, "xhigh"), None)
lr.log_path = logp
cc._live_reader(lr)
rows = [json.loads(x) for x in (Path(_data.name) / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip() and json.loads(x).get("project") == "reader-proj"]
check("the carried total was not banked", len(rows) == 1, rows)
check("the real turn is priced from the carried total", rows and abs(rows[0]["cost"] - 1.255002) < 1e-6,
      rows and rows[0]["cost"])
check("the conversation's size is the main thread's", lr.ctx_tokens == 705002, lr.ctx_tokens)
check("the 1-hour lifetime is learned from the write", lr.cache_ttl == 3600.0, lr.cache_ttl)
check("the build is learned from init", lr.cli_version == "2.1.322", lr.cli_version)
cg.flush()
rec = cg.record("reader-proj")
check("and kept for the next backend", rec.get("ctx") == 705002 and rec.get("version") == "2.1.322"
      and rec.get("session_id") == "sess-r", rec)

section("adoption: the next backend inherits the high-water mark")
cc._register(lr, "C:/x")
lr.call_cost = 60.0
cc._reg_checkpoint(lr)
reg = json.loads(cc._reg_path("reader-proj").read_text(encoding="utf-8"))
check("the checkpoint stores the process total", reg.get("call_cost") == 60.0, reg.get("call_cost"))
reg["pid"] = 999_999_901          # no such process: the adopted reader drains the log and stops
check("adoption succeeds", cc._adopt_one(reg))
adopted = cc._live.get("reader-proj")
check("the adopted session prices from 60.00, not 0", adopted is not None and adopted.call_cost == 60.0,
      adopted and adopted.call_cost)
check("and keeps the guard's clock", adopted is not None and adopted.ctx_tokens == 705002)
cc._live.pop("reader-proj", None)


# --- 4. the build a warm session resumes on --------------------------------------------------------
section("warm_binary: resume on the old build only while it matters")
vdir = Path(_data.name) / "versions"
vdir.mkdir()
(vdir / "2.1.314").write_bytes(b"x")
saved = (cg.versions_dir, cg._is_native, cg.binary_version)
cg.versions_dir = lambda: vdir
cg._is_native = lambda exe: True
cg.binary_version = lambda exe: "2.1.322"
try:
    cg.note_version("warm-proj", "sess-w", "2.1.314")
    now = time.time()
    cg.note_request("warm-proj", "sess-w", "2.1.314", 600_000, 3600.0, at=now - 900)
    run, why = cg.warm_binary("warm-proj", "sess-w", "C:/bin/claude.exe", now=now)
    check("warm and large: the old build", run == str(vdir / "2.1.314"), (run, why))
    check("and it says why", "2.1.314" in why and "2.1.322" in why, why)
    run, _ = cg.warm_binary("warm-proj", "another-session", "C:/bin/claude.exe", now=now)
    check("another conversation: the installed build", run == "C:/bin/claude.exe")
    run, _ = cg.warm_binary("warm-proj", "sess-w", "C:/bin/claude.exe", now=now + 3600)
    check("cold: the installed build", run == "C:/bin/claude.exe")
    cg.note_compacted("warm-proj")
    run, _ = cg.warm_binary("warm-proj", "sess-w", "C:/bin/claude.exe", now=now)
    check("just compacted: the installed build", run == "C:/bin/claude.exe")
    cg.note_request("warm-proj", "sess-w", "2.1.314", 600_000, 3600.0, at=now - 900)
    (vdir / "2.1.314").unlink()
    run, why = cg.warm_binary("warm-proj", "sess-w", "C:/bin/claude.exe", now=now)
    check("old build gone: the installed build, and it says so", run == "C:/bin/claude.exe" and "no longer" in why, why)
finally:
    cg.versions_dir, cg._is_native, cg.binary_version = saved


# --- 5. the screenshot shrink --------------------------------------------------------------------
section("read_shrink: the same pixels in fewer bytes, only where it is safe")
from PIL import Image                              # noqa: E402
work = Path(_data.name) / "proj"
work.mkdir()
random.seed(7)
img = Image.new("RGB", (900, 600))
img.putdata([(x % 256, (x * 7) % 256, random.randrange(256)) for x in range(900 * 600)])
big = work / "shot.png"
img.save(big)
data_dir = Path(_data.name)
real_deny = read_shrink._deny_files
read_shrink._deny_files = lambda cwd: [Path(cwd) / ".claude" / "settings.json"] if cwd else []
try:
    pay = {"tool_name": "Read", "permission_mode": "bypassPermissions", "cwd": str(work),
           "tool_input": {"file_path": str(big)}}
    new = read_shrink.redirect(pay, data_dir)
    check("a large screenshot is redirected", bool(new) and new["file_path"].endswith(".webp"), new)
    if new:
        with Image.open(new["file_path"]) as cp:
            check("same pixels", cp.size == img.size, cp.size)
        check("fewer bytes", os.path.getsize(new["file_path"]) < 0.8 * os.path.getsize(big))
        check("the copy is reused", read_shrink.redirect(pay, data_dir) == new)
    check("left alone outside bypass mode", read_shrink.redirect({**pay, "permission_mode": "default"}, data_dir) is None)
    small = work / "small.png"
    Image.new("RGB", (40, 40)).save(small)
    check("left alone when small", read_shrink.redirect({**pay, "tool_input": {"file_path": str(small)}}, data_dir) is None)
    check("left alone for text", read_shrink.redirect({**pay, "tool_input": {"file_path": str(work / "a.txt")}}, data_dir) is None)
    (work / ".claude").mkdir()
    (work / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Read(./secret/**)"]}}))
    check("left alone where a Read deny rule exists", read_shrink.redirect(pay, data_dir) is None)
    (work / ".claude" / "settings.json").unlink()
    (data_dir / "settings.json").write_text(json.dumps({"cc_image_shrink": False}))
    check("left alone when switched off", read_shrink.redirect(pay, data_dir) is None)
    (data_dir / "settings.json").unlink()
finally:
    read_shrink._deny_files = real_deny


# --- 5b. what the agents are told ------------------------------------------------------------------
section("the notes: subagents keep waits short; an unrelated task may start fresh")


def _sub_text(args):
    """The subagent prompt, from the command line or from the file it was moved into."""
    for i, a in enumerate(args):
        if a == "--append-subagent-system-prompt":
            return args[i + 1]
        if a == "--append-subagent-system-prompt-file":
            return Path(args[i + 1]).read_text(encoding="utf-8")
    return ""


args_on = cc._claude_stream_args("claude", None, "claude-opus-5-5[1m]", "bypassPermissions", False,
                                 "xhigh", "notes-proj", str(work))
check("subagents get the wait rule", "PROMPT CACHE" in _sub_text(args_on))
check("the main prompt does not", all("PROMPT CACHE" not in a for a in args_on
                                      if not a.startswith("--") and a != _sub_text(args_on)))
from asset_studio.config import settings as _settings            # noqa: E402
_settings.update({"cc_cache_guard": False})
args_off = cc._claude_stream_args("claude", None, "claude-opus-5-5[1m]", "bypassPermissions", False,
                                  "xhigh", "notes-proj", str(work))
check("...and not with the guard off", "PROMPT CACHE" not in _sub_text(args_off))
_settings.update({"cc_cache_guard": True})
note = cc._context_window_note("claude-opus-5-5[1m]")
check("the 1M note lets the agent say once that an unrelated task costs less in a new conversation",
      (not note) or "UNRELATED" in note, note[-200:])


# --- 6. the old no-work rows -----------------------------------------------------------------------
section("drop_phantoms: no-work rows leave both ledgers, once")
ts = time.time()
rows_in = [
    {"ts": ts, "project": "ledger-p", "model": "claude-opus-5-5[1m]", "effort": "xhigh", "tokens": 0,
     "messages": 0, "cost": 58.744998, "compacting": False, "call": 1},
    {"ts": ts, "project": "ledger-p", "model": "claude-opus-5-5", "effort": "xhigh", "tokens": 8675,
     "messages": 10, "cost": 10.68534, "compacting": False, "call": 2},
    {"ts": ts, "project": "ledger-p", "model": "claude-opus-5-5", "effort": "xhigh", "tokens": 0,
     "messages": 0, "cost": 0.5, "compacting": True, "call": 2},
]
with open(ledger_repair.TURNS, "a", encoding="utf-8") as f:
    for r in rows_in:
        f.write(json.dumps(r) + "\n")
for r in rows_in:
    spend.record("ledger-p", r["model"], r["cost"], r["tokens"], billed=False)
before = spend.for_project("ledger-p")["total"]
rep = ledger_repair.drop_phantoms()
after = spend.for_project("ledger-p")
left = [json.loads(x) for x in ledger_repair.TURNS.read_text(encoding="utf-8").splitlines()
        if x.strip() and json.loads(x).get("project") == "ledger-p"]
check("one row removed", rep.get("rows") == 1, rep)
check("the real turn and the compaction stay", len(left) == 2 and {r["cost"] for r in left} == {10.68534, 0.5}, left)
check("the spend total drops by the carried figure", abs(before - after["total"] - 58.744998) < 1e-6,
      (before, after["total"]))
check("the turn count drops by one", after["turns"] == 2, after["turns"])
check("a backup was kept", bool(rep.get("turns_backup")) and bool(rep.get("spend_backup")), rep)
check("it runs once", ledger_repair.drop_phantoms().get("already") is True)

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

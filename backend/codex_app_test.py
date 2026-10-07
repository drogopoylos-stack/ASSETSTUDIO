"""Codex in the chat: sign-in, models, and a streamed conversation through the real app-server.

Two halves.

The DRAWING is proven offline: a Codex log drawn as feed events (answer, reasoning, commands,
file edits with their diff, tools, a subagent card, a generated image, a phase list, a refused key),
the approval words, the mode table, the effort fit, and an image written into the project.

The BEHAVIOUR is proven against the real Codex CLI, pointed at a stand-in for the OpenAI Responses
API that this file serves on 127.0.0.1. Codex runs for real - its tools, its patches, its subagents -
and only the model's words are scripted. It needs no account and no network. Everything runs in a
temporary Codex home and a temporary Studio data folder, so the user's own ~/.codex is never read
or written. Skipped, not failed, on a PC without the Codex CLI.

Run: python codex_app_test.py
"""
import base64
import io
import json
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="codex-test-"))
os.environ["ASSET_STUDIO_DATA"] = str(TMP / "data")          # before the Studio is imported
# ...and the same for Codex's own home. The Studio now reads a thread's rollout file to recover
# its token total, so without this the offline half of this test would walk the user's real
# ~/.codex — which this file promises never to read.
os.environ["CODEX_HOME"] = str(TMP / "codex-home-offline")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import codex_app as cx      # noqa: E402

ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, str(extra)[:600]))


def png(rgb=(200, 40, 40), w=8, h=8) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
print("\n[1] modes, commands, diffs")
check("an unknown or old mode is full access", cx.mode_of("full-auto") == "full" and cx.mode_of("") == "full"
      and cx.mode_of("yolo") == "full" and cx.mode_of("bypassPermissions") == "full")
check("ask and read-only are kept", cx.mode_of("ask") == "ask" and cx.mode_of("read-only") == "read-only"
      and cx.mode_of("plan") == "read-only")
ap, pol = cx._policy("full")
check("full access = no sandbox, never asks", ap == "never" and pol == {"type": "dangerFullAccess"}, (ap, pol))
ap, pol = cx._policy("ask")
check("ask = workspace sandbox, asks on request", ap == "on-request" and pol.get("type") == "workspaceWrite", (ap, pol))
wrapped = '"C:\\\\WINDOWS\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe" -Command \'echo studio-codex-test\''
check("the PowerShell wrapper comes off a command", cx._short_cmd(wrapped) == "echo studio-codex-test", cx._short_cmd(wrapped))
check("a plain command stays as it is", cx._short_cmd("git status") == "git status")
a, r, rows = cx._parse_unified("--- a/x.py\n+++ b/x.py\n@@ -3,3 +3,4 @@\n ctx one\n-old line\n+new line\n+added\n ctx two\n")
check("a unified diff counts +2 -1", (a, r) == (2, 1), (a, r))
check("diff rows carry line numbers", rows[0] == {"t": "ctx", "s": "ctx one", "n": 3, "m": 3}
      and {"t": "del", "s": "old line", "n": 4} in rows and {"t": "add", "s": "new line", "m": 4} in rows, rows)

print("\n[2] approval words")
conv = cx._conv("unit-approvals")
check("no waiting approval: an ordinary 'yes' is a message", cx._answer_approval(conv, "yes") is None)


class _FakeSrv:
    def __init__(self):
        self.sent = []

    def alive(self):
        return True

    def respond(self, rid, result=None, error=None):
        self.sent.append((rid, result))


fs = _FakeSrv()
conv.approvals.append({"rid": 7, "method": "item/commandExecution/requestApproval", "params": {},
                       "what": "run x", "srv": fs})
res = cx._answer_approval(conv, "Allow")
check("'Allow' answers the waiting command", res and res.get("approval") == "Allow" and fs.sent == [(7, {"decision": "accept"})], (res, fs.sent))
conv.approvals.append({"rid": 8, "method": "item/fileChange/requestApproval", "params": {}, "what": "edit", "srv": fs})
cx._answer_approval(conv, "deny")
check("'deny' declines a file change", fs.sent[-1] == (8, {"decision": "decline"}), fs.sent)
conv.approvals.append({"rid": 9, "method": "execCommandApproval", "params": {}, "what": "x", "srv": fs})
cx._answer_approval(conv, "Allow for this session")
check("the old protocol gets approved_for_session", fs.sent[-1] == (9, {"decision": "approved_for_session"}), fs.sent)

print("\n[3] efforts")
cx._MODELS.update(at=time.time() + 3600, v={"default": "m1", "error": "", "models": [
    {"id": "m1", "efforts": [{"id": "low"}, {"id": "medium"}, {"id": "high"}, {"id": "xhigh"}, {"id": "max"}, {"id": "ultra"}]},
    {"id": "m2", "efforts": [{"id": "low"}, {"id": "medium"}, {"id": "high"}]}]})
check("default effort is left to the model", cx.fit_effort("m1", "default") == "")
check("a listed effort is sent", cx.fit_effort("m1", "ultra") == "ultra" and cx.fit_effort("m2", "medium") == "medium")
check("an effort the model lacks steps down", cx.fit_effort("m2", "max") == "high", cx.fit_effort("m2", "max"))
check("ultracode means ultra", cx.fit_effort("m1", "ultracode") == "ultra")
check("the default model is used when none is picked", cx.fit_effort("default", "xhigh") == "xhigh")
cx._MODELS["v"]["all_ids"] = ["m1", "m2", "hidden-m3"]
check("a model this Codex offers is sent", cx.fit_model("m2") == ("m2", ""))
check("a hidden model is still accepted", cx.fit_model("hidden-m3")[0] == "hidden-m3")
mm, note = cx.fit_model("gpt-5-codex")
check("a model from the old fixed list goes to the default, with a note", mm == "" and "does not offer gpt-5-codex" in note, (mm, note))
check("default stays default", cx.fit_model("default") == ("", ""))
cx._MODELS.clear()

print("\n[4] a Codex log drawn as the feed")
proj = TMP / "proj"
proj.mkdir(parents=True, exist_ok=True)
tid = "t-unit"
recs = [
    {"t": "user", "id": "u1", "text": "do the thing", "images": [str(proj / "shot.png")], "at": 1000.0},
    {"t": "turn_start", "turn": "turn1", "at": 1000.1},
    {"t": "item", "item": {"type": "reasoning", "id": "r1", "summary": ["Plan the edit."], "content": []}, "at": 1000.2},
    {"t": "item", "item": {"type": "commandExecution", "id": "c1", "command": wrapped, "status": "completed",
                           "commandActions": [{"type": "unknown", "command": "echo studio-codex-test"}],
                           "aggregatedOutput": "studio-codex-test\r\n", "exitCode": 0}, "at": 1000.3},
    {"t": "item", "item": {"type": "commandExecution", "id": "c2", "command": "rg foo", "status": "completed",
                           "commandActions": [{"type": "search", "command": "rg foo", "query": "foo", "path": "src"}],
                           "aggregatedOutput": "", "exitCode": 0}, "at": 1000.35},
    {"t": "item", "item": {"type": "commandExecution", "id": "c4", "command": "type src\app.js", "status": "completed",
                           "commandActions": [{"type": "read", "command": "type src\app.js", "name": "app.js",
                                               "path": str(proj / "src" / "app.js")}],
                           "aggregatedOutput": "let x = 1", "exitCode": 0}, "at": 1000.355},
    {"t": "item", "item": {"type": "commandExecution", "id": "c3", "command": "bad", "status": "failed",
                           "commandActions": [{"type": "unknown", "command": "bad"}],
                           "aggregatedOutput": "not found", "exitCode": 1}, "at": 1000.36},
    {"t": "item", "item": {"type": "fileChange", "id": "f1", "status": "completed", "changes": [
        {"path": str(proj / "a.txt"), "kind": {"type": "add"}, "diff": "one\ntwo\n"},
        {"path": str(proj / "b.py"), "kind": {"type": "update", "move_path": None},
         "diff": "@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n y = 3\n"}]}, "at": 1000.4},
    {"t": "item", "item": {"type": "mcpToolCall", "id": "m1", "server": "studio", "tool": "graphify_query",
                           "status": "completed", "arguments": {"q": "resolve"},
                           "result": {"content": [{"type": "text", "text": "resolve at a.py:3"}]}}, "at": 1000.45},
    {"t": "item", "item": {"type": "webSearch", "id": "w1", "query": "codex app server", "action": None}, "at": 1000.5},
    {"t": "item", "item": {"type": "webSearch", "id": "w2", "query": "", "action": {"type": "openPage", "url": "https://example.com/docs"}}, "at": 1000.51},
    {"t": "item", "item": {"type": "sleep", "id": "z1", "durationMs": 30000}, "at": 1000.52},
    {"t": "item", "item": {"type": "imageGeneration", "id": "ig1", "status": "completed", "revisedPrompt": "a red square",
                           "studioRel": ".studio-uploads/codex/ig1.png"}, "at": 1000.55},
    {"t": "item", "item": {"type": "subAgentActivity", "id": "s1", "kind": "started", "agentThreadId": "sub-1",
                           "agentPath": "/root/helper"}, "at": 1000.6},
    {"t": "plan", "plan": [{"step": "Read", "status": "completed"}, {"step": "Write", "status": "inProgress"}], "at": 1000.65},
    {"t": "item", "item": {"type": "agentMessage", "id": "a1", "text": "Done. **All** good."}, "at": 1000.7},
    {"t": "turn_end", "turn": "turn1", "status": "completed", "duration_ms": 4200, "out_tokens": 120, "at": 1000.8},
    {"t": "user", "id": "u2", "text": "again", "at": 1001.0},
    {"t": "turn_end", "turn": "turn2", "status": "failed", "error": "unexpected status 401 Unauthorized", "at": 1001.5},
]
cx._THREADS.mkdir(parents=True, exist_ok=True)
with open(cx._log_path(tid), "w", encoding="utf-8") as fh:
    for rr in recs:
        fh.write(json.dumps(rr) + "\n")
sub_recs = [
    {"t": "item", "item": {"type": "userMessage", "id": "su", "content": [{"type": "text", "text": "check the tests"}]}, "at": 1000.61},
    {"t": "item", "item": {"type": "commandExecution", "id": "sc", "command": "pytest", "status": "completed",
                           "commandActions": [], "aggregatedOutput": "ok", "exitCode": 0}, "at": 1000.62},
    {"t": "item", "item": {"type": "agentMessage", "id": "sa", "text": "All tests pass."}, "at": 1000.63},
    {"t": "turn_end", "turn": "st", "status": "completed", "at": 1000.64},
]
with open(cx._log_path("sub-1"), "w", encoding="utf-8") as fh:
    for rr in sub_recs:
        fh.write(json.dumps(rr) + "\n")
ev = cx.events_for(tid, "unit-feed", str(proj))
kinds = [e["kind"] for e in ev]
check("the user's message, with its picture named in the project", ev[0]["kind"] == "user"
      and "shot.png" in ev[0]["text"], ev[0])
check("...and no id, so the feed offers no 'edit & retry' Codex cannot do", "id" not in ev[0], ev[0])
check("reasoning is a thinking row", any(e["kind"] == "thinking" and e["text"] == "Plan the edit." for e in ev))
sh = [e for e in ev if e.get("title") in ("PowerShell", "Bash")]
check("a command is a PowerShell card (named like Claude's) with the command as typed",
      sh and sh[0]["title"] == "PowerShell" and sh[0]["command"] == "echo studio-codex-test", sh)
check("...and its output is the result under it", any(e["kind"] == "result" and e["text"] == "studio-codex-test" for e in ev))
check("a search command reads as a search", any(e.get("tool") == "Grep" and e.get("subtitle", "").startswith("foo") for e in ev))
check("a file read is named by its path in the project, so the link opens that file",
      any(e.get("tool") == "Read" and e.get("subtitle") == "src/app.js" for e in ev), [e for e in ev if e.get("tool") == "Read"])
check("a failed command says so, with its exit code", any(e["kind"] == "result" and e.get("ok") is False and "exit code 1" in e["text"] for e in ev))
w = [e for e in ev if e.get("title") == "Write"]
check("an added file is a Write card with its lines", w and w[0]["subtitle"] == "a.txt" and w[0]["diff"]["added"] == 2, w)
ed = [e for e in ev if e.get("title") == "Edit"]
check("an updated file is an Edit card with +1 -1", ed and ed[0]["subtitle"] == "b.py" and ed[0]["diff"]["added"] == 1
      and ed[0]["diff"]["removed"] == 1, ed)
check("an MCP tool is named server · tool, with its answer", any(e.get("title") == "studio · graphify_query" for e in ev)
      and any(e["kind"] == "result" and "a.py:3" in e["text"] for e in ev))
check("a web search is a WebSearch card", any(e.get("tool") == "WebSearch" and e["subtitle"] == "codex app server" for e in ev))
check("opening a page names the page", any(e.get("tool") == "WebFetch" and e["subtitle"] == "https://example.com/docs" for e in ev))
check("a wait says how long", any(e.get("title") == "Wait" and e["subtitle"] == "30 s" for e in ev))
check("a generated image is a card plus its project path (the feed shows the picture)",
      any(e.get("title") == "Generate image" for e in ev) and any(e["kind"] == "text" and e["text"] == ".studio-uploads/codex/ig1.png" for e in ev))
ag = [e for e in ev if e["kind"] == "agent"]
check("a subagent is an agent card with its task and its answer", ag and ag[0]["agent"]["agent_id"] == "sub-1"
      and ag[0]["agent"]["prompt"] == "check the tests" and ag[0]["agent"]["result"] == "All tests pass."
      and ag[0]["agent"]["description"] == "helper", ag)
todo = [e for e in ev if e.get("title") == "Phases"]
check("a plan is the phase list", todo and todo[0]["todos"] == [{"content": "Read", "status": "completed"},
                                                                {"content": "Write", "status": "in_progress"}], todo)
check("the answer is a text row", any(e["kind"] == "text" and e["text"] == "Done. **All** good." for e in ev))
tb = [e for e in ev if e["kind"] == "turn"]
check("a finished turn gets its bar: tools, files, lines, time", tb and tb[0]["tools"] >= 5 and tb[0]["files"] == 2
      and tb[0]["added"] == 3 and tb[0]["removed"] == 1 and tb[0]["wall_s"] == 4.2 and tb[0]["tokens"] == 120, tb)
bad = [e for e in ev if e["kind"] == "result" and "401" in e.get("text", "")]
check("a refused key says to sign in again", bad and "Sign in again" in bad[-1]["text"], bad)
check("rows carry the UTC clock the Claude feed prints", ev[0]["ts"] == time.strftime("%H:%M:%S", time.gmtime(1000.0)))
det = cx.subagent_detail("codex--unit-feed", "sub-1")
check("a subagent opens on its own timeline", [e["kind"] for e in det["lines"]][:1] == ["tool"]
      and any(e["kind"] == "text" and e["text"] == "All tests pass." for e in det["lines"]), det["lines"])

print("\n[4a] a Codex agent reaches the UI, not just this module")
# THE BUG THIS PINS. `codex_app.subagents` and `subagent_detail` existed, worked, and were tested
# — and NOTHING CALLED THEM. The endpoints behind the agent pill, the agent card and the agent
# pane went straight to Claude's module, which reads Claude's transcript layout and nothing else,
# so a Codex fan-out was invisible in the Studio while the same fan-out on Claude was fully
# auditable. The module-level checks above could not catch that; the ROUTE can, so this tests the
# route. `mission.project_subagents` is what the router calls.
from asset_studio import mission as mi      # noqa: E402
cx._index_update("unit-feed", lambda d: d.setdefault("subs", {}).update(
    {"sub-1": {"started": 1000.6, "ended": 1000.64, "path": "/root/helper"}}))
listed = mi.project_subagents("codex--unit-feed")
one = next((a for a in listed["agents"] if a["agent_id"] == "sub-1"), None)
check("a Codex agent is listed by the route the UI calls", one is not None, listed)
check("...named, with its task and what it reported",
      bool(one) and one["description"] == "helper" and one["prompt"] == "check the tests"
      and one["result"] == "All tests pass.", one)
check("...and what it did, not merely that it existed",
      bool(one) and one["stats"]["bash"] == 1 and one["touched"]["commands"] == 1, (one or {}).get("stats"))
check("one agent opens in full through the same route",
      len(mi.project_subagent("codex--unit-feed", "sub-1")["lines"]) >= 3)
check("clashes are answered honestly empty for Codex rather than guessed",
      mi.project_subagent_collisions("codex--unit-feed") == [])

# WHAT IT IS DOING THIS INSTANT, and the phase that stops a finished tool reading as a live one.
tool = cx._sub_activity({"type": "commandExecution", "command": "pytest", "commandActions": []})
check("a running tool is phase 'tool', with the command as its detail",
      tool["phase"] == "tool" and tool["tool"] == "Running" and tool["detail"] == "pytest", tool)
gen = cx._sub_activity({"type": "agentMessage", "text": "x"}, tool)
check("...and when it comes back the agent is 'generating', with the tool kept",
      gen["phase"] == "generating" and gen["tool"] == "Running", gen)

# THE BILL, in the two numbers the card shows.
tk = cx._sub_tokens({"input_tokens": 460495, "cached_input_tokens": 428288,
                     "cache_write_input_tokens": 0, "output_tokens": 6940,
                     "total_tokens": 467435}, 258400)
check("the bill counts NEW tokens, with the cache reads kept apart",
      tk["tokens"] == 39147 and tk["usage"]["cache_read"] == 428288
      and tk["wire"] == 467435 and tk["ctx_max"] == 258400, tk)

print("\n[4b] a late item, and a folder known only to Codex")
late = [{"t": "user", "text": "x", "at": 1.0}, {"t": "turn_start", "turn": "A", "at": 1.1},
        {"t": "item", "turn": "A", "item": {"type": "agentMessage", "id": "m", "text": "done"}, "at": 1.2},
        {"t": "turn_end", "turn": "A", "status": "completed", "at": 1.3},
        {"t": "item", "turn": "A", "item": {"type": "commandExecution", "id": "c", "command": "echo hi", "status": "completed",
                                            "commandActions": [], "aggregatedOutput": "hi", "exitCode": 0}, "at": 1.4}]
order = [r["t"] for r in cx._turn_end_last(late)]
check("a command reported after its turn ended is drawn inside the turn", order[-1] == "turn_end" and order.count("turn_end") == 1, order)
check("records of other turns keep their order", [r["t"] for r in cx._turn_end_last(late[:4])] == ["user", "turn_start", "item", "turn_end"])
cx._index_update("unit-cwd", lambda d: d["threads"].update({"t1": {"cwd": str(proj), "created": 5.0}}))
check("the folder of a Codex-only conversation is known from its thread", cx.known_cwd("codex--unit-cwd") == str(proj), cx.known_cwd("unit-cwd"))
check("...and nothing is invented for an unknown folder", cx.known_cwd("unit-nothing") == "")

print("\n[5] an image is kept in the project")
kc = cx._conv("unit-image")
kc.cwd = str(proj)
kept = cx._keep_image(kc, {"type": "imageGeneration", "id": "ig/../9", "result": base64.b64encode(png()).decode()})
dest = Path(kept.get("studioPath") or "")
check("written under .studio-uploads/codex, the name made safe", dest.is_file() and dest.parent == proj / ".studio-uploads" / "codex"
      and ".." not in dest.name, kept)
check("the bytes are the image", dest.is_file() and dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")
check("the slim record keeps no base64", "result" not in cx._slim({**kept, "result": "AAAA"}))

# ---------------------------------------------------------------------------
# The live half: the real Codex CLI against a stand-in Responses API.
# ---------------------------------------------------------------------------
SCRIPT_LOG: list = []


def _items_after_user(body):
    items = body.get("input") or []
    idx, text = -1, ""
    for i, it in enumerate(items):
        if it.get("type") == "message" and it.get("role") == "user":
            t = " ".join(p.get("text", "") for p in it.get("content") or [] if isinstance(p, dict))
            if "scenario:" in t or not text:
                idx, text = i, t
    after = [it for it in items[idx + 1:] if it.get("type") in ("function_call_output", "custom_tool_call_output")]
    return text, len(after)


def _code_mode(body):
    """Codex 0.145+ offers its tools in an `additional_tools` input item. {tool name: namespace}:
    0.159 nests `exec` in a `functions` namespace, 0.145 lists it bare (namespace None)."""
    for it in body.get("input") or []:
        if it.get("type") == "additional_tools":
            names = {}
            for t in it.get("tools") or []:
                group = t.get("tools") if t.get("type") == "namespace" else [t]
                for n in group or []:
                    names[n.get("name")] = t.get("name") if t.get("type") == "namespace" else None
                    if n.get("name") == "exec":
                        # the tools a script may call: `### `name`` headings in exec's description
                        for nested in re.findall(r"^### `([^`]+)`", n.get("description") or "", re.M):
                            names.setdefault("nested:" + nested, True)
            return names
    return None


def _call(kind, name, ns, **fields):
    it = {"type": kind, "name": name, **fields}
    if ns:
        it["namespace"] = ns
    return it


def _msg(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _script(body):
    text, step = _items_after_user(body)
    scen = text.split("scenario:", 1)[1].split()[0].strip(" .,") if "scenario:" in text else "hello"
    cm = _code_mode(body)
    tools = {t.get("name") for t in body.get("tools") or []}
    SCRIPT_LOG.append((scen, step))
    if scen == "slow":
        time.sleep(4.0)
        return [_msg("slow answer")]
    if scen == "shell":
        if step == 0:
            if cm is not None and "exec" in cm:
                call = ('tools.exec_command({cmd: "echo studio-codex-test"})' if "nested:exec_command" in cm
                        else 'tools.shell_command({command: "echo studio-codex-test"})')
                return [_call("custom_tool_call", "exec", cm["exec"], input="const r = await %s;\ntext(r);" % call)]
            name = next((n for n in ("shell_command", "shell", "exec_command") if n in tools), "shell_command")
            args = {"cmd": "echo studio-codex-test"} if name == "exec_command" else {"command": "echo studio-codex-test"}
            return [{"type": "function_call", "name": name, "arguments": json.dumps(args)}]
        return [_msg("The command printed its line.")]
    if scen == "edit":
        patch = "*** Begin Patch\n*** Add File: hello_codex.txt\n+first line\n+second line\n*** End Patch\n"
        if step == 0:
            if cm is not None and "exec" in cm:
                return [_call("custom_tool_call", "exec", cm["exec"],
                              input="const r = await tools.apply_patch(%s);\ntext(r);" % json.dumps(patch))]
            return [{"type": "custom_tool_call", "name": "apply_patch", "input": patch}]
        return [_msg("I added hello_codex.txt.")]
    if scen == "plan":
        if step == 0 and cm is not None and "nested:update_plan" in cm:
            plan = {"explanation": "Two steps.", "plan": [{"step": "Read the task", "status": "completed"},
                                                          {"step": "Write the answer", "status": "in_progress"}]}
            return [_call("custom_tool_call", "exec", cm["exec"],
                          input="const r = await tools.update_plan(%s);\ntext(r);" % json.dumps(plan))]
        return [_msg("Plan done.")]
    if scen == "agent":
        if cm is not None and "spawn_agent" in cm:
            if step == 0:
                return [_call("function_call", "spawn_agent", cm["spawn_agent"],
                              arguments=json.dumps({"message": "scenario:hello (subagent task)", "task_name": "helper",
                                                    "fork_turns": "none"}))]
            if step == 1:
                return [_call("function_call", "wait_agent", cm.get("wait_agent"),
                              arguments=json.dumps({"timeout_ms": 10000}))]
        return [_msg("The subagent finished.")]
    return [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "Say hello."}]},
            _msg("Hello from the stand-in model.")]


class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        # what the real endpoint answers for /v1/models; a 404 here made Codex 0.145 hold its next
        # answer for more than 20 s after an API-key sign-in
        body = json.dumps({"object": "list", "data": []}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        out = _script(body)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()

        def ev(obj):
            self.wfile.write(("event: %s\ndata: %s\n\n" % (obj["type"], json.dumps(obj))).encode())
            self.wfile.flush()
        rid = "resp_%d" % int(time.time() * 1000)
        ev({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
        done = []
        for i, it in enumerate(out):
            it = dict(it, id="it_%s_%d" % (rid, i))
            if it["type"] in ("function_call", "custom_tool_call"):
                it["call_id"] = "call_%s_%d" % (rid, i)
            if it["type"] == "message":
                ev({"type": "response.output_item.added", "output_index": i,
                    "item": {"type": "message", "id": it["id"], "role": "assistant", "content": []}})
                t = it["content"][0]["text"]
                for j in range(0, len(t), 10):
                    ev({"type": "response.output_text.delta", "item_id": it["id"], "output_index": i,
                        "content_index": 0, "delta": t[j:j + 10]})
            else:
                ev({"type": "response.output_item.added", "output_index": i, "item": it})
            ev({"type": "response.output_item.done", "output_index": i, "item": it})
            done.append(it)
        ev({"type": "response.completed", "response": {"id": rid, "status": "completed", "output": done, "usage": {
            "input_tokens": 1500, "input_tokens_details": {"cached_tokens": 500}, "output_tokens": 60,
            "output_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 1560}}})


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_idle(pid, t=60.0):
    end = time.time() + t
    time.sleep(0.2)
    while time.time() < end:
        if not cx.is_sending(pid):
            return True
        time.sleep(0.1)
    return False


def _lines(pid):
    return cx.feed("codex--" + pid, limit=400)["lines"]


if os.environ.get("CODEX_TEST_EXE"):
    # another Codex than the one on PATH, e.g. the newest one unpacked to a folder of its own
    _exe = os.environ["CODEX_TEST_EXE"]
    _fixed = {"shim": _exe, "exe": _exe, "argv": [_exe], "version": cx._version_of([_exe]), "npm": False, "package": ""}
    cx.find_codex = lambda refresh=False: _fixed
info = cx.find_codex(refresh=True)
if not info:
    skip += 1
    print("\n[6] SKIP: the Codex CLI is not installed on this PC")
else:
    print("\n[6] the real Codex %s against a stand-in model" % info.get("version"))
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    home = TMP / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'model = "gpt-5.6-sol"\nmodel_provider = "standin"\n\n[model_providers.standin]\nname = "Stand-in"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\nenv_key = "STANDIN_KEY"\n'
        'supports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n', encoding="utf-8")
    os.environ["CODEX_HOME"] = str(home)
    os.environ["STANDIN_KEY"] = "sk-standin"
    work = TMP / "work"
    work.mkdir(parents=True, exist_ok=True)
    PID = "c--test-work"
    try:
        st = cx.status(fresh=True)
        check("status: installed, running, and ready to chat", st["installed"] and st["running"] and st.get("ready"), st)
        check("status names the version", bool(st.get("version")), st.get("version"))
        ms = cx.models(refresh=True)
        check("models come from Codex, each with efforts", ms["models"] and all(m["efforts"] for m in ms["models"]), ms)
        check("one model is the default", bool(ms.get("default")), ms.get("default"))

        r = cx.send(PID, "scenario:hello", str(work), mode="full")
        check("a message starts a turn", r.get("ok") and r.get("session_id"), r)
        check("the turn finishes", _wait_idle(PID))
        L = _lines(PID)
        check("the feed has the question, the thinking and the answer",
              [e["kind"] for e in L][:1] == ["user"] and any(e["kind"] == "thinking" for e in L)
              and any(e["kind"] == "text" and "stand-in model" in e["text"] for e in L), L)
        first_tid = r.get("session_id")

        r = cx.send(PID, "scenario:shell", str(work), mode="full")
        check("full access: the same conversation continues", r.get("session_id") == first_tid, r)
        _wait_idle(PID)
        L = _lines(PID)
        sh = [e for e in L if e.get("title") in ("PowerShell", "Bash")]
        check("the command ran and shows as typed", sh and "echo studio-codex-test" in sh[-1].get("command", ""), sh)
        check("...with its real output", any(e["kind"] == "result" and "studio-codex-test" in e["text"] for e in L))

        r = cx.send(PID, "scenario:edit", str(work), mode="full")
        _wait_idle(PID)
        L = _lines(PID)
        check("the patch really wrote the file", (work / "hello_codex.txt").is_file()
              and (work / "hello_codex.txt").read_text().splitlines() == ["first line", "second line"])
        check("the edit shows as a Write card with +2", any(e.get("title") == "Write" and e["diff"]["added"] == 2
                                                           and e["subtitle"].endswith("hello_codex.txt") for e in L), L[-6:])

        r = cx.send(PID, "scenario:plan", str(work), mode="full")
        _wait_idle(PID)
        if any(s == "plan" and n >= 1 for s, n in SCRIPT_LOG):
            L = _lines(PID)
            ph = [e for e in L if e.get("title") == "Phases"]
            check("a plan from Codex is the phase list in the feed", ph and ph[-1]["todos"][1] ==
                  {"content": "Write the answer", "status": "in_progress"}, ph)
            td = cx.todos("codex--" + PID)
            check("...and the Phases panel has it", td["total"] == 2 and td["done"] == 1, td)
        else:
            skip += 1
            print("  SKIP  plans: this Codex (%s) offers no update_plan tool" % info.get("version"))

        r = cx.send(PID, "scenario:agent", str(work), mode="full")
        _wait_idle(PID, 90)
        L = _lines(PID)
        ag = [e for e in L if e["kind"] == "agent"]
        if any(s == "agent" and n >= 1 for s, n in SCRIPT_LOG):
            # its task text travels encrypted on a real account, so the card names it and shows its answer
            check("a subagent shows as a card with its name and answer", ag and ag[-1]["agent"]["description"] == "helper"
                  and "stand-in model" in ag[-1]["agent"]["result"] and not ag[-1]["agent"]["running"], ag)
            if ag:
                d = cx.subagent_detail("codex--" + PID, ag[-1]["agent"]["agent_id"])
                check("the subagent opens on its own timeline", any(e["kind"] == "text" for e in d["lines"]), d)
            rows = cx.subagents("codex--" + PID)["agents"]
            check("the subagent is listed for the folder", any(a["agent_id"] == (ag[-1]["agent"]["agent_id"] if ag else "") for a in rows), rows)
        else:
            skip += 1
            print("  SKIP  subagents: this Codex (%s) offers no spawn_agent tool" % info.get("version"))

        # a message during a turn steers it
        r1 = cx.send(PID, "scenario:slow", str(work), mode="full")
        time.sleep(1.2)
        live = cx.live_state("codex--" + PID)
        check("while it works, the live state says so", live.get("working") and live.get("elapsed", 0) > 0, live)
        r2 = cx.send(PID, "and one more thing", str(work), mode="full")
        check("a message sent mid-turn steers it (or waits its turn)", r2.get("ok") and (r2.get("steering") or r2.get("queued")), r2)
        _wait_idle(PID, 60)
        L = _lines(PID)
        check("the steering message is in the feed", any(e["kind"] == "user" and e["text"] == "and one more thing" for e in L))

        # Stop
        cx.send(PID, "scenario:slow", str(work), mode="full")
        time.sleep(1.0)
        c = cx.cancel("codex--" + PID)
        check("Stop interrupts the turn", c.get("ok"), c)
        check("...and it ends", _wait_idle(PID, 20))
        L = _lines(PID)
        check("the feed says it was stopped", any(e["kind"] == "result" and e["text"] == "Stopped." for e in L), L[-4:])

        # ask mode: the command waits for an answer
        (work / "hello_codex.txt").unlink(missing_ok=True)
        r = cx.send(PID, "scenario:shell", str(work), mode="ask")
        asked = False
        end = time.time() + 30
        while time.time() < end:
            q = [e for e in _lines(PID) if e["kind"] == "question"]
            if q:
                asked = True
                break
            if not cx.is_sending(PID):
                break
            time.sleep(0.2)
        if asked:
            check("ask mode: the command waits, as a question with Allow and Deny",
                  {o["label"] for o in q[-1]["options"]} >= {"Allow", "Deny"} and "echo studio-codex-test" in q[-1]["text"], q)
            check("the live line says it waits for you", cx.live_state("codex--" + PID).get("activity") == "Waiting for your answer")
            a = cx.send(PID, "Allow", str(work), mode="ask")
            check("answering Allow resolves it", a.get("approval") == "Allow", a)
            _wait_idle(PID, 60)
            L = _lines(PID)
            check("...and the command ran", any(e["kind"] == "result" and "studio-codex-test" in e["text"] for e in L[-8:]), L[-8:])
        else:
            skip += 1
            print("  SKIP  ask mode: this Codex ran the command without asking (its sandbox is set up)")
            _wait_idle(PID, 60)

        # a new conversation
        r = cx.send(PID, "scenario:hello", str(work), mode="full", new_session=True)
        _wait_idle(PID)
        check("a new conversation gets a new thread", r.get("session_id") and r.get("session_id") != first_tid, r)
        ss = cx.sessions("codex--" + PID)
        check("both conversations are listed, the new one active", len(ss) >= 2 and ss[0]["active"], ss)
        old = cx.feed("codex--" + PID, session=first_tid)["lines"]
        check("the old conversation still opens", any(e["kind"] == "user" and e["text"] == "scenario:hello" for e in old))

        ctx = cx.context("codex--" + PID)
        check("the context meter has a window and a fill", ctx.get("ctx_max") and ctx.get("ctx_used"), ctx)

        once = cx.run_once(str(work), "scenario:hello")
        check("a one-shot question (the co-agent review) returns the answer", "stand-in model" in once, once)
        check("...without joining the folder's conversation list", len(cx.sessions("codex--" + PID)) == len(ss))
    finally:
        cx.stop_server()

    print("\n[7] sign-in through the real Codex (a temporary Codex home)")
    home2 = TMP / "codex-home-auth"
    home2.mkdir(parents=True, exist_ok=True)
    (home2 / "config.toml").write_text(f'openai_base_url = "http://127.0.0.1:{port}/v1"\n', encoding="utf-8")
    os.environ["CODEX_HOME"] = str(home2)
    try:
        st = cx.status(fresh=True)
        check("a fresh home is signed out and needs a sign-in", not st["signed_in"] and st["requires_auth"] and not st.get("ready"), st)
        r = cx.send(PID + "2", "hello", str(work), mode="full")
        check("sending then asks for a sign-in instead of failing silently", not r.get("ok") and r.get("needs_login"), r)
        r = cx.login("chatgpt", open_browser=False)
        check("ChatGPT sign-in gives the page to open", r.get("ok") and str((r.get("login") or {}).get("auth_url", "")).startswith("https://"), r)
        check("...and waits for it", (r.get("login") or {}).get("pending") is True)
        cx.login_cancel()
        check("cancel clears it", cx.status()["login"] is None)
        r = cx.login("apikey", api_key="sk-test-not-real")
        check("an API key signs in at once", r.get("ok") and r["account"]["signed_in"] and r["account"]["auth"] == "apiKey", r)
        check("...where the Codex CLI keeps it (its own auth.json)", (home2 / "auth.json").is_file())
        st = cx.status(fresh=True)
        check("status then says signed in with an API key", st["signed_in"] and st["auth"] == "apiKey" and st.get("ready"), st)
        r = cx.logout()
        check("sign out", r.get("ok") and not r["account"]["signed_in"], r)
        r = cx.login("apikey", api_key="")
        check("an empty key is refused before Codex sees it", not r.get("ok"), r)
    finally:
        cx.stop_server()
        httpd.shutdown()

print("\n%d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)

# -*- coding: utf-8 -*-
"""An agent's tokens, counted the way they were spent: every API call ONCE.

The user saw the subagent card say far more tokens than an agent could have used. Measured on the
v3.1 tree agent: the card said 77.9M, the calls say 33.6M in all, and the NEW tokens - the number
worth a headline - were 747k. Three faults, one in each place that counted:

  1. The card added every transcript line, and Claude Code writes one line per block of an answer
     (thinking, text, each tool call), each repeating the usage of the WHOLE call.
  2. The card read only the last 400 records, so a long agent's first calls were never counted.
  3. A Task agent's own record (usage, totalTokens) is its LAST call, and it replaced the card's
     figures. The Workflows tab showed the journal's context size for finished runs.

Run:  python agent_tokens_test.py     (from backend/)
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from asset_studio import agent_usage as AU                                 # noqa: E402
from asset_studio import subagents as sa                                   # noqa: E402
from asset_studio import workflows as wf                                   # noqa: E402
from asset_studio import mission                                           # noqa: E402

ok = fail = skip = 0


def check(name, cond, got=None):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + name)
    else:
        fail += 1
        print("  FAIL  " + name + ("   " + repr(got)[:300] if got is not None else ""))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s: %s" % (name, why))


def u(inp=0, out=0, cr=0, cw=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw}


def asst(mid, usage, block, ts="2026-09-23T15:44:00.000Z"):
    """One assistant line, the way Claude Code writes it: ONE block, the call's whole usage."""
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "role": "assistant", "model": "claude-opus-5-5",
                        "usage": usage, "content": [block]}}


def tool(tid, name="Bash", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {"command": "ls"}}


# ---------------------------------------------------------------------------------------------
print("\nOne row per API call")
c = AU.Calls()
c.add("m1", u(3, 8, 1000, 200))          # thinking line: the output count as it stood then
c.add("m1", u(3, 8, 1000, 200))          # text line
c.add("m1", u(3, 245, 1000, 200))        # the call's last line: the final output count
c.add("m2", u(0, 100, 1200, 50))
t = c.totals()
check("three lines of one call count once, with the call's final output",
      len(c) == 2 and t["output"] == 345 and t["cache_read"] == 2200 and t["input"] == 3, t)
check("new tokens = input + cache writes + output", AU.new_tokens(t) == 3 + 250 + 345, AU.new_tokens(t))
check("wire = new tokens + cache reads", AU.wire_tokens(t) == AU.new_tokens(t) + 2200)
check("the first call's context is what the agent was handed", c.first == 1203, c.first)
check("the peak is the largest request", c.peak == 1250, c.peak)
anon = AU.Calls()
anon.add("", u(1, 1))
anon.add("", u(1, 1))
check("lines with no call id at all are each their own call, as before", len(anon) == 2)
check("the 1-hour cache split is read from its own object",
      AU.of({"cache_creation_input_tokens": 9, "cache_creation": {"ephemeral_1h_input_tokens": 4}})["cache_write_1h"] == 4)

# ---------------------------------------------------------------------------------------------
print("\nThe subagent ledger: the whole transcript, then only what was appended")
TMP = Path(tempfile.mkdtemp(prefix="agent-tokens-"))
f = TMP / "agent-a1.jsonl"
lines = [{"type": "user", "timestamp": "2026-09-23T15:43:00.000Z",
          "message": {"role": "user", "content": "build the tree"}}]
for i in range(300):                       # more than the 400-record tail can hold, in lines
    mid = "msg_%03d" % i
    lines.append(asst(mid, u(1, 5, 10_000 + i, 100), {"type": "thinking", "thinking": "..."}))
    lines.append(asst(mid, u(1, 5, 10_000 + i, 100), {"type": "text", "text": "ok"}))
    lines.append(asst(mid, u(1, 40, 10_000 + i, 100), tool("t%03d" % i)))
    lines.append({"type": "user", "timestamp": "2026-09-23T15:44:30.000Z",
                  "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t%03d" % i,
                                                          "content": "x" * 50}]}})
lines[-2]["timestamp"] = "2026-09-23T16:20:00.000Z"
f.write_text("".join(json.dumps(o) + "\n" for o in lines), encoding="utf-8")
sa._SCANS.clear()
L = sa._ledger_read(f)
check("300 calls written as 900 assistant lines are 300 calls", L["turns"] == 300, L["turns"])
check("...their output is 300 x 40, not the line sum", L["usage"]["output"] == 12_000, L["usage"])
check("...cache reads are counted once per call", L["usage"]["cache_read"] == sum(10_000 + i for i in range(300)),
      L["usage"]["cache_read"])
check("the headline is the new tokens", L["tokens"] == 300 * 1 + 300 * 100 + 12_000, L["tokens"])
check("...and `wire` adds the cache reads back", L["wire"] == L["tokens"] + L["usage"]["cache_read"])
check("every tool call is counted - the first ones too (the tail missed them)", L["tools"] == 300, L["tools"])
check("the duration runs from the first record to the last answer", L["ms"] == 37 * 60 * 1000, L["ms"])
check("the agent was handed its first call's context (input + cache read + cache write)",
      L["inherited"] == 1 + 10_000 + 100, L["inherited"])

# Appended: one more call, and a line still being written (no newline yet).
with f.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(asst("msg_new", u(2, 7, 20_000, 300), tool("t_new"))) + "\n")
    fh.write(json.dumps(asst("msg_half", u(1, 1, 1, 1), tool("t_half")))[:40])
pos_before = sa._SCANS[str(f)].pos
L2 = sa._ledger_read(f)
check("a grown file adds only its new complete lines", L2["turns"] == 301 and L2["tools"] == 301, (L2["turns"], L2["tools"]))
check("...read from where the last pass stopped", sa._SCANS[str(f)].pos > pos_before)
with f.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(asst("msg_half", u(1, 1, 1, 1), tool("t_half")))[40:] + "\n")
L3 = sa._ledger_read(f)
check("a line finished later is counted once it is whole", L3["turns"] == 302 and L3["tools"] == 302,
      (L3["turns"], L3["tools"]))
f.write_text(json.dumps(asst("only", u(1, 2, 3, 4), tool("tz"))) + "\n", encoding="utf-8")
L4 = sa._ledger_read(f)
check("a file rewritten shorter is read again from the start", L4["turns"] == 1 and L4["tools"] == 1,
      (L4["turns"], L4["tools"]))
check("the remembered ledgers are a new file: the old one holds the line sums",
      sa._LEDGER_DISK.name == "agent_ledgers_v2")

# ---------------------------------------------------------------------------------------------
print("\nA Task agent's own record is its LAST call")
rec = sa._record({"timestamp": "2026-09-23T16:00:00.000Z", "toolUseResult": {
    "agentId": "a9", "status": "completed", "totalTokens": 58844, "totalToolUseCount": 24,
    "totalDurationMs": 60000, "usage": u(2, 5430, 52531, 881),
    "toolStats": {"readCount": 3, "bashCount": 20}}})
check("its totalTokens and usage are not carried as the agent's tokens",
      "tokens" not in rec and "usage" not in rec, rec)
check("...its whole-run counts still are", rec.get("tools") == 24 and rec.get("ms") == 60000
      and rec.get("stats", {}).get("bash") == 20, rec)
merged = sa._merge({"tokens": 747_364, "usage": {"input": 206, "output": 214_051, "cache_read": 1, "cache_write": 533_107,
                                                 "cache_write_1h": 0}, "model": "claude-opus-5-5"}, rec)
check("so the card keeps the transcript's figures", merged["tokens"] == 747_364 and merged["usage"]["output"] == 214_051,
      merged)

# ---------------------------------------------------------------------------------------------
print("\nThe Workflows tab: the same figure as the card")
run = TMP / "wf_x"
run.mkdir()
g = run / "agent-ab1.jsonl"
g.write_text(json.dumps({"type": "user", "timestamp": "2026-09-23T10:00:00.000Z",
                         "message": {"role": "user", "content": "go"}}) + "\n"
             + json.dumps(asst("m1", u(5, 10, 100, 1000), {"type": "text", "text": "a"})) + "\n"
             + json.dumps(asst("m1", u(5, 90, 100, 1000), tool("q1"))) + "\n", encoding="utf-8")
agents = [{"agentId": "ab1", "tokens": 533_117}, {"agentId": "gone", "tokens": 7}]
total = wf._overlay_real_tokens(agents, run)
check("a finished run's agent shows its new tokens, not the journal's context size",
      agents[0]["tokens"] == 5 + 1000 + 90, agents[0])
check("...an agent with no transcript keeps the journal's figure, and the run adds them up",
      agents[1]["tokens"] == 7 and total == 1095 + 7, (agents, total))
check("no agent folder: the journal's figures stand", wf._overlay_real_tokens([{"agentId": "x"}], None) is None)
wf._USAGE_CACHE.clear()
check("a live run's agent reads the same figure (it added up output tokens by line)",
      wf._agent_usage(g)[0] == 1095, wf._agent_usage(g))

# ---------------------------------------------------------------------------------------------
print("\nThe main chat's own counter")
turn = [{"type": "user", "message": {"role": "user", "content": "hi"}},
        asst("c1", u(0, 8, 0, 0), {"type": "thinking", "thinking": ""}),
        asst("c1", u(0, 8, 0, 0), {"type": "text", "text": "x"}),
        asst("c1", u(0, 245, 0, 0), tool("k1"))]
check("one call's lines count once, at its final output", mission._turn_tokens(turn) == 245, mission._turn_tokens(turn))
old = [{"type": "user", "message": {"role": "user", "content": "hi"}},
       {"type": "assistant", "message": {"usage": u(0, 10)}}, {"type": "assistant", "message": {"usage": u(0, 5)}}]
check("records with no call id still add up as before", mission._turn_tokens(old) == 15, mission._turn_tokens(old))

# ---------------------------------------------------------------------------------------------
print("\nThe real agent the user asked about")
REAL = Path(os.path.expanduser("~")) / ".claude" / "projects" / "c--Users-Administrator-Downloads-STUDIO" / \
    "5463c4e9-38f5-4d90-b81d-9b20bc6dad65" / "subagents" / "workflows" / "wf_0abbad1c-321" / "agent-ab413e3e89ed5e5cd.jsonl"
if REAL.is_file():
    sa._SCANS.pop(str(REAL), None)
    R = sa._ledger_read(REAL)
    check("the v3.1 tree agent: 747,364 new tokens in 103 calls and 115 tool calls",
          R["tokens"] == 747_364 and R["turns"] == 103 and R["tools"] == 115, (R["tokens"], R["turns"], R["tools"]))
    check("...33,612,390 in all, 32,865,026 of them cache reads",
          R["wire"] == 33_612_390 and R["usage"]["cache_read"] == 32_865_026, (R["wire"], R["usage"]))
else:
    skipped("the v3.1 tree agent", "its transcript is not on this PC")

shutil.rmtree(TMP, ignore_errors=True)
print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)

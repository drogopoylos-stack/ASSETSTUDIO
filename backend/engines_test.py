# -*- coding: utf-8 -*-
"""One interface for every engine (engines.py) — and every door in the app goes through it.

The bug this guards against is one bug with many faces: a feature written as
`if project_id.startswith("codex--")` in one place and forgotten in the next, so it works for
Claude and silently does nothing for Codex or DeepSeek. So this checks two things:

  * the CONTRACT — every engine answers every question the app asks (busy, cancel, live state,
    live rows, reset, skill roots, and the feed readers when it keeps its own log), and the
    registry maps every id shape to the right engine.
  * the DOORS — the session layer, the feed readers and the HTTP routes reach the engine's own
    implementation through the registry, for each engine, with nothing spawned.

Run:  backend/.venv/Scripts/python.exe backend/engines_test.py
"""
import os
import tempfile
from unittest.mock import patch

os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="engines-test-")

from asset_studio import cc_session, codex_app, deepseek_session, engines, mission, skills  # noqa: E402
from asset_studio.routers import mission as mission_router  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  -- " + str(detail)[:300]) if detail else ""))


F = "d--engines-fixture"

# --- the registry -------------------------------------------------------------------------------
print("registry")
check("an agent id names its engine",
      [engines.for_agent(a).id for a in ("claude", "codex", "deepseek-harness")]
      == ["claude", "codex", "deepseek-harness"])
check("an alternate engine (kimi) runs on Claude's plumbing", engines.for_agent("kimi") is engines.CLAUDE)
check("a feed id names its engine",
      (engines.for_feed(F), engines.for_feed("codex--" + F), engines.for_feed("deepseek-harness--" + F))
      == (engines.CLAUDE, engines.CODEX, engines.DEEPSEEK))
check("an alternate engine's feed is Claude's", engines.for_feed("kimi--" + F) is engines.CLAUDE)
check("bare_folder strips every kind of prefix",
      {engines.bare_folder(x) for x in (F, "codex--" + F, "deepseek-harness--" + F, "kimi--" + F, "qwen--" + F)}
      == {F})
check("a person's name for each feed",
      [engines.name_of(x) for x in (F, "codex--" + F, "deepseek-harness--" + F, "kimi--" + F)]
      == ["Claude", "Codex", "DeepSeek", "kimi"])
feeds = engines.folder_feeds(F)
check("folder_feeds lists each engine's feed once",
      len(feeds) == len(set(feeds)) and {F, "codex--" + F, "deepseek-harness--" + F, "kimi--" + F} <= set(feeds),
      feeds)
check("only Codex answers the feed readers itself",
      engines.native("codex--" + F) is engines.CODEX and engines.native(F) is None
      and engines.native("deepseek-harness--" + F) is None)

# --- the contract -------------------------------------------------------------------------------
print("contract")
REQUIRED = ("is_sending", "cancel", "live_state")
NATIVE = ("feed", "sessions", "context", "todos", "subagents", "subagent_detail")
for e in engines.ALL:
    for m in REQUIRED + (NATIVE if e.native_feed else ()):
        check(f"{e.name} implements {m}", getattr(type(e), m) is not getattr(engines.Engine, m))
    check(f"{e.name} has a name, an id and a prefix rule", bool(e.name and e.id) and (e.prefix == "" or e.prefix.endswith("--")))
    if e is not engines.CLAUDE:
        check(f"{e.name} starts its own turn", type(e).start_turn is not engines.Engine.start_turn)

# --- the doors ----------------------------------------------------------------------------------
print("doors")
with patch.object(codex_app, "is_sending", return_value=True) as cx, \
        patch.object(deepseek_session, "is_sending", return_value=True) as ds, \
        patch.object(cc_session, "_claude_is_sending", return_value=False) as cl:
    check("cc_session.is_sending asks Codex for a Codex feed", cc_session.is_sending("codex--" + F) and cx.called)
    check("...DeepSeek for a DeepSeek feed", cc_session.is_sending("deepseek-harness--" + F) and ds.called)
    check("...and Claude for a bare folder", cc_session.is_sending(F) is False and cl.called)
    check("the /sending route answers for Codex too (it used to need a special case)",
          mission_router.sending("codex--" + F)["sending"] is True)

with patch.object(codex_app, "live_state", return_value={"working": True, "who": "codex"}), \
        patch.object(deepseek_session, "live_state", return_value={"working": True, "who": "dsh"}):
    check("live_state reaches Codex", cc_session.live_state("codex--" + F).get("who") == "codex")
    check("live_state reaches DeepSeek", cc_session.live_state("deepseek-harness--" + F).get("who") == "dsh")
check("live_state for an idle Claude folder", cc_session.live_state(F).get("working") is False)

with patch.object(codex_app, "cancel", return_value={"ok": True, "e": "codex"}) as cc, \
        patch.object(deepseek_session, "cancel", return_value={"ok": True, "e": "dsh"}) as dc:
    check("cancel reaches Codex", cc_session.cancel("codex--" + F).get("e") == "codex")
    check("cancel reaches DeepSeek", cc_session.cancel("deepseek-harness--" + F).get("e") == "dsh")
    check("the cancel route with agent=codex stops Codex",
          mission_router.cancel_send(F, agent="codex").get("e") == "codex"
          and cc.call_args.args[0] == "codex--" + F, cc.call_args)
    check("the cancel route with agent=deepseek-harness stops DeepSeek",
          mission_router.cancel_send(F, agent="deepseek-harness").get("e") == "dsh")

with patch.object(engines.CODEX, "start_turn", return_value={"ok": True, "e": "codex"}) as st, \
        patch("asset_studio.spend.over_cap", return_value=""), \
        patch.object(cc_session, "_snapshot_before_turn"):
    r = cc_session.send(F, "hello", agent="codex", model="m1")
    check("send(agent=codex) goes to the Codex adapter", r.get("e") == "codex" and st.call_args.kwargs["model"] == "m1", r)
with patch.object(engines.DEEPSEEK, "start_turn", return_value={"ok": True, "e": "dsh"}), \
        patch("asset_studio.spend.over_cap", return_value=""), \
        patch.object(cc_session, "_snapshot_before_turn"):
    check("send(agent=deepseek-harness) goes to the DeepSeek adapter",
          cc_session.send(F, "hello", agent="deepseek-harness").get("e") == "dsh")
with patch("asset_studio.spend.over_cap", return_value="cap reached"), \
        patch.object(engines.CODEX, "start_turn", side_effect=AssertionError("ran")):
    r = cc_session.send(F, "hello", agent="codex")
    check("the money guard still runs before every adapter", r.get("ok") is False and "cap" in r.get("error", ""), r)

with patch.object(codex_app, "feed", return_value={"lines": ["codex"]}), \
        patch.object(codex_app, "sessions", return_value=["s"]), \
        patch.object(codex_app, "context", return_value={"c": 1}), \
        patch.object(codex_app, "todos", return_value={"t": 1}), \
        patch.object(codex_app, "subagents", return_value={"a": 1}), \
        patch.object(codex_app, "subagent_detail", return_value={"d": 1}):
    check("the feed reader asks Codex for a Codex feed", mission.project_feed("codex--" + F) == {"lines": ["codex"]})
    check("...and sessions", mission.list_sessions("codex--" + F) == ["s"])
    check("...and context", mission.project_context("codex--" + F) == {"c": 1})
    check("...and todos", mission.project_todos("codex--" + F) == {"t": 1})
    check("...and subagents", mission.project_subagents("codex--" + F) == {"a": 1})
    check("...and one subagent", mission.project_subagent("codex--" + F, "x") == {"d": 1})
    check("...and has no collision report to guess at", mission.project_subagent_collisions("codex--" + F) == [])

with patch.object(skills, "_claude_roots", return_value=["claude-roots"]), \
        patch.object(skills, "_dsh_roots", return_value=["dsh-roots"]):
    check("skill roots: each engine answers for itself",
          [skills._roots_for(F, a) for a in ("claude", "codex", "deepseek-harness")]
          == [["claude-roots"], [], ["dsh-roots"]])

with patch.object(deepseek_session, "reset") as dr:
    engines.for_feed("deepseek-harness--" + F).reset("deepseek-harness--" + F)
    check("reset reaches DeepSeek (the edit-and-resend memory fix)", dr.called)

with patch.object(codex_app, "live_rows", return_value=[(F, True, 2)]), \
        patch.object(deepseek_session, "live_rows", return_value=[("d--other", False)]):
    st = cc_session.live_status()
    check("live_status lists the Codex conversation under its feed and its folder",
          st["statuses"].get("codex--" + F) is True and st["statuses"].get(F) is True, st["statuses"])
    check("...counts its subagents", st["running_agents"].get("codex--" + F) == 2)
    check("...and the DeepSeek one, idle", st["statuses"].get("deepseek-harness--d--other") is False)
    check("...naming the engine per folder", st["agents"].get(F) == "codex" and st["agents"].get("d--other") == "deepseek-harness",
          st["agents"])

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)

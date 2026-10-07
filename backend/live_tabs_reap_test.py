"""Does a game tab close once no agent uses it, and does the engine stop calling it "in use"?

A tab is opened by an agent's first live call and kept, so the next call finds the game as it was
left. Nothing ever closed one. The browser closes after ten idle minutes only when all of it is
idle, so one agent at work kept every finished agent's game running behind it: four PlayCanvas
games were found open at once, two of them 26 and 38 hours after their agents had stopped, and the
Engine window named all four as "in use" because `busy` was simply "a tab is open".

No browser is started here. `review._state` says whether one runs, and the websocket and the CDP
client are stand-ins that record what they were asked to close.
"""
import io
import sys
import time

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import engine                  # noqa: E402
from asset_studio import live                    # noqa: E402
from asset_studio import live_stream             # noqa: E402
from asset_studio import review                  # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


class _Proc:
    def poll(self):
        return None


class _WS:
    async def close(self):
        pass


closed_targets: list = []


class _Cdp:
    def __init__(self, ws):
        pass

    async def call(self, method, params):
        if method == "Target.closeTarget":
            closed_targets.append(params.get("targetId"))
        return {}


async def _fake_connect(url):
    return _WS()


async def _refuse_connect(url):
    raise AssertionError("no browser runs; nothing may connect to one")


def _refuse_start(*a, **k):
    raise AssertionError("the reaper must never start the browser")


saved = {
    "tabs": dict(live._tabs), "activity": dict(live._ACTIVITY), "pref": live._engine_pref,
    "connect": live._connect, "cdp": review._Cdp, "ensure": review._ensure_browser,
    "proc": review._state.get("proc"), "ws": review._state.get("ws"),
    "watched": live_stream.watched_keys, "status": live.status, "records": engine._forge_records,
}
try:
    live._tabs.clear()
    live._ACTIVITY.clear()
    review._ensure_browser = _refuse_start
    live_stream.watched_keys = lambda: set()

    print("\nThe decision")
    now = 1_000_000.0
    tabs = {"used": {"seen": now - 5}, "old": {"seen": now - 700}, "watched": {"seen": now - 700},
            "never-seen": {"opened": now - 900}}
    keys = live._idle_tab_keys(tabs, now, 600, keep={"watched"})
    check("a tab no agent called for longer than the limit is closed", "old" in keys, keys)
    check("a record with only an opening time is judged by it", "never-seen" in keys, keys)
    check("a tab used seconds ago stays", "used" not in keys, keys)
    check("a watched tab stays, however quiet", "watched" not in keys, keys)

    print("\nThe limit, from Settings")
    live._engine_pref = lambda key, default=None: 3 if key == "live_tab_idle_min" else default
    check("three minutes in Settings is 180 seconds", live._tab_idle_limit() == 180.0, live._tab_idle_limit())
    for bad in (0, None, "x", -5):
        live._engine_pref = lambda key, default=None, b=bad: b if key == "live_tab_idle_min" else default
        check("a setting of %r falls back to ten minutes" % (bad,), live._tab_idle_limit() == 600.0,
              live._tab_idle_limit())
    live._engine_pref = saved["pref"]

    print("\nNo browser running")
    review._state["proc"] = None
    live._connect = _refuse_connect
    k_old, k_fresh = live.key_for("C:/tabs-test/old"), live.key_for("C:/tabs-test/fresh")
    live._tabs[k_old] = {"project": "C:/tabs-test/old", "target": "T-old", "opened": time.time() - 7200,
                         "seen": time.time() - 3600}
    live._tabs[k_fresh] = {"project": "C:/tabs-test/fresh", "target": "", "opened": time.time(),
                           "seen": time.time() - 5}
    r = live.reap_idle_tabs()
    check("an hour-old record is forgotten: its tab died with the browser",
          k_old not in live._tabs and r["forgotten"] == 1 and r["closed"] == 0, r)
    check("a record a call made seconds ago is left for that call", k_fresh in live._tabs)
    check("and nothing started or connected to a browser to do it", True)
    live._tabs.clear()

    print("\nThe browser running")
    review._state["proc"] = _Proc()
    review._state["ws"] = "ws://127.0.0.1:1/devtools/browser/test"
    live._connect = _fake_connect
    review._Cdp = _Cdp
    k_idle = live.key_for("C:/tabs-test/finished-agent")
    k_live = live.key_for("C:/tabs-test/working-agent")
    k_watch = live.key_for("C:/tabs-test/watched")
    k_call = live.key_for("C:/tabs-test/long-call")
    t = time.time()
    live._tabs[k_idle] = {"project": "C:/tabs-test/finished-agent", "target": "T-idle", "opened": t - 99999,
                          "seen": t - 3600}
    live._tabs[k_live] = {"project": "C:/tabs-test/working-agent", "target": "T-live", "opened": t - 99999,
                          "seen": t - 10}
    live._tabs[k_watch] = {"project": "C:/tabs-test/watched", "target": "T-watch", "opened": t - 99999,
                           "seen": t - 3600}
    live._tabs[k_call] = {"project": "C:/tabs-test/long-call", "target": "T-call", "opened": t - 99999,
                          "seen": t - 3600}
    live_stream.watched_keys = lambda: {k_watch}
    live._ACTIVITY[live._slug("C:/tabs-test/long-call")] = {"project": "C:/tabs-test/long-call",
                                                            "phase": "aim", "since": t - 30}
    r = live.reap_idle_tabs()
    check("the finished agent's tab is closed in the browser", closed_targets == ["T-idle"], closed_targets)
    check("and its record is gone", k_idle not in live._tabs and r["closed"] == 1, r)
    check("the working agent's tab stays", k_live in live._tabs)
    check("a tab someone watches in the Game tab stays", k_watch in live._tabs)
    check("a tab a call is working in right now stays", k_call in live._tabs)
    live._ACTIVITY.clear()

    print("\nWho is watching")

    class _Cast:
        def __init__(self, n):
            self.viewers = {i: object() for i in range(n)}

    saved_casts = dict(live_stream._casts)
    try:
        live_stream._casts.clear()
        live_stream._casts.update({"watched-tab": _Cast(1), "left-tab": _Cast(0)})
        check("a tab with a viewer is watched; a screencast nobody watches is not",
              saved["watched"]() == {"watched-tab"}, saved["watched"]())
    finally:
        live_stream._casts.clear()
        live_stream._casts.update(saved_casts)

    print("\nWhat the engine says")
    engine._forge_records = lambda: []
    live.status = lambda: {"ok": True, "enabled": True, "available": True, "why": "", "tabs": [
        {"project": "C:/tabs-test/working-agent", "url": "", "engine": "playcanvas", "device": "desktop",
         "served_by": "", "age_s": 900.0, "idle_s": 12.0},
        {"project": "C:/tabs-test/finished-agent", "url": "", "engine": "playcanvas", "device": "desktop",
         "served_by": "", "age_s": 139246.9, "idle_s": 136431.4},
    ]}
    st = engine.state()
    check("in use while one agent called its tab in the last five minutes", st["busy"] is True, st.get("busy"))
    check("and only that tab counts", st.get("in_use") == 1 and st.get("open_games") == 2,
          (st.get("in_use"), st.get("open_games")))
    check("each tab says whether it is in use", [x.get("active") for x in st["tabs"]] == [True, False],
          [x.get("active") for x in st["tabs"]])
    live.status = lambda: {"ok": True, "enabled": True, "available": True, "why": "", "tabs": [
        {"project": "C:/tabs-test/finished-agent", "url": "", "engine": "playcanvas", "device": "desktop",
         "served_by": "", "age_s": 139246.9, "idle_s": 136431.4},
    ]}
    st = engine.state()
    check("a tab open for a day with no agent on it is not 'in use'", st["busy"] is False and st.get("in_use") == 0,
          (st.get("busy"), st.get("in_use")))
finally:
    live._tabs.clear()
    live._tabs.update(saved["tabs"])
    live._ACTIVITY.clear()
    live._ACTIVITY.update(saved["activity"])
    live._engine_pref = saved["pref"]
    live._connect = saved["connect"]
    review._Cdp = saved["cdp"]
    review._ensure_browser = saved["ensure"]
    review._state["proc"] = saved["proc"]
    review._state["ws"] = saved["ws"]
    live_stream.watched_keys = saved["watched"]
    live.status = saved["status"]
    engine._forge_records = saved["records"]

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

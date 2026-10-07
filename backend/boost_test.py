# -*- coding: utf-8 -*-
"""BOOST: the local economies, and the promise that each number on the button is real.

The whole feature is a claim about money, so every claim is checked here rather than
asserted in a comment:

  * the compressor folds a run of identical log lines, collapses blank runs and strips
    ANSI — and NEVER rewords the user's own sentence or drops an identifier
  * a fenced code block is left completely alone, because a run of identical lines
    inside one can be data, not noise
  * a rewrite that saves less than the floor is thrown away whole, so the transcript
    never differs from what was typed for no reason
  * "safe" leaves a base64 payload alone; only "hard" elides it
  * a symbol is only taken from a message when the message is POINTING at one
    (backticks, snake_case, camelCase) — English prose is not a symbol lookup
  * switching BOOST on applies its profile, and switching it off puts back exactly the
    values that were there before — including a switch the user had set by hand
  * the ledger counts tokens ONLY where text was removed, and a prefetch (which COSTS
    tokens) is counted as a lookup, never as a saving

Run:  backend/.venv/Scripts/python.exe backend/boost_test.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# The Studio's data dir holds settings.json and the ledger. Point it at a throwaway BEFORE
# the package is imported, or this test would edit the running app's own settings.
_TMP = Path(tempfile.mkdtemp(prefix="boost-test-"))
os.environ["ASSET_STUDIO_DATA"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import boost                       # noqa: E402
from asset_studio.config import settings             # noqa: E402

passed = 0
fails = []


def ok(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
        return
    fails.append(name + ("   <- " + str(extra) if extra else ""))
    print("  FAIL  %s   %s" % (name, extra))


def c(text):
    """compress() with the floor out of the way, for the rule-level checks."""
    old = boost._MIN_SAVE_CHARS
    boost._MIN_SAVE_CHARS = 1
    try:
        return boost.compress(text)
    finally:
        boost._MIN_SAVE_CHARS = old


try:
    print("\n-- the switch --")
    ok("defaults to OFF", boost.enabled() is False, settings.get("boost"))
    ok("every key BOOST reads has a default",
       all(k in settings.all() for k in
           ("boost", "boost_compress", "boost_prefetch", "boost_directive", "boost_level")),
       [k for k in ("boost", "boost_compress", "boost_prefetch", "boost_directive",
                    "boost_level") if k not in settings.all()])

    print("\n-- the directive is constant --")
    d1, d2 = boost.directive(), boost.directive()
    ok("directive is not empty", len(d1) > 100, len(d1))
    ok("directive is byte-identical between calls", d1 == d2)
    ok("directive is cheap enough to be worth it", boost._tokens(d1) < 400, boost._tokens(d1))
    ok("directive names the local index", "graph" in d1.lower())
    settings.update({"boost_directive": False})
    ok("directive honours its own switch", boost.directive() == "")
    settings.update({"boost_directive": True})

    print("\n-- the compressor: what it removes --")
    ansi = "\x1b[31mERROR\x1b[0m build failed\n" + ("noise line padding padding\n" * 8)
    r = c(ansi)
    ok("ANSI escape sequences are stripped", "\x1b" not in r["text"], repr(r["text"][:40]))
    ok("the words survive ANSI stripping", "ERROR build failed" in r["text"])
    ok("ANSI stripping is recorded as a reason", r["reasons"].get("ansi", 0) > 0, r["reasons"])

    stack = "Traceback (most recent call last):\n" + ("  at frame () line 12\n" * 10)
    r = c(stack)
    ok("a run of identical lines is folded to one", r["text"].count("at frame () line 12") == 1,
       r["text"].count("at frame () line 12"))
    ok("the fold says how many it folded", "[BOOST folded 9 identical lines]" in r["text"], r["text"])
    ok("the fold is recorded", r["reasons"].get("repeated_lines") == 9, r["reasons"])

    two = "same\nsame\n" + ("other\n" * 8)
    r = c(two)
    ok("a pair is not a loop: two identical lines are kept", r["text"].count("same") == 2,
       r["text"][:60])

    shortrun = ("error: module not found in the build output\n" * 8) + ("err\n" * 3)
    r = c(shortrun)
    ok("a run too short to pay for its own marker is left alone",
       r["text"].rstrip("\n").endswith("err\nerr\nerr"), repr(r["text"][-90:]))
    ok("...while a run that does pay is still folded", "folded 7 identical" in r["text"], r["text"][:80])

    r = c("alpha\n\n\n\n\nbravo\n" + ("pad\n" * 8))
    ok("a run of blank lines collapses to one", "\n\n\n" not in r["text"], repr(r["text"][:40]))

    print("\n-- the compressor: what it must NOT remove --")
    fence_line = "const duplicate = true; // a long identical line\n"
    outside = "error: module not found in the build output\n"
    fenced = ("please review\n\n\n\n" + "```\n" + (fence_line * 8) + "```\n" + (outside * 8))
    r = c(fenced)
    ok("a fenced code block is copied through untouched",
       r["text"].count(fence_line.strip()) == 8, r["text"].count(fence_line.strip()))
    ok("...while the noise outside it still folds",
       "folded 7 identical lines" in r["text"], r["text"][-160:])

    prose = "Please fix the retry loop in `refreshWidget`.\n" + ("log line\n" * 10)
    r = c(prose)
    ok("the user's sentence is kept verbatim",
       "Please fix the retry loop in `refreshWidget`." in r["text"], r["text"][:60])
    ok("the identifier is not dropped", "refreshWidget" in r["text"])

    short = "fix the bug"
    r = boost.compress(short)
    ok("a short message is left exactly as typed", r["text"] == short and not r["changed"])

    small = "hello\n\n\n\nworld"        # a real change, but far under the floor
    r = boost.compress(small)
    ok("a rewrite under the floor is thrown away whole",
       r["text"] == small and r["changed"] is False, repr(r["text"]))

    print("\n-- safe vs hard --")
    blob = "data:image/png;base64," + ("A" * 2100)
    msg = "what is wrong with this?\n" + blob + "\n" + ("tail\n" * 8)
    settings.update({"boost_level": "safe"})
    r = c(msg)
    ok("safe leaves a base64 payload alone", "AAAA" in r["text"] and "elided" not in r["text"])
    settings.update({"boost_level": "hard"})
    r = c(msg)
    ok("hard elides it and says how much", "BOOST elided" in r["text"], r["text"][:120])
    ok("hard still keeps the question", "what is wrong with this?" in r["text"])
    settings.update({"boost_level": "safe"})

    print("\n-- which words are symbols --")
    syms = boost._symbols("why does `renderWorld` run twice after _apply_1m fires in the builder?")
    ok("a backticked name is a symbol", "renderWorld" in syms, syms)
    ok("snake_case is a symbol", "_apply_1m" in syms, syms)
    ok("English prose is not", not any(s.lower() in ("why", "does", "after", "fires", "the") for s in syms), syms)
    ok("at most a handful are asked about", len(syms) <= boost._MAX_PREFETCH_SYMBOLS, syms)
    ok("a plain sentence yields nothing",
       boost._symbols("please make it faster and cleaner") == [],
       boost._symbols("please make it faster and cleaner"))

    print("\n-- the local index, asked before the agent --")
    ok("no cwd, no prefetch", boost.prefetch("fix `renderWorld`", "")["block"] == "")
    ok("no graph, no prefetch",
       boost.prefetch("fix `renderWorld`", str(_TMP))["block"] == "")

    proj = _TMP / "proj"
    (proj / "graphify-out").mkdir(parents=True)
    (proj / "graphify-out" / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "n1", "label": "renderWorld", "source_file": "src/world.ts",
             "source_location": "L41"},
            {"id": "n2", "label": "main", "source_file": "src/main.ts", "source_location": "L7"},
        ],
        "links": [{"source": "n2", "target": "n1"}],
    }), encoding="utf-8")
    pre = boost.prefetch("why does `renderWorld` run twice?", str(proj))
    ok("the graph answers when it exists", bool(pre["block"]), pre)
    ok("the answer carries file:line", "src/world.ts:41" in pre["block"], pre["block"])
    ok("the answer names a caller", "main" in pre["block"], pre["block"])
    ok("the block says where it came from", "local index" in pre["block"])
    ok("a lookup counts as a lookup, not a saving", pre["lookups"] == 1, pre)

    print("\n-- the profile saves and restores --")
    # A PROFILE THAT CANNOT COST A CACHE RE-WRITE. Every key BOOST moves must be one that does NOT
    # ride the system prompt: a switch that changes the text a CLI is given makes the next send
    # re-send the whole conversation as a cache MISS, and on a long session that single line costs
    # more than every note the switch could remove. The two that would do that are recommended to
    # the user instead, with their price, so the economy stays their decision.
    settings.update({"cc_autolearn": True, "cc_graphify": False, "cc_phase_archive": False})
    st = boost.set_enabled(True)
    ok("switch is on", boost.enabled() is True and st["on"] is True)
    ok("the phase archive is switched on", settings.get("cc_phase_archive") is True)
    ok("the prompt snapshot is switched on", settings.get("cc_prompt_snapshot") is True)
    ok("no prompt switch is flipped behind the user's back",
       settings.get("cc_graphify") is False and settings.get("cc_autolearn") is True)
    ok("...and those two are recommended instead, each with what it buys and what it costs",
       {r["key"] for r in st["recommend"]} == {"cc_graphify", "cc_autolearn"}
       and all(r["why"] and r["cost"] for r in st["recommend"]), st["recommend"])
    ok("the profile is made ONLY of switches that cannot respawn a session",
       not (set(boost._PROFILE) & {"cc_graphify", "cc_autolearn", "cc_forge", "cc_live",
                                   "cc_ops", "cc_review", "cc_web_tools", "cc_phases",
                                   "cc_memory", "studio_tools_prompt", "boost",
                                   "boost_directive"}), sorted(boost._PROFILE))
    ok("what it replaced is written down",
       boost._read_saved().get("cc_phase_archive") is False, boost._read_saved())

    boost.set_enabled(False)
    ok("switch is off", boost.enabled() is False)
    ok("the user's own archive choice is put back", settings.get("cc_phase_archive") is False)
    ok("a switch BOOST never moved is left exactly as it was",
       settings.get("cc_graphify") is False and settings.get("cc_autolearn") is True)
    ok("the saved snapshot is cleared", boost._read_saved() == {}, boost._read_saved())

    boost.set_enabled(True)
    boost.set_enabled(True)
    ok("turning it on twice does not overwrite the saved values",
       boost._read_saved().get("cc_phase_archive") is False, boost._read_saved())
    boost.set_enabled(False)
    ok("...and the restore is still right", settings.get("cc_phase_archive") is False)

    # THE SETTINGS-PAGE PATH. The switchboard writes `boost` through the ordinary settings
    # endpoint, which saves the key BEFORE boost.set_enabled runs — so applying the profile only
    # on a false->true transition turned the button on and none of the economy behind it.
    settings.update({"boost": True, "cc_phase_archive": False})
    boost.set_enabled(True)
    ok("the profile applies even when the key was already written",
       settings.get("cc_phase_archive") is True, settings.get("cc_phase_archive"))
    boost.set_enabled(False)
    ok("...and the values from before that write are put back",
       settings.get("cc_phase_archive") is False)

    print("\n-- prepare: the one call the send path makes --")
    boost.set_enabled(False)
    p = boost.prepare("proj", "hello there")
    ok("off means nothing is touched", p["applied"] is False and p["message"] == "hello there")

    boost.set_enabled(True)
    boost.forget()
    noisy = "Please check this build.\n" + ("  at frame () line 12\n" * 12)
    p = boost.prepare("proj", noisy, engine="claude", cwd=str(proj))
    ok("on means the message is rewritten", p["applied"] is True and p["compressed"] is True, p)
    ok("the rewrite is shorter", len(p["message"]) < len(noisy), (len(p["message"]), len(noisy)))
    ok("the saving is counted", p["saved_tokens"] > 0, p["saved_tokens"])
    ok("the instruction is still there", "Please check this build." in p["message"])

    st = boost.stats()
    ok("the ledger banked the saving", st["saved_tokens"] == p["saved_tokens"], st)
    ok("the ledger counted the message", st["messages"] == 1, st)

    # A prefetch COSTS tokens. It must never appear in the saving.
    #
    # IT ASKS THE GRAPH, AND BOOST NO LONGER TURNS THE GRAPH ON BY ITSELF (that switch rides the
    # system prompt, so flipping it mid-conversation costs a cache re-write of the whole session —
    # see the profile test above). So the switch is set here, explicitly, and the reason BOOST did
    # nothing without it is visible on the button as `prefetch_ready`.
    settings.update({"cc_graphify": False})
    off = boost.prepare("proj", "why does `renderWorld` run twice?", engine="claude", cwd=str(proj))
    ok("graph switch off: no local-index block is added, and the button says why",
       "local index" not in off["message"] and boost.status()["prefetch_ready"] is False)
    settings.update({"cc_graphify": True})
    p2 = boost.prepare("proj", "why does `renderWorld` run twice?", engine="claude", cwd=str(proj))
    ok("a named symbol brings the local index answer with it",
       "local index" in p2["message"], p2["message"][:140])
    st2 = boost.stats()
    ok("a prefetch is banked as a lookup", st2["prefetched"] >= 1, st2)
    ok("a prefetch is NOT banked as tokens saved",
       st2["saved_tokens"] == st["saved_tokens"] + p2["saved_tokens"], (st2, st))

    boost.forget()
    ok("forget() starts the total again", boost.stats()["saved_tokens"] == 0)

    print("\n-- the status the button reads --")
    st = boost.status()
    ok("status says it is on", st["on"] is True)
    ok("status carries the running total", "saved_tokens" in st and "messages" in st)
    ok("status names what it moved", isinstance(st["changed"], dict) and "cc_phase_archive" in st["changed"])
    # ...and it names the switches it ACTUALLY moved, measured against what was there before,
    # not against what the profile wants (which would read "nothing changed" the moment it landed).
    boost.set_enabled(False)          # clear what an earlier section saved, so `from` is what we set
    settings.update({"cc_phase_archive": False, "cc_prompt_snapshot": True})
    boost.set_enabled(True)
    ch = boost.status()["changed"]
    ok("status names a switch BOOST really moved",
       ch["cc_phase_archive"]["changed"] is True and ch["cc_phase_archive"]["from"] is False,
       ch["cc_phase_archive"])
    ok("...and does not name one it left alone",
       ch["cc_prompt_snapshot"]["changed"] is False, ch["cc_prompt_snapshot"])
    ok("status reports the directive price", st["directive_tokens"] > 0, st["directive_tokens"])
    boost.set_enabled(False)

    print("\n-- every engine actually gets the note --")
    try:
        from asset_studio import cc_session, codex_app
        ok("nothing is added while BOOST is off",
           "BUDGET MODE" not in " ".join(cc_session._claude_stream_args(
               "claude", None, "default", "acceptEdits", False, "default",
               project_id="p", cwd=str(_TMP))))
        sig_off = cc_session._note_sig()
        boost.set_enabled(True)
        ok("the streaming session's system prompt carries it",
           "BUDGET MODE" in " ".join(cc_session._claude_stream_args(
               "claude", None, "default", "acceptEdits", False, "default",
               project_id="p", cwd=str(_TMP))))
        ok("...and so does the one-shot builder",
           "BUDGET MODE" in " ".join(cc_session._claude_args(
               "claude", "hi", None, "default", "acceptEdits", False, "default")))
        # BOOST'S NOTE MUST NEVER BE THE REASON A SESSION RESPAWNS. It used to be in `_note_sig`,
        # and a respawn re-sends the whole conversation under a new request prefix — a cache READ
        # at ~0.1x becomes a cache WRITE at ~1.25x, which on a long session costs more than the
        # note saves. The directive now rides the next natural respawn instead.
        ok("switching BOOST on does NOT force a live session to respawn",
           cc_session._note_sig() == sig_off)
        ok("a Codex thread is told too", "BUDGET MODE" in codex_app._developer_notes())
        boost.set_enabled(False)
        ok("...and it goes quiet again", "BUDGET MODE" not in codex_app._developer_notes())
    except ImportError as e:
        print("  SKIP  the engine wiring: %s" % e)

    print("\n-- the routes the button calls --")
    try:
        from asset_studio.routers import settings as R
        ok("PUT /api/boost switches it on and applies the profile",
           R.set_boost(R.BoostBody(on=True))["on"] is True and settings.get("cc_phase_archive") is True)
        ok("GET /api/boost answers", R.boost_status()["on"] is True)
        ok("PUT /api/boost switches it off again", R.set_boost(R.BoostBody(on=False))["on"] is False)
        # The Settings-page path: the key is written FIRST, then the profile has to follow.
        R.update_settings(R.SettingsPatch(patch={"cc_phase_archive": False}))
        R.update_settings(R.SettingsPatch(patch={"boost": True}))
        ok("PUT /api/settings {boost} applies the profile too",
           boost.enabled() is True and settings.get("cc_phase_archive") is True,
           {k: settings.get(k) for k in ("boost", "cc_phase_archive")})
        R.set_boost(R.BoostBody(on=False))
        pv = R.boost_preview(R.BoostPreview(text="review this\n" + "  at frame () line 12\n" * 12))
        ok("POST /api/boost/preview compresses without sending", pv["saved_tokens"] > 0, pv)
        ok("...and a preview does not need BOOST to be on", boost.enabled() is False)
        R.boost_forget()
        ok("POST /api/boost/forget resets the total", boost.stats()["saved_tokens"] == 0)
    except ImportError as e:
        print("  SKIP  the routes: fastapi is not installed here (%s)" % e)

finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAILED: %s" % f)
sys.exit(1 if fails else 0)

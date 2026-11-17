"""D6 (per-conversation context window) and D7 (warn before an expensive respawn).

No vendor requests, no real session spawns, no user-data writes. Run:
    python -m unittest window_respawn_test -v
"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_data = tempfile.TemporaryDirectory(prefix="studio-window-test-")
os.environ["ASSET_STUDIO_DATA"] = _data.name

from asset_studio import cc_session as cc, mission, pricing, subagents  # noqa: E402


class _FakeLive:
    """Only the fields `_respawn_warning` reads, so the cost maths can be tested on its own."""

    def __init__(self, ctx=800_000, age=10.0, ttl=3600.0):
        self.ctx_tokens = ctx
        self.cache_ttl = ttl
        self.last_api = time.time() - age


class WindowTests(unittest.TestCase):
    """D6: the 1M choice belongs to the conversation, and is resolved before the model is built."""

    def test_explicit_choice_beats_the_global_setting(self):
        with patch.object(cc.settings, "get", return_value=True):
            self.assertEqual(cc._apply_1m("claude-opus-5-5", False), "claude-opus-5-5")
        with patch.object(cc.settings, "get", return_value=False):
            self.assertEqual(cc._apply_1m("claude-opus-5-5", True), "claude-opus-5-5[1m]")

    def test_no_choice_falls_back_to_the_setting(self):
        for on in (True, False):
            with patch.object(cc.settings, "get", return_value=on):
                self.assertEqual(cc._apply_1m("claude-opus-5-5"),
                                 "claude-opus-5-5[1m]" if on else "claude-opus-5-5")

    def test_env_gate_follows_the_conversation_not_the_switch(self):
        """The env is read at launch; a 1M conversation must not be downgraded by a later flip."""
        with patch.object(cc.settings, "get", return_value=True):
            self.assertEqual(cc._claude_context_env(False),
                             {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"})
        with patch.object(cc.settings, "get", return_value=False):
            self.assertEqual(cc._claude_context_env(True), {})

    def test_the_switch_is_not_a_session_signature_any_more(self):
        """The 1M slot now carries the CONVERSATION's window, so the global switch cannot move it."""
        with patch.object(cc.settings, "get", return_value=True):
            before = cc._note_sig(True)
        with patch.object(cc.settings, "get",
                          side_effect=lambda k, d=True: False if k == "cc_1m" else True):
            self.assertEqual(before, cc._note_sig(True))
        # a conversation that chose 200k holds that too
        self.assertEqual(cc._note_sig(False), cc._note_sig(False))
        # AND THE LENGTH IS PART OF THE CONTRACT — an inherited session's stored signature is
        # compared positionally, so a different-sized tuple respawns every live session on upgrade.
        self.assertEqual(len(cc._note_sig(True)), 13)
        self.assertEqual(len(cc._note_sig(True)), len(cc._note_sig(False)))

    def test_a_conversation_keeps_its_window_across_a_global_flip(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_window("p", "s", False)
            with patch.object(cc.settings, "get", return_value=True):
                self.assertFalse(cc._resolve_window("p", "s", False))
            # a NEW conversation, though, takes the current default
            with patch.object(cc.settings, "get", return_value=True):
                self.assertTrue(cc._resolve_window("p", "", True))


class RespawnWarningTests(unittest.TestCase):
    """D7: report the EXTRA cost of the cache re-write, and only when it is real and avoidable."""

    def test_warm_large_cache_reports_the_difference_not_the_whole_context(self):
        w = cc._respawn_warning(_FakeLive(), "claude-opus-5-5", ["model"])
        self.assertIsNotNone(w)
        self.assertEqual(w["tokens"], 800_000)
        self.assertEqual(w["reasons"], ["model"])
        want = (pricing.cost("claude-opus-5-5", {"cache_write_1h": 800_000})
                - pricing.cost("claude-opus-5-5", {"cache_read": 800_000}))
        self.assertAlmostEqual(w["cost_usd"], round(want, 2), places=2)
        self.assertLess(w["cost_usd"], 10.0)      # NOT the whole context at the write price
        self.assertIn("$", w["message"])

    def test_expired_cache_is_silent(self):
        """The next request would write the conversation anyway — a respawn costs nothing extra."""
        self.assertIsNone(cc._respawn_warning(_FakeLive(age=4000.0), "claude-opus-5-5", ["model"]))

    def test_a_five_minute_ttl_is_honoured(self):
        live = _FakeLive(age=400.0, ttl=0.0)      # no 1-hour write seen -> 5-minute default
        self.assertIsNone(cc._respawn_warning(live, "claude-opus-5-5", ["model"]))
        live.last_api = time.time() - 100.0       # still inside 5 minutes: warn
        self.assertIsNotNone(cc._respawn_warning(live, "claude-opus-5-5", ["model"]))

    def test_no_request_yet_is_silent(self):
        self.assertIsNone(cc._respawn_warning(_FakeLive(ctx=0), "claude-opus-5-5", []))

    def test_small_context_is_not_worth_interrupting_for(self):
        self.assertIsNone(cc._respawn_warning(_FakeLive(ctx=1_000), "claude-opus-5-5", ["style"]))

    def test_unknown_model_is_silent_rather_than_wrong(self):
        """No rate card means no honest number. Fail quiet: never invent a cost."""
        self.assertIsNone(cc._respawn_warning(_FakeLive(), "not-a-model-at-all", ["notes"]))

    def test_the_ttl_decides_the_write_rate(self):
        """A 1-hour write bills at cw1h (~2x cw5m), so the number must follow the session's TTL.

        Pricing every session as a 5-minute one understated exactly the long conversations this
        warning exists for (found by review, 2026-11-16). The `[1m]` suffix must not hide the rate
        either — the tier is looked up from the model id with the selector stripped."""
        one_h = cc._respawn_warning(_FakeLive(ctx=800_000, ttl=3600.0), "claude-opus-5-5[1m]", ["model"])
        five = cc._respawn_warning(_FakeLive(ctx=800_000, ttl=0.0), "claude-opus-5-5[1m]", ["model"])
        self.assertAlmostEqual(one_h["cost_usd"], 6.24, places=2)   # 800k x $8/M, less the read
        self.assertAlmostEqual(five["cost_usd"], 3.84, places=2)    # 800k x $5/M, less the read
        self.assertGreater(one_h["cost_usd"], five["cost_usd"])

    def test_a_one_hour_session_at_150k_is_not_suppressed(self):
        """The reviewer's case: 150k on a 1-hour cache is $1.17 — over the threshold, so it must ask.

        At the 5-minute rate the same conversation is $0.72 and stays silent, which is how the
        threshold used to hide a real cost."""
        w = cc._respawn_warning(_FakeLive(ctx=150_000, ttl=3600.0), "claude-opus-5-5", ["notes"])
        self.assertIsNotNone(w)
        self.assertAlmostEqual(w["cost_usd"], 1.17, places=2)
        self.assertIsNone(cc._respawn_warning(_FakeLive(ctx=150_000, ttl=0.0),
                                              "claude-opus-5-5", ["notes"]))


class MeterWindowTests(unittest.TestCase):
    """D6 coherence: the fill meter must use the CONVERSATION's window, not the global default.

    Otherwise a 200k conversation is drawn against a 1M window as soon as the switch is flipped
    for the next task, and reads as four times emptier than it is.
    """

    @staticmethod
    def _switch(on: bool):
        """Patch ONLY cc_1m — a blanket True would also answer `context_window` with 1."""
        return patch.object(mission.settings, "get",
                            side_effect=lambda k, d=None: on if k == "cc_1m" else d)

    def test_the_conversation_overrides_the_global(self):
        with self._switch(True):
            self.assertEqual(mission.model_window("claude-opus-5-5"), 1_000_000)
            self.assertEqual(mission.model_window("claude-opus-5-5", 0, False), 200_000)
        with self._switch(False):
            self.assertEqual(mission.model_window("claude-opus-5-5"), 200_000)
            self.assertEqual(mission.model_window("claude-opus-5-5", 0, True), 1_000_000)

    def test_evidence_still_beats_a_recorded_choice(self):
        """A turn that already billed >200k proves the big window whatever the record says."""
        self.assertEqual(mission.model_window("claude-opus-5-5", 400_000, False), 1_000_000)

    def test_the_window_note_follows_the_conversation(self):
        """The note that tells the MODEL its own window states the same number as the meter.

        That is the invariant `_context_window_note` was written to protect, and before D6 it held
        only because both read the global switch."""
        with self._switch(False):
            # the switch is off, but THIS conversation chose 1M — it must be told 1M
            self.assertIn("1,000,000", cc._context_window_note("claude-opus-5-5", True))
            self.assertEqual(cc._context_window_note("claude-opus-5-5", False), "")
        with self._switch(True):
            self.assertIn("1,000,000", cc._context_window_note("claude-opus-5-5", True))
            self.assertEqual(cc._context_window_note("claude-opus-5-5", False), "")

    def test_a_pending_launch_choice_is_claimed_once(self):
        """A terminal / one-shot launch has no conversation id to record against yet (D6).

        Its choice is parked under the project and claimed the first time that conversation is
        seen, so a later global flip cannot silently re-window it. Writing the record clears the
        pending slot, so it can never be claimed by a second conversation."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_pending_window("p", False)
            self.assertIs(mission.pending_window("p"), False)
            with patch.object(cc.settings, "get", return_value=True):
                # the conversation with no record claims the parked choice, not the global
                self.assertFalse(cc._resolve_window("p", "s7", False))
            self.assertIs(mission.window_for("p", "s7"), False)
            self.assertIsNone(mission.pending_window("p"))          # claimed, so cleared
            with patch.object(cc.settings, "get", return_value=True):
                self.assertTrue(cc._resolve_window("p", "s8", False))  # a later chat gets the global

    def test_an_identified_launch_clears_a_stale_pending_slot(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_pending_window("p", True)
            mission.remember_window("p", "s1", True)     # the streaming path identifies the chat
            self.assertIsNone(mission.pending_window("p"))

    def test_two_conversations_cannot_both_claim_the_pending_window(self):
        """The claim is ATOMIC — read, write and clear happen under ONE lock.

        Done as read-then-remember, two sends into two different conversations could both see the
        parked value before either wrote it and both store it (found by review, 2026-11-16)."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_pending_window("p", True)
            got = []
            barrier = threading.Barrier(2)

            def claim(sid):
                barrier.wait()                       # both threads hit the claim at once
                got.append(mission.claim_pending_window("p", sid))

            ts = [threading.Thread(target=claim, args=(s,)) for s in ("a", "b")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(sorted(x is True for x in got), [False, True])   # exactly one
            self.assertIsNone(mission.pending_window("p"))
            self.assertEqual(sorted(bool(mission.window_for("p", s)) for s in ("a", "b")),
                             [False, True])                                  # exactly one stored

    def test_window_for_session_finds_it_whichever_project(self):
        """A subagent row holds a transcript PATH, not a project — so the lookup goes by session."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_window("p1", "s1", True)
            mission.remember_window("p2", "s2", False)
            self.assertIs(mission.window_for_session("s1"), True)
            self.assertIs(mission.window_for_session("s2"), False)
            self.assertIsNone(mission.window_for_session("never-seen"))
            self.assertIsNone(mission.window_for_session(""))

    def test_a_cached_ledger_row_still_reports_the_conversation_window(self):
        """`ctx_max` is recomputed on every read, never taken from the cached scan (D6).

        The ledger cache is per-transcript and written to DISK, so a remembered window went stale
        and stayed stale across restarts — a parent record of 200k still drew a 1M subagent card
        (found by review, 2026-11-16)."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_window("p", "sess", False)
            p = Path(d) / "agent.jsonl"
            p.write_text("{}", encoding="utf-8")
            # the CACHED row carries a window computed when the switch was on
            cached = {"model": "claude-opus-5-5", "ctx_max": 1_000_000, "usage": {}, "stats": {}}
            with patch.object(subagents, "_ledger_read", return_value=dict(cached)), \
                 patch.object(subagents, "_session_of", return_value="sess"), \
                 patch.object(mission.settings, "get",
                              side_effect=lambda k, d=None: True if k == "cc_1m" else d):
                out = subagents._ledger(p)
                self.assertEqual(out["ctx_max"], 200_000)     # the conversation's record wins
                # ...and again through the DISK cache, which is what survives a restart
                subagents._ledger_cache.clear()
                self.assertEqual(subagents._ledger(p)["ctx_max"], 200_000)

    def test_a_recorded_conversation_clears_a_parked_choice(self):
        """A named conversation supersedes a parked guess, so the slot cannot linger (D6).

        Without this the slot outlived even an identified launch: park 200k for a fresh terminal
        chat, resume a recorded 1M chat, and the 200k was still there for the next unrecorded
        conversation to claim (found by review, 2026-11-16)."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_window("p", "known", True)
            mission.remember_pending_window("p", False)
            with patch.object(cc.settings, "get", return_value=True):
                self.assertTrue(cc._resolve_window("p", "known", False))   # its own record wins
            self.assertIsNone(mission.pending_window("p"))                 # ...and clears the guess
            with patch.object(cc.settings, "get", return_value=True):
                # a later conversation with no record gets the GLOBAL, not the stale guess
                self.assertTrue(cc._resolve_window("p", "other", False))

    def test_an_alt_engine_one_shot_send_does_not_crash_after_launch(self):
        """The window bookkeeping must be readable on the ONE-SHOT path for every engine.

        `fresh` was bound inside the native-Claude branch only, so a Qwen/Kimi one-shot with
        streaming off raised UnboundLocalError AFTER the process had been spawned and registered —
        a turn that had already launched and then threw (found by review, 2026-11-16)."""
        from asset_studio import keychain, spend
        proc = SimpleNamespace(pid=4321, poll=lambda: 0)

        def setting(k, d=None):
            return False if k == "cc_streaming" else d

        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}), \
                patch.object(cc.settings, "get", side_effect=setting), \
                patch.object(spend, "over_cap", return_value=""), \
                patch.object(keychain, "has_key", return_value=True), \
                patch.object(cc, "_snapshot_before_turn"), \
                patch.object(cc, "_resolve", return_value=(d, None)), \
                patch.object(cc, "_resolve_node_shim", return_value=None), \
                patch.object(cc.subprocess, "Popen", return_value=proc):
            r = cc.send("proj", "hello", agent="qwen", new_session=True)
        self.assertTrue(r.get("ok"), r)            # launched, not an UnboundLocalError
        self.assertEqual(r.get("pid"), 4321)

    def test_the_window_follows_the_conversation_actually_resumed(self):
        """With no session named, the LIVE process's conversation wins over `newest_session`.

        Otherwise the window resolved for one chat is launched on another and then written over
        that chat's own record — no parked value or concurrency needed (found by review,
        2026-11-16)."""
        from asset_studio import spend
        proc = SimpleNamespace(pid=99, poll=lambda: None)   # None = still running
        live = cc._Live("p", proc, None, ("exe",), "A")     # the conversation really running
        live.alive = True

        def setting(k, d=None):
            return False if k == "cc_streaming" else d

        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}), \
                patch.object(cc.settings, "get", side_effect=setting), \
                patch.object(spend, "over_cap", return_value=""), \
                patch.object(cc, "_snapshot_before_turn"), \
                patch.object(cc, "_resolve", return_value=(d, "B")), \
                patch.object(cc, "_resolve_node_shim", return_value=None), \
                patch.object(cc.subprocess, "Popen", return_value=proc), \
                patch.dict(cc._live, {"p": live}):
            cc.send("p", "hello")                 # no session named; newest is B, live is A
        self.assertEqual(mission._load_active().get("p"), "A")

    def test_a_record_is_write_once(self):
        """A conversation's window, once recorded, is never changed (D6).

        This is what makes a wrong LAUNCH a one-turn cost instead of permanent corruption: a stale
        resolution for one chat applied to another used to rewrite the other's own record
        (found by review, 2026-11-16)."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            mission.remember_window("p", "s", True)
            mission.remember_window("p", "s", False)          # ignored
            self.assertIs(mission.window_for("p", "s"), True)
            # ...and a parked guess cannot be written over a record either
            mission.remember_pending_window("p", False)
            self.assertIs(mission.claim_pending_window("p", "s"), True)
            self.assertIs(mission.window_for("p", "s"), True)
            self.assertIsNone(mission.pending_window("p"))

    def test_write_once_holds_under_concurrency(self):
        """The absence test and the insert are ONE critical section (D6).

        Checking outside the lock let a writer that had already passed the check overwrite a first
        record that landed in between — `True` became `False` (found by review, 2026-11-16)."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            barrier = threading.Barrier(2)

            def write(v):
                barrier.wait()                        # both arrive at the write together
                mission.remember_window("p", "s", v)

            ts = [threading.Thread(target=write, args=(v,)) for v in (True, False)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            first = mission.window_for("p", "s")
            self.assertIsInstance(first, bool)
            mission.remember_window("p", "s", not first)     # the loser cannot move it
            self.assertIs(mission.window_for("p", "s"), first)

    def test_remember_window_reports_the_value_that_landed(self):
        """A resolver that LOSES the write-once race must be told, and launch on the winner.

        Returning the caller's own value made a launch contradict the record: the record said 1M
        while the launch set CLAUDE_CODE_DISABLE_1M_CONTEXT (found by review, 2026-11-16)."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            self.assertIs(mission.remember_window("p", "s", True), True)
            self.assertIs(mission.remember_window("p", "s", False), True)    # the loser is told
            self.assertIs(mission.window_for("p", "s"), True)
            self.assertIs(mission.remember_window("p", "s2", False), False)  # a new chat still writes

    def test_conversation_window_reports_no_opinion_rather_than_false(self):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}), \
                patch.object(mission, "active_session_id", return_value="s2"):
            self.assertIsNone(mission.conversation_window("p"))    # nothing recorded yet
            mission.remember_window("p", "s2", True)
            self.assertIs(mission.conversation_window("p"), True)
            self.assertIsNone(mission.conversation_window("another-project"))


if __name__ == "__main__":
    unittest.main()

"""Subagents get one line about the engine, unless Settings asks for the full engine notes.

Measured on the STUDIO session: every subagent paid 13,149 chars of notes (a subagent's prompt is
not shared with the parent's cache), ~11k of them the engine notes (live link, forge, navigate,
scene edit). With cc_engine_subagents off (the default) a subagent gets a ~400-char pointer to
GET /api/settings/engine-notes instead, and reads the notes there when its task needs the engine.

The real settings are never written: `settings` is replaced by a proxy for the test.
Run: python subagent_notes_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import cc_session   # noqa: E402
from asset_studio.routers import settings as settings_router   # noqa: E402

passed = failed = 0


def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL", label, extra)


class Over:
    """The real settings, with a few keys answered differently. Nothing is saved."""
    def __init__(self, real, **over):
        self.real, self.over = real, over

    def get(self, k, d=None):
        return self.over[k] if k in self.over else self.real.get(k, d)

    def __getattr__(self, name):
        return getattr(self.real, name)


def sub_prompt(args: list) -> str:
    for i, a in enumerate(args):
        if a == "--append-subagent-system-prompt":
            return args[i + 1]
        if a == "--append-subagent-system-prompt-file":
            return Path(args[i + 1]).read_text(encoding="utf-8")
    return ""


CWD = str(Path(__file__).resolve().parents[1])     # the Studio folder: it has pages to open
real = cc_session.settings
try:
    # The engine on, as on this PC; the two cases differ only in the new switch.
    base = dict(cc_live=True, cc_forge=True, cc_scene_edit=True)
    engine = cc_session._engine_subagent_notes(CWD)
    if not engine:
        print("SKIP: no engine notes here (no browser, or the engine switches are off on this PC)")
    else:
        cc_session.settings = Over(real, cc_engine_subagents=False, **base)
        args = cc_session._claude_stream_args("claude", None, "claude-opus-5-5", "default", False, "high", "", CWD)
        sp = sub_prompt(args)
        check("off: a subagent gets the one-line pointer", "STUDIO ENGINE:" in sp and "/api/settings/engine-notes" in sp, sp[:200])
        check("off: ...and none of the engine notes", all(n[:40] not in sp for n in engine), [n[:30] for n in engine])
        check("off: the browser rule is still there (a safety rule)", "SHARED BROWSER" in sp)
        pointer = cc_session._engine_pointer_note(CWD)
        check("the pointer is small (under 600 chars) against %d chars of notes" % sum(map(len, engine)),
              len(pointer) < 600 and sum(map(len, engine)) > 5 * len(pointer), len(pointer))
        main_notes = next((args[i + 1] for i, a in enumerate(args) if a == "--append-system-prompt"), "")
        check("the MAIN session still gets its full notes", any(n[:40] in main_notes for n in engine))

        cc_session.settings = Over(real, cc_engine_subagents=True, **base)
        sp2 = sub_prompt(cc_session._claude_stream_args("claude", None, "claude-opus-5-5", "default", False, "high", "", CWD))
        check("on: every subagent gets the full engine notes again", all(n[:40] in sp2 for n in engine))
        check("on: ...and not the pointer", "STUDIO ENGINE:" not in sp2)

        cc_session.settings = Over(real, cc_engine_subagents=False, **base)
        body = settings_router.engine_notes(CWD).body.decode("utf-8")
        check("the endpoint serves the full notes the pointer names", all(n[:40] in body for n in engine), body[:120])
    # With every engine switch off there is nothing to point at, and nothing is sent.
    cc_session.settings = Over(real, cc_live=False, cc_forge=False, cc_ops=False, cc_scene_edit=False,
                               cc_navigate=False, cc_animate=False, cc_debugger=False, cc_vertedit=False,
                               cc_code_tools=False, cc_engine_subagents=False)
    sp3 = sub_prompt(cc_session._claude_stream_args("claude", None, "claude-opus-5-5", "default", False, "high", "", CWD))
    check("engine off: no pointer either", "STUDIO ENGINE:" not in sp3, sp3[:200])
    body = settings_router.engine_notes(CWD).body.decode("utf-8")
    check("engine off: the endpoint says so", body.startswith("No engine notes"), body[:80])
finally:
    cc_session.settings = real

print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)

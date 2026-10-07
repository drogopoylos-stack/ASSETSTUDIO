"""Does every claude launch fit the Windows command line — and does a launch that cannot start
leave the working session alone?

Windows hands a new process ONE command-line string of at most 32,767 characters, and every
agent note rides in it. On 2026-09-23 two new notes took it to 34,210 characters, and every
workspace that needed a new process got "[WinError 206] The filename or extension is too long":
nothing could be sent there at all. Worse, the old process had already been stopped before the
new one failed, so the workspace was left with no process.

The fix moves prompt text into files only when the line would not fit — the subagent notes
first, because the CLI marks a session whose MAIN prompt came from a file as fork-restricted
(`/fork` in the terminal then refuses). This file holds all of that in place.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cs      # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


real_dir = cs._PROMPT_DIR
cs._PROMPT_DIR = Path(tempfile.mkdtemp(prefix="cmdfit-"))
try:
    exe = r"C:\users\x\.local\bin\claude.EXE"
    sub = "SUB " + "s" * 12_000 + "\nline two\n"
    main = "MAIN " + "m" * 21_000 + "\n\nünïcode — dash\n"

    print("A line that fits is left exactly as it was")
    small = [exe, "-p", "--append-subagent-system-prompt", "short", "--append-system-prompt", "short"]
    check("unchanged, the same list", cs._fit_command_line(small) == small)

    if os.name == "nt":
        print("\nOver the ceiling, the SUBAGENT notes move first")
        args = [exe, "-p", "--append-subagent-system-prompt", sub, "--append-system-prompt", main]
        check("the raw line is over the ceiling", cs._cmdline_len(args) > cs._CMDLINE_MAX, cs._cmdline_len(args))
        fit = cs._fit_command_line(args)
        check("the fitted line is under it", cs._cmdline_len(fit) <= cs._CMDLINE_MAX, cs._cmdline_len(fit))
        check("the subagent text went to a file", "--append-subagent-system-prompt-file" in fit, fit[:4])
        check("the MAIN text stayed on the line, so /fork still works",
              "--append-system-prompt" in fit and "--append-system-prompt-file" not in fit)
        f = Path(fit[fit.index("--append-subagent-system-prompt-file") + 1])
        check("the file holds the text byte for byte (no CRLF, no BOM)", f.read_bytes() == sub.encode("utf-8"))
        check("the input list was not edited", args[2] == "--append-subagent-system-prompt")

        print("\nStill over, and the main notes move too")
        huge = [exe, "-p", "--append-subagent-system-prompt", sub, "--append-system-prompt", main * 2]
        fit2 = cs._fit_command_line(huge)
        check("both went to files", "--append-subagent-system-prompt-file" in fit2
              and "--append-system-prompt-file" in fit2, [a for a in fit2 if a.startswith("--")])
        check("and the line fits", cs._cmdline_len(fit2) <= cs._CMDLINE_MAX, cs._cmdline_len(fit2))
        g = Path(fit2[fit2.index("--append-system-prompt-file") + 1])
        check("the main file is exact, unicode included", g.read_bytes() == (main * 2).encode("utf-8"))

        print("\nFiles are named by what they hold")
        check("the same text is the same file", cs._prompt_file(sub) == f)
        check("different text is a different file", cs._prompt_file(sub + "x") != f)

        print("\nA batch-file shim has cmd.exe's smaller ceiling")
        shim = [r"C:\npm\claude.cmd", "-p", "--append-subagent-system-prompt", "s" * 5000,
                "--append-system-prompt", "m" * 5000]
        fit3 = cs._fit_command_line(shim)
        check("a 10k line through a .cmd is fitted", cs._cmdline_len(fit3) <= cs._CMDLINE_MAX_CMD, cs._cmdline_len(fit3))

        print("\nA launch that cannot start is refused BEFORE anything is stopped")
        check("a fitted line has no problem", cs._launch_problem(fit) == "")
        bad = [exe, "-p", "x" * 40_000]
        msg = cs._launch_problem(bad)
        check("an impossible line is named, with its length", "40" in msg and "Settings" in msg, msg)

        # Drive the real send path with a live session whose settings changed (so it must be
        # replaced), and a replacement that cannot start. The old process must survive.
        killed, spawned = [], []

        class _Proc:
            pid = 999999

            def poll(self):
                return None

        class _Fake:
            alive = True
            proc = _Proc()
            sig = ("old settings",)
            session_id = "s1"

        fake = _Fake()
        saved = {k: getattr(cs, k) for k in ("_claude_stream_args", "_kill_live", "_spawn_live",
                                              "_stream_busy", "_has_recent_agent_activity",
                                              "_open_background", "_stop_shared")}
        cs._claude_stream_args = lambda *a, **k: bad
        cs._kill_live = lambda live: killed.append(live)
        cs._spawn_live = lambda *a, **k: spawned.append(a)
        cs._stream_busy = lambda live: False
        cs._has_recent_agent_activity = lambda pid: False
        cs._open_background = lambda pid: []
        cs._stop_shared = lambda pid: None
        pid = "cmdfit-test-project"
        cs._live[pid] = fake
        try:
            r = cs._send_streaming(pid, "hello", tempfile.gettempdir(), "default", "acceptEdits", False,
                                   "default", exe, False, "", None)
        finally:
            for k, v in saved.items():
                setattr(cs, k, v)
            still = cs._live.pop(pid, None)
        check("the send is refused with the reason", r.get("ok") is False and "command line" in r.get("error", ""), r)
        check("the old process was NOT stopped", killed == [], killed)
        check("...it is still the project's session", still is fake)
        check("...and nothing was launched", spawned == [])
    else:
        print("\n(not Windows: the ceiling checks do not apply)")
finally:
    import shutil
    shutil.rmtree(cs._PROMPT_DIR, ignore_errors=True)
    cs._PROMPT_DIR = real_dir

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

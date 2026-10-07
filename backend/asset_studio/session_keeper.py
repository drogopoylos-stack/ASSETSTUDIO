"""Hold a Claude session's stdin open, so the session outlives the backend.

MEASURED, not assumed: a `claude -p --input-format stream-json` process EXITS ONE SECOND AFTER
ITS STDIN CLOSES. The backend owns the write end of that pipe, so when the backend stops — a
restart, a crash, the Electron supervisor taking the port back — Windows closes the handle, the
CLI sees EOF, and it is gone before the replacement backend has finished starting. Every
background agent lives inside that process, so they die with it, with no result recorded.

No guard inside the backend can prevent that; the process is already gone. The pipe has to be
held by something that is not the backend. That is this file, and it does nothing else:

    keeper stdout ──> the CLI's stdin        (the pipe, held open for the session's whole life)
    inbox/*.msg   ──> keeper                 (how any backend, present or future, sends a message)

The keeper is deliberately tiny and deliberately dull. It imports four stdlib modules, it never
parses what it forwards, and every loop is wrapped so that no error can end it — because if it
ends, stdin closes and the session dies. It stops when the session it serves stops, and not
before.

    python -m asset_studio.session_keeper <inbox> <pidfile> [<backend_beat> <session_log>]

A message is a file: `<inbox>/<ns>-<n>.msg`, holding the exact bytes to write, delivered oldest
first and deleted once written. The delete IS the receipt — a sender that watches the file
vanish knows the CLI has the bytes.

The last two arguments are the one way this ever gives up. Holding stdin open means a session no
longer ends by itself, so a closed app would leave one Node process per project running for ever.
If NO backend has left a heartbeat for half an hour AND the session has written nothing for half
an hour, there is no workspace left to report to and nothing in flight to report — so the keeper
lets go, and the session ends a second later. Either sign of life resets it.
"""
from __future__ import annotations

import os
import sys
import time

POLL = 0.05             # inbox scan interval; an empty scandir is a few microseconds
PIDFILE_WAIT = 60.0     # how long to wait for the backend to name the process we serve
BEAT = 5.0              # how often to touch the heartbeat file
GRACE = 1800.0          # no backend AND no output for this long = let the session go


def _alive(pid: int) -> bool:
    """Is that pid still running? Native on Windows, signal 0 elsewhere. psutil is deliberately
    not imported: a missing or broken optional dependency must never be able to end a session."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259           # STILL_ACTIVE
            return False
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pidfile(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int((fh.read() or "0").strip() or 0)
    except Exception:
        return 0


def _age(path: str) -> float:
    """Seconds since that file was last written; a huge number if it is not there."""
    try:
        return max(0.0, time.time() - os.stat(path).st_mtime)
    except Exception:
        return 1e9


def main(argv: list) -> int:
    if len(argv) < 3:
        return 2
    inbox, pidfile = argv[1], argv[2]
    backend_beat = argv[3] if len(argv) > 3 else ""
    session_log = argv[4] if len(argv) > 4 else ""
    beat = os.path.join(inbox, "keeper.beat")
    out = sys.stdout.buffer

    # Wait for the backend to say which process we are serving. It spawns us FIRST, so that the
    # write end of the pipe is held before the CLI is ever started — if the CLI came first and we
    # failed to launch, it would see EOF and die immediately.
    child = 0
    deadline = time.time() + PIDFILE_WAIT
    while time.time() < deadline:
        child = _read_pidfile(pidfile)
        if child:
            break
        time.sleep(0.1)
    if not child:
        return 3

    started = time.time()
    last_beat = 0.0
    misses = 0                       # consecutive polls where the child looked gone
    while True:
        try:
            names = []
            try:
                with os.scandir(inbox) as it:
                    for e in it:
                        if e.name.endswith(".msg"):
                            names.append(e.name)
            except FileNotFoundError:
                # Somebody removed the inbox. While the session lives, put it back rather than
                # stopping: an inbox can be recreated, a closed stdin cannot. Once the session is
                # gone the removal was the backend tidying up, so leave it removed.
                if _alive(child):
                    try:
                        os.makedirs(inbox, exist_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass

            # Oldest first. The name starts with a nanosecond stamp, so a plain sort is the
            # order the messages were sent in.
            for name in sorted(names):
                p = os.path.join(inbox, name)
                try:
                    with open(p, "rb") as fh:
                        data = fh.read()
                except Exception:
                    continue
                if data:
                    try:
                        out.write(data)
                        out.flush()
                    except Exception:
                        # The pipe is gone, which means the CLI is gone. Nothing left to serve.
                        return 0
                try:
                    os.remove(p)          # the receipt: the sender watches for this
                except Exception:
                    pass

            now = time.time()
            ticked = now - last_beat > BEAT
            if ticked:
                last_beat = now
                try:
                    with open(beat, "w", encoding="utf-8") as fh:
                        fh.write("%d %d %.0f" % (os.getpid(), child, now))
                except Exception:
                    pass

            # Stop only when the session really has stopped. Two consecutive misses, because a
            # pid check that races a handle close should not be the thing that ends a session.
            if not _alive(child):
                misses += 1
                if misses >= 2:
                    return 0
            else:
                misses = 0

            # The one deliberate give-up: no backend anywhere, and this session has produced
            # nothing, for half an hour. All three conditions, and the third is not a formality —
            # without it a keeper started before any backend had ever written a heartbeat read
            # "no backend for ever" on its FIRST tick and let the session go a second after
            # starting it. "No backend for half an hour" cannot be true in the first half hour.
            # The other two are a pair because either alone is ordinary: a restart is seconds of
            # no beat, and a session thinking hard writes nothing for minutes. Together they mean
            # the app is closed and this process is only holding memory.
            if ticked and backend_beat and (now - started) > GRACE:
                if _age(backend_beat) > GRACE and (not session_log or _age(session_log) > GRACE):
                    return 0
        except Exception:
            # Never, under any circumstances, leave this loop by accident.
            pass
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(1)

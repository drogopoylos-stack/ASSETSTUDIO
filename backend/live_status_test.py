"""Does the rail call a workspace "working" when its subagents are the ones working?

A parent that hands a task to subagents goes quiet while it waits for them: no tokens, no stream
writes, nothing for the busy check to see. The reaper has always known this and refuses to reap a
session whose agent files are still being written. The INDICATOR did not, so the busiest
workspace on the machine went dark — and its agent count vanished at the same time, because the
count was only computed for a project the stream had already called busy.

Counting used to mean reading every parent transcript a project owns (523 MB on one real
workspace), which is why it was rationed. It is mtimes now, so every live session can be asked.

No real session is started here; `_live` is populated with stand-ins.
"""
import io
import sys

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cc        # noqa: E402
from asset_studio import subagents as sa         # noqa: E402

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


class _Live:
    """Enough of a live session for live_status(), with the stream idle or busy on demand."""
    def __init__(self, project_id, streaming):
        self.alive = True
        self.proc = _Proc()
        self.turn_active = streaming
        self.last_write = 1 if streaming else 0
        self.last_result = 0 if streaming else 1
        self.project_id = project_id


QUIET = "quiet-parent-project"
LOUD = "streaming-project"
IDLE = "genuinely-idle-project"
# Two agents on the quiet parent: one still writing, one dispatched in the background and quiet
# for ten minutes. The second is the case that used to disappear from the rail while the card
# under the chat still showed it -- so it must count here as much as the first does.
FAKE = {QUIET: 2, LOUD: 0, IDLE: 0}

real_working = sa.working
saved = dict(cc._live)
try:
    cc._live.clear()
    cc._live[QUIET] = _Live(QUIET, streaming=False)
    cc._live[LOUD] = _Live(LOUD, streaming=True)
    cc._live[IDLE] = _Live(IDLE, streaming=False)
    # Stub the SHARED definition, because that is what every screen now reads.
    sa.working = lambda pid, window=sa.OPEN_WINDOW: [
        {"agent_id": "a%d" % i, "moving": i == 0, "background": True}
        for i in range(FAKE.get(pid, 0))]

    st = cc.live_status()

    print("A quiet parent whose subagents are running")
    check("its stream really is idle", cc.is_sending(QUIET) is False)
    check("but the workspace reads as working", st["statuses"].get(QUIET) is True,
          st["statuses"].get(QUIET))
    check("and its agent count is shown", st["running_agents"].get(QUIET) == 2,
          st["running_agents"].get(QUIET))
    check("the quiet one is counted, not dropped", st["quiet_agents"].get(QUIET) == 1,
          st.get("quiet_agents"))

    print("\nA parent that is streaming, with no subagents")
    check("still reads as working", st["statuses"].get(LOUD) is True, st["statuses"].get(LOUD))
    check("and reports no fan-out", LOUD not in st["running_agents"], st["running_agents"])

    print("\nA session with nothing happening at all")
    check("reads as idle", st["statuses"].get(IDLE) is False, st["statuses"].get(IDLE))
    check("and is not counted", IDLE not in st["running_agents"], st["running_agents"])

    print("\nThe total is counted once, not once per key")
    # The map is keyed by BOTH the prefixed and the bare id so either lookup works, which makes
    # summing its values wrong. The total is counted where the prefix is known.
    check("total matches the agents actually running", st["running_agents_total"] == 2,
          st["running_agents_total"])
finally:
    sa.working = real_working
    cc._live.clear()
    cc._live.update(saved)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

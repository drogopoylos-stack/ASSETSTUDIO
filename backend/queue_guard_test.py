# -*- coding: utf-8 -*-
"""The two guards that keep a heavy local job from freezing the PC.

Why these exist, measured on this machine: one MiniMax H3 clip (the Video tab's local engine)
leaves ~13.8 GB of VRAM and ~30.8 GB of private working set behind it (`providers/comfy_common.py`).
The queue ran TWO jobs at once by design, and its only preflight checks were free DISK and the
monthly spend cap — neither of which knows what a local job costs. So the second job started on top
of the first, ComfyUI offloaded the resident model into host RAM, and the whole desktop stopped
answering: which is also what a "the assistant suddenly stopped" report turned out to look like.

Two things are checked here:
  * a LOCAL job is refused when free system RAM is under `min_free_ram_gb`
  * local jobs are SERIALIZED (one card, one job), while API jobs still overlap

Run:  backend/.venv/Scripts/python.exe backend/queue_guard_test.py
"""
import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

# The queue writes job rows through the Studio's sqlite db. Point it at a throwaway BEFORE the
# package is imported, or this test would touch the running app's own catalog.
os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="queue-guard-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from asset_studio.config import settings            # noqa: E402
# The MODULE, not the package attribute: `asset_studio.jobs.queue` is shadowed by the singleton
# instance `asset_studio.jobs.queue` re-exported from the package __init__.
import importlib                                    # noqa: E402
Q = importlib.import_module("asset_studio.jobs.queue")   # noqa: E402
from asset_studio.models import Job, JobStatus, ProviderKind, StageType  # noqa: E402
from asset_studio.providers import registry as reg  # noqa: E402
from asset_studio import system_stats               # noqa: E402

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


class _Provider:
    def __init__(self, kind):
        self.kind = kind
        self.id = "fake-" + kind.value


def _stats(total, used, free_disk=500.0):
    return types.SimpleNamespace(
        disk=types.SimpleNamespace(free_gb=free_disk),
        ram_total_gb=total, ram_used_gb=used, ram_percent=(used / total) * 100 if total else 0)


print("\n-- the memory floor --")
real_collect = system_stats.collect
q = Q.JobQueue(concurrency=2)
try:
    settings.update({"min_free_gb": 3.0, "min_free_ram_gb": 4.0, "monthly_spend_cap_usd": 0.0})

    system_stats.collect = lambda: _stats(64.0, 30.0)
    ok("plenty of RAM: a local job is allowed",
       q._preflight(_Provider(ProviderKind.local)) == "")

    system_stats.collect = lambda: _stats(64.0, 62.0)
    why = q._preflight(_Provider(ProviderKind.local))
    ok("low RAM: a LOCAL job is refused", "Low memory" in why, why)
    # THE INSTRUCTION MUST POINT AT SOMETHING THAT EXISTS. It said "Press Free memory" and there is
    # no control by that name — releasing cached models is a click on the RAM or VRAM reading in the
    # bottom bar. A dead end reads as the app being broken, which is worse than saying nothing.
    ok("...and the message says what to do about it",
       "RAM or VRAM reading in the bottom bar" in why, why)
    ok("...and points at the pane where the floor can be raised",
       "Settings > Cost > Limits" in why, why)
    ok("...and never names a control that does not exist", "Press Free memory" not in why, why)
    ok("...and it names the numbers it saw", "2.0 GB free of 64 GB" in why, why)

    system_stats.collect = lambda: _stats(64.0, 62.0)
    ok("a REMOTE job is not blocked by local memory",
       q._preflight(_Provider(ProviderKind.api)) == "")

    settings.update({"min_free_ram_gb": 0})
    ok("the floor can be switched off", q._preflight(_Provider(ProviderKind.local)) == "")
    settings.update({"min_free_ram_gb": 4.0})

    system_stats.collect = lambda: _stats(64.0, 30.0, free_disk=1.0)
    ok("the disk floor still wins when it is the one that is out",
       "Low disk" in q._preflight(_Provider(ProviderKind.local)))
finally:
    system_stats.collect = real_collect


print("\n-- the guards are settable from the UI --")
# THE GUARD AND THE CONTROL HAVE TO BE THE SAME SETTING. The refusal sentence told people to lower
# `min_free_ram_gb` in Settings while nothing in the UI wrote that key, so the instruction was a
# dead end. This exercises the exact route the Limits tab calls, then asks the queue whether it
# obeyed — the two ends of one wire.
try:
    from asset_studio.routers import settings as R
    system_stats.collect = lambda: _stats(64.0, 62.0)
    try:
        R.update_settings(R.SettingsPatch(patch={"min_free_ram_gb": 1.0}))
        ok("a lower floor saved from the UI reaches the queue",
           q._preflight(_Provider(ProviderKind.local)) == "",
           settings.get("min_free_ram_gb"))
        R.update_settings(R.SettingsPatch(patch={"min_free_ram_gb": 8.0}))
        ok("...and raising it again refuses the job",
           "Low memory" in q._preflight(_Provider(ProviderKind.local)))
        R.update_settings(R.SettingsPatch(patch={"serialize_local_jobs": False}))
        ok("the one-GPU-one-job switch is settable too",
           settings.get("serialize_local_jobs") is False, settings.get("serialize_local_jobs"))
        R.update_settings(R.SettingsPatch(patch={"serialize_local_jobs": True}))
        ok("...and it reads back on by default",
           settings.get("serialize_local_jobs") is True)
    finally:
        system_stats.collect = real_collect
except ImportError as e:
    print("  SKIP  the settings route: %s" % e)


print("\n-- one GPU, one job --")


async def _run(providers, serialize=True, jobs=4):
    """Push `providers` through the gate and report the peak number of jobs in flight."""
    settings.update({"serialize_local_jobs": serialize})
    q2 = Q.JobQueue(concurrency=2)
    await q2.start()
    active, peak = [], []

    async def fake_run(job):
        active.append(job.id)
        peak.append(len(active))
        await asyncio.sleep(0.05)          # long enough for an unguarded second job to overlap
        active.remove(job.id)

    q2._run_job = fake_run                 # shadow the real body: this test is about the gate
    q2._evt = lambda job, **_kw: None
    Q.db.save_job = lambda job: None
    Q.bus.publish = _async_noop

    async def one(i, kind):
        job = types.SimpleNamespace(id="j%d" % i, provider_id="fake-" + kind.value, step="queued")
        await q2._run_job_gated(job)

    try:
        await asyncio.gather(*(one(i, p) for i, p in enumerate(providers)))
    finally:
        await q2.stop()
        settings.update({"serialize_local_jobs": True})
    return max(peak or [0])


async def _async_noop(*_a, **_kw):
    return None


def _patch_providers(kind):
    """`_run_job_gated` resolves the provider through the registry, so that is what to stand in
    for: it is the one place the kind of the job is read."""
    reg.get_provider = lambda pid, _k=kind: _Provider(_k)


_local = [ProviderKind.local] * 4
_patch_providers(ProviderKind.local)
_peak = asyncio.run(_run(_local))
ok("four local jobs run ONE at a time", _peak == 1, "peak in flight = %s" % _peak)

_patch_providers(ProviderKind.api)
_peak_api = asyncio.run(_run([ProviderKind.api] * 4))
ok("remote jobs still overlap (the pool is still worth two workers)", _peak_api > 1, _peak_api)

_patch_providers(ProviderKind.local)
_peak_off = asyncio.run(_run(_local, serialize=False))
ok("the gate can be switched off", _peak_off > 1, _peak_off)


print("\n-- stop means stop, even while it waits for the card --")
# THE GATE MADE A WIDE WINDOW, and this closes it. `cancel()` finishes a QUEUED job on the spot —
# there is nothing yet to interrupt — and `_finish` DISCARDS the id from `self._cancel` as it writes
# the row. So the cancel set cannot be what remembers the decision; `job.status` is. Without the
# check, a waiter parked on the gate would wake up, spend the GPU on the job it was told to drop,
# and overwrite the canceled row with whatever the run ended as.


async def _stopped_while_waiting(status):
    """Run the REAL `_run_job` for a job in `status`; report whether the provider was resolved.

    `get_provider` is the first thing `_run_job` does AFTER the decision this test is about, so
    "was it called?" is exactly "did this job start?".
    """
    seen = []
    q4 = Q.JobQueue(concurrency=1)
    await q4.start()
    Q.db.save_job = lambda job: None
    Q.bus.publish = _async_noop
    Q.bus.publish_threadsafe = lambda *_a, **_k: None
    q4._evt = lambda job, **_kw: None
    reg.get_provider = lambda pid: (seen.append(pid), None)[1]
    job = types.SimpleNamespace(id="j-" + status.value, provider_id="fake-local", status=status,
                                step="queued", progress=0.0, error="", error_hint="", outputs=[])
    try:
        await q4._run_job(job)
    finally:
        await q4.stop()
    return seen, job


seen_canceled, canceled_job = asyncio.run(_stopped_while_waiting(JobStatus.canceled))
ok("a job canceled while it was waiting never starts", seen_canceled == [], seen_canceled)
ok("...and its canceled row is left exactly as it was",
   canceled_job.status == JobStatus.canceled, canceled_job.status)

seen_queued, _ = asyncio.run(_stopped_while_waiting(JobStatus.queued))
# The FIRST resolution is the one this checks: `_finish` looks the provider up a second time for
# `classify_error`, so the count is not the assertion — "did the job get past the decision?" is.
ok("...while a job that is still wanted does start",
   seen_queued[:1] == ["fake-local"], seen_queued)

# AND THE GUARD MUST NOT BLOCK AN ORDINARY JOB. `_run_job` now refuses anything that is not
# `queued`, so a job that arrives in any other state would be silently dropped and the whole
# generation side of the app would stop — the worst possible failure, and invisible until someone
# tried to render something. This pins the one fact that makes the check safe: a SUBMITTED job is
# built as `queued`, so a real submission walks straight through it.
real = Job(stage=StageType.image2d, provider_id="fake-local")
ok("a freshly submitted job is `queued`, so a real submission is never blocked",
   real.status == JobStatus.queued, real.status)

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAILED: %s" % f)
sys.exit(1 if fails else 0)

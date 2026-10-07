"""Agent tools — jobs, the test suites, code that can be checked, and the sidecar with nothing open."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import agent_tools
from ..config import settings

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _guard(key: str, what: str) -> dict:
    """Off means off. The same contract the forge keeps: with the switch down the agent is never
    told these exist, and the endpoint refuses rather than working anyway."""
    if not settings.get(key, False):
        return {"ok": False,
                "error": "%s is off. Turn it on in Settings → Studio engine." % what}
    return {}


# ---------------------------------------------------------------------------- jobs
@router.get("/job/{job_id}")
def job(job_id: str, log: bool = False):
    """How a job is going. The log is left out unless asked for."""
    return agent_tools.job(job_id, log=log)


@router.get("/jobs")
def jobs(limit: int = 20):
    return agent_tools.jobs(limit)


# ---------------------------------------------------------------------------- tests
@router.get("/suites")
def suites():
    """Every suite that can be run, and the one that is excluded because it takes minutes."""
    bad = _guard("cc_code_tools", "Code tools")
    return bad or agent_tools.suites()


class TestsBody(BaseModel):
    which: str = "all"
    timeout: float = 300.0
    # A job by default. The whole suite is minutes of esbuild and headless Chrome, and holding a
    # connection open for that is how a poll stacks up behind it.
    wait: bool = False


@router.post("/tests")
def tests(body: TestsBody):
    """Run one suite, one side, or everything. Counts and failing lines, never the whole log."""
    bad = _guard("cc_code_tools", "Code tools")
    if bad:
        return bad
    if body.wait:
        return agent_tools.run_tests(body.which, body.timeout)
    return agent_tools.tests_async(body.which, body.timeout)


# ---------------------------------------------------------------------------- code
@router.get("/sha")
def sha(path: str):
    """A file's fingerprint without its contents — "is this still what I read?" for no tokens."""
    bad = _guard("cc_code_tools", "Code tools")
    return bad or agent_tools.sha(path)


@router.get("/validate")
def validate(path: str):
    """Does it still hold together? Python by parse, TypeScript by the project's own tsc."""
    bad = _guard("cc_code_tools", "Code tools")
    return bad or agent_tools.validate(path)


class EditBody(BaseModel):
    path: str
    old: str
    new: str
    # Pass the sha you last read. The edit refuses if the file moved under you.
    expect_sha: str = ""
    check: bool = True


@router.post("/edit")
def edit(body: EditBody):
    """Replace one exact, unique span, and say at once whether the file still parses."""
    bad = _guard("cc_code_tools", "Code tools")
    return bad or agent_tools.edit(body.path, body.old, body.new, body.expect_sha, body.check)


# ---------------------------------------------------------------------------- the sidecar
@router.get("/sidecar")
def sidecar(path: str):
    """What a saved document holds, counted. Needs no browser and no build."""
    bad = _guard("cc_ops", "Mesh ops")
    return bad or agent_tools.sidecar_read(path)


class SidecarApplyBody(BaseModel):
    sidecar: str
    module: str
    export: str = ""
    args: list = []


@router.post("/sidecar/apply")
def sidecar_apply(body: SidecarApplyBody):
    """Build the asset in node and put the document on it: what bound, and what the code moved
    out from under. The check that matters after a parameter change, with nothing running."""
    bad = _guard("cc_ops", "Mesh ops")
    return bad or agent_tools.sidecar_apply(body.sidecar, body.module, body.export, body.args)

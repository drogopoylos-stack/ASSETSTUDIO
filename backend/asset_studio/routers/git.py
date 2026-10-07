"""Source control over HTTP.

Status is a GET. Everything that CHANGES something is a POST with an explicit body, so nothing
here can happen because a page polled. Push especially: it leaves this machine, and it only ever
runs because someone pressed the button that says so.

Every handler is sync — each shells out to git, and a push over a slow line takes seconds — so
FastAPI runs them in the threadpool and never on the event loop.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import gitops

router = APIRouter(prefix="/api/git", tags=["git"])


@router.get("/status")
def status(path: str = Query(...)):
    """Branch, what changed, how far ahead or behind, the remote, and who git thinks you are."""
    return gitops.status(path)


@router.get("/github")
def github():
    """Whether the GitHub CLI is installed and signed in, and as whom. Read-only."""
    return gitops.gh_account()


class CommitBody(BaseModel):
    path: str
    message: str
    all_changes: bool = True


@router.post("/commit")
def commit(body: CommitBody):
    return gitops.commit(body.path, body.message, body.all_changes)


class PathBody(BaseModel):
    path: str


@router.post("/push")
def push(body: PathBody):
    """Send this branch up. Never forced; a diverged branch is refused, not resolved."""
    return gitops.push(body.path)


@router.post("/pull")
def pull(body: PathBody):
    """Fast-forward only, and only with a clean tree."""
    return gitops.pull(body.path)


class CreateRepoBody(BaseModel):
    path: str
    name: str = ""
    private: bool = True
    # Separate from creating it, and off by default: making somewhere to push and actually
    # publishing your code are two decisions, and only one of them is hard to take back.
    push: bool = False


@router.post("/github/create")
def github_create(body: CreateRepoBody):
    return gitops.gh_create_repo(body.path, body.name, body.private, body.push)

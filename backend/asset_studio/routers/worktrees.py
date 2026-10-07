"""Worktrees over HTTP.

Every handler is sync, so FastAPI runs it in the threadpool: each one shells out to git, and a
`worktree add` on a large repo takes seconds. None of this may sit on the event loop.

Creating a worktree also REGISTERS it as a workspace root. That is the whole point — a registered
folder gets its own project id, and a project id is what gives it its own session, transcript,
model and agent. Without that step you would have made a folder, not a workspace.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import workspace, worktrees

router = APIRouter(prefix="/api/worktrees", tags=["worktrees"])


@router.get("/map")
def repo_map():
    """Every open root, with the git facts the rail needs to nest them.

    `repo` is the grouping key — all worktrees of one repository report the same shared git dir,
    so the tree can nest folders that were never created through this API at all.
    """
    return {"roots": worktrees.annotate(workspace.roots())}


@router.get("/list")
def list_worktrees(path: str = Query(...)):
    return worktrees.list_for(path)


@router.get("/branches")
def list_branches(path: str = Query(...)):
    return worktrees.branches(path)


@router.get("/suggest")
def suggest(path: str = Query(...), name: str = Query("")):
    """Where a worktree of this name would land, so the dialog can show it before you commit."""
    return {"dest": worktrees.default_dest(path, name)}


class CreateBody(BaseModel):
    path: str                      # the project the worktree comes from
    name: str
    branch: str = ""               # blank = derived from the name
    base: str = ""                 # blank = current HEAD
    dest: str = ""                 # blank = beside the project
    existing_branch: bool = False  # check out a branch that already exists instead of making one
    open_in_workspace: bool = True


@router.post("/create")
def create(body: CreateBody):
    r = worktrees.create(body.path, body.name, body.branch, body.base,
                         body.dest, body.existing_branch)
    if not r.get("ok"):
        return r
    if body.open_in_workspace:
        try:
            root = workspace.add_root(r["path"])
            r["root"] = root
        except Exception as e:                    # noqa: BLE001
            # The worktree EXISTS — say so rather than imply the whole thing failed, or the user
            # will make it again and hit "already exists and is not empty".
            r["root_error"] = (f"The worktree was created at {r['path']}, but it could not be "
                               f"opened as a workspace: {e}")
    return r


class RemoveBody(BaseModel):
    path: str
    force: bool = False            # required when it holds uncommitted work
    delete_branch: bool = False


@router.post("/remove")
def remove(body: RemoveBody):
    r = worktrees.remove(body.path, body.force, body.delete_branch)
    if r.get("ok"):
        try:
            workspace.remove_root(body.path)      # stop showing a folder that is gone
        except Exception:
            pass
    return r

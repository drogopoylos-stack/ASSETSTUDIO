"""Git worktrees — several branches of one repo, open side by side, one agent each.

Why this needs almost no machinery: the Studio's unit is a FOLDER, and a worktree is a folder.
A project id is derived from the absolute path (``workspace._claude_id``), so a worktree gets its
own id, and therefore its own session, transcript, model, agent picker and place in Mission
Control — with no change to the session layer at all. All that was missing was the git side and a
way to see them grouped.

Two rules git enforces for us, and both are features:

* One branch may be checked out in one worktree at a time. That is what stops two agents fighting
  over the same branch, without the Studio arbitrating anything.
* ``.git`` is shared. Objects and refs live once, so three checkouts cost disk for their files,
  not three copies of the history.

Every call here is a short ``git`` subprocess. They run in FastAPI's threadpool (the routes are
sync), never on the event loop. Grouping is cached because the rail asks about every open root at
once and this machine has forty of them — forty uncached calls would be felt as a stutter.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# A repo's shape changes when you switch branch or add a worktree — both rare next to how often
# the rail re-renders. Short enough that a new worktree shows up on the next poll by itself.
_TTL = 20.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}

# git refuses these outright; catching them here turns a raw git error into a sentence.
_BAD_BRANCH = re.compile(r"(^[.\-/]|[.\-/]$|\.\.|[\x00-\x20~^:?*\[\\]|@\{|//|\.lock$)")


def _git(path: str, args: list[str], timeout: float = 6.0) -> tuple[bool, str]:
    """Run git in `path`. Returns (ok, output-or-error). Never raises."""
    try:
        r = subprocess.run(["git", "-C", path, *args],
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "git is not installed, or not on PATH"
    except Exception as e:                                    # noqa: BLE001 — a probe never raises
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or f"git exited {r.returncode}").strip()
    return True, r.stdout.strip()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def repo_info(path: str, with_dirty: bool = False) -> dict:
    """What this folder is, in git terms.

    ``repo`` is the shared git dir, and it is the grouping key: every worktree of one repository
    reports the same one, so folders can be nested under their repo without knowing anything
    about how they were created — including roots the user added by hand years ago.

    One ``rev-parse`` answers the common dir, the git dir and the branch together — three
    processes collapsed into one, which matters because the rail asks about every open root and
    this machine has forty-six of them. ``dirty`` stays opt-in: `git status` is the expensive
    call on a large repo, and the rail does not need it to draw the tree.
    """
    key = f"{str(path).lower()}|{int(with_dirty)}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]

    info: dict = {"is_repo": False, "repo": "", "branch": "", "primary": False,
                  "detached": False, "dirty": False, "kind": "plain", "rel": "", "top": ""}
    if Path(path).exists():
        ok, out = _git(path, ["rev-parse", "--path-format=absolute", "--git-common-dir",
                              "--git-dir", "--show-toplevel", "--abbrev-ref", "HEAD"])
        lines = out.splitlines() if ok else []
        if ok and len(lines) >= 3:
            common, gitdir, top = lines[0], lines[1], lines[2]
            branch = lines[3] if len(lines) > 3 else ""
            here = Path(path).resolve()
            info["is_repo"] = True
            info["repo"] = str(Path(common).resolve()).lower()
            info["top"] = str(Path(top).resolve())
            # In the MAIN checkout the two are the same path. In a worktree, --git-dir points at
            # .git/worktrees/<name> while --git-common-dir still points at the shared .git.
            info["primary"] = Path(gitdir).resolve() == Path(common).resolve()
            info["branch"] = branch
            info["detached"] = branch == "HEAD"
            # Three different things share one repo, and the rail has to draw them differently.
            # A folder INSIDE a checkout answers every git question with the checkout's answers,
            # so --show-toplevel is the only thing that separates "this is the checkout" from
            # "this is a folder in it". That second case is a scoped sub-workspace: same files,
            # same branch, but an agent pointed at one corner of the repo instead of all of it.
            if Path(top).resolve() != here:
                info["kind"] = "subfolder"
                try:
                    info["rel"] = here.relative_to(Path(top).resolve()).as_posix()
                except ValueError:
                    info["rel"] = here.name
            else:
                info["kind"] = "primary" if info["primary"] else "worktree"
            if with_dirty:
                okd, st = _git(path, ["status", "--porcelain", "--untracked-files=no"])
                info["dirty"] = bool(okd and st)

    with _lock:
        _cache[key] = (now, info)
    return info


def annotate(roots: list[dict], with_dirty: bool = False) -> list[dict]:
    """Add git facts to a list of workspace roots, leaving every existing field alone.

    Concurrent because it is 46 folders here and each one is a subprocess: serially that is
    seconds of dead UI, and the rail redraws often enough that anyone would notice.
    """
    paths = [r.get("path", "") for r in roots]
    if not paths:
        return list(roots)
    with ThreadPoolExecutor(max_workers=min(16, len(paths))) as pool:
        infos = list(pool.map(lambda p: repo_info(p, with_dirty), paths))
    return [{**r, **i} for r, i in zip(roots, infos)]


def list_for(path: str) -> dict:
    """Every worktree of the repository this folder belongs to, primary first."""
    info = repo_info(path)
    if not info["is_repo"]:
        return {"ok": False, "error": "This folder is not a git repository.", "worktrees": []}
    ok, out = _git(path, ["worktree", "list", "--porcelain"])
    if not ok:
        return {"ok": False, "error": out, "worktrees": []}
    trees, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur.get("path"):
                trees.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        if k == "worktree":
            cur["path"] = str(Path(v).resolve())
        elif k == "HEAD":
            cur["head"] = v[:8]
        elif k == "branch":
            cur["branch"] = v.rsplit("/", 1)[-1]
        elif k in ("bare", "detached", "locked", "prunable"):
            cur[k] = True
    for t in trees:
        t.setdefault("branch", "")
        t["primary"] = Path(t["path"]).resolve() == Path(_main_of(path) or t["path"]).resolve()
        t["name"] = Path(t["path"]).name
    trees.sort(key=lambda t: (not t.get("primary"), t["name"].lower()))
    return {"ok": True, "worktrees": trees}


def _main_of(path: str) -> str:
    """The primary checkout of this repo — the one whose git dir IS the common dir."""
    ok, common = _git(path, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if not ok or not common:
        return ""
    c = Path(common)
    # <repo>/.git  ->  <repo>.  A bare or unusual layout has no single obvious main checkout.
    return str(c.parent) if c.name == ".git" else ""


def branches(path: str, limit: int = 200) -> dict:
    """Local branches, and which ones are already checked out somewhere (so the UI can say so)."""
    info = repo_info(path)
    if not info["is_repo"]:
        return {"ok": False, "error": "Not a git repository.", "branches": [], "current": ""}
    ok, out = _git(path, ["for-each-ref", "--sort=-committerdate", f"--count={int(limit)}",
                          "--format=%(refname:short)", "refs/heads"])
    taken = {t.get("branch", "") for t in list_for(path).get("worktrees", [])}
    return {"ok": True, "current": info["branch"], "taken": sorted(x for x in taken if x),
            "branches": out.splitlines() if ok else []}


def default_dest(project_path: str, name: str) -> str:
    """Where a new worktree lands by default: beside the project, named after it.

    Sibling rather than hidden away, because you WILL open it in Explorer, and a folder you
    cannot find is a folder you cannot trust.
    """
    p = Path(project_path)
    return str(p.parent / f"{p.name}-{_slug(name) or 'worktree'}")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip()).strip("-.")


def create(project_path: str, name: str, branch: str = "", base: str = "",
           dest: str = "", existing_branch: bool = False) -> dict:
    """Add a worktree to this repo. Returns {ok, path, branch} or {ok: False, error}.

    Refuses before running git wherever a clear sentence beats git's own wording — the caller is
    a dialog box, and "fatal: invalid reference" is not something to show a person.
    """
    info = repo_info(project_path)
    if not info["is_repo"]:
        return {"ok": False, "error": "This folder is not a git repository, so it has no worktrees."}

    name = (name or "").strip()
    branch = (branch or _slug(name)).strip()
    if not name:
        return {"ok": False, "error": "Give the worktree a name."}
    if not branch:
        return {"ok": False, "error": "Give the branch a name."}
    if _BAD_BRANCH.search(branch):
        return {"ok": False, "error": f"'{branch}' is not a valid branch name."}

    target = Path(dest.strip() or default_dest(project_path, name))
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "error": f"{target} already exists and is not empty."}

    taken = {t.get("branch", ""): t["path"] for t in list_for(project_path).get("worktrees", [])}
    if existing_branch and branch in taken:
        return {"ok": False,
                "error": f"Branch '{branch}' is already checked out at {taken[branch]}. "
                         "Git allows one worktree per branch — pick another, or open that one."}

    args = ["worktree", "add"]
    if existing_branch:
        args += [str(target), branch]
    else:
        args += ["-b", branch, str(target)]
        if base.strip():
            args.append(base.strip())
    ok, out = _git(project_path, args, timeout=120.0)
    if not ok:
        return {"ok": False, "error": out}
    clear_cache()
    return {"ok": True, "path": str(target.resolve()), "branch": branch,
            "name": target.name, "message": out}


def remove(path: str, force: bool = False, delete_branch: bool = False) -> dict:
    """Remove a worktree. Never the primary, and never uncommitted work without `force`."""
    info = repo_info(path)
    if not info["is_repo"]:
        return {"ok": False, "error": "Not a git repository."}
    if info["primary"]:
        return {"ok": False,
                "error": "This is the repository's main checkout, not a worktree. "
                         "Removing it would delete the project itself."}
    if info["dirty"] and not force:
        return {"ok": False, "dirty": True,
                "error": "This worktree has uncommitted changes. Commit them, or remove it anyway."}

    branch = info["branch"]
    main = _main_of(path) or path
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(Path(path).resolve())]
    ok, out = _git(main, args, timeout=60.0)
    if not ok:
        return {"ok": False, "error": out}
    if delete_branch and branch and branch != "HEAD":
        _git(main, ["branch", "-D", branch])      # best effort: the worktree is already gone
    _git(main, ["worktree", "prune"])
    clear_cache()
    return {"ok": True, "removed": str(Path(path).resolve()), "branch": branch}

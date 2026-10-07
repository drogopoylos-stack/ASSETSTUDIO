"""Source control for a workspace or a worktree — status, commit, push, pull.

Scope on purpose: this is not a git client. It is the four things you want without leaving the
Studio — see what changed, write it down, send it up, take what is there. Anything with a real
chance of losing work (rebase, reset, force push, history rewriting) stays out; those belong in a
terminal where you can see exactly what you are agreeing to, and the Studio has one of those.

Two rules the whole module keeps:

* **Nothing here is implicit.** Every call is something you pressed. Push in particular is
  outward-facing and irreversible-ish, so it never rides along with anything else.
* **Refuse rather than improvise.** No upstream, no identity configured, nothing staged, a
  diverged branch — each returns a sentence explaining the situation instead of guessing which
  repair you meant.

Every call is a short subprocess and every route that uses it is sync, so it runs in FastAPI's
threadpool, never on the event loop.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

_TIMEOUT = 20.0
_NET_TIMEOUT = 180.0          # a push over a slow line is not a hang


def _git(path: str, args: list[str], timeout: float = _TIMEOUT) -> tuple[bool, str]:
    """Run git in `path`. Returns (ok, stdout-or-stderr). Never raises, never prompts.

    GIT_TERMINAL_PROMPT=0 matters more than it looks: without it a push to a repo needing
    credentials blocks forever on a prompt nobody can see, and the request just hangs.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                           timeout=timeout, env=env)
    except FileNotFoundError:
        return False, "git is not installed, or not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} timed out"
    except Exception as e:                                  # noqa: BLE001
        return False, str(e)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return False, err or out or f"git exited {r.returncode}"
    return True, out


_XY = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "U": "conflict"}


def status(path: str) -> dict:
    """Everything the panel needs, in one pass."""
    if not Path(path).exists():
        return {"ok": False, "error": "That folder is not there."}
    # `rev-parse HEAD` is the WRONG probe: a repository with no commits yet — a fresh `git init`,
    # or a clone of an empty repo — has an unborn HEAD and fails it, so a perfectly real repo
    # reported as "not a repository". `--git-dir` answers for the repo itself, and
    # `branch --show-current` names an unborn branch happily (and returns empty when detached).
    ok, _gd = _git(path, ["rev-parse", "--git-dir"])
    if not ok:
        return {"ok": False, "is_repo": False,
                "error": "This folder is not a git repository."}
    _, branch = _git(path, ["branch", "--show-current"])
    detached = not branch
    if detached:
        _, short = _git(path, ["rev-parse", "--short", "HEAD"])
        branch = f"detached at {short}" if short else "HEAD"

    ok, porcelain = _git(path, ["status", "--porcelain=v1", "--untracked-files=all"])
    files, staged, unstaged, untracked, conflicts = [], 0, 0, 0, 0
    for line in (porcelain.splitlines() if ok else []):
        if len(line) < 3:
            continue
        x, y, name = line[0], line[1], line[3:].strip()
        if x == "?" and y == "?":
            untracked += 1
            files.append({"path": name, "state": "untracked", "staged": False})
            continue
        if "U" in (x, y):
            conflicts += 1
            files.append({"path": name, "state": "conflict", "staged": False})
            continue
        if x != " ":
            staged += 1
        if y != " ":
            unstaged += 1
        files.append({"path": name, "staged": x != " ",
                      "state": _XY.get(x if x != " " else y, "changed")})

    ok_up, upstream = _git(path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    ahead = behind = 0
    if ok_up and upstream:
        ok_c, counts = _git(path, ["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
        if ok_c:
            parts = counts.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])

    # `_git` returns the ERROR TEXT in the same slot as the output, so every one of these has to
    # honour the ok flag. Ignoring it put "fatal: your current branch 'main' does not have any
    # commits yet" into last_commit.hash — and, far worse, put "git exited 1" into user.name,
    # which made an unconfigured identity read as configured and let commit() proceed.
    ok_r, remote = _git(path, ["remote", "get-url", "origin"])
    remote = remote if ok_r else ""
    ok_l, last = _git(path, ["log", "-1", "--format=%h\x1f%s\x1f%cr\x1f%an"])
    h, subj, when, who = ((last.split("\x1f") + ["", "", "", ""])[:4] if ok_l else ("", "", "", ""))
    ok_n, name_cfg = _git(path, ["config", "user.name"])
    ok_m, mail_cfg = _git(path, ["config", "user.email"])
    name_cfg = name_cfg if ok_n else ""
    mail_cfg = mail_cfg if ok_m else ""

    return {
        "ok": True, "is_repo": True, "branch": branch,
        "detached": detached,
        # No commits yet. Worth naming: push, pull and "what changed since" all mean something
        # different before the first commit, and the panel should say so rather than look broken.
        "unborn": not bool(h),
        "upstream": upstream if ok_up else "",
        "ahead": ahead, "behind": behind,
        "remote": remote, "host": _host_of(remote),
        "files": files[:400], "truncated": max(0, len(files) - 400),
        "staged": staged, "unstaged": unstaged, "untracked": untracked, "conflicts": conflicts,
        "clean": not files,
        "last_commit": {"hash": h, "subject": subj, "when": when, "author": who} if h else None,
        "identity": {"name": name_cfg, "email": mail_cfg,
                     "ok": bool(name_cfg and mail_cfg)},
    }


def _host_of(remote: str) -> str:
    if not remote:
        return ""
    m = re.search(r"(?:@|//)([^/:]+)", remote)
    return m.group(1) if m else ""


def commit(path: str, message: str, all_changes: bool = True) -> dict:
    """Stage and commit. Refuses on an empty message, no identity, or nothing to record."""
    msg = (message or "").strip()
    if not msg:
        return {"ok": False, "error": "Write a commit message first."}
    st = status(path)
    if not st.get("ok"):
        return st
    if not st["identity"]["ok"]:
        return {"ok": False, "error":
                "git does not know who you are yet. Set it once:\n"
                '  git config --global user.name "Your Name"\n'
                '  git config --global user.email "you@example.com"'}
    if st["conflicts"]:
        return {"ok": False, "error":
                f"{st['conflicts']} file(s) still have merge conflicts. Resolve them first — "
                "committing now would record the conflict markers."}
    if all_changes:
        ok, out = _git(path, ["add", "-A"])
        if not ok:
            return {"ok": False, "error": out}
    ok, staged_now = _git(path, ["diff", "--cached", "--name-only"])
    if not ok or not staged_now.strip():
        return {"ok": False, "error": "Nothing to commit — no changes are staged."}
    ok, out = _git(path, ["commit", "-m", msg])
    if not ok:
        return {"ok": False, "error": out}
    _, h = _git(path, ["rev-parse", "--short", "HEAD"])
    return {"ok": True, "hash": h, "files": len(staged_now.splitlines()), "message": out}


def push(path: str, set_upstream: bool = True) -> dict:
    """Send this branch to its remote. Never forced, never to a branch you did not ask for."""
    st = status(path)
    if not st.get("ok"):
        return st
    if st["detached"]:
        return {"ok": False, "error": "HEAD is detached — there is no branch to push."}
    if not st["remote"]:
        return {"ok": False, "no_remote": True,
                "error": "This repository has no `origin` remote yet, so there is nowhere to "
                         "push. Create one on GitHub first."}
    if st["behind"] and st["ahead"]:
        return {"ok": False, "error":
                f"The branch has diverged — {st['ahead']} commit(s) here, {st['behind']} there. "
                "Pull first, sort out the merge, then push."}
    args = ["push"]
    if not st["upstream"] and set_upstream:
        args += ["--set-upstream", "origin", st["branch"]]
    ok, out = _git(path, args, timeout=_NET_TIMEOUT)
    if not ok:
        return {"ok": False, "error": out}
    return {"ok": True, "message": out or f"pushed {st['branch']}", "branch": st["branch"]}


def pull(path: str) -> dict:
    """Fast-forward only. A pull that would need a merge commit stops and says so, rather than
    creating one you did not ask for in a workspace an agent is editing."""
    st = status(path)
    if not st.get("ok"):
        return st
    if not st["upstream"]:
        return {"ok": False, "error": "This branch has no upstream yet — push it first."}
    if not st["clean"]:
        return {"ok": False, "error":
                "There are uncommitted changes here. Commit them first, so a pull cannot land "
                "on top of work that is not saved."}
    ok, out = _git(path, ["pull", "--ff-only"], timeout=_NET_TIMEOUT)
    if not ok:
        return {"ok": False, "error": out}
    return {"ok": True, "message": out}


# --- GitHub ---------------------------------------------------------------

def gh_available() -> bool:
    return bool(shutil.which("gh"))


def gh_account() -> dict:
    """Who `gh` is signed in as. Read-only, and it never opens a browser."""
    if not gh_available():
        return {"ok": False, "installed": False,
                "error": "The GitHub CLI (gh) is not installed."}
    try:
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                           capture_output=True, text=True, timeout=_TIMEOUT,
                           env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "installed": True, "error": str(e)}
    if r.returncode != 0:
        return {"ok": False, "installed": True, "authenticated": False,
                "error": "gh is installed but not signed in. Run `gh auth login` once."}
    return {"ok": True, "installed": True, "authenticated": True,
            "login": (r.stdout or "").strip()}


def gh_create_repo(path: str, name: str, private: bool = True, push_now: bool = False) -> dict:
    """Make a GitHub repo for this folder and wire it up as `origin`.

    `push_now` is separate and defaults to OFF: creating somewhere to push and actually
    publishing your code are two different decisions, and only one of them is hard to undo.
    """
    acct = gh_account()
    if not acct.get("ok"):
        return acct
    repo = (name or Path(path).name).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo):
        return {"ok": False, "error": f"'{repo}' is not a valid repository name."}
    st = status(path)
    if not st.get("ok"):
        return st
    if st["remote"]:
        return {"ok": False, "error": f"This folder already has an origin: {st['remote']}"}
    args = ["gh", "repo", "create", repo, "--source", str(Path(path).resolve()),
            "--private" if private else "--public"]
    if push_now:
        args.append("--push")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=_NET_TIMEOUT,
                           env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "gh failed").strip()}
    _, remote = _git(path, ["remote", "get-url", "origin"])
    return {"ok": True, "remote": remote, "pushed": push_now,
            "message": (r.stdout or r.stderr or "").strip()}

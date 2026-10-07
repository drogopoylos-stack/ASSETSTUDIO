"""Claude Code login state + in-app re-login for the Studio.

When the user's Claude OAuth token can't be refreshed, every Claude call fails with a 401
("Invalid authentication credentials"). Detection here is observation-based: whatever sees a
401 first — the usage poll (``usage.py``) or a live chat turn (``cc_session.py``) — reports it,
and a later success clears it. The UI polls :func:`status` and, when ``needs_login`` is true,
shows a popup whose button calls :func:`open_login` to (re)authenticate in the browser.

Auth lives in ``~/.claude/.credentials.json`` (``claudeAiOauth``): running the Claude CLI once
refreshes that file — covering both a silent token refresh and a full browser re-login — which
is exactly what the Studio's headless calls and the usage check read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

_lock = threading.Lock()
_fail: Optional[dict] = None            # observed auth failure, or None. {"reason","source","at"}
_last_verify = [0.0]                    # throttle the background "is the token actually dead?" check
from .config import claude_home as _claude_home    # noqa: E402 - stdlib-only config, no cycle
_CREDS = _claude_home() / ".credentials.json"

# strings that mark a genuine Claude auth failure (used to classify a turn's error result)
AUTH_ERR_RE = re.compile(
    r"\b401\b|invalid authentication|invalid api key|unauthorized|please run /login"
    r"|authentication_error|oauth token (has )?expired|credentials? (expired|invalid)",
    re.I,
)


def _creds() -> dict:
    try:
        d = json.loads(_CREDS.read_text(encoding="utf-8"))
        return d.get("claudeAiOauth") or d
    except Exception:
        return {}


def usage_limits() -> dict:
    """The account's live rate-limit buckets (5h session, weekly all-models, weekly
    per-model e.g. Fable) — the same numbers the CLI's /usage shows. Delegates to
    usage.compute(), the single fetcher/cache for the OAuth usage endpoint: running a
    second independent poller against it is exactly what triggered 429s. Stale (last
    known good) values are served during backoff, marked with `stale`."""
    try:
        from . import usage
        d = usage.compute()
    except Exception as e:
        return {"ok": False, "limits": [], "stale": True, "error": str(e)[:200]}
    return {"ok": bool(d.get("available")), "limits": d.get("limits") or [],
            "stale": bool(d.get("stale")), "error": d.get("error") or ""}


_CLI_TTL = 60.0
_cli_cache: dict = {"ts": 0.0, "data": None}


def _cli_status(force: bool = False) -> dict:
    """Ask the Claude CLI itself whether we are signed in — ``claude auth status`` is
    instant, non-interactive, costs no model tokens, and is AUTHORITATIVE (it returns
    ``loggedIn``). This is the ground truth a 401 gets checked against, so a merely
    stale access token can never be mistaken for a dead login. Cached (incl. failures)
    so repeated 401s can't turn into a process-spawn storm."""
    now = time.time()
    if not force and _cli_cache["data"] is not None and (now - _cli_cache["ts"]) < _CLI_TTL:
        return _cli_cache["data"]
    exe = _find_claude()
    if not exe:
        return {}
    try:
        kw = {}
        if os.name == "nt":                       # never flash a console at the user
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        env = dict(os.environ)
        env.pop("ASSET_STUDIO_CC", None)          # keep the window-sweeper off these children
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                           timeout=45, cwd=str(Path.home()), env=env, **kw)
        d = json.loads((r.stdout or "").strip() or "{}")
        d = d if isinstance(d, dict) else {}
    except Exception:
        d = {}
    _cli_cache["ts"], _cli_cache["data"] = now, d
    return d


def cli_logged_in() -> Optional[bool]:
    """True / False from the CLI, or None when it couldn't be determined."""
    d = _cli_status()
    return bool(d.get("loggedIn")) if d else None


def _refresh_alive(c: Optional[dict] = None) -> bool:
    """Is the OAuth REFRESH token still valid? Access tokens last ~8h, refresh tokens ~28
    days — while the refresh token lives, a new access token can be minted silently and the
    user does NOT need to log in again."""
    c = c if c is not None else _creds()
    exp = c.get("refreshTokenExpiresAt")
    try:
        return bool(exp) and (float(exp) / 1000.0) > time.time() + 60
    except Exception:
        return False


_REFRESH_GAP = 180.0
_last_refresh = [0.0]


def _kick_silent_refresh() -> None:
    """Nudge the CLI to mint a fresh access token from the still-valid refresh token, in a
    hidden window, then drop the usage cache so the next poll retries immediately."""
    if time.time() - _last_refresh[0] < _REFRESH_GAP:
        return
    _last_refresh[0] = time.time()

    def run() -> None:
        _cli_status(force=True)          # running the CLI is what rewrites .credentials.json
        try:
            from . import usage
            usage.invalidate()
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def report_401(source: str, message: str = "") -> None:
    """A 401 from the usage-METERING endpoint — which is called with the access token read
    straight off disk, and nothing in the Studio refreshes that token. Access tokens expire
    every ~8h while the refresh token stays valid ~28 days, so a 401 here almost always means
    "our cached token went stale", NOT "the user is logged out". Escalating it straight to the
    blocking popup is what made the login modal appear ~10x/day and re-open after "Not now"
    (a successful chat turn cleared the flag, the next 90s poll re-raised it with a NEW
    timestamp, and the modal treats a changed timestamp as a brand-new failure). Only a CLI
    that actually reports loggedIn:false counts as a real logout; everything else self-heals."""
    live = cli_logged_in()
    if live is True:                      # signed in — stale token only
        _kick_silent_refresh()
        return
    if live is False:                     # genuinely signed out
        report_error(source, message or "You're signed out of Claude Code. Sign in again to continue.")
        return
    if _refresh_alive():                  # CLI unavailable — trust the refresh-token clock
        _kick_silent_refresh()
        return
    report_error(source, message)


_FLAP_WINDOW = 1800.0                     # one outage = one prompt, for at most 30 min
_last_fail_at: dict = {}


def report_error(source: str, message: str = "") -> None:
    """A CONFIRMED auth failure — surfaces the re-login popup. The popup keys "is this a NEW
    failure?" on the timestamp, so re-using the previous one within a single outage is what
    keeps "Not now" from being undone every time a successful turn briefly clears the flag."""
    global _fail
    with _lock:
        if _fail is not None:
            return
        prev = float(_last_fail_at.get(source) or 0.0)
        at = prev if (prev and time.time() - prev < _FLAP_WINDOW) else time.time()
        _last_fail_at[source] = at
        _fail = {"reason": message or "Your Claude login expired (401).", "source": source, "at": at}


def report_ok(source: str = "") -> None:
    """Claude auth is working again (a call just succeeded) — clears the failure + the popup."""
    global _fail
    if _fail is None:
        return
    with _lock:
        _fail = None


_last_suspect = [0.0]


def verify_soon(source: str = "") -> None:
    """A chat turn LOOKED auth-related — but a turn's text is unreliable: it routinely contains code
    or tool output that merely mentions 401 / unauthorized / authentication_error (constant in web/API
    work), and an errored turn can just be a 429, a tool failure, or a refusal. NEVER pop the login
    modal from that. Instead kick a throttled REAL account check; only a genuine 401 from it (via
    usage.compute → report_error) surfaces the popup. This is what stops the false 're-login' prompts."""
    if time.time() - _last_suspect[0] < 20:
        return
    _last_suspect[0] = time.time()
    threading.Thread(target=_bg_verify, daemon=True).start()


def _bg_verify() -> None:
    try:
        from . import usage
        usage.compute(force=True)   # a real account call → reports ok / error back here
    except Exception:
        pass


def status() -> dict:
    """Instant (no network): current login state from the observed-failure flag + the creds file.
    If the stored token is expired by timestamp, kick a throttled background check so a truly dead
    token flips ``needs_login`` on the next poll — without ever blocking this call."""
    c = _creds()
    has_token = bool(c.get("accessToken")) or bool(os.environ.get("ANTHROPIC_API_KEY"))
    exp = c.get("expiresAt")
    expired = bool(exp) and (float(exp) / 1000.0) < time.time()
    if has_token and expired and (time.time() - _last_verify[0] > 30):
        _last_verify[0] = time.time()
        threading.Thread(target=_bg_verify, daemon=True).start()
    with _lock:
        fail = dict(_fail) if _fail else None
    needs_login = (not has_token) or bool(fail)
    reason = ("You're not logged in to Claude Code." if not has_token
              else (fail.get("reason") if fail else ""))
    return {
        "ok": not needs_login,
        "needs_login": needs_login,
        "logged_in": has_token,
        "reason": reason,
        "source": (fail.get("source") if fail else ""),
        "since": (fail.get("at") if fail else None),
        "plan": c.get("subscriptionType") or "",
        "expires_at": exp,
    }


def recheck() -> dict:
    """Force a fresh, real auth check (after the user logs in) and return the new status."""
    _bg_verify_sync()
    return status()


def _bg_verify_sync() -> None:
    try:
        from . import usage
        usage.compute(force=True)
    except Exception:
        pass


# --- launch the browser re-login -------------------------------------------
def _find_claude() -> Optional[str]:
    from . import cc_session
    return cc_session.find_claude()


def open_login() -> dict:
    """Open Claude Code in a visible terminal so the user can (re)authenticate in the browser.

    Running the CLI interactively refreshes ``~/.claude/.credentials.json`` — a silent token
    refresh if the refresh token is still good, or a full browser login if not — which is what
    every Studio Claude call reads. Returns the command so the UI can show a manual fallback."""
    exe = _find_claude()
    if not exe:
        return {"ok": False, "error": "Claude Code CLI not found. Install it, then retry.",
                "command": "claude"}
    # `claude auth login` goes straight to the browser sign-in. (Launching the bare CLI instead
    # just drops the user at an interactive prompt they then have to drive themselves.)
    cmd = f'"{exe}" auth login'
    home = str(Path.home())                       # neutral cwd — avoids a project trust prompt
    env = dict(os.environ)
    # The window-sweeper relocates browser windows tagged ASSET_STUDIO_CC to a spare monitor.
    # The OAuth window must stay in front of the user, so never let the login inherit that tag.
    env.pop("ASSET_STUDIO_CC", None)
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            note = ("Signing in to Claude Code. Finish the login in your browser, "
                    "then return to Asset Studio and click I have logged in.")
            inner = f'title Claude Code Login & echo {note} & echo. & {cmd}'
            subprocess.Popen(["cmd", "/k", inner], creationflags=flags, cwd=home, env=env)
        elif sys_is_mac():
            subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "{cmd}"'],
                             cwd=home, env=env)
        else:
            for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["xterm", "-e"]):
                try:
                    subprocess.Popen([*term, exe, "auth", "login"], cwd=home, env=env)
                    break
                except FileNotFoundError:
                    continue
        return {"ok": True, "command": cmd}
    except Exception as e:
        return {"ok": False, "error": str(e), "command": cmd}


def sys_is_mac() -> bool:
    import sys
    return sys.platform == "darwin"

"""Real Claude Code plan-usage limits.

Claude Code's /usage panel (Session 5h %, Weekly 7d %) is served by the Anthropic
endpoint ``/api/oauth/usage``. We call that same endpoint with the OAuth token
Claude Code already stored in ``~/.claude/.credentials.json`` — so the numbers
match Claude Code exactly (this is the user's own account/token on their machine).

No reverse-engineering of the limit math is needed: the API returns the
utilization percentages and reset times directly.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from .config import DATA_DIR, claude_home

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CACHE: dict = {"ts": 0.0, "data": None}      # last response served (success or fallback)
_GOOD: dict = {"data": None}                  # last SUCCESSFUL fetch (for graceful fallback)
_RETRY_AFTER: dict = {"ts": 0.0}              # don't call the API again before this time
_TTL = 90.0
# last-good survives restarts: without this, a backend that starts INSIDE a 429 backoff
# window has nothing to fall back to and the UI shows a raw error instead of numbers
_GOOD_F = DATA_DIR / "usage_cache.json"


def _load_good() -> None:
    if _GOOD["data"] is None:
        try:
            d = json.loads(_GOOD_F.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("available"):
                _GOOD["data"] = d
        except Exception:
            pass


def _creds() -> dict:
    try:
        data = json.loads((claude_home() / ".credentials.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("claudeAiOauth") or data


def _reset_seconds(resets_at: str | None) -> int:
    if not resets_at:
        return 0
    try:
        s = resets_at.replace("Z", "+00:00")
        return max(0, round(datetime.fromisoformat(s).timestamp() - time.time()))
    except Exception:
        return 0


def _plan_label(creds: dict) -> str:
    # subscriptionType ("max"/"pro") is reliable; the internal rateLimitTier is NOT
    # the marketed multiplier, so we don't guess "(Nx)" from it.
    plan = (creds.get("subscriptionType") or "").strip()
    return plan.capitalize() if plan else "Claude"


def invalidate() -> None:
    """Drop the cached usage response so the next poll really refetches. Called right after a
    silent token refresh, so a stale-token 401 is retried at once instead of after the TTL."""
    _CACHE["ts"] = 0.0


def compute(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["data"] and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]

    creds = _creds()
    token = creds.get("accessToken")
    plan = _plan_label(creds)
    if not token:
        return _fallback(plan, "No Claude Code credentials found (~/.claude/.credentials.json).")

    # rate-limit backoff: keep serving the last known usage instead of hammering the API
    if now < _RETRY_AFTER["ts"]:
        return _fallback(plan, "usage API rate-limited — showing last known values")

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "User-Agent": "AssetStudio-MissionControl",
    }
    try:
        r = httpx.get(USAGE_URL, headers=headers, timeout=12)
    except Exception as e:
        return _fallback(plan, f"request failed: {e}")
    if r.status_code == 429:
        wait = 300
        ra = r.headers.get("retry-after")
        try:
            if ra:
                wait = max(120, min(900, int(ra)))
        except ValueError:
            pass
        _RETRY_AFTER["ts"] = time.time() + wait
        return _fallback(plan, "usage API rate-limited — showing last known values")
    if r.status_code == 401:
        try:
            from . import claude_auth
            # NOT necessarily a logout: this call uses the access token straight off disk and
            # nothing here refreshes it, so an expired 8h token 401s while the login is fine.
            # report_401 verifies against `claude auth status` and only nags if truly signed out.
            claude_auth.report_401("usage", "Your Claude login expired (401). Log in again to keep using Claude Code.")
        except Exception:
            pass
        return _fallback(plan, "Claude credentials expired — open Claude Code once to refresh, then retry.")
    if r.status_code != 200:
        return _fallback(plan, f"usage API returned HTTP {r.status_code}")

    d = r.json()

    def win(key: str, label: str, node) -> dict | None:
        if not isinstance(node, dict) or node.get("utilization") is None:
            return None
        return {
            "key": key, "label": label,
            "percent": round(float(node["utilization"]), 1),
            "reset_seconds": _reset_seconds(node.get("resets_at")),
            "resets_at": node.get("resets_at"),
        }

    windows = [w for w in [
        win("session", "Current session", d.get("five_hour")),
        win("weekly", "Weekly · all models", d.get("seven_day")),
        win("weekly_opus", "Weekly · Opus", d.get("seven_day_opus")),
        win("weekly_sonnet", "Weekly · Sonnet", d.get("seven_day_sonnet")),
    ] if w]

    # the canonical per-bucket list (includes per-model buckets like Fable) — feeds the
    # top-bar meter via claude_auth.usage_limits(); parsed here so ONE fetch serves both
    # UIs (a second independent poller of this endpoint is what got us 429'd).
    limits = []
    for l in d.get("limits") or []:
        kind = l.get("kind") or ""
        model = ((((l.get("scope") or {}).get("model")) or {}).get("display_name") or "")
        label = ("5h session" if kind == "session"
                 else f"{model} · week" if model
                 else "All models · week" if kind == "weekly_all" else kind)
        limits.append({"kind": kind, "label": label, "model": model,
                       "percent": int(round(float(l.get("percent") or 0))),
                       "resets_at": l.get("resets_at") or ""})

    extra = d.get("extra_usage") or {}
    data = {
        "available": True, "plan": plan, "windows": windows, "limits": limits,
        "extra_usage": {
            "enabled": bool(extra.get("is_enabled")),
            "used_credits": extra.get("used_credits"),
            "monthly_limit": extra.get("monthly_limit"),
            "currency": extra.get("currency"),
        } if extra else None,
        "stale": False, "source": "api", "generated_at": time.time(),
    }
    _RETRY_AFTER["ts"] = 0.0      # healthy again — clear any backoff
    _GOOD["data"] = data
    try:
        _GOOD_F.parent.mkdir(parents=True, exist_ok=True)
        _GOOD_F.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    try:
        from . import claude_auth
        claude_auth.report_ok("usage")   # a real account call succeeded → auth is good
    except Exception:
        pass
    return _store(data)


def _fallback(plan: str, err: str) -> dict:
    """Serve the last *successful* usage (marked stale) instead of blanking out on a
    transient error/429 — so the bar keeps showing numbers."""
    _load_good()                  # a restart during backoff still has the on-disk snapshot
    if _GOOD["data"]:
        g = dict(_GOOD["data"])
        g["stale"] = True
        g["error"] = err
        g["generated_at"] = time.time()
        return _store(g)
    return _store({"available": False, "plan": plan, "windows": [], "limits": [], "stale": True, "error": err})


def _store(data: dict) -> dict:
    _CACHE.update(ts=time.time(), data=data)
    return data

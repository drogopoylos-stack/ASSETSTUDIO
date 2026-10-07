"""Every account the Plan usage bar can show, and what is left on each.

Two kinds of account:

``plan``
    A subscription with rolling windows - Claude (5h session + weekly, read by ``usage.py``)
    and Codex signed in with ChatGPT (5h + weekly, read from the Codex app-server the chat
    already runs, so no second Codex process is started for this).

``balance``
    A pay-as-you-go API key. There is no 5h or weekly window, so the useful number is the
    money left on the account: DeepSeek ``/user/balance``, Kimi ``/v1/users/me/balance`` and
    OpenRouter ``/api/v1/key``. The key is read from the keychain - the same slot the chat
    agent uses - and is never returned to the UI.

Every answer has the shape of ``usage.compute()`` (``available``, ``windows``, ``error`` ...)
plus ``provider``, and for a balance account a ``balance`` block. A failed read serves the
last good answer marked ``stale``, so a short outage does not blank the bar.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx

_UA = {"User-Agent": "AssetStudio-PlanUsage", "Accept": "application/json"}
_TTL = {"codex": 60.0, "balance": 120.0}
_CACHE: dict[str, dict] = {}          # provider -> {"ts", "data"}
_GOOD: dict[str, dict] = {}           # provider -> last answer with available=True
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# keys (never leave this module)
# ---------------------------------------------------------------------------
def _key(*names: str) -> str:
    from . import keychain
    for n in names:
        v = (keychain.get_key(n) or "").strip()
        if v:
            return v
    return ""


def _deepseek_key() -> str:
    from . import deepseek_session
    return (deepseek_session.api_key() or "").strip()


def _kimi_key() -> str:
    return _key("moonshot", "chat:moonshot", "chat:kimi")


def _openrouter_key() -> str:
    return _key("chat:openrouter")


# ---------------------------------------------------------------------------
# the list the picker shows
# ---------------------------------------------------------------------------
ACCOUNTS: list[dict] = [
    {"id": "claude", "name": "Claude", "kind": "plan", "connect": "claude",
     "hint": "Claude Code subscription: 5h session and weekly limits."},
    {"id": "codex", "name": "Codex (ChatGPT)", "kind": "plan", "connect": "codex",
     "hint": "Codex signed in with ChatGPT: 5h and weekly limits. An API-key sign-in has no limits to show."},
    {"id": "deepseek", "name": "DeepSeek", "kind": "balance", "connect": "key", "key": "deepseek",
     "key_url": "https://platform.deepseek.com/api_keys",
     "hint": "DeepSeek API key: the money left on the account."},
    {"id": "kimi", "name": "Kimi (Moonshot)", "kind": "balance", "connect": "key", "key": "moonshot",
     "key_url": "https://platform.moonshot.ai/console/api-keys",
     "hint": "Moonshot / Kimi API key: the money left on the account."},
    {"id": "openrouter", "name": "OpenRouter", "kind": "balance", "connect": "key", "key": "chat:openrouter",
     "key_url": "https://openrouter.ai/keys",
     "hint": "OpenRouter API key: the credit left on the key."},
]
_BY_ID = {a["id"]: a for a in ACCOUNTS}


def _claude_connected() -> bool:
    try:
        from . import claude_auth
        return bool(claude_auth.status().get("logged_in"))
    except Exception:
        return False


def _codex_account() -> Optional[dict]:
    """Codex's sign-in as codex_app reads it ({signed_in, auth, email, plan}), or None when
    Codex is not installed or does not start."""
    from . import codex_app
    if not codex_app.find_codex():
        return None
    srv = codex_app.server()
    if srv is None:
        return None
    return codex_app._read_account(srv)


def _connected(acc_id: str) -> bool:
    if acc_id == "claude":
        return _claude_connected()
    if acc_id == "codex":
        try:
            a = _codex_account()
        except Exception:
            return False
        return bool(a and a.get("signed_in"))
    if acc_id == "deepseek":
        return bool(_deepseek_key())
    if acc_id == "kimi":
        return bool(_kimi_key())
    if acc_id == "openrouter":
        return bool(_openrouter_key())
    return False


def accounts() -> list[dict]:
    """The picker's rows: what each account is, whether it is connected, and how to connect it."""
    return [{**a, "connected": _connected(a["id"])} for a in ACCOUNTS]


def known(acc_id: str) -> bool:
    return acc_id in _BY_ID


# ---------------------------------------------------------------------------
# reading one account
# ---------------------------------------------------------------------------
def _cached(pid: str, ttl: float, force: bool, fetch: Callable[[], dict]) -> dict:
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(pid)
    if hit and not force and now - hit["ts"] < ttl:
        return hit["data"]
    try:
        data = fetch()
    except Exception as e:  # noqa: BLE001
        data = _missing(pid, f"{type(e).__name__}: {e}"[:240])
    data = {**data, "provider": pid}
    if data.get("available"):
        _GOOD[pid] = data
    elif not data.get("needs_connect") and pid in _GOOD:
        data = {**_GOOD[pid], "stale": True, "error": data.get("error") or ""}
    with _LOCK:
        _CACHE[pid] = {"ts": time.time(), "data": data}
    return data


def invalidate(pid: str = "") -> None:
    """Drop the cached answer (one account, or all) so the next poll reads it again."""
    with _LOCK:
        if pid:
            _CACHE.pop(pid, None)
            _GOOD.pop(pid, None)
        else:
            _CACHE.clear()


def _missing(pid: str, error: str, connect: bool = False, plan: str = "") -> dict:
    return {"available": False, "provider": pid, "plan": plan or _BY_ID.get(pid, {}).get("name", pid),
            "windows": [], "limits": [], "stale": False, "error": error, "needs_connect": connect}


def _balance(pid: str, plan: str, amount: float, currency: str, *, limit: Optional[float] = None,
             used: Optional[float] = None, detail: str = "", ok: bool = True) -> dict:
    return {"available": True, "provider": pid, "plan": plan, "windows": [], "limits": [],
            "balance": {"amount": round(amount, 4), "currency": currency or "USD",
                        "limit": None if limit is None else round(limit, 4),
                        "used": None if used is None else round(used, 4),
                        "detail": detail, "ok": ok},
            "stale": False, "source": "api", "generated_at": time.time()}


def _http_error(r: httpx.Response, what: str) -> str:
    if r.status_code in (401, 403):
        return f"The {what} API key was refused (HTTP {r.status_code}). Paste a new key."
    if r.status_code == 429:
        return f"The {what} API is rate-limited. Retrying later."
    return f"The {what} API returned HTTP {r.status_code}."


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# --- Codex ------------------------------------------------------------------
def _codex() -> dict:
    from . import codex_app
    if not codex_app.find_codex():
        return _missing("codex", "Codex is not installed on this PC.", connect=True)
    srv = codex_app.server()
    if srv is None:
        return _missing("codex", "Codex did not start.")
    acct = codex_app._read_account(srv)
    plan = f"Codex {(acct.get('plan') or '').capitalize()}".strip()
    if not acct.get("signed_in"):
        return _missing("codex", "Codex is not signed in.", connect=True, plan=plan)
    if acct.get("auth") == "apiKey":
        # OpenAI bills an API key per token. There is no 5h or weekly window to read.
        return {"available": True, "provider": "codex", "plan": "Codex · API key", "windows": [], "limits": [],
                "note": "Signed in with an OpenAI API key: OpenAI bills per token, so there is no 5h or weekly limit.",
                "stale": False, "source": "app-server", "generated_at": time.time()}
    result = srv.request("account/rateLimits/read", {}, timeout=20) or {}
    buckets = result.get("rateLimitsByLimitId") or {}
    main = buckets.get("codex") or result.get("rateLimits") or {}
    now = time.time()
    windows: list[dict] = []

    def add(bucket: dict, prefix: str, name_of: str) -> None:
        for slot in ("primary", "secondary"):
            w = bucket.get(slot)
            if not isinstance(w, dict):
                continue
            pct = _num(w.get("usedPercent"))
            if pct is None:
                continue
            mins = w.get("windowDurationMins")
            span = "5h" if mins == 300 else "7d" if mins == 10080 else (f"{mins}m" if mins else slot)
            key = ("session" if mins == 300 else "weekly" if mins == 10080 else slot) if not prefix \
                else f"{prefix}:{span}"
            label = (("Session (5h)" if mins == 300 else "Weekly (7d)" if mins == 10080 else span)
                     if not prefix else f"{name_of} ({span})")
            reset = _num(w.get("resetsAt"))
            pct = round(max(0.0, min(100.0, pct)), 1)
            windows.append({"key": key, "label": label, "percent": pct,
                            "remaining_percent": round(100 - pct, 1),
                            "reset_seconds": max(0, round(reset - now)) if reset else 0,
                            "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset else None})

    add(main, "", "")
    # A model with its own allowance (a separate pot) comes as another bucket id.
    for lid, b in buckets.items():
        if lid != "codex" and isinstance(b, dict):
            add(b, str(lid), str(b.get("limitName") or lid))
    if not windows:
        return _missing("codex", "Codex has not reported its plan limits yet. Retrying.", plan=plan)
    return {"available": True, "provider": "codex", "plan": plan, "windows": windows, "limits": [],
            "stale": False, "source": "app-server", "generated_at": now}


# --- DeepSeek ---------------------------------------------------------------
def _deepseek() -> dict:
    key = _deepseek_key()
    if not key:
        return _missing("deepseek", "Add a DeepSeek API key to see the balance.", connect=True)
    r = httpx.get("https://api.deepseek.com/user/balance",
                  headers={**_UA, "Authorization": f"Bearer {key}"}, timeout=12)
    if r.status_code != 200:
        return _missing("deepseek", _http_error(r, "DeepSeek"), connect=r.status_code in (401, 403))
    d = r.json() or {}
    infos = d.get("balance_infos") or []
    info = next((i for i in infos if i.get("currency") == "USD"), infos[0] if infos else {})
    amount = _num(info.get("total_balance")) or 0.0
    cur = info.get("currency") or "USD"
    detail = f"granted {info.get('granted_balance', '0')} · topped up {info.get('topped_up_balance', '0')} {cur}"
    return _balance("deepseek", "DeepSeek API", amount, cur, detail=detail, ok=bool(d.get("is_available", True)))


# --- Kimi (Moonshot) ---------------------------------------------------------
def _kimi() -> dict:
    key = _kimi_key()
    if not key:
        return _missing("kimi", "Add a Moonshot / Kimi API key to see the balance.", connect=True)
    # The Kimi agent talks to the international platform (USD). A key from the China platform
    # (CNY) is refused there, so that host is the second try.
    last: Optional[httpx.Response] = None
    for host, cur in (("https://api.moonshot.ai", "USD"), ("https://api.moonshot.cn", "CNY")):
        r = httpx.get(f"{host}/v1/users/me/balance",
                      headers={**_UA, "Authorization": f"Bearer {key}"}, timeout=12)
        last = r
        if r.status_code == 200:
            d = (r.json() or {}).get("data") or {}
            amount = _num(d.get("available_balance")) or 0.0
            detail = f"cash {d.get('cash_balance', 0)} · voucher {d.get('voucher_balance', 0)} {cur}"
            return _balance("kimi", "Kimi API", amount, cur, detail=detail, ok=amount > 0)
        if r.status_code not in (401, 403):
            break
    assert last is not None
    return _missing("kimi", _http_error(last, "Moonshot"), connect=last.status_code in (401, 403))


# --- OpenRouter ---------------------------------------------------------------
def _openrouter() -> dict:
    key = _openrouter_key()
    if not key:
        return _missing("openrouter", "Add an OpenRouter API key to see the credit.", connect=True)
    h = {**_UA, "Authorization": f"Bearer {key}"}
    r = httpx.get("https://openrouter.ai/api/v1/key", headers=h, timeout=12)
    if r.status_code != 200:
        return _missing("openrouter", _http_error(r, "OpenRouter"), connect=r.status_code in (401, 403))
    d = (r.json() or {}).get("data") or {}
    used = _num(d.get("usage"))
    limit = _num(d.get("limit"))
    left = _num(d.get("limit_remaining"))
    if left is not None:
        return _balance("openrouter", "OpenRouter", left, "USD", limit=limit, used=used,
                        detail=f"limit on this key: ${limit:.2f}" if limit is not None else "")
    # No limit on the key: the account credit is the useful number. /credits needs a
    # management key, so a normal key falls through to "used" only.
    c = httpx.get("https://openrouter.ai/api/v1/credits", headers=h, timeout=12)
    if c.status_code == 200:
        cd = (c.json() or {}).get("data") or {}
        total, spent = _num(cd.get("total_credits")), _num(cd.get("total_usage"))
        if total is not None and spent is not None:
            return _balance("openrouter", "OpenRouter", total - spent, "USD", limit=total, used=spent,
                            detail="account credit")
    return {**_balance("openrouter", "OpenRouter", 0.0, "USD", used=used,
                       detail="This key has no credit limit. Set a limit on the key at openrouter.ai/keys to see what is left."),
            "balance_unknown": True}


_FETCH: dict[str, tuple[str, Callable[[], dict]]] = {
    "codex": ("codex", _codex),
    "deepseek": ("balance", _deepseek),
    "kimi": ("balance", _kimi),
    "openrouter": ("balance", _openrouter),
}


def read(pid: str, force: bool = False) -> dict:
    """Usage for one account that is not Claude (Claude is ``usage.compute``)."""
    kind, fn = _FETCH[pid]
    return _cached(pid, _TTL[kind], force, fn)

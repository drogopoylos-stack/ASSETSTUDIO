"""What the agent work actually costs, banked turn by turn.

The Dashboard's "Total cost" card reads the asset catalog: image and mesh generations. On this
machine that is $1.80. The same machine's agent work is two orders of magnitude above it — one
project's fifteen subagents came to $46 at list price — and none of it was recorded anywhere,
so the one number on the landing screen was quietly the smallest one.

Every finished turn already carries the answer. The CLI puts `total_cost_usd` on its `result`
event, priced by its own rate card, and the Studio was throwing it away. This banks it.

Two things worth knowing before reading a figure out of here:

* A turn's cost ALREADY INCLUDES the subagents it spawned — `modelUsage` on the same event lists
  every model the turn touched, sub-models included. So a fan-out's price is a BREAKDOWN of the
  turn, never something to add to it. `subagents.cost` exists to attribute, not to total.
* On a Max subscription none of this is billed. It is the API list price of the same work, which
  is what makes two models, two effort levels or two ways of asking comparable.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Optional

from . import fsutil
from .config import DATA_DIR, settings

_FILE = DATA_DIR / "spend.json"
_lock = threading.Lock()
_state: Optional[dict] = None
# Keep a rolling window of days rather than for ever: the card shows today and the total, and an
# unbounded map would be re-read on every dashboard poll.
_KEEP_DAYS = 60


def _load() -> dict:
    global _state
    if _state is None:
        try:
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            _state = d if isinstance(d, dict) else {}
        except Exception:
            _state = {}
        _state.setdefault("projects", {})
        _state.setdefault("days", {})
    return _state


def _save() -> None:
    if _state is None:
        return
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state), encoding="utf-8")
        fsutil.replace(tmp, _FILE)
    except OSError:
        pass


def record(project_id: str, model: str, cost: float, tokens: int = 0) -> None:
    """Bank one finished turn. Called from the result handler, so it runs once per message."""
    try:
        cost = float(cost or 0)
    except (TypeError, ValueError):
        return
    if cost <= 0 or not project_id:
        return
    day = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        s = _load()
        p = s["projects"].setdefault(project_id, {"total": 0.0, "turns": 0, "tokens": 0,
                                                  "by_model": {}, "last": 0.0})
        p["total"] = round(p["total"] + cost, 6)
        p["turns"] += 1
        p["tokens"] += int(tokens or 0)
        m = (model or "unknown").split("[")[0]
        p["by_model"][m] = round(p["by_model"].get(m, 0.0) + cost, 6)
        p["last"] = time.time()
        s["days"][day] = round(s["days"].get(day, 0.0) + cost, 6)
        if len(s["days"]) > _KEEP_DAYS:
            for k in sorted(s["days"])[:-_KEEP_DAYS]:
                s["days"].pop(k, None)
        _save()


def for_project(project_id: str) -> dict:
    with _lock:
        return dict(_load()["projects"].get(project_id) or {})


def totals() -> dict:
    """Everything the card needs, in one read: the total, today, and the biggest spenders."""
    with _lock:
        s = _load()
        projects = s["projects"]
        total = round(sum(float(p.get("total") or 0) for p in projects.values()), 4)
        by_model: dict[str, float] = {}
        for p in projects.values():
            for m, v in (p.get("by_model") or {}).items():
                by_model[m] = round(by_model.get(m, 0.0) + float(v or 0), 6)
        top = sorted(({"project": k, "cost": round(float(v.get("total") or 0), 4),
                       "turns": int(v.get("turns") or 0)}
                      for k, v in projects.items()), key=lambda x: -x["cost"])[:6]
        month = round(sum(float(v or 0) for k, v in s["days"].items()
                          if k.startswith(datetime.now().strftime("%Y-%m"))), 4)
        out = {
            "total": total,
            "today": round(float(s["days"].get(datetime.now().strftime("%Y-%m-%d"), 0.0)), 4),
            "month": month,
            "turns": sum(int(p.get("turns") or 0) for p in projects.values()),
            "projects": len(projects),
            "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])),
            "top": top,
            "days": dict(sorted(s["days"].items())[-14:]),
        }
    # Taken OUTSIDE the lock: `month_to_date` and the token windows below take it themselves, and
    # a plain re-entrant call here would deadlock the dashboard's poll.
    out["cap"] = cap_state(month)
    out["budgets"] = budget_state()
    return out


# ---------------------------------------------------------------------------
# THE GUARD THAT WAS MISSING.
#
# `monthly_spend_cap_usd` existed and was checked in exactly one place — `jobs/queue._preflight`,
# for asset-generation providers whose `kind` is "api". Agent turns, which on this machine are two
# orders of magnitude the larger number, were not covered by anything at all: no cap, no budget, no
# warning. `usage_budgets` looked like the missing piece and was dead config — declared in
# config.py with a comment describing a gauge, and read by nothing anywhere in the tree.
#
# Both are wired here. The money cap BLOCKS, because that is what a cap means. The token budgets
# only INFORM, because that is what the config says they are, and a token count is not a bill.
# ---------------------------------------------------------------------------

def cap_state(month: Optional[float] = None) -> dict:
    """The monthly money guard: what is set, what is spent, and whether a send may proceed."""
    try:
        cap = float(settings.get("monthly_spend_cap_usd", 0) or 0)
    except (TypeError, ValueError):
        cap = 0.0
    spent = month_to_date() if month is None else month
    return {"limit": round(cap, 4), "spent": round(float(spent or 0), 4),
            "enabled": cap > 0, "over": bool(cap > 0 and spent >= cap)}


def month_to_date() -> float:
    """Dollars banked since the 1st of this month — the window the cap guards."""
    with _lock:
        s = _load()
        prefix = datetime.now().strftime("%Y-%m")
        return round(sum(float(v or 0) for k, v in s["days"].items() if k.startswith(prefix)), 4)


def over_cap() -> str:
    """A refusal message when the month's agent spend has reached the cap, else ''.

    Called on the send path, so it covers every engine the Studio can drive — Claude, Codex,
    DeepSeek and the alternate providers all reach the session layer through the same door."""
    st = cap_state()
    if not st["over"]:
        return ""
    return (f"Monthly spend cap reached (${st['spent']:.2f} of ${st['limit']:.2f}). "
            "Raise or clear it in Settings → Cost, then send again.")


def budget_state() -> list[dict]:
    """The token windows from `usage_budgets`, as used/limit/percent, for the gauge.

    Read from the turn ledger rather than kept as a running counter, so it survives a restart and
    cannot drift from the turns it is describing. Unknown/absent keys are simply not shown."""
    try:
        budgets = settings.get("usage_budgets") or {}
    except Exception:
        budgets = {}
    if not isinstance(budgets, dict) or not budgets:
        return []
    windows = {"session": 5 * 3600.0, "daily": 24 * 3600.0, "weekly": 7 * 24 * 3600.0}
    out = []
    for key, seconds in windows.items():
        try:
            limit = int(float(budgets.get(key) or 0))
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            continue
        used = _window_tokens(seconds)
        out.append({"key": key, "label": {"session": "5h session", "daily": "Today",
                                          "weekly": "This week"}[key],
                    "used": used, "limit": limit,
                    "percent": round(min(100.0, 100.0 * used / limit), 1)})
    return out


def _window_tokens(seconds: float) -> int:
    """Output tokens banked in the last `seconds`. A missing ledger is zero, never an exception."""
    try:
        from . import turns
        return turns.tokens_since(seconds)
    except Exception:
        return 0

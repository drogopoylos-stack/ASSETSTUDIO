"""The Dashboard's answer to "what have I got left, and where did it go?"

The landing screen used to lead with dollars. On a fixed subscription those dollars are a list
price for work that was never billed: a turn on a Max plan or on a ChatGPT Plus plan costs
nothing extra, so a big number there means nothing a user can act on. What is actually worth
knowing is when each plan renews, how much of the current window is spent, which models did the
work, and which projects got it.

Two providers only - Claude and Codex. Those are the two subscriptions; everything else the
Studio can talk to is billed per token and belongs in the usage bar, not here.

WHERE EACH NUMBER COMES FROM
  plan, windows   Claude: the OAuth usage endpoint (`usage.compute`). Codex: the app-server's
                  `account/rateLimits/read` (`usage_accounts.read`). Both are already cached by
                  those modules, so polling this costs one dict copy most of the time.
  renewal         Codex states it: the id_token on disk carries
                  `chatgpt_subscription_active_until`, so it is read offline, with no request and
                  no app-server. Anthropic publishes NO billing date on any endpoint the CLI is
                  allowed to read - `subscriptionType` is the whole story - so Claude's renewal
                  is a value the user types once into Settings (`claude_renews_on`).
  work            `data/turns.jsonl` - every finished turn with its model, project and tokens.
                  Tokens rather than dollars: the tokens were measured, the dollars are a guess
                  off a rate card that the plan does not charge against.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import turns
from .config import settings

# A day in seconds, named because it appears in every window below.
DAY = 86400.0


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _parse_iso(s) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _month_day(y: int, m: int, day: int) -> date:
    """`day` of that month, clamped to the length of the month.

    A plan billed on the 31st renews on the 28th of February, not on the 3rd of March.
    """
    for d in (day, 31, 30, 29, 28):
        if d > day:
            continue
        try:
            return date(y, m, d)
        except ValueError:
            continue
    return date(y, m, 28)


def _add_month(d: date) -> date:
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return _month_day(y, m, d.day)


def _renewal_from_setting(value) -> float:
    """The next renewal, from what the user typed. 0.0 when nothing is set.

    Two forms are accepted, because both are natural to type: a day of the month ("15") and a
    full date ("2026-11-15"). A date that is already past is rolled forward a month at a time, so
    the value keeps answering correctly without the user editing it every month.
    """
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    today = date.today()

    if raw.isdigit() and len(raw) <= 2:
        nxt = _month_day(today.year, today.month, max(1, min(31, int(raw))))
        if nxt <= today:
            nxt = _add_month(nxt)
        return datetime(nxt.year, nxt.month, nxt.day, tzinfo=timezone.utc).timestamp()

    ts = _parse_iso(raw)
    if not ts:
        return 0.0
    d = datetime.fromtimestamp(ts, timezone.utc).date()
    guard = 0
    while d <= today and guard < 240:
        d = _add_month(d)
        guard += 1
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def _renewal(ts: float, source: str) -> dict:
    if not ts:
        return {"renews_at": None, "renews_in_days": None, "renewal_source": ""}
    return {"renews_at": _iso(ts),
            "renews_in_days": max(0, round((ts - time.time()) / DAY)),
            "renewal_source": source}


# ---------------------------------------------------------------------------
# Codex: the plan and its renewal, read off disk
# ---------------------------------------------------------------------------
_OPENAI_AUTH = "https://api.openai.com/auth"


def codex_claims() -> dict:
    """The auth claims in the Codex id_token: plan type and subscription dates.

    Offline and cheap - no request, and no Codex app-server. CODEX_HOME is honoured because that
    is the variable the Codex CLI itself reads.
    """
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    try:
        data = json.loads((home / "auth.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    token = ((data.get("tokens") or {}).get("id_token")) or ""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    body = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        return {}
    auth = claims.get(_OPENAI_AUTH) or {}
    return {
        "email": claims.get("email") or "",
        "plan_type": auth.get("chatgpt_plan_type") or "",
        "active_until": auth.get("chatgpt_subscription_active_until") or "",
        "active_start": auth.get("chatgpt_subscription_active_start") or "",
    }


# ---------------------------------------------------------------------------
# the two subscriptions
# ---------------------------------------------------------------------------
def _claude(force: bool = False) -> dict:
    from . import usage
    d = usage.compute(force=force)
    plan = (d.get("plan") or "").strip()
    label = "Claude" if not plan or plan.lower() == "claude" else f"Claude {plan}"
    row = {
        "id": "claude",
        "name": "Claude Code",
        "plan": label,
        "available": bool(d.get("available")),
        "windows": d.get("windows") or [],
        "extra_usage": d.get("extra_usage"),
        "stale": bool(d.get("stale")),
        "note": d.get("note") or d.get("error") or "",
        # Anthropic does not publish the billing date. Say so, rather than inventing one.
        "renewal_hint": "Anthropic does not publish a renewal date. Set yours in Settings.",
        "setting": "claude_renews_on",
    }
    row.update(_renewal(_renewal_from_setting(settings.get("claude_renews_on")), "you"))
    return row


def _codex(force: bool = False) -> dict:
    claims = codex_claims()
    plan_type = (claims.get("plan_type") or "").strip()
    row = {
        "id": "codex",
        "name": "Codex",
        "plan": f"ChatGPT {plan_type.capitalize()}" if plan_type else "Codex",
        "available": False,
        "windows": [],
        "extra_usage": None,
        "stale": False,
        "note": "",
        "renewal_hint": "",
        "setting": "",
        "email": claims.get("email") or "",
    }
    row.update(_renewal(_parse_iso(claims.get("active_until")), "account"))
    try:
        from . import usage_accounts
        d = usage_accounts.read("codex", force=force)
    except Exception as e:
        row["note"] = f"Codex usage unavailable: {e}"
        return row
    # The plan from the app-server wins: it is the live answer, the token on disk is a snapshot.
    if d.get("plan"):
        row["plan"] = d["plan"]
    row["available"] = bool(d.get("available"))
    row["windows"] = d.get("windows") or []
    row["stale"] = bool(d.get("stale"))
    row["note"] = d.get("note") or d.get("error") or ""
    return row


def subscriptions(force: bool = False) -> list[dict]:
    """Claude and Codex, in that order. One reader that fails never hides the other."""
    out: list[dict] = []
    for pid, fn in (("claude", _claude), ("codex", _codex)):
        try:
            out.append(fn(force=force))
        except Exception as e:                       # a panel is never worth a 500
            out.append({"id": pid, "name": pid.capitalize(), "plan": "", "available": False,
                        "windows": [], "extra_usage": None, "stale": False,
                        "note": f"could not read: {e}", "renewal_hint": "", "setting": "",
                        "renews_at": None, "renews_in_days": None, "renewal_source": ""})
    return out


# ---------------------------------------------------------------------------
# the work: which models, which projects, which days
# ---------------------------------------------------------------------------
def _split_harness(pid: str) -> tuple[str, str]:
    """A harness runs the same folder under another provider and banks its turns under its own
    id ("deepseek-harness--d--KAPOW"). The folder is what the user recognises, so the prefix
    comes off and is shown as a tag beside it."""
    if "-harness--" in pid:
        head, _, tail = pid.partition("-harness--")
        return tail, head
    return pid, ""


def _names() -> dict:
    """project id (lowercased) -> {name, path}, from the cheap project index."""
    try:
        from . import mission
        return {str(r.get("id", "")).lower(): {"name": r.get("name") or r.get("id") or "",
                                               "path": r.get("path") or ""}
                for r in mission.project_index()}
    except Exception:
        return {}


def _pretty(pid: str, names: dict) -> str:
    """The folder name when it can be resolved, otherwise the id made readable.

    The id is a path with every separator turned into a dash, so the folder name cannot be
    recovered by splitting it - "d--Asset-Studio" could be "Asset Studio" or "Asset-Studio". The
    index knows the real cwd; this only has to be sane for a project the index has never seen.
    """
    hit = (names.get(pid.lower()) or {}).get("name")
    if hit:
        return hit
    tail = pid.split("--")[-1]
    return tail.replace("-", " ").strip() or pid


def _unique(projects: list[dict], names: dict) -> None:
    """Two folders can share a name. Add the parent folder to the ones that do.

    "Asset Studio" twice in a list of projects is a bug report waiting to happen, and the second
    one really is a different folder - a copy of the tree under another workspace.
    """
    seen: dict[str, int] = {}
    for p in projects:
        seen[p["name"]] = seen.get(p["name"], 0) + 1
    for p in projects:
        if seen.get(p["name"], 0) < 2:
            continue
        path = (names.get(p["id"].lower()) or {}).get("path") or ""
        # A folder at the root of a drive has no parent name - say which drive instead.
        parent = (Path(path).parent.name or Path(path).drive) if path else ""
        p["name"] = f"{p['name']} ({parent})" if parent else f"{p['name']} ({p['id']})"


def work(days: int = 7, top: int = 8) -> dict:
    """Turns, tokens, models, projects and a per-day bar, over the last `days` days."""
    days = max(1, min(90, int(days or 7)))
    rows = turns.since(days * DAY)
    names = _names()

    by_model: dict[str, dict] = {}
    by_project: dict[str, dict] = {}
    by_effort: dict[str, int] = {}
    by_day: dict[str, dict] = {}
    total_turns = 0
    total_tokens = 0

    for r in rows:
        tokens = int(r.get("tokens") or 0)
        ts = float(r.get("ts") or 0)
        total_turns += 1
        total_tokens += tokens

        model = (r.get("model") or "unknown").strip() or "unknown"
        m = by_model.setdefault(model, {"model": model, "turns": 0, "tokens": 0, "last": 0.0})
        m["turns"] += 1
        m["tokens"] += tokens
        m["last"] = max(m["last"], ts)

        pid, harness = _split_harness((r.get("project") or "unknown").strip() or "unknown")
        p = by_project.setdefault(pid, {"id": pid, "name": _pretty(pid, names), "turns": 0,
                                        "tokens": 0, "last": 0.0, "harness": ""})
        p["turns"] += 1
        p["tokens"] += tokens
        p["last"] = max(p["last"], ts)
        if harness and harness not in p["harness"]:
            p["harness"] = (p["harness"] + " " + harness).strip()

        effort = (r.get("effort") or "default").strip().lower() or "default"
        by_effort[effort] = by_effort.get(effort, 0) + 1

        key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""
        if key:
            d = by_day.setdefault(key, {"day": key, "turns": 0, "tokens": 0})
            d["turns"] += 1
            d["tokens"] += tokens

    def share(n: int) -> float:
        return round(100.0 * n / total_turns, 1) if total_turns else 0.0

    models = sorted(by_model.values(), key=lambda x: (-x["turns"], x["model"]))
    projects = sorted(by_project.values(), key=lambda x: (-x["turns"], x["name"]))
    for m in models:
        m["share"] = share(m["turns"])
    for p in projects:
        p["share"] = share(p["turns"])
    _unique(projects, names)

    # Every day in the window, including the quiet ones - a bar chart with holes in it reads as
    # missing data rather than as a day off.
    today = date.today()
    series = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        series.append(by_day.get(key) or {"day": key, "turns": 0, "tokens": 0})

    return {
        "days": days,
        "turns": total_turns,
        "tokens": total_tokens,
        "models": models[:top],
        "model_count": len(models),
        "projects": projects[:top],
        "project_count": len(projects),
        "efforts": [{"effort": k, "turns": v} for k, v in
                    sorted(by_effort.items(), key=lambda kv: -kv[1])],
        "by_day": series,
        "busiest_day": max(series, key=lambda d: d["turns"]) if series else None,
    }


def dashboard(days: int = 7, force: bool = False) -> dict:
    """Everything the Dashboard's plan panel needs, in one request."""
    return {
        "subscriptions": subscriptions(force=force),
        "work": work(days=days),
        "generated_at": time.time(),
    }

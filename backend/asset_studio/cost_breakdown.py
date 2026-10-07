"""What the money actually bought, per tool and per kind of round trip.

WHY THIS EXISTS. The Dashboard can say a project cost $164 and nothing about why. The number that
changes behaviour is the one underneath it: on this machine a single session's 1,552 round trips
cost $316 between them, of which **Bash was 455 calls and $101 (32%)**, pure deliberation before an
action was $88 (28%), and the phases panel's bookkeeping — TaskCreate/TaskUpdate/TaskStop — was 104
calls and $15.65 (5%). None of that is visible from a project total, and all of it is arithmetic on
data already on disk.

THE MODEL, IN ONE SENTENCE. The CLI is stateless per request: every round trip re-sends the whole
conversation, so the price of "calling a tool" is the price of the assistant message that called it
— its cache read (the context), its cache write (what the round trip added) and its output. Summing
that by tool name answers "what did the money buy" exactly.

PRICED FROM THE RATE CARD, NOT FROM THE CLI. The CLI's `total_cost_usd` is a running total per
process (see `spend.py`), which is what made the old Dashboard figure 2.34x too high. The transcript
carries the per-request token counts, so this prices each request itself with `pricing.cost`. The
two will not agree to the cent, and that is expected: one is the vendor's client-side estimate of a
process, the other is arithmetic over the tokens that were actually sent.

A model with no rate is reported with basis "unknown" and its tokens are still counted, so a Codex
or DeepSeek session shows up as work rather than as $0 (see `pricing.cost_with_basis`).
"""
from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from pathlib import Path

from . import pricing

# Parsing a 129 MB transcript is seconds of work, and the panel polls while it is open. Keyed on
# (path, mtime, size) for the same reason `turns.py` is: a cache that cannot go stale.
_cache: dict = {}
_lock = threading.Lock()


def _price(msg_usage: dict, model: str) -> tuple[float, str]:
    """(dollars, basis) for one request's `usage`, in the Studio's own key names."""
    u = {"input": msg_usage.get("input_tokens") or 0,
         "output": msg_usage.get("output_tokens") or 0,
         "cache_read": msg_usage.get("cache_read_input_tokens") or 0,
         "cache_write": msg_usage.get("cache_creation_input_tokens") or 0}
    return pricing.cost_with_basis(model, u)


def _parse(path: Path) -> dict:
    by_tool_cost: dict[str, float] = defaultdict(float)
    by_tool_calls: Counter = Counter()
    by_kind_cost = {"tool action": 0.0, "deliberation": 0.0, "final answer": 0.0}
    by_kind_msgs = Counter()
    by_model_cost: dict[str, float] = defaultdict(float)
    by_model_basis: dict[str, str] = {}
    ctx: list[int] = []
    model_msgs: Counter = Counter()
    pending: list[dict] = []          # assistant rows, so a no-tool row can see what follows it

    def classify() -> None:
        """Attribute each assistant row once the NEXT row is known.

        Claude Code writes thinking/text and the tool call as separate assistant records, so
        "messages with no tool call" is not the same as idle chatter — most of them are the
        deliberation on the way to the action in the next record. Counting them as chatter would
        overstate the overhead; counting them as tool calls would hide it. This splits them."""
        for i, m in enumerate(pending):
            nxt = pending[i + 1] if i + 1 < len(pending) else None
            _add(m, "tool action" if m["tool"]
                 else ("deliberation" if (nxt and nxt["tool"]) else "final answer"))

    def _add(m: dict, kind: str) -> None:
        by_kind_cost[kind] = by_kind_cost.get(kind, 0.0) + m["cost"]
        by_kind_msgs[kind] += 1
        if m["tools"]:
            share = m["cost"] / len(m["tools"])
            for t in m["tools"]:
                by_tool_cost[t] += share
                by_tool_calls[t] += 1

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "assistant":
                    continue
                msg = row.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                model = str(msg.get("model") or "?")
                cost, basis = _price(u, model)
                by_model_cost[model] += cost
                by_model_basis[model] = basis
                model_msgs[model] += 1
                ctx.append((int(u.get("input_tokens") or 0)
                            + int(u.get("cache_read_input_tokens") or 0)
                            + int(u.get("cache_creation_input_tokens") or 0)))
                blocks = msg.get("content") or []
                tools = [b.get("name") or "?" for b in blocks
                         if isinstance(b, dict) and b.get("type") == "tool_use"]
                pending.append({"cost": cost, "tool": bool(tools), "tools": tools})
    except OSError:
        return {}

    classify()
    total = sum(by_kind_cost.values())
    ctx.sort()

    def pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    buckets = [(0, 100_000), (100_000, 200_000), (200_000, 300_000), (300_000, 400_000),
               (400_000, 500_000), (500_000, 700_000), (700_000, 10**12)]
    spread = []
    for lo, hi in buckets:
        n = sum(1 for c in ctx if lo <= c < hi)
        spread.append({"label": (f"{lo // 1000}k–{hi // 1000}k" if hi < 10**12 else f"{lo // 1000}k+"),
                       "messages": n, "percent": pct(n, len(ctx))})

    return {
        "round_trips": len(pending),
        "total": round(total, 2),
        "by_tool": sorted(({"tool": k, "calls": by_tool_calls[k], "cost": round(v, 2),
                            "percent": pct(v, total),
                            "per_call": round(v / by_tool_calls[k], 3) if by_tool_calls[k] else 0.0}
                           for k, v in by_tool_cost.items()), key=lambda x: -x["cost"]),
        "by_kind": sorted(({"kind": k, "messages": by_kind_msgs[k], "cost": round(v, 2),
                            "percent": pct(v, total)} for k, v in by_kind_cost.items()),
                          key=lambda x: -x["cost"]),
        "by_model": sorted(({"model": k, "messages": model_msgs[k], "cost": round(v, 2),
                             "basis": by_model_basis.get(k, "unknown")}
                            for k, v in by_model_cost.items()), key=lambda x: -x["cost"]),
        "context": {
            "messages": len(ctx),
            "median": ctx[len(ctx) // 2] if ctx else 0,
            "p90": ctx[int(len(ctx) * 0.9)] if ctx else 0,
            "max": ctx[-1] if ctx else 0,
            "spread": spread,
        },
    }


def for_project(project_id: str, calls: int = 4) -> dict:
    """Cost anatomy for a project's most recent `calls` transcripts, newest last.

    Several transcripts are read because a Studio conversation is not one file: a respawn resumes
    the same session id (one file), but a NEW conversation is a new file, and a project's spend is
    usually spread across both."""
    from . import mission
    pid = str(project_id or "")
    if not pid:
        return {"ok": False, "error": "no project"}
    try:
        _root, pdir, _bare = mission.project_dir(pid)
    except Exception as e:
        return {"ok": False, "error": f"could not resolve that project: {e}"}
    if not pdir or not Path(pdir).exists():
        return {"ok": True, "round_trips": 0, "total": 0.0, "by_tool": [], "by_kind": [],
                "by_model": [], "context": {"messages": 0, "median": 0, "p90": 0, "max": 0,
                                            "spread": []},
                "note": "no transcript for this project yet"}

    files = sorted(Path(pdir).glob("*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, int(calls or 1))]
    out = {"round_trips": 0, "total": 0.0, "by_tool": [], "by_kind": [], "by_model": [],
           "context": {"messages": 0, "median": 0, "p90": 0, "max": 0, "spread": []}}

    agg_tool: dict[str, dict] = {}
    agg_kind: dict[str, dict] = {}
    agg_model: dict[str, dict] = {}
    agg_spread: dict[str, int] = {}
    ctx_all: list[int] = []

    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        key = (str(f), st.st_mtime, st.st_size)
        with _lock:
            got = _cache.get(key)
        if got is None:
            got = _parse(f)
            with _lock:
                _cache.clear()          # one project's panel at a time; never grows unbounded
                _cache[key] = got
        if not got:
            continue
        out["round_trips"] += got["round_trips"]
        out["total"] = round(out["total"] + got["total"], 2)
        for row in got["by_tool"]:
            a = agg_tool.setdefault(row["tool"], {"tool": row["tool"], "calls": 0, "cost": 0.0})
            a["calls"] += row["calls"]
            a["cost"] = round(a["cost"] + row["cost"], 2)
        for row in got["by_kind"]:
            a = agg_kind.setdefault(row["kind"], {"kind": row["kind"], "messages": 0, "cost": 0.0})
            a["messages"] += row["messages"]
            a["cost"] = round(a["cost"] + row["cost"], 2)
        for row in got["by_model"]:
            a = agg_model.setdefault(row["model"], {"model": row["model"], "messages": 0,
                                                    "cost": 0.0, "basis": row["basis"]})
            a["messages"] += row["messages"]
            a["cost"] = round(a["cost"] + row["cost"], 2)
        for b in got["context"]["spread"]:
            agg_spread[b["label"]] = agg_spread.get(b["label"], 0) + b["messages"]

    total = out["total"]
    for a in agg_tool.values():
        a["percent"] = round(100.0 * a["cost"] / total, 1) if total else 0.0
        a["per_call"] = round(a["cost"] / a["calls"], 3) if a["calls"] else 0.0
    for a in agg_kind.values():
        a["percent"] = round(100.0 * a["cost"] / total, 1) if total else 0.0
    for a in agg_model.values():
        a["percent"] = round(100.0 * a["cost"] / total, 1) if total else 0.0
    msgs = sum(agg_spread.values()) or 1
    for label in agg_spread:
        agg_spread[label] = {"label": label, "messages": agg_spread[label],
                             "percent": round(100.0 * agg_spread[label] / msgs, 1)}

    out["by_tool"] = sorted(agg_tool.values(), key=lambda x: -x["cost"])
    out["by_kind"] = sorted(agg_kind.values(), key=lambda x: -x["cost"])
    out["by_model"] = sorted(agg_model.values(), key=lambda x: -x["cost"])
    out["context"] = {"files": len(files), "messages": msgs,
                      "spread": [agg_spread[k] for k in agg_spread]}
    out["ok"] = True
    return out

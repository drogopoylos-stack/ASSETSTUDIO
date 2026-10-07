"""How fast each model actually generates — tokens per second, per workspace.

The Studio can run Opus, Fable, Kimi, Qwen or anything added in Settings → Models, at effort
levels from low to max. They differ a lot in speed, and nothing showed it. This records every
finished turn so the difference is a number rather than a feeling.

The metric that matters is GENERATION time, not wall-clock. A turn that thinks for 8s, runs a
2-minute test, then writes for 4s took 2m12s, but the model was only producing tokens for 12 of
those seconds. Dividing by wall-clock would make a model look slow because its tools were slow —
useless for comparing models. So the reader clocks only the message_start..message_stop windows,
and `tps` is output tokens over that time. `wall_tps` is kept alongside for the "how long did
this actually take me" view.

One JSON line per turn in ``data/speed.jsonl``, trimmed when it grows. Small enough to keep
forever in practice, and readable with any text editor.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from . import fsutil
from .config import DATA_DIR

STORE = DATA_DIR / "speed.jsonl"
_lock = threading.Lock()
_MAX_LINES = 4000            # ~1 MB; trimmed to the newest half when exceeded
_MIN_TOKENS = 40             # below this the timing is noise, not a measurement
_MIN_GEN_S = 0.4


def record(project_id: str, model: str, effort: str, tokens: int,
           gen_s: float, wall_s: float, messages: int = 0) -> Optional[dict]:
    """Log one finished turn. Returns the row, or None if it was too small to mean anything."""
    if tokens < _MIN_TOKENS or gen_s < _MIN_GEN_S:
        return None
    row = {
        "ts": round(time.time(), 1),
        "project": project_id,
        "model": (model or "").strip() or "unknown",
        "effort": (effort or "default").strip().lower(),
        "tokens": int(tokens),
        "gen_s": round(gen_s, 2),
        "wall_s": round(wall_s, 2),
        "messages": int(messages),
        "tps": round(tokens / gen_s, 1),
        "wall_tps": round(tokens / wall_s, 1) if wall_s > 0 else 0.0,
    }
    try:
        with _lock:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            with open(STORE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            _trim_locked()
    except OSError:
        pass
    return row


def _trim_locked() -> None:
    try:
        if STORE.stat().st_size < 900_000:
            return
        lines = STORE.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= _MAX_LINES:
            return
        keep = lines[-(_MAX_LINES // 2):]
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        fsutil.replace(tmp, STORE)
    except OSError:
        pass


def _rows(project_id: str = "", limit: int = 400) -> list[dict]:
    try:
        raw = STORE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(raw):          # newest first, stop early
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if project_id and r.get("project") != project_id:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _short(model: str) -> str:
    m = (model or "").split("[")[0]
    return (m.replace("claude-", "")
             .replace("-20250929", "").replace("-20251001", "")
             .strip("-") or "unknown")


def summary(project_id: str = "", limit: int = 400) -> dict:
    """Recent turns plus an average per (model, effort) — the comparison the user is after.

    Averages are token-weighted: a 3000-token turn says more about a model's speed than a
    60-token one, and a plain mean of per-turn rates lets the tiny turns dominate.
    """
    rows = _rows(project_id, limit)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (_short(str(r.get("model", ""))), str(r.get("effort", "default")))
        g = groups.setdefault(key, {"model": key[0], "effort": key[1], "turns": 0,
                                    "tokens": 0, "gen_s": 0.0, "best": 0.0})
        g["turns"] += 1
        g["tokens"] += int(r.get("tokens", 0))
        g["gen_s"] += float(r.get("gen_s", 0.0))
        g["best"] = max(g["best"], float(r.get("tps", 0.0)))
    out = []
    for g in groups.values():
        if g["gen_s"] <= 0:
            continue
        out.append({**g, "gen_s": round(g["gen_s"], 1),
                    "tps": round(g["tokens"] / g["gen_s"], 1),
                    "best": round(g["best"], 1)})
    out.sort(key=lambda g: g["tps"], reverse=True)
    return {
        "by_model": out,
        "recent": [{**r, "model_short": _short(str(r.get("model", "")))} for r in rows[:25]],
        "turns": len(rows),
    }

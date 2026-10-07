"""What a run would cost at list price, from the CLI's own rate card.

The token counts the Studio already shows are not a quantity anyone has intuition for. "44,781,325
tokens across 15 agents" says nothing about whether that fan-out was worth running; "$312" does.

The rates are lifted verbatim from the model catalog baked into the Claude Code binary
(2.1.280) — the same table the CLI bills `/cost` from — so a number here and a number in the CLI
cannot drift apart by guesswork. See the `claude-cli-model-catalog` memory for how to re-read it
when a model is added.

IMPORTANT, and the UI must say so: on a Max subscription you do not pay this. The plan is a flat
fee and the usage meters are the real limit. This is the API list price of the same work, which is
what makes two agents, two models or two effort levels comparable.
"""
from __future__ import annotations

from .config import settings

# $ per million tokens. cache_write_5m / cache_write_1h are the two TTLs the API bills
# differently; cache_read is the cheap one, which is why a warm agent costs so little.
TIERS: dict[str, dict[str, float]] = {
    "tier_2_10":  {"input": 2.0,  "output": 10.0, "cw5m": 2.5,   "cw1h": 4.0,  "read": 0.2},
    "tier_3_15":  {"input": 3.0,  "output": 15.0, "cw5m": 3.75,  "cw1h": 6.0,  "read": 0.3},
    "tier_5_25":  {"input": 5.0,  "output": 25.0, "cw5m": 6.25,  "cw1h": 10.0, "read": 0.5},
    "tier_15_75": {"input": 15.0, "output": 75.0, "cw5m": 18.75, "cw1h": 30.0, "read": 1.5},
    "tier_10_50": {"input": 10.0, "output": 50.0, "cw5m": 12.5,  "cw1h": 20.0, "read": 1.0},
    # Fable 5.1 and Mythos 5.1 read from cache at a QUARTER of what Fable 5 charges. On a long
    # agent, which is nearly all cache read, that is the difference between the two models.
    "tier_10_50_cr025": {"input": 10.0, "output": 50.0, "cw5m": 12.5, "cw1h": 20.0, "read": 0.25},
    # Opus 5.5 (2.1.280, `tier_4_20_cache_read_0_20`): cheaper than Opus 5 on every line, and less
    # than half on cache reads — the line a long agent spends most of its money on.
    "tier_4_20_cr020": {"input": 4.0, "output": 20.0, "cw5m": 5.0, "cw1h": 8.0, "read": 0.2},
    "haiku_35":   {"input": 0.8,  "output": 4.0,  "cw5m": 1.0,   "cw1h": 1.6,  "read": 0.08},
    "haiku_45":   {"input": 1.0,  "output": 5.0,  "cw5m": 1.25,  "cw1h": 2.0,  "read": 0.1},
}

# Longest match wins, so `claude-opus-4-8` is not caught by the `claude-opus-4` prefix of an
# older tier. Ids carry a date suffix in the transcript (`claude-haiku-4-5-20251001`), so this
# is prefix matching on purpose.
_MODEL_TIER: list[tuple[str, str]] = [
    ("claude-fable-5-1", "tier_10_50_cr025"),
    ("claude-mythos-5-1", "tier_10_50_cr025"),
    ("claude-fable-5", "tier_10_50"),
    ("claude-mythos-5", "tier_10_50"),
    ("claude-opus-5-5", "tier_4_20_cr020"),
    ("claude-opus-5", "tier_5_25"),
    ("claude-opus-4-8", "tier_5_25"),
    ("claude-opus-4-7", "tier_5_25"),
    ("claude-opus-4-6", "tier_5_25"),
    ("claude-opus-4-5", "tier_5_25"),
    ("claude-opus-4-1", "tier_15_75"),
    ("claude-opus-4-0", "tier_15_75"),
    ("claude-sonnet-5", "tier_2_10"),
    ("claude-sonnet-4-6", "tier_3_15"),
    ("claude-sonnet-4-5", "tier_3_15"),
    ("claude-sonnet-4-0", "tier_3_15"),
    ("claude-3-7-sonnet", "tier_3_15"),
    ("claude-3-5-sonnet", "tier_3_15"),
    ("claude-haiku-4-5", "haiku_45"),
    ("claude-3-5-haiku", "haiku_35"),
]


# DeepSeek, $ per million tokens at PEAK, read off https://api-docs.deepseek.com/quick_start/pricing
# on 2026-10-07. DeepSeek is the one engine here that bills every token to a topped-up balance, and
# without these rows its turns were banked at $0 — so `monthly_spend_cap_usd` could never stop it.
#
# There is no cache WRITE line: a miss is billed as ordinary input. Off-peak is exactly half of peak.
# Peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday. Chinese public holidays are off-peak
# too; they are not modelled, so a holiday turn is counted at the peak price — the cap trips early,
# never late.
#
# `deepseek-v4-pro` is its own model (DeepSeek-V4-Pro-0813) with its own price. The legacy Flash ids
# (`deepseek-v4-flash`, `deepseek-v4-flash-vision-exp`) are served AND billed as `deepseek-flash`.
_DEEPSEEK: list[tuple[str, dict[str, float]]] = [
    ("deepseek-v4-pro", {"input": 1.32, "read": 0.044, "output": 3.96}),
    ("deepseek-", {"input": 0.30, "read": 0.006, "output": 1.20}),
]
_DEEPSEEK_PEAK_UTC = ((1, 4), (6, 10))


def _deepseek_rate(model: str, at: float | None = None) -> dict[str, float]:
    """The DeepSeek rate for this model at this moment (peak or off-peak), or {} for another model."""
    import time as _time
    m = (model or "").split("[")[0].strip().lower()
    for prefix, rate in _DEEPSEEK:
        if m.startswith(prefix):
            t = _time.gmtime(_time.time() if at is None else at)
            peak = t.tm_wday < 5 and any(a <= t.tm_hour < b for a, b in _DEEPSEEK_PEAK_UTC)
            return rate if peak else {k: v / 2 for k, v in rate.items()}
    return {}


def deepseek_cost(model: str, usage: dict, at: float | None = None) -> float:
    """Dollars for one DeepSeek bundle. `input` is the cache MISS count, `cache_read` the hits —
    the same disjoint split the harness reports."""
    r = _deepseek_rate(model, at)
    if not r:
        return 0.0
    u = usage or {}
    total = (float(u.get("input") or 0) * r["input"]
             + float(u.get("cache_read") or 0) * r["read"]
             + float(u.get("output") or 0) * r["output"])
    return round(total / 1_000_000, 6)


def tier_for(model: str) -> str:
    """The rate-card key for a model id, or '' when it is not a Claude model we have rates for
    (a Kimi/Qwen/OpenRouter session, say — those bill on the provider's own account)."""
    m = (model or "").split("[")[0].strip().lower()
    if m.startswith("us.anthropic.") or m.startswith("anthropic."):
        m = "claude-" + m.split("claude-", 1)[1] if "claude-" in m else m
    for prefix, tier in _MODEL_TIER:
        if m.startswith(prefix):
            return tier
    return ""


def cost(model: str, usage: dict) -> float:
    """Dollars for one bundle of usage. `usage` takes the Studio's own key names
    (input/output/cache_read/cache_write) and, when the transcript recorded the split,
    cache_write_1h — the 1-hour TTL is billed at 2x the 5-minute one, so a session using it
    would otherwise be under-counted by a third of its cache writes.
    """
    t = TIERS.get(tier_for(model))
    if not t:
        return 0.0
    u = usage or {}
    w1h = float(u.get("cache_write_1h") or 0)
    w5m = max(0.0, float(u.get("cache_write") or 0) - w1h)
    total = (float(u.get("input") or 0) * t["input"]
             + float(u.get("output") or 0) * t["output"]
             + float(u.get("cache_read") or 0) * t["read"]
             + w5m * t["cw5m"]
             + w1h * t["cw1h"])
    return round(total / 1_000_000, 6)


def _override(model: str) -> dict:
    """A rate the user supplied for a model this table does not know (see `pricing_overrides`)."""
    try:
        table = settings.get("pricing_overrides") or {}
    except Exception:
        return {}
    if not isinstance(table, dict):
        return {}
    m = (model or "").split("[")[0].strip().lower()
    best = {}
    for key, rate in table.items():
        k = str(key).split("[")[0].strip().lower()
        if k and m.startswith(k) and isinstance(rate, dict) and len(k) > len(best.get("_k", "")):
            best = {**rate, "_k": k}
    return best


def cost_with_basis(model: str, usage: dict) -> tuple[float, str]:
    """(dollars, how those dollars were arrived at) — one of ``list``/``override``/``unknown``.

    WHY THE BASIS MATTERS MORE THAN THE NUMBER. `cost()` above returns 0.0 for any model the rate
    card does not recognise, and 0.0 is indistinguishable from "this turn was free". Codex, the
    DeepSeek rows and the vision judges all land in that hole, so their spend was not merely
    unmeasured — it was being reported as nothing. The CLI's own `modelUsage` carries a
    `costBasis` field for exactly this distinction and the Studio never read it.

    So an unrated model comes back as ``unknown`` and the caller banks its TOKENS anyway: the turn
    is visible, and the money is honestly marked absent rather than shown as $0.00. A rate typed
    into `pricing_overrides` (settings) turns it into ``override``.
    """
    u = usage or {}
    if not (float(u.get("input") or 0) or float(u.get("output") or 0)
            or float(u.get("cache_read") or 0) or float(u.get("cache_write") or 0)):
        return 0.0, "none"
    if TIERS.get(tier_for(model)):
        return cost(model, u), "list"
    ov = _override(model)
    # A rate the user typed in still wins: they may be on a discount or a different region.
    if not ov and _deepseek_rate(model):
        return deepseek_cost(model, u), "list"
    if ov:
        total = (float(u.get("input") or 0) * float(ov.get("input") or 0)
                 + float(u.get("output") or 0) * float(ov.get("output") or 0)
                 + float(u.get("cache_read") or 0) * float(ov.get("cache_read") or 0)
                 + float(u.get("cache_write") or 0)
                 * float(ov.get("cache_write") or ov.get("input") or 0))
        return round(total / 1_000_000, 6), "override"
    return 0.0, "unknown"

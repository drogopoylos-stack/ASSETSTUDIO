"""Which Claude models this account can actually run, learned from the turns it already runs.

CLI 2.1.257 put `claude-fable-5-1` in its baked model catalog before the API served it to
every account. Picking it there loses the turn:

    There's an issue with the selected model (claude-fable-5-1). It may not exist or you
    may not have access to it. Run --model to pick a different model.

A hardcoded picker cannot know that. The answer is per account, it changes without a CLI
release, and there is no endpoint that lists it — the `cedar_lagoon` entitlement flags in
`~/.claude.json` said `claude-fable: true` on this machine while `claude-fable-5-1` was
still refused, so the flags describe the family, not the model.

So the Studio learns it from the refusal itself. The CLI names the model in the message; a
turn that ends that way records the model here, and the picker greys that entry with the
reason instead of offering a pick that throws the message away. Nothing is probed — no call
is ever made for this — and a record expires after RETRY_AFTER, so access arriving later is
found on the next ordinary attempt rather than needing a reset.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Optional

from . import fsutil
from .config import DATA_DIR

# The CLI's own wording, with the model id captured. Matched against the turn's text, so it
# also catches the message arriving as the assistant's only output rather than as an error.
NO_ACCESS_RE = re.compile(
    r"issue with the selected model \(([^)]+)\).{0,120}?(?:may not exist|access)",
    re.I | re.S)

# How long a refusal is believed. Access is granted server-side during a rollout, so this is
# a "try again tomorrow", not a permanent verdict.
RETRY_AFTER = 24 * 3600.0

_FILE = DATA_DIR / "model_access.json"
_lock = threading.Lock()
_cache: dict[str, dict] = {}
_loaded = False


def _load() -> dict[str, dict]:
    global _loaded
    if not _loaded:
        try:
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _cache.update({str(k): v for k, v in d.items() if isinstance(v, dict)})
        except Exception:
            pass
        _loaded = True
    return _cache


def _save() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cache, indent=1), encoding="utf-8")
        fsutil.replace(tmp, _FILE)
    except OSError:
        pass


def _norm(model: str) -> str:
    """The catalog id, without a context selector — `claude-fable-5-1[1m]` and
    `claude-fable-5-1` are the same model as far as access goes."""
    return (model or "").split("[")[0].strip().lower()


def note_turn(model: str, text: str) -> Optional[str]:
    """Record what a finished turn proved about its model. Returns the id it recorded as
    refused, or None.

    Called for every turn, refused or not, because a clean turn is the evidence that clears
    a stale refusal — that is what makes access arriving later show up without a restart.
    """
    m = _norm(model)
    hit = NO_ACCESS_RE.search(text or "")
    with _lock:
        _load()
        if hit:
            # Trust the id in the MESSAGE over the one we asked for: an alias (`fable`) is
            # refused under the id it resolved to, and that resolved id is what to grey out.
            named = _norm(hit.group(1))
            _cache[named] = {"ok": False, "at": time.time(),
                             "reason": "This account can't run it yet — the CLI knows the "
                                       "model but the API refused it."}
            if m and m != named:
                _cache[m] = dict(_cache[named], alias_of=named)
            _save()
            return named
        if m and m in _cache and not _cache[m].get("ok"):
            _cache.pop(m, None)          # it ran — whatever we recorded is out of date
            _save()
    return None


def blocked() -> dict[str, dict]:
    """The refusals still believed, `{model id: {reason, at}}`. Expired ones are dropped."""
    now = time.time()
    with _lock:
        _load()
        out = {k: {"reason": v.get("reason", ""), "at": v.get("at", 0)}
               for k, v in _cache.items()
               if not v.get("ok") and now - float(v.get("at", 0) or 0) < RETRY_AFTER}
    return out


def is_blocked(model: str) -> bool:
    return _norm(model) in blocked()


def forget(model: str = "") -> None:
    """Drop one record, or all of them. Used by the retry button in the picker."""
    with _lock:
        _load()
        if model:
            _cache.pop(_norm(model), None)
        else:
            _cache.clear()
        _save()

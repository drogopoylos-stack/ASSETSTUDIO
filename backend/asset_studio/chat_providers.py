"""User-added chat engines — any model, in the Workspace, exactly like Claude.

The Studio already runs Kimi and Qwen by pointing the *same* claude CLI at a vendor's
Anthropic-compatible endpoint with its own ``CLAUDE_CONFIG_DIR``. That gives the engine
its own sessions, its own context meter, and the same effort/permission controls — the
whole live-feed pipeline is reused, not re-implemented.

This module turns that from hardcoded entries into a list the user edits in
Settings → Models. Add a provider, paste a key, and it appears in the agent picker.

Two protocols:

``anthropic``
    The endpoint already speaks the Messages API (DeepSeek, Z.ai, MiniMax, Moonshot,
    DashScope…). The CLI talks to it directly. First-class: nothing is translated.

``openai``
    The endpoint speaks OpenAI chat-completions (Ollama, LM Studio, OpenRouter, Groq,
    xAI, Together…). The CLI cannot read that, so the provider is pointed at this
    backend's local bridge, which translates both ways. See :mod:`openai_bridge`.

The API key lives in the OS keychain under ``chat:<id>``, never in the JSON file.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

from . import fsutil
from .config import DATA_DIR
from . import quarantine

STORE = DATA_DIR / "chat_providers.json"
_lock = threading.Lock()

# ids that already mean something to the chat layer
RESERVED = {"claude", "kimi", "qwen", "codex", "deepseek-harness", "gemini", "cursor", "default", "none", "all"}
_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")

PROTOCOLS = ("anthropic", "openai")

# One-click starting points. `base_url` is the vendor's documented endpoint; `models` only
# seeds the picker — an id that is not listed can still be typed in and sent.
PRESETS: list[dict[str, Any]] = [
    {"id": "deepseek", "name": "DeepSeek", "vendor": "DeepSeek", "protocol": "anthropic",
     "base_url": "https://api.deepseek.com/anthropic", "color": "#4d6bfe",
     "models": ["deepseek-chat", "deepseek-reasoner"], "default_model": "deepseek-chat", "context_window": 128000,
     "key_url": "https://platform.deepseek.com/api_keys"},
    {"id": "glm", "name": "Z.ai GLM", "vendor": "Z.ai", "protocol": "anthropic",
     "base_url": "https://api.z.ai/api/anthropic", "color": "#2f6bff",
     "models": ["glm-4.6", "glm-4.5-air"], "default_model": "glm-4.6", "context_window": 200000,
     "key_url": "https://z.ai/manage-apikey/apikey-list"},
    {"id": "minimax", "name": "MiniMax", "vendor": "MiniMax", "protocol": "anthropic",
     "base_url": "https://api.minimax.io/anthropic", "color": "#ff5a36",
     "models": ["MiniMax-M2"], "default_model": "MiniMax-M2", "context_window": 204800,
     "key_url": "https://platform.minimax.io/user-center/basic-information/interface-key"},
    {"id": "openrouter", "name": "OpenRouter", "vendor": "OpenRouter", "protocol": "openai",
     "base_url": "https://openrouter.ai/api/v1", "color": "#8b8b8b",
     "models": ["deepseek/deepseek-chat", "qwen/qwen3-max", "z-ai/glm-4.6"],
     "default_model": "deepseek/deepseek-chat", "key_url": "https://openrouter.ai/keys"},
    {"id": "ollama", "name": "Ollama (local)", "vendor": "Ollama", "protocol": "openai",
     "base_url": "http://127.0.0.1:11434/v1", "color": "#12b886",
     "models": ["qwen3:14b", "llama3.1:8b"], "default_model": "qwen3:14b",
     "key_url": "", "note": "Runs on this PC. Leave the API key blank."},
    {"id": "lmstudio", "name": "LM Studio (local)", "vendor": "LM Studio", "protocol": "openai",
     "base_url": "http://127.0.0.1:1234/v1", "color": "#7048e8", "models": [],
     "default_model": "", "key_url": "", "note": "Runs on this PC. Leave the API key blank."},
    {"id": "groq", "name": "Groq", "vendor": "Groq", "protocol": "openai",
     "base_url": "https://api.groq.com/openai/v1", "color": "#f55036",
     "models": ["moonshotai/kimi-k2-instruct", "llama-3.3-70b-versatile"],
     "default_model": "moonshotai/kimi-k2-instruct", "context_window": 131072,
     "key_url": "https://console.groq.com/keys"},
    {"id": "xai", "name": "xAI Grok", "vendor": "xAI", "protocol": "openai",
     "base_url": "https://api.x.ai/v1", "color": "#111111",
     "models": ["grok-4", "grok-code-fast-1"], "default_model": "grok-4", "context_window": 256000,
     "key_url": "https://console.x.ai"},
    {"id": "together", "name": "Together AI", "vendor": "Together", "protocol": "openai",
     "base_url": "https://api.together.xyz/v1", "color": "#0f6fff", "models": [],
     "default_model": "", "key_url": "https://api.together.ai/settings/api-keys"},
    {"id": "mistral", "name": "Mistral", "vendor": "Mistral AI", "protocol": "openai",
     "base_url": "https://api.mistral.ai/v1", "color": "#fa5000",
     "models": ["mistral-large-latest", "devstral-medium-latest"],
     "default_model": "mistral-large-latest", "context_window": 128000,
     "key_url": "https://console.mistral.ai/api-keys"},
    {"id": "cerebras", "name": "Cerebras", "vendor": "Cerebras", "protocol": "openai",
     "base_url": "https://api.cerebras.ai/v1", "color": "#f26722",
     "models": ["qwen-3-coder-480b"], "default_model": "qwen-3-coder-480b", "context_window": 131072,
     "key_url": "https://cloud.cerebras.ai"},
]

_PRESET_BY_ID = {p["id"]: p for p in PRESETS}


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {"stamp": None, "items": []}


def _read() -> list[dict]:
    """Providers from disk, cached on (mtime, size). ``alt_prefix()`` calls this on every
    feed/context lookup, so re-parsing the file each time would be a real cost."""
    try:
        st = STORE.stat()
        stamp = (st.st_mtime, st.st_size)
    except OSError:
        _cache["stamp"], _cache["items"] = None, []
        return []
    if _cache["stamp"] == stamp:
        return _cache["items"]          # type: ignore[return-value]
    # Falling back to [] here silently deletes every configured provider — the API keys are
    # in the keychain, but the endpoints, models and routing are only in this file, and
    # _write() would persist the empty list. Keep the bad copy before that can happen.
    raw = quarantine.safe_load_json(STORE, [], expect=list)
    items = [p for p in raw if isinstance(p, dict) and p.get("id")]
    _cache["stamp"], _cache["items"] = stamp, items
    return items


def _write(items: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    fsutil.replace(tmp, STORE)


def all_providers() -> list[dict]:
    return _read()


def get(pid: str) -> Optional[dict]:
    for p in _read():
        if p.get("id") == pid:
            return p
    return None


KEY_POOL_MAX = 8          # more than anyone has typed in, small enough to probe every time


def key_name(pid: str, slot: int = 0) -> str:
    """Where a provider's key lives. Slot 0 keeps the name it has always had, so nothing that
    was stored before this existed has to move."""
    return f"chat:{pid}" if slot <= 0 else f"chat:{pid}#{slot + 1}"


def keys_for(pid: str) -> list:
    """Every key stored for this provider, in slot order.

    A gateway can hold one request per key in flight and queue the rest. Spreading a fan-out
    over several keys then multiplies what gets through — but only where the limit is really
    per key, so measure before believing it: OpenRouter meters per ACCOUNT and extra keys there
    change nothing at all."""
    from . import keychain
    out = []
    for slot in range(KEY_POOL_MAX):
        v = (keychain.get_key(key_name(pid, slot)) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def has_key(pid: str) -> bool:
    from . import keychain
    return keychain.has_key(key_name(pid))


def validate(spec: dict, *, updating: str = "") -> dict:
    """Normalise + check a provider spec. Raises ValueError with a message meant to be
    shown to the user as-is."""
    pid = str(spec.get("id", "")).strip().lower()
    if not _ID_RE.match(pid):
        raise ValueError("The provider ID must be 2–31 characters, start with a letter, and use only "
                         "lowercase letters, digits and dashes.")
    if pid in RESERVED:
        raise ValueError(f"'{pid}' is already used by a built-in agent. Pick another ID.")
    if pid != updating and get(pid):
        raise ValueError(f"A provider with the ID '{pid}' already exists.")
    protocol = str(spec.get("protocol", "anthropic")).strip().lower()
    if protocol not in PROTOCOLS:
        raise ValueError(f"API protocol must be one of: {', '.join(PROTOCOLS)}.")
    base = str(spec.get("base_url", "")).strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("The base URL must start with http:// or https://.")
    models = [str(m).strip() for m in (spec.get("models") or []) if str(m).strip()]
    default_model = str(spec.get("default_model", "")).strip() or (models[0] if models else "")
    try:
        window = max(0, int(spec.get("context_window") or 0))
    except (TypeError, ValueError):
        window = 0
    return {
        "id": pid,
        "name": str(spec.get("name", "")).strip() or pid,
        "vendor": str(spec.get("vendor", "")).strip() or str(spec.get("name", "")).strip() or pid,
        "protocol": protocol,
        "base_url": base,
        "models": models,
        "default_model": default_model,
        # The CLI does not know a third-party model's window, so it assumes 200k and
        # auto-compacts there. Stating the real number stops it throwing away context
        # a 1M-window model could still hold.
        "context_window": window,
        "color": str(spec.get("color", "")).strip() or "#8b8b8b",
        "note": str(spec.get("note", "")).strip(),
        "key_url": str(spec.get("key_url", "")).strip(),
        "preset": str(spec.get("preset", "")).strip(),
        # Route this Anthropic provider through the Studio so every upstream request streams.
        # OpenRouter drops a connection after 120s of SILENCE, and a non-streaming request is one
        # long silence -- its whole duration is the gap. Measured: the same hard turn took 118.6s
        # non-streaming (1.4s from being killed) and 166.6s streaming across 2414 chunks, never
        # at risk. Nothing about the model or the prompt size changes; only the silence does.
        "force_stream": bool(spec.get("force_stream", False)),
        # Share another provider's chats. Two routes to the same model -- a metered one and a
        # free one, say -- then keep ONE conversation, so switching between them continues the
        # work instead of opening an empty chat next to it.
        "session_with": str(spec.get("session_with", "")).strip(),
        # Models to fall back to when the first one cannot serve the request -- it is rate
        # limited, down, or filtered. The gateway does the switch itself, inside the same call,
        # so the agent never sees the failure. Measured: with the primary's daily quota spent,
        # naming one fallback turned a 429 into an answer in 12.4s.
        "fallback_models": [str(m).strip() for m in (spec.get("fallback_models") or [])
                            if str(m).strip()][:5],
        # Passed through untouched as the request's `provider` object: order, only, ignore, sort,
        # require_parameters and the rest. It picks between the machines serving ONE model, so it
        # does nothing for a model with a single endpoint -- check before setting it.
        "provider_routing": (spec.get("provider_routing")
                             if isinstance(spec.get("provider_routing"), dict) else {}),
    }


def save(spec: dict, *, updating: str = "") -> dict:
    clean = validate(spec, updating=updating)
    with _lock:
        items = _read()
        old = updating or clean["id"]
        items = [p for p in items if p.get("id") != old]
        items.append(clean)
        _write(items)
    return clean


def delete(pid: str) -> bool:
    from . import keychain
    with _lock:
        items = _read()
        rest = [p for p in items if p.get("id") != pid]
        if len(rest) == len(items):
            return False
        _write(rest)
    try:
        keychain.delete_key(key_name(pid))
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# what the chat layer needs
# ---------------------------------------------------------------------------
def prefix(pid: str) -> str:
    """The project-id prefix that routes a feed/context/rewind lookup into this
    engine's own session universe."""
    return f"{pid}--"


def _home_stem(pid: str, table: dict) -> str:
    """Whose session universe `pid` uses. Follows `session_with` to the end of the chain and
    stops on a cycle, so no pair of settings can hang a caller."""
    seen: set = set()
    cur = str(pid)
    while cur not in seen:
        seen.add(cur)
        nxt = str((table.get(cur) or {}).get("session_with") or "").strip()
        if not nxt or nxt == cur or nxt not in table:
            break
        cur = nxt
    return cur


def home_dirname(pid: str) -> str:
    """The folder holding this provider's chats — its own, or the one it was pointed at."""
    return f"{_home_stem(pid, {str(p.get('id')): p for p in _read()})}-home"


def engines() -> dict[str, dict]:
    """{prefix: engine spec} for every user-added provider, in the shape
    :mod:`cc_session` expects for a built-in alternate engine."""
    out: dict[str, dict] = {}
    for p in _read():
        pid = str(p["id"])
        out[prefix(pid)] = {
            "id": pid,
            "key": key_name(pid),
            "base": effective_base_url(p),
            "vendor": p.get("vendor") or p.get("name") or pid,
            "setting": f"{pid}_model",
            "default_model": p.get("default_model") or "",
            "home_dirname": home_dirname(pid),
            "protocol": p.get("protocol", "anthropic"),
            "name": p.get("name") or pid,
            "context_window": int(p.get("context_window") or 0),
        }
    return out


def effective_base_url(p: dict) -> str:
    """Where the claude CLI should actually point.

    An OpenAI-protocol provider is served through this backend's translating bridge — the CLI only
    speaks the Messages API. An Anthropic provider goes direct, unless it is marked `force_stream`,
    in which case it takes the same road for a different reason: the bridge streams upstream on its
    behalf so a long quiet turn is never mistaken for a dead connection."""
    if str(p.get("protocol", "anthropic")) == "openai" or p.get("force_stream"):
        from .config import settings
        host = settings.get("host") or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = settings.get("port") or 8777
        return f"http://{host}:{port}/api/bridge/{p['id']}"
    return str(p.get("base_url", "")).rstrip("/")


def preset(pid: str) -> Optional[dict]:
    return _PRESET_BY_ID.get(pid)


def list_for_ui() -> list[dict]:
    """Providers + whether a key is present (never the key itself)."""
    out = []
    for p in _read():
        out.append({**p, "has_key": has_key(str(p["id"])), "agent_id": str(p["id"])})
    return out

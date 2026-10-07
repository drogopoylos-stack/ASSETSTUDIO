"""Chat providers + the OpenAI translation bridge.

``/api/chat-providers``  — add, edit and delete the models that appear in the chat's
agent picker. A saved provider becomes a full Studio agent: its own sessions, its own
context meter, the same effort and permission controls Claude has.

``/api/bridge/<id>/v1/...`` — an Anthropic Messages endpoint that an OpenAI-compatible
provider is served through. Only the claude CLI calls it, and only for providers saved
with ``protocol: "openai"``.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .. import chat_providers, keychain, openai_bridge

router = APIRouter(prefix="/api", tags=["models"])


# ---------------------------------------------------------------------------
# provider CRUD
# ---------------------------------------------------------------------------
@router.get("/chat-providers")
def list_providers():
    provs = chat_providers.list_for_ui()
    for p in provs:                      # what the gateway last said about this key's quota
        lim = _limits.get(str(p.get("id")))
        if lim:
            p["limits"] = lim
        p["key_count"] = len(chat_providers.keys_for(str(p.get("id"))))
    return {"providers": provs,
            "presets": chat_providers.PRESETS,
            "protocols": list(chat_providers.PROTOCOLS)}


class ProviderBody(BaseModel):
    id: str
    name: str = ""
    vendor: str = ""
    protocol: str = "anthropic"
    base_url: str = ""
    models: list[str] = []
    default_model: str = ""
    context_window: int = 0
    color: str = ""
    note: str = ""
    key_url: str = ""
    preset: str = ""
    force_stream: bool = False         # anthropic providers only; see effective_base_url
    session_with: str = ""             # share another provider's chats; see home_dirname
    fallback_models: list[str] = []    # tried in order when the first cannot serve the request
    provider_routing: dict = {}        # the gateway's own `provider` object, passed through
    api_key: Optional[str] = None      # write-only; stored in the OS keychain


def _save(body: ProviderBody, updating: str = "") -> dict:
    try:
        saved = chat_providers.save(body.model_dump(exclude={"api_key"}), updating=updating)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.api_key is not None:
        # One key per line. Several means the bridge takes them in turn, which is only worth
        # doing where the provider's limit is per key.
        pool = [k.strip() for k in str(body.api_key).replace(",", "\n").splitlines() if k.strip()]
        for slot in range(chat_providers.KEY_POOL_MAX if pool else 0):
            name = chat_providers.key_name(saved["id"], slot)
            if slot < len(pool):
                keychain.set_key(name, pool[slot])
            else:
                try:
                    keychain.delete_key(name)
                except Exception:
                    pass
    return {**saved, "has_key": chat_providers.has_key(saved["id"])}


@router.post("/chat-providers")
def create_provider(body: ProviderBody):
    return _save(body)


@router.put("/chat-providers/{pid}")
def update_provider(pid: str, body: ProviderBody):
    if not chat_providers.get(pid):
        raise HTTPException(404, f"no provider {pid!r}")
    old_key = keychain.get_key(chat_providers.key_name(pid))
    saved = _save(body, updating=pid)
    if saved["id"] != pid:
        # the id is the keychain name and the session-universe prefix — carry the key over
        if body.api_key is None and old_key:
            keychain.set_key(chat_providers.key_name(saved["id"]), old_key)
        keychain.delete_key(chat_providers.key_name(pid))
        saved["has_key"] = chat_providers.has_key(saved["id"])
    return saved


@router.delete("/chat-providers/{pid}")
def delete_provider(pid: str):
    if not chat_providers.delete(pid):
        raise HTTPException(404, f"no provider {pid!r}")
    return {"ok": True}


class KeyBody(BaseModel):
    value: str = ""


@router.put("/chat-providers/{pid}/key")
def set_provider_key(pid: str, body: KeyBody):
    if not chat_providers.get(pid):
        raise HTTPException(404, f"no provider {pid!r}")
    keychain.set_key(chat_providers.key_name(pid), body.value.strip())
    return {"ok": True, "has_key": chat_providers.has_key(pid)}


def _provider_or_404(pid: str) -> dict:
    p = chat_providers.get(pid)
    if not p:
        raise HTTPException(404, f"no provider {pid!r}")
    return p


@router.get("/chat-providers/{pid}/models")
async def fetch_models(pid: str):
    """Ask the provider which models it serves, for the picker. Both protocols expose
    ``GET /models``; a provider that does not is not an error — the user types an id."""
    p = _provider_or_404(pid)
    key = keychain.get_key(chat_providers.key_name(pid)) or ""
    url = str(p["base_url"]).rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            r = await client.get(url, headers=openai_bridge.upstream_headers(key, p))
        if r.status_code >= 400:
            return {"ok": False, "models": [], "error": f"{r.status_code} {r.text[:200]}"}
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "error": str(e)[:200]}
    items = data.get("data") if isinstance(data, dict) else data
    ids = [str(m.get("id")) for m in (items or []) if isinstance(m, dict) and m.get("id")]
    return {"ok": True, "models": sorted(set(ids))}


@router.post("/chat-providers/{pid}/test")
async def test_provider(pid: str):
    """One cheap round-trip so the user knows the key and URL work before starting a chat."""
    p = _provider_or_404(pid)
    key = keychain.get_key(chat_providers.key_name(pid)) or ""
    model = p.get("default_model") or (p.get("models") or [""])[0]
    if not model:
        return {"ok": False, "error": "No model set for this provider. Add one first."}
    base = str(p["base_url"]).rstrip("/")
    anthropic = str(p.get("protocol")) == "anthropic"
    url = f"{base}/v1/messages" if anthropic else f"{base}/chat/completions"
    if anthropic:
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                   "x-api-key": key, "Authorization": f"Bearer {key}"}
        payload: dict[str, Any] = {"model": model, "max_tokens": 16,
                                   "messages": [{"role": "user", "content": "Reply with OK."}]}
    else:
        headers = openai_bridge.upstream_headers(key, p)
        payload = {"model": model, "max_tokens": 16,
                   "messages": [{"role": "user", "content": "Reply with OK."}]}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=60.0,
                                                           write=30.0, pool=15.0)) as client:
            r = await client.post(url, json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not reach {url} — {str(e)[:200]}"}
    if r.status_code >= 400:
        return {"ok": False, "error": f"{r.status_code}: {r.text[:300]}"}
    try:
        data = r.json()
        if anthropic:
            reply = "".join(b.get("text", "") for b in data.get("content", []) if isinstance(b, dict))
        else:
            reply = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except Exception:  # noqa: BLE001
        reply = ""
    return {"ok": True, "model": model, "reply": reply.strip()[:120]}


# ---------------------------------------------------------------------------
# the bridge — Anthropic in, OpenAI out
# ---------------------------------------------------------------------------
bridge = APIRouter(prefix="/api/bridge", tags=["bridge"])


def _bridge_provider(pid: str) -> dict:
    p = chat_providers.get(pid)
    if not p:
        raise HTTPException(404, f"no provider {pid!r}")
    if str(p.get("protocol")) != "openai" and not p.get("force_stream"):
        raise HTTPException(404, f"provider {pid!r} is not served through the bridge")
    return p


_RETRIES = 2                 # three tries in all
_RETRY_WAIT = (1.5, 4.0)     # seconds before try 2 and try 3
_SOFT_WAIT = (0.6, 1.8)      # a busy endpoint refuses in under a second; do not make the user wait

# How long one attempt may sit with NOTHING arriving before it is abandoned.
#
# httpx reads this as the gap BETWEEN bytes, so a stream that is genuinely generating resets it
# on every token and may run as long as it likes. It bites only when the far end has gone quiet.
# The shared 900s ceiling was survivable with a single attempt; with three it became 45 minutes
# of silence, and a real session sat at `status: requesting` for a quarter of an hour looking
# dead. Bounding the stall bounds the whole turn.
_STALL_STREAM = httpx.Timeout(connect=20.0, read=90.0, write=120.0, pool=20.0)
_STALL_ONCE = httpx.Timeout(connect=20.0, read=240.0, write=120.0, pool=20.0)

# Sending the same request again can only help when the first attempt failed for a reason that
# might not repeat. A refusal -- a spent quota, a bad key, a malformed body -- is not one of those.
# Retrying it spends another request against the very limit that rejected the last one, delays the
# real message behind two backoff waits, and then hides it inside a "3 tries" wrapper.
_FINAL_STATUS = {400, 401, 402, 403, 404, 405, 413, 422, 429}
_FINAL_WORDS = ("rate limit", "rate_limit", "quota", "credit", "insufficient",
                "authentication", "invalid_api_key", "permission", "invalid_request",
                "not_found", "context_length", "too large", "moderation")

# Last limiter headers seen per provider, so the Studio can show what is left before it runs out.
_limits: dict = {}

# Round-robin cursor per provider. Whole-number counter rather than random, so N concurrent
# calls land on N different keys instead of colliding by chance.
_key_turn: dict = {}


def _next_key(pid: str, request: Request) -> str:
    """The key to use for THIS request, taking each stored key in turn.

    With one key stored this is exactly what it always was. With several, concurrent subagent
    calls are spread across them, which only helps where the provider's limit is per key."""
    pool = chat_providers.keys_for(pid)
    if len(pool) > 1:
        n = _key_turn.get(pid, 0)
        _key_turn[pid] = (n + 1) % len(pool)
        return pool[n % len(pool)]
    if pool:
        return pool[0]
    hdr = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization") or ""
    if hdr and hdr != "studio-local":
        return hdr
    if auth.lower().startswith("bearer ") and auth[7:] != "studio-local":
        return auth[7:]
    return ""


# Which providers answer /v1/messages/count_tokens. OpenRouter returns 404, and asking again on
# every turn is a round trip that buys nothing and still meets the gateway's request limiter.
_counts_tokens: dict = {}


def _resolve_model(body: dict, provider: dict) -> str:
    """The CLI sends whatever ANTHROPIC_MODEL held. If that still looks like an Anthropic
    id (an internal call the Studio did not set), fall back to the provider's default so
    the request cannot 404 upstream."""
    m = str(body.get("model") or "").split("[")[0].strip()
    if not m or m.startswith("claude-"):
        return str(provider.get("default_model") or m)
    return m


def _has_substance(msg: dict) -> bool:
    """Is there anything in this reply the agent can use?

    Text or a tool call counts. A reply carrying only a `thinking` block does not: the CLI has
    nothing to print and nothing to run, so the turn ends the instant it arrives — which reads
    as the agent stopping for no reason. Measured on OpenCode Zen: 52 of 212 replies came back
    exactly like that, `stop_reason: end_turn` with one output token.
    """
    for b in msg.get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use":
            return True
        if b.get("type") == "text" and str(b.get("text") or "").strip():
            return True
    return False


def _chunk_has_substance(raw) -> bool:
    """The same question, asked of one raw SSE chunk on its way to the caller."""
    t = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
    t = t.replace(" ", "")
    return ('"type":"text_delta"' in t or '"type":"input_json_delta"' in t
            or '"type":"tool_use"' in t)


def _same_model(want: str, got: str) -> bool:
    """Whether `got` is the model that was asked for.

    Deliberately forgiving: a gateway may echo a dated or variant id for the same model, and a
    false accusation would break every turn. It compares the ids with a trailing date, `:free`
    or `latest` removed, accepts either being a prefix of the other, and treats a missing id as
    a match. Only a plainly different model is rejected."""
    if not want or not got:
        return True                       # nothing to compare -- never block a good answer
    def norm(s: str) -> str:
        s = str(s).strip().lower()
        s = re.sub(r"[:@](free|beta|preview|latest|nitro|floor)$", "", s)
        return re.sub(r"[-@](\d{8}|\d{4}-\d{2}-\d{2}|latest)$", "", s)
    a, b = norm(want), norm(got)
    return a == b or a.startswith(b) or b.startswith(a)


def _final(status: int, body: str) -> bool:
    """True when another try cannot do better."""
    if status in _FINAL_STATUS:
        return True
    low = (body or "").lower()
    return any(w in low for w in _FINAL_WORDS)


def _is_quota(status: int, body: str) -> bool:
    low = (body or "").lower()
    return status == 429 or "rate limit" in low or "rate_limit" in low or "quota" in low


def _note_limits(pid: str, headers) -> None:
    """Remember what the gateway said about the quota, whatever the request did."""
    got = {}
    for k, v in (headers or {}).items():
        kl = k.lower()
        if kl in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"):
            try:
                got[kl.replace("x-ratelimit-", "")] = int(float(v))
            except (TypeError, ValueError):
                pass
    if got:
        _limits[pid] = {**got, "at": time.time()}


def _reset_words(ms) -> str:
    """`1787443200000` as something a person can act on."""
    try:
        n = float(ms)
        n = n / 1000 if n > 1e11 else n
        return "%s (in %.1f h)" % (datetime.fromtimestamp(n).strftime("%H:%M on %d %b"),
                                   (n - time.time()) / 3600)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _quota_words(name: str, pid: str, body: str) -> str:
    """A spent quota in a sentence, with the hour it lifts.

    The raw body is a wall of JSON that the caller truncates mid-header, and the generic advice
    that follows it -- "temporary, try again in a moment" -- is wrong by about a day."""
    src = {}
    try:
        src = ((json.loads(body) or {}).get("metadata") or {}).get("headers") or {}
    except Exception:
        src = {}
    lim = _limits.get(pid) or {}
    cap = src.get("X-RateLimit-Limit") or lim.get("limit") or ""
    when = _reset_words(src.get("X-RateLimit-Reset") or lim.get("reset"))
    # A gateway meters each family of free models separately, so the rest of this provider is
    # usually still answering. Naming one is the difference between "blocked" and "switch model".
    spare = ""
    try:
        others = [m for m in (chat_providers.get(pid) or {}).get("models") or []
                  if m != (chat_providers.get(pid) or {}).get("default_model")]
        if others:
            spare = ("Other models on this provider are metered separately — try %s. "
                     % ", ".join(others[:2]))
    except Exception:
        spare = ""
    return ("%s has spent its daily quota%s on this account. %s%sExtra API keys on the same "
            "account share it." % (name, (" of %s requests" % cap) if cap else "",
                                   ("It resets at %s. " % when) if when else "", spare))


class _Retry(Exception):
    """The upstream leg failed while the caller still had nothing. Try it again."""


def _sse(ev: dict) -> bytes:
    ev = {k: v for k, v in ev.items() if not k.startswith("__")}
    return ("event: " + str(ev.get("type", "message")) + "\ndata: "
            + json.dumps(ev) + "\n\n").encode()


async def _stream_events(url: str, headers: dict, body: dict, pid: str = "", name: str = "",
                         retries: int = _RETRIES, only: str = ""):
    """Anthropic events from the provider, retried while nothing has reached the caller.

    A gateway in front of a busy model can drop a request that has produced no tokens yet --
    OpenRouter reports it as `Upstream idle timeout exceeded`, and the agent turn dies with it.
    Nothing had been delivered at that point, so the request is safe to send again. Once real
    content is on its way the stream is committed: a later error is passed through, never retried,
    because a retry would repeat text the caller already has.

    `message_start` is emitted once. A retry's own `message_start` is dropped, so the caller sees
    one continuous message and never learns that the first attempt happened.
    """
    started = False      # message_start already delivered
    content = False      # real content delivered -- past the point of no return
    last = "the provider closed the connection without sending anything"
    status = 0
    final = False        # the failure is a refusal, not a hiccup: stop trying
    tries = 0
    for attempt in range(retries + 1):
        tries = attempt + 1
        try:
            async with httpx.AsyncClient(timeout=_STALL_STREAM) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    _note_limits(pid, resp.headers)
                    if resp.status_code >= 400:
                        last = (await resp.aread()).decode("utf-8", "ignore")[:600]
                        status = resp.status_code
                        final = _final(status, last)
                        raise _Retry()
                    buf = ""
                    async for raw in resp.aiter_bytes():
                        buf += raw.decode("utf-8", "ignore")
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload or payload == "[DONE]":
                                continue
                            try:
                                ev = json.loads(payload)
                            except Exception:
                                continue
                            t = str(ev.get("type") or "")
                            if t == "error":
                                last = str((ev.get("error") or {}).get("message")
                                           or ev.get("error") or "error")[:600]
                                if content:
                                    yield ev
                                    return
                                final = _final(0, last)
                                raise _Retry()
                            if t == "message_start":
                                got = str((ev.get("message") or {}).get("model") or "")
                                if only and not _same_model(only, got):
                                    # Nothing has been delivered yet, so this is refusable. A
                                    # provider with no fallbacks means "this model or nothing",
                                    # and passing another model's words off as its own would be
                                    # the one thing the setting exists to prevent.
                                    yield {"type": "error", "__status": 502, "error": {
                                        "type": "api_error",
                                        "message": ("%s is set to %s only, and the gateway "
                                                    "answered as %s. The reply was discarded."
                                                    % (name or pid, only, got))}}
                                    return
                                if started:
                                    continue     # a retry must not restart the message
                                started = True
                            elif t.startswith("content_block"):
                                content = True
                            yield ev
                            if t == "message_stop":
                                return
            if content:
                return
            raise _Retry()
        except _Retry:
            pass
        except Exception as e:  # noqa: BLE001 -- a dropped socket is the case this exists for
            last = (type(e).__name__ + ": " + str(e))[:300]
            if content:
                yield {"type": "error", "error": {"type": "api_error", "message": last}}
                return
        if final:
            break
        if attempt < retries:
            await asyncio.sleep(_RETRY_WAIT[min(attempt, len(_RETRY_WAIT) - 1)])
    quota = _is_quota(status, last)
    if quota:
        msg, kind = _quota_words(name or pid or "This provider", pid, last), "rate_limit_error"
    elif final:
        msg, kind = last, "api_error"
    else:
        msg = "%d tries, none delivered anything: %s" % (tries, last)
        kind = "api_error"
    yield {"type": "error", "__status": status or 502,
           "error": {"type": kind, "message": msg}}


def _assemble(events) -> dict:
    """Rebuild one Messages response from its own stream of events.

    The caller asked for a whole answer and gets a whole answer; only the hop to the provider was
    streamed. Deltas accumulate per content block -- text, thinking, the signature, and a tool
    call's partial JSON -- exactly as the non-streaming response would have contained them.
    """
    msg: dict = {}
    blocks: dict = {}
    for ev in events:
        t = ev.get("type")
        if t == "message_start":
            msg = dict(ev.get("message") or {})
        elif t == "content_block_start":
            blocks[int(ev.get("index", 0))] = dict(ev.get("content_block") or {})
        elif t == "content_block_delta":
            b = blocks.setdefault(int(ev.get("index", 0)), {})
            d = ev.get("delta") or {}
            kind = d.get("type")
            if kind == "text_delta":
                b["text"] = b.get("text", "") + str(d.get("text", ""))
            elif kind == "thinking_delta":
                b["thinking"] = b.get("thinking", "") + str(d.get("thinking", ""))
            elif kind == "signature_delta":
                b["signature"] = b.get("signature", "") + str(d.get("signature", ""))
            elif kind == "input_json_delta":
                b["__partial"] = b.get("__partial", "") + str(d.get("partial_json", ""))
        elif t == "message_delta":
            for k, v in (ev.get("delta") or {}).items():
                msg[k] = v
            if ev.get("usage"):
                msg["usage"] = {**(msg.get("usage") or {}), **ev["usage"]}
        elif t == "error":
            raise RuntimeError(str((ev.get("error") or {}).get("message") or ev.get("error"))[:300])
    out = []
    for i in sorted(blocks):
        b = blocks[i]
        if "__partial" in b:                       # a tool call arrives as partial JSON
            try:
                b["input"] = json.loads(b.pop("__partial") or "{}")
            except Exception:
                b["input"] = {}
                b.pop("__partial", None)
        out.append(b)
    msg["content"] = out
    msg.setdefault("type", "message")
    msg.setdefault("role", "assistant")
    return msg


def _upstream(p: dict, pid: str, request: Request) -> tuple:
    """The provider's real URL and headers, with the stored key preferred over the sent one."""
    key = _next_key(pid, request)
    headers = {"content-type": "application/json", "authorization": "Bearer " + key,
               "x-api-key": key}
    for h in ("anthropic-version", "anthropic-beta"):
        if request.headers.get(h):
            headers[h] = request.headers[h]
    return str(p["base_url"]).rstrip("/") + "/v1/messages", headers


async def _anthropic_relay(p: dict, pid: str, request: Request, body: dict):
    """Forward an Anthropic request untouched, but stream and retry the upstream leg."""
    url, headers = _upstream(p, pid, request)
    upstream = {**body, "stream": True}

    name = str(p.get("name") or pid)
    # The requested model always leads the list; the fallbacks follow. Composing it here rather
    # than storing it whole keeps it right when the user switches model in the chat -- a stored
    # list would pin the old model back to the front of every request.
    spare = [m for m in (p.get("fallback_models") or [])
             if m and m != upstream.get("model")]
    if spare:
        upstream["models"] = [upstream.get("model"), *spare][:6]
    if p.get("provider_routing"):
        upstream["provider"] = {**p["provider_routing"], **(upstream.get("provider") or {})}
    # No fallbacks configured is not merely "do not add a models array" -- it is a promise that
    # the chosen model is the only one that may answer. Checked on the way back, because a
    # gateway can substitute a model without being asked.
    only = "" if spare else str(upstream.get("model") or "")

    if body.get("stream"):
        async def relay():
            async for ev in _stream_events(url, headers, upstream, pid, name, only=only):
                yield _sse(ev)
        return StreamingResponse(relay(), media_type="text/event-stream")

    events = [ev async for ev in _stream_events(url, headers, upstream, pid, name, only=only)]
    err = next((e for e in events if e.get("type") == "error"), None)
    if err and not any(str(e.get("type", "")).startswith("content_block") for e in events):
        # Hand back the gateway's own status. A 429 read as a 502 tells the caller to try again
        # shortly, which is exactly the wrong thing to do with a quota that lasts until tomorrow.
        return JSONResponse(status_code=int(err.get("__status") or 502),
                            content={"type": "error", "error": err.get("error")})
    try:
        return JSONResponse(content=_assemble(events))
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={
            "type": "error", "error": {"type": "api_error", "message": name + ": " + str(e)[:300]}})


@bridge.post("/{pid}/v1/messages")
async def bridge_messages(pid: str, request: Request):
    p = _bridge_provider(pid)
    body = await request.json()
    if str(p.get("protocol")) != "openai":
        return await _anthropic_relay(p, pid, request, body)
    model = _resolve_model(body, p)
    key = _next_key(pid, request)      # a local server needs none and gets ""
    url = str(p["base_url"]).rstrip("/") + "/chat/completions"
    payload = openai_bridge.to_openai(body, model)
    headers = openai_bridge.upstream_headers(key, p)

    # A busy endpoint answers `503 Endpoint is unavailable` in well under a second, and the next
    # try usually lands. Measured on OpenCode Zen: 5 refusals in 12 calls. Without this an agent
    # loses roughly two turns in five to a fault that clears by itself. The rule is the one the
    # Anthropic path already follows -- try again only while the caller still has nothing, and
    # never retry a refusal that a retry cannot change, such as a spent quota or a bad key.
    name = str(p.get("name") or pid)

    if not payload.get("stream"):
        last = ""
        status = 502
        for attempt in range(_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_STALL_ONCE) as client:
                    r = await client.post(url, json=payload, headers=headers)
            except Exception as e:  # noqa: BLE001
                last, status = f"{name} unreachable: {str(e)[:300]}", 502
            else:
                if r.status_code < 400:
                    out = openai_bridge.to_anthropic(r.json(), model)
                    if _has_substance(out):
                        return JSONResponse(content=out)
                    # Nothing usable came back. Another try usually lands, and it costs the
                    # caller nothing because it was handed no answer to begin with. The one
                    # case a retry cannot help is a reply cut off by the token budget -- the
                    # next one is cut off in the same place.
                    status = 502
                    last = (name + " returned a reply with no text and no tool call"
                            + (" — the token budget ran out while it was still thinking"
                               if out.get("stop_reason") == "max_tokens" else ""))
                    if out.get("stop_reason") == "max_tokens":
                        break
                else:
                    last, status = f"{name}: {r.text[:600]}", r.status_code
                    if _final(status, last):
                        break
            if attempt < _RETRIES:
                await asyncio.sleep(_SOFT_WAIT[min(attempt, len(_SOFT_WAIT) - 1)])
        return JSONResponse(status_code=status, content={
            "type": "error", "error": {"type": "api_error", "message": last}})

    # Counted once, before the request goes out — the agent needs it in the first event.
    est_in = openai_bridge.count_tokens(body)

    async def gen():
        # Hold the opening events back until real content appears. They carry nothing the caller
        # needs immediately, and holding them is what makes a silent reply retryable: once
        # `message_stop` has gone out the turn is over and cannot be taken back. As soon as the
        # first token or tool call arrives everything is flushed and the rest streams live.
        sent = False
        last = f"{name} did not answer"
        for attempt in range(_RETRIES + 1):
            held: list = []
            try:
                async with httpx.AsyncClient(timeout=_STALL_STREAM) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as r:
                        if r.status_code >= 400:
                            last = f"{name}: {(await r.aread()).decode('utf-8', 'replace')[:600]}"
                            if _final(r.status_code, last):
                                break
                            raise _Retry()
                        async for chunk in openai_bridge.stream_anthropic(
                                r, model, input_estimate=est_in):
                            if sent:
                                yield chunk
                                continue
                            held.append(chunk)
                            if _chunk_has_substance(chunk):
                                sent = True
                                for c in held:
                                    yield c
                                held = []
                if sent:
                    return
                last = f"{name} returned a reply with no text and no tool call"
                raise _Retry()
            except _Retry:
                pass
            except Exception as e:  # noqa: BLE001
                last = f"{name} stream failed: {str(e)[:300]}"
                if sent:
                    break
            if sent or attempt >= _RETRIES:
                break
            await asyncio.sleep(_SOFT_WAIT[min(attempt, len(_SOFT_WAIT) - 1)])
        # Say why. Silence is the one thing that leaves the user with nothing to act on.
        yield openai_bridge.error_sse(last)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bridge.post("/{pid}/v1/messages/count_tokens")
async def bridge_count_tokens(pid: str, request: Request):
    p = _bridge_provider(pid)
    body = await request.json()
    if str(p.get("protocol")) != "openai" and _counts_tokens.get(pid, True):
        # The provider speaks this endpoint itself, and its own number is the one the agent's
        # context meter should show. Fall back to the local estimate only if the call fails.
        try:
            key = keychain.get_key(chat_providers.key_name(pid)) or ""
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    str(p["base_url"]).rstrip("/") + "/v1/messages/count_tokens", json=body,
                    headers={"content-type": "application/json", "x-api-key": key,
                             "authorization": f"Bearer {key}",
                             "anthropic-version": request.headers.get(
                                 "anthropic-version", "2023-06-01")})
            if r.status_code < 400:
                _counts_tokens[pid] = True
                return r.json()
            if r.status_code in (404, 405, 501):
                _counts_tokens[pid] = False      # it does not have this endpoint; stop asking
        except Exception:
            pass
    return {"input_tokens": openai_bridge.count_tokens(body)}

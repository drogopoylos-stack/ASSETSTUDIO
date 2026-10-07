"""Anthropic Messages API ⇄ OpenAI chat-completions, so any OpenAI-compatible model
can drive the agent engine.

The Studio runs an alternate engine by pointing the claude CLI at another vendor's
endpoint. That only works when the vendor speaks the Anthropic Messages API. Most do
not — Ollama, LM Studio, OpenRouter, Groq, xAI, Together and Mistral all speak OpenAI
chat-completions instead.

This module is the missing adapter. A provider saved with ``protocol: "openai"`` gets
``ANTHROPIC_BASE_URL=http://127.0.0.1:8777/api/bridge/<id>``; the CLI sends a normal
Messages request here, and this code translates it, calls the real endpoint, and
translates the answer back — including the streaming events and tool calls the agent
loop depends on.

What is translated
    * system prompt (string or block list) → a leading ``system`` message
    * text, image and tool_result blocks → OpenAI content parts and ``tool`` messages
    * ``tools`` + ``tool_choice`` → ``functions`` with JSON-schema parameters
    * streamed text, tool-call arguments and reasoning → Anthropic SSE events
    * ``reasoning_content`` (DeepSeek, Qwen3) → ``thinking`` blocks, so a reasoning
      model's chain shows in the feed instead of arriving as silence

Known limits: token counts are the provider's own (or an estimate when it sends none),
and prompt caching is not forwarded — no OpenAI-shaped endpoint has an equivalent.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator, Optional

import httpx

# An agent turn can think for minutes before the first token; only the connect phase
# should fail fast.
TIMEOUT = httpx.Timeout(connect=20.0, read=900.0, write=120.0, pool=20.0)

# Room for a reasoning model to think and still answer; see to_openai.
_REASONING_FLOOR = 8192

_STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Anthropic request → OpenAI request
# ---------------------------------------------------------------------------
def _text_of(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    out = []
    for b in blocks:
        if isinstance(b, str):
            out.append(b)
        elif isinstance(b, dict) and b.get("type") == "text":
            out.append(str(b.get("text", "")))
    return "\n".join(x for x in out if x)


def _image_part(block: dict) -> Optional[dict]:
    """An Anthropic image block as an OpenAI content part, or None if it carries no picture."""
    src = block.get("source") or {}
    if src.get("type") == "base64" and src.get("data"):
        media = src.get("media_type") or "image/png"
        return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{src['data']}"}}
    if src.get("type") == "url" and src.get("url"):
        return {"type": "image_url", "image_url": {"url": str(src["url"])}}
    return None


def _user_message(content: Any) -> list[dict]:
    """A user turn may carry tool results, which OpenAI models as separate ``tool``
    messages that must come BEFORE any remaining user text."""
    if isinstance(content, str):
        return [{"role": "user", "content": content}] if content else []
    if not isinstance(content, list):
        return []
    tool_msgs: list[dict] = []
    parts: list[dict] = []
    tool_images: list[dict] = []      # pictures a tool handed back; see the tool_result branch
    for b in content:
        if not isinstance(b, dict):
            if isinstance(b, str) and b:
                parts.append({"type": "text", "text": b})
            continue
        t = b.get("type")
        if t == "text":
            if str(b.get("text", "")):
                parts.append({"type": "text", "text": str(b["text"])})
        elif t == "image":
            p = _image_part(b)
            if p:
                parts.append(p)
        elif t == "tool_result":
            body = b.get("content")
            text = _text_of(body) if not isinstance(body, str) else body
            if b.get("is_error"):
                text = f"[error] {text}"
            # A tool result can carry pictures: reading a .png hands back an image block, and
            # that is how an agent looks at a screenshot or a piece of art. OpenAI's `tool` role
            # accepts text and nothing else, so the pixels cannot ride along with it. Flattening
            # to text silently dropped them, and the agent then said it could not see an image
            # it had just read. They are carried over to the user turn below instead.
            shots = [p for p in (_image_part(x) for x in body
                                 if isinstance(x, dict) and x.get("type") == "image") if p]
            if isinstance(body, list) and shots:
                tool_images.extend(shots)
            tool_msgs.append({"role": "tool", "tool_call_id": str(b.get("tool_use_id", "")),
                              "content": text or (f"[{len(shots)} image(s) follow]" if shots
                                                  else "(no output)")})
    if tool_images:
        parts = [{"type": "text",
                  "text": "Images returned by the tool call(s) above:"}, *tool_images, *parts]
    msgs = list(tool_msgs)
    if parts:
        # a single text part is sent as a plain string — some local servers reject the
        # array form for text-only turns
        if len(parts) == 1 and parts[0].get("type") == "text":
            msgs.append({"role": "user", "content": parts[0]["text"]})
        else:
            msgs.append({"role": "user", "content": parts})
    return msgs


def _assistant_message(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"role": "assistant", "content": content}] if content else []
    if not isinstance(content, list):
        return []
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            if str(b.get("text", "")):
                text_parts.append(str(b["text"]))
        elif t == "tool_use":
            tool_calls.append({
                "id": str(b.get("id", "")),
                "type": "function",
                "function": {"name": str(b.get("name", "")),
                             "arguments": json.dumps(b.get("input") or {})},
            })
        # thinking / redacted_thinking blocks are dropped: they carry an Anthropic
        # signature no other vendor can verify, and replaying them upstream errors.
    if not text_parts and not tool_calls:
        return []
    msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return [msg]


def to_openai(body: dict, model: str) -> dict:
    msgs: list[dict] = []
    system = body.get("system")
    sys_text = _text_of(system) if system is not None else ""
    if sys_text.strip():
        msgs.append({"role": "system", "content": sys_text})
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant":
            msgs.extend(_assistant_message(m.get("content")))
        else:
            msgs.extend(_user_message(m.get("content")))

    out: dict[str, Any] = {"model": model, "messages": msgs, "stream": bool(body.get("stream"))}
    if body.get("max_tokens"):
        # A reasoning model spends this budget on thinking BEFORE it writes anything, and it
        # thinks harder as a conversation grows. Measured on a real 241-message chat: at
        # max_tokens=256 one reply in three came back with 871 characters of reasoning and zero
        # content, which the agent sees as the turn ending for no reason. The same request at
        # 1024 and above always produced content, and one attempt used 3,086 tokens of reasoning
        # on its own. This is a CAP, not a target — a model still stops when it is done, so
        # raising the floor cannot make replies longer, only stop them being cut off mid-thought.
        out["max_tokens"] = max(int(body["max_tokens"]), _REASONING_FLOOR)
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p")):
        if body.get(src) is not None:
            out[dst] = body[src]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    tools = body.get("tools") or []
    fns = []
    for t in tools:
        if not isinstance(t, dict) or not t.get("name"):
            continue          # server-side tools (web_search…) have no local equivalent
        fns.append({"type": "function", "function": {
            "name": str(t["name"]),
            "description": str(t.get("description", ""))[:1024],
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    if fns:
        out["tools"] = fns
        tc = body.get("tool_choice") or {}
        kind = tc.get("type") if isinstance(tc, dict) else None
        if kind == "any":
            out["tool_choice"] = "required"
        elif kind == "tool" and tc.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif kind == "none":
            out["tool_choice"] = "none"
        else:
            out["tool_choice"] = "auto"
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}
    return out


# ---------------------------------------------------------------------------
# OpenAI response → Anthropic response
# ---------------------------------------------------------------------------
def _msg_id() -> str:
    return "msg_" + uuid.uuid4().hex[:24]


def to_anthropic(data: dict, model: str) -> dict:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks: list[dict] = []
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if reasoning:
        blocks.append({"type": "thinking", "thinking": str(reasoning), "signature": ""})
    text = msg.get("content")
    if isinstance(text, list):
        text = _text_of(text)
    if text:
        blocks.append({"type": "text", "text": str(text)})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        blocks.append({"type": "tool_use", "id": str(tc.get("id") or ("toolu_" + uuid.uuid4().hex[:20])),
                       "name": str(fn.get("name", "")), "input": args})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id") or _msg_id()),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": _STOP_REASON.get(str(choice.get("finish_reason") or "stop"), "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": int(usage.get("prompt_tokens") or 0),
                  "output_tokens": int(usage.get("completion_tokens") or 0)},
    }


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class _StreamState:
    """Tracks which Anthropic content block is open while OpenAI deltas arrive.

    OpenAI interleaves text and tool-call fragments freely; Anthropic requires a strict
    start → delta* → stop sequence per block with sequential indices. This closes the
    open block whenever the kind changes.
    """

    def __init__(self) -> None:
        self.index = -1
        self.kind = ""            # "" | "text" | "thinking" | "tool"
        self.tool_slots: dict[int, int] = {}   # OpenAI tool_call index -> our block index

    def open(self, kind: str, block: dict) -> list[bytes]:
        out = self.close()
        self.index += 1
        self.kind = kind
        out.append(_sse("content_block_start", {"type": "content_block_start",
                                                "index": self.index, "content_block": block}))
        return out

    def close(self) -> list[bytes]:
        if not self.kind:
            return []
        self.kind = ""
        return [_sse("content_block_stop", {"type": "content_block_stop", "index": self.index})]


async def stream_anthropic(resp: httpx.Response, model: str,
                           input_estimate: int = 0) -> AsyncIterator[bytes]:
    """Re-emit an OpenAI SSE stream as Anthropic Messages events.

    `input_estimate` is how big the request was, counted locally before it was sent. It matters
    more than it looks: an Anthropic client reads the size of its context from `message_start`,
    and this stream cannot know the real figure yet because the provider only reports usage at
    the END. Sending 0 told the agent its context was empty on every single turn, so it never
    reached the threshold that triggers compaction — a conversation grew to 28 MB unchecked
    until the requests started drawing 503s. The estimate goes out first, and the provider's
    real number replaces it in `message_delta` as soon as it arrives.
    """
    st = _StreamState()
    mid = _msg_id()
    usage = {"input_tokens": 0, "output_tokens": 0}
    stop_reason = "end_turn"
    started = False
    text_seen = 0

    yield _sse("message_start", {"type": "message_start", "message": {
        "id": mid, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": int(input_estimate or 0), "output_tokens": 0}}})
    started = True

    async for raw in resp.aiter_lines():
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        if ev.get("usage"):
            u = ev["usage"]
            usage["input_tokens"] = int(u.get("prompt_tokens") or usage["input_tokens"])
            usage["output_tokens"] = int(u.get("completion_tokens") or usage["output_tokens"])
        choices = ev.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        delta = ch.get("delta") or {}

        reason = delta.get("reasoning_content") or delta.get("reasoning")
        if reason:
            if st.kind != "thinking":
                for b in st.open("thinking", {"type": "thinking", "thinking": ""}):
                    yield b
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": st.index,
                                               "delta": {"type": "thinking_delta", "thinking": str(reason)}})

        piece = delta.get("content")
        if isinstance(piece, list):
            piece = _text_of(piece)
        if piece:
            if st.kind != "text":
                for b in st.open("text", {"type": "text", "text": ""}):
                    yield b
            text_seen += len(str(piece))
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": st.index,
                                               "delta": {"type": "text_delta", "text": str(piece)}})

        for tc in delta.get("tool_calls") or []:
            slot = int(tc.get("index") or 0)
            fn = tc.get("function") or {}
            if slot not in st.tool_slots:
                for b in st.open("tool", {"type": "tool_use",
                                          "id": str(tc.get("id") or ("toolu_" + uuid.uuid4().hex[:20])),
                                          "name": str(fn.get("name", "")), "input": {}}):
                    yield b
                st.tool_slots[slot] = st.index
            args = fn.get("arguments")
            if args:
                yield _sse("content_block_delta", {"type": "content_block_delta",
                                                   "index": st.tool_slots[slot],
                                                   "delta": {"type": "input_json_delta",
                                                             "partial_json": str(args)}})

        if ch.get("finish_reason"):
            stop_reason = _STOP_REASON.get(str(ch["finish_reason"]), "end_turn")

    for b in st.close():
        yield b
    if not usage["output_tokens"]:
        usage["output_tokens"] = _estimate_tokens("x" * text_seen)
    # The provider's own count, captured above, in place of the estimate.
    yield _sse("message_delta", {"type": "message_delta",
                                 "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                 "usage": {"output_tokens": usage["output_tokens"],
                                           "input_tokens": int(usage["input_tokens"]
                                                               or input_estimate or 0)}})
    yield _sse("message_stop", {"type": "message_stop"})
    _ = started


def error_sse(message: str) -> bytes:
    return _sse("error", {"type": "error", "error": {"type": "api_error", "message": message}})


def count_tokens(body: dict) -> int:
    """Best-effort estimate. No OpenAI-shaped endpoint exposes a counting endpoint, and
    the agent only needs this for its context meter."""
    total = len(_text_of(body.get("system")))
    for m in body.get("messages") or []:
        c = m.get("content") if isinstance(m, dict) else None
        total += len(c) if isinstance(c, str) else len(json.dumps(c or "", ensure_ascii=False))
    for t in body.get("tools") or []:
        total += len(json.dumps(t, ensure_ascii=False))
    return _estimate_tokens("x" * total)


def upstream_headers(api_key: str, provider: dict) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    if "openrouter.ai" in str(provider.get("base_url", "")):
        # OpenRouter asks callers to identify themselves; it also drives their app ranking.
        h["HTTP-Referer"] = "https://github.com/grmortis-create/asset-studio"
        h["X-Title"] = "Asset Studio"
    return h


def stamp() -> float:
    return time.time()

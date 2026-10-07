"""General-purpose multi-model chat via OpenRouter (one key → DeepSeek, Llama, Qwen,
GPT, Gemini, Claude…). Separate from the coding agents — this is a plain "ask a
question" chat with ChatGPT-style saved conversations (create / delete / pick model).

Conversations are stored as JSON under ``DATA_DIR/chats/<id>.json`` so they survive
restarts and never touch the coding-agent transcripts. The OpenRouter API key lives in
the OS keychain under the name ``openrouter`` (see :mod:`asset_studio.keychain`).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

import httpx

from . import fsutil
from .config import DATA_DIR, settings
from . import keychain

OPENROUTER = "https://openrouter.ai/api/v1"
_HEADERS_EXTRA = {"HTTP-Referer": "http://127.0.0.1:8777", "X-Title": "Asset Studio"}
_MODELS_CACHE: dict = {"ts": 0.0, "data": None}


def _chats_dir() -> Path:
    d = DATA_DIR / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(cid: str) -> Path:
    safe = "".join(c for c in cid if c.isalnum() or c in "-_")
    return _chats_dir() / f"{safe}.json"


def get_key() -> Optional[str]:
    return keychain.get_key("openrouter")


def set_key(value: str) -> None:
    keychain.set_key("openrouter", (value or "").strip())


def has_key() -> bool:
    return bool(get_key())


# --- conversation storage ---------------------------------------------------
def _load(cid: str) -> Optional[dict]:
    try:
        return json.loads(_path(cid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(conv: dict) -> None:
    p = _path(conv["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
    fsutil.replace(tmp, p)


def list_conversations() -> list[dict]:
    out = []
    for f in _chats_dir().glob("*.json"):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": c.get("id"), "title": c.get("title") or "New chat",
                    "model": c.get("model", ""), "updated": c.get("updated", 0),
                    "messages": len(c.get("messages") or [])})
    out.sort(key=lambda x: x.get("updated") or 0, reverse=True)
    return out


def get_conversation(cid: str) -> Optional[dict]:
    return _load(cid)


def create_conversation(model: str, title: str = "") -> dict:
    now = time.time()
    conv = {"id": uuid.uuid4().hex[:12], "title": title or "New chat",
            "model": model or "", "created": now, "updated": now, "messages": []}
    _save(conv)
    return conv


def delete_conversation(cid: str) -> bool:
    try:
        _path(cid).unlink()
        return True
    except OSError:
        return False


def rename_conversation(cid: str, title: str) -> Optional[dict]:
    conv = _load(cid)
    if not conv:
        return None
    conv["title"] = (title or "").strip()[:120] or "New chat"
    conv["updated"] = time.time()
    _save(conv)
    return conv


def _append(conv: dict, role: str, content: str, model: str = "", attachments: Optional[list] = None) -> None:
    msg = {"role": role, "content": content, "ts": time.time()}
    if model:
        msg["model"] = model
    if attachments:
        msg["attachments"] = attachments
    conv.setdefault("messages", []).append(msg)
    conv["updated"] = time.time()


# --- attachments (images → vision, code/text → context) ---------------------
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _uploads_dir() -> Path:
    d = _chats_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(filename: str, data: bytes) -> dict:
    src = Path(filename or "file")
    ext = src.suffix.lower()
    safe = "".join(c for c in src.stem if c.isalnum() or c in " ._-").strip()[:40] or "file"
    name = f"{int(time.time() * 1000)}-{safe}{ext}"
    (_uploads_dir() / name).write_bytes(data)
    kind = "image" if ext in _IMAGE_EXT else "text"
    return {"name": name, "orig": src.name, "kind": kind, "url": f"/api/chat/file?name={name}"}


def file_path(name: str) -> Optional[Path]:
    safe = "".join(c for c in (name or "") if c.isalnum() or c in ".-_")
    p = (_uploads_dir() / safe).resolve()
    return p if (p.exists() and _uploads_dir().resolve() in p.parents) else None


def _build_content(msg: dict):
    """OpenRouter message content: plain string, or a multimodal array when the message
    carries attachments (image_url parts for images, file text appended for code/text)."""
    atts = msg.get("attachments") or []
    if not atts:
        return msg.get("content", "")
    import base64
    import mimetypes
    parts: list = [{"type": "text", "text": msg.get("content", "")}]
    extra_text = []
    for a in atts:
        p = _uploads_dir() / a.get("name", "")
        if not p.exists():
            continue
        if a.get("kind") == "image":
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        else:
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")[:24000]
                extra_text.append(f"\n\n--- Attached file: {a.get('orig', a.get('name'))} ---\n{txt}")
            except OSError:
                pass
    if extra_text:
        parts[0]["text"] = (parts[0]["text"] or "") + "".join(extra_text)
    return parts


# --- model catalogue --------------------------------------------------------
def list_models(force: bool = False) -> dict:
    """All OpenRouter models, each flagged free/paid, with context length. Cached 1h."""
    now = time.time()
    if not force and _MODELS_CACHE["data"] and now - _MODELS_CACHE["ts"] < 3600:
        return _MODELS_CACHE["data"]
    key = get_key()
    headers = {**_HEADERS_EXTRA}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = httpx.get(f"{OPENROUTER}/models", headers=headers, timeout=20)
        r.raise_for_status()
        raw = r.json().get("data", [])
    except Exception as e:
        return {"ok": False, "error": str(e), "models": [], "has_key": bool(key)}
    models = []
    for m in raw:
        pr = m.get("pricing") or {}
        free = (str(pr.get("prompt", "0")) in ("0", "0.0")) and (str(pr.get("completion", "0")) in ("0", "0.0"))
        models.append({
            "id": m.get("id"), "name": m.get("name") or m.get("id"),
            "free": free or str(m.get("id", "")).endswith(":free"),
            "context": m.get("context_length") or (m.get("top_provider") or {}).get("context_length") or 0,
            "in_price": pr.get("prompt"), "out_price": pr.get("completion"),
        })
    models.sort(key=lambda x: (not x["free"], x["name"].lower()))
    data = {"ok": True, "models": models, "has_key": bool(key)}
    _MODELS_CACHE.update(ts=now, data=data)
    return data


# --- streaming chat ---------------------------------------------------------
def stream_chat(cid: str, user_message: str, model: str, attachments: Optional[list] = None) -> Iterator[str]:
    """Append the user message (+ any attachments), stream the model's reply (yielding text
    deltas), then save the assistant message. Yields JSON lines: {"delta"} / {"error"} /
    {"title","model"} / {"done": true}."""
    conv = _load(cid)
    if conv is None:
        yield json.dumps({"error": "conversation not found"}) + "\n"
        return
    key = get_key()
    if not key:
        yield json.dumps({"error": "No OpenRouter API key set. Add one in the Ask AI tab."}) + "\n"
        return
    model = model or conv.get("model") or "deepseek/deepseek-chat"
    conv["model"] = model
    _append(conv, "user", user_message, attachments=attachments)
    if (conv.get("title") or "New chat") == "New chat":
        conv["title"] = user_message.strip().replace("\n", " ")[:60] or "New chat"
    _save(conv)
    yield json.dumps({"title": conv["title"], "model": model}) + "\n"

    payload = {
        "model": model,
        "messages": [{"role": m["role"], "content": _build_content(m)} for m in conv["messages"]],
        "stream": True,
        # Cap the reply so OpenRouter doesn't pre-authorize the model's FULL budget (e.g. 16k),
        # which 402s accounts with limited credit. Tune via settings["chat_max_tokens"].
        "max_tokens": int(settings.get("chat_max_tokens", 2048) or 2048),
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **_HEADERS_EXTRA}
    full = []
    try:
        with httpx.stream("POST", f"{OPENROUTER}/chat/completions", json=payload,
                          headers=headers, timeout=httpx.Timeout(180.0, connect=20.0)) as resp:
            if resp.status_code != 200:
                resp.read()
                if resp.status_code == 402:
                    yield json.dumps({"error": "Out of OpenRouter credits for this paid model. Pick a ★ "
                                      "free model (no credits needed), or add credits at openrouter.ai/settings/credits."}) + "\n"
                else:
                    msg = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
                    yield json.dumps({"error": f"OpenRouter error: {msg}"}) + "\n"
                return
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (((obj.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                if delta:
                    full.append(delta)
                    yield json.dumps({"delta": delta}) + "\n"
    except Exception as e:
        yield json.dumps({"error": f"stream failed: {e}"}) + "\n"
    finally:
        text = "".join(full)
        if text:
            fresh = _load(cid) or conv          # reload in case it changed
            _append(fresh, "assistant", text, model=model)
            _save(fresh)
        yield json.dumps({"done": True}) + "\n"

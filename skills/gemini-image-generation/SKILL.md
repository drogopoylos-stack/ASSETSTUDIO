---
name: gemini-image-generation
description: Use when generating or editing images with Google Gemini's API ("Nano Banana" / Nano Banana Pro) — model ids, the generateContent image endpoint, and reference images.
metadata:
  category: backend
  updated: 2026-06-17
  confidence: verified (model ids rotate)
  source: https://ai.google.dev/gemini-api/docs/image-generation
disable-model-invocation: true
---

# Google Gemini Image Generation ("Nano Banana")

Google's image models in the Gemini API, nicknamed **"Nano Banana"**:
- **`gemini-2.5-flash-image`** — Nano Banana (GA; fast generate + edit).
- **`gemini-3-pro-image-preview`** — Nano Banana **Pro** (higher quality; preview).
- Older: `gemini-2.5-flash-image-preview`, `gemini-2.0-flash-preview-image-generation`.

⚠️ Preview ids rotate as they graduate — if one 404s, try another.

## Endpoint
`POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=API_KEY`

You MUST request the IMAGE modality, and attach any reference images inline (base64):
```json
{
  "contents": [{ "parts": [
    { "text": "your prompt" },
    { "inline_data": { "mime_type": "image/png", "data": "<base64>" } }
  ]}],
  "generationConfig": { "responseModalities": ["IMAGE", "TEXT"] }
}
```
- **Text → image:** just the `text` part.
- **Edit / references:** append one or more `inline_data` parts (multiple images → blend/condition).

## Read the result (note the camelCase trap)
The image returns inline; the key may be `inline_data` OR `inlineData`:
```python
parts = resp.json()["candidates"][0]["content"]["parts"]
img = next(p for p in parts if p.get("inline_data") or p.get("inlineData"))
png = base64.b64decode((img.get("inline_data") or img["inlineData"])["data"])
```

## Minimal Python
```python
import base64, httpx
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
parts = [{"text": prompt}] + [
    {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(open(p, "rb").read()).decode()}}
    for p in refs
]
r = httpx.post(url, params={"key": key},
    json={"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}},
    timeout=180)
```
Cost ≈ $0.04/image. Key from Google AI Studio. (Pairs with the `openai-image-generation` skill.)

---
name: openai-image-generation
description: Use when generating or editing images with OpenAI's API (GPT Image / gpt-image-2 / gpt-image-1) — model ids, generations vs edits, reference images, sizes, and output format.
metadata:
  category: backend
  updated: 2026-06-17
  confidence: verified
  source: https://developers.openai.com/api/docs/models/gpt-image-2
disable-model-invocation: true
---

# OpenAI Image Generation (GPT Image)

OpenAI's current image model is **`gpt-image-2`** (released 2026-04-21; pinned snapshot `gpt-image-2-2026-04-21`) — natively multimodal, with a reasoning "thinking mode" and high-fidelity image **inputs**. The predecessor **`gpt-image-1`** (April 2025) still works. **DALL·E 2 / 3 are deprecated** (support ends 2026-05-12). Both GPT Image models always return **base64 PNG** (`data[0].b64_json`), never URLs.

## Endpoints
- **Text → image:** `POST https://api.openai.com/v1/images/generations` (JSON body)
  ```json
  { "model": "gpt-image-2", "prompt": "...", "size": "1024x1024", "quality": "high", "background": "auto", "n": 1 }
  ```
- **Edit / reference image(s):** `POST https://api.openai.com/v1/images/edits` (multipart/form-data)
  - One reference → form field **`image`** (a file).
  - Multiple references → repeat field **`image[]`**, one file each (blend/condition on several inputs).
  - Plus form fields `model`, `prompt`, `size`, `n`.

Auth on both: header `Authorization: Bearer <OPENAI_API_KEY>`.

## Params
- `size`: `1024x1024`, `1536x1024` (landscape), `1024x1536` (portrait), or `auto`.
- `quality`: `low | medium | high | auto`.
- `background`: `auto | transparent | opaque` (transparent PNGs supported).
- Cost ≈ **$0.04–0.17 / image**.

## Minimal Python (httpx)
```python
import base64, httpx
H = {"Authorization": f"Bearer {key}"}

# text -> image
r = httpx.post("https://api.openai.com/v1/images/generations", headers=H,
    json={"model": "gpt-image-2", "prompt": p, "size": "1024x1024", "n": 1}, timeout=300)

# OR condition on 1+ reference images:
field = "image[]" if len(refs) > 1 else "image"
files = [(field, (name, open(path, "rb").read(), "image/png")) for path in refs]
r = httpx.post("https://api.openai.com/v1/images/edits", headers=H,
    data={"model": "gpt-image-2", "prompt": p, "size": "1024x1024", "n": "1"},
    files=files, timeout=300)

png = base64.b64decode(r.json()["data"][0]["b64_json"])
```

## Gotchas
- Output is base64 — decode it before writing the file.
- Single reference uses field `image`; multiple use `image[]`.
- Model ids rotate as they leave preview — if a call 404s on the model, try the other id (`gpt-image-1` ⇄ `gpt-image-2`). **Re-verify the current id periodically.**

"""Asset judging for the autonomous agent loop.

Two backends:
  * ``heuristic`` — deterministic, dependency-light image metrics (sharpness,
    contrast, subject coverage). Always available; no key, no network.
  * vision API — if an OpenAI or Gemini key is present, ask a multimodal model to
    score the asset against the goal and return structured feedback.

For 3D assets we first render a QA frame (via the software renderer) and judge
that image — so the agent literally *sees* the model, exactly as requested.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import numpy as np

from .. import keychain
from ..models import Asset, AgentGoal, AssetType, JudgeResult


def _image_for(asset: Asset) -> str | None:
    if asset.type in (AssetType.image, AssetType.atlas, AssetType.render, AssetType.texture):
        return asset.path
    if asset.preview_path and Path(asset.preview_path).exists():
        return asset.preview_path
    if asset.type == AssetType.model:
        try:
            from ..render.software_render import render_views

            frames = render_views(asset.path, n=1, size=448)
            out = Path(asset.path).with_suffix(".judge.png")
            frames[0].save(out, "PNG")
            return str(out)
        except Exception:
            return None
    return None


def _heuristic(image_path: str) -> JudgeResult:
    from PIL import Image

    im = Image.open(image_path).convert("RGBA")
    arr = np.asarray(im).astype(np.float32)
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]

    gray = rgb.mean(axis=2)
    # sharpness: variance of a Laplacian
    lap = (
        -4 * gray
        + np.roll(gray, 1, 0) + np.roll(gray, -1, 0)
        + np.roll(gray, 1, 1) + np.roll(gray, -1, 1)
    )
    sharp = float(np.var(lap))
    sharp_n = min(sharp / 800.0, 1.0)

    contrast = float(gray.std()) / 128.0
    contrast_n = min(contrast, 1.0)

    if (alpha < 250).any():
        subject = float((alpha > 16).mean())
        subject_n = 1.0 - abs(subject - 0.5) * 1.2  # reward ~half-filled framing
    else:
        # opaque: penalise near-empty / flat frames using non-bg variance
        subject_n = min(contrast_n + 0.3, 1.0)
    subject_n = max(0.0, min(subject_n, 1.0))

    colorful = float(rgb.std(axis=(0, 1)).mean()) / 90.0
    colorful_n = min(colorful, 1.0)

    score = 0.42 * sharp_n + 0.24 * contrast_n + 0.2 * subject_n + 0.14 * colorful_n
    score = round(max(0.0, min(score, 1.0)), 3)
    reasoning = (
        f"heuristic — sharpness={sharp_n:.2f}, contrast={contrast_n:.2f}, "
        f"subject={subject_n:.2f}, color={colorful_n:.2f}"
    )
    sugg = ""
    if sharp_n < 0.4:
        sugg += "increase detail/sharpness; "
    if subject_n < 0.4:
        sugg += "make the subject fill more of the frame, clean background; "
    if colorful_n < 0.3:
        sugg += "add more color contrast; "
    return JudgeResult(score=score, accept=False, reasoning=reasoning, suggestions=sugg.strip())


def _openai_vision(image_path: str, goal: AgentGoal, key: str) -> JudgeResult | None:
    import httpx

    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    rubric = (
        "You are a strict game-art director. Score this asset 0..1 for how well it "
        f"matches the brief: '{goal.prompt}'. Judge clarity, composition, usability as a "
        "game asset, and absence of artifacts. Reply with JSON: "
        '{"score": <0..1>, "reasoning": "...", "suggestions": "concrete reprompt advice"}.'
    )
    try:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": rubric},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }],
                "max_tokens": 300,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        return JudgeResult(
            score=float(data.get("score", 0)),
            accept=False,
            reasoning="gpt-4o-mini: " + str(data.get("reasoning", "")),
            suggestions=str(data.get("suggestions", "")),
        )
    except Exception:
        return None


def _gemini_vision(image_path: str, goal: AgentGoal, key: str) -> JudgeResult | None:
    import httpx

    b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    rubric = (
        "Act as a strict game-art director. Score this asset 0..1 against the brief: "
        f"'{goal.prompt}'. Return ONLY JSON "
        '{"score":0..1,"reasoning":"...","suggestions":"..."}.'
    )
    try:
        r = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={key}",
            json={
                "contents": [{
                    "parts": [
                        {"text": rubric},
                        {"inline_data": {"mime_type": "image/png", "data": b64}},
                    ]
                }]
            },
            timeout=60,
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip().lstrip("`json").rstrip("`").strip()
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return JudgeResult(
            score=float(data.get("score", 0)),
            accept=False,
            reasoning="gemini: " + str(data.get("reasoning", "")),
            suggestions=str(data.get("suggestions", "")),
        )
    except Exception:
        return None


def judge_asset(asset: Asset, goal: AgentGoal) -> JudgeResult:
    image_path = _image_for(asset)
    if not image_path or not Path(image_path).exists():
        return JudgeResult(score=0.0, reasoning="no viewable image to judge", accept=False)

    mode = goal.judge or "auto"
    result: JudgeResult | None = None
    if mode in ("auto", "openai", "gpt") and keychain.has_key("openai"):
        result = _openai_vision(image_path, goal, keychain.get_key("openai"))
    if result is None and mode in ("auto", "gemini", "nanobanana") and keychain.has_key("gemini"):
        result = _gemini_vision(image_path, goal, keychain.get_key("gemini"))
    if result is None:
        result = _heuristic(image_path)

    result.accept = result.score >= goal.accept_threshold
    return result

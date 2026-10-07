"""Turn raw exceptions/tracebacks into a short, actionable hint for the UI.

Always falls back to the raw error, so nothing is hidden — the hint is an extra
friendly line, not a replacement.
"""
from __future__ import annotations

import re
from typing import Optional


def classify_error(error_text: str, provider=None) -> str:
    t = (error_text or "").lower()
    pname = getattr(provider, "name", "") if provider else ""

    rules: list[tuple[str, str]] = [
        (r"out of memory|cuda error|cublas|cudnn|alloc",
         "GPU ran out of VRAM. Lower the resolution / step count, close other GPU apps, or use an API provider."),
        (r"connection refused|connect.*timed out|connecterror|failed to establish|max retries|getaddrinfo|"
         r"name or service not known|actively refused",
         f"Couldn't reach the local server for {pname or 'this provider'}. Start it from the Servers tab "
         "(or check the URL in Settings)."),
        (r"401|unauthor|invalid api key|incorrect api key|forbidden|403|invalid_api_key",
         "API key looks wrong or missing. Re-enter it in Settings → API Keys."),
        (r"429|rate limit|quota|insufficient_quota|billing",
         "The API rate-limited or quota/billing was hit. Wait and retry, or check your account."),
        (r"no module named|modulenotfound|importerror",
         "A Python dependency is missing. Install the optional deps for this provider (see requirements-optional.txt)."),
        (r"blender.*not|no such file.*blender|'blender' is not recognized",
         "Blender wasn't found. Set tools.blender_path in Settings to your Blender executable."),
        (r"gltf-transform.*not|'gltf-transform' is not recognized",
         "gltf-transform CLI not found. Install it: npm i -g @gltf-transform/cli (a no-compression fallback is used otherwise)."),
        (r"needs an input|requires.*input|no input image|input 3d model|attach an input",
         "This stage needs an input asset. Pick one from the catalog before running."),
        (r"api key is missing|add the .* api key",
         "Add the provider's API key in Settings → API Keys."),
        (r"timed out|timeout",
         "The operation timed out — the server/API was too slow or unreachable. Retry, or start the local server."),
        (r"disk|no space",
         "Low disk space. Free some space or lower min_free_gb in Settings."),
    ]
    for pattern, hint in rules:
        if re.search(pattern, t):
            return hint
    return ""

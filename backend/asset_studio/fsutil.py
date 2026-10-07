"""Atomic file replace that survives Windows' sharing rules.

`os.replace(tmp, path)` is atomic, but on Windows it FAILS with `PermissionError` [WinError 5]
whenever another handle has `path` open at that instant — a feed poll reading the same JSON, the
indexer, an antivirus scan. The window is milliseconds wide and the reader lets go almost at once,
so the right answer is to wait a moment and try again, not to fail the request.

What it looked like here (backend.log, 2026-10-06): a Codex send returned HTTP 500 because
`codex_app._save_index` could not replace `data/codex/projects/<folder>.json` while a poll was
reading it. The turn never started and the message was lost.
"""
from __future__ import annotations

import os
import time

# About 1.3 s in total before giving up. A reader holds the file for far less than that; a lock
# that lasts longer is a real problem, and the caller should see the error.
_DELAYS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.6)


def replace(src, dst) -> None:
    """`os.replace(src, dst)`, retried on Windows while another handle briefly holds `dst`."""
    for delay in _DELAYS:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(delay)
    os.replace(src, dst)

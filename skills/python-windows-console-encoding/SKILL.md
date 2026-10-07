---
name: python-windows-console-encoding
description: Use when a Python script on Windows crashes with "UnicodeEncodeError: 'charmap' codec can't encode character" while printing emoji/arrows/Unicode, or writes garbled text files.
metadata:
  category: general
  updated: 2026-06-19
  confidence: verified
  source: experience
disable-model-invocation: true
---

# Python on Windows: console & file encoding (cp1252) gotchas

## The crash
```python
print("↪ done")
# UnicodeEncodeError: 'charmap' codec can't encode character '↪' ...
#                     character maps to <undefined>
```
Windows defaults `sys.stdout` to the legacy ANSI code page (**cp1252**), which can't
represent most non-Latin-1 characters (arrows, emoji, box-drawing, CJK). Key insight: the
program **logic ran fine** — only the final `print` to the console failed. Don't "fix" it by
stripping characters; fix the **output encoding**.

## Fixes (pick by situation)
- **One-off / CI / spawning the process:** set the env var before launch —
  - bash: `PYTHONIOENCODING=utf-8 python script.py`
  - PowerShell: `$env:PYTHONIOENCODING="utf-8"; python script.py`
  - cmd: `set PYTHONIOENCODING=utf-8 && python script.py`
- **Inside the script (portable, Python 3.7+):**
  ```python
  import sys
  sys.stdout.reconfigure(encoding="utf-8")
  ```
- **Globally enable UTF-8 mode (3.7+):** run `python -X utf8 script.py` or set
  `PYTHONUTF8=1`. This also makes `open()` default to UTF-8 instead of cp1252.
- **Writing files:** ALWAYS pass `encoding="utf-8"` to `open(...)`. On Windows the default
  text encoding is cp1252, so any Unicode silently mangles (or raises) without it.

## Gotchas
- `subprocess` capturing a child's stdout: decode with
  `subprocess.run(..., text=True, encoding="utf-8", errors="replace")` rather than trusting
  the locale (raw bytes are safest, decode yourself).
- The **same script can pass in one shell and crash in another**: Git Bash on Windows is
  usually UTF-8; `cmd.exe`/PowerShell consoles often are not.
- Python 3.15 is slated to make UTF-8 mode the default — until you can rely on that, set it
  explicitly. (Re-verify this version claim over time.)
- Redirecting to a file (`> out.txt`) uses `PYTHONIOENCODING`/locale too, so the env-var fix
  covers both console and pipe output.

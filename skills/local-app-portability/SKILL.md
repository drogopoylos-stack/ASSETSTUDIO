---
name: local-app-portability
description: Use when shipping/copying a local desktop app (Electron + Python venv + Vite build) to another machine — what travels, what must be rebuilt, and the stale-UI trap.
metadata:
  category: general
  updated: 2026-06-20
  confidence: verified
  source: experience
disable-model-invocation: true
---

# Porting a Local App (Electron + Python + Vite) to Another PC

What a plain zip gets wrong, and the reliable recipe.

## NOT portable — recreate on the target
- **Python `.venv`** — machine-specific paths/native binaries; `pyvenv.cfg` points at a base Python install. Copying it breaks. Recreate: `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt`.
- **`node_modules`** — native bindings, platform-specific. Recreate: `npm install`.

## The stale-UI trap (the #1 "my changes aren't there" cause)
The app serves the **built** frontend (`frontend/dist`), not the source — and `dist` is usually **gitignored**. So:
- A zip made by a git-aware tool **omits `dist`** → target has no/old UI.
- A copied old `dist` is served as-is unless you rebuild.

**Fix:** on the target, rebuild from current source: `npm run build`. Never trust a copied `dist`; rebuild from the source you actually shipped.

## Other gotchas
- **API keys** in the OS keychain (Windows Credential Manager / macOS Keychain) **don't travel** — re-enter on the new machine.
- **Electron main-process** changes (preload, `setWindowOpenHandler`, menus) need a full app **restart** — a renderer reload (Ctrl+R) is not enough.

## Reliable recipe
1. Copy the source; **skip** `.venv` and `node_modules`.
2. On target: `python -m venv` + `pip install -r requirements.txt`; `npm install`; `npm run build`.
3. Re-enter API keys; install any external CLIs the app shells out to.

## No-install portable bundle for Windows (embeddable Python, Defender-safe)
When the target PC has **no Python** and the app must launch by double-click without antivirus blocking it:
- **Do NOT ship a PyInstaller one-file `.exe`** — packed single-file exes are the #1 false-positive trigger for Defender/SmartScreen. Ship a **plain folder: embeddable Python + a `.bat` launcher**. Loose `python.exe` + scripts aren't quarantined like a packed exe (a downloaded zip may still show one SmartScreen "More info → Run anyway").
- Build recipe (reproducible):
  1. Download `python-<ver>-embed-amd64.zip` from python.org/ftp, extract to `runtime/`.
  2. Edit `runtime/python3XX._pth` so site-packages and your app import — uncomment `import site` and add the paths:
     ```
     python3XX.zip
     .
     ..
     Lib\site-packages
     import site
     ```
     (`..` puts the app root — parent of `runtime/` — on `sys.path` so `import app` works.)
  3. Bootstrap pip: download `get-pip.py` from bootstrap.pypa.io, run `runtime\python.exe get-pip.py`.
  4. `runtime\python.exe -m pip install -r requirements.txt`.
  5. Bundle a Playwright browser offline: `PLAYWRIGHT_BROWSERS_PATH=<dist>\ms-playwright runtime\python.exe -m playwright install chromium`; the launcher sets the same env var at runtime so Chromium is found.
  6. Zip with `System.IO.Compression.ZipFile.CreateFromDirectory(..., Fastest)` — far faster than `Compress-Archive` for many small files.
- **Launcher `.bat`:** detect `runtime\python.exe` (portable) else fall back to a system-Python venv; set `PLAYWRIGHT_BROWSERS_PATH` if `ms-playwright\` exists; `start "" http://127.0.0.1:8000` then `runtime\python.exe -m uvicorn app.main:app`.
- The embeddable distro **includes `_sqlite3.pyd`**, so SQLite apps work with no extra steps.
- Verify by running `runtime\python.exe` directly (it's fully independent of any system Python): import every dependency and boot the server on a test port before zipping.

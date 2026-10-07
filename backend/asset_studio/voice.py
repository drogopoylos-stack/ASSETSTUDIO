"""On-device speech-to-text for the chat composer — free, private, no API key.

Runs OpenAI Whisper locally via **faster-whisper**, installed self-contained into a dedicated venv
under the data dir (no pipx, no PATH, prebuilt wheels so NO C/C++ compiler is needed — same pattern
as graphify_index). Whisper's ``translate`` task turns ANY spoken language directly into ENGLISH in
one pass, so Greek speech → English text needs no separate translator.

A persistent worker process (the voice venv's python) loads the model ONCE and answers transcription
requests over a JSON-lines stdin/stdout protocol, so the multi-GB model isn't reloaded per click.
Nothing here runs on the event loop — the FastAPI voice endpoints are sync ``def`` handlers (FastAPI
threadpools those) and installs / model-loads run on background threads. Everything is lazy: nothing
is installed or loaded until the user actually turns voice on.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .config import DATA_DIR, settings

_NF = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_VENV_DIR = DATA_DIR / "tools" / "voice-venv"
_LOG = DATA_DIR / "voice.log"

_lock = threading.Lock()
_wlock = threading.Lock()   # serialize transcription requests to the single worker
_INSTALL: dict = {"installing": False, "done": False, "error": "", "started": 0.0}
_WORKER: dict = {"proc": None, "ready": False, "loading": False, "device": "", "model": "", "error": "", "last_use": 0.0}
_reaper_started = False


# The worker: load Whisper once, then per stdin JSON line → transcribe → one stdout JSON line.
# Writes ONLY JSON to stdout (logs/download progress go to stderr → the log file), and the reader
# skips any non-JSON line, so stray output can never corrupt the protocol.
_WORKER_SRC = r'''# ASSET_STUDIO_VOICE_WORKER  (marker so reap_orphans can find leftover workers by cmdline)
import sys, json, os

def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

# Windows: let CTranslate2 find the cuDNN/cuBLAS DLLs shipped by the nvidia pip wheels.
if os.name == "nt":
    import importlib.util
    _dirs = []
    for pkg in ("nvidia.cudnn", "nvidia.cublas"):
        try:
            spec = importlib.util.find_spec(pkg)
            if spec and spec.submodule_search_locations:
                base = list(spec.submodule_search_locations)[0]
                for sub in ("bin", "lib"):
                    d = os.path.join(base, sub)
                    if os.path.isdir(d):
                        _dirs.append(d)
        except Exception:
            pass
    if _dirs:
        # CTranslate2 uses the legacy LoadLibrary search on Windows, so the cuBLAS/cuDNN DLLs must be
        # on PATH — os.add_dll_directory alone is NOT enough (it only covers USER_DIRS). This is the
        # difference between GPU working and the "cublas64_12.dll not found" fall-back to CPU.
        os.environ["PATH"] = os.pathsep.join(_dirs) + os.pathsep + os.environ.get("PATH", "")
        for d in _dirs:
            try:
                os.add_dll_directory(d)
            except Exception:
                pass

model_name = sys.argv[1] if len(sys.argv) > 1 else "large-v3"

from faster_whisper import WhisperModel

want_gpu = False
try:
    import ctranslate2
    want_gpu = ctranslate2.get_cuda_device_count() > 0
except Exception:
    pass

def _load_and_verify(dev, comp):
    m = WhisperModel(model_name, device=dev, compute_type=comp)
    import numpy as np   # force REAL compute so a missing cuBLAS/cuDNN fails HERE, not on the user
    list(m.transcribe(np.zeros(16000, dtype=np.float32), vad_filter=False, beam_size=1)[0])
    return m

# Try GPU first (fast), but verify it actually computes; on any failure fall back to CPU, which
# always works. So "ready" always reflects a device that genuinely transcribes.
model = None; device = "cpu"; last = ""
for dev, comp in ([("cuda", "float16"), ("cpu", "int8")] if want_gpu else [("cpu", "int8")]):
    try:
        out({"stage": "loading", "device": dev, "model": model_name})
        model = _load_and_verify(dev, comp); device = dev; break
    except Exception as e:
        last = str(e)
if model is None:
    out({"ready": False, "error": ("load failed: " + last)[:300]}); sys.exit(1)
out({"ready": True, "device": device})

while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    try:
        segs, info = model.transcribe(req["audio"], task=req.get("task", "translate"),
                                      vad_filter=True, beam_size=5)
        text = "".join(s.text for s in segs).strip()
        out({"text": text, "language": getattr(info, "language", "") or ""})
    except Exception as e:
        out({"error": str(e)[:300]})
'''


def _py() -> Path:
    return _VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_installed() -> bool:
    py = _py()
    if not py.exists():
        return False
    try:
        r = subprocess.run([str(py), "-c", "import faster_whisper"], timeout=60,
                           creationflags=_NF, capture_output=True)
        return r.returncode == 0
    except Exception:
        return False


def _gpu_present() -> bool:
    return bool(shutil.which("nvidia-smi"))


def status() -> dict:
    return {
        "installed": _py().exists(),
        "installing": bool(_INSTALL["installing"]),
        "install_error": _INSTALL["error"],
        "ready": bool(_WORKER["ready"]),
        "loading": bool(_WORKER["loading"]),
        "device": _WORKER["device"],
        "model": _WORKER["model"] or (settings.get("voice_model") or "large-v3"),
        "worker_error": _WORKER["error"],
        "task": (settings.get("voice_task") or "translate"),
        # Which engine actually answers, and whether the cloud one is usable. The UI shows
        # "Groq (no key)" rather than letting the user discover it on their first dictation.
        "engine": str(settings.get("voice_engine") or "local").lower(),
        "groq_model": str(settings.get("voice_groq_model") or "whisper-large-v3"),
        "groq_key": bool(groq_key()),
    }


# --- install (self-contained venv) ------------------------------------------
def ensure_installed(block: bool = False, timeout: float = 1800.0) -> bool:
    if is_installed():
        _INSTALL["done"] = True
        return True
    start = False
    with _lock:
        if not _INSTALL["installing"]:
            _INSTALL.update(installing=True, error="", started=time.time())
            start = True
    if start:
        t = threading.Thread(target=_do_install, daemon=True)
        t.start()
        if block:
            t.join(timeout)
    return is_installed()


def _do_install() -> None:
    try:
        py = _py()
        if not py.exists():
            _VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)], timeout=180,
                           creationflags=_NF, capture_output=True)
        if not py.exists():
            raise RuntimeError("could not create the voice venv")
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
                       timeout=180, creationflags=_NF, capture_output=True)
        r = subprocess.run([str(py), "-m", "pip", "install", "--prefer-binary", "--quiet",
                            "faster-whisper"], timeout=1800, creationflags=_NF,
                           capture_output=True, text=True)
        chk = subprocess.run([str(py), "-c", "import faster_whisper"], timeout=120,
                             creationflags=_NF, capture_output=True, text=True)
        if chk.returncode != 0:
            raise RuntimeError((r.stderr or chk.stderr or "").strip()[:400] or "faster-whisper install failed")
        # GPU accel (best-effort): cuDNN/cuBLAS wheels so CTranslate2 can use CUDA on this machine.
        if _gpu_present():
            try:
                subprocess.run([str(py), "-m", "pip", "install", "--prefer-binary", "--quiet",
                                "nvidia-cublas-cu12", "nvidia-cudnn-cu12"], timeout=1800,
                               creationflags=_NF, capture_output=True)
            except Exception:
                pass
        _INSTALL.update(done=True, error="")
    except Exception as e:
        _INSTALL["error"] = str(e)[:400] or "voice install failed"
    finally:
        _INSTALL["installing"] = False


# --- persistent Whisper worker ----------------------------------------------
def ensure_worker(block: bool = False, timeout: float = 1800.0) -> None:
    """Start (or restart) the persistent Whisper worker so the model is loaded and ready. Restarts
    it if the configured model changed. Returns immediately (load runs on a background thread)."""
    if not is_installed():
        ensure_installed()
        return
    want = settings.get("voice_model") or "large-v3"
    with _wlock:
        p = _WORKER["proc"]
        alive = p is not None and p.poll() is None
        if alive and _WORKER["model"] == want and (_WORKER["ready"] or _WORKER["loading"]):
            pass   # already up (or coming up) with the right model
        else:
            if alive:
                try:
                    p.terminate()
                except Exception:
                    pass
            _WORKER.update(proc=None, ready=False, loading=True, error="", model=want, device="")
            threading.Thread(target=_spawn_worker, args=(want,), daemon=True).start()
    if block:
        end = time.time() + timeout
        while _WORKER["loading"] and time.time() < end:
            time.sleep(0.5)


def _spawn_worker(model_name: str) -> None:
    try:
        logf = open(_LOG, "ab", buffering=0)
    except Exception:
        logf = subprocess.DEVNULL
    try:
        proc = subprocess.Popen([str(_py()), "-u", "-c", _WORKER_SRC, model_name],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=logf,
                                creationflags=_NF, text=True, encoding="utf-8")
    except Exception as e:
        _WORKER.update(loading=False, ready=False, error=str(e)[:300])
        return
    _WORKER["proc"] = proc
    while True:   # read the handshake: stage line(s) then {"ready": ...}
        line = proc.stdout.readline()
        if not line:
            _WORKER.update(loading=False, ready=False,
                           error=_WORKER["error"] or "voice worker exited during model load")
            return
        try:
            o = json.loads(line.strip())
        except Exception:
            continue
        if "stage" in o:
            _WORKER["device"] = o.get("device", "")
            continue
        if o.get("ready"):
            _WORKER.update(ready=True, loading=False, device=o.get("device", _WORKER["device"]),
                           error="", last_use=time.time())
            _start_idle_reaper()
            return
        if "ready" in o:   # ready == False
            _WORKER.update(ready=False, loading=False, error=o.get("error", "model load failed"))
            return


GROQ_BASE = "https://api.groq.com/openai/v1/audio"
GROQ_MAX_BYTES = 25 * 1024 * 1024      # free-tier upload ceiling; bigger needs the paid tier


def groq_key() -> str:
    """The Groq key, preferring a voice-specific slot but falling back to the chat provider's.

    Most people who have a Groq key already pasted it once for chat; making them paste the same
    key twice to use the same account is friction for no benefit.
    """
    from . import keychain
    for name in ("voice:groq", "chat:groq"):
        try:
            k = keychain.get_key(name)
        except Exception:
            k = None
        if k:
            return k.strip()
    return ""


def _groq_transcribe(audio: bytes, filename: str, task: str) -> dict:
    """Send one clip to Groq and return the same {'text','language'} the local worker returns.

    Two endpoints, not one: /translations always answers in English, /transcriptions answers in
    the language spoken. voice_task decides which, exactly as it does locally.

    The model is pinned to whisper-large-v3 for translate whatever the setting says, because
    Groq's translations endpoint rejects whisper-large-v3-turbo outright — the distilled model
    has no translation head. Silently sending turbo there would fail every Greek dictation.
    """
    key = groq_key()
    if not key:
        return {"error": "No Groq API key. Settings -> General -> Voice input, or switch the "
                         "engine back to Local."}
    if len(audio) > GROQ_MAX_BYTES:
        return {"error": f"Clip is {len(audio) / 1e6:.1f} MB; Groq accepts 25 MB. "
                         f"Record something shorter or use the Local engine."}
    translating = task == "translate"
    model = str(settings.get("voice_groq_model") or "whisper-large-v3").strip()
    if translating and model.endswith("turbo"):
        model = "whisper-large-v3"
    url = f"{GROQ_BASE}/{'translations' if translating else 'transcriptions'}"
    try:
        import httpx
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename or "audio.webm", audio, "application/octet-stream")},
            data={"model": model, "response_format": "verbose_json", "temperature": "0"},
            timeout=httpx.Timeout(connect=15.0, read=120.0, write=120.0, pool=15.0),
        )
    except Exception as e:
        return {"error": f"Groq unreachable ({type(e).__name__}). Check the network, or switch "
                         f"the engine back to Local."}
    if r.status_code == 401:
        return {"error": "Groq rejected the API key (401). Check it in Settings."}
    if r.status_code == 429:
        return {"error": "Groq rate limit reached — wait a moment, or switch to Local."}
    if r.status_code != 200:
        detail = ""
        try:
            detail = str((r.json().get("error") or {}).get("message") or "")[:200]
        except Exception:
            detail = r.text[:200]
        return {"error": f"Groq error {r.status_code}: {detail}"}
    try:
        d = r.json()
    except Exception:
        return {"error": "Groq returned a response that was not JSON."}
    text = (d.get("text") or "").strip()
    if not text:
        return {"error": "Groq heard nothing in that clip."}
    # /translations reports the language it heard, not the language it produced.
    return {"text": text, "language": d.get("language") or ("en" if translating else "")}


def transcribe(audio: bytes, filename: str = "audio.webm", task: str = "") -> dict:
    """Blocking transcription (called from a threadpool endpoint). Ensures the worker is up, writes
    the audio to a temp file, sends one request, returns ``{'text','language'}`` or ``{'error'}``."""
    # Normalise the task BEFORE branching: the cloud engine picks its endpoint from it, and the
    # local path re-uses the same value below.
    task = (task or settings.get("voice_task") or "translate").lower()
    if task not in ("translate", "transcribe"):
        task = "translate"
    # Cloud engine short-circuit. Deliberately above the install and worker checks — Groq needs
    # neither faster-whisper installed nor a model resident, and gating it behind a local install
    # would make the cloud option depend on the very thing it exists to avoid.
    if str(settings.get("voice_engine") or "local").lower() == "groq":
        return _groq_transcribe(audio, filename, task)
    if not is_installed():
        ensure_installed()
        return {"error": "Voice is still installing — try again in a moment."}
    ensure_worker()
    end = time.time() + 8   # brief wait; on first use the model may still be downloading/loading
    while _WORKER["loading"] and time.time() < end:
        time.sleep(0.3)
    if not _WORKER["ready"]:
        if _WORKER["error"]:
            return {"error": "Voice model failed to load: " + _WORKER["error"]}
        return {"error": "Voice model is still loading (first-time model download) — try again shortly."}
    suffix = os.path.splitext(filename)[1] or ".webm"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=suffix, dir=str(DATA_DIR))
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        with _wlock:
            p = _WORKER["proc"]
            if p is None or p.poll() is not None:
                _WORKER["ready"] = False
                return {"error": "Voice worker not running — try again."}
            _WORKER["last_use"] = time.time()
            p.stdin.write(json.dumps({"audio": tmp, "task": task}) + "\n")
            p.stdin.flush()
            while True:
                line = p.stdout.readline()
                if not line:
                    _WORKER.update(ready=False)
                    return {"error": "Voice worker stopped unexpectedly."}
                try:
                    o = json.loads(line.strip())
                except Exception:
                    continue
                if "text" in o or "error" in o:
                    return o
    except Exception as e:
        return {"error": str(e)[:300]}
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _start_idle_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True
    threading.Thread(target=_idle_reaper_loop, daemon=True).start()


def _idle_reaper_loop() -> None:
    """Unload the Whisper model (freeing its GPU VRAM — several GB) after it's sat idle; it reloads on
    the next transcribe. Keeps voice from permanently holding VRAM on an 8 GB card shared with 3D gen."""
    while True:
        time.sleep(20)
        try:
            secs = float(settings.get("voice_idle_unload_seconds", 180) or 0)
            if secs <= 0:
                continue
            with _wlock:
                p = _WORKER["proc"]
                if p is None or p.poll() is not None:
                    continue
                lu = _WORKER.get("last_use") or 0.0
                if lu and (time.time() - lu) > secs:
                    try:
                        if p.stdin:
                            p.stdin.close()
                    except Exception:
                        pass
                    try:
                        p.terminate()
                    except Exception:
                        pass
                    _WORKER.update(proc=None, ready=False, loading=False, device="", error="")
        except Exception:
            pass


def reap_orphans() -> int:
    """Kill leftover Whisper workers from a previous backend / a crash (they hold GPU VRAM). Identified
    PRECISELY by the ASSET_STUDIO_VOICE_WORKER marker in the -c script — never touches the user's own
    Python or ComfyUI. Called at startup, off the event loop."""
    try:
        import psutil
    except Exception:
        return 0
    me = os.getpid()
    cur = _WORKER.get("proc")
    cur_pid = cur.pid if cur is not None else -1
    killed = 0
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if p.pid in (me, cur_pid):
                continue
            cl = " ".join(p.info.get("cmdline") or [])
            if "ASSET_STUDIO_VOICE_WORKER" in cl:
                p.kill()
                killed += 1
        except Exception:
            continue
    if killed:
        print(f"[voice] reaped {killed} orphaned Whisper worker(s) that were holding GPU VRAM")
    return killed


def shutdown() -> None:
    p = _WORKER.get("proc")
    if p is not None:
        try:
            if p.stdin:
                p.stdin.close()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass

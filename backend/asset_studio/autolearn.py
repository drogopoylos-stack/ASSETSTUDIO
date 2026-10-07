"""Zero-token auto-learn: mine session transcripts for validated learnings and turn
them into personal skills — WITHOUT spending Claude tokens.

Two stages, both gated HARD on the ``cc_autolearn`` setting (toggle OFF = nothing
runs, nothing installs, nothing scans):

1. **Harvester** (pure Python, graphify-style): incrementally tails every Claude Code
   transcript under ``~/.claude/projects/*/*.jsonl`` and deterministically extracts
   candidate learnings:
     - *approval moments* — a short user message like "perfect / works / keep it /
       push it" right after Claude built something → bundle the recipe behind it
       (last assistant explanation + the recent file edits + last command).
     - *error→fix pairs* — a command that failed, then succeeded after edits →
       bundle the failing command, the error tail, and the edits that fixed it.
   Candidates land in ``data/autolearn/candidates.jsonl``; per-file byte offsets in
   ``state.json`` keep rescans O(new bytes).

2. **Distiller**: rewrites each candidate into a proper skill entry appended to
   ``~/.claude/skills/learned-<category>/SKILL.md`` (created disabled, so the user
   opts in from the Skills tab). Preferred writer is a LOCAL LLM — Qwen3-4B-Instruct
   GGUF via llama-cpp-python in a dedicated venv (CPU-only, below-normal priority:
   it must never touch VRAM or fight a game). If the model isn't installed/ready it
   falls back to a mechanical template, so the feature works either way.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from . import fsutil
from .config import DATA_DIR, claude_home, settings

_LOCK = threading.Lock()
_DIR = DATA_DIR / "autolearn"
_STATE_F = _DIR / "state.json"
_CAND_F = _DIR / "candidates.jsonl"
_VENV_DIR = DATA_DIR / "tools" / "autolearn-venv"
_MODEL_DIR = DATA_DIR / "models" / "llm"
# Qwen3-4B-Instruct: Apache-2.0, the strongest writer in the ~4B class, ~2.4 GB at Q4_K_M —
# small enough for background CPU inference, good enough to write clean skill entries.
_MODEL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
_MODEL_URLS = [
    "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    "https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
]
_CPU_WHEELS = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
_INSTALL: dict = {"installing": False, "error": "", "model_pct": 0, "step": ""}
_RUN: dict = {"scanning": False, "distilling": False, "last_scan": 0.0, "skills_written": 0}
_NF = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# the distiller must never compete with a game or the user's work: lowest sensible priority
_LOWPRI = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0

LOOP_TICK = 20.0        # how often the loop re-checks the toggle (OFF = this is ALL it does)
SCAN_EVERY = 180.0      # harvest cadence while ON
DISTILL_BATCH = 4       # candidates polished per cycle (keeps CPU bursts short)

# short, plain approval signals (EN + the user's Greek) — must be a SHORT standalone
# message, not buried praise inside a long instruction
APPROVAL_RE = re.compile(
    r"\b(perfect|works|working|it works|keep (it|this)|love (it|this)|i like (it|this)|looks good|"
    r"good now|great|awesome|nice one|well done|push it|ship it|thanks|thank you|"
    r"τέλεια|τελεια|ωραία|ωραια|μπράβο|μπραβο|δουλεύει|δουλευει)\b", re.I)
# ...but praise buried in a complaint or a new instruction is NOT approval ("kinda works now
# BUT...", "it's off AGAIN... thanks"). Any of these in the message → skip it.
NEG_RE = re.compile(
    r"\b(but|doesn'?t|don'?t|isn'?t|wasn'?t|can'?t|cannot|won'?t|not|broken|bug|issue|problem|"
    r"wrong|still|again|error|fails?|failing|crash|missing|remove|instead|make sure|should)\b", re.I)


# ───────────────────────── state ─────────────────────────

def _load_state() -> dict:
    try:
        st = json.loads(_STATE_F.read_text(encoding="utf-8"))
        if isinstance(st, dict):
            st.setdefault("files", {}); st.setdefault("seen", []); st.setdefault("done", [])
            st.setdefault("ctx", {})
            return st
    except Exception:
        pass
    return {"files": {}, "seen": [], "done": [], "ctx": {}}


def _save_state(st: dict) -> None:
    st["seen"] = st["seen"][-4000:]
    st["done"] = st["done"][-4000:]
    _DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    fsutil.replace(tmp, _STATE_F)


def _hash(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8", "ignore")).hexdigest()[:16]


# ───────────────────────── harvest ─────────────────────────

def _norm_cmd(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip())[:160].lower()


def _blocks(msg) -> list:
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def _tail(s: str, n: int) -> str:
    s = (s or "").strip()
    return s[-n:] if len(s) > n else s


def _scan_file(path: Path, st: dict, out: list) -> None:
    """Tail one transcript from its stored offset; update rolling context + emit candidates."""
    key = str(path)
    rec = st["files"].get(key) or {"off": 0}
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= rec.get("off", 0):
        return
    ctx = st["ctx"].get(key) or {"assistant": "", "edits": [], "bash": "", "fails": {}, "pending_use": {}}
    project = ""
    try:
        with open(path, "rb") as fh:
            fh.seek(rec.get("off", 0))
            data = fh.read()
            rec["off"] = fh.tell()
    except OSError:
        return
    for raw in data.splitlines():
        try:
            ev = json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            continue
        if not project:
            cwd = ev.get("cwd") or ""
            if cwd:
                project = Path(cwd).name
        typ = ev.get("type")
        msg = ev.get("message") or {}
        ts = ev.get("timestamp") or ""
        if typ == "assistant":
            for b in _blocks(msg):
                bt = b.get("type")
                if bt == "text" and (b.get("text") or "").strip():
                    ctx["assistant"] = _tail(b.get("text") or "", 1400)
                elif bt == "tool_use":
                    name, inp = b.get("name") or "", b.get("input") or {}
                    if name in ("Edit", "Write", "MultiEdit"):
                        fp = inp.get("file_path") or ""
                        code = inp.get("new_string") or inp.get("content") or ""
                        if fp:
                            ctx["edits"] = (ctx["edits"] + [{"file": fp, "code": _tail(code, 1600)}])[-4:]
                    elif name == "Bash" and inp.get("command"):
                        ctx["bash"] = _tail(inp.get("command") or "", 300)
                        ctx["pending_use"][b.get("id") or ""] = _norm_cmd(inp.get("command") or "")
        elif typ == "user":
            for b in _blocks(msg):
                bt = b.get("type")
                if bt == "tool_result":
                    cmd = ctx["pending_use"].pop(b.get("tool_use_id") or "", "")
                    if not cmd:
                        continue
                    body = b.get("content")
                    text = ""
                    if isinstance(body, list):
                        text = " ".join((x.get("text") or "") for x in body if isinstance(x, dict))
                    elif isinstance(body, str):
                        text = body
                    if b.get("is_error"):
                        ctx["fails"][cmd] = _tail(text, 500)
                        if len(ctx["fails"]) > 20:
                            ctx["fails"].pop(next(iter(ctx["fails"])))
                    elif cmd in ctx["fails"]:
                        err = ctx["fails"].pop(cmd)
                        cid = _hash("fix", cmd, err[:120])
                        if cid not in st["seen"]:
                            st["seen"].append(cid)
                            out.append({"id": cid, "kind": "fix", "project": project, "ts": ts,
                                        "cmd": cmd, "error": err, "edits": ctx["edits"][-3:],
                                        "assistant": _tail(ctx["assistant"], 700)})
                elif bt == "text":
                    text = (b.get("text") or "").strip()
                    if (not text or len(text) > 140 or text.startswith("/") or "?" in text
                            or text.startswith("↪") or not APPROVAL_RE.search(text)
                            or NEG_RE.search(text)):
                        continue
                    if not (ctx["edits"] or ctx["bash"]):
                        continue        # praise with nothing built behind it — nothing to bank
                    cid = _hash("ok", text.lower(), (ctx["edits"][-1]["file"] if ctx["edits"] else ctx["bash"]))
                    if cid not in st["seen"]:
                        st["seen"].append(cid)
                        out.append({"id": cid, "kind": "approval", "project": project, "ts": ts,
                                    "quote": text, "edits": ctx["edits"][-3:], "bash": ctx["bash"],
                                    "assistant": ctx["assistant"]})
    # trim the persisted rolling context so state.json stays small
    ctx["pending_use"] = dict(list(ctx["pending_use"].items())[-10:])
    st["ctx"][key] = ctx
    st["files"][key] = rec


def scan_all() -> int:
    """Harvest every project transcript incrementally. Returns new candidate count."""
    root = claude_home() / "projects"
    if not root.is_dir():
        return 0
    with _LOCK:
        if _RUN["scanning"]:
            return 0
        _RUN["scanning"] = True
    new: list = []
    try:
        st = _load_state()
        for f in root.glob("*/*.jsonl"):
            if f.name.startswith("agent-"):
                continue            # subagent transcripts: their "user" turns are synthetic
            _scan_file(f, st, new)
        if new:
            _DIR.mkdir(parents=True, exist_ok=True)
            with open(_CAND_F, "a", encoding="utf-8") as fh:
                for c in new:
                    fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        _save_state(st)
        _RUN["last_scan"] = time.time()
    finally:
        _RUN["scanning"] = False
    return len(new)


def pending() -> list[dict]:
    st = _load_state()
    done = set(st["done"])
    out, seen = [], set()
    try:
        for raw in _CAND_F.read_text(encoding="utf-8").splitlines():
            try:
                c = json.loads(raw)
            except Exception:
                continue
            if c.get("id") and c["id"] not in done and c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
    except OSError:
        pass
    return out


# ───────────────────────── local LLM install ─────────────────────────

def _venv_py() -> Path:
    return _VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _model_path() -> Path:
    return _MODEL_DIR / _MODEL_FILE


def llm_ready() -> bool:
    return _venv_py().exists() and _model_path().exists()


def install_status() -> dict:
    return {"available": llm_ready(), "installing": bool(_INSTALL["installing"]),
            "error": _INSTALL["error"], "model_pct": int(_INSTALL["model_pct"]),
            "step": _INSTALL["step"], "model": _MODEL_FILE}


def ensure_installed(block: bool = False, timeout: float = 3600.0) -> bool:
    """Install the local distiller (dedicated venv + GGUF model). Idempotent, background
    by default. Only ever called when the auto-learn toggle is ON."""
    if llm_ready():
        return True
    start = False
    with _LOCK:
        if not _INSTALL["installing"]:
            _INSTALL.update(installing=True, error="", step="starting")
            start = True
    if start:
        t = threading.Thread(target=_do_install, daemon=True)
        t.start()
        if block:
            t.join(timeout=timeout)
    elif block:
        end = time.time() + timeout
        while _INSTALL["installing"] and time.time() < end:
            time.sleep(1.0)
    return llm_ready()


def _do_install() -> None:
    try:
        py = _venv_py()
        if not py.exists():
            _INSTALL["step"] = "creating venv"
            _VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)],
                           timeout=180, creationflags=_NF, capture_output=True)
        if not py.exists():
            raise RuntimeError("could not create the autolearn venv")
        _INSTALL["step"] = "installing llama-cpp (CPU)"
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
                       timeout=180, creationflags=_NF, capture_output=True)
        # CPU wheels first (no compiler on most PCs); plain PyPI as a fallback
        r = subprocess.run([str(py), "-m", "pip", "install", "--prefer-binary", "--quiet",
                            "llama-cpp-python", "--extra-index-url", _CPU_WHEELS],
                           timeout=1200, creationflags=_NF, capture_output=True, text=True)
        chk = subprocess.run([str(py), "-c", "import llama_cpp"], timeout=120,
                             creationflags=_NF, capture_output=True, text=True)
        if chk.returncode != 0:
            raise RuntimeError(("llama-cpp-python install failed: "
                                + (r.stderr or chk.stderr or "")).strip()[:400])
        if not _model_path().exists():
            _download_model()
        _INSTALL.update(error="", step="ready")
    except Exception as e:
        _INSTALL["error"] = str(e)[:400] or "autolearn install failed"
        _INSTALL["step"] = ""
    finally:
        _INSTALL["installing"] = False


def _download_model() -> None:
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    part = _model_path().with_suffix(".part")
    last_err = ""
    for url in _MODEL_URLS:
        try:
            _INSTALL["step"] = "downloading model"
            req = urllib.request.Request(url, headers={"User-Agent": "asset-studio"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    if total:
                        _INSTALL["model_pct"] = min(99, int(got * 100 / total))
            if part.stat().st_size < 500_000_000:      # a real 4B Q4 GGUF is ~2.4 GB
                raise RuntimeError("download truncated")
            part.replace(_model_path())
            _INSTALL["model_pct"] = 100
            return
        except Exception as e:
            last_err = str(e)[:200]
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
    raise RuntimeError(f"model download failed ({last_err})")


# ───────────────────────── distill ─────────────────────────

# marker lets a startup reaper find leftover workers (they exit per-batch, but belt+braces)
_WORKER_SRC = r'''# ASSET_STUDIO_AUTOLEARN_WORKER
import sys, json, os
from llama_cpp import Llama
llm = Llama(model_path=sys.argv[1], n_ctx=8192, n_gpu_layers=0,
            n_threads=max(2, (os.cpu_count() or 4) // 2), verbose=False)
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        r = llm.create_chat_completion(messages=req["messages"],
                                       max_tokens=req.get("max_tokens", 800),
                                       temperature=req.get("temperature", 0.35))
        print(json.dumps({"text": r["choices"][0]["message"]["content"]}), flush=True)
    except Exception as e:
        print(json.dumps({"error": str(e)[:300]}), flush=True)
'''

_SYS_PROMPT = (
    "You turn a raw captured coding-session moment into ONE reusable skill entry in Markdown. "
    "Output ONLY the entry, exactly this shape:\n"
    "## <short specific title>\n"
    "**Use when:** <one line — the situation that should trigger this recipe>\n"
    "**Recipe:** <2-6 sentences: the validated approach, written GENERICALLY so it works on a "
    "future project — the pattern, not the project-specific instance. Name exact APIs/flags/"
    "values that matter.>\n"
    "```\n<the essential code or commands, trimmed to what someone needs to reuse it>\n```\n"
    "Rules: be concrete and exact; keep parameter values verbatim; drop anything project-specific "
    "(paths, names) unless it IS the lesson; no preamble, no explanation outside the entry."
)


def _cand_prompt(c: dict) -> str:
    lines = [f"Kind: {c.get('kind')}", f"Project: {c.get('project')}"]
    if c.get("kind") == "approval":
        lines.append(f"The user approved with: \"{c.get('quote')}\"")
    else:
        lines.append(f"Failing command: {c.get('cmd')}")
        lines.append(f"Error (tail): {c.get('error')}")
    if c.get("assistant"):
        lines.append(f"Assistant's explanation just before: {c['assistant']}")
    for e in c.get("edits") or []:
        lines.append(f"--- edit to {e.get('file')} ---\n{e.get('code')}")
    if c.get("bash"):
        lines.append(f"Last command run: {c['bash']}")
    return "\n".join(lines)[:6000]


def _template_entry(c: dict) -> str:
    """Mechanical fallback when the local model isn't ready — still a usable drop-in kit."""
    date = (c.get("ts") or "")[:10]
    if c.get("kind") == "fix":
        title = f"Fix: {(c.get('cmd') or 'command')[:70]}"
        body = [f"**Use when:** `{c.get('cmd')}` fails with an error like the one below.",
                f"**Error:** `{_tail(c.get('error') or '', 200)}`",
                "**Fix (the edits that made it pass):**"]
    else:
        top = (c.get("edits") or [{}])[-1].get("file") or c.get("bash") or "the change"
        title = f"Validated: {Path(str(top)).name} recipe"
        body = [f"**Use when:** rebuilding what the user approved with \"{c.get('quote')}\".",
                "**Recipe (captured verbatim from the approved build):**"]
    parts = [f"## {title} — {date} ({c.get('project')})", *body]
    for e in c.get("edits") or []:
        parts.append(f"`{e.get('file')}`:\n```\n{e.get('code')}\n```")
    if c.get("bash"):
        parts.append(f"Command: `{c['bash']}`")
    return "\n".join(parts)


_CAT_RULES = [
    ("ui", re.compile(r"\.(tsx|jsx|css|scss|vue|svelte)\b|classname|tailwind|component", re.I)),
    ("game", re.compile(r"\b(unity|godot|phaser|three\.?js|shader|sprite|game|player|enemy|level)\b", re.I)),
    ("backend", re.compile(r"\b(fastapi|flask|django|uvicorn|endpoint|router|server|sql|database|asyncio)\b|\.py\b", re.I)),
    ("integrations", re.compile(r"\b(api.key|provider|webhook|oauth|sdk|huggingface|openai|anthropic|comfyui)\b", re.I)),
]


def _category(c: dict) -> str:
    blob = " ".join([c.get("assistant") or "", c.get("bash") or "", c.get("cmd") or ""]
                    + [f"{e.get('file')} {e.get('code')}" for e in (c.get("edits") or [])])
    best, hits = "general", 0
    for name, rx in _CAT_RULES:
        n = len(rx.findall(blob))
        if n > hits:
            best, hits = name, n
    return best


def _skills_root() -> Path:
    from . import skills as _skills
    return _skills._claude_home() / "skills"


def _write_entry(cat: str, entry: str) -> None:
    d = _skills_root() / f"learned-{cat}"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    today = time.strftime("%Y-%m-%d")
    if not f.exists():
        f.write_text(
            "---\n"
            f"name: learned-{cat}\n"
            f"description: Auto-learned validated recipes ({cat}) mined from your sessions — "
            "approaches you approved and error→fix pairs that worked. Zero-token capture.\n"
            f"category: studio-{cat if cat != 'general' else 'general'}\n"
            "disable-model-invocation: true\n"
            "metadata:\n"
            f"  created: {today}\n"
            f"  updated: {today}\n"
            "---\n\n"
            f"# Learned recipes — {cat}\n\n",
            encoding="utf-8")
    text = f.read_text(encoding="utf-8")
    text = re.sub(r"(  updated: )\S+", rf"\g<1>{today}", text, count=1)
    first = entry.splitlines()[0].strip() if entry.strip() else ""
    if first and first in text:
        return                      # same title already banked — don't duplicate
    f.write_text(text.rstrip() + "\n\n" + entry.strip() + "\n", encoding="utf-8")
    _RUN["skills_written"] += 1


def distill_pending(limit: int = DISTILL_BATCH) -> int:
    """Polish up to `limit` pending candidates into skill entries. Local LLM when ready,
    template fallback when the install failed; waits (returns 0) while installing."""
    cands = pending()[:limit]
    if not cands:
        return 0
    with _LOCK:
        if _RUN["distilling"]:
            return 0
        _RUN["distilling"] = True
    done_ids: list[str] = []
    try:
        use_llm = llm_ready()
        if not use_llm and _INSTALL["installing"]:
            return 0                # model on its way — better entries are worth the wait
        proc = None
        if use_llm:
            try:
                proc = subprocess.Popen([str(_venv_py()), "-c", _WORKER_SRC, str(_model_path())],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, creationflags=_LOWPRI,
                                        text=True, encoding="utf-8", bufsize=1)
                ready = json.loads(proc.stdout.readline() or "{}")
                if not ready.get("ready"):
                    raise RuntimeError("worker failed to load the model")
            except Exception:
                use_llm = False
                if proc:
                    proc.kill()
                    proc = None
        for c in cands:
            entry = ""
            if use_llm and proc:
                try:
                    proc.stdin.write(json.dumps({"messages": [
                        {"role": "system", "content": _SYS_PROMPT},
                        {"role": "user", "content": _cand_prompt(c)}]}) + "\n")
                    proc.stdin.flush()
                    r = json.loads(proc.stdout.readline() or "{}")
                    entry = (r.get("text") or "").strip()
                    date = (c.get("ts") or "")[:10]
                    if entry.startswith("## ") and date:   # stamp date+project like the template does
                        head, _, rest = entry.partition("\n")
                        entry = f"{head} — {date} ({c.get('project')})\n{rest}"
                except Exception:
                    entry = ""
            if not entry or not entry.startswith("## "):
                entry = _template_entry(c)
            _write_entry(_category(c), entry)
            done_ids.append(c["id"])
        if proc:
            try:
                proc.stdin.close()
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
    finally:
        if done_ids:
            st = _load_state()
            st["done"].extend(done_ids)
            _save_state(st)
        _RUN["distilling"] = False
    return len(done_ids)


# ───────────────────────── loop + status ─────────────────────────

def status() -> dict:
    return {"enabled": bool(settings.get("cc_autolearn")), "pending": len(pending()),
            "skills_written": _RUN["skills_written"], "scanning": _RUN["scanning"],
            "distilling": _RUN["distilling"], "last_scan": _RUN["last_scan"],
            "llm": install_status()}


def run_now() -> dict:
    """Force a scan + distill cycle (fire-and-forget). No-op when the toggle is OFF."""
    if not settings.get("cc_autolearn"):
        return {"started": False, "reason": "autolearn is off"}
    def _job():
        n = scan_all()
        if pending():
            if not llm_ready() and not _INSTALL["installing"] and not _INSTALL["error"]:
                ensure_installed()
            distill_pending(limit=10)
        return n
    threading.Thread(target=_job, daemon=True).start()
    return {"started": True}


_loop_started = False


def start_loop() -> None:
    """Background loop. The toggle is checked EVERY tick — OFF means the only work done
    per 20s is one settings read: no scans, no installs, no model, no worker."""
    global _loop_started
    with _LOCK:
        if _loop_started:
            return
        _loop_started = True
    threading.Thread(target=_loop, daemon=True).start()


def _loop() -> None:
    while True:
        try:
            time.sleep(LOOP_TICK)
            if not settings.get("cc_autolearn"):
                continue
            if time.time() - _RUN["last_scan"] >= SCAN_EVERY:
                scan_all()
                if pending():
                    if llm_ready() or _INSTALL["error"]:
                        distill_pending()          # model ready → polish; install failed → template
                    elif not _INSTALL["installing"]:
                        ensure_installed()         # first time: kick the venv+model install
        except Exception:
            pass


def reap_orphans() -> int:
    """Kill leftover distiller workers from a crashed previous backend (they hold ~3 GB RAM)."""
    killed = 0
    try:
        import psutil
    except Exception:
        return 0
    me = os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if p.pid == me:
                continue
            cl = " ".join(p.info.get("cmdline") or [])
            if "ASSET_STUDIO_AUTOLEARN_WORKER" in cl:
                p.kill()
                killed += 1
        except Exception:
            continue
    return killed

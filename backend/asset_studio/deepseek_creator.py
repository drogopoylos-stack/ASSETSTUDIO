"""DeepSeek Creator mode: the official DeepSeek Harness app, run by the Studio for one folder.

WHY A SECOND DEEPSEEK RUNTIME. The DeepSeek engine in the chat box drives the Python SDK
(`deepseek-harness-sdk` 0.1.5). Its bundled runtime ships two profiles, `sdk` and `sdk-minimal`, and
no agent presets: `session/prompt` creates the agent with a folder and a model and nothing else, so
a session there cannot be put in Creator mode at all. The SDK 0.2.1 on PyPI adds `session/resume`
and still no preset.

Creator mode is a PRESET of the full app, `@deepseek-ai/dsh` (announced 2026-11-02): preset id
`cordis`, beside `standard`, `ptc` and `minimal`. It adds `plugin_manager` (install, enable, disable,
remove plugin bundles), two read-only inspection tools and the `cordis-plugin-development` skill. The
agent writes a plugin bundle in the workspace and installs it into the PROFILE, where it stays for
every later session. Many of those plugins are panels of the app's own Web UI. So the Studio runs that
app rather than imitating it: `dsh --profile web` for the folder, shown inside the Studio.

WHAT THE STUDIO SETS, all through one `--patch` overlay (see `patch_text`):
- new sessions start in Creator mode (`agent-preset-registry.default: cordis`); the picker in the app
  still offers Standard and the others;
- the folder is the app's workspace (a three-line host plugin, `studio-workspace.mjs`, because the
  registry has no launch option for it) and DSH's first-use folder goes under the Studio's data, not
  into the user's Documents;
- the model key is the one the DeepSeek engine already uses. A RIVER.AI key cannot use DSH's DeepSeek
  route: that route speaks Anthropic Messages, and RIVER answers `/v1/messages` with an empty gRPC
  reply (measured 2026-11-11). It goes through `llm-pi-ai` as an OpenAI-compatible gateway instead;
- optional session-log uploads stay off, like the engine's own runtime.

ONE PROFILE PER FOLDER. A Creator plugin runs with the user's permissions and loads into every session
of its profile, so a plugin made for one game must not load in another. Each folder gets its own
`DSH_HOME` under `data/dsh-creator/homes/`.

Servers outlive a backend restart, like chat sessions do; `servers.json` lets the next backend find
and reuse them, and Stop ends one by its own process id.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

PACKAGE = "@deepseek-ai/dsh"
VERSION = "0.2.2-rc.1"          # the first release whose Creator mode installs persistent bundles
NODE_MIN = (24, 9, 0)           # the package's own `engines.node`
# THE APP'S OWN PNPM. `plugin_manager install_bundle` runs pnpm from PATH, and a PC with Node but no
# pnpm failed every install. Measured 2026-11-11: the Creator agent then WROTE A pnpm.cmd SHIM INTO
# THE USER'S GLOBAL npm FOLDER to get past it - a change to the user's profile, approved in a stream
# of "Allow once" prompts. So the Studio installs pnpm beside the app and puts only that folder on the
# app's PATH. 12.11.2 is the version that install ran with (corepack's default here).
PNPM = "12.11.2"
ROOT = Path(os.environ.get("STUDIO_DSH_CREATOR_DIR") or (DATA_DIR / "dsh-creator"))
APP = ROOT / "app"              # `npm install --prefix` target
HOMES = ROOT / "homes"          # one DSH_HOME per folder
DOCS = ROOT / "documents"       # DSH's first-use folder, kept out of the user's Documents
STATE = ROOT / "servers.json"
PLUGIN = ROOT / "studio-workspace.mjs"
URL_RE = re.compile(r"dsh web: (http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+)")
START_TIMEOUT = 60.0
# The route name of the RIVER.AI gateway inside the app (`llm-pi-ai` providers key).
RIVER_PROVIDER = "river"

_lock = threading.RLock()
_install: dict = {"running": False, "ok": None, "error": "", "started": 0.0, "finished": 0.0}

# The host plugin that makes the folder a workspace. `ready` is NOT used: a plugin mounted from a
# patch is applied after the app is ready, so a `ready` listener never fired (measured).
PLUGIN_SOURCE = """// Asset Studio: open DeepSeek Harness with the Studio's project folder as a workspace.
// create() returns the existing workspace for a folder that already has one.
export const name = 'studio-workspace'
export const inject = ['workspaceRegistry']
export function apply(ctx, config) {
  const dirs = Array.isArray(config?.directories) ? config.directories : []
  Promise.resolve().then(async () => {
    for (const dir of dirs) {
      try { await ctx.workspaceRegistry.create(dir) }
      catch (error) { console.error('[studio-workspace] ' + dir + ': ' + (error?.message || error)) }
    }
  })
}
"""


# ---------------------------------------------------------------------------
# What is on this machine
# ---------------------------------------------------------------------------
def _node() -> str:
    return shutil.which("node") or ""


def node_version() -> tuple:
    exe = _node()
    if not exe:
        return ()
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                             creationflags=_flags()).stdout.strip()
        return tuple(int(x) for x in re.findall(r"\d+", out)[:3])
    except Exception:
        return ()


def _bin_js() -> Path:
    return APP / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def installed_version() -> str:
    try:
        meta = json.loads((APP / "node_modules" / "@deepseek-ai" / "dsh" / "package.json").read_text(encoding="utf-8"))
        return str(meta.get("version") or "") if _bin_js().is_file() else ""
    except (OSError, ValueError):
        return ""


def _bin_dir() -> Path:
    return APP / "node_modules" / ".bin"


def pnpm_installed() -> bool:
    return any((_bin_dir() / n).is_file() for n in ("pnpm.cmd", "pnpm"))


def ready() -> bool:
    """The app at the pinned version, with its own pnpm."""
    return installed_version() == VERSION and pnpm_installed()


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
def install() -> dict:
    """Install the app into the Studio's data folder, in the background (~900 MB, ~2 minutes)."""
    npm = shutil.which("npm")
    if not npm:
        return {"ok": False, "error": "Node.js is not installed. Install Node.js 24.9 or newer, then try again."}
    nv = node_version()
    if nv and nv < NODE_MIN:
        return {"ok": False, "error": "DeepSeek Harness needs Node.js %s or newer; this PC has %s."
                % (".".join(map(str, NODE_MIN)), ".".join(map(str, nv)))}
    with _lock:
        if _install["running"]:
            return {"ok": True, "running": True}
        _install.update(running=True, ok=None, error="", started=time.time(), finished=0.0)

    def run():
        log = ROOT / "install.log"
        try:
            APP.mkdir(parents=True, exist_ok=True)
            pkg = APP / "package.json"
            if not pkg.is_file():
                pkg.write_text(json.dumps({"name": "studio-dsh-creator", "private": True}, indent=1),
                               encoding="utf-8")
            with open(log, "w", encoding="utf-8", errors="replace") as fh:
                p = subprocess.run([npm, "install", "--prefix", str(APP), "--no-fund", "--no-audit",
                                    "--loglevel=error", "%s@%s" % (PACKAGE, VERSION), "pnpm@" + PNPM],
                                   stdout=fh, stderr=subprocess.STDOUT, timeout=1800, creationflags=_flags())
            ok = p.returncode == 0 and ready()
            err = "" if ok else ("npm exited with %d; see %s" % (p.returncode, log))
        except Exception as ex:                                  # noqa: BLE001
            ok, err = False, "%s: %s" % (type(ex).__name__, ex)
        with _lock:
            _install.update(running=False, ok=ok, error=err, finished=time.time())

    threading.Thread(target=run, name="dsh-creator-install", daemon=True).start()
    return {"ok": True, "running": True}


def _install_tail() -> str:
    try:
        return (ROOT / "install.log").read_text(encoding="utf-8", errors="replace")[-1500:]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# The patch
# ---------------------------------------------------------------------------
def _q(s: str) -> str:
    # Single-quoted YAML keeps a Windows path's backslashes as written.
    return "'" + str(s).replace("'", "''") + "'"


def patch_text(folder: str, via: str, river_model: str = "", river_base: str = "") -> str:
    """The `--patch` overlay for one folder. See the module docstring for each row."""
    lines = [
        "# Written by Asset Studio for DeepSeek Creator mode. Rewritten at every start.",
        "- id: session-log-deepseek", "  config:", "    enabled: false",
        "- id: agent-preset-registry", "  config:", "    default: cordis",
        "- id: workspace-controller", "  config:", "    documentsDirectory: " + _q(DOCS),
    ]
    if via == "river":
        lines += [
            "- id: llm-pi-ai", "  config:", "    providers:", "      " + RIVER_PROVIDER + ":",
            "        displayName: RIVER.AI", "        apiKeyEnv: RIVER_API_KEY",
            "        api: openai-completions", "        baseURL: " + _q(river_base),
            "        compat:", "          thinkingFormat: deepseek", "        models:",
            "          - id: " + _q(river_model), "            name: 'DeepSeek V4.1 Flash (RIVER.AI)'",
            "            contextWindow: 1000000", "            input: [text, image]",
            "- id: agent-default-model", "  config:", "    provider: " + RIVER_PROVIDER,
            "    model: " + _q(river_model),
        ]
    lines += [
        "- insert:", "    - id: studio-workspace", "      name: " + _q(PLUGIN.resolve().as_uri()),
        "      config:", "        directories:", "          - " + _q(folder),
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------
def _key(folder: str) -> str:
    return os.path.normcase(os.path.abspath(folder)).rstrip("\\/")


def _home(folder: str) -> Path:
    return HOMES / hashlib.sha256(_key(folder).encode("utf-8")).hexdigest()[:16]


def _read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def _ours(pid: int) -> bool:
    """The process is alive AND is the DSH app — a reused pid must never be stopped."""
    try:
        import psutil
        p = psutil.Process(int(pid))
        return p.is_running() and "dsh" in " ".join(p.cmdline()).lower()
    except Exception:
        return False


def _answers(url: str) -> bool:
    """Something is listening there.

    ANY HTTP answer counts. The token URL answers 303 with a cookie, and the page then answers 401
    to a client that did not keep it — which is what urllib does. Counting only a 200 called every
    live server dead, so a second open started a DUPLICATE server and left the first one running
    with nothing tracking it (measured 2026-11-11)."""
    try:
        with urllib.request.urlopen(url, timeout=5):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def servers() -> list:
    """The running servers, each checked; dead rows are dropped."""
    with _lock:
        state = _read_state()
        live = {k: v for k, v in state.items() if _ours(int(v.get("pid") or 0))}
        if live != state:
            _write_state(live)
    return [dict(v, folder=v.get("folder") or k) for k, v in sorted(live.items())]


def status() -> dict:
    nv = node_version()
    with _lock:
        inst = dict(_install)
    return {"ok": True, "package": PACKAGE, "version": VERSION, "installed": installed_version(),
            "pnpm": pnpm_installed(), "ready": ready(),
            "node": ".".join(map(str, nv)), "node_ok": bool(nv) and nv >= NODE_MIN,
            "install": inst, "install_log": _install_tail() if inst.get("running") or inst.get("ok") is False else "",
            "servers": servers()}


def open_(folder: str) -> dict:
    """Start (or reuse) the app for one folder; returns its URL with the app's access token."""
    folder = os.path.abspath(str(folder or ""))
    if not folder or not os.path.isdir(folder):
        return {"ok": False, "error": "no such folder: %s" % folder}
    if not ready():
        return {"ok": False, "needs_install": True,
                "error": "DeepSeek Harness %s is not installed yet." % VERSION}
    if not node_version() or node_version() < NODE_MIN:
        return {"ok": False, "error": "DeepSeek Harness needs Node.js %s or newer." % ".".join(map(str, NODE_MIN))}
    from . import deepseek_session
    rt = deepseek_session.route()
    if not rt.get("key"):
        return {"ok": False, "needs_key": True,
                "error": "Add a DeepSeek or RIVER.AI key in Settings > API Keys > DeepSeek Harness."}
    k = _key(folder)
    with _lock:
        for row in servers():
            if _key(row["folder"]) != k:
                continue
            if _answers(row["url"]):
                return {"ok": True, "url": row["url"], "reused": True, "folder": row["folder"]}
            # Alive but not answering: replaced below, so end it now rather than orphan it.
            _kill(int(row["pid"]))
        home = _home(folder)
        home.mkdir(parents=True, exist_ok=True)
        DOCS.mkdir(parents=True, exist_ok=True)
        PLUGIN.write_text(PLUGIN_SOURCE, encoding="utf-8")
        patch = home / "studio.patch.yml"
        patch.write_text(patch_text(folder, rt["via"], getattr(deepseek_session, "RIVER_MODEL", ""),
                                    rt.get("base_url") or ""), encoding="utf-8")
        env = dict(os.environ)
        # The app's credential store wins over these, so a key saved inside the app takes over.
        for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "RIVER_API_KEY"):
            env.pop(name, None)
        env.update({"DSH_HOME": str(home), "DSH_TELEMETRY_DISABLED": "1",
                    # The app's own pnpm first (see PNPM); nothing global is needed or changed.
                    "PATH": str(_bin_dir()) + os.pathsep + env.get("PATH", "")})
        if rt["via"] == "river":
            env["RIVER_API_KEY"] = rt["key"]
        else:
            env["DEEPSEEK_API_KEY"] = rt["key"]
        out, err = home / "web.out", home / "web.err"
        argv = [_node(), str(_bin_js()), "--profile", "web", "--patch", str(patch),
                "--no-open", "--host", "127.0.0.1", "--port", "0"]
        with open(out, "w", encoding="utf-8") as fo, open(err, "w", encoding="utf-8") as fe:
            proc = subprocess.Popen(argv, cwd=folder, env=env, stdout=fo, stderr=fe,
                                    stdin=subprocess.DEVNULL, creationflags=_flags())
        url, t0 = "", time.time()
        while time.time() - t0 < START_TIMEOUT:
            m = URL_RE.search(out.read_text(encoding="utf-8", errors="replace"))
            if m:
                url = m.group(1)
                break
            if proc.poll() is not None:
                break
            time.sleep(0.3)
        if not url:
            tail = err.read_text(encoding="utf-8", errors="replace")[-800:]
            if proc.poll() is None:
                _kill(proc.pid)
            return {"ok": False, "error": "DeepSeek Harness did not start: " + (tail.strip() or "no URL printed")}
        state = _read_state()
        state[k] = {"folder": folder, "pid": proc.pid, "url": url, "home": str(home),
                    "via": rt["via"], "started": time.time()}
        _write_state(state)
    return {"ok": True, "url": url, "reused": False, "folder": folder}


def _kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True,
                       timeout=30, creationflags=_flags())
    else:
        import signal
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass


def stop(folder: str = "") -> dict:
    """Stop the server of one folder, or every one when no folder is named."""
    stopped = []
    with _lock:
        state = _read_state()
        for k in list(state):
            if folder and k != _key(folder):
                continue
            pid = int(state[k].get("pid") or 0)
            if pid and _ours(pid):
                _kill(pid)
            stopped.append(state.pop(k).get("folder") or k)
        _write_state(state)
    return {"ok": True, "stopped": stopped}

"""OpenAI Codex in the chat, through Codex's own app-server.

The chat used to run `codex exec` once per message and show whatever it printed. On a new PC that
failed three ways at once:
  * nothing could sign Codex in, so the first message got ten seconds of "401 Unauthorized" and no
    answer at all;
  * the model list was a fixed guess (gpt-5-codex, o3, o4-mini) that the CLI no longer offers;
  * the default mode, `--sandbox workspace-write`, blocks EVERY command on Windows while Codex's
    own Windows sandbox is not set up ("rejected: blocked by policy") - measured on 0.145 and 0.159.

`codex app-server` is the protocol the Codex IDE extension speaks: JSON-RPC over stdio, one
process, many conversations ("threads"). Through it the Studio can
  * read and change the sign-in - ChatGPT in the browser, a device code, or an API key. They are
    the Codex CLI's own credentials (CODEX_HOME, ~/.codex), so a PC where `codex login` already ran
    is signed in here too, and a sign-in made here works in the CLI;
  * list the models the installed CLI offers the account, each with its own reasoning efforts;
  * keep one conversation per folder, stream the answer, and see every command, file edit,
    subagent and image as it happens.

A Codex conversation is filed under "codex--<folder id>", the way Kimi's is under "kimi--", so the
feed, the live stream, the context meter and the phases all look it up by that id. What Codex did is
kept in data/codex/threads/<thread>.jsonl as the protocol's own items; the feed is built from them
when it is read, so a better drawing applies to old conversations too.

Protocol facts this module leans on, measured against the real binary (0.145.0 and 0.159.2):
  * responses carry no "jsonrpc" field; one JSON object per line each way;
  * a thread's `sandbox` given to thread/start can come back as read-only, while a turn's
    `sandboxPolicy` is honoured - so every turn states its own policy;
  * `error` with willRetry=true is a reconnect in progress ("Reconnecting... 2/5"), not a failure;
  * a subagent runs in its own thread; the parent reports it with `subAgentActivity` items (0.159)
    or a `collabAgentToolCall` spawnAgent (0.145), and the child's items arrive under its own id.
"""
from __future__ import annotations

import base64
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from . import fsutil
from .config import DATA_DIR, settings

CODEX_PREFIX = "codex--"
CLIENT_NAME = "asset_studio"
_DIR = DATA_DIR / "codex"
_THREADS = _DIR / "threads"
_PROJECTS = _DIR / "projects"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# The Studio's approval choices, and what each one asks Codex for. "full" is the default because it
# is the one that works on every PC: on Windows the sandboxed modes need Codex's own sandbox, and
# without it every command is blocked or asked about.
MODES = {
    "full": ("never", "danger-full-access"),
    "ask": ("on-request", "workspace-write"),
    "read-only": ("on-request", "read-only"),
}
_MODE_ALIASES = {
    "": "full", "default": "full", "full-auto": "full", "yolo": "full", "bypasspermissions": "full",
    "danger-full-access": "full", "acceptedits": "full", "auto": "full",
    "on-request": "ask", "untrusted": "ask", "workspace-write": "ask",
    "readonly": "read-only", "plan": "read-only", "suggest": "read-only",
}

# The words an approval card offers. Answering one of them while Codex waits resolves the wait
# instead of starting a turn (Workspace.answerQuestion sends the label as a message).
ALLOW, ALLOW_SESSION, DENY = "Allow", "Allow for this session", "Deny"


def mode_of(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in MODES:
        return m
    return _MODE_ALIASES.get(m, "full")


def _policy(mode: str) -> tuple[str, dict]:
    """(approvalPolicy, sandboxPolicy) for a Studio mode."""
    approval, sandbox = MODES[mode_of(mode)]
    if sandbox == "danger-full-access":
        pol: dict = {"type": "dangerFullAccess"}
    elif sandbox == "read-only":
        pol = {"type": "readOnly", "networkAccess": False}
    else:
        pol = {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False,
               "excludeTmpdirEnvVar": False, "excludeSlashTmp": False}
    return approval, pol


# ---------------------------------------------------------------------------
# Where the CLI is
# ---------------------------------------------------------------------------
_FIND: dict = {}
_VERSIONS: dict = {}


def _shim_candidates() -> list[str]:
    """Paths that may start Codex, best first. The .cmd shim is tried before the bare name: npm
    also writes a `codex` shell script for Git Bash, and Windows cannot start that one."""
    out: list[str] = []
    over = str(settings.get("codex_path") or "").strip()
    if over:
        out.append(over)
    if os.name == "nt":
        for ext in (".cmd", ".exe", ".bat"):
            p = shutil.which("codex" + ext)
            if p:
                out.append(p)
        appdata = os.environ.get("APPDATA")
        if appdata:                    # npm's global folder, even when PATH has not caught up yet
            out.append(str(Path(appdata) / "npm" / "codex.cmd"))
    else:
        p = shutil.which("codex")
        if p:
            out.append(p)
    seen: set = set()
    res: list[str] = []
    for c in out:
        try:
            k = os.path.normcase(os.path.abspath(c))
        except (OSError, ValueError):
            continue
        if k in seen or not Path(c).is_file():
            continue
        seen.add(k)
        res.append(c)
    return res


def _targets() -> list[tuple[str, str]]:
    """(target triple, npm platform package) for this machine, its own architecture first."""
    import platform
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if os.name == "nt":
        a, x = ("aarch64-pc-windows-msvc", "codex-win32-arm64"), ("x86_64-pc-windows-msvc", "codex-win32-x64")
    elif sys.platform == "darwin":
        a, x = ("aarch64-apple-darwin", "codex-darwin-arm64"), ("x86_64-apple-darwin", "codex-darwin-x64")
    else:
        a, x = ("aarch64-unknown-linux-musl", "codex-linux-arm64"), ("x86_64-unknown-linux-musl", "codex-linux-x64")
    return [a, x] if arm else [x, a]


def _package_of(shim: Path) -> Optional[Path]:
    """The @openai/codex package folder behind an npm shim."""
    for base in (shim.parent / "node_modules" / "@openai" / "codex",
                 shim.parent.parent / "lib" / "node_modules" / "@openai" / "codex"):
        if (base / "package.json").is_file():
            return base
    try:
        real = shim.resolve()
        if real.name == "codex.js" and (real.parent.parent / "package.json").is_file():
            return real.parent.parent
    except OSError:
        pass
    return None


def _native_of(pkg: Path) -> Optional[Path]:
    """The platform binary that the package's codex.js would start: the platform package nested
    in the package, or hoisted beside it, then the package's own vendor folder."""
    name = "codex.exe" if os.name == "nt" else "codex"
    for triple, plat in _targets():
        for base in (pkg / "node_modules" / "@openai" / plat, pkg.parent / plat, pkg):
            for sub in (("bin",), ("codex",)):
                cand = base.joinpath("vendor", triple, *sub, name)
                if cand.is_file():
                    return cand
    return None


def _shim_argv(shim: Path) -> list[str]:
    low = shim.name.lower()
    if os.name != "nt":
        return [str(shim)]
    if low.endswith((".cmd", ".bat")):
        try:
            from .cc_session import _resolve_node_shim
            node = _resolve_node_shim(str(shim))
        except Exception:
            node = None
        if node:
            return node
        return [os.environ.get("COMSPEC") or "cmd.exe", "/c", str(shim)]
    if low.endswith(".exe"):
        return [str(shim)]
    return []


def _version_of(argv: list[str]) -> str:
    key = tuple(argv)
    try:
        stamp = os.path.getmtime(argv[-1] if len(argv) > 1 else argv[0])
    except (OSError, IndexError):
        stamp = 0.0
    hit = _VERSIONS.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    ver = ""
    try:
        r = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        m = re.search(r"(\d+\.\d+\.\d+(?:[-.][\w.]+)?)", (r.stdout or "") + " " + (r.stderr or ""))
        ver = m.group(1) if m else ""
    except Exception:
        ver = ""
    _VERSIONS[key] = (stamp, ver)
    return ver


def find_codex(refresh: bool = False) -> dict:
    """How to start Codex here: {shim, exe, argv, version, npm, package}. {} = not installed.

    The native binary is preferred to the npm shim: a long-lived stdio server behind cmd.exe and
    node is three processes to stop instead of one, and cmd.exe mangles arguments."""
    now = time.time()
    hit = _FIND.get("v")
    if hit is not None and not refresh and now - hit[0] < 30:
        return hit[1]
    info: dict = {}
    for shim in _shim_candidates():
        p = Path(shim)
        if p.name.lower() == "codex.exe" and not _package_of(p):
            info = {"shim": str(p), "exe": str(p), "npm": False, "package": ""}
            break
        pkg = _package_of(p)
        exe = _native_of(pkg) if pkg else None
        if exe:
            info = {"shim": str(p), "exe": str(exe), "npm": True, "package": str(pkg)}
            break
        argv = _shim_argv(p)
        if argv:
            info = {"shim": str(p), "exe": "", "argv": argv, "npm": True, "package": str(pkg or "")}
            break
    if info:
        info["argv"] = info.get("argv") or [info["exe"]]
        info["version"] = _version_of(info["argv"])
    _FIND["v"] = (now, info)
    return info


def _env(info: dict) -> dict:
    env = dict(os.environ)
    if info.get("npm") and info.get("package"):
        # what codex.js sets before it starts the binary (the update hint names npm)
        env["CODEX_MANAGED_BY_NPM"] = "1"
        env["CODEX_MANAGED_PACKAGE_ROOT"] = info["package"]
    return env


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


# ---------------------------------------------------------------------------
# The app-server process
# ---------------------------------------------------------------------------
class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class AppServer:
    """One `codex app-server`, spoken to over stdio: one JSON object per line each way.

    `on_note(server, method, params)` runs on the reader thread and must never wait on Codex: a
    reply it waited for would arrive on the very thread it is blocking. `on_request(server, id,
    method, params)` runs on a worker, so it may wait (an approval waits for a person)."""

    def __init__(self, argv: list[str], env: dict, on_note: Callable[["AppServer", str, dict], None],
                 on_request: Callable[["AppServer", Any, str, dict], None]):
        self.argv, self.env = argv, env
        self.on_note, self.on_request = on_note, on_request
        self.proc: Optional[subprocess.Popen] = None
        self._wlock = threading.Lock()
        self._plock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._next = 0
        self.stderr: collections.deque = collections.deque(maxlen=80)
        self.info: dict = {}
        self.started = 0.0
        self.loaded: set[str] = set()          # threads this process has open
        self.dead_reason = ""

    @property
    def pid(self) -> int:
        return self.proc.pid if self.proc else 0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and not self.dead_reason

    def start(self, timeout: float = 30.0) -> None:
        self.proc = subprocess.Popen(self.argv + ["app-server"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env,
                                     creationflags=_NO_WINDOW, bufsize=0)
        self.started = time.time()
        threading.Thread(target=self._read, daemon=True, name="codex-app-out").start()
        threading.Thread(target=self._read_err, daemon=True, name="codex-app-err").start()
        try:
            self.info = self.request("initialize", {
                "clientInfo": {"name": CLIENT_NAME, "title": "Asset Studio", "version": "1.0"},
                "capabilities": {"experimentalApi": True, "requestAttestation": False},
            }, timeout=timeout)
        except Exception:
            self.stop()
            raise
        self.notify("initialized")

    def stop(self) -> None:
        p = self.proc
        if p is None:
            return
        self.dead_reason = self.dead_reason or "stopped"
        try:
            if p.stdin:
                p.stdin.close()          # the server exits when its input closes
        except OSError:
            pass
        try:
            p.wait(3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        self._fail_all("the Codex app-server stopped")

    # -- wire ----------------------------------------------------------------
    def _write(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            if self.proc is None or self.proc.stdin is None:
                raise RpcError(-1, "the Codex app-server is not running")
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                self.dead_reason = self.dead_reason or f"write failed: {e}"
                raise RpcError(-1, "the Codex app-server stopped") from e

    def request(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> Any:
        slot: dict = {"ev": threading.Event()}
        with self._plock:
            self._next += 1
            rid = self._next
            self._pending[rid] = slot
        try:
            self._write({"id": rid, "method": method, "params": params if params is not None else {}})
        except RpcError:
            with self._plock:
                self._pending.pop(rid, None)
            raise
        if not slot["ev"].wait(timeout):
            with self._plock:
                self._pending.pop(rid, None)
            raise RpcError(-2, f"Codex did not answer {method} within {int(timeout)} s")
        if "error" in slot:
            e = slot["error"] or {}
            raise RpcError(int(e.get("code") or -1), str(e.get("message") or "error"), e.get("data"))
        return slot.get("result")

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        msg: dict = {"method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def respond(self, rid: Any, result: Any = None, error: Optional[dict] = None) -> None:
        self._write({"id": rid, "error": error} if error is not None else {"id": rid, "result": result})

    def _fail_all(self, why: str) -> None:
        with self._plock:
            slots = list(self._pending.values())
            self._pending.clear()
        for s in slots:
            s["error"] = {"code": -1, "message": why}
            s["ev"].set()

    def _read(self) -> None:
        p = self.proc
        try:
            for raw in p.stdout:          # type: ignore[union-attr]
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    # A server request (an approval, a question). Answered on a worker so this
                    # loop keeps reading while the answer waits for a person.
                    threading.Thread(target=self._serve, args=(msg,), daemon=True).start()
                elif "id" in msg and ("result" in msg or "error" in msg):
                    with self._plock:
                        slot = self._pending.pop(msg["id"], None)
                    if slot is not None:
                        if "error" in msg:
                            slot["error"] = msg.get("error")
                        else:
                            slot["result"] = msg.get("result")
                        slot["ev"].set()
                elif "method" in msg:
                    try:
                        self.on_note(self, str(msg["method"]), msg.get("params") or {})
                    except Exception:
                        pass
        except Exception:
            pass
        self.dead_reason = self.dead_reason or "the Codex app-server exited"
        self._fail_all(self.dead_reason)
        try:
            self.on_note(self, "__exit__", {"reason": self.dead_reason})
        except Exception:
            pass

    def _serve(self, msg: dict) -> None:
        try:
            self.on_request(self, msg["id"], str(msg["method"]), msg.get("params") or {})
        except Exception as e:  # noqa: BLE001
            try:
                self.respond(msg["id"], error={"code": -32000, "message": f"Studio: {e}"})
            except Exception:
                pass

    def _read_err(self) -> None:
        p = self.proc
        try:
            for raw in p.stderr:          # type: ignore[union-attr]
                s = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode("utf-8", "replace")).rstrip()
                if s:
                    self.stderr.append(s[:600])
        except Exception:
            pass


_SRV: Optional[AppServer] = None
_SRV_LOCK = threading.RLock()
_SRV_ERR = {"text": "", "at": 0.0}
_SRV_KEY: dict = {}
_EXTRA_ARGS: list[str] = []            # tests point Codex at a stand-in endpoint with -c flags


def server(start: bool = True) -> Optional[AppServer]:
    """The running app-server, started on first use and again after it dies or Codex is updated."""
    global _SRV
    with _SRV_LOCK:
        info = find_codex()
        if not info:
            return None
        key = {"argv": info["argv"], "version": info.get("version", "")}
        if _SRV is not None and _SRV.alive() and _SRV_KEY.get("v") == key:
            return _SRV
        if _SRV is not None and _SRV.alive() and _SRV_KEY.get("v") != key and not _busy():
            _SRV.stop()                    # Codex was updated: the next process runs the new one
        elif _SRV is not None and _SRV.alive():
            return _SRV
        if not start:
            return None
        srv = AppServer(list(info["argv"]) + list(_EXTRA_ARGS), _env(info), _on_note, _on_request)
        try:
            srv.start()
        except Exception as e:  # noqa: BLE001
            tail = " | ".join(list(srv.stderr)[-3:])
            _SRV_ERR.update(text=f"{e}{(' - ' + tail) if tail else ''}", at=time.time())
            raise
        _SRV_ERR.update(text="", at=0.0)
        _SRV, _SRV_KEY["v"] = srv, key
        _ACCOUNT.clear()
        _MODELS.clear()
    _start_recycler()
    return srv


def stop_server() -> None:
    global _SRV
    with _SRV_LOCK:
        if _SRV is not None:
            _SRV.stop()
        _SRV = None


# AN IDLE APP-SERVER THAT HAS GROWN IS RECYCLED. The Studio keeps one Codex app-server alive for as
# long as the backend runs, and Windows flagged it on 2026-10-08 (RADAR_PRE_LEAK_64: codex.exe) —
# a leak in a process that never restarts only ever grows. When nothing is running in Codex and the
# server with its children (node runtimes, sandbox helpers) holds more than the limit, it is stopped.
# Nothing is lost: conversations live on disk, and the next send starts a server and resumes the
# thread (see `_start_turn`, `thread/resume`). `codex_recycle_mb` changes the limit; 0 disables it.
_RECYCLE_EVERY_S = 300.0
_RECYCLE_IDLE_S = 600.0
_recycler = {"on": False, "idle_since": 0.0}


def _tree_mb(pid: int) -> float:
    try:
        import psutil
        p = psutil.Process(pid)
        procs = [p] + p.children(recursive=True)
        total = 0
        for q in procs:
            try:
                total += q.memory_info().rss
            except Exception:
                pass
        return total / (1024 * 1024)
    except Exception:
        return 0.0


def recycle_if_bloated(now: Optional[float] = None) -> bool:
    """Stop the app-server when it has been idle a while and grown past the limit. True if stopped."""
    try:
        limit = float(settings.get("codex_recycle_mb", 2048) or 0)
    except (TypeError, ValueError):
        limit = 2048.0
    now = time.time() if now is None else now
    srv = _SRV
    if limit <= 0 or srv is None or not srv.alive():
        _recycler["idle_since"] = 0.0
        return False
    if _busy():
        _recycler["idle_since"] = 0.0
        return False
    if not _recycler["idle_since"]:
        _recycler["idle_since"] = now
        return False
    if now - _recycler["idle_since"] < _RECYCLE_IDLE_S:
        return False
    mb = _tree_mb(srv.pid)
    if mb <= limit:
        return False
    with _SRV_LOCK:
        if _SRV is not srv or _busy():
            return False
        print(f"[codex] recycling the idle app-server: {mb:.0f} MB > {limit:.0f} MB")
        stop_server()
    _recycler["idle_since"] = 0.0
    return True


def _start_recycler() -> None:
    with _SRV_LOCK:
        if _recycler["on"]:
            return
        _recycler["on"] = True

    def loop() -> None:
        while True:
            time.sleep(_RECYCLE_EVERY_S)
            try:
                recycle_if_bloated()
            except Exception:
                pass
    threading.Thread(target=loop, name="codex-recycler", daemon=True).start()


def _busy() -> bool:
    return any(c.working for c in list(_CONVS.values())) or bool(_WAITERS)


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------
_ACCOUNT: dict = {}
_LOGIN: dict = {}
_LOGIN_LOCK = threading.Lock()


def _read_account(srv: AppServer, fresh: bool = False) -> dict:
    now = time.time()
    if _ACCOUNT and not fresh and now - _ACCOUNT.get("at", 0) < 20:
        return _ACCOUNT["v"]
    r = srv.request("account/read", {}, timeout=20) or {}
    acct = r.get("account") or None
    v = {"signed_in": acct is not None, "requires_auth": bool(r.get("requiresOpenaiAuth", True)),
         "auth": "", "email": "", "plan": ""}
    if acct:
        v["auth"] = str(acct.get("type") or "")
        v["email"] = str(acct.get("email") or "")
        v["plan"] = str(acct.get("planType") or "")
    _ACCOUNT.update(at=now, v=v)
    return v


def ready_to_chat(v: dict) -> bool:
    """Signed in, or a provider that needs no OpenAI sign-in (a local model, a custom endpoint)."""
    return bool(v.get("signed_in") or not v.get("requires_auth"))


def _open_url(url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    if os.environ.get("ASSET_STUDIO_NO_BROWSER_OPEN") == "1":
        return False                  # a test backend must not pop a window on the desktop

    try:
        if os.name == "nt":
            os.startfile(url)          # type: ignore[attr-defined]
        else:
            import webbrowser
            webbrowser.open(url)
        return True
    except Exception:
        return False


def login(kind: str, api_key: str = "", open_browser: bool = True) -> dict:
    """Start a sign-in. kind: "chatgpt" (browser), "device" (a code to type on a web page) or
    "apikey". The browser and device kinds finish later, when Codex reports account/login/completed."""
    srv = server()
    if srv is None:
        return {"ok": False, "error": "Codex is not installed"}
    kind = (kind or "chatgpt").lower()
    key = (api_key or "").strip()
    if kind == "apikey" and not key:
        return {"ok": False, "error": "Paste an OpenAI API key first."}
    # _LOGIN_LOCK is held only around the dict, never across a call to Codex: the reader thread
    # takes it for account/login/completed, which Codex may send BEFORE its reply - holding it
    # here made an API-key sign-in wait 30 s and then report a failure that had not happened.
    with _LOGIN_LOCK:
        old = _LOGIN.get("login_id") if _LOGIN.get("pending") else ""
        _LOGIN.clear()
    if old:
        try:
            srv.request("account/login/cancel", {"loginId": old}, timeout=10)
        except Exception:
            pass
    if kind == "apikey":
        try:
            srv.request("account/login/start", {"type": "apiKey", "apiKey": key}, timeout=30)
        except RpcError as e:
            return {"ok": False, "error": e.message}
        _ACCOUNT.clear()
        _MODELS.clear()
        with _LOGIN_LOCK:
            _LOGIN.update(kind="apikey", pending=False, success=True, at=time.time())
        return {"ok": True, "kind": "apikey", "account": _read_account(srv, fresh=True)}
    params = {"type": "chatgptDeviceCode"} if kind == "device" else {"type": "chatgpt"}
    try:
        r = srv.request("account/login/start", params, timeout=45) or {}
    except RpcError as e:
        return {"ok": False, "error": e.message}
    with _LOGIN_LOCK:
        _LOGIN.update(kind=kind, pending=True, success=None, error="", at=time.time(),
                      login_id=str(r.get("loginId") or ""), auth_url=str(r.get("authUrl") or ""),
                      verification_url=str(r.get("verificationUrl") or ""),
                      user_code=str(r.get("userCode") or ""))
        url = _LOGIN.get("auth_url") or _LOGIN.get("verification_url") or ""
    opened = _open_url(url) if (open_browser and url) else False
    with _LOGIN_LOCK:
        _LOGIN["opened"] = opened
    return {"ok": True, **_login_view(), "opened": opened}


def login_cancel() -> dict:
    srv = server(start=False)
    with _LOGIN_LOCK:
        lid = _LOGIN.get("login_id") if _LOGIN.get("pending") else ""
        _LOGIN.clear()
    if srv is not None and lid:
        try:
            srv.request("account/login/cancel", {"loginId": lid}, timeout=10)
        except Exception:
            pass
    return {"ok": True}


def logout() -> dict:
    srv = server()
    if srv is None:
        return {"ok": False, "error": "Codex is not installed"}
    try:
        srv.request("account/logout", {}, timeout=20)
    except RpcError as e:
        return {"ok": False, "error": e.message}
    _ACCOUNT.clear()
    _MODELS.clear()
    return {"ok": True, "account": _read_account(srv, fresh=True)}


def _login_view() -> dict:
    if not _LOGIN:
        return {"login": None}
    return {"login": {k: _LOGIN.get(k) for k in ("kind", "pending", "success", "error", "auth_url",
                                                  "verification_url", "user_code", "at", "opened")}}


def _on_account_note(method: str, p: dict) -> None:
    if method == "account/login/completed":
        with _LOGIN_LOCK:
            if _LOGIN and (not p.get("loginId") or p.get("loginId") == _LOGIN.get("login_id")):
                _LOGIN.update(pending=False, success=bool(p.get("success")),
                              error=str(p.get("error") or ""))
    if method in ("account/login/completed", "account/updated"):
        _ACCOUNT.clear()
        _MODELS.clear()


# ---------------------------------------------------------------------------
# Status, models, sandbox, install
# ---------------------------------------------------------------------------
_LATEST: dict = {}
_INSTALL: dict = {"running": False, "log": [], "ok": None, "error": "", "at": 0.0, "what": ""}


def _npm() -> str:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm") or ""
    return shutil.which("npm") or ""


def latest_version() -> str:
    """The newest @openai/codex on npm, looked up on a worker and remembered for six hours."""
    now = time.time()
    if _LATEST.get("at") and now - _LATEST["at"] < 6 * 3600:
        return _LATEST.get("v", "")
    if _LATEST.get("busy"):
        return _LATEST.get("v", "")
    npm = _npm()
    if not npm:
        return ""
    _LATEST["busy"] = True

    def work() -> None:
        v = ""
        try:
            r = subprocess.run([npm, "view", "@openai/codex", "version"], capture_output=True, text=True,
                               timeout=60, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
            m = re.search(r"(\d+\.\d+\.\d+)", r.stdout or "")
            v = m.group(1) if m else ""
        except Exception:
            v = ""
        _LATEST.update(v=v or _LATEST.get("v", ""), at=time.time(), busy=False)

    threading.Thread(target=work, daemon=True).start()
    return _LATEST.get("v", "")


def status(fresh: bool = False) -> dict:
    """Everything the chat box shows about Codex: installed, version, signed in, how, sandbox."""
    info = find_codex(refresh=fresh)
    out: dict = {"installed": bool(info), "path": info.get("shim", ""), "exe": info.get("exe", ""),
                 "version": info.get("version", ""), "npm": bool(_npm()),
                 "latest": latest_version() if info or _npm() else "",
                 "signed_in": False, "requires_auth": True, "auth": "", "email": "", "plan": "",
                 "running": False, "error": "", "sandbox": "",
                 "install": {k: _INSTALL.get(k) for k in ("running", "ok", "error", "what")},
                 **_login_view()}
    out["install"]["log"] = list(_INSTALL.get("log") or [])[-12:]
    out["outdated"] = bool(out["version"] and out["latest"]
                           and _vtuple(out["latest"]) > _vtuple(out["version"]))
    if not info:
        return out
    try:
        srv = server()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Codex could not start: {e}"
        return out
    if srv is None:
        return out
    out["running"] = True
    try:
        out.update(_read_account(srv, fresh=fresh))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"Codex did not report its sign-in: {getattr(e, 'message', e)}"
    out["ready"] = ready_to_chat(out)
    if os.name == "nt":
        out["sandbox"] = _sandbox_state(srv)
    return out


def recheck() -> dict:
    """"Check again": read the sign-in afresh. A `codex login` run in a terminal writes the
    credentials the running server read at start, so it is restarted once when nothing is running
    and it still says signed out."""
    st = status(fresh=True)
    if st.get("installed") and not st.get("ready") and not _busy():
        stop_server()
        st = status(fresh=True)
    return st


_SANDBOX: dict = {}


def _sandbox_state(srv: AppServer) -> str:
    now = time.time()
    if _SANDBOX.get("at") and now - _SANDBOX["at"] < 60:
        return _SANDBOX.get("v", "")
    try:
        r = srv.request("windowsSandbox/readiness", {}, timeout=10) or {}
        v = str(r.get("status") or "")
    except Exception:
        v = ""
    _SANDBOX.update(v=v, at=now)
    return v


def sandbox_setup(cwd: str = "") -> dict:
    """Codex's own non-admin Windows sandbox, for the "Ask" and "Read only" modes. It writes
    `[windows] sandbox = "unelevated"` into Codex's config; no admin prompt."""
    srv = server()
    if srv is None:
        return {"ok": False, "error": "Codex is not installed"}
    params: dict = {"mode": "unelevated"}
    if cwd:
        params["cwd"] = cwd
    try:
        r = srv.request("windowsSandbox/setupStart", params, timeout=30) or {}
    except RpcError as e:
        return {"ok": False, "error": e.message}
    _SANDBOX.clear()
    return {"ok": bool(r.get("started")), "started": bool(r.get("started"))}


_MODELS: dict = {}


def models(refresh: bool = False) -> dict:
    """The models this Codex offers this account, each with its own reasoning efforts."""
    now = time.time()
    if _MODELS and not refresh and now - _MODELS.get("at", 0) < 300:
        return _MODELS["v"]
    srv = server()
    if srv is None:
        return {"models": [], "default": "", "error": "Codex is not installed"}
    rows: list[dict] = []
    cursor = None
    try:
        for _ in range(10):
            params: dict = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            r = srv.request("model/list", params, timeout=30) or {}
            rows += list(r.get("data") or [])
            cursor = r.get("nextCursor")
            if not cursor:
                break
    except RpcError as e:
        return {"models": [], "default": "", "error": e.message}
    out = []
    default = ""
    # every id this Codex accepts, hidden ones too, so a saved pick can be checked (fit_model)
    all_ids = [str(m.get("id") or m.get("model") or "") for m in rows if isinstance(m, dict)]
    for m in rows:
        if not isinstance(m, dict) or m.get("hidden"):
            continue
        mid = str(m.get("id") or m.get("model") or "")
        if not mid:
            continue
        efforts = [{"id": str(e.get("reasoningEffort") or ""), "description": str(e.get("description") or "")}
                   for e in (m.get("supportedReasoningEfforts") or []) if isinstance(e, dict)]
        tiers = [t for t in (m.get("serviceTiers") or []) if isinstance(t, dict)]
        fast = next((t for t in tiers if str(t.get("id")) == "priority"), None)
        row = {"id": mid, "name": str(m.get("displayName") or mid), "description": str(m.get("description") or ""),
               "efforts": [e for e in efforts if e["id"]], "default_effort": str(m.get("defaultReasoningEffort") or ""),
               "images": "image" in (m.get("inputModalities") or []), "is_default": bool(m.get("isDefault")),
               "fast": bool(fast) or "fast" in (m.get("additionalSpeedTiers") or []),
               "fast_note": str((fast or {}).get("description") or "")}
        if row["is_default"] and not default:
            default = mid
        out.append(row)
    v = {"models": out, "default": default or (out[0]["id"] if out else ""), "error": "",
         "all_ids": [i for i in all_ids if i]}
    _MODELS.update(at=now, v=v)
    return v


def fit_model(model: str) -> tuple[str, str]:
    """(model to send, a note for the feed). A model this Codex does not offer - one saved from the
    Studio's old fixed list (gpt-5-codex, o3, o4-mini) - would fail the turn; the default is used
    instead, and the feed says so."""
    m = (model or "").strip()
    if not m or m == "default":
        return "", ""
    try:
        ids = models().get("all_ids") or []
    except Exception:
        ids = []
    if ids and m not in ids:
        return "", f"This Codex does not offer {m}; the message went to its default model. Pick a model in the chat box settings."
    return m, ""


def _model_row(model: str) -> Optional[dict]:
    try:
        for m in models().get("models") or []:
            if m["id"] == model:
                return m
    except Exception:
        pass
    return None


def fit_effort(model: str, effort: str) -> str:
    """The effort to ask for, or "" for the model's own default. A level the model does not list
    is left out rather than sent - Codex refuses an unknown effort."""
    e = (effort or "").strip().lower()
    if not e or e == "default":
        return ""
    if e == "ultracode":
        e = "ultra"
    row = _model_row(model) if model and model != "default" else None
    if row is None and (not model or model == "default"):
        d = models().get("default") or ""
        row = _model_row(d) if d else None
    if row is None:
        return e
    ids = [x["id"] for x in row.get("efforts") or []]
    if e in ids:
        return e
    order = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
    if e in order:
        below = [x for x in order[:order.index(e)] if x in ids]
        if below:
            return below[-1]
    return ""


def install(update: bool = True) -> dict:
    """npm install -g @openai/codex@latest, on a worker. The running server is stopped first:
    Windows will not replace an .exe that a process has open."""
    npm = _npm()
    if not npm:
        return {"ok": False, "error": "Node.js is not installed, and Codex installs with npm. Install "
                                      "Node.js LTS from https://nodejs.org, restart the Studio, then try again."}
    if _INSTALL.get("running"):
        return {"ok": True, "running": True}
    if _busy():
        return {"ok": False, "error": "Codex is working in a chat. Stop it or wait, then update."}
    stop_server()
    _INSTALL.update(running=True, log=[], ok=None, error="", at=time.time(),
                    what="update" if (update and find_codex()) else "install")

    def work() -> None:
        try:
            p = subprocess.Popen([npm, "install", "-g", "@openai/codex@latest", "--no-fund", "--no-audit"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 creationflags=_NO_WINDOW)
            for raw in p.stdout:          # type: ignore[union-attr]
                s = raw.decode("utf-8", "replace").rstrip()
                if s:
                    _INSTALL["log"].append(s[:300])
                    del _INSTALL["log"][:-40]
            rc = p.wait(900)
            try:
                from .config import refresh_path
                refresh_path()
            except Exception:
                pass
            info = find_codex(refresh=True)
            ok = rc == 0 and bool(info)
            _INSTALL.update(ok=ok, error="" if ok else f"npm exited with {rc}" if rc else "codex was not found after the install")
            try:
                from . import agents
                agents.invalidate()
            except Exception:
                pass
            _LATEST.clear()
        except Exception as e:  # noqa: BLE001
            _INSTALL.update(ok=False, error=str(e))
        finally:
            _INSTALL["running"] = False

    threading.Thread(target=work, daemon=True, name="codex-install").start()
    return {"ok": True, "running": True}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
class _Conv:
    """What is happening in one folder's Codex conversation right now."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        # Two locks, on purpose. `lock` guards the fields below and is only ever held for a few
        # lines. `send_lock` keeps two sends from starting two turns, and is held across calls to
        # Codex - which the reader thread must never wait behind, or a notification that arrives
        # before its reply (turn/started often does) blocks the reply it is waiting for.
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.usage: dict = {}
        self.cwd = ""
        self.thread_id = ""
        self.turn_id = ""
        self.working = False
        self.turn_started = 0.0
        self.text = ""
        self.kind = ""
        self.activity = ""
        self.retry = ""
        self.out_base = 0
        self.out_now = 0
        self.gen_s = 0.0
        self.msg_started = 0.0
        self.model = ""
        self.effort = ""
        self.mode = "full"
        self.planner = False
        self.approvals: list[dict] = []
        self.input_requests: list[dict] = []
        self.queue: list[dict] = []
        self.subs: dict[str, dict] = {}
        self.last_pub = 0.0
        self.done: collections.deque = collections.deque(maxlen=64)   # turns already finished


_CONVS: dict[str, _Conv] = {}
_OWNER: dict[str, str] = {}            # thread id -> folder id (its own threads and its subagents')
_PARENT: dict[str, str] = {}           # subagent thread id -> the thread that started it
_STASH: dict[str, list] = {}           # notes for a thread not yet known to belong to anyone
_WAITERS: dict[str, dict] = {}         # one-shot runs (the co-agent review)
_IDX_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()


def bare(project_id: str) -> str:
    return project_id[len(CODEX_PREFIX):] if project_id.startswith(CODEX_PREFIX) else project_id


def _conv(project_id: str) -> _Conv:
    pid = bare(project_id)
    c = _CONVS.get(pid)
    if c is None:
        c = _CONVS.setdefault(pid, _Conv(pid))
    return c


def _safe(name: str) -> str:
    return re.sub(r"\.{2,}", ".", re.sub(r"[^A-Za-z0-9_.-]", "_", name or ""))[:160]


def _index_path(pid: str) -> Path:
    return _PROJECTS / f"{_safe(pid)}.json"


def _index(pid: str) -> dict:
    try:
        d = json.loads(_index_path(pid).read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("threads", {})
            return d
    except (OSError, ValueError):
        pass
    return {"active": "", "threads": {}}


def _save_index(pid: str, d: dict) -> None:
    _PROJECTS.mkdir(parents=True, exist_ok=True)
    path = _index_path(pid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    fsutil.replace(tmp, path)


def _index_update(pid: str, fn: Callable[[dict], None]) -> dict:
    with _IDX_LOCK:
        d = _index(pid)
        fn(d)
        _save_index(pid, d)
        return d


def _log_path(thread_id: str) -> Path:
    return _THREADS / f"{_safe(thread_id)}.jsonl"


def _log(thread_id: str, rec: dict) -> None:
    if not thread_id:
        return
    rec.setdefault("at", time.time())
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _LOG_LOCK:
        _THREADS.mkdir(parents=True, exist_ok=True)
        with open(_log_path(thread_id), "a", encoding="utf-8") as fh:
            fh.write(line)


def _read_log(thread_id: str, tail_bytes: int = 0) -> list[dict]:
    path = _log_path(thread_id)
    try:
        with open(path, "rb") as fh:
            if tail_bytes:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - tail_bytes))
                if size > tail_bytes:
                    fh.readline()           # a partial first line
            raw = fh.read()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def _developer_notes() -> str:
    """What a Codex thread is told about the Studio. Codex has no --append-system-prompt; the
    thread's developer instructions are the channel, sent once and cached, where the old path put
    the Claude graph note in front of every single message."""
    parts = []
    if settings.get("cc_graphify"):
        host = str(settings.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        base = f"http://{host}:{settings.get('port') or 8777}/api/graphify/query"
        parts.append(
            "CODE GRAPH: this project has a live code knowledge-graph kept current by Asset Studio. Ask it "
            "where a symbol is declared, what calls it and what it uses, before a broad text search:\n"
            f"  curl -s --get '{base}' --data-urlencode 'root=<ABSOLUTE WORKSPACE PATH>' --data-urlencode 'q=<symbol>'\n"
            "Do not read graphify-out/graph.json itself - it is megabytes. Text searches (strings, "
            "config values) still belong to rg.")
    # BOOST's one constant note. It rides the thread's developer instructions, which Codex sends
    # once and caches, so it is paid for once per thread rather than once per message.
    try:
        from . import boost
        if boost.enabled():
            _bd = boost.directive()
            if _bd:
                parts.append(_bd)
    except Exception:
        pass                       # a saving never fails a send
    return "\n\n".join(parts)


def _mcp_config() -> Optional[dict]:
    """The Studio's own tool server, the same one Claude sessions get (Settings -> Studio engine
    -> "Studio tools over MCP")."""
    try:
        from . import cc_session
        if not cc_session._mcp_on():
            return None
        script = Path(__file__).resolve().parent / "mcp_engine.py"
        if not script.is_file():
            return None
        return {"command": sys.executable, "args": [str(script)],
                "env": {"STUDIO_BASE": cc_session._base_url(), "PYTHONIOENCODING": "utf-8"}}
    except Exception:
        return None


def _thread_params(cwd: str, mode: str, model: str) -> dict:
    approval, _pol = _policy(mode)
    p: dict = {"cwd": cwd, "approvalPolicy": approval, "sandbox": MODES[mode_of(mode)][1]}
    if model and model != "default":
        p["model"] = model
    notes = _developer_notes()
    if notes:
        p["developerInstructions"] = notes
    mcp = _mcp_config()
    if mcp:
        mcp["env"] = {**mcp["env"], "STUDIO_CWD": cwd}
        p["config"] = {"mcp_servers": {"studio": mcp}}
    # BOOST: cap how much of one tool output stays in the thread (see boost.codex_tool_tokens).
    try:
        from . import boost
        lim = boost.codex_tool_tokens()
        if lim:
            p.setdefault("config", {})["tool_output_token_limit"] = lim
    except Exception:
        pass
    return p


def _inputs(message: str, images: Optional[list[str]]) -> list[dict]:
    out: list[dict] = []
    if message:
        out.append({"type": "text", "text": message, "text_elements": []})
    for im in images or []:
        if im and Path(im).is_file():
            out.append({"type": "localImage", "path": str(Path(im).resolve())})
    return out


def _answer_approval(conv: _Conv, text: str) -> Optional[dict]:
    """A chat message that answers the oldest waiting approval, or None."""
    t = (text or "").strip().rstrip(".!").lower()
    choice = {"allow": ALLOW, "yes": ALLOW, "approve": ALLOW, "allow for this session": ALLOW_SESSION,
              "always": ALLOW_SESSION, "deny": DENY, "no": DENY, "decline": DENY}.get(t)
    with conv.lock:
        if not conv.approvals or choice is None:
            return None
        ap = conv.approvals.pop(0)
    srv = ap.get("srv") or _SRV
    if srv is None or not srv.alive():
        return {"ok": False, "error": "Codex stopped while it waited"}
    try:
        srv.respond(ap["rid"], _decision(ap["method"], choice, ap.get("params") or {}))
    except RpcError as e:
        return {"ok": False, "error": e.message}
    _log(conv.thread_id, {"t": "approval", "answer": choice, "what": ap.get("what", "")})
    _publish(conv, force=True)
    return {"ok": True, "agent": "codex", "streams": True, "approval": choice, "session_id": conv.thread_id}


def _answer_input(conv: _Conv, text: str) -> Optional[dict]:
    """Answer the next Planner question; return all answers together to the pending RPC."""
    answer = (text or "").strip()
    if not answer:
        return None
    with conv.send_lock:
        with conv.lock:
            if not conv.input_requests:
                return None
            req = conv.input_requests[0]
            question = next((q for q in req["questions"] if q["id"] not in req["answers"]), None)
            if question is None:
                return None
            answers = {**req["answers"], question["id"]: {"answers": [answer]}}
        srv = req["srv"]
        if not srv.alive():
            return {"ok": False, "agent": "codex", "error": "Codex stopped while it waited for your answer."}
        complete = len(answers) == len(req["questions"])
        if complete:
            try:
                srv.respond(req["rid"], {"answers": answers})
            except RpcError as e:
                return {"ok": False, "agent": "codex", "error": e.message}
        with conv.lock:
            req["answers"] = answers
            if complete and req in conv.input_requests:
                conv.input_requests.remove(req)
            conv.activity = "Waiting for your answer" if conv.input_requests else "Thinking"
        _log(req["thread"], {"t": "user", "id": uuid.uuid4().hex, "text": answer})
        _publish(conv, force=True)
        return {"ok": True, "agent": "codex", "streams": True, "session_id": req["thread"], "user_input": True}


def send(project_id: str, message: str, cwd: str, model: str = "default", effort: str = "default",
         mode: str = "", images: Optional[list[str]] = None, new_session: bool = False,
         session: str = "", fast: bool = False, planner: bool = False, steer: bool = False) -> dict:
    """A chat message to Codex: answers a waiting approval, steers a running turn, or starts one."""
    info = find_codex()
    if not info:
        return {"ok": False, "needs_install": True, "agent": "codex",
                "error": "Codex is not installed. Install it from the chat box: pick Codex, then Install."}
    try:
        srv = server()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "agent": "codex", "error": f"Codex could not start: {e}"}
    if srv is None:
        return {"ok": False, "needs_install": True, "agent": "codex", "error": "Codex is not installed."}
    try:
        acct = _read_account(srv)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "agent": "codex", "error": f"Codex did not report its sign-in: {getattr(e, 'message', e)}"}
    if not ready_to_chat(acct):
        return {"ok": False, "needs_login": True, "agent": "codex",
                "error": "Codex is not signed in. Sign in from the chat box: ChatGPT, a code, or an API key."}
    conv = _conv(project_id)
    pid = conv.project_id
    conv.cwd = cwd or conv.cwd
    answered = _answer_approval(conv, message) if not images and not steer else None
    if answered is not None:
        return answered
    answered = _answer_input(conv, message) if not images and not steer and not new_session and (not session or session == conv.thread_id) else None
    if answered is not None:
        return answered
    text = (message or "").strip()
    if not steer and text.lower() in ("/compact",):
        return _compact(conv, srv)
    inputs = _inputs(text, images)
    if not inputs:
        return {"ok": False, "error": "empty message"}
    user_rec = {"t": "user", "id": uuid.uuid4().hex, "text": text,
                "images": [str(Path(i).resolve()) for i in images or [] if i and Path(i).is_file()]}
    job = {"inputs": inputs, "rec": user_rec, "model": model, "effort": effort, "mode": mode,
           "fast": fast, "planner": planner}
    with conv.send_lock:
        with conv.lock:
            busy = bool(conv.working and conv.turn_id and conv.thread_id)
            cur_tid, cur_turn = conv.thread_id, conv.turn_id
        switching = bool(new_session or (session and session != cur_tid))
        if steer and not busy:
            return {"ok": False, "agent": "codex", "error": "The turn has finished. Use Send to start another turn."}
        if busy and switching:
            # A new or another conversation while this one works. This used to interrupt the
            # running turn silently — and with two panes on one folder, "new chat" in one pane
            # stopped the other pane's Codex answer mid-turn. Refuse and point at Stop instead,
            # the same as the Claude side.
            return {"ok": False, "agent": "codex", "busy": True, "error": (
                "Codex is still working in this folder's current conversation (maybe in another "
                "pane). Starting or switching conversation would stop it. Press Stop first, or "
                "wait for it to finish.")}
        if busy:
            # Steer cannot change collaboration mode. Let the next turn apply the saved choice.
            with conv.lock:
                if conv.planner != planner and not steer:
                    conv.queue.append(job)
                    return {"ok": True, "agent": "codex", "streams": True, "session_id": cur_tid,
                            "queued": True, "model": model, "permission_mode": mode_of(mode)}
            try:
                srv.request("turn/steer", {"threadId": cur_tid, "expectedTurnId": cur_turn, "input": inputs},
                            timeout=30)
                _log(cur_tid, {**user_rec, "steer": True})
                _publish(conv, force=True)
                return {"ok": True, "agent": "codex", "streams": True, "session_id": cur_tid,
                        "steering": True, "steer_mode": "live", "model": model, "permission_mode": mode_of(mode)}
            except RpcError as e:
                if steer:
                    return {"ok": False, "agent": "codex", "error": f"Codex could not steer this turn: {e.message}. Use Send to queue the update."}
                with conv.lock:
                    still = conv.working
                    if still:
                        conv.queue.append(job)
                if still:
                    return {"ok": True, "agent": "codex", "streams": True, "session_id": cur_tid,
                            "queued": True, "model": model, "permission_mode": mode_of(mode)}
                # the turn ended between the check and the steer: start a new one below
        return _start_turn(conv, srv, inputs, user_rec, model, effort, mode, fast, new_session, session, planner)


def _open_thread(srv: AppServer, method: str, params: dict) -> dict:
    """thread/start or thread/resume. The Studio's tool server rides in `config`; if this Codex
    will not take it, the conversation is opened without it rather than not at all."""
    try:
        return srv.request(method, params, timeout=60) or {}
    except RpcError:
        if "config" not in params:
            raise
        # BOOST's output cap is the newest key in `config`: drop it first, so a Codex that does
        # not take it still gets the Studio's tool server.
        cfg = {k: v for k, v in (params.get("config") or {}).items() if k != "tool_output_token_limit"}
        if cfg and cfg != params.get("config"):
            try:
                return srv.request(method, {**params, "config": cfg}, timeout=60) or {}
            except RpcError:
                pass
        slim = {k: v for k, v in params.items() if k != "config"}
        return srv.request(method, slim, timeout=60) or {}


def _start_turn(conv: _Conv, srv: AppServer, inputs: list[dict], user_rec: dict, model: str, effort: str,
                mode: str, fast: bool, new_session: bool = False, session: str = "", planner: bool = False) -> dict:
    """Open (or reopen) the folder's thread and start a turn in it. Called with send_lock held and
    `lock` NOT held: the calls below wait on Codex."""
    pid = conv.project_id
    model, model_note = fit_model(model)
    idx = _index(pid)
    tid = "" if new_session else (session or idx.get("active") or "")
    tp = _thread_params(conv.cwd, mode, model)
    if tid and tid not in srv.loaded:
        _OWNER[tid] = pid
        try:
            rr = _open_thread(srv, "thread/resume", {"threadId": tid, **tp})
            srv.loaded.add(tid)
            if rr.get("model"):
                with conv.lock:
                    conv.model = str(rr["model"])      # the header names the model after a restart too
        except RpcError as e:
            _log(tid, {"t": "note", "text": f"Codex could not reopen this conversation ({e.message}); a new one starts."})
            tid = ""
    if not tid:
        try:
            r = _open_thread(srv, "thread/start", tp)
        except RpcError as e:
            return {"ok": False, "agent": "codex", "error": f"Codex could not start a conversation: {e.message}"}
        tid = str(((r.get("thread") or {}).get("id")) or "")
        if not tid:
            return {"ok": False, "agent": "codex", "error": "Codex started no conversation"}
        srv.loaded.add(tid)
        _OWNER[tid] = pid
        for method, p in _STASH.pop(tid, []):      # its thread/started, sent before the reply
            try:
                _on_main_note(conv, tid, method, p)
            except Exception:
                pass
        with conv.lock:
            conv.model = str(r.get("model") or "")
        now = time.time()

        def add(d: dict) -> None:
            d["threads"][tid] = {"created": now, "cwd": conv.cwd, "title": (user_rec.get("text") or "")[:90]}
        _index_update(pid, add)
    _OWNER[tid] = pid
    with conv.lock:
        conv.thread_id = tid

    def act(d: dict) -> None:
        d["active"] = tid
        d["threads"].setdefault(tid, {"created": time.time(), "cwd": conv.cwd,
                                      "title": (user_rec.get("text") or "")[:90]})
        d["threads"][tid]["updated"] = time.time()
    _index_update(pid, act)
    approval, pol = _policy(mode)
    fx = fit_effort(model, effort)
    params: dict = {"threadId": tid, "input": inputs, "approvalPolicy": approval, "sandboxPolicy": pol,
                    "summary": "auto"}
    if model and model != "default":
        params["model"] = model
    if fx:
        params["effort"] = fx
    if fast:
        params["serviceTier"] = "priority"
    # Explicitly send default when leaving Plan mode: Codex remembers mode on the thread.
    # Use the model returned by thread/start/resume, preserving custom provider defaults.
    active_model = model or conv.model or models().get("default") or ""
    if active_model:
        params["collaborationMode"] = {
            "mode": "plan" if planner else "default",
            "settings": {"model": active_model, "reasoning_effort": fx or None,
                         "developer_instructions": None},
        }
    elif planner:
        return {"ok": False, "agent": "codex", "error": "Codex did not report a model for Planner. Pick a model and try again."}
    with conv.lock:
        conv.mode, conv.effort = mode_of(mode), fx
        conv.planner = planner
        if model and model != "default":
            conv.model = model
    _log(tid, user_rec)
    if model_note:
        _log(tid, {"t": "note", "text": model_note})
    try:
        r = srv.request("turn/start", params, timeout=60) or {}
    except RpcError as e:
        _log(tid, {"t": "error", "message": f"Codex refused the message: {e.message}"})
        return {"ok": False, "agent": "codex", "error": f"Codex refused the message: {e.message}"}
    turn = r.get("turn") or {}
    turn_id = str(turn.get("id") or "")
    with conv.lock:
        # A quick turn can be over before this reply is read (its turn/started and turn/completed
        # came first); marking it working now would leave the chat spinning for ever.
        if turn_id and turn_id not in conv.done:
            conv.turn_id = turn_id
            if not conv.working:
                _begin(conv)
    _publish(conv, force=True)
    return {"ok": True, "agent": "codex", "streams": True, "session_id": tid, "model": model,
            "permission_mode": mode_of(mode), "cwd": conv.cwd, "pid": srv.pid}


def _compact(conv: _Conv, srv: AppServer) -> dict:
    if not conv.thread_id:
        return {"ok": False, "agent": "codex", "error": "Nothing to compact yet."}
    try:
        if conv.thread_id not in srv.loaded:
            srv.request("thread/resume", {"threadId": conv.thread_id, **_thread_params(conv.cwd, conv.mode, "")}, timeout=60)
            srv.loaded.add(conv.thread_id)
        srv.request("thread/compact/start", {"threadId": conv.thread_id}, timeout=60)
    except RpcError as e:
        return {"ok": False, "agent": "codex", "error": e.message}
    _log(conv.thread_id, {"t": "note", "text": "Compacting the conversation…"})
    return {"ok": True, "agent": "codex", "streams": True, "session_id": conv.thread_id, "compacting": True}


def _begin(conv: _Conv) -> None:
    conv.working = True
    conv.turn_started = time.time()
    conv.text, conv.kind, conv.activity, conv.retry = "", "", "Thinking", ""
    conv.gen_s, conv.msg_started = 0.0, 0.0
    conv.out_base = conv.out_now


def cancel(project_id: str) -> dict:
    conv = _CONVS.get(bare(project_id))
    srv = server(start=False)
    if conv is None or srv is None or not conv.working:
        return {"ok": True, "idle": True}
    conv.queue.clear()
    try:
        srv.request("turn/interrupt", {"threadId": conv.thread_id, "turnId": conv.turn_id}, timeout=20)
    except RpcError as e:
        return {"ok": False, "error": e.message}
    return {"ok": True, "interrupted": True}


def known_cwd(project_id: str) -> str:
    """The folder this Codex conversation works in, as far as Codex knows it: the live one, then
    the newest thread's. A folder that only ever talked to Codex has no Claude transcript to read
    it from, and a question answered from the feed arrives without the folder's path."""
    pid = bare(project_id)
    c = _CONVS.get(pid)
    if c is not None and c.cwd and Path(c.cwd).is_dir():
        return c.cwd
    d = _index(pid)
    threads = d.get("threads") or {}
    order = [d.get("active") or ""] + sorted(threads, key=lambda t: -float((threads[t] or {}).get("updated")
                                                                           or (threads[t] or {}).get("created") or 0))
    for tid in order:
        cwd = str((threads.get(tid) or {}).get("cwd") or "")
        if cwd and Path(cwd).is_dir():
            return cwd
    return ""


def is_sending(project_id: str) -> bool:
    c = _CONVS.get(bare(project_id))
    return bool(c and (c.working or c.queue))


def new_conversation(project_id: str) -> dict:
    """/clear: the next message starts a fresh Codex thread; the old one stays in the list."""
    pid = bare(project_id)
    _index_update(pid, lambda d: d.update(active=""))
    c = _CONVS.get(pid)
    if c is not None and not c.working:
        c.thread_id = ""
    return {"ok": True}


# ---------------------------------------------------------------------------
# What Codex tells us
# ---------------------------------------------------------------------------
def _on_note(srv: AppServer, method: str, p: dict) -> None:
    if method == "__exit__":
        if _SRV is not None and srv is not _SRV:
            return                          # an old process (before an update) ending: not ours now
        for c in list(_CONVS.values()):
            if c.working:
                _log(c.thread_id, {"t": "error", "message": "The Codex app-server stopped. Send again to continue."})
                c.working = False
                c.approvals.clear()
                c.input_requests.clear()
                _publish(c, force=True)
        for w in list(_WAITERS.values()):
            w["error"] = "Codex stopped"
            w["ev"].set()
        return
    if method.startswith("account/"):
        _on_account_note(method, p)
        return
    if method == "windowsSandbox/setupCompleted":
        _SANDBOX.clear()
        return
    tid = str(p.get("threadId") or (p.get("thread") or {}).get("id") or "")
    if not tid:
        return
    w = _WAITERS.get(tid)
    if w is not None:
        _on_waiter_note(w, method, p)
        return
    pid = _OWNER.get(tid)
    if pid is None:
        par = str((p.get("thread") or {}).get("parentThreadId") or "")
        if par and par in _OWNER:
            _adopt(tid, par)
            pid = _OWNER.get(tid)
        else:
            lst = _STASH.setdefault(tid, [])
            if len(lst) < 400:
                lst.append((method, p))
            if len(_STASH) > 64:
                _STASH.pop(next(iter(_STASH)))
            return
    conv = _CONVS.get(pid)
    if conv is None:
        conv = _conv(pid)
    if method == "thread/closed":
        srv.loaded.discard(tid)
    if tid in _PARENT:
        _on_sub_note(conv, tid, method, p)
    else:
        _on_main_note(conv, tid, method, p)


def _adopt(sub: str, parent: str) -> None:
    """A subagent thread now known to belong to `parent`: route its notes, replay the early ones."""
    if not sub or sub in _OWNER:
        return
    pid = _OWNER.get(parent)
    if pid is None:
        return
    _OWNER[sub] = pid
    _PARENT[sub] = parent
    conv = _conv(pid)
    conv.subs.setdefault(sub, {"started": time.time(), "running": True, "path": "",
                               "parent": parent, "activity": {}, "last": time.time()})
    for method, p in _STASH.pop(sub, []):
        try:
            _on_sub_note(conv, sub, method, p)
        except Exception:
            pass


_ACTIVITY = {
    "reasoning": "Thinking", "agentMessage": "Writing", "fileChange": "Editing files",
    "webSearch": "Searching the web", "imageGeneration": "Generating an image",
    "contextCompaction": "Compacting the conversation", "collabAgentToolCall": "Working with agents",
    "subAgentActivity": "Starting an agent", "imageView": "Looking at an image", "plan": "Planning",
}


def _activity(item: dict) -> str:
    typ = str(item.get("type") or "")
    if typ == "commandExecution":
        acts = item.get("commandActions") or []
        cmd = str((acts[0] or {}).get("command") if acts and isinstance(acts[0], dict) else "") or _short_cmd(item.get("command"))
        return ("Running " + " ".join(cmd.split()))[:64]
    if typ == "fileChange":
        ch = item.get("changes") or []
        if ch and isinstance(ch[0], dict):
            return ("Editing " + Path(str(ch[0].get("path") or "")).name)[:64]
    if typ == "mcpToolCall":
        return f"{item.get('server') or 'tool'} · {item.get('tool') or ''}"[:64]
    return _ACTIVITY.get(typ, "Working")


def _short_cmd(cmd: Any) -> str:
    """The command a person typed, out of Codex's `"...powershell.exe" -Command '...'` wrapper."""
    s = str(cmd or "")
    m = re.match(r"""^\s*"?[^"]*?(?:powershell|pwsh|cmd|bash|sh)(?:\.exe)?"?\s+(?:-NoProfile\s+)?(?:-Command|/c|-lc|-c)\s+(.*)$""", s, re.I | re.S)
    if m:
        s = m.group(1).strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
            s = s[1:-1]
    return s


# The Codex item types that ARE a tool doing something, as opposed to the model talking. The split
# is what makes `phase` mean anything: one of these is still running while it happens, and the
# moment it completes the agent is writing again. Naming a finished command as if it were still
# running is what made a working agent read as hung for thirteen minutes.
_TOOL_ITEMS = ("commandExecution", "fileChange", "mcpToolCall", "webSearch",
               "imageGeneration", "imageView")


def _sub_activity(item: dict, prev: Optional[dict] = None) -> dict:
    """What ONE delegated agent is doing this instant, in the shape the pill draws.

    The parent's own ``_activity()`` returns one sentence for a line of the feed. A subagent needs
    that row split up — the tool on its own, the detail beside it, and ``phase`` — because the
    bottom bar shows it under the agent's name and has to answer "is it working or stuck".

    A tool item in ``phase: "tool"`` means it is still running. When it completes the phase flips
    to ``"generating"`` and the last tool is kept, so the row reads "writing · after Read" rather
    than claiming the read is still in flight.
    """
    typ = str(item.get("type") or "")
    if typ not in _TOOL_ITEMS:
        # reasoning / agentMessage / plan: the model itself is working. Keep the last tool.
        a = dict(prev or {})
        a["phase"] = "generating"
        return a
    tool, detail = _ACTIVITY.get(typ, "Working"), ""
    if typ == "commandExecution":
        acts = item.get("commandActions") or []
        cmd = str((acts[0] or {}).get("command") if acts and isinstance(acts[0], dict) else "") \
            or _short_cmd(item.get("command"))
        tool, detail = "Running", " ".join(cmd.split())
    elif typ == "fileChange":
        ch = item.get("changes") or []
        tool = "Editing"
        if ch and isinstance(ch[0], dict):
            detail = Path(str(ch[0].get("path") or "")).name
    elif typ == "mcpToolCall":
        tool, detail = str(item.get("server") or "tool"), str(item.get("tool") or "")
    return {"tool": tool[:48], "detail": detail[:160], "phase": "tool"}


def _on_main_note(conv: _Conv, tid: str, method: str, p: dict) -> None:
    force = False
    if conv.thread_id and tid != conv.thread_id:
        # A turn of a conversation this folder has moved away from (a new chat was started while
        # it was stopping): keep its record, leave the live state to the current conversation.
        if method == "item/completed":
            item = p.get("item") or {}
            if item.get("type") not in ("userMessage", "hookPrompt", "functionCallOutput"):
                _log(tid, {"t": "item", "turn": str(p.get("turnId") or ""), "item": _slim(item)})
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            _log(tid, {"t": "turn_end", "turn": str(turn.get("id") or ""), "status": str(turn.get("status") or "")})
        return
    if method == "turn/started":
        turn = p.get("turn") or {}
        turn_id = str(turn.get("id") or "")
        with conv.lock:
            conv.thread_id = tid
            if turn_id not in conv.done:
                conv.turn_id = turn_id or conv.turn_id
                if not conv.working:
                    _begin(conv)
        _log(tid, {"t": "turn_start", "turn": conv.turn_id, "model": conv.model, "effort": conv.effort,
                   "mode": conv.mode})
        force = True
    elif method == "item/started":
        item = p.get("item") or {}
        typ = item.get("type")
        with conv.lock:
            conv.retry = ""
            if typ != "userMessage":
                conv.activity = _activity(item)
            if typ == "agentMessage":
                conv.text, conv.kind, conv.msg_started = "", "text", time.time()
            elif typ == "reasoning":
                conv.kind = "thinking"
        if typ == "subAgentActivity" and item.get("agentThreadId"):
            _adopt(str(item["agentThreadId"]), tid)
        if typ == "collabAgentToolCall":
            for sub in item.get("receiverThreadIds") or []:
                _adopt(str(sub), tid)
        force = True
    elif method == "item/agentMessage/delta":
        with conv.lock:
            conv.text += str(p.get("delta") or "")
            conv.kind = "text"
            conv.retry = ""
    elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
        with conv.lock:
            conv.activity = "Thinking"
    elif method == "item/completed":
        item = p.get("item") or {}
        typ = item.get("type")
        if typ in ("userMessage", "hookPrompt", "functionCallOutput"):
            return
        if typ == "agentMessage":
            with conv.lock:
                if conv.msg_started:
                    conv.gen_s += time.time() - conv.msg_started
                    conv.msg_started = 0.0
                conv.text = ""
        if typ == "imageGeneration":
            item = _keep_image(conv, item)
        if typ == "subAgentActivity" and item.get("agentThreadId"):
            sub = str(item["agentThreadId"])
            _adopt(sub, tid)
            if str(item.get("kind")) in ("completed", "interrupted"):
                s = conv.subs.get(sub)
                if s is not None:
                    s["running"] = False
                    s["ended"] = time.time()
            if item.get("agentPath") and sub in conv.subs:
                conv.subs[sub]["path"] = str(item["agentPath"])
        if typ == "collabAgentToolCall":
            for sub in item.get("receiverThreadIds") or []:
                _adopt(str(sub), tid)
            for sub, st in (item.get("agentsStates") or {}).items():
                s = conv.subs.get(str(sub))
                if s is not None and isinstance(st, dict) and str(st.get("status")) in (
                        "completed", "errored", "shutdown", "interrupted", "notFound"):
                    s["running"] = False
                    s.setdefault("ended", time.time())
        _log(tid, {"t": "item", "turn": str(p.get("turnId") or conv.turn_id), "item": _slim(item)})
        force = True
    elif method == "turn/plan/updated":
        _log(tid, {"t": "plan", "turn": str(p.get("turnId") or ""), "explanation": p.get("explanation"),
                   "plan": p.get("plan") or []})
    elif method == "thread/tokenUsage/updated":
        tu = p.get("tokenUsage") or {}
        tot = (tu.get("total") or {})
        with conv.lock:
            conv.out_now = int(tot.get("outputTokens") or 0)
        with conv.lock:
            conv.usage[tid] = {"total": tot, "last": tu.get("last") or {}, "window": tu.get("modelContextWindow")}
    elif method == "error":
        err = p.get("error") or {}
        msg = str(err.get("message") or "error")
        if p.get("willRetry"):
            with conv.lock:
                conv.retry = msg
                conv.activity = msg[:64]
        else:
            _log(tid, {"t": "error", "message": msg, "details": str(err.get("additionalDetails") or "")[:2000]})
        force = True
    elif method == "turn/completed":
        turn = p.get("turn") or {}
        err = turn.get("error") or None
        rec = {"t": "turn_end", "turn": str(turn.get("id") or conv.turn_id), "status": str(turn.get("status") or ""),
               "duration_ms": turn.get("durationMs"), "model": conv.model, "effort": conv.effort,
               "out_tokens": max(0, conv.out_now - conv.out_base), "gen_s": round(conv.gen_s, 1)}
        if err:
            rec["error"] = str(err.get("message") or "")
            rec["details"] = str(err.get("additionalDetails") or "")[:2000]
        _log(tid, rec)
        if err and _auth_error(rec.get("error", "") + " " + rec.get("details", "")):
            _ACCOUNT.clear()
        with conv.lock:
            conv.done.append(rec["turn"])
            conv.working = False
            conv.text, conv.activity, conv.retry = "", "", ""
            conv.approvals.clear()
            conv.input_requests.clear()
            nxt = conv.queue.pop(0) if conv.queue else None
            use = conv.usage.get(tid)

        # CODEX SPEND WAS INVISIBLE — AND CODEX IS THE ENGINE THIS MACHINE RUNS.
        #
        # `spend.record` and `turns.record` had exactly one caller: the Claude stream handler. So
        # every Codex turn banked nothing, and the Dashboard's cost card could not see the agent
        # actually doing the work. Codex reports its own per-turn usage (`tokenUsage.last`), which
        # is all this needs — the same input / cache_read / cache_write / output bundle `pricing`
        # takes for a Claude turn.
        #
        # `last.inputTokens` ALREADY INCLUDES the cached ones, so the fresh part is the difference;
        # charging both would double the input side. An id the rate card does not know prices at 0
        # with basis "unknown": the TOKENS still bank, so the turn is visible, and the dollars are
        # marked unmeasured instead of being reported as free. `pricing_overrides` in settings is
        # where a rate can be supplied.
        try:
            from . import pricing, spend, turns as _turns
            last = (use or {}).get("last") or {}
            inp = int(last.get("inputTokens") or 0)
            cached = int(last.get("cachedInputTokens") or 0)
            out_tok = int(last.get("outputTokens") or 0)
            if inp or out_tok:
                usage = {"input": max(0, inp - cached), "cache_read": cached,
                         "cache_write": int(last.get("cacheWriteInputTokens") or 0),
                         "output": out_tok}
                mdl = str(conv.model or rec.get("model") or "")
                cost, basis = pricing.cost_with_basis(mdl, usage)
                _turns.record(conv.project_id, mdl, str(conv.effort or "default"), out_tok,
                              float(rec.get("gen_s") or 0), 0.0, 0, cost, False, 0, basis)
                if cost > 0:
                    spend.record(conv.project_id, mdl, cost, out_tok)
        except Exception:
            pass

        model_now = conv.model

        def keep(d: dict) -> None:
            meta = d["threads"].setdefault(tid, {})
            meta["updated"] = time.time()
            if use:
                meta["usage"] = use
            if model_now:
                meta["model"] = model_now      # the header names it after a restart, before a message
        _index_update(conv.project_id, keep)
        force = True
        if nxt is not None:
            threading.Thread(target=_run_queued, args=(conv, nxt), daemon=True).start()
    elif method == "thread/compacted":
        _log(tid, {"t": "note", "text": "The conversation was compacted."})
    elif method == "model/rerouted":
        to = p.get("toModel") or p.get("model") or ""
        if to:
            _log(tid, {"t": "note", "text": f"Codex answered with {to}."})
    elif method == "thread/status/changed":
        st = (p.get("status") or {}).get("type")
        if st == "systemError":
            _log(tid, {"t": "error", "message": "Codex reported a system error in this conversation."})
    _publish(conv, force=force)


def _auth_error(text: str) -> bool:
    t = (text or "").lower()
    return "401" in t or "unauthorized" in t or "not logged in" in t or "invalid api key" in t


def _run_queued(conv: _Conv, job: dict) -> None:
    srv = _SRV
    if srv is None or not srv.alive():
        return
    with conv.send_lock:
        _start_turn(conv, srv, job["inputs"], job["rec"], job["model"], job["effort"], job["mode"], job["fast"],
                    planner=job.get("planner", False))


def _on_sub_note(conv: _Conv, sub: str, method: str, p: dict) -> None:
    s = conv.subs.setdefault(sub, {"started": time.time(), "running": True, "path": "",
                                   "parent": _PARENT.get(sub, ""), "activity": {}, "last": time.time()})
    # The moment it last said anything. `idle_s` is measured from here, and it is the figure that
    # separates "writing" from "quiet inside one long step" — the distinction the pill draws.
    s["last"] = time.time()
    if method == "turn/started":
        s["running"] = True
    elif method == "item/started":
        item = p.get("item") or {}
        if item.get("type") not in ("userMessage",):
            s["activity"] = _sub_activity(item, s.get("activity"))
        if item.get("type") == "subAgentActivity" and item.get("agentThreadId"):
            _adopt(str(item["agentThreadId"]), sub)
    elif method == "item/completed":
        item = p.get("item") or {}
        typ = item.get("type")
        if typ in _TOOL_ITEMS:
            # The tool came back. From this moment the agent is writing, which is a different
            # question from "is a command still running" — see `_sub_activity`.
            a = dict(s.get("activity") or {})
            a["phase"] = "generating"
            s["activity"] = a
        if typ in ("hookPrompt", "functionCallOutput"):
            return
        if typ == "imageGeneration":
            item = _keep_image(conv, item)
        if typ == "agentMessage":
            s["result"] = str(item.get("text") or "")[:4000]
        if typ == "userMessage" and not s.get("prompt"):
            s["prompt"] = " ".join(str(c.get("text") or "") for c in item.get("content") or [] if isinstance(c, dict))[:4000]
        if typ not in ("userMessage", "agentMessage", "reasoning"):
            s["tools"] = int(s.get("tools") or 0) + 1
        _log(sub, {"t": "item", "turn": str(p.get("turnId") or ""), "item": _slim(item)})
    elif method == "turn/completed":
        turn = p.get("turn") or {}
        s["running"] = False
        s["ended"] = time.time()
        s["status"] = str(turn.get("status") or "")
        _log(sub, {"t": "turn_end", "turn": str(turn.get("id") or ""), "status": s["status"],
                   "duration_ms": turn.get("durationMs"),
                   **({"error": str((turn.get("error") or {}).get("message") or "")} if turn.get("error") else {})})
        par = _PARENT.get(sub, "")
        _index_update(conv.project_id, lambda d: d.setdefault("subs", {}).update(
            {sub: {k: s.get(k) for k in ("started", "ended", "path", "prompt", "result", "tools", "status",
                                         "parent", "nickname", "model", "tokens", "wire", "usage",
                                         "ctx_max")}}))
        if par:
            _log(par, {"t": "sub_end", "sub": sub})
        if not s.get("nickname"):
            threading.Thread(target=_name_sub, args=(conv, sub), daemon=True).start()
    elif method == "thread/tokenUsage/updated":
        tu = p.get("tokenUsage") or {}
        d = _sub_tokens(tu.get("total") or {}, int(tu.get("modelContextWindow") or 0))
        s.update(d)
        # KEPT, not just held in memory. Codex reports this once per turn and keeps it nowhere the
        # Studio can read, so a restart used to leave every agent's bill at zero — the one number
        # on the card that is supposed to say what the fan-out cost.
        _log(sub, {"t": "tokens", **d})
    _publish(conv)


def _name_sub(conv: _Conv, sub: str) -> None:
    """A subagent's nickname and model, from Codex's own record of the thread. Its task text is
    not among them: Codex hands it over encrypted, so the card names the agent instead."""
    srv = _SRV
    if srv is None or not srv.alive():
        return
    try:
        th = (srv.request("thread/read", {"threadId": sub}, timeout=15) or {}).get("thread") or {}
    except Exception:
        return
    s = conv.subs.setdefault(sub, {})
    if th.get("agentNickname"):
        s["nickname"] = str(th["agentNickname"])
    if th.get("model"):
        s["model"] = str(th["model"])
    spawn = (((th.get("source") or {}).get("subAgent") or {}).get("thread_spawn") or {})
    if spawn.get("agent_path") and not s.get("path"):
        s["path"] = str(spawn["agent_path"])
    _index_update(conv.project_id, lambda d: d.setdefault("subs", {}).setdefault(sub, {}).update(
        {k: s.get(k) for k in ("nickname", "model", "path") if s.get(k)}))
    _publish(conv, force=True)


def _slim(item: dict) -> dict:
    """The item as kept on disk: what the feed draws, not the base64 of an image or a novel of output."""
    it = dict(item)
    if it.get("type") == "imageGeneration":
        it.pop("result", None)
    if it.get("type") == "commandExecution" and isinstance(it.get("aggregatedOutput"), str):
        out = it["aggregatedOutput"]
        if len(out) > 20000:
            it["aggregatedOutput"] = out[:8000] + "\n…\n" + out[-8000:]
    if it.get("type") == "fileChange":
        ch = []
        for c in it.get("changes") or []:
            if isinstance(c, dict) and isinstance(c.get("diff"), str) and len(c["diff"]) > 60000:
                c = {**c, "diff": c["diff"][:60000]}
            ch.append(c)
        it["changes"] = ch
    return it


def _keep_image(conv: _Conv, item: dict) -> dict:
    """A generated image, copied into the project where the feed can show it
    (<folder>/.studio-uploads/codex/<id>.png) - the Studio serves files only from open folders."""
    it = dict(item)
    root = Path(conv.cwd) if conv.cwd else None
    if root is None or not root.is_dir():
        return it
    dest_dir = root / ".studio-uploads" / "codex"
    name = _safe(str(it.get("id") or uuid.uuid4().hex)) or uuid.uuid4().hex
    try:
        src = str(it.get("savedPath") or "")
        data = b""
        if src and Path(src).is_file():
            data = Path(src).read_bytes()
        elif isinstance(it.get("result"), str) and it["result"]:
            raw = it["result"]
            if raw.startswith("data:"):
                raw = raw.split(",", 1)[-1]
            data = base64.b64decode(raw, validate=False)
        if data:
            ext = ".png"
            if data[:3] == b"\xff\xd8\xff":
                ext = ".jpg"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                ext = ".webp"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / (name + ext)
            dest.write_bytes(data)
            it["studioPath"] = str(dest)
            it["studioRel"] = dest.relative_to(root).as_posix()
    except Exception:
        pass
    return it


# ---------------------------------------------------------------------------
# Approvals and questions from Codex
# ---------------------------------------------------------------------------
def _decision(method: str, choice: str, params: dict) -> dict:
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        return {"decision": {ALLOW: "accept", ALLOW_SESSION: "acceptForSession", DENY: "decline"}[choice]}
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"decision": {ALLOW: "approved", ALLOW_SESSION: "approved_for_session",
                             DENY: {"denied": {"rejection": "The user denied this in Asset Studio."}}}[choice]}
    if method == "item/permissions/requestApproval":
        if choice == DENY:
            return {"permissions": {}, "scope": "turn"}
        return {"permissions": params.get("permissions") or {}, "scope": "session" if choice == ALLOW_SESSION else "turn"}
    return {}


def _approval_text(method: str, p: dict) -> str:
    if "commandExecution" in method or method == "execCommandApproval":
        cmd = p.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        why = p.get("reason") or ""
        return (f"Codex wants to run a command:\n\n```\n{_short_cmd(cmd)}\n```" + (f"\n\n{why}" if why else ""))
    if "fileChange" in method or method == "applyPatchApproval":
        why = p.get("reason") or ""
        root = p.get("grantRoot") or ""
        return ("Codex wants to change files" + (f" under {root}" if root else "") + "." + (f"\n\n{why}" if why else ""))
    if "permissions" in method:
        return "Codex asks for more access: " + str(p.get("reason") or json.dumps(p.get("permissions") or {})[:300])
    return "Codex asks: " + method


def _on_request(srv: AppServer, rid: Any, method: str, p: dict) -> None:
    tid = str(p.get("threadId") or p.get("conversationId") or "")
    if tid in _WAITERS:
        # a one-shot review never edits: say no to anything that would
        srv.respond(rid, _decision(method, DENY, p) if method in (
            "item/commandExecution/requestApproval", "item/fileChange/requestApproval", "execCommandApproval",
            "applyPatchApproval", "item/permissions/requestApproval") else {})
        return
    pid = _OWNER.get(tid)
    conv = _CONVS.get(pid) if pid else None
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                  "execCommandApproval", "applyPatchApproval", "item/permissions/requestApproval") and conv is not None:
        with conv.lock:
            conv.approvals.append({"rid": rid, "method": method, "params": p, "at": time.time(),
                                   "what": _approval_text(method, p), "thread": tid, "srv": srv})
            conv.activity = "Waiting for your answer"
        _log(conv.thread_id or tid, {"t": "ask", "what": _approval_text(method, p)})
        _publish(conv, force=True)
        return
    if method == "item/tool/requestUserInput":
        questions = p.get("questions") or []
        if conv is None or not questions:
            srv.respond(rid, {"answers": {}})
            return
        with conv.lock:
            conv.input_requests.append({"rid": rid, "srv": srv, "questions": questions,
                                        "answers": {}, "at": time.time(), "thread": tid})
            conv.activity = "Waiting for your answer"
        _publish(conv, force=True)
        return
    if method == "mcpServer/elicitation/request":
        srv.respond(rid, {"action": "decline", "content": None, "_meta": None})
        return
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                  "execCommandApproval", "applyPatchApproval", "item/permissions/requestApproval"):
        srv.respond(rid, _decision(method, DENY, p))
        return
    srv.respond(rid, error={"code": -32601, "message": f"Asset Studio does not handle {method}"})


# ---------------------------------------------------------------------------
# One-shot runs (the co-agent review after a Claude turn)
# ---------------------------------------------------------------------------
def _on_waiter_note(w: dict, method: str, p: dict) -> None:
    if method == "item/completed":
        item = p.get("item") or {}
        if item.get("type") == "agentMessage" and item.get("text"):
            w["texts"].append(str(item["text"]))
    elif method == "error" and not p.get("willRetry"):
        w["error"] = str((p.get("error") or {}).get("message") or "error")
    elif method == "turn/completed":
        turn = p.get("turn") or {}
        if turn.get("error"):
            w["error"] = str((turn.get("error") or {}).get("message") or w.get("error") or "error")
        w["ev"].set()


def run_once(cwd: str, prompt: str, model: str = "default", effort: str = "default",
             timeout: float = 900.0) -> str:
    """Ask Codex one question in a throwaway conversation and return its answer (read-only)."""
    if not find_codex():
        return "[Codex is not installed. Install it from the chat box: pick Codex, then Install.]"
    try:
        srv = server()
        acct = _read_account(srv) if srv else {}
    except Exception as e:  # noqa: BLE001
        return f"[Codex could not start: {e}]"
    if srv is None:
        return "[Codex is not installed.]"
    if not ready_to_chat(acct):
        return "[Codex is not signed in. Sign in from the chat box: pick Codex, then Sign in.]"
    model, _note = fit_model(model)
    tp = _thread_params(cwd, "read-only", model)
    tp["ephemeral"] = True
    try:
        r = srv.request("thread/start", tp, timeout=60) or {}
    except RpcError as e:
        return f"[Codex could not start a conversation: {e.message}]"
    tid = str(((r.get("thread") or {}).get("id")) or "")
    w = {"ev": threading.Event(), "texts": [], "error": ""}
    _WAITERS[tid] = w
    try:
        approval, pol = _policy("read-only")
        params = {"threadId": tid, "input": _inputs(prompt, None), "approvalPolicy": "never", "sandboxPolicy": pol}
        if model and model != "default":
            params["model"] = model
        fx = fit_effort(model, effort)
        if fx:
            params["effort"] = fx
        srv.request("turn/start", params, timeout=60)
        if not w["ev"].wait(timeout):
            try:
                srv.request("turn/interrupt", {"threadId": tid, "turnId": ""}, timeout=10)
            except Exception:
                pass
            return "[timed out]"
        text = "\n\n".join(w["texts"]).strip()
        if w["error"] and not text:
            return f"[Codex: {w['error']}]"
        return text or "[no answer]"
    except RpcError as e:
        return f"[Codex: {e.message}]"
    finally:
        _WAITERS.pop(tid, None)


# ---------------------------------------------------------------------------
# Live state (the answer being written, the working line)
# ---------------------------------------------------------------------------
def live_state(project_id: str) -> dict:
    c = _CONVS.get(bare(project_id))
    if c is None or not (c.working or c.approvals):
        return {"working": False, "tokens": 0, "activity": "", "elapsed": 0.0, "text": "", "kind": "",
                "compacting": False, "tps": 0.0, "gen_s": 0.0, "model": c.model if c else ""}
    with c.lock:
        tokens = max(0, c.out_now - c.out_base)
        gen = c.gen_s + ((time.time() - c.msg_started) if c.msg_started else 0.0)
        activity = "Waiting for your answer" if c.approvals else (c.retry[:64] if c.retry else c.activity)
        return {"working": True, "tokens": tokens, "activity": activity,
                "elapsed": round(time.time() - c.turn_started, 1) if c.turn_started else 0.0,
                "text": c.text[-6000:], "kind": c.kind if c.text else "",
                "compacting": activity.startswith("Compacting"),
                "tps": round(tokens / gen, 1) if gen > 0.4 and tokens else 0.0,
                "gen_s": round(gen, 1), "model": c.model}


def _publish(conv: _Conv, force: bool = False) -> None:
    now = time.time()
    if not force and now - conv.last_pub < 0.06:
        return
    conv.last_pub = now
    try:
        from .events import bus
        from .models import ProgressEvent, WSEventType
        if bus.subscriber_count() > 0:
            pid = CODEX_PREFIX + conv.project_id
            bus.publish_threadsafe(ProgressEvent(type=WSEventType.cc_live,
                                                 data={"project_id": pid, **live_state(pid)}))
    except Exception:
        pass


def live_rows() -> list[tuple[str, bool, int]]:
    """(folder id, busy, running subagents) for each Codex conversation that is doing something.

    Only while it works: the chat box shows its Stop button for every folder in the live list, and
    an idle Codex conversation has no process of its own to stop."""
    out = []
    for pid, c in list(_CONVS.items()):
        subs = sum(1 for s in c.subs.values() if s.get("running"))
        busy = bool(c.working or c.approvals or c.queue)
        if busy or subs:
            out.append((pid, busy, subs))
    return out


# ---------------------------------------------------------------------------
# The feed
# ---------------------------------------------------------------------------
def _hms(at: float) -> str:
    # UTC, the clock the Claude feed prints (its transcript stamps are ISO UTC), so the two engines'
    # rows read on one clock side by side
    try:
        return time.strftime("%H:%M:%S", time.gmtime(float(at)))
    except Exception:
        return ""


def _active_thread(pid: str, session: str = "") -> str:
    if session:
        return session
    d = _index(pid)
    if d.get("active"):
        return str(d["active"])
    c = _CONVS.get(pid)
    return c.thread_id if c else ""


def _parse_unified(diff: str, cap: int = 36) -> tuple[int, int, list[dict]]:
    """Unified diff text -> (added, removed, rows) in the feed's diff-row shape."""
    added = removed = 0
    rows: list[dict] = []
    n = m = 0
    for line in (diff or "").splitlines():
        if line.startswith("@@"):
            mm = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if mm:
                n, m = int(mm.group(1)), int(mm.group(2))
                if rows:
                    rows.append({"t": "gap"}) if len(rows) < cap else None
            continue
        if line.startswith(("---", "+++", "diff ", "index ")):
            continue
        if line.startswith("+"):
            added += 1
            if len(rows) < cap:
                rows.append({"t": "add", "s": line[1:][:260], "m": m})
            m += 1
        elif line.startswith("-"):
            removed += 1
            if len(rows) < cap:
                rows.append({"t": "del", "s": line[1:][:260], "n": n})
            n += 1
        elif line.startswith("\\"):
            continue
        else:
            if len(rows) < cap:
                rows.append({"t": "ctx", "s": line[1:][:260] if line.startswith(" ") else line[:260], "n": n, "m": m})
            n += 1
            m += 1
    return added, removed, rows


def _file_events(item: dict, ts: str, at: float, root: str) -> list[dict]:
    out = []
    for ch in item.get("changes") or []:
        if not isinstance(ch, dict):
            continue
        kind = str(((ch.get("kind") or {}).get("type")) or "update")
        path = str(ch.get("path") or "")
        rel = path
        try:
            if root and Path(path).is_absolute():
                rel = os.path.relpath(path, root)
                if rel.startswith(".."):
                    rel = path
        except (ValueError, OSError):
            rel = path
        diff = str(ch.get("diff") or "")
        ev = {"kind": "tool", "ts": ts, "at": at, "tool": "Edit", "icon": "edit", "subtitle": rel.replace("\\", "/"),
              "title": {"add": "Write", "delete": "Delete"}.get(kind, "Edit")}
        if "@@" in diff:
            a, r, rows = _parse_unified(diff)
        elif kind == "delete":
            lines = diff.splitlines()
            a, r, rows = 0, len(lines), [{"t": "del", "s": l[:260], "n": i + 1} for i, l in enumerate(lines[:24])]
        else:
            lines = diff.splitlines()
            a, r, rows = len(lines), 0, [{"t": "add", "s": l[:260], "m": i + 1} for i, l in enumerate(lines[:24])]
        ev["diff"] = {"added": a, "removed": r, "hunks": rows}
        if str(item.get("status") or "") in ("failed", "declined"):
            ev["ok"] = False
        out.append(ev)
    return out


def _mcp_result_text(item: dict) -> tuple[str, bool]:
    err = item.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"]), False
    res = item.get("result") or {}
    parts = []
    for c in (res.get("content") or []) if isinstance(res, dict) else []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
        elif isinstance(c, dict) and c.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts), True


def _rel(path: str, root: str) -> str:
    """A path as the project names it: relative to the folder when it is inside, else as it is."""
    p = str(path or "")
    try:
        if root and p and Path(p).is_absolute():
            r = os.path.relpath(p, root)
            if not r.startswith(".."):
                p = r
    except (ValueError, OSError):
        pass
    return p.replace("\\", "/")


def _cmd_events(item: dict, ts: str, at: float, root: str = "") -> list[dict]:
    acts = [a for a in item.get("commandActions") or [] if isinstance(a, dict)]
    cmd = _short_cmd(item.get("command"))
    kinds = {a.get("type") for a in acts}
    ev: dict = {"kind": "tool", "ts": ts, "at": at, "icon": "terminal", "command": cmd[:4000]}
    if kinds == {"read"}:
        # the path inside the project, so the link opens THAT file (a bare name can match several)
        ev.update(tool="Read", title="Read", icon="search",
                  subtitle=", ".join(_rel(str(a.get("path") or a.get("name") or ""), root) for a in acts)[:200])
    elif kinds == {"search"}:
        a = acts[0]
        ev.update(tool="Grep", title="Search", icon="search",
                  subtitle=(str(a.get("query") or "") + (f" in {a.get('path')}" if a.get("path") else ""))[:200])
    elif kinds == {"listFiles"}:
        ev.update(tool="Glob", title="List files", icon="search", subtitle=str(acts[0].get("path") or ".")[:200])
    else:
        # named like Claude's shell cards; Codex gives no description, so the feed shows the
        # command itself on the card (SessionFeed ToolEvent)
        shell = "PowerShell" if "powershell" in str(item.get("command") or "").lower() else "Bash"
        ev.update(tool=shell, title=shell, subtitle="")
    out = [ev]
    status = str(item.get("status") or "")
    code = item.get("exitCode")
    text = str(item.get("aggregatedOutput") or "")
    ok = status == "completed" and (code in (0, None))
    if status == "declined":
        text, ok = "Declined.", False
    if text.strip() or not ok:
        try:
            from .mission import _clean_out
            text = _clean_out(text)
        except Exception:
            text = text[:4000]
        if not ok and code not in (None, 0):
            text = (text + f"\n(exit code {code})").strip()
        out.append({"kind": "result", "ts": ts, "at": at, "ok": ok, "text": text or ("failed" if not ok else "")})
    return out


# ---------------------------------------------------------------------------
# What a delegated agent has done, and what it is doing now
# ---------------------------------------------------------------------------
# Codex logs every item of a subagent under that agent's OWN thread id. That is the same material
# Claude's `subagents.py` mines out of `agent-<id>.jsonl`: the prompt it was given, every command
# it ran, every file it changed, what it reported back, and the moment it last wrote.
#
# None of it reached the Studio. The endpoints behind the agent pill, the agent card and the agent
# pane went straight to the Claude module, so a Codex fan-out was invisible while the same fan-out
# on Claude was fully auditable — you could not even see that agents existed.
#
# A subagent's log is its whole working life and the pill polls every agent every few seconds, so
# nothing here reads one twice without reason: both reads are cached on (mtime, size), which is
# exact — a log that has not been appended to cannot have changed.
_SUB_TAIL = 256 * 1024          # bytes of a log read to answer "what is it doing right now"
_SUB_QUIET = 90.0               # wrote within this = still moving (subagents.RUNNING_WINDOW)
_SUB_OPEN = 1800.0              # dispatched, no outcome, still recent enough to be open
_sub_log_cache: dict = {}


def _sub_log(sub: str, whole: bool = False) -> list[dict]:
    """A subagent's own log: tail-bounded, or whole when the caller needs the head.

    `whole` exists for the prompt, which is the FIRST record and so invisible to a tail read.
    Counts and the current activity are all at the end and take the tail.

    The two variants are cached apart. They used to share one slot per agent, so the tail read and
    the whole read evicted each other and every poll paid for both.
    """
    path = _log_path(sub)
    try:
        st = path.stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        return []
    ck = (sub, whole)
    hit = _sub_log_cache.get(ck)
    if hit and hit[0] == key:
        return hit[1]
    recs = _read_log(sub) if whole else _read_log(sub, tail_bytes=_SUB_TAIL)
    _sub_log_cache[ck] = (key, recs)
    if len(_sub_log_cache) > 128:            # a fan-out is a handful of agents, not hundreds
        for k in list(_sub_log_cache)[:64]:
            _sub_log_cache.pop(k, None)
    return recs


def _ranked(counts: dict) -> list[dict]:
    """`{path: times}` as the card wants it: most-touched first, which is what you scan for."""
    return [{"path": p, "times": n} for p, n in sorted(counts.items(), key=lambda kv: -kv[1])]


def _count_item(typ: str, it: dict, stats: dict, edited: dict, read: dict) -> int:
    """File one finished item into the counters the agent card shows. Returns 1 if it was a command."""
    if typ == "commandExecution":
        acts = [a for a in it.get("commandActions") or [] if isinstance(a, dict)]
        kinds = {a.get("type") for a in acts}
        if kinds == {"read"}:
            stats["read"] += 1
            for a in acts:
                p = str(a.get("path") or a.get("name") or "")
                if p:
                    read[p] = read.get(p, 0) + 1
        elif kinds & {"search", "listFiles"}:
            stats["search"] += 1
        else:
            stats["bash"] += 1
        return 1
    if typ == "fileChange":
        for ch in it.get("changes") or []:
            if not isinstance(ch, dict):
                continue
            stats["edits"] += 1
            p = str(ch.get("path") or "")
            if p:
                edited[p] = edited.get(p, 0) + 1
        for ev in _file_events(it, "", 0.0, ""):
            d = ev.get("diff") or {}
            stats["added"] += int(d.get("added") or 0)
            stats["removed"] += int(d.get("removed") or 0)
        return 0
    stats["other"] += 1
    return 0


# Codex's own record of every thread it has run, on disk. Each rollout file is named with the
# thread id, so a subagent's rollout can be found from the id the Studio already has.
#
# The root is resolved per call rather than at import, and it honours CODEX_HOME: that is the
# variable the CLI itself reads, so anything that points Codex at a temporary home — a test, a
# script, a second account — must not have this quietly read the real one instead.
_TOK_TAIL = 1_000_000           # bytes read from the end of a rollout to find its running total
_rollout_map: dict = {}
_rollout_map_at = [0.0]
_rollout_hit_at = [0.0]
_tok_cache: dict = {}


def _sessions_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"


def _rebuild_rollouts() -> None:
    """thread id -> its rollout file. 49 files on this machine, and the walk costs ~2ms."""
    found: dict = {}
    try:
        for f in _sessions_root().rglob("rollout-*.jsonl"):
            parts = f.name[:-6].split("-")          # drop ".jsonl", thread id is the last 5 groups
            if len(parts) < 5:
                continue
            tid = "-".join(parts[-5:])
            prev = found.get(tid)
            try:
                if prev is None or f.stat().st_mtime > prev.stat().st_mtime:
                    found[tid] = f
            except OSError:
                found[tid] = f
    except OSError:
        pass
    _rollout_map.clear()
    _rollout_map.update(found)
    _rollout_map_at[0] = time.time()


def _rollout_for(thread_id: str) -> Optional[Path]:
    """Codex's rollout for a thread, re-walking once when a thread is not in the map.

    A subagent that started since the last walk would otherwise have no bill for two minutes, and
    a fresh agent is exactly when you are looking at the number.
    """
    now = time.time()
    if not _rollout_map or now - _rollout_map_at[0] > 120.0:
        _rebuild_rollouts()
    p = _rollout_map.get(thread_id)
    if p is None and now - _rollout_hit_at[0] > 2.0:
        _rollout_hit_at[0] = now
        _rebuild_rollouts()
        p = _rollout_map.get(thread_id)
    return p


def _rollout_tokens(thread_id: str) -> dict:
    """A thread's running token total, out of Codex's OWN rollout file.

    The app-server reports this live and keeps it nowhere the Studio can read, so for the agents
    that had already run, their bill was simply absent — the pill said "20 agents · 0 tokens",
    which reads as free. Codex's rollout has the same `total_token_usage` the app-server sent, so
    the number survives a restart and does not have to be re-derived from anything.

    Read from the END, because the total is cumulative: the last `token_count` in the file is the
    thread's whole life. A rollout can be large, so only the tail is read at all.
    """
    f = _rollout_for(thread_id)
    if f is None:
        return {}
    try:
        st = f.stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        return {}
    hit = _tok_cache.get(thread_id)
    if hit and hit[0] == key:
        return hit[1]
    try:
        with open(f, "rb") as fh:
            fh.seek(max(0, st.st_size - _TOK_TAIL))
            raw = fh.read()
    except OSError:
        return {}
    out: dict = {}
    for line in reversed(raw.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line or "total_token_usage" not in line:
            continue                        # most records are conversation, not accounting
        try:
            r = json.loads(line)
        except ValueError:
            continue                        # the first line is partial when the tail was cut
        info = ((r.get("payload") or {}).get("info") or {}) if r.get("type") == "event_msg" else {}
        tot = info.get("total_token_usage") or {}
        if not tot.get("total_tokens"):
            continue
        out = _sub_tokens(tot, int(info.get("model_context_window") or 0))
        break
    _tok_cache[thread_id] = (key, out)
    if len(_tok_cache) > 256:
        for k in list(_tok_cache)[:128]:
            _tok_cache.pop(k, None)
    return out


def _sub_tokens(tot: dict, window: int = 0) -> dict:
    """Codex's cumulative token counters as the two numbers the agent card shows.

    In Codex's totals `input_tokens` INCLUDES the cached ones (`total = input + output`), so the
    NEW input is the difference. That difference is the card's ``tokens`` — the same quantity
    Claude's module reports, so the pill's "N new tokens" means one thing on both engines.

    The cache reads stay in ``usage.cache_read`` and the raw total in ``wire``. Folding one context
    read again on every call into the headline is what once made a 747k agent read as 33.6M.
    """
    inp = int(tot.get("input_tokens") or 0)
    cached = int(tot.get("cached_input_tokens") or 0)
    cw = int(tot.get("cache_write_input_tokens") or 0)
    out = int(tot.get("output_tokens") or 0)
    fresh = max(0, inp - cached)
    d = {"tokens": fresh + cw + out,
         "wire": int(tot.get("total_tokens") or 0) or (inp + out),
         "usage": {"input": fresh, "output": out, "cache_read": cached, "cache_write": cw}}
    if window:
        d["ctx_max"] = int(window)
    return d


def _sub_from_log(sub: str) -> dict:
    """What a subagent's own transcript says about it, for when live memory has nothing.

    A Studio restart empties ``conv.subs`` and the agent index holds only what was saved when a
    turn ENDED, so an agent that was mid-flight comes back as a card with no activity, no counts
    and no files. Its log survives, and everything the card needs is in it.

    Every item on disk is one Codex already COMPLETED — the live path is what records a tool as
    still running — so a tool found at the end means it has come back and the agent is writing.

    Read whole rather than from the tail: the counts and the file list are what an auditor reads,
    and a partial tool tally is worse than a slower one. It shares `_sub_log`'s cache with
    `_sub_info`, which already reads this file whole, so in practice it costs nothing extra.
    """
    recs = _sub_log(sub, whole=True)
    if not recs:
        return {}
    info: dict = {}
    stats = {"read": 0, "search": 0, "bash": 0, "edits": 0, "added": 0, "removed": 0, "other": 0}
    edited: dict = {}
    read: dict = {}
    commands = 0
    last_tool: Optional[dict] = None
    for r in recs:
        t = r.get("t")
        if t == "item":
            it = r.get("item") or {}
            typ = it.get("type")
            if typ == "userMessage" and not info.get("prompt"):
                info["prompt"] = " ".join(str(c.get("text") or "")
                                          for c in it.get("content") or []
                                          if isinstance(c, dict))[:4000]
            elif typ == "agentMessage" and it.get("text"):
                info["result"] = str(it["text"])[:4000]
            if typ in _TOOL_ITEMS:
                commands += _count_item(typ, it, stats, edited, read)
                last_tool = it
            if typ not in ("userMessage", "agentMessage", "reasoning"):
                info["tools"] = int(info.get("tools") or 0) + 1
        elif t == "tokens":
            for k in ("tokens", "wire", "usage", "ctx_max"):
                if r.get(k) is not None:
                    info[k] = r[k]
        elif t == "turn_end":
            info["ended"] = r.get("at")
            info["status"] = str(r.get("status") or "")
    info.setdefault("tools", 0)
    info["stats"] = stats
    info["touched"] = {"edited": _ranked(edited), "read": _ranked(read), "commands": commands}
    if last_tool is not None:
        act = _sub_activity(last_tool, None)
        act["phase"] = "generating"        # it is on disk, so it finished
        info["activity"] = act
    if recs and not info.get("started"):
        info["started"] = recs[0].get("at")
    info["last"] = float(recs[-1].get("at") or 0)
    # An agent that ran BEFORE the Studio started keeping its usage has none of its own records,
    # and its bill would read as zero. Codex's rollout for that thread still has the total.
    if not info.get("tokens"):
        tk = _rollout_tokens(sub)
        if tk:
            info["tokens"] = tk["tokens"]
            info["usage"] = tk.get("usage") or {}
            if tk.get("wire"):
                info["wire"] = tk["wire"]
            if tk.get("ctx_max"):
                info["ctx_max"] = tk["ctx_max"]
    return info


def _decorate_sub(card: dict, sub: str, info: dict, now: float) -> None:
    """Add the half of a card Codex does not hand over on its own.

    ``is it moving or merely quiet``, ``what is it doing this instant``, and ``what has it read,
    run and changed``. Claude's module answers all three from the agent's transcript; Codex keeps
    the same material, so the pill, the card and the pane draw both engines from one structure and
    neither of them has to know which engine it is looking at.
    """
    from_log = _sub_from_log(sub)

    last = float(info.get("last") or 0) or float(from_log.get("last") or 0)
    if not last:
        try:
            last = _log_path(sub).stat().st_mtime
        except OSError:
            last = 0.0
    started = float(info.get("started") or from_log.get("started") or 0)
    ended = float(info.get("ended") or from_log.get("ended") or 0)

    # WRITING, or DISPATCHED AND QUIET? Ninety seconds of silence inside one long turn is normal,
    # and calling that "stuck" is worse than saying nothing — so the two are separate questions,
    # exactly as they are for Claude.
    if info.get("running"):
        idle = max(0.0, now - last) if last else 0.0
        moving = idle <= _SUB_QUIET
        card["running"] = moving
        card["open"] = not moving
        card["idle_s"] = round(idle, 1)
    else:
        card["running"] = False
        # No outcome recorded and the transcript stopped recently: dispatched, still unanswered.
        # This is the state a Studio restart leaves an in-flight agent in.
        card["open"] = bool(started and not ended and last and (now - last) <= _SUB_OPEN)
        if card["open"]:
            card["idle_s"] = round(max(0.0, now - last), 1)

    act = dict(info.get("activity") or {})
    if not (act.get("tool") or act.get("phase")):
        act = dict(from_log.get("activity") or {})
    if act.get("tool") or act.get("phase"):
        card["activity"] = act

    stats = from_log.get("stats") or {}
    if any(int(v or 0) for v in stats.values()):
        card["stats"] = stats
    touched = from_log.get("touched") or {}
    if touched.get("edited") or touched.get("read") or touched.get("commands"):
        card["touched"] = touched

    # The bill, and the window it ran in. Live memory has this once a turn has reported it; a
    # restart means the log or Codex's own rollout has to supply it, or the card shows free.
    if not card.get("tokens") and from_log.get("tokens"):
        card["tokens"] = int(from_log["tokens"])
    if from_log.get("usage"):
        card["usage"] = from_log["usage"]
    if from_log.get("ctx_max"):
        card["ctx_max"] = int(from_log["ctx_max"])
    if not card.get("wire") and from_log.get("wire"):
        card["wire"] = int(from_log["wire"])


def _agent_card(sub: str, info: dict, ts: str, at: float) -> dict:
    path = str(info.get("path") or "")
    name = path.rstrip("/").split("/")[-1] if path else ""
    started, ended = info.get("started"), info.get("ended")
    card = {"agent_id": sub, "description": name or "agent",
            "agent_type": str(info.get("nickname") or "codex"),
            "prompt": info.get("prompt") or "", "result": info.get("result") or "",
            "running": bool(info.get("running")), "status": info.get("status") or ("running" if info.get("running") else "completed"),
            "tools": int(info.get("tools") or 0)}
    if info.get("tokens"):
        card["tokens"] = int(info["tokens"])
    if info.get("usage"):
        card["usage"] = dict(info["usage"])
    if info.get("ctx_max"):
        card["ctx_max"] = int(info["ctx_max"])
    if info.get("wire"):
        card["wire"] = int(info["wire"])
    if started and ended:
        card["ms"] = int((float(ended) - float(started)) * 1000)
    if info.get("model"):
        card["model"] = info["model"]
    return {"kind": "agent", "ts": ts, "at": at, "agent": card}


def _sub_info(pid: str, sub: str) -> dict:
    """What is known about one subagent: live memory first, then the index, then its own log."""
    c = _CONVS.get(pid)
    live = dict(c.subs.get(sub) or {}) if c else {}
    saved = dict((_index(pid).get("subs") or {}).get(sub) or {})
    info = {**saved, **{k: v for k, v in live.items() if v not in (None, "")}}
    if not info.get("prompt") or not info.get("result") or "tools" not in info:
        recs = _sub_log(sub, whole=True)      # the prompt is the first record: a tail read misses it
        tools = 0
        for r in recs:
            it = r.get("item") or {}
            typ = it.get("type")
            if typ == "userMessage" and not info.get("prompt"):
                info["prompt"] = " ".join(str(x.get("text") or "") for x in it.get("content") or [] if isinstance(x, dict))
            elif typ == "agentMessage" and it.get("text"):
                info["result"] = str(it["text"])
            elif typ and typ not in ("userMessage", "agentMessage", "reasoning"):
                tools += 1
            if r.get("t") == "turn_end":
                info.setdefault("ended", r.get("at"))
                info["running"] = False if not live.get("running") else live.get("running")
        info.setdefault("tools", tools)
        if recs and "started" not in info:
            info["started"] = recs[0].get("at")
    return info


def _turn_end_last(recs: list[dict]) -> list[dict]:
    """Each turn's end moved after the last record of that turn.

    Codex can report an item after its turn is over: a command that waited for an approval
    arrived after turn/completed, so its card was drawn under the turn's summary bar."""
    last: dict = {}
    for i, r in enumerate(recs):
        if r.get("t") != "turn_end" and r.get("turn"):
            last[r["turn"]] = i
    out: list[dict] = []
    held: dict = {}
    for i, r in enumerate(recs):
        t = r.get("turn")
        if r.get("t") == "turn_end" and t and last.get(t, -1) > i:
            held[t] = r
            continue
        out.append(r)
        if t in held and last.get(t) == i:
            out.append(held.pop(t))
    out.extend(held.values())
    return out


def events_for(thread_id: str, pid: str, root: str = "", tail_bytes: int = 0) -> list[dict]:
    """A Codex thread's log, drawn as feed events in the shape mission.project_feed returns."""
    recs = _turn_end_last(_read_log(thread_id, tail_bytes=tail_bytes))
    ev: list[dict] = []
    cards: dict[str, dict] = {}
    turn_rows: list[dict] = []
    tools = files = added = removed = 0
    fileset: set = set()
    thought_in_turn = False
    for r in recs:
        t = r.get("t")
        at = float(r.get("at") or 0)
        ts = _hms(at)
        if t == "user":
            text = str(r.get("text") or "")
            ims = r.get("images") or []
            if ims:
                rels = []
                for p in ims:
                    try:
                        rel = os.path.relpath(p, root) if root else p
                        rels.append((p if rel.startswith("..") else rel).replace("\\", "/"))
                    except (ValueError, OSError):
                        rels.append(p)
                text = (text + "\n\n" + "\n".join(rels)).strip()
            # no "id": that is what offers "edit & retry from here", a rewind Codex does not have yet
            ev.append({"kind": "user", "ts": ts, "at": at, "text": text,
                       **({"steer": True} if r.get("steer") else {})})
            tools = files = added = removed = 0
            fileset = set()
            thought_in_turn = False
        elif t == "turn_start":
            thought_in_turn = False
        elif t == "item":
            it = r.get("item") or {}
            typ = it.get("type")
            if typ == "agentMessage":
                try:
                    from .mission import _clean_md
                    txt = _clean_md(str(it.get("text") or ""))
                except Exception:
                    txt = str(it.get("text") or "")
                if txt.strip():
                    ev.append({"kind": "text", "ts": ts, "at": at, "text": txt})
            elif typ == "reasoning":
                txt = "\n\n".join(str(x) for x in (it.get("summary") or []) if x) or \
                      "\n\n".join(str(x) for x in (it.get("content") or []) if x)
                if txt.strip() or not thought_in_turn:
                    ev.append({"kind": "thinking", "ts": ts, "at": at, "text": txt.strip()})
                    thought_in_turn = True
            elif typ == "commandExecution":
                ev += _cmd_events(it, ts, at, root)
                tools += 1
            elif typ == "fileChange":
                fe = _file_events(it, ts, at, root)
                ev += fe
                tools += 1
                for f in fe:
                    fileset.add(f.get("subtitle"))
                    added += int((f.get("diff") or {}).get("added") or 0)
                    removed += int((f.get("diff") or {}).get("removed") or 0)
            elif typ == "mcpToolCall":
                args = it.get("arguments")
                brief = ""
                if isinstance(args, dict):
                    for k in ("query", "q", "path", "url", "file", "name", "prompt"):
                        if isinstance(args.get(k), str) and args[k].strip():
                            brief = args[k].strip()
                            break
                    if not brief:
                        brief = json.dumps(args, ensure_ascii=False)[:200]
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": f"mcp__{it.get('server')}__{it.get('tool')}",
                           "title": f"{it.get('server')} · {it.get('tool')}", "icon": "tool", "subtitle": brief[:220]})
                txt, ok = _mcp_result_text(it)
                if txt.strip():
                    try:
                        from .mission import _clean_out
                        txt = _clean_out(txt)
                    except Exception:
                        txt = txt[:4000]
                    ev.append({"kind": "result", "ts": ts, "at": at, "ok": ok, "text": txt})
                tools += 1
            elif typ == "dynamicToolCall":
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": str(it.get("tool") or "tool"),
                           "title": str(it.get("tool") or "tool"), "icon": "tool",
                           "subtitle": json.dumps(it.get("arguments"), ensure_ascii=False)[:200]})
                tools += 1
            elif typ == "webSearch":
                # a search names its query; opening or searching a page names the page (0.145 spells
                # the action types open_page / find_in_page, 0.159 openPage / findInPage)
                act = it.get("action") if isinstance(it.get("action"), dict) else {}
                kind = str(act.get("type") or "search").replace("_", "").lower()
                if kind == "openpage":
                    title, sub = "WebFetch", str(act.get("url") or "")
                elif kind == "findinpage":
                    title, sub = "WebFetch", f"{act.get('pattern') or ''} in {act.get('url') or ''}".strip()
                else:
                    qs = act.get("queries") if isinstance(act.get("queries"), list) else []
                    title, sub = "WebSearch", str(it.get("query") or act.get("query") or " · ".join(map(str, qs)))
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": title, "title": title,
                           "icon": "globe", "subtitle": sub[:220]})
                tools += 1
            elif typ == "sleep":
                secs = round(float(it.get("durationMs") or 0) / 1000.0, 1)
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": "Wait", "title": "Wait",
                           "icon": "tool", "subtitle": f"{secs:g} s" if secs else ""})
            elif typ == "imageView":
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": "Read", "title": "View image",
                           "icon": "search", "subtitle": _rel(str(it.get("path") or ""), root)[:220]})
                tools += 1
            elif typ == "imageGeneration":
                fail = it.get("failure")
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": "ImageGen", "title": "Generate image",
                           "icon": "tool", "subtitle": str(it.get("revisedPrompt") or "")[:220],
                           **({"ok": False} if fail else {})})
                rel = it.get("studioRel") or it.get("savedPath") or ""
                if rel:
                    ev.append({"kind": "text", "ts": ts, "at": at, "text": str(rel)})
                elif fail:
                    ev.append({"kind": "result", "ts": ts, "at": at, "ok": False,
                               "text": str((fail or {}).get("message") if isinstance(fail, dict) else fail)})
                tools += 1
            elif typ == "subAgentActivity":
                sub = str(it.get("agentThreadId") or "")
                if not sub:
                    continue
                if sub not in cards:
                    info = _sub_info(pid, sub)
                    if it.get("agentPath"):
                        info.setdefault("path", str(it["agentPath"]))
                    card = _agent_card(sub, info, ts, at)
                    cards[sub] = card
                    ev.append(card)
            elif typ == "collabAgentToolCall":
                tool = str(it.get("tool") or "")
                if tool == "spawnAgent":
                    for sub in it.get("receiverThreadIds") or []:
                        sub = str(sub)
                        if sub in cards:
                            continue
                        info = _sub_info(pid, sub)
                        info.setdefault("prompt", str(it.get("prompt") or ""))
                        if it.get("model"):
                            info.setdefault("model", str(it["model"]))
                        card = _agent_card(sub, info, ts, at)
                        cards[sub] = card
                        ev.append(card)
                elif tool in ("wait", "waitAgent"):
                    continue
                else:
                    label = {"sendInput": "Message an agent", "sendMessage": "Message an agent",
                             "followupTask": "Follow-up task", "closeAgent": "Close an agent",
                             "interruptAgent": "Interrupt an agent", "resumeAgent": "Resume an agent",
                             "listAgents": "List agents"}.get(tool, tool)
                    ev.append({"kind": "tool", "ts": ts, "at": at, "tool": "Agent", "title": label, "icon": "bot",
                               "subtitle": str(it.get("prompt") or "")[:200]})
            elif typ == "plan":
                if str(it.get("text") or "").strip():
                    ev.append({"kind": "text", "ts": ts, "at": at, "text": str(it["text"])})
            elif typ == "contextCompaction":
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": "Compact", "title": "Compacted the conversation",
                           "icon": "tool", "subtitle": ""})
            elif typ in ("enteredReviewMode", "exitedReviewMode"):
                if it.get("review"):
                    ev.append({"kind": "text", "ts": ts, "at": at, "text": str(it["review"])})
        elif t == "plan":
            st = {"inProgress": "in_progress", "in_progress": "in_progress", "completed": "completed"}
            todos = [{"content": str(s.get("step") or ""), "status": st.get(str(s.get("status")), "pending")}
                     for s in r.get("plan") or [] if isinstance(s, dict)]
            if todos:
                ev.append({"kind": "tool", "ts": ts, "at": at, "tool": "TodoWrite", "title": "Phases", "icon": "check",
                           "todos": todos[:14], **({"subtitle": str(r['explanation'])[:200]} if r.get("explanation") else {})})
        elif t == "ask":
            pass                                   # the live card is drawn from memory while it waits
        elif t == "approval":
            ev.append({"kind": "user", "ts": ts, "at": at, "text": str(r.get("answer") or ""), "btw": True})
        elif t == "error":
            msg = str(r.get("message") or "error")
            if _auth_error(msg + " " + str(r.get("details") or "")):
                msg += "\n\nCodex is not signed in, or the key was refused. Sign in again from the chat box."
            ev.append({"kind": "result", "ts": ts, "at": at, "ok": False, "text": msg})
        elif t == "note":
            ev.append({"kind": "result", "ts": ts, "at": at, "ok": True, "text": str(r.get("text") or "")})
        elif t == "turn_end":
            status = str(r.get("status") or "")
            if status == "failed" and r.get("error"):
                msg = str(r["error"])
                if _auth_error(msg + " " + str(r.get("details") or "")):
                    msg += "\n\nCodex is not signed in, or the key was refused. Sign in again from the chat box."
                ev.append({"kind": "result", "ts": ts, "at": at, "ok": False, "text": msg})
            elif status == "interrupted":
                ev.append({"kind": "result", "ts": ts, "at": at, "ok": False, "text": "Stopped."})
            row = {"kind": "turn", "ts": ts, "at": at, "tools": tools, "files": len(fileset),
                   "added": added, "removed": removed, "wall_from": "cli"}
            if r.get("duration_ms"):
                row["wall_s"] = round(float(r["duration_ms"]) / 1000.0, 1)
            if r.get("out_tokens"):
                row["tokens"] = int(r["out_tokens"])
            if r.get("gen_s"):
                row["gen_s"] = float(r["gen_s"])
            if r.get("model"):
                row["model"] = str(r["model"])
            if r.get("effort"):
                row["effort"] = str(r["effort"])
            if tools or r.get("out_tokens"):
                ev.append(row)
            tools = files = added = removed = 0
            fileset = set()
    # a card's state is refreshed from memory: the log only knows what had happened by then
    c = _CONVS.get(pid)
    if c is not None:
        for sub, card in cards.items():
            live = c.subs.get(sub)
            if live:
                card["agent"]["running"] = bool(live.get("running"))
                if live.get("result"):
                    card["agent"]["result"] = str(live["result"])[:4000]
                if live.get("tools"):
                    card["agent"]["tools"] = int(live["tools"])
    return ev


def _approval_events(pid: str) -> list[dict]:
    c = _CONVS.get(pid)
    if c is None:
        return []
    out = []
    for ap in list(c.approvals):
        out.append({"kind": "question", "ts": _hms(ap.get("at") or time.time()), "tool": "CodexApproval",
                    "text": ap.get("what") or "Codex asks for permission.",
                    "options": [{"label": ALLOW, "description": "this time"},
                                {"label": ALLOW_SESSION, "description": "the same kind of request, until Codex restarts"},
                                {"label": DENY, "description": "Codex is told no and carries on"}]})
    with c.lock:
        if c.input_requests:
            req = c.input_requests[0]
            question = next((q for q in req["questions"] if q["id"] not in req["answers"]), None)
            if question:
                out.append({"kind": "question", "ts": _hms(req["at"]), "tool": "CodexPlanner",
                            "text": question["question"], "options": question.get("options") or []})
    return out


def feed(project_id: str, limit: int = 150, session: str = "", kinds: str = "") -> dict:
    pid = bare(project_id)
    tid = _active_thread(pid, session)
    c = _CONVS.get(pid)
    cwd = (c.cwd if c else "") or str((_index(pid).get("threads") or {}).get(tid, {}).get("cwd") or "")
    kb = min(24576, 200 + limit * 8)
    events = events_for(tid, pid, cwd, tail_bytes=kb * 1024) if tid else []
    events += _approval_events(pid)
    try:
        from .mission import _filter_kinds
        events = _filter_kinds(events, kinds)
    except Exception:
        pass
    working = bool(c and (c.working or c.approvals))
    subs = sum(1 for s in (c.subs.values() if c else []) if s.get("running"))
    return {"btw_pending": "", "lines": events[-limit:], "working": working,
            "tokens": max(0, (c.out_now - c.out_base)) if c and c.working else 0,
            "agents_active": subs, "agent": "", "agent_log": "", "companions": {}, "session": tid,
            "awaiting_input": bool(c and c.approvals)}


def sessions(project_id: str) -> list[dict]:
    pid = bare(project_id)
    d = _index(pid)
    active = d.get("active") or ""
    out = []
    for tid, meta in (d.get("threads") or {}).items():
        path = _log_path(tid)
        try:
            st = path.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, float(meta.get("updated") or meta.get("created") or 0)
        out.append({"id": tid, "ts": mtime, "size": size, "title": str(meta.get("title") or "(empty conversation)"),
                    "active": tid == active})
    out.sort(key=lambda o: o["ts"], reverse=True)
    if out and not any(o["active"] for o in out) and active == "":
        pass
    return out


def remember_session(project_id: str, thread_id: str) -> None:
    pid = bare(project_id)
    if thread_id:
        _index_update(pid, lambda d: d.update(active=thread_id))


def context(project_id: str) -> dict:
    pid = bare(project_id)
    tid = _active_thread(pid)
    d = _index(pid)
    meta = (d.get("threads") or {}).get(tid) or {}
    c = _CONVS.get(pid)
    use = ((c.usage.get(tid) if c else None) or meta.get("usage") or {})
    last = use.get("last") or {}
    window = int(use.get("window") or 0)
    used = int(last.get("inputTokens") or 0) + int(last.get("outputTokens") or 0)
    model = (c.model if c and c.model else "") or str(meta.get("model") or "")
    if not window or not used:
        return {"model": model, "working": bool(c and c.working)} if model else {}
    frac = used / window
    compact_at = 0.9
    cached = int(last.get("cachedInputTokens") or 0)
    inp = int(last.get("inputTokens") or 0)
    out = {"model": model, "ctx_used": used, "ctx_max": window, "ctx_pct": round(100 * frac, 1),
           "ctx_remaining": round(max(0.0, 100 * (compact_at - frac) / compact_at), 1),
           "working": bool(c and c.working), "awaiting_input": bool(c and c.approvals),
           "agents_active": sum(1 for s in (c.subs.values() if c else []) if s.get("running")),
           "compacting": bool(c and c.working and c.activity.startswith("Compacting"))}
    if inp:
        out.update(cache_read=cached, cache_write=int(last.get("cacheWriteInputTokens") or 0),
                   fresh_in=max(0, inp - cached), cache_pct=round(100 * cached / inp, 1))
    return out


def todos(project_id: str) -> dict:
    pid = bare(project_id)
    tid = _active_thread(pid)
    plan: list = []
    ts = ""
    for r in reversed(_read_log(tid, tail_bytes=512 * 1024)) if tid else []:
        if r.get("t") == "plan":
            plan = r.get("plan") or []
            ts = _hms(r.get("at") or 0)
            break
    st = {"inProgress": "in_progress", "completed": "completed"}
    items = [{"content": str(s.get("step") or ""), "status": st.get(str(s.get("status")), "pending")}
             for s in plan if isinstance(s, dict)]
    return {"todos": items, "done": sum(1 for x in items if x["status"] == "completed"), "total": len(items),
            "source": "codex" if items else "", "ts": ts, "earlier": 0}


def subagents(project_id: str) -> dict:
    """Every agent this Codex conversation has delegated to, in the shape the Claude module
    returns.

    The Studio's agent pill, agent card, agent pane and "who is running" list were all wired to
    `subagents.list_for`, which reads Claude's transcript layout and nothing else — so a Codex
    fan-out was not merely thin, it was absent. This returns the same structure for a Codex
    conversation, which is why no consumer has to learn a second shape.
    """
    pid = bare(project_id)
    c = _CONVS.get(pid)
    subs = dict((_index(pid).get("subs") or {}))
    if c is not None:
        for k, v in c.subs.items():
            subs[k] = {**subs.get(k, {}), **v}
    now = time.time()
    rows: list[dict] = []
    for sub in subs:
        info = _sub_info(pid, sub)
        at = float(info.get("started") or 0)
        card = _agent_card(sub, info, "", at)["agent"]
        _decorate_sub(card, sub, info, now)
        card["ts"] = at
        rows.append(card)
    rows.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return {"agents": rows, "running": sum(1 for r in rows if r.get("running")),
            "open": sum(1 for r in rows if r.get("open")),
            "tokens": sum(int(r.get("tokens") or 0) for r in rows),
            "cache_read": 0}


def subagent_detail(project_id: str, agent_id: str, limit: int = 400) -> dict:
    pid = bare(project_id)
    c = _CONVS.get(pid)
    cwd = c.cwd if c else ""
    lines = events_for(agent_id, pid, cwd)[-limit:]
    info = _sub_info(pid, agent_id)
    card = _agent_card(agent_id, info, "", float(info.get("started") or 0))["agent"]
    _decorate_sub(card, agent_id, info, time.time())
    return {"ok": True, "agent": card, "lines": lines,
            "touched": card.get("touched") or {}, "working": bool(card.get("running"))}


def shutdown() -> None:
    stop_server()

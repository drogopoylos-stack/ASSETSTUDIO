"""Local Services Manager — launch & supervise the localhost model servers that
the free/local providers depend on (ComfyUI, TRELLIS, Hunyuan3D, …).

This is what makes "pick a local provider and it just works" real: the studio can
START the required server, wait until its port is reachable, then run the job — and
stop it again. Each service has a user-configurable launch command (installs vary),
a health URL, and the provider ids it powers. Everything degrades gracefully:
if no command is configured, the UI tells you to set one.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .config import DATA_DIR, settings

SERVICES_DIR = DATA_DIR / "services"
SERVICES_DIR.mkdir(parents=True, exist_ok=True)


class ServiceSpec(BaseModel):
    id: str
    name: str
    health_url: str
    port: Optional[int] = None
    command: str = ""        # shell command to launch the server
    cwd: str = ""            # working directory for the command
    autostart: bool = True   # auto-start when a job needs it
    powers: list[str] = []   # provider ids this service enables
    docs: str = ""


class ServiceStatus(BaseModel):
    id: str
    name: str
    state: str               # stopped | starting | running | unreachable
    reachable: bool
    managed: bool            # started by us and still alive
    pid: Optional[int] = None
    port: Optional[int] = None
    health_url: str
    command: str
    cwd: str = ""
    autostart: bool = True
    configured: bool
    powers: list[str] = []
    last_error: str = ""
    docs: str = ""


def _tool(name: str, default: str) -> str:
    return settings.get("tools", {}).get(name, default) or default


def _default_specs() -> list[ServiceSpec]:
    comfy = _tool("comfyui_url", "http://127.0.0.1:8188").rstrip("/")
    trellis = _tool("trellis_url", "http://127.0.0.1:7860").rstrip("/")
    hunyuan = _tool("hunyuan_url", "http://127.0.0.1:8080").rstrip("/")
    comfy_cmd, comfy_cwd = _detect_comfyui(_port_of(comfy))
    return [
        ServiceSpec(
            id="comfyui", name="ComfyUI (SDXL/Flux/Qwen, Hunyuan3D, TRELLIS2 texture)",
            health_url=f"{comfy}/system_stats", port=_port_of(comfy),
            command=comfy_cmd, cwd=comfy_cwd,
            powers=["comfyui", "comfyui-3d", "trellis-texture", "minimax-h3"],
            docs=("Auto-detected your ComfyUI install — just press Start. If it's elsewhere, set the "
                  "launch command (e.g.  python main.py --port 8188) + its folder below."),
        ),
        ServiceSpec(
            id="trellis", name="TRELLIS / TRELLIS2 server",
            health_url=trellis, port=_port_of(trellis),
            powers=["trellis", "trellis-texture"],
            docs="Launch your TRELLIS Gradio/HTTP server; set its command + cwd here.",
        ),
        ServiceSpec(
            id="hunyuan3d", name="Hunyuan3D-2 server",
            health_url=f"{hunyuan}/health", port=_port_of(hunyuan),
            powers=["hunyuan3d", "hunyuan-paint"],
            docs="Launch Hunyuan3D-2 api_server.py; set its command + cwd here.",
        ),
    ]


def _port_of(url: str) -> Optional[int]:
    try:
        return int(url.rsplit(":", 1)[1].split("/")[0])
    except Exception:
        return None


def _find_python(comfy_dir: Path) -> str:
    """Pick the interpreter that runs this ComfyUI (its own venv first)."""
    cands = [
        comfy_dir / "venv" / "Scripts" / "python.exe",
        comfy_dir / ".venv" / "Scripts" / "python.exe",
        comfy_dir.parent / "python_embeded" / "python.exe",   # ComfyUI_windows_portable
        comfy_dir / "venv" / "bin" / "python",
        comfy_dir / ".venv" / "bin" / "python",
    ]
    for c in cands:
        try:
            if c.exists():
                return str(c)
        except OSError:
            pass
    return "python"


def _detect_comfyui(port: Optional[int]) -> tuple[str, str]:
    """Best-effort: locate a local ComfyUI install and build a launch command so
    the Start button (and auto-start-on-Generate) work out of the box. A command
    the user saved in Settings always overrides this (see ServiceManager.specs)."""
    home = Path.home()
    cands: list[Path] = []
    custom = (settings.get("tools", {}) or {}).get("comfyui_dir", "")
    if custom:
        cands.append(Path(custom))
    cands += [
        home / "ComfyUI",
        home / "ComfyUI_windows_portable" / "ComfyUI",
        home / "Documents" / "ComfyUI",
        Path("C:/ComfyUI"),
    ]
    for d in cands:
        try:
            if not (d / "main.py").exists():
                continue
        except OSError:
            continue
        py = _find_python(d)
        p = port or 8188
        # --use-split-cross-attention keeps an 8GB card alive; --preview-method auto matches the user's run script.
        cmd = f'"{py}" -s main.py --use-split-cross-attention --preview-method auto --port {p}'
        return cmd, str(d)
    return "", ""


class ServiceManager:
    def __init__(self):
        self._procs: dict[str, subprocess.Popen] = {}

    # --- specs (defaults merged with user settings) -----------------------
    def specs(self) -> list[ServiceSpec]:
        overrides = settings.get("services", {}) or {}
        out = []
        for spec in _default_specs():
            ov = overrides.get(spec.id, {})
            data = spec.model_dump()
            for k, v in ov.items():
                if v is None:
                    continue
                # A blank command/cwd override (e.g. a stale Save) must NOT erase an
                # auto-detected launch command — otherwise Start goes dead again.
                if k in ("command", "cwd") and isinstance(v, str) and not v.strip() and data.get(k):
                    continue
                data[k] = v
            out.append(ServiceSpec(**data))
        # allow fully custom user services
        for sid, ov in overrides.items():
            if sid not in {s.id for s in out} and ov.get("health_url"):
                out.append(ServiceSpec(id=sid, name=ov.get("name", sid), **{
                    k: ov[k] for k in ("health_url", "port", "command", "cwd", "autostart", "powers", "docs")
                    if k in ov
                }))
        return out

    def spec(self, sid: str) -> Optional[ServiceSpec]:
        return next((s for s in self.specs() if s.id == sid), None)

    def service_for_provider(self, provider_id: str) -> Optional[ServiceSpec]:
        for s in self.specs():
            if provider_id in s.powers:
                return s
        return None

    # --- reachability ------------------------------------------------------
    def is_reachable(self, health_url: str, timeout: float = 1.5) -> bool:
        import httpx

        try:
            httpx.get(health_url, timeout=timeout)
            return True  # any HTTP response means the port is up
        except httpx.HTTPError:
            return False
        except Exception:
            return False

    # --- status ------------------------------------------------------------
    def status(self, sid: str) -> Optional[ServiceStatus]:
        spec = self.spec(sid)
        if not spec:
            return None
        reachable = self.is_reachable(spec.health_url)
        proc = self._procs.get(sid)
        alive = proc is not None and proc.poll() is None
        state = "running" if reachable else ("starting" if alive else "stopped")
        last_error = ""
        if not reachable and proc is not None and proc.poll() not in (None, 0):
            state = "unreachable"
            last_error = self.log_tail(sid, lines=8)
        return ServiceStatus(
            id=spec.id, name=spec.name, state=state, reachable=reachable,
            managed=alive, pid=proc.pid if alive and proc else None,
            port=spec.port, health_url=spec.health_url, command=spec.command,
            cwd=spec.cwd, autostart=spec.autostart, configured=bool(spec.command.strip()),
            powers=spec.powers, docs=spec.docs, last_error=last_error,
        )

    def all_status(self) -> list[ServiceStatus]:
        return [s for s in (self.status(spec.id) for spec in self.specs()) if s]

    # --- start / stop ------------------------------------------------------
    def _log_path(self, sid: str) -> Path:
        return SERVICES_DIR / f"{sid}.log"

    def start(self, sid: str) -> ServiceStatus:
        spec = self.spec(sid)
        if not spec:
            raise RuntimeError(f"unknown service '{sid}'")
        if self.is_reachable(spec.health_url):
            return self.status(sid)  # already up (maybe started outside the studio)
        if not spec.command.strip():
            st = self.status(sid)
            st.last_error = "No launch command configured. Set it in Settings → Servers."
            return st
        # don't double-spawn
        existing = self._procs.get(sid)
        if existing and existing.poll() is None:
            return self.status(sid)

        logf = open(self._log_path(sid), "ab", buffering=0)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        try:
            proc = subprocess.Popen(
                spec.command, cwd=spec.cwd or None, shell=True,
                stdout=logf, stderr=subprocess.STDOUT, creationflags=flags,
            )
        except Exception as e:
            st = self.status(sid)
            st.last_error = f"failed to launch: {e}"
            return st
        self._procs[sid] = proc
        return self.status(sid)

    def ensure(self, sid: str, timeout: float = 120.0) -> tuple[bool, str]:
        """Start the service (if needed) and block until reachable or timeout."""
        spec = self.spec(sid)
        if not spec:
            return False, f"unknown service '{sid}'"
        if self.is_reachable(spec.health_url):
            return True, ""
        if not spec.command.strip():
            return False, f"{spec.name} is not running and no launch command is configured (Settings → Servers)."
        self.start(sid)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.is_reachable(spec.health_url):
                return True, ""
            proc = self._procs.get(sid)
            if proc and proc.poll() not in (None, 0):
                return False, f"{spec.name} exited during startup. Log:\n{self.log_tail(sid, 12)}"
            time.sleep(1.0)
        return False, f"{spec.name} did not become reachable within {int(timeout)}s."

    @staticmethod
    def _kill_tree(pid: Optional[int]) -> bool:
        """Kill a process and all its children. Refuses to kill the studio itself."""
        if not pid or pid == os.getpid():
            return False
        try:
            import psutil

            p = psutil.Process(pid)
            for child in p.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            p.kill()
            return True
        except Exception:
            return False

    @staticmethod
    def _pids_on_port(port: Optional[int]) -> set[int]:
        """PIDs LISTENing on `port` — lets us stop a server the studio didn't spawn."""
        pids: set[int] = set()
        if not port:
            return pids
        me = os.getpid()
        try:
            import psutil

            for c in psutil.net_connections(kind="inet"):
                try:
                    if (c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN
                            and c.pid and c.pid != me):
                        pids.add(c.pid)
                except Exception:
                    pass
        except Exception:
            pass
        return pids

    def stop(self, sid: str) -> ServiceStatus:
        spec = self.spec(sid)
        proc = self._procs.get(sid)
        if proc and proc.poll() is None:
            if not self._kill_tree(proc.pid):
                try:
                    proc.terminate()
                except Exception:
                    pass
        self._procs.pop(sid, None)
        # Also stop a server the studio didn't spawn (a .bat, a terminal, a prior run) so the
        # Stop button works no matter who launched it.
        if spec and spec.port and self.is_reachable(spec.health_url):
            for pid in self._pids_on_port(spec.port):
                self._kill_tree(pid)
        return self.status(sid)

    def stop_all_managed(self) -> None:
        for sid in list(self._procs.keys()):
            self.stop(sid)

    def log_tail(self, sid: str, lines: int = 60) -> str:
        p = self._log_path(sid)
        if not p.exists():
            return ""
        try:
            data = p.read_bytes()[-16384:].decode("utf-8", "ignore")
        except OSError:
            return ""
        return "\n".join(data.splitlines()[-lines:])

    # --- config ------------------------------------------------------------
    def update(self, sid: str, patch: dict) -> ServiceStatus:
        services = dict(settings.get("services", {}) or {})
        cur = dict(services.get(sid, {}))
        cur.update({k: v for k, v in patch.items() if v is not None})
        services[sid] = cur
        settings.update({"services": services})
        return self.status(sid)


manager = ServiceManager()

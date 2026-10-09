"""Asset Studio FastAPI application — REST + WebSocket control plane.

Run:  python -m asset_studio.main      (or)   uvicorn asset_studio.main:app
Serves the built frontend from ``frontend/dist`` when present, so the whole app
is one local origin.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import FRONTEND_DIST, settings
from .events import bus
from .jobs.queue import queue
from .models import ProgressEvent, WSEventType
from .routers import agent, catalog, jobs, mission, providers, system
from .routers import auth as auth_router
from .routers import autolearn as autolearn_router
from .routers import chat as chat_router
from .routers import chat_models as models_router
from .routers import plans as plans_router
from .routers import review as review_router
from .routers import services as services_router
from .routers import settings as settings_router
from .routers import setup as setup_router
from .routers import skills as skills_router
from .routers import uploads as uploads_router
from .routers import voice as voice_router
from .routers import workspace as workspace_router
from .routers import workflows as workflows_router
from .routers import git as git_router
from .routers import live as live_router
from .routers import live_stream as live_stream_router
from .routers import engine as engine_router
from .routers import terminal as terminal_router
from .routers import tools as tools_router
from .routers import web as web_router
from .routers import worktrees as worktrees_router
from .routers import mcp as mcp_router
from .routers import codex as codex_router
from .routers import ws
from .system_stats import collect

app = FastAPI(title="Asset Studio", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local-first single-user; Electron + Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (system.router, providers.router, jobs.router, catalog.router,
          settings_router.router, agent.router, mission.router,
          services_router.router, setup_router.router, skills_router.router,
          uploads_router.router, workspace_router.router, chat_router.router,
          workflows_router.router, auth_router.router, voice_router.router,
          autolearn_router.router, models_router.router, models_router.bridge,
          plans_router.router, terminal_router.router,
          review_router.router, worktrees_router.router, git_router.router,
          web_router.router, live_router.router, live_stream_router.router, engine_router.router,
          tools_router.router, mcp_router.router, codex_router.router,
          ws.router):
    app.include_router(r)


# Did this process ever actually serve? A backend that loses the race for the port never does.
#
# THIS IS A GUARD ON THE SHUTDOWN PATH, not a statistic. uvicorn runs the start-up handlers, then
# binds, and on a bind failure runs the SHUTDOWN handlers — so a doomed duplicate adopted the live
# sessions and then, on its way out, closed the idle ones. A real conversation was lost that way:
# a message was queued to it, the process that owned it was closed by a backend that had never
# served a single request, and the reply never came. A process that never served owns nothing.
_SERVED = False


def _may_close_sessions() -> bool:
    """May this process close the live sessions on its way out?

    Only if it ever served a request. A backend that lost the race for the port adopted them and
    never served anybody, so they were never its to close. A SECONDARY backend never owns them.
    """
    return _SERVED and not SECONDARY


# A SECOND BACKEND, FOR TESTING — `ASSET_STUDIO_SECONDARY=1` with its own `ASSET_STUDIO_PORT`.
#
# The way to prove a backend change without restarting the one the user works in is a second
# process on another port. But start-up is written for THE backend: it adopts every live Claude
# session (and kills the ones it cannot place), reaps the voice worker, sweeps review browsers,
# marks the other process's running jobs as failed, and on the way out closes sessions and stops
# managed services. A test backend doing any of that takes the user's conversations — or the very
# agent that started it — away from the backend that owns them. So a secondary one does none of
# it: it serves the API, runs its own headless browser in its own temporary profile, and leaves
# everything shared alone, coming up and going down.
SECONDARY = os.environ.get("ASSET_STUDIO_SECONDARY", "").strip().lower() not in ("", "0", "false", "no")


@app.middleware("http")
async def _log_slow(request, call_next):
    """Name any request that takes more than a second.

    Written because a workspace list that appeared a minute late could not be attributed from the
    outside: every endpoint measured fast once warm, and the cold case is the one nobody can
    reproduce on demand. Now the backend says which request was slow, and when.
    """
    global _SERVED
    _SERVED = True
    t0 = time.perf_counter()
    response = await call_next(request)
    dt = time.perf_counter() - t0
    if dt > 1.0:
        print(f"[slow] {dt:6.2f}s  {request.method} {request.url.path}"
              f"{('?' + request.url.query) if request.url.query else ''}")
    return response


@app.get("/api/health")
async def health():
    # ASYNC ON PURPOSE. A plain `def` route runs in the worker thread pool, behind every busy
    # feed parse and every slow provider check waiting there — and the desktop shell KILLS the
    # backend when this route stops answering. The answer below needs no I/O, so it runs on the
    # event loop and says "alive" whenever the loop is, however full the pool is.
    #
    # `pid` IS LOad-BEARING, not diagnostics. On Windows `.venv/Scripts/python.exe` is a
    # virtualenv REDIRECTOR: it launches the real interpreter as a child and stays alive as a
    # parent. The desktop launcher spawns the redirector, so the pid it holds is the stub's —
    # and killing that leaves the real backend running, still holding port 8777. A restart then
    # looked successful (health answered, because the survivor answered it) while changing
    # nothing at all. The supervisor reads this to kill the process that is really serving.
    return {"ok": True, "version": __version__, "active_jobs": len(queue.active()),
            "pid": os.getpid()}


async def _stats_broadcaster():
    """Push live system stats to all WS clients on an interval. collect() shells out to
    nvidia-smi, which can stall for seconds under GPU load — so it MUST run OFF the event
    loop. Calling it inline (as before) blocked every request and made the backend look
    'offline' until a manual restart, exactly under heavy GPU use."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            # Nobody connected (window closed / minimized to tray) → skip entirely: no
            # nvidia-smi shell-out, no publish. ~43k GPU-driver wakeups/day saved while
            # idle, and nothing competing with a running game.
            if bus.subscriber_count() > 0:
                stats = await loop.run_in_executor(None, collect)
                await bus.publish(ProgressEvent(type=WSEventType.stats, data=stats.model_dump()))
        except Exception:
            pass
        await asyncio.sleep(2.0)


async def _availability_warmer():
    """Keep provider availability cached & current (local model servers may come
    up after launch). Runs the parallel checks off the event loop."""
    from .providers import registry

    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, registry.warm_availability)
        except Exception:
            pass
        # UI open → keep availability snappy; nobody watching → a slow heartbeat is
        # plenty (REST calls still get the cached result on demand).
        await asyncio.sleep(25.0 if bus.subscriber_count() > 0 else 120.0)


async def _warm_up() -> None:
    """Everything that does NOT have to finish before the first request is served.

    This all used to be awaited inside the startup event, and a startup event blocks uvicorn from
    answering ANYTHING — not even /api/health. So the window sat on its splash while the backend
    reaped voice workers and scanned for stale distillers: measured here at ~2.7s of it, before
    the process scan, on a warm machine. Cold, with the OS file cache empty, it is worse, and the
    app looks dead rather than slow.

    Each step is timed and printed, so the next restart says where the time actually went instead
    of leaving anyone to guess.
    """
    loop = asyncio.get_running_loop()

    async def step(label: str, fn) -> None:
        t0 = time.perf_counter()
        try:
            await loop.run_in_executor(None, fn)
        except Exception as e:                       # noqa: BLE001 — warm-up never breaks boot
            print(f"[warmup] {label}: {type(e).__name__}: {e}")
        print(f"[warmup] {label}: {time.perf_counter() - t0:.2f}s")

    # FIRST, because it is the one the window actually waits on. Building the workspace list
    # walks every project directory and reads a transcript head from each — measured at 2.37s
    # cold here with 45 of them, and 0.01s once cached. Doing it now means the list is already
    # in hand by the time the UI asks, instead of the UI paying for it while you watch.
    try:
        from . import workspace as _ws
        await step("workspace list", _ws.roots)
    except Exception:
        pass
    # Reclaim what a previous backend left behind: a headless review browser nobody will ever
    # close again, and its profile directory. Measured here at 4 browsers holding 2.3 GB and 4
    # profile directories, from restarts across one afternoon. Early, and in any case before
    # anything in the UI has had time to ask for a review — the sweep skips a live browser.
    try:
        from . import review as _review
        await step("review browser sweep", _review.sweep_leftovers)
    except Exception:
        pass
    # If Graphify is on, make sure it's actually installed so the graph is ready to build —
    # covers the fresh-PC case where setup's pipx install silently failed.
    try:
        if settings.get("cc_graphify"):
            from . import graphify_index
            await step("graphify install check", graphify_index.ensure_installed)
    except Exception:
        pass
    # The web tools' own venv and browsers, if the toggle is on — the fresh-PC case again.
    # The SETTING, not web_tools.enabled(): that one also asks whether the tool is installed, which
    # on a fresh PC is exactly what is not yet true.
    try:
        if settings.get("cc_web_tools", True):
            from . import web_tools
            await step("web tools install check", web_tools.ensure_installed)
    except Exception:
        pass
    # A headless browser for the forge, the review and the live link. Nothing happens on a PC that
    # has Chrome or Edge; on one with neither, a Chrome for Testing is fetched in the background.
    try:
        if settings.get("cc_forge", True) or settings.get("cc_live") or settings.get("cc_review"):
            from . import browser_install
            await step("browser install check", browser_install.ensure_installed)
    except Exception:
        pass
    # The first run on a new PC: no Claude Code project yet and no folder opened — open theirs.
    try:
        from . import workspace as _ws_disc
        await step("workspace discovery", _ws_disc.autodiscover_roots)
    except Exception:
        pass
    # Install the bundled output styles (ASD-STE100) if absent, so the chat toggle has something
    # to pick and `/output-style` finds it in a plain terminal too. Never overwrites an edit.
    try:
        from . import output_styles
        await step("output styles", output_styles.ensure_installed)
    except Exception:
        pass
    # Reap a leftover Whisper voice worker orphaned by a previous backend — it holds GPU VRAM.
    try:
        from . import voice
        await step("voice worker reap", voice.reap_orphans)
    except Exception:
        pass
    # Zero-token auto-learn: reap leftover distiller workers, then start the loop. The loop gates
    # itself on the cc_autolearn toggle every tick — OFF costs one settings read per 20s.
    try:
        from . import autolearn
        await step("autolearn reap", autolearn.reap_orphans)
        autolearn.start_loop()
    except Exception:
        pass
    # Sweep stray automation-browser windows (headless:false test scripts) off the main screen.
    try:
        if settings.get("sweep_browser_windows", True):
            from . import window_sweeper
            window_sweeper.start()
    except Exception:
        pass
    # Keep the code graph current for every project being worked in — not only the one the
    # Workspace tab happens to be showing.
    try:
        from . import graph_watch
        graph_watch.start()
    except Exception:
        pass
    # Mark jobs left 'running'/'queued' by a previous process as failed.
    try:
        from . import db
        fixed = db.reconcile_orphans({j.id for j in queue.active()})
        if fixed:
            print(f"[warmup] reconciled {fixed} orphaned job(s) from a previous run")
    except Exception:
        pass
    # Repair the cost ledgers once, after the streaming running-total bug. `cc_session` banks a
    # turn's DELTA now, but the rows already on disk were summed running totals — 2.34x over on
    # this machine — and leaving them would keep the Dashboard wrong by that factor. Backs both
    # files up, runs once, and does nothing to a tree that already carries call ids.
    try:
        from . import ledger_repair
        rep = ledger_repair.repair()
        if rep.get("saved"):
            print(f"[warmup] repaired the cost ledgers: {rep['rows']} turns, "
                  f"{rep['dropped']} notifications dropped, ${rep['before']:.2f} -> "
                  f"${rep['after']:.2f}")
    except Exception as e:
        print(f"[warmup] cost-ledger repair skipped: {e}")


@app.on_event("startup")
async def _startup():
    """Only what MUST happen before the first request. Everything else is warmed in the
    background, because a startup event that takes ten seconds is ten seconds of an app that
    answers nothing and therefore looks broken."""
    boot = time.perf_counter()
    if SECONDARY:
        # See SECONDARY above: no adoption, no reaper, no sweeps, no job reconciliation, no
        # warm-up that touches what the primary backend owns. Only the API and its own browser.
        print(f"[startup] SECONDARY backend — shared state left alone; serving after "
              f"{time.perf_counter() - boot:.2f}s")
        return
    # Reap live claude streams orphaned by a previous backend (crash / hard restart) BEFORE
    # serving: a leftover duplicate on a project doubles its API calls and burns the rate limit
    # (429), which is what broke a workspace after a restart. This one genuinely cannot wait —
    # a send that arrives first would spawn the duplicate this prevents.
    try:
        from . import cc_session
        t0 = time.perf_counter()
        # First, before anything: tell the session keepers a backend is running again. They hold
        # the sessions' stdin open, and the missing beat is the only thing that would eventually
        # let them go — so this has to land before adoption, not after it.
        cc_session._beat()
        await asyncio.get_running_loop().run_in_executor(None, cc_session.adopt_orphans)
        cc_session.sweep_keeper_mail()      # mailboxes belonging to sessions that are gone
        cc_session._start_reaper()          # ...and keep beating, even with nothing adopted
        print(f"[startup] adopt orphaned sessions: {time.perf_counter() - t0:.2f}s")
    except Exception:
        pass
    # Deliver any /btw side-note stranded by a previous backend. Starts a thread; returns at once.
    try:
        from . import cc_session as _cc
        _cc.start_btw_sweeper()
    except Exception:
        pass
    # Bring the idle GPU-memory reaper up NOW, not on the first job. It was reachable only through
    # the job queue's lazy import, so a Studio that started and sat idle never ran its startup
    # sweep — and that sweep is the only thing that reclaims a ComfyUI left loaded by the PREVIOUS
    # session (a job that ran before a restart is invisible to this process's `_dirty`).
    try:
        from . import gpu_memory  # noqa: F401 - importing it is what starts the thread
    except Exception:
        pass
    await queue.start()
    app.state._stats_task = asyncio.create_task(_stats_broadcaster())
    app.state._avail_task = asyncio.create_task(_availability_warmer())
    app.state._warm_task = asyncio.create_task(_warm_up())
    print(f"[startup] serving after {time.perf_counter() - boot:.2f}s")


@app.on_event("shutdown")
async def _shutdown():
    try:
        from . import deepseek_session
        deepseek_session.shutdown()
    except Exception:
        pass
    for attr in ("_stats_task", "_avail_task", "_warm_task"):
        task = getattr(app.state, attr, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    try:
        # this process's own Codex app-server (a secondary backend has its own, not the primary's)
        from . import codex_app
        codex_app.shutdown()
    except Exception:
        pass
    if SECONDARY:
        # Its own browser only — the managed services, the sessions and the voice worker all
        # belong to the primary backend. See SECONDARY.
        try:
            from . import review as _review
            _review.shutdown()
        except Exception:
            pass
        print("[shutdown] SECONDARY backend — shared state left alone")
        return
    try:
        from .services import manager
        manager.stop_all_managed()
    except Exception:
        pass
    try:
        from . import cc_session
        if _may_close_sessions():
            cc_session.shutdown_all()  # close live claude stdin pipes so children exit
        else:
            # Never served a request, so it never owned these sessions — it lost the port and is
            # unwinding. Closing another backend's live sessions here is how a conversation was
            # silently ended mid-message.
            print("[shutdown] this process never served — leaving the live sessions alone")
    except Exception:
        pass
    try:
        from . import voice
        voice.shutdown()  # stop the Whisper worker if it's running
    except Exception:
        pass
    await queue.stop()


# Serve the built SPA with client-side-routing fallback: deep URLs like
# /workspace or /mission return index.html so a refresh stays on that tab.
# Registered last so /api/* and /ws (added above) win.
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    # index.html must NEVER be cached, or Electron/the browser keeps loading an OLD bundle after a
    # rebuild (the hashed files under /assets are content-addressed, so those stay cacheable).
    # Without this, freshly built UI (e.g. the Workflows / Ask AI tabs) only showed after a Ctrl+R.
    _NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api") or full_path.startswith("ws"):
            raise HTTPException(404, "not found")
        candidate = FRONTEND_DIST / full_path
        if full_path and full_path != "index.html" and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html", headers=_NO_CACHE)


def _wait_for_port(host: str, port: int, timeout: float = 8.0) -> None:
    """Wait until nobody is listening on the port, or give up and let uvicorn report it.

    A restart overlaps by nature: the process being replaced can still own the socket for a
    moment. Without this, the replacement died on bind and the app came back only because the
    launcher tried again — or not at all, when the launcher was not there. A few seconds of
    patience turns "crashed at start-up" into "started a moment later".

    CONNECT, do not bind, to test it. On Windows SO_REUSEADDR lets a second socket bind a port
    that is already in use, so a bind test would answer "free" while the old server was serving.
    """
    import socket
    import time as _t
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    end = _t.time() + timeout
    while _t.time() < end:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex((target, port)) != 0:
                return                       # nothing is listening — the port is ours
        _t.sleep(0.3)


def _already_serving(host: str, port: int) -> bool:
    """Is a healthy Asset Studio backend already on this port?"""
    import json as _json
    import urllib.request
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (target, port), timeout=1.5) as r:
            return bool(_json.load(r).get("ok"))
    except Exception:
        return False


def main():
    import uvicorn

    # Tools installed since the launcher started (by the installer, or by Settings) are on the
    # PATH Windows holds, not on the one this process was given. Every agent inherits this PATH.
    from .config import refresh_path
    added = refresh_path()
    if added:
        print("[startup] PATH: added %d folder(s) Windows has and the launcher did not pass: %s"
              % (len(added), "; ".join(added)))

    # env overrides let the desktop launcher flip LAN access on/off without editing settings
    host = os.environ.get("ASSET_STUDIO_HOST") or settings.get("host", "127.0.0.1")
    port = int(os.environ.get("ASSET_STUDIO_PORT") or settings.get("port", 8777))
    _wait_for_port(host, port)

    # A SECOND BACKEND MUST NOT TRY, AND MUST NOT DO ANYTHING ON ITS WAY OUT.
    #
    # Two spawners can start one at the same moment — the launcher's respawn-on-exit and a
    # deliberate restart. One wins the port and the other used to get as far as `Errno 10048`,
    # which happens AFTER uvicorn has run the start-up handlers: so the doomed process adopted
    # live sessions, started a reaper, and only then died. Its exit brought the launcher straight
    # back to respawn it. That loop ran twenty-six times here.
    #
    # Deciding it HERE, before uvicorn, means a duplicate touches nothing and leaves quietly.
    if _already_serving(host, port):
        print("[startup] an Asset Studio backend already serves %s:%d — leaving it alone" % (host, port))
        return

    uvicorn.run("asset_studio.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()

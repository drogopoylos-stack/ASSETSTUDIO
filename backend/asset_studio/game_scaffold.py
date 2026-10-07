"""A new game that is Studio-ready from the first minute.

"New project" makes an empty folder, and the last game started from one (Downloads/NEW GAME OPUS 5)
grew about sixty ad-hoc screenshots and test pages in its root: its agent spent its first hours
building tools the Studio already has, because every one of them depends on a few lines the game
has to carry and nobody adds those lines to a blank folder. So a new game is made from a template
that already carries them:

  window.__game        the renderer/scene/camera, or the PlayCanvas app: where the live link, the
                       forge and the scene tools look first. A module game has no window.THREE and
                       no window.pc; without this line the engine is found by a heap hunt or not at
                       all.
  studio.edits.json    applied before the first frame by src/studio-runtime.js, a copy of the
                       Studio's own runtime taken now, so a move saved in the Studio ships.
  window.__review      one real action the visual review can fire, and review/targets.js with an
                       isolate target per builder.
  src/assets.js        two builders that return a named group with its feet on y = 0: the shape
                       the Library lists, the forge builds and the scene tools place.
  serve.mjs            a zero-dependency static server that prints its URL the way Vite does, so
                       the Studio's dev-server launcher (dev_server.py) starts it like any other,
                       that answers the isolate review's page with the game's import map, and
                       that tells the open page to reload when a file of the game changes
                       (/__studio_live, and the few lines in index.html that listen to it).

The engine install (`npm install`) runs in the background and logs to `<game>/.studio/install.log`;
the request that made the game returns at once and `install_status` reports on it. Nothing here
runs on the event loop: the routes are sync, so FastAPI runs them in its threadpool, and the one
long wait is a daemon thread.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zlib
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .config import FRONTEND_DIST, PKG_DIR, settings

TEMPLATES = PKG_DIR / "templates" / "new_game"
ENGINES = ("three", "playcanvas")
_ALIASES = {"three": "three", "threejs": "three", "three.js": "three",
            "playcanvas": "playcanvas", "pc": "playcanvas", "play-canvas": "playcanvas"}
# 2: serve.mjs reloads the open page when a file changes. A game made at 1 has no /__studio_live,
# so a tool that reads studio.game.json can tell which games reload by themselves.
TEMPLATE_VERSION = 2
LIVE_RELOAD = "/__studio_live"

# THE RUNTIME IS COPIED, NOT LINKED. A shipped game has no Studio to fetch it from, so the file the
# game imports is a copy taken when the game is made. When the Studio has not built it yet the game
# gets a stub with the same exports that says so, and its imports still work.
RUNTIME_SRC = FRONTEND_DIST / "studio-runtime.js"
RUNTIME_EXPORTS = ("version", "engineOf", "rootOf", "studioKeys", "findByKey", "loadStudioEdits",
                   "applyStudioEdits", "placeStudioItem", "unplaceStudioItem")

# Files whose text carries placeholders; anything else is copied byte for byte.
_TEXT_EXT = {".html", ".htm", ".js", ".mjs", ".md", ".css", ".txt", ""}
_NOT_COPIED = {"template.json"}
# A template cannot carry a real `.gitignore`: inside the Studio's own repository it would be a
# live ignore file for the template folder itself. npm's templates rename it on the way out too.
_RENAME = {"gitignore": ".gitignore"}
_RESERVED = {"con", "prn", "aux", "nul", *("com%d" % i for i in range(1, 10)),
             *("lpt%d" % i for i in range(1, 10))}

# Each game prefers a port of its own, from its name, so two new games are not both fighting over
# 5173 with every Vite project on the machine. serve.mjs still walks up when the port is taken.
_PORT_LO, _PORT_SPAN = 5200, 700

# `npm install` for three is ~10 s here and PlayCanvas ~20 s; a registry that stalls must still end.
INSTALL_TIMEOUT = 15 * 60.0

_installs: dict[str, dict] = {}
_lock = threading.Lock()
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# ---------------------------------------------------------------------------
# Names, engines, places
# ---------------------------------------------------------------------------
def engine_of(value: str = "") -> str:
    """The engine a caller meant; "" means the `new_game_engine` setting."""
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(settings.get("new_game_engine", "three") or "three").strip().lower()
    eng = _ALIASES.get(raw, "")
    if not eng:
        raise ValueError('unknown engine %r: use "three" or "playcanvas"' % (value or raw))
    return eng


def template(engine: str) -> dict:
    """The template's own description of itself: dependency, import map, builders, targets."""
    return json.loads((TEMPLATES / engine / "template.json").read_text(encoding="utf-8"))


def title_of(name: str) -> str:
    """The name as a person wrote it, minus control characters. It goes into the page title and the
    README, so a newline in it would break a line nobody would think to check."""
    t = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(name or "")).split())[:64].strip()
    if not any(c.isalnum() for c in t):
        raise ValueError("give the game a name with at least one letter or digit in it")
    return t


def folder_of(title: str) -> str:
    """The folder's name: the same characters `new_project` allows, and never a name Windows keeps
    for a device (a folder called `con` cannot be created or deleted by Explorer)."""
    f = "".join(c for c in title if c.isalnum() or c in " ._-()&").strip(" .")
    if not f:
        raise ValueError("that name has nothing a folder name can keep: use letters or digits")
    if f.split(".")[0].strip().lower() in _RESERVED:
        f += " game"
    return f


def slug_of(title: str) -> str:
    """The package name npm accepts: lower case, no spaces, no leading dot or underscore."""
    s = re.sub(r"[^a-z0-9._-]+", "-", title.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-._")[:60].strip("-._")
    return s or "game"


def port_for(slug: str) -> int:
    return _PORT_LO + zlib.crc32(slug.encode("utf-8")) % _PORT_SPAN


def _studio_origin() -> str:
    host = str(settings.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return "http://%s:%s" % (host, settings.get("port") or 8777)


def default_parent(beside: str = "") -> tuple[Path, str]:
    """Where a game goes when the caller names no folder, and why there.

    The `new_game_parent` setting first; else beside the project the caller is in (`beside`),
    because that is where a person looks for the thing they just made; else the Desktop."""
    s = str(settings.get("new_game_parent", "") or "").strip()
    if s:
        return Path(s).expanduser(), "setting"
    b = str(beside or "").strip()
    if b:
        p = Path(b).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if p.is_file():
            p = p.parent
        return p.parent, "beside"
    home = Path.home()
    desk = home / "Desktop"
    return (desk if desk.is_dir() else home), "home"


def _parent(parent: str, beside: str) -> Path:
    from . import workspace
    raw = str(parent or "").strip()
    p = Path(raw).expanduser() if raw else default_parent(beside)[0]
    # The same rule as "New project": home, Downloads, Desktop, Documents and the folders beside
    # an open project. Anything else is refused with the reason, never created quietly.
    rp = workspace._check_create(str(p))
    if rp.exists() and not rp.is_dir():
        raise NotADirectoryError("%s is a file, not a folder" % rp)
    return rp


def _game_dir(path: str) -> Path:
    from . import workspace
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path is required")
    rp = workspace._check_create(str(Path(raw).expanduser()))
    if not rp.exists():
        raise FileNotFoundError(str(rp))
    if not rp.is_dir():
        raise NotADirectoryError(str(rp))
    return rp


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------
def _runtime_missing(text: str) -> list[str]:
    """The runtime exports a built file does not have. A half-written or wrong file is refused and
    the stub is used instead, because a game importing a name that is not there does not start."""
    return [n for n in RUNTIME_EXPORTS if not re.search(r"\b%s\b" % re.escape(n), text)]


def runtime_info() -> dict:
    """Is the Studio's runtime built, and would a new game get it or the stub?"""
    p = RUNTIME_SRC
    try:
        data = p.read_bytes()
    except OSError:
        return {"ready": False, "path": str(p), "bytes": 0,
                "why": "frontend/dist/studio-runtime.js is not built yet"}
    missing = _runtime_missing(data.decode("utf-8", "replace"))
    return {"ready": not missing, "path": str(p), "bytes": len(data),
            "sha1": hashlib.sha1(data).hexdigest()[:12], "missing": missing}


def _install_runtime(game: Path, values: dict) -> dict:
    dest = game / "src" / "studio-runtime.js"
    dest.parent.mkdir(parents=True, exist_ok=True)
    why = "frontend/dist/studio-runtime.js is not built yet"
    try:
        data = RUNTIME_SRC.read_bytes()
    except OSError:
        data = b""
    if data.strip():
        missing = _runtime_missing(data.decode("utf-8", "replace"))
        if not missing:
            dest.write_bytes(data)
            return {"file": "src/studio-runtime.js", "source": "studio", "bytes": len(data),
                    "sha1": hashlib.sha1(data).hexdigest()[:12]}
        why = "the built runtime lacks %s" % ", ".join(missing)
    text = _render((TEMPLATES / "runtime-stub.js").read_text(encoding="utf-8"), ".js", values)
    dest.write_text(text, encoding="utf-8", newline="\n")
    return {"file": "src/studio-runtime.js", "source": "stub", "bytes": len(text.encode("utf-8")),
            "why": why}


# ---------------------------------------------------------------------------
# Writing the game
# ---------------------------------------------------------------------------
def _render(text: str, suffix: str, values: dict) -> str:
    for key, val in values.items():
        token = "__GAME_%s__" % key
        if token in text:
            text = text.replace(token, html.escape(val, quote=True)
                                if suffix in (".html", ".htm") else val)
    return text


def _template_files(engine: str) -> dict[str, Path]:
    """Output path -> template file. The engine's own files win over the shared ones."""
    out: dict[str, Path] = {}
    for src in (TEMPLATES / "common", TEMPLATES / engine):
        for p in sorted(src.rglob("*")):
            if not p.is_file() or p.name in _NOT_COPIED:
                continue
            rel = p.relative_to(src)
            name = _RENAME.get(rel.name, rel.name)
            out[(rel.parent / name).as_posix()] = p
    return out


def _write_json(path: Path, data: dict) -> None:
    # Temp and replace, like every other JSON file the Studio writes.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _package_json(slug: str, title: str, meta: dict) -> dict:
    return {
        "name": slug,
        "version": "0.1.0",
        "private": True,
        "description": "%s: a %s game made with the Asset Studio" % (title, meta["label"]),
        "type": "module",
        "scripts": {"dev": "node serve.mjs", "start": "node serve.mjs"},
        "dependencies": dict(meta["dependencies"]),
    }


def _manifest(title: str, engine: str, meta: dict, runtime: dict, port: int) -> dict:
    return {
        "studio_game": 1,
        "name": title,
        "engine": engine,
        "engine_label": meta["label"],
        "engine_file": meta.get("engine_file", ""),
        "entry": "index.html",
        "main": "src/main.js",
        "handle": meta.get("handle", ""),
        "builders": meta.get("builders", []),
        "runtime": {k: runtime[k] for k in ("file", "source", "bytes", "sha1") if k in runtime},
        "edits": "studio.edits.json",
        "review": {"targets": "review/targets.js", "names": meta.get("targets", []),
                   "actions": meta.get("actions", [])},
        "objects": meta.get("objects", []),
        "dev": {"script": "dev", "command": "node serve.mjs", "port": port, "live_reload": LIVE_RELOAD},
        "made": {"by": "Asset Studio", "template": TEMPLATE_VERSION,
                 "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
    }


def _undo(game: Path, made_dir: bool) -> None:
    """A game that failed halfway is removed, so a retry with the same name is not refused.

    Only ever called on a folder that was missing or EMPTY when `create` began, so everything in
    it is what this call wrote."""
    if made_dir:
        shutil.rmtree(game, ignore_errors=True)
        return
    try:
        children = list(game.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def scaffold(game: Path, title: str, engine: str) -> dict:
    """Write the template into `game` (which must exist and be empty). Returns what was written."""
    meta = template(engine)
    slug = slug_of(title)
    port = port_for(slug)
    values = {"TITLE": title, "SLUG": slug, "PORT": str(port), "STUDIO_ORIGIN": _studio_origin()}
    written: list[str] = []
    for rel, src in _template_files(engine).items():
        out = game / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() in _TEXT_EXT:
            out.write_text(_render(src.read_text(encoding="utf-8"), src.suffix.lower(), values),
                           encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(src, out)
        written.append(rel)
    _write_json(game / "package.json", _package_json(slug, title, meta))
    written.append("package.json")
    runtime = _install_runtime(game, values)
    written.append(runtime["file"])
    _write_json(game / "studio.game.json", _manifest(title, engine, meta, runtime, port))
    written.append("studio.game.json")
    return {"files": sorted(written), "runtime": runtime, "port": port, "slug": slug, "meta": meta}


def create(name: str, parent: str = "", engine: str = "", install: Optional[bool] = None,
           open_: bool = True, beside: str = "") -> dict:
    """Make a new game folder from the engine's template; optionally install and open it."""
    title = title_of(name)
    eng = engine_of(engine)
    base = _parent(parent, beside)
    folder = folder_of(title)
    game = base / folder
    if game.exists():
        if not game.is_dir():
            raise FileExistsError("'%s' already exists in %s, and it is a file" % (folder, base))
        if any(game.iterdir()):
            raise FileExistsError("'%s' already exists in %s and is not empty: pick another name"
                                  % (folder, base))
        made_dir = False
    else:
        game.mkdir(parents=True, exist_ok=False)
        made_dir = True
    try:
        made = scaffold(game, title, eng)
    except Exception:
        # Everything written so far goes, or a retry with the same name is refused as "not empty".
        _undo(game, made_dir)
        raise
    want = bool(settings.get("new_game_install", True)) if install is None else bool(install)
    inst = start_install(game) if want else {"state": "skipped"}
    root, root_error = None, ""
    if open_:
        try:
            from . import workspace
            root = workspace.add_root(str(game))
        except Exception as e:                       # noqa: BLE001 — the game exists either way
            root_error = str(e)
    port = made["port"]
    out = {
        "ok": True,
        "path": str(game),
        "name": game.name,
        "title": title,
        "engine": eng,
        "files": made["files"],
        "install": inst,
        "dev": {"script": "dev", "command": "node serve.mjs", "port": port,
                "url": "http://127.0.0.1:%d/" % port,
                "note": "the preferred port; serve.mjs takes the next free one and prints it"},
        "runtime": made["runtime"],
        "root": root,
        "next": _next_steps(game, eng, inst.get("state") == "running"),
    }
    if root_error:
        out["root_error"] = root_error
    return out


def _next_steps(game: Path, engine: str, installing: bool) -> list[str]:
    p = str(game)
    q = json.dumps(p)
    add = "add(m.buildCrate({ app }))" if engine == "playcanvas" else "add(m.buildCrate())"
    steps = [("npm install is running: GET /api/workspace/new-game/status?path=%s until done is true"
              % quote(p)) if installing else ("install the engine first: `npm install` in %s" % p)]
    steps += [
        'open it live: POST /api/live/open {"project": %s} (starts `npm run dev`, which is node serve.mjs)' % q,
        "see one builder: POST /api/live/forge {\"project\": %s, \"js\": \"const m = await import('./src/assets.js'); %s\"}" % (q, add),
        'review it: POST /api/review/render {"project": %s, "mode": "scene"}; one builder: "mode": "isolate", "target": "crate"' % q,
        "add a builder: export buildThing({...} = {}) from src/assets.js returning a named group with its feet on y = 0, then add its target to review/targets.js",
        "moves saved in the Studio land in studio.edits.json; src/studio-runtime.js applies them before the first frame",
        "an edit to the game's code reloads the open page by itself within a second (serve.mjs, %s); "
        "saving studio.edits.json does not, because the page already shows that move" % LIVE_RELOAD,
    ]
    return steps


def defaults(beside: str = "") -> dict:
    """What the New game dialog starts from: the settings, the engines, and whether npm is here."""
    parent, source = default_parent(beside)
    try:
        eng = engine_of("")
    except ValueError:
        eng = "three"
    engines = []
    for e in ENGINES:
        meta = template(e)
        engines.append({"id": e, "label": meta["label"], "dependencies": meta["dependencies"],
                        "handle": meta.get("handle", "")})
    npm = _npm()
    node = shutil.which("node") or ""
    return {"ok": True, "parent": str(parent), "parent_from": source, "engine": eng,
            "install": bool(settings.get("new_game_install", True)), "engines": engines,
            "runtime": runtime_info(), "npm": {"found": bool(npm), "path": npm},
            "node": {"found": bool(node), "path": node}}


# ---------------------------------------------------------------------------
# The engine install, in the background
# ---------------------------------------------------------------------------
def _npm() -> str:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm") or ""
    return shutil.which("npm") or ""


def _state_path(game: Path) -> Path:
    return game / ".studio" / "install.json"


def _write_state(game: Path, state: dict) -> None:
    try:
        p = _state_path(game)
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_json(p, state)
    except OSError:
        pass


def _read_state(game: Path) -> dict:
    try:
        d = json.loads(_state_path(game).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _kill_tree(pid: int) -> None:
    """npm on Windows is cmd.exe running node running more node: killing the first leaves the
    rest downloading, so the whole tree goes."""
    try:
        import psutil
        p = psutil.Process(pid)
        for kid in p.children(recursive=True):
            try:
                kid.kill()
            except Exception:
                pass
        p.kill()
    except Exception:
        pass


def start_install(game: Path, cmd: Optional[list] = None, timeout: float = INSTALL_TIMEOUT) -> dict:
    """Run `npm install` in the game folder, in the background. Returns at once.

    `cmd` replaces the command, for tests. A second call while one is running answers with the
    running one instead of starting a rival that would fight it over node_modules."""
    game = Path(game).resolve()
    key = str(game).lower()
    with _lock:
        rec = _installs.get(key)
        if rec and not rec.get("finished"):
            return {"state": "running", "pid": rec["proc"].pid, "log": rec["log"], "already": True}
    studio = game / ".studio"
    studio.mkdir(parents=True, exist_ok=True)
    log = studio / "install.log"
    if cmd is None:
        npm = _npm()
        if not npm:
            msg = ("npm was not found on PATH. Install Node.js from nodejs.org, then run "
                   "`npm install` in %s" % game)
            log.write_text(msg + "\n", encoding="utf-8")
            _write_state(game, {"state": "failed", "ok": False, "error": msg,
                                "finished": time.time()})
            return {"state": "failed", "error": msg, "log": str(log)}
        cmd = [npm, "install", "--no-audit", "--no-fund", "--progress=false"]
    env = dict(os.environ, NO_UPDATE_NOTIFIER="1", npm_config_update_notifier="false")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0      # type: ignore[attr-defined]
    fh = open(log, "wb")                       # our own log, new for each install
    fh.write(("$ %s\n  in %s\n\n" % (" ".join([Path(cmd[0]).name] + [str(c) for c in cmd[1:]]),
                                      game)).encode("utf-8"))
    fh.flush()
    try:
        proc = subprocess.Popen(cmd, cwd=str(game), stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, creationflags=flags)
    except OSError as e:
        fh.close()
        msg = "could not start %s: %s" % (Path(cmd[0]).name, e)
        with open(log, "ab") as f:
            f.write((msg + "\n").encode("utf-8"))
        _write_state(game, {"state": "failed", "ok": False, "error": msg, "finished": time.time()})
        return {"state": "failed", "error": msg, "log": str(log)}
    started = time.time()
    rec = {"proc": proc, "log": str(log), "fh": fh, "started": started, "finished": 0.0,
           "code": None, "timed_out": False}
    with _lock:
        _installs[key] = rec
    _write_state(game, {"state": "running", "pid": proc.pid, "started": started})
    threading.Thread(target=_finish, args=(game, rec, timeout), name="new-game-install",
                     daemon=True).start()
    return {"state": "running", "pid": proc.pid, "log": str(log)}


def _finish(game: Path, rec: dict, timeout: float) -> None:
    proc = rec["proc"]
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        rec["timed_out"] = True
        _kill_tree(proc.pid)
        try:
            code = proc.wait(timeout=15)
        except Exception:                            # noqa: BLE001
            code = -1
    try:
        rec["fh"].close()
    except Exception:                                # noqa: BLE001
        pass
    ok = code == 0 and (game / "node_modules").is_dir()
    try:
        with open(rec["log"], "ab") as f:
            f.write(("\n[studio] %s: exit %s after %.1fs\n" % (
                "timed out" if rec["timed_out"] else ("done" if ok else "failed"), code,
                time.time() - rec["started"])).encode("utf-8"))
    except OSError:
        pass
    _write_state(game, {"state": "done" if ok else "failed", "ok": ok, "code": code,
                        "started": rec["started"], "finished": time.time(),
                        "timed_out": rec["timed_out"]})
    # `finished` last: install_status reads it as "the state file and the log are complete".
    rec["code"] = code
    rec["finished"] = time.time()


def _tail(log: Path, lines: int = 14, cap: int = 2400) -> str:
    try:
        with open(log, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 24_000))
            raw = f.read()
    except OSError:
        return ""
    text = _ANSI.sub("", raw.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n")
    kept = [ln.rstrip() for ln in text.split("\n") if ln.strip()][-lines:]
    return "\n".join(kept)[-cap:]


def _alive(pid: int, started: float) -> bool:
    try:
        import psutil
        p = psutil.Process(int(pid))
        # A pid is reused; the process that holds it now must have started with the install.
        return p.is_running() and abs(p.create_time() - float(started or 0)) < 30
    except Exception:
        return False


def install_status(path: str) -> dict:
    """Where the engine install of a game stands: {installing, done, ok, tail, ...}.

    `ok` is true once npm exited 0 and node_modules is there. A game whose install was started by
    an earlier backend is judged from `.studio/install.json`, and from node_modules when even that
    was cut short."""
    game = _game_dir(path)
    key = str(game).lower()
    log = game / ".studio" / "install.log"
    nm = (game / "node_modules").is_dir()
    with _lock:
        rec = _installs.get(key)
    if rec:
        finished = float(rec.get("finished") or 0)
        running = not finished
        code = rec.get("code")
        ok = bool(finished and code == 0 and nm)
        state = "running" if running else ("done" if ok else "failed")
        started = rec["started"]
        end = finished or time.time()
        timed_out = bool(rec.get("timed_out"))
    else:
        st = _read_state(game)
        state = str(st.get("state") or "")
        code = st.get("code")
        started = float(st.get("started") or 0)
        end = float(st.get("finished") or 0) or time.time()
        timed_out = bool(st.get("timed_out"))
        if state == "running":
            if _alive(st.get("pid") or 0, started):
                running, ok = True, False
            else:
                # Cut short: the backend that watched it is gone. node_modules is the best witness.
                running, ok, state = False, nm, ("done" if nm else "stopped")
        elif state in ("done", "failed"):
            running, ok = False, bool(st.get("ok")) and nm
        else:
            running, ok, state = False, nm, ("present" if nm else "never")
    return {"ok": ok, "installing": running, "done": not running and state != "never",
            "state": state, "code": code, "tail": _tail(log), "log": str(log),
            "node_modules": nm, "timed_out": timed_out,
            "seconds": round(end - started, 1) if started else None}

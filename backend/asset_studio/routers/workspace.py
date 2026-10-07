"""Workspace explorer/editor endpoints — VSCode-style file access for projects."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import workspace

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except (IsADirectoryError, NotADirectoryError, ValueError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))


@router.post("/discover")
def discover(body: dict | None = None):
    """Folders on this machine that look like projects and are not open yet; add them when asked.

    `{"add": true}` opens every one found (the first-run path does this by itself when nothing is
    open); without it this only lists them, so a person can pick."""
    from .. import workspace as _ws
    found = _ws.discover_projects()
    added = _ws.add_roots([f["path"] for f in found]) if (body or {}).get("add") else 0
    return {"found": found, "added": added}


@router.get("/roots")
def roots():
    return workspace.roots()


@router.get("/tree")
def tree(path: str = Query(...)):
    return _guard(workspace.list_dir, path)


@router.get("/file")
def file(path: str = Query(...)):
    return _guard(workspace.read_file, path)


@router.get("/stat")
def stat(path: str = Query(...)):
    return _guard(workspace.stat_file, path)


@router.get("/changes")
def changes(root: str = Query(""), since: int = 0):
    """Live filesystem change-feed for the explorer: ensures we're watching `root`
    and returns the global version + paths changed since the client's last version."""
    from .. import fswatch
    if root and workspace._within(Path(root), workspace._browse_roots()):
        fswatch.ensure_watching(str(Path(root).resolve()))
    return fswatch.changes_since(since)


@router.get("/search")
def search(root: str = Query(...), q: str = Query("", alias="q"), limit: int = 40):
    return _guard(workspace.search, root, q, limit)


@router.get("/raw")
def raw(path: str = Query(...), root: str = Query("")):
    # View-only byte serving (inline chat thumbnails). Uses the same resolver as reveal/open so a
    # bare filename, or a render that lives outside every project, still displays — matching what
    # the chat links already do. `run` stays strictly project-scoped via _locate().
    # `root` is the workspace the link came from: a bare name like "final.png" resolves inside its
    # OWN project instead of the first open project that happens to contain that name.
    rp = _guard(workspace._locate_for_view, path, root)
    mt = mimetypes.guess_type(rp.name)[0] or "application/octet-stream"
    # The URL is the path as WRITTEN, so two different files can share one. Without an explicit
    # policy the browser applies heuristic freshness and can keep showing the first image it
    # cached under that URL. "no-cache" only forces revalidation — a 304 still costs nothing.
    return FileResponse(rp, media_type=mt, headers={"Cache-Control": "no-cache"})


@router.get("/model")
def model(path: str = Query(...)):
    """A viewable GLB for meshes the 3D view can't read natively (.obj/.stl/.ply) — converted
    on demand and cached, so an OBJ opens in the same viewer as a GLB instead of dead-ending."""
    p = _guard(workspace.mesh_as_glb, path)
    return FileResponse(p, media_type="model/gltf-binary")


class WriteBody(BaseModel):
    path: str
    text: str = ""


@router.put("/file")
def write(body: WriteBody):
    return _guard(workspace.write_file, body.path, body.text)


class PathBody(BaseModel):
    path: str
    # the workspace the link came from; scopes a name several projects share
    root: str = ""


@router.post("/mkdir")
def mkdir(body: PathBody):
    return _guard(workspace.mkdir, body.path)


@router.post("/newfile")
def newfile(body: PathBody):
    return _guard(workspace.write_file, body.path, "")


@router.post("/delete")
def delete(body: PathBody):
    return _guard(workspace.delete, body.path)


class RenameBody(BaseModel):
    path: str
    name: str


@router.post("/rename")
def rename(body: RenameBody):
    return _guard(workspace.rename, body.path, body.name)


@router.post("/rename-root")
def rename_root(body: RenameBody):
    """Rename a PROJECT folder + migrate its Claude Code transcripts so the conversation
    context survives the rename (a bare rename would orphan ~/.claude/projects/<slug>)."""
    return _guard(workspace.rename_root, body.path, body.name)


class DestBody(BaseModel):
    path: str
    dest_dir: str


@router.post("/move")
def move(body: DestBody):
    return _guard(workspace.move, body.path, body.dest_dir)


@router.post("/copy")
def copy(body: DestBody):
    return _guard(workspace.copy, body.path, body.dest_dir)


@router.get("/resolve")
def resolve_path(path: str = Query(...), root: str = Query("")):
    """Where a chat link actually points, without opening anything — for "copy full path".
    Same resolver as the thumbnail and reveal, so all three agree on which file is meant."""
    return {"path": str(_guard(workspace._locate_for_view, path, root))}


@router.post("/reveal")
def reveal(body: PathBody):
    return _guard(workspace.reveal, body.path, body.root)


@router.post("/open-browser")
def open_browser(body: PathBody):
    """Serve the file/folder over http and open it in Chrome (or the default browser)."""
    return _guard(workspace.open_in_browser, body.path)


class UrlBody(BaseModel):
    url: str


@router.post("/open-url")
def open_url(body: UrlBody):
    """Open a local URL (a running dev server) in the browser."""
    return _guard(workspace.open_browser_url, body.url)


@router.get("/entries")
def entries(path: str = Query(...)):
    """Every page in this project that could be the app, best first — for the localhost menu.
    One project often holds several games; this is how you pick a different one."""
    return {"entries": _guard(workspace.find_entries, path)}


class DevBody(BaseModel):
    path: str          # the PROJECT folder (not the page)


@router.post("/dev-server")
def dev_server_start(body: DevBody):
    """Start the project's own dev server (npm run dev) and return the URL it printed.
    This is what makes the localhost button show the CURRENT source instead of a stale build."""
    from .. import dev_server
    from pathlib import Path
    p = _guard(workspace._check_browse, body.path)
    return dev_server.start(Path(p))


@router.post("/dev-server/stop")
def dev_server_stop(body: DevBody):
    from .. import dev_server
    from pathlib import Path
    p = _guard(workspace._check_browse, body.path)
    return {"stopped": dev_server.stop(Path(p))}


@router.get("/preview-url")
def preview_url(path: str = Query(...)):
    """Localhost URL for the in-app HTML preview iframe (serves the file's folder over http)."""
    return _guard(workspace.preview_url, path)


@router.post("/run")
def run(body: PathBody):
    """Launch a file the right way for its type (html→serve+open, scripts→console, exe/bat→run)."""
    return _guard(workspace.run, body.path)


@router.post("/open")
def open_file(body: PathBody):
    """Open a chat-referenced file in the OS default app (image viewer, etc.) — works out-of-project."""
    return _guard(workspace.open_file, body.path, body.root)


class ImportBody(BaseModel):
    src: str
    dest_dir: str


@router.post("/import")
def import_external(body: ImportBody):
    return _guard(workspace.import_external, body.src, body.dest_dir)


@router.post("/upload-file")
async def upload_file(dest_dir: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    return _guard(workspace.upload_file, dest_dir, file.filename or "file", data)


class NewProjectBody(BaseModel):
    parent: str
    name: str


@router.post("/new-project")
def new_project(body: NewProjectBody):
    return _guard(workspace.new_project, body.parent, body.name)


@router.post("/add-root")
def add_root(body: PathBody):
    """Open an existing folder in the workspace."""
    return _guard(workspace.add_root, body.path)


@router.post("/remove-root")
def remove_root(body: PathBody):
    return _guard(workspace.remove_root, body.path)


@router.post("/upload-image")
async def upload_image(project_path: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    ext = Path(file.filename or "paste.png").suffix or ".png"
    return _guard(workspace.save_upload, project_path, data, ext)


# ---------------------------------------------------------------------------
# A new game, Studio-ready from the first minute (see game_scaffold.py)
# ---------------------------------------------------------------------------
class NewGameBody(BaseModel):
    name: str
    parent: str = ""               # "" = the new_game_parent setting, else beside `beside`
    engine: str = ""               # "three" | "playcanvas"; "" = the new_game_engine setting
    install: bool | None = None    # `npm install` in the background; None = new_game_install
    open: bool = True              # add it as a workspace root
    beside: str = ""               # the project the caller is in: "" parent puts the game next to it


@router.post("/new-game")
def new_game(body: NewGameBody):
    """Make a game that the live link, the forge, the review and the scene tools can all use from
    its first minute: `window.__game`, saved edits applied at start, review targets, builders with
    their feet on y = 0 and a zero-dependency dev server. The install runs in the background."""
    from .. import game_scaffold
    return _guard(game_scaffold.create, body.name, parent=body.parent, engine=body.engine,
                  install=body.install, open_=body.open, beside=body.beside)


@router.get("/new-game/status")
def new_game_status(path: str = Query(...)):
    """Where a new game's engine install stands: {installing, done, ok, tail}."""
    from .. import game_scaffold
    return _guard(game_scaffold.install_status, path)


@router.get("/new-game/defaults")
def new_game_defaults(beside: str = Query("")):
    """What the New game dialog starts from: folder, engine, install, and whether npm is here."""
    from .. import game_scaffold
    return _guard(game_scaffold.defaults, beside)

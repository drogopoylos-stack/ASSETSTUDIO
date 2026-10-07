"""The engine window's API.

Read-only by design, and that is the point: the window is a viewer over what the agents are
already doing. It never starts a browser, never starts a dev server and never runs a forge. If it
did, opening a panel would quietly cost a gigabyte on a machine that is already running the game,
a build and a model.
"""
from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import engine

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/state")
def state():
    """One poll for the status pill: in use or not, by which games, and what was made last."""
    return engine.state()


@router.get("/projects")
def projects(fresh: bool = False):
    """The pinned workspaces that are games, with engine, dev server and what is installed."""
    return engine.projects(fresh)


@router.get("/project")
def project(path: str):
    """One project in full, including its dev-server URL — the slow lookup, on demand only."""
    return engine.project_detail(path)


@router.get("/games")
def games(project: str, fresh: bool = False):
    """EVERY game inside one workspace, each with its own dev server.

    One workspace here holds three games on three ports. Anything that opens one of their files
    has to know which of the three owns it, because the other two cannot serve it.
    """
    return engine.games(project, fresh)


@router.get("/history")
def history(project: str = "", limit: int = 80, kind: str = "", type: str = "", subject: str = "", q: str = ""):
    """Everything made so far, newest first, with counts per project, type and subject."""
    return engine.history(project, limit, kind, type, subject, q)


class TagBody(BaseModel):
    id: str
    subject: str = ""
    tags: list = []


@router.post("/tag")
def tag(body: TagBody):
    """The one write in this API: a person's word on what a generation depicts. Metadata only —
    the sheet and the code are untouched — and it sticks over any later guess."""
    return engine.retag(body.id, body.subject, body.tags)


@router.get("/assets")
def assets(project: str, type: str = "", subject: str = "", q: str = "", fresh: bool = False,
           root: str = ""):
    """What the project already HAS: builder functions and spec tables in its code, its model
    files and its images — on the same shelves as the history, so a game's dinos are findable
    whether or not the forge ever drew them."""
    from .. import assets_index
    return assets_index.list_assets(project, type, subject, q, fresh, root)


@router.get("/generation")
def generation(id: str):
    """One generation in full, including the code — which is what makes it replayable."""
    return engine.generation(id)


@router.get("/sheet")
def sheet(path: str):
    """A rendered sheet, confined to the Studio's data directory."""
    p = engine.sheet_path(path)
    if not p:
        return PlainTextResponse("not a sheet", status_code=404)
    return FileResponse(p, media_type="image/png")


class ThumbsBody(BaseModel):
    project: str
    """The asset ids to render, from GET /api/engine/assets. Only code and spec entries need one:
    a model or a picture is already a picture."""
    ids: list = []
    size: int = 288
    """Most to look at in one call. The body used to have no such field, so the default of 120
    inside `thumbs` silently discarded everything past the 120th id."""
    limit: int = 120


@router.post("/thumbs")
def thumbs(body: ThumbsBody):
    """Render a picture of each named asset, by calling the project's own builders.

    Batched because opening the project's page is the expensive part — forty in one visit cost
    about what one costs alone — and cached on the source file's mtime, so a builder that changes
    invalidates its own picture and nothing else has to.
    """
    from .. import live
    return live.thumbs(body.project, body.ids, body.size, limit=body.limit)


@router.get("/loader")
def loader(project: str, name: str = "GLTFLoader.js", three: str = ""):
    """A glTF loader that shares the project's own three.

    Not the project's copy: most of these games never install three as a package — they vendor one
    file — so there are no examples beside it to load. This is the Studio's copy, rewritten to
    import the project's three, which is the only part that has to match.
    """
    # `three`: the module URL the viewport actually loaded. See `engine.loader_source`.
    got = engine.loader_source(project, name, three)
    if not got:
        return PlainTextResponse("no such loader", status_code=404)
    text, media = got
    return PlainTextResponse(text, media_type=media)


@router.get("/model")
def model(project: str, file: str = "", path: str = ""):
    """A model file with nothing left in it that needs a decoder.

    A game's models are compressed for the game — Draco geometry, meshopt buffers — and every
    decoder is something the ENGINE has to be configured with at startup. The Studio's viewport is
    not that game's startup, so a compressed model arrives as a WebAssembly error about a magic
    word. Decompressing is a property of the file, so it happens once, here, and every renderer on
    the machine can then open it with nothing at all.
    """
    from .. import glb
    src = glb.resolve(project, file) if file else (Path(path) if path else None)
    if not src or not src.is_file():
        return PlainTextResponse("no such model in this project", status_code=404)
    got = glb.plain(str(src))
    if not got.get("ok"):
        return PlainTextResponse(got.get("error") or "could not decode", status_code=422)
    return FileResponse(got["path"], media_type="model/gltf-binary",
                        headers={"x-studio-decoded": "1" if got.get("decoded") else "0"})


@router.get("/source")
def source(project: str = "", path: str = ""):
    """One of the project's own JavaScript files, as a module the page can import.

    THE LAST RESORT, AND THE ONLY ONE FOR A FILE OUTSIDE EVERY GAME. A dev server mounts one root
    and answers its SPA fallback for everything else - measured: a builder beside the games came
    back `200 text/html` from both dev servers and `403` from Vite's own `/@fs/`, which is Vite
    behaving correctly. Nothing but the Studio can serve it, and the Studio already knows which
    directory the project is.

    Confined to the project root, and to files that really are JavaScript. TypeScript is refused
    in words rather than served as broken JavaScript: transpiling is a dev server's job and
    pretending otherwise produces a syntax error a hundred lines in.
    """
    p = Path(path)
    root = Path(project) if project else None
    if not path or not p.is_file():
        return PlainTextResponse("no such file", status_code=404)
    try:
        p = p.resolve()
        if root:
            root = root.resolve()
            p.relative_to(root)
    except (OSError, ValueError):
        return PlainTextResponse("that file is not inside this project", status_code=403)
    ext = p.suffix.lower()
    if ext in (".ts", ".tsx", ".jsx"):
        return PlainTextResponse(
            "the Studio serves files, it does not transpile them. %s needs the game's own dev "
            "server, which strips the types on the way out." % p.name, status_code=415)
    if ext not in (".js", ".mjs"):
        return PlainTextResponse("not a JavaScript module: %s" % p.name, status_code=415)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as ex:
        return PlainTextResponse("could not read it: %s" % ex, status_code=404)
    return PlainTextResponse(text, media_type="text/javascript")


@router.get("/module")
def module(project: str, engine_kind: str = "", file: str = ""):
    """The project's own engine build, so the window renders with what the game renders with.

    `file` fetches a sibling of a split package — three.js since r163 ships `three.module.js`
    plus `three.core.js`. The text comes back with its relative imports rewritten to point at
    this same endpoint, because a bare `./three.core.js` would otherwise resolve against
    `/api/engine/` and 404.
    """
    got = engine.module_source(project, engine_kind, file)
    if not got:
        return PlainTextResponse(
            "no three.js or PlayCanvas build found for this project", status_code=404)
    text, media = got
    return PlainTextResponse(text, media_type=media)


@router.get("/asset-sheet")
def asset_sheet(project: str, root: str = "", type: str = "", q: str = "", ids: str = "",
                limit: int = 24, size: int = 192, fresh: bool = False):
    """The project's assets as ONE labelled, numbered grid PNG, instead of one image each.

    Same selection as /assets (`root` one game of a workspace, `type`, `q`) or exact `ids`
    (comma-separated). Missing thumbnails are rendered through /thumbs's own renderer and cache;
    an asset that could not be drawn is a tile that says why. The answer maps every tile to its
    asset: `cells: [{n, id, name, file, row, col, ok, reason?}]`. Like /thumbs, and unlike the rest
    of this router, this may open the project's page in the headless browser to draw.
    """
    from .. import asset_sheet as _asset_sheet
    return _asset_sheet.sheet(project, root, type, q, ids, limit, size, fresh)

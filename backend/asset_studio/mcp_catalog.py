"""The Studio engine as MCP tools: which tools a session gets, with what arguments.

The HTTP API stays the engine. This module only DESCRIBES it for an MCP client, and the stdio
server (mcp_engine.py) forwards each call to the same endpoint an agent would curl. Three rules:

- ONE SOURCE FOR THE ARGUMENTS. Every tool's input schema is read off the running app's own
  routes — the Pydantic body of a POST, the signature of a GET — so a field added to ForgeBody is
  a field of the `forge` tool with no second list to keep in step.
- THE SAME SWITCHES AS THE NOTES. A tool family is offered exactly when its note would be sent
  (cc_session._forge_on, _live_on, ...). Off in Settings means the agent is not offered the tool,
  as it is not told about the endpoint.
- OPTIONAL, ALWAYS. The instructions say the tools and curl are the same calls, that plain code is
  a fine way to build, and that the modelling library is optional. Nothing here pushes an agent
  toward a tool it did not choose.
"""
from __future__ import annotations

import copy
import inspect
import typing
from typing import Any, Callable, Optional

# name, method, path, family, one-line description. The families are the switches.
_TOOLS: list[tuple[str, str, str, str, str]] = [
    # the forge: build, look, measure — how an agent (and the person watching) SEES its work
    ("forge", "POST", "/api/live/forge", "forge",
     "Build asset code in the lit studio. Answers with findings, numbers and a contact sheet "
     "(numbers:true for numbers only)."),
    ("look", "POST", "/api/live/look", "forge",
     "Photograph what is already on the bench again: other angles, one part, other lights. No rebuild."),
    ("aim", "POST", "/api/live/aim", "forge",
     "Find the camera the reference was taken from: a silhouette sweep, or a solve from anchors. Numbers only."),
    ("bench", "GET", "/api/live/bench", "forge",
     "What the bench holds right now: the code, the camera and the stats."),
    ("forge_clear", "POST", "/api/live/forge/clear", "forge", "Empty the bench."),
    ("glb", "POST", "/api/live/glb", "forge",
     "Put a GLB on the bench, judged with the same camera and numbers as a build."),
    ("compare", "POST", "/api/live/compare", "forge", "Two GLBs, one camera, both scored."),
    ("export", "POST", "/api/live/export", "forge", "Write what the bench holds as a GLB file."),
    ("batch", "POST", "/api/live/batch", "forge",
     "Several forge, look, edit or place calls in ONE request."),
    ("api", "GET", "/api/live/api", "forge",
     "What the modelling library exports (name=Ops: its exact contract) and every field of every "
     "call (name=bodies)."),
    ("terrain", "POST", "/api/live/terrain", "forge",
     "Heightfield ground: make, brush a list of strokes, layers, scatter, look, glb, code."),
    ("animate", "POST", "/api/live/animate", "animate",
     "Pose an animated asset over time on one fixed camera, with the feet measured against the ground."),
    ("debug", "POST", "/api/live/debug", "debugger",
     "Run asset code and stop where it threw, with every local variable and real line numbers."),
    # the live link: the running game
    ("live_open", "POST", "/api/live/open", "live", "Open the project's running game in the Studio's tab."),
    ("live_eval", "POST", "/api/live/eval", "live",
     "Run JavaScript in the running game and get the value back, flattened. It can also set values."),
    ("live_input", "POST", "/api/live/input", "live", "Keys, mouse, wheel, taps and swipes into the running game."),
    ("live_scene", "GET", "/api/live/scene", "live", "The running game's scene tree: materials, lights, cameras."),
    ("live_find", "GET", "/api/live/find", "live", "Where a thing is in the running game, by name."),
    ("live_console", "GET", "/api/live/console", "live", "Errors, warnings and failed loads of the running game."),
    ("live_perf", "GET", "/api/live/perf", "live", "Frame cost, draw calls, triangles, GPU objects."),
    ("live_close", "POST", "/api/live/close", "live", "Close the running game's tab."),
    # scene edit: the running game's objects as tools
    ("objects", "GET", "/api/live/objects", "scene",
     "The running game's objects: key, world box, in view, screen position, source file, code line."),
    ("edit", "POST", "/api/live/edit", "scene",
     "Move, turn, scale, hide or drop an object of the running game; save:true keeps it."),
    ("place", "POST", "/api/live/place", "scene",
     "Place the game's own assets (a builder, a model, a clone, a primitive) in the running game."),
    ("edits", "GET", "/api/live/edits", "scene", "The saved scene edits of the project."),
    ("shot", "POST", "/api/live/shot", "scene", "One framed picture of one thing in the running game."),
    ("watch", "POST", "/api/live/watch", "scene", "Real-time frames of the running game, with findings."),
    # navigate
    ("goto", "POST", "/api/live/goto", "navigate",
     "Put a Studio-owned camera at a place in the running game and look; says what is in view."),
    ("where", "GET", "/api/live/where", "navigate", "Where the Studio camera is, and what is in view."),
    ("locate", "POST", "/api/live/locate", "navigate", "From a pasted screenshot to the camera pose it was taken from."),
    # review
    ("review_render", "POST", "/api/review/render", "review",
     "A contact sheet of a running page on a stepped clock, with findings and metrics."),
    # the code graph and the web
    ("graphify_query", "GET", "/api/graphify/query", "graphify",
     "Where a symbol is declared, what calls it, and the path between two symbols."),
    ("web_fetch", "POST", "/api/web/fetch", "web", "Fetch a page, past bot checks."),
    ("web_search", "GET", "/api/web/search", "web", "Search the web, with no API key."),
]

# An answer that carries one of these (at the top, or under "look") has a picture worth attaching.
PICTURE_KEYS = ("sheet", "detail_sheet", "peak", "shot", "picture", "image")
# The tools whose answers can carry a picture: they take the MCP-only `picture` switch.
PICTURE_TOOLS = {"forge", "look", "glb", "compare", "terrain", "animate", "edit", "place", "shot",
                 "watch", "goto", "locate", "review_render"}
# Arguments the server fills from the session's folder when the call leaves them out.
FOLDER_ARGS = ("project", "root")

INSTRUCTIONS = (
    "STUDIO: the same engine your notes describe, as tools. Each tool is the HTTP call of the same "
    "name: use the tool or curl, whichever you prefer. `project` defaults to this session's folder. "
    "A picture in an answer comes back attached (picture:false leaves it out; numbers:true on forge "
    "or look skips drawing it at all). Plain code is always a fine way to build; the modelling "
    "library is optional, for when it saves you work or you are asked to use it."
)


def _family_on(family: str, cwd: str) -> bool:
    """The note's own switch for each family, so a tool exists exactly when its note would."""
    from . import cc_session as cc
    from . import web_tools
    from .config import settings
    try:
        if family == "forge":
            return cc._forge_on(cwd)
        if family == "animate":
            return cc._animate_on(cwd)
        if family == "debugger":
            return cc._debugger_on(cwd)
        if family == "live":
            return cc._live_on(cwd)
        if family == "scene":
            return cc._scene_on(cwd)
        if family == "navigate":
            return cc._navigate_on(cwd)
        if family == "review":
            return cc._review_on(cwd)
        if family == "graphify":
            return bool(settings.get("cc_graphify"))
        if family == "web":
            return bool(web_tools.enabled())
    except Exception:
        return False
    return False


def _strip(node: Any, defs: dict, depth: int = 0) -> Any:
    """A Pydantic schema made small and self-contained: $refs inlined, titles dropped."""
    if depth > 12:
        return {}
    if isinstance(node, list):
        return [_strip(x, defs, depth + 1) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        target = defs.get(name, {})
        merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
        return _strip(merged, defs, depth + 1)
    out = {}
    for k, v in node.items():
        if k in ("title", "$defs", "definitions"):
            continue
        out[k] = _strip(v, defs, depth + 1)
    return out


_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}


def _type_schema(ann: Any) -> dict:
    """A JSON schema for a plain annotation: str, int, float, bool, dict, list, Optional of those."""
    origin = typing.get_origin(ann)
    if origin is typing.Union or str(origin) in ("types.UnionType", "<class 'types.UnionType'>"):
        inner = [a for a in typing.get_args(ann) if a is not type(None)]
        return _type_schema(inner[0]) if len(inner) == 1 else {}
    if origin in (list, tuple):
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}
    t = _JSON_TYPES.get(ann)
    return {"type": t} if t else {}


def _query_schema(fn: Callable) -> dict:
    """A GET endpoint's query parameters, read off its signature."""
    props: dict = {}
    required: list = []
    # The routers use `from __future__ import annotations`, so a signature holds the STRING 'str';
    # get_type_hints evaluates it back to the type.
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    for name, p in inspect.signature(fn).parameters.items():
        ann = hints.get(name, p.annotation)
        if name in ("request", "response", "background_tasks") or ann is inspect.Parameter.empty and name.startswith("_"):
            continue
        if getattr(ann, "__name__", "") in ("Request", "Response", "BackgroundTasks", "WebSocket"):
            continue
        default = p.default
        # FastAPI's Query(...) object carries the real default.
        if default is not inspect.Parameter.empty and hasattr(default, "default") and type(default).__name__ in ("Query", "FieldInfo", "Param"):
            default = default.default
        sch = _type_schema(ann)
        if default is inspect.Parameter.empty or default is Ellipsis or type(default).__name__ == "PydanticUndefinedType":
            required.append(name)
        elif default is not None and isinstance(default, (str, int, float, bool)):
            sch["default"] = default
        props[name] = sch
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


def _body_model(body_field: Any) -> Any:
    """The Pydantic model of a route's body, whichever FastAPI holds it.

    0.115 (this PC) names it `body_field.type_`; 0.141 (a new PC's wheels) dropped `type_` and
    keeps it at `body_field.field_info.annotation`. Read the old way only, every POST tool of the
    installed Studio had an empty schema, so nothing filled `project` and each call was a 422."""
    if body_field is None:
        return None
    for get in (lambda b: getattr(b, "type_", None),
                lambda b: getattr(getattr(b, "field_info", None), "annotation", None),
                lambda b: getattr(b, "annotation", None)):
        try:
            model = get(body_field)
        except Exception:
            model = None
        if model is not None and hasattr(model, "model_json_schema"):
            return model
    return None


def _body_schema(model: Any) -> dict:
    """A POST endpoint's body model as a self-contained schema."""
    try:
        raw = model.model_json_schema()
    except Exception:
        return {"type": "object", "additionalProperties": True}
    defs = raw.get("$defs") or raw.get("definitions") or {}
    sch = _strip(raw, defs)
    sch["type"] = "object"
    return sch


def _route_index(app: Any) -> dict:
    """(METHOD, path) -> APIRoute, for every route the app serves.

    NESTED ROUTERS ARE WALKED. FastAPI up to 0.11x copies every included router's routes into
    app.routes. 0.141 - the version a new PC's wheels bring - keeps ONE `_IncludedRouter` per
    include_router call instead: the router itself in `original_router`, any extra prefix in
    `include_context`. Read flat, that app had 2 routes and the installed Studio offered 0 tools
    while this PC offered 32. Found by installing the release into an empty profile (2026-09-25)."""
    from fastapi.routing import APIRoute
    idx: dict = {}

    def walk(routes, prefix: str, depth: int) -> None:
        if depth > 8:
            return
        for r in routes or ():
            if isinstance(r, APIRoute):
                for m in r.methods or ():
                    idx.setdefault((m.upper(), prefix + r.path), r)
                continue
            inner = getattr(r, "original_router", None)
            if inner is not None:
                ctx = getattr(r, "include_context", None)
                walk(getattr(inner, "routes", None), prefix + str(getattr(ctx, "prefix", "") or ""),
                     depth + 1)

    walk(getattr(app, "routes", None), "", 0)
    return idx


def catalog(app: Any, cwd: str = "") -> dict:
    """Every tool this session may be offered, with its schema; and the instructions."""
    idx = _route_index(app)
    tools: list = []
    for name, method, path, family, desc in _TOOLS:
        if not _family_on(family, cwd):
            continue
        r = idx.get((method, path))
        if r is None:
            continue
        if method == "GET":
            schema = _query_schema(r.endpoint)
        else:
            model = _body_model(getattr(r, "body_field", None))
            schema = _body_schema(model) if model is not None else {"type": "object", "properties": {}}
        props = schema.setdefault("properties", {})
        # `project` (and the graph's `root`) are filled in from the session's folder by the server
        # when a call leaves them out, so they are never required here.
        fills = [a for a in FOLDER_ARGS if a in props]
        if fills:
            schema["required"] = [x for x in (schema.get("required") or []) if x not in fills]
            if not schema["required"]:
                schema.pop("required", None)
            for a in fills:
                props[a].setdefault("description", "Absolute path; default: this session's folder.")
        if name in PICTURE_TOOLS:
            props["picture"] = {"type": "boolean", "default": True,
                                "description": "Attach the answer's main picture to the result. false: paths and numbers only."}
        tools.append({"name": name, "description": desc, "method": method, "path": path,
                      "family": family, "inputSchema": schema, "fills": fills,
                      "picture": name in PICTURE_TOOLS})
    return {"ok": True, "instructions": INSTRUCTIONS, "tools": tools}

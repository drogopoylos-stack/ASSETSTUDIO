"""The live game, over HTTP.

Same contract as /api/graphify/query and /api/web/fetch: one curl, a compact JSON answer, nothing
to install in the project. An agent does not need to know that a headless Chrome is involved,
only that it can ask the running game a question and get a real value back.

`eval` is the whole feature and the rest are shortcuts. `scene`, `find`, `perf` and `console`
exist because writing that traversal by hand is where an agent burns three round trips and still
gets a circular-structure error.
"""
from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from .. import live

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("/status")
def status():
    return live.status()


class OpenBody(BaseModel):
    project: str
    url: str = ""
    device: str = ""          # desktop | phone | tablet
    width: int = 0
    height: int = 0
    reload: bool = False
    start_dev: bool = True
    wait_ms: int = 1500


@router.post("/open")
def open_(body: OpenBody):
    """Put the game in a tab and keep it there. Prefers the project's dev server, so edits show."""
    return live.open_(body.project, body.url, body.device, body.width, body.height,
                      body.reload, body.start_dev, body.wait_ms)


class EvalBody(BaseModel):
    project: str
    js: str
    depth: int = 6


@router.post("/eval")
def evaluate(body: EvalBody):
    """Run it in the live game and bring the value back — including what the value is made of."""
    return live.evaluate(body.project, body.js, body.depth)


@router.get("/scene")
def scene(project: str, depth: int = 3, wide: int = 40, root: str = "", index: int = 0):
    return live.scene(project, depth, wide, root, index)


@router.get("/find")
def find(project: str, q: str, limit: int = 25, code: bool = False):
    return live.find(project, q, limit, code)


@router.get("/console")
def console(project: str, level: str = "all", drain: bool = True):
    return live.console(project, level, drain)


@router.get("/perf")
def perf(project: str, ms: int = 1500):
    return live.perf(project, ms)


class InputBody(BaseModel):
    project: str
    events: list


@router.post("/input")
def send_input(body: InputBody):
    return live.send_input(body.project, body.events)


class ShotBody(BaseModel):
    project: str
    path: str = ""
    full: bool = False
    # A CAMERA, when a thing is named. Left out, this is exactly the shot it always was. Given: a key,
    # a name or a path (as /api/live/objects hands out), a Studio camera framed on its world box from
    # `angle` ("3q" | "front" | "side" | "back" | "top" relative to the game camera's heading, or
    # "player" for the game's own camera), the box drawn in, and the view handed back to the game.
    target: str = ""
    angle: str = ""
    distance: Optional[float] = None
    box: bool = True
    # [width, height] of the picture; the frame is scaled to cover it and cut from the middle.
    size: Optional[list] = None
    scene: Optional[int] = None


@router.post("/shot")
def shot(body: ShotBody):
    """One frame, now — of the game's own view, or of one thing through a Studio camera.

    Both answer `stale` when the page is running code older than the files on disk."""
    from .. import live_view
    return live_view.shot(body.project, body.path, body.full, body.target, body.angle, body.distance,
                          body.box, body.size, body.scene)


class WatchBody(BaseModel):
    project: str
    # Real time, not a frozen clock: the frames of the agent's own live tab, unsaved tries included.
    seconds: float = 2.0            # 0.2 .. 10
    frames: int = 6                 # 2 .. 16
    # Frame the camera on this first (key | name | path), from `angle` as /shot takes it.
    target: str = ""
    angle: str = ""
    # Events in the shape /api/live/input takes, sent `at_ms` after the first frame.
    input: list = []
    at_ms: int = 0
    scene: Optional[int] = None


@router.post("/watch")
def watch(body: WatchBody):
    """A short strip of the live game over real time: press a key, watch what it does.

    One sheet with `t=` on every frame, how much each frame changed from the last, and findings in
    words ("STILL: nothing moved", "moved in the box x .40–.52, y .31–.60 from t=0.40 s")."""
    from .. import live_view
    return live_view.watch(body.project, body.seconds, body.frames, body.target, body.angle,
                           body.input, body.at_ms, body.scene)


class PickBody(BaseModel):
    project: str
    x: float = 0.5           # 0..1 of the frame, never pixels — the frame is scaled twice
    y: float = 0.5
    pad: int = 8
    shot: bool = True


@router.post("/pick")
def pick(body: PickBody):
    """What is under that point of the running page: the element, its CSS, and a picture of it.

    The Studio's own Inspect tab calls this on a click. An agent can call it too — it is the
    cheapest way to ask "what actually renders here", because the page answers rather than the
    source being guessed at.
    """
    from .. import live_pick
    return live_pick.pick(body.project, body.x, body.y, body.pad, body.shot)


class ForgeBody(BaseModel):
    project: str
    js: str
    views: list = ["3q"]      # 3q front back side left top bottom low hero back3q
    width: int = 0              # 0 / "": the session's last size and backdrop (640x480, #1a1e26 at first)
    height: int = 0
    background: str = ""
    ground: bool = False
    engine: str = ""          # "" = whichever the page already loaded
    clear: bool = True
    margin: float = 0.0          # 0: the Settings value (1.06), the same framing look uses
    label: str = ""
    quality: str = ""
    sky: bool = False
    # A procedural asset IS its parameters: render the neighbours side by side and the choice is
    # compared instead of guessed. Each entry lands in `params` for that run.
    variants: list = []
    # N steps around the subject, one framing. Judges form, which four fixed angles do not.
    turntable: int = 0
    # "silhouette" | "wireframe" | "normals". Silhouette is the readability test a game asset
    # lives or dies by; wireframe shows the topology a triangle count only hints at.
    passes: list = []
    # The target image. Sent ONCE per project and remembered, so every later sheet carries it as
    # the first panel — checking a render against a picture read twenty tool calls ago is checking
    # it against a memory.
    ref: str = ""
    # Part or material names to frame the camera on, instead of the whole subject. Every ordinary
    # view fits the entire asset, which prices small features down to a few pixels and out of the
    # review entirely; `findings` names the ones that happened to.
    focus: list = []
    # DETAIL REVIEW: parts photographed close up from the reference's angle, 40 degrees round, the
    # side and the back, each beside the same crop of the reference and checked as built, upside
    # down and mirrored. A list names the parts; true pictures the automatic ones; false turns it
    # off. Left out, a character with a reference gets it by itself (Settings → Studio engine).
    detail: Optional[Union[bool, str, list]] = None
    # The angles, when the four above are not the right ones.
    detail_views: list = []
    # THE STUDIO'S LOOK. `ortho` for a turnaround reference, which is orthographic - a
    # perspective render of the same model is a different picture and scores against a different
    # projection. `light` is a preset: "studio" (the default rig), "flat" (even, for reading
    # colour), "reference" (near-black stays near-black, a little environment for reflections),
    # "hard". `env`, `ambient` and `exposure` are multipliers; `lights` takes
    # {"key":1,"fill":0.5,"rim":1.2}. What a call leaves out stays as this project had it.
    ortho: Optional[bool] = None
    light: str = ""
    env: Optional[float] = None
    ambient: Optional[float] = None
    exposure: Optional[float] = None
    lights: Optional[dict] = None
    # A shadow-casting key light. Off by default: it costs a second pass, and most
    # of what the bench is asked is a silhouette or a colour. On, it is what makes
    # the parts of a mass read as separate instead of as one shape.
    shadows: Optional[bool] = None
    # The reference, when its own colours cannot be separated from its backdrop or when it is a
    # SHEET of several figures: a mask picture (white where the subject is), and a crop -
    # "x0,y0,x1,y1" in 0..1 of the picture or in pixels. Both are remembered with the reference.
    ref_mask: str = ""
    ref_crop: str = ""

    # The shelves in the Engine window: what the asset depicts (character, creature, building, prop,
    # vehicle, environment, weapon, ui, effect) and any other words worth finding it by later.
    tags: list = []
    category: str = ""
    # Numbers and no picture. Everything the sheet cannot say - what is too small to have been
    # judged, what the asset stands on, how many surfaces it is built from, and whether it matches
    # the reference - for a few hundred tokens instead of a few thousand. Measure after every
    # edit; spend a look only when the numbers stop moving.
    numbers: bool = False
    # Move what the code built, without rebuilding it and without writing anything:
    #   [{"target":"wing","rotate":[0,-20,0]}, {"target":"head","move":[0,0.1,0]},
    #    {"target":"tail","scale":1.2}, {"target":"backSpike","visible":false}]
    # The answer echoes the transform each target ended up with, to be written into the builder.
    edits: list = []


@router.post("/forge")
def forge(body: ForgeBody):
    """Run code that makes an asset, in a lit studio, and get a picture of it back.

    The point is the loop, not the endpoint: write, look, change, look. It builds with the
    project's own engine and `import()` works, so an agent can preview the game's real asset
    functions instead of a copy.
    """
    return live.forge(body.project, body.js, body.views, body.width, body.height,
                      body.background, body.ground, body.engine, body.clear, body.margin,
                      body.label, body.quality, body.sky,
                      body.variants, body.turntable, body.passes, body.ref, body.focus,
                      body.tags, body.category, body.numbers, body.edits,
                      detail=body.detail, detail_views=body.detail_views,
                      ortho=body.ortho, light=body.light, env=body.env, ambient=body.ambient,
                      exposure=body.exposure, lights=body.lights, shadows=body.shadows, ref_mask=body.ref_mask,
                      ref_crop=body.ref_crop)


class LookBody(BaseModel):
    project: str
    # The same view with the key light in N places (2..5). A finish cannot be judged under
    # one fixed lamp: three attempts at gloss on the old bench changed nothing measurable.
    sweep: int = 0
    # Any set of angles: presets, or `az=..,el=..,zoom=..`. With none given the camera stays where
    # it is and `orbit` / `zoom` move it from there.
    views: list = []
    # [dAz, dEl] in degrees, ADDED to wherever the camera is now. "A bit further round" needs no
    # arithmetic and no memory of which preset the last call used.
    orbit: list = []
    zoom: float = 0.0
    focus: list = []
    turntable: int = 0
    passes: list = []
    # Move parts before shooting, exactly as `forge` does. Nothing is written to the file.
    # `"reset"` instead of a list puts every edited node back to what the builder made — edits
    # used to persist for the life of the bench with nothing able to undo them.
    edits: Union[list, str] = []
    # The panel size, WITHOUT losing the scene. `forge` rebuilds on a size change; this does not.
    width: int = 0
    height: int = 0
    # One bare PNG per view, beside the composed sheet, so nothing has to be cropped back out.
    frames: bool = False
    # The REFERENCE, as numbers: where the subject sits in it, its width at sixteen heights, and
    # its tone percentiles. Off by default because it is only interesting while matching one.
    facts: bool = False
    margin: float = 0.0
    quality: str = ""
    label: str = ""
    ref: str = ""
    numbers: bool = False
    tags: list = []
    category: str = ""
    # See ForgeBody.detail.
    detail: Optional[Union[bool, str, list]] = None
    detail_views: list = []
    # THE STUDIO'S LOOK. `ortho` for a turnaround reference, which is orthographic - a
    # perspective render of the same model is a different picture and scores against a different
    # projection. `light` is a preset: "studio" (the default rig), "flat" (even, for reading
    # colour), "reference" (near-black stays near-black, a little environment for reflections),
    # "hard". `env`, `ambient` and `exposure` are multipliers; `lights` takes
    # {"key":1,"fill":0.5,"rim":1.2}. What a call leaves out stays as this project had it.
    ortho: Optional[bool] = None
    light: str = ""
    env: Optional[float] = None
    ambient: Optional[float] = None
    exposure: Optional[float] = None
    lights: Optional[dict] = None
    # A shadow-casting key light. Off by default: it costs a second pass, and most
    # of what the bench is asked is a silhouette or a colour. On, it is what makes
    # the parts of a mass read as separate instead of as one shape.
    shadows: Optional[bool] = None
    # The reference, when its own colours cannot be separated from its backdrop or when it is a
    # SHEET of several figures: a mask picture (white where the subject is), and a crop -
    # "x0,y0,x1,y1" in 0..1 of the picture or in pixels. Both are remembered with the reference.
    ref_mask: str = ""
    ref_crop: str = ""
    # CAMERA FROM THE REFERENCE: [{"at": [x, y, z] | {"part": "helmet", "where": "top"}, "px": [x, y]}, ...],
    # px in the reference's own pixels, three or more. The camera is SOLVED from them and `snap` says
    # how far each part must move to land on its pixel ("solve": false on an anchor = snap it only).
    anchors: list = []



@router.post("/look")
def look(body: LookBody):
    """Photograph what is already built, from anywhere, without building it again.

    `forge` clears and re-runs on every call, so a second angle used to cost a second build. This
    reuses the scene the page is already holding: front, side, back, three-quarter and a close-up
    of one part, in about the time one build takes.
    """
    return live.look(body.project, body.views, body.orbit, body.zoom, body.focus, body.edits,
                     body.turntable, body.passes, body.margin, body.quality, body.label,
                     body.ref, body.numbers, body.tags, body.category,
                     detail=body.detail, detail_views=body.detail_views,
                     ortho=body.ortho, light=body.light, env=body.env, ambient=body.ambient,
                     exposure=body.exposure, lights=body.lights, shadows=body.shadows, ref_mask=body.ref_mask,
                     ref_crop=body.ref_crop, sweep=body.sweep,
                     width=body.width, height=body.height, frames=body.frames,
                     facts=body.facts, anchors=body.anchors or None)


class PicturesBody(BaseModel):
    project: str = ""
    limit: int = 40
    size: int = 384


@router.post("/pictures")
def pictures(body: PicturesBody):
    """Draw and keep a picture for every recorded run that has code but no sheet.

    The cheap half of the loop measures without spending a sheet, which is why it exists — but it
    left a run in the history with nothing to show. The code was always kept, so the picture can
    be taken afterwards.
    """
    return live.pictures(body.project, body.limit, body.size)


class GotoBody(BaseModel):
    project: str
    # One of these says where. `name` finds a thing by name; `at` is a camera position; `look_at`
    # frames a point. `step` is [right, up, forward] in metres from where the camera is;
    # `orbit` is [dYaw, dPitch] in degrees around the last target. `pose` is "last" or "game".
    name: str = ""
    at: list = []
    look_at: list = []
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    distance: Optional[float] = None
    fov: Optional[float] = None
    step: list = []
    orbit: list = []
    pose: str = ""
    release: bool = False
    limit: int = 12


@router.post("/goto")
def goto(body: GotoBody):
    """Put the Studio's camera somewhere in the running game, and hand back the picture."""
    from .. import live_navigate
    opts = {k: v for k, v in body.model_dump().items()
            if k != "project" and v not in (None, "", [], False)}
    if body.release:
        opts["release"] = True
    return live_navigate.goto(body.project, opts)


@router.get("/where")
def where(project: str, limit: int = 12):
    """Where the camera is, and the named things in view — without moving."""
    from .. import live_navigate
    return live_navigate.where(project, limit)


class LocateBody(BaseModel):
    project: str
    image: str
    hint: str = ""
    names: list = []
    max_candidates: int = 40
    refine: bool = True
    keep: int = 3


@router.post("/locate")
def locate(body: LocateBody):
    """From a screenshot to a camera pose: candidates rendered and scored against it."""
    from .. import live_navigate
    return live_navigate.locate(body.project, body.image, body.hint, body.names,
                                body.max_candidates, body.refine, body.keep)


class AnimateBody(BaseModel):
    project: str
    # Build it, or leave empty to animate whatever is already on the bench.
    js: str = ""
    # "auto" finds an AnimationMixer clip or an update function; "fn" runs your own expression
    # with `t` in scope; "mixer" and "call" force one of the two.
    drive: str = "auto"
    fn: str = ""
    clip: str = ""
    # THE ONE THING THAT IS ASKED RATHER THAN GUESSED. Half of all update functions take the time
    # since the last frame and half take the time since the start; passing one to the other does
    # not throw, it produces a plausible wrong animation.
    mode: str = "absolute"
    step: float = 0.0
    duration: float = 0.0
    times: list = []
    frames: int = 0
    view: str = "side"
    ground: float = 0.0
    track: list = []
    margin: float = 1.15
    label: str = ""
    quality: str = ""
    numbers: bool = False
    engine: str = ""
    width: int = 0
    height: int = 0
    params: dict = {}


@router.post("/animate")
def animate(body: AnimateBody):
    """Run the cycle, photograph it on ONE fixed camera, and report the feet as numbers.

    A gait bug does not live in a still. A foot that slides while it is planted, or floats a
    centimetre above the floor, is correct-looking in every single frame and from every angle —
    it is only visible across time against a ground that does not move. So the camera is pinned
    over the whole sequence, the floor is pinned where you say it is, and the findings name the
    foot, the frame and the distance before you open the picture.
    """
    from .. import live_animate
    return live_animate.animate(
        body.project, body.js, body.drive, body.fn, body.clip, body.mode, body.step,
        body.duration, body.times, body.frames, body.view, body.ground, body.track,
        body.margin, body.label, body.quality, body.numbers, body.engine,
        body.width, body.height, body.params or None)


class BatchBody(BaseModel):
    project: str
    # Each entry is {"op": "forge"|"look"|"aim"|"eval"|"scene"|"find"|"console"|"perf"|"bench"|
    # "pictures", ...that op's own arguments}. `project` is given once, never per call.
    calls: list
    stop_on_error: bool = True


@router.post("/batch")
def batch(body: BatchBody):
    """Several calls in one request, in order, on the same warm tab.

    Build a part, measure it, nudge it, look — four HTTP calls is four round trips through the
    model, and that loop runs dozens of times per asset. This is the same work in one.
    """
    return live.batch(body.project, body.calls, body.stop_on_error)


class DebugBody(BaseModel):
    project: str
    js: str
    # "uncaught" stops where it threw; "all" stops on caught throws too; "none" leaves only the
    # breakpoints armed.
    pause_on: str = "uncaught"
    # 1-based line numbers in YOUR OWN code.
    lines: list = []
    steps: int = 0
    depth: int = 1
    params: dict = {}


@router.post("/debug")
def debug(body: DebugBody):
    """Stop where the code went wrong and read the variables that were in scope.

    Godot's MCP is the only one of the three editor integrations with a debugger, and it needs the
    Godot editor open. Ours needs nothing: the asset code already runs in a Chrome we drive, and
    the whole stop-and-inspect happens inside this one request. The page is never left paused.
    """
    from .. import live_debug
    return live_debug.debug(body.project, body.js, body.pause_on, body.lines,
                            body.steps, body.depth, body.params or None)


@router.get("/api")
def api_reflect(q: str = "", name: str = ""):
    """What the modelling library actually exports, read out of the source.

    Unity's MCP has `unity_reflect` and it is the right idea: an agent should be able to ask what
    a module exposes rather than guess and find out when it throws. Ours matters more, because
    `forge-ops.js` is imported into code the agent is writing blind.

    Cheap by default — names only, grouped. `?name=Ops` gives the exact contract of
    `makeOps(THREE)`; `?q=<word>` searches names and docs. Every answer is parsed from the .ts
    source and cross-checked against the built bundle, so it cannot advertise a function that was
    never shipped.
    """
    from .. import apireflect
    return apireflect.reflect(q=q, name=name)


@router.get("/bench")
def bench(project: str = ""):
    """What the forge page is holding right now: the code, a signature for it, and the camera.

    This is what a window follows to watch an agent work. It describes a live browser, so it is
    never read from disk and `open` says whether the tab is still there.
    """
    return live.bench(project)


class AimBody(BaseModel):
    project: str
    """Leave it out to aim at the bench that is already standing. `look` has always worked that
    way; `aim` used to demand the code a second time to point a camera at what it had just
    built."""
    js: str = ""
    # The reference, as numbers, beside the winning angle.
    facts: bool = False
    # How many azimuths to try, and at which elevations. The default sweep is 36 angles, which is
    # about seven seconds and costs no image tokens at all.
    steps: int = 12
    els: list = []
    # Score exactly these angles instead of sweeping. This is how a coarse sweep is refined.
    views: list = []
    ref: str = ""
    width: int = 420
    height: int = 420
    engine: str = ""
    # THE STUDIO'S LOOK. `ortho` for a turnaround reference, which is orthographic - a
    # perspective render of the same model is a different picture and scores against a different
    # projection. `light` is a preset: "studio" (the default rig), "flat" (even, for reading
    # colour), "reference" (near-black stays near-black, a little environment for reflections),
    # "hard". `env`, `ambient` and `exposure` are multipliers; `lights` takes
    # {"key":1,"fill":0.5,"rim":1.2}. What a call leaves out stays as this project had it.
    ortho: Optional[bool] = None
    light: str = ""
    env: Optional[float] = None
    ambient: Optional[float] = None
    exposure: Optional[float] = None
    lights: Optional[dict] = None
    # A shadow-casting key light. Off by default: it costs a second pass, and most
    # of what the bench is asked is a silhouette or a colour. On, it is what makes
    # the parts of a mass read as separate instead of as one shape.
    shadows: Optional[bool] = None
    # The reference, when its own colours cannot be separated from its backdrop or when it is a
    # SHEET of several figures: a mask picture (white where the subject is), and a crop -
    # "x0,y0,x1,y1" in 0..1 of the picture or in pixels. Both are remembered with the reference.
    ref_mask: str = ""
    ref_crop: str = ""
    # CAMERA FROM THE REFERENCE: [{"at": [x, y, z] | {"part": "helmet", "where": "top"}, "px": [x, y]}, ...],
    # px in the reference's own pixels, three or more. The camera is SOLVED from them and `snap` says
    # how far each part must move to land on its pixel ("solve": false on an anchor = snap it only).
    anchors: list = []



@router.post("/aim")
def aim(body: AimBody):
    """Find the camera angle whose silhouette matches the reference best.

    A camera angle is a search, and a search is work for a machine. This renders the subject's
    silhouette from every angle in the sweep, scores each against the reference picture, and
    answers with the winner as a number. No image comes back, so nothing is charged for looking.
    """
    return live.aim(body.project, body.js, body.steps, body.els, body.views, body.ref,
                    body.width, body.height, body.engine, ortho=body.ortho, light=body.light,
                    env=body.env, ambient=body.ambient, exposure=body.exposure,
                    lights=body.lights, shadows=body.shadows, ref_mask=body.ref_mask,
                    ref_crop=body.ref_crop, facts=body.facts, anchors=body.anchors or None)


class ForgeClearBody(BaseModel):
    project: str
    dispose: bool = False


@router.post("/forge/clear")
def forge_clear(body: ForgeClearBody):
    return live.forge_clear(body.project, body.dispose)


class GlbBody(BaseModel):
    project: str
    # A .glb on disk. It is served to the page by the Studio, decompressed on the way.
    path: str
    name: str = ""
    clear: bool = True
    width: int = 0              # 0 / "": the session's last size and backdrop (640x480, #1a1e26 at first)
    height: int = 0
    background: str = ""
    engine: str = ""
    ortho: Optional[bool] = None
    light: str = ""
    env: Optional[float] = None
    ambient: Optional[float] = None
    exposure: Optional[float] = None
    lights: Optional[dict] = None
    # A shadow-casting key light. Off by default: it costs a second pass, and most
    # of what the bench is asked is a silhouette or a colour. On, it is what makes
    # the parts of a mass read as separate instead of as one shape.
    shadows: Optional[bool] = None


@router.post("/glb")
def glb(body: GlbBody):
    """Put a GLB on the bench: an asset from Blender or a store, judged with the same camera."""
    return live.glb(body.project, body.path, body.name, body.clear, body.width, body.height,
                    body.background, body.engine, ortho=body.ortho, light=body.light,
                    env=body.env, ambient=body.ambient, exposure=body.exposure, lights=body.lights, shadows=body.shadows)


class ExportBody(BaseModel):
    project: str
    path: str


@router.post("/export")
def export_glb(body: ExportBody):
    """Write what the bench holds to a .glb file."""
    return live.export_glb(body.project, body.path)


class CompareBody(BaseModel):
    project: str
    # Two .glb files. Leave `a` out to compare what is already on the bench against `b`.
    a: str = ""
    b: str
    labels: list = []
    views: list = ["front"]
    ref: str = ""
    width: int = 0              # 0 / "": the session's last size and backdrop (640x480, #1a1e26 at first)
    height: int = 0
    background: str = ""
    engine: str = ""
    margin: float = 0.0
    label: str = ""
    quality: str = ""
    tags: list = []
    ortho: Optional[bool] = None
    light: str = ""
    env: Optional[float] = None
    ambient: Optional[float] = None
    exposure: Optional[float] = None
    lights: Optional[dict] = None
    # A shadow-casting key light. Off by default: it costs a second pass, and most
    # of what the bench is asked is a silhouette or a colour. On, it is what makes
    # the parts of a mass read as separate instead of as one shape.
    shadows: Optional[bool] = None
    ref_mask: str = ""
    ref_crop: str = ""
    # A part's name: both assets framed on that part with ONE camera, a close-up of the same place
    # on each. The close-up is not scored against the reference.
    focus: str = ""


@router.post("/compare")
def compare(body: CompareBody):
    """Two assets, one camera, one reference, both scored — the A/B nobody should compose by hand."""
    return live.compare(body.project, body.a, body.b, body.labels, body.views, body.ref,
                        body.width, body.height, body.background, body.engine, body.margin,
                        body.label, body.quality, body.tags, ortho=body.ortho, light=body.light,
                        env=body.env, ambient=body.ambient, exposure=body.exposure,
                        lights=body.lights, shadows=body.shadows, ref_mask=body.ref_mask, ref_crop=body.ref_crop,
                        focus=body.focus)


class CloseBody(BaseModel):
    project: str = ""


@router.post("/close")
def close(body: CloseBody):
    return live.close(body.project)


class TerrainBody(BaseModel):
    # EVERY KEY REACHES THE MODULE, DECLARED OR NOT.
    #
    # Pydantic drops what it has no field for, silently. `plan` was built, tested and shipped
    # unable to work, because `goal` was thrown away between the request and the function - and
    # its error message had to name this endpoint's own body model to stop the next person
    # debugging the search. `flow`, `channel`, `whole` and `tiles` sat in exactly the same
    # position and only looked fine because their defaults happened to be right.
    #
    # The actions here are a growing table that teaches itself (`action: "help"` prints the
    # shapes), so the body cannot be a fixed list of every argument every action will ever take.
    model_config = ConfigDict(extra="allow")

    project: str
    # plan: {"walkable":0.7,"maxSlope":30,"relief":[8,40],"coverage":{"grass":0.6},
    #        "region":{...},"budget":24,"ms":4000}. Declared as well as allowed, so it is in
    # `/api/live/api?name=bodies` where somebody will find it.
    goal: dict = {}
    # make | brush | layers | scatter | report | look | glb | code | save | load | undo | clear.
    # A wrong one, or "help", answers with the table of shapes. The endpoint teaches itself, which
    # is why it costs the agent note two lines instead of twenty.
    action: str = "report"
    # make: world units across, samples per side (2^n+1, snapped if not), what a stored 1.0 means.
    size: float = 0
    res: int = 0
    maxHeight: float = 0
    seed: int = 0
    origin: list = []
    # brush: A LIST, ALWAYS. One stroke per request is one turn of the model per stroke, and a
    # valley is a dozen strokes. Each is {kind,x,z,radius,strength,falloff,seconds} plus `to`
    # ([x,z], with `steps`) to drag a line, `target` for flatten, `layer` for paint, and
    # `asset`/`density` for scatter.
    strokes: list = []
    # scatter by hand: [{asset,x,z,rot,scale}], y snapped to the ground. Painting them is a brush.
    items: list = []
    # up to four, by index or in order: {index,name,colour,texture,tiling}
    layers: list = []
    # report: the slope, in degrees, above which the ground is not walkable.
    maxSlope: float = 30.0
    # look: the forge's own presets and angle specs, so the picture matches every other one.
    views: list = []
    margin: float = 0.0
    quality: str = ""
    label: str = ""
    tags: list = []
    category: str = ""
    # glb, code, save, load. `path` is resolved inside the project and refused outside it.
    path: str = ""
    name: str = ""
    engine: str = ""
    # Skip the preview mesh, when a hundred strokes are going in and only the last needs drawing.
    draw: Optional[bool] = None
    # Samples skipped when drawing. Left out, it is chosen from the resolution: a 1025-sample
    # field is 2.1M triangles and rebuilding that on every stroke costs a second for a picture
    # nobody can read.
    lod: int = 0
    width: int = 0
    height: int = 0
    background: str = ""


@router.post("/terrain")
def terrain(body: TerrainBody):
    """Sculpt the ground from the command line, and read it back as numbers.

    Unity, Blender and Godot all have a terrain brush and all three need a person holding a
    mouse. This one answers with the report — slope histogram, per-layer coverage, walkable
    share, what the scatter is standing on — and takes a picture only when one is asked for, so
    an agent can choose the next stroke from the numbers instead of from a screenshot.

    The maths is not here and not in Python: it is the one implementation in terrain.ts, imported
    into the forge's page, which is the same code the editor's Terrain mode runs.
    """
    from .. import live_terrain
    return live_terrain.terrain(body.project, body.model_dump())


# ---------------------------------------------------------------------------------------- the scene API
# The running game's own objects as an agent's tools: list them with the key a saved edit uses,
# move them, keep the move, undo it. The module is live_scene.py; nothing here but the bodies.

class SceneEditBody(BaseModel):
    # EVERY KEY REACHES THE MODULE, as in TerrainBody: a field it does not know comes back as
    # `ignored` instead of silently doing nothing.
    model_config = ConfigDict(extra="allow")

    project: str
    # A key from /api/live/objects (exact), a name only one object has, or the path a row gives.
    target: str = ""
    # Absolute LOCAL position, relative to the parent; with `world: true`, a WORLD position.
    pos: Optional[list] = None
    # A WORLD delta, in metres.
    move: Optional[list] = None
    # Absolute local XYZ euler in DEGREES; `rotate` adds degrees to it. The file stores radians.
    rot: Optional[list] = None
    rotate: Optional[list] = None
    # Absolute local scale: one number for all three axes, or [x, y, z].
    scale: Optional[Union[float, list]] = None
    visible: Optional[bool] = None
    # Rest it on whatever is under it, after the rest of the edit.
    drop: bool = False
    world: bool = False
    # Write the new LOCAL values into parts[key] of <project>/studio.edits.json. Off: only a try.
    save: bool = False
    # Several edits in one call, each shaped like the fields above; one undo covers them all.
    edits: list = []
    # Put the last call back, live and on disk.
    undo: bool = False
    # Take saved entries out of the file: a list of keys, or "all". The running game keeps them
    # until it reloads.
    clear: Optional[Union[list, str]] = None
    # Code hints for what was edited: on when they are installed, false turns them off.
    code: Optional[bool] = None
    # Which three.js scene, on a page with several. Left out: the one drawn to the canvas.
    scene: Optional[int] = None
    # THE PICTURE OF THE CHANGE: a before|after sheet from one Studio camera framed on what moved.
    # true | "3q" | "front" | "side" | "back" | "top" | "player" | false. Left out: one edit follows
    # Settings → scene_look (on); `edits:[…]` and /api/live/batch take one only when asked.
    look: Optional[Union[bool, str]] = None


@router.get("/objects")
def scene_objects(project: str, q: str = "", near: str = "", radius: Optional[float] = None,
                  in_view: bool = False, pick: str = "", limit: int = 60, code: bool = False,
                  scene: Optional[int] = None):
    """The scene as rows: the key a saved edit uses, world bounds, in view or not, where on screen.

    `pick=x,y` (0..1 of the view) names the object under that point and puts its chain first.
    `near=x,y,z` (or a key) with `radius` keeps what is within that many metres of it.
    """
    from .. import live_scene
    return live_scene.objects(project, q, near, radius, in_view, pick, limit, code, scene)


@router.post("/edit")
def scene_edit(body: SceneEditBody):
    """Move, turn, scale, hide or drop an object in the running game; `save` keeps it, `undo` takes
    the last call back, `clear` takes saved entries out of the file. Rotations are degrees."""
    from .. import live_scene
    return live_scene.edit(body.project, body.model_dump())


@router.get("/edits")
def scene_edits(project: str):
    """The saved edits file, summarised: what is moved, hidden and placed; rotations in degrees."""
    from .. import live_scene
    return live_scene.edits(project)


@router.get("/edits/file")
def scene_edits_file(project: str):
    """`<project>/studio.edits.json` itself, for the page to apply. 404 when there is none."""
    from fastapi.responses import JSONResponse
    from .. import live_scene
    status, doc = live_scene.edits_file(project)
    return JSONResponse(status_code=status, content=doc)


class ScenePlaceBody(BaseModel):
    # As SceneEditBody: every key reaches the module, and one it does not know comes back as `ignored`.
    model_config = ConfigDict(extra="allow")

    project: str
    # What to put in: {"builder": "src/assets.js#buildCrystal", "args": [...]}, {"model": "<project-relative
    # .glb>"}, {"clone": "<key>"}, {"primitive": "box", "color": "#ff8800"}, {"id": "<asset id from
    # /api/engine/assets>"} — or the same as one string ("src/assets.js#buildCrystal", "x.glb", "box", a key).
    asset: Optional[Union[dict, str]] = None
    # Its name, which is its key: made unique if taken (crystal-1..5 make the next crystal-6).
    name: str = ""
    # Its id in `placed`. The same id again REPLACES that placement. Left out: a new one.
    id: str = ""
    # Where the middle of its BOTTOM goes, in world metres.
    at: Optional[list] = None
    # Beside this object (key, name or path), as the camera sees it, standing on what is there.
    near: str = ""
    # Its origin, root-local, exactly as the file keeps it (no bottom rule).
    pos: Optional[list] = None
    # Absolute XYZ euler in DEGREES (the file stores radians), and scale: one number or [x, y, z].
    rot: Optional[list] = None
    scale: Optional[Union[float, list]] = None
    # Rest it on what is under it (`near` always does). Neither at, near nor pos: where the middle
    # of the view meets the scene, resting there.
    drop: bool = False
    # Metres between it and the `near` object (default: half the smaller footprint, 0.1–1 m).
    gap: Optional[float] = None
    # A clone of something not spawned yet: place it when it appears (tried for 60 s).
    wait: bool = False
    # Write it into `placed` in <project>/studio.edits.json. Off: a try, gone at the next reload.
    save: bool = False
    # Take a placement out instead (id, key or name); with save, out of the file too.
    remove: str = ""
    # Put the last call back — place, remove, edit or clear — live and on disk.
    undo: bool = False
    code: Optional[bool] = None
    scene: Optional[int] = None
    # The picture of the placement: the spot without it beside the spot with it. As SceneEditBody.
    look: Optional[Union[bool, str]] = None


@router.post("/place")
def scene_place(body: ScenePlaceBody):
    """Put one of the game's own things into the running game — a builder called, a model (in
    PlayCanvas a copy of the one the game drew, with its paint and size), a clone, a primitive —
    made by the same runtime that re-makes it from the saved file after every reload. Answers like
    an edit: where it stands, what it stands on, what it runs into. `save` keeps it."""
    from .. import live_scene
    return live_scene.place(body.project, body.model_dump())

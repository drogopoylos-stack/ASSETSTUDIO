"""Can an agent question the running game, and change it?

Two halves, same shape as web_tools_test.py.

The WIRING is proven offline: the setting, the gate, the note, the route table, and the fact that
the injected script is valid JavaScript. Those decide whether the feature is ever reached, and
they are what rots silently when something else moves.

The BEHAVIOUR is proven against a real page in a real headless Chrome — a WebGL fixture written
here, so the test needs no network and no game. It draws a triangle every frame, logs a warning,
throws once, and asks for an image that does not exist. Every one of those is a thing a contact
sheet cannot report and this must. Skipped, not failed, on a machine with no Chrome.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import inspect as _inspect                     # noqa: E402

from asset_studio import cc_session as cc      # noqa: E402
from asset_studio import live                  # noqa: E402
from asset_studio.routers import live as _rt_live   # noqa: E402
from asset_studio.live_shim import SHIM        # noqa: E402

ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s — %s" % (name, why))


FIXTURE = """<!doctype html><html><head><meta charset="utf-8"><title>live fixture</title>
<style>html,body{margin:0;background:#101418}canvas{display:block}</style></head><body>
<img src="/definitely-missing.png" style="display:none">
<canvas id="c" width="640" height="360"></canvas>
<script>
window.FIXTURE = { colour: '#3aa0ff', spin: 1.0, frames: 0 };
console.warn('fixture booted');
setTimeout(function () { throw new Error('a deliberate fixture error'); }, 30);
var gl = document.getElementById('c').getContext('webgl');
var vs = gl.createShader(gl.VERTEX_SHADER);
gl.shaderSource(vs, 'attribute vec2 p;uniform float a;void main(){float s=sin(a),c=cos(a);' +
                    'gl_Position=vec4(p.x*c-p.y*s,p.x*s+p.y*c,0.0,1.0);}');
gl.compileShader(vs);
var fs = gl.createShader(gl.FRAGMENT_SHADER);
gl.shaderSource(fs, 'precision mediump float;uniform vec3 col;void main(){gl_FragColor=vec4(col,1.0);}');
gl.compileShader(fs);
var pr = gl.createProgram();
gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr); gl.useProgram(pr);
var buf = gl.createBuffer();
gl.bindBuffer(gl.ARRAY_BUFFER, buf);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0,0.7,-0.7,-0.6,0.7,-0.6]), gl.STATIC_DRAW);
var loc = gl.getAttribLocation(pr, 'p');
gl.enableVertexAttribArray(loc);
gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
var uA = gl.getUniformLocation(pr, 'a'), uC = gl.getUniformLocation(pr, 'col');
function hex(h){return [parseInt(h.substr(1,2),16)/255,parseInt(h.substr(3,2),16)/255,
                        parseInt(h.substr(5,2),16)/255];}
var t = 0;
function frame() {
  t += 0.016 * window.FIXTURE.spin;
  window.FIXTURE.frames++;
  gl.clearColor(0.06, 0.08, 0.09, 1); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.uniform1f(uA, t);
  var c = hex(window.FIXTURE.colour);
  gl.uniform3f(uC, c[0], c[1], c[2]);
  /* Twelve draws a frame, so a draw-call count of "about 12" is a real measurement. */
  for (var i = 0; i < 12; i++) gl.drawArrays(gl.TRIANGLES, 0, 3);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
window.addEventListener('keydown', function (e) { window.LAST_KEY = e.key; });
window.addEventListener('pointerdown', function (e) { window.LAST_TAP = [e.clientX, e.clientY]; });
</script></body></html>
"""

print("The switch, and what it gates")
check("cc_live exists and is OFF on a fresh install (a new PC's agents know the code graph, the web fetch and the forge)",
      __import__("asset_studio.config", fromlist=["x"]).DEFAULT_SETTINGS.get("cc_live") is False)
src_cc = open("asset_studio/cc_session.py", encoding="utf-8").read()
# Indent-anchored: "sub_notes.append(...)" contains "notes.append(...)" as a substring, so a bare
# count reads one too many and says nothing about which call site it found.
check("the session prompt carries it",
      src_cc.count("\n        notes.append(_live_note(cwd))") == 1,
      src_cc.count("\n        notes.append(_live_note(cwd))"))
# A subagent's engine notes include it: in full with cc_engine_subagents on, else one line that
# points at /api/settings/engine-notes (the notes cost ~2,700 tokens a subagent otherwise).
check("and so does a subagent's (in full when Settings asks, else a pointer to it)",
      "out.append(_live_note(cwd))" in src_cc and 'settings.get("cc_engine_subagents", False)' in src_cc)
check("it is gated on the setting", 'settings.get("cc_live", True)' in src_cc)
check("…and on the project having a page at all", "_live_on" in src_cc and "_has_renderable(cwd)" in src_cc)

print("\nThe note says what a model cannot guess")
note = cc._live_note("C:/games/thing")
check("it names the two endpoints that matter",
      "/api/live/open" in note and "/api/live/eval" in note)
check("it says eval returns a VALUE", "returns the VALUE" in note)
check("it says eval also writes", "WRITES" in note)
check("it names the endpoints a screenshot cannot replace",
      "/api/live/console" in note and "/api/live/perf" in note)
check("it forbids a second browser", "never start your own" in note)
# The visual-review note is ~707 tokens and rides in front of every turn. This one has to earn
# its place beside it, not double the bill.
#
# Raised from 2300 to 2550 when the forge gained the three things that make it worth calling
# twice: the reference riding in the sheet, the parts that were too small to have been judged,
# and the stance. That is about 170 characters of prompt for the difference between an agent
# that compares against the target and one that compares against a memory of it — and the rest
# of the addition was paid for by compressing the prose around it, not by moving this number.
#
# Raised again, from 2550 to 2900, for the mesh operations. This is the largest single addition
# the note has taken and the easiest to justify: an agent that does not know `weld` exists ships
# a creature made of overlapping primitives with a crack at every join, and no amount of looking
# at a contact sheet tells it what to do about that. `check` is worth more still — it is the only
# line in the whole note that hands back a defect an image physically cannot show.
check("it stays well under the review note", len(note) < 2900, len(note))
ops = cc._ops_note()
check("the toolbox is its own note, and names the operations", "forge-ops.js" in ops and "makeOps" in ops)
check("with the catalogue one curl away", "forge-ops.md" in ops)
# 700 -> 900 after the goblin A/B against Blender (data/ab/goblin/). What Blender had and the forge
# did not was one chain: sculpt, decimate, bake a material onto the low mesh, one texture set for
# every part. Every tool of it was built; a tool the agent is not told about is not used, and the
# agent that lost wrote its own decimator and its own painter. One sentence names the chain.
check("and it stays short, because the catalogue carries the detail", len(ops) < 900, len(ops))
check("...and names the Blender recipe, in order",
      all(w in ops for w in ("densify", "sculpt", "decimate", "bakeMaterial", "applyMaps", "bakeAtlas")))
check("...and the hand that closes ON its handle", "grip" in ops and "hand" in ops)
check("and the one call that turns primitives into a surface", "skin(object)" in ops)
check("the forge is its own note, out of the live one", "/api/live/forge" not in note and "/api/live/forge" in cc._forge_note())
# The note grew when it stopped being a list of flags and became the loop: aim, measure, look,
# move. It is paid on every turn, so it still has a ceiling — but a ceiling that lets it teach the
# cheap half of the loop, which is what stops an agent reading a 2,300-token sheet eleven times.
#
# 1500 -> 2700 bought the loop itself, and then the three numbers that come back free with every
# render: the outline score, where each part landed in the frame, and what moved since the last
# shot. About 300 tokens a turn. It is worth that: without them an agent moved a diamond inset
# across the front of a chest between two shots and could not tell, which cost far more than 300
# tokens to find by hand.
#
# 2700 -> 3350 bought `look`, and that one is the cheapest of the lot. An agent judged a whole
# asset from a single angle because a second angle cost a second build; a chest shipped showing
# the open end of its own lid. A look is 0.34s and the note says so with the measurement, because
# an agent will only take the other angles if it is told they are nearly free.
#
# 3350 -> 3700 bought BATCH and REFLECTION, and the arithmetic is not close. Batch collapses
# build-measure-nudge-look from four HTTP calls into one; every call it removes is a whole turn of
# the model, and that loop runs dozens of times per asset. Reflection replaces "guess what the
# library exports, call it, read the exception, try again" with one 444-token answer asked for
# only when wanted. The two lines cost about 88 tokens a turn between them.
#
# The limit is here so that growth has to be argued for, not so that it never happens. Both of
# these were trimmed to a fifth of their first draft before this number was touched, and the
# detail they used to carry now lives in forge-ops.md and /api/live/api, where it is free.
# 3700 -> 4400 after the character A/B against Blender. The agent that lost wrote about twenty
# Python scripts to do what the bench would not: crop a reference sheet into one figure, segment
# near-black clothes, read a colour off a render, measure a part as a share of the figure, compose
# a before/after strip, export a GLB. Every line added here deletes one of those scripts, and a
# script the model writes costs far more than the sentence that makes it unnecessary.
# 4400 -> 4550 after the re-run. Six words bought six measurements the bench did not have, and
# every one of them is a fault the user could see and the bench could not say: TONE (the hair that
# never gets dark), BUSY and PLAIN (the seam nobody asked for, the pocket nobody drew), OFFSET (the
# hand two pixels low), FEATURES (the mouth the wrong shape), MESH (a vertex tint no rig can undo),
# and "shadows". Naming a measurement costs about twelve characters; not naming it costs the round.
_fn = cc._forge_note()
# 4550 -> 4700 -> 4800, and this is the raise that should end the raises.
#
# Six things landed this round that an agent would want: aim with no js, `"edits":"reset"`,
# width/height on look, `"frames":true`, `"facts":true`, and what `stats.shadow` means. Six
# sentences do not fit in 68 characters, and the alternative to raising the ceiling was leaving
# the API undocumented — which is how the note came to say `aim` needs `js` when it does not.
#
# What was built instead is `/api/live/api?name=bodies`: every field of every request body, read
# off the Pydantic models, with its type and its default. 19 bodies, 221 fields, and none of them
# costs a character here. From now on a new field needs NO note text at all, so the pressure that
# produced every previous raise is gone. The 100 characters this raise buys went on the one
# sentence that points at it, plus room for the terrain paragraph to name its new actions.
check("and the forge note stays short", len(_fn) < 4800, len(_fn))
check("...and points at the reflection instead of listing every field", "name=bodies" in _fn)
check("...and says the reference can be left out of one call", '"ref\\":\\"none' in _fn
      or 'ref\\":\\"none' in _fn or "none" in _fn)
check("...and names every measurement an agent would otherwise have to write a script for",
      all(w in _fn for w in ("TONE", "BUSY", "OFFSET", "FEATURES", "MESH")))
# Wired, echoed, tested — and it does not change a PlayCanvas render, measured to the pixel
# on a box over a plate. An agent told about a switch that does nothing spends a round
# finding that out, so the note does not mention it until the picture changes.
check("...and does not promise the shadow switch until it changes a picture",
      "shadows" not in _fn)
check("...and it says the camera can be orthographic", '"ortho"' in _fn)
check("...and that the rig and the exposure are the agent's", '"light"' in _fn and "exposure" in _fn)
check("...and how to hand it a reference that is a sheet", "ref_crop" in _fn and "ref_mask" in _fn)
check("...and that somebody else's GLB can be judged here",
      "/api/live/glb" in _fn and "/api/live/compare" in _fn)
check("...and that a part's colour and size come back as numbers",
      "COLOUR" in _fn and "SIZE" in _fn)
check("...and it teaches the loop that costs almost nothing",
      "/api/live/look" in _fn and "orbit" in _fn, len(_fn))
check("...and that the bench is what a person watches", "bench live" in _fn, len(_fn))
check("...and it teaches the three free numbers",
      all(k in _fn for k in ("score.overlap", "placement.parts", "SINCE YOUR LAST SHOT")),
      len(_fn))
check("the catalogue groups the forge things apart", {r["group"] for r in cc.agent_notes_catalog()["notes"]} == {"studio", "forge"})
from asset_studio import workspace as _wsd, browser_install as _bi
check("project discovery answers with a list", isinstance(_wsd.discover_projects(limit=5), list))
check("the browser installer reports its state", "installing" in _bi.status())
check("and says the defect report is numbers", "NUMBERS" in ops)
check("it says to read the findings before the picture", "findings` FIRST" in cc._forge_note())
check("it names what a whole-subject framing cannot judge",
      "NOT CHECKED" in cc._forge_note() and "focus" in cc._forge_note())
check("the JSON path is not backslashed", "C:/games/thing" in note)

# ---------------------------------------------------------------- the kiln note, on any PC
# It used to ASSERT this machine's three facts in every agent's system prompt: a Blender version
# under one exact folder, a gltf-transform on PATH, and two Hunyuan3D checkpoints already
# downloaded. On any other PC all three were a lie, and a tool that is announced and then absent
# is worse than one never mentioned - the plan is built around it before it fails.
# A BARE PC IS NOT JUST AN EMPTY PATH. Hiding `which` leaves the folder scan, which finds the
# real Blender on this machine - so the three sources are each replaced to get the answer a new
# PC would get.
_real = (cc._kiln_skill, cc.shutil.which, cc._blender_exe, cc._hunyuan_models)
try:
    cc._kiln_skill = lambda: False
    _n = cc._blender_kiln_note()
    check("with the skill not installed, the note says exactly that",
          "NOT installed on this PC" in _n and "pipeline" not in _n, _n[:90])

    cc._kiln_skill = lambda: True
    cc.shutil.which = lambda *_a, **_k: None
    cc._blender_exe = lambda: ""
    cc._hunyuan_models = lambda: []
    cc._kiln_cache = (0.0, [])
    _joined = " ".join(cc._kiln_facts())
    check("a PC without Blender is told so, not told where Blender is",
          "Blender: NOT found" in _joined, _joined)
    check("...and the same for gltf-transform", "gltf-transform: NOT installed" in _joined, _joined)
    check("...and the same for the Hunyuan3D weights", "NOT on this PC" in _joined, _joined)
    check("nothing claims a version this machine happens to have",
          "Blender 5.2" not in _joined and "Program Files" not in _joined, _joined)
    check("and the whole note still comes out", "BLENDER-KILN is installed" in cc._blender_kiln_note())
finally:
    cc._kiln_skill, cc.shutil.which, cc._blender_exe, cc._hunyuan_models = _real
    cc._kiln_cache = (0.0, [])

# The other half: the discovery really does find what is here, or the "missing" branch above
# would be the only one anybody ever sees.
check("Blender is found on a machine that has it", bool(cc._blender_exe()), cc._blender_exe())

_facts_here = " ".join(cc._kiln_facts())
check("on THIS machine it reports what is really here",
      "Blender" in _facts_here and "Hunyuan3D" in _facts_here, _facts_here[:120])
check("and the note is still short", len(cc._blender_kiln_note()) < 1400,
      len(cc._blender_kiln_note()))

print("\nThe route table")
src_r = open("asset_studio/routers/live.py", encoding="utf-8").read()
for path in ("/status", "/open", "/eval", "/scene", "/find", "/console", "/perf", "/input",
             "/shot", "/close"):
    check("route %s" % path, '"%s"' % path in src_r)
src_main = open("asset_studio/main.py", encoding="utf-8").read()
check("the router is registered", "live_router.router" in src_main)

print("\nThe injected script is real JavaScript")
# A syntax error here fails silently in the page and every later call returns "__live is not
# defined" — a confusing error a long way from its cause.
tmpjs = Path(tempfile.gettempdir()) / "live_shim_test.js"
tmpjs.write_text(SHIM, encoding="utf-8")
node = shutil.which("node")
if not node:
    skipped("node --check accepts the shim", "node is not installed")
else:
    p = subprocess.run([node, "--check", str(tmpjs)], capture_output=True, text=True)
    check("node --check accepts the shim", p.returncode == 0, (p.stderr or "")[:300])
check("it refuses to install itself twice", "if (window.__live) return;" in SHIM)
check("it hooks three.js the supported way", "__THREE_DEVTOOLS__" in SHIM)
check("it counts draws at the WebGL prototype",
      "drawElementsInstanced" in SHIM and "WebGL2RenderingContext" in SHIM)

print("\nOff means off")
# The switch is read through live.enabled(), so the test swaps THAT and never writes to
# settings.json. Flipping the real setting here would leave the feature off for the user if this
# file ever failed between the two writes.
_real_enabled = live.enabled
try:
    live.enabled = lambda: False
    r = live.evaluate("C:/", "1+1")
    check("a call is refused while the switch is off", r.get("ok") is False, r)
    check("and it says where the switch is", "Settings" in str(r.get("error", "")), r.get("error"))
    check("every route is guarded, not just eval",
          all(f(**k).get("ok") is False for f, k in (
              (live.scene, {"project": "C:/"}), (live.console, {"project": "C:/"}),
              (live.perf, {"project": "C:/"}), (live.find, {"project": "C:/", "query": "x"}),
              (live.shot, {"project": "C:/"}), (live.open_, {"project": "C:/"}),
              (live.send_input, {"project": "C:/", "events": [{"type": "wait"}]}))))
finally:
    live.enabled = _real_enabled

print("\nThe forge has a switch of its own")
# The forge is how an asset gets MADE, and a person may want it gone while the live link stays.
# Same discipline as above: only the reader is swapped, settings.json is never written.


class _Say:
    """The settings object, with some keys answered differently. Restored either way.

    Nothing here writes to settings.json. Whoever runs this file has their switches exactly as
    they left them afterwards - which matters, because two of the switches these tests need are
    OFF on a fresh install by design.
    """

    def __init__(self, real, key, value=None, **more):
        self._real = real
        self._over = dict(more) if key is None else {key: value, **more}

    def get(self, k, d=None):
        return self._over[k] if k in self._over else self._real.get(k, d)

    def __getattr__(self, n):
        return getattr(self._real, n)


_real_settings = live.settings
try:
    live.settings = _Say(_real_settings, "cc_forge", False)
    _off = {
        "forge": live.forge("C:/", "add(1)"),
        "aim": live.aim("C:/", "add(1)"),
        "forge_clear": live.forge_clear("C:/"),
        "thumbs": live.thumbs("C:/", ["x"]),
    }
    check("with the forge off, every door refuses",
          all(v.get("ok") is False for v in _off.values()),
          {k: v.get("ok") for k, v in _off.items()})
    check("...and each says where the switch is",
          all("Settings" in str(v.get("error", "")) for v in _off.values()),
          [v.get("error") for v in _off.values()][:1])
    check("...including the thumbnail renderer, which is the same machinery",
          _off["thumbs"].get("ok") is False, _off["thumbs"])
    # The live link is a different switch: turning the forge off must not take it with it.
    check("...while the live link is untouched by it",
          'settings.get("cc_forge"' not in cc._live_note.__doc__ if cc._live_note.__doc__ else True)
    check("the agent is not told it exists either",
          cc._forge_on(str(Path(__file__).resolve().parent)) is False)
finally:
    live.settings = _real_settings

# EVERY TEST BELOW NEEDS THE FORGE AND THE LIVE LINK ON, and the user's switches are not this
# file's business. Swapped, not written: whoever runs this still has them exactly as they left
# them. `cc_live` is OFF on a fresh install on purpose, so without this the whole browser half
# failed on a clean checkout with "Live game link is off" - a true statement about a default,
# and nothing at all about the code under test.
live.settings = _Say(_real_settings, None, cc_forge=True, cc_live=True)

print("\nA real page, in a real browser")
avail, why = live.available()
if not avail:
    skipped("the fixture answers questions", why)
else:
    root = Path(tempfile.mkdtemp(prefix="live-fixture-"))
    (root / "index.html").write_text(FIXTURE, encoding="utf-8")
    proj = str(root)
    try:
        got = live.open_(proj, start_dev=False, wait_ms=2200)
        check("the page opens", got.get("ok"), got.get("error"))
        check("the engine is detected from the canvas, with no cooperation",
              got.get("engine") == "webgl", got.get("engine"))

        r = live.evaluate(proj, "FIXTURE.colour")
        check("eval brings a value back", r.get("ok") and r.get("value") == "#3aa0ff", r)

        r = live.evaluate(proj, "FIXTURE")
        check("an object comes back flattened, not as an error",
              isinstance(r.get("value"), dict) and "frames" in (r.get("value") or {}), r.get("value"))

        # The whole point of the feature: change it, then see the change.
        live.evaluate(proj, "FIXTURE.colour = '#ff0044'")
        r = live.evaluate(proj, "FIXTURE.colour")
        check("a live change sticks", r.get("value") == "#ff0044", r.get("value"))

        r = live.evaluate(proj, "console.error('from an eval'); return 1;")
        check("eval reports what its own code logged",
              any("from an eval" in str(x.get("msg")) for x in (r.get("console") or [])), r)

        r = live.evaluate(proj, "let a = 2; let b = 3; return a * b;")
        check("a block of statements works too", r.get("value") == 6, r)
        check("…and it says which form it used", r.get("form") == "statements", r.get("form"))

        # The eval calls above must NOT have eaten these. That was the first version's bug: eval
        # drained, so this endpoint was permanently empty and a real page error was unreportable.
        c = live.console(proj)
        kinds = {row.get("kind") for row in (c.get("records") or [])}
        check("the console warning is captured", "warn" in kinds, kinds)
        check("the thrown error is captured", "error" in kinds, kinds)
        check("the missing image is captured", "resource" in kinds, kinds)
        check("draining means the second read is empty",
              not (live.console(proj).get("records") or []))

        p = live.perf(proj, ms=1200)
        check("frames are actually measured", p.get("frames", 0) > 20, p.get("frames"))
        # NOT an fps range. Headless Chrome has no monitor to wait for, so rAF runs flat out and
        # this fixture reads ~360fps. The honest number is what a frame COSTS.
        check("a frame's cost is measured", 0 < p.get("frame_ms_median", 0) < 60,
              p.get("frame_ms_median"))
        check("the answer says fps is a ceiling, not a measurement",
              p.get("vsync") is False and "ceiling" in str(p.get("note", "")), p.get("note"))
        check("the 60fps budget is reported", 0 < p.get("budget_60_pct", 0) < 400,
              p.get("budget_60_pct"))
        # This is the number a contact sheet can never give: the fixture draws 12 times a frame.
        check("draw calls are counted at the GL layer",
              8 <= p.get("draw_calls", 0) <= 16, p.get("draw_calls"))
        check("triangles are counted too", p.get("triangles", 0) >= 8, p.get("triangles"))
        check("GL objects are counted", p.get("gl_programs", 0) >= 1, p.get("gl_programs"))

        i = live.send_input(proj, [{"type": "key", "key": "a", "code": "KeyA", "keyCode": 65},
                                   {"type": "wait", "ms": 60},
                                   {"type": "click", "x": 120, "y": 90}])
        check("input is delivered", i.get("ok"), i.get("error"))
        check("the page saw the key", live.evaluate(proj, "LAST_KEY").get("value") == "a")
        check("the page saw the click",
              (live.evaluate(proj, "LAST_TAP").get("value") or [0])[0] == 120,
              live.evaluate(proj, "LAST_TAP").get("value"))

        s = live.shot(proj)
        check("a screenshot is written", s.get("ok") and Path(s.get("path", "")).exists(), s)
        check("and it is not an empty file", s.get("bytes", 0) > 2000, s.get("bytes"))

        # open {reload:true} reloads THE SAME TAB. It used to drop the record's target and make a
        # new one, and the old tab kept running the game at full speed (vsync is off) until the
        # browser idled out: ten edit-and-reload rounds were ten copies of the game.
        def _page_targets():
            async def go():
                from asset_studio import review as _rv
                ws = await live._connect(_rv._ensure_browser())
                try:
                    return len(await live._targets_alive(_rv._Cdp(ws)))
                finally:
                    await ws.close()
            return live._run(go)

        t_before, n_before = live._entry(proj).get("target"), _page_targets()
        live.evaluate(proj, "window.__mark = 7")
        got = live.open_(proj, start_dev=False, wait_ms=600, reload=True)
        check("open reload answers, and says it reloaded in place",
              got.get("ok") and got.get("reloaded") == "in place", got)
        check("…the tab is the same one", live._entry(proj).get("target") == t_before,
              [t_before, live._entry(proj).get("target")])
        check("…no tab is left behind", _page_targets() == n_before, [n_before, _page_targets()])
        check("…the document is a fresh one", live.evaluate(proj, "window.__mark === undefined").get("value") is True)
        check("…and the shim was in it from the start (its resource list raised to 5,000)",
              live.evaluate(proj, "!!(window.__live && __live.rtMax === 5000 && !__live.rtFull)").get("value") is True)
        # Another device changes the window, so that one IS a new tab: made first, old one closed.
        got = live.open_(proj, start_dev=False, wait_ms=600, reload=True, device="phone")
        check("a reload onto another device opens a new tab and says why",
              got.get("ok") and str(got.get("reloaded", "")).startswith("a new tab: the device"), got)
        check("…the old tab is closed, not left behind", _page_targets() == n_before, [n_before, _page_targets()])
        got = live.open_(proj, start_dev=False, wait_ms=600, reload=True, device="desktop")
        check("…and back again", got.get("ok") and _page_targets() == n_before, [got.get("reloaded"), _page_targets()])

        # Closing the tab must not be the end of the story: an agent that calls an hour later,
        # after the browser was reaped, should get an answer rather than a dead-tab error.
        live.close(proj)
        r = live.evaluate(proj, "1 + 1")
        check("a closed tab reopens itself", r.get("ok") and r.get("value") == 2, r)

        st = live.status()
        check("status lists the open tab", any(t.get("project") for t in st.get("tabs", [])), st)
    finally:
        try:
            live.close(proj)
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)


# A bundled game is the normal case AND the hard one: no window.pc, no window.THREE, because the
# engine is an ES module. These two pages are the two halves of it. The first hands itself a debug
# handle at a path no fixed list of names would contain — which is exactly what the real
# PlayCanvas game does, at `__game.renderer.app`. The second hands out nothing at all, and can
# therefore be named but not read; the answer has to say so instead of claiming "unknown engine".
HUNT = """<!doctype html><html><head><meta charset="utf-8"><title>hunt</title></head><body>
<canvas id="game" data-engine="PlayCanvas 2.21.4" width="320" height="200"></canvas>
<script>
// The closure is the point: it is what a bundler does. A bare `var app` in a classic script
// would put `app` on window, and the hunt would find it there instead — which passed the test
// while proving nothing about the case that actually matters.
(function () {
  var app = { root: { name: 'Root', children: [
                   { name: 'Sun', children: [], enabled: true },
                   { name: 'hero', children: [], enabled: true } ] },
              scene: { ambientLight: { r: 0.5, g: 0.25, b: 0.125 }, fog: 'none' },
              graphicsDevice: {}, start: function () {} };
  window.__demo = { renderer: { app: app } };
  document.getElementById('game').getContext('webgl');
})();
</script></body></html>
"""

TAG_ONLY = """<!doctype html><html><head><meta charset="utf-8"><title>tag only</title></head><body>
<canvas id="game" data-engine="PlayCanvas 2.21.4" width="320" height="200"></canvas>
<script>document.getElementById('game').getContext('webgl');</script></body></html>
"""


def serve(html, name):
    d = Path(tempfile.mkdtemp(prefix="live-%s-" % name))
    (d / "index.html").write_text(html, encoding="utf-8")
    return d


print("\nAn engine with no global to find it by")
if not live.available()[0]:
    skipped("the engine is hunted down", live.available()[1])
else:
    d = serve(HUNT, "hunt")
    try:
        got = live.open_(str(d), start_dev=False, wait_ms=1500)
        check("the engine is identified", got.get("engine") == "playcanvas", got.get("engine"))
        check("its object is found at a path nobody could have guessed",
              got.get("found_at") == "__demo.renderer.app", got.get("found_at"))
        check("the version comes off the canvas the engine tagged itself",
              got.get("engine_version") == "PlayCanvas 2.21.4", got.get("engine_version"))
        sc = live.scene(str(d), depth=2)
        kids = [c.get("name") for c in ((sc.get("root") or {}).get("children") or [])]
        check("the scene tree comes back", kids == ["Sun", "hero"], kids)
        check("scene settings are read", (sc.get("settings") or {}).get("ambient") == "#804020",
              (sc.get("settings") or {}).get("ambient"))
        f = live.find(str(d), "hero")
        first = (f.get("matches") or [{}])[0]
        check("find returns a path rooted where the engine actually is",
              str(first.get("path", "")).startswith("__demo.renderer.app.root"), f.get("matches"))
        back = live.evaluate(str(d), str(first.get("path", "")) + ".name")
        check("…and that path really resolves", back.get("value") == "hero", back)
        # The real game has 49 canvases: it draws its own gradients into offscreen 2D ones. A
        # dump of all of them buried the answer, so the list must be a summary plus a count.
        check("the canvas list is a summary, not a dump",
              isinstance(sc.get("canvases"), dict) and "total" in sc["canvases"], sc.get("canvases"))
    finally:
        live.close(str(d))
        shutil.rmtree(d, ignore_errors=True)

    d = serve(TAG_ONLY, "tagonly")
    try:
        got = live.open_(str(d), start_dev=False, wait_ms=1500)
        check("an engine that hides itself is still named",
              got.get("engine") == "playcanvas", got.get("engine"))
        check("…and is honestly reported as unreachable", got.get("reachable") is False, got)
        check("…with the one line that would fix it",
              "window.__game" in str(got.get("hint", "")), got.get("hint"))
        # Everything that does not need the engine has to keep working regardless.
        check("cost is still measured without the engine",
              live.perf(str(d), ms=600).get("frames", 0) > 5)
        check("the console still works without the engine",
              live.console(str(d)).get("ok") is True)
    finally:
        live.close(str(d))
        shutil.rmtree(d, ignore_errors=True)
        try:
            from asset_studio import review
            review.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTML5 engines. Phaser and PixiJS between them are most of the HTML5 games in existence, and
# both defeat every in-page route: the UMD build puts the CLASS on window (`Phaser`, `PIXI`) and
# keeps the INSTANCE inside the game's own closure. Nothing reachable from `window` points at it,
# so the only way in is a heap query over the class prototype. These run against the REAL engine
# builds, not a mock — a mock would have agreed with whatever the adapter happened to do.
ENGINES = Path(__file__).resolve().parent.parent / "data" / "enginetest"

print("\nReal HTML5 engines")
if not live.available()[0]:
    skipped("Phaser and PixiJS are reachable", live.available()[1])
elif not (ENGINES / "phaser.min.js").exists():
    skipped("Phaser and PixiJS are reachable",
            "no engine builds in %s — download phaser.min.js and pixi.min.js to run this" % ENGINES)
else:
    from asset_studio import review as _rev                                      # noqa: E402
    origin, _how = _rev.origin_for(ENGINES)
    proj = str(ENGINES)

    got = live.open_(proj, url=origin + "/phaser.html", start_dev=False,
                     wait_ms=3000, reload=True)
    check("Phaser is reached through the heap, not through window",
          got.get("engine") == "phaser" and got.get("reachable") is True, got)
    check("…and its version comes off the console banner it prints itself",
          str(got.get("engine_version", "")).startswith("Phaser 3"), got.get("engine_version"))
    check("the pin is a path that eval can use", got.get("found_at") == "__live.pinned",
          got.get("found_at"))
    sc = live.scene(proj, depth=3, wide=10)
    scene0 = ((sc.get("root") or {}).get("children") or [{}])[0]
    names = [c.get("name") for c in (scene0.get("children") or [])]
    check("the Phaser display list comes back", names == ["box", "label", "hero"], names)
    check("a Text object reports its text",
          any(c.get("text") == "phaser probe" for c in (scene0.get("children") or [])), names)
    check("the scene reports active and visible",
          scene0.get("active") is True and scene0.get("visible") is True, scene0)
    check("and the renderer and background are read",
          (sc.get("settings") or {}).get("renderer") == "webgl" and
          (sc.get("settings") or {}).get("background") == "#152033", sc.get("settings"))
    f = live.find(proj, "hero", 5)
    first = (f.get("matches") or [{}])[0]
    # A Phaser Container keeps children on `.list`, so a path built with `.children` would not
    # resolve. This is the check that catches that.
    check("find gives a Phaser path with the right child key",
          ".children.list[" in str(first.get("path", "")), first.get("path"))
    back = live.evaluate(proj, str(first.get("path", "")) + ".name")
    check("…and the path resolves in the live game", back.get("value") == "hero", back)

    got = live.open_(proj, url=origin + "/pixi.html", start_dev=False, wait_ms=3000, reload=True)
    check("PixiJS is reached the same way",
          got.get("engine") == "pixi" and got.get("reachable") is True, got)
    sc = live.scene(proj, depth=3, wide=10)
    kids = (sc.get("root") or {}).get("children") or []
    check("the Pixi stage comes back", [c.get("name") for c in kids] == ["box", "hero", "label"],
          [c.get("name") for c in kids])
    # A production Pixi build is minified, so `constructor.name` is "dr". Pixi labels its own
    # objects and those labels are what an agent can act on.
    check("object kinds are Pixi's, not the minifier's",
          [c.get("type") for c in kids] == ["graphics", "graphics", "text"],
          [c.get("type") for c in kids])
    check("a Text reports its text", kids[2].get("text") == "pixi probe", kids[2])
    check("a Graphics does not claim a texture it does not have", "texture" not in kids[0], kids[0])
    f = live.find(proj, "hero", 5)
    back = live.evaluate(proj, str((f.get("matches") or [{}])[0].get("path", "")) + ".label")
    check("the Pixi path resolves too", back.get("value") == "hero", back)

    got = live.open_(proj, url=origin + "/canvas2d.html", start_dev=False,
                     wait_ms=2000, reload=True)
    check("a game with no engine at all still opens", got.get("engine") == "canvas2d", got)
    p = live.perf(proj, ms=1200)
    # The fixture draws exactly 13 times a frame: 1 background, 10 boxes, 1 stroke, 1 text. Before
    # this, a canvas-2D game reported draw_calls 0 and read as a dead page.
    check("canvas 2D work is counted", 11 <= p.get("canvas2d_ops", 0) <= 15,
          p.get("canvas2d_ops"))
    check("…and WebGL numbers are not invented for it", p.get("draw_calls") == 0,
          p.get("draw_calls"))
    live.open_(proj, url=origin + "/pixi.html", start_dev=False, wait_ms=2000, reload=True)
    check("a WebGL game is not given a row of 2D zeroes to read past",
          live.perf(proj, ms=800).get("canvas2d_ops", 0) == 0,
          live.perf(proj, ms=800).get("canvas2d_ops"))

    # ---------------------------------------------------------------- the forge
    # An agent that writes assets in code has, until now, had no way to SEE one. This is the
    # Blender loop for a code-first project: run the script, look at the result, change it.
    # Tested on three.js because the arena covers PlayCanvas — both paths need proving.
    print("\nThe forge: code in, a picture out")
    if not (ENGINES / "three.module.js").exists():
        skipped("the forge builds and frames an asset", "no three.module.js fixture")
    else:
        live.open_(proj, url=origin + "/three.html", start_dev=False, wait_ms=2500, reload=True)
        r = live.forge(proj, """
            const g = new THREE.Group(); g.name = 'thing';
            const m = new THREE.MeshStandardMaterial({color: 0xcc4422, roughness: 0.4});
            m.name = 'clay';
            for (let i = 0; i < 3; i++) {
              const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), m);
              box.position.set(i * 1.4 - 1.4, i * 0.5, 0);
              g.add(box);
            }
            add(g);
            return g.children.length;
        """, views=["3q", "front"], width=320, height=320, label="forge test")
        check("the forge builds with the page's own engine",
              r.get("ok") and r.get("engine") == "three", r.get("error") or r.get("engine"))
        check("it returns the code's value", r.get("value") == 3, r.get("value"))
        st = r.get("stats") or {}
        check("it counts what was made", st.get("meshes") == 3 and st.get("triangles") == 36,
              [st.get("meshes"), st.get("triangles")])
        check("it names the materials", st.get("material_names") == ["clay"], st.get("material_names"))
        check("it reports the bounding box", (st.get("bbox_size") or [0])[0] > 3, st.get("bbox_size"))
        # The framing guarantee. A cropped asset is worse than no picture: it looks like an answer.
        check("it says the subject was framed, not cropped", st.get("framed") == "fit",
              st.get("framed"))
        check("a sheet is written", bool(r.get("sheet")) and Path(r["sheet"]).exists(), r.get("sheet"))
        check("with one panel per view", r.get("views") == ["3q", "front"], r.get("views"))

        # A subject far bigger than the default frame must be backed off, not clipped.
        big = live.forge(proj, """
            const m = new THREE.MeshStandardMaterial({color: 0x3399ff});
            const bar = new THREE.Mesh(new THREE.BoxGeometry(0.4, 40, 0.4), m);
            bar.name = 'very-tall'; add(bar); return 1;
        """, views=["front"], width=320, height=320, label="tall")
        check("a subject far larger than the frame is backed off, not cropped",
              (big.get("stats") or {}).get("framed") in ("fit", "widened"),
              (big.get("stats") or {}).get("framed"))

        # Half this project's assets are procedural TEXTURES. A normal map shown as a lit quad
        # tells you nothing, so a flat subject is drawn flat, on a checkerboard.
        flat = live.forge(proj, """
            const c = document.createElement('canvas'); c.width = 64; c.height = 64;
            const x = c.getContext('2d');
            const g = x.createRadialGradient(32, 32, 1, 32, 32, 31);
            g.addColorStop(0, '#ffffff'); g.addColorStop(1, 'rgba(255,255,255,0)');
            x.fillStyle = g; x.fillRect(0, 0, 64, 64);
            add(c); return 'blob';
        """, views=["3q"], width=256, height=256, label="soft blob")
        check("a procedural texture is judged flat, not as a lit quad",
              (flat.get("stats") or {}).get("flat") is True, flat.get("stats"))
        check("…at its real size", (flat.get("stats") or {}).get("image") == [64, 64],
              (flat.get("stats") or {}).get("image"))

        # Code that throws must still say where, and still hand back the picture.
        bad = live.forge(proj, "add(nope.missing);", views=["3q"], width=160, height=160)
        check("a throw is reported with its message", bad.get("ok") is False and
              "nope" in str(bad.get("error", "")), bad.get("error"))
        check("the forge survives a throw", live.forge(proj, "return 7;", views=["3q"],
                                                       width=160, height=160).get("value") == 7)
        check("clearing empties the subject",
              live.forge_clear(proj).get("cleared") is True, live.forge_clear(proj))
        # The forge with NO prior open: what a fresh install does, where the live link is off. It
        # must serve the project's own page and find the engine there by itself.
        solo = serve((ENGINES / "three.html").read_text(encoding="utf-8"), "forge-solo")
        (solo / "three.module.js").write_bytes((ENGINES / "three.module.js").read_bytes())
        r2 = live.forge(str(solo), "add(new THREE.Mesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshStandardMaterial()));",
                        views=["3q"], width=160, height=160, label="forge test (solo)", tags=["test"])
        check("the forge opens the project's page itself when nothing opened it first",
              r2.get("ok") and r2.get("engine") == "three", r2.get("error") or r2.get("engine"))

    live.close(proj)
    try:
        from asset_studio import review
        review.shutdown()
    except Exception:
        pass

print("\nWhere a part landed, and what moved since last time")
# The fault this exists for, in one sentence: an agent moved a diamond inset across the front of
# a chest between two shots and had no way to know. The silhouette could not see it — an outline
# is blind to anything inside the outline — so positions have to be compared instead.
_A = {"view": "front", "parts": [
    {"name": "diamondOuter", "at": [0.395, 0.613], "box": [0.33, 0.53, 0.46, 0.69], "tris": 96},
    {"name": "clasp", "at": [0.50, 0.60], "box": [0.45, 0.4, 0.55, 0.8], "tris": 240},
    {"name": "sideDiamond", "at": [0.12, 0.60], "box": [0.08, 0.55, 0.16, 0.66], "tris": 48},
]}
_B = {"view": "front", "parts": [
    {"name": "diamondOuter", "at": [0.302, 0.610], "box": [0.24, 0.53, 0.36, 0.69], "tris": 96},
    {"name": "clasp", "at": [0.50, 0.60], "box": [0.45, 0.4, 0.55, 0.8], "tris": 240},
    {"name": "lidGem", "at": [0.50, 0.20], "box": [0.47, 0.17, 0.53, 0.23], "tris": 32},
]}
_d = live._drift_finding(_B, _A)
check("a part that moved is named, with the distance", "diamondOuter left 0.09" in _d, _d)
check("...and where it went, from and to", "(0.40,0.61 -> 0.30,0.61)" in _d, _d)
check("a part that vanished is named", "GONE: sideDiamond" in _d, _d)
check("a part that appeared is named", "new: lidGem" in _d, _d)
check("a part that did not move is not mentioned", "clasp" not in _d, _d)
check("nothing moved means nothing is said", live._drift_finding(_A, _A) == "")
check("two different cameras are never compared",
      live._drift_finding({"view": "3q", "parts": _B["parts"]}, _A) == "")
check("no history means no line", live._drift_finding(_B, {}) == "")

_pf = live._placement_finding(_B)
check("the placement line gives the view and the coordinates",
      "front view" in _pf and "(0.50, 0.60)" in _pf, _pf)
check("...and says the reference is ruled the same way", "0..1" in _pf, _pf)

print("\nThe score runs on every shot, not only in the sweep")
_HERE = Path(__file__).resolve().parent
_src = (_HERE / "asset_studio" / "live.py").read_text(encoding="utf-8")
check("forge scores the first RENDER, never the reference panel", "frames[1]" in _src)
check("...and the record keeps the score, so the next shot can compare",
      '"score": res.get("score")' in _src)
check("...and keeps where every part was", '"placement": res.get("placement")' in _src)
check("the previous run is read from the records, not the history list",
      "forge-*.json" in _src and "def _last_run" in _src)
_fj = (_HERE / "asset_studio" / "live_forge.py").read_text(encoding="utf-8")
check("the browser can report a part's box in the frame", "F.where = function" in _fj)
# Matched in two pieces because the sentence wraps in the source, and a check that depends on
# where a comment happens to break is a check that fails for no reason.
check("...in the picture's own coordinates, y down from the top",
      "y from 0 at" in _fj and "the TOP to 1 at the bottom" in _fj)

print("\nA lit render is not a silhouette pass")
# The fault an agent found by cross-checking a render against `aim`: 0.62 against 0.91 on one
# chest. `_mask_of_render` reads the silhouette pass — black on near-white — so pointed at an
# ordinary lit render it selects the DARK BACKDROP and rejects the BRIGHT subject.
import numpy as _np                                     # noqa: E402
from PIL import Image as _PIm                           # noqa: E402
_lit = _PIm.new("RGB", (200, 200), (26, 30, 38))        # the forge's own backdrop, #1a1e26
_lit.paste(_PIm.new("RGB", (80, 100), (210, 200, 240)), (60, 50))


def _bbox(m):
    ys, xs = _np.where(m)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(ys) else None


check("the silhouette reader gets a lit render wrong, and that is why there are two",
      _bbox(live._mask_of_render(_lit)) == (0, 0, 199, 199))
check("the photo reader finds the subject, not the backdrop",
      _bbox(live._mask_of_photo(_lit)) == (60, 50, 139, 149), _bbox(live._mask_of_photo(_lit)))
check("the angle sweep still uses the silhouette reader",
      "_compare_masks(_mask_of_render(im)" in _src)

print("\nA render's score and aim's mean the same thing")
# The v2 chest agent lost a shot to a score it could not trust, and said so: "the `score.overlap`
# that comes back with a render is not trustworthy here". It was right. `aim` sweeps with no sky,
# no floor and the silhouette override; a render measured the LIT picture and had to tell the
# subject from its own backdrop by brightness. Measured on one chest at one angle: 0.899 against
# 0.916. So a render now takes one extra hidden silhouette, at the same camera the placement was
# measured through, and scores THAT.
check("a render says which kind of frame its score came from",
      live._score_against.__code__.co_varnames[:4] == ("ref_path", "frame", "view", "drawn"))
check("the render takes a silhouette of its own", "__forge.setPass('silhouette')" in _src)
check("...at the first view, the one placement was measured through",
      "score_shot = await live.raw" in _src and "json.dumps(want[0]), float(margin)" in _src)
check("...only when there is a reference to score against", "if score_ref:" in _src)
check("...and it never reaches the sheet, so it costs no image tokens",
      'shots.append(("score' not in _src and 'score_shot = ""' in _src)
check("the score reads it as a silhouette, not as a photo", "drawn=sil is not None" in _src)
# THE ORDER, and it is the whole difference. `__forge.view()` does not only fit the bounding
# sphere — it renders, looks at the pixels, and backs off if the subject touches the border,
# because a skinned mesh or a displacing shader draws outside its own box. So the pass has to be
# ON before the framing, or the camera lands somewhere aim never puts it. Framing first and
# swapping after measured 0.892 against aim's 0.916 on the same chest at the same angle; setting
# the pass first makes both 0.916 exactly.
_order = _src.split("if score_ref:", 1)[1][:400]
check("the pass goes on BEFORE the framing, as it does in aim",
      _order.index("setPass('silhouette')") < _order.index("__forge.view"), _order[:120])
check("...and the framing call is the one that returns the picture",
      "score_shot = await live.raw(\"__forge.view" in _src)
check("a lit frame is still the fallback when no silhouette came back",
      "sil if sil is not None else frames[1]" in _src)
check("and the reason is written down where the next person will look",
      "0.554" in (live._score_against.__doc__ or "")
      and "0.899" in (live._score_against.__doc__ or ""))

check("the floor leaves every pass, because it is not the subject",
      "S.ground.visible = false" in _fj and "S.ground.enabled = false" in _fj)
check("...and comes back afterwards", "S.ground.visible = passState.floor" in _fj)
check("...even when the pass could not be set up", _fj.count("floorWas") >= 5)


print("\nA silhouette cannot tell a front from a back")
# The same agent: aim returned az=150 and az=330 at 0.889 and 0.887, and it had to go and read
# `live_forge.py` to learn which one faced the clasp. Picking wrong photographs the back, which
# is how the first chest ended up showing the open end of its own lid.
check("aim says which way az points", "az=0 looks at the subject's front (+Z)" in _src)
# Corrected 2026-09-24 (the goblin A/B): az=90 puts the camera at +X, and under glTF's convention -
# facing +Z - a subject's own LEFT is +X. The old words said "right" and sent an agent to the wrong
# side of a three-quarter reference.
check("...naming all four quarters, so nothing has to be inferred",
      "az=90 at its LEFT side" in _src and "az=180 at its back" in _src
      and "az=270 at its right side" in _src)

_az = live._az_of
check("an azimuth is read out of a view spec", _az("az=330,el=30") == 330.0)
check("...whichever order the parts come in", _az("el=30,az=45") == 45.0)
check("...and wraps, so -30 and 330 are the same angle", _az("az=-30") == 330.0)
check("a named preset has no azimuth to read", _az("3q") is None and _az("front") is None)

_mf = live._mirror_finding
_twins = [{"view": "az=150,el=25", "overlap": 0.889}, {"view": "az=330,el=25", "overlap": 0.887}]
_warn = _mf(_twins)
check("two opposite angles scoring the same are called out", _warn.startswith("MIRROR:"), _warn)
check("...naming both, with their numbers",
      "az=150,el=25" in _warn and "az=330,el=25" in _warn, _warn)
check("...and saying what DOES break the tie", "placement.parts" in _warn, _warn)
check("a clear winner is not called a mirror",
      _mf([{"view": "az=330", "overlap": 0.91}, {"view": "az=150", "overlap": 0.62}]) == "")
check("two nearby angles are not a mirror, however close their scores",
      _mf([{"view": "az=330", "overlap": 0.91}, {"view": "az=340", "overlap": 0.909}]) == "")
check("a preset sweep says nothing rather than guessing",
      _mf([{"view": "3q", "overlap": 0.9}, {"view": "back", "overlap": 0.9}]) == "")
check("nothing scored, nothing said", _mf([]) == "")
check("the warning goes second, under the angle it is about", "lines.insert(1, twin)" in _src)


print("\nOne decoder for every captured frame")
_png = __import__("io").BytesIO()
_lit.save(_png, "PNG")
_url = "data:image/png;base64," + __import__("base64").b64encode(_png.getvalue()).decode()
check("a data URL becomes an image", live._decode_shot(_url) is not None)
check("an error string does not", live._decode_shot("no engine on the page") is None)
check("and neither does nothing at all", live._decode_shot("") is None)


print("\nLooking again, without building again")
# The habit this exists to break: judging a whole asset from one angle, because a second angle
# cost a second build. The forge's tab always survived between calls and so did the scene inside
# it — `forge` simply chose to clear and re-run. Measured on the chest: build 3.0s, four more
# angles 0.6s, an orbit 0.34s, one part framed 0.19s.
check("there is a way to look without building", callable(getattr(live, "look", None)))
# Read through the decorator: `_settles` wraps look so its activity row always closes, and the
# wrapper's own code object has only (project, *a, **kw).
import inspect as _inspect                     # noqa: E402
_look_args = tuple(_inspect.signature(live.look).parameters)
check("...and it takes no js at all", "js" not in _look_args, _look_args)
check("...it takes the same words the forge does",
      all(k in _look_args for k in ("views", "focus", "turntable", "edits", "ref", "passes")))
check("...plus an orbit and a zoom, relative to where the camera is",
      "orbit" in _look_args and "zoom" in _look_args)
check("it refuses politely when nothing has been built",
      "Build it once with" in (io.open("asset_studio/live.py", encoding="utf-8").read()))
check("it never clears the scene it is photographing",
      "__forge.clear()" not in _src.split("def look(", 1)[1].split("\ndef ", 1)[0])
check("...and never runs the agent's code again",
      "__forge.params =" not in _src.split("def look(", 1)[1].split("\ndef ", 1)[0])
check("the forge remembers which way it is looking", "F.at = function" in _fj)
check("...and a preset is resolved to a real azimuth, not left to be guessed",
      "Math.atan2(d[0], d[2])" in _fj)
check("a render says where the camera ended up, so the next call can orbit from there",
      '"at": got.get("at") or {}' in _src)

print("\nThe bench: what the page is holding right now")
# A generation is a finished run. The bench is what the agent has in front of it, and it changes
# on every call including the ones that draw no picture — which is most of them.
check("the bench can be read", callable(getattr(live, "bench", None)))
check("...and it is guarded like the rest of the forge",
      "_guard(project, forge=True)" in _src.split("def bench(", 1)[1].split("\ndef ", 1)[0])
check("a run that does not clear APPENDS, so building a part at a time works",
      "and then" in _src and "if clear or not old:" in _src)
check("...which is what a mirror needs to rebuild the WHOLE scene", "def session_code" in _src)
check("the bench says whether the tab is still there",
      'b["open"] = bool(e and e.get("target"))' in _src)
check("it is never written to disk, because it describes a browser",
      "_bench: dict = {}" in _src)
# A real folder that has simply never been on the bench. A folder that does not exist is refused
# by the guard, which is a different question and already covered above.
_b = live.bench(str(_HERE.parent))
check("a project that has never been on the bench is empty, not an error",
      _b.get("ok") and not _b.get("open"), _b)

print("\nEvery creation can be looked at")
# 59 of 507 generations on this machine showed nothing in the strip. Every one held its code: they
# were made by the cheap half of the loop, which measures without spending a sheet.
check("a picture can be drawn for a run that never drew one",
      callable(getattr(live, "pictures", None)))
_pic = _src.split("def pictures(", 1)[1].split("\ndef ", 1)[0]
check("...only for the ones that have code and no picture",
      "if png.is_file()" in _pic and 'code = str(rec.get("code")' in _pic)
check("...in one visit for the whole batch", "_run_pictures(e, want_url, opts" in _src)
check("a run whose code draws nothing is said once, not retried forever",
      'rec["no_picture"]' in _pic)
check("...and a whole batch that cannot be served gives up after two tries",
      'int(rec.get("picture_tries") or 0) >= 2' in _pic)
check("what it draws is kept beside the record it belongs to",
      'rec_path.with_suffix(".png")' in _pic)

print("\nThe reference is ruled in the same coordinates")
from PIL import Image as _PI                          # noqa: E402
_tmp = _HERE.parent / "data" / "tmp" / "_rule_probe.png"
_tmp.parent.mkdir(parents=True, exist_ok=True)
_flat = _PI.new("RGB", (200, 200), (40, 40, 40))
_before = len(set(_flat.getdata()))
live._rule(_flat)
check("the grid is drawn", len(set(_flat.getdata())) > _before, len(set(_flat.getdata())))
check("...and stays faint enough to read the picture under it",
      max(sum(c) for c in _flat.getdata()) < 700, max(sum(c) for c in _flat.getdata()))

# ---------------------------------------------------------------- the studio's look
#
# The A/B against Blender lost points to three things this bench could not do: an orthographic
# camera for a turnaround reference, an ambient floor low enough for black cloth, and a reference
# whose near-black jeans were read as backdrop. All three are per call now.
print("\nThe studio's look is the agent's, and it is sticky")
_o = live._studio_opts(ortho=True, light="reference", env=0.2, exposure=1.4,
                       lights={"key": 1.2, "rim": 0})
check("every field the caller named comes through",
      _o == {"ortho": True, "light": "reference", "env": 0.2, "exposure": 1.4,
             "lights": {"key": 1.2, "rim": 0.0}}, _o)
check("a rig nobody has heard of is ignored, not passed on", "light" not in live._studio_opts(light="disco"))
check("nothing asked for, nothing sent", live._studio_opts() == {})
_e = {}
live._look_state(_e, {"light": "reference", "ortho": True})
live._look_state(_e, {"exposure": 1.5})
check("a later call keeps what an earlier one set",
      _e["look"] == {"light": "reference", "ortho": True, "exposure": 1.5}, _e["look"])
check("forge, look and aim all take it",
      all("ortho" in _inspect.signature(f).parameters for f in (live.forge, live.look, live.aim)))
check("...and look can sweep the key light", "sweep" in _inspect.signature(live.look).parameters)
_src_live = io.open("asset_studio/live.py", encoding="utf-8").read()
check("the look is sent to the page with every build", "**look_now))" in _src_live)
check("...and a look that changes nothing does not reset the rig",
      "if given_look:" in _src_live and "__forge.studio(" in _src_live)

print("\nA reference that is a sheet, and one whose clothes are its backdrop")
_rt = Path(tempfile.mkdtemp(prefix="refcrop-"))
_pic = _PI.new("RGB", (400, 300), (16, 16, 18))
_PI_draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(_pic)
_PI_draw.rectangle([40, 40, 120, 260], fill=(30, 32, 34))        # a dark figure on a dark plate
_PI_draw.rectangle([260, 40, 340, 260], fill=(200, 180, 160))    # a second figure, the other tile
_sheet_path = _rt / "sheet.png"
_pic.save(_sheet_path)
_proj = str(_rt)
_got = live.reference(_proj, str(_sheet_path))
check("a reference is remembered", _got.get("reference") == str(_sheet_path.resolve()), _got)
_got = live.reference(_proj, "", "", "0.05,0.1,0.35,0.95")
check("a crop cuts one figure out of the sheet and becomes the reference",
      _got.get("reference", "").endswith("reference-crop.png") and _got.get("source") == str(_sheet_path.resolve()),
      _got)
check("...and the sheet is still named, so the crop can be taken again",
      live.reference(_proj).get("source") == str(_sheet_path.resolve()))
_back = live.reference(_proj, "", "", "-")
check("a crop can be given back", _back.get("reference") == str(_sheet_path.resolve()), _back)
_dark = _PI.new("RGB", (120, 200), (16, 16, 18))
__import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(_dark).rectangle([40, 30, 80, 180], fill=(26, 27, 30))
_m_col = live._mask_of_photo(_dark, edges=False)
_m_edge = live._mask_of_photo(_dark, edges=True)
check("near-black cloth on a near-black plate is kept by colour alone",
      int(_m_col.sum()) > 4000, int(_m_col.sum()))
check("...and by the edges", int(_m_edge.sum()) > 4000, int(_m_edge.sum()))
# It used to be lost. The threshold was `max(60, border noise)` and charcoal denim on a
# near-black plate is 44 away from it, so the mask kept 542 of the 13,896 pixels the reference
# boy's shins occupy and the silhouette score read 0.38 where a hand-made mask read 0.86.
check("the threshold is a ladder walked downwards, not one number",
      "THE THRESHOLD IS A LADDER, NOT A NUMBER" in _src_live
      and "40.0, 26.0, 18.0, 12.0, 8.0" in _src_live)
check("...whose floor a border that clips its neighbours cannot raise",
      "90.0)) * 3.0" in _src_live)
check("...and whose walk stops when the plate joins the subject",
      "cover >= 0.80 or (was >= 0.05 and cover > was * 2.5)" in _src_live)
check("the reference mask is cached on the file's own modification time",
      "_REF_MASKS" in _src_live and "os.path.getmtime(path)" in _src_live)
# A body whose legs are DARKER than its torso and almost the plate's own colour: the shape the
# old threshold cut in half.
_legs = _PI.new("RGB", (120, 200), (16, 16, 18))
_dr = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(_legs)
_dr.rectangle([40, 30, 80, 110], fill=(26, 27, 30))
_dr.rectangle([46, 110, 58, 190], fill=(21, 22, 24))
_dr.rectangle([62, 110, 74, 190], fill=(21, 22, 24))
_m_legs = live._mask_of_photo(_legs, edges=True)
_leg_px = int(_m_legs[150:186, 44:76].sum())
check("a near-black pair of legs under a lighter torso survives the mask",
      _leg_px > 700, "%d of about 936" % _leg_px)
_mask_png = _rt / "mask.png"
_mm = _PI.new("L", (120, 200), 0)
__import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(_mm).rectangle([40, 30, 80, 180], fill=255)
_mm.save(_mask_png)
_dark_path = _rt / "dark.png"
_dark.save(_dark_path)
check("a mask the caller supplies is used as it is",
      int(live._mask_of_reference(str(_dark_path), str(_mask_png)).sum()) == 41 * 151,
      int(live._mask_of_reference(str(_dark_path), str(_mask_png)).sum()))
check("...and one that does not fit the picture is ignored rather than trusted",
      live._mask_of_reference(str(_dark_path), str(_sheet_path)).shape == (200, 120))
shutil.rmtree(_rt, ignore_errors=True)
shutil.rmtree(live._LIVE_DIR / live._slug(_proj), ignore_errors=True)

print("\nSomebody else's asset, judged here")
check("a GLB can be put on the bench", callable(getattr(live, "glb", None)))
check("...the bench can be written out as one", callable(getattr(live, "export_glb", None)))
check("...and two of them can be scored side by side", callable(getattr(live, "compare", None)))
check("the page is handed a loader by the Studio, not left to guess",
      "/api/engine/loader?project=" in _src_live and "GLTFExporter.js" in _src_live)
check("the file is served to the page rather than read from disk by it",
      "/api/engine/model?project=&path=" in _src_live)
check("the A/B hides one subject and then the other, with one camera",
      "__forge.solo(\'__B\',true,true)" in _src_live and "one camera, one rig" in _src_live)
_routes = [getattr(r, "path", "") for r in _rt_live.router.routes]
check("all three have a route",
      all(p in _routes for p in ("/api/live/glb", "/api/live/export", "/api/live/compare")), _routes)

print(chr(10) + "The cheap half of the loop is scored too")
check("a numbers run resolves the reference instead of skipping it",
      "so the mode an agent repeats after every edit is exactly the mode that needs it" in _src_live
      # The line gained an "unless it was switched off" since; what matters is that `numbers`
      # is no longer what decides it.
      and 'score_ref = "" if numbers else' not in _src_live)
check("...and scores the silhouette it has already captured, before it returns",
      "THE SCORE, IN THE CHEAP HALF TOO" in _src_live
      and _src_live.index("THE SCORE, IN THE CHEAP HALF TOO")
      < _src_live.index('res["numbers_only"] = True'))
check("...with the same scorer the sheet path uses",
      "cmp = _score_against(score_ref, sil, want[0], drawn=True," in _src_live)
check("...and records it, so the next run can say better or worse",
      _src_live.count('"score": res.get("score") or {},') == 3)
check("a look says what the rig actually became, rather than what was asked for",
      'look_got = await live.ask("__forge.studio(' in _src_live
      and 'res["look"] = got["look"]' in _src_live)

print(chr(10) + "Shadows, and what the mesh itself says")
from asset_studio.live_forge import FORGE as _forge_src   # noqa: E402
_src_rt = io.open("asset_studio/routers/live.py", encoding="utf-8").read()
check("the key light can cast, on both engines",
      "S.renderer.shadowMap.enabled = !!S.shadows" in _forge_src
      and "kl.castShadows = !!S.shadows" in _forge_src)
check("...every mesh both casts and receives, because the shadow that matters is between parts",
      "n.castShadow = !!S.shadows; n.receiveShadow = !!S.shadows" in _forge_src
      and "comp.castShadows = !!S.shadows" in _forge_src
      and "mis[i].castShadow = !!S.shadows" in _forge_src)
check("...the shadow camera is sized on the same sphere the view is framed on",
      "F.shadowFit = function (b)" in _forge_src and "F.shadowFit(b);" in _forge_src)
check("...and a call can confirm it happened", '"shadows": !!S.shadows' in _forge_src
      or "shadows: !!S.shadows" in _forge_src)
check("off unless asked: the switch only moves when the caller sends it",
      "if (o.shadows !== undefined) S.shadows = !!o.shadows;" in _forge_src)
check("shadows reach the page from every call that carries the rig",
      _src_live.count("_studio_opts(ortho, light, env, ambient, exposure, lights, shadows)") >= 4)
check("...and are a field on the bodies", _src_rt.count("shadows: Optional[bool] = None") >= 4)
check("the mesh says whether it has normals, no-area faces and a painted tint",
      "F.health = function ()" in _forge_src and "r2.tint" in _forge_src)
_h = live._health_finding([{"part": "hair", "tris": 26530, "normals": True, "degenerate": 0,
                            "tint": 2.84}])
check("a vertex tint no rig can undo is named", any("vertex tint" in x for x in _h), _h)
check("...with the reason it matters", any("before any lamp" in x for x in _h), _h)
_h2 = live._health_finding([{"part": "blob", "tris": 900, "normals": False, "degenerate": 40,
                             "tint": None}])
check("a mesh with no normals is named", any("has no normals" in x for x in _h2), _h2)
check("...and one with no-area triangles", any("no area" in x for x in _h2), _h2)
check("a healthy mesh says nothing",
      live._health_finding([{"part": "ok", "tris": 500, "normals": True, "degenerate": 0,
                             "tint": 1.1}]) == [])

print(chr(10) + "A picture the project's reference has nothing to say about")
# The reference is sticky per project, which is right for a character built against a turnaround
# and wrong for the terrain in the same project: every terrain sheet came back with a roblox boy
# as its first panel, and that is image tokens spent on a comparison nobody asked for.
for _w in ("none", "OFF", "no", "false", "0"):
    check("`ref: %s` switches the reference off" % _w, live._ref_off(_w))
check("...and a path is still a path", not live._ref_off("C:/somewhere/target.png"))
check("...and an empty ref still means the one this project remembers", not live._ref_off(""))
check("both sheet paths ask before they prepend the panel",
      _src_live.count('_ref_off(ref) else') >= 2
      and _src_live.count("(None, \"\") if _ref_off(ref)") == 2)
check("...and nothing is scored against a reference that was switched off",
      "score_ref = \"\" if _ref_off(ref) else" in _src_live
      and "score_ref = \"\" if (numbers or _ref_off(ref)) else" in _src_live)
check("...nor is a close-up held against it", _src_live.count("not det_mode or _ref_off(ref)") == 2)

print(chr(10) + "Shadows: the switch that reported nothing")
# THE CAUSE. `F.ensure` calls `F.studio(opts)` BEFORE the agent's code runs, so the traverse that
# flags every mesh walked an EMPTY root every single time. three.js defaults Mesh.castShadow to
# false, so nothing built after that moment ever cast or received. Two renders of a box over a
# plate came back identical to the pixel with the switch on and off, and `casters` went 0 -> 2
# the moment the flags moved to draw time.
check("the flags go on at draw time, not when the bench is empty",
      "F.shadowCast = function" in _forge_src
      and "  F.shadowCast();" in _forge_src)
check("...and F.studio hands off to it rather than doing it early",
      _forge_src.count("      F.shadowCast();") == 2)
check("...and a call can tell 'no shadows' from 'no meshes'",
      "casters: S.casters || 0" in _forge_src)
# THE SECOND CAUSE, and the more expensive one: the obvious control is wrong. PlayCanvas does not
# recompile a material when a light's `castShadows` changes, so differencing two frames across
# that flag reports zero while the shadow is plainly on screen. `shadowIntensity` is a uniform.
check("the probe moves the intensity, which needs no recompile",
      "F.shadowProbe = function" in _forge_src
      and "shadowIntensity = v" in _forge_src)
check("...and never flips castShadows to measure with",
      "castShadows = v" not in _forge_src)
check("...two frames per state, because the first one is spent settling",
      "set(1); F.shot(); F.shot();" in _forge_src)
check("...and the numbers reach the caller", "out.shadow = S.shadowSeen" in _forge_src)
check("...with a finding that names a rig that would show it",
      "SHADOWS:" in _src_live and "peak 164" in _src_live)

print(chr(10) + "The bench you can put back, resize and photograph")
check("an exact name beats a substring", "return exact.length ? exact : loose;" in _forge_src)
check("...and the answer says which nodes it took",
      "names: nodeNames(hit)" in _forge_src)
check("every edited node can be put back", "F.revert = function" in _forge_src
      and "keepWas(o);" in _forge_src)
check('...and `"edits": "reset"` is what asks for it',
      '"reset", "revert", "clear"' in _src_live and "__forge.revert()" in _src_live)
check("the panel resizes without disposing the scene", "F.resize = function" in _forge_src
      and "__forge.resize(" in _src_live)
check("one bare PNG per view, when asked", 'res["frames"] = made' in _src_live)
check("...and the composed sheet still reads its own list",
      "frames_list" in _src_live and "_review._sheet(frames_list" in _src_live)

print(chr(10) + "The reference, and the words that mean 'the automatic one'")
for _w in ("auto", "default", "on", "true"):
    check("`ref_mask: %s` means the ladder, not a file" % _w, live._mask_auto(_w))
check("...and a real path is still a path", not live._mask_auto("C:/x/mask.png"))
check("the reference can describe itself", hasattr(live, "ref_facts"))
check("...and aim no longer demands js it does not use",
      "reuse = not str(js).strip()" in _src_live)
check("...nor does the note say it does", "no js once" in _fn)

print(chr(10) + "Reflection instead of a longer note")
from asset_studio import apireflect as _ap    # noqa: E402
_bodies = _ap.bodies()
check("every request body is readable from the API", _bodies.get("ok") is True)
check("...all of them", len(_bodies.get("bodies") or []) >= 15, len(_bodies.get("bodies") or []))
_look = [b for b in _bodies["bodies"] if b["body"] == "LookBody"]
check("...LookBody among them", bool(_look))
if _look:
    _names = {f["name"] for f in _look[0]["fields"]}
    check("...with the fields this round added, and no note text spent on them",
          {"width", "height", "frames", "facts"} <= _names, sorted(_names)[:6])
    # `from __future__ import annotations` turns every annotation into a STRING, and the first
    # walk asked each one for `__name__`. Nineteen bodies came back attributed to no route at all.
    check("...and the route that uses it", _look[0]["used_by"] == ["/api/live/look"],
          _look[0]["used_by"])

print(chr(10) + "The body that stops dropping keys nobody declared")
# `plan` was built, tested and shipped unable to work: Pydantic drops a key it has no field for,
# so `goal` never reached the terrain module and the action could only refuse. `flow`, `channel`,
# `whole` and `tiles` were in the same position and only looked fine because their defaults were.
_tb = _rt_live.TerrainBody(project="p", action="plan", goal={"walkable": 0.8},
                           flow=True, whole=True)
check("`goal` reaches the module", _tb.goal == {"walkable": 0.8})
check("...and so does a field nobody declared", getattr(_tb, "flow", None) is True
      and getattr(_tb, "whole", None) is True)
check("...because the actions are a table that grows",
      'extra="allow"' in _src_rt)
# `NO REFERENCE: pass ref=<image path>` fired on a call that had just passed `ref:"none"` - the
# tool arguing with the caller about a decision the caller had made one line earlier.
_off = live._findings({"parts": []}, False, [], ref_off=True)
_on = live._findings({"parts": []}, False, [], ref_off=False)
check("a reference switched off is not asked for again",
      not any(str(x).startswith("NO REFERENCE") for x in _off))
check("...but a project that simply has none is still told once",
      any(str(x).startswith("NO REFERENCE") for x in _on))

print(chr(10) + "The Library assets that had no picture")
_src_eng_rt = open("asset_studio/routers/engine.py", encoding="utf-8").read()
# 232 of 352 ids came back with no picture AND no error, because the route never passed `limit`
# and `thumbs` quietly cut the list at its own default of 120.
check("the thumbnail route passes the ceiling it was given",
      "limit=body.limit" in _src_eng_rt and "limit: int = 120" in _src_eng_rt)
check("...and the answer says what it did not look at", '"not_asked"' in _src_live)
# 23 failed with `Invalid magic number ... 0x6f64213c`, which is the four bytes `<!do`: a spec
# that names a GLB was fetching it from a dev server that answers index.html for anything it does
# not know. Models already came through the Studio; models NAMED BY A SPEC did not.
# A BEHAVIOURAL CHECK, NOT A STRING ONE. The first version of this asserted the exact `elif`
# line, and the fix that made the branch actually work — resolving the model against every base
# it could be relative to — changed that line and broke the test that was meant to guard it.
_spec = {"type": "spec", "file": "g/src/a.ts", "root": "g", "model": "g/assets/m.glb",
         "path": str(Path(tempfile.gettempdir()) / "nope" / "a.ts")}
_tmpdir = Path(tempfile.mkdtemp(prefix="thumbmodel-"))
(_tmpdir / "g" / "assets").mkdir(parents=True, exist_ok=True)
(_tmpdir / "g" / "assets" / "m.glb").write_bytes(bytes([0x67, 0x6c, 0x54, 0x46]))
_js_m = live._thumb_js(_spec, str(_tmpdir), "http://127.0.0.1:5179")
_js_m = live._thumb_js(_spec, str(_tmpdir), "http://127.0.0.1:5179")
check("a model named by a spec comes from the Studio too",
      "/api/engine/model?" in _js_m and "m.glb" in _js_m,
      [l for l in _js_m.split(chr(10)) if "model" in l][:1])
check("...and a spec whose model is not on disk is left alone",
      "/api/engine/model?" not in live._thumb_js(
          {"type": "spec", "file": "g/src/a.ts", "model": "g/assets/gone.glb"},
          str(_tmpdir), "http://127.0.0.1:5179"))
shutil.rmtree(_tmpdir, ignore_errors=True)
check("...and an empty container names the URL it came from",
      "there was nothing inside it" in _src_live)
# 65 are `x_idle.glb`: animation with no mesh. A blank frame is the truth about that file and it
# read as a broken builder, which sent people to look at code that was fine.
check("a file with no mesh says so, instead of reading as a failure",
      "holds no mesh at " in _src_live)
check("...which needs the count, taken while the object is still in hand",
      '"tris": Math.round(__t)' in _src_live or "tris: Math.round(__t)" in _src_live)
# A page remembers a failed import for the life of the tab. 60 assets reported "could not import,
# tried 5 URLs" while both of those URLs imported by hand, in that same tab, under a fresh query.
_ao = open(str(Path(__file__).resolve().parent.parent / "frontend" / "src" / "components"
               / "engine" / "edit" / "assetOpen.ts"), encoding="utf-8").read()
# THE BASE IS THE GAME'S ORIGIN, NOT "". `openAsset` lives in `asset-open.js`, which the page
# fetches from the Studio, and a bare `/src/render/x.ts` inside a module resolves against THAT
# module's base URL — so every candidate went to the Studio on :8777 and 404ed while the identical
# path imported by hand from the page's own scope loaded from :5179. 60 assets, invisibly.
_js_o = live._thumb_js({"type": "spec", "file": "g/src/x.ts", "root": "g", "path": "C:/w/g/src/x.ts"},
                       "C:/w/g", "http://127.0.0.1:5179")
check("the thumbnail import is based on the game's own origin",
      'bases: ["http://127.0.0.1:5179"]' in _js_o, [l for l in _js_o.split(chr(10)) if "bases" in l])
check("...which the batch has and now passes down",
      "_thumb_batch(e, want_url, opts, _pairs, origin)" in _src_live)
check("...and the reason a candidate failed comes BEFORE the URL list, which truncation eats",
      '"could not import " + rel + ": " + first' in _ao)

check("a failed import is retried under a fresh query",
      'const stamp = "open=" + Date.now().toString(36);' in _ao)
# A file outside every game root: 200 text/html from both dev servers, 403 from Vite's /@fs.
check("a file no dev server owns is served by the Studio, last",
      "/api/engine/source?path=" in _ao and '@router.get("/source")' in _src_eng_rt)
check("...and TypeScript is refused in words rather than served broken",
      "it does not transpile them" in _src_eng_rt)

# ---- what the two cartoon-tree agents found (2026-09-23) ----------------------------------------
print("\nWhat the two tree agents found")
from asset_studio.live_forge import FORGE as _fg2   # noqa: E402
# /compare hid '__B' through the part search, which sees MESHES by substring: a GLB root is a
# Group, nothing matched, nothing was hidden, and both panels held both trees with one score.
check("compare hides each asset by its EXACT root name, groups included",
      "var byName = function (q)" in _fg2 and "var keep = exact ? byName(q) : pick(q);" in _fg2
      and _src_live.count("__forge.solo('__B',true,true)") == 2
      and _src_live.count("__forge.solo('__B',false,true)") == 2)
check("...and never through the substring part search again",
      "__forge.solo('__B')" not in _src_live and "__forge.solo('__B',true)\"" not in _src_live)
check("an export carries the asset, not the bench's wrapper node or 'AuxScene'",
      "sc = new S.T.Scene();" in _fg2 and "parse(src, res, rej" in _fg2
      and "S.root.add(kids[k2])" in _fg2)
_cv = {}
check("the backdrop and the picture size are sticky across calls",
      live._canvas(_cv, 400, 772, "#a9a9a8") == (400, 772, "#a9a9a8")
      and live._canvas(_cv) == (400, 772, "#a9a9a8")
      and live._canvas(_cv, 0, 0, "") == (400, 772, "#a9a9a8"))
check("...an aim sweep's own size is not remembered",
      live._canvas(_cv, 420, 420, "", remember=False) == (420, 420, "#a9a9a8")
      and live._canvas(_cv)[0] == 400)
check("...and a fresh session starts where it always did",
      live._canvas({}) == (640, 480, "#1a1e26"))
check("no request body resets them any more, and forge frames like look",
      _rt_live.ForgeBody(project="x", js="y").background == ""
      and _rt_live.GlbBody(project="x", path="y").background == ""
      and _rt_live.CompareBody(project="x", b="y").background == ""
      and _rt_live.ForgeBody(project="x", js="y").width == 0
      and _rt_live.ForgeBody(project="x", js="y").margin == 0.0)
check("a look near the reference angle is scored; a side or back view is not",
      live._near_view("front", "az=0,el=26") and live._near_view("az=0,el=18", "az=0,el=26,zoom=1.25")
      and not live._near_view("front", "side") and not live._near_view("front", "back")
      and live._near_view("mystery", "mystery") and not live._near_view("mystery", "front"))
_hf = live._health_finding([
    {"part": "tier1", "tris": 500, "normals": True, "tint": 22.8, "unlit": True},
    {"part": "hair", "tris": 500, "normals": True, "tint": 2.9, "unlit": False}])
check("a painted UNLIT part is not told its tint is a problem; a lit one still is",
      not any("tier1" in l for l in _hf) and any("hair" in l for l in _hf), _hf)
check("the asset's own textures are counted, the GPU's apart",
      "out.gpu_textures = S.renderer.info.memory.textures;" in _fg2
      and "out.textures = texs.length;" in _fg2 and "out.textures = ptx.length;" in _fg2)
check("a remembered ref_mask says so in the MATCH line",
      "ref_mask m.png" in live._score_finding(
          {"overlap": 0.9, "view": "front", "width_ratio": 1.0, "mask": "m.png"}, {})[0])
check("the note says variants get `params`, and that backdrop and size stay as set",
      "(your js gets `params`)" in cc._forge_note() and "backdrop and size" in cc._forge_note())

# ---- what the version 3 and 3.1 tree agents found (2026-09-23) ----------------------------------
print("\nWhat the version 3 and 3.1 tree agents found")
import time as _tm                                      # noqa: E402
import socket as _so                                    # noqa: E402
import urllib.request as _ur                            # noqa: E402
from asset_studio import preview_server as _pv          # noqa: E402
_pdir = Path(tempfile.mkdtemp(prefix="pv_"))
(_pdir / "index.html").write_text("<p>hi</p>", encoding="utf-8")
_pport = _pv.serve_dir(str(_pdir))
_pkey = str(_pdir.resolve())
_pv._servers[_pkey]["last"] = 0.0
_ur.urlopen("http://127.0.0.1:%d/index.html" % _pport, timeout=5).read()
# The page server was reaped 30 minutes after it was STARTED: `last` moved only in serve_dir().
check("a page server counts every request as use, not only its start",
      _tm.time() - _pv._servers[_pkey]["last"] < 5)
_pv._servers[_pkey]["last"] = _tm.time() - 3600
check("an idle page server is stopped", _pv._reap_once() >= 1 and _pkey not in _pv._servers)
try:
    _so.create_connection(("127.0.0.1", _pport), 3.0).close()
    _prefused = False
except OSError:
    _prefused = True
# shutdown() alone left the socket listening: the kernel still took the connection, the request
# waited in the backlog, and the forge hung 45 s. Closed, the connect fails (2 s on Windows).
check("...and its port no longer takes a connection", _prefused)
_src3 = io.open("asset_studio/live.py", encoding="utf-8").read()
check("a remembered page URL whose server is gone is noticed",
      live._origin_gone({"url": "http://127.0.0.1:1/"}) and not live._origin_gone({}))
_pport2 = _pv.serve_dir(str(_pdir))
_t0 = _tm.perf_counter()
_la = live._answers("http://localhost:%d/" % _pport2)
_ldt = _tm.perf_counter() - _t0
# localhost is ::1 AND 127.0.0.1; tried in turn, ::1 took the whole 0.6 s timeout first.
check("a localhost URL is answered without waiting for ::1", _la and _ldt < 0.3, "%.3f s" % _ldt)
_pv._servers[str(_pdir.resolve())]["last"] = _tm.time() - 3600
_pv._reap_once()
check("...and forge, aim and every _forge_open start it again", _src3.count("if _origin_gone(e):") >= 3)
from asset_studio.live_shim import SHIM as _sh3        # noqa: E402
# 792 canvases after 70 forge calls: the page script held every canvas strongly, forever.
check("the live page script holds canvases weakly",
      "new WeakRef(this)" in _sh3 and "canvasSeen" in _sh3 and "canvases.push" not in _sh3)
from asset_studio.live_forge import FORGE as _fg3      # noqa: E402
check("dispose frees the GL context at once", "S.renderer.forceContextLoss()" in _fg3)
check("a preset with modifiers is a view",
      live._view_ok("top,zoom=2.5") == "top,zoom=2.5" and live._view_ok("side,el=20") == "side,el=20"
      and live._view_ok("top,zoom") == "")
check("...and the page starts from the preset's own angle",
      "if (first && first.indexOf('=') < 0 && DIRS[first])" in _fg3)
check("...and 'near the reference' reads it too",
      live._near_view("top,zoom=2.5", "top") and not live._near_view("front", "top,zoom=2"))
check("a view that cannot be read is named, never silently dropped",
      "'bogus'" in (live._view_notes(["bogus", "3q"]) or [""])[0] and live._view_notes(["3q"]) == [])
check("variants say which views they dropped", "VARIANTS: one view per variants call" in _src3)
check("close-up panels name their angle", _src3.count('"focus: %s @ %s" % (fname, want[0])') == 2)
check("a look says which edits matched nothing, and that edits add up",
      "matched nothing - a target is a node's name" in _src3 and "edits add up across calls" in _src3)
check("/glb draws one frame before it counts draw calls", "(__forge.shot(), 0)" in _src3)
check("/scene says when the page also holds a forge bench", "this page also holds a forge bench" in _src3)

# A focus close-up kept the part's fit and threw the zoom away; the border check read the
# silhouette pass's white backdrop as the subject; compare could not frame a part.
check("a focus close-up keeps its zoom: the part fits at zoom 1, then the zoom is applied",
      "if (zf > 1) { grow(zf); place(); }" in _fg3 and "if (zf > 1) { grow(1 / zf); place(); }" in _fg3)
from asset_studio.live_forge import FORGE as _fg4      # noqa: E402
check("under the silhouette pass the border is compared with that pass's own backdrop",
      "var bg = (passState && passState.bgPixel) || S.bgPixel" in _fg4
      and "if (name === 'silhouette' && passState) passState.bgPixel = sampleEmpty();" in _fg4)
check("...sampled with the subject hidden for one frame, and put back even on a throw",
      "var sampleEmpty = function ()" in _fg4 and "} finally {" in _fg4.split("var sampleEmpty")[1][:900])
import inspect as _in4                                  # noqa: E402
from asset_studio.routers import live as _rl4           # noqa: E402
check("compare takes a part to frame, in the function and in the request body",
      "focus" in _in4.signature(live.compare).parameters and "focus" in _rl4.CompareBody.model_fields
      and "focus=body.focus" in io.open("asset_studio/routers/live.py", encoding="utf-8").read())
_src4 = io.open("asset_studio/live.py", encoding="utf-8").read()
check("...one camera on the part in both, and a missing part is named, never silent",
      "__forge.view(%s,%s,0,%s)" in _src4 and "FOCUS: no part named %r in either asset" in _src4
      and 'close = (at or {}).get("framed") == "focus"' in _src4)

live.settings = _real_settings           # the user's own switch, back as it was

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)

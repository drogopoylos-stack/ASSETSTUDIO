# -*- coding: utf-8 -*-
"""TERRAIN, over HTTP — a world sculpted from the command line, answered in numbers.

Unity, Blender and Godot all have a terrain brush and all three need a person holding a mouse.
The thing none of them can do is the thing an agent needs: put a stroke down, ask what the
ground IS now, and choose the next stroke from the answer rather than from a screenshot. So the
report is the default answer here and a picture is taken only when one is asked for.

ONE IMPLEMENTATION. The maths lives in frontend/src/components/engine/edit/terrain.ts and is not
ported here — this module imports that module into the forge's page and drives it. The editor's
Terrain mode runs the same code in the same browser, so a person and an agent cannot end up
disagreeing about what a stroke did.

STATE STICKS, like the rest of the bench. The field sits in the page next to whatever the forge
is holding, so a world is built a stroke at a time across many requests. But a tab can be
reloaded and the browser is reaped after ten idle minutes, and forty strokes is far too much to
lose to that — so every mutating call is journalled to disk and replayed into a page that turns
out to be empty. The answer says `restored` when that happened; nothing else changes.

ONE REQUEST, MANY STROKES. `brush` takes a list, and a stroke carrying `to` is expanded into a
line of steps before it is sent. A round trip per brush step is a whole turn of the model per
brush step, which is the difference between usable and not.

ROUND TWO, and the one number that explains it. A stroke of radius 32 on a 513-sample field moves
4,225 samples out of 263,169 — one and a half per cent — and round one rebuilt the whole mesh for
every one of them. Measured in the page on that field: the rebuild is 13 ms at the lod the
endpoint picks and 52 ms at lod 1, against 13 ms for the erode stroke itself. It is not the
biggest cost, but it is the only one that scales with the FIELD instead of with the STROKE, so it
is the one that gets worse as the world gets bigger. The field is cut into tiles now and only the
tiles a stroke dirtied are rebuilt; every brush already answered with its rectangle and
`terrainMesh` already took a region, so both ends of this existed before either was used.

PLAN. `report()` was already a fitness function — walkable share, slope bands, relief, coverage —
so given a target it can be searched. `plan` applies candidate strokes, scores them, and rolls
back the ones that made the distance worse. The answer is the report either side of it and the
strokes it kept: no picture, because the whole point is that the numbers decided.

FLOW. An agent with no picture cannot see that its erode stroke did anything, so `report` says how
much of the field drains and `look` will false-colour the drainage. Aiming a river needs both.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from .live import (_LIVE_DIR, _MAX_VALUE, _bench_put, _bridge, _done, _doing,
                   _forge_open, _forge_script, _guard, _look_state, _run, _session,
                   _settles, _slug, _studio_opts)
from . import live as _live

# Bumped whenever the page glue below changes. A tab outlives a backend restart, so a script that
# only installs itself when absent leaves the old one in place — the same trap `_forge_script`
# documents, and the same cure.
TERRAIN_VERSION = 10

_MAX_RES = 1025             # 1.05M samples; the mesh alone is ~75MB of typed arrays past this
_MAX_STROKES = 4000         # after `to` is expanded, so one request cannot hang the tab
_MAX_STEPS = 256            # steps a single dragged stroke may become
_JOURNAL_MAX = 400          # actions kept before a snapshot is taken and the journal restarts
_PROXY_CAP = 1200           # scatter items drawn as markers; past this the picture is mud anyway
_LOD_BUDGET = 300_000       # triangles the preview mesh is allowed before `lod` is raised
_TILES = 8                  # tiles per side, so one stroke dirties four of sixty-four
_PLAN_STEPS = 40            # annotated steps an agent READS; and clear of the shim's 60-item cap
_PLAN_REPLAY = 800          # steps the JOURNAL stores; short of this and a rebuilt tab is wrong
_FLOW_MODES = ("flow", "cut")

# One line per action, handed back whenever an action is missing or misspelt. This is what pays
# for the two lines the agent note can afford: the endpoint teaches its own shapes.
_SHAPES = {
    "make": '{"size":512,"res":257,"maxHeight":48,"seed":1,"origin":[0,0,0]}',
    "brush": '{"strokes":[{"kind":"raise|lower|smooth|flatten|noise|erode|paint|scatter|erase",'
             '"x":0,"z":0,"radius":40,"strength":0.6,"falloff":0.7,"seconds":1}]} — plus '
             '"to":[x,z] (+"steps") to drag a line, "target" for flatten, "layer" for paint, '
             '"asset"/"density"/"maxSlope"/"spacing" for scatter. Many strokes per request; '
             'the seconds of a dragged stroke are spread along the line.',
    "layers": '{"layers":[{"index":0,"name":"grass","colour":"#5f7a3f","texture":"","tiling":8}]}'
              ' — four of them, by index or in order.',
    "scatter": '{"items":[{"asset":"pine","x":10,"z":-4,"rot":0,"scale":1}]} — y is snapped to '
               'the ground unless you give one. To PAINT them, brush kind:"scatter".',
    "report": '{"maxSlope":30,"channel":0.3} — the default action. Slope histogram, per-layer '
              'coverage, walkable share, scatter on ground too steep for it, triangles, lo and '
              'hi — and, once anything has been eroded, how much of the field drains.',
    "look": '{"views":["top","3q"],"label":"valley"} — the forge\'s own camera, so the picture '
            'matches every other one the Studio takes. A view of "flow" or "cut" false-colours '
            'the drainage instead of the layers: blue to white with rising water, or red where '
            'the water took ground away and blue where it laid it down.',
    "plan": '{"goal":{"walkable":0.7,"maxSlope":30,"relief":[8,40],"coverage":{"grass":0.6},'
            '"budget":24,"ms":4000}} — searches strokes with `report` as the fitness function, '
            'keeping the ones that move the numbers towards the goal and rolling back the ones '
            'that do not. THE FIELD IS CHANGED. The answer is the report either side of it, the '
            'strokes it kept and the count it tried — no picture. {"action":"undo"} puts the '
            'whole plan back.',
    "glb": '{"path":"out/terrain.glb"}',
    "code": '{"path":"src/terrain.js","name":"buildTerrain","engine":"three|playcanvas"} — the '
            'emitted builder, written into the project so the game can call it.',
    "save": '{"name":"valley"}',
    "load": '{"name":"valley"} — or no name, to replay this project\'s journal.',
    "undo": '{} — puts the last brush request back.',
    "clear": '{} — forget the field and take it off the bench.',
}

# What `_apply` knows how to run, and what the journal replays. `undo` and `clear` are neither:
# one edits the journal instead of extending it, and the other throws it away. `plan` mutates but
# is not in `_apply` either — it is journalled as the strokes it KEPT, because a search replayed is
# a search re-run, and a second run of a timed search does not have to reach the same field.
_MUTATES = ("make", "brush", "layers", "scatter", "load")
_JOURNALLED = _MUTATES + ("undo", "plan")


# ---------------------------------------------------------------------------
# The page glue
#
# It holds the field and nothing else: every number in it comes out of terrain.ts. The two things
# it does own are the ones a pure logic module cannot — turning MeshArrays into a mesh for
# whichever engine the page loaded, and drawing the scatter as something you can see.
# ---------------------------------------------------------------------------
TERRAIN_JS = r"""
(() => {
if (window.__terra && window.__terra.version === __TERRAIN_VERSION__) return;
var T = {};
T.version = __TERRAIN_VERSION__;

var M = null;          /* the terrain module, imported once per tab */
var src = '';
var t = null;          /* the field itself */
var nodes = [];        /* what this module put on the bench, so it can take it off again */
var undoStack = [];    /* the LAST brush request's patches, newest applied last */
var strokeCount = 0;
var drawnTris = 0;
var shading = '';
var flowNote = '';     /* why there is no drainage, when there is none */

/* WHAT IS DIRTY. `rects` are sample rectangles the strokes since the last draw wrote — every
   brush answers with exactly one — and `allDirty` is the escape hatch for the things that change
   the whole field at once: a new field, a load, a layer's colour, a change of lod or of overlay.
   Kept here rather than worked out at draw time because only the caller of applyBrush ever sees
   a Patch, and it is thrown away a line later. */
var lastReplay = [];   /* the last plan's strokes, fetched raw: see T.planReplay */
var tiles = null;      /* {lod, flow, res, tile, n, root, engine, nodes: []} */
var rects = [];
var allDirty = true;
var scatterDirty = true;
var scatterNode = null;

var clamp = function (v, a, b) { return v < a ? a : (v > b ? b : v); };
var now = function () {
  return (window.performance && performance.now) ? performance.now() : Date.now();
};

var markAll = function () { allDirty = true; rects = []; };
var markRect = function (p) {
  /* A patch with no rectangle is a stroke that wrote nothing — off the edge of the field, or a
     scatter that placed nobody. It is not an error and it is not a redraw. */
  if (!p || !(p.w > 0) || !(p.h > 0)) return false;
  if (rects.length > 600) { allDirty = true; return true; }
  rects.push([p.x0 | 0, p.z0 | 0, p.w | 0, p.h | 0]);
  return true;
};

/* The tile grid. Eight a side by default: a 32-unit brush on a 513-sample field then dirties four
   tiles of sixty-four, and a full rebuild costs the shared edge rows — about 5% more vertices
   than one mesh, which is the whole price of this. */
var gridFor = function (res, per) {
  var span = Math.max(1, res - 1);
  var want = span / Math.max(1, per);
  var k = 32;
  while (k < want) k *= 2;
  k = Math.min(span, Math.max(1, k));
  return { tile: k, n: Math.ceil(span / k) };
};

var regionOf = function (g, cx, cz, res) {
  var span = res - 1;
  var x0 = cx * g.tile, z0 = cz * g.tile;
  /* INCLUSIVE of the row and column shared with the tile next door. Drop it and a one-cell strip
     of sky shows between two tiles; keep it and the two evaluate the same heights on the seam. */
  return { x0: x0, z0: z0,
           w: Math.min(g.tile, span - x0) + 1, h: Math.min(g.tile, span - z0) + 1 };
};

var dirtyTiles = function (g, lod) {
  /* Padded by the lod, because terrainMesh differences its normals over `lod` cells against the
     whole field: a stroke that stops at the tile edge still turns the light on the samples just
     outside it, and an unpadded rebuild leaves that seam lit differently from its neighbour. */
  var pad = lod + 1, out = {}, k = g.tile;
  for (var r = 0; r < rects.length; r++) {
    var a = rects[r];
    var x0 = a[0] - pad, z0 = a[1] - pad;
    var x1 = a[0] + a[2] - 1 + pad, z1 = a[1] + a[3] - 1 + pad;
    /* A sample on a tile boundary belongs to BOTH tiles, so the low end rounds up and steps back
       one; the high end is a plain floor. */
    var cx0 = Math.max(0, Math.ceil(x0 / k) - 1), cx1 = Math.min(g.n - 1, Math.floor(x1 / k));
    var cz0 = Math.max(0, Math.ceil(z0 / k) - 1), cz1 = Math.min(g.n - 1, Math.floor(z1 / k));
    for (var cz = cz0; cz <= cz1; cz++) {
      for (var cx = cx0; cx <= cx1; cx++) out[cz * g.n + cx] = true;
    }
  }
  return out;
};

var rgbOf = function (hex) {
  var s = String(hex || '#808080').replace('#', '').trim();
  if (s.length === 3) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
  var n = parseInt(s, 16);
  if (!isFinite(n)) n = 0x808080;
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
};

T.load = async function (urls) {
  if (M) return { ok: true, from: src, cached: true };
  var last = '';
  for (var i = 0; i < urls.length; i++) {
    var u = String(urls[i]);
    try {
      /* A tab remembers a failed import for the rest of its life, so every attempt carries a
         fresh query string. The forge library needed exactly this and for the same reason. */
      var m = await import(u + (u.indexOf('?') < 0 ? '?' : '&') + 'v=' + Date.now());
      /* Both shapes. ops.ts carries terrain as a NAMESPACE - `export * as terrain` - so the one
         bundle everything already imports has it under `.terrain`, while a build of terrain.ts on
         its own has it at the top level. Asking for `makeTerrain` and giving up found neither. */
      var got = (m && typeof m.makeTerrain === 'function') ? m
              : (m && m.terrain && typeof m.terrain.makeTerrain === 'function') ? m.terrain : null;
      if (got) {
        M = got; src = u.split('?')[0].split('/').pop() + (got === m ? '' : ' (ops.terrain)');
        return { ok: true, from: src };
      }
      last = u.split('?')[0].split('/').pop() + ' loaded, but it exports no makeTerrain';
    } catch (e) { last = String((e && e.message) || e).slice(0, 240); }
  }
  return { ok: false, error: last || 'nothing to import' };
};

T.state = function () {
  return { has: !!t, module: src, strokes: strokeCount, undo: undoStack.length,
           drawn: nodes.length > 0, triangles: drawnTris,
           tiles: tiles ? tiles.n * tiles.n : 0, dirty: allDirty ? -1 : rects.length };
};

T.spec = function () {
  if (!t) return null;
  var s = t.spec || {};
  var ls = [];
  for (var i = 0; i < (t.layers || []).length; i++) {
    var l = t.layers[i] || {};
    ls.push({ i: i, name: l.name, colour: l.colour, tiling: l.tiling, texture: l.texture || '' });
  }
  return { size: s.size, res: s.res, maxHeight: s.maxHeight, origin: s.origin, seed: s.seed,
           layers: ls, scatter: (t.scatter || []).length, strokes: strokeCount };
};

T.make = function (spec) {
  t = M.makeTerrain(spec);
  strokeCount = 0; undoStack = [];
  markAll();
  return T.spec();
};

T.strokes = function (list, keepUndo) {
  var n = 0, patches = [], missed = 0, wrote = 0, moved = 0, drops = 0;
  var lo = null;
  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    /* Everything except where and for how long IS the brush. Naming the fields here as
       well as in Python made two lists to keep in step, and terrain.ts grew five of them
       (maxSlope, spacing, scaleRange, freq, droplets) in the hour this was written. */
    var b = {};
    for (var k in s) {
      if (k === 'x' || k === 'z' || k === 'seconds') continue;
      if (s[k] !== null && s[k] !== undefined) b[k] = s[k];
    }
    var p = M.applyBrush(t, b, +s.x, +s.z, +s.seconds);
    if (keepUndo && p) patches.push(p);
    /* THE RECTANGLE IS THE REDRAW. Nothing else in the page knows which samples changed, and the
       patch is discarded on the next line, so it is read for the tiles here or not at all. */
    if (markRect(p)) {
      wrote++;
      if (lo === null) lo = { x0: p.x0, z0: p.z0, x1: p.x0 + p.w, z1: p.z0 + p.h };
      else {
        lo.x0 = Math.min(lo.x0, p.x0); lo.z0 = Math.min(lo.z0, p.z0);
        lo.x1 = Math.max(lo.x1, p.x0 + p.w); lo.z1 = Math.max(lo.z1, p.z0 + p.h);
      }
    } else { missed++; }
    var st = p && p.stats;
    if (st) { moved += +st.moved || 0; drops += +st.droplets || 0; }
    n++;
  }
  /* Only the last request is kept. A patch carries the before-image of everything it touched, so
     an unbounded stack of them is an unbounded copy of the field. One request back is the undo an
     agent actually uses: try a stroke, read the numbers, put it back if they got worse. */
  if (keepUndo) undoStack = patches;
  strokeCount += n;
  var out = { applied: n, undo: undoStack.length, wrote: wrote, missed: missed };
  if (moved) out.moved = Math.round(moved * 1000) / 1000;
  if (drops) out.droplets = drops;
  if (lo) {
    out.touched = { x0: lo.x0, z0: lo.z0, w: lo.x1 - lo.x0, h: lo.z1 - lo.z0 };
    out.share = Math.round(1000 * (lo.x1 - lo.x0) * (lo.z1 - lo.z0)
                           / (t.spec.res * t.spec.res)) / 1000;
  }
  return out;
};

T.undo = function () {
  var n = 0;
  for (var i = undoStack.length - 1; i >= 0; i--) {
    M.undoPatch(t, undoStack[i]);
    markRect(undoStack[i]);
    n++;
  }
  undoStack = [];
  strokeCount = Math.max(0, strokeCount - n);
  return { undone: n };
};

T.setLayers = function (list) {
  if (!t.layers) t.layers = [];
  var out = [];
  for (var i = 0; i < list.length; i++) {
    var l = list[i] || {};
    var at = (l.index === null || l.index === undefined) ? i : (l.index | 0);
    if (at < 0 || at > 3) continue;
    while (t.layers.length <= at) {
      t.layers.push({ name: 'layer' + t.layers.length, colour: '#808080', tiling: 8 });
    }
    var cur = t.layers[at];
    t.layers[at] = {
      name: l.name !== undefined && l.name !== null ? String(l.name) : cur.name,
      colour: l.colour !== undefined && l.colour !== null ? String(l.colour) : cur.colour,
      texture: l.texture !== undefined && l.texture !== null ? String(l.texture) : cur.texture,
      tiling: l.tiling !== undefined && l.tiling !== null ? +l.tiling : cur.tiling };
    out.push(at);
  }
  /* A layer's colour is blended into every vertex of the field, so this is the one edit that is
     genuinely global — the tiles cannot save anything here and pretending otherwise would leave
     half the world the old green. */
  markAll();
  return { set: out, layers: T.spec().layers };
};

T.place = function (items) {
  if (!t.scatter) t.scatter = [];
  var added = 0;
  for (var i = 0; i < items.length; i++) {
    var it = items[i] || {};
    var x = +it.x, z = +it.z;
    if (it.at && it.at.length === 3) { x = +it.at[0]; z = +it.at[2]; }
    var y = (it.y === null || it.y === undefined) ? M.heightAt(t, x, z) : +it.y;
    t.scatter.push({ asset: String(it.asset || 'item'), at: [x, y, z],
                     rot: it.rot === undefined || it.rot === null ? 0 : +it.rot,
                     scale: it.scale === undefined || it.scale === null ? 1 : +it.scale });
    added++;
  }
  scatterDirty = true;
  return { added: added, total: t.scatter.length };
};

/* WHERE THE WATER RAN, as numbers. An agent with no picture has no other way to tell that an
   erode stroke did anything at all: the heights moved by centimetres and `walkable` barely
   twitches, while the drainage goes from nothing to a third of the field. */
T.flow = function (channel) {
  var f = flowOf();
  if (!f) return { has: false, why: flowNote };
  var n = f.res * f.res, wet = 0, ch = 0, deep = 0, thick = 0, net = 0, sum = 0;
  var thr = (channel === null || channel === undefined) ? 0.30 : +channel;
  var cut = f.cut;
  for (var i = 0; i < n; i++) {
    var v = f.flow[i];
    if (v > 0) { wet++; sum += v; }
    if (v >= thr) ch++;
    var c = cut ? cut[i] : 0;
    net += c;
    if (c > deep) deep = c;
    if (-c > thick) thick = -c;
  }
  /* DOES THE BUSY GROUND AGREE WITH THE CUT GROUND? A drainage map is only useful if the samples
     carrying the most water are the ones the water carved, because that is the assumption
     `flowLayers` and the river brush are both built on — rock in the channels, silt where the load
     was dropped. It is one more pass over an array already in cache, and it is the only way an
     agent aiming a river finds out that `channel` marks the deposits instead. Measured on a real
     513 field: the busiest samples averaged -3.46 (laid down) and the quiet ones +0.54 (cut). */
  var mean = wet ? sum / wet : 0;
  var hiN = 0, hiC = 0, loN = 0, loC = 0;
  if (mean > 0 && cut) {
    for (var k = 0; k < n; k++) {
      var q = f.flow[k];
      if (q <= 0) continue;
      if (q > mean * 2) { hiN++; hiC += cut[k]; }
      else if (q < mean * 0.5) { loN++; loC += cut[k]; }
    }
  }
  var r3 = function (v) { return Math.round(v * 1000) / 1000; };
  return { has: true, res: f.res, drains: r3(wet / n), channels: r3(ch / n), channel: thr,
           droplets: f.droplets, strokes: f.strokes, peak: r3(f.peak), mean: r3(mean),
           cut_deepest: r3(deep), fill_thickest: r3(thick), net: r3(net),
           busiest_cut: hiN ? r3(hiC / hiN) : null, quietest_cut: loN ? r3(loC / loN) : null };
};

T.report = function (maxSlope, channel) {
  var r = M.report(t, maxSlope), out = {};
  for (var k in r) { if (Object.prototype.hasOwnProperty.call(r, k)) out[k] = r[k]; }
  /* WHERE THE SCATTER IS STANDING. report() counts the items; it cannot know that eleven of them
     are halfway up a cliff, and an agent that painted trees has no other way to find out. */
  var steep = 0, s = t.scatter || [];
  for (var i = 0; i < s.length; i++) {
    var n = M.normalAt(t, s[i].at[0], s[i].at[2]);
    if (Math.acos(clamp(n[1], -1, 1)) * 180 / Math.PI > maxSlope) steep++;
  }
  out.scatter_steep = steep;
  out.drawn_triangles = drawnTris;
  out.flow = T.flow(channel);
  out.spec = T.spec();
  return out;
};

/* SEARCH THE STROKES. terrain.ts owns the search; this owns the consequences of it — the tiles
   the accepted strokes dirtied, and an undo stack so a plan an agent does not like costs one
   POST to put back. The patches themselves never leave the page: they are megabytes of
   before-image, and the envelope would cut them into a lie. */
T.plan = function (goal) {
  if (typeof M.plan !== 'function') {
    return { ok: false, error: 'this build of terrain.ts has no plan()' };
  }
  var r = M.plan(t, goal || {}) || {};
  var ps = r.patches || [];
  for (var i = 0; i < ps.length; i++) markRect(ps[i]);
  var kept = r.steps || [];
  strokeCount += kept.length;
  undoStack = ps.slice();
  /* TWO LISTS, and they are not the same list for a reason a live run found. `steps` is what the
     agent READS — capped, because sixty annotated strokes is already a page of prose and four
     hundred is noise. `replay` is what the JOURNAL stores, and it must be every stroke or a
     rebuilt tab quietly holds a different field: the first run of this kept 62 and journalled the
     60 that fitted the display cap. It is the same strokes without the sentences. */
  var steps = [], replay = [];
  for (var k = 0; k < kept.length; k++) {
    var s = kept[k] || {};
    if (k < __PLAN_STEPS__) {
      steps.push({ brush: s.brush, x: s.x, z: s.z, dt: s.dt, why: s.why, score: s.score });
    }
    if (k < __PLAN_REPLAY__) replay.push({ brush: s.brush, x: s.x, z: s.z, dt: s.dt });
  }
  /* NOT IN THE ANSWER. `__live.short` caps EVERY array at sixty and appends '…+12 more', so a
     72-stroke plan handed its replay list back as 60 strokes and a string — and the journal took
     it, and a reloaded tab would have rebuilt a field twelve strokes short. It is fetched instead
     as one raw JSON string, which is the same door the emitted builder goes through and the only
     one with no cap on it. */
  lastReplay = replay;
  return { ok: true, steps: steps, replay_of: replay.length, of: kept.length, before: r.before,
           after: r.after, tried: r.tried, ms: Math.round(r.ms || 0), met: !!r.met,
           notes: r.notes || [], undo: undoStack.length };
};

T.planReplay = function () {
  try { return JSON.stringify(lastReplay || []); } catch (e) { return '[]'; }
};

/* MeshArrays.colors is RGBA and is ALREADY the layers' colours blended by the splat weights -
   terrain.ts made it so deliberately, because handing a plain vertexColors material the raw
   weights paints layer 0 red. Blending it a second time here averaged four layer colours into
   (0.80, 0.82, 0.80) and rendered a 512-metre valley as a sheet of white paper. Nothing is
   recomputed now; three.js just wants three components instead of four. */
var rgb3 = function (rgba, count) {
  var out = new Float32Array(count * 3);
  for (var v = 0; v < count; v++) {
    out[v * 3] = rgba[v * 4]; out[v * 3 + 1] = rgba[v * 4 + 1]; out[v * 3 + 2] = rgba[v * 4 + 2];
  }
  return out;
};

/* A hex colour is sRGB and a vertex colour attribute is read as LINEAR by both renderers, so
   handing #5f7a3f over unchanged draws it at sRGB 0.75 — the first correct-looking render came
   back with dark olive grass as lime and mid-grey rock as near-white, and the coverage numbers
   under it read as a lie. ASKED, not assumed: a renderer with the transfer switched off wants
   the values as they are, and converting anyway would make everything half as bright. */
var gammaOn = function (ctx) {
  try {
    if (ctx.engine === 'three') {
      var r = ctx.renderer;
      if (r.outputColorSpace !== undefined) return r.outputColorSpace === 'srgb';
      return r.outputEncoding !== undefined && r.outputEncoding !== 3000;   /* LinearEncoding */
    }
    /* PlayCanvas moved the switch and then removed it. In 1.x it is scene.gammaCorrection; in 2.x
       it is per-camera; in the 2.21.3 this was measured on BOTH read undefined and the renderer
       writes sRGB unconditionally. Reading the absent property as "off" left the whole field
       twice as bright as its own hex colours. */
    var cam = ctx.camera && (ctx.camera.camera || ctx.camera);
    if (cam && cam.gammaCorrection !== undefined) return !!cam.gammaCorrection;
    var sc = ctx.app && ctx.app.scene;
    if (sc && sc.gammaCorrection !== undefined) return !!sc.gammaCorrection;
    return parseInt(String((ctx.pc && ctx.pc.version) || '0'), 10) >= 2;
  } catch (e) { return false; }
};

var linearise = function (rgba, count) {
  var out = new Float32Array(rgba.length);
  for (var i = 0; i < count; i++) {
    var o = i * 4;
    for (var k = 0; k < 3; k++) {
      var c = rgba[o + k];
      out[o + k] = c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }
    out[o + 3] = rgba[o + 3];
  }
  return out;
};

/* THE DRAINAGE, IN COLOUR. Not decoration and not a second renderer: the same mesh, with the
   layer colours replaced by the flow field, so the relief is still lit and still readable and the
   hue is the only thing that changed. An agent that cannot see where the water goes cannot aim a
   river, and a `report` that says "34% drains" cannot say WHERE. */
var flowOf = function () {
  flowNote = '';
  if (typeof M.flowField !== 'function') {
    flowNote = 'this build of terrain.ts has no flowField()';
    return null;
  }
  try {
    var f = M.flowField(t);
    if (!f) { flowNote = 'nothing has been eroded yet, so no water has run'; return null; }
    return f;
  } catch (e) {
    flowNote = String((e && e.message) || e).slice(0, 200);
    return null;
  }
};

/* Dry ground is grey, not black: a field where nothing drains has to look like ground rather than
   like a render that failed. */
var DRY = [0.20, 0.21, 0.23];
var WET = [[0.00, 0.09, 0.15, 0.34], [0.35, 0.10, 0.44, 0.78],
           [0.70, 0.26, 0.80, 0.91], [1.00, 0.96, 0.99, 1.00]];
var ramp = function (stops, v) {
  for (var i = 1; i < stops.length; i++) {
    if (v <= stops[i][0] || i === stops.length - 1) {
      var a = stops[i - 1], b = stops[i];
      var f = (v - a[0]) / Math.max(1e-6, b[0] - a[0]);
      f = f < 0 ? 0 : (f > 1 ? 1 : f);
      return [a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f];
    }
  }
  return DRY;
};

/* WHERE THE MIDDLE OF THE RAMP GOES. Normalising against the PEAK looked obviously right and drew
   a solid white disc: a real erode pass leaves most of the eroded ground carrying some water, so
   against the busiest sample almost everything lands in the top of the curve and the gullies stop
   standing out from the ground between them. Keyed to the MEAN of the wet samples instead, with
   q/(q+k), the middle of the picture is the middle of the distribution whatever the distribution
   is — and it is one pass, no sort. */
var flowMid = function (f) {
  var n = f.res * f.res, sum = 0, wet = 0;
  for (var i = 0; i < n; i++) { var v = f.flow[i]; if (v > 0) { sum += v; wet++; } }
  return wet ? Math.max(1e-4, sum / wet) : 1e-4;
};

var paintFlow = function (m, f, mode, scale) {
  var count = m.positions.length / 3, res = t.spec.res, cell = M.cellSize(t.spec);
  var ox = t.spec.origin[0], oz = t.spec.origin[2];
  for (var v = 0; v < count; v++) {
    /* WHICH SAMPLE THIS VERTEX IS, read back out of its own position instead of by repeating
       terrainMesh's stepping. A position is origin + i * cell exactly, so the round is exact —
       and a change to how a region is stepped, or to the lod, cannot silently mis-colour the
       field the way a second copy of the loop would. */
    var i = Math.round((m.positions[v * 3] - ox) / cell);
    var j = Math.round((m.positions[v * 3 + 2] - oz) / cell);
    var o = clamp(j, 0, res - 1) * res + clamp(i, 0, res - 1);
    var c;
    if (mode === 'cut') {
      var d = (f.cut ? f.cut[o] : 0) / scale;
      c = d > 0 ? [0.55 + 0.45 * Math.min(1, d), 0.26 * (1 - Math.min(1, d)) + 0.10, 0.10]
        : d < 0 ? [0.10, 0.32 + 0.20 * Math.min(1, -d), 0.55 + 0.45 * Math.min(1, -d)]
        : DRY;
    } else {
      var q = f.flow[o];
      c = q > 0 ? ramp(WET, q / (q + scale)) : DRY;
    }
    m.colors[v * 4] = c[0]; m.colors[v * 4 + 1] = c[1];
    m.colors[v * 4 + 2] = c[2]; m.colors[v * 4 + 3] = 1;
  }
};

var cutScale = function (f) {
  var n = f.res * f.res, mx = 0;
  for (var i = 0; i < n; i++) { var a = f.cut ? Math.abs(f.cut[i]) : 0; if (a > mx) mx = a; }
  return mx || 1;
};

var PALETTE = ['#3f6b34', '#5a7d3a', '#6b5a3a', '#8a7a52', '#4a6b58', '#7a4a3a',
               '#93a06a', '#54614a'];
var assetColour = function (name) {
  var h = 0, s = String(name || '');
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0x7fffffff;
  return PALETTE[h % PALETTE.length];
};

/* Scatter, as one mesh of little spikes. Not the real assets: resolving a Library id would mean
   building somebody else's model inside a terrain call. A marker per item is enough to answer the
   question the picture is being taken for — where did they land, and did any land in the lake. */
var proxyArrays = function (cap) {
  var items = t.scatter || [], n = Math.min(items.length, cap);
  if (!n) return null;
  var SEG = 5, verts = n * SEG * 3;
  var pos = new Float32Array(verts * 3), nor = new Float32Array(verts * 3);
  var col = new Float32Array(verts * 4);   /* RGBA, the shape terrainMesh answers with */
  var w = 0;
  for (var i = 0; i < n; i++) {
    var it = items[i], sc = +it.scale || 1;
    var h = 2.4 * sc, rad = 0.6 * sc;
    var c = rgbOf(assetColour(it.asset));
    var x = it.at[0], y = it.at[1], z = it.at[2];
    for (var k = 0; k < SEG; k++) {
      var a0 = (k / SEG) * Math.PI * 2, a1 = ((k + 1) / SEG) * Math.PI * 2;
      var p = [x, y + h, z,
               x + Math.cos(a1) * rad, y, z + Math.sin(a1) * rad,
               x + Math.cos(a0) * rad, y, z + Math.sin(a0) * rad];
      var ux = p[3] - p[0], uy = p[4] - p[1], uz = p[5] - p[2];
      var vx = p[6] - p[0], vy = p[7] - p[1], vz = p[8] - p[2];
      var nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      var len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
      nx /= len; ny /= len; nz /= len;
      for (var v = 0; v < 3; v++) {
        pos[w] = p[v * 3]; pos[w + 1] = p[v * 3 + 1]; pos[w + 2] = p[v * 3 + 2];
        nor[w] = nx; nor[w + 1] = ny; nor[w + 2] = nz;
        var cw = (w / 3) * 4;
        col[cw] = c[0]; col[cw + 1] = c[1]; col[cw + 2] = c[2]; col[cw + 3] = 1;
        w += 3;
      }
    }
  }
  var idx = new Uint32Array(verts);
  for (var q = 0; q < verts; q++) idx[q] = q;
  return { positions: pos, normals: nor, colors: col, indices: idx, shown: n,
           verts: verts, triangles: n * SEG };
};

var drop = function (ctx, nd) {
  if (!nd) return;
  try {
    if (ctx.engine === 'three') {
      if (nd.parent) nd.parent.remove(nd);
      if (nd.geometry && nd.geometry.dispose) nd.geometry.dispose();
      if (nd.material && nd.material.dispose) nd.material.dispose();
    } else {
      if (nd.parent) nd.parent.removeChild(nd);
      if (nd.destroy) nd.destroy();
    }
  } catch (e) {}
};

var strip = function (ctx) {
  for (var i = 0; i < nodes.length; i++) drop(ctx, nodes[i]);
  nodes = [];
  tiles = null;
  scatterNode = null;
  scatterDirty = true;
};

var threeNode = function (ctx, arrays, name) {
  var rgb = rgb3(arrays.colors, arrays.positions.length / 3);
  var TH = ctx.THREE;
  var g = new TH.BufferGeometry();
  g.setAttribute('position', new TH.BufferAttribute(arrays.positions, 3));
  if (arrays.normals) g.setAttribute('normal', new TH.BufferAttribute(arrays.normals, 3));
  if (arrays.uvs) g.setAttribute('uv', new TH.BufferAttribute(arrays.uvs, 2));
  g.setAttribute('color', new TH.BufferAttribute(rgb, 3));
  if (arrays.indices) g.setIndex(new TH.BufferAttribute(arrays.indices, 1));
  if (!arrays.normals) g.computeVertexNormals();
  var mat = new TH.MeshStandardMaterial({ vertexColors: true, roughness: 0.94, metalness: 0.0 });
  var mesh = new TH.Mesh(g, mat);
  mesh.name = name;
  shading = 'vertex colours';
  return mesh;
};

var pcNode = function (ctx, arrays, name) {
  var pc = ctx.pc, mesh = new pc.Mesh(ctx.device);
  mesh.setPositions(arrays.positions);
  if (arrays.normals) mesh.setNormals(arrays.normals);
  if (arrays.uvs) mesh.setUvs(0, arrays.uvs);
  mesh.setColors(arrays.colors);
  if (arrays.indices) mesh.setIndices(arrays.indices);
  mesh.update(pc.PRIMITIVE_TRIANGLES);
  var mat = new pc.StandardMaterial();
  /* Asked, not assumed. The property moved between engine majors, and a run that silently
     rendered one flat grey while reporting "vertex colours" is a wrong answer, not a bad
     picture. When it is missing the ground at least takes layer 0's colour. */
  if ('diffuseVertexColor' in mat) {
    mat.diffuse = new pc.Color(1, 1, 1);
    mat.diffuseVertexColor = true;
    shading = 'vertex colours';
  } else {
    var c = rgbOf((((t.layers || [])[0]) || {}).colour || '#77855f');
    mat.diffuse = new pc.Color(c[0], c[1], c[2]);
    shading = 'flat: this PlayCanvas has no diffuseVertexColor, so the layers are not shown';
  }
  try { mat.useMetalness = false; } catch (e) {}
  mat.update();
  var mi = new pc.MeshInstance(mesh, mat);
  var ent = new pc.Entity(name);
  ent.addComponent('render', { meshInstances: [mi] });
  return ent;
};

T.draw = function (lod, proxyCap, opts) {
  opts = opts || {};
  var t0 = now();
  var F = window.__forge;
  if (!F || !F.ctx) return { ok: false, error: 'the forge studio is not up' };
  var ctx = F.ctx();
  if (!ctx || !ctx.engine) return { ok: false, error: 'the forge studio is not up' };
  lod = (lod && lod > 1) ? Math.floor(lod) : 1;
  var res = t.spec.res;
  var mode = String(opts.flow || '');
  if (mode !== 'flow' && mode !== 'cut') mode = '';
  var fld = mode ? flowOf() : null;
  var asked = mode;
  if (mode && !fld) mode = '';
  var scale = !mode ? 1 : (mode === 'cut' ? cutScale(fld) : flowMid(fld));
  /* `whole` is the round-one path, kept because it is the thing the tiles are measured against
     and because a renderer that cannot hold sixty-four meshes should still get its field. */
  var g = opts.whole ? { tile: res - 1, n: 1 }
                     : gridFor(res, Math.max(1, (opts.tiles | 0) || __TILES__));

  /* A NEW STUDIO IS A NEW SCENE. `__forge.ensure` disposes and rebuilds when the canvas size or
     the engine changes, and the tiles from before are then held by nothing — kept in this list,
     drawn nowhere. Comparing the root identity is what catches it; counting nodes does not. */
  var fresh = !tiles || tiles.lod !== lod || tiles.flow !== mode || tiles.res !== res
              || tiles.tile !== g.tile || tiles.n !== g.n
              || tiles.root !== ctx.root || tiles.engine !== ctx.engine;
  if (fresh) {
    strip(ctx);
    tiles = { lod: lod, flow: mode, res: res, tile: g.tile, n: g.n,
              root: ctx.root, engine: ctx.engine, nodes: [], tris: [], verts: [] };
    allDirty = true;
  }
  var want = allDirty ? null : dirtyTiles(g, lod);
  var total = g.n * g.n;
  var gamma = gammaOn(ctx);
  shading = '';

  var rebuilt = 0, rebuiltTris = 0, verts = 0, tri = 0;
  for (var idx = 0; idx < total; idx++) {
    var have = tiles.nodes[idx];
    if (have && want && !want[idx]) { verts += tiles.verts[idx]; tri += tiles.tris[idx]; continue; }
    var reg = regionOf(g, idx % g.n, Math.floor(idx / g.n), res);
    var m = M.terrainMesh(t, { lod: lod, region: reg });
    var count = m.positions.length / 3;
    if (mode) paintFlow(m, fld, mode, scale);
    if (gamma) m.colors = linearise(m.colors, count);
    var nd = ctx.engine === 'three' ? threeNode(ctx, m, 'terrain-' + idx)
                                    : pcNode(ctx, m, 'terrain-' + idx);
    drop(ctx, have);
    if (have) { var at = nodes.indexOf(have); if (at >= 0) nodes.splice(at, 1); }
    ctx.add(nd);
    nodes.push(nd);
    tiles.nodes[idx] = nd;
    tiles.verts[idx] = count;
    tiles.tris[idx] = Math.round((m.indices ? m.indices.length : count) / 3);
    verts += count; tri += tiles.tris[idx];
    rebuilt++; rebuiltTris += tiles.tris[idx];
  }

  var px = null;
  if (scatterDirty || !scatterNode || fresh) {
    px = proxyArrays(proxyCap || 0);
    drop(ctx, scatterNode);
    if (scatterNode) { var sa = nodes.indexOf(scatterNode); if (sa >= 0) nodes.splice(sa, 1); }
    scatterNode = null;
    if (px) {
      if (gamma) px.colors = linearise(px.colors, px.verts);
      scatterNode = ctx.engine === 'three' ? threeNode(ctx, px, 'scatter')
                                           : pcNode(ctx, px, 'scatter');
      ctx.add(scatterNode);
      nodes.push(scatterNode);
      tiles.scatterTris = px.triangles;
      tiles.scatterShown = px.shown;
    } else { tiles.scatterTris = 0; tiles.scatterShown = 0; }
    scatterDirty = false;
  }
  tri += tiles.scatterTris || 0;
  drawnTris = tri;
  rects = [];
  allDirty = false;

  if (gamma) shading += ', sRGB to linear';
  if (mode) {
    shading = (mode === 'cut'
      ? 'FALSE COLOUR: red where the water took ground away, blue where it laid it down, grey at '
        + 'zero, full red at ' + (Math.round(scale * 100) / 100) + ' world units'
      : 'FALSE COLOUR: the drainage, grey where no water ran and blue through cyan to white with '
        + 'rising flow, mid-ramp at ' + (Math.round(scale * 10000) / 10000) + ' of peak - the '
        + 'MEAN of the wet samples, so the middle of the picture is the middle of the field')
      + ' - these are NOT the layer colours' + (gamma ? ', sRGB to linear' : '');
  }
  var out = { ok: true, triangles: drawnTris, vertices: verts, lod: lod,
              tiles: total, rebuilt: rebuilt, rebuilt_triangles: rebuiltTris,
              ms: Math.round((now() - t0) * 10) / 10,
              scatter_shown: tiles.scatterShown || 0, scatter_total: (t.scatter || []).length,
              shading: shading };
  if (asked && !mode) out.flow_missing = flowNote;
  if (mode) out.flow = mode;
  return out;
};

T.dump = function () { return M.serialize(t); };
T.restore = function (s) {
  t = M.deserialize(s); strokeCount = 0; undoStack = [];
  markAll(); scatterDirty = true;
  return T.spec();
};
T.emit = function (name, engine) { return M.emitBuilder(t, name, engine); };
T.collider = function () { var c = M.collider(t); return { kind: c.kind, res: c.res, size: c.size,
                                                           maxHeight: c.maxHeight, origin: c.origin }; };

T.forget = function () {
  var F = window.__forge;
  if (F && F.ctx) { try { strip(F.ctx()); } catch (e) {} }
  t = null; undoStack = []; strokeCount = 0; drawnTris = 0;
  markAll();
  return { ok: true };
};

window.__terra = T;
})()
"""


def _script() -> str:
    return (TERRAIN_JS.replace("__TERRAIN_VERSION__", str(TERRAIN_VERSION))
            .replace("__TILES__", str(_TILES))
            .replace("__PLAN_STEPS__", str(_PLAN_STEPS))
            .replace("__PLAN_REPLAY__", str(_PLAN_REPLAY)))


async def _install(live) -> str:
    """Put the current glue in the page, replacing an older one. See `_forge_script` for why."""
    have = await live.raw("window.__terra ? (window.__terra.version || 0) : -1")
    try:
        have = int(have)
    except (TypeError, ValueError):
        have = 0
    if have == TERRAIN_VERSION:
        return "current"
    if have != -1:
        # The old glue's meshes are held in ITS closure, so a replacement cannot see them and the
        # bench would keep a ghost terrain under the new one. The outgoing script takes its own
        # nodes off first — the same dispose-then-replace `_forge_script` does, for the same
        # reason. The field itself is rebuilt from the journal a moment later.
        await live.raw("try { window.__terra && window.__terra.forget && window.__terra.forget(); }"
                       " catch (e) {}")
    await live.raw(_script())
    return "installed" if have == -1 else "replaced"


async def _call(live, expr: str, depth: int = 8) -> dict:
    """One bench call, with the throw and the console records that came with it.

    Every terrain call goes through this rather than `live.ask`, because the interesting failure
    here is a throw from terrain.ts — `applyBrush is not built yet`, a bad layer index, a res the
    module refuses — and a bare `ask` turns all of those into `null`.
    """
    body = ("(async()=>{const __m=__live.mark();let __v,__e;"
            "try{__v=await (%s);}catch(err){__e=String((err&&err.stack)||err);}"
            "return JSON.stringify({v:__live.cap(__v,%d,%d),e:__e,c:__live.since(__m)});})()"
            "\n//# sourceURL=studio-terrain.js" % (expr, int(depth), _MAX_VALUE))
    try:
        return json.loads(await live.raw(body) or "{}")
    except Exception as ex:
        return {"e": str(ex)[:600]}


def _console(rows: Any) -> list:
    """Errors first, then warnings. The same ranking the forge answers with."""
    if not isinstance(rows, list):
        return []
    rank = {"error": 0, "promise": 0, "resource": 0, "net": 0, "warn": 1}
    return sorted(rows, key=lambda r: rank.get((r or {}).get("kind"), 2))[:20]


# ---------------------------------------------------------------------------
# What is on disk, per project
# ---------------------------------------------------------------------------
def _dir(project: str) -> Path:
    return _LIVE_DIR / _slug(project) / "terrain"


def _save_file(project: str, name: str) -> Path:
    safe = "".join(c for c in str(name or "terrain") if c.isalnum() or c in "-_") or "terrain"
    return _dir(project) / (safe + ".terrain.json")


def _write(path: Path, text: str) -> None:
    """Temp file then replace, always. A half-written field is worse than no field."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _journal(project: str) -> list:
    p = _dir(project) / "journal.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _journal_set(project: str, rows: list) -> None:
    _write(_dir(project) / "journal.json", json.dumps(rows)[:8_000_000])


def _journal_add(project: str, entry: dict) -> list:
    rows = _journal(project)
    rows.append(entry)
    _journal_set(project, rows)
    return rows


# ---------------------------------------------------------------------------
# Turning a request into strokes
# ---------------------------------------------------------------------------
def _snap_res(n: int) -> int:
    """The nearest 2^k + 1. The contract wants it so the centre of a quad-tree split is a sample."""
    n = max(33, min(_MAX_RES, int(n or 0) or 257))
    k = 5
    while (1 << k) + 1 < n:
        k += 1
    lo = (1 << (k - 1)) + 1
    return lo if k > 5 and (n - lo) < ((1 << k) + 1 - n) else (1 << k) + 1


# Every field the Brush contract carries, READ OUT OF THE CONTRACT. Not a filter — a stroke is
# forwarded whole either way, and this is only what a misspelling is measured against. It is read
# rather than listed because listing it was wrong twice in one hour: terrain.ts gained maxSlope,
# spacing, scaleRange, freq, droplets, then minHeight and maxHeight, while this was being written.
# The literal below is the fallback for an install with no frontend source beside it.
_CONTRACT = (Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "components" /
             "engine" / "edit" / "terrain.ts")
_FALLBACK_FIELDS = {"kind", "radius", "strength", "falloff", "target", "layer", "asset", "density",
                    "jitter", "maxSlope", "minHeight", "maxHeight", "spacing", "scaleRange",
                    "freq", "droplets", "talus", "flow", "depth", "channel"}
_STROKE_ONLY = {"x", "z", "seconds", "to", "steps"}


def _brush_fields() -> set:
    global _BRUSH_FIELDS
    if _BRUSH_FIELDS is None:
        named = set()
        try:
            body = _CONTRACT.read_text(encoding="utf-8").split("export interface Brush {", 1)
            if len(body) == 2:
                lines = []
                for line in body[1].splitlines():
                    if line.startswith("}"):
                        break
                    lines.append(line)
                named = set(re.findall(r"^\s{2}(\w+)\??:", "\n".join(lines), re.M))
        except OSError:
            pass
        _BRUSH_FIELDS = named or set(_FALLBACK_FIELDS)
    return _BRUSH_FIELDS


_BRUSH_FIELDS: Optional[set] = None


def _stroke(r: dict) -> tuple[dict, list]:
    b = {"kind": str(r.get("kind") or "raise"),
         "x": float(r.get("x") or 0.0), "z": float(r.get("z") or 0.0),
         "radius": float(r.get("radius") or 20.0),
         "strength": float(r.get("strength") if r.get("strength") is not None else 0.5),
         "falloff": float(r.get("falloff") if r.get("falloff") is not None else 0.6),
         "seconds": float(r.get("seconds") if r.get("seconds") is not None else 1.0)}
    odd = []
    for k, v in r.items():
        if k in _STROKE_ONLY or v is None:
            continue
        b[k] = v
        if k not in _brush_fields():
            odd.append(k)
    return b, odd


def _expand(rows: list) -> tuple[list, list]:
    """Strokes as the page will get them, plus anything worth telling the caller.

    `to` is the whole reason this exists. A valley is a LINE, and a line drawn as one dab is a
    crater; drawn as eight separate requests it is eight turns of the model. So a stroke with a
    destination becomes a run of steps here, spaced at half a radius so the edges overlap, and
    its `seconds` are SPREAD along the run — a drag is one dose of brush, not one dose per step.
    """
    out: list = []
    notes: list = []
    unknown: set = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        b, odd = _stroke(r)
        unknown.update(odd)
        to = r.get("to")
        if not (isinstance(to, (list, tuple)) and len(to) >= 2):
            out.append(b)
            continue
        x1, z1 = float(to[0]), float(to[-1] if len(to) == 2 else to[2])
        dist = math.hypot(x1 - b["x"], z1 - b["z"])
        auto = int(math.ceil(dist / max(1e-6, b["radius"] * 0.5))) + 1
        steps = int(r.get("steps") or 0) or auto
        steps = max(2, min(_MAX_STEPS, steps))
        each = b["seconds"] / steps
        for i in range(steps):
            f = i / float(steps - 1)
            s = dict(b)
            s["x"] = b["x"] + (x1 - b["x"]) * f
            s["z"] = b["z"] + (z1 - b["z"]) * f
            s["seconds"] = each
            out.append(s)
        notes.append("%s dragged %.0f units became %d steps" % (b["kind"], dist, steps))
    if unknown:
        # Sent on regardless: a field this does not recognise may be one terrain.ts gained
        # today. But a misspelt `radius` silently doing nothing is an hour of an agent's life.
        notes.append("brush fields the contract does not name, sent anyway: %s"
                     % ", ".join(sorted(unknown)))
    return out[:_MAX_STROKES], notes


# ---------------------------------------------------------------------------
# The goal a plan is searching for, and the overlay a look is asking for
# ---------------------------------------------------------------------------
_GOAL_KEYS = ("walkable", "maxSlope", "relief", "coverage", "region", "budget", "ms", "seed")


def _goal(o: dict) -> dict:
    """The PlanGoal, from `goal` or from flat fields of the same names.

    Both, because the two ways of writing it are equally natural and neither is worth an error:
    `{"goal":{"walkable":0.7}}` reads like the contract, `{"walkable":0.7}` reads like the rest of
    this endpoint. A nested key wins, so the two cannot disagree in silence.
    """
    g = dict(o["goal"]) if isinstance(o.get("goal"), dict) else {}
    for k in _GOAL_KEYS:
        v = o.get(k)
        if k in g or v is None or v == "" or v == [] or v == {}:
            continue
        if k == "maxSlope" and float(v or 0) == 30.0 and "maxSlope" not in (o.get("goal") or {}):
            continue                      # the body's own default, not something anybody asked for
        g[k] = v
    return g


def _wants(g: dict) -> bool:
    """Is there anything in here to search FOR? A goal of nothing is a budget spent on nothing."""
    return any(g.get(k) not in (None, "", [], {}) for k in ("walkable", "relief", "coverage"))


def _flow_mode(o: dict, views: list) -> tuple[str, list]:
    """(the overlay, the views left over).

    `flow` as a VIEW is not a pun. The forge already spells "show me this differently" as a pass
    or a view name, and it is the one spelling that survives the trip: the router's body model
    names its fields one by one, so a key it has never heard of is dropped by pydantic before this
    module sees it, while `views` is a list of strings that arrives whole.
    """
    mode = str(o.get("flow") or "").strip().lower()
    if mode in ("true", "1", "yes", "on"):
        mode = "flow"
    keep = []
    for v in views:
        s = str(v).strip().lower()
        if s in _FLOW_MODES:
            mode = mode or s
        else:
            keep.append(v)
    return (mode if mode in _FLOW_MODES else ""), keep


# ---------------------------------------------------------------------------
# What the numbers mean
# ---------------------------------------------------------------------------
def _flow_findings(rep: dict, drew: dict) -> list:
    """The drainage, said out loud. The only way an agent with no picture knows erode did anything.

    An erode stroke moves the ground by centimetres. `walkable` barely twitches and `lo`/`hi` do
    not move at all, so round one could run forty droplet passes and report numbers that looked
    identical to no passes at all. The flow field is the thing that changed.
    """
    out: list = []
    f = rep.get("flow") or {}
    if f.get("has"):
        line = ("%d%% of the field drains and %.1f%% of it is channel at %g of peak flow, from "
                "%s droplets over %d erode stroke%s"
                % (round(float(f.get("drains") or 0) * 100),
                   float(f.get("channels") or 0) * 100, f.get("channel"),
                   "{:,}".format(int(f.get("droplets") or 0)), int(f.get("strokes") or 0),
                   "" if int(f.get("strokes") or 0) == 1 else "s"))
        cut, fill = float(f.get("cut_deepest") or 0), float(f.get("fill_thickest") or 0)
        if cut or fill:
            line += "; deepest cut %.2f, thickest deposit %.2f world units" % (cut, fill)
        out.append(line + ".")
        if float(f.get("drains") or 0) < 0.02:
            out.append("almost nothing drains: the erode stroke was too small or too weak to "
                       "carve anything you will see.")
        if not f.get("channels"):
            out.append("no sample reaches the channel threshold, so a river brush has nothing to "
                       "follow yet - erode harder, or lower `channel`.")
        # WHERE THE BUSY GROUND IS, against where the cut ground is. A river brush and `flow:true`
        # painting both assume the two are the same place. When they are not, everything aimed at
        # `channel` lands on the silt, and nothing in the numbers above would have said so.
        hi, lo = f.get("busiest_cut"), f.get("quietest_cut")
        if isinstance(hi, (int, float)) and isinstance(lo, (int, float)) and hi < 0 <= lo:
            out.append("CAREFUL: the busiest samples are where the pass LAID GROUND DOWN (mean "
                       "%.2f world units) and the quiet ones are where it cut (%.2f). In this "
                       "field `flow` marks the deposits, so a river brush or paint aimed at "
                       "`channel` follows the silt and not the gullies." % (hi, lo))
        elif isinstance(hi, (int, float)) and hi > 0:
            out.append("the busiest samples are also the ones cut deepest (mean %.2f world "
                       "units), which is what a river brush wants." % hi)
    elif f.get("why"):
        out.append("no drainage: %s." % f["why"])
    if drew.get("flow"):
        out.append("THE PICTURE IS FALSE-COLOURED (%s), not the layers - %s"
                   % (drew["flow"], drew.get("shading") or ""))
    if drew.get("flow_missing"):
        out.append("a %s overlay was asked for and drawn without one: %s."
                   % ("drainage", drew["flow_missing"]))
    return out


def _plan_findings(goal: dict, before: dict, after: dict, pr: dict) -> list:
    """Every part of the goal, before and after, against what was asked for.

    A search that answers only "met: false" has told the caller nothing it can act on. Which part
    moved, which part did not, and by how much, is the difference between a next request and a
    shrug.
    """
    out: list = []

    def band(name, b, a, target, fmt="%.2f"):
        arrow = (fmt + " -> " + fmt) % (b, a)
        return "%s %s (asked for %s)" % (name, arrow, target)

    if goal.get("walkable") is not None:
        w = float(goal["walkable"])
        out.append(band("walkable", float(before.get("walkable") or 0),
                        float(after.get("walkable") or 0), "%.2f" % w)
                   + (": met." if float(after.get("walkable") or 0) >= w - 0.005 else ": short."))
    rel = goal.get("relief")
    if isinstance(rel, (list, tuple)) and len(rel) == 2:
        rb = float(before.get("hi") or 0) - float(before.get("lo") or 0)
        ra = float(after.get("hi") or 0) - float(after.get("lo") or 0)
        ok = float(rel[0]) - 0.01 <= ra <= float(rel[1]) + 0.01
        out.append(band("relief", rb, ra, "%g to %g" % (float(rel[0]), float(rel[1])), "%.1f")
                   + (": met." if ok else ": outside the band."))
    cov = goal.get("coverage") or {}
    if isinstance(cov, dict):
        bs = {str(r.get("layer")): float(r.get("share") or 0) for r in before.get("coverage") or []}
        as_ = {str(r.get("layer")): float(r.get("share") or 0) for r in after.get("coverage") or []}
        for name, want in cov.items():
            out.append(band("layer '%s'" % name, bs.get(str(name), 0.0), as_.get(str(name), 0.0),
                            "%.2f" % float(want))
                       + (": met." if as_.get(str(name), 0.0) >= float(want) - 0.005
                          else ": short."))
    # WHAT THE FITNESS FUNCTION DOES NOT MEASURE. Looked at once: a plan that met grass 0.56 and
    # rock 0.24 exactly had painted three overlapping discs across a hillside. Every number in the
    # goal was green and the ground was not shippable, because `coverage` counts HOW MUCH of a
    # layer there is and nothing counts whether it follows the ground.
    paints = sum(1 for s in pr.get("steps") or []
                 if str(((s or {}).get("brush") or {}).get("kind")) == "paint")
    if paints and cov:
        out.append("the coverage was reached with %d paint stroke%s, which are discs. `coverage` "
                   "is a share, not a pattern: nothing in this report can tell you whether the "
                   "layers follow the ground. Spend one look before you ship it, or paint with "
                   '{"kind":"paint","flow":true} so the drainage places them.'
                   % (paints, "" if paints == 1 else "s"))
    kept, tried = int(pr.get("of") or len(pr.get("steps") or [])), int(pr.get("tried") or 0)
    out.append("%d stroke%s kept out of %d tried, in %d ms%s."
               % (kept, "" if kept == 1 else "s", tried, int(pr.get("ms") or 0),
                  "" if pr.get("met") else " - and it stopped short"))
    for n in (pr.get("notes") or [])[:6]:
        out.append(str(n))
    if kept:
        out.append('every one of them is on the field now; {"action":"undo"} puts the whole plan '
                   "back.")
    return out


def _findings(rep: dict, drew: dict, max_slope: float) -> list:
    """The report, read out loud. Same job as the forge's findings: say it before it is looked at."""
    out: list = []
    spec = rep.get("spec") or {}
    lo, hi = float(rep.get("lo") or 0.0), float(rep.get("hi") or 0.0)
    mx = float(spec.get("maxHeight") or 0.0)
    if mx and (hi - lo) < 0.01 * mx:
        out.append("the field is flat: nothing has moved it yet.")
    elif mx and hi >= mx * 0.995:
        out.append("the highest point is at maxHeight (%g): the field is clipping, so raise "
                   "maxHeight or lower the strokes." % mx)
    walk = rep.get("walkable")
    if isinstance(walk, (int, float)):
        line = "%d%% of the ground is walkable at %g degrees" % (round(walk * 100), max_slope)
        if walk < 0.5:
            line += " - more than half of it cannot be stood on"
        out.append(line + ".")
    slopes = rep.get("slopes") or []
    if len(slopes) >= 5 and float(slopes[4]) > 0.15:
        out.append("%d%% of the field is over 45 degrees, which reads as cliff, not hill."
                   % round(float(slopes[4]) * 100))
    for row in rep.get("coverage") or []:
        if isinstance(row, dict) and float(row.get("share") or 0) <= 0.0005:
            out.append("layer '%s' covers nothing: no paint stroke has reached it."
                       % row.get("layer"))
    steep, total = int(rep.get("scatter_steep") or 0), int(rep.get("scatter") or 0)
    if steep:
        out.append("%d of %d scattered items stand on ground steeper than %g degrees."
                   % (steep, total, max_slope))
    if drew.get("lod", 1) > 1:
        out.append("drawn at lod %d (%s triangles): the picture is coarser than the field."
                   % (drew["lod"], "{:,}".format(int(drew.get("triangles") or 0))))
    if drew.get("scatter_total", 0) > drew.get("scatter_shown", 0):
        out.append("the picture shows %d of %d scattered items."
                   % (drew.get("scatter_shown", 0), drew.get("scatter_total", 0)))
    if "flat:" in str(drew.get("shading") or ""):
        out.append(str(drew["shading"]) + ".")
    tiles, rebuilt = int(drew.get("tiles") or 0), int(drew.get("rebuilt") or 0)
    if tiles > 1 and rebuilt < tiles:
        out.append("redrew %d of %d tiles - %s of %s triangles - in %g ms."
                   % (rebuilt, tiles, "{:,}".format(int(drew.get("rebuilt_triangles") or 0)),
                      "{:,}".format(int(drew.get("triangles") or 0)), drew.get("ms")))
    return out + _flow_findings(rep, drew)


def _inside(project: str, path: str, default_name: str) -> tuple[Optional[Path], str]:
    """A path the caller may write to, resolved inside the project. (path, why-not)."""
    root = Path(project).resolve()
    raw = str(path or "").strip()
    if not raw:
        sub = root / "src"
        target = (sub if sub.is_dir() else root) / default_name
    else:
        target = Path(raw)
        if not target.is_absolute():
            target = root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, ("that path is outside the project: %s. Terrain writes into the game, not "
                      "next to it." % target)
    return target, ""


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------
@_settles
def terrain(project: str, opts: Optional[dict] = None) -> dict:
    o = dict(opts or {})
    action = str(o.get("action") or "report").strip().lower()
    bad = _guard(project, forge=True)
    if bad:
        return bad
    if action in ("help", "actions"):
        return {"ok": True, "actions": _SHAPES}
    if action not in _SHAPES:
        return {"ok": False, "error": "no such terrain action: %s" % (action or "(none)"),
                "actions": _SHAPES}

    max_slope = float(o.get("maxSlope") or 30.0)
    strokes, stroke_notes = ([], [])
    if action == "brush":
        strokes, stroke_notes = _expand(o.get("strokes") or [])
        if not strokes:
            return {"ok": False, "error": "brush needs strokes", "shape": _SHAPES["brush"]}
    if action == "layers" and not (o.get("layers") or []):
        return {"ok": False, "error": "layers needs layers", "shape": _SHAPES["layers"]}
    if action == "scatter" and not (o.get("items") or []):
        return {"ok": False, "error": "scatter needs items, or brush kind:\"scatter\" to paint "
                                      "them", "shape": _SHAPES["scatter"]}
    goal: dict = {}
    if action == "plan":
        goal = _goal(o)
        if not _wants(goal):
            return {"ok": False, "shape": _SHAPES["plan"],
                    "error": "plan needs something to aim at: walkable, relief or coverage. If "
                             "you did send a goal and it did not arrive, TerrainBody in "
                             "routers/live.py names its fields one by one and pydantic drops the "
                             "ones it has never heard of - `goal` is not one of them yet."}
    views = [str(v) for v in (o.get("views") or []) if str(v).strip()]
    flow, views = _flow_mode(o, views)

    spec = {}
    if action == "make":
        rr = _snap_res(int(o.get("res") or 0) or 257)
        spec = {"size": float(o.get("size") or 0) or 512.0, "res": rr,
                "maxHeight": float(o.get("maxHeight") or 0) or 48.0,
                "seed": int(o.get("seed") or 1),
                "origin": [float(v) for v in (o.get("origin") or [0, 0, 0])][:3] or [0, 0, 0]}
        if int(o.get("res") or 0) and int(o.get("res")) != rr:
            stroke_notes.append("res %d is not 2^n+1, so %d was used"
                                % (int(o.get("res")), rr))

    out_path: Optional[Path] = None
    if action == "code":
        out_path, why = _inside(project, o.get("path") or "",
                                (str(o.get("name") or "buildTerrain")) + ".js")
        if why:
            return {"ok": False, "error": why}
    if action == "glb":
        out_path, why = _inside(project, o.get("path") or "", "terrain.glb")
        if why:
            return {"ok": False, "error": why}

    try:
        e, want_url = _forge_open(project)
    except Exception as ex:
        return {"ok": False, "error": "could not find a way to serve the project: %s" % ex}

    # ops FIRST. `forge-ops.js` re-exports terrain as a namespace and is the bundle everything
    # else already imports, so it is the one that gets rebuilt. The side bundle is a fallback for
    # a checkout where ops has not been rebuilt yet — and it is only a fallback, because
    # `vite build` empties dist/ and deleted it once while this was being written.
    base = _live._self_base().rstrip("/")
    urls = [base + "/forge-ops.js", base + "/forge-terrain.js"]
    ensure = json.dumps(dict({"width": int(o.get("width") or 0) or 720,
                              "height": int(o.get("height") or 0) or 540,
                              "background": str(o.get("background") or "#1a1e26"),
                              "ground": False, "engine": str(o.get("engine") or ""), "sky": True},
                             **_look_state(e, _studio_opts())))
    lod = int(o.get("lod") or 0)
    draw = bool(o.get("draw")) if o.get("draw") is not None else True
    # `whole` is the round-one redraw, kept as a switch rather than deleted: it is what the tiled
    # one is measured against, and it is the answer if a renderer ever chokes on sixty-four meshes.
    draw_opts = {"flow": flow, "whole": bool(o.get("whole")),
                 "tiles": int(o.get("tiles") or 0) or _TILES}
    channel = float(o.get("channel") or 0.30)
    journal = _journal(project)
    # `look` and `glb` hand off to functions that open an activity row of their own, and a row is
    # only ever closed by the thread that opened it — so this one is closed before the handoff.
    # An unclosed row is what left the Engine window saying "building" for 27 minutes.
    _doing(project, "terrain", action, str(o.get("label") or ""))

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % ensure)
            if not (built or {}).get("ok"):
                return {"built": built}
            await _install(live)
            loaded = await _call(live, "__terra.load(%s)" % json.dumps(urls), depth=4)
            mod = loaded.get("v") or {}
            if not mod.get("ok"):
                return {"built": built, "module": loaded}
            state = (await _call(live, "__terra.state()", depth=4)).get("v") or {}

            replayed = 0
            if not state.get("has") and action not in ("make", "load", "clear") and journal:
                # THE TAB WAS RELOADED OR REAPED. Everything needed to rebuild the field is on
                # disk and the maths is deterministic, so it is rebuilt rather than reported as
                # lost. Silent would be wrong; the answer says how many actions it cost.
                for row in journal:
                    r = await _apply(live, project, row, keep_undo=False)
                    if r.get("e"):
                        return {"built": built, "replay_failed": r, "at": replayed}
                    replayed += 1

            ran: dict = {}
            res_replay = ""
            if action in _MUTATES:
                entry = _entry_for(action, o, spec, strokes)
                ran = await _apply(live, project, entry, keep_undo=(action == "brush"))
                if ran.get("e"):
                    return {"built": built, "ran": ran, "replayed": replayed}
            elif action == "undo":
                ran = await _call(live, "__terra.undo()", depth=4)
                if ran.get("e"):
                    return {"built": built, "ran": ran, "replayed": replayed}
            elif action == "plan":
                # Deeper than the rest: a step carries a whole Brush inside it, and a report
                # carries a list of coverage rows. Flattened any shallower, the answer is the
                # word "Object" where the strokes should be.
                ran = await _call(live, "__terra.plan(%s)" % json.dumps(goal), depth=7)
                if ran.get("e"):
                    return {"built": built, "ran": ran, "replayed": replayed}
                # RAW, past the envelope, for the same reason `code` is: every array in an
                # enveloped answer is cut at sixty items.
                res_replay = await live.raw("__terra.planReplay()")
            elif action == "clear":
                return {"built": built, "cleared": await _call(live, "__terra.forget()", depth=2),
                        "at": await live.ask("__forge.at()", depth=4) or {}}

            state = (await _call(live, "__terra.state()", depth=4)).get("v") or {}
            if not state.get("has"):
                return {"built": built, "empty": True, "replayed": replayed}

            # LOD is decided here and not by the caller, because the caller does not know the res
            # until `make` has answered. A 1025-sample field is 2.1M triangles, which draws but
            # takes about a second every stroke for a picture nobody can read anyway.
            use_lod = lod
            if use_lod < 1:
                sp = (await _call(live, "__terra.spec()", depth=4)).get("v") or {}
                r = int(sp.get("res") or 257)
                use_lod = max(1, int(math.ceil(math.sqrt(max(1.0, (r * r * 2.0) / _LOD_BUDGET)))))

            drew = {}
            if draw:
                d = await _call(live, "__terra.draw(%d,%d,%s)"
                                % (use_lod, _PROXY_CAP, json.dumps(draw_opts)), depth=4)
                drew = d.get("v") or {}
                if d.get("e"):
                    drew = {"error": d["e"][:300]}

            res: dict = {"built": built, "module": loaded, "ran": ran, "drew": drew,
                         "replayed": replayed, "plan_replay": res_replay,
                         "report": (await _call(live, "__terra.report(%g,%g)"
                                                % (max_slope, channel), depth=6)).get("v") or {},
                         "stats": await live.ask("__forge.stats()", depth=8) or {},
                         "at": await live.ask("__forge.at()", depth=4) or {}}
            if action == "save":
                res["dump"] = await live.raw("__terra.dump()")
            if action == "code":
                # RAW, not `_call`. The console envelope goes through `__live.json`, which cuts
                # any string over 2000 characters and appends an ellipsis — so the first real
                # emit wrote a 2,001-byte file with a perfect header, a correct opening array and
                # no end to it. A builder is tens of kilobytes; it cannot be summarised. The
                # throw is carried in the value instead, the way `exportGlb` does it.
                res["code"] = await live.raw(
                    "(()=>{try{return __terra.emit(%s,%s);}"
                    "catch(e){return 'ERR:'+String((e&&e.stack)||e);}})()"
                    % (json.dumps(str(o.get("name") or "buildTerrain")),
                       json.dumps(str(o.get("engine") or "three"))))
            return res
        finally:
            await ws.close()

    try:
        got = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:1200]}

    built = got.get("built") or {}
    if not built.get("ok"):
        return {"ok": False, "error": built.get("error") or "the forge could not start"}
    menv = got.get("module") or {}
    mval = menv.get("v") if isinstance(menv.get("v"), dict) else {}
    if got.get("module") is not None and not (mval or {}).get("ok"):
        m = menv
        return {"ok": False,
                "error": "the terrain module is not in the page: %s"
                         % ((mval or {}).get("error") or m.get("e") or "no answer"),
                "tried": urls,
                "fix": "build it: cd frontend && npm run build:ops (terrain.ts rides in "
                       "forge-ops.js), or esbuild terrain.ts to dist/forge-terrain.js",
                "console": _console((m.get("c") or []))}
    if got.get("replay_failed"):
        rf = got["replay_failed"]
        return {"ok": False, "error": "the page was empty and the journal would not replay: %s"
                                      % str(rf.get("e"))[:600],
                "replayed": got.get("at", 0), "of": len(journal),
                "console": _console(rf.get("c"))}

    if got.get("cleared") is not None:
        _journal_set(project, [])
        _bench_put(project, "/* terrain: cleared */", True, got.get("at") or {}, {}, "terrain")
        _done(project, "cleared")
        return {"ok": True, "action": "clear", "cleared": True,
                "next": 'POST {"action":"make","size":512,"res":257,"maxHeight":48}'}

    ran = got.get("ran") or {}
    if ran.get("e"):
        return {"ok": False, "action": action, "error": ran["e"][:1200],
                "console": _console(ran.get("c")),
                "hint": "terrain.ts is the one implementation; a 'not built yet' throw means its "
                        "bodies have not landed, not that the wiring is wrong."}
    if got.get("empty"):
        return {"ok": False, "action": action,
                "error": "there is no terrain for this project yet",
                "fix": 'POST {"action":"make","size":512,"res":257,"maxHeight":48}'}

    rep = got.get("report") or {}
    drew = got.get("drew") or {}
    res: dict = {"ok": True, "action": action, "engine": built.get("engine"),
                 "module": (mval or {}).get("from", ""),
                 "findings": _findings(rep, drew, max_slope) + stroke_notes,
                 "report": rep, "spec": rep.get("spec") or {}}
    if drew:
        res["drawn"] = drew
    if got.get("replayed"):
        res["restored"] = ("the page had no field, so %d journalled actions were replayed"
                           % got["replayed"])
    if action == "brush":
        rv = ran.get("v") or {}
        res["applied"] = rv.get("applied", 0)
        res["undo"] = 'POST {"action":"undo"} puts this whole request back'
        if rv.get("touched"):
            res["touched"] = rv["touched"]
        # A STROKE THAT WROTE NOTHING IS NOT AN ERROR AND MUST NOT BE SILENT. The field runs from
        # its origin, not from the middle: a stroke at x = -60 on a 512-unit field whose origin is
        # 0 lands entirely outside it, applies cleanly, changes nothing, and reports a healthy
        # `applied: 1`. That cost the writer of this an hour on the day it was written.
        if rv.get("missed"):
            sp = rep.get("spec") or {}
            og = (sp.get("origin") or [0, 0, 0])
            sz = float(sp.get("size") or 0)
            res["findings"].insert(0, "%d of %d strokes wrote nothing - the field runs x %g..%g, "
                                      "z %g..%g and a stroke outside it applies cleanly and moves "
                                      "no ground."
                                   % (rv["missed"], rv.get("applied", 0), og[0], og[0] + sz,
                                      og[2], og[2] + sz))
    if action == "undo":
        res["undone"] = (ran.get("v") or {}).get("undone", 0)
    if action in ("scatter", "layers"):
        res[action] = ran.get("v") or {}
    if ran.get("c"):
        res["console"] = _console(ran["c"])

    plan_steps: list = []
    if action == "plan":
        pr = ran.get("v") or {}
        if not pr.get("ok"):
            res.update({"ok": False, "error": pr.get("error") or "plan gave nothing back",
                        "fix": "terrain.ts owns the search; a build without plan() is a build to "
                               "rebuild: cd frontend && npm run build:ops"})
            return res
        # THE JOURNAL TAKES `replay`, NOT `steps`. They are different lengths on purpose, and the
        # first live run of this journalled 60 of the 62 strokes it had just applied — a rebuilt
        # tab would have held a field two strokes short of the one it had been answered about.
        plan_steps = _plan_strokes(_raw_list(got.get("plan_replay")) or pr.get("steps") or [])
        if not int(pr.get("of") or 0):
            # The search kept nothing. `of` is the count the page stands behind; the raw list is
            # fetched by a second call and a stale one must never be journalled as this field's.
            plan_steps = []
        res["goal"] = goal
        res["before"] = pr.get("before") or {}
        res["after"] = pr.get("after") or {}
        res["met"] = bool(pr.get("met"))
        res["plan"] = {"kept": int(pr.get("of") or len(pr.get("steps") or [])),
                       "tried": int(pr.get("tried") or 0), "ms": int(pr.get("ms") or 0),
                       "steps": pr.get("steps") or [], "notes": pr.get("notes") or []}
        res["findings"] = (_plan_findings(goal, res["before"], res["after"], pr)
                           + res["findings"])
        res["undo"] = 'POST {"action":"undo"} puts the whole plan back'
        kept_n = int(pr.get("of") or 0)
        if kept_n > len(plan_steps):
            res["findings"].append(
                "%d of %d kept strokes came back, and the journal has the same %d: a tab that is "
                'reloaded would rebuild a field %d strokes short of this one. {"action":"save"} '
                "writes the field itself and does not depend on the journal."
                % (len(plan_steps), kept_n, len(plan_steps), kept_n - len(plan_steps)))
        if len(pr.get("steps") or []) < kept_n:
            res["plan"]["shown"] = ("%d of %d steps are listed; every one of them is on the field"
                                    % (len(pr.get("steps") or []), kept_n))

    # Journal AFTER the page said yes, so a stroke that threw is never replayed into a good page.
    if action in _JOURNALLED:
        _record(project, action, o, spec, plan_steps if action == "plan" else strokes, journal)

    _bench_put(project, "/* terrain: %s */" % _bench_line(rep), True, got.get("at") or {},
               got.get("stats") or {}, str(o.get("label") or "terrain"))

    if action == "save":
        name = str(o.get("name") or "terrain")
        dump = got.get("dump")
        if not isinstance(dump, str) or not dump:
            res.update({"ok": False, "error": "serialize() gave nothing back"})
            return res
        p = _save_file(project, name)
        _write(p, dump)
        res.update({"saved": str(p), "bytes": len(dump)})
        _journal_set(project, [{"action": "load", "name": name}])
        _done(project, "saved", p.name)
        return res

    if action == "code":
        emitted = got.get("code")
        if isinstance(emitted, str) and emitted.startswith("ERR:"):
            res.update({"ok": False, "error": emitted[4:][:1200]})
            return res
        if not isinstance(emitted, str) or not emitted.strip():
            res.update({"ok": False, "error": "emitBuilder() gave nothing back"})
            return res
        if emitted.rstrip().endswith("…"):
            # The one failure that looks like a success: a truncated builder is still a file.
            res.update({"ok": False, "error": "the builder came back truncated (%d chars, ending "
                                              "in an ellipsis) and was not written"
                                              % len(emitted)})
            return res
        _write(out_path, emitted)
        res.update({"wrote": str(out_path), "bytes": len(emitted),
                    "next": "the game calls %s() from %s; nothing in it needs the Studio"
                            % (str(o.get("name") or "buildTerrain"), out_path.name)})
        _done(project, "wrote", out_path.name)
        return res

    if action == "glb":
        _done(project, "drawn", "%s triangles" % drew.get("triangles"))
        exported = _live.export_glb(project, str(out_path))
        res["glb"] = exported
        res["ok"] = bool(exported.get("ok"))
        if not exported.get("ok"):
            res["error"] = exported.get("error")
        return res

    if action == "look":
        _done(project, "drawn", "%s triangles" % drew.get("triangles"))
        shots = [str(v) for v in views] or ["top", "3q"]
        # `ref` OFF unless somebody asks. The reference is remembered per project and it is a
        # CHARACTER: prepending a boy in a red shirt to a sheet of hillsides costs two thousand
        # image tokens and scores a silhouette against the wrong subject.
        res["look"] = _live.look(project, views=shots, margin=float(o.get("margin") or 0.0),
                                 label=str(o.get("label") or ("terrain " + flow if flow
                                                              else "terrain")),
                                 quality=str(o.get("quality") or ""),
                                 ref=str(o.get("ref") or "none"),
                                 tags=list(o.get("tags") or ["environment", "terrain"]),
                                 category=str(o.get("category") or "environment"))
        if flow and drew.get("flow"):
            res["legend"] = ("cut: red is ground the water took away, blue is ground it laid "
                             "down, grey is untouched"
                             if flow == "cut" else
                             "flow: grey is ground no droplet crossed; blue to cyan to white with "
                             "rising drainage; the middle of the ramp is the MEAN wet sample, "
                             "not the busiest one, or an eroded field draws as a white disc")
            res["next"] = ('the bench is false-coloured until the next call redraws it; any other '
                           'action, or {"action":"look"} with no flow view, puts the layers back.')
        return res

    res.setdefault("next", 'strokes are cheap in bulk: one "brush" with a list, then read '
                           '`report`. Spend a "look" when the numbers stop moving.')
    _done(project, action, "%s triangles" % (drew.get("triangles") or rep.get("triangles")))
    return res


def _bench_line(rep: dict) -> str:
    sp = rep.get("spec") or {}
    return ("%gm at res %s, %d layers, %d scattered, %s triangles"
            % (sp.get("size") or 0, sp.get("res") or 0, len(sp.get("layers") or []),
               int(rep.get("scatter") or 0), rep.get("triangles")))


def _raw_list(text) -> list:
    """A JSON array that came back OUTSIDE the envelope, or nothing.

    Outside on purpose. `__live.short` cuts EVERY array at sixty entries and appends a string
    saying how many it dropped — so a 72-stroke plan handed its replay list back as 60 strokes
    and a sentence, and the journal took them, and a reloaded tab would have rebuilt a field
    twelve strokes short of the one the agent had been answered about. A raw string has no such
    cap; it is the same door the emitted builder goes through, for the same reason.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    try:
        got = json.loads(text)
    except ValueError:
        return []
    return got if isinstance(got, list) else []


def _plan_strokes(steps: list) -> list:
    """A plan's kept steps, in the shape `T.strokes` takes.

    THE JOURNAL STORES THE STROKES, NOT THE SEARCH. Replaying `plan` would re-run it, and a search
    with a millisecond budget does not have to reach the same field twice — a rebuilt tab would
    quietly hold a different world from the one the agent was answered about. The strokes it kept
    are deterministic; those are what goes on disk.
    """
    out: list = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        b = dict(s.get("brush") or {})
        if not b:
            continue
        b["x"] = float(s.get("x") or 0.0)
        b["z"] = float(s.get("z") or 0.0)
        b["seconds"] = float(s.get("dt") if s.get("dt") is not None else 1.0)
        out.append(b)
    return out


def _entry_for(action: str, o: dict, spec: dict, strokes: list) -> dict:
    """One mutating request, in the form the journal stores and `_apply` replays."""
    if action == "make":
        return {"action": "make", "spec": spec}
    if action in ("brush", "plan"):
        return {"action": "brush", "strokes": strokes}
    if action == "layers":
        return {"action": "layers", "layers": list(o.get("layers") or [])}
    if action == "scatter":
        return {"action": "scatter", "items": list(o.get("items") or [])}
    if action == "load":
        return {"action": "load", "name": str(o.get("name") or "terrain")}
    return {"action": action}


async def _apply(live, project: str, entry: dict, keep_undo: bool) -> dict:
    """One mutating action against the page's field — for this request, and for a replay."""
    a = str(entry.get("action") or "")
    if a == "make":
        return await _call(live, "__terra.make(%s)" % json.dumps(entry.get("spec") or {}), depth=4)
    if a == "brush":
        return await _call(live, "__terra.strokes(%s,%s)"
                           % (json.dumps(entry.get("strokes") or []),
                              "true" if keep_undo else "false"), depth=3)
    if a == "layers":
        return await _call(live, "__terra.setLayers(%s)" % json.dumps(entry.get("layers") or []),
                           depth=4)
    if a == "scatter":
        return await _call(live, "__terra.place(%s)" % json.dumps(entry.get("items") or []),
                           depth=3)
    if a == "load":
        p = _save_file(project, str(entry.get("name") or "terrain"))
        if not p.is_file():
            return {"e": "no save called %s for this project" % entry.get("name")}
        return await _call(live, "__terra.restore(%s)" % json.dumps(p.read_text(encoding="utf-8")),
                           depth=4)
    if a == "clear":
        return await _call(live, "__terra.forget()", depth=2)
    return {"e": "nothing to apply for %s" % a}


def _record(project: str, action: str, o: dict, spec: dict, strokes: list, journal: list) -> None:
    """Keep the journal short enough to replay quickly, and let a `make` or a `load` reset it."""
    if action == "make":
        _journal_set(project, [{"action": "make", "spec": spec, "ts": int(time.time())}])
        return
    if action == "load":
        _journal_set(project, [{"action": "load", "name": str(o.get("name") or "terrain")}])
        return
    if action == "plan" and not strokes:
        return                            # a search that kept nothing left the field where it was
    if action == "undo" and journal and journal[-1].get("action") == "brush":
        # An undone stroke must not come back on the next replay. Only the tail is dropped,
        # because that is exactly what the page's own single-request undo stack holds.
        _journal_set(project, journal[:-1])
        return
    rows = _journal_add(project, _entry_for(action, o, spec, strokes))
    if len(rows) > _JOURNAL_MAX:
        # Replay is O(the whole journal) on a cold tab, and past a few hundred strokes that is
        # slower than the field is worth. The tail is dropped rather than the head: a snapshot
        # would need another round trip through the page, and the head is the `make`.
        _journal_set(project, rows[:1] + rows[-(_JOURNAL_MAX - 1):])

# -*- coding: utf-8 -*-
"""The running game's own objects, as an agent's tools: see them, move them, keep the move.

The asks this answers, in the order an agent meets them:

  "What is in the scene, where, how big, and what is in view?"  -> objects()
  "What is under the middle of the screen?"                       -> objects(pick=[0.5, 0.5])
  "Move pillar-2 one metre toward the middle, and keep it."       -> edit(target, move, save)
  "Put another crystal beside pillar-1, and keep that too."       -> place(asset, near, save)
  "That was wrong."                                               -> edit(undo=True)
  "What has been saved for this game?"                            -> edits()

WHY IT EXISTS. Before this an agent could reach a game object only through an `eval` it wrote
itself: `find` gave a name, a JS path and a LOCAL position, nothing about world bounds, the key the
Edit tab saves under, or where the object stands. A move made that way died with the next reload,
because nothing but the Edit tab could write `studio.edits.json`. Here the move is made by key,
answered with numbers a person would check by eye (does it float, does it sink, what does it run
into), and written to the same file the Edit tab and the shim already read.

THREE DECISIONS, each forced by something measured on the two test games.

  1. KEYS ARE ops.stableKeys, VERBATIM. The editor, the shim's applier and the game's runtime all
     find a saved edit by that key; a different function here would write entries nothing applies.
     It is ported line for line into the page script and the test compares it with the real one.

  2. NUMBERS FROM GEOMETRY, NOT FROM ORIGINS. rot-rush's `lab-sign` has its origin at 0,0,0 and its
     mesh forty metres away, baked in world space; its `base-props` is ONE 20,812-triangle mesh
     holding a whole base. So bounds come from the meshes, `pick` and `ground` hit TRIANGLES, and an
     overlap against a merged mesh is measured against its welded parts, not its whole box — the
     box of `base-props` would "overlap" everything standing on the base.

  3. DEGREES FOR AGENTS, RADIANS ON DISK. Every rotation an agent types or reads here is in
     degrees; the file keeps three's convention (XYZ euler, radians), which PlayCanvas reads back
     through quatFromEulerXYZ. Answers say `"units": "degrees"` once.

Every endpoint is gated by `cc_scene_edit` and by the live link's own switch.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from . import live as L
from .config import settings

EDITS_NAME = "studio.edits.json"

# ---------------------------------------------------------------------------------------- the page
# Installed on demand into the live tab as `window.__scene`, beside `__live` and `__nav`, and
# REPLACED when its version changes: a tab opened before an upgrade would otherwise keep answering
# with the old code for the rest of its life. Nothing it installs holds a reference to the old
# script — the three.js render hook calls `window.__scene.seen` by name, so a new version takes
# over the hook without wrapping it a second time.
#
# Engine-neutral where it can be: both engines keep 4x4 matrices column-major (three's `elements`,
# PlayCanvas's `data`), both cameras look down their own -Z, and both projections share the GL
# layout, so the projection, the pick ray, the boxes and the triangle tests are written once.
SCENE_JS = r"""
(() => {
var VERSION = __VERSION__;
if (window.__scene && window.__scene.version === VERSION) return;
var S = { version: VERSION };
try { Object.defineProperty(window, '__scene', { value: S, configurable: true, writable: true }); }
catch (e) { window.__scene = S; }

var DEG = Math.PI / 180;
var r3 = function (n) { return (typeof n === 'number' && isFinite(n)) ? Math.round(n * 1000) / 1000 : null; };
var r3v = function (v) { return v ? [r3(v[0]), r3(v[1]), r3(v[2])] : null; };
var r6 = function (n) { return Math.round(n * 1e6) / 1e6; };
var r6v = function (v) { return [r6(v[0]), r6(v[1]), r6(v[2])]; };
var liveOf = function () { try { return window.__live || null; } catch (e) { return null; } };
var STUDIO_NAME = /^__studio/;

/* ------------------------------------------------------------------ keys
   ops.stableKeys, line for line. The editor, the shim and the game's runtime all look a saved edit
   up by this key, so it must be the same function or the file means two different things. A name
   used once is its own key; a repeated name gets Blender's `.000` suffix in traversal order; an
   unnamed node gets its index path; a piece the editor split out of a merged mesh keeps its own. */
var stableKeys = function (root) {
  var keys = new Map();
  var counts = new Map();
  var pieceOf = function (o) {
    try { return (o && o.userData && typeof o.userData.pieceKey === 'string') ? o.userData.pieceKey : ''; }
    catch (e) { return ''; }
  };
  var walkTree = function (o, cb) {
    cb(o);
    var k = (o && o.children) || [];
    for (var i = 0; i < k.length; i++) walkTree(k[i], cb);
  };
  /* A placement is not the game's: kept out of the counts and keyed apart, so a copy with the same
     inner names as something the game drew never renumbers the game's keys (ops.ts says why). */
  var placedOf = function (o) {
    try { return String((o && o.userData && o.userData.studioPlaced) || (o && o.__studioPlaced) || ''); }
    catch (e) { return ''; }
  };
  var count = function (o) {
    if (o !== root && placedOf(o)) return;
    if (o !== root && o.name && !pieceOf(o)) counts.set(o.name, (counts.get(o.name) || 0) + 1);
    var k = (o && o.children) || [];
    for (var i = 0; i < k.length; i++) count(k[i]);
  };
  count(root);
  var used = new Map();
  var placed = [];
  var walk = function (o, path) {
    if (o !== root) {
      if (placedOf(o)) { placed.push([o, path]); return; }
      var n = o.name || '';
      if (pieceOf(o)) keys.set(o, pieceOf(o));
      else if (n && counts.get(n) === 1) keys.set(o, n);
      else if (n) { var i = used.get(n) || 0; used.set(n, i + 1); keys.set(o, n + '.' + String(i).padStart(3, '0')); }
      else keys.set(o, path);
    }
    var kids = o.children || [];
    for (var j = 0; j < kids.length; j++) walk(kids[j], path + '/' + j);
  };
  walk(root, '');
  if (placed.length) {
    var names = new Map();
    placed.forEach(function (p) { var n = p[0].name; if (n) names.set(n, (names.get(n) || 0) + 1); });
    placed.forEach(function (p) {
      var o = p[0], n = o.name || '';
      var k = (n && !counts.has(n) && names.get(n) === 1) ? n : (n || p[1]) + '#' + placedOf(o);
      keys.set(o, k);
      stableKeys(o).forEach(function (ck, c) { keys.set(c, k + '/' + ck); });
    });
  }
  return keys;
};
S.stableKeys = stableKeys;

/* ------------------------------------------------------------------ maths
   Column-major 4x4, the layout both engines keep. */
var xf = function (m, p) {
  return [m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
          m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
          m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]];
};
var xd = function (m, d) {
  return [m[0] * d[0] + m[4] * d[1] + m[8] * d[2],
          m[1] * d[0] + m[5] * d[1] + m[9] * d[2],
          m[2] * d[0] + m[6] * d[1] + m[10] * d[2]];
};
var inv4 = function (a) {
  var a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3], a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7],
      a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11], a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];
  var b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10,
      b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12,
      b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30,
      b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
  var det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  if (!det || !isFinite(det)) return null;
  det = 1 / det;
  return [(a11 * b11 - a12 * b10 + a13 * b09) * det, (a02 * b10 - a01 * b11 - a03 * b09) * det,
          (a31 * b05 - a32 * b04 + a33 * b03) * det, (a22 * b04 - a21 * b05 - a23 * b03) * det,
          (a12 * b08 - a10 * b11 - a13 * b07) * det, (a00 * b11 - a02 * b08 + a03 * b07) * det,
          (a32 * b02 - a30 * b05 - a33 * b01) * det, (a20 * b05 - a22 * b02 + a23 * b01) * det,
          (a10 * b10 - a11 * b08 + a13 * b06) * det, (a01 * b08 - a00 * b10 - a03 * b06) * det,
          (a30 * b04 - a31 * b02 + a33 * b00) * det, (a21 * b02 - a20 * b04 - a23 * b00) * det,
          (a11 * b07 - a10 * b09 - a12 * b06) * det, (a00 * b09 - a01 * b07 + a02 * b06) * det,
          (a31 * b01 - a30 * b03 - a32 * b00) * det, (a20 * b03 - a21 * b01 + a22 * b00) * det];
};
var norm = function (v) { var l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; };

/* three's XYZ euler and back, so a rotation means one thing in both engines and on disk. */
var quatXYZ = function (x, y, z) {
  var c1 = Math.cos(x / 2), c2 = Math.cos(y / 2), c3 = Math.cos(z / 2);
  var s1 = Math.sin(x / 2), s2 = Math.sin(y / 2), s3 = Math.sin(z / 2);
  return [s1 * c2 * c3 + c1 * s2 * s3, c1 * s2 * c3 - s1 * c2 * s3,
          c1 * c2 * s3 + s1 * s2 * c3, c1 * c2 * c3 - s1 * s2 * s3];
};
var eulerXYZ = function (q) {
  var x = q[0], y = q[1], z = q[2], w = q[3];
  var x2 = x + x, y2 = y + y, z2 = z + z;
  var xx = x * x2, xy = x * y2, xz = x * z2, yy = y * y2, yz = y * z2, zz = z * z2;
  var wx = w * x2, wy = w * y2, wz = w * z2;
  var m11 = 1 - (yy + zz), m12 = xy - wz, m13 = xz + wy, m22 = 1 - (xx + zz), m23 = yz - wx;
  var m32 = yz + wx, m33 = 1 - (xx + yy);
  var ey = Math.asin(Math.max(-1, Math.min(1, m13)));
  if (Math.abs(m13) < 0.9999999) return [Math.atan2(-m23, m33), ey, Math.atan2(-m12, m11)];
  return [Math.atan2(m32, m22), ey, 0];
};

/* ------------------------------------------------------------------ boxes */
var emptyBox = function () { return { lo: [Infinity, Infinity, Infinity], hi: [-Infinity, -Infinity, -Infinity] }; };
var okBox = function (b) { return !!(b && isFinite(b.lo[0]) && isFinite(b.hi[0]) && isFinite(b.lo[1]) && isFinite(b.hi[2])); };
var grow = function (b, p) {
  for (var i = 0; i < 3; i++) { if (p[i] < b.lo[i]) b.lo[i] = p[i]; if (p[i] > b.hi[i]) b.hi[i] = p[i]; }
};
var mergeBox = function (b, c) {
  if (!okBox(c)) return;
  for (var i = 0; i < 3; i++) { if (c.lo[i] < b.lo[i]) b.lo[i] = c.lo[i]; if (c.hi[i] > b.hi[i]) b.hi[i] = c.hi[i]; }
};
var xfBox = function (m, lo, hi) {
  var b = emptyBox();
  for (var i = 0; i < 8; i++) grow(b, xf(m, [i & 1 ? hi[0] : lo[0], i & 2 ? hi[1] : lo[1], i & 4 ? hi[2] : lo[2]]));
  return b;
};
var centre = function (b) { return [(b.lo[0] + b.hi[0]) / 2, (b.lo[1] + b.hi[1]) / 2, (b.lo[2] + b.hi[2]) / 2]; };
var sizeOf = function (b) { return [b.hi[0] - b.lo[0], b.hi[1] - b.lo[1], b.hi[2] - b.lo[2]]; };
var meet = function (a, b) {
  return a.lo[0] <= b.hi[0] && a.hi[0] >= b.lo[0] && a.lo[1] <= b.hi[1] && a.hi[1] >= b.lo[1] &&
         a.lo[2] <= b.hi[2] && a.hi[2] >= b.lo[2];
};
var inter = function (a, b) {
  var lo = [Math.max(a.lo[0], b.lo[0]), Math.max(a.lo[1], b.lo[1]), Math.max(a.lo[2], b.lo[2])];
  var hi = [Math.min(a.hi[0], b.hi[0]), Math.min(a.hi[1], b.hi[1]), Math.min(a.hi[2], b.hi[2])];
  if (hi[0] < lo[0] || hi[1] < lo[1] || hi[2] < lo[2]) return null;
  return { lo: lo, hi: hi };
};
/* A flat thing (a floor, a sign) has no volume at all; a centimetre of thickness keeps 0/0 out
   of every share. */
var vol = function (b) {
  var v = 1;
  for (var i = 0; i < 3; i++) v *= Math.max(0.01, b.hi[i] - b.lo[i]);
  return v;
};
var padBox = function (b, p) {
  return { lo: [b.lo[0] - p, b.lo[1] - p, b.lo[2] - p], hi: [b.hi[0] + p, b.hi[1] + p, b.hi[2] + p] };
};
var pointBoxDist = function (p, b) {
  var d = 0;
  for (var i = 0; i < 3; i++) { var e = Math.max(b.lo[i] - p[i], 0, p[i] - b.hi[i]); d += e * e; }
  return Math.sqrt(d);
};
var dist3 = function (a, b) { var x = a[0] - b[0], y = a[1] - b[1], z = a[2] - b[2]; return Math.sqrt(x * x + y * y + z * z); };

/* ------------------------------------------------------------------ the engine
   The app first, from wherever it was pinned: the shim's hunt, the closure hunt (which is how a
   bundled game like rot-rush is reached at all), the navigator, or the one dev line. */
var isPc = function (a) {
  try { return !!(a && a.root && a.root.children && a.scene && a.graphicsDevice && typeof a.start === 'function'); }
  catch (e) { return false; }
};
var pcApp = function () {
  var l = liveOf();
  try { if (l && l.pinned && l.pinnedKind === 'playcanvas' && isPc(l.pinned)) return l.pinned; } catch (e) {}
  /* Found by the shim's own walk of the page's globals: it says where, as a path. */
  try {
    var rr = l && l.reach ? l.reach() : null;
    if (rr && rr.engine === 'playcanvas' && rr.reachable && rr.at && l.node) {
      var a = rr.at === '__live.pinned' ? l.pinned : l.node(rr.at);
      if (isPc(a)) return a;
    }
  } catch (e) {}
  try { var n = window.__nav; if (n && n.app && isPc(n.app)) return n.app; } catch (e) {}
  try { if (window.pc && window.pc.app && isPc(window.pc.app)) return window.pc.app; } catch (e) {}
  try { var g = window.__game; if (g) { if (isPc(g.app)) return g.app; if (isPc(g)) return g; } } catch (e) {}
  return null;
};
var threeScenes = function () {
  var out = [];
  var add = function (s) { try { if (s && s.isScene && out.indexOf(s) < 0) out.push(s); } catch (e) {} };
  var l = liveOf();
  try { if (l && l.scenes) (l.scenes() || []).forEach(add); } catch (e) {}
  try { if (l && l.pinnedKind === 'three') add(l.pinned); } catch (e) {}
  try { var n = window.__nav; if (n && n.three && n.three.scene) add(n.three.scene); } catch (e) {}
  try { var g = window.__game; if (g && g.scene) add(g.scene); } catch (e) {}
  return out;
};
S.app = pcApp;

/* THE CAMERA THE GAME RENDERS WITH, for three. A game's camera need not be in the scene — the proof
   game's is not — and nothing on the page names it. But three calls `scene.onBeforeRender(renderer,
   scene, camera, target)` on every render, so a hook on the scene hears the renderer AND the camera
   within a frame. The hook calls `window.__scene.seen` by name, so a newer script takes it over. A
   render into a target (a minimap, a post-processing pass) is kept apart from the render to the
   canvas, which is the view a screenshot shows. */
var hook = function (list) {
  list.forEach(function (s) {
    try {
      if (s.__studioSceneHook) return;
      var prev = s.onBeforeRender;
      s.onBeforeRender = function (renderer, scene, camera, target) {
        try { var cur = window.__scene; if (cur && cur.seen) cur.seen(this, renderer, camera, target); } catch (e) {}
        if (typeof prev === 'function') return prev.apply(this, arguments);
      };
      Object.defineProperty(s, '__studioSceneHook', { value: true, configurable: true });
    } catch (e) {}
  });
};
/* A place the game switched off, switched on while the view photographs it (S.reveal below). Held
   on every render, not set once: rot-rush turns its far platforms off again on every tick. For three
   the hold runs here, in onBeforeRender, which comes after the game's update and before the scene
   is culled; PlayCanvas gets an app 'prerender' listener for as long as something is held. */
var HELD = { list: [], app: null };
var holdNow = function () {
  for (var i = 0; i < HELD.list.length; i++) {
    try { var h = HELD.list[i]; if (h[0][h[1]] === false) h[0][h[1]] = true; } catch (e) {}
  }
};
S.seen = function (scene, renderer, camera, target) {
  if (HELD.list.length) holdNow();
  var r = scene.__studioSeen;
  if (!r) {
    r = { cam: null, canvasCam: null, renderer: null, at: 0, canvasAt: 0 };
    try { Object.defineProperty(scene, '__studioSeen', { value: r, configurable: true, writable: true }); }
    catch (e) { return; }
  }
  var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  r.cam = camera; r.renderer = renderer; r.at = now;
  if (!target) { r.canvasCam = camera; r.canvasAt = now; }
};
var seenOf = function (scene) { try { return scene.__studioSeen || null; } catch (e) { return null; } };
var countNodes = function (o, cap) {
  var n = 0;
  var walk = function (x) { if (n > cap) return; n++; var k = x.children || []; for (var i = 0; i < k.length; i++) walk(k[i]); };
  walk(o);
  return n;
};
/* The scene the game renders: the one drawn to the canvas most recently, then the biggest. */
var chooseScene = function (ss, idx) {
  if (typeof idx === 'number' && idx >= 0 && idx < ss.length) return ss[idx];
  var now = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  var best = null, bestN = -1;
  ss.forEach(function (s) {
    var r = seenOf(s);
    if (r && r.canvasCam && now - r.canvasAt < 1500) {
      var n = countNodes(s, 50000);
      if (n > bestN) { best = s; bestN = n; }
    }
  });
  if (best) return best;
  ss.forEach(function (s) { var n = countNodes(s, 50000); if (n > bestN) { best = s; bestN = n; } });
  return best;
};

/* ------------------------------------------------------------------ three, as nodes */
var T3 = {
  kind: 'three',
  kids: function (o) { return o.children || []; },
  selfOn: function (o) { return o.visible !== false; },
  studio: function (o) { return STUDIO_NAME.test(String(o.name || '')) || !!(o.userData && o.userData.__studio); },
  hasBody: function (o) { return !!((o.isMesh && o.geometry) || o.isSprite); },
  drawn: function (o) {
    var m = o.material;
    if (m && !Array.isArray(m) && m.visible === false) return false;
    return true;
  },
  ownBox: function (o) {
    try {
      var W = o.matrixWorld.elements;
      if (o.isSprite) {
        var s = Math.max(Math.hypot(W[0], W[1], W[2]), Math.hypot(W[4], W[5], W[6])) / 2;
        return { lo: [W[12] - s, W[13] - s, W[14] - s], hi: [W[12] + s, W[13] + s, W[14] + s] };
      }
      var bb = null;
      if (o.isInstancedMesh && typeof o.computeBoundingBox === 'function') {
        if (!o.boundingBox || o.count <= 50000) o.computeBoundingBox();
        bb = o.boundingBox;
      }
      if (!bb) {
        var g = o.geometry;
        if (!g) return null;
        if (!g.boundingBox) g.computeBoundingBox();
        bb = g.boundingBox;
      }
      if (!bb || !isFinite(bb.min.x) || !isFinite(bb.max.x)) return null;
      return xfBox(W, [bb.min.x, bb.min.y, bb.min.z], [bb.max.x, bb.max.y, bb.max.z]);
    } catch (e) { return null; }
  },
  tris: function (o) {
    try {
      if (!o.isMesh || !o.geometry) return 0;
      var g = o.geometry, pa = g.attributes && g.attributes.position;
      var n = g.index ? g.index.count / 3 : (pa ? pa.count / 3 : 0);
      if (g.drawRange && isFinite(g.drawRange.count)) n = Math.min(n, g.drawRange.count / 3);
      if (o.isInstancedMesh) n *= (o.count || 0);
      return Math.round(n);
    } catch (e) { return 0; }
  },
  type: function (o) {
    if (o.isMesh) return 'mesh';
    if (o.isSprite) return 'sprite';
    if (o.isLight) return 'light';
    if (o.isCamera) return 'camera';
    if (o.isGroup || (o.children && o.children.length && !o.isBone)) return 'group';
    return 'other';
  },
  local: function (o) {
    return { pos: [o.position.x, o.position.y, o.position.z], rot: [o.rotation.x, o.rotation.y, o.rotation.z],
             order: o.rotation.order, scale: [o.scale.x, o.scale.y, o.scale.z], on: o.visible !== false };
  },
  setLocal: function (o, st) {
    if (st.pos) o.position.set(st.pos[0], st.pos[1], st.pos[2]);
    if (st.rot) {
      if (st.order && o.rotation.order !== st.order) o.rotation.order = st.order;
      o.rotation.set(st.rot[0], st.rot[1], st.rot[2]);
    }
    if (st.scale) o.scale.set(st.scale[0], st.scale[1], st.scale[2]);
    if (st.on === true || st.on === false) o.visible = st.on;
    o.updateMatrixWorld(true);
  },
  worldPos: function (o) { o.updateWorldMatrix(true, false); var m = o.matrixWorld.elements; return [m[12], m[13], m[14]]; },
  setWorldPos: function (o, w) {
    var p = o.parent, l = w;
    if (p) {
      p.updateWorldMatrix(true, false);
      var inv = inv4(p.matrixWorld.elements);
      if (inv) l = xf(inv, w);
    }
    o.position.set(l[0], l[1], l[2]);
    o.updateMatrixWorld(true);
  },
  refresh: function (root) { try { root.updateMatrixWorld(true); } catch (e) {} },
  /* The triangles, for a ray. Instanced meshes and sprites answer with their box. */
  sources: function (o) {
    try {
      if (!o.isMesh || o.isInstancedMesh || !o.geometry) return [];
      var g = o.geometry, pa = g.attributes && g.attributes.position;
      if (!pa) return [];
      var idx = g.index ? g.index.array : null;
      var nt = Math.floor((idx ? g.index.count : pa.count) / 3);
      var t0 = 0, t1 = nt;
      if (g.drawRange && isFinite(g.drawRange.count)) {
        t0 = Math.floor((g.drawRange.start || 0) / 3);
        t1 = Math.min(nt, t0 + Math.floor(g.drawRange.count / 3));
      }
      var get;
      if (pa.isInterleavedBufferAttribute || pa.normalized) {
        get = function (i, out) { out[0] = pa.getX(i); out[1] = pa.getY(i); out[2] = pa.getZ(i); };
      } else {
        var arr = pa.array, st = pa.itemSize || 3;
        get = function (i, out) { var k = i * st; out[0] = arr[k]; out[1] = arr[k + 1]; out[2] = arr[k + 2]; };
      }
      return [{ idx: idx, t0: t0, t1: t1, get: get, m: o.matrixWorld.elements, owner: g,
                vcount: pa.count, ver: (pa.version || 0) }];
    } catch (e) { return []; }
  },
  src: function (o) {
    try { var u = o.userData || {}; var s = u.src || u.url || u.file; return typeof s === 'string' ? s : ''; }
    catch (e) { return ''; }
  }
};

/* ------------------------------------------------------------------ PlayCanvas, as nodes */
var pcMis = function (e) {
  try {
    var c = (e.render && e.render.meshInstances && e.render.meshInstances.length) ? e.render
          : (e.model && e.model.meshInstances && e.model.meshInstances.length) ? e.model : null;
    return c ? { comp: c, list: c.meshInstances } : null;
  } catch (e2) { return null; }
};
var TPC = {
  kind: 'playcanvas',
  kids: function (e) { return e.children || []; },
  /* PlayCanvas's `enabled` already folds in every parent. */
  selfOn: function (e) { return e.enabled !== false; },
  studio: function (e) { return STUDIO_NAME.test(String(e.name || '')); },
  hasBody: function (e) { return !!pcMis(e); },
  drawn: function (e) {
    var m = pcMis(e);
    if (!m || m.comp.enabled === false) return false;
    for (var i = 0; i < m.list.length; i++) if (m.list[i] && m.list[i].visible !== false) return true;
    return false;
  },
  /* mesh instance boxes are world space, but lazily: a node moved this call has not been synced,
     and its box stays where it was until something asks for its world transform. Ask first. */
  ownBox: function (e) {
    var m = pcMis(e);
    if (!m) return null;
    var b = emptyBox(), n = 0;
    for (var i = 0; i < m.list.length; i++) {
      var mi = m.list[i];
      if (!mi) continue;
      try {
        if (mi.node && mi.node.getWorldTransform) mi.node.getWorldTransform();
        var a = mi.aabb;
        if (!a) continue;
        var c = a.center, h = a.halfExtents;
        grow(b, [c.x - h.x, c.y - h.y, c.z - h.z]);
        grow(b, [c.x + h.x, c.y + h.y, c.z + h.z]);
        n++;
      } catch (e2) {}
    }
    return n ? b : null;
  },
  tris: function (e) {
    var m = pcMis(e), n = 0;
    if (!m) return 0;
    for (var i = 0; i < m.list.length; i++) {
      try {
        var p = m.list[i].mesh.primitive[0];
        if (p && (p.type === 4 || p.type === undefined)) n += Math.floor(p.count / 3) * (m.list[i].instancingCount || 1);
      } catch (e2) {}
    }
    return n;
  },
  type: function (e) {
    if (pcMis(e)) return 'mesh';
    if (e.light) return 'light';
    if (e.camera) return 'camera';
    if (e.sprite || e.element) return 'sprite';
    if (e.children && e.children.length) return 'group';
    return 'other';
  },
  local: function (e) {
    var p = e.getLocalPosition(), q = e.getLocalRotation(), s = e.getLocalScale();
    var qa = [q.x, q.y, q.z, q.w];
    return { pos: [p.x, p.y, p.z], q: qa, rot: eulerXYZ(qa), scale: [s.x, s.y, s.z],
             on: (e._enabled !== undefined ? e._enabled : e.enabled) !== false };
  },
  setLocal: function (e, st) {
    if (st.pos) e.setLocalPosition(st.pos[0], st.pos[1], st.pos[2]);
    if (st.q) e.setLocalRotation(st.q[0], st.q[1], st.q[2], st.q[3]);
    else if (st.rot) { var q = quatXYZ(st.rot[0], st.rot[1], st.rot[2]); e.setLocalRotation(q[0], q[1], q[2], q[3]); }
    if (st.scale) e.setLocalScale(st.scale[0], st.scale[1], st.scale[2]);
    if (st.on === true || st.on === false) e.enabled = st.on;
  },
  worldPos: function (e) { var p = e.getPosition(); return [p.x, p.y, p.z]; },
  setWorldPos: function (e, w) { e.setPosition(w[0], w[1], w[2]); },
  refresh: function () {},
  sources: function (e) {
    var m = pcMis(e), out = [];
    if (!m) return out;
    m.list.forEach(function (mi) {
      try {
        var mesh = mi && mi.mesh;
        if (!mesh || typeof mesh.getPositions !== 'function' || mi.instancingData) return;
        var prim = mesh.primitive && mesh.primitive[0];
        if (prim && prim.type !== undefined && prim.type !== 4) return;       /* 4 = triangles */
        var cache = mesh.__studioTri;
        var ver = mesh._aabbVer || 0;
        if (!cache || cache.ver !== ver) {
          var P = [], I = [];
          mesh.getPositions(P);
          if (typeof mesh.getIndices === 'function') mesh.getIndices(I);
          cache = { ver: ver, P: P, I: I.length ? I : null };
          try { Object.defineProperty(mesh, '__studioTri', { value: cache, configurable: true, writable: true }); } catch (e3) {}
        }
        var P2 = cache.P;
        var nt = Math.floor((cache.I ? cache.I.length : P2.length / 3) / 3);
        var t0 = 0, t1 = nt;
        if (prim && typeof prim.count === 'number' && prim.count > 0) {
          t0 = Math.floor((prim.base || 0) / 3);
          t1 = Math.min(nt, t0 + Math.floor(prim.count / 3));
        }
        out.push({ idx: cache.I, t0: t0, t1: t1, owner: mesh, vcount: P2.length / 3, ver: ver,
                   get: function (i, o) { var k = i * 3; o[0] = P2[k]; o[1] = P2[k + 1]; o[2] = P2[k + 2]; },
                   m: (mi.node || e).getWorldTransform().data });
      } catch (e2) {}
    });
    return out;
  },
  /* A PlayCanvas render built from a GLB carries a render asset named `<file>/render/<n>`; the
     container asset of that file knows the URL it was loaded from. */
  src: function (e, app) {
    try {
      var comp = e.render || e.model;
      var a = comp && comp.asset;
      if (!a || !app || !app.assets) return '';
      var as = typeof a === 'number' ? app.assets.get(a) : (a.id !== undefined ? a : app.assets.get(a));
      if (!as || !as.name) return '';
      var file = String(as.name).replace(/\/render\/\d+$/, '');
      var cont = app.assets.find(file);
      var url = (cont && cont.file && cont.file.url) || (as.file && as.file.url) || '';
      if (url) {
        try { return decodeURIComponent(new URL(url, location.href).pathname); } catch (e3) { return url; }
      }
      return file;
    } catch (e2) { return ''; }
  }
};

/* ------------------------------------------------------------------ where we are */
var pcBase = function () {
  try { var r = liveOf().reach() || {}; if (r.engine === 'playcanvas' && r.reachable && r.at) return r.at; } catch (e) {}
  return '__scene.app()';
};
var ctxOf = function (sceneIdx) {
  var app = pcApp();
  if (app) return { kind: 'playcanvas', app: app, root: app.root, E: TPC, base: pcBase() + '.root' };
  var ss = threeScenes();
  if (ss.length) {
    hook(ss);
    var sc = chooseScene(ss, sceneIdx);
    /* THE ROOT THE SAVED EDITS ARE APPLIED TO, when the shim names it. A key means an object only
       relative to one root; if this API keyed from one scene and the runtime applied the file to
       another, a saved move would land on whatever the other scene calls by that name. */
    if (typeof sceneIdx !== 'number') {
      try {
        var er = liveOf() && liveOf().editRoot ? liveOf().editRoot() : null;
        if (er && er.isScene) { if (ss.indexOf(er) < 0) ss.push(er); sc = er; }
      } catch (e) {}
    }
    var li = -1;
    try { li = (liveOf().scenes() || []).indexOf(sc); } catch (e) {}
    return { kind: 'three', root: sc, E: T3, scenes: ss.length, index: ss.indexOf(sc),
             base: li >= 0 ? '__live.scenes()[' + li + ']' : '__scene.root()' };
  }
  var r = {};
  try { r = liveOf().reach() || {}; } catch (e) {}
  return { kind: '', engine: r.engine || '', reachable: !!r.reachable, hint: r.hint || '' };
};
S.root = function () { var c = ctxOf(); return c.root || null; };
var notReachable = function (cx) {
  return { ok: false, engine: cx.engine || '', reachable: cx.reachable,
           readonly: !!(cx.engine && cx.reachable), hint: cx.hint || '' };
};
var nowMs = function () { return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now(); };
var frames = function (n) {
  return new Promise(function (res) {
    var k = 0, done = false;
    var tick = function () { if (done) return; if (++k >= n) { done = true; res(true); } else requestAnimationFrame(tick); };
    try { requestAnimationFrame(tick); } catch (e) { done = true; res(false); }
    setTimeout(function () { if (!done) { done = true; res(false); } }, 400);
  });
};

/* ------------------------------------------------------------------ one pass over the tree
   Every node once, pre-order (the order the keys are counted in), with its own box, whether it
   is drawn, and the union box of its subtree. rot-rush has 1,543 entities; this pass is a few
   milliseconds there, so every call starts from a fresh one rather than a cache that goes stale. */
var scan = function (cx) {
  var E = cx.E, root = cx.root;
  E.refresh(root);
  var keys = stableKeys(root);
  var list = [], byObj = new Map(), draw = [];
  var walk = function (o, parent, on, top) {
    var ent = { o: o, key: keys.get(o), parent: parent, on: on, top: top || null, kids: [], own: null, box: null,
                body: false, drawn: false, tris: 0, studio: E.studio(o) };
    if (parent && parent.studio) ent.studio = true;
    list.push(ent); byObj.set(o, ent);
    if (E.hasBody(o)) {
      ent.body = true;
      ent.own = E.ownBox(o);
      ent.tris = E.tris(o);
      ent.drawn = on && E.drawn(o) && okBox(ent.own) && !ent.studio;
      if (ent.drawn) draw.push(ent);
    }
    var b = emptyBox();
    if (okBox(ent.own)) mergeBox(b, ent.own);
    var ks = E.kids(o);
    for (var i = 0; i < ks.length; i++) {
      var c = ks[i];
      var ce = walk(c, ent, on && E.selfOn(c), top || c);
      ent.kids.push(ce);
      if (ce.box) mergeBox(b, ce.box);
    }
    ent.box = okBox(b) ? b : null;
    return ent;
  };
  var rootEnt = { o: root, key: null, kids: [], studio: false };
  byObj.set(root, rootEnt);
  var ks = E.kids(root), rootOn = E.selfOn(root);
  for (var i = 0; i < ks.length; i++) rootEnt.kids.push(walk(ks[i], null, rootOn && E.selfOn(ks[i]), ks[i]));
  return { cx: cx, keys: keys, list: list, byObj: byObj, draw: draw };
};
/* After a move: the moved subtree's boxes, then every ancestor's union. */
var rebox = function (tab, ent) {
  var E = tab.cx.E;
  var redo = function (e) {
    if (e.body) {
      e.own = E.ownBox(e.o);
      var was = e.drawn;
      e.drawn = e.on && E.drawn(e.o) && okBox(e.own) && !e.studio;
      if (e.drawn && !was) tab.draw.push(e);
      if (!e.drawn && was) tab.draw.splice(tab.draw.indexOf(e), 1);
    }
    var b = emptyBox();
    if (okBox(e.own)) mergeBox(b, e.own);
    e.kids.forEach(function (k) { redo(k); if (k.box) mergeBox(b, k.box); });
    e.box = okBox(b) ? b : null;
  };
  redo(ent);
  for (var p = ent.parent; p; p = p.parent) {
    var b = emptyBox();
    if (okBox(p.own)) mergeBox(b, p.own);
    p.kids.forEach(function (k) { if (k.box) mergeBox(b, k.box); });
    p.box = okBox(b) ? b : null;
  }
};
var subtreeSet = function (ent) {
  var s = new Set();
  var add = function (e) { s.add(e); e.kids.forEach(add); };
  add(ent);
  return s;
};
var pathOf = function (tab, o) {
  var parts = [], cur = o, root = tab.cx.root;
  while (cur && cur !== root && cur.parent) {
    parts.unshift('.children[' + cur.parent.children.indexOf(cur) + ']');
    cur = cur.parent;
  }
  return tab.cx.base + parts.join('');
};

/* ------------------------------------------------------------------ the camera and the screen */
var threeCam = function (cx) {
  var r = seenOf(cx.root);
  if (r && r.canvasCam) return r.canvasCam;
  if (r && r.cam) return r.cam;
  try { var n = window.__nav; if (n && n.on && n.ours && n.kind === 'three') return n.ours; if (n && n.three && n.three.camera) return n.three.camera; } catch (e) {}
  var found = null;
  try { cx.root.traverse(function (o) { if (!found && o.isCamera) found = o; }); } catch (e) {}
  return found;
};
/* The camera whose picture is on screen. The Studio's own while the navigator holds the view;
   otherwise the lowest-priority enabled camera that draws the world layer (0), which is the
   game's — a UI camera draws only its own layers on top. */
var pcViewCam = function (app) {
  try { var n = window.__nav; if (n && n.on && n.ours && n.kind === 'playcanvas' && n.ours.camera && n.ours.camera.enabled) return n.ours; } catch (e) {}
  var cams = [];
  try { cams = (app.systems.camera.cameras || []).slice(); } catch (e) {}
  var best = null;
  cams.forEach(function (c) {
    try {
      if (!c || !c.entity || c.enabled === false || c.entity.enabled === false) return;
      if (STUDIO_NAME.test(String(c.entity.name || ''))) return;
      var world = !c.layers || c.layers.indexOf(0) >= 0;
      var score = (world ? 0 : 1000) + (c.priority || 0);
      if (!best || score < best.score) best = { ent: c.entity, score: score };
    } catch (e) {}
  });
  return best ? best.ent : null;
};
var canvasEl = function (cx) {
  try {
    if (cx.kind === 'playcanvas') return cx.app.graphicsDevice.canvas || null;
    var r = seenOf(cx.root);
    if (r && r.renderer && r.renderer.domElement) return r.renderer.domElement;
  } catch (e) {}
  try {
    if (typeof document === 'undefined') return null;
    var best = null, area = 0;
    var all = document.querySelectorAll('canvas');
    for (var i = 0; i < all.length; i++) { var a = all[i].clientWidth * all[i].clientHeight; if (a > area) { area = a; best = all[i]; } }
    return best;
  } catch (e) { return null; }
};
/* 0..1 of the VIEWPORT is what an agent reads off a screenshot; the projection gives 0..1 of the
   canvas. The canvas usually fills the page, and when it does not the two differ. */
var rectOf = function (cx) {
  var vw = (typeof window !== 'undefined' && window.innerWidth) || 1, vh = (typeof window !== 'undefined' && window.innerHeight) || 1;
  var el = canvasEl(cx);
  try {
    if (el && el.getBoundingClientRect) {
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return { x: r.left, y: r.top, w: r.width, h: r.height, vw: vw, vh: vh };
    }
  } catch (e) {}
  return { x: 0, y: 0, w: vw, h: vh, vw: vw, vh: vh };
};
var camInfo = function (cx) {
  var W, P, near = 0.1, far = 1e6, ortho = false, cam = null, layers = null;
  try {
    if (cx.kind === 'three') {
      cam = threeCam(cx);
      if (!cam) return null;
      if (!cam.parent && cam.updateMatrixWorld) cam.updateMatrixWorld();
      W = Array.prototype.slice.call(cam.matrixWorld.elements);
      P = Array.prototype.slice.call(cam.projectionMatrix.elements);
      near = cam.near || 0.1; far = cam.far || 1e6; ortho = !!cam.isOrthographicCamera;
      layers = cam.layers || null;
    } else {
      cam = pcViewCam(cx.app);
      if (!cam) return null;
      W = Array.prototype.slice.call(cam.getWorldTransform().data);
      P = Array.prototype.slice.call(cam.camera.projectionMatrix.data);
      near = cam.camera.nearClip || 0.1; far = cam.camera.farClip || 1e6; ortho = cam.camera.projection === 1;
    }
  } catch (e) { return null; }
  var V = inv4(W);
  if (!V) return null;
  return { cam: cam, W: W, V: V, P: P, near: near, far: far, ortho: ortho, layers: layers,
           pos: [W[12], W[13], W[14]], fwd: norm([-W[8], -W[9], -W[10]]), rect: rectOf(cx),
           name: String(cam.name || '') };
};
var clipOf = function (ci, v) {
  var P = ci.P;
  var x = P[0] * v[0] + P[4] * v[1] + P[8] * v[2] + P[12];
  var y = P[1] * v[0] + P[5] * v[1] + P[9] * v[2] + P[13];
  var w = P[3] * v[0] + P[7] * v[1] + P[11] * v[2] + P[15];
  if (!(w > 1e-9)) return null;
  return [(x / w + 1) / 2, (1 - y / w) / 2];
};
var toView = function (ci, c) { var R = ci.rect; return [(R.x + c[0] * R.w) / R.vw, (R.y + c[1] * R.h) / R.vh]; };
var toCanvas = function (ci, v) { var R = ci.rect; return [(v[0] * R.vw - R.x) / R.w, (v[1] * R.vh - R.y) / R.h]; };
var inside01 = function (c) { return c && c[0] >= 0 && c[0] <= 1 && c[1] >= 0 && c[1] <= 1; };
/* Where a box lands on the canvas, as a rectangle — the corners in front of the near plane, plus
   the points where the box's edges cross it, so a floor that runs from behind the lens into the
   distance still covers the part of the screen it covers. null: entirely behind, past the far
   plane, or off to the side. */
var boxOnCanvas = function (ci, b) {
  var cs = [], zc = -ci.near, pts = [], allFar = true, i;
  for (i = 0; i < 8; i++) cs.push(xf(ci.V, [i & 1 ? b.hi[0] : b.lo[0], i & 2 ? b.hi[1] : b.lo[1], i & 4 ? b.hi[2] : b.lo[2]]));
  for (i = 0; i < 8; i++) { if (cs[i][2] <= zc) pts.push(cs[i]); if (cs[i][2] >= -ci.far) allFar = false; }
  if (allFar) return null;
  var EDGES = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
  for (i = 0; i < EDGES.length; i++) {
    var a = cs[EDGES[i][0]], c = cs[EDGES[i][1]];
    if ((a[2] - zc) * (c[2] - zc) < 0) {
      var t = (zc - a[2]) / (c[2] - a[2]);
      pts.push([a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t, zc]);
    }
  }
  if (!pts.length) return null;
  var lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
  for (i = 0; i < pts.length; i++) {
    var s = clipOf(ci, pts[i]);
    if (!s) continue;
    if (s[0] < lo[0]) lo[0] = s[0]; if (s[1] < lo[1]) lo[1] = s[1];
    if (s[0] > hi[0]) hi[0] = s[0]; if (s[1] > hi[1]) hi[1] = s[1];
  }
  if (!isFinite(lo[0])) return null;
  if (hi[0] < 0 || lo[0] > 1 || hi[1] < 0 || lo[1] > 1) return null;
  return { lo: lo, hi: hi };
};
var pointOnCanvas = function (ci, p) {
  var v = xf(ci.V, p);
  if (v[2] > -ci.near || v[2] < -ci.far) return null;
  return clipOf(ci, v);
};
var layerOk = function (ci, o) {
  try { if (ci && ci.layers && o.layers && typeof ci.layers.test === 'function') return ci.layers.test(o.layers); } catch (e) {}
  return true;
};
/* In view = inside the camera's frustum and drawn. Not an occlusion test: a pillar behind a wall
   is in view. `screen` is where its middle lands, in 0..1 of the page, or the middle of the part
   of it that is on screen when its own middle is not. */
var viewOf = function (ci, ent, E) {
  if (!ci) return { in_view: null, screen: null };
  var vis = ent.on && layerOk(ci, ent.o);
  var p = ent.box ? centre(ent.box) : E.worldPos(ent.o);
  var c = pointOnCanvas(ci, p);
  if (ent.box) {
    var r = boxOnCanvas(ci, ent.box);
    if (!r) return { in_view: false, screen: null };
    if (!inside01(c)) c = [(Math.max(0, r.lo[0]) + Math.min(1, r.hi[0])) / 2, (Math.max(0, r.lo[1]) + Math.min(1, r.hi[1])) / 2];
    var sv = toView(ci, c);
    return { in_view: !!vis, screen: [r3(sv[0]), r3(sv[1])] };
  }
  if (!inside01(c)) return { in_view: false, screen: null };
  var s2 = toView(ci, c);
  return { in_view: !!vis, screen: [r3(s2[0]), r3(s2[1])] };
};

/* ------------------------------------------------------------------ rays */
var rayAt = function (ci, c) {
  var nx = c[0] * 2 - 1, ny = 1 - c[1] * 2, P = ci.P, o, d;
  if (ci.ortho) { o = [(nx - P[12]) / P[0], (ny - P[13]) / P[5], 0]; d = [0, 0, -1]; }
  else { o = [0, 0, 0]; d = [(nx + P[8]) / P[0], (ny + P[9]) / P[5], -1]; }
  return { o: xf(ci.W, o), d: norm(xd(ci.W, d)) };
};
var rayBox = function (o, d, b) {
  var t0 = -Infinity, t1 = Infinity;
  for (var i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-12) { if (o[i] < b.lo[i] || o[i] > b.hi[i]) return null; continue; }
    var a = (b.lo[i] - o[i]) / d[i], c = (b.hi[i] - o[i]) / d[i];
    if (a > c) { var tmp = a; a = c; c = tmp; }
    if (a > t0) t0 = a;
    if (c < t1) t1 = c;
    if (t0 > t1) return null;
  }
  if (t1 < 0) return null;
  return Math.max(0, t0);
};
/* Moller-Trumbore against one source, in the mesh's own frame: the ray is taken into it rather
   than every corner out of it. The direction is left unnormalised, so t is still world metres. */
var TRI_BUDGET = 3000000;
var rayTris = function (src, o, d, maxT, budget) {
  var inv = inv4(src.m);
  if (!inv) return null;
  var ol = xf(inv, o), dl = xd(inv, d);
  var best = maxT, bestTri = -1;
  var A = [0, 0, 0], B = [0, 0, 0], C = [0, 0, 0], idx = src.idx;
  var n = src.t1 - src.t0;
  if (n > budget.left) return { skipped: true };
  budget.left -= n;
  for (var t = src.t0; t < src.t1; t++) {
    var k = t * 3;
    src.get(idx ? idx[k] : k, A); src.get(idx ? idx[k + 1] : k + 1, B); src.get(idx ? idx[k + 2] : k + 2, C);
    var e1x = B[0] - A[0], e1y = B[1] - A[1], e1z = B[2] - A[2];
    var e2x = C[0] - A[0], e2y = C[1] - A[1], e2z = C[2] - A[2];
    var px = dl[1] * e2z - dl[2] * e2y, py = dl[2] * e2x - dl[0] * e2z, pz = dl[0] * e2y - dl[1] * e2x;
    var det = e1x * px + e1y * py + e1z * pz;
    if (det > -1e-12 && det < 1e-12) continue;
    var id = 1 / det;
    var sx = ol[0] - A[0], sy = ol[1] - A[1], sz = ol[2] - A[2];
    var u = (sx * px + sy * py + sz * pz) * id;
    if (u < 0 || u > 1) continue;
    var qx = sy * e1z - sz * e1y, qy = sz * e1x - sx * e1z, qz = sx * e1y - sy * e1x;
    var v = (dl[0] * qx + dl[1] * qy + dl[2] * qz) * id;
    if (v < 0 || u + v > 1) continue;
    var tt = (e2x * qx + e2y * qy + e2z * qz) * id;
    if (tt > 1e-7 && tt < best) { best = tt; bestTri = t; }
  }
  return bestTri >= 0 ? { t: best, tri: bestTri } : null;
};
/* The nearest drawn surface along a ray, from the drawables whose boxes it enters, nearest box
   first. What cannot be tested by triangle (a sprite, an instanced mesh, a mesh past the budget)
   answers with its box, and the hit says so. */
var cast = function (tab, ray, skip, ci) {
  var cands = [];
  tab.draw.forEach(function (ent) {
    if (skip && skip.has(ent)) return;
    if (ci && !layerOk(ci, ent.o)) return;
    var t = rayBox(ray.o, ray.d, padBox(ent.own, 1e-4));
    if (t !== null) cands.push({ ent: ent, t: t });
  });
  cands.sort(function (a, b) { return a.t - b.t; });
  var best = null, budget = { left: TRI_BUDGET };
  for (var i = 0; i < cands.length; i++) {
    var c = cands[i];
    if (best && c.t > best.t) break;
    var srcs = tab.cx.E.sources(c.ent.o);
    if (!srcs.length) {
      if (!best || c.t < best.t) best = { ent: c.ent, t: c.t, approx: true };
      continue;
    }
    for (var j = 0; j < srcs.length; j++) {
      var h = rayTris(srcs[j], ray.o, ray.d, best ? best.t : Infinity, budget);
      if (!h) continue;
      if (h.skipped) { if (!best || c.t < best.t) best = { ent: c.ent, t: c.t, approx: true }; continue; }
      if (!best || h.t < best.t) best = { ent: c.ent, t: h.t, tri: h.tri };
    }
  }
  return best;
};
var chainOf = function (ent) {
  var out = [];
  for (var e = ent; e && e.key; e = e.parent) out.push(e.key);
  return out;
};
/* What a mesh is part of, for a person reading the answer: its parent, past the wrapper nodes a
   model file brings with it (`RootNode`, `Armature`, an unnamed node). `pillar-shaft.001` is part
   of `pillar-2`; rot-rush's `tread-sign-free.003` of `platform-4`. */
var WRAPPER = /^(rootnode|root|scene|armature|__root__|gltf|model)(\.\d+)?$/i;
var ofKey = function (ent) {
  for (var p = ent.parent; p; p = p.parent) {
    var n = String(p.o.name || '');
    if (p.key && n && !WRAPPER.test(n)) return p.key;
  }
  return null;
};

/* ------------------------------------------------------------------ ground and overlaps
   What an agent cannot see from a list of positions: does it stand on something, float over it or
   sink into it, and does it now run through something else. Rays go DOWN from just above the
   object's top at five points of its footprint, so a thing sunk into the floor still finds the
   floor, and the highest surface under it is its support. */
var castDown = function (tab, x, y, z, skip) {
  return cast(tab, { o: [x, y, z], d: [0, -1, 0] }, skip, null);
};
var groundOf = function (tab, ent) {
  var b = ent.box;
  if (!b) return { support: null, gap: null, note: 'it has no body to stand on anything' };
  var skip = subtreeSet(ent);
  var c = centre(b), hx = (b.hi[0] - b.lo[0]) / 4, hz = (b.hi[2] - b.lo[2]) / 4, y0 = b.hi[1] + 1e-3;
  var pts = [[c[0], c[2]], [c[0] - hx, c[2] - hz], [c[0] + hx, c[2] - hz], [c[0] - hx, c[2] + hz], [c[0] + hx, c[2] + hz]];
  var top = -Infinity, sup = null, approx = false;
  pts.forEach(function (p) {
    var h = castDown(tab, p[0], y0, p[1], skip);
    if (h && y0 - h.t > top) { top = y0 - h.t; sup = h.ent; approx = !!h.approx; }
  });
  if (!sup) return { support: null, gap: null, note: 'nothing below it' };
  var gap = b.lo[1] - top;
  var out = { support: sup.key, gap: r3(gap), rests: Math.abs(gap) <= 0.01 };
  var of = ofKey(sup);
  if (of) out.support_of = of;
  if (approx) out.approx = true;
  return { top: top, out: out };
};
var groundAnswer = function (g) { return g.out || g; };
/* A merged mesh is measured by its WELDED PARTS. rot-rush's `base-props` is one box the size of
   the base, so by boxes alone a sign on the base "overlaps" it completely. Corners are welded by
   position (a box with hard edges is six islands by index, one by position), the connected parts
   found, and each part's box tested instead. Cached on the geometry until its vertex data changes. */
var partsOf = function (src) {
  var sig = src.vcount + ':' + (src.t1 - src.t0) + ':' + src.ver;
  try { var cached = src.owner && src.owner.__studioParts; if (cached && cached.sig === sig) return cached.parts; } catch (e) {}
  if (src.vcount > 400000) return null;
  var nv = src.vcount, id = new Int32Array(nv), seen = new Map(), p = [0, 0, 0], Q = 1e4, i;
  for (i = 0; i < nv; i++) {
    src.get(i, p);
    var k = Math.round(p[0] * Q) + ',' + Math.round(p[1] * Q) + ',' + Math.round(p[2] * Q);
    var v = seen.get(k);
    if (v === undefined) { seen.set(k, i); v = i; }
    id[i] = v;
  }
  var par = new Int32Array(nv);
  for (i = 0; i < nv; i++) par[i] = i;
  var find = function (x) { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  var idx = src.idx, t;
  for (t = src.t0; t < src.t1; t++) {
    var q = t * 3;
    var a = find(id[idx ? idx[q] : q]), b = find(id[idx ? idx[q + 1] : q + 1]), c = find(id[idx ? idx[q + 2] : q + 2]);
    if (a !== b) par[a] = b;
    var bb = find(b);
    if (bb !== c) par[bb] = c;
  }
  var byRoot = new Map(), parts = [];
  for (t = src.t0; t < src.t1; t++) {
    var q2 = t * 3;
    var r = find(id[idx ? idx[q2] : q2]);
    var part = byRoot.get(r);
    if (!part) { part = { lo: [Infinity, Infinity, Infinity], hi: [-Infinity, -Infinity, -Infinity], t0: t, t1: t + 1 }; byRoot.set(r, part); parts.push(part); }
    part.t1 = t + 1;
    for (var kk = 0; kk < 3; kk++) { src.get(idx ? idx[q2 + kk] : q2 + kk, p); grow(part, p); }
  }
  if (parts.length > 8192) parts = null;
  try { Object.defineProperty(src.owner, '__studioParts', { value: { sig: sig, parts: parts }, configurable: true, writable: true }); } catch (e) {}
  return parts;
};
var contains = function (a, b) {
  return a.lo[0] <= b.lo[0] && a.lo[1] <= b.lo[1] && a.lo[2] <= b.lo[2] &&
         a.hi[0] >= b.hi[0] && a.hi[1] >= b.hi[1] && a.hi[2] >= b.hi[2];
};
/* A SHELL — a sky dome, a room, a terrain skirt — holds a thing inside its box without touching it.
   rot-rush's sky is one welded part 760 m across, and by boxes every sign on the map overlapped it
   at share 1. When a part's box swallows the target whole and is far bigger than it, the part's
   TRIANGLES decide: the share is what their own boxes put inside the target's box, so a thing in
   the middle of the dome shares nothing with it and a thing through a wall shares the wall. null:
   too many triangles to check within the budget, or none to check, and the box answer stands. */
var shellShare = function (ranges, tb, vt, budget) {
  if (!ranges.length) return null;
  var n = 0;
  ranges.forEach(function (r) { n += r.t1 - r.t0; });
  if (n > budget.left) return null;
  budget.left -= n;
  var u = emptyBox(), hit = 0, A = [0, 0, 0];
  ranges.forEach(function (r) {
    var src = r.src, idx = src.idx;
    for (var t = r.t0; t < r.t1; t++) {
      var tri = emptyBox();
      for (var k = 0; k < 3; k++) { var q = t * 3 + k; src.get(idx ? idx[q] : q, A); grow(tri, xf(src.m, A)); }
      var I = inter(tb, tri);
      if (I) { mergeBox(u, I); hit++; }
    }
  });
  if (!hit) return 0;
  if ((u.hi[1] - u.lo[1]) < 0.05 && u.hi[1] <= tb.lo[1] + 0.05 + 1e-9) return 0;   /* standing on its floor */
  return vol(u) / vt;
};
/* Two boxes that share a face are touching, not overlapping: under a centimetre of shared depth on
   an axis where both have depth. A flat thing has none, so for it the rule does not apply. */
var touching = function (I, a, b) {
  for (var i = 0; i < 3; i++) {
    if ((I.hi[i] - I.lo[i]) < 0.01 && (a.hi[i] - a.lo[i]) > 0.02 && (b.hi[i] - b.lo[i]) > 0.02) return true;
  }
  return false;
};
var overlapsOf = function (tab, ent) {
  var tb = ent.box;
  if (!tb) return [];
  var skip = subtreeSet(ent);
  for (var p = ent.parent; p; p = p.parent) skip.add(p);
  var vt = vol(tb), out = [], budget = { left: 2000000 };
  tab.draw.forEach(function (d) {
    if (skip.has(d) || !meet(tb, d.own)) return;
    var srcs = null;
    var sourcesOf = function () { if (!srcs) srcs = tab.cx.E.sources(d.o); return srcs; };
    var parts = [{ box: d.own }];
    if (vol(d.own) > 8 * vt && d.tris > 12) {
      var got = [], measured = 0;
      sourcesOf().forEach(function (src) {
        var ps = partsOf(src);
        if (!ps) return;
        measured++;
        ps.forEach(function (pp) {
          var wb = xfBox(src.m, pp.lo, pp.hi);
          if (meet(tb, wb)) got.push({ box: wb, t: [pp.t0, pp.t1], src: src });
        });
      });
      /* Measured by its parts, even when none of them comes near: that IS the answer. Only a mesh
         whose parts could not be measured falls back to its whole box. */
      if (measured) parts = got;
    }
    var best = 0, bestPart = null;
    parts.forEach(function (pt) {
      var I = inter(tb, pt.box);
      if (!I || touching(I, tb, pt.box)) return;
      /* Standing on it: a slab under five centimetres at its own bottom is contact, not overlap. */
      if ((I.hi[1] - I.lo[1]) < 0.05 && I.hi[1] <= tb.lo[1] + 0.05 + 1e-9) return;
      var s = null;
      if (contains(pt.box, tb) && vol(pt.box) > 50 * vt) {
        var ranges = pt.src ? [{ src: pt.src, t0: pt.t[0], t1: pt.t[1] }]
                            : sourcesOf().map(function (sr) { return { src: sr, t0: sr.t0, t1: sr.t1 }; });
        s = shellShare(ranges, tb, vt, budget);
      }
      if (s === null) s = vol(I) / Math.min(vt, vol(pt.box));
      if (s > best) { best = s; bestPart = pt; }
    });
    if (best > 0.05) {
      var row = { key: d.key, share: Math.round(Math.min(1, best) * 100) / 100 };
      var of = ofKey(d);
      if (of) row.of = of;
      if (bestPart && bestPart.t) row.tris = bestPart.t;
      out.push(row);
    }
  });
  out.sort(function (a, b) { return b.share - a.share; });
  return out.slice(0, 12);
};

/* How far a point is from a thing. A group is its area, so the distance to its box. A mesh whose box
   holds the point is measured to its TRIANGLES' boxes instead: rot-rush's sky dome, and every merged
   mesh the size of a base, hold every point there is, and "within a metre of the sign" listed the
   sky. Past the budget the box answer stands. */
var surfaceDist = function (tab, ent, p, budget) {
  var b = ent.box;
  if (!b) return dist3(p, tab.cx.E.worldPos(ent.o));
  var d = pointBoxDist(p, b);
  if (d > 0 || !ent.body) return d;
  var srcs = tab.cx.E.sources(ent.o);
  if (!srcs.length) return d;
  var n = 0;
  srcs.forEach(function (s) { n += s.t1 - s.t0; });
  if (n > budget.left) return d;
  budget.left -= n;
  var best = Infinity, A = [0, 0, 0];
  srcs.forEach(function (src) {
    var idx = src.idx;
    for (var t = src.t0; t < src.t1 && best > 0; t++) {
      var tri = emptyBox();
      for (var k = 0; k < 3; k++) { var q = t * 3 + k; src.get(idx ? idx[q] : q, A); grow(tri, xf(src.m, A)); }
      var dd = pointBoxDist(p, tri);
      if (dd < best) best = dd;
    }
  });
  return isFinite(best) ? best : d;
};

/* ------------------------------------------------------------------ what a target means
   A key first — exactly what the file uses. Then a name that only one object has; then a JS path
   (what `find` and `objects` hand out); then a name in any case. A key two objects share (a node
   literally named `Udindindindun.001` beside a generated `Udindindindun.001`) resolves to the LAST
   in traversal order, because that is the one the applier's key map keeps. */
var PIECE = /^(.+)~t(\d+)-(\d+)$/;
var resolve = function (tab, target) {
  var t = String(target == null ? '' : target).trim();
  if (!t) return { error: 'no target: give a key, a name or a path (from /api/live/objects)' };
  var byKey = [], byName = [], byCase = [], low = t.toLowerCase();
  tab.list.forEach(function (e) {
    if (e.studio) return;
    if (e.key === t) byKey.push(e);
    var n = String(e.o.name || '');
    if (n === t) byName.push(e);
    else if (n.toLowerCase() === low) byCase.push(e);
  });
  if (byKey.length) {
    var r = { ent: byKey[byKey.length - 1], how: 'key' };
    if (byKey.length > 1) r.ambiguous = byKey.length;
    return r;
  }
  if (byName.length === 1) return { ent: byName[0], how: 'name' };
  if (byName.length > 1) {
    return { error: byName.length + ' objects are named ' + JSON.stringify(t) + ' — give one of their keys',
             keys: byName.slice(0, 12).map(function (e) { return e.key; }) };
  }
  var pm = PIECE.exec(t);
  if (pm) {
    return { error: JSON.stringify(t) + ' is a piece of the merged mesh ' + JSON.stringify(pm[1]) +
                    ' — it has no object of its own to move here; the Edit tab moves pieces' };
  }
  if (/[.\[]/.test(t) && /^[A-Za-z_$][\w$]*/.test(t)) {
    try {
      var v = (0, eval)('(' + t + ')');
      if (v && tab.byObj.has(v) && tab.byObj.get(v).key) return { ent: tab.byObj.get(v), how: 'path' };
      if (v) return { error: JSON.stringify(t) + ' is not under the scene root' };
    } catch (e) {}
  }
  if (byCase.length === 1) return { ent: byCase[0], how: 'name' };
  var like = [];
  tab.list.forEach(function (e) {
    if (like.length < 12 && !e.studio && e.key && e.key.toLowerCase().indexOf(low) >= 0) like.push(e.key);
  });
  return { error: 'nothing is keyed or named ' + JSON.stringify(t), like: like };
};
S.get = function (target) {
  var cx = ctxOf();
  if (!cx.kind) return null;
  var r = resolve(scan(cx), target);
  return r.ent ? r.ent.o : null;
};
/* What a ray meets, for `eval`: `__scene.ray([x, 10, z], [0, -1, 0])` is "what is under this spot".
   Every surface along it, nearest first (up to `max`), with the key and the triangle. */
S.ray = function (origin, dir, max) {
  var cx = ctxOf();
  if (!cx.kind) return null;
  var tab = scan(cx), o = origin.map(Number), d = norm(dir.map(Number)), out = [], skip = new Set();
  for (var i = 0; i < (max || 4); i++) {
    var h = cast(tab, { o: o, d: d }, skip, null);
    if (!h) break;
    out.push({ key: h.ent.key, t: r3(h.t), at: r3v([o[0] + d[0] * h.t, o[1] + d[1] * h.t, o[2] + d[2] * h.t]),
               tri: h.tri, approx: !!h.approx });
    skip.add(h.ent);
  }
  return out;
};

/* ------------------------------------------------------------------ objects */
S.objects = async function (o) {
  o = o || {};
  var cx = ctxOf(o.scene);
  if (!cx.kind) return notReachable(cx);
  var notes = [];
  if (cx.kind === 'three') {
    var rec = seenOf(cx.root);
    if (!rec || !rec.cam) await frames(3);
    if (!seenOf(cx.root) || !seenOf(cx.root).cam) notes.push('the scene has not been rendered since the Studio started listening, so the camera was found by searching the scene');
  }
  var tab = scan(cx), E = cx.E;
  var ci = camInfo(cx);
  if (!ci) notes.push('no camera: in_view, screen and pick need one');
  var q = String(o.q || '').toLowerCase();
  var near = null;
  if (Array.isArray(o.near) && o.near.length === 3) near = o.near.map(Number);
  else if (typeof o.near === 'string' && o.near) {
    var nr = resolve(tab, o.near);
    if (!nr.ent) return { ok: false, engine: cx.kind, error: 'near: ' + nr.error, like: nr.like, keys: nr.keys };
    near = nr.ent.box ? centre(nr.ent.box) : E.worldPos(nr.ent.o);
  }
  var radius = (typeof o.radius === 'number' && isFinite(o.radius)) ? o.radius : null;
  var ref = near || (ci ? ci.pos : null);
  var pick = null, chainEnts = [];
  if (o.pick) {
    if (!ci) pick = { hit: null, chain: [], error: 'no camera to pick with' };
    else {
      var at = [Number(o.pick[0]), Number(o.pick[1])];
      var ray = rayAt(ci, toCanvas(ci, at));
      var h = cast(tab, ray, null, ci);
      if (!h) pick = { at: at, hit: null, chain: [] };
      else {
        var pt = [ray.o[0] + ray.d[0] * h.t, ray.o[1] + ray.d[1] * h.t, ray.o[2] + ray.d[2] * h.t];
        pick = { at: at, hit: h.ent.key, name: String(h.ent.o.name || ''), chain: chainOf(h.ent),
                 point: r3v(pt), dist: r3(h.t) };
        if (h.tri !== undefined) pick.triangle = h.tri;
        if (h.approx) pick.approx = 'box';
        for (var e = h.ent; e && e.key; e = e.parent) chainEnts.push(e);
      }
    }
  }
  var inChain = new Set(chainEnts);
  var wantView = !!o.in_view;
  var matched = [], nearBudget = { left: 2000000 };
  tab.list.forEach(function (ent) {
    if (ent.studio || !ent.key) return;
    var chained = inChain.has(ent);
    if (!chained) {
      if (q && ent.key.toLowerCase().indexOf(q) < 0 && String(ent.o.name || '').toLowerCase().indexOf(q) < 0) return;
      if (near && radius !== null && surfaceDist(tab, ent, near, nearBudget) > radius) return;
    }
    var vw = null;
    /* In view means something to look at. rot-rush's runner is a skeleton of 29 bones with no
       geometry around one mesh, and the bones filled three quarters of the list. They are still
       rows — `q` finds them — just not what `in_view` is asked for. */
    if (wantView && !chained) {
      if (!ent.box) return;
      vw = viewOf(ci, ent, E);
      if (!vw.in_view) return;
    }
    var c = ent.box ? centre(ent.box) : E.worldPos(ent.o);
    matched.push({ ent: ent, view: vw, d: ref ? dist3(ref, c) : 0, chain: chained ? chainEnts.indexOf(ent) : -1 });
  });
  matched.sort(function (a, b) {
    if (a.chain >= 0 || b.chain >= 0) {
      if (a.chain < 0) return 1;
      if (b.chain < 0) return -1;
      return a.chain - b.chain;
    }
    return a.d - b.d;
  });
  var limit = Math.max(1, Math.min(500, o.limit || 60));
  var rows = matched.slice(0, limit).map(function (m) {
    var ent = m.ent, b = ent.box, vw = m.view || viewOf(ci, ent, E);
    var row = { key: ent.key, name: String(ent.o.name || ''), type: E.type(ent.o), path: pathOf(tab, ent.o),
                pos: r3v(E.worldPos(ent.o)), size: b ? r3v(sizeOf(b)) : null, min: b ? r3v(b.lo) : null,
                max: b ? r3v(b.hi) : null, visible: !!ent.on, in_view: vw.in_view, screen: vw.screen,
                children: ent.kids.length };
    if (ref) row.dist = r3(m.d);
    var tris = 0;
    (function sum(e) { tris += e.tris || 0; e.kids.forEach(sum); })(ent);
    if (tris) row.tris = tris;
    var src = srcOfTree(cx, ent);
    if (src) row.src = src;
    /* A placement (from /api/live/place, the Edit tab or the saved edits) says so: it is the one
       kind of object that can be REMOVED rather than hidden, and its transform lives in `placed`. */
    var pm = markOf(cx, ent.o);
    if (pm) row.placed = pm;
    return row;
  });
  var out = { ok: true, engine: cx.kind, total: matched.length,
              camera: ci ? { pos: r3v(ci.pos), fwd: r3v(ci.fwd), name: ci.name } : null, rows: rows };
  if (pick) out.pick = pick;
  if (cx.kind === 'three' && cx.scenes > 1) out.scene = { index: cx.index, of: cx.scenes };
  if (notes.length) out.notes = notes;
  return out;
};
/* One file for the row: its own, or the one every drawn thing under it shares. */
var srcOfTree = function (cx, ent) {
  var found = '', mixed = false, n = 0;
  var visit = function (e) {
    if (mixed || n > 400) return;
    n++;
    if (e.body) {
      var s = cx.E.src(e.o, cx.app);
      if (s) { if (!found) found = s; else if (s !== found) { mixed = true; return; } }
    }
    e.kids.forEach(visit);
  };
  visit(ent);
  return mixed ? '' : found;
};

/* ------------------------------------------------------------------ edit */
var snap = function (tab, ent) {
  var E = tab.cx.E, loc = E.local(ent.o);
  return { local: loc, world: E.worldPos(ent.o), box: ent.box };
};
var shown = function (s) {
  return { pos: r3v(s.local.pos), rot: [r3(s.local.rot[0] / DEG), r3(s.local.rot[1] / DEG), r3(s.local.rot[2] / DEG)],
           scale: r3v(s.local.scale), world: r3v(s.world),
           min: s.box ? r3v(s.box.lo) : null, max: s.box ? r3v(s.box.hi) : null, visible: s.local.on };
};
var v3 = function (v) {
  if (typeof v === 'number' && isFinite(v)) return [v, v, v];
  if (Array.isArray(v) && v.length === 3 && v.every(function (x) { return typeof x === 'number' && isFinite(x); })) return v.slice();
  return null;
};
S.edit = async function (specs, o) {
  o = o || {};
  var cx = ctxOf(o.scene);
  if (!cx.kind) return notReachable(cx);
  var tab = scan(cx), E = cx.E, results = [];
  (specs || []).forEach(function (sp) {
    sp = sp || {};
    var r = resolve(tab, sp.target);
    if (!r.ent) { results.push({ ok: false, target: sp.target, error: r.error, like: r.like, keys: r.keys }); return; }
    var ent = r.ent, obj = ent.o;
    var before = snap(tab, ent);
    /* Asked for by a look (live_view.py): what it stood on BEFORE, for the caption "ground gap
       0.00 -> floats 0.42 m". Five rays down; only paid for when a picture is coming. */
    var groundBefore = o.look ? groundAnswer(groundOf(tab, ent)) : null;
    var touched = {}, notes = [];
    try {
      if (sp.visible === true || sp.visible === false) { E.setLocal(obj, { on: sp.visible }); touched.hidden = true; }
      var sc = v3(sp.scale);
      if (sc) { E.setLocal(obj, { scale: sc }); touched.scale = true; }
      var rot = v3(sp.rot);
      if (rot) { E.setLocal(obj, { rot: [rot[0] * DEG, rot[1] * DEG, rot[2] * DEG], order: before.local.order }); touched.rot = true; }
      var spin = v3(sp.rotate);
      if (spin) {
        var cur = E.local(obj).rot;
        E.setLocal(obj, { rot: [cur[0] + spin[0] * DEG, cur[1] + spin[1] * DEG, cur[2] + spin[2] * DEG], order: before.local.order });
        touched.rot = true;
      }
      var pos = v3(sp.pos);
      if (pos) { if (sp.world) E.setWorldPos(obj, pos); else E.setLocal(obj, { pos: pos }); touched.pos = true; }
      var mv = v3(sp.move);
      if (mv) { var w = E.worldPos(obj); E.setWorldPos(obj, [w[0] + mv[0], w[1] + mv[1], w[2] + mv[2]]); touched.pos = true; }
      E.refresh(cx.root);
      rebox(tab, ent);
      if (sp.drop) {
        var g0 = groundOf(tab, ent);
        if (g0.top !== undefined && ent.box) {
          var w2 = E.worldPos(obj), dy = g0.top - ent.box.lo[1];
          E.setWorldPos(obj, [w2[0], w2[1] + dy, w2[2]]);
          E.refresh(cx.root);
          rebox(tab, ent);
          touched.pos = true;
          /* Down onto what is under it — or UP onto it, when it had sunk in: say which. */
          notes.push((dy <= 0 ? 'dropped ' : 'lifted ') + r3(Math.abs(dy)) + ' m onto ' + g0.out.support);
        } else {
          notes.push('drop: ' + ((g0 && g0.note) || 'nothing below it') + ' — not moved');
        }
      }
    } catch (err) {
      try { E.setLocal(obj, before.local); E.refresh(cx.root); rebox(tab, ent); } catch (e2) {}
      results.push({ ok: false, target: sp.target, key: ent.key, error: 'the edit threw and was put back: ' + String(err && err.message || err).slice(0, 200) });
      return;
    }
    var after = snap(tab, ent);
    var ground = groundOf(tab, ent);
    var save = {};
    if (touched.pos) save.pos = r6v(after.local.pos);
    if (touched.rot) save.rot = r6v(after.local.rot);
    if (touched.scale) save.scale = r6v(after.local.scale);
    if (touched.hidden) save.hidden = !after.local.on;
    /* `redo` beside `undo`: the exact local values the edit left, so a look can put the old state
       back for its "before" frame and then this one again, bit for bit. Python keeps both. */
    var res = { ok: true, key: ent.key, name: String(obj.name || ''), how: r.how, before: shown(before), after: shown(after),
                ground: groundAnswer(ground), overlaps: overlapsOf(tab, ent), save: save, undo: before.local,
                redo: after.local, changed: Object.keys(touched) };
    if (groundBefore) res.ground_before = groundBefore;
    if (r.ambiguous) res.ambiguous = r.ambiguous + ' objects share this key; the last in the tree was edited, which is the one a saved edit applies to';
    if (notes.length) res.notes = notes;
    /* A PLACEMENT'S TRANSFORM IS ITS OWN ENTRY. Saved as parts[key] it would be a second record of
       one object, applied only because the runtime retries missing parts after placing; so the
       Python side writes it into placed[id] instead, and an unsaved placement is saved whole. */
    var pm = markOf(cx, obj);
    if (pm) { res.placed = pm; var li = liveItem(cx, pm); if (li) res.placed_item = li; }
    results.push(res);
  });
  return { ok: true, engine: cx.kind, results: results };
};
/* Put objects back exactly: three's own euler and order, PlayCanvas's own quaternion. */
S.restore = function (list, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return notReachable(cx);
  var tab = scan(cx), done = [], missing = [];
  (list || []).forEach(function (it) {
    var r = resolve(tab, it.key);
    if (!r.ent || r.how !== 'key') { missing.push(it.key); return; }
    try { cx.E.setLocal(r.ent.o, it.local); done.push(it.key); } catch (e) { missing.push(it.key); }
  });
  cx.E.refresh(cx.root);
  return { ok: true, engine: cx.kind, restored: done, missing: missing };
};

/* ------------------------------------------------------------------ place
   One of the game's own things, put into the running game by `placeStudioItem` — the function a
   shipped game applies its saved edits with, so what is placed here is exactly what the file
   makes again after a reload. The runtime MAKES the object (calls the builder, copies the model
   the game drew, clones, builds a primitive) and marks it with its id. This side decides WHERE,
   because only this side has the boxes, the rays and the camera, and it answers the way an edit
   does: where the thing is, what it stands on, what it runs into. */
var RT = { url: '', mod: null, tries: 0 };
var errText = function (e) { return String((e && e.message) || e).slice(0, 300); };
var runtimeOf = function (url) {
  if (!url) return Promise.reject(new Error('no runtime URL was given'));
  if (RT.mod && RT.url === url) return RT.mod;
  RT.url = url;
  /* A fresh query for each attempt: an import that failed once fails for the life of the page
     under the same URL, and the runtime may simply not have been built the first time. */
  RT.mod = import(url + (url.indexOf('?') < 0 ? '?' : '&') + 'v=' + VERSION + '-' + (RT.tries++))
    .catch(function (e) { RT.mod = null; throw e; });
  return RT.mod;
};
/* What this API placed on this page, by id: the recipe the file would hold. Kept on window, so a
   newer version of this script still knows what an older one placed. */
var REG = (function () {
  try {
    if (!window.__studioPlacements) Object.defineProperty(window, '__studioPlacements', { value: {}, configurable: true, writable: true });
    return window.__studioPlacements;
  } catch (e) { return {}; }
})();
/* The runtime's mark: three `userData.studioPlaced`, PlayCanvas `entity.__studioPlaced`, on the
   placement's own root only. */
var markOf = function (cx, o) {
  try { return String((cx.kind === 'three' ? (o && o.userData && o.userData.studioPlaced) : (o && o.__studioPlaced)) || ''); }
  catch (e) { return ''; }
};
var placedOf = function (cx, id) {
  var hit = null;
  var walk = function (o) {
    if (hit) return;
    if (o !== cx.root && markOf(cx, o) === id) { hit = o; return; }
    var k = o.children || [];
    for (var i = 0; i < k.length; i++) walk(k[i]);
  };
  if (id) walk(cx.root);
  return hit;
};
/* A placement as the file would hold it NOW: its recipe, with the transform read off the live
   object, because an edit or an undo since it was placed moved the object, not the record. */
var liveItem = function (cx, id) {
  var it = REG[id];
  if (!it) return null;
  var out = JSON.parse(JSON.stringify(it)), o = placedOf(cx, id);
  if (o) { var l = cx.E.local(o); out.pos = r6v(l.pos); out.rot = r6v(l.rot); out.scale = r6v(l.scale); }
  return out;
};
/* The module this page loaded as `three`: its import map says, else the resources it fetched do.
   The Studio's glTF loader is bound to it BY URL, because module identity is the URL, and a loader
   on a second copy of three makes meshes this page's renderer refuses. */
var threeUrl = function () {
  /* Never the copy the FORGE imported into this tab: it matches the same file pattern, and a loader
     bound to it makes meshes from a second three inside the game's scene. */
  var forgeHref = '';
  try { forgeHref = (window.__forge && __forge.engineHref) ? String(__forge.engineHref() || '') : ''; } catch (e) {}
  var bare = function (u) { return String(u || '').split('?')[0].split('#')[0]; };
  try {
    var maps = document.querySelectorAll('script[type="importmap"]');
    for (var i = 0; i < maps.length; i++) {
      var j = JSON.parse(maps[i].textContent || '{}');
      if (j && j.imports && j.imports.three) return new URL(j.imports.three, document.baseURI).href;
    }
  } catch (e) {}
  try {
    var res = performance.getEntriesByType('resource') || [];
    for (var k = 0; k < res.length; k++) {
      var n = String(res[k].name || '');
      if (forgeHref && bare(n) === bare(forgeHref)) continue;
      if (/\/\.vite\/deps\/three\.js(\?|$)/.test(n) || /\/three(\.module)?(\.min)?\.m?js(\?|$)/.test(n)) return n;
    }
  } catch (e) {}
  return '';
};
var runtimeOpts = async function (cx, rt, item) {
  var o = { retryMs: (typeof rt.retryMs === 'number') ? rt.retryMs : 60000 };
  if (rt.base) o.base = rt.base;
  if (rt.studio && rt.project) {
    /* Models through the Studio, which hands a file back with nothing left in it that needs a
       decoder: Draco and meshopt are set up by a game at its start-up, and this is not that. */
    o.url = function (p, kind) {
      return kind === 'model' ? rt.studio + '/api/engine/model?project=' + encodeURIComponent(rt.project) +
        '&file=' + encodeURIComponent(p) : '';
    };
  }
  if (cx.kind === 'three' && item && item.ref && item.ref.kind === 'model') {
    var tu = threeUrl();
    if (!tu) throw new Error('this page\'s three.js is bundled into the game, so no glTF loader can share it — ' +
                             'place a clone of something the game drew, or one of its builders');
    var m = await import(rt.studio + '/api/engine/loader?project=' + encodeURIComponent(rt.project) +
                         '&three=' + encodeURIComponent(tu));
    o.GLTFLoader = m.GLTFLoader;
  }
  return o;
};
/* A placement that has not finished in time is answered as a failure, and the object it makes when
   it does finish is taken out again: a call that said "not placed" must not leave one behind. */
var withTimeout = function (p, ms, late) {
  return new Promise(function (res) {
    var done = false;
    var timer = setTimeout(function () {
      if (!done) { done = true; res({ ok: false, timeout: true, error: 'not built after ' + Math.round(ms / 100) / 10 + ' s' }); }
    }, ms);
    Promise.resolve(p).then(function (r) {
      if (!done) { done = true; clearTimeout(timer); res(r); }
      else if (late) { try { late(r); } catch (e) {} }
    }, function (e) {
      if (!done) { done = true; clearTimeout(timer); res({ ok: false, error: errText(e) }); }
    });
  });
};
/* A NAME OF ITS OWN. A placement's name is its key, and a name another object already has would
   renumber that object's key too: crystal-1 would become crystal-1.000, and every saved edit under
   it would miss. The game's own numbering first — crystal-1..5 make the next one crystal-6, also
   when the name was not asked for and plain "crystal" is free — then the editor's rule,
   "Labubu_idle", "Labubu_idle 2". An object being replaced keeps no claim to its name. */
var reEsc = function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); };
var uniqueName = function (tab, want, skip, given) {
  var taken = new Set(), gone = (skip && tab.byObj.get(skip)) ? subtreeSet(tab.byObj.get(skip)) : null;
  tab.list.forEach(function (e) {
    if (gone && gone.has(e)) return;
    taken.add(String(e.o.name || ''));
    if (e.key) taken.add(e.key);
  });
  want = String(want || '').trim() || 'placed';
  var base = want.replace(/-\d+$/, ''), rx = new RegExp('^' + reEsc(base) + '-(\\d+)$'), top = 0, any = false;
  taken.forEach(function (n) { var m = rx.exec(n); if (m) { any = true; top = Math.max(top, +m[1]); } });
  if (!taken.has(want) && (given || !any)) return want;
  if (any) { var k = top + 1; while (taken.has(base + '-' + k)) k++; return base + '-' + k; }
  var j = 2;
  while (taken.has(want + ' ' + j)) j++;
  return want + ' ' + j;
};

S.place = async function (spec, o) {
  o = o || {};
  spec = spec || {};
  var cx = ctxOf(o.scene);
  if (!cx.kind) return notReachable(cx);
  var t0 = nowMs(), E = cx.E, rt = spec.rt || {}, notes = [];
  var item = JSON.parse(JSON.stringify(spec.item || {}));
  if (!item.id || !item.ref || !item.ref.kind) return { ok: false, engine: cx.kind, error: 'no placement to make: it needs an id and a ref' };
  var R;
  try { R = await runtimeOf(rt.runtime); }
  catch (e) {
    return { ok: false, engine: cx.kind, error: 'the Studio runtime did not load from ' + rt.runtime + ': ' + errText(e) +
             ' — `npm run build:runtime` in frontend/ makes it' };
  }
  if (cx.kind === 'three') { var sr = seenOf(cx.root); if (!sr || !sr.cam) await frames(3); }
  var tab0 = scan(cx), ci = camInfo(cx);
  /* Checked BEFORE anything is made: a typo in `near` would otherwise leave an object nobody asked
     for, and a typo in a clone's source a placement that waits a minute for nothing. */
  var nearEnt = null;
  if (spec.near) {
    var nr = resolve(tab0, spec.near);
    if (!nr.ent) return { ok: false, engine: cx.kind, error: 'near: ' + nr.error, like: nr.like, keys: nr.keys };
    if (!nr.ent.box) return { ok: false, engine: cx.kind, error: 'near: ' + nr.ent.key + ' has no body to stand beside (a light, an empty) — give at:[x,y,z]' };
    nearEnt = nr.ent;
  }
  var waiting = false;
  if (item.ref.kind === 'clone') {
    var cr = resolve(tab0, item.ref.of);
    if (cr.ent) {
      item.ref.of = cr.ent.key;                         /* the file keeps the KEY, whatever was typed */
      if (!item.name) item.name = String(cr.ent.o.name || '') || cr.ent.key;
    } else if (spec.wait) {
      waiting = true;
      if (nearEnt) return { ok: false, engine: cx.kind, error: 'near needs the object now, and ' + item.ref.of + ' does not exist yet — give at:[x,y,z], which is then its origin' };
    } else {
      return { ok: false, engine: cx.kind, error: 'clone: ' + cr.error + ' — or add "wait": true to place it when the game spawns it', like: cr.like, keys: cr.keys };
    }
  }
  var old = placedOf(cx, item.id), prev = liveItem(cx, item.id);
  var want = String(item.name || '').trim() || 'placed';
  item.name = uniqueName(tab0, want, old, !!spec.name_given);
  if (item.name !== want && spec.name_given) notes.push(JSON.stringify(want) + ' is taken, so this one is ' + JSON.stringify(item.name) + ': a placement\'s name is its key');
  item.pos = (spec.pos || spec.at || [0, 0, 0]).slice(0, 3);
  var ro;
  try { ro = await runtimeOpts(cx, rt, item); }
  catch (e) { return { ok: false, engine: cx.kind, error: errText(e) }; }
  var target = cx.kind === 'playcanvas' ? cx.app : cx.root, id = item.id;
  var got = await withTimeout(R.placeStudioItem(target, item, ro), spec.timeoutMs || 30000, function (late) {
    if (late && late.ok) R.unplaceStudioItem(target, id);
  });
  if (!got || (!got.ok && !got.pending)) return { ok: false, engine: cx.kind, id: id, error: (got && got.error) || 'the runtime placed nothing' };
  if (got.pending) {
    REG[id] = item;
    return { ok: true, engine: cx.kind, pending: true, id: id, key: null, name: item.name, how: String(got.error || 'waiting'),
             where: 'not built yet, so ' + (spec.at ? 'at' : 'pos') + ' is taken as its origin: ' + JSON.stringify(r3v(item.pos)),
             item: item, prev: prev, replaced: !!old, ms: Math.round(nowMs() - t0),
             notes: notes.concat(['it goes in when ' + item.ref.of + ' appears; the runtime keeps trying for ' + Math.round((ro.retryMs || 0) / 1000) + ' s']) };
  }
  var obj = got.object, tab = scan(cx), ent = tab.byObj.get(obj);
  if (!ent) { R.unplaceStudioItem(target, id); return { ok: false, engine: cx.kind, id: id, error: 'the runtime placed it outside the root this API reads' }; }

  var settle = function () { E.refresh(cx.root); rebox(tab, ent); };
  /* `at` is where the middle of its BOTTOM goes, not its origin: a builder's origin is wherever
     its author put it, and rot-rush's signs keep theirs forty metres from their mesh. */
  var bottomTo = function (p) {
    if (ent.box) {
      var c = centre(ent.box), w = E.worldPos(obj);
      E.setWorldPos(obj, [w[0] + p[0] - c[0], w[1] + p[1] - ent.box.lo[1], w[2] + p[2] - c[2]]);
    } else E.setWorldPos(obj, p);
    settle();
  };
  var dropNow = function () {
    var g = groundOf(tab, ent);
    if (g.top === undefined || !ent.box) return { note: (g && g.note) || 'nothing below it' };
    var dy = g.top - ent.box.lo[1];
    if (Math.abs(dy) > 1e-9) { var w = E.worldPos(obj); E.setWorldPos(obj, [w[0], w[1] + dy, w[2]]); settle(); }
    return { dy: dy, support: g.out.support };
  };
  /* BESIDE, AS THE CAMERA SEES IT: right of it first, then left, in front, behind, the diagonals —
     each spot far enough out that the two boxes cannot meet (their extents along that direction
     plus a gap), put down on what is under it, and the first with nothing in the way and ground
     under it at the same level is kept. */
  var beside = function (ne) {
    var nb = ne.box, c = centre(nb), r = [1, 0];
    if (ci) { var rl = Math.hypot(ci.W[0], ci.W[2]); if (rl > 1e-6) r = [ci.W[0] / rl, ci.W[2] / rl]; }
    var f = [-r[1], r[0]];
    var mix = function (a, b) { var x = a[0] + b[0], z = a[1] + b[1], l = Math.hypot(x, z) || 1; return [x / l, z / l]; };
    var neg = function (a) { return [-a[0], -a[1]]; };
    var sides = [['to its right', r], ['to its left', neg(r)], ['in front of it', f], ['behind it', neg(f)],
                 ['in front and to the right', mix(r, f)], ['in front and to the left', mix(neg(r), f)],
                 ['behind and to the right', mix(r, neg(f))], ['behind and to the left', mix(neg(r), neg(f))]];
    var ob = ent.box, ohx = (ob.hi[0] - ob.lo[0]) / 2, ohz = (ob.hi[2] - ob.lo[2]) / 2;
    var hx = (nb.hi[0] - nb.lo[0]) / 2, hz = (nb.hi[2] - nb.lo[2]) / 2;
    /* THE LEVEL IS WHAT IT STANDS ON, never below it. A box can lie about its bottom: a skinned
       model's is its file's raw size, and beside FrigoToToTo on rot-rush "put down on what is under
       it" stood a sphere 361 m down, on the underside of the world mesh. So the level is the higher
       of the box's bottom and the surface under the thing's own origin (where a model's feet are):
       the same for an honest box on the ground, and the right one for a box that reaches through it. */
    var y0 = nb.lo[1], raised = 0;
    try {
      var wo = E.worldPos(ne.o), hy = castDown(tab, wo[0], wo[1] + 0.1, wo[2], subtreeSet(ne));
      if (hy) { var gy = wo[1] + 0.1 - hy.t; if (gy > y0 + 0.05) { raised = gy - y0; y0 = gy; } }
    } catch (e) {}
    var best = null, passedOver = [];
    for (var i = 0; i < sides.length; i++) {
      var d = sides[i][1];
      var nh = Math.abs(d[0]) * hx + Math.abs(d[1]) * hz, ph = Math.abs(d[0]) * ohx + Math.abs(d[1]) * ohz;
      var gap = (typeof spec.gap === 'number' && isFinite(spec.gap)) ? Math.max(0, spec.gap)
              : Math.min(1, Math.max(0.1, 0.5 * Math.min(nh, ph)));
      var t = nh + ph + gap;
      bottomTo([c[0] + d[0] * t, y0, c[2] + d[1] * t]);
      var dn = dropNow(), ov = overlapsOf(tab, ent), score = 0;
      ov.forEach(function (x) { score += x.share; });
      if (!dn.support) score += 10;
      else if (Math.abs(dn.dy) > 0.5) score += 0.5;       /* off the edge of the thing it stands on */
      var cand = { side: sides[i][0], gap: gap, world: E.worldPos(obj), score: score, support: dn.support || null };
      if (!best || score < best.score) best = cand;
      if (score === 0) break;
      /* Why a nearer side was not taken, so a surprising "behind it" explains itself. */
      passedOver.push(sides[i][0] + ': ' + (ov.length ? 'runs into ' + ov[0].key + ' (' + ov[0].share + ')'
        : !dn.support ? 'nothing under it'
        : 'a ' + r3(Math.abs(dn.dy)) + ' m ' + (dn.dy < 0 ? 'drop' : 'step up')));
    }
    E.setWorldPos(obj, best.world);
    settle();
    if (best.score > 0) {
      notes.push('no side of ' + ne.key + ' was clear (something in the way, nothing under it, or a step of more than 0.5 m); ' +
                 'this side was the least bad: ' + best.side);
    } else if (passedOver.length) {
      notes.push('passed over — ' + passedOver.slice(0, 7).join('; '));
    }
    /* A BOX CAN LIE. rot-rush keeps two brainrot packs at the origin whose mesh boxes are their
       files' raw centimetres — 117 x 339 x 120 m, enabled, on the world layer, and nowhere on
       screen — and "beside" one of them was 60 m away. The size is in the answer, and a near thing
       far bigger than what is placed says so. */
    var ns = sizeOf(nb), big = Math.max(ns[0], ns[1], ns[2]), mine = Math.max(ohx, ohz) * 2 || 1;
    if (big > 30 && big > 20 * mine) {
      notes.push(ne.key + ' is ' + r3(big) + ' m across by its box, so beside it is far off; a skinned model\'s box can be ' +
                 'its file\'s raw size — check it is the object you mean, or give at:[x,y,z]');
    }
    if (raised > 1) {
      notes.push(ne.key + '\'s box reaches ' + r3(raised) + ' m below what it stands on, so this was put down from the ' +
                 'surface under its origin, not from the bottom of its box');
    }
    return 'beside ' + ne.key + ' (' + ns.map(function (v) { return r3(v); }).join('×') + ' m), ' + best.side +
           ' as the camera sees it, ' + r3(best.gap) + ' m from it' + (best.support ? ', standing on ' + best.support : '');
  };

  var where, dropIt = !!spec.drop;
  if (spec.pos) where = 'its origin at ' + JSON.stringify(r3v(item.pos)) + ', as the file keeps it';
  else if (spec.at) { bottomTo(spec.at); where = 'the middle of its bottom at ' + JSON.stringify(r3v(spec.at)); }
  else if (nearEnt && ent.box) where = beside(nearEnt);
  else if (nearEnt) {
    /* Nothing to stand: a light goes half a metre over the thing it is near. */
    var nc = centre(nearEnt.box);
    E.setWorldPos(obj, [nc[0], nearEnt.box.hi[1] + 0.5, nc[2]]);
    settle();
    where = 'half a metre above ' + nearEnt.key + ' (it has no body to stand anywhere)';
  } else {
    /* Neither `at` nor `near`: where the middle of the view meets the GROUND, resting there. A
       third-person camera looks at the player, and the first try put a box on the proof game's
       player's head; so the ray goes on past anything not at least twice as wide as the thing
       being placed (a player, a crystal), and stops at what could hold it (a floor, a platform,
       a table under a cup). The answer names what it passed. */
    var hit = null, first = null, passed = [], skipSet = subtreeSet(ent);
    if (ci) {
      var ray = rayAt(ci, toCanvas(ci, [0.5, 0.5]));
      var fp = ent.box ? Math.max(ent.box.hi[0] - ent.box.lo[0], ent.box.hi[2] - ent.box.lo[2]) : 0.5;
      for (var k = 0; k < 8 && !hit; k++) {
        var hh = cast(tab, ray, skipSet, ci);
        if (!hh) break;
        var hp = { key: hh.ent.key, t: hh.t, p: [ray.o[0] + ray.d[0] * hh.t, ray.o[1] + ray.d[1] * hh.t, ray.o[2] + ray.d[2] * hh.t] };
        if (!first) first = hp;
        var hb = hh.ent.own || hh.ent.box;
        if (hb && (hb.hi[0] - hb.lo[0]) >= 2 * fp && (hb.hi[2] - hb.lo[2]) >= 2 * fp) hit = hp;
        else { passed.push(hh.ent.key); subtreeSet(hh.ent).forEach(function (x) { skipSet.add(x); }); }
      }
    }
    if (!hit && first) { hit = first; passed = []; }
    if (hit) {
      bottomTo(hit.p);
      where = 'where the middle of the view meets ' + hit.key + ', ' + r3(hit.t) + ' m from the camera' +
              (passed.length ? ' (past ' + passed.slice(0, 4).join(', ') + ')' : '');
    } else { bottomTo([0, 0, 0]); where = 'the world origin: the middle of the view meets nothing — give at or near'; }
    dropIt = true;
  }
  if (dropIt && !nearEnt) {
    var dd = dropNow();
    if (dd.dy === undefined) notes.push('drop: ' + dd.note + ' — not moved');
    else if (Math.abs(dd.dy) > 0.005) notes.push((dd.dy < 0 ? 'dropped ' : 'lifted ') + r3(Math.abs(dd.dy)) + ' m onto ' + dd.support);
  }

  var l = E.local(obj);
  item.pos = r6v(l.pos);
  REG[id] = item;
  /* KEYS THAT MOVED. A name inside the placement that repeats a name the game used once gives the
     game's object a numbered key while the placement exists (rot-rush: a brainrot copy renumbered
     8 keys). Measured, not guessed: every key before against every key after. */
  var ren = [];
  tab0.keys.forEach(function (k, ob2) { var k2 = tab.keys.get(ob2); if (k2 !== undefined && k2 !== k) ren.push({ from: k, to: k2 }); });
  var res = { ok: true, engine: cx.kind, key: tab.keys.get(obj) || null, name: String(obj.name || ''), id: id,
              how: String(got.how || ''), where: where, after: shown(snap(tab, ent)),
              ground: groundAnswer(groundOf(tab, ent)), overlaps: overlapsOf(tab, ent),
              item: item, replaced: !!old, ms: Math.round(nowMs() - t0) };
  if (prev) res.prev = prev;
  if (ren.length) { res.renumbered = ren.slice(0, 12); res.renumbered_n = ren.length; }
  if (notes.length) res.notes = notes;
  return res;
};

var rtTarget = function (cx) { return cx.kind === 'playcanvas' ? cx.app : cx.root; };
/* Taken out by id, the runtime's own way (nested copies go with it, what it made gives its GPU
   memory back). */
S.unplace = async function (ids, rt, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return notReachable(cx);
  var R;
  try { R = await runtimeOf((rt || {}).runtime); }
  catch (e) { return { ok: false, engine: cx.kind, error: 'the Studio runtime did not load: ' + errText(e) }; }
  var removed = {};
  (ids || []).forEach(function (id) { id = String(id); removed[id] = R.unplaceStudioItem(rtTarget(cx), id); delete REG[id]; });
  return { ok: true, engine: cx.kind, removed: removed };
};
/* Undo's hands: each entry puts an exact item back (its own pos, rot and scale, no placing logic)
   or, with no item, takes that id out. */
S.sync = async function (list, rt, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return notReachable(cx);
  rt = rt || {};
  var R;
  try { R = await runtimeOf(rt.runtime); }
  catch (e) { return { ok: false, engine: cx.kind, error: 'the Studio runtime did not load: ' + errText(e) }; }
  var target = rtTarget(cx), placed = [], removed = [], failed = [];
  var dropLate = function (id2) { return function (late) { if (late && late.ok) R.unplaceStudioItem(target, id2); }; };
  for (var i = 0; i < (list || []).length; i++) {
    var it = list[i] || {}, id = String(it.id || (it.put && it.put.id) || '');
    if (!id) continue;
    if (!it.put) { R.unplaceStudioItem(target, id); delete REG[id]; removed.push(id); continue; }
    var ro;
    try { ro = await runtimeOpts(cx, rt, it.put); } catch (e) { failed.push({ id: id, error: errText(e) }); continue; }
    var g = await withTimeout(R.placeStudioItem(target, it.put, ro), 30000, dropLate(id));
    if (g && (g.ok || g.pending)) { placed.push(id); REG[id] = JSON.parse(JSON.stringify(it.put)); }
    else failed.push({ id: id, error: (g && g.error) || 'nothing was placed' });
  }
  return { ok: true, engine: cx.kind, placed: placed, removed: removed, failed: failed };
};
/* Remove means remove for a placement, and is refused for what the game's code built: the code
   builds it again on the next run, so the honest edit is a hide (the editor's rule). */
S.remove = async function (target, rt, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return notReachable(cx);
  var tab = scan(cx), t = String(target == null ? '' : target).trim(), id = '';
  if (t && placedOf(cx, t)) id = t;
  else {
    var r = resolve(tab, t);
    if (!r.ent) return { ok: false, engine: cx.kind, error: r.error, like: r.like, keys: r.keys };
    for (var e = r.ent; e; e = e.parent) { var m = markOf(cx, e.o); if (m) { id = m; break; } }
    if (!id) {
      return { ok: false, engine: cx.kind, key: r.ent.key, error: r.ent.key + ' was built by the game\'s own code: it cannot be removed, only hidden — ' +
               'POST /api/live/edit {"target": ' + JSON.stringify(r.ent.key) + ', "visible": false}' };
    }
  }
  var R;
  try { R = await runtimeOf((rt || {}).runtime); }
  catch (e2) { return { ok: false, engine: cx.kind, error: 'the Studio runtime did not load: ' + errText(e2) }; }
  var obj = placedOf(cx, id), key = obj ? (tab.keys.get(obj) || null) : null, name = obj ? String(obj.name || '') : '';
  var item = liveItem(cx, id);
  var n = R.unplaceStudioItem(rtTarget(cx), id);
  delete REG[id];
  return { ok: true, engine: cx.kind, id: id, key: key, name: name, removed: n, item: item };
};

/* ------------------------------------------------------------------ the view (live_view.py)
   What the Studio's own camera needs to answer a change with a picture of it: world boxes by key,
   where those boxes land in the frame under the camera that is drawing NOW, the navigator's camera
   saved before a look and put back after it, and a switched-off place switched on while it is
   photographed. The camera itself is the navigator's (`window.__nav`, live_navigate.py): one Studio
   camera in the page, whoever moves it. */
var navOf = function () { try { return window.__nav || null; } catch (e) { return null; } };
S.frames = frames;
/* Targets as the edit API reads them (key, name or path), with the world box of each subtree. */
S.boxes = function (targets, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return notReachable(cx);
  var tab = scan(cx), E = cx.E, items = [];
  (targets || []).forEach(function (t) {
    var r = resolve(tab, t);
    if (!r.ent) { items.push({ target: t, error: r.error, like: r.like, keys: r.keys }); return; }
    var e = r.ent;
    items.push({ target: t, key: e.key, name: String(e.o.name || ''), how: r.how, on: !!e.on,
                 min: e.box ? r3v(e.box.lo) : null, max: e.box ? r3v(e.box.hi) : null, world: r3v(E.worldPos(e.o)) });
  });
  return { ok: true, engine: cx.kind, items: items };
};
/* Where each key's box lands in the picture, in 0..1 of the VIEWPORT (what a screenshot is), clipped
   to the frame: the 8 corners projected, plus where the box's edges cross the near plane (the rule
   `in_view` already uses). The camera is the one the canvas was last drawn with, so after the
   navigator takes the view and two frames pass, it is the Studio's; after it hands the view back,
   the game's. A thing with no body is a half-metre cube round its origin, so a light still gets a
   mark. `whole`: all of it inside the frame. */
S.screenBoxes = function (keys, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return notReachable(cx);
  var tab = scan(cx), ci = camInfo(cx), E = cx.E, items = {};
  if (!ci) return { ok: false, engine: cx.kind, error: 'no camera draws this scene yet' };
  var cl = function (v) { return Math.max(0, Math.min(1, v)); };
  (keys || []).forEach(function (k) {
    var r = resolve(tab, k);
    if (!r.ent) { items[k] = { box: null, error: r.error }; return; }
    var b = r.ent.box;
    if (!b) { var w = E.worldPos(r.ent.o); b = { lo: [w[0] - 0.25, w[1] - 0.25, w[2] - 0.25], hi: [w[0] + 0.25, w[1] + 0.25, w[2] + 0.25] }; }
    /* The object's own values at the moment of the frame: a look compares them with what the edit
       set, because a game that drives an object every frame puts it straight back (rot-rush bobs
       its lucky block: y was back at 5.50 within a frame of an edit that set 6.50). */
    var loc = null;
    try { var L0 = E.local(r.ent.o); loc = { pos: L0.pos, rot: L0.rot, q: L0.q || null, scale: L0.scale, on: L0.on }; } catch (e) {}
    var s = boxOnCanvas(ci, b);
    if (!s) { items[k] = { box: null, off: true, drawn: !!r.ent.on, local: loc }; return; }
    var a = toView(ci, [cl(s.lo[0]), cl(s.lo[1])]), z = toView(ci, [cl(s.hi[0]), cl(s.hi[1])]);
    items[k] = { box: [r3(a[0]), r3(a[1]), r3(z[0]), r3(z[1])], drawn: !!r.ent.on, local: loc,
                 whole: s.lo[0] >= 0 && s.lo[1] >= 0 && s.hi[0] <= 1 && s.hi[1] <= 1 };
  });
  var n = navOf();
  return { ok: true, engine: cx.kind, items: items,
           camera: { pos: r3v(ci.pos), fwd: r3v(ci.fwd), name: ci.name, studio: !!(n && n.on && n.ours && ci.cam === n.ours) } };
};
/* What is in the picture, by key: the scene's own top-level things first (a crate, not its eight
   beams — the first version listed eight beams of one crate), nearest the middle of the frame first,
   then the parts inside them to fill the list. A thing whose box covers most of the frame (rot-rush's
   `world` and `actors`, a floor) is a container or the ground, and goes last: it led the list before.
   Drawn things only, in the frustum. */
S.inView = function (limit, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return [];
  var tab = scan(cx), ci = camInfo(cx), E = cx.E, rows = [];
  if (!ci) return [];
  tab.list.forEach(function (ent) {
    if (ent.studio || !ent.key || !ent.box || !ent.on) return;
    var v = viewOf(ci, ent, E);
    if (!v.in_view || !v.screen) return;
    var sb = boxOnCanvas(ci, ent.box), area = 1;
    if (sb) area = Math.max(0, Math.min(1, sb.hi[0]) - Math.max(0, sb.lo[0])) * Math.max(0, Math.min(1, sb.hi[1]) - Math.max(0, sb.lo[1]));
    rows.push({ key: ent.key, top: !ent.parent, big: area > 0.6, d: Math.hypot(v.screen[0] - 0.5, v.screen[1] - 0.5) });
  });
  rows.sort(function (a, b) {
    if (a.big !== b.big) return a.big ? 1 : -1;
    return a.top === b.top ? a.d - b.d : (a.top ? -1 : 1);
  });
  return rows.slice(0, Math.max(1, Math.min(60, limit || 8))).map(function (r) { return r.key; });
};
/* Is every mesh of these keys really drawn yet? PlayCanvas links a new material's shader in parallel
   and skips the mesh until it has: on rot-rush, two frames after a place only the new box's SHADOW
   was in the picture (the shadow pass does not wait), and a "did its box change" pixel test passed on
   the shadow alone. So the engine is asked: every shader instance a mesh has made is ready or failed,
   and it has made at least one. `known` is false where this cannot be read (another engine version),
   and three.js compiles on the first draw, so it is ready at once. */
S.drawReady = function (keys, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return { ready: true, known: false };
  if (cx.kind !== 'playcanvas') return { ready: true, known: true };
  var tab = scan(cx), pending = 0, known = false, meshes = 0;
  var walk = function (ent) {
    var m = pcMis(ent.o);
    if (m) {
      m.list.forEach(function (mi) {
        meshes++;
        var c = mi && mi._shaderCache;
        if (!c || typeof c.forEach !== 'function') return;
        known = true;
        var n = 0;
        c.forEach(function (si) { n++; var sh = si && si.shader; if (sh && !sh.ready && !sh.failed) pending++; });
        if (!n) pending++;                   /* not drawn once yet */
      });
    }
    ent.kids.forEach(walk);
  };
  (keys || []).forEach(function (k) { var r = resolve(tab, k); if (r.ent) walk(r.ent); });
  return { ready: known ? pending === 0 : true, known: known, pending: pending, meshes: meshes };
};
/* THE NAVIGATOR'S CAMERA, SAVED AND PUT BACK. A look must hand the view back to the game — but an
   agent that stood the Studio camera somewhere with /goto (and revealed a far platform with it) has
   a view of its own, and release() would throw that away with its reveals. So: saved at the start,
   and at the end either put back exactly (position, rotation, fov, the orbit target) or released,
   whichever it was. The camera is moved by transform, never by angles read back: PlayCanvas reports
   a camera turned 180 degrees as pitch 160 plus a hidden roll (live_navigate.py measured it). */
var VIEW = { prev: null };
var camXf = function (n) {
  var c = n.ours;
  if (!c) return null;
  if (n.kind === 'playcanvas') {
    var p = c.getPosition(), q = c.getRotation();
    return { p: [p.x, p.y, p.z], q: [q.x, q.y, q.z, q.w], fov: c.camera ? c.camera.fov : null };
  }
  return { p: [c.position.x, c.position.y, c.position.z], q: [c.quaternion.x, c.quaternion.y, c.quaternion.z, c.quaternion.w],
           fov: typeof c.fov === 'number' ? c.fov : null };
};
var setCamXf = function (n, x) {
  var c = n.ours;
  if (!c || !x) return;
  if (n.kind === 'playcanvas') {
    c.setPosition(x.p[0], x.p[1], x.p[2]);
    c.setRotation(x.q[0], x.q[1], x.q[2], x.q[3]);
    if (x.fov !== null && c.camera) c.camera.fov = x.fov;
    return;
  }
  c.position.set(x.p[0], x.p[1], x.p[2]);
  c.quaternion.set(x.q[0], x.q[1], x.q[2], x.q[3]);
  if (x.fov !== null && typeof c.fov === 'number') { c.fov = x.fov; if (c.updateProjectionMatrix) c.updateProjectionMatrix(); }
  c.updateMatrixWorld(true);
};
/* The navigator's own on/off, without its release(): release() also puts back what /goto revealed. */
var studioOn = function (n, on) {
  if (n.kind === 'playcanvas') {
    try { if (n.ours && n.ours.camera) n.ours.camera.enabled = on; } catch (e) {}
    try { if (n.game && n.game.camera) n.game.camera.enabled = !on; } catch (e) {}
  }
  n.on = on;
};
S.viewBegin = function () {
  var n = navOf();
  if (!n || typeof n.reach !== 'function') return { ok: false, error: 'the navigator is not on this page' };
  var rr = n.reach();
  if (!rr || !rr.kind) return { ok: false, error: 'no engine reachable' };
  /* The navigator hears the game's camera through its own render hook, a frame after it first pins
     the scene; this script's hook heard it already. Handed over, the game's pose is known on the
     first look of a page too — measured: without it the first look framed "front" from yaw 0 and
     could not say whether the game's camera stayed put. */
  try {
    if (n.kind === 'three' && n.three && !n.three.camera) {
      var cx0 = ctxOf(), sr = cx0.kind === 'three' ? seenOf(cx0.root) : null;
      if (sr && sr.canvasCam && sr.canvasCam.isCamera && sr.canvasCam !== n.ours) n.three.camera = sr.canvasCam;
    }
  } catch (e) {}
  var st = { on: !!n.on, xf: null, target: null, near: null };
  try { if (n.on) st.xf = camXf(n); } catch (e) {}
  try { st.target = n.target ? JSON.parse(JSON.stringify(n.target)) : null; } catch (e) {}
  VIEW.prev = st;
  var cx = ctxOf(), ci = cx.kind ? camInfo(cx) : null, R = cx.kind ? rectOf(cx) : null;
  var gp = null, hfov = false;
  try { gp = n.gamePose(); } catch (e) {}
  try { if (n.kind === 'playcanvas' && n.game && n.game.camera) hfov = !!n.game.camera.horizontalFov; } catch (e) {}
  var vw = (typeof innerWidth === 'number' && innerWidth) || (R ? R.vw : 1280), vh = (typeof innerHeight === 'number' && innerHeight) || (R ? R.vh : 720);
  return { ok: true, engine: n.kind, studio_on: st.on, game: gp, horizontal_fov: hfov,
           aspect: R && R.h > 0 ? R.w / R.h : vw / vh, vw: vw, vh: vh,
           dpr: (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1,
           near: ci ? ci.near : 0.1, far: ci ? ci.far : 1e6, ortho: ci ? !!ci.ortho : false };
};
/* THE STUDIO CAMERA'S OWN NEAR PLANE, for one look. It is cloned from the game's, and rot-rush's
   near clip is 4.2 m: a 1.5 m block could not be framed closer than 10 m without the plane cutting
   it. Lowered for the look (never raised), and put back by viewEnd. The game's camera is not touched. */
var nearOf = function (n) {
  var c = n && n.ours;
  if (!c) return null;
  if (n.kind === 'playcanvas') return c.camera ? c.camera.nearClip : null;
  return typeof c.near === 'number' ? c.near : null;
};
var setNear = function (n, v) {
  var c = n && n.ours;
  if (!c || typeof v !== 'number' || !(v > 0)) return;
  if (n.kind === 'playcanvas') { if (c.camera) c.camera.nearClip = v; return; }
  c.near = v;
  if (c.updateProjectionMatrix) c.updateProjectionMatrix();
};
S.viewNear = function (v) {
  var n = navOf(), st = VIEW.prev;
  var cur = nearOf(n);
  if (cur === null || !(v > 0) || v >= cur) return cur;
  if (st && st.near === null) st.near = cur;
  setNear(n, v);
  return v;
};
/* "player": the game's own camera for this look, even when a /goto view is up. */
S.viewGame = function () { var n = navOf(); if (n && n.on) studioOn(n, false); return true; };
/* Is the Studio camera the one drawing? For three, only once the navigator's render wrap has swapped
   it in, which can take a frame after the wrap is first installed. */
S.viewDrawing = function () {
  var n = navOf();
  if (!n || !n.on || !n.ours) return false;
  if (n.kind === 'playcanvas') { try { return !!(n.ours.camera && n.ours.camera.enabled); } catch (e) { return false; } }
  var cx = ctxOf();
  var r = cx.kind === 'three' ? seenOf(cx.root) : null;
  return !!(r && r.canvasCam === n.ours);
};
S.viewEnd = function () {
  var n = navOf(), st = VIEW.prev || { on: false, xf: null, target: null };
  VIEW.prev = null;
  if (!n) return { ok: false, error: 'the navigator is not on this page' };
  var out = { ok: true };
  try { if (st.near !== null && st.near !== undefined) setNear(n, st.near); } catch (e) {}
  if (st.on) {
    try { if (st.xf) setCamXf(n, st.xf); } catch (e) { out.error = String(e && e.message || e); }
    n.target = st.target;
    studioOn(n, true);
    out.view = 'restored';
  } else {
    var rel = null;
    try { rel = n.release(); } catch (e) { out.error = String(e && e.message || e); }
    n.target = st.target;
    out.view = 'released';
    if (rel && rel.put_back) out.put_back = rel.put_back;
  }
  try { out.game = n.gamePose(); } catch (e) {}
  return out;
};
/* REVEAL, for a look: every switched-off ancestor of these keys switched on and HELD (see HELD at
   the top), never the object itself — an edit that hid it must be photographed hidden. unreveal()
   puts every one back. */
S.reveal = function (keys, o) {
  var cx = ctxOf(o && o.scene);
  if (!cx.kind) return [];
  var tab = scan(cx), names = [], prop = cx.kind === 'playcanvas' ? 'enabled' : 'visible';
  var held = function (p) { for (var i = 0; i < HELD.list.length; i++) if (HELD.list[i][0] === p) return true; return false; };
  (keys || []).forEach(function (k) {
    var r = resolve(tab, k);
    if (!r.ent) return;
    for (var p = r.ent.o.parent; p && p !== cx.root; p = p.parent) {
      var own = cx.kind === 'playcanvas' ? (p._enabled !== undefined ? p._enabled : p.enabled) : p.visible;
      if (own === false && !held(p)) {
        try { p[prop] = true; HELD.list.push([p, prop]); names.push(String(p.name || '?')); } catch (e) {}
      }
    }
  });
  if (HELD.list.length && cx.kind === 'playcanvas' && !HELD.app) {
    try { cx.app.on('prerender', holdNow); HELD.app = cx.app; } catch (e) {}
  }
  return names;
};
S.unreveal = function () {
  var list = HELD.list;
  HELD.list = [];                          /* first, so the hold stops re-applying mid-way */
  for (var i = list.length - 1; i >= 0; i--) { try { list[i][0][list[i][1]] = false; } catch (e) {} }
  if (HELD.app) { try { HELD.app.off('prerender', holdNow); } catch (e) {} HELD.app = null; }
  return list.length;
};
})();
"""


# THE VERSION IS THE SCRIPT'S OWN HASH. A number bumped by hand was forgotten once already in this
# file's first day: the page kept the old script, because the check said the version matched.
VERSION = hashlib.sha1(SCENE_JS.encode("utf-8")).hexdigest()[:12]


def page_script() -> str:
    return SCENE_JS.replace("__VERSION__", json.dumps(VERSION))


# ---------------------------------------------------------------------------------------- guards
def _off() -> Optional[dict]:
    if not settings.get("cc_scene_edit", True):
        return {"ok": False, "error": "Scene edit is off. Turn it on in Settings → Studio engine → Scene edit."}
    return None


def _need_open(entry: dict) -> Optional[dict]:
    if not entry.get("url"):
        return {"ok": False, "error": "no game is open for this project — POST /api/live/open first"}
    return None


_UNREACHABLE = ("the engine's app object could not be reached: not on the page, not in the heap, not in "
                "the closures of the game's frame callback. One dev-only line where the game is created "
                "unlocks it: window.__game = { app }; (PlayCanvas) or window.__game = { scene }; (three).")


def _page_refusal(res: dict) -> dict:
    """What the page said when it had no three.js scene and no PlayCanvas app to work on."""
    eng = str(res.get("engine") or "")
    if eng and res.get("reachable"):
        return {"ok": False, "engine": eng, "error": "%s is read-only here — use /api/live/eval" % eng}
    if eng and eng not in ("dom", "canvas2d", "webgl"):
        return {"ok": False, "engine": eng, "error": _UNREACHABLE, "hint": res.get("hint") or ""}
    return {"ok": False, "engine": eng, "error": "no three.js scene and no PlayCanvas app on this page. "
            "Only those two are editable here — use /api/live/eval for anything else."}


# ---------------------------------------------------------------------------------------- the page, driven
async def ensure_scene(live) -> None:
    """Install the page script, or replace a stale one."""
    if not await live.raw("!!(window.__scene && window.__scene.version === %s)" % json.dumps(VERSION)):
        await live.raw(page_script(), wait=False)


async def page(live, expr: str) -> Any:
    """Evaluate `expr` (a call that may return a promise) and bring its JSON back whole.

    Not `live.ask`: its flattener cuts every list at 60 entries and every object at 60 keys and
    drops keys that start with an underscore — right for a stranger's object, wrong for rows this
    script built to be read whole."""
    out = await live.raw("(async()=>JSON.stringify(await (%s)))()" % expr)
    return json.loads(out) if out else None


async def _prepared(live, entry: dict) -> None:
    await L._bridge(live, entry)
    try:
        r = await live.ask("__live.reach()") or {}
    except Exception:
        r = {}
    # THE PIN DOES NOT SURVIVE A RELOAD, AND THE BRIDGE RUNS ONCE PER TAB. rot-rush reloaded its own
    # page once, a few seconds after Space; the app pinned at open was gone with the old document,
    # and every call after that answered "unreachable" beside a game that was plainly running. So
    # when the page names an engine it cannot reach, the closure hunt runs again, as the navigator's
    # own reach does. Only then: a game the shim reaches unaided never pays for it.
    if not r.get("reachable") and str(r.get("engine") or "") in ("playcanvas", "three"):
        try:
            from .live_navigate import hunt_closures
            if await asyncio.wait_for(hunt_closures(live), 10.0):
                await asyncio.sleep(0.1)            # a renderer-only find sees its scene next frame
        except Exception:
            pass
    await ensure_scene(live)


def _in_tab(project: str, expr: str) -> Any:
    """One call into the page on a short-lived connection."""
    e = L._entry(project)

    async def go():
        ws, live = await L._session(e)
        try:
            await _prepared(live, e)
            try:
                return await page(live, expr)
            except RuntimeError as ex:
                # The document was replaced between the install and the call: install again, once.
                if "__scene" not in str(ex) and "is not defined" not in str(ex):
                    raise
                await _prepared(live, e)
                return await page(live, expr)
        finally:
            await ws.close()

    return L._run(go)


# ---------------------------------------------------------------------------------------- the saved edits file
_FILE_LOCK = threading.Lock()
_BAKED: set = set()


def edits_path(project: str) -> Path:
    return Path(project) / EDITS_NAME


def new_doc() -> dict:
    """The shape `emptyEdits()` in kit.ts makes, so the Edit tab reads a file this wrote."""
    return {"version": 1, "asset": "", "params": {}, "parts": {}, "bones": [], "pose": {}, "clips": [], "mods": []}


def read_doc(project: str) -> tuple[Optional[dict], str]:
    """(the document or None when there is no file, an error). A broken file is never written over."""
    p = edits_path(project)
    if not p.is_file():
        return None, ""
    try:
        text = p.read_text(encoding="utf-8")
        doc = json.loads(text) if text.strip() else {}
    except Exception as ex:
        return None, ("%s is not valid JSON (%s) — fix it or delete it; nothing was written"
                      % (p, str(ex)[:160]))
    if not isinstance(doc, dict):
        return None, "%s does not hold a JSON object — nothing was written" % p
    for section, kind in (("parts", dict), ("placed", list)):
        if section in doc and not isinstance(doc[section], kind):
            return None, "%s: `%s` is not a%s — nothing was written" % (
                p, section, "n object" if kind is dict else " list")
    return doc, ""


def _backup_once(p: Path, info: dict) -> None:
    """One `.bak` of what the file held before this backend first REPLACED OR DELETED it. Call with
    `_FILE_LOCK` held.

    Both, not only the replace: a `clear` that empties the file deletes it, and a delete with no
    backup left the in-memory undo stack as the only copy — gone at the next restart. Marked done
    only once the copy exists, so a copy that failed is tried again rather than never."""
    key = str(p.resolve()).lower()
    if key in _BAKED:
        return
    if p.is_file():
        bak = p.with_name(EDITS_NAME + ".bak")
        tmpb = p.with_name(EDITS_NAME + ".bak.tmp-%d" % os.getpid())
        tmpb.write_bytes(p.read_bytes())
        os.replace(tmpb, bak)
        info["bak"] = str(bak)
    _BAKED.add(key)          # no file yet is a state too: our own later writes need no backup


def write_doc(project: str, doc: dict) -> dict:
    """Atomically: a temp file beside it, then a replace. One `.bak` of what was there before the
    first write this backend makes to that file — enough to get back to where the session started,
    and not a new backup on every nudge."""
    p = edits_path(project)
    info: dict = {}
    with _FILE_LOCK:
        _backup_once(p, info)
        out = dict(doc)
        out.setdefault("version", 1)
        out["updated"] = int(time.time() * 1000)
        tmp = p.with_name(EDITS_NAME + ".tmp-%d" % os.getpid())
        tmp.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    return info


def merge_part(doc: dict, key: str, values: dict) -> Optional[dict]:
    """Merge LOCAL absolute values into `parts[key]`, keeping every field not named. Returns what the
    entry was before (None when there was none), which is what undo puts back.

    `hidden: False` is WRITTEN. Removing the key only undid a saved hide, so an object the game's own
    code hides came back hidden after a reload while the answer said "written: hidden false". The
    runtime and the editor's applier both set visible = !hidden, and the Edit tab keeps `false` too."""
    parts = doc.setdefault("parts", {})
    prev = copy.deepcopy(parts[key]) if key in parts else None
    cur = dict(parts.get(key) or {})
    for f, v in (values or {}).items():
        if f == "hidden":
            cur["hidden"] = bool(v)
        elif f in ("pos", "rot", "scale"):
            cur[f] = [float(x) for x in v]
    if cur:
        parts[key] = cur
    else:
        parts.pop(key, None)
    return prev


_SECTIONS = ("parts", "placed", "params", "mods", "verts", "bones", "pose", "clips", "bakes", "world")
_META = {"version", "asset", "updated"}


def is_empty_doc(doc: dict) -> bool:
    """Nothing left to apply: every section empty and nothing unknown beside the header. The Edit
    tab never keeps a sidecar for an untouched asset, and neither does this."""
    for k, v in (doc or {}).items():
        if k in _META:
            continue
        if k in _SECTIONS and not v:
            continue
        return False
    return True


def remove_doc(project: str) -> dict:
    """Delete the file, with the same once-per-backend `.bak` a write makes. Returns {bak?}."""
    info: dict = {}
    p = edits_path(project)
    with _FILE_LOCK:
        _backup_once(p, info)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return info


def restore_disk(doc: dict, entries: list) -> list:
    """Put saved entries back as they were, newest change first. Returns the keys touched.

    Generic over the two sections an agent writes: `parts` (a map by key) and `placed` (a list by
    id), so `place` can push its own records onto the same undo stack."""
    touched = []
    for d in reversed(entries or []):
        section = d.get("section", "parts")
        if section == "placed":
            items = doc.setdefault("placed", [])
            items[:] = [it for it in items if not (isinstance(it, dict) and it.get("id") == d.get("id"))]
            if d.get("prev") is not None:
                items.append(copy.deepcopy(d["prev"]))
            touched.append(d.get("id"))
        else:
            parts = doc.setdefault("parts", {})
            if d.get("prev") is None:
                parts.pop(d["key"], None)
            else:
                parts[d["key"]] = copy.deepcopy(d["prev"])
            touched.append(d["key"])
    return touched


def _deg(v) -> Optional[list]:
    try:
        return [round(math.degrees(float(x)), 3) for x in v][:3]
    except Exception:
        return None


def _r3(v) -> Optional[list]:
    try:
        return [round(float(x), 3) for x in v][:3]
    except Exception:
        return None


def summarise(doc: dict, cap: int = 300) -> dict:
    """The file, as an agent reads it: rotations in degrees, every section counted."""
    parts_in = doc.get("parts") if isinstance(doc.get("parts"), dict) else {}
    parts: dict = {}
    for i, (k, v) in enumerate(parts_in.items()):
        if i >= cap:
            break
        if not isinstance(v, dict):
            continue
        row = {}
        for f, val in v.items():
            if f == "rot":
                row["rot"] = _deg(val)
            elif f in ("pos", "scale"):
                row[f] = _r3(val)
            else:
                row[f] = val
        parts[k] = row
    placed = []
    for it in (doc.get("placed") if isinstance(doc.get("placed"), list) else [])[:cap]:
        if not isinstance(it, dict):
            continue
        ref = it.get("ref") if isinstance(it.get("ref"), dict) else {}
        placed.append({"id": it.get("id"), "name": it.get("name"), "kind": ref.get("kind"),
                       "ref": {k: v for k, v in ref.items() if k != "kind" and not isinstance(v, (list, dict))},
                       "pos": _r3(it.get("pos") or [0, 0, 0]), "rot": _deg(it.get("rot") or [0, 0, 0]),
                       "scale": _r3(it.get("scale") or [1, 1, 1])})
    world = doc.get("world") if isinstance(doc.get("world"), dict) else {}

    def n(section):
        v = doc.get(section)
        return len(v) if isinstance(v, (list, dict)) else 0

    counts = {"parts": n("parts"), "placed": n("placed"), "params": n("params"), "mods": n("mods"),
              "verts": n("verts"), "bones": n("bones"), "pose": n("pose"), "clips": n("clips"),
              "bakes": n("bakes"), "world": int("background" in world) + int("fog" in world)}
    out = {"parts": parts, "placed": placed, "counts": counts}
    if counts["parts"] > cap:
        out["parts_more"] = counts["parts"] - cap
    return out


# ---------------------------------------------------------------------------------------- undo
# One stack per project, for the life of this backend. A record is what one call changed: the
# objects' local transforms before it (live), and each saved entry before it (disk).
_UNDO: dict = {}
_UNDO_MAX = 20
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(project: str) -> threading.Lock:
    k = L.key_for(project)
    with _LOCKS_GUARD:
        if k not in _LOCKS:
            _LOCKS[k] = threading.Lock()
        return _LOCKS[k]


def _push_undo(project: str, rec: dict) -> None:
    st = _UNDO.setdefault(L.key_for(project), [])
    st.append(rec)
    del st[:-_UNDO_MAX]


def undo_depth(project: str) -> int:
    return len(_UNDO.get(L.key_for(project)) or [])


# ---------------------------------------------------------------------------------------- code hints
def _hints(project: str, names: list) -> Optional[dict]:
    """Package D's `code_hints.hints_for`, when it is installed; None when it is not."""
    try:
        from .code_hints import hints_for
    except Exception:
        return None
    try:
        names = [n for n in dict.fromkeys(str(x) for x in names if x)][:200]
        return hints_for(project, names) if names else {}
    except Exception:
        return None


# ---------------------------------------------------------------------------------------- src -> file
def _src_file(project: str, url_path: str, cache: dict) -> str:
    """A model's URL path on the game's server, as a file in the project when one is there.

    rot-rush's workspace holds the game in `rot-rush/`, and its dev server serves that folder, so
    `/src/assets/brainrots/x.glb` is `rot-rush/src/assets/brainrots/x.glb` under the project."""
    if url_path in cache:
        return cache[url_path]
    rel = url_path.lstrip("/")
    got = url_path
    root = Path(project)
    try:
        if rel and not re.search(r"(^|/)\.\.(/|$)", rel):
            cands = [root / rel]
            try:
                cands += [d / rel for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith(".")][:60]
            except Exception:
                pass
            for c in cands:
                if c.is_file():
                    got = c.relative_to(root).as_posix()
                    break
    except Exception:
        pass
    cache[url_path] = got
    return got


# ---------------------------------------------------------------------------------------- endpoints
def _num3(v, name: str, allow_scalar: bool = False):
    if v is None:
        return None, ""
    if allow_scalar and isinstance(v, (int, float)) and not isinstance(v, bool):
        if not math.isfinite(float(v)):
            return None, "%s must be a finite number" % name
        return float(v), ""
    if isinstance(v, (list, tuple)) and len(v) == 3:
        try:
            out = [float(x) for x in v]
        except Exception:
            return None, "%s must be three numbers" % name
        if all(math.isfinite(x) for x in out):
            return out, ""
    return None, "%s must be %sthree numbers [x, y, z]" % (name, "a number or " if allow_scalar else "")


_SPEC_KEYS = {"target", "pos", "move", "rot", "rotate", "scale", "visible", "drop", "world"}
_ALIASES = {"position": "pos", "rotation": "rot", "name": "target"}


def _specs(body: dict) -> tuple[list, str, list]:
    """The edits asked for: `edits:[…]`, or the one edit in the body itself."""
    raw = body.get("edits") or []
    if not isinstance(raw, list):
        return [], "edits must be a list of edits", []
    if not raw:
        # A body model fills every field it declares; `drop: false` and `world: false` are its
        # defaults, not a request. `visible: false` IS one.
        # "" too: the body model fills `target` with "", and a filled target hid the `name` alias,
        # so {name: "pillar-2", move: [1,0,0]} answered "no target".
        one = {k: body.get(k) for k in list(_SPEC_KEYS) + list(_ALIASES)
               if body.get(k) is not None and body.get(k) != ""
               and not (k in ("drop", "world") and not body.get(k))}
        raw = [one] if one else []
    if not raw:
        return [], ("nothing to do: give a target and what to change (pos, move, rot, rotate, scale, "
                    "visible, drop), or edits:[…], undo:true, clear:[keys]|\"all\""), []
    specs, ignored = [], []
    for i, sp in enumerate(raw):
        if not isinstance(sp, dict):
            return [], "edit %d is not an object" % i, []
        sp = dict(sp)
        for a, real in _ALIASES.items():
            if a in sp and real not in sp:
                sp[real] = sp.pop(a)
        out = {"target": str(sp.get("target") or "").strip()}
        if not out["target"]:
            return [], "edit %d has no target (a key, a name or a path from /api/live/objects)" % i, []
        for f in ("pos", "move", "rot", "rotate"):
            v, err = _num3(sp.get(f), f)
            if err:
                return [], "edit %d: %s" % (i, err), []
            if v is not None:
                out[f] = v
        v, err = _num3(sp.get("scale"), "scale", allow_scalar=True)
        if err:
            return [], "edit %d: %s" % (i, err), []
        if v is not None:
            out["scale"] = v
        if isinstance(sp.get("visible"), bool):
            out["visible"] = sp["visible"]
        if sp.get("drop"):
            out["drop"] = True
        if sp.get("world"):
            out["world"] = True
        if len(out) == 1:
            return [], ("edit %d names %r but changes nothing — give pos, move, rot, rotate, scale, visible "
                        "or drop" % (i, out["target"])), []
        ignored += [k for k in sp if k not in _SPEC_KEYS and k not in _ALIASES]
        specs.append(out)
    return specs, "", sorted(set(ignored))


def objects(project: str, q: str = "", near=None, radius: Optional[float] = None, in_view: bool = False,
            pick=None, limit: int = 60, code: bool = False, scene: Optional[int] = None) -> dict:
    """The scene as rows: key, world bounds, what is in view, what is under a point of the view."""
    bad = _off() or L._guard(project)
    if bad:
        return bad
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad
    opts: dict = {"q": str(q or ""), "limit": max(1, min(500, int(limit or 60))), "in_view": bool(in_view)}
    if near not in (None, "", []):
        if isinstance(near, (list, tuple)):
            v, err = _num3(list(near), "near")
            if err:
                return {"ok": False, "error": err}
            opts["near"] = v
        else:
            parts = [p for p in str(near).split(",")]
            try:
                opts["near"] = [float(p) for p in parts] if len(parts) == 3 else str(near)
            except ValueError:
                opts["near"] = str(near)
    if radius is not None:
        try:
            opts["radius"] = float(radius)
        except (TypeError, ValueError):
            return {"ok": False, "error": "radius must be a number of metres"}
    if pick not in (None, "", []):
        try:
            xy = [float(p) for p in (pick if isinstance(pick, (list, tuple)) else str(pick).split(","))]
        except ValueError:
            xy = []
        if len(xy) != 2 or not all(-0.5 <= v <= 1.5 for v in xy):
            return {"ok": False, "error": "pick is x,y in 0..1 of the view (0,0 top left) — e.g. pick=0.5,0.5"}
        opts["pick"] = xy
    if scene is not None:
        opts["scene"] = int(scene)
    t0 = time.time()
    try:
        res = _in_tab(project, "__scene.objects(%s)" % json.dumps(opts))
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:600]}
    if not isinstance(res, dict):
        return {"ok": False, "error": "the page returned nothing"}
    if not res.get("ok"):
        return res if res.get("error") else _page_refusal(res)
    cache: dict = {}
    for row in res.get("rows") or []:
        if row.get("src") and str(row["src"]).startswith("/"):
            row["src"] = _src_file(project, str(row["src"]), cache)
    if code:
        hints = _hints(project, [r.get("name") for r in res.get("rows") or []])
        if hints is None:
            res["code_note"] = "code hints are not available in this Studio yet"
        else:
            for row in res.get("rows") or []:
                row["code"] = hints.get(row.get("name") or "", [])
    res["ms"] = int((time.time() - t0) * 1000)
    return res


def edit(project: str, body: dict) -> dict:
    """Move, turn, scale, hide or drop objects in the running game; save, undo, clear."""
    bad = _off() or L._guard(project)
    if bad:
        return bad
    body = dict(body or {})
    if body.get("undo"):
        return _undo(project)
    if body.get("clear") not in (None, "", [], False):
        return _clear(project, body.get("clear"))
    specs, err, ignored = _specs(body)
    if err:
        return {"ok": False, "error": err}
    known = _SPEC_KEYS | set(_ALIASES) | {"project", "save", "edits", "undo", "clear", "code", "scene", "look"}
    ignored = sorted(set(ignored) | {k for k in body if k not in known})
    # THE PICTURE OF THE CHANGE (live_view.py). The word is checked here, before a browser is
    # touched; left out, one edit gets a picture when the setting says so and a batch does not.
    from . import live_view
    look_angle, lerr = live_view.look_request(body.get("look"), batch=bool(body.get("edits")))
    if lerr:
        return {"ok": False, "error": lerr}
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad
    save = bool(body.get("save"))
    if save:
        _doc, derr = read_doc(project)
        if derr:
            return {"ok": False, "error": derr}
    opts: dict = {}
    if body.get("scene") is not None:
        opts["scene"] = int(body["scene"])
    if look_angle:
        opts["look"] = True
    look = None
    with _lock_for(project):
        try:
            res = _in_tab(project, "__scene.edit(%s, %s)" % (json.dumps(specs), json.dumps(opts)))
        except Exception as ex:
            return {"ok": False, "error": str(ex)[:600]}
        if not isinstance(res, dict):
            return {"ok": False, "error": "the page returned nothing"}
        if not res.get("ok"):
            return res if res.get("error") else _page_refusal(res)
        results = res.get("results") or []
        applied = [r for r in results if r.get("ok")]
        disk = None
        file_info: dict = {}
        if save and applied:
            doc, derr = read_doc(project)
            if derr:
                # The live change stands; nothing on disk was touched. Undo still puts the live one back.
                for r in applied:
                    r["saved"] = False
                file_info["error"] = derr
            else:
                created = doc is None
                doc = doc if doc is not None else new_doc()
                disk = []
                file_info["created"] = created
                for r in applied:
                    vals = dict(r.get("save") or {})
                    if not vals:
                        continue
                    pid = r.get("placed")
                    tf = {k: v for k, v in vals.items() if k in ("pos", "rot", "scale")}
                    if pid and tf:
                        # A placement's own transform lives in its entry of `placed`. One the file
                        # does not have yet (a try from /api/live/place) is saved whole.
                        found, prev = merge_placed_fields(doc, pid, tf)
                        if not found and r.get("placed_item"):
                            found, prev = True, merge_placed(doc, r["placed_item"])
                        if found:
                            disk.append({"section": "placed", "id": pid, "prev": prev})
                            r["saved_to"] = "placed[%s]" % pid
                            vals = {k: v for k, v in vals.items() if k not in tf}
                    if vals:
                        disk.append({"section": "parts", "key": r["key"], "prev": merge_part(doc, r["key"], vals)})
                    r["saved"] = True
                try:
                    file_info.update(write_doc(project, doc))
                except Exception as ex:
                    disk = None
                    file_info["error"] = "could not write %s: %s" % (edits_path(project), ex)
                    for r in applied:
                        r["saved"] = False
        if applied:
            _push_undo(project, {"kind": "edit", "at": time.time(),
                                 "what": ", ".join(r["key"] for r in applied)[:200],
                                 "live": [{"key": r["key"], "local": r["undo"]} for r in applied],
                                 "disk": disk, "created": bool(disk is not None and file_info.get("created")),
                                 "scene": opts.get("scene")})
            # Inside the lock: the look puts the old state back for one frame, and another edit of
            # this game must not land in that frame.
            if look_angle:
                try:
                    look = live_view.look_edit(project, e, applied, look_angle, opts.get("scene"))
                except Exception as ex:                  # noqa: BLE001
                    look = {"ok": False, "error": "the look failed: %s" % str(ex)[:300]}
    want_code = body.get("code")
    hints = _hints(project, [r.get("name") for r in applied]) if want_code is not False else None
    for r in results:
        r.setdefault("saved", False)
        r.pop("undo", None)
        r.pop("redo", None)
        r.pop("ground_before", None)
        r.pop("placed_item", None)
        written = r.pop("save", None)
        if r.get("saved") and written:
            r["written"] = {k: (_deg(v) if k == "rot" else v) for k, v in written.items()}
        if hints is not None and r.get("ok"):
            r["code"] = hints.get(r.get("name") or "", [])
    out: dict = {"ok": all(r.get("ok") for r in results), "engine": res.get("engine"), "units": "degrees"}
    if len(results) == 1 and not body.get("edits"):
        out.update(results[0])
        out["ok"] = bool(results[0].get("ok"))
    else:
        out["results"] = results
        out["failed"] = sum(1 for r in results if not r.get("ok"))
    if save:
        out["file"] = str(edits_path(project))
        out.update({k: v for k, v in file_info.items() if k in ("bak", "error")})
        if "written" in out and "rot" in (out.get("written") or {}):
            out["note"] = "rot is shown in degrees; the file stores radians"
    if ignored:
        out["ignored"] = ignored
    if applied:
        out["undo"] = "POST /api/live/edit {\"project\": …, \"undo\": true} puts this call back%s" % (
            ", live and on disk" if save else "")
    if look is not None:
        out["look"] = look
    return out


def _undo(project: str) -> dict:
    with _lock_for(project):
        st = _UNDO.get(L.key_for(project)) or []
        if not st:
            return {"ok": False, "error": "nothing to undo: no edit, place, remove or clear has been made on this "
                                          "project since this Studio started"}
        rec = st.pop()
        out: dict = {"ok": True, "undid": rec.get("kind"), "what": rec.get("what", ""), "left": len(st)}
        if rec.get("placed_live"):
            # Placements: each id is put back as the exact item it was (its own pos, rot and scale),
            # or taken out when there was nothing under that id before.
            e = L._entry(project)
            if not e.get("url"):
                out["placed"] = "no game is open for this project, so only the file was put back"
            else:
                try:
                    opts = {"scene": rec["scene"]} if rec.get("scene") is not None else {}
                    got = _in_tab(project, "__scene.sync(%s, %s, %s)" % (
                        json.dumps(list(reversed(rec["placed_live"]))), json.dumps(_rt(project)), json.dumps(opts))) or {}
                    if got.get("placed"):
                        out["placed_back"] = got["placed"]
                    if got.get("removed"):
                        out["unplaced"] = got["removed"]
                    if got.get("failed"):
                        out["placed_failed"] = got["failed"]
                    if not got.get("ok"):
                        out["placed"] = got.get("error") or "the page could not be reached"
                except Exception as ex:
                    out["placed"] = "the running game could not be put back: %s" % str(ex)[:300]
        if rec.get("live"):
            e = L._entry(project)
            if not e.get("url"):
                out["live"] = "no game is open for this project, so only the file was put back"
            else:
                try:
                    items = list(reversed(rec["live"]))
                    opts = {"scene": rec["scene"]} if rec.get("scene") is not None else {}
                    got = _in_tab(project, "__scene.restore(%s, %s)" % (json.dumps(items), json.dumps(opts))) or {}
                    out["restored"] = got.get("restored", [])
                    if got.get("missing"):
                        out["missing"] = got["missing"]
                    if not got.get("ok"):
                        out["live"] = got.get("error") or "the page could not be reached"
                except Exception as ex:
                    out["live"] = "the running game could not be put back: %s" % str(ex)[:300]
        if rec.get("disk"):
            doc, derr = read_doc(project)
            if derr:
                out["file_error"] = derr
            else:
                doc = doc if doc is not None else new_doc()
                out["file_restored"] = restore_disk(doc, rec["disk"])
                try:
                    # The call being undone MADE the file, and nothing else has been put in it
                    # since: the state before it is no file at all.
                    if rec.get("created") and is_empty_doc(doc):
                        remove_doc(project)
                        out["file_removed"] = str(edits_path(project))
                    else:
                        write_doc(project, doc)
                        out["file"] = str(edits_path(project))
                except Exception as ex:
                    out["file_error"] = "could not write %s: %s" % (edits_path(project), ex)
        return out


def _clear(project: str, what) -> dict:
    """Take saved entries out of the file: parts by key, placements by id or name.

    A cleared PART is left as it is in the running game until it reloads: the value the game's
    code gives it is not known here. A cleared PLACEMENT is taken out of the running game at once:
    it exists only because of its entry, so there is no question what the game looks like without
    it, and a file and a scene that disagree until a reload are two answers to one question."""
    with _lock_for(project):
        doc, derr = read_doc(project)
        p = edits_path(project)
        if derr:
            return {"ok": False, "error": derr}
        if doc is None:
            return {"ok": True, "cleared": [], "file": str(p), "exists": False,
                    "note": "there is no studio.edits.json here — nothing to clear"}
        parts = doc.get("parts") or {}
        if what == "all":
            part_keys = list(parts.keys())
            place_ids = [str(it.get("id")) for it in (doc.get("placed") or [])
                         if isinstance(it, dict) and it.get("id")]
            names = []
        elif isinstance(what, (str, list)):
            names = [what] if isinstance(what, str) else [str(k) for k in what]
            part_keys, place_ids = [], []
        else:
            return {"ok": False, "error": "clear takes a list of keys and placement ids, or \"all\""}
        missing = []
        for n in names:
            if n in parts:
                part_keys.append(n)
            else:
                it = placed_by(doc, n)
                if it is not None:
                    place_ids.append(str(it.get("id")))
                else:
                    missing.append(n)
        disk, cleared, gone = [], [], []
        for k in dict.fromkeys(part_keys):
            disk.append({"section": "parts", "key": k, "prev": copy.deepcopy(parts[k])})
            del parts[k]
            cleared.append(k)
        for pid in dict.fromkeys(place_ids):
            prev = take_placed(doc, pid)
            if prev is not None:
                disk.append({"section": "placed", "id": pid, "prev": prev})
                gone.append(prev)
                cleared.append(pid)
        out = {"ok": True, "cleared": cleared, "file": str(p)}
        if missing:
            out["not_saved"] = missing
        live = []
        if cleared:
            doc["parts"] = parts
            try:
                if is_empty_doc(doc):
                    # Nothing left to apply: no file, like an asset nobody has edited. The .bak and
                    # undo both still have what was in it.
                    info = remove_doc(project)
                    out["file_removed"] = True
                else:
                    info = write_doc(project, doc)
            except Exception as ex:
                return {"ok": False, "error": "could not write %s: %s" % (p, ex)}
            if info.get("bak"):
                out["bak"] = info["bak"]
            if gone:
                e = L._entry(project)
                if not e.get("url"):
                    live.append("placements: no game is open, so they are simply gone from its next load")
                else:
                    try:
                        got = _in_tab(project, "__scene.unplace(%s, %s, {})" % (
                            json.dumps([it.get("id") for it in gone]), json.dumps(_rt(project)))) or {}
                        if got.get("ok"):
                            out["removed_live"] = [k for k, n in (got.get("removed") or {}).items() if n]
                            live.append("placements: taken out of the running game as well")
                        else:
                            live.append("placements: still in the running game (%s) — they go at its next reload"
                                        % (got.get("error") or "the page could not be reached"))
                    except Exception as ex:
                        live.append("placements: still in the running game (%s) — they go at its next reload"
                                    % str(ex)[:200])
            _push_undo(project, {"kind": "clear", "at": time.time(), "what": ", ".join(cleared)[:200],
                                 "live": [], "disk": disk,
                                 "placed_live": [{"id": it.get("id"), "put": it} for it in gone]})
            out["undo"] = "POST /api/live/edit {\"project\": …, \"undo\": true} puts the cleared entries back"
        s = summarise(doc)["counts"]
        kept = {k: v for k, v in s.items() if v and k != "parts"}
        if kept:
            out["kept"] = kept
        if len(gone) < len(cleared):                   # some of what was cleared were parts
            live.insert(0, "parts: unchanged — the running game keeps the old values until it reloads "
                           "(POST /api/live/open {\"reload\": true})")
        out["live"] = "; ".join(live) or "unchanged"
        return out


def edits(project: str) -> dict:
    """The saved edits file, summarised: what is moved, hidden and placed, rotations in degrees."""
    bad = _off() or L._guard(project)
    if bad:
        return bad
    p = edits_path(project)
    doc, derr = read_doc(project)
    if derr:
        return {"ok": False, "path": str(p), "exists": True, "error": derr}
    bak = p.with_name(EDITS_NAME + ".bak")
    if doc is None:
        return {"ok": True, "path": str(p), "exists": False, "parts": {}, "placed": [],
                "counts": summarise({})["counts"], "units": "degrees", "undo": undo_depth(project)}
    out = {"ok": True, "path": str(p), "exists": True, **summarise(doc), "units": "degrees",
           "note": "rot is shown in degrees; the file stores radians",
           "updated": doc.get("updated"), "bytes": p.stat().st_size, "undo": undo_depth(project)}
    if bak.is_file():
        out["bak"] = str(bak)
    return out


def edits_file(project: str) -> tuple[int, dict]:
    """The document itself, for the page to apply — `<project>/studio.edits.json` and nothing else.

    Why a route of its own: the shim was handed `/api/workspace/file?path=…`, which serves only the
    folders open in the Studio. The proof game on the Desktop is not one, and that route answered
    403 "path is outside any open project/folder", so a saved edit never came back after a reload.
    This serves one fixed file name inside the folder asked for, so it needs no such list."""
    if not project or not Path(project).is_dir():
        return 404, {"ok": False, "error": "no such folder: %s" % project}
    doc, derr = read_doc(project)
    if derr:
        return 422, {"ok": False, "error": derr}
    if doc is None:
        return 404, {"ok": False, "error": "no %s in %s" % (EDITS_NAME, project)}
    return 200, doc


# ---------------------------------------------------------------------------------------- place (build 3)
# "Put another crystal beside pillar-1, and keep it." The object is MADE in the page by package
# B's runtime (`placeStudioItem`, the same function that re-makes it from the file after every
# reload, in the Studio and in a shipped game), STOOD where it was asked to go by the page script
# above, and WRITTEN here as a PlacedItem in `placed` — the Edit tab's own record of "a thing the
# code did not make". Undo shares the edit stack: one stack per project, whatever the call was.

_SHAPES = ("box", "sphere", "cylinder", "cone", "plane", "torus", "pointLight", "spotLight", "dirLight")
_PLACE_KEYS = {"project", "asset", "name", "id", "at", "near", "pos", "rot", "scale", "drop", "gap", "wait",
               "save", "remove", "undo", "code", "scene", "look"}
_ASSET_FORMS = ('{"builder": "src/assets.js#buildCrystal", "args": [{…}]}, {"model": "<project-relative .glb>"}, '
                '{"clone": "<key>"}, {"primitive": "box", "color": "#ff8800"} or '
                '{"id": "<asset id from /api/engine/assets>"}')
_BUILD_PREFIX = re.compile(r"^(?:build|make|create|spawn|new|gen|generate)(?=[A-Z0-9_])")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _studio_base() -> str:
    """This Studio's own origin: where the page imports the runtime and loads models from.

    Not live._self_base(), which reads the port from settings: a second backend shares the first
    one's settings, so on :8791 that named :8777 — another Studio, serving whatever it was built
    with. This process serves on ASSET_STUDIO_PORT when that is set, which main.py reads the same way."""
    h = str(settings.get("host") or "127.0.0.1")
    if h in ("0.0.0.0", "::"):
        h = "127.0.0.1"
    port = os.environ.get("ASSET_STUDIO_PORT") or settings.get("port") or 8777
    return "http://%s:%s" % (h, port)


def _rt(project: str) -> dict:
    """What the page needs to reach the runtime and to load a model through this Studio."""
    base = _studio_base()
    return {"runtime": base + "/studio-runtime.js", "studio": base, "project": str(Path(project))}


def _runtime_missing() -> str:
    """A refusal when this Studio has no runtime to place with (package B's build output)."""
    try:
        from .config import FRONTEND_DIST
        if not (Path(FRONTEND_DIST) / "studio-runtime.js").is_file():
            return ("this Studio has no studio-runtime.js to place with — `npm run build:runtime` in frontend/ "
                    "makes it")
    except Exception:
        pass
    return ""


def _new_id() -> str:
    """The editor's shape of id ("p" and seven base-36 characters), so the Edit tab reads it as one of its own."""
    import random
    return "p" + "".join(random.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(7))


def _project_file(project: str, rel: str) -> str:
    """A path an agent gave, as the project-relative path of a file that is there — or "".

    A workspace keeps its games in folders (`rot-rush/src/…`) and an agent often names a file from
    the game's own root (`src/…`), so the first-level folders are tried too. Models then go through
    /api/engine/model, which takes only a path from the project root."""
    root = Path(project)
    s = str(rel or "").strip().replace("\\", "/")
    if not s:
        return ""
    try:
        if Path(s).is_absolute():
            s = Path(s).resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return ""
    s = s.lstrip("/")
    if re.search(r"(^|/)\.\.(/|$)", s):
        return ""
    cands = [s]
    try:
        cands += [d.name + "/" + s for d in sorted(root.iterdir())
                  if d.is_dir() and not d.name.startswith(".") and d.name != "node_modules"][:60]
    except Exception:
        pass
    for c in cands:
        try:
            if (root / c).is_file():
                return c
        except Exception:
            continue
    return ""


def _builder_name(export: str, file: str = "") -> str:
    """buildCrystal -> crystal, makeTreeLarge -> tree-large: the thing a builder makes, not the verb."""
    n = _BUILD_PREFIX.sub("", str(export or ""))
    if not n or n == "default":
        n = Path(str(file or "")).stem
    n = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", n).replace("_", "-").replace(" ", "-").lower().strip("-")
    return n or "placed"


def _asset_rows(project: str) -> list:
    """Every asset /api/engine/assets knows for this project, from its cached scan."""
    from . import assets_index
    res = assets_index._cached(project)
    return list(res.get("items") or []) if res.get("ok") else []


def _args_of(asset: dict) -> list:
    args = asset.get("args", [])
    if args is None:
        return []
    # One options object is the common case, so {"args": {"height": 2}} means [{"height": 2}].
    return args if isinstance(args, list) else [args]


def asset_ref(project: str, asset) -> tuple[Optional[dict], str, str]:
    """`asset` as the runtime's PlacedItem.ref, with a name for the placement: (ref, name, error).

    A string is read by its shape — `file#export` a builder, `*.glb` a model, `code:…`/`model:…`
    an asset id, a shape name a primitive, anything else the key of something to clone — so the
    common cases need no object at all. Every file is checked here, before a browser is touched."""
    if isinstance(asset, str):
        s = asset.strip()
        if not s:
            return None, "", "asset is empty — give " + _ASSET_FORMS
        if re.match(r"^(code|spec|model|image|texture|audio):", s):
            asset = {"id": s}
        elif "#" in s:
            asset = {"builder": s}
        elif re.search(r"\.(glb|gltf)$", s, re.I):
            asset = {"model": s}
        elif s in _SHAPES:
            asset = {"primitive": s}
        else:
            asset = {"clone": s}
    if not isinstance(asset, dict) or not asset:
        return None, "", "asset must be one of " + _ASSET_FORMS
    forms = [k for k in ("builder", "model", "clone", "primitive", "id") if asset.get(k) not in (None, "")]
    if len(forms) != 1:
        return None, "", ("asset names %s — give exactly one of builder, model, clone, primitive or id"
                          % (" and ".join(forms) if forms else "none of them"))
    kind = forms[0]
    if kind == "builder":
        file, _, export = str(asset["builder"]).strip().partition("#")
        file, export = file.strip(), export.strip() or "default"
        if not re.search(r"\.(m?js|ts|tsx|jsx)$", file, re.I):
            return None, "", ('builder is "<code file>#<export>", e.g. "src/assets.js#buildCrystal" — %r names no '
                              'code file' % file)
        if not re.match(r"^[A-Za-z_$][\w$]*$", export):
            return None, "", "builder: %r is not the name of an export" % export
        rel = _project_file(project, file)
        if not rel:
            return None, "", "builder: there is no %s in %s" % (file, project)
        return {"kind": "code", "file": rel, "export": export, "args": _args_of(asset)}, _builder_name(export, rel), ""
    if kind == "model":
        rel = _project_file(project, str(asset["model"]))
        if not rel:
            return None, "", "model: there is no %s in %s" % (asset["model"], project)
        if not re.search(r"\.(glb|gltf)$", rel, re.I):
            return None, "", "model: %s is not a .glb or .gltf" % rel
        ref = {"kind": "model", "url": rel}
        for f in ("nodes", "parts"):
            v = asset.get(f)
            if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                ref[f] = list(v)
        return ref, Path(rel).stem, ""
    if kind == "clone":
        key = str(asset["clone"]).strip()
        # A key less the `.000` a repeated name is given; a JS path names nothing here, so the page
        # names the copy after the object the path reaches.
        name = "" if ("[" in key or key.startswith("__")) else re.sub(r"\.\d{3}$", "", key)
        return {"kind": "clone", "of": key}, name, ""
    if kind == "primitive":
        shape = str(asset["primitive"]).strip()
        if shape not in _SHAPES:
            return None, "", "primitive: %r is not one of %s" % (shape, ", ".join(_SHAPES))
        color = asset.get("color", "#9aa7b8")
        if isinstance(color, int) and not isinstance(color, bool) and 0 <= color <= 0xFFFFFF:
            color = "#%06x" % color
        if not isinstance(color, str) or not _HEX.match(color):
            return None, "", 'primitive: color is "#rrggbb" (or a number such as 0xff8800)'
        return {"kind": "primitive", "shape": shape, "color": color.lower()}, shape, ""
    # An asset id from the Library, turned into a ref by the palette's own rules (Editor.tsx).
    aid = str(asset["id"]).strip()
    try:
        rows = _asset_rows(project)
    except Exception as ex:
        return None, "", "id: the project's assets could not be listed: %s" % str(ex)[:200]
    row = next((r for r in rows if r.get("id") == aid), None)
    if not row:
        low = aid.lower().split(":", 1)[-1]
        like = [r.get("id") for r in rows if low and (low in str(r.get("id", "")).lower()
                                                      or low in str(r.get("name", "")).lower())][:8]
        return None, "", "id: no asset %r in /api/engine/assets for this project%s" % (
            aid, (" — like: " + ", ".join(like)) if like else "")
    t, name = row.get("type"), str(row.get("name") or "")
    if row.get("model"):
        ref = {"kind": "model", "url": str(row["model"])}
        for f in ("nodes", "parts"):
            if isinstance(row.get(f), list) and row[f]:
                ref[f] = list(row[f])
        return ref, name or Path(str(row["model"])).stem, ""
    if t == "code":
        exp = str(row.get("export") or name)
        return ({"kind": "code", "file": str(row.get("file")), "export": exp, "args": _args_of(asset)},
                _builder_name(exp, str(row.get("file") or "")), "")
    if t == "spec":
        return None, "", ('%s is one entry of the table %s in %s and names no model file; the runtime makes '
                          'exported builders, not table entries — place a builder: {"builder": "<file>#<function>", '
                          '"args": [...]}' % (aid, row.get("table"), row.get("file")))
    if t == "model":
        if row.get("meshes") == 0:
            return None, "", ("%s holds %s and no mesh, so there is nothing to place — place the model it belongs to"
                              % (row.get("file"), "an animation" if row.get("anims") else "nothing"))
        return {"kind": "model", "url": str(row.get("file"))}, name, ""
    if t in ("image", "texture"):
        return {"kind": "image", "url": str(row.get("file"))}, name, ""
    return None, "", "%s is %s: there is nothing to place" % (aid, "a sound" if t == "audio" else "a " + str(t))


def _place_spec(project: str, body: dict) -> tuple[Optional[dict], str]:
    """The page's placement request, or the reason there is none — before a browser is touched."""
    ref, name, err = asset_ref(project, body.get("asset"))
    if err:
        return None, err
    got = {}
    for f in ("at", "pos", "rot"):
        v, err = _num3(body.get(f), f)
        if err:
            return None, err
        got[f] = v
    sc, err = _num3(body.get("scale"), "scale", allow_scalar=True)
    if err:
        return None, err
    if isinstance(sc, float):
        sc = [sc, sc, sc]
    if sc and any(abs(x) < 1e-9 for x in sc):
        return None, "scale must not be 0 on any axis"
    near = body.get("near")
    if near in (None, ""):
        near = None
    elif not isinstance(near, str):
        return None, "near is a key, a name or a path from /api/live/objects; a point is at:[x,y,z]"
    if sum(1 for v in (got["at"], got["pos"], near) if v is not None) > 1:
        return None, ("give one of at (where the middle of its bottom goes), near (beside that object, on "
                      "the ground) or pos (its origin, as the file keeps it)")
    gap = body.get("gap")
    if gap is not None:
        try:
            gap = float(gap)
        except (TypeError, ValueError):
            return None, "gap is metres between it and the object it is near"
        if not math.isfinite(gap) or gap < 0:
            return None, "gap is metres, 0 or more"
    pid = str(body.get("id") or "").strip() or _new_id()
    if len(pid) > 80:
        return None, "id is at most 80 characters"
    item = {"id": pid, "name": str(body.get("name") or "").strip()[:120] or name, "ref": ref,
            "pos": [0.0, 0.0, 0.0],
            "rot": [math.radians(x) for x in got["rot"]] if got["rot"] else [0.0, 0.0, 0.0],
            "scale": sc or [1.0, 1.0, 1.0]}
    return {"item": item, "at": got["at"], "pos": got["pos"], "near": near, "drop": bool(body.get("drop")),
            "gap": gap, "wait": bool(body.get("wait")), "name_given": bool(str(body.get("name") or "").strip()),
            "rt": _rt(project)}, ""


def placed_by(doc: dict, what) -> Optional[dict]:
    """The entry of `placed` with this id, else the one with this name."""
    items = [it for it in (doc.get("placed") if isinstance(doc.get("placed"), list) else []) if isinstance(it, dict)]
    return next((it for it in items if it.get("id") == what), None) or \
        next((it for it in items if it.get("name") == what), None)


def merge_placed(doc: dict, item: dict) -> Optional[dict]:
    """Put a PlacedItem in `placed`, replacing any entry with its id where the first of them stood.
    Returns the entry it replaced, or None — what undo puts back."""
    items = doc.setdefault("placed", [])
    prev, at, keep = None, None, []
    for it in items:
        if isinstance(it, dict) and it.get("id") == item.get("id"):
            if prev is None:
                prev, at = copy.deepcopy(it), len(keep)
            continue
        keep.append(it)
    new = copy.deepcopy(item)
    if at is None:
        keep.append(new)
    else:
        keep.insert(at, new)
    items[:] = keep
    return prev


def take_placed(doc: dict, pid: str) -> Optional[dict]:
    """Take the entry with this id out of `placed`: the entry, or None when there was none."""
    items = doc.get("placed")
    if not isinstance(items, list):
        return None
    prev, keep = None, []
    for it in items:
        if isinstance(it, dict) and it.get("id") == pid:
            if prev is None:
                prev = copy.deepcopy(it)
            continue
        keep.append(it)
    items[:] = keep
    return prev


def merge_placed_fields(doc: dict, pid: str, values: dict) -> tuple[bool, Optional[dict]]:
    """A placement moved by an edit: its entry's pos/rot/scale (root-local, radians) replaced and
    every other field kept. (found, the entry as it was)."""
    items = doc.get("placed") if isinstance(doc.get("placed"), list) else []
    for i, it in enumerate(items):
        if isinstance(it, dict) and it.get("id") == pid:
            prev = copy.deepcopy(it)
            new = dict(it)
            for f in ("pos", "rot", "scale"):
                if f in values:
                    new[f] = [float(x) for x in values[f]]
            items[i] = new
            return True, prev
    return False, None


def _shown_item(item: dict) -> dict:
    """A PlacedItem as an agent reads it: rotations in degrees."""
    out = {k: v for k, v in item.items() if k not in ("pos", "rot", "scale")}
    out["pos"] = _r3(item.get("pos") or [0, 0, 0])
    out["rot"] = _deg(item.get("rot") or [0, 0, 0])
    out["scale"] = _r3(item.get("scale") or [1, 1, 1])
    return out


def _hint_names(ref: dict, name: str) -> list:
    """What to look up in the code for a placement: the builder, the model's name, the source."""
    k = (ref or {}).get("kind")
    if k == "code":
        return [str(ref.get("export") or "")]
    if k == "model":
        return [Path(str(ref.get("url") or "")).stem]
    if k == "clone":
        return [re.sub(r"\.\d{3}$", "", str(ref.get("of") or ""))]
    return []                            # a primitive or a picture: no code made it


def place(project: str, body: dict) -> dict:
    """Put one of the game's own things into the running game, where it was asked to stand."""
    bad = _off() or L._guard(project)
    if bad:
        return bad
    body = dict(body or {})
    if body.get("undo"):
        return _undo(project)
    if body.get("remove") not in (None, "", False):
        return _remove(project, body)
    if body.get("asset") in (None, "", {}, []):
        return {"ok": False, "error": ("nothing to place: give asset — %s. Or remove:<id|key> takes a placement "
                                       "out, undo:true takes the last call back" % _ASSET_FORMS)}
    spec, err = _place_spec(project, body)
    if err:
        return {"ok": False, "error": err}
    from . import live_view
    look_angle, lerr = live_view.look_request(body.get("look"))
    if lerr:
        return {"ok": False, "error": lerr}
    miss = _runtime_missing()
    if miss:
        return {"ok": False, "error": miss}
    ignored = sorted(k for k in body if k not in _PLACE_KEYS)
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad
    save = bool(body.get("save"))
    if save:
        _doc, derr = read_doc(project)
        if derr:
            return {"ok": False, "error": derr}
    opts: dict = {}
    if body.get("scene") is not None:
        opts["scene"] = int(body["scene"])
    t0 = time.time()
    look = None
    with _lock_for(project):
        try:
            res = _in_tab(project, "__scene.place(%s, %s)" % (json.dumps(spec), json.dumps(opts)))
        except Exception as ex:
            return {"ok": False, "error": str(ex)[:600]}
        if not isinstance(res, dict):
            return {"ok": False, "error": "the page returned nothing"}
        if not res.get("ok"):
            return res if res.get("error") else _page_refusal(res)
        item = res.get("item") or spec["item"]
        pid = str(item.get("id") or spec["item"]["id"])
        doc, derr = read_doc(project)
        # What undo puts back under this id: what the page itself placed there, else what the file
        # had (the shim applied it at load), else nothing — undo then takes the id out.
        prev_live = res.get("prev")
        if prev_live is None and res.get("replaced") and doc:
            got_prev = placed_by(doc, pid)
            prev_live = copy.deepcopy(got_prev) if got_prev and got_prev.get("id") == pid else None
        disk, file_info, saved = None, {}, False
        if save:
            if derr:
                file_info["error"] = derr
            else:
                created = doc is None
                doc = doc if doc is not None else new_doc()
                disk = [{"section": "placed", "id": pid, "prev": merge_placed(doc, item)}]
                file_info["created"] = created
                try:
                    file_info.update(write_doc(project, doc))
                    saved = True
                except Exception as ex:
                    disk = None
                    file_info["error"] = "could not write %s: %s" % (edits_path(project), ex)
        _push_undo(project, {"kind": "place", "at": time.time(), "what": "%s (placed)" % item.get("name"),
                             "live": [], "placed_live": [{"id": pid, "put": prev_live}],
                             "disk": disk, "created": bool(disk is not None and file_info.get("created")),
                             "scene": opts.get("scene")})
        # The picture: the new thing, and the same view with it hidden. Inside the lock, as an edit's.
        if look_angle:
            if res.get("pending") or not res.get("key"):
                look = {"ok": False, "error": "not built yet, so there is nothing to photograph — it goes in when "
                                              "its source appears"}
            else:
                try:
                    look = live_view.look_place(project, e, res, look_angle, opts.get("scene"))
                except Exception as ex:                  # noqa: BLE001
                    look = {"ok": False, "error": "the look failed: %s" % str(ex)[:300]}
    out: dict = {"ok": True, "engine": res.get("engine"), "units": "degrees", "key": res.get("key"),
                 "name": res.get("name") or item.get("name"), "id": pid, "how": res.get("how"),
                 "where": res.get("where")}
    if res.get("pending"):
        out["pending"] = True
    for k in ("after", "ground", "overlaps"):
        if k in res:
            out[k] = res[k]
    out["saved"] = saved
    if res.get("replaced"):
        out["replaced"] = "a placement with id %s was already in the game; this one took its place" % pid
    if res.get("renumbered"):
        out["renumbered"] = res["renumbered"]
        out["renumbered_note"] = (
            "%d key(s) of objects already in the scene changed while this is in it: a name inside it repeats one "
            "of theirs. The runtime applies saved edits under either key (parts before placements, then a retry); "
            "the Edit tab only under the old one." % int(res.get("renumbered_n") or len(res["renumbered"])))
    notes = list(res.get("notes") or [])
    if saved and res.get("engine") == "playcanvas" and (item.get("ref") or {}).get("kind") == "model" \
            and str(res.get("how") or "").startswith("a copy of the one the game draws"):
        # The runtime prefers a copy of one the game has drawn, and loads the raw file when there
        # is none: rot-rush draws its brainrots only after the title card, and its files are
        # unpainted and in centimetres (package B measured 386 m tall).
        notes.append("saved as the model file: after a reload it is copied from the game's own again only if "
                     "the game has drawn one by the time the saved edits apply; otherwise the raw file loads")
    if notes:
        out["notes"] = notes
    if save:
        out["file"] = str(edits_path(project))
        if saved:
            out["written"] = _shown_item(item)
        out.update({k: v for k, v in file_info.items() if k in ("bak", "error")})
    else:
        out["try"] = ("nothing was written: the next reload takes it away. save:true keeps it (or an edit of it "
                      "with save:true)")
    if ignored:
        out["ignored"] = ignored
    out["undo"] = ("POST /api/live/place {\"project\": …, \"undo\": true} (the edit stack) takes it back out%s"
                   % (", live and on disk" if saved else ""))
    if body.get("code") is not False:
        names = [n for n in _hint_names(item.get("ref") or {}, item.get("name") or "") if n]
        hints = _hints(project, names) if names else None
        if hints is not None:
            out["code"] = [h for n in names for h in hints.get(n, [])][:6]
    if look is not None:
        out["look"] = look
    out["ms"] = int((time.time() - t0) * 1000)
    return out


def _remove(project: str, body: dict) -> dict:
    """Take a placement out of the running game — by id, key or name; with save, out of the file too.
    What the game's own code built is refused: it can only be hidden."""
    what = str(body.get("remove")).strip()
    miss = _runtime_missing()
    if miss:
        return {"ok": False, "error": miss}
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad
    save = bool(body.get("save"))
    if save:
        _doc, derr = read_doc(project)
        if derr:
            return {"ok": False, "error": derr}
    opts: dict = {}
    if body.get("scene") is not None:
        opts["scene"] = int(body["scene"])
    with _lock_for(project):
        try:
            res = _in_tab(project, "__scene.remove(%s, %s, %s)" % (json.dumps(what), json.dumps(_rt(project)),
                                                                    json.dumps(opts)))
        except Exception as ex:
            return {"ok": False, "error": str(ex)[:600]}
        if not isinstance(res, dict):
            return {"ok": False, "error": "the page returned nothing"}
        if not res.get("ok"):
            return res if res.get("error") else _page_refusal(res)
        pid = str(res.get("id") or "")
        item = res.get("item")
        doc, derr = read_doc(project)
        in_file = placed_by(doc, pid) if doc else None
        in_file = in_file if in_file and in_file.get("id") == pid else None
        if item is None and in_file:
            item = copy.deepcopy(in_file)
        disk, file_info = None, {}
        if save and in_file and not derr:
            disk = [{"section": "placed", "id": pid, "prev": take_placed(doc, pid)}]
            try:
                if is_empty_doc(doc):
                    file_info.update(remove_doc(project))
                    file_info["file_removed"] = True
                else:
                    file_info.update(write_doc(project, doc))
            except Exception as ex:
                disk = None
                file_info["error"] = "could not write %s: %s" % (edits_path(project), ex)
        if item or disk:
            _push_undo(project, {"kind": "remove", "at": time.time(), "what": "%s (removed)" % (res.get("name") or pid),
                                 "live": [], "placed_live": [{"id": pid, "put": item}] if item else [],
                                 "disk": disk, "scene": opts.get("scene")})
    out: dict = {"ok": True, "engine": res.get("engine"), "id": pid, "key": res.get("key"), "name": res.get("name"),
                 "removed": res.get("removed"), "saved": bool(disk)}
    if save:
        out["file"] = str(edits_path(project))
        out.update({k: v for k, v in file_info.items() if k in ("bak", "error", "file_removed")})
        if not in_file:
            out["note"] = "it was not in studio.edits.json, so the file is unchanged"
    elif in_file:
        out["note"] = ("it is still in studio.edits.json, so the next reload puts it back — save:true, or "
                       "/api/live/edit {\"clear\": [id]}, takes it out of the file")
    if item or disk:
        out["undo"] = "POST /api/live/place {\"project\": …, \"undo\": true} puts it back"
    else:
        out["undo"] = "nothing can put it back: neither this page nor the file knew what it was made from"
    return out

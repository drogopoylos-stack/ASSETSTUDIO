# -*- coding: utf-8 -*-
"""Go to a place in the RUNNING game, and look at it.

The asks this answers, in the order an agent meets them:

  "Go where the user's screenshot was taken."   -> locate(image, hint)
  "Stand at the treadmills and look at them."   -> goto(name="treadmill")
  "What is around me, and where on screen?"     -> where()
  "Turn left a bit, step closer."               -> goto(orbit=[-30, 0]) / goto(step=[0, 0, 2])
  "I changed the code. Same view, what moved?"  -> goto(pose="last")  -> changed_pct

THREE THINGS HAD TO BE TRUE FIRST, and each was measured on ROT RUSH (PlayCanvas 2.21, Vite).

  1. REACH THE APP WITH NO GAME CHANGE. The shim's hunt walks page globals; the bridge queries the
     heap by a prototype the page exposes. A bundled game gives neither — `reachable=False`. What
     every game does give is its frame callback: the function it hands to requestAnimationFrame.
     PlayCanvas's tick and a three.js animate loop both close over the engine, and the debugger
     can read a closure (`Runtime.getProperties` → `[[Scopes]]`). On ROT RUSH the callback's
     Module scope holds `pc`, and `pc.app` is the app. Measured: two callbacks, one hit, under a
     second. DOM listeners were tried first and found nothing here — arrows over a device, not
     the app — so they are the second source, not the first.

  2. A CAMERA THE STUDIO OWNS. Moving the game's camera does not work: a follow-rig writes it back
     every frame. So a camera of ours is made from the game's (same fov, clip, layers, clear), the
     game's camera component is switched off while ours is on, and switched back by `release`.
     For three the renderer's `render(scene, camera)` is wrapped and the camera argument swapped —
     the game's rig can move its own camera all it likes.

  3. FROM A PICTURE TO A POSE. Candidate views are rendered through that camera and scored against
     the screenshot: named things first (a hint word wins), then a coarse sweep of the scene bounds,
     then a refinement around the best. The score is a similarity of small grayscale and colour
     thumbnails, reported as a number so a guess reads as a guess.

Everything here is gated by `cc_navigate` and by the live link's own switch.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import time
from pathlib import Path
from typing import Optional

from . import live as L
from .config import settings

# ---------------------------------------------------------------------------------------- the page
# Installed on demand into the live tab as `window.__nav`, beside `__live`. Separate on purpose: a
# tab opened before this file existed still has the old shim, and this has to work there too.
NAV = r"""
(() => {
if (window.__nav) return;
var L = window.__live || null;
var N = { rafs: [], kind: '', app: null, game: null, ours: null, on: false, target: null,
          three: { renderer: null, scene: null, camera: null } };
try { Object.defineProperty(window, '__nav', { value: N, configurable: true, writable: true }); }
catch (e) { window.__nav = N; }
var r3 = function (n) { return typeof n === 'number' ? Math.round(n * 1000) / 1000 : n; };
var DEG = Math.PI / 180;

/* 1. THE FRAME CALLBACKS. The game re-registers its tick every frame, so a wrap installed late
   still sees it on the next one. The debugger reads their closures from the Python side. */
try {
  var orig = window.requestAnimationFrame;
  window.requestAnimationFrame = function (cb) {
    try {
      if (typeof cb === 'function' && N.rafs.indexOf(cb) < 0) {
        if (N.rafs.length >= 8) N.rafs.shift();
        N.rafs.push(cb);
      }
    } catch (e) {}
    return orig.apply(this, arguments);
  };
} catch (e) {}

var isPc = function (a) {
  try { return !!(a && a.root && a.root.children && a.scene && a.graphicsDevice && typeof a.start === 'function'); }
  catch (e) { return false; }
};
N.pinPc = function (app) { try { if (L && L.pin) L.pin(app, 'playcanvas'); } catch (e) {} N.app = app; N.kind = 'playcanvas'; return 'playcanvas'; };
N.pinThreeScene = function (s) {
  try { if (L && L.pin) L.pin(s, 'three'); } catch (e) {}
  N.three.scene = s; N.kind = 'three';
  /* A game's camera need not be in its scene, and a renderer nobody found is never wrapped: the proof
     game answered goto with "the game has no camera yet". three hands the renderer AND the camera to
     scene.onBeforeRender on every render, so a pinned scene is enough, within one frame. Only a
     render to the canvas names the camera — a minimap or a post pass renders into a target. */
  try {
    if (!s.__navSceneHook) {
      var prev = s.onBeforeRender;
      s.onBeforeRender = function (renderer, scene, camera, target) {
        try {
          if (renderer && typeof renderer.render === 'function' && N.three.renderer !== renderer) N.pinThreeRenderer(renderer);
          if (!target && camera && camera.isCamera && camera !== N.ours) N.three.camera = camera;
        } catch (e) {}
        if (typeof prev === 'function') return prev.apply(this, arguments);
      };
      Object.defineProperty(s, '__navSceneHook', { value: true, configurable: true });
    }
  } catch (e) {}
  return 'three';
};
N.pinThreeRenderer = function (r) {
  N.three.renderer = r;
  if (!r.__navWrapped) {
    r.__navWrapped = true;
    var render = r.render;
    /* The scene and the camera arrive here every frame, which is how a renderer-only find turns
       into a scene within one frame — and how our camera replaces the game's without touching it. */
    r.render = function (scene, camera) {
      try {
        if (scene && scene.isScene && N.three.scene !== scene) N.pinThreeScene(scene);
        if (camera && camera.isCamera && camera !== N.ours) N.three.camera = camera;
        if (N.on && N.ours && N.kind === 'three' && camera !== N.ours) arguments[1] = N.ours;
        if (N.revealed.length) N.hold();
      } catch (e) {}
      return render.apply(this, arguments);
    };
  }
  return 'three-renderer';
};
/* Does this object, or one of its direct properties, quack like an engine? One level down is the
   level that matters: a namespace (`pc.app`), a game object (`game.app`), a rig (`this.renderer`). */
N.pinAny = function (o) {
  try {
    if (!o || (typeof o !== 'object' && typeof o !== 'function')) return '';
    if (isPc(o)) return N.pinPc(o);
    if (o.isScene) return N.pinThreeScene(o);
    if (o.isWebGLRenderer) return N.pinThreeRenderer(o);
    if (o.isCamera && !N.three.camera) N.three.camera = o;
    /* The names a handle usually hangs on, first; then EVERY key. No cap: the thing found on
       ROT RUSH was `pc.app`, and the PlayCanvas namespace has over a thousand exports with the
       capitalised classes sorted ahead of it — a scan of the first few hundred never got there. */
    var pref = ['app', '_app', 'application', 'game', 'engine', 'renderer', 'scene', 'world', 'instance'];
    for (var p = 0; p < pref.length; p++) {
      var pv; try { pv = o[pref[p]]; } catch (e) { continue; }
      if (!pv || typeof pv !== 'object') continue;
      if (isPc(pv)) return N.pinPc(pv);
      if (pv.isScene) return N.pinThreeScene(pv);
      if (pv.isWebGLRenderer) return N.pinThreeRenderer(pv);
    }
    var keys; try { keys = Object.keys(o); } catch (e) { return ''; }
    for (var i = 0; i < keys.length && i < 6000; i++) {
      var v; try { v = o[keys[i]]; } catch (e) { continue; }
      if (!v || typeof v !== 'object') continue;
      if (isPc(v)) return N.pinPc(v);
      if (v.isScene) return N.pinThreeScene(v);
      if (v.isWebGLRenderer) return N.pinThreeRenderer(v);
    }
  } catch (e) {}
  return '';
};

/* ------------------------------------------------------------------ PlayCanvas */
var pcApp = function () {
  if (N.app && isPc(N.app)) return N.app;
  try { if (L && L.pinned && L.pinnedKind === 'playcanvas' && isPc(L.pinned)) { N.app = L.pinned; return N.app; } } catch (e) {}
  try { if (window.pc && window.pc.app && isPc(window.pc.app)) { N.app = window.pc.app; return N.app; } } catch (e) {}
  try { if (window.__game && window.__game.app && isPc(window.__game.app)) { N.app = window.__game.app; return N.app; } } catch (e) {}
  return null;
};
var pcGameCam = function (app) {
  if (N.game && N.game.camera) return N.game;
  var cams = (app.systems && app.systems.camera && app.systems.camera.cameras) || [];
  var best = null;
  for (var i = 0; i < cams.length; i++) {
    var c = cams[i];
    if (!c || !c.entity || c.entity.name === '__studio_cam') continue;
    if (!best || (c.priority || 0) < (best.priority || 0)) best = c;
  }
  N.game = best ? best.entity : null;
  return N.game;
};
var pcEnsure = function (app) {
  if (N.ours && N.ours.parent && N.kind === 'playcanvas') return N.ours;
  var game = pcGameCam(app);
  var E = app.root.constructor;
  var ent = new E('__studio_cam', app);
  var src = game && game.camera;
  var opts = {};
  ['fov', 'nearClip', 'farClip', 'projection', 'orthoHeight', 'aspectRatioMode', 'aspectRatio',
   'frustumCulling', 'clearColorBuffer', 'clearDepthBuffer', 'clearStencilBuffer', 'toneMapping',
   'gammaCorrection', 'horizontalFov'].forEach(function (k) {
    try { if (src && src[k] !== undefined && src[k] !== null) opts[k] = src[k]; } catch (e) {}
  });
  try { if (src && src.clearColor) opts.clearColor = src.clearColor.clone(); } catch (e) {}
  try { if (src && src.layers) opts.layers = src.layers.slice(); } catch (e) {}
  try { if (src && src.rect) opts.rect = src.rect.clone(); } catch (e) {}
  opts.priority = (src ? (src.priority || 0) : 0) - 1;
  try { ent.addComponent('camera', opts); }
  catch (e) {
    /* A camera option this PlayCanvas does not know throws. The four that matter never do. */
    var few = { priority: opts.priority };
    ['fov', 'nearClip', 'farClip', 'clearColor', 'layers'].forEach(function (k) { if (opts[k] !== undefined) few[k] = opts[k]; });
    ent.addComponent('camera', few);
  }
  ent.camera.enabled = false;
  app.root.addChild(ent);
  if (game) { var p = game.getPosition(); ent.setPosition(p.x, p.y, p.z); ent.setRotation(game.getRotation()); }
  N.ours = ent;
  N.kind = 'playcanvas';
  return ent;
};
var pcOn = function (app) {
  var ent = pcEnsure(app);
  if (N.on) return ent;
  if (N.game && N.game.camera) N.game.camera.enabled = false;
  ent.camera.enabled = true;
  N.on = true;
  return ent;
};
var pcOff = function () {
  if (!N.on) return;
  try { if (N.ours && N.ours.camera) N.ours.camera.enabled = false; } catch (e) {}
  try { if (N.game && N.game.camera) N.game.camera.enabled = true; } catch (e) {}
  N.on = false;
};
/* YAW AND PITCH COME FROM WHERE THE CAMERA LOOKS, never from an Euler read-back. PlayCanvas
   reports a camera turned 180 degrees as pitch 160 with a hidden roll; restoring that as angles
   gave a different view and "63.8% changed" on an unchanged scene. A forward vector has one
   answer, and setting orientation by look-at (up = world Y) reproduces it exactly. */
var anglesOf = function (f) {
  var y = Math.max(-1, Math.min(1, f[1]));
  return { yaw: r3(Math.atan2(-f[0], -f[2]) / DEG), pitch: r3(Math.asin(y) / DEG) };
};
var pcPoseOf = function (ent) {
  var p = ent.getPosition(), f = ent.forward;
  var a = anglesOf([f.x, f.y, f.z]);
  return { pos: [r3(p.x), r3(p.y), r3(p.z)], yaw: a.yaw, pitch: a.pitch, fov: ent.camera ? r3(ent.camera.fov) : null };
};
var pcSet = function (ent, o) {
  if (o.pos) ent.setPosition(o.pos[0], o.pos[1], o.pos[2]);
  if (o.look_at) ent.lookAt(o.look_at[0], o.look_at[1], o.look_at[2]);
  else if (o.yaw != null || o.pitch != null) {
    var cur = pcPoseOf(ent);
    var f = forward(o.yaw != null ? o.yaw : cur.yaw, o.pitch != null ? o.pitch : cur.pitch);
    var p = ent.getPosition();
    ent.lookAt(p.x + f[0], p.y + f[1], p.z + f[2]);
  }
  if (o.fov != null && ent.camera) ent.camera.fov = o.fov;
};
var pcPath = function (e, app) {
  var parts = [];
  var cur = e;
  while (cur && cur !== app.root && cur.parent) {
    parts.unshift('.children[' + cur.parent.children.indexOf(cur) + ']');
    cur = cur.parent;
  }
  var at = '__live.pinned';
  try { if (L && L.reach) at = L.reach().at || at; } catch (e2) {}
  return at + '.root' + parts.join('');
};
/* A "thing" is anything with a name and a place: an entity with meshes, or a named GROUP over the
   union of what is under it. Without the second kind `platform-8` — a group of belts, signs and
   props — had no row, and "go to platform 8" had nothing to go to. */
var pcThings = function (app, ent, limit) {
  var cam = ent && ent.camera, cp = ent ? ent.getPosition() : null;
  var V = function () { return app.root.getPosition().clone(); };
  var W = app.graphicsDevice.width || 1, H = app.graphicsDevice.height || 1;
  var own = new Map(), order = [];
  var walk = function (e) {
    order.push(e);
    try {
      var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
      if (mi.length) {
        var mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
        for (var i = 0; i < mi.length; i++) {
          var a = mi[i].aabb; if (!a) continue;
          var c = a.center, h = a.halfExtents;
          mn[0] = Math.min(mn[0], c.x - h.x); mn[1] = Math.min(mn[1], c.y - h.y); mn[2] = Math.min(mn[2], c.z - h.z);
          mx[0] = Math.max(mx[0], c.x + h.x); mx[1] = Math.max(mx[1], c.y + h.y); mx[2] = Math.max(mx[2], c.z + h.z);
        }
        if (isFinite(mn[0])) own.set(e, { mn: mn, mx: mx, meshes: mi.length });
      }
    } catch (e0) {}
    var k = e.children || [];
    for (var j = 0; j < k.length; j++) walk(k[j]);
  };
  walk(app.root);
  var rowFor = function (e, mn, mx, meshes, group) {
    var center = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
    var size = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]];
    var radius = Math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2]) / 2;
    var row = { name: e.name, path: pcPath(e, app), center: center.map(r3), size: size.map(r3),
                radius: r3(radius), meshes: meshes, min: mn.map(r3), max: mx.map(r3) };
    if (group) row.group = true;
    if (e.enabled === false) row.hidden = true;
    /* The entity itself, for reveal(); not enumerable, so it never crosses the wire. */
    try { Object.defineProperty(row, 'ref', { value: e, enumerable: false }); } catch (e9) {}
    var dx = 0, dy = 0, dz = 0;
    if (cp) { dx = center[0] - cp.x; dy = center[1] - cp.y; dz = center[2] - cp.z; row.dist = r3(Math.sqrt(dx * dx + dy * dy + dz * dz)); }
    if (cam) {
      try {
        var v = V(); v.set(center[0], center[1], center[2]);
        var s = cam.worldToScreen(v, V());
        row.screen = [r3(s.x / W), r3(s.y / H)];
        /* In front means in front of the lens, tested with the camera's own forward vector. A
           point behind the camera still projects onto the screen, and worldToScreen's z does not
           say which side it came from — the actors' badges 380 m behind read as "in view". */
        var fw = ent.forward;
        var front = (dx * fw.x + dy * fw.y + dz * fw.z) > 0;
        row.in_view = front && s.x >= 0 && s.x <= W && s.y >= 0 && s.y <= H;
      } catch (e2) {}
    }
    return row;
  };
  var out = [];
  order.forEach(function (e) {
    try {
      var name = e.name || '';
      if (!name || name === 'Untitled' || name === '__studio_cam' || e === app.root) return;
      var b = own.get(e);
      if (b) { out.push(rowFor(e, b.mn, b.mx, b.meshes, false)); return; }
      if (!(e.children && e.children.length)) return;
      var mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity], n = 0;
      var sub = function (c) {
        var bb = own.get(c);
        if (bb) { n += bb.meshes; for (var i = 0; i < 3; i++) { mn[i] = Math.min(mn[i], bb.mn[i]); mx[i] = Math.max(mx[i], bb.mx[i]); } }
        var k = c.children || [];
        for (var j = 0; j < k.length; j++) sub(k[j]);
      };
      sub(e);
      if (n && isFinite(mn[0])) out.push(rowFor(e, mn, mx, n, true));
    } catch (e3) {}
  });
  out.sort(function (a, b) { return (a.dist || 0) - (b.dist || 0); });
  return out.slice(0, limit || 400);
};

/* ------------------------------------------------------------------ three */
var thScene = function () {
  if (N.three.scene) return N.three.scene;
  /* Through pinThreeScene, not a bare assignment: that is what hooks the scene's render and so
     hears the camera. Set directly, a game whose camera is not in its scene never had one. */
  try { var s = L && L.scenes && L.scenes(); if (s && s.length) { N.pinThreeScene(s[0]); return s[0]; } } catch (e) {}
  return null;
};
var thEnsure = function () {
  var sc = thScene();
  if (!N.three.camera && sc) {
    /* The scene API hooks the same render and keeps the camera it drew to the canvas with. */
    try { var seen = sc.__studioSeen; if (seen && seen.canvasCam && seen.canvasCam.isCamera) N.three.camera = seen.canvasCam; } catch (e) {}
  }
  var game = N.three.camera;
  if (!game) return null;
  if (N.ours && N.kind === 'three') return N.ours;
  var ours = game.clone();
  ours.name = '__studio_cam';
  ours.matrixAutoUpdate = true;
  try { ours.position.copy(game.getWorldPosition(game.position.clone())); } catch (e) {}
  try { ours.quaternion.copy(game.getWorldQuaternion(game.quaternion.clone())); } catch (e) {}
  ours.updateMatrixWorld(true);
  N.ours = ours; N.game = game; N.kind = 'three';
  return ours;
};
var thPoseOf = function (cam) {
  var d = cam.getWorldDirection(cam.position.clone());
  var a = anglesOf([d.x, d.y, d.z]);
  var p = cam.getWorldPosition(cam.position.clone());
  return { pos: [r3(p.x), r3(p.y), r3(p.z)], yaw: a.yaw, pitch: a.pitch, fov: cam.fov != null ? r3(cam.fov) : null };
};
var thSet = function (cam, o) {
  if (o.pos) cam.position.set(o.pos[0], o.pos[1], o.pos[2]);
  if (o.look_at) cam.lookAt(o.look_at[0], o.look_at[1], o.look_at[2]);
  else if (o.yaw != null || o.pitch != null) {
    var cur = thPoseOf(cam);
    var f = forward(o.yaw != null ? o.yaw : cur.yaw, o.pitch != null ? o.pitch : cur.pitch);
    cam.lookAt(cam.position.x + f[0], cam.position.y + f[1], cam.position.z + f[2]);
  }
  if (o.fov != null && cam.fov != null) { cam.fov = o.fov; cam.updateProjectionMatrix && cam.updateProjectionMatrix(); }
  cam.updateMatrixWorld(true);
};
var thThings = function (scene, cam, limit) {
  var out = [];
  var cp = cam ? cam.position : null;
  var own = new Map(), order = [];
  scene.traverse(function (o) {
    order.push(o);
    try {
      if (!o.isMesh || o === N.ours) return;
      var g = o.geometry; if (!g) return;
      if (!g.boundingSphere) g.computeBoundingSphere();
      var bs = g.boundingSphere; if (!bs || !isFinite(bs.radius)) return;
      o.updateMatrixWorld && o.updateMatrixWorld(true);
      var c = bs.center.clone().applyMatrix4(o.matrixWorld);
      var sc = o.getWorldScale(o.position.clone());
      var r = bs.radius * Math.max(Math.abs(sc.x), Math.abs(sc.y), Math.abs(sc.z));
      own.set(o, { mn: [c.x - r, c.y - r, c.z - r], mx: [c.x + r, c.y + r, c.z + r], meshes: 1 });
    } catch (e) {}
  });
  var rowFor = function (o, mn, mx, meshes, group) {
    var center = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
    var size = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]];
    var radius = Math.sqrt(size[0] * size[0] + size[1] * size[1] + size[2] * size[2]) / 2;
    var row = { name: o.name, center: center.map(r3), size: size.map(r3), radius: r3(radius), meshes: meshes,
                min: mn.map(r3), max: mx.map(r3) };
    if (group) row.group = true;
    if (o.visible === false) row.hidden = true;
    try { Object.defineProperty(row, 'ref', { value: o, enumerable: false }); } catch (e9) {}
    if (cp) row.dist = r3(Math.sqrt(Math.pow(center[0] - cp.x, 2) + Math.pow(center[1] - cp.y, 2) + Math.pow(center[2] - cp.z, 2)));
    if (cam) {
      var v = cam.position.clone().set(center[0], center[1], center[2]).project(cam);
      var d = cam.getWorldDirection(cam.position.clone());
      var front = ((center[0] - cp.x) * d.x + (center[1] - cp.y) * d.y + (center[2] - cp.z) * d.z) > 0;
      row.screen = [r3((v.x + 1) / 2), r3((1 - v.y) / 2)];
      row.in_view = front && Math.abs(v.x) <= 1 && Math.abs(v.y) <= 1 && v.z < 1 && v.z > -1;
    }
    return row;
  };
  order.forEach(function (o) {
    try {
      if (!o.name || o === scene || o === N.ours) return;
      var b = own.get(o);
      if (b) { out.push(rowFor(o, b.mn, b.mx, 1, false)); return; }
      if (!(o.children && o.children.length)) return;
      var mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity], n = 0;
      o.traverse(function (c) {
        var bb = own.get(c);
        if (bb) { n++; for (var i = 0; i < 3; i++) { mn[i] = Math.min(mn[i], bb.mn[i]); mx[i] = Math.max(mx[i], bb.mx[i]); } }
      });
      if (n && isFinite(mn[0])) out.push(rowFor(o, mn, mx, n, true));
    } catch (e) {}
  });
  out.sort(function (a, b) { return (a.dist || 0) - (b.dist || 0); });
  return out.slice(0, limit || 400);
};

/* ------------------------------------------------------------------ the front */
var forward = function (yaw, pitch) {
  var cy = Math.cos(yaw * DEG), sy = Math.sin(yaw * DEG), cp = Math.cos(pitch * DEG), sp = Math.sin(pitch * DEG);
  return [-sy * cp, sp, -cy * cp];
};
N.reach = function () {
  if (pcApp()) { N.kind = 'playcanvas'; return { kind: 'playcanvas', camera: !!pcGameCam(N.app) }; }
  if (thScene()) { N.kind = 'three'; return { kind: 'three', camera: !!N.three.camera }; }
  return { kind: '' };
};
var cam = function () {
  if (N.kind === 'playcanvas') return pcOn(N.app);
  if (N.kind === 'three') { var c = thEnsure(); if (c) N.on = true; return c; }
  return null;
};
var poseOf = function (c) { return N.kind === 'playcanvas' ? pcPoseOf(c) : thPoseOf(c); };
var setPose = function (c, o) { return N.kind === 'playcanvas' ? pcSet(c, o) : thSet(c, o); };
var things = function (c, limit) {
  return N.kind === 'playcanvas' ? pcThings(N.app, c, limit) : thThings(thScene(), c, limit);
};
N.gamePose = function () {
  if (!N.reach().kind) return null;
  if (N.kind === 'playcanvas') { var g = pcGameCam(N.app); return g ? pcPoseOf(g) : null; }
  return N.three.camera ? thPoseOf(N.three.camera) : null;
};
/* The sky dome, a ground plane, a label quad the size of the world: one of those makes the scene
   700 metres across and the sweep stands in the clouds. Anything more than 20x the median radius
   is scenery, not a place. */
var trimmed = function (all) {
  if (all.length < 4) return all;
  var rs = all.map(function (t) { return t.radius; }).sort(function (a, b) { return a - b; });
  var med = rs[Math.floor(rs.length / 2)] || 0;
  var lim = med * 20;
  return all.filter(function (t) { return !(t.radius > lim && lim > 0); });
};
N.bounds = function () {
  var c = N.on ? (N.ours || cam()) : cam();
  var all = trimmed(things(c, 2000));
  var mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  all.forEach(function (t) { for (var i = 0; i < 3; i++) { mn[i] = Math.min(mn[i], t.min[i]); mx[i] = Math.max(mx[i], t.max[i]); } });
  return isFinite(mn[0]) ? { min: mn.map(r3), max: mx.map(r3), things: all.length } : null;
};
N.things = function (limit) { var c = cam(); return things(c, limit || 60); };

/* REVEAL. A game switches distant places off until the player reaches them — platform-8 here
   is `enabled: false` with every mesh in place. Looking at it needs it on. The target and every
   switched-off ancestor are turned on while the Studio camera looks, remembered, and put back by
   release(). That is a change to the running game, so the answer says what was revealed. */
N.revealed = [];
/* HELD EVERY FRAME, not set once. The game's own update switches the far platforms off again
   on every tick — a reveal set once was undone before the picture was taken, and the camera
   photographed sky. So the revealed set is re-applied after the game's update and before the
   render: PlayCanvas fires `prerender` there; for three it is done inside the render wrap. */
var holdInstalled = false;
N.hold = function () {
  for (var i = 0; i < N.revealed.length; i++) {
    try { var r = N.revealed[i]; if (r[0][r[1]] === false) r[0][r[1]] = true; } catch (e) {}
  }
};
var installHold = function () {
  if (holdInstalled) return;
  holdInstalled = true;
  try { if (N.kind === 'playcanvas' && N.app && N.app.on) N.app.on('prerender', N.hold); } catch (e) {}
};
var reveal = function (ref) {
  var names = [];
  if (!ref) return names;
  try {
    var cur = ref;
    while (cur) {
      var prop = N.kind === 'playcanvas' ? 'enabled' : 'visible';
      if (cur[prop] === false) {
        var known = false;
        for (var i = 0; i < N.revealed.length; i++) if (N.revealed[i][0] === cur) { known = true; break; }
        cur[prop] = true;
        if (!known) { N.revealed.push([cur, prop]); names.push(cur.name || '?'); }
      }
      cur = cur.parent;
    }
  } catch (e) {}
  if (N.revealed.length) installHold();
  return names;
};
var unreveal = function () {
  var n = 0;
  var list = N.revealed;
  N.revealed = [];                       /* first, so hold() stops re-applying mid-way */
  for (var i = list.length - 1; i >= 0; i--) {
    try { list[i][0][list[i][1]] = false; n++; } catch (e) {}
  }
  return n;
};
N.revealedNames = function () { return N.revealed.map(function (r) { return r[0].name || '?'; }); };
/* The thing meant by a name, a centre, or both. Names repeat — `tread-sign-free` is on every
   platform — so a centre beside the name picks the instance nearest to it. */
var resolveThing = function (all, o) {
  var needle = o.name ? String(o.name).toLowerCase() : '';
  var pool = all;
  if (needle) {
    var exact = all.filter(function (t) { return t.name.toLowerCase() === needle; });
    pool = exact.length ? exact : all.filter(function (t) { return t.name.toLowerCase().indexOf(needle) >= 0; });
    if (!pool.length) return null;
  }
  if (o.look_at && o.look_at.length === 3) {
    var best = null, bd = Infinity;
    for (var i = 0; i < pool.length; i++) {
      var c = pool[i].center;
      var d = Math.pow(c[0] - o.look_at[0], 2) + Math.pow(c[1] - o.look_at[1], 2) + Math.pow(c[2] - o.look_at[2], 2);
      if (d < bd) { bd = d; best = pool[i]; }
    }
    // A centre with no name is a point, not a thing — unless a thing sits right on it.
    return (!needle && bd > 1.0) ? null : best;
  }
  return needle ? pool[0] : null;
};
/* The rows locate needs and nothing else. 600 full rows overflowed the page's answer cap and came
   back as a string; these four fields for 200 things are a tenth of that. */
N.brief = function (limit) {
  var c = cam();
  var all = trimmed(things(c, 2000)).slice(0, limit || 200);
  return all.map(function (t) { return { name: t.name, center: t.center, radius: t.radius }; });
};
N.release = function () {
  var put_back = unreveal();
  if (N.kind === 'playcanvas') pcOff(); else N.on = false;
  return { released: true, put_back: put_back };
};
var view = function (c, limit) {
  /* A thing with no size — a marker, an empty at the origin — projects to "in view" from
     everywhere and says nothing about the place. Keep the ones with a body. */
  var all = things(c, 600).filter(function (t) { return t.radius >= 0.02; });
  var inv = all.filter(function (t) { return t.in_view; });
  inv.sort(function (a, b) {
    var da = Math.hypot(a.screen[0] - 0.5, a.screen[1] - 0.5), db = Math.hypot(b.screen[0] - 0.5, b.screen[1] - 0.5);
    return da - db;
  });
  return { in_view: inv.slice(0, limit || 12), nearest: all.slice(0, 3), things: all.length };
};
/* o: name | at | look_at | yaw | pitch | distance | step[right,up,forward] | orbit[dYaw,dPitch] | fov */
N.goto = function (o) {
  o = o || {};
  var r = N.reach();
  if (!r.kind) return { ok: false, error: 'no engine reachable' };
  var c = cam();
  if (!c) return { ok: false, error: 'the game has no camera yet' };
  var pose = poseOf(c);
  var target = null;
  var revealedNow = [];
  if (o.name || (o.look_at && o.reveal)) {
    var all = things(c, 2000);
    var hit = resolveThing(all, o);
    if (!hit && o.name) {
      return { ok: false, error: 'nothing named like ' + JSON.stringify(o.name),
               names: all.slice(0, 40).map(function (t) { return t.name; }) };
    }
    if (hit) {
      target = hit;
      if (o.reveal !== false && hit.ref) revealedNow = reveal(hit.ref);
    }
  }
  if (o.look_at && !target) target = { center: o.look_at, radius: 1 };
  var yaw = o.yaw != null ? o.yaw : pose.yaw;
  var pitch = o.pitch != null ? o.pitch : (target ? -20 : pose.pitch);
  if (target) {
    var dist = o.distance != null ? o.distance : Math.max(1.5, target.radius * 2.4);
    var f = forward(yaw, pitch);
    var pos = [target.center[0] - f[0] * dist, target.center[1] - f[1] * dist, target.center[2] - f[2] * dist];
    setPose(c, { pos: pos, look_at: target.center, fov: o.fov });
    N.target = { center: target.center, radius: target.radius, name: target.name || '', dist: dist };
  } else if (o.at) {
    setPose(c, { pos: o.at, yaw: o.yaw != null ? yaw : null, pitch: o.pitch != null ? pitch : null, fov: o.fov });
    if (o.yaw == null && o.pitch == null && o.look_at) setPose(c, { look_at: o.look_at });
  } else if (o.orbit && N.target) {
    var ny = poseOf(c).yaw + (o.orbit[0] || 0), np = poseOf(c).pitch + (o.orbit[1] || 0);
    np = Math.max(-89, Math.min(89, np));
    var f2 = forward(ny, np), d2 = o.distance != null ? o.distance : N.target.dist;
    var p2 = [N.target.center[0] - f2[0] * d2, N.target.center[1] - f2[1] * d2, N.target.center[2] - f2[2] * d2];
    setPose(c, { pos: p2, look_at: N.target.center, fov: o.fov });
    N.target.dist = d2;
  } else if (o.orbit) {
    setPose(c, { yaw: pose.yaw + (o.orbit[0] || 0), pitch: Math.max(-89, Math.min(89, pose.pitch + (o.orbit[1] || 0))), fov: o.fov });
  } else if (o.step) {
    var p0 = poseOf(c), fw = forward(p0.yaw, p0.pitch), right = [Math.cos(p0.yaw * DEG), 0, -Math.sin(p0.yaw * DEG)];
    var s = o.step, np3 = [
      p0.pos[0] + right[0] * (s[0] || 0) + fw[0] * (s[2] || 0),
      p0.pos[1] + (s[1] || 0) + fw[1] * (s[2] || 0),
      p0.pos[2] + right[2] * (s[0] || 0) + fw[2] * (s[2] || 0)];
    setPose(c, { pos: np3, fov: o.fov });
  } else if (o.yaw != null || o.pitch != null || o.fov != null) {
    setPose(c, { yaw: o.yaw, pitch: o.pitch, fov: o.fov });
  }
  var v = view(c, o.limit);
  var out = { ok: true, engine: N.kind, pose: poseOf(c), target: N.target, in_view: v.in_view, nearest: v.nearest, things: v.things };
  if (revealedNow.length) out.revealed_now = revealedNow;
  if (N.revealed.length) out.revealed = N.revealedNames();
  return out;
};
N.where = function (limit) {
  var r = N.reach();
  if (!r.kind) return { ok: false, error: 'no engine reachable' };
  var c = N.on && N.ours ? N.ours : (N.kind === 'playcanvas' ? (pcGameCam(N.app) || cam()) : (N.three.camera || cam()));
  if (!c) return { ok: false, error: 'the game has no camera yet' };
  var v = view(c, limit);
  return { ok: true, engine: N.kind, studio_camera: !!(N.on && c === N.ours), pose: poseOf(c),
           in_view: v.in_view, nearest: v.nearest, things: v.things, bounds: N.bounds() };
};
N.frame = function () {
  return new Promise(function (res) { requestAnimationFrame(function () { requestAnimationFrame(function () { res(true); }); }); });
};
})();
"""

PIN_THIS = "function () { try { return window.__nav.pinAny(this) || ''; } catch (e) { return ''; } }"
PIN_MANY = ("function () { for (var i = 0; i < arguments.length; i++) { try { var k = window.__nav.pinAny(arguments[i]);"
            " if (k) return k; } catch (e) {} } return ''; }")
_HUNT_BUDGET = 8.0


async def ensure_nav(live) -> None:
    if not await live.raw("!!window.__nav"):
        await live.raw(NAV, wait=False)


async def _pin_function(live, fn_oid: str, deadline: float) -> str:
    """One function: what it is bound to, then every object in every scope it closes over."""
    props = await live.call("Runtime.getProperties", {"objectId": fn_oid, "ownProperties": True})
    ip = {p["name"]: p.get("value") for p in props.get("internalProperties") or []}
    bt = ip.get("[[BoundThis]]")
    if bt and bt.get("objectId"):
        q = await live.call("Runtime.callFunctionOn", {"objectId": bt["objectId"], "functionDeclaration": PIN_THIS,
                                                       "returnByValue": True})
        kind = (q.get("result") or {}).get("value")
        if kind:
            return str(kind)
    sc = ip.get("[[Scopes]]")
    if not (sc and sc.get("objectId")):
        return ""
    scopes = (await live.call("Runtime.getProperties", {"objectId": sc["objectId"], "ownProperties": True})).get("result") or []
    for s in scopes[:6]:
        if time.monotonic() > deadline:
            return ""
        so = (s.get("value") or {}).get("objectId")
        if not so:
            continue
        # The global scope is the page's 1,100 builtins; the shim's own hunt already walked those.
        if str((s.get("value") or {}).get("description", "")).lower().startswith("global"):
            continue
        rows = (await live.call("Runtime.getProperties", {"objectId": so, "ownProperties": True})).get("result") or []
        args = [{"objectId": r["value"]["objectId"]} for r in rows
                if (r.get("value") or {}).get("type") == "object" and (r.get("value") or {}).get("objectId")]
        # One call per scope, every object handed over as an argument: the quacking happens in the
        # page, so a 60-variable scope costs one round trip instead of sixty.
        for i in range(0, len(args), 90):
            q = await live.call("Runtime.callFunctionOn", {"objectId": so, "functionDeclaration": PIN_MANY,
                                                           "arguments": args[i:i + 90], "returnByValue": True})
            kind = (q.get("result") or {}).get("value")
            if kind:
                return str(kind)
    return ""


LAST_HUNT: dict = {}


async def hunt_closures(live, budget: float = _HUNT_BUDGET) -> str:
    """Find the engine in the closures of the game's own functions. Returns the kind pinned, or "".

    Sources, in order: the frame callbacks (every game has one and it closes over the engine),
    then the DOM listeners on the document subtree and the window. What it looked at is left in
    LAST_HUNT, so an "unreachable" answer can say how hard it tried.
    """
    deadline = time.monotonic() + budget
    t0 = time.monotonic()
    await ensure_nav(live)
    await asyncio.sleep(0.15)                       # a frame or two, so the recorder has seen the tick
    n = int(await live.raw("window.__nav.rafs.length") or 0)
    LAST_HUNT.clear()
    LAST_HUNT.update({"rafs": n, "listeners": 0, "functions": 0})
    for i in range(min(n, 8)):
        if time.monotonic() > deadline:
            break
        r = await live.call("Runtime.evaluate", {"expression": "window.__nav.rafs[%d]" % i, "returnByValue": False})
        oid = (r.get("result") or {}).get("objectId")
        if oid:
            LAST_HUNT["functions"] += 1
            kind = await _pin_function(live, oid, deadline)
            if kind:
                LAST_HUNT.update({"found": kind, "via": "requestAnimationFrame[%d]" % i,
                                  "ms": int((time.monotonic() - t0) * 1000)})
                return kind
    handlers: list = []
    for expr, depth in (("document", -1), ("window", 0)):
        try:
            r = await live.call("Runtime.evaluate", {"expression": expr, "returnByValue": False})
            oid = (r.get("result") or {}).get("objectId")
            if not oid:
                continue
            params = {"objectId": oid, "depth": depth}
            if depth < 0:
                params["pierce"] = True
            ls = (await live.call("DOMDebugger.getEventListeners", params)).get("listeners") or []
            for l in ls:
                h = (l.get("handler") or {}).get("objectId")
                if h:
                    handlers.append(h)
        except Exception:
            continue
    LAST_HUNT["listeners"] = len(handlers)
    for h in handlers[:48]:
        if time.monotonic() > deadline:
            break
        try:
            LAST_HUNT["functions"] += 1
            kind = await _pin_function(live, h, deadline)
        except Exception:
            continue
        if kind:
            LAST_HUNT.update({"found": kind, "via": "listener", "ms": int((time.monotonic() - t0) * 1000)})
            return kind
    LAST_HUNT["ms"] = int((time.monotonic() - t0) * 1000)
    return ""


# ---------------------------------------------------------------------------------------- scoring
_GRAY = (48, 27)
_COLOR = (12, 7)


def _pil():
    from PIL import Image
    return Image


def _thumbs(img) -> tuple:
    Image = _pil()
    g = img.convert("L").resize(_GRAY, Image.BILINEAR)
    gray = list(g.getdata())
    mean = sum(gray) / len(gray)
    var = sum((v - mean) ** 2 for v in gray) / len(gray)
    sd = math.sqrt(var) or 1.0
    gz = [(v - mean) / sd for v in gray]
    c = img.convert("RGB").resize(_COLOR, Image.BILINEAR)
    color = list(c.getdata())
    return gz, color


def score_thumbs(a: tuple, b: tuple) -> float:
    """0..1. Brightness-normalised grayscale structure carries most of it; colour the rest.

    Not a matcher anyone should trust blindly: the same plaza from two angles scores in the middle,
    a different scene scores low, the exact view scores near one. It ranks candidates; the number
    is reported so a middling best reads as the guess it is.
    """
    ga, ca = a
    gb, cb = b
    n = min(len(ga), len(gb))
    if not n:
        return 0.0
    dg = sum(abs(ga[i] - gb[i]) for i in range(n)) / n           # z-units, ~0 same, ~1.1 unrelated
    sg = max(0.0, 1.0 - dg / 1.2)
    m = min(len(ca), len(cb))
    dc = sum(abs(ca[i][k] - cb[i][k]) for i in range(m) for k in range(3)) / (3 * m) if m else 255
    sc = max(0.0, 1.0 - dc / 128.0)
    return round(0.65 * sg + 0.35 * sc, 4)


def score_images(a_img, b_img) -> float:
    return score_thumbs(_thumbs(a_img), _thumbs(b_img))


def changed_pct(a_img, b_img, thresh: int = 14) -> float:
    """How much of the frame changed between two shots at the same pose, in percent of pixels."""
    Image = _pil()
    a = a_img.convert("L").resize((64, 36), Image.BILINEAR)
    b = b_img.convert("L").resize((64, 36), Image.BILINEAR)
    da, db = list(a.getdata()), list(b.getdata())
    n = min(len(da), len(db))
    if not n:
        return 0.0
    return round(100.0 * sum(1 for i in range(n) if abs(da[i] - db[i]) > thresh) / n, 1)


# ---------------------------------------------------------------------------------------- candidates
def _words(s: str) -> list:
    """Hint words: three letters or more, or a number of any length — "platform 8" is two words."""
    return [w for w in "".join(ch if ch.isalnum() else " " for ch in str(s or "").lower()).split()
            if len(w) >= 3 or w.isdigit()]


def _norm(s: str) -> str:
    """`platform-8`, `platform_8` and `Platform 8` are the same name."""
    return "-".join(_words(s))


def trim_outliers(things: list) -> list:
    """Drop the sky dome and its kind: anything over 20x the median radius is scenery, not a place."""
    rows = [t for t in (things or []) if isinstance(t, dict)]
    if len(rows) < 4:
        return rows
    rs = sorted(float(t.get("radius") or 0.0) for t in rows)
    med = rs[len(rs) // 2]
    lim = med * 20
    return [t for t in rows if not (lim > 0 and float(t.get("radius") or 0.0) > lim)]


def candidates(things: list, bounds: Optional[dict], game_pose: Optional[dict],
               hint: str = "", names: Optional[list] = None, cap: int = 40) -> list:
    """The views worth rendering, best guess first. Pure, so it is tested without a browser.

    A hint word that appears in a thing's name puts that thing first; then the biggest things,
    because a screenshot is usually of something big. Each gets four bearings at the game
    camera's own pitch. Whatever room is left goes to a coarse sweep of the scene bounds.
    """
    things = trim_outliers(things)
    cap = max(4, min(96, int(cap or 40)))
    pitch = float((game_pose or {}).get("pitch") or -20.0)
    if pitch > -5:
        pitch = -20.0
    yaw0 = float((game_pose or {}).get("yaw") or 0.0)
    wants = set(_words(hint)) | {w for nm in (names or []) for w in _words(nm)}
    said = " " + _norm(hint) + " " + " ".join(" " + _norm(n) + " " for n in (names or []))
    ranked = []
    for t in things or []:
        nm = str(t.get("name", ""))
        tw = set(_words(nm))
        match = len(wants & tw) if wants else 0
        # The whole name said outright — "platform 8" — beats a shared word: every platform shares
        # "platform", and only one of them is the one asked for.
        if nm and (" " + _norm(nm) + " ") in said:
            match += 5
        ranked.append((match, float(t.get("radius") or 0.0), t))
    ranked.sort(key=lambda r: (-r[0], -r[1]))
    out: list = []
    seen = set()
    for match, radius, t in ranked:
        nm = str(t.get("name", ""))
        if nm in seen:
            continue
        seen.add(nm)
        for k in range(4):
            if len(out) >= cap:
                break
            # Name AND centre: the name lets goto reveal a switched-off place, the centre picks the
            # right one of several with that name.
            out.append({"name": nm, "look_at": t.get("center"), "yaw": (yaw0 + 90 * k) % 360, "pitch": pitch,
                        "distance": max(2.0, min(80.0, radius * 2.4)), "why": nm + (" (hint)" if match else "")})
        if match:
            # A screenshot of a place is usually taken from IN it, low, the way a player sees it —
            # not from a survey height. Two close, low views along the axis, for the hinted things.
            for k in (0, 2):
                if len(out) >= cap:
                    break
                out.append({"name": nm, "look_at": t.get("center"), "yaw": (yaw0 + 90 * k) % 360, "pitch": -8.0,
                            "distance": max(2.0, min(40.0, radius * 0.9)), "why": nm + " (hint, close)"})
        if len(out) >= cap or len(seen) >= 10:
            break
    out.extend(sweep(bounds, game_pose, pitch, yaw0, cap - len(out)))
    return out[:cap]


def sweep(bounds: Optional[dict], game_pose: Optional[dict], pitch: float, yaw0: float, room: int) -> list:
    """Standpoints over the scene, for when no named thing says where to look.

    A square world gets a 3x3 grid with four bearings. A LONG world — a runner's track is 1,470 m
    by 90 m — gets standpoints along its long axis, each looking up and down the axis, because a
    grid of nine over that length never stands within sight of any one platform.
    """
    if not bounds or room <= 0:
        return []
    mn, mx = list(bounds.get("min") or [0, 0, 0]), list(bounds.get("max") or [0, 0, 0])
    y = float(((game_pose or {}).get("pos") or [0, 0, 0])[1] or (mn[1] + 3.0))
    lx, lz = max(0.0, mx[0] - mn[0]), max(0.0, mx[2] - mn[2])
    out: list = []
    if max(lx, lz) > 3 * max(1e-6, min(lx, lz)):
        axis = 2 if lz >= lx else 0
        length = max(lx, lz)
        n = max(2, min(room // 2, 24))
        spacing = length / n
        for i in range(n):
            t = mn[axis] + spacing * (i + 0.5)
            at = [(mn[0] + mx[0]) / 2, y, (mn[2] + mx[2]) / 2]
            at[axis] = t
            ax = [0.0, 0.0, 0.0]
            ax[axis] = 1.0
            # yaw 180 looks down +Z, yaw 0 down -Z; yaw 270 looks down +X, yaw 90 down -X.
            yaws = (180.0, 0.0) if axis == 2 else (270.0, 90.0)
            for yw in yaws:
                if len(out) >= room:
                    return out
                out.append({"at": [round(v, 3) for v in at], "yaw": yw, "pitch": pitch, "why": "sweep",
                            "axis": ax, "spacing": round(spacing, 3)})
        return out
    for gx in (0.2, 0.5, 0.8):
        for gz in (0.2, 0.5, 0.8):
            x = mn[0] + lx * gx
            z = mn[2] + lz * gz
            for k in range(4):
                if len(out) >= room:
                    return out
                out.append({"at": [round(x, 3), round(y, 3), round(z, 3)], "yaw": (yaw0 + 90 * k) % 360,
                            "pitch": pitch, "why": "sweep"})
    return out


def refinements(best: dict) -> list:
    """Around one candidate: a little left and right, up and down, closer and further."""
    out = []
    if best.get("look_at") is not None:
        d = float(best.get("distance") or 5.0)
        for dy in (-18, 18):
            out.append({**best, "yaw": (best["yaw"] + dy) % 360, "why": "refine yaw"})
        for dp in (-10, 10):
            out.append({**best, "pitch": max(-85, min(10, best["pitch"] + dp)), "why": "refine pitch"})
        for f in (0.7, 1.45):
            out.append({**best, "distance": max(1.5, d * f), "why": "refine distance"})
    else:
        for dy in (-25, 25):
            out.append({**best, "yaw": (best["yaw"] + dy) % 360, "why": "refine yaw"})
        ax, sp = best.get("axis"), float(best.get("spacing") or 0)
        if ax and sp:
            for s in (-0.5, 0.5):
                at = [round(best["at"][i] + ax[i] * sp * s, 3) for i in range(3)]
                out.append({**best, "at": at, "why": "refine along the axis"})
    return out


# ---------------------------------------------------------------------------------------- endpoints
def _off() -> Optional[dict]:
    if not settings.get("cc_navigate", False):
        return {"ok": False, "error": "Navigate is off. Turn it on in Settings → Studio engine → "
                                      "Navigate the running game."}
    return None


def _pose_file(project: str) -> Path:
    return L._LIVE_DIR / L._slug(project) / "pose.json"


def _load_pose(project: str) -> Optional[dict]:
    try:
        return json.loads(_pose_file(project).read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_pose(project: str, pose: dict, shot: str, engine: str) -> None:
    p = _pose_file(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pose": pose, "shot": shot, "engine": engine, "at": time.time()}), encoding="utf-8")
    tmp.replace(p)


async def _reach(live, entry: dict) -> dict:
    """The shim's hunt, the bridge, and the closure hunt if those did not do it."""
    await L._bridge(live, entry)
    await ensure_nav(live)
    r = await live.ask("__nav.reach()") or {}
    if not r.get("kind"):
        try:
            kind = await asyncio.wait_for(hunt_closures(live), _HUNT_BUDGET + 2)
        except Exception as ex:
            kind = ""
            LAST_HUNT["error"] = str(ex)[:160]
        if kind:
            await asyncio.sleep(0.1)
            r = await live.ask("__nav.reach()") or {}
    if not r.get("kind"):
        r = {**r, "hunt": dict(LAST_HUNT)}
    return r


_UNREACHABLE = ("the engine's app object could not be reached: not on the page, not in the heap, not in "
                "the closures of the game's frame callback or its DOM listeners. One dev-only line where "
                "the game is created unlocks everything: window.__game = { app };")


async def _snap(live) -> bytes:
    await live.ask("__nav.frame()")
    got = await live.call("Page.captureScreenshot", {"format": "png"})
    return base64.b64decode(got.get("data") or "")


def _write(project: str, name: str, data: bytes) -> str:
    out = L._LIVE_DIR / L._slug(project) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return str(out)


def _need_open(entry: dict) -> Optional[dict]:
    if not entry.get("url"):
        return {"ok": False, "error": "no game is open for this project — POST /api/live/open first"}
    return None


_BUSY = ("another call is using this game's camera (a look, a shot or a watch) — try again in a moment")


def _locked(project: str, go) -> dict:
    """Run a navigation under the game's view lock (live_scene._lock_for).

    A look photographs the game with the Studio camera and hands the view back after; a goto that
    landed in the middle of one was thrown away by the look's release, and a shot taken then showed
    the look's camera, or the state before the edit. One lock per game, bounded: a stuck call is a
    message, not a hung request."""
    from .live_scene import _lock_for
    lk = _lock_for(project)
    if not lk.acquire(timeout=60):
        return {"ok": False, "error": _BUSY}
    try:
        return L._run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    finally:
        lk.release()


def goto(project: str, opts: dict) -> dict:
    """Move the Studio's camera and hand back the picture and what is in it."""
    bad = _off() or L._guard(project)
    if bad:
        return bad
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad
    opts = dict(opts or {})
    release = bool(opts.pop("release", False))
    pose_word = str(opts.pop("pose", "") or "")
    prev = _load_pose(project)
    if pose_word == "last":
        if not prev:
            return {"ok": False, "error": "no pose was kept for this project yet — goto somewhere first"}
        pp = prev["pose"]
        opts = {"at": pp.get("pos"), "yaw": pp.get("yaw"), "pitch": pp.get("pitch"), "fov": pp.get("fov"), **opts}

    async def go():
        ws, live = await L._session(e)
        try:
            r = await _reach(live, e)
            if not r.get("kind"):
                return {"ok": False, "error": _UNREACHABLE, "hunt": r.get("hunt"),
                        "reach": await live.ask("__live.reach()")}
            if release or pose_word == "game":
                rel = await live.ask("__nav.release()") or {}
                await live.ask("__nav.frame()")
                res = {"ok": True, "released": True, "pose": await live.ask("__nav.gamePose()"),
                       "put_back": (rel.get("put_back") if isinstance(rel, dict) else 0)}
                data = await _snap(live)
                res["shot"] = _write(project, "goto-%d.png" % int(time.time() * 1000), data)
                return res
            res = await live.ask("__nav.goto(%s)" % json.dumps(opts), depth=8) or {}
            if res.get("error") == "the game has no camera yet":
                # The scene's render hook hears the camera on the next frame the game draws; the
                # first ask installed it. One short wait, then ask again.
                await asyncio.sleep(0.3)
                res = await live.ask("__nav.goto(%s)" % json.dumps(opts), depth=8) or {}
            if not res.get("ok"):
                return {"ok": False, **res}
            data = await _snap(live)
            res["shot"] = _write(project, "goto-%d.png" % int(time.time() * 1000), data)
            if pose_word == "last" and prev and prev.get("shot") and Path(prev["shot"]).is_file():
                try:
                    Image = _pil()
                    res["changed_pct"] = changed_pct(Image.open(prev["shot"]), Image.open(io.BytesIO(data)))
                    res["compared_to"] = prev["shot"]
                except Exception as ex:
                    res["changed_pct_error"] = str(ex)[:120]
            _save_pose(project, res.get("pose") or {}, res["shot"], res.get("engine", ""))
            return res
        finally:
            await ws.close()

    return _locked(project, go)


def where(project: str, limit: int = 12) -> dict:
    bad = _off() or L._guard(project)
    if bad:
        return bad
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad

    async def go():
        ws, live = await L._session(e)
        try:
            r = await _reach(live, e)
            if not r.get("kind"):
                return {"ok": False, "error": _UNREACHABLE, "hunt": r.get("hunt"),
                        "reach": await live.ask("__live.reach()")}
            res = await live.ask("__nav.where(%d)" % int(limit or 12), depth=8) or {}
            if res.get("ok"):
                res["shot"] = _write(project, "where-%d.png" % int(time.time() * 1000), await _snap(live))
            return res
        finally:
            await ws.close()

    return _locked(project, go)


def locate(project: str, image: str, hint: str = "", names: Optional[list] = None,
           max_candidates: int = 40, refine: bool = True, keep: int = 3) -> dict:
    """From a screenshot to a camera pose: render the candidates, score them, take the best."""
    bad = _off() or L._guard(project)
    if bad:
        return bad
    e = L._entry(project)
    bad = _need_open(e)
    if bad:
        return bad
    p = Path(str(image or ""))
    if not p.is_file():
        return {"ok": False, "error": "no such image: %s" % image}
    try:
        Image = _pil()
        ref = _thumbs(Image.open(p))
    except Exception as ex:
        return {"ok": False, "error": "could not read the image: %s" % ex}

    async def go():
        ws, live = await L._session(e)
        try:
            r = await _reach(live, e)
            if not r.get("kind"):
                return {"ok": False, "error": _UNREACHABLE, "hunt": r.get("hunt"),
                        "reach": await live.ask("__live.reach()")}
            game_pose = await live.ask("__nav.gamePose()")
            # The page flattens a list to its first 60 rows and appends "…+N more" as a STRING row,
            # so ask for what will arrive whole and keep only the dicts.
            things = await live.ask("__nav.brief(60)", depth=6) or []
            things = [t for t in things if isinstance(t, dict)] if isinstance(things, list) else []
            bounds = await live.ask("__nav.bounds()") or None
            if not isinstance(bounds, dict):
                bounds = None
            if not isinstance(game_pose, dict):
                game_pose = None
            cands = candidates(things, bounds, game_pose, hint, names, max_candidates)
            if not cands:
                return {"ok": False, "error": "nothing to aim at: the scene reports no named things and no bounds"}
            t0 = time.time()
            scored: list = []
            Image = _pil()

            async def try_one(c: dict) -> dict:
                res = await live.ask("__nav.goto(%s)" % json.dumps({k: v for k, v in c.items() if k != "why"}), depth=6) or {}
                if not res.get("ok"):
                    return {"score": -1.0, "cand": c, "error": res.get("error", "")[:80]}
                data = await _snap(live)
                s = score_thumbs(ref, _thumbs(Image.open(io.BytesIO(data))))
                return {"score": s, "cand": c, "pose": res.get("pose"), "png": data}

            # The game's own view first: if the user photographed what the game shows by
            # default, no sweep is needed and the answer says so.
            await live.ask("__nav.release()")
            data0 = await _snap(live)
            base_score = score_thumbs(ref, _thumbs(Image.open(io.BytesIO(data0))))
            scored.append({"score": base_score, "cand": {"pose": "game", "why": "the game's own camera"},
                           "pose": game_pose, "png": data0})
            for c in cands:
                scored.append(await try_one(c))
            scored.sort(key=lambda x: -x["score"])
            if refine and scored and scored[0].get("cand", {}).get("why") != "the game's own camera":
                for c in refinements(scored[0]["cand"]):
                    scored.append(await try_one(c))
                scored.sort(key=lambda x: -x["score"])
            best = scored[0]
            top = []
            for i, s in enumerate(scored[:max(1, int(keep or 3))]):
                shot = _write(project, "locate-%d-%d.png" % (int(t0), i), s["png"]) if s.get("png") else ""
                top.append({"score": s["score"], "pose": s.get("pose"), "why": s["cand"].get("why", ""),
                            "shot": shot})
            # Land on the best, for real, so the next goto/where starts there.
            if best["cand"].get("pose") == "game":
                await live.ask("__nav.release()")
            else:
                await live.ask("__nav.goto(%s)" % json.dumps({k: v for k, v in best["cand"].items() if k != "why"}))
                await live.ask("__nav.frame()")
            if best.get("pose"):
                _save_pose(project, best["pose"], top[0]["shot"], r.get("kind", ""))
            verdict = ("a match" if best["score"] >= 0.72 else
                       "probably the place, from a different angle" if best["score"] >= 0.55 else
                       "a guess — say so, and check with where()")
            wants = set(_words(hint)) | {w for nm in (names or []) for w in _words(nm)}
            matched = sorted({str(t.get("name")) for t in things if wants & set(_words(t.get("name", "")))})
            out = {"ok": True, "engine": r.get("kind"), "best": top[0], "top": top, "verdict": verdict,
                   "tried": len(scored), "seconds": round(time.time() - t0, 1),
                   "game_view_score": base_score, "matched_names": matched[:12], "bounds": bounds}
            if wants and not matched:
                out["note"] = ("no named thing matched the hint, so only the sweep was used. A world built "
                               "from merged buckets has few names; and a place that is SPAWNED by play does "
                               "not exist until the game is driven there — drive it (input, or the game's own "
                               "dev hooks), then locate again.")
            return out
        finally:
            await ws.close()

    return _locked(project, go)

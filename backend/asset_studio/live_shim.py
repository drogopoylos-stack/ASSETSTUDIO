"""The script that turns any web game into something an agent can question.

It is injected before the page's own code runs, so it sees the first error and the first frame.
Everything it does is a wrap that calls through: `console.error` still prints, `drawElements`
still draws, `getContext` still returns the context. A game must not be able to tell it is there.

Five things are instrumented, and none of them need the engine's cooperation:

  * the console, plus `onerror`, promise rejections, failed `<img>`/`<script>` loads, and failed
    `fetch`/XHR — the reasons a game looks wrong that a screenshot can never show;
  * `requestAnimationFrame`, for the frame time the player actually gets;
  * the WebGL draw calls, for cost — a scene that reads fine at 900 draw calls is still broken;
  * `CanvasRenderingContext2D`, for the same reason and the other half of the web: an HTML5 game
    that draws in 2D issues no WebGL calls at all, so it used to report zero and read as dead;
  * `HTMLCanvasElement.getContext`, which is how we find the drawing surface of ANY engine.

On top of that sits one engine-aware layer, because "what is in the scene" cannot be answered
generically — and finding the engine at all is the hard part. A bundled game has no `window.pc`
and no `window.THREE`: the app lives in module scope. So five routes, in order (the fifth is in
live.py, because only the debugger can do it):

  * three.js dispatches its renderer and scenes at `__THREE_DEVTOOLS__`, an EventTarget it looks
    for itself. Define it before the bundle loads and the scene announces itself. Supported, and
    the only way in for three.js, which publishes no global at all.
  * The canvas names its own engine: PlayCanvas and three.js both write
    `data-engine="PlayCanvas 2.21.3"` onto it. That is detection without access, which is still
    worth having — the answer can then say WHICH engine is out of reach instead of "unknown".
  * The page's own globals, found by diffing `window` against a blank iframe's, then walked a few
    levels deep for anything that quacks like a scene. Games hand themselves a debug handle far
    more often than not, and this locates it wherever it happens to be: in the game this was
    built against, the PlayCanvas app is at `__game.renderer.app`, which no fixed list of names
    would ever have contained.
  * The console banner. Phaser prints "Phaser v3.90.0" and PixiJS 7 prints its version on boot,
    and the console wrapper above already has every line — so an engine that neither tags its
    canvas nor exports a global still gets named.
  * `Runtime.queryObjects` from live.py, which is the only route that works for the commonest
    HTML5 case of all. Phaser and PixiJS put their CLASS on `window` and keep the INSTANCE inside
    the game's own closure, so nothing in this file can ever walk to it; the debugger can query
    the heap for it, and hands the result back through `L.pin`.

Whatever cannot be reached is named rather than guessed at. Unity, Godot and Defold compile to
WebAssembly and keep their scene inside linear memory, where no JavaScript can walk it by any
means — so those say so plainly, and report what does still work.
"""
from __future__ import annotations

SHIM = r"""
(() => {
if (window.__live) return;

var L = { errors: [], born: Date.now() };
try { Object.defineProperty(window, '__live', { value: L, configurable: true, writable: true }); }
catch (e) { window.__live = L; }

/* THE PAGE'S OWN LIST OF WHAT IT LOADED, kept whole. Chrome keeps 250 resource entries and drops
   the rest, and `stale` (live_view.py) reads this list to tell a file the page runs an old copy of
   from one it never loaded; a full list cannot tell them apart. Raised at document start. `rtFull`
   says the list is not whole: it had filled before a shim put in late could raise it, or it filled
   again. */
try {
  L.rtFull = performance.getEntriesByType('resource').length >= 250;
  performance.setResourceTimingBufferSize(5000);
  L.rtMax = 5000;
  performance.addEventListener('resourcetimingbufferfull', function () { L.rtFull = true; });
} catch (e) {}

var r3 = function (n) { return typeof n === 'number' ? Math.round(n * 1000) / 1000 : n; };
var ctorName = function (v) {
  try { return (v && v.constructor && v.constructor.name) || ''; } catch (e) { return ''; }
};
/* Every path this file hands back is meant to be pasted straight into eval, so it has to be
   valid JavaScript. Joining with a dot produced `app.root.children.1`, which is a syntax error —
   the agent got a path it could not use. */
var step = function (k) {
  k = String(k);
  if (/^[0-9]+$/.test(k)) return '[' + k + ']';
  if (/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(k)) return '.' + k;
  return '[' + JSON.stringify(k) + ']';
};
var hexOf = function (c) {
  try {
    var p = [c.r, c.g, c.b].map(function (x) {
      var v = Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16);
      return v.length < 2 ? '0' + v : v;
    });
    return '#' + p.join('');
  } catch (e) { return null; }
};

/* ------------------------------------------------------------------ values
   A game object is circular, deep, and full of getters. Handing one to the
   protocol raw either fails or returns 40k of noise, so everything an agent
   reads passes through here: bounded depth, bounded width, cycles named. */
var short = function (v, depth, seen) {
  if (depth == null) depth = 3;
  seen = seen || [];
  var t = typeof v;
  if (v === null) return null;
  if (t === 'number') return Number.isFinite(v) ? (Math.round(v * 100000) / 100000) : String(v);
  if (t === 'boolean') return v;
  if (t === 'string') return v.length > 2000 ? v.slice(0, 2000) + '…' : v;
  if (t === 'undefined') return '[undefined]';
  if (t === 'function') return '[function ' + (v.name || 'anonymous') + ']';
  if (t === 'symbol' || t === 'bigint') return String(v);
  if (v === window) return '[window]';
  if (typeof document !== 'undefined' && v === document) return '[document]';
  if (seen.indexOf(v) >= 0) return '[circular]';
  if (depth <= 0) return '[' + (ctorName(v) || 'object') + ']';
  seen.push(v);
  try {
    if (v instanceof Error) {
      return { __type: 'Error', message: String(v.message || ''),
               stack: String(v.stack || '').slice(0, 900) };
    }
    if (typeof Node !== 'undefined' && v instanceof Node) {
      var tag = (v.tagName || v.nodeName || 'node').toLowerCase();
      return '<' + tag + (v.id ? '#' + v.id : '') +
             (v.className && v.className.baseVal === undefined && v.className
               ? '.' + String(v.className).trim().split(/\s+/).slice(0, 3).join('.') : '') + '>';
    }
    if (typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView && ArrayBuffer.isView(v)) {
      return { __type: ctorName(v) || 'TypedArray', length: v.length,
               head: Array.prototype.slice.call(v.subarray ? v.subarray(0, 12) : v, 0, 12) };
    }
    if (Array.isArray(v)) {
      var arr = [];
      for (var i = 0; i < v.length && i < 60; i++) arr.push(short(v[i], depth - 1, seen));
      if (v.length > 60) arr.push('…+' + (v.length - 60) + ' more');
      return arr;
    }
    if (typeof Map !== 'undefined' && v instanceof Map) {
      var m = { __type: 'Map', size: v.size, entries: {} }, mi = 0;
      v.forEach(function (val, k) {
        if (mi++ < 30) { try { m.entries[String(k)] = short(val, depth - 1, seen); } catch (e) {} }
      });
      return m;
    }
    if (typeof Set !== 'undefined' && v instanceof Set) {
      var s = { __type: 'Set', size: v.size, values: [] }, si = 0;
      v.forEach(function (val) { if (si++ < 30) s.values.push(short(val, depth - 1, seen)); });
      return s;
    }
    var out = {};
    var n = ctorName(v);
    if (n && n !== 'Object') out.__type = n;
    var keys = 0;
    for (var k in v) {
      if (keys >= 60) { out.__more = true; break; }
      if (k.charCodeAt(0) === 95) continue;            /* _private: engine internals, always huge */
      var val;
      try { val = v[k]; } catch (e) { continue; }      /* a getter that throws is not our problem */
      if (typeof val === 'function') continue;
      keys++;
      out[k] = short(val, depth - 1, seen);
    }
    return out;
  } catch (e) {
    return '[unreadable ' + (ctorName(v) || 'value') + ']';
  } finally {
    seen.pop();
  }
};
L.json = short;

/* ----------------------------------------------------------------- console
   Every record carries a sequence number, and that is not decoration. Two
   readers want this list — an `eval` that should report only what ITS code
   caused, and `console` which owns everything since it last drained. A drain
   inside `eval` left the second reader permanently empty, so `eval` marks and reads back
   from the mark instead, and removes nothing. */
var CAP = 300;
var seq = 0;
var rec = function (kind, msg, extra) {
  try {
    if (L.errors.length >= CAP) L.errors.shift();
    var row = { seq: ++seq, ms: Math.round(performance.now()), kind: kind,
                msg: String(msg).slice(0, 900) };
    if (extra) for (var k in extra) row[k] = extra[k];
    L.errors.push(row);
  } catch (e) {}
};
var fmt = function (args) {
  try {
    return Array.prototype.map.call(args, function (a) {
      if (typeof a === 'string') return a;
      if (a instanceof Error) return String(a.stack || a.message || a);
      try { return JSON.stringify(short(a, 2)); } catch (e) { return String(a); }
    }).join(' ');
  } catch (e) { return '(unprintable)'; }
};

try {
  ['error', 'warn', 'log', 'info'].forEach(function (lvl) {
    var orig = console[lvl];
    if (typeof orig !== 'function') return;
    console[lvl] = function () {
      rec(lvl === 'info' ? 'log' : lvl, fmt(arguments));
      return orig.apply(console, arguments);
    };
  });
} catch (e) {}

try {
  /* Capture phase, because a failed <img>/<script>/<audio> does not bubble. */
  window.addEventListener('error', function (e) {
    var t = e && e.target;
    if (t && t !== window && (t.src || t.href)) {
      rec('resource', 'failed to load ' + (t.src || t.href),
          { tag: String(t.tagName || '').toLowerCase() });
    } else {
      rec('error', (e && e.error && (e.error.stack || e.error.message)) || (e && e.message) || 'error',
          { at: ((e && e.filename) || '') + ':' + ((e && e.lineno) || 0) });
    }
  }, true);
  window.addEventListener('unhandledrejection', function (e) {
    var r = e && e.reason;
    rec('promise', (r && (r.stack || r.message)) || String(r));
  });
} catch (e) {}

try {
  if (typeof fetch === 'function') {
    var of = fetch;
    window.fetch = function () {
      var url;
      try { url = (arguments[0] && arguments[0].url) || String(arguments[0]); } catch (e) { url = '?'; }
      return of.apply(this, arguments).then(function (r) {
        if (!r.ok) rec('net', r.status + ' ' + url, { status: r.status });
        return r;
      }, function (err) {
        rec('net', 'fetch failed ' + url + ' — ' + err, { status: 0 });
        throw err;
      });
    };
  }
} catch (e) {}

try {
  var xo = XMLHttpRequest.prototype.open, xs = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    try { this.__liveUrl = String(u); } catch (e) {}
    return xo.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var self = this;
    try {
      this.addEventListener('loadend', function () {
        if (self.status === 0 || self.status >= 400) {
          rec('net', (self.status || 0) + ' ' + (self.__liveUrl || '?'), { status: self.status });
        }
      });
    } catch (e) {}
    return xs.apply(this, arguments);
  };
} catch (e) {}

/* -------------------------------------------------------------- frames, GL
   Counting draws at the WebGL prototype works for every engine and needs none
   of them: three.js, PlayCanvas, Babylon and hand-written GL all end up here. */
var gl = { calls: 0, tris: 0, tex: 0, buf: 0, prog: 0, fbo: 0, ops2d: 0, images: 0 };
var hist = { dt: [], calls: [], tris: [], ops2d: [], images: [] };
var HCAP = 900;
var put = function (a, v) { if (a.length >= HCAP) a.shift(); a.push(v); };

var triCount = function (mode, count) {
  if (mode === 4) return count / 3;                 /* TRIANGLES */
  if (mode === 5 || mode === 6) return Math.max(0, count - 2);   /* STRIP, FAN */
  return 0;
};
var wrapGl = function (proto) {
  if (!proto) return;
  /* [name, index of the vertex count, index of the instance count or -1] */
  [['drawArrays', 2, -1], ['drawElements', 1, -1],
   ['drawArraysInstanced', 2, 3], ['drawElementsInstanced', 1, 4],
   ['drawRangeElements', 3, -1]].forEach(function (spec) {
    var orig = proto[spec[0]];
    if (typeof orig !== 'function') return;
    proto[spec[0]] = function () {
      try {
        gl.calls++;
        var inst = spec[2] >= 0 ? (arguments[spec[2]] || 1) : 1;
        gl.tris += triCount(arguments[0], arguments[spec[1]] || 0) * inst;
      } catch (e) {}
      return orig.apply(this, arguments);
    };
  });
  [['createTexture', 'deleteTexture', 'tex'], ['createBuffer', 'deleteBuffer', 'buf'],
   ['createProgram', 'deleteProgram', 'prog'],
   ['createFramebuffer', 'deleteFramebuffer', 'fbo']].forEach(function (spec) {
    var mk = proto[spec[0]], rm = proto[spec[1]];
    if (typeof mk === 'function') {
      proto[spec[0]] = function () { try { gl[spec[2]]++; } catch (e) {} return mk.apply(this, arguments); };
    }
    if (typeof rm === 'function') {
      proto[spec[1]] = function () { try { gl[spec[2]]--; } catch (e) {} return rm.apply(this, arguments); };
    }
  });
};
try {
  wrapGl(window.WebGLRenderingContext && window.WebGLRenderingContext.prototype);
  wrapGl(window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype);
} catch (e) {}

/* An HTML5 game that draws with canvas 2D issues no WebGL calls at all, so `draw_calls` read 0
   and the game looked dead in the numbers while it was plainly running in the picture. Count the
   2D operations the same way: one number for the cost of a frame, and `drawImage` separately
   because for a sprite game that IS the frame. */
try {
  var c2d = window.CanvasRenderingContext2D && window.CanvasRenderingContext2D.prototype;
  if (c2d) {
    ['fillRect', 'strokeRect', 'clearRect', 'fill', 'stroke', 'fillText', 'strokeText',
     'putImageData', 'drawFocusIfNeeded'].forEach(function (name) {
      var orig = c2d[name];
      if (typeof orig !== 'function') return;
      c2d[name] = function () { try { gl.ops2d++; } catch (e) {} return orig.apply(this, arguments); };
    });
    var di = c2d.drawImage;
    if (typeof di === 'function') {
      c2d.drawImage = function () {
        try { gl.ops2d++; gl.images++; } catch (e) {}
        return di.apply(this, arguments);
      };
    }
  }
} catch (e) {}

/* EVERY CANVAS THE PAGE DRAWS ON - HELD WEAKLY. This list held each canvas strongly, forever:
   a page that makes a canvas per label, per sprite sheet or per read-back (the forge bench made
   about eleven a call: 792 after 70 calls) could never free one, and a WebGL canvas kept its
   context alive after its renderer was disposed. Watching a page must not keep anything alive. */
var canvasRefs = [];
var canvasSeen = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
var canvasesNow = function () {
  var out = [], keep = [];
  for (var i = 0; i < canvasRefs.length; i++) {
    var r = canvasRefs[i];
    var c = (typeof WeakRef !== 'undefined' && r instanceof WeakRef) ? r.deref() : r;
    if (c) { out.push(c); keep.push(r); }
  }
  canvasRefs = keep;
  return out;
};
try {
  var cg = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type) {
    var ctx = cg.apply(this, arguments);
    try {
      if (ctx) {
        this.__liveCtx = String(type);
        var fresh = canvasSeen ? !canvasSeen.has(this) : canvasesNow().indexOf(this) < 0;
        if (fresh) {
          if (canvasSeen) canvasSeen.add(this);
          canvasRefs.push(typeof WeakRef !== 'undefined' ? new WeakRef(this) : this);
          if (canvasRefs.length > 256 && canvasRefs.length % 256 === 1) canvasesNow();
        }
        if (!L.gpu && ctx.getExtension) {
          var dbg = ctx.getExtension('WEBGL_debug_renderer_info');
          if (dbg) L.gpu = String(ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || '');
        }
      }
    } catch (e) {}
    return ctx;
  };
} catch (e) {}

var last = 0;
var tick = function (now) {
  try {
    if (last) { var dt = now - last; if (dt > 0 && dt < 2000) put(hist.dt, dt); }
    last = now;
    put(hist.calls, gl.calls);
    put(hist.tris, Math.round(gl.tris));
    put(hist.ops2d, gl.ops2d);
    put(hist.images, gl.images);
    gl.calls = 0; gl.tris = 0; gl.ops2d = 0; gl.images = 0;
  } catch (e) {}
  requestAnimationFrame(tick);
};
try { requestAnimationFrame(tick); } catch (e) {}

/* ------------------------------------------------------------------ three
   three.js exports no global, so the only supported way in is the EventTarget
   its own Scene and WebGLRenderer dispatch an 'observe' event to. Define it
   before the bundle loads and the scene announces itself. */
var three = { renderer: null, scenes: [] };
try {
  if (!window.__THREE_DEVTOOLS__ && typeof EventTarget === 'function') {
    var tgt = new EventTarget();
    window.__THREE_DEVTOOLS__ = tgt;
    tgt.addEventListener('observe', function (e) {
      var o = e && e.detail;
      if (!o) return;
      if (o.isScene) {
        if (three.scenes.indexOf(o) < 0) three.scenes.push(o);
        /* A scene that announces itself after the sidecar was named gets the sidecar too. */
        setTimeout(function () { applySidecar(); }, 0);
      }
      else if (o.isWebGLRenderer) three.renderer = o;
    });
  }
} catch (e) {}

/* -------------------------------------------------------------- the engine
   The hard case, and the normal one. A bundled game imports its engine as an ES
   module, so there is no `window.pc` and no `window.THREE` to find — the app
   object lives in module scope where nothing outside can reach it.

   Two ways in, and neither asks the project to change.

   1. The canvas tells you WHICH engine: PlayCanvas and three.js both call
      `canvas.setAttribute('data-engine', 'PlayCanvas 2.21.3')` on themselves.
      That is detection, not access, but it is enough to name the engine and to
      say honestly that its object is out of reach.
   2. Almost every game hands ITSELF a handle for debugging — `window.game`,
      `window.__game`, `window.app`. So instead of guessing a name, diff the
      page's globals against a blank iframe's, and walk what is left looking for
      something that quacks like a scene. In this codebase that finds the app at
      `__game.renderer.app`, which no fixed list would have contained. */
var NATIVE = null;
var pageGlobals = function () {
  try {
    if (!NATIVE) {
      NATIVE = {};
      var f = document.createElement('iframe');
      f.style.display = 'none';
      (document.body || document.documentElement).appendChild(f);
      if (f.contentWindow) {
        Object.getOwnPropertyNames(f.contentWindow).forEach(function (k) { NATIVE[k] = 1; });
      }
      f.parentNode.removeChild(f);
      /* If the iframe trick was blocked, every browser builtin would look like a page
         global. The budget below still bounds it, but say so rather than pretend. */
      NATIVE.__live = NATIVE.__THREE_DEVTOOLS__ = 1;
    }
    return Object.getOwnPropertyNames(window).filter(function (k) { return !NATIVE[k]; });
  } catch (e) { return []; }
};

var found = { pc: null, pcAt: '', three: [], threeAt: '', babylon: null, babylonAt: '',
              phaser: null, phaserAt: '', pixi: null, pixiAt: '', cocos: null, cocosAt: '' };
var quacksPc = function (o) {
  try {
    return !!(o && o.root && o.root.children && o.scene && o.graphicsDevice &&
              typeof o.start === 'function');
  } catch (e) { return false; }
};
var quacksBabylon = function (o) {
  try { return !!(o && o.meshes && o.lights && o.cameras && typeof o.getEngine === 'function'); }
  catch (e) { return false; }
};
/* Phaser.Game: a scene manager holding a list of scenes, plus the canvas it drew into. */
var quacksPhaser = function (o) {
  try { return !!(o && o.scene && o.scene.scenes && o.scene.scenes.length !== undefined &&
                  o.canvas && o.renderer); }
  catch (e) { return false; }
};
/* PIXI.Application: a stage, a renderer and a ticker. */
var quacksPixi = function (o) {
  try { return !!(o && o.stage && o.stage.children && o.renderer &&
                  (o.ticker || o._ticker)); }
  catch (e) { return false; }
};
var quacksCocos = function (o) {
  try { return !!(o && typeof o.getScene === 'function' && typeof o.getWinSize === 'function'); }
  catch (e) { return false; }
};

/* ------------------------------------------------------------ saved edits
   `<project>/studio.edits.json` is what the Studio's Edit tab and its agents save: part overrides
   by stable key, the world, and the things placed that the game's code did not make. It is
   applied here with the module a shipped game imports itself (`/studio-runtime.js`), to a
   three.js scene and a PlayCanvas app alike, so a game opened live shows its saved edits with no
   line of code, and a game that has the line shows the same thing.

   This used to import forge-ops.js (285 KB of modelling tools) and call its three.js applier on
   every scene: never a PlayCanvas app, never the `placed` list.

   Named from outside once live.py knows the file's URL. Applied then, when a scene appears, when
   an engine is pinned from outside, and after an in-page reload; but once per root per version of
   the file, because applying the same file again puts back an object the player has since moved.
   The runtime itself retries what is not there yet (a part the game builds late, a clone of
   something it spawns late) for a minute. */
var SC = { url: '', studio: '', project: '', from: '', text: '', root: null, engine: '', busy: false,
           again: false, watch: 0, until: 0, tries: 0, report: null, error: '', runtime: null };
L.sidecarUrl = '';
L.sidecarApplied = 0;

/* The one root the saved edits are keyed from: a PlayCanvas app's root, else the first three.js
   scene, which is the scene the Edit tab mirrors. Exposed so page-side tools key from the same
   root the file is applied to. */
var editTarget = function () {
  try { var app = pcApp(); if (app && app.root) return { engine: 'playcanvas', target: app, root: app.root }; } catch (e) {}
  try { var s = threeScenes(); if (s && s.length) return { engine: 'three', target: s[0], root: s[0] }; } catch (e) {}
  return null;
};
L.editRoot = function () { var t = editTarget(); return t ? t.root : null; };

var nameSidecar = function (url) {
  SC.url = String(url || '');
  L.sidecarUrl = SC.url;
  var m = /^(https?:\/\/[^\/?#]+)/.exec(SC.url);
  SC.studio = m ? m[1] : '';
  SC.project = '';
  try {
    var q = new URL(SC.url).searchParams;
    /* /api/live/edits/file?project=<folder> names it; the older /api/workspace/file?path=<file>
       names the file, whose folder it is. */
    SC.project = String(q.get('project') || '') ||
      String(q.get('path') || '').replace(/[\\\/][^\\\/]*$/, '');
  } catch (e) {}
};

/* The file's text, or null. The Studio first; it answers `{text}`, and only for files inside a
   project the Studio has open. A game that is not one (a folder on the Desktop, opened by path)
   is still served by its own server, which is also where a shipped game reads this file. */
var readSidecar = function () {
  var read = function (u) {
    return fetch(u, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) return null;
      return r.json().then(function (raw) {
        if (!raw || typeof raw !== 'object') return null;
        if (typeof raw.text === 'string') return raw.text;
        return raw.parts || raw.placed || raw.world || raw.params || raw.mods || raw.verts ||
          raw.version !== undefined ? JSON.stringify(raw) : null;
      }, function () { return null; });
    }, function () { return null; });
  };
  return read(SC.url).then(function (t) {
    if (t != null) { SC.from = 'the Studio'; return t; }
    var own = '';
    try { own = new URL('studio.edits.json', location.href).href; } catch (e) {}
    return own ? read(own).then(function (t2) { if (t2 != null) SC.from = own; return t2; }) : null;
  });
};

/* The module the three.js page loaded as `three`: its import map says, else the resources it
   fetched do (a Vite dev server serves it as /node_modules/.vite/deps/three.js?v=...). */
var threeModuleUrl = function () {
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

/* A fresh query for each attempt: a module import that failed once fails for the life of the
   page under the same URL, and the file may simply not have been built yet the first time. */
var loadRuntime = function () {
  if (!SC.runtime) {
    SC.runtime = import(SC.studio + '/studio-runtime.js?v=' + L.born + '-' + (SC.tries++))
      .catch(function (e) { SC.runtime = null; throw e; });
  }
  return SC.runtime;
};

var runtimeOpts = function (er, edits) {
  var o = { retryMs: 60000, onSettle: function (r) {
    try { if (r && r.errors && r.errors.length) console.warn('[studio] saved edits: ' + r.errors.join('; ')); } catch (e) {}
  } };
  if (SC.studio && SC.project) {
    /* Models through the Studio, which hands a file back with nothing left in it that needs a
       decoder: Draco and meshopt are set up by a game at its start-up, and this is not that. */
    o.url = function (p, kind) {
      return kind === 'model' ? SC.studio + '/api/engine/model?project=' + encodeURIComponent(SC.project) +
        '&file=' + encodeURIComponent(p) : '';
    };
  }
  var wantsLoader = er.engine === 'three' && (edits.placed || []).some(function (it) {
    return it && it.ref && it.ref.kind === 'model';
  });
  var tu = wantsLoader ? threeModuleUrl() : '';
  if (!tu || !SC.studio) return Promise.resolve(o);
  /* The Studio's glTF loader, bound to the page's OWN three by URL: module identity is the URL, and
     a loader on a second copy of three makes meshes this page's renderer refuses. */
  return import(SC.studio + '/api/engine/loader?project=' + encodeURIComponent(SC.project) +
                '&three=' + encodeURIComponent(tu))
    .then(function (m) { o.GLTFLoader = m.GLTFLoader; return o; }, function () { return o; });
};

var applySidecar = function () {
  if (!SC.url) return;
  if (SC.busy) { SC.again = true; return; }
  var er = editTarget();
  if (!er) { watchForRoot(); return; }
  SC.busy = true;
  readSidecar().then(function (text) {
    if (text == null) { SC.error = 'no saved edits could be read from ' + SC.url; return; }
    if (er.root === SC.root && text === SC.text) return;
    var edits = JSON.parse(text);
    return loadRuntime().then(function (R) {
      return runtimeOpts(er, edits).then(function (o) { return R.applyStudioEdits(er.target, edits, o); });
    }, function (e) {
      /* No runtime on this Studio (never built): the three.js parts at least, the old way. */
      if (er.engine !== 'three') throw e;
      return import(SC.studio + '/forge-ops.js').then(function (m) {
        var r = m.applyEdits(er.root, edits);
        return { parts: r.parts, placed: 0, pending: 0, missing: r.missing, errors: r.errors.concat(
          ['the Studio has no /studio-runtime.js (npm run build:runtime): parts applied, placements not']) };
      });
    }).then(function (rep) {
      SC.root = er.root; SC.text = text; SC.engine = er.engine; SC.report = rep; SC.error = '';
      L.sidecarApplied += 1;
      try {
        console.info('[studio] saved edits applied to the ' + er.engine + ' scene: ' + rep.parts + ' parts, ' +
                     rep.placed + ' placed' + (rep.pending ? ', ' + rep.pending + ' waiting' : '') +
                     (rep.missing && rep.missing.length ? ', not found: ' + rep.missing.slice(0, 8).join(', ') : '') +
                     (SC.from === 'the Studio' ? '' : ' (read from the game\'s own server: the Studio only serves ' +
                      'files inside a project it has open)'));
      } catch (e) {}
    });
  }).catch(function (e) {
    SC.error = String((e && e.message) || e).slice(0, 300);
  }).then(function () {
    SC.busy = false;
    if (SC.again) { SC.again = false; applySidecar(); }
  });
};

/* Named before the game has an engine to apply to: look once a second, for a minute. */
var watchForRoot = function () {
  if (SC.watch) return;
  SC.until = Date.now() + 60000;
  SC.watch = setInterval(function () {
    if (editTarget()) { clearInterval(SC.watch); SC.watch = 0; applySidecar(); }
    else if (Date.now() > SC.until) {
      clearInterval(SC.watch); SC.watch = 0;
      SC.error = 'no three.js scene or PlayCanvas app appeared within 60 s to apply the saved edits to';
    }
  }, 1000);
};

L.sidecar = function (url) {
  nameSidecar(url);
  /* Kept for this tab, so a reload the game does itself (a dev server's full reload) comes back
     with its edits too, not only a reload the Studio asked for. */
  try { if (window.top === window) sessionStorage.setItem('__studioSidecar', SC.url); } catch (e) {}
  applySidecar();
  return true;
};
L.sidecarState = function () {
  var t = editTarget();
  return { url: SC.url, read_from: SC.from, project: SC.project, applied: L.sidecarApplied,
           engine: SC.engine || (t ? t.engine : ''), waiting_for_engine: !!SC.watch, busy: SC.busy,
           error: SC.error, report: SC.report };
};
try {
  var resumed = window.top === window ? sessionStorage.getItem('__studioSidecar') : '';
  if (resumed) { nameSidecar(resumed); setTimeout(applySidecar, 0); }
} catch (e) {}

/* Pinned from outside. The page cannot always find its own engine — Phaser and PixiJS put their
   CLASS on window but not the instance, so nothing here can walk to it. The Python side can, with
   a heap query over the class prototype, and hands the result back through this. */
L.pin = function (o, kind) {
  try {
    if (!o) return false;
    if (kind === 'phaser' && !quacksPhaser(o)) return false;
    if (kind === 'pixi' && !quacksPixi(o)) return false;
    if (kind === 'playcanvas' && !quacksPc(o)) return false;
    if (kind === 'babylon' && !quacksBabylon(o)) return false;
    if (kind === 'three' && !o.isScene) return false;
    L.pinned = o;
    L.pinnedKind = kind;
    if (kind === 'three' && found.three.indexOf(o) < 0) found.three.push(o);
    /* An engine the page could not find alone is one the saved edits could not reach either. */
    if (SC.url && (kind === 'three' || kind === 'playcanvas')) setTimeout(applySidecar, 0);
    return true;
  } catch (e) { return false; }
};

var hunt = function () {
  if (found.pc || found.three.length || found.babylon || found.phaser || found.pixi) return found;
  var seen = typeof Set === 'function' ? new Set() : null;
  var list = [];
  var budget = 4000;                      /* bounded: this runs on a live frame budget */
  var walk = function (o, path, depth) {
    if (budget-- < 0 || o == null || depth > 4) return;
    var t = typeof o;
    if (t !== 'object' && t !== 'function') return;
    if (seen) { if (seen.has(o)) return; seen.add(o); }
    else { if (list.indexOf(o) >= 0) return; list.push(o); }
    try {
      if (!found.pc && quacksPc(o)) { found.pc = o; found.pcAt = path; return; }
      if (o.isScene) {
        if (found.three.indexOf(o) < 0) found.three.push(o);
        if (!found.threeAt) found.threeAt = path;
        return;
      }
      if (!found.babylon && quacksBabylon(o)) { found.babylon = o; found.babylonAt = path; return; }
      if (!found.phaser && quacksPhaser(o)) { found.phaser = o; found.phaserAt = path; return; }
      if (!found.pixi && quacksPixi(o)) { found.pixi = o; found.pixiAt = path; return; }
      if (!found.cocos && quacksCocos(o)) { found.cocos = o; found.cocosAt = path; return; }
    } catch (e) {}
    var keys;
    try { keys = Object.keys(o); } catch (e) { return; }
    for (var i = 0; i < keys.length && i < 80; i++) {
      var v;
      try { v = o[keys[i]]; } catch (e) { continue; }
      walk(v, path + step(keys[i]), depth + 1);
    }
  };
  var gs = pageGlobals();
  for (var g = 0; g < gs.length && g < 80; g++) {
    var v;
    try { v = window[gs[g]]; } catch (e) { continue; }
    walk(v, gs[g], 0);
  }
  return found;
};

var pcApp = function () {
  /* Pinned first. The closure hunt and the heap query hand the app in through L.pin, and a scene
     or find that then went looking on `window.pc` again reported an empty tree beside
     `reachable: true` — true, and useless. */
  try { if (L.pinned && L.pinnedKind === 'playcanvas' && quacksPc(L.pinned)) return L.pinned; } catch (e) {}
  try {
    var pc = window.pc;
    if (pc) {
      if (pc.app && pc.app.root) return pc.app;
      var names = ['AppBase', 'Application'];
      for (var i = 0; i < names.length; i++) {
        var C = pc[names[i]];
        if (C && typeof C.getApplication === 'function') {
          var a = C.getApplication();
          if (a && a.root) return a;
        }
        if (C && C._applications) {
          for (var k in C._applications) {
            if (C._applications[k] && C._applications[k].root) return C._applications[k];
          }
        }
      }
    }
  } catch (e) {}
  return hunt().pc;
};
/* The forge builds its bench with `new Scene()` in this same page, and three announces EVERY scene
   it constructs — the announcement fires inside the constructor, before the forge can mark it. Left
   in, the bench became scenes[0]: the edit root, the scene the saved edits were applied to, and
   what /api/live/objects listed. So the mark is read here, when the list is asked for. */
var notForge = function (s) {
  try { return !(s && s.userData && s.userData.__studioForge); } catch (e) { return true; }
};
var threeScenes = function () {
  var own = three.scenes.filter(notForge);
  if (own.length) return own;
  return (hunt().three || []).filter(notForge);
};
var babylonScenes = function () {
  try {
    var B = window.BABYLON;
    if (B && B.EngineStore && B.EngineStore.Instances) {
      var out = [];
      B.EngineStore.Instances.forEach(function (eng) {
        (eng.scenes || []).forEach(function (s) { out.push(s); });
      });
      if (out.length) return out;
    }
  } catch (e) {}
  var b = hunt().babylon;
  return b ? [b] : [];
};

/* The engine names itself on its own canvas. Detection with zero cooperation. */
var engineTag = function () {
  try {
    var cvs = canvasesNow();
    for (var i = 0; i < cvs.length; i++) {
      var t = cvs[i].getAttribute && cvs[i].getAttribute('data-engine');
      if (t) return String(t);
    }
    var c = document.querySelector('canvas[data-engine]');
    if (c) return String(c.getAttribute('data-engine'));
  } catch (e) {}
  return '';
};

/* Third route: the engine says its own name into the console on boot.
   Phaser prints "Phaser v3.90.0 (WebGL | Web Audio)" and PixiJS 7 prints "PixiJS 7.4.0". The
   console wrapper above is already recording every line, so this is free — and it is the only
   detection that works for an engine that neither tags the canvas nor exports a global. */
var BANNERS = [
  [/phaser\s*v?\s*([0-9][\w.\-]*)/i, 'phaser', 'Phaser'],
  [/pixi\.?js\s*v?\s*([0-9][\w.\-]*)/i, 'pixi', 'PixiJS'],
  [/cocos\s*(?:creator|2d)?\s*v?\s*([0-9][\w.\-]*)/i, 'cocos', 'Cocos'],
  [/melonjs\s*v?\s*([0-9][\w.\-]*)/i, 'melonjs', 'melonJS'],
  [/excalibur\s*v?\s*([0-9][\w.\-]*)/i, 'excalibur', 'Excalibur']
];
var bannerEngine = function () {
  try {
    for (var i = 0; i < L.errors.length; i++) {
      var msg = String(L.errors[i].msg || '');
      for (var b = 0; b < BANNERS.length; b++) {
        var m = msg.match(BANNERS[b][0]);
        if (m) return { kind: BANNERS[b][1], tag: BANNERS[b][2] + ' ' + m[1] };
      }
    }
  } catch (e) {}
  return null;
};

/* Fourth route, for the engines that cannot be read at all. Unity, Godot and Defold compile to
   WebAssembly and keep their scene inside linear memory, where no JavaScript can walk it. Saying
   "unknown engine" would be a lie; naming it and saying what DOES work is the honest answer. */
var wasmEngine = function () {
  try {
    if (window.unityInstance || window.createUnityInstance ||
        document.querySelector('#unity-canvas, #unityContainer')) {
      return { kind: 'unity',
               tag: 'Unity WebGL',
               hint: 'Unity keeps its scene inside WebAssembly memory, so the tree cannot be read ' +
                     'from JavaScript by anything. What does work: console, perf, input, shot, and ' +
                     'eval — including unityInstance.SendMessage("Object","Method","arg"), which is ' +
                     'the supported way to drive a Unity build from the page.' };
    }
    var res = (performance.getEntriesByType && performance.getEntriesByType('resource')) || [];
    for (var i = 0; i < res.length; i++) {
      var n = String(res[i].name || '');
      if (n.slice(-5).toLowerCase() === '.wasm') {
        var file = n.split('/').pop();
        var low = file.toLowerCase();
        var name = low.indexOf('godot') >= 0 ? 'Godot'
                 : low.indexOf('dmengine') >= 0 || low.indexOf('defold') >= 0 ? 'Defold'
                 : 'a WebAssembly engine';
        return { kind: 'wasm', tag: name + ' (' + file + ')',
                 hint: name + ' compiles to WebAssembly and keeps its scene in linear memory, ' +
                       'which no JavaScript can walk — so there is no scene tree to read, from ' +
                       'here or from any other tool. Console, perf, input, shot and eval all work.' };
      }
    }
  } catch (e) {}
  return null;
};

var UNREACHED = 'is running, but its instance is not reachable from the page and no heap query ' +
                'found one either. Everything except the scene tree still works, and eval works ' +
                'on whatever the game does expose. One dev-only line where the game is created ' +
                'unlocks the rest: window.__game = { app };';

L.reach = function () {
  var tag = engineTag();
  /* Pinned first: it was handed in from outside precisely because nothing here could find it. */
  if (L.pinned && L.pinnedKind) {
    var alive = (L.pinnedKind === 'phaser' && quacksPhaser(L.pinned)) ||
                (L.pinnedKind === 'pixi' && quacksPixi(L.pinned)) ||
                (L.pinnedKind === 'playcanvas' && quacksPc(L.pinned)) ||
                (L.pinnedKind === 'babylon' && quacksBabylon(L.pinned)) ||
                (L.pinnedKind === 'three' && L.pinned.isScene);
    if (alive) {
      var pinTag = tag || (bannerEngine() || {}).tag || '';
      return { engine: L.pinnedKind, reachable: true, at: '__live.pinned', tag: pinTag };
    }
  }
  if (pcApp()) return { engine: 'playcanvas', reachable: true, at: found.pcAt || 'pc.app', tag: tag };
  if (threeScenes().length) {
    return { engine: 'three', reachable: true, at: found.threeAt || '__live.scenes()[0]', tag: tag };
  }
  if (babylonScenes().length) {
    return { engine: 'babylon', reachable: true,
             at: found.babylonAt || 'BABYLON.EngineStore', tag: tag };
  }
  var h = hunt();
  if (h.phaser) return { engine: 'phaser', reachable: true, at: h.phaserAt,
                         tag: tag || (bannerEngine() || {}).tag || '' };
  if (h.pixi) return { engine: 'pixi', reachable: true, at: h.pixiAt,
                       tag: tag || (bannerEngine() || {}).tag || '' };
  if (h.cocos) return { engine: 'cocos', reachable: true, at: h.cocosAt, tag: tag };

  var wasm = wasmEngine();
  if (wasm) return { engine: wasm.kind, reachable: false, at: '', tag: wasm.tag, hint: wasm.hint };

  var low = tag.toLowerCase();
  if (low) {
    var name = low.indexOf('playcanvas') >= 0 ? 'playcanvas'
             : low.indexOf('three') >= 0 ? 'three'
             : low.indexOf('babylon') >= 0 ? 'babylon' : 'webgl';
    return { engine: name, reachable: false, at: '', tag: tag, hint: tag + ' ' + UNREACHED };
  }
  var ban = bannerEngine();
  if (ban) return { engine: ban.kind, reachable: false, at: '', tag: ban.tag,
                    hint: ban.tag + ' ' + UNREACHED };

  var cvs2 = canvasesNow();
  for (var i = 0; i < cvs2.length; i++) {
    if (String(cvs2[i].__liveCtx || '').indexOf('webgl') === 0) {
      return { engine: 'webgl', reachable: false, at: '', tag: '' };
    }
  }
  return { engine: cvs2.length ? 'canvas2d' : 'dom', reachable: false, at: '', tag: '' };
};
L.engine = function () { return L.reach().engine; };
/* Which class prototypes the Python side should try a heap query on, given what IS on window.
   Nothing is guessed: each entry is only offered when the engine's own namespace is present. */
L.probes = function () {
  var out = [];
  try {
    if (window.Phaser && window.Phaser.Game) out.push(['phaser', 'Phaser.Game.prototype']);
    if (window.PIXI && window.PIXI.Application) out.push(['pixi', 'PIXI.Application.prototype']);
    if (window.pc && (window.pc.AppBase || window.pc.Application)) {
      out.push(['playcanvas', '(pc.AppBase||pc.Application).prototype']);
    }
    if (window.THREE && window.THREE.Scene) out.push(['three', 'THREE.Scene.prototype']);
    if (window.BABYLON && window.BABYLON.Scene) out.push(['babylon', 'BABYLON.Scene.prototype']);
  } catch (e) {}
  return out;
};

/* The last resort, and the reason an engine nobody wrote an adapter for is not a dead end: almost
   every scene graph in existence is "an object with a name and a list of children". melonJS,
   Excalibur, Kaboom, Cocos and a hand-rolled engine all fit, so walk it generically rather than
   answer "unsupported". */
var treeNode = function (o, depth, wide) {
  var n = { name: o.name || o.label || o._name || '', type: ctorName(o) };
  try {
    if (!n.name) delete n.name;
    var p = o.position || o._position || (typeof o.x === 'number' ? { x: o.x, y: o.y, z: o.z } : null);
    if (p && typeof p.x === 'number') {
      n.pos = p.z === undefined ? [r3(p.x), r3(p.y)] : [r3(p.x), r3(p.y), r3(p.z)];
    }
    if (o.visible === false || o.active === false || o.enabled === false) n.visible = false;
    if (typeof o.alpha === 'number' && o.alpha < 1) n.alpha = r3(o.alpha);
    var kids = o.children || o._children || [];
    if (kids.length) {
      if (depth > 0) {
        n.children = [];
        for (var i = 0; i < kids.length && i < wide; i++) {
          n.children.push(treeNode(kids[i], depth - 1, wide));
        }
        if (kids.length > wide) n.more_children = kids.length - wide;
      } else {
        n.children_count = kids.length;
      }
    }
  } catch (e) { n.error = String(e).slice(0, 160); }
  return n;
};

var engineObj = function (kind) {
  if (L.pinned && L.pinnedKind === kind) return L.pinned;
  var h = hunt();
  if (kind === 'phaser') return h.phaser;
  if (kind === 'pixi') return h.pixi;
  if (kind === 'cocos') return h.cocos;
  return null;
};

/* --------------------------------------------------------- HTML5 2D engines
   Phaser and PixiJS between them are most of the HTML5 games in existence, and neither answers
   the questions the 3D adapters answer unless someone writes this down: what is on screen, where,
   which texture it uses, and whether it is even visible. `visible:false` and `alpha:0` are the
   two commonest reasons a sprite "did not appear", and neither shows in a screenshot. */
var phaserNode = function (o, depth, wide) {
  var n = { name: o.name || '', type: o.type || ctorName(o) };
  try {
    if (!n.name) delete n.name;
    n.pos = [r3(o.x), r3(o.y)];
    if (o.visible === false) n.visible = false;
    if (typeof o.alpha === 'number' && o.alpha < 1) n.alpha = r3(o.alpha);
    if (o.active === false) n.active = false;
    if (o.depth) n.depth = o.depth;
    if (typeof o.scaleX === 'number' && (r3(o.scaleX) !== 1 || r3(o.scaleY) !== 1)) {
      n.scale = [r3(o.scaleX), r3(o.scaleY)];
    }
    if (typeof o.rotation === 'number' && r3(o.rotation)) n.rotation = r3(o.rotation);
    if (o.texture && o.texture.key) n.texture = String(o.texture.key);
    if (o.frame && o.frame.name != null && String(o.frame.name) !== '__BASE') {
      n.frame = String(o.frame.name);
    }
    if (typeof o.text === 'string') n.text = o.text.slice(0, 60);
    if (o.anims && o.anims.currentAnim) n.anim = o.anims.currentAnim.key;
    if (typeof o.width === 'number') n.size = [Math.round(o.width), Math.round(o.height)];
    var kids = o.list || (o.getChildren && o.getChildren());       /* Container, Group */
    if (kids && kids.length) {
      if (depth > 0) {
        n.children = [];
        for (var i = 0; i < kids.length && i < wide; i++) {
          n.children.push(phaserNode(kids[i], depth - 1, wide));
        }
        if (kids.length > wide) n.more_children = kids.length - wide;
      } else {
        n.children_count = kids.length;
      }
    }
  } catch (e) { n.error = String(e).slice(0, 160); }
  return n;
};
var phaserScene = function (game, depth, wide, out) {
  try {
    out.settings = {
      size: [game.config.width, game.config.height],
      renderer: game.renderer && game.renderer.gl ? 'webgl' : 'canvas',
      background: game.config.backgroundColor && hexOf({
        r: game.config.backgroundColor.redGL, g: game.config.backgroundColor.greenGL,
        b: game.config.backgroundColor.blueGL }),
      fps: game.loop ? r3(game.loop.actualFps) : null,
      textures: game.textures && game.textures.list ? Object.keys(game.textures.list).length : null,
      running: game.isRunning !== false
    };
    var scenes = game.scene.scenes || [];
    out.scenes = scenes.length;
    out.root = { name: 'scenes', children: [] };
    for (var i = 0; i < scenes.length && i < wide; i++) {
      var s = scenes[i];
      var row = { name: (s.scene && s.scene.key) || ('scene' + i), type: 'Scene' };
      try {
        row.active = !!(s.scene && s.scene.isActive && s.scene.isActive());
        row.visible = !!(s.scene && s.scene.isVisible && s.scene.isVisible());
        var cam = s.cameras && s.cameras.main;
        if (cam) row.camera = { scroll: [r3(cam.scrollX), r3(cam.scrollY)], zoom: r3(cam.zoom) };
        var list = (s.children && s.children.list) || [];
        row.objects = list.length;
        if (depth > 0) {
          row.children = [];
          for (var j = 0; j < list.length && j < wide; j++) {
            row.children.push(phaserNode(list[j], depth - 1, wide));
          }
          if (list.length > wide) row.more_children = list.length - wide;
        }
      } catch (e) { row.error = String(e).slice(0, 160); }
      out.root.children.push(row);
    }
  } catch (err) { out.error = String(err).slice(0, 300); }
};

/* A production PixiJS build is minified, so the class name is `dr` or `Yi` and tells an agent
   nothing. Pixi labels its own objects instead: v8 carries `renderPipeId` ('sprite', 'graphics',
   'text'), and the older flags cover v7. Fall back to the class name only when neither answers. */
var pixiKind = function (o) {
  try {
    if (typeof o.text === 'string') return 'text';
    if (o.renderPipeId) return String(o.renderPipeId);
    if (o.isSprite) return 'sprite';
    if (o.geometry || o.context) return 'graphics';
    if (o.children) return 'container';
  } catch (e) {}
  return ctorName(o) || 'object';
};
var pixiNode = function (o, depth, wide) {
  var n = { name: o.label || o.name || '', type: pixiKind(o) };
  try {
    if (!n.name) delete n.name;
    n.pos = [r3(o.x), r3(o.y)];
    if (o.visible === false) n.visible = false;
    if (o.renderable === false) n.renderable = false;
    if (typeof o.alpha === 'number' && o.alpha < 1) n.alpha = r3(o.alpha);
    if (o.zIndex) n.zIndex = o.zIndex;
    if (o.scale && (r3(o.scale.x) !== 1 || r3(o.scale.y) !== 1)) {
      n.scale = [r3(o.scale.x), r3(o.scale.y)];
    }
    if (typeof o.rotation === 'number' && r3(o.rotation)) n.rotation = r3(o.rotation);
    if (typeof o.text === 'string') n.text = o.text.slice(0, 60);
    if (o.texture) {
      /* v8 puts a label on the texture; v7 keeps the URL on the base texture's resource. Only
         report a texture that has a real identity — a v8 Graphics carries the shared white pixel,
         and printing "texture" on every shape says nothing and looks like an answer. */
      var t = o.texture;
      var tn = t.label || (t.textureCacheIds && t.textureCacheIds[0]) ||
               (t.baseTexture && t.baseTexture.resource && t.baseTexture.resource.url) || '';
      if (tn && tn !== 'WHITE' && tn !== 'EMPTY') {
        n.texture = String(tn);
        if (t.width) n.texture_size = [Math.round(t.width), Math.round(t.height)];
      }
    }
    if (o.filters && o.filters.length) n.filters = o.filters.length;
    /* Only when it is not the default — every object reporting "inherit" is noise on every row. */
    if (o.blendMode && o.blendMode !== 'inherit' && o.blendMode !== 'normal') n.blend = o.blendMode;
    var kids = o.children || [];
    if (kids.length) {
      if (depth > 0) {
        n.children = [];
        for (var i = 0; i < kids.length && i < wide; i++) {
          n.children.push(pixiNode(kids[i], depth - 1, wide));
        }
        if (kids.length > wide) n.more_children = kids.length - wide;
      } else {
        n.children_count = kids.length;
      }
    }
  } catch (e) { n.error = String(e).slice(0, 160); }
  return n;
};
var pixiScene = function (app, depth, wide, out) {
  try {
    var r = app.renderer || {};
    var bg = r.background || {};
    out.settings = {
      size: [Math.round(r.width || 0), Math.round(r.height || 0)],
      renderer: String(r.name || r.type || '?'),
      resolution: r3(r.resolution),
      background: (bg.color && bg.color.toHex && bg.color.toHex()) ||
                  (typeof r.backgroundColor === 'number'
                    ? '#' + r.backgroundColor.toString(16) : null),
      ticker_fps: app.ticker ? r3(app.ticker.FPS) : null
    };
    out.root = pixiNode(app.stage, depth, wide);
    out.root.name = out.root.name || 'stage';
  } catch (err) { out.error = String(err).slice(0, 300); }
};

/* ------------------------------------------------------------------- scene */
var pcMat = function (m) {
  if (!m) return null;
  var o = { name: m.name || '(unnamed)' };
  try {
    if (m.diffuse) o.diffuse = hexOf(m.diffuse);
    if (m.emissive) o.emissive = hexOf(m.emissive);
    if (typeof m.opacity === 'number' && m.opacity < 1) o.opacity = r3(m.opacity);
    if (typeof m.gloss === 'number') o.gloss = r3(m.gloss);
    if (typeof m.metalness === 'number') o.metalness = r3(m.metalness);
    if (m.blendType) o.blend = m.blendType;
    if (m.useLighting === false) o.unlit = true;
    var maps = [];
    ['diffuseMap', 'normalMap', 'emissiveMap', 'opacityMap', 'aoMap', 'glossMap',
     'metalnessMap', 'sphereMap', 'lightMap'].forEach(function (k) { if (m[k]) maps.push(k); });
    if (maps.length) o.maps = maps;
  } catch (e) {}
  return o;
};
var pcNode = function (e, depth, wide) {
  var o = { name: e.name || '(unnamed)' };
  try {
    if (e.enabled === false) o.enabled = false;
    var p = e.getPosition && e.getPosition();
    if (p) o.pos = [r3(p.x), r3(p.y), r3(p.z)];
    var s = e.getLocalScale && e.getLocalScale();
    if (s && (r3(s.x) !== 1 || r3(s.y) !== 1 || r3(s.z) !== 1)) o.scale = [r3(s.x), r3(s.y), r3(s.z)];
    var comps = [];
    ['render', 'model', 'light', 'camera', 'script', 'collision', 'rigidbody',
     'anim', 'animation', 'sound', 'element', 'particlesystem', 'sprite'].forEach(function (k) {
      if (e[k]) comps.push(k);
    });
    if (comps.length) o.components = comps;
    var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
    if (mi.length) {
      o.meshes = mi.length;
      var seenMats = [];
      for (var i = 0; i < mi.length && seenMats.length < 4; i++) {
        var mm = pcMat(mi[i] && mi[i].material);
        if (mm && seenMats.every(function (x) { return x.name !== mm.name; })) seenMats.push(mm);
      }
      o.materials = seenMats;
    }
    if (e.light) o.light = { type: e.light.type, intensity: r3(e.light.intensity),
                             color: hexOf(e.light.color), castShadows: !!e.light.castShadows };
    if (e.camera) o.camera = { fov: r3(e.camera.fov), near: r3(e.camera.nearClip),
                               far: r3(e.camera.farClip) };
    if (e.script && e.script.scripts) {
      o.scripts = e.script.scripts.slice(0, 8).map(function (s) {
        return (s && (s.__scriptType && s.__scriptType.__name)) || ctorName(s) || '?';
      });
    }
    var kids = e.children || [];
    if (depth > 0 && kids.length) {
      o.children = [];
      for (var j = 0; j < kids.length && j < wide; j++) o.children.push(pcNode(kids[j], depth - 1, wide));
      if (kids.length > wide) o.more_children = kids.length - wide;
    } else if (kids.length) {
      o.children_count = kids.length;
    }
  } catch (err) { o.error = String(err).slice(0, 200); }
  return o;
};

var threeMat = function (m) {
  if (!m) return null;
  if (Array.isArray(m)) return m.slice(0, 3).map(threeMat);
  var o = { name: m.name || '(unnamed)', type: ctorName(m) };
  try {
    if (m.color) o.color = hexOf(m.color);
    if (m.emissive) o.emissive = hexOf(m.emissive);
    if (typeof m.roughness === 'number') o.roughness = r3(m.roughness);
    if (typeof m.metalness === 'number') o.metalness = r3(m.metalness);
    if (typeof m.opacity === 'number' && m.opacity < 1) o.opacity = r3(m.opacity);
    if (m.transparent) o.transparent = true;
    if (m.wireframe) o.wireframe = true;
    var maps = [];
    ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'aoMap', 'emissiveMap',
     'alphaMap', 'envMap', 'displacementMap'].forEach(function (k) { if (m[k]) maps.push(k); });
    if (maps.length) o.maps = maps;
  } catch (e) {}
  return o;
};
var threeNode = function (n, depth, wide) {
  var o = { name: n.name || '(unnamed)', type: ctorName(n) };
  try {
    if (!n.visible) o.visible = false;
    if (n.position) o.pos = [r3(n.position.x), r3(n.position.y), r3(n.position.z)];
    if (n.scale && (r3(n.scale.x) !== 1 || r3(n.scale.y) !== 1 || r3(n.scale.z) !== 1)) {
      o.scale = [r3(n.scale.x), r3(n.scale.y), r3(n.scale.z)];
    }
    if (n.material) o.material = threeMat(n.material);
    if (n.geometry) {
      var g = n.geometry;
      o.geometry = { type: ctorName(g) };
      try {
        if (g.attributes && g.attributes.position) o.geometry.vertices = g.attributes.position.count;
        if (g.index) o.geometry.indices = g.index.count;
      } catch (e) {}
    }
    if (n.isLight) o.light = { intensity: r3(n.intensity), color: hexOf(n.color) };
    if (n.isCamera) o.camera = { fov: r3(n.fov), near: r3(n.near), far: r3(n.far) };
    var kids = n.children || [];
    if (depth > 0 && kids.length) {
      o.children = [];
      for (var i = 0; i < kids.length && i < wide; i++) o.children.push(threeNode(kids[i], depth - 1, wide));
      if (kids.length > wide) o.more_children = kids.length - wide;
    } else if (kids.length) {
      o.children_count = kids.length;
    }
  } catch (err) { o.error = String(err).slice(0, 200); }
  return o;
};

L.scene = function (opts) {
  opts = opts || {};
  var depth = opts.depth == null ? 3 : Math.max(0, Math.min(12, opts.depth | 0));
  var wide = opts.wide == null ? 40 : Math.max(1, Math.min(400, opts.wide | 0));
  var reach = L.reach();
  var eng = reach.engine;
  var out = { engine: eng, reachable: reach.reachable, url: location.href, title: document.title };
  if (reach.at) out.found_at = reach.at;
  if (reach.tag) out.engine_version = reach.tag;
  if (reach.hint) out.hint = reach.hint;
  try {
    /* This game creates 45 canvases — it draws its own gradients and sprite sheets into
       offscreen 2D ones. Listing all of them buried the answer, so: the drawing surfaces
       first, then the largest few of the rest, then a count. */
    var cvs3 = canvasesNow();
    var shown = cvs3.filter(function (c) {
      return String(c.__liveCtx || '').indexOf('webgl') === 0;
    });
    var rest = cvs3.filter(function (c) { return shown.indexOf(c) < 0; })
      .sort(function (a, b) { return (b.width * b.height) - (a.width * a.height); }).slice(0, 4);
    out.canvases = { total: cvs3.length, shown: shown.concat(rest).map(function (c) {
      return { ctx: c.__liveCtx || '?', w: c.width, h: c.height,
               css: [Math.round(c.clientWidth), Math.round(c.clientHeight)],
               onscreen: c.clientWidth > 0 };
    }) };
  } catch (e) {}
  try {
    if (eng === 'playcanvas') {
      var app = pcApp();
      var sc = app.scene || {};
      out.settings = { ambient: hexOf(sc.ambientLight), fog: sc.fog || 'none',
                       skyboxIntensity: r3(sc.skyboxIntensity),
                       exposure: r3(sc.exposure), toneMapping: sc.toneMapping,
                       gammaCorrection: sc.gammaCorrection };
      out.root = pcNode(opts.root ? (L.node(opts.root) || app.root) : app.root, depth, wide);
    } else if (eng === 'three') {
      var all = threeScenes();
      out.scenes = all.length;
      var target = all[opts.index || 0];
      if (target) {
        out.settings = { background: target.background && hexOf(target.background),
                         fog: target.fog ? ctorName(target.fog) : 'none',
                         environment: !!target.environment };
        out.root = threeNode(target, depth, wide);
      }
      if (three.renderer && three.renderer.info) {
        var inf = three.renderer.info;
        out.renderer = { calls: inf.render && inf.render.calls, tris: inf.render && inf.render.triangles,
                         geometries: inf.memory && inf.memory.geometries,
                         textures: inf.memory && inf.memory.textures };
      }
    } else if (eng === 'phaser' && engineObj('phaser')) {
      phaserScene(engineObj('phaser'), depth, wide, out);
    } else if (eng === 'pixi' && engineObj('pixi')) {
      pixiScene(engineObj('pixi'), depth, wide, out);
    } else if (eng === 'cocos' && engineObj('cocos')) {
      /* Untested against a real Cocos build — the walk is duck-typed on `name`/`children`, which
         has been stable across Cocos 2 and 3, and it degrades to an empty tree rather than an
         error if that ever stops being true. */
      var cs = engineObj('cocos').getScene && engineObj('cocos').getScene();
      out.root = cs ? treeNode(cs, depth, wide) : { note: 'no active Cocos scene' };
    } else if (eng === 'babylon') {
      var bs = babylonScenes()[opts.index || 0];
      if (bs) {
        out.root = {
          meshes: (bs.meshes || []).slice(0, wide).map(function (m) {
            return { name: m.name, visible: m.isVisible, verts: m.getTotalVertices && m.getTotalVertices(),
                     material: m.material && m.material.name };
          }),
          lights: (bs.lights || []).map(function (l) { return { name: l.name, type: ctorName(l) }; }),
          cameras: (bs.cameras || []).map(function (c) { return { name: c.name, type: ctorName(c) }; }),
          counts: { meshes: (bs.meshes || []).length, materials: (bs.materials || []).length }
        };
      }
    } else {
      /* No engine: report the page itself, which is what a 2D or DOM game is. */
      var els = [];
      var all = document.body ? document.body.querySelectorAll('[id],[data-testid],canvas,button') : [];
      for (var i = 0; i < all.length && i < wide; i++) {
        var el = all[i];
        /* Keys are added, never set to undefined: the value flattener renders an undefined as
           the string "[undefined]", which then looks like a value that is there. */
        var row = { tag: el.tagName.toLowerCase() };
        if (el.id) row.id = el.id;
        var txt = (el.textContent || '').trim().slice(0, 40);
        if (txt) row.text = txt;
        if (el.tagName === 'CANVAS') row.size = [el.width, el.height];
        els.push(row);
      }
      out.root = { elements: els, total: all.length };
    }
  } catch (err) { out.error = String(err).slice(0, 400); }
  return out;
};

/* Resolve a dotted path against window, so `--data-urlencode 'root=app.root.children.2'`
   and `js=app.scene.ambientLight` mean the same thing to an agent. */
L.node = function (path) {
  try {
    var cur = window;
    /* Splitting on "." alone cannot read back the paths this file hands out, because they carry
       array indices: `__game.renderer.app.root.children[2]`. Take every run of non-separator
       characters instead, so both forms resolve. */
    var parts = String(path).match(/[^.\[\]"']+/g) || [];
    for (var i = 0; i < parts.length; i++) {
      cur = cur[parts[i]];
      if (cur == null) return null;
    }
    return cur;
  } catch (e) { return null; }
};

/* "Where is the player?" — the question that otherwise costs three round trips. */
L.find = function (q, limit) {
  limit = Math.max(1, Math.min(200, limit || 25));
  var needle = String(q || '').toLowerCase();
  var out = [];
  var reach = L.reach();
  var eng = reach.engine;
  /* `kidKey` is not decoration: a Phaser Container keeps its children on `.list`, not
     `.children`, so a path built with the wrong key resolves to undefined when pasted back. */
  var visit = function (node, path, kids, name, kidKey, kind) {
    if (out.length >= limit) return;
    var nm = String(name(node) || '');
    if (needle && nm.toLowerCase().indexOf(needle) >= 0) {
      var row = { path: path, name: nm,
                  type: kind ? kind(node) : (node.type || ctorName(node)) };
      try {
        var p = node.getPosition ? node.getPosition() : node.position;
        if (p && typeof p.x === 'number') {
          row.pos = p.z === undefined ? [r3(p.x), r3(p.y)] : [r3(p.x), r3(p.y), r3(p.z)];
        } else if (typeof node.x === 'number') {
          row.pos = [r3(node.x), r3(node.y)];        /* 2D engines put x/y on the object itself */
        }
        if (node.enabled === false || node.visible === false || node.active === false) {
          row.hidden = true;
        }
        if (typeof node.alpha === 'number' && node.alpha === 0) row.hidden = true;
        if (node.texture && node.texture.key) row.texture = String(node.texture.key);
      } catch (e) {}
      out.push(row);
    }
    var cs = kids(node) || [];
    for (var i = 0; i < cs.length && out.length < limit; i++) {
      visit(cs[i], path + '.' + (kidKey || 'children') + '[' + i + ']', kids, name, kidKey, kind);
    }
  };
  try {
    if (eng === 'playcanvas' && pcApp()) {
      /* The path is the point: it is what the agent pastes straight back into eval. */
      visit(pcApp().root, (reach.at || 'pc.app') + '.root',
            function (n) { return n.children; }, function (n) { return n.name; });
    } else if (eng === 'three' && threeScenes().length) {
      var ts = threeScenes();
      for (var s = 0; s < ts.length && out.length < limit; s++) {
        visit(ts[s], '__live.scenes()[' + s + ']',
              function (n) { return n.children; }, function (n) { return n.name; });
      }
    } else if (eng === 'phaser' && engineObj('phaser')) {
      var sc = engineObj('phaser').scene.scenes || [];
      for (var k = 0; k < sc.length && out.length < limit; k++) {
        var list = (sc[k].children && sc[k].children.list) || [];
        for (var m = 0; m < list.length && out.length < limit; m++) {
          visit(list[m], reach.at + '.scene.scenes[' + k + '].children.list[' + m + ']',
                function (n) { return n.list; }, function (n) { return n.name; }, 'list');
        }
      }
    } else if (eng === 'pixi' && engineObj('pixi')) {
      var st = engineObj('pixi').stage;
      visit(st, reach.at + '.stage', function (n) { return n.children; },
            function (n) { return n.label || n.name; }, 'children', pixiKind);
    } else if (eng === 'cocos' && engineObj('cocos')) {
      var croot = engineObj('cocos').getScene && engineObj('cocos').getScene();
      if (croot) {
        visit(croot, reach.at + '.getScene()', function (n) { return n.children; },
              function (n) { return n.name; });
      }
    } else if (eng === 'babylon') {
      var bs = babylonScenes()[0];
      (bs ? bs.meshes || [] : []).forEach(function (m, i) {
        if (out.length < limit && String(m.name || '').toLowerCase().indexOf(needle) >= 0) {
          out.push({ path: 'scene.meshes[' + i + ']', name: m.name, type: ctorName(m) });
        }
      });
    }
  } catch (e) {}
  var res = { engine: eng, reachable: reach.reachable, matches: out,
              truncated: out.length >= limit };
  if (reach.hint) res.hint = reach.hint;
  return res;
};
L.scenes = threeScenes;

/* -------------------------------------------------------------------- perf */
L.resetPerf = function () { hist.dt.length = 0; hist.calls.length = 0; hist.tris.length = 0; return true; };
L.perf = function () {
  var dt = hist.dt.slice().sort(function (a, b) { return a - b; });
  var avg = function (a) {
    if (!a.length) return 0;
    var s = 0;
    for (var i = 0; i < a.length; i++) s += a[i];
    return s / a.length;
  };
  var pct = function (p) {
    return dt.length ? Math.round(dt[Math.min(dt.length - 1, Math.floor(p * dt.length))] * 100) / 100 : 0;
  };
  var mx = function (a) {
    var m = 0;
    for (var i = 0; i < a.length; i++) if (a[i] > m) m = a[i];
    return m;
  };
  var o = {
    engine: L.engine(),
    frames: dt.length,
    fps: dt.length ? Math.round(1000 / avg(hist.dt) * 10) / 10 : 0,
    frame_ms_median: pct(0.5),
    frame_ms_p95: pct(0.95),
    frame_ms_worst: dt.length ? Math.round(dt[dt.length - 1] * 100) / 100 : 0,
    draw_calls: Math.round(avg(hist.calls)),
    draw_calls_peak: mx(hist.calls),
    triangles: Math.round(avg(hist.tris)),
    gl_textures: gl.tex, gl_buffers: gl.buf, gl_programs: gl.prog, gl_framebuffers: gl.fbo,
    hidden: document.hidden
  };
  /* Only reported when the game actually draws in 2D, so a WebGL game is not given a row of
     zeroes to read past. */
  if (mx(hist.ops2d)) {
    o.canvas2d_ops = Math.round(avg(hist.ops2d));
    o.canvas2d_ops_peak = mx(hist.ops2d);
    o.canvas2d_images = Math.round(avg(hist.images));
  }
  try { if (L.gpu) o.gpu = L.gpu; } catch (e) {}
  try {
    if (performance.memory) o.heap_mb = Math.round(performance.memory.usedJSHeapSize / 1048576 * 10) / 10;
  } catch (e) {}
  return o;
};

L.drain = function () { var a = L.errors; L.errors = []; return a; };
L.peek = function () { return L.errors.slice(); };
L.mark = function () { return seq; };
L.since = function (n) {
  return L.errors.filter(function (r) { return r.seq > n; });
};

/* One answer, bounded before it leaves the page.
   Truncating the JSON on the Python side breaks the envelope it is wrapped in, so the caller
   loses the console records as well as the value. Deciding here means the wire always carries
   valid JSON, and an over-large value says so about itself. */
L.cap = function (v, depth, max) {
  var j = short(v, depth);
  var s;
  try { s = JSON.stringify(j); } catch (e) { return '[unserialisable ' + (ctorName(v) || 'value') + ']'; }
  if (s === undefined) return '[undefined]';
  if (s.length > max) {
    return { __truncated: s.length, __limit: max, preview: s.slice(0, max),
             hint: 'ask for one field instead of the whole object' };
  }
  return j;
};
L.ready = function () {
  var r = L.reach();
  var out = { engine: r.engine, reachable: r.reachable, url: location.href, title: document.title,
              canvases: canvasesNow().length, up_ms: Date.now() - L.born, errors: L.errors.length };
  if (r.at) out.found_at = r.at;
  if (r.tag) out.engine_version = r.tag;
  if (r.hint) out.hint = r.hint;
  return out;
};
})();
"""

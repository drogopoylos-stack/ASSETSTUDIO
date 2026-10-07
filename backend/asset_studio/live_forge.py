"""A lit studio inside the running game, so code that makes an asset can be looked at.

This is the Blender loop for a project whose assets are written, not modelled. In Blender an
agent runs a script, takes a viewport screenshot, and iterates. Here the same loop needs three
things a game page does not offer on its own:

  * an ENGINE to build with. Not a second copy — the forge imports the exact module URL the game
    already loaded (Vite serves it at `/node_modules/.vite/deps/playcanvas.js?v=…`), so
    `module.Application === app.constructor` is true and the asset is built by the same code that
    will ship. Bringing a bundled engine of our own would have meant judging a different renderer
    from the one the game runs.
  * a STUDIO. Nobody judges a sword by dropping it into a fog-lerped candy world at 40 pixels.
    A neutral backdrop, a three-point rig and a camera that frames the subject are what turn a
    screenshot into a look, and they are exactly the part an agent should never have to write.
  * ISOLATION. The forge is a second canvas and a second scene, off-screen, beside the game. The
    game is never touched, never paused, and never has to be restarted afterwards.

It is injected on demand rather than with the always-on shim. Most sessions never forge anything,
and a game page should not carry a renderer it will not use.

The subject can be 3D or 2D. A procedural texture — the arena writes several by hand, as canvas
gradients — is judged flat on a checkerboard so its alpha is visible, because a normal map or a
soft blob viewed as a lit quad tells you nothing.
"""
from __future__ import annotations

FORGE = r"""
(() => {
if (window.__forge) return;

var F = {};
/* WHICH SCRIPT THIS IS. A tab outlives a backend restart, and `if (window.__forge) return` above
   kept the OLD script in it for good — so a function added to this file was simply missing from
   the page that needed it, and the call that wanted it failed. The backend compares this number
   and replaces a stale script before it builds. */
F.version = __FORGE_VERSION__;
try { Object.defineProperty(window, '__forge', { value: F, configurable: true, writable: true }); }
catch (e) { window.__forge = F; }

var S = null;                       /* the studio, once built */
var log = [];
var r3 = function (n) { return typeof n === 'number' ? Math.round(n * 1000) / 1000 : n; };

/* ------------------------------------------------------------------ engine
   The module the game already loaded, found by URL among the page's own resources. Importing it
   again is a module-cache hit, so it is the SAME namespace object — not a second engine. */
var kindOf = function (m) {
  try {
    if (m.WebGLRenderer && m.Scene && m.PerspectiveCamera) return 'three';
    if (m.Application && m.Entity && m.StandardMaterial) return 'playcanvas';
  } catch (e) {}
  return '';
};
F.engineUrls = function (want) {
  var names = [];
  try {
    names = performance.getEntriesByType('resource').map(function (r) { return r.name; });
  } catch (e) {}
  var pats = [];
  if (!want || want === 'playcanvas') pats.push(/playcanvas/i);
  if (!want || want === 'three') pats.push(/(^|[\/.])three([.\-]|$)/i, /three/i);
  var out = [];
  for (var p = 0; p < pats.length; p++) {
    for (var i = 0; i < names.length; i++) {
      if (pats[p].test(names[i]) && /\.(m?js)(\?|$)/.test(names[i]) && out.indexOf(names[i]) < 0) {
        out.push(names[i]);
      }
    }
  }
  /* Nothing loaded yet — the game may not have booted. Try where a bundler would have put it. */
  out.push('/node_modules/playcanvas/build/playcanvas.mjs');
  out.push('/node_modules/three/build/three.module.js');
  return out;
};

var loadEngine = async function (want) {
  var urls = F.engineUrls(want);
  for (var i = 0; i < urls.length; i++) {
    /* A page remembers a failed import for as long as it lives: the same URL rejects at once,
       even after the file is there. Measured: a forge that ran before the engine could be served
       left every later forge on that tab with "no engine", while the same URL with a query loaded.
       So a failure gets one more try under a fresh query, which is a fresh fetch. The record keeps
       the plain URL, because a new page has nothing remembered. */
    var tries = [urls[i], urls[i] + (urls[i].indexOf('?') < 0 ? '?' : '&') + 'forge=' + Date.now()];
    for (var t = 0; t < tries.length; t++) {
      try {
        var m = await import(/* @vite-ignore */ tries[t]);
        var k = kindOf(m) || kindOf(m.default || {});
        if (k && (!want || k === want)) {
          return { mod: kindOf(m) ? m : m.default, kind: k, url: urls[i] };
        }
        break;          /* it loaded and it is not the engine: a second copy will not be either */
      } catch (e) { /* the fresh query, then the next candidate */ }
    }
  }
  return null;
};

/* -------------------------------------------------------------- the studio */
var mkCanvas = function (w, h) {
  var host = document.getElementById('__forge_host');
  if (!host) {
    host = document.createElement('div');
    host.id = '__forge_host';
    /* Off-screen, not display:none — a hidden canvas stops rasterising and reads back black. */
    host.style.cssText = 'position:fixed;left:-20000px;top:0;width:1px;height:1px;overflow:visible;';
    document.body.appendChild(host);
  }
  host.innerHTML = '';
  var c = document.createElement('canvas');
  c.width = w; c.height = h;
  c.style.width = w + 'px'; c.style.height = h + 'px';
  host.appendChild(c);
  return c;
};

var hexToRgb = function (hex) {
  var h = String(hex || '#1a1e26').replace('#', '');
  if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  var n = parseInt(h, 16);
  return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 };
};

/* The world, and it is not decoration.
   The first forge had three directional lights and no environment, and a `metalness: 1` blade
   rendered PURE BLACK — correctly, because a metal has no diffuse and takes its colour entirely
   from what it reflects, and there was nothing to reflect. Reading the code would never have
   shown that; the picture showed it immediately. Blender has a world for exactly this reason, so
   the forge gets one: a small equirect gradient, sky over ground, with one warm blob for a metal
   to catch. It lights the scene without ever being drawn — the backdrop stays the flat colour. */
var envCanvas = function () {
  var c = document.createElement('canvas');
  c.width = 256; c.height = 128;
  var x = c.getContext('2d');
  var g = x.createLinearGradient(0, 0, 0, 128);
  g.addColorStop(0.00, '#93b8ec');
  g.addColorStop(0.46, '#e4ecf7');
  g.addColorStop(0.54, '#70757f');
  g.addColorStop(1.00, '#2b2e35');
  x.fillStyle = g;
  x.fillRect(0, 0, 256, 128);
  var s = x.createRadialGradient(72, 32, 2, 72, 32, 50);
  s.addColorStop(0, 'rgba(255,247,226,1)');
  s.addColorStop(1, 'rgba(255,247,226,0)');
  x.fillStyle = s;
  x.fillRect(0, 0, 256, 128);
  return c;
};

var buildThree = function (T, canvas, opts) {
  var bg = hexToRgb(opts.background);
  var renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true,
                                       preserveDrawingBuffer: true, alpha: false });
  renderer.setPixelRatio(1);
  renderer.setSize(canvas.width, canvas.height, false);
  if (renderer.outputColorSpace !== undefined && T.SRGBColorSpace) {
    renderer.outputColorSpace = T.SRGBColorSpace;
  }
  var scene = new T.Scene();
  /* The Studio's bench, not the game's: the live shim leaves a scene with this mark out of the game's
     scene list, or it became the root the saved edits were applied to and keyed from. */
  scene.name = '__studio-forge';
  scene.userData.__studioForge = true;
  /* From the STRING, not from linear floats. three's colour management reads a hex or CSS string
     as sRGB and converts it to linear itself; handing it 0..1 components skips that step, so the
     output encoding then lightened it — `#1a1e26` was drawn as mid-grey. */
  try { scene.background = new T.Color(opts.background || '#1a1e26'); }
  catch (e) { scene.background = new T.Color(bg.r, bg.g, bg.b); }
  var camera = new T.PerspectiveCamera(38, canvas.width / canvas.height, 0.01, 10000);
  /* A three-point rig, not one light. One light gives a flat front and a black back, which is
     what makes a screenshot of a model unreadable. */
  var key = new T.DirectionalLight(0xffffff, 2.6); key.position.set(3, 5, 4);
  var fill = new T.DirectionalLight(0xbcd0ff, 0.9); fill.position.set(-4, 1.5, 3);
  var rim = new T.DirectionalLight(0xffe6c0, 1.6); rim.position.set(-2, 3, -5);
  var amb = new T.HemisphereLight(0xa8c4ff, 0x40332a, 0.7);
  scene.add(key); scene.add(fill); scene.add(rim); scene.add(amb);
  var envTex = null;
  try {
    var pm = new T.PMREMGenerator(renderer);
    var eq = new T.CanvasTexture(envCanvas());
    eq.mapping = T.EquirectangularReflectionMapping;
    envTex = pm.fromEquirectangular(eq).texture;
    scene.environment = envTex;
    eq.dispose();
    pm.dispose();
  } catch (e) { /* an old three without PMREM still gets the three-point rig */ }
  var root = new T.Group(); root.name = 'forge-subject'; scene.add(root);
  var ground = null;
  if (opts.ground) {
    ground = new T.Mesh(new T.PlaneGeometry(1000, 1000),
                        new T.MeshStandardMaterial({ color: 0x2a2f38, roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);
  }
  return { kind: 'three', T: T, renderer: renderer, scene: scene, camera: camera,
           cameraP: camera, root: root, ground: ground, canvas: canvas, envTex: envTex,
           /* Held so the rig can change per call instead of being fixed for the life of the page:
              a bench that cannot put its ambient floor near zero cannot judge black cloth. */
           lights: { key: key, fill: fill, rim: rim, amb: amb },
           base: { key: 2.6, fill: 0.9, rim: 1.6, amb: 0.7 } };
};

var buildPc = function (pc, canvas, opts) {
  var bg = hexToRgb(opts.background);
  var app = new pc.Application(canvas, {
    graphicsDeviceOptions: { preserveDrawingBuffer: true, antialias: true, alpha: false }
  });
  app.setCanvasFillMode(pc.FILLMODE_NONE);
  app.setCanvasResolution(pc.RESOLUTION_FIXED, canvas.width, canvas.height);
  app.scene.ambientLight = new pc.Color(0.22, 0.25, 0.30);
  var cam = new pc.Entity('forge-camera');
  cam.addComponent('camera', { clearColor: new pc.Color(bg.r, bg.g, bg.b), fov: 38,
                               nearClip: 0.01, farClip: 10000 });
  app.root.addChild(cam);
  var mkLight = function (name, colour, intensity, eul) {
    var e = new pc.Entity(name);
    e.addComponent('light', { type: 'directional', color: colour, intensity: intensity,
                              castShadows: false });
    e.setEulerAngles(eul[0], eul[1], eul[2]);
    app.root.addChild(e);
    return e;
  };
  var lKey = mkLight('key', new pc.Color(1, 0.98, 0.94), 1.9, [-42, 38, 0]);
  var lFill = mkLight('fill', new pc.Color(0.74, 0.82, 1), 0.7, [-14, -60, 0]);
  var lRim = mkLight('rim', new pc.Color(1, 0.9, 0.75), 1.4, [-18, 190, 0]);
  try {
    var tex = new pc.Texture(app.graphicsDevice, {
      name: 'forge-env', width: 256, height: 128, format: pc.PIXELFORMAT_RGBA8,
      projection: pc.TEXTUREPROJECTION_EQUIRECT, mipmaps: false,
      addressU: pc.ADDRESS_REPEAT, addressV: pc.ADDRESS_CLAMP_TO_EDGE
    });
    tex.setSource(envCanvas());
    /* envAtlas only — deliberately NOT scene.skybox, so the world lights the asset without
       being drawn behind it and the backdrop stays the flat colour that was asked for. */
    app.scene.envAtlas = pc.EnvLighting.generateAtlas(pc.EnvLighting.generateLightingSource(tex));
    app.scene.skyboxIntensity = 1;
    /* In PlayCanvas `scene.envAtlas` both LIGHTS the scene and DRAWS as the sky — measured: the
       backdrop came back #dae5f6 instead of the #1a1e26 that was asked for, and setting
       `sky.node.enabled = false` was silently undone at render time. Dropping the skybox LAYER
       from this camera is the supported way to keep the lighting and lose the drawn sky. Three.js
       needs none of this: `scene.environment` and `scene.background` are already separate. */
    if (!opts.sky && pc.LAYERID_SKYBOX !== undefined) {
      var ll = (app.scene.layers && app.scene.layers.layerList) || [];
      var ids = [];
      for (var li = 0; li < ll.length; li++) {
        if (ll[li].id !== pc.LAYERID_SKYBOX) ids.push(ll[li].id);
      }
      if (ids.length) cam.camera.layers = ids;
    }
  } catch (e) { /* an engine build without EnvLighting still gets the three-point rig */ }
  var root = new pc.Entity('forge-subject');
  app.root.addChild(root);
  var ground = null;
  if (opts.ground) {
    ground = new pc.Entity('forge-ground');
    var gm = new pc.StandardMaterial();
    gm.diffuse = new pc.Color(0.16, 0.18, 0.22);
    gm.update();
    ground.addComponent('render', { type: 'plane', material: gm });
    ground.setLocalScale(1000, 1, 1000);
    app.root.addChild(ground);
  }
  /* Never app.start(): that begins a second animation loop beside the game's. The forge steps
     itself, one frame per view, so an idle forge costs nothing at all. */
  return { kind: 'playcanvas', pc: pc, app: app, camera: cam, root: root, ground: ground,
           canvas: canvas, envAtlas: app.scene.envAtlas || null,
           lights: { key: lKey, fill: lFill, rim: lRim },
           base: { key: 1.9, fill: 0.7, rim: 1.4, amb: [0.22, 0.25, 0.30] } };
};

F.ensure = async function (opts) {
  opts = opts || {};
  var w = Math.max(64, Math.min(2048, opts.width || 640));
  var h = Math.max(64, Math.min(2048, opts.height || 480));
  var want = opts.engine || '';
  if (S && S.canvas && (S.canvas.width !== w || S.canvas.height !== h)) F.dispose();
  if (S && (!want || S.kind === want)) {
    return { ok: true, engine: S.kind, reused: true, look: F.studio(opts) };
  }
  if (S) F.dispose();
  var got = await loadEngine(want);
  if (!got) {
    return { ok: false, engine: '',
             error: 'no three.js or PlayCanvas module could be loaded from this page. The forge ' +
                    'builds with the project\'s OWN engine, so the page has to have one — open ' +
                    'the game first, or point the forge at a page that loads the engine.' };
  }
  var canvas = mkCanvas(w, h);
  var o = { background: opts.background || '#1a1e26', ground: !!opts.ground, sky: !!opts.sky };
  S = got.kind === 'three' ? buildThree(got.mod, canvas, o) : buildPc(got.mod, canvas, o);
  S.opts = o;
  S.engineUrl = got.url;
  F.studio(opts);
  /* Sample the empty studio once. This is the only moment the backdrop is guaranteed to be the
     only thing on screen, and every later "is the subject clipped" check measures against it. */
  try {
    F.shot();
    var t = document.createElement('canvas');
    t.width = 1; t.height = 1;
    var xc = t.getContext('2d');
    xc.drawImage(canvas, 2, 2, 1, 1, 0, 0, 1, 1);
    var px = xc.getImageData(0, 0, 1, 1).data;
    S.bgPixel = [px[0], px[1], px[2]];
  } catch (e) {}
  return { ok: true, engine: S.kind, engine_url: String(got.url).split('/').pop(), reused: false,
           backdrop: S.bgPixel || null, look: S.look || null };
};

F.clear = function () {
  log = [];
  if (!S) return true;
  try {
    if (S.kind === 'three') {
      while (S.root.children.length) S.root.remove(S.root.children[0]);
    } else {
      var kids = S.root.children.slice();
      for (var i = 0; i < kids.length; i++) S.root.removeChild(kids[i]);
    }
    S.flat = null;
  } catch (e) {}
  return true;
};

F.dispose = function () {
  try {
    if (S && S.kind === 'three' && S.renderer) {
      S.renderer.dispose();
      /* Free the GL context now. dispose() releases buffers, but the context lives until the
         canvas is collected, and Chrome keeps only about 16 before it drops the oldest. */
      try { S.renderer.forceContextLoss(); } catch (e2) {}
    }
    if (S && S.kind === 'playcanvas' && S.app) S.app.destroy();
  } catch (e) {}
  var host = document.getElementById('__forge_host');
  if (host && host.parentNode) host.parentNode.removeChild(host);
  S = null;
  return true;
};

/* --------------------------------------------------------------- the subject
   `add` takes whatever the agent's code made. A mesh, an entity, an array of them — or a canvas
   or a texture, because half of this project's assets are procedural TEXTURES, and a normal map
   shown as a lit quad tells you nothing about the normal map. */
var asFlat = function (v) {
  try {
    if (!v) return null;
    if (typeof HTMLCanvasElement !== 'undefined' && v instanceof HTMLCanvasElement) return v;
    if (typeof HTMLImageElement !== 'undefined' && v instanceof HTMLImageElement) return v;
    if (typeof ImageBitmap !== 'undefined' && v instanceof ImageBitmap) return v;
    if (typeof ImageData !== 'undefined' && v instanceof ImageData) return v;
    /* three.js Texture / PlayCanvas Texture wrapping one of the above */
    var src = v.image || (v.getSource && v.getSource()) || v._levels && v._levels[0];
    if (src && (src.width || src.videoWidth)) return src;
  } catch (e) {}
  return null;
};

/* The parameters of the current run. A procedural asset IS its parameters — blade width, segment
   count, taper — and the way to choose one is to see the neighbours beside it, not to render a
   guess, look, edit the number, and render another guess a minute later. */
F.params = {};

F.ctx = function () {
  return {
    params: F.params,
    pc: S && S.kind === 'playcanvas' ? S.pc : undefined,
    THREE: S && S.kind === 'three' ? S.T : undefined,
    engine: S ? S.kind : '',
    app: S && S.app,
    device: S && S.app && S.app.graphicsDevice,
    renderer: S && S.renderer,
    scene: S && (S.scene || (S.app && S.app.scene)),
    camera: S && S.camera,
    root: S && S.root,
    forge: F,
    log: function () {
      try { log.push(Array.prototype.map.call(arguments, String).join(' ').slice(0, 400)); }
      catch (e) {}
    },
    clear: F.clear,
    add: function (thing) {
      if (!S || thing == null) return thing;
      var many = Array.isArray(thing) ? thing : [thing];
      for (var i = 0; i < many.length; i++) {
        var t = many[i];
        if (t == null) continue;
        var flat = asFlat(t);
        if (flat) { S.flat = flat; continue; }
        try {
          if (S.kind === 'three') {
            /* A bare Geometry or Material is a common thing to hand over; wrap it so it shows. */
            if (t.isBufferGeometry) {
              t = new S.T.Mesh(t, new S.T.MeshStandardMaterial({ color: 0xb9c3d0, roughness: 0.55,
                                                                 metalness: 0.05 }));
            } else if (t.isMaterial) {
              t = new S.T.Mesh(new S.T.SphereGeometry(1, 48, 32), t);
            }
            S.root.add(t);
          } else {
            if (t instanceof S.pc.Mesh) {
              var mi = new S.pc.MeshInstance(t, new S.pc.StandardMaterial());
              var e = new S.pc.Entity('mesh');
              e.addComponent('render', { meshInstances: [mi] });
              t = e;
            } else if (t instanceof S.pc.StandardMaterial) {
              var e2 = new S.pc.Entity('material');
              e2.addComponent('render', { type: 'sphere', material: t });
              t = e2;
            }
            S.root.addChild(t);
          }
        } catch (err) { log.push('add failed: ' + String(err).slice(0, 200)); }
      }
      return thing;
    }
  };
};

/* ------------------------------------------------------------------ framing
   The bounding box is the whole trick. Without it every asset is either a speck or clipped, and
   two runs of the same code never frame the same way, so nothing can be compared. */
var bounds = function (only) {
  if (!S) return null;
  try {
    if (S.kind === 'three') {
      var box = new S.T.Box3();
      if (only && only.length) {
        for (var oi = 0; oi < only.length; oi++) box.expandByObject(only[oi]);
      } else {
        box.setFromObject(S.root);
      }
      if (box.isEmpty()) return null;
      var c = box.getCenter(new S.T.Vector3()), sz = box.getSize(new S.T.Vector3());
      return { c: [c.x, c.y, c.z], size: [sz.x, sz.y, sz.z],
               radius: Math.max(0.0001, box.getSize(new S.T.Vector3()).length() / 2) };
    }
    var mins = [1e9, 1e9, 1e9], maxs = [-1e9, -1e9, -1e9], any = false;
    var walk = function (e) {
      var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
      for (var i = 0; i < mi.length; i++) {
        var a = mi[i].aabb;
        if (!a) continue;
        var lo = [a.center.x - a.halfExtents.x, a.center.y - a.halfExtents.y,
                  a.center.z - a.halfExtents.z];
        var hi = [a.center.x + a.halfExtents.x, a.center.y + a.halfExtents.y,
                  a.center.z + a.halfExtents.z];
        for (var k = 0; k < 3; k++) {
          if (lo[k] < mins[k]) mins[k] = lo[k];
          if (hi[k] > maxs[k]) maxs[k] = hi[k];
        }
        any = true;
      }
      var kids = e.children || [];
      for (var j = 0; j < kids.length; j++) walk(kids[j]);
    };
    if (only && only.length) { for (var wi = 0; wi < only.length; wi++) walk(only[wi]); }
    else walk(S.root);
    if (!any) return null;
    var ctr = [(mins[0] + maxs[0]) / 2, (mins[1] + maxs[1]) / 2, (mins[2] + maxs[2]) / 2];
    var size = [maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2]];
    var rad = Math.max(0.0001, Math.sqrt(size[0] * size[0] + size[1] * size[1] +
                                         size[2] * size[2]) / 2);
    return { c: ctr, size: size, radius: rad };
  } catch (e) { return null; }
};
F.bounds = bounds;

/* ------------------------------------------------------------------- parts
   THE FRAMING DECIDES WHAT GETS JUDGED, and nobody was told which parts it had priced out.
   Every view fits the WHOLE subject, so a claw on a three-unit creature lands on about ten
   pixels. An agent looks, sees nothing wrong at ten pixels, and moves on -- and then the claws
   point the wrong way in the finished asset, having passed twelve inspections. Measured on the
   real run: the claws and the sail's rib grooves were the two things that came out worst, and
   both were the two smallest features on the sheet.

   So: measure each part IN PIXELS, in the framing that was actually rendered, and say which ones
   were too small to have been seen. A number nobody can act on is worse than no number, so this
   also names them, which is exactly what `focus` then takes. */
var nameOf = function (o) {
  var n = (o && o.name) || '';
  if (n) return n;
  var m = o && (Array.isArray(o.material) ? o.material[0] : o.material);
  return (m && m.name) || '';
};

/* A thing that draws: what `pick` hands back, and what a matching group brings with it. */
var drawn = function (o) { return !!(o && (o.isMesh || o.isPoints || o.isLine)); };

/* Case-insensitive substring, against the object's own name AND its material's, because an agent
   names one or the other and hardly ever both.

   A GROUP, WHEN NO MESH MATCHES. This matched meshes only, so on the goblin A/B (2026-09-24)
   `focus:["sword"]` - a `sword` group holding `blade`, `crossguard`, `grip` and `pommel`, none of
   them named sword - matched nothing, and the close-up came back as the whole figure with no word
   about why; `focus:["blade"]` worked. A GLB's parts arrive in groups the same way. Only as a
   fallback: a word that finds meshes finds exactly the meshes it always found.

   "=name" is EXACTLY that name (any case), groups included. A substring is right for "the claws"
   and wrong for one hand: `hand` is handL, handR - and the goblin's shieldHandle. */
var pick = function (want) {
  var q = String(want || '').toLowerCase().trim();
  var hits = [];
  if (!S || !q) return hits;
  if (q.charAt(0) === '=') return exactNamed(q.slice(1).trim());
  if (S.kind === 'three') {
    S.root.traverse(function (o) {
      if (o === S.root) return;
      if (!(o.isMesh || o.isPoints || o.isLine)) return;
      var mats = Array.isArray(o.material) ? o.material : [o.material];
      var mn = '';
      for (var i = 0; i < mats.length; i++) mn += ' ' + ((mats[i] && mats[i].name) || '');
      if (String(o.name || '').toLowerCase().indexOf(q) >= 0 ||
          mn.toLowerCase().indexOf(q) >= 0) hits.push(o);
    });
    if (hits.length) return hits;
    var seen = new Set();
    S.root.traverse(function (o) {
      if (o === S.root || drawn(o) || String(o.name || '').toLowerCase().indexOf(q) < 0) return;
      o.traverse(function (k) { if (k !== o && drawn(k) && !seen.has(k)) { seen.add(k); hits.push(k); } });
    });
    return hits;
  }
  var walk = function (e) {
    if (e !== S.root && String(e.name || '').toLowerCase().indexOf(q) >= 0) hits.push(e);
    var kids = e.children || [];
    for (var j = 0; j < kids.length; j++) walk(kids[j]);
  };
  walk(S.root);
  return hits;
};
F.pick = function (q) { return pick(q).length; };

/* Every drawn thing under a node whose own name is exactly `q` (lower case), the node included. */
var exactNamed = function (q) {
  var out = [];
  if (!S || !q) return out;
  if (S.kind === 'three') {
    var seen = new Set();
    var take = function (o) { if (!seen.has(o)) { seen.add(o); out.push(o); } };
    S.root.traverse(function (o) {
      if (o === S.root || String(o.name || '').toLowerCase() !== q) return;
      if (drawn(o)) take(o);
      else o.traverse(function (k) { if (k !== o && drawn(k)) take(k); });
    });
  } else {
    var walk = function (e) {
      if (e !== S.root && String(e.name || '').toLowerCase() === q) { out.push(e); return; }
      var kids = e.children || [];
      for (var j = 0; j < kids.length; j++) walk(kids[j]);
    };
    walk(S.root);
  }
  return out;
};

/* THE PART AN ANCHOR NAMES: exactly that name first, as `edits` does, then part of a name. An
   anchor is ONE point, and on the forge goblin `head` by substring is also `headGroup`, whose
   tusks stick out further forward than the nose the anchor meant. */
var pickPart = function (want) {
  var q = String(want || '').toLowerCase().trim().replace(/^=/, '');
  var out = exactNamed(q);
  return out.length ? out : pick(q);
};

/* WHICH NAMES A WORD REACHES: for each, the topmost nodes whose own name holds it. The detail
   review asks, so it can split `hand` into handL and handR instead of judging one merged box. */
F.matches = function (names) {
  var out = {};
  (names || []).forEach(function (n) {
    var q = String(n || '').toLowerCase().trim(), got = [];
    if (!S || !q) { out[n] = got; return; }
    var walk = function (e) {
      if (e !== S.root && String(e.name || '').toLowerCase().indexOf(q) >= 0) {
        if (got.indexOf(String(e.name)) < 0 && got.length < 16) got.push(String(e.name));
        return;
      }
      var kids = e.children || [];
      for (var j = 0; j < kids.length; j++) walk(kids[j]);
    };
    walk(S.root);
    out[n] = got;
  });
  return out;
};

/* World point -> pixel, in whatever camera is set right now. Returns null behind the camera. */
var toPx = function (x, y, z) {
  try {
    if (S.kind === 'three') {
      var v = new S.T.Vector3(x, y, z).project(S.camera);
      if (v.z > 1) return null;
      return [(v.x * 0.5 + 0.5) * S.canvas.width, (0.5 - v.y * 0.5) * S.canvas.height];
    }
    var pp = S.camera.camera.worldToScreen(new S.pc.Vec3(x, y, z));
    return [pp.x, pp.y];
  } catch (e) { return null; }
};

/* The screen rectangle of a world box, from its eight corners. */
var pxBox = function (c, size) {
  var lo = null, hi = null;
  for (var i = 0; i < 8; i++) {
    var pt = toPx(c[0] + (i & 1 ? 0.5 : -0.5) * size[0],
                  c[1] + (i & 2 ? 0.5 : -0.5) * size[1],
                  c[2] + (i & 4 ? 0.5 : -0.5) * size[2]);
    if (!pt) continue;
    if (!lo) { lo = [pt[0], pt[1]]; hi = [pt[0], pt[1]]; continue; }
    lo[0] = Math.min(lo[0], pt[0]); lo[1] = Math.min(lo[1], pt[1]);
    hi[0] = Math.max(hi[0], pt[0]); hi[1] = Math.max(hi[1], pt[1]);
  }
  if (!lo) return null;
  return [Math.round(hi[0] - lo[0]), Math.round(hi[1] - lo[1])];
};

/* EVERY MESH, measured on its own, before anything is grouped.
   The first version grouped by name and then measured, and it got both of its two answers wrong.
   "claw" reported 101px, because that is the box around all TWELVE claws -- while one claw, the
   thing you actually have to look at, is twelve. And the stance reported one footprint, because
   "skin" is twenty-nine meshes and the box around all of them has a single centre. Group after
   measuring, never before. */
var meshBoxes = function () {
  var rows = [];
  if (!S || S.flat) return rows;
  try {
    if (S.kind === 'three') {
      S.root.traverse(function (o) {
        if (!(o.isMesh || o.isPoints || o.isLine)) return;
        var g = o.geometry, tri = 0;
        if (g && g.index) tri = g.index.count / 3;
        else if (g && g.attributes && g.attributes.position) tri = g.attributes.position.count / 3;
        var b = new S.T.Box3().setFromObject(o);
        if (b.isEmpty()) return;
        rows.push({ name: nameOf(o) || '(unnamed)', tris: tri,
                    lo: [b.min.x, b.min.y, b.min.z], hi: [b.max.x, b.max.y, b.max.z] });
      });
      return rows;
    }
    var walk = function (e) {
      var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
      for (var i = 0; i < mi.length; i++) {
        var a = mi[i].aabb;
        if (!a) continue;
        var pr = mi[i].mesh && mi[i].mesh.primitive && mi[i].mesh.primitive[0];
        rows.push({ name: e.name || '(unnamed)', tris: pr && pr.count ? pr.count / 3 : 0,
                    lo: [a.center.x - a.halfExtents.x, a.center.y - a.halfExtents.y,
                         a.center.z - a.halfExtents.z],
                    hi: [a.center.x + a.halfExtents.x, a.center.y + a.halfExtents.y,
                         a.center.z + a.halfExtents.z] });
      }
      var kids = e.children || [];
      for (var j = 0; j < kids.length; j++) walk(kids[j]);
    };
    walk(S.root);
  } catch (e) { /* a measurement must never cost the picture */ }
  return rows;
};

var boxPx = function (r) {
  var c = [(r.lo[0] + r.hi[0]) / 2, (r.lo[1] + r.hi[1]) / 2, (r.lo[2] + r.hi[2]) / 2];
  return pxBox(c, [r.hi[0] - r.lo[0], r.hi[1] - r.lo[1], r.hi[2] - r.lo[2]]);
};

/* ------------------------------------------------------------------- detail
   WHERE ONE PART SITS ON SCREEN, in the camera that is set right now, as 0..1 of the frame.
   `where()` groups meshes by their OWN name, so a container -- `head`, holding the block, the face
   and the hair -- has no row there at all. This takes exactly what `focus` takes: every match of
   the name, and everything under each match. */
F.boxOf = function (q) {
  if (!S || S.flat || !S.canvas) return null;
  var only = pick(q);
  if (!only.length) return null;
  var b = bounds(only);
  if (!b) return null;
  var W = S.canvas.width || 1, H = S.canvas.height || 1;
  var lo = null, hi = null;
  for (var i = 0; i < 8; i++) {
    var pt = toPx(b.c[0] + (i & 1 ? 0.5 : -0.5) * b.size[0],
                  b.c[1] + (i & 2 ? 0.5 : -0.5) * b.size[1],
                  b.c[2] + (i & 4 ? 0.5 : -0.5) * b.size[2]);
    if (!pt) continue;
    if (!lo) { lo = [pt[0], pt[1]]; hi = [pt[0], pt[1]]; continue; }
    lo[0] = Math.min(lo[0], pt[0]); lo[1] = Math.min(lo[1], pt[1]);
    hi[0] = Math.max(hi[0], pt[0]); hi[1] = Math.max(hi[1], pt[1]);
  }
  if (!lo) return null;
  return { box: [r3(lo[0] / W), r3(lo[1] / H), r3(hi[0] / W), r3(hi[1] / H)],
           px: [Math.round(hi[0] - lo[0]), Math.round(hi[1] - lo[1])], matched: only.length };
};

/* Which of these names are in the scene at all -- one call instead of one per name. */
F.present = function (names) {
  var out = [];
  (names || []).forEach(function (n) { if (pick(n).length) out.push(n); });
  return out;
};

/* ONE PART ALONE. Everything outside the named part is hidden, so the comparison that decides
   "upside down" sees the part and not what hangs in front of it -- measured on a character whose
   hair strands crossed its face: 0.29 as built and 0.28 turned over, no signal either way.
   Hidden per renderable (three: a layer the camera does not draw; PlayCanvas: the mesh instance),
   never per node, so a part parented under a hidden mesh still draws. F.solo() puts it all back. */
var soloSaved = null;
/* The object with EXACTLY this name, groups included. `pick` matches by substring - right for
   "the claw", wrong for a GLB loaded as one Group named '__B': when it saw meshes only, nothing
   matched, nothing was hidden, and /compare photographed both assets in both panels with identical
   scores. It takes groups now, but '__b' is still part of other names; an A/B needs the one root. */
var byName = function (q) {
  if (!S || !q) return [];
  var o = null;
  try { o = S.kind === 'three' ? S.root.getObjectByName(String(q)) : S.root.findByName(String(q)); }
  catch (e) { o = null; }
  return o && o !== S.root ? [o] : [];
};

F.solo = function (q, invert, exact) {
  if (soloSaved) {
    for (var i = 0; i < soloSaved.length; i++) {
      var s = soloSaved[i];
      try { if (s.o) s.o.layers.mask = s.mask; else s.mi.visible = s.vis; } catch (e) {}
    }
    soloSaved = null;
  }
  if (!q || !S || S.flat) return { hidden: 0, kept: 0 };
  var keep = exact ? byName(q) : pick(q);
  if (!keep.length) return { hidden: 0, kept: 0 };
  soloSaved = [];
  var flip = !!invert;
  if (S.kind === 'three') {
    var inKeep = function (o) {
      for (var p = o; p; p = p.parent) {
        if (keep.indexOf(p) >= 0) return true;
        if (p === S.root) break;
      }
      return false;
    };
    S.root.traverse(function (o) {
      if (!(o.isMesh || o.isPoints || o.isLine || o.isSprite)) return;
      if (inKeep(o) !== flip) return;
      soloSaved.push({ o: o, mask: o.layers.mask });
      o.layers.set(31);
    });
  } else {
    var walk = function (e, kept) {
      kept = kept || keep.indexOf(e) >= 0;
      if (kept === flip) {
        var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        for (var j = 0; j < mi.length; j++) {
          soloSaved.push({ mi: mi[j], vis: mi[j].visible });
          mi[j].visible = false;
        }
      }
      var kids = e.children || [];
      for (var k = 0; k < kids.length; k++) walk(kids[k], kept);
    };
    walk(S.root, false);
  }
  return { hidden: soloSaved.length, kept: keep.length };
};

/* THE TEXTURES A PART IS PAINTED WITH, AND WHICH WAY UP EACH IS STORED.
   Asked only after a close-up has already matched the reference best upside down, so the answer
   can name the texture instead of saying "something is flipped". The trap it exists for: a
   PlayCanvas texture defaults to flipY:false, which puts the canvas's TOP row at v=0 -- so a face
   drawn on a canvas and mapped with three.js's v-up UVs comes out with the mouth above the eyes.
   Measured on a real character; the render showed it and nothing in the answer said so. */
/* A TEXTURE A glTF LOADER MADE IS THE RIGHT WAY UP BY DEFINITION. glTF keeps an image's top row at
   v=0, and three's GLTFLoader sets flipY:false to match and stamps `userData.mimeType` on every
   texture it makes. The advice "set flipY = true" turns such a texture upside down - the goblin A/B
   got it for a baked atlas. Anything under a GLB this bench loaded (F.glb marks the root) counts
   too, which is how a PlayCanvas container's textures are known. */
var underGlb = function (o) {
  for (var p = o; p; p = p.parent) {
    if (p.__forgeGlb) return true;
    if (p === (S && S.root)) break;
  }
  return false;
};
F.texFacts = function (q) {
  var out = [];
  if (!S || S.flat) return out;
  var seen = [];
  var srcKind = function (s) {
    try {
      if (!s) return 'none';
      if (typeof HTMLCanvasElement !== 'undefined' && s instanceof HTMLCanvasElement) return 'canvas';
      if (typeof OffscreenCanvas !== 'undefined' && s instanceof OffscreenCanvas) return 'canvas';
      if (typeof HTMLImageElement !== 'undefined' && s instanceof HTMLImageElement) return 'image';
      if (typeof ImageBitmap !== 'undefined' && s instanceof ImageBitmap) return 'bitmap';
      if (s.data) return 'data';
    } catch (e) {}
    return '?';
  };
  var SLOTS = S.kind === 'three'
    ? ['map', 'emissiveMap', 'alphaMap', 'normalMap']
    : ['diffuseMap', 'emissiveMap', 'opacityMap', 'normalMap'];
  var visitMat = function (m, owner) {
    if (!m) return;
    for (var i = 0; i < SLOTS.length; i++) {
      var t = m[SLOTS[i]];
      if (!t || seen.indexOf(t) >= 0) continue;
      seen.push(t);
      var src = null;
      try { src = S.kind === 'three' ? t.image : (t.getSource ? t.getSource() : null); } catch (e) {}
      var flip = null;
      try { flip = !!t.flipY; } catch (e) {}
      var gl = !!owner;
      try { if (t.userData && t.userData.mimeType) gl = true; } catch (e) {}
      out.push({ material: m.name || '(unnamed)', slot: SLOTS[i], flipY: flip, source: srcKind(src),
                 size: src && src.width ? [src.width, src.height] : null, gltf: gl });
    }
  };
  try {
    if (S.kind === 'three') {
      var meshes = [];
      if (q) meshes = pick(q);
      else S.root.traverse(function (o) { if (o.isMesh) meshes.push(o); });
      meshes.forEach(function (o) {
        var own = underGlb(o);
        (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (m) {
          visitMat(m, own);
        });
      });
    } else {
      var walk = function (e) {
        var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        var own = mi.length ? underGlb(e) : false;
        for (var k = 0; k < mi.length; k++) visitMat(mi[k].material, own);
        var kids = e.children || [];
        for (var j = 0; j < kids.length; j++) walk(kids[j]);
      };
      (q ? pick(q) : [S.root]).forEach(walk);
    }
  } catch (e) { /* a fact we could not read is not a fact */ }
  return out.slice(0, 12);
};

var median = function (a) {
  if (!a.length) return 0;
  var b = a.slice().sort(function (x, y) { return x - y; });
  return b[Math.floor(b.length / 2)];
};

/* One row per NAME for reading, but every number in it comes from the per-mesh measurements.
   `px` is the size of ONE of them -- the question is always "can I see a claw", never "can I see
   the region all the claws occupy". `px_span` keeps the other number for when it is wanted. */
F.parts = function () {
  var rows = meshBoxes();
  var by = {}, order = [];
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i], e = by[r.name];
    if (!e) { e = by[r.name] = { name: r.name, meshes: 0, tris: 0, lo: null, hi: null, each: [] };
              order.push(e); }
    e.meshes++;
    e.tris += r.tris;
    var pb = boxPx(r);
    if (pb) e.each.push(Math.max(pb[0], pb[1]));
    if (!e.lo) { e.lo = r.lo.slice(); e.hi = r.hi.slice(); }
    else {
      for (var k = 0; k < 3; k++) {
        e.lo[k] = Math.min(e.lo[k], r.lo[k]);
        e.hi[k] = Math.max(e.hi[k], r.hi[k]);
      }
    }
  }
  var out = [];
  for (var j = 0; j < order.length; j++) {
    var g = order[j];
    var row = { name: g.name, meshes: g.meshes, tris: Math.round(g.tris) };
    if (g.lo) {
      var sz = [g.hi[0] - g.lo[0], g.hi[1] - g.lo[1], g.hi[2] - g.lo[2]];
      row.size = [r3(sz[0]), r3(sz[1]), r3(sz[2])];
      row.min_y = r3(g.lo[1]);
      var span = pxBox([(g.lo[0] + g.hi[0]) / 2, (g.lo[1] + g.hi[1]) / 2,
                        (g.lo[2] + g.hi[2]) / 2], sz);
      if (span) row.px_span = span;
    }
    if (g.each.length) row.px = median(g.each);
    out.push(row);
  }
  out.sort(function (a, b) { return (b.tris || 0) - (a.tris || 0); });
  return out.slice(0, 40);
};

/* ------------------------------------------------------------------- stance
   What touches the floor, and where. The tool cannot know what pose the reference is in -- that
   would take reading the picture -- but it can state OURS exactly, beside it. A creature the
   reference holds up on two legs, built as a flat four-point quadruped, is then a difference you
   cannot miss rather than one you have to notice. */
/* ------------------------------------------------------------------- where
   WHERE EACH PART SITS IN THE FRAME, in the same 0..1 coordinates the reference panel is ruled
   with. This is the number that was missing, and its absence is measurable: an agent moved a
   diamond inset off centre between two shots of the same chest and had no way to know, because
   the only thing it could read about that diamond was that it was 34 pixels wide.

   `px` says whether a part can be SEEN. This says whether it is in the RIGHT PLACE. They are
   different questions and the second one is the one that decides whether a thing reads as the
   thing it is copying.

   Coordinates are the picture's own: x from 0 at the left edge to 1 at the right, y from 0 at
   the TOP to 1 at the bottom, so they can be read straight off the ruled reference and
   subtracted. Measured against whatever camera is set right now, so the caller has to say which
   view it framed -- and `F.view` returns that name. */
/* THE PARTS AS PIXELS. `where` used to report each part's world box projected onto the screen,
   and a projected box is not where the part is: the goblin A/B's forge builder found "the
   helmet's box starts well above the helmet", and wrote its own flat-colour ID render to place
   the hands (forge/work/run_id.js). This is that render. Every mesh is painted one flat, unlit,
   unique colour and drawn once into a render target with NO multisampling - so no edge pixel is
   a blend of two ids that happens to spell a third - with the camera the picture was taken with.
   Per name: the visible pixels' box, their centroid and their count. Hidden pixels are not
   counted: a hand behind the shield is placed by what shows of it. three.js only; PlayCanvas
   keeps the projected box. */
var idPixels = function () {
  if (!S || S.kind !== 'three' || S.flat || !S.canvas || !S.camera) return null;
  var T = S.T, W = S.canvas.width, H = S.canvas.height;
  var meshes = [];
  var shown = function (o) {
    for (var p = o; p; p = p.parent) {
      if (p.visible === false) return false;
      if (p === S.root) break;
    }
    return true;
  };
  S.root.traverse(function (o) { if (o.isMesh && shown(o)) meshes.push(o); });
  if (!meshes.length || meshes.length > 60000) return null;
  var saved = [], rt = null, sc = S.scene;
  var was = { bg: sc.background, env: sc.environment, ov: sc.overrideMaterial, fog: sc.fog,
              ground: S.ground ? S.ground.visible : null, target: S.renderer.getRenderTarget() };
  var buf = null;
  try {
    meshes.forEach(function (o, i) {
      var id = i + 1, m0 = Array.isArray(o.material) ? o.material[0] : o.material;
      var m = new T.MeshBasicMaterial({ toneMapped: false, fog: false,
                                        side: (m0 && m0.side !== undefined) ? m0.side : T.FrontSide });
      /* Linear, straight into the working space: the id is written to the target as it is. */
      m.color.setRGB(((id >> 16) & 255) / 255, ((id >> 8) & 255) / 255, (id & 255) / 255,
                     T.LinearSRGBColorSpace);
      saved.push([o, o.material]);
      o.material = m;
    });
    rt = new T.WebGLRenderTarget(W, H, { depthBuffer: true });
    sc.background = new T.Color(0, 0, 0);
    sc.environment = null;
    sc.overrideMaterial = null;
    sc.fog = null;
    if (S.ground) S.ground.visible = false;
    S.renderer.setRenderTarget(rt);
    S.renderer.clear();
    S.renderer.render(sc, S.camera);
    buf = new Uint8Array(W * H * 4);
    S.renderer.readRenderTargetPixels(rt, 0, 0, W, H, buf);
  } catch (e) {
    buf = null;
  } finally {
    try { S.renderer.setRenderTarget(was.target || null); } catch (e2) {}
    for (var s = 0; s < saved.length; s++) {
      try { saved[s][0].material.dispose(); } catch (e3) {}
      saved[s][0].material = saved[s][1];
    }
    sc.background = was.bg; sc.environment = was.env; sc.overrideMaterial = was.ov; sc.fog = was.fog;
    if (S.ground && was.ground !== null) S.ground.visible = was.ground;
    try { if (rt) rt.dispose(); } catch (e4) {}
  }
  if (!buf) return null;
  var n = meshes.length;
  var cnt = new Float64Array(n + 1), sx = new Float64Array(n + 1), sy = new Float64Array(n + 1);
  var x0 = new Int32Array(n + 1).fill(1 << 30), y0 = new Int32Array(n + 1).fill(1 << 30);
  var x1 = new Int32Array(n + 1).fill(-1), y1 = new Int32Array(n + 1).fill(-1);
  for (var row = 0; row < H; row++) {
    var y = H - 1 - row;                        /* GL reads bottom up; the picture is top down */
    for (var x = 0; x < W; x++) {
      var k = (row * W + x) * 4;
      var id = (buf[k] << 16) | (buf[k + 1] << 8) | buf[k + 2];
      if (id < 1 || id > n) continue;
      cnt[id]++; sx[id] += x; sy[id] += y;
      if (x < x0[id]) x0[id] = x;
      if (x > x1[id]) x1[id] = x;
      if (y < y0[id]) y0[id] = y;
      if (y > y1[id]) y1[id] = y;
    }
  }
  /* One entry per NAME, as `where` reports: every mesh with that name, merged. */
  var by = {};
  for (var i = 1; i <= n; i++) {
    if (!cnt[i]) continue;
    var nm = nameOf(meshes[i - 1]) || '(unnamed)', e = by[nm];
    if (!e) e = by[nm] = { px: 0, sx: 0, sy: 0, x0: 1 << 30, y0: 1 << 30, x1: -1, y1: -1 };
    e.px += cnt[i]; e.sx += sx[i]; e.sy += sy[i];
    e.x0 = Math.min(e.x0, x0[i]); e.y0 = Math.min(e.y0, y0[i]);
    e.x1 = Math.max(e.x1, x1[i]); e.y1 = Math.max(e.y1, y1[i]);
  }
  var out = {};
  Object.keys(by).forEach(function (nm) {
    var e = by[nm];
    out[nm] = { px: e.px,
                box: [r3(e.x0 / W), r3(e.y0 / H), r3((e.x1 + 1) / W), r3((e.y1 + 1) / H)],
                at: [r3((e.sx / e.px + 0.5) / W), r3((e.sy / e.px + 0.5) / H)] };
  });
  return out;
};

F.where = function () {
  var out = [];
  if (!S || S.flat || !S.canvas) return out;
  var W = S.canvas.width || 1, H = S.canvas.height || 1;
  var pix = null;
  try { pix = idPixels(); } catch (e) { pix = null; }
  var rows = meshBoxes();
  var by = {}, order = [];
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i], e = by[r.name];
    if (!e) { e = by[r.name] = { name: r.name, n: 0, tris: 0, lo: null, hi: null }; order.push(e); }
    e.n++;
    e.tris += r.tris || 0;
    /* The screen box of every mesh with this name, merged. A row of four planks is one entry
       spanning all four, which is what "where are the planks" means. */
    for (var c = 0; c < 8; c++) {
      var pt = toPx(c & 1 ? r.hi[0] : r.lo[0], c & 2 ? r.hi[1] : r.lo[1], c & 4 ? r.hi[2] : r.lo[2]);
      if (!pt) continue;
      if (!e.lo) { e.lo = [pt[0], pt[1]]; e.hi = [pt[0], pt[1]]; continue; }
      e.lo[0] = Math.min(e.lo[0], pt[0]); e.lo[1] = Math.min(e.lo[1], pt[1]);
      e.hi[0] = Math.max(e.hi[0], pt[0]); e.hi[1] = Math.max(e.hi[1], pt[1]);
    }
  }
  for (var j = 0; j < order.length; j++) {
    var g = order[j];
    if (!g.lo) continue;
    var x0 = g.lo[0] / W, x1 = g.hi[0] / W, y0 = g.lo[1] / H, y1 = g.hi[1] / H;
    var row = {
      name: g.name, meshes: g.n, tris: Math.round(g.tris),
      box: [r3(x0), r3(y0), r3(x1), r3(y1)],
      at: [r3((x0 + x1) / 2), r3((y0 + y1) / 2)],
      size: [r3(x1 - x0), r3(y1 - y0)],
    };
    /* ...and, beside the projected box, what SHOWS of it: the visible pixels' box and centroid,
       and how many there are - 0 when it is measured and hidden, or out of the frame. */
    if (pix) {
      var p = pix[g.name];
      row.px = p ? p.px : 0;
      if (p) { row.px_box = p.box; row.px_at = p.at; }
    }
    out.push(row);
  }
  /* Left to right, then top to bottom: the order a person reads a picture in, so the list can be
     compared against the reference by eye without sorting it first. */
  out.sort(function (a, b) { return (a.at[0] - b.at[0]) || (a.at[1] - b.at[1]); });
  return out.slice(0, 60);
};

F.health = function () {
  /* What a modelling package says about a mesh and the bench did not: whether it has normals,
     whether any of its triangles have no area, and how much brightness is painted into its
     vertex colours. The last is not a defect by itself - it is the number behind "why is my hair
     one glossy tangle", because a tint is applied before a single lamp is switched on. */
  var out = [];
  if (!S || S.flat) return out;
  var by = {};
  var row = function (name) {
    var r = by[name];
    if (!r) {
      r = by[name] = { part: name, tris: 0, normals: true, degenerate: 0, lum: [], unlit: null };
      out.push(r);
    }
    return r;
  };
  /* THE TINT IS A MULTIPLIER ON THE MATERIAL'S COLOUR, so it is read as one. The first version took
     the darkest and the brightest vertex, and a normalised byte colour wider than 1.5 was guessed
     to be 0..255 per vertex: the goblin A/B's baked occlusion read "42.9 times lighter" on every
     part, one near-black crease vertex against one white one. Percentiles do not care about one
     vertex, and the scale now comes from the attribute itself - `normalized` and its array type -
     with the item size read too, so an RGBA colour is not read three channels at a time. */
  var colScale = function (arr, normalized) {
    if (normalized) {
      if (arr instanceof Uint8Array || arr instanceof Uint8ClampedArray) return 1 / 255;
      if (arr instanceof Uint16Array) return 1 / 65535;
      if (arr instanceof Int8Array) return 1 / 127;
      if (arr instanceof Int16Array) return 1 / 32767;
      return 1;
    }
    if (arr instanceof Float32Array || arr instanceof Float64Array) return 1;
    /* Integers with no flag: a PlayCanvas stream or a hand-built array. Bytes if anything is. */
    for (var i = 0; i < arr.length; i++) if (arr[i] > 1.5) return 1 / 255;
    return 1;
  };
  var scan = function (r, pos, idx, nrm, col, csize, cscale) {
    if (nrm == null || !nrm.length) r.normals = false;
    var n = idx && idx.length ? idx.length : (pos ? pos.length / 3 : 0);
    var tri = 0;
    for (var i = 0; i + 2 < n && tri < 200000; i += 3, tri++) {
      var a = (idx && idx.length ? idx[i] : i) * 3;
      var b = (idx && idx.length ? idx[i + 1] : i + 1) * 3;
      var c = (idx && idx.length ? idx[i + 2] : i + 2) * 3;
      var ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
      var vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
      var cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
      if (cx * cx + cy * cy + cz * cz < 1e-16) r.degenerate++;
    }
    r.tris += tri;
    if (col && csize >= 3 && col.length >= csize) {
      var nv = Math.floor(col.length / csize);
      var step = Math.max(1, Math.ceil(nv / 20000));
      for (var v = 0; v < nv; v += step) {
        var k = v * csize;
        r.lum.push((0.299 * col[k] + 0.587 * col[k + 1] + 0.114 * col[k + 2]) * cscale);
      }
    }
  };
  try {
    if (S.kind === 'three') {
      S.root.traverse(function (o) {
        if (!o.isMesh || !o.geometry) return;
        var g = o.geometry, at = g.attributes || {};
        if (!at.position) return;
        /* UNLIT = the vertex colour IS the picture. A tint on a lit part is light painted in
           before any lamp (the hair); on MeshBasicMaterial it is the painting itself. */
        var hr = row(nameOf(o) || '(unnamed)');
        var hm = Array.isArray(o.material) ? o.material : [o.material];
        var basic = hm.length > 0, uses = false;
        for (var hi = 0; hi < hm.length; hi++) {
          if (!(hm[hi] && hm[hi].isMeshBasicMaterial)) basic = false;
          if (hm[hi] && hm[hi].vertexColors) uses = true;
        }
        hr.unlit = hr.unlit === null ? basic : (hr.unlit && basic);
        /* A colour attribute the material does not read tints nothing. */
        var ca = uses && at.color ? at.color : null;
        var carr = null, csize = 0, cscale = 1;
        if (ca && ca.isInterleavedBufferAttribute) {
          /* getX() already turns a normalised value back into 0..1, so no scale on top of it. */
          carr = [];
          for (var ci = 0; ci < ca.count; ci++) carr.push(ca.getX(ci), ca.getY(ci), ca.getZ(ci));
          csize = 3;
        } else if (ca) {
          carr = ca.array;
          csize = ca.itemSize || 3;
          cscale = colScale(ca.array, !!ca.normalized);
        }
        scan(hr, at.position.array,
             g.index ? g.index.array : null,
             at.normal ? at.normal.array : null,
             carr, csize, cscale);
      });
    } else {
      var walk = function (e) {
        var mis = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        for (var i = 0; i < mis.length; i++) {
          var m = mis[i].mesh;
          if (!m || !m.getPositions) continue;
          var pos = [], idx = [], nrm = [], col = [], cn = 0;
          try { m.getPositions(pos); } catch (e2) { continue; }
          try { m.getIndices(idx); } catch (e3) { idx = []; }
          try { m.getNormals(nrm); } catch (e4) { nrm = []; }
          try { cn = m.getVertexStream(S.pc.SEMANTIC_COLOR, col) || 0; } catch (e5) { col = []; }
          var pr0 = row(e.name || '(unnamed)');
          var pmat = mis[i].material;
          var pflat = !!(pmat && pmat.useLighting === false);
          pr0.unlit = pr0.unlit === null ? pflat : (pr0.unlit && pflat);
          var csz = cn > 0 && col.length ? Math.round(col.length / cn) : 4;
          scan(pr0, pos, idx, nrm, col, csz, col.length ? colScale(col, false) : 1);
        }
        var ch = e.children || [];
        for (var j = 0; j < ch.length; j++) walk(ch[j]);
      };
      walk(S.root);
    }
  } catch (e) { /* a measurement must never cost the picture */ }
  var pct = function (sorted, p) {
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))];
  };
  for (var q = 0; q < out.length; q++) {
    var r2 = out[q];
    r2.tint = null;
    if (r2.lum.length >= 3) {
      var ls = r2.lum.slice().sort(function (x, y) { return x - y; });
      var p5 = pct(ls, 0.05), p95 = pct(ls, 0.95);
      /* 5th and 95th percentile of the tint's luma, 1.0 = the material's own colour. */
      r2.tint_p5 = Math.round(p5 * 1000) / 1000;
      r2.tint_p95 = Math.round(p95 * 1000) / 1000;
      r2.tint = p5 > 0.001 ? Math.round((p95 / p5) * 100) / 100 : null;
    }
    delete r2.lum;
    if (r2.unlit === null) r2.unlit = false;
  }
  out.sort(function (a, b) { return b.tris - a.tris; });
  return out.slice(0, 40);
};

/* ------------------------------------------------------------------ symmetry
   WHAT A FRONT VIEW WOULD SHOW, WITHOUT TAKING ONE. The user looked at the forge goblin from the
   front and saw the belt was not in the middle: the ring was centred, but `buckle` sat 4.6 cm to
   the goblin's left, `tassets` 5.8 cm and `keeper` 3.9 cm the other way - and the Blender goblin's
   buckle 6.6 cm. Both builders had moved centred parts SIDEWAYS to land them on the pixels of a
   three-quarter picture, where a centred buckle already sits right of the belt's screen centre.
   Nothing on the bench looked from the front unless asked. So every part's exact world box (its
   vertices, not a transformed bounding box) and its surface area - which, unlike a box, does not
   change when a twin is posed - go to the backend, which says what is off (live.py). */
F.symmetry = function () {
  var res = { parts: [], box: null };
  if (!S || S.flat) return res;
  var b = bounds();
  if (!b) return res;
  res.box = { lo: [b.c[0] - b.size[0] / 2, b.c[1] - b.size[1] / 2, b.c[2] - b.size[2] / 2],
              hi: [b.c[0] + b.size[0] / 2, b.c[1] + b.size[1] / 2, b.c[2] + b.size[2] / 2] };
  var by = {};
  /* The groups a part sits in, so a blade under `sword` is known for a held prop. The root the
     bench names after the file (F.glb) is not one of them: "knight_with_sword.glb" is a knight. */
  var under = function (o) {
    var names = [];
    for (var p = o.parent; p && p !== S.root; p = p.parent) {
      if (!p.__forgeGlb && p.name && names.length < 6) names.push(String(p.name));
    }
    return names;
  };
  var add = function (name, lo, hi, area, tris, ups) {
    var e = by[name];
    if (!e) {
      e = by[name] = { name: name, meshes: 0, tris: 0, area: 0, lo: lo.slice(), hi: hi.slice(), under: [] };
      res.parts.push(e);
    }
    e.meshes++; e.tris += tris; e.area += area;
    for (var k = 0; k < 3; k++) { e.lo[k] = Math.min(e.lo[k], lo[k]); e.hi[k] = Math.max(e.hi[k], hi[k]); }
    (ups || []).forEach(function (u) { if (e.under.indexOf(u) < 0 && e.under.length < 6) e.under.push(u); });
  };
  var one = function (name, P, idx, ups) {
    var n = P.length / 3, lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (var i = 0; i < n; i++) {
      for (var k = 0; k < 3; k++) {
        var v = P[3 * i + k];
        if (v < lo[k]) lo[k] = v;
        if (v > hi[k]) hi[k] = v;
      }
    }
    if (!(n > 0)) return;
    var tris = idx ? Math.floor(idx.length / 3) : Math.floor(n / 3), area = 0;
    for (var t = 0; t < tris && t < 400000; t++) {
      var a = 3 * (idx ? idx[3 * t] : 3 * t), c1 = 3 * (idx ? idx[3 * t + 1] : 3 * t + 1),
          c2 = 3 * (idx ? idx[3 * t + 2] : 3 * t + 2);
      var ux = P[c1] - P[a], uy = P[c1 + 1] - P[a + 1], uz = P[c1 + 2] - P[a + 2];
      var vx = P[c2] - P[a], vy = P[c2 + 1] - P[a + 1], vz = P[c2 + 2] - P[a + 2];
      var cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
      area += Math.sqrt(cx * cx + cy * cy + cz * cz) / 2;
    }
    add(name, lo, hi, area, tris, ups);
  };
  try {
    if (S.kind === 'three') {
      S.root.updateMatrixWorld(true);
      S.root.traverse(function (o) {
        if (!o.isMesh || !o.geometry || !o.geometry.attributes || !o.geometry.attributes.position) return;
        var pa = o.geometry.attributes.position, e = o.matrixWorld.elements;
        var P = new Float64Array(pa.count * 3);
        for (var i = 0; i < pa.count; i++) {
          var x = pa.getX(i), y = pa.getY(i), z = pa.getZ(i);
          P[3 * i] = e[0] * x + e[4] * y + e[8] * z + e[12];
          P[3 * i + 1] = e[1] * x + e[5] * y + e[9] * z + e[13];
          P[3 * i + 2] = e[2] * x + e[6] * y + e[10] * z + e[14];
        }
        one(nameOf(o) || '(unnamed)', P, o.geometry.index ? o.geometry.index.array : null, under(o));
      });
    } else {
      (function walk(en) {
        var mis = (en.render && en.render.meshInstances) || (en.model && en.model.meshInstances) || [];
        for (var i = 0; i < mis.length; i++) {
          var p = [], ix = [];
          try { mis[i].mesh.getPositions(p); } catch (e1) { continue; }
          try { mis[i].mesh.getIndices(ix); } catch (e2) { ix = []; }
          var d = mis[i].node.getWorldTransform().data, P = new Float64Array(p.length);
          for (var k = 0; k + 2 < p.length; k += 3) {
            P[k] = d[0] * p[k] + d[4] * p[k + 1] + d[8] * p[k + 2] + d[12];
            P[k + 1] = d[1] * p[k] + d[5] * p[k + 1] + d[9] * p[k + 2] + d[13];
            P[k + 2] = d[2] * p[k] + d[6] * p[k + 1] + d[10] * p[k + 2] + d[14];
          }
          one(en.name || '(unnamed)', P, ix.length ? ix : null, under(en));
        }
        var kids = en.children || [];
        for (var j = 0; j < kids.length; j++) walk(kids[j]);
      })(S.root);
    }
  } catch (e) { /* a measurement must never cost the picture */ }
  res.parts.forEach(function (p) {
    p.lo = p.lo.map(r6); p.hi = p.hi.map(r6); p.area = r6(p.area);
  });
  res.box.lo = res.box.lo.map(r6); res.box.hi = res.box.hi.map(r6);
  return res;
};

F.stance = function () {
  var out = { contacts: [], count: 0, front: 0, rear: 0, meshes_touching: 0 };
  var b = bounds();
  if (!S || !b || S.flat) return out;
  var rows = meshBoxes();
  if (!rows.length) return out;
  var floor = 1e9;
  for (var i = 0; i < rows.length; i++) floor = Math.min(floor, rows[i].lo[1]);
  /* A tolerance in the subject's own scale: a foot on the floor and a foot 2% of the body above
     it are the same thing, and a fixed epsilon is wrong at every other scale. */
  var tol = Math.max(1e-4, b.size[1] * 0.02);
  out.floor = r3(floor);
  out.tolerance = r3(tol);
  /* Distinct FOOTPRINTS, clustered in the ground plane. Six meshes on one foot is one contact,
     and it was reporting six. */
  var near = Math.max(1e-4, Math.max(b.size[0], b.size[2]) * 0.12);
  var clusters = [];
  for (var j = 0; j < rows.length; j++) {
    var r = rows[j];
    if (r.lo[1] > floor + tol) continue;
    var x = (r.lo[0] + r.hi[0]) / 2, z = (r.lo[2] + r.hi[2]) / 2;
    var got = null;
    for (var m = 0; m < clusters.length; m++) {
      var dx = clusters[m].x - x, dz = clusters[m].z - z;
      if (Math.sqrt(dx * dx + dz * dz) <= near) { got = clusters[m]; break; }
    }
    if (!got) {
      got = { x: x, z: z, n: 0, names: [] };
      clusters.push(got);
    }
    got.n++;
    if (got.names.indexOf(r.name) < 0 && got.names.length < 4) got.names.push(r.name);
  }
  for (var c = 0; c < clusters.length; c++) {
    var k = clusters[c];
    /* The forge asks for the subject to face -Z, so a contact in front of the centre is a FRONT
       foot. Stated rather than assumed silently, because it is the one convention here that a
       differently-oriented asset would break. */
    var side = (k.z < b.c[2] ? 'front' : 'rear') + (k.x < b.c[0] ? '-left' : '-right');
    out.contacts.push({ at: [r3(k.x), r3(k.z)], side: side, meshes: k.n,
                        names: k.names.join('+') });
    if (k.z < b.c[2]) out.front++; else out.rear++;
  }
  out.count = out.contacts.length;              /* distinct spots, not distinct meshes */
  var touching = 0;
  for (var t = 0; t < clusters.length; t++) touching += clusters[t].n;
  out.meshes_touching = touching;
  return out;
};

/* WHERE EACH PRESET STANDS - the direction from the subject to the camera. glTF's convention, which
   is the forge's: the subject faces +Z and its OWN left is +X (face it, and its left hand is on your
   right). So `front` stands at +Z; `side` stands at +X and shows the subject's LEFT side; `left`
   stands at -X - on your left as you face the subject - and shows its RIGHT side. The names are
   kept because agents already use them; this is what they show. */
var DIRS = {
  '3q': [1, 0.62, 1.15], front: [0, 0, 1], back: [0, 0, -1], side: [1, 0, 0],
  left: [-1, 0, 0], top: [0, 1, 0.0001], bottom: [0, -1, 0.0001],
  low: [0.8, -0.32, 1], hero: [0.55, 0.28, 1], back3q: [-1, 0.5, -1]
};

/* Where every corner of the subject lands on screen. 1.0 is exactly the frame edge.
   A bounding SPHERE and a fixed margin is the usual way to frame, and it is what crops assets:
   the sphere fits, the silhouette does not, and a crest or a foot leaves the frame. Asking the
   engine to project the eight bounding-box corners gives the real answer instead. */
var worstNdc = function (b) {
  if (!b) b = bounds();
  if (!b) return 0;
  var worst = 0;
  try {
    for (var i = 0; i < 8; i++) {
      var x = b.c[0] + (i & 1 ? 0.5 : -0.5) * b.size[0];
      var y = b.c[1] + (i & 2 ? 0.5 : -0.5) * b.size[1];
      var z = b.c[2] + (i & 4 ? 0.5 : -0.5) * b.size[2];
      if (S.kind === 'three') {
        var v = new S.T.Vector3(x, y, z).project(S.camera);
        if (v.z > 1) return 99;                       /* behind the camera: back off hard */
        worst = Math.max(worst, Math.abs(v.x), Math.abs(v.y));
      } else {
        var p = S.camera.camera.worldToScreen(new S.pc.Vec3(x, y, z));
        worst = Math.max(worst,
                         Math.abs(p.x / S.canvas.width * 2 - 1),
                         Math.abs(p.y / S.canvas.height * 2 - 1));
      }
    }
  } catch (e) { return 0; }
  return worst;
};

/* The last word, because a bounding box can under-report — a skinned mesh, a particle system or
   a shader that displaces vertices all draw outside it. So after framing, LOOK: if the subject
   touches the border of the actual rendered pixels, back off and render again. */
var touchesBorder = function () {
  try {
    if (S.opts.ground || S.opts.sky) return false;    /* the backdrop legitimately fills the edge */
    var n = 96;
    var t = document.createElement('canvas');
    t.width = n; t.height = n;
    var x = t.getContext('2d');
    x.drawImage(S.canvas, 0, 0, n, n);
    var d = x.getImageData(0, 0, n, n).data;
    /* The MEASURED backdrop, not the nominal one. three.js encodes its output to sRGB, so the
       pixel it draws for `#1a1e26` is not `#1a1e26` — comparing against the hex reported every
       border pixel as the subject and every asset as clipped. Sampled once from an empty studio,
       this is right whatever the renderer does to colour. A pass that paints its own backdrop
       (the silhouette's near-white) is measured against its own sample - see setPass. */
    var bg = (passState && passState.bgPixel) || S.bgPixel || (function () {
      var c = hexToRgb(S.opts.background);
      return [c.r * 255, c.g * 255, c.b * 255];
    })();
    var br = bg[0], bgc = bg[1], bb = bg[2];
    var hit = function (px, py) {
      var i = (py * n + px) * 4;
      return Math.abs(d[i] - br) + Math.abs(d[i + 1] - bgc) + Math.abs(d[i + 2] - bb) > 26;
    };
    for (var k = 0; k < n; k++) {
      if (hit(k, 0) || hit(k, n - 1) || hit(0, k) || hit(n - 1, k)) return true;
    }
  } catch (e) {}
  return false;
};

/* The backdrop pixel of the studio as it is drawn NOW, with the subject hidden for one frame. */
var sampleEmpty = function () {
  var r = S && S.root, was = null;
  try {
    if (r) {
      if (S.kind === 'three') { was = r.visible; r.visible = false; }
      else { was = r.enabled; r.enabled = false; }
    }
    F.shot();
    var t = document.createElement('canvas'); t.width = 1; t.height = 1;
    var xc = t.getContext('2d');
    xc.drawImage(S.canvas, 2, 2, 1, 1, 0, 0, 1, 1);
    var px = xc.getImageData(0, 0, 1, 1).data;
    return [px[0], px[1], px[2]];
  } catch (e) {
    return null;
  } finally {
    if (r && was !== null) {
      if (S.kind === 'three') r.visible = was; else r.enabled = was;
    }
  }
};

/* `hold` fixes the distance to a radius the caller chose, instead of fitting this subject.
   A sweep is the reason. Framed independently, a blade 60% wider is drawn the same size as a
   narrow one and the panels compare nothing — the only thing being varied is the thing the
   framing cancels out. One radius for the whole set, and the difference is what you see. */
/* A NAMED PRESET IS A CONVENIENCE. IT MUST NEVER BE THE LIMIT.
   Ten names cannot contain the angle a particular photograph was taken from, and an agent asked
   to match a picture needs exactly that angle. So a view is either a preset name or a spec:

     az=35            spin around the subject; 0 looks at its front (+Z), 90 at its LEFT side
                      (+X, its own left under glTF), 180 at its back, 270 at its right side (-X)
     az=35,el=12      and lift the camera 12 degrees above the horizon
     az=35,el=12,zoom=2   fill twice as much of the panel with it

   zoom divides the framing margin, so zoom=3 is a close-up of the whole subject rather than of
   one named part -- `focus` is still the right tool when the part has a name.

   A WRITTEN ZOOM IS A CAMERA. Without one the fit below may back the camera off a subject that
   touches the border; with one, the camera stands exactly where the numbers say, every time. The
   anchor solve depends on that: the view it answers with has to be the camera it solved. */
var dirOf = function (name) {
  var s = String(name || '');
  if (s.indexOf('=') < 0) return { d: DIRS[s] || DIRS['3q'], zoom: 1, pinned: false };
  var az = 0, el = 0, zoom = 1, pinned = false;
  /* A PRESET WITH MODIFIERS - "top,zoom=2.5". It fell through to az=0,el=0, a front view at the
     zoom asked for. The preset now sets the starting az and el, and the modifiers change them. */
  var first = s.split(/[,;\s]+/)[0];
  if (first && first.indexOf('=') < 0 && DIRS[first]) {
    var d0 = DIRS[first];
    var n0 = Math.sqrt(d0[0] * d0[0] + d0[1] * d0[1] + d0[2] * d0[2]) || 1;
    az = Math.atan2(d0[0], d0[2]) * 180 / Math.PI;
    el = Math.asin(Math.max(-1, Math.min(1, d0[1] / n0))) * 180 / Math.PI;
  }
  s.split(/[,;\s]+/).forEach(function (kv) {
    var p = kv.split('=');
    if (p.length !== 2) return;
    var k = p[0].trim().toLowerCase(), v = parseFloat(p[1]);
    if (!isFinite(v)) return;
    if (k === 'az' || k === 'azimuth') az = v;
    else if (k === 'el' || k === 'elev' || k === 'elevation') el = v;
    else if (k === 'zoom' || k === 'z') { zoom = v; pinned = true; }
  });
  var a = az * Math.PI / 180;
  var e = Math.max(-89, Math.min(89, el)) * Math.PI / 180;
  return { d: [Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e)],
           zoom: zoom > 0 ? Math.max(0.15, Math.min(20, zoom)) : 1, pinned: pinned && zoom > 0 };
};

/* THE LOOK OF THE STUDIO, PER CALL. The rig was fixed for the life of the page, and a bench
   whose ambient floor renders pure black as #2c2e30 cannot judge a near-black hoodie against a
   reference whose hoodie is #1b1b1a. Measured on the roblox-boy A/B: the only way to reach black
   was to switch the environment off, and that removed every reflection with it. Presets first,
   then numbers for anyone who wants them. */
var RIGS = {
  studio:    { key: 1.00, fill: 1.00, rim: 1.00, amb: 1.00, env: 1.00 },
  flat:      { key: 0.55, fill: 0.90, rim: 0.35, amb: 1.70, env: 1.30 },
  reference: { key: 1.15, fill: 0.55, rim: 0.95, amb: 0.10, env: 0.12 },
  hard:      { key: 1.35, fill: 0.25, rim: 0.60, amb: 0.35, env: 0.45 }
};

var num = function (v, dflt, lo, hi) {
  var n = (v === undefined || v === null || v === '') ? dflt : +v;
  if (!isFinite(n)) n = dflt;
  return Math.max(lo, Math.min(hi, n));
};

var orthoCam = function () {
  if (!S.cameraO) S.cameraO = new S.T.OrthographicCamera(-1, 1, 1, -1, 0.001, 10000);
  return S.cameraO;
};

F.studio = function (o) {
  if (!S || S.flat) return null;
  o = o || {};
  var r = RIGS[String(o.light || 'studio')] || RIGS.studio;
  var envF = num(o.env, 1, 0, 4) * r.env;
  var ambF = num(o.ambient, 1, 0, 4) * r.amb;
  var expF = num(o.exposure, 1, 0.05, 6);
  var mul = o.lights || {};
  var f = function (k) { return r[k] * num(mul[k], 1, 0, 4); };
  S.ortho = !!o.ortho;
  if (o.shadows !== undefined) S.shadows = !!o.shadows;
  S.shadowProbed = false;
  S.shadowSeen = null;
  try {
    if (S.kind === 'three') {
      S.lights.key.intensity = S.base.key * f('key');
      S.lights.fill.intensity = S.base.fill * f('fill');
      S.lights.rim.intensity = S.base.rim * f('rim');
      S.lights.amb.intensity = S.base.amb * ambF;
      S.scene.environment = envF > 0.001 ? (S.envTex || null) : null;
      if (S.scene.environmentIntensity !== undefined) S.scene.environmentIntensity = envF;
      if (S.renderer.toneMappingExposure !== undefined) S.renderer.toneMappingExposure = expF;
      if (o.background) { try { S.scene.background = new S.T.Color(o.background); } catch (e) {} }
      /* THE SHADOW is applied by F.shadowCast, at DRAW time. Calling it here as well is only so
         a `look` that never reaches F.view still has a consistent renderer. See shadowCast. */
      F.shadowCast();
    } else {
      S.lights.key.light.intensity = S.base.key * f('key');
      S.lights.fill.light.intensity = S.base.fill * f('fill');
      S.lights.rim.light.intensity = S.base.rim * f('rim');
      var a = S.base.amb;
      S.app.scene.ambientLight = new S.pc.Color(a[0] * ambF, a[1] * ambF, a[2] * ambF);
      S.app.scene.envAtlas = envF > 0.001 ? (S.envAtlas || null) : null;
      S.app.scene.skyboxIntensity = envF;
      if (S.app.scene.exposure !== undefined) S.app.scene.exposure = expF;
      if (o.background) {
        var bgc = hexToRgb(o.background);
        S.camera.camera.clearColor = new S.pc.Color(bgc.r, bgc.g, bgc.b);
      }
      F.shadowCast();
    }
  } catch (e) { /* an engine build without one of these still gets the rest */ }
  S.look = { light: String(o.light || 'studio'), env: r3(envF), ambient: r3(ambF),
             exposure: r3(expF), ortho: !!S.ortho, shadows: !!S.shadows,
             /* HOW MANY MESHES WERE ACTUALLY FLAGGED. `shadows: true` with `casters: 0` is the
                exact state this switch sat in for its whole life, and no other field showed it. */
             casters: S.casters || 0,
             key: r3(f('key')), fill: r3(f('fill')), rim: r3(f('rim')) };
  /* The backdrop sample every clipping check measures against must follow a background change. */
  if (o.background && S.opts && o.background !== S.opts.background) {
    S.opts.background = o.background;
    try {
      F.shot();
      var t = document.createElement('canvas'); t.width = 1; t.height = 1;
      var xc = t.getContext('2d');
      xc.drawImage(S.canvas, 2, 2, 1, 1, 0, 0, 1, 1);
      var px = xc.getImageData(0, 0, 1, 1).data;
      S.bgPixel = [px[0], px[1], px[2]];
    } catch (e) {}
  }
  return S.look;
};

/* THE DARKEST AND THE BRIGHTEST THING ACTUALLY DRAWN. An agent rendered a swatch strip by hand to
   learn whether its black could ever be black; this answers it from the frame already taken. */
F.levels = function () {
  if (!S || !S.canvas) return null;
  try {
    var c = document.createElement('canvas');
    var w = Math.min(240, S.canvas.width), h = Math.min(180, S.canvas.height);
    c.width = w; c.height = h;
    var cx = c.getContext('2d');
    cx.drawImage(S.canvas, 0, 0, w, h);
    var d = cx.getImageData(0, 0, w, h).data;
    var bg = S.bgPixel || [26, 30, 38];
    var lo = null, hi = null, n = 0, sum = 0;
    for (var i = 0; i < d.length; i += 4) {
      var off = Math.abs(d[i] - bg[0]) + Math.abs(d[i + 1] - bg[1]) + Math.abs(d[i + 2] - bg[2]);
      if (off < 24) continue;                    /* the backdrop, not the subject */
      var l = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
      n++; sum += l;
      if (!lo || l < lo[3]) lo = [d[i], d[i + 1], d[i + 2], l];
      if (!hi || l > hi[3]) hi = [d[i], d[i + 1], d[i + 2], l];
    }
    if (!n) return null;
    var hex = function (p) {
      return '#' + [p[0], p[1], p[2]].map(function (v) {
        return ('0' + Math.round(v).toString(16)).slice(-2); }).join('');
    };
    return { darkest: hex(lo), brightest: hex(hi), mean_luma: Math.round(sum / n),
             backdrop: hex(bg), pixels: n };
  } catch (e) { return null; }
};

/* A GLB ON THE BENCH, AND THE BENCH OUT AS A GLB. An asset that came from somewhere else -
   Blender, a store, another agent - has to be judgeable with the same camera and the same numbers,
   or "ours against theirs" is two different pictures again. The loader URL is handed in by the
   backend, which knows where its own copy of three lives; PlayCanvas needs no loader at all. */
/* ONE example module (the glTF loader or exporter), bound to the three THIS page built with.
   First the Studio's vendored copy with `three=` set to the page's own engine URL, so the module
   and the bench share one copy of the engine; then the file beside a packaged build, but only when
   the engine really is under build/; then node_modules. A module counts only if it HAS the class.
   The old loop took whatever imported first: on a page that loads three from its root the
   "exporter" was three.module.js itself, and the answer was "Ex is not a constructor". */
F.exampleModule = async function (vendorUrl, sub, cls) {
  var cands = [], last = '';
  var href = F.engineHref();
  if (vendorUrl) cands.push(href ? vendorUrl + '&three=' + encodeURIComponent(href) : vendorUrl);
  var eng = String(S.engineUrl || '');
  if (/build\/three[^/]*$/.test(eng)) cands.push(eng.replace(/build\/three[^/]*$/, 'examples/jsm/' + sub));
  cands.push('/node_modules/three/examples/jsm/' + sub);
  for (var i = 0; i < cands.length; i++) {
    try {
      var mod = await import(/* @vite-ignore */ cands[i]);
      var C = mod && (mod[cls] || (mod.default && mod.default[cls]));
      if (typeof C === 'function') return { C: C, url: cands[i] };
      last = cands[i].split('?')[0] + ' has no ' + cls;
    } catch (e) { last = String(e && e.message || e).slice(0, 160); }
  }
  return { error: 'no ' + cls + ' could be loaded: ' + last };
};

F.glb = async function (url, name, loaderUrl) {
  if (!S || S.flat) return { ok: false, error: 'no studio' };
  try {
    if (S.kind === 'playcanvas') {
      var app = S.app;
      var asset = await new Promise(function (res, rej) {
        app.assets.loadFromUrl(url, 'container', function (err, a) {
          if (err) rej(new Error(String(err))); else res(a);
        });
      });
      var ent = asset.resource.instantiateRenderEntity();
      ent.name = name || 'glb';
      /* A plain property, never userData: GLTFExporter writes userData into the file's extras. */
      ent.__forgeGlb = true;
      S.root.addChild(ent);
      return { ok: true, name: ent.name, engine: 'playcanvas' };
    }
    var got = await F.exampleModule(loaderUrl, 'loaders/GLTFLoader.js', 'GLTFLoader');
    if (!got.C) return { ok: false, error: got.error };
    var gltf = await new got.C().loadAsync(url);
    var obj = gltf.scene || (gltf.scenes && gltf.scenes[0]);
    if (!obj) return { ok: false, error: 'the file held no scene' };
    obj.name = name || 'glb';
    obj.__forgeGlb = true;
    S.root.add(obj);
    return { ok: true, name: obj.name, engine: 'three' };
  } catch (e) { return { ok: false, error: String(e && e.message || e).slice(0, 300) }; }
};

F.exportGlb = async function (exporterUrl) {
  if (!S || S.flat) return { ok: false, error: 'no studio' };
  var toB64 = function (buf) {
    var b = new Uint8Array(buf), s = '', CH = 0x8000;
    for (var i = 0; i < b.length; i += CH) {
      s += String.fromCharCode.apply(null, b.subarray(i, i + CH));
    }
    return btoa(s);
  };
  try {
    /* THE ASSET, NOT THE BENCH. Exporting S.root wrote the forge's own wrapper node
       ('forge-subject') above the asset, inside a scene three calls 'AuxScene', so a Blender
       import got an extra empty parent. The asset's own nodes go out instead - only while the
       wrapper carries no transform, or a turned bench would export turned. */
    if (S.kind === 'playcanvas') {
      var pk = S.root.children || [];
      var one = pk.length === 1 ? pk[0] : S.root;
      var buf = await new S.pc.GltfExporter().build(one, { maxTextureSize: 2048 });
      return { ok: true, bytes: buf.byteLength, b64: toB64(buf), root: one.name || '' };
    }
    var got = await F.exampleModule(exporterUrl, 'exporters/GLTFExporter.js', 'GLTFExporter');
    if (!got.C) return { ok: false, error: got.error };
    var kids = S.root.children.slice();
    var sc0 = S.root.scale;
    var plain = S.root.position.lengthSq() < 1e-12 && Math.abs(S.root.quaternion.w) > 0.999999 &&
                Math.abs(sc0.x - 1) + Math.abs(sc0.y - 1) + Math.abs(sc0.z - 1) < 1e-9;
    var src = S.root, sc = null;
    if (kids.length && plain) {
      sc = new S.T.Scene();
      sc.name = kids.length === 1 && kids[0].name ? kids[0].name : 'asset';
      for (var k = 0; k < kids.length; k++) sc.add(kids[k]);
      src = sc;
    }
    var out;
    try {
      out = await new Promise(function (res, rej) {
        new got.C().parse(src, res, rej, { binary: true });
      });
    } finally {
      if (sc) for (var k2 = 0; k2 < kids.length; k2++) S.root.add(kids[k2]);
    }
    return { ok: true, bytes: out.byteLength, b64: toB64(out), root: sc ? sc.name : (S.root.name || '') };
  } catch (e) { return { ok: false, error: String(e && e.message || e).slice(0, 300) }; }
};

/* THE KEY LIGHT, MOVED. A matte hair and a glossy hair are the same picture under one fixed lamp,
   which is why three attempts at gloss on this bench changed nothing measurable. Degrees are
   relative to the camera, so a sweep means the same thing from any angle. */
F.keyAt = function (deg) {
  if (!S || S.flat || !S.lights || !S.lights.key) return null;
  /* Where the rig put it, kept once, so a sweep can always put it back. */
  try {
    if (S.keyHome === undefined) {
      S.keyHome = S.kind === 'three'
        ? [S.lights.key.position.x, S.lights.key.position.y, S.lights.key.position.z]
        : [S.lights.key.getLocalEulerAngles().x, S.lights.key.getLocalEulerAngles().y,
           S.lights.key.getLocalEulerAngles().z];
    }
    if (deg === null || deg === undefined || deg === 'home') {
      if (S.kind === 'three') {
        S.lights.key.position.set(S.keyHome[0], S.keyHome[1], S.keyHome[2]);
        S.lights.key.updateMatrixWorld(true);
      } else {
        S.lights.key.setLocalEulerAngles(S.keyHome[0], S.keyHome[1], S.keyHome[2]);
      }
      return { key_at: 'home' };
    }
  } catch (e) { return null; }
  var aim = dirOf(S.view || '3q');
  var camAz = Math.atan2(aim.d[0], aim.d[2]);
  var a = camAz + (Number(deg) || 0) * Math.PI / 180;
  var el = 35 * Math.PI / 180;
  var dx = Math.sin(a) * Math.cos(el), dy = Math.sin(el), dz = Math.cos(a) * Math.cos(el);
  try {
    if (S.kind === 'three') {
      S.lights.key.position.set(dx * 10, dy * 10, dz * 10);
      S.lights.key.updateMatrixWorld(true);
    } else {
      S.lights.key.setEulerAngles(-35, Math.atan2(dx, dz) * 180 / Math.PI + 180, 0);
    }
  } catch (e) { return null; }
  return { key_at: Math.round(Number(deg) || 0) };
};

F.resize = function (w, h) {
  /* THE SIZE CHANGES, THE SCENE DOES NOT. `F.ensure` disposes on a size change - correct for a
     rebuild, fatal for `look`, whose whole purpose is to photograph what is already there. So
     `look` had no width and no height, and a part worth a close-up came back at the size the
     last build happened to use. */
  if (!S || !S.canvas) return null;
  w = Math.max(64, Math.min(2048, Math.round(Number(w) || 0)));
  h = Math.max(64, Math.min(2048, Math.round(Number(h) || 0)));
  if (!w || !h || (S.canvas.width === w && S.canvas.height === h)) {
    return { width: S.canvas.width, height: S.canvas.height, changed: false };
  }
  try {
    S.canvas.width = w;
    S.canvas.height = h;
    S.canvas.style.width = w + 'px';
    S.canvas.style.height = h + 'px';
    if (S.kind === 'three') {
      S.renderer.setSize(w, h, false);
      if (S.cameraP) { S.cameraP.aspect = w / h; S.cameraP.updateProjectionMatrix(); }
      if (S.cameraO) { S.cameraO = null; }        /* rebuilt by orthoCam at the new aspect */
    } else {
      S.app.setCanvasResolution(S.pc.RESOLUTION_FIXED, w, h);
      S.app.resizeCanvas(w, h);
    }
  } catch (e) { return { width: S.canvas.width, height: S.canvas.height, changed: false,
                         error: String(e && e.message || e).slice(0, 160) }; }
  return { width: w, height: h, changed: true };
};

F.shadowCast = function () {
  /* WHY THIS IS NOT IN F.studio, WHERE IT LIVED AND DID NOTHING.

     `F.ensure` calls `F.studio(opts)` BEFORE the agent's code runs, so this traverse walked an
     EMPTY root every single time. The light reported castShadows true, the shadow camera was
     fitted, the map was enabled, and not one mesh in the picture was ever flagged - three.js
     defaults Mesh.castShadow to false, so everything built after that moment cast nothing and
     received nothing. Measured: a box over a plate rendered identical to the pixel with the
     switch on and off.

     Flags go on at DRAW time now, on whatever is in the scene when the shutter opens, and the
     count comes back so a call can tell the difference between "no shadows" and "no meshes". */
  if (!S) return 0;
  var cnt = 0;
  try {
    if (S.kind === 'three') {
      S.renderer.shadowMap.enabled = !!S.shadows;
      try { if (S.shadows) S.renderer.shadowMap.type = S.T.PCFSoftShadowMap; } catch (e) {}
      S.lights.key.castShadow = !!S.shadows;
      try { S.lights.key.shadow.mapSize.set(2048, 2048); } catch (e) {}
      /* Every mesh both casts and receives, because the shadow that matters here is the one a
         strand throws on the strand behind it, not the one the figure throws on a floor that is
         usually not even there. */
      S.root.traverse(function (n) {
        if (n && n.isMesh) { n.castShadow = !!S.shadows; n.receiveShadow = !!S.shadows; cnt++; }
      });
      if (S.ground) S.ground.receiveShadow = !!S.shadows;
    } else {
      var kl = S.lights.key.light;
      kl.castShadows = !!S.shadows;
      if (S.shadows) {
        kl.shadowResolution = 2048;
        if (S.pc.SHADOW_PCF3 !== undefined) kl.shadowType = S.pc.SHADOW_PCF3;
        /* A starting bias only. `F.shadowFit` scales it by the subject's radius, because the
           depth error a shadow texel can hide grows with the world size the map is stretched
           over, and a constant tuned on a figure puts acne across a wide floor. */
        kl.normalOffsetBias = 0.05;
        kl.shadowBias = -0.0006;
      }
      /* BOTH COMPONENT KINDS, AND THE MESH INSTANCES. A GLB's meshes arrive under `model`, not
         `render`, and the component flag does not always reach the instance - measured: the
         light cast, the walk found zero render components, and the picture was identical with
         the switch on and off. `MeshInstance.castShadow` is the level the renderer reads. */
      (function walk(e) {
        if (!e) return;
        var comps = [e.render, e.model];
        for (var c = 0; c < comps.length; c++) {
          var comp = comps[c];
          if (!comp) continue;
          try { comp.castShadows = !!S.shadows; } catch (e1) {}
          try { comp.receiveShadows = !!S.shadows; } catch (e2) {}
          var mis = comp.meshInstances || [];
          for (var i = 0; i < mis.length; i++) {
            try { mis[i].castShadow = !!S.shadows; cnt++; } catch (e3) {}
            try { if (mis[i].receiveShadow !== undefined) mis[i].receiveShadow = !!S.shadows; }
            catch (e4) {}
          }
        }
        var ch = e.children || [];
        for (var j = 0; j < ch.length; j++) walk(ch[j]);
      })(S.root);
      if (S.ground && S.ground.render) {
        try { S.ground.render.receiveShadows = !!S.shadows; } catch (e5) {}
      }
    }
  } catch (e) {}
  S.casters = cnt;
  /* AND THE ANSWER THE CALLER READS. `S.look` is built by F.studio, which runs BEFORE the agent's
     code, so its caster count was always the previous call's - 0 on the first. Same class of
     fault as the flags themselves; the count has to be written when the count is taken. */
  if (S.look) S.look.casters = cnt;
  return cnt;
};

F.shadowFit = function (b) {
  /* A shadow camera that does not hold the subject casts nothing, or casts a band across it. It
     is sized from the same bounding sphere the view camera is framed on, every shot. */
  F.shadowCast();
  if (!S || !S.shadows || !b) return;
  try {
    if (S.kind === 'three') {
      var sh = S.lights.key.shadow;
      var c = sh && sh.camera;
      if (!c) return;
      /* THE LIGHT MOVES, THE DIRECTION DOES NOT. A directional light's shadow camera sits at the
         light and looks at its target, and the key is pinned at (3, 5, 4) in world space - so a
         subject built anywhere else falls outside a shadow camera sized from its own radius. It
         is pushed out along the direction it already points, to a known distance. A directional
         light shades by direction alone, so the picture's lighting is unchanged; only the shadow
         camera's near and far get to be tight enough to have any depth precision. Nothing here
         runs with the switch off, so every render made without shadows is bit-identical. */
      var kp = S.lights.key.position;
      var dx = kp.x - b.c[0], dy = kp.y - b.c[1], dz = kp.z - b.c[2];
      var dl = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (!(dl > 1e-6)) { dx = 3; dy = 5; dz = 4; dl = Math.sqrt(50); }
      var want = b.radius * 4 + 0.01;
      kp.set(b.c[0] + dx / dl * want, b.c[1] + dy / dl * want, b.c[2] + dz / dl * want);
      S.lights.key.updateMatrixWorld();
      var r = b.radius * 1.7;
      c.left = -r; c.right = r; c.top = r; c.bottom = -r;
      c.near = Math.max(0.01, want - b.radius * 2.2);
      c.far = want + b.radius * 2.2;
      c.updateProjectionMatrix();
      sh.bias = -0.0005;
      sh.normalBias = Math.max(0.01, b.radius * 0.01);
      var t = S.lights.key.target;
      if (t) { t.position.set(b.c[0], b.c[1], b.c[2]); t.updateMatrixWorld(); }
    } else {
      var kl2 = S.lights.key.light;
      kl2.shadowDistance = b.radius * 12 + 1;
      /* One shadow texel covers about 2 * radius * 1.7 / 2048 world units, and the bias has to
         clear the depth slope across one of them. Scaled, not constant. */
      kl2.normalOffsetBias = Math.min(0.5, Math.max(0.01, b.radius * 0.012));
      kl2.shadowBias = -Math.min(0.01, Math.max(0.0004, b.radius * 0.00025));
    }
  } catch (e) {}
};

F.shadowProbe = function () {
  /* THE SWITCH THAT REPORTS ITSELF, AND THE CONTROL THAT DOES NOT LIE.

     `shadows: true` used to be a claim with nothing behind it. Two things kept it that way. The
     first was that F.studio runs BEFORE the agent's code, so the traverse that flags every mesh
     walked an empty root - see F.shadowCast. The second was that the obvious way to check, flip
     `castShadows` and difference the two frames, reports zero even when the shadow is plainly on
     screen: PlayCanvas does not recompile a material when a light's `castShadows` changes, so the
     receiving surface goes on sampling the shadow map it was compiled against.

     `shadowIntensity` is a uniform. 1 -> 0 removes the shadow's contribution and nothing else,
     with no recompile. Measured on a box over a plate: studio 0.0% of the frame at peak 0, flat
     0.6% at 34, reference 0.6% at 75, hard 0.6% at 164 - one shadow, four amounts of visible. */
  if (!S || !S.shadows) return null;
  try {
    var cv = S.canvas;
    var t = document.createElement('canvas');
    t.width = cv.width; t.height = cv.height;
    var x = t.getContext('2d', { willReadFrequently: true });
    var grab = function () {
      x.clearRect(0, 0, t.width, t.height);
      x.drawImage(cv, 0, 0);
      return x.getImageData(0, 0, t.width, t.height).data;
    };
    var how = '';
    var set;
    if (S.kind === 'three') {
      var sh = S.lights.key.shadow;
      if (sh && sh.intensity !== undefined) {
        how = 'intensity';
        set = function (v) { sh.intensity = v; };
      } else {
        /* three DOES recompile on this - it sets needsUpdate across the scene - so the toggle is
           an honest control there, unlike PlayCanvas. Older three has no shadow.intensity. */
        how = 'shadowMap';
        set = function (v) { S.renderer.shadowMap.enabled = v > 0; };
      }
    } else {
      how = 'intensity';
      set = function (v) { S.lights.key.light.shadowIntensity = v; };
    }
    /* Two frames per state. The first after a change is the one the engine spends settling, and a
       one-frame probe reads the state from before the change every single time. */
    set(1); F.shot(); F.shot();
    var on = grab();
    set(0); F.shot(); F.shot();
    var off = grab();
    set(1); F.shot();
    var n = 0, mx = 0, px = on.length / 4;
    for (var i = 0; i < on.length; i += 4) {
      var d = Math.abs(on[i] - off[i]) + Math.abs(on[i + 1] - off[i + 1])
            + Math.abs(on[i + 2] - off[i + 2]);
      /* 6, not 0: an 8-bit canvas dithers by a level or two between identical draws. */
      if (d > 6) n++;
      if (d > mx) mx = d;
    }
    return { share: Math.round(1000 * n / px) / 10, peak: mx, px: n, by: how };
  } catch (e) { return null; }
};
F.view = function (name, margin, hold, focus) {
  if (!S) return '';
  /* WHICH WAY WE ARE LOOKING, kept. Nothing else in the forge stored it, so `look` had no angle
     to orbit from and the Engine window had no camera to follow. */
  S.view = String(name || '3q');
  S.margin = margin || 1.06;
  S.focus = focus ? (Array.isArray(focus) ? focus.slice() : [focus]) : null;
  /* Framing a PART is the whole answer to "I could not see the claws". The camera fits that
     object's own box, so a ten-pixel detail becomes five hundred, and everything else is allowed
     to leave the frame -- which is why the border check below is skipped when focused. */
  var only = focus ? pick(focus) : null;
  if (focus && (!only || !only.length)) { S.framed = 'no-such-part'; return F.shot(); }
  var b = bounds(only && only.length ? only : null);
  var aim = dirOf(name);
  var d = aim.d;
  var len = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) || 1;
  var fov = 38 * Math.PI / 180;
  var m = (margin || 1.35) / aim.zoom;
  S.framed = 'no-subject';
  if (b && hold > 0) {
    b = { c: b.c, size: b.size, radius: hold };
  }
  if (b) {
    F.shadowFit(b);
    /* ORTHOGRAPHIC WHEN ASKED. A turnaround reference is orthographic, and a perspective render
       of the same model is a different picture - the far leg is smaller, and the silhouette score
       then compares two projections. Under ortho the distance no longer sets the size, so the fit
       loops grow the frame instead of backing the camera away. */
    var ortho = !!S.ortho;
    var dist = ortho ? b.radius * 4 + 0.001 : (b.radius / Math.tan(fov / 2)) * m;
    var half = b.radius * m;
    var grow = function (k) { if (ortho) half *= k; else dist *= k; };
    var place = function () {
      var px = b.c[0] + d[0] / len * dist,
          py = b.c[1] + d[1] / len * dist,
          pz = b.c[2] + d[2] / len * dist;
      if (S.kind === 'three') {
        var cam = ortho ? orthoCam() : S.cameraP;
        S.camera = cam;
        cam.position.set(px, py, pz);
        cam.lookAt(b.c[0], b.c[1], b.c[2]);
        if (ortho) {
          var asp = (S.canvas.width || 1) / (S.canvas.height || 1);
          cam.left = -half * asp; cam.right = half * asp;
          cam.top = half; cam.bottom = -half;
          cam.near = 0.001;
        } else {
          cam.near = Math.max(0.001, dist - b.radius * 4);
        }
        cam.far = dist + b.radius * 8;
        cam.updateProjectionMatrix();
        cam.updateMatrixWorld(true);
        if (S.ground) S.ground.position.y = b.c[1] - b.size[1] / 2;
      } else {
        S.camera.setPosition(px, py, pz);
        S.camera.lookAt(b.c[0], b.c[1], b.c[2]);
        var cc = S.camera.camera;
        cc.projection = ortho ? S.pc.PROJECTION_ORTHOGRAPHIC : S.pc.PROJECTION_PERSPECTIVE;
        if (ortho) cc.orthoHeight = half;
        cc.nearClip = ortho ? 0.001 : Math.max(0.001, dist - b.radius * 4);
        cc.farClip = dist + b.radius * 8;
        if (S.ground) S.ground.setPosition(b.c[0], b.c[1] - b.size[1] / 2, b.c[2]);
      }
    };
    var SAFE = 0.88;                       /* keep a visible gap, not a hairline */
    place();
    if (only && only.length) {
      /* THE PART FITS FIRST, THEN THE ZOOM. The fit below refits the part's box, so a zoom put
         into the distance before it was undone: focus with zoom=2.5 drew the frame zoom=1 draws.
         So the fit runs at zoom 1, and the zoom is applied to what it found. */
      var zf = aim.zoom > 1.001 ? aim.zoom : 1;
      if (zf > 1) { grow(zf); place(); }
      for (var fp = 0; fp < 5; fp++) {
        F.shot();
        var fw = worstNdc(b);
        if (fw <= SAFE) break;
        grow(Math.min(4, fw / SAFE) * 1.03);
        place();
      }
      if (zf > 1) { grow(1 / zf); place(); }
      S.framed = 'focus';
      return F.shot();                     /* the rest of the body SHOULD cross the border */
    }
    // A held frame is deliberate: refitting it would undo the comparison it exists for. The
    // caller sized it from the WIDEST member of the set, so nothing clips.
    if (hold > 0) { S.framed = 'held'; return F.shot(); }
    /* SO IS A ZOOM. The two loops below exist to guarantee that nothing is ever cropped by
       accident, and they would quietly undo a crop that was asked for on purpose. Someone who
       wrote zoom=2.2 wants the frame filled and the edges cut - and someone who wrote zoom=0.95
       wants THAT camera: a solved view has to render as the camera that was solved. */
    if (aim.zoom > 1.001 || aim.pinned) { S.framed = 'zoom'; return F.shot(); }
    for (var pass = 0; pass < 5; pass++) {
      F.shot();                            /* the projection needs current matrices */
      var w = worstNdc();
      if (w <= SAFE) break;
      grow(Math.min(4, w / SAFE) * 1.03);
      place();
    }
    S.framed = 'fit';
    for (var back = 0; back < 3; back++) {
      F.shot();
      if (!touchesBorder()) break;
      grow(1.22);
      place();
      S.framed = 'widened';
    }
    if (touchesBorder()) S.framed = 'still-clipped';
  }
  var shot = F.shot();
  if (S.shadows && !S.shadowProbed) {
    S.shadowProbed = true;
    S.shadowSeen = F.shadowProbe();
    shot = F.shot();
  }
  return shot;
};

/* A flat subject bypasses the 3D pass entirely: a checkerboard, then the image on top at its own
   aspect. That is the only way to see whether a generated texture has the alpha it claims. */
var drawFlat = function (img) {
  var c = S.canvas, x = c.getContext('2d');
  if (!x) {
    var c2 = document.createElement('canvas');
    c2.width = c.width; c2.height = c.height;
    x = c2.getContext('2d');
    c = c2;
  }
  var W = c.width, H = c.height, sq = 16;
  for (var yy = 0; yy < H; yy += sq) {
    for (var xx = 0; xx < W; xx += sq) {
      x.fillStyle = ((xx / sq + yy / sq) % 2) ? '#2b3038' : '#232830';
      x.fillRect(xx, yy, sq, sq);
    }
  }
  var iw = img.width || img.videoWidth || 1, ih = img.height || img.videoHeight || 1;
  var s = Math.min(W / iw, H / ih) * 0.92;
  var dw = iw * s, dh = ih * s;
  try {
    if (typeof ImageData !== 'undefined' && img instanceof ImageData) {
      var tmp = document.createElement('canvas');
      tmp.width = iw; tmp.height = ih;
      tmp.getContext('2d').putImageData(img, 0, 0);
      img = tmp;
    }
    x.imageSmoothingEnabled = s < 2;      /* pixel art must stay crisp when magnified */
    x.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
  } catch (e) {
    x.fillStyle = '#ff5555';
    x.fillText('could not draw: ' + String(e).slice(0, 60), 8, 20);
  }
  return c.toDataURL('image/png');
};

F.shot = function () {
  if (!S) return '';
  try {
    if (S.flat) return drawFlat(S.flat);
    if (S.kind === 'three') {
      S.renderer.render(S.scene, S.camera);
    } else {
      /* One deliberate step, then one render. No animation loop is ever started. */
      S.app.update(1 / 60);
      S.app.render();
    }
    return S.canvas.toDataURL('image/png');
  } catch (e) {
    return 'error:' + String(e).slice(0, 300);
  }
};

/* ------------------------------------------------------------------ passes
   The two views that judge an asset rather than admire it.

   SILHOUETTE is the readability test a game asset lives or dies by: at 96px on a busy screen the
   player sees an outline and nothing else, and a shape that only reads because of its colours does
   not read. WIREFRAME shows the topology that a triangle count only hints at — 7,000 triangles
   spent well and 7,000 spent on a hidden cylinder cap look identical in the numbers. */
var passState = null;

F.clearPass = function () {
  if (!S || !passState) return true;
  try {
    if (passState.kind === 'three') {
      S.scene.overrideMaterial = passState.override || null;
      if (passState.bg !== undefined) S.scene.background = passState.bg;
      if (passState.mat && passState.mat.dispose) passState.mat.dispose();
    } else {
      for (var i = 0; i < passState.saved.length; i++) {
        var r = passState.saved[i];
        r.mi.material = r.m;
        r.mi.renderStyle = r.rs;
      }
      if (passState.clear) S.camera.camera.clearColor = passState.clear;
    }
    if (S.ground && passState.floor !== null && passState.floor !== undefined) {
      if (S.kind === 'three') S.ground.visible = passState.floor;
      else S.ground.enabled = passState.floor;
    }
  } catch (e) {}
  passState = null;
  return true;
};

/* A matcap, drawn rather than loaded. A sphere lit from the upper left, baked into a texture
   the material samples by view-space normal. It reads FORM better than a lit render for the same
   reason a clay model does: colour and lighting stop competing with shape, and every version of
   the asset is shaded identically so two of them can be compared. */
var matcapTexture = function (T) {
  var n = 256;
  var c = document.createElement('canvas');
  c.width = n; c.height = n;
  var x = c.getContext('2d');
  x.fillStyle = '#0d1015';
  x.fillRect(0, 0, n, n);
  var g = x.createRadialGradient(n * 0.36, n * 0.30, n * 0.04, n * 0.5, n * 0.5, n * 0.52);
  g.addColorStop(0.00, '#ffffff');
  g.addColorStop(0.28, '#d3dae4');
  g.addColorStop(0.62, '#7d8794');
  g.addColorStop(0.88, '#3c434e');
  g.addColorStop(1.00, '#20252d');
  x.fillStyle = g;
  x.beginPath();
  x.arc(n / 2, n / 2, n / 2 - 1, 0, Math.PI * 2);
  x.fill();
  var t = new T.CanvasTexture(c);
  if (T.SRGBColorSpace) t.colorSpace = T.SRGBColorSpace;
  return t;
};

/* Face orientation, exactly as Blender draws it: a face pointing AT the camera is blue, one
   pointing away is red. Not decoration. A previous asset here shipped with its winding inverted
   on every path whose z descended -- the head and jaw were built inside out -- and it was only
   caught by running a signed-volume check in Node afterwards. In this pass it would have been a
   red creature, in a picture, in one look. `gl_FrontFacing` is the whole trick, and it needs a
   shader because no stock material exposes it. */
var facesMaterial = function (T) {
  return new T.ShaderMaterial({
    side: T.DoubleSide,
    vertexShader: 'void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
    fragmentShader:
      'void main(){gl_FragColor=gl_FrontFacing?vec4(0.16,0.42,0.86,1.0):vec4(0.90,0.16,0.13,1.0);}'
  });
};

F.setPass = function (name) {
  if (!S) return false;
  F.clearPass();
  if (!name || name === 'beauty') return true;
  /* THE FLOOR IS NOT PART OF THE SUBJECT, in any pass. The silhouette override paints every
     mesh in the scene black, the floor plane included, so a silhouette taken over a floor is a
     silhouette of a rectangle. It is also what the overlap score is measured on, which is how an
     agent read "every band exactly 510 pixels" and lost a shot working out why. The same is true
     of wireframe and the rest: nobody inspects the topology of the backdrop. Hidden for the pass
     and put back by clearPass. */
  var floorWas = null;
  if (S.ground) {
    if (S.kind === 'three') { floorWas = S.ground.visible; S.ground.visible = false; }
    else { floorWas = S.ground.enabled; S.ground.enabled = false; }
  }
  try {
    if (S.kind === 'three') {
      var T = S.T;
      var mat = name === 'wireframe'
        ? new T.MeshBasicMaterial({ color: 0x8fd0ff, wireframe: true })
        : name === 'normals' ? new T.MeshNormalMaterial()
        : name === 'faces' ? facesMaterial(T)
        : name === 'matcap' ? new T.MeshMatcapMaterial({ matcap: matcapTexture(T) })
        : new T.MeshBasicMaterial({ color: 0x000000 });
      passState = { kind: 'three', override: S.scene.overrideMaterial,
                    bg: S.scene.background, mat: mat, floor: floorWas };
      S.scene.overrideMaterial = mat;
      if (name === 'silhouette') S.scene.background = new T.Color(0xf2f4f8);
    } else {
      var saved = [];
      var walk = function (e) {
        var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        for (var i = 0; i < mi.length; i++) {
          saved.push({ mi: mi[i], m: mi[i].material, rs: mi[i].renderStyle });
        }
        var kids = e.children || [];
        for (var j = 0; j < kids.length; j++) walk(kids[j]);
      };
      walk(S.root);
      if (name === 'faces' || name === 'matcap') return false;   /* three.js only; say so */
      var pm = new S.pc.StandardMaterial();
      pm.useLighting = false;                       /* flat: a silhouette must not be shaded */
      var c = name === 'silhouette' ? new S.pc.Color(0.02, 0.03, 0.05)
                                    : new S.pc.Color(0.56, 0.82, 1);
      pm.diffuse = c;
      pm.emissive = c;
      pm.update();
      var keep = S.camera.camera.clearColor.clone();
      for (var k = 0; k < saved.length; k++) {
        saved[k].mi.material = pm;
        if (name === 'wireframe' && S.pc.RENDERSTYLE_WIREFRAME !== undefined) {
          saved[k].mi.renderStyle = S.pc.RENDERSTYLE_WIREFRAME;
        }
      }
      if (name === 'silhouette') S.camera.camera.clearColor = new S.pc.Color(0.95, 0.96, 0.97);
      passState = { kind: 'pc', saved: saved, clear: keep, mat: pm, floor: floorWas };
    }
  } catch (e) {
    /* Put the floor back by hand: passState was never set, so clearPass has nothing to undo. */
    if (S.ground && floorWas !== null) {
      if (S.kind === 'three') S.ground.visible = floorWas; else S.ground.enabled = floorWas;
    }
    return false;
  }
  /* THE BACKDROP THIS PASS DRAWS, measured. The silhouette pass paints the background near-white
     and the border check went on comparing with the beauty backdrop: every border pixel read as
     the subject, so every framing under the pass backed off three times (1.8x) and said
     'still-clipped' - which /export then reported for a sheet that was not clipped. */
  if (name === 'silhouette' && passState) passState.bgPixel = sampleEmpty();
  return true;
};

/* Turn the SUBJECT, not the camera. The framing is computed from the bounding sphere, which does
   not change as the thing rotates — so one framing holds for every step and nothing clips or
   drifts between frames of a turntable. */
F.turn = function (deg) {
  if (!S) return false;
  try {
    if (S.kind === 'three') S.root.rotation.y = (deg || 0) * Math.PI / 180;
    else S.root.setLocalEulerAngles(0, deg || 0, 0);
  } catch (e) { return false; }
  return true;
};

/* HOW THE BODY IS BUILT, AS A NUMBER.
   A creature that has to read as one soft skin is a different object from a pile of primitives
   that intersect, and in a lit render at 400px the difference is invisible until it is too late
   to change method. Two meshes whose boxes overlap are not necessarily a fault -- an eye sits
   inside a head on purpose -- so this reports and never refuses. What it makes visible is the
   SHAPE of the build: 122 surfaces with 300 overlapping pairs is a kitbash, 1 surface is a skin,
   and only one of those two can have a seamless silhouette. */
/* WHAT IS IN HERE, AND WHERE IT IS.
   An agent cannot use a mouse, so "select the wing and rotate it" has to be a list of names and a
   list of numbers. This is that list: every named node under the subject, with the transform it
   currently has and the size of the box it occupies. It is what `F.apply` moves. */
/* The camera, as a caller can use it: the spec string it was framed with, and that spec read
   back as numbers. `az` and `el` are what an orbit is added to; `view` is what goes into the next
   call. A preset like `3q` has no azimuth of its own, so it is resolved to one here rather than
   left for the caller to guess. */
F.at = function () {
  if (!S) return null;
  var name = S.view || '3q';
  var a = dirOf(name);
  var d = a.d;
  var len = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) || 1;
  var el = Math.asin(Math.max(-1, Math.min(1, d[1] / len))) * 180 / Math.PI;
  var az = Math.atan2(d[0], d[2]) * 180 / Math.PI;
  if (az < 0) az += 360;
  var b = bounds();
  return {
    view: name,
    az: Math.round(az * 10) / 10,
    el: Math.round(el * 10) / 10,
    zoom: a.zoom,
    margin: S.margin || 1.06,
    focus: S.focus || null,
    framed: S.framed || '',
    /* What a mirroring viewport needs to put its own camera in the same place. */
    target: b ? [r3(b.c[0]), r3(b.c[1]), r3(b.c[2])] : null,
    radius: b ? r3(b.radius) : 0
  };
};

F.nodes = function (limit) {
  var out = [];
  if (!S || S.flat) return out;
  var seen = {};
  var push = function (o, name, pos, rot, scl, box) {
    if (!name || seen[name]) { if (seen[name]) { seen[name].count++; return; } }
    var row = { name: name, count: 1, position: pos, rotation: rot, scale: scl, size: box };
    seen[name] = row;
    out.push(row);
  };
  try {
    if (S.kind === 'three') {
      S.root.traverse(function (o) {
        if (o === S.root) return;
        var nm = nameOf(o);
        if (!nm) return;
        var b = new S.T.Box3().setFromObject(o);
        var sz = b.isEmpty() ? [0, 0, 0]
               : [r3(b.max.x - b.min.x), r3(b.max.y - b.min.y), r3(b.max.z - b.min.z)];
        push(o, nm,
             [r3(o.position.x), r3(o.position.y), r3(o.position.z)],
             [r3(o.rotation.x * 180 / Math.PI), r3(o.rotation.y * 180 / Math.PI),
              r3(o.rotation.z * 180 / Math.PI)],
             [r3(o.scale.x), r3(o.scale.y), r3(o.scale.z)], sz);
      });
    } else {
      var walk = function (e) {
        if (e !== S.root && e.name) {
          var lp = e.getLocalPosition(), lr = e.getLocalEulerAngles(), ls = e.getLocalScale();
          push(e, e.name, [r3(lp.x), r3(lp.y), r3(lp.z)],
               [r3(lr.x), r3(lr.y), r3(lr.z)], [r3(ls.x), r3(ls.y), r3(ls.z)], [0, 0, 0]);
        }
        var k = e.children || [];
        for (var i = 0; i < k.length; i++) walk(k[i]);
      };
      walk(S.root);
    }
  } catch (e) { /* a listing must never cost the picture */ }
  return out.slice(0, limit || 60);
};

/* MOVE IT. This is the half of a modelling tool that a renderer alone is not.
   Each edit names a target and says what to do to it. Every field is optional, so an edit can be
   only a nudge:

     {"target":"wing", "rotate":[0,-20,0]}          turn it, degrees, relative
     {"target":"wing", "rotation":[0,-20,0]}        set the angle outright
     {"target":"head", "move":[0,0.1,0]}            nudge it, relative
     {"target":"head", "position":[0,1.8,0.2]}      put it there
     {"target":"tail", "scale":1.2}                 one number scales all three axes
     {"target":"backSpike", "visible":false}        hide it to see what it was covering
     {"target":"belly", "color":"#e8c39a"}          try a colour without a rebuild

   Nothing is rebuilt, so an edit is instant, and NOTHING IS SAVED EITHER: the answer echoes the
   transform each target ended up with, and the agent writes the numbers it liked into the builder
   that made the asset. A viewer that silently became the source of truth would be a worse tool
   than a viewer. */
/* WHO TO GRAB WHEN A NAME MATCHES.
   `pick` answers a different question — it finds GEOMETRY, because framing a camera on a group
   that holds no triangles frames nothing. Moving is the opposite: a wing is a group with fourteen
   meshes in it, and rotating each of the fourteen about its own origin is not the same operation
   as rotating the wing about the shoulder. The second one is what a person means.

   So: every node whose own name or material matches, minus any node that already has a matching
   ancestor. The topmost match wins and its children come with it, which is what selecting a
   parent does in Blender. */
var pickNodes = function (want) {
  /* AN EXACT NAME WINS, ALWAYS.

     This matched a case-insensitive SUBSTRING and stopped descending at the topmost hit, so
     `{"target": "head"}` grabbed `headGroup` - and hiding it took the hair, which lives inside
     it, with no word about either. A name that is exactly the one asked for cannot be the wrong
     node, so it is preferred; the substring pass is the fallback it always was, and the names it
     landed on come back in the answer. */
  var q = String(want || '').toLowerCase().trim();
  if (!S || !q) return [];
  var exact = [], loose = [];
  var nameHit = function (o) {
    var n = String(o.name || '').toLowerCase();
    if (n === q) return 2;
    if (n.indexOf(q) >= 0) return 1;
    var mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    for (var i = 0; i < mats.length; i++) {
      var mn = String((mats[i] && mats[i].name) || '').toLowerCase();
      if (mn === q) return 2;
      if (mn.indexOf(q) >= 0) return 1;
    }
    return 0;
  };
  var walk = function (o, under) {
    var k = o === S.root ? 0 : nameHit(o);
    if (k === 2) exact.push(o);
    if (k && !under) loose.push(o);
    var kids = o.children || [];
    for (var i = 0; i < kids.length; i++) walk(kids[i], under || !!k);
  };
  walk(S.root, false);
  return exact.length ? exact : loose;
};

var nodeNames = function (list) {
  var out = [];
  for (var i = 0; i < (list || []).length && i < 6; i++) out.push(String(list[i].name || '?'));
  return out;
};

/* WHAT IT WAS BEFORE THE FIRST EDIT TOUCHED IT. Recorded once per node, so a chain of nudges
   still reverts to the state the builder produced rather than to the previous nudge. */
var keepWas = function (o) {
  if (!o || o.__forgeWas) return;
  try {
    if (S.kind === 'three') {
      o.__forgeWas = { p: [o.position.x, o.position.y, o.position.z],
                       r: [o.rotation.x, o.rotation.y, o.rotation.z],
                       s: [o.scale.x, o.scale.y, o.scale.z], v: o.visible };
    } else {
      var lp = o.getLocalPosition(), lr = o.getLocalEulerAngles(), ls = o.getLocalScale();
      o.__forgeWas = { p: [lp.x, lp.y, lp.z], r: [lr.x, lr.y, lr.z], s: [ls.x, ls.y, ls.z],
                       v: o.enabled };
    }
  } catch (e) { o.__forgeWas = null; }
};

F.revert = function () {
  /* PUT EVERY EDITED NODE BACK. Edits used to persist for the life of the bench with no way to
     undo them, so one exploratory `{"target":"wing","rotate":[0,-20,0]}` silently changed every
     picture and every score after it. */
  var n = 0;
  if (!S) return n;
  var walk = function (o) {
    var w = o.__forgeWas;
    if (w) {
      try {
        if (S.kind === 'three') {
          o.position.set(w.p[0], w.p[1], w.p[2]);
          o.rotation.set(w.r[0], w.r[1], w.r[2]);
          o.scale.set(w.s[0], w.s[1], w.s[2]);
          o.visible = w.v;
          o.updateMatrixWorld(true);
        } else {
          o.setLocalPosition(w.p[0], w.p[1], w.p[2]);
          o.setLocalEulerAngles(w.r[0], w.r[1], w.r[2]);
          o.setLocalScale(w.s[0], w.s[1], w.s[2]);
          o.enabled = w.v;
        }
        n++;
      } catch (e) {}
      try { delete o.__forgeWas; } catch (e2) { o.__forgeWas = null; }
    }
    var kids = o.children || [];
    for (var i = 0; i < kids.length; i++) walk(kids[i]);
  };
  walk(S.root);
  return n;
};

F.apply = function (edits) {
  var done = [];
  if (!S || !edits || !edits.length) return done;
  var num3 = function (v, fallback) {
    if (typeof v === 'number') return [v, v, v];
    if (Array.isArray(v) && v.length === 3) return [Number(v[0]), Number(v[1]), Number(v[2])];
    return fallback;
  };
  for (var i = 0; i < edits.length; i++) {
    var ed = edits[i] || {};
    var hit = pickNodes(ed.target || ed.name || '');
    if (!hit || !hit.length) { done.push({ target: ed.target || '', found: 0 }); continue; }
    for (var j = 0; j < hit.length; j++) {
      var o = hit[j];
      keepWas(o);
      try {
        var mv = num3(ed.move || ed.translate, null);
        var pos = num3(ed.position, null);
        var rot = num3(ed.rotation, null);
        var spin = num3(ed.rotate || ed.turn, null);
        var scl = num3(ed.scale, null);
        if (S.kind === 'three') {
          if (pos) o.position.set(pos[0], pos[1], pos[2]);
          if (mv) o.position.set(o.position.x + mv[0], o.position.y + mv[1], o.position.z + mv[2]);
          var D = Math.PI / 180;
          if (rot) o.rotation.set(rot[0] * D, rot[1] * D, rot[2] * D);
          if (spin) o.rotation.set(o.rotation.x + spin[0] * D, o.rotation.y + spin[1] * D,
                                   o.rotation.z + spin[2] * D);
          if (scl) o.scale.set(scl[0], scl[1], scl[2]);
          if (ed.visible === false || ed.visible === true) o.visible = ed.visible;
          if (ed.color && o.material) {
            var ms = Array.isArray(o.material) ? o.material : [o.material];
            for (var m = 0; m < ms.length; m++) {
              if (ms[m] && ms[m].color && ms[m].color.set) ms[m].color.set(ed.color);
            }
          }
          o.updateMatrixWorld(true);
        } else {
          if (pos) o.setLocalPosition(pos[0], pos[1], pos[2]);
          if (mv) { var lp = o.getLocalPosition();
                    o.setLocalPosition(lp.x + mv[0], lp.y + mv[1], lp.z + mv[2]); }
          if (rot) o.setLocalEulerAngles(rot[0], rot[1], rot[2]);
          if (spin) { var lr = o.getLocalEulerAngles();
                      o.setLocalEulerAngles(lr.x + spin[0], lr.y + spin[1], lr.z + spin[2]); }
          if (scl) o.setLocalScale(scl[0], scl[1], scl[2]);
          if (ed.visible === false || ed.visible === true) o.enabled = ed.visible;
        }
      } catch (err) { /* one bad edit must not lose the others */ }
    }
    var o0 = hit[0], row = { target: ed.target || ed.name || '', found: hit.length,
                             /* WHICH NODES. `head` matching `headGroup` is a real answer and a
                                real surprise; naming them costs six words. */
                             names: nodeNames(hit) };
    try {
      if (S.kind === 'three') {
        row.position = [r3(o0.position.x), r3(o0.position.y), r3(o0.position.z)];
        row.rotation = [r3(o0.rotation.x * 180 / Math.PI), r3(o0.rotation.y * 180 / Math.PI),
                        r3(o0.rotation.z * 180 / Math.PI)];
        row.scale = [r3(o0.scale.x), r3(o0.scale.y), r3(o0.scale.z)];
      }
    } catch (err) { /* the echo is a courtesy, not the operation */ }
    done.push(row);
  }
  return done;
};

F.solidity = function () {
  var out = { surfaces: 0, overlapping_pairs: 0, largest_share: 0 };
  if (!S || S.flat) return out;
  var rows = meshBoxes();
  out.surfaces = rows.length;
  if (!rows.length) return out;
  var tot = 0, big = 0;
  for (var i = 0; i < rows.length; i++) { tot += rows[i].tris || 0; big = Math.max(big, rows[i].tris || 0); }
  out.largest_share = tot > 0 ? Math.round(big / tot * 100) : 0;
  /* Quadratic, and deliberately so: a few hundred meshes is 50k box tests, which is nothing
     beside one render, and any cheaper structure would be a lie about how exact this is. */
  var cap = Math.min(rows.length, 400), pairs = 0;
  for (var a = 0; a < cap; a++) {
    for (var b = a + 1; b < cap; b++) {
      var A = rows[a], B = rows[b], hit = true;
      for (var k = 0; k < 3; k++) {
        /* A shared face is not an overlap. Require real interpenetration, scaled to the smaller
           box, so two blocks stacked on each other do not read as one fused mass. */
        var slack = Math.min(A.hi[k] - A.lo[k], B.hi[k] - B.lo[k]) * 0.08;
        if (A.lo[k] + slack >= B.hi[k] || B.lo[k] + slack >= A.hi[k]) { hit = false; break; }
      }
      if (hit) pairs++;
    }
  }
  out.overlapping_pairs = pairs;
  out.measured_of = cap;
  return out;
};

F.stats = function () {
  var out = { engine: S ? S.kind : '', flat: !!(S && S.flat),
              framed: S ? (S.framed || 'unknown') : '' };
  if (!S) return out;
  if (S.shadows && S.shadowSeen) out.shadow = S.shadowSeen;
  try {
    var b = bounds();
    if (b) {
      out.bbox_size = [r3(b.size[0]), r3(b.size[1]), r3(b.size[2])];
      out.bbox_center = [r3(b.c[0]), r3(b.c[1]), r3(b.c[2])];
      out.radius = r3(b.radius);
    }
    if (S.flat) {
      out.image = [S.flat.width || 0, S.flat.height || 0];
      return out;
    }
    var tris = 0, meshes = 0, mats = [], objs = 0;
    if (S.kind === 'three') {
      S.root.traverse(function (o) {
        objs++;
        if (!o.isMesh && !o.isPoints && !o.isLine) return;
        meshes++;
        var g = o.geometry;
        if (g && g.index) tris += g.index.count / 3;
        else if (g && g.attributes && g.attributes.position) tris += g.attributes.position.count / 3;
        var ms = Array.isArray(o.material) ? o.material : [o.material];
        for (var i = 0; i < ms.length; i++) {
          if (ms[i] && mats.indexOf(ms[i]) < 0) mats.push(ms[i]);
        }
      });
      if (S.renderer && S.renderer.info) {
        out.draw_calls = S.renderer.info.render.calls;
        out.gpu_triangles = S.renderer.info.render.triangles;
        /* Everything on the GPU: the bench's environment map and render targets included. */
        out.gpu_textures = S.renderer.info.memory.textures;
      }
      /* THE ASSET'S TEXTURES. `textures` used to be the GPU count above, so one painted atlas
         read 2, then 3, then 4 as a session went on. Distinct textures on the subject's own
         materials is the number the question is about. */
      var texs = [];
      for (var mt = 0; mt < mats.length; mt++) {
        for (var tk in mats[mt]) {
          var tv = mats[mt][tk];
          if (tk !== 'envMap' && tv && tv.isTexture && texs.indexOf(tv) < 0) texs.push(tv);
        }
      }
      out.textures = texs.length;
    } else {
      var walk = function (e) {
        objs++;
        var mi = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        for (var i = 0; i < mi.length; i++) {
          meshes++;
          var pr = mi[i].mesh && mi[i].mesh.primitive && mi[i].mesh.primitive[0];
          if (pr && pr.count) tris += pr.count / 3;
          if (mi[i].material && mats.indexOf(mi[i].material) < 0) mats.push(mi[i].material);
        }
        var kids = e.children || [];
        for (var j = 0; j < kids.length; j++) walk(kids[j]);
      };
      walk(S.root);
      var PCMAPS = ['diffuseMap', 'opacityMap', 'normalMap', 'emissiveMap', 'specularMap',
                    'metalnessMap', 'glossMap', 'aoMap', 'lightMap', 'heightMap'];
      var ptx = [];
      for (var pm = 0; pm < mats.length; pm++) {
        for (var pq = 0; pq < PCMAPS.length; pq++) {
          var t2 = null;
          try { t2 = mats[pm][PCMAPS[pq]]; } catch (e6) { t2 = null; }
          if (t2 && ptx.indexOf(t2) < 0) ptx.push(t2);
        }
      }
      out.textures = ptx.length;
    }
    out.objects = objs - 1;
    out.meshes = meshes;
    out.triangles = Math.round(tris);
    out.materials = mats.length;
    out.material_names = mats.slice(0, 6).map(function (m) { return m.name || '(unnamed)'; });
  } catch (e) { out.error = String(e).slice(0, 200); }
  try {
    out.parts = F.parts();
    out.stance = F.stance();
    out.solidity = F.solidity();
  } catch (e) { /* measurement must never cost the picture */ }
  if (log.length) out.log = log.slice(0, 30);
  return out;
};

/* ---------------------------------------------------------------------------
   THE TIME AXIS

   ABSOLUTE OR DELTA IS ASKED, NEVER GUESSED. Half the update functions ever written take the
   time since the last frame; the other half take the time since the start. Passing one to the
   other does not throw — it produces a plausible, wrong animation, which is the worst kind of
   failure for a tool whose whole job is to show you what is wrong. So the caller says which, and
   `delta` steps forward in fixed increments from the previous sampled time, which is why the
   sampled times have to ascend.
*/
F.anim = { how: '', dur: 0, mixer: null, action: null, host: null, call: '',
           mode: 'absolute', step: 1 / 60, at: 0 };

F.arm = function (opts) {
  opts = opts || {};
  var want = opts.drive || 'auto';
  var out = { how: '', duration: 0, error: '', looked_for: [], clips: [] };
  F.anim = { how: '', dur: 0, mixer: null, action: null, host: null, call: '',
             mode: (opts.mode === 'delta' ? 'delta' : 'absolute'),
             step: (+opts.step > 0 ? +opts.step : 1 / 60), at: 0 };
  if (!S || !S.root) { out.error = 'nothing is built'; return out; }

  var tryMixer = function () {
    if (S.kind !== 'three' || !S.T || !S.T.AnimationMixer) return false;
    var clips = null, holder = null;
    if (S.root.animations && S.root.animations.length) { clips = S.root.animations; holder = S.root; }
    if (!clips) {
      S.root.traverse(function (o) {
        if (!clips && o.animations && o.animations.length) { clips = o.animations; holder = o; }
      });
    }
    if (!clips || !clips.length) return false;
    var pick = clips[0];
    for (var i = 0; i < clips.length; i++) {
      out.clips.push(clips[i].name || ('clip' + i));
      if (opts.clip && clips[i].name === opts.clip) pick = clips[i];
    }
    if (opts.clip && pick.name !== opts.clip) {
      out.error = 'no clip called "' + opts.clip + '"; this model has: ' + out.clips.join(', ');
      return false;
    }
    F.anim.mixer = new S.T.AnimationMixer(holder);
    F.anim.action = F.anim.mixer.clipAction(pick);
    F.anim.action.play();
    F.anim.how = 'mixer';
    F.anim.dur = pick.duration || 0;
    out.clip = pick.name;
    return true;
  };

  var tryCall = function () {
    var names = ['update', 'tick', 'animate', 'step'];
    /* The root first, then what was ADDED to it, then one level below that. An agent writes
       `const g = new THREE.Group(); g.userData.update = ...; add(g);` — so the function lives on
       a child, and looking only at the root made the ordinary case the unsupported one.
       Deliberately shallow: traversing the whole tree would eventually find some helper's
       `update` deep inside a model and drive that instead. */
    var hosts = [];
    var push = function (o, as) {
      if (!o) return;
      hosts.push({ o: o, as: as });
      if (o.userData) hosts.push({ o: o.userData, as: as + 'userData.' });
    };
    push(S.root, 'root.');
    var kids = (S.root && S.root.children) || [];
    for (var i = 0; i < kids.length && i < 12; i++) {
      var nm = (kids[i] && kids[i].name) || ('child[' + i + ']');
      push(kids[i], nm + '.');
      var gk = (kids[i] && kids[i].children) || [];
      for (var j = 0; j < gk.length && j < 12; j++) {
        push(gk[j], nm + '.' + ((gk[j] && gk[j].name) || ('child[' + j + ']')) + '.');
      }
    }
    for (var h = 0; h < hosts.length; h++) {
      for (var n = 0; n < names.length; n++) {
        if (typeof hosts[h].o[names[n]] === 'function') {
          F.anim.host = hosts[h].o;
          F.anim.call = names[n];
          F.anim.how = 'call';
          out.calls = hosts[h].as + names[n] + '(' + F.anim.mode + ')';
          return true;
        }
      }
    }
    return false;
  };

  if (want === 'mixer') { if (!tryMixer() && !out.error) out.error = 'no AnimationClip on this model'; }
  else if (want === 'call') { if (!tryCall()) out.error = 'no update function on the asset'; }
  else if (want === 'fn') { F.anim.how = 'fn'; }      /* the caller evaluates it, per frame */
  else if (!tryMixer() && !tryCall() && !out.error) {
    out.error = 'nothing here advances with time';
  }
  if (!F.anim.how && !out.error) out.error = 'could not arm';
  if (out.error) {
    /* Name what was looked for. "It does not animate" is not actionable; "I looked for these six
       things and none of them was there" tells you exactly what to add. */
    out.looked_for = ['AnimationClips on the model or any child',
                      'update / tick / animate / step on the root, on anything you add()ed, '
                      + 'or one level below that',
                      '...and on the .userData of each of those',
                      'or pass drive:"fn" with an expression of your own'];
  }
  out.how = F.anim.how;
  out.mode = F.anim.mode;
  out.duration = r3(+opts.duration > 0 ? +opts.duration : F.anim.dur || 0);
  F.anim.dur = out.duration;
  return out;
};

F.pose = function (t) {
  t = +t || 0;
  if (F.anim.how === 'mixer' && F.anim.mixer) {
    F.anim.mixer.setTime(t);
    if (S.root.updateMatrixWorld) S.root.updateMatrixWorld(true);
    F.anim.at = t;
    return { ok: true, t: r3(t) };
  }
  if (F.anim.how === 'call' && F.anim.host) {
    try {
      if (F.anim.mode === 'delta') {
        if (t < F.anim.at - 1e-9) {
          return { ok: false, error: 'delta mode cannot go backwards; sample times must ascend' };
        }
        var left = t - F.anim.at, st = F.anim.step, guard = 0;
        while (left > 1e-9 && guard++ < 20000) {
          var d = Math.min(st, left);
          F.anim.host[F.anim.call](d);
          left -= d;
        }
      } else {
        F.anim.host[F.anim.call](t);
      }
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e).slice(0, 300) };
    }
    if (S.root.updateMatrixWorld) S.root.updateMatrixWorld(true);
    F.anim.at = t;
    return { ok: true, t: r3(t) };
  }
  if (F.anim.how === 'fn') { F.anim.at = t; return { ok: true, t: r3(t), by: 'the caller' }; }
  return { ok: false, error: 'not armed - call arm() first' };
};

/* Where the named parts are, and how far each is from a ground the CALLER fixed.

   The difference from F.stance() is the whole point: stance() finds the floor by taking the
   lowest mesh in the model, which is right for judging one pose and wrong for judging a walk.
   Here the ground is given once and held for every frame, so a foot that leaves it is visible.

   Matched by SUBSTRING, case-insensitive, and the boxes of every match are unioned: a foot is
   usually several meshes, and asking for exact names would report a foot as missing because the
   toes are called footL_toe1. */
F.trackAt = function (names, ground) {
  var g = (typeof ground === 'number' && isFinite(ground)) ? ground : 0;
  var rows = meshBoxes();
  var b = bounds();
  var tol = Math.max(1e-4, (b && b.size ? b.size[1] : 1) * 0.02);
  var out = { ground: r3(g), tolerance: r3(tol), parts: {}, missing: [],
              lowest: null, root: null, meshes: rows.length };
  if (b) out.root = [r3(b.c[0]), r3(b.c[1]), r3(b.c[2])];
  var low = 1e9;
  for (var i = 0; i < rows.length; i++) low = Math.min(low, rows[i].lo[1]);
  if (rows.length) out.lowest = r3(low);

  var want = (names && names.length) ? names : [];
  for (var w = 0; w < want.length; w++) {
    var q = String(want[w]).toLowerCase();
    var lo = 1e9, lx = 1e9, hx = -1e9, lz = 1e9, hz = -1e9, hit = 0;
    for (var j = 0; j < rows.length; j++) {
      if (String(rows[j].name).toLowerCase().indexOf(q) < 0) continue;
      hit++;
      if (rows[j].lo[1] < lo) lo = rows[j].lo[1];
      if (rows[j].lo[0] < lx) lx = rows[j].lo[0];
      if (rows[j].hi[0] > hx) hx = rows[j].hi[0];
      if (rows[j].lo[2] < lz) lz = rows[j].lo[2];
      if (rows[j].hi[2] > hz) hz = rows[j].hi[2];
    }
    if (!hit) { out.missing.push(want[w]); continue; }
    var gap = lo - g;
    out.parts[want[w]] = {
      meshes: hit,
      bottom: r3(lo),
      /* Positive is floating, negative is sinking through the floor. */
      gap: r3(gap),
      down: gap <= tol && gap >= -tol,
      /* Where it stands on the ground plane. A planted foot's `at` must not move. */
      at: [r3((lx + hx) / 2), r3((lz + hz) / 2)]
    };
  }
  return out;
};

/* Pin the camera and the ground, for a sequence.

   Framing each frame on its own re-centres the subject and cancels out exactly the motion being
   judged -- the same trap the variant sweep hit, one axis over. The caller computes one centre
   and one radius across every sampled time and passes them here, so the frames are comparable and
   nothing leaves the frame mid-cycle.

   `groundY` is where the floor really is. `view()` moves the ground under the subject; here it
   does not move at all, which is what makes a foot's gap visible. */
F.lock = function (name, margin, center, radius, groundY) {
  if (!S) return '';
  S.view = String(name || 'side');
  S.margin = margin || 1.15;
  S.focus = null;
  var aim = dirOf(name);
  var d = aim.d;
  var len = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) || 1;
  var fov = 38 * Math.PI / 180;
  var m = (margin || 1.15) / aim.zoom;
  var r = Math.max(1e-4, +radius || 1);
  var c = (center && center.length === 3) ? [+center[0], +center[1], +center[2]] : [0, 0, 0];
  var gy = (typeof groundY === 'number' && isFinite(groundY)) ? groundY : 0;
  var dist = (r / Math.tan(fov / 2)) * m;
  var px = c[0] + d[0] / len * dist,
      py = c[1] + d[1] / len * dist,
      pz = c[2] + d[2] / len * dist;
  if (S.kind === 'three') {
    S.camera.position.set(px, py, pz);
    S.camera.lookAt(c[0], c[1], c[2]);
    S.camera.near = Math.max(0.001, dist - r * 4);
    S.camera.far = dist + r * 8;
    S.camera.updateProjectionMatrix();
    S.camera.updateMatrixWorld(true);
    if (S.ground) S.ground.position.y = gy;
  } else {
    S.camera.setPosition(px, py, pz);
    S.camera.lookAt(c[0], c[1], c[2]);
    S.camera.camera.nearClip = Math.max(0.001, dist - r * 4);
    S.camera.camera.farClip = dist + r * 8;
    if (S.ground) S.ground.setPosition(c[0], gy, c[2]);
  }
  S.framed = 'locked';
  S.lock = { c: c, r: r3(r), ground: r3(gy) };
  return F.shot();
};

/* ------------------------------------------------------------------ anchors
   THE CAMERA, SOLVED FROM POINTS THE PICTURE SHOWS. A silhouette has no front and no back, so the
   goblin A/B's forge builder got its top five angles within 0.012 of each other and could not
   choose the side; the Blender builder solved its camera from three points instead (helmet apex,
   nose, buckle) and then moved ear tips, fists and the sword tip onto the picture's own pixels.
   The page's half of that is here: where each named point IS, in world space, and where the
   current camera puts a world point. The least squares runs in the backend (live.py), against
   exactly the camera F.view places for an `az=..,el=..,zoom=..` spec. */
var r6 = function (n) { return Math.round(n * 1e6) / 1e6; };

/* Every vertex of these parts, in world space, as one flat [x, y, z, ...] list. */
var worldVerts = function (list) {
  var out = [];
  try {
    if (S.kind === 'three') {
      S.root.updateMatrixWorld(true);
      list.forEach(function (o) {
        var g = o.geometry, pa = g && g.attributes && g.attributes.position;
        if (!pa) return;
        var e = o.matrixWorld.elements;
        for (var i = 0; i < pa.count; i++) {
          var x = pa.getX(i), y = pa.getY(i), z = pa.getZ(i);
          out.push(e[0] * x + e[4] * y + e[8] * z + e[12], e[1] * x + e[5] * y + e[9] * z + e[13],
                   e[2] * x + e[6] * y + e[10] * z + e[14]);
        }
      });
    } else {
      var walk = function (en) {
        var mis = (en.render && en.render.meshInstances) || (en.model && en.model.meshInstances) || [];
        for (var i = 0; i < mis.length; i++) {
          var p = [];
          try { mis[i].mesh.getPositions(p); } catch (e1) { continue; }
          var d = mis[i].node.getWorldTransform().data;
          for (var k = 0; k + 2 < p.length; k += 3) {
            var x = p[k], y = p[k + 1], z = p[k + 2];
            out.push(d[0] * x + d[4] * y + d[8] * z + d[12], d[1] * x + d[5] * y + d[9] * z + d[13],
                     d[2] * x + d[6] * y + d[10] * z + d[14]);
          }
        }
        var kids = en.children || [];
        for (var j = 0; j < kids.length; j++) walk(kids[j]);
      };
      list.forEach(walk);
    }
  } catch (e) { /* a part we could not read has no anchor, and says so */ }
  return out;
};

/* `where` is the subject's own direction, glTF's: top +Y, front +Z, LEFT +X (its own left), so
   `right` is -X. An extreme is the mean of every vertex within 1% of the part's extent of it -
   the apex of a pointed helmet, the middle of a flat top - never one stray vertex. */
var ANCHOR_AXES = { top: [1, 1], bottom: [1, -1], left: [0, 1], right: [0, -1], front: [2, 1],
                    back: [2, -1], '+x': [0, 1], '-x': [0, -1], '+y': [1, 1], '-y': [1, -1],
                    '+z': [2, 1], '-z': [2, -1] };
var anchorOf = function (part, where) {
  var list = pickPart(part);
  if (!list.length) return { found: false, error: 'no part named ' + part };
  var v = worldVerts(list);
  if (v.length < 3) return { found: false, error: part + ' has no vertices to anchor on' };
  var lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (var i = 0; i < v.length; i += 3) {
    for (var k = 0; k < 3; k++) {
      if (v[i + k] < lo[k]) lo[k] = v[i + k];
      if (v[i + k] > hi[k]) hi[k] = v[i + k];
    }
  }
  var w = String(where || 'centre').toLowerCase().trim(), ax = ANCHOR_AXES[w], at;
  if (!ax) {
    at = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    w = 'centre';
  } else {
    var a = ax[0], sg = ax[1], ext = sg > 0 ? hi[a] : lo[a];
    var tol = Math.max(1e-7, (hi[a] - lo[a]) * 0.01), sum = [0, 0, 0], n = 0;
    for (var j = 0; j < v.length; j += 3) {
      if (sg > 0 ? v[j + a] >= ext - tol : v[j + a] <= ext + tol) {
        sum[0] += v[j]; sum[1] += v[j + 1]; sum[2] += v[j + 2]; n++;
      }
    }
    at = [sum[0] / n, sum[1] / n, sum[2] / n];
  }
  var names = [];
  list.forEach(function (o) {
    var nm = String(o.name || '');
    if (names.indexOf(nm) < 0 && names.length < 4) names.push(nm);
  });
  return { found: true, at: at, where: w, meshes: list.length, names: names,
           nodes: pickNodes(part).length, lo: lo, hi: hi };
};

/* Each anchor resolved to a world point, and the frame the solve needs: the subject's bounds (the
   camera is placed from them), the field of view and the projection. */
F.anchorFacts = function (list) {
  if (!S || S.flat) return { ok: false, error: 'nothing is on the bench' };
  try { if (S.kind === 'three') S.root.updateMatrixWorld(true); } catch (e) {}
  var b = bounds();
  if (!b) return { ok: false, error: 'the bench is empty' };
  var rows = [];
  (list || []).forEach(function (a, i) {
    var at = a && a.at;
    if (Array.isArray(at) && at.length === 3) {
      rows.push({ i: i, found: true, at: [+at[0], +at[1], +at[2]], where: 'point' });
      return;
    }
    if (at && typeof at === 'object' && at.part) {
      var got = anchorOf(String(at.part), at.where);
      got.i = i;
      got.part = String(at.part);
      got.asked = String(at.where || 'centre');
      rows.push(got);
      return;
    }
    rows.push({ i: i, found: false, error: 'at is [x, y, z] or {part, where}' });
  });
  /* The midline and the height, so a snap can keep a centred part centred: x = 0, the builder's
     own origin, whenever the asset straddles it; otherwise the middle of its box. */
  var lx = b.c[0] - b.size[0] / 2, hx = b.c[0] + b.size[0] / 2;
  return { ok: true, c: b.c, radius: b.radius, fov: 38, ortho: !!S.ortho,
           canvas: [S.canvas.width, S.canvas.height], engine: S.kind, rows: rows,
           mid: (lx < 0 && hx > 0) ? 0 : b.c[0], height: b.size[1] };
};

/* Where the camera that is set NOW puts these world points, in canvas pixels. */
F.project = function (pts) {
  if (!S || !S.canvas) return null;
  var out = [];
  (pts || []).forEach(function (p) {
    var q = Array.isArray(p) && p.length === 3 ? toPx(+p[0], +p[1], +p[2]) : null;
    out.push(q ? [r6(q[0]), r6(q[1])] : null);
  });
  return { canvas: [S.canvas.width, S.canvas.height], px: out, view: S.view || '' };
};

/* THE EDIT THAT MAKES A WORLD MOVE. `edits` move a node in its PARENT's space, so a part inside a
   scaled or turned group - the forge goblin's head sits in a head space scaled 1.3x - moves by
   something else. The solve's world move is carried into that frame here. */
F.snapEdit = function (part, move) {
  var hit = pickNodes(part);
  var m = [+(move && move[0]) || 0, +(move && move[1]) || 0, +(move && move[2]) || 0];
  if (!hit.length) return { target: String(part || ''), found: 0 };
  var o = hit[0], loc = m.slice();
  try {
    if (S.kind === 'three') {
      if (o.parent) {
        o.parent.updateMatrixWorld(true);
        var M3 = new S.T.Matrix3().setFromMatrix4(o.parent.matrixWorld).invert();
        var v = new S.T.Vector3(m[0], m[1], m[2]).applyMatrix3(M3);
        loc = [v.x, v.y, v.z];
      }
    } else if (o.parent && o.parent.getWorldTransform) {
      var inv = o.parent.getWorldTransform().clone().invert();
      var v2 = new S.pc.Vec3(m[0], m[1], m[2]);
      inv.transformVector(v2, v2);
      loc = [v2.x, v2.y, v2.z];
    }
  } catch (e) { /* the world move is still the answer */ }
  return { target: String(part), found: hit.length, names: nodeNames(hit),
           move: [r6(loc[0]), r6(loc[1]), r6(loc[2])] };
};

/* -------------------------------------------------------------------- holes
   AN OPEN EDGE IS NOT A HOLE UNTIL SOMEBODY CAN SEE IT. `ops.check` counts every open edge, so a
   surface that deliberately ends INSIDE another part - 267 open edges inside the forge goblin's
   helmet - reads exactly like a real gap, and the blind graders saw a real one at the same
   goblin's sword wrist. So each open boundary loop gets two questions, answered by ray parity
   (three axis rays, a majority of odd crossings = inside) against the asset's OTHER meshes:
     hidden   its edge points lie inside another part: the surface ends in there
     plugged  another part fills its middle: an arm through a sleeve, a head under a helmet
   What is left is either the rim of an open shell - a pauldron, a cape, a leaf, whose opening is
   a large share of its own area - or a HOLE somebody can look into from outside.

   Two more are not holes, both found on the goblins' own GLBs:
     seam     the loop IS another mesh's open loop: one surface split into two named parts - both
              builders cut leg and foot apart at the ankle, and the leg read "a 19 cm hole"
     pinhole  narrower than 1.5% of the subject's radius (a centimetre on a 1 m figure): the pole
              of a radial grid, a sliver where a lathe closes - never a clear fault, so never said */
var HOLE_SHEET = 0.35;
var HOLE_MIN = 0.015;
var holesMemo = null;

var soupOf = function () {
  var list = [];
  var shown = function (o) {
    for (var p = o; p; p = p.parent) {
      if (p.visible === false) return false;
      if (p === S.root) break;
    }
    return true;
  };
  var ups = function (o) {
    var names = [];
    for (var p = o.parent; p && p !== S.root; p = p.parent) {
      if (!p.__forgeGlb && p.name && names.length < 6) names.push(String(p.name));
    }
    return names;
  };
  try {
    if (S.kind === 'three') {
      S.root.updateMatrixWorld(true);
      S.root.traverse(function (o) {
        if (!o.isMesh || !o.geometry || !o.geometry.attributes || !o.geometry.attributes.position) return;
        if (!shown(o)) return;
        var pa = o.geometry.attributes.position;
        list.push({ o: o, name: nameOf(o) || '(unnamed)', count: pa.count, under: ups(o),
                    key: o.uuid + ':' + (o.geometry.uuid || '') + ':' + (pa.version || 0) + ':' +
                         Array.prototype.join.call(o.matrixWorld.elements, ',') });
      });
    } else {
      (function walk(e) {
        if (e.enabled === false) return;
        var mis = (e.render && e.render.meshInstances) || (e.model && e.model.meshInstances) || [];
        for (var i = 0; i < mis.length; i++) {
          list.push({ mi: mis[i], name: e.name || '(unnamed)', count: 0, under: ups(e),
                      key: (e.getGuid ? e.getGuid() : e.name) + ':' + i + ':' +
                           Array.prototype.join.call(mis[i].node.getWorldTransform().data, ',') });
        }
        var kids = e.children || [];
        for (var j = 0; j < kids.length; j++) walk(kids[j]);
      })(S.root);
    }
  } catch (e) {}
  return list;
};

var meshOf = function (it) {
  /* One mesh as world-space triangles, with every triangle's box for the ray cull. */
  var pos, idx = null;
  if (it.o) {
    var g = it.o.geometry, pa = g.attributes.position, e = it.o.matrixWorld.elements;
    pos = new Float32Array(pa.count * 3);
    for (var i = 0; i < pa.count; i++) {
      var x = pa.getX(i), y = pa.getY(i), z = pa.getZ(i);
      pos[3 * i] = e[0] * x + e[4] * y + e[8] * z + e[12];
      pos[3 * i + 1] = e[1] * x + e[5] * y + e[9] * z + e[13];
      pos[3 * i + 2] = e[2] * x + e[6] * y + e[10] * z + e[14];
    }
    if (g.index) idx = g.index.array;
  } else {
    var p = [], ix = [];
    it.mi.mesh.getPositions(p);
    try { it.mi.mesh.getIndices(ix); } catch (e2) { ix = []; }
    var d = it.mi.node.getWorldTransform().data;
    pos = new Float32Array(p.length);
    for (var k = 0; k + 2 < p.length; k += 3) {
      pos[k] = d[0] * p[k] + d[4] * p[k + 1] + d[8] * p[k + 2] + d[12];
      pos[k + 1] = d[1] * p[k] + d[5] * p[k + 1] + d[9] * p[k + 2] + d[13];
      pos[k + 2] = d[2] * p[k] + d[6] * p[k + 1] + d[10] * p[k + 2] + d[14];
    }
    if (ix.length) idx = ix;
  }
  var tris = idx ? Math.floor(idx.length / 3) : Math.floor(pos.length / 9);
  var tb = new Float32Array(tris * 6), area = 0;
  var lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (var t = 0; t < tris; t++) {
    var a = 3 * (idx ? idx[3 * t] : 3 * t), b = 3 * (idx ? idx[3 * t + 1] : 3 * t + 1),
        c = 3 * (idx ? idx[3 * t + 2] : 3 * t + 2);
    for (var q = 0; q < 3; q++) {
      var mn = Math.min(pos[a + q], pos[b + q], pos[c + q]), mx = Math.max(pos[a + q], pos[b + q], pos[c + q]);
      tb[6 * t + q] = mn; tb[6 * t + 3 + q] = mx;
      if (mn < lo[q]) lo[q] = mn;
      if (mx > hi[q]) hi[q] = mx;
    }
    var ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
    var vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
    var cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
    area += Math.sqrt(cx * cx + cy * cy + cz * cz) / 2;
  }
  return { name: it.name, pos: pos, idx: idx, tris: tris, tb: tb, lo: lo, hi: hi, area: area };
};

/* The open boundary loops of one mesh, welded by position: a UV seam or a hard edge splits
   vertices, and without the weld every seam would read as two open edges. */
var loopsOf = function (M) {
  var pos = M.pos, nv = pos.length / 3;
  var dg = Math.sqrt(Math.pow(M.hi[0] - M.lo[0], 2) + Math.pow(M.hi[1] - M.lo[1], 2) +
                     Math.pow(M.hi[2] - M.lo[2], 2)) || 1;
  var qd = dg * 1e-5, ids = new Int32Array(nv), map = new Map(), rep = [];
  for (var v = 0; v < nv; v++) {
    var key = Math.round(pos[3 * v] / qd) + '_' + Math.round(pos[3 * v + 1] / qd) + '_' +
              Math.round(pos[3 * v + 2] / qd);
    var id = map.get(key);
    if (id === undefined) { id = rep.length; map.set(key, id); rep.push(v); }
    ids[v] = id;
  }
  var nw = rep.length, cnt = new Map();
  var ek = function (a, b) { return a < b ? a * nw + b : b * nw + a; };
  /* The connected pieces of the mesh, and each one's area: a loop is the rim of an open shell or
     a hole in a surface by the piece it belongs to - six foliage flaps in one mesh are six shells,
     not one surface with six holes (the cartoon pine read "20 holes" before this). */
  var par = new Int32Array(nw);
  for (var i0 = 0; i0 < nw; i0++) par[i0] = i0;
  var fnd = function (x) { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  var tri3 = [];
  for (var t = 0; t < M.tris; t++) {
    var a = ids[M.idx ? M.idx[3 * t] : 3 * t], b = ids[M.idx ? M.idx[3 * t + 1] : 3 * t + 1],
        c = ids[M.idx ? M.idx[3 * t + 2] : 3 * t + 2];
    if (a === b || b === c || a === c) continue;
    var k1 = ek(a, b), k2 = ek(b, c), k3 = ek(c, a);
    cnt.set(k1, (cnt.get(k1) || 0) + 1);
    cnt.set(k2, (cnt.get(k2) || 0) + 1);
    cnt.set(k3, (cnt.get(k3) || 0) + 1);
    var ra = fnd(a), rb = fnd(b), rc = fnd(c);
    if (ra !== rb) par[ra] = rb;
    rc = fnd(c); rb = fnd(b);
    if (rc !== rb) par[rc] = rb;
    tri3.push(a, b, c);
  }
  var areaBy = new Float64Array(nw);
  for (var q = 0; q < tri3.length; q += 3) {
    var pa3 = 3 * rep[tri3[q]], pb3 = 3 * rep[tri3[q + 1]], pc3 = 3 * rep[tri3[q + 2]];
    var ux = pos[pb3] - pos[pa3], uy = pos[pb3 + 1] - pos[pa3 + 1], uz = pos[pb3 + 2] - pos[pa3 + 2];
    var vx = pos[pc3] - pos[pa3], vy = pos[pc3 + 1] - pos[pa3 + 1], vz = pos[pc3 + 2] - pos[pa3 + 2];
    var cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
    areaBy[fnd(tri3[q])] += Math.sqrt(cx * cx + cy * cy + cz * cz) / 2;
  }
  var adj = new Map(), open = 0;
  var link = function (a, b) { var l = adj.get(a); if (!l) { l = []; adj.set(a, l); } l.push(b); };
  cnt.forEach(function (n, k) {
    if (n !== 1) return;
    open++;
    var a = Math.floor(k / nw), b = k - a * nw;
    link(a, b); link(b, a);
  });
  var used = new Set(), loops = [], areas = [];
  adj.forEach(function (nb, s) {
    for (var i = 0; i < nb.length; i++) {
      var k0 = ek(s, nb[i]);
      if (used.has(k0)) continue;
      used.add(k0);
      var ring = [s], cur = nb[i], guard = 0;
      while (cur !== s && guard++ < 500000) {
        ring.push(cur);
        var nn = adj.get(cur) || [], nxt = -1;
        for (var j = 0; j < nn.length; j++) {
          var k2b = ek(cur, nn[j]);
          if (!used.has(k2b)) { used.add(k2b); nxt = nn[j]; break; }
        }
        if (nxt < 0) break;
        cur = nxt;
      }
      if (ring.length >= 3) {
        loops.push(ring.map(function (w) { var p = rep[w]; return [pos[3 * p], pos[3 * p + 1], pos[3 * p + 2]]; }));
        areas.push(areaBy[fnd(ring[0])]);
      }
    }
  });
  return { open: open, loops: loops, areas: areas };
};

/* Crossings of the ray from p along +axis with one mesh's triangles. */
var crossings = function (M, p, a) {
  var b = (a + 1) % 3, c = (a + 2) % 3, pos = M.pos, tb = M.tb, n = 0;
  var pa = p[a], pb = p[b], pc = p[c];
  for (var t = 0; t < M.tris; t++) {
    var o6 = 6 * t;
    if (tb[o6 + 3 + a] < pa || tb[o6 + b] > pb || tb[o6 + 3 + b] < pb ||
        tb[o6 + c] > pc || tb[o6 + 3 + c] < pc) continue;
    var i0 = 3 * (M.idx ? M.idx[3 * t] : 3 * t), i1 = 3 * (M.idx ? M.idx[3 * t + 1] : 3 * t + 1),
        i2 = 3 * (M.idx ? M.idx[3 * t + 2] : 3 * t + 2);
    var x0 = pos[i0 + b] - pb, y0 = pos[i0 + c] - pc, x1 = pos[i1 + b] - pb, y1 = pos[i1 + c] - pc,
        x2 = pos[i2 + b] - pb, y2 = pos[i2 + c] - pc;
    var w0 = x1 * y2 - x2 * y1, w1 = x2 * y0 - x0 * y2, w2 = x0 * y1 - x1 * y0;
    if (!((w0 > 0 && w1 > 0 && w2 > 0) || (w0 < 0 && w1 < 0 && w2 < 0))) continue;
    var h = (w0 * pos[i0 + a] + w1 * pos[i1 + a] + w2 * pos[i2 + a]) / (w0 + w1 + w2);
    if (h > pa) n++;
  }
  return n;
};

var insideOf = function (M, p, jit) {
  var q = [p[0] + jit[0], p[1] + jit[1], p[2] + jit[2]];
  for (var k = 0; k < 3; k++) if (q[k] < M.lo[k] || q[k] > M.hi[k]) return false;
  var odd = 0;
  for (var ax = 0; ax < 3; ax++) {
    if (crossings(M, q, ax) % 2 === 1) odd++;
    if (odd >= 2) return true;
    if (ax === 1 && odd === 0) return false;
  }
  return odd >= 2;
};

/* The asset as world-space triangles, built once per state of the scene and shared by holes and
   gaps: the signature is every mesh's id, geometry version and world matrix, so an edit that
   moves a part is a new soup and a second look at the same bench is free. */
var soupMemo = null;
var soupNow = function () {
  var list = soupOf();
  var sig = list.map(function (it) { return it.key; }).join('|');
  if (soupMemo && soupMemo.sig === sig) return soupMemo;
  var soup = [], total = 0;
  list.forEach(function (it) {
    if (total > 600000) return;
    try {
      var M = meshOf(it);
      M.under = it.under || [];
      total += M.tris;
      if (M.tris) soup.push(M);
    } catch (e) {}
  });
  soupMemo = { sig: sig, soup: soup };
  return soupMemo;
};

F.holes = function (budgetMs) {
  var res = { rows: [], partial: false, meshes: 0, ms: 0 };
  if (!S || S.flat) return res;
  var t0 = performance.now(), budget = +budgetMs > 0 ? +budgetMs : 900;
  var sn = soupNow(), sig = sn.sig, soup = sn.soup;
  if (holesMemo && holesMemo.sig === sig) return holesMemo.res;
  res.meshes = soup.length;
  var b = bounds();
  var R = b ? b.radius : 1;
  var jd = R * 1e-6, jit = [0.37 * jd, 0.61 * jd, 0.83 * jd];
  var insideOther = function (M, p) {
    for (var i = 0; i < soup.length; i++) {
      if (soup[i] !== M && insideOf(soup[i], p, jit)) return true;
    }
    return false;
  };
  var every = function (ring, k) {
    var n = ring.length, m = Math.min(k, n), out = [];
    for (var i = 0; i < m; i++) out.push(ring[Math.floor(i * n / m)]);
    return out;
  };
  /* Every mesh's open loops first, and every open-edge point in one hash, so a loop can ask
     whether another mesh's open edge runs along it - a surface cut in two, not a hole. */
  var loopsAll = soup.map(function (M) {
    try { return loopsOf(M); } catch (e) { return { open: 0, loops: [] }; }
  });
  var tol = Math.max(1e-7, R * 1e-4), grid = new Map();
  var cellOf = function (x, y, z) {
    return Math.floor(x / tol) + ',' + Math.floor(y / tol) + ',' + Math.floor(z / tol);
  };
  loopsAll.forEach(function (L, mi) {
    L.loops.forEach(function (ring) {
      ring.forEach(function (p) {
        var k = cellOf(p[0], p[1], p[2]), l = grid.get(k);
        if (!l) { l = []; grid.set(k, l); }
        l.push([mi, p[0], p[1], p[2]]);
      });
    });
  });
  var onOtherEdge = function (mi, p) {
    var cx = Math.floor(p[0] / tol), cy = Math.floor(p[1] / tol), cz = Math.floor(p[2] / tol);
    for (var dx = -1; dx <= 1; dx++) for (var dy = -1; dy <= 1; dy++) for (var dz = -1; dz <= 1; dz++) {
      var l = grid.get((cx + dx) + ',' + (cy + dy) + ',' + (cz + dz));
      if (!l) continue;
      for (var i = 0; i < l.length; i++) {
        if (l[i][0] === mi) continue;
        var ex = l[i][1] - p[0], ey = l[i][2] - p[1], ez = l[i][3] - p[2];
        if (ex * ex + ey * ey + ez * ez <= tol * tol) return true;
      }
    }
    return false;
  };
  for (var mi = 0; mi < soup.length; mi++) {
    var M = soup[mi];
    var L = loopsAll[mi];
    if (!L.open) continue;
    var row = { part: M.name, open_edges: L.open, loops: L.loops.length, seam: 0, pinhole: 0,
                ground: 0, hidden: 0, plugged: 0, shell: 0, visible: 0, holes: [] };
    for (var li = 0; li < L.loops.length; li++) {
      if (performance.now() - t0 > budget) { res.partial = true; break; }
      var ring = L.loops[li];
      var c = [0, 0, 0];
      ring.forEach(function (p) { c[0] += p[0]; c[1] += p[1]; c[2] += p[2]; });
      c = [c[0] / ring.length, c[1] / ring.length, c[2] / ring.length];
      var sam = every(ring, 48), across = 0;
      for (var i = 0; i < sam.length; i++) {
        for (var j = i + 1; j < sam.length; j++) {
          var dx = sam[i][0] - sam[j][0], dy = sam[i][1] - sam[j][1], dz = sam[i][2] - sam[j][2];
          across = Math.max(across, Math.sqrt(dx * dx + dy * dy + dz * dz));
        }
      }
      var va = [0, 0, 0];
      for (var r = 0; r < ring.length; r++) {
        var p0 = ring[r], p1 = ring[(r + 1) % ring.length];
        var ax0 = p0[0] - c[0], ay0 = p0[1] - c[1], az0 = p0[2] - c[2];
        var ax1 = p1[0] - c[0], ay1 = p1[1] - c[1], az1 = p1[2] - c[2];
        va[0] += ay0 * az1 - az0 * ay1; va[1] += az0 * ax1 - ax0 * az1; va[2] += ax0 * ay1 - ay0 * ax1;
      }
      var cap = Math.sqrt(va[0] * va[0] + va[1] * va[1] + va[2] * va[2]) / 2;
      /* `var` does not reset per iteration: every flag is set here, every time. */
      var rim = every(ring, 12), inRim = 0, onEdge = 0, state = '';
      for (var ri = 0; ri < rim.length; ri++) if (onOtherEdge(mi, rim[ri])) onEdge++;
      /* Flat and at the floor: it faces the ground, and nobody sees under a thing that stands. */
      var vn = Math.sqrt(va[0] * va[0] + va[1] * va[1] + va[2] * va[2]) || 1;
      var atFloor = b && Math.abs(va[1] / vn) > 0.8 && c[1] - (b.c[1] - b.size[1] / 2) < b.size[1] * 0.02;
      if (across < R * HOLE_MIN) {
        state = 'pinhole';
      } else if (onEdge >= Math.ceil(rim.length * 0.75)) {
        state = 'seam';
      } else if (atFloor) {
        state = 'ground';
      } else {
        for (var rj = 0; rj < rim.length; rj++) if (insideOther(M, rim[rj])) inRim++;
        /* Half is enough: an open shell round it defeats parity for some of the points (the
           cartoon pine's core, buried in six open cones, read 6 of 10). A real hole reads 0. */
        if (inRim >= Math.ceil(rim.length * 0.5)) {
          state = 'hidden';
        } else {
          var capPts = [c], quarter = every(ring, 4), inCap = 0;
          for (var qi = 0; qi < quarter.length; qi++) {
            var qp = quarter[qi];
            capPts.push([(c[0] + qp[0]) / 2, (c[1] + qp[1]) / 2, (c[2] + qp[2]) / 2]);
          }
          for (var ci = 0; ci < capPts.length; ci++) if (insideOther(M, capPts[ci])) inCap++;
          var piece = (L.areas && L.areas[li]) || M.area;
          if (inCap >= 3) state = 'plugged';
          else if (piece > 0 && cap / piece >= HOLE_SHEET) state = 'shell';
          else state = 'hole';
        }
      }
      if (state === 'hole') {
        row.visible++;
        if (row.holes.length < 4) {
          row.holes.push({ across: Math.round(across * 10000) / 10000,
                           at: [r3(c[0]), r3(c[1]), r3(c[2])],
                           share: Math.round(cap / Math.max(1e-12, (L.areas && L.areas[li]) || M.area) * 1000) / 1000,
                           rim_inside: inRim + '/' + rim.length });
        }
      } else row[state]++;
    }
    res.rows.push(row);
    if (res.partial) break;
  }
  res.ms = Math.round(performance.now() - t0);
  holesMemo = { sig: sig, res: res };
  return res;
};

/* --------------------------------------------------------------------- gaps
   A PART THAT FLOATS. Blind graders looked at the forge goblin from BEHIND and saw "pauldron
   shells floating with gaps" that nobody had seen from the picture's angle. So: every part joined
   to every part its surface comes within 1% of the figure's height of (sampled vertices against
   the other's triangles, both ways), and whatever is not joined to the largest piece is floating.
   A held sword is joined through the hand that holds its grip; one the hand misses is not - which
   is the fault. A piece hidden inside another part is left out. */
var gapsMemo = null;
/* Squared distance from a point to one triangle of M (Ericson, Real-Time Collision Detection 5.1.5). */
var triD2 = function (px, py, pz, M, t) {
  var P = M.pos;
  var ia = 3 * (M.idx ? M.idx[3 * t] : 3 * t), ib = 3 * (M.idx ? M.idx[3 * t + 1] : 3 * t + 1),
      ic = 3 * (M.idx ? M.idx[3 * t + 2] : 3 * t + 2);
  var ax = P[ia], ay = P[ia + 1], az = P[ia + 2];
  var abx = P[ib] - ax, aby = P[ib + 1] - ay, abz = P[ib + 2] - az;
  var acx = P[ic] - ax, acy = P[ic + 1] - ay, acz = P[ic + 2] - az;
  var apx = px - ax, apy = py - ay, apz = pz - az;
  var d1 = abx * apx + aby * apy + abz * apz, d2 = acx * apx + acy * apy + acz * apz;
  var qx, qy, qz;
  if (d1 <= 0 && d2 <= 0) { qx = ax; qy = ay; qz = az; }
  else {
    var bpx = px - P[ib], bpy = py - P[ib + 1], bpz = pz - P[ib + 2];
    var d3 = abx * bpx + aby * bpy + abz * bpz, d4 = acx * bpx + acy * bpy + acz * bpz;
    var cpx = px - P[ic], cpy = py - P[ic + 1], cpz = pz - P[ic + 2];
    var d5 = abx * cpx + aby * cpy + abz * cpz, d6 = acx * cpx + acy * cpy + acz * cpz;
    var vc = d1 * d4 - d3 * d2, vb = d5 * d2 - d1 * d6, va = d3 * d6 - d5 * d4;
    if (d3 >= 0 && d4 <= d3) { qx = P[ib]; qy = P[ib + 1]; qz = P[ib + 2]; }
    else if (vc <= 0 && d1 >= 0 && d3 <= 0) {
      var v = d1 / (d1 - d3); qx = ax + v * abx; qy = ay + v * aby; qz = az + v * abz;
    } else if (d6 >= 0 && d5 <= d6) { qx = P[ic]; qy = P[ic + 1]; qz = P[ic + 2]; }
    else if (vb <= 0 && d2 >= 0 && d6 <= 0) {
      var w = d2 / (d2 - d6); qx = ax + w * acx; qy = ay + w * acy; qz = az + w * acz;
    } else if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
      var u = (d4 - d3) / ((d4 - d3) + (d5 - d6));
      qx = P[ib] + u * (P[ic] - P[ib]); qy = P[ib + 1] + u * (P[ic + 1] - P[ib + 1]);
      qz = P[ib + 2] + u * (P[ic + 2] - P[ib + 2]);
    } else {
      var dn = 1 / (va + vb + vc), vv = vb * dn, ww = vc * dn;
      qx = ax + abx * vv + acx * ww; qy = ay + aby * vv + acy * ww; qz = az + abz * vv + acz * ww;
    }
  }
  var dx = px - qx, dy = py - qy, dz = pz - qz;
  return dx * dx + dy * dy + dz * dz;
};
/* Points ON a part's surface to test with: its vertices and its triangles' centres, at most `k`
   of each. Centres matter for low-poly parts: a grip run through a box fist crosses the fist's
   faces with no vertex of either anywhere near the other. */
var samplesOf = function (M, k) {
  M.samp = M.samp || {};
  if (M.samp[k]) return M.samp[k];
  var out = [], P = M.pos, nv = P.length / 3;
  var sv = Math.max(1, Math.floor(nv / k)), st = Math.max(1, Math.floor(M.tris / k));
  for (var v = 0; v < nv; v += sv) out.push(P[3 * v], P[3 * v + 1], P[3 * v + 2]);
  for (var t = 0; t < M.tris; t += st) {
    var ia = 3 * (M.idx ? M.idx[3 * t] : 3 * t), ib = 3 * (M.idx ? M.idx[3 * t + 1] : 3 * t + 1),
        ic = 3 * (M.idx ? M.idx[3 * t + 2] : 3 * t + 2);
    out.push((P[ia] + P[ib] + P[ic]) / 3, (P[ia + 1] + P[ib + 1] + P[ic + 1]) / 3,
             (P[ia + 2] + P[ib + 2] + P[ic + 2]) / 3);
  }
  M.samp[k] = out;
  return out;
};
/* The nearest approach of two parts' surfaces, no further than `cap` (sampled points of each
   against the other's triangles, both ways). Stops as soon as it is within `touch`. */
var partGap = function (A, B, cap, touch) {
  var best = cap;
  var side = function (P, Q) {
    var sp = samplesOf(P, 500);
    for (var v = 0; v + 2 < sp.length; v += 3) {
      var px = sp[v], py = sp[v + 1], pz = sp[v + 2];
      var bx = Math.max(0, Q.lo[0] - px, px - Q.hi[0]), by = Math.max(0, Q.lo[1] - py, py - Q.hi[1]),
          bz = Math.max(0, Q.lo[2] - pz, pz - Q.hi[2]);
      if (bx * bx + by * by + bz * bz >= best * best) continue;
      for (var t = 0; t < Q.tris; t++) {
        var o6 = 6 * t, tb = Q.tb;
        var ex = Math.max(0, tb[o6] - px, px - tb[o6 + 3]), ey = Math.max(0, tb[o6 + 1] - py, py - tb[o6 + 4]),
            ez = Math.max(0, tb[o6 + 2] - pz, pz - tb[o6 + 5]);
        if (ex * ex + ey * ey + ez * ez >= best * best) continue;
        var d2 = triD2(px, py, pz, Q, t);
        if (d2 < best * best) {
          best = Math.sqrt(d2);
          if (best <= touch) return true;
        }
      }
    }
    return false;
  };
  if (side(A, B)) return best;
  side(B, A);
  return best;
};
var boxGap = function (A, B) {
  var s = 0;
  for (var k = 0; k < 3; k++) {
    var d = Math.max(0, A.lo[k] - B.hi[k], B.lo[k] - A.hi[k]);
    s += d * d;
  }
  return Math.sqrt(s);
};
/* Triangles of M by cell, built once per mesh and cell size, so "is any triangle within `touch`
   of this point" looks at a handful of triangles and not all of them. */
var gridOf = function (M, cell) {
  if (M.grid && M.gridCell === cell) return M.grid;
  var map = new Map(), big = [], tb = M.tb;
  for (var t = 0; t < M.tris; t++) {
    var o6 = 6 * t;
    var x0 = Math.floor(tb[o6] / cell), x1 = Math.floor(tb[o6 + 3] / cell);
    var y0 = Math.floor(tb[o6 + 1] / cell), y1 = Math.floor(tb[o6 + 4] / cell);
    var z0 = Math.floor(tb[o6 + 2] / cell), z1 = Math.floor(tb[o6 + 5] / cell);
    if ((x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) > 64) { big.push(t); continue; }
    for (var x = x0; x <= x1; x++) for (var y = y0; y <= y1; y++) for (var z = z0; z <= z1; z++) {
      var k = x + ',' + y + ',' + z, l = map.get(k);
      if (!l) { l = []; map.set(k, l); }
      l.push(t);
    }
  }
  M.grid = { map: map, big: big };
  M.gridCell = cell;
  return M.grid;
};
/* Does any part of A's surface come within `touch` of B's, or B's of A's - or pass inside it?
   `jit` is the ray parity's jitter, from the subject's own size. */
var touches = function (A, B, touch, jit) {
  var t2 = touch * touch;
  var near = function (px, py, pz, Q, t) {
    var o6 = 6 * t, tb = Q.tb;
    var ex = Math.max(0, tb[o6] - px, px - tb[o6 + 3]), ey = Math.max(0, tb[o6 + 1] - py, py - tb[o6 + 4]),
        ez = Math.max(0, tb[o6 + 2] - pz, pz - tb[o6 + 5]);
    return ex * ex + ey * ey + ez * ez <= t2 && triD2(px, py, pz, Q, t) <= t2;
  };
  var side = function (P, Q) {
    var dg = Math.sqrt(Math.pow(Q.hi[0] - Q.lo[0], 2) + Math.pow(Q.hi[1] - Q.lo[1], 2) +
                       Math.pow(Q.hi[2] - Q.lo[2], 2));
    var cell = Math.max(touch, dg / 40), G = gridOf(Q, cell);
    var sp = samplesOf(P, 3000);
    for (var v = 0; v + 2 < sp.length; v += 3) {
      var px = sp[v], py = sp[v + 1], pz = sp[v + 2];
      if (px < Q.lo[0] - touch || px > Q.hi[0] + touch || py < Q.lo[1] - touch ||
          py > Q.hi[1] + touch || pz < Q.lo[2] - touch || pz > Q.hi[2] + touch) continue;
      var x0 = Math.floor((px - touch) / cell), x1 = Math.floor((px + touch) / cell);
      var y0 = Math.floor((py - touch) / cell), y1 = Math.floor((py + touch) / cell);
      var z0 = Math.floor((pz - touch) / cell), z1 = Math.floor((pz + touch) / cell);
      for (var x = x0; x <= x1; x++) for (var y = y0; y <= y1; y++) for (var z = z0; z <= z1; z++) {
        var l = G.map.get(x + ',' + y + ',' + z);
        if (!l) continue;
        for (var i = 0; i < l.length; i++) if (near(px, py, pz, Q, l[i])) return true;
      }
      for (var j = 0; j < G.big.length; j++) if (near(px, py, pz, Q, G.big[j])) return true;
    }
    return false;
  };
  if (side(A, B) || side(B, A)) return true;
  /* THROUGH IT, not near it: a neck tube pushed into a torso crosses the torso's face where
     neither has a vertex, so no vertex is near the other surface - but some are INSIDE it. */
  if (boxGap(A, B) > 0 || !jit) return false;
  var inside = function (P, Q) {
    var sp = samplesOf(P, 24);
    for (var v = 0; v + 2 < sp.length; v += 3) {
      if (insideOf(Q, [sp[v], sp[v + 1], sp[v + 2]], jit)) return true;
    }
    return false;
  };
  return inside(A, B) || inside(B, A);
};

F.gaps = function (budgetMs) {
  var res = { rows: [], pieces: 0, partial: false, ms: 0 };
  if (!S || S.flat) return res;
  var t0 = performance.now(), budget = +budgetMs > 0 ? +budgetMs : 900;
  var sn = soupNow(), soup = sn.soup;
  if (gapsMemo && gapsMemo.sig === sn.sig) return gapsMemo.res;
  var b = bounds();
  var H = b ? b.size[1] : 1, touch = H * 0.01;
  var jd = (b ? b.radius : 1) * 1e-6, jit = [0.37 * jd, 0.61 * jd, 0.83 * jd];
  var n = soup.length, up = [];
  for (var i = 0; i < n; i++) up.push(i);
  var find = function (i) { while (up[i] !== i) { up[i] = up[up[i]]; i = up[i]; } return i; };
  for (var a = 0; a < n && !res.partial; a++) {
    for (var c = a + 1; c < n; c++) {
      if (find(a) === find(c)) continue;
      if (boxGap(soup[a], soup[c]) > touch) continue;
      if (touches(soup[a], soup[c], touch, jit)) up[find(a)] = find(c);
      if (performance.now() - t0 > budget) { res.partial = true; break; }
    }
  }
  var groups = {};
  for (var m = 0; m < n; m++) { var r = find(m); (groups[r] = groups[r] || []).push(m); }
  var keys = Object.keys(groups), mainKey = null, mainArea = -1;
  keys.forEach(function (k) {
    var ar = groups[k].reduce(function (s, j) { return s + soup[j].area; }, 0);
    if (ar > mainArea) { mainArea = ar; mainKey = k; }
  });
  res.pieces = keys.length;
  keys.forEach(function (k) {
    if (k === mainKey || res.rows.length >= 12) return;
    var g = groups[k], lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    g.forEach(function (j) {
      for (var q = 0; q < 3; q++) { lo[q] = Math.min(lo[q], soup[j].lo[q]); hi[q] = Math.max(hi[q], soup[j].hi[q]); }
    });
    var ctr = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    for (var s = 0; s < n; s++) {
      if (g.indexOf(s) < 0 && insideOf(soup[s], ctr, jit)) return;     /* hidden inside a part */
    }
    var gap = Infinity, near = '';
    groups[mainKey].forEach(function (j) {
      g.forEach(function (i2) {
        if (boxGap(soup[i2], soup[j]) >= gap) return;
        var d = partGap(soup[i2], soup[j], Math.min(gap, H * 0.5), 0);
        if (d < gap) { gap = d; near = soup[j].name; }
      });
    });
    var names = [], under = null;
    g.forEach(function (j) {
      if (names.indexOf(soup[j].name) < 0 && names.length < 8) names.push(soup[j].name);
      var u = soup[j].under || [];
      under = under === null ? u.slice() : under.filter(function (x) { return u.indexOf(x) >= 0; });
    });
    var tris = g.reduce(function (s2, j) { return s2 + soup[j].tris; }, 0);
    res.rows.push({ parts: names, group: (under && under.length) ? under[0] : '', meshes: g.length,
                    tris: tris, gap: isFinite(gap) ? Math.round(gap * 10000) / 10000 : null,
                    nearest: near, at: [r3(ctr[0]), r3(ctr[1]), r3(ctr[2])] });
  });
  res.rows.sort(function (x, y) { return (y.gap || 0) - (x.gap || 0); });
  res.ms = Math.round(performance.now() - t0);
  gapsMemo = { sig: sn.sig, res: res };
  return res;
};

F.ready = function () { return { built: !!S, engine: S ? S.kind : '' }; };

/* ------------------------------------------------------------ the import heal
   A forge tab opened before `index.html` had its import map keeps THAT document, and every
   `import('./src/assets.js')` whose file says `from 'three'` then fails with `Failed to resolve
   module specifier "three"` until the tab is reloaded - which nothing used to say. The backend
   heals it (live.py, `_heal_imports`); these are the page's half of that. */

/* The engine module the forge built with, as an absolute URL - what an import map points the bare
   name at, so the agent's module and the forge share ONE copy of the engine. */
F.engineHref = function () {
  try { return S && S.engineUrl ? new URL(S.engineUrl, location.href).href : ''; }
  catch (e) { return ''; }
};

/* Does the page's SERVED html map this specifier, and does the document it is showing? Served
   yes and document no is a stale tab, and a reload fixes it; neither means a reload cannot help. */
F.mapState = async function (spec) {
  var maps = function (txt) {
    try {
      var im = (JSON.parse(txt) || {}).imports || {};
      /* The exact name, or a prefix entry for a subpath: `three` alone does not map
         `three/addons/x.js`, and saying it did would skip the one step that fixes it. */
      return Object.keys(im).some(function (k) {
        return k === spec || (k.charAt(k.length - 1) === '/' && String(spec).indexOf(k) === 0);
      });
    } catch (e) { return false; }
  };
  var mine = Array.prototype.slice.call(document.querySelectorAll('script[type="importmap"]'))
    .filter(function (s) { return !s.hasAttribute('data-studio'); })
    .some(function (s) { return maps(s.textContent || ''); });
  var served = null;
  try {
    var r = await fetch(location.href, { cache: 'no-store' });
    var html = await r.text();
    var rx = /<script\b[^>]*\btype\s*=\s*["']?importmap["']?[^>]*>([\s\S]*?)<\/script>/gi, m;
    served = false;
    while ((m = rx.exec(html))) { if (maps(m[1])) { served = true; break; } }
  } catch (e) { served = null; }
  return { document: mine, served: served, stale: served === true && !mine };
};

/* An import map added NOW. Chrome before 133 refuses one once any module has loaded - measured on
   the Chrome 131 this machine runs: added after the forge's own engine import, `import('three')`
   still failed - so the answer says whether it took, and the backend falls back to a map put in
   at document start. A newer Chrome merges it and the bench survives. */
F.lateMap = async function (map, probe) {
  try {
    var s = document.createElement('script');
    s.type = 'importmap';
    s.setAttribute('data-studio', 'heal');
    s.textContent = JSON.stringify(map);
    (document.head || document.documentElement).appendChild(s);
  } catch (e) { return { ok: false, error: String(e && e.message || e) }; }
  try { await import(/* @vite-ignore */ probe); return { ok: true }; }
  catch (e) { return { ok: false, error: String(e && e.message || e).slice(0, 300) }; }
};
})();
"""

# THE IMPORT MAP AT DOCUMENT START, for the Chrome that will not take one late.
#
# Registered for the next document of a tab whose bare `three` still fails after a reload, i.e. a
# page with no import map at all whose code was written for a bundler. It watches the parser and
# slips the map in just ahead of the page's first `<script type=module>` - the one moment Chrome
# 131 cannot refuse it, because no module has been asked for yet. Measured on the throwaway heal
# page: the page's own `import * as THREE from 'three'` booted (REVISION 183) and so did the
# forge's `import('three')`. A page that brings its own map is left alone: two maps is an error
# before Chrome 133, and the page's is the one that is right.
IMPORT_MAP_AT_START = r"""
(() => {
  if (window.__studioImportMap) return;
  window.__studioImportMap = 'waiting';
  var MAP = __MAP__;
  var done = false;
  var own = function () {
    return !!document.querySelector('script[type="importmap"]:not([data-studio])');
  };
  var put = function (before) {
    if (done) return;
    done = true;
    if (own()) { window.__studioImportMap = 'the page has its own'; return; }
    try {
      var s = document.createElement('script');
      s.type = 'importmap';
      s.setAttribute('data-studio', 'heal');
      s.textContent = JSON.stringify(MAP);
      if (before && before.parentNode) before.parentNode.insertBefore(s, before);
      else (document.head || document.documentElement).appendChild(s);
      window.__studioImportMap = 'inserted';
    } catch (e) { window.__studioImportMap = 'failed: ' + e; }
  };
  var mo = new MutationObserver(function (recs) {
    for (var i = 0; i < recs.length; i++) {
      var added = recs[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (done) { mo.disconnect(); return; }
        if (n.nodeName !== 'SCRIPT') continue;
        var t = String(n.getAttribute('type') || '').toLowerCase();
        if (t === 'importmap' && !n.hasAttribute('data-studio')) {
          done = true; window.__studioImportMap = 'the page has its own'; mo.disconnect(); return;
        }
        if (t === 'module') { put(n); mo.disconnect(); return; }
      }
    }
  });
  mo.observe(document, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', function () { put(null); mo.disconnect(); });
})();
"""

# Bump whenever the page script gains or changes a function the backend calls. A tab that outlived
# a backend restart holds the old script, and `_forge_script` replaces it only when this differs.
# 2: boxOf, present and texFacts, for the detail review.
# 15: engineHref, mapState and lateMap, for the import heal.
# 16: the bench scene carries a mark, so the live shim never takes it for the game's scene.
# 17: exampleModule, so glb/exportGlb bind to the page's own three and never take three for the
#     exporter (a page that loads three from its root answered "Ex is not a constructor").
# 18: solo(q, invert, exact) for compare; export without the bench wrapper; health `unlit`;
#     stats.textures counts the asset's own textures.
# 19: dirOf reads a preset with modifiers ("top,zoom=2.5"); dispose() frees the GL context.
# 21: the goblin A/B bench. pick() falls back to groups and takes "=name"; a written zoom pins the
#     camera; texFacts marks glTF textures; health reports tint percentiles; where() adds each
#     part's visible pixels (px, px_box, px_at) from an id render; matches, anchorFacts, project,
#     snapEdit, holes and symmetry.
FORGE_VERSION = 21
FORGE = FORGE.replace("__FORGE_VERSION__", str(FORGE_VERSION))

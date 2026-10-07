// review/targets.js: what the Studio's visual review can look at on its own, one builder at a time.
//
//   POST /api/review/render {"project": "<this folder>", "mode": "isolate", "target": "crate"}
//
// Each target makes a small PlayCanvas app of its own and mounts one builder in it on a plain
// backdrop, lit, framed by its bounding box, standing on a disc at y = 0 (so a builder whose feet
// are not on the ground is visible at once), and turning slowly, so the frames the review samples
// over time are a turntable. The review drives the clock: the app's frames are stepped by the
// harness, never by real time.
//
// WHICH DOCUMENT IT DRAWS IN. The review loads this file into whatever page the game's server
// answers at /__studio_review__. serve.mjs answers with this game's import map, `import
// 'playcanvas'` resolves, and the builder is drawn right here. Any other server (the Studio's own
// static server serves the game when no dev server is running) answers a bare page where it
// cannot, and the headless Chrome the Studio drives (131, measured) refuses an import map added
// once a module has loaded, which this file already is. So there the builder is drawn in an iframe
// whose own document carries the map, and this document drives its frames: the review steps this
// document's clock and no other.
//
// Add one target per builder you add to src/assets.js.

const TARGETS = [
  { name: 'crate', note: 'buildCrate(): a 1 m crate, turning', build: (a, app) => a.buildCrate({ app, size: 1 }) },
  { name: 'tree', note: 'buildTree(): a 3 m tree, turning', build: (a, app) => a.buildTree({ app, height: 3 }) },
];

function resolves(name) {
  try {
    import.meta.resolve(name);
    return true;
  } catch (e) {
    return false;
  }
}

function boundsOf(pc, entity) {
  let box = null;
  for (const r of entity.findComponents('render')) {
    for (const mi of r.meshInstances || []) {
      if (!box) box = new pc.BoundingBox(mi.aabb.center.clone(), mi.aabb.halfExtents.clone());
      else box.add(mi.aabb);
    }
  }
  return box;
}

/** Draw one target into `root` and return its frame function. `own`: run the app's own loop
 *  (false when a parent document drives the frames). */
export async function draw(root, ctx, name, own = true) {
  const pc = await import('playcanvas');
  const assets = await import('../src/assets.js');
  const target = TARGETS.find((t) => t.name === name);
  if (!target) throw new Error('no target ' + name);

  const dpr = ctx.pixelRatio || 1;
  const canvas = document.createElement('canvas');
  canvas.id = 'review-target';
  canvas.width = Math.round(ctx.width * dpr);
  canvas.height = Math.round(ctx.height * dpr);
  canvas.style.cssText = `width:${ctx.width}px;height:${ctx.height}px;display:block`;
  root.appendChild(canvas);

  const app = new pc.Application(canvas, { graphicsDeviceOptions: { antialias: true, preserveDrawingBuffer: true } });
  app.setCanvasFillMode(pc.FILLMODE_NONE);
  app.setCanvasResolution(pc.RESOLUTION_FIXED, canvas.width, canvas.height);
  app.scene.ambientLight = new pc.Color(0.34, 0.37, 0.44);

  const bg = new pc.Color();
  bg.fromString(ctx.background || '#12151c');
  const camera = new pc.Entity('review-camera', app);
  camera.addComponent('camera', { clearColor: bg, fov: 35, nearClip: 0.01, farClip: 1000 });
  app.root.addChild(camera);
  const key = new pc.Entity('review-key', app);
  key.addComponent('light', { type: 'directional', intensity: 1.5, castShadows: true,
                              shadowDistance: 30, shadowBias: 0.2, normalOffsetBias: 0.05 });
  key.setEulerAngles(45, 35, 0);
  app.root.addChild(key);

  const thing = target.build(assets, app);
  app.root.addChild(thing);
  app.root.syncHierarchy();
  const box = boundsOf(pc, thing);
  const c = box ? box.center.clone() : new pc.Vec3(0, 0.5, 0);
  const r = Math.max(box ? box.halfExtents.length() : 0.5, 0.05);

  const floorMat = new pc.StandardMaterial();
  floorMat.diffuse = new pc.Color(0.165, 0.19, 0.235);
  floorMat.update();
  const floor = new pc.Entity('review-floor', app);
  floor.addComponent('render', { type: 'cylinder', material: floorMat, castShadows: false, receiveShadows: true });
  floor.setLocalScale(r * 3.2, 0.01, r * 3.2);
  floor.setLocalPosition(c.x, -0.005, c.z);
  app.root.addChild(floor);

  const fov = 35 * (Math.PI / 180);
  const dist = (r / Math.sin(fov / 2)) * 1.05;
  const az = 35 * (Math.PI / 180);
  const el = 18 * (Math.PI / 180);
  camera.setPosition(c.x + Math.sin(az) * Math.cos(el) * dist, c.y + Math.sin(el) * dist,
                     c.z + Math.cos(az) * Math.cos(el) * dist);
  camera.lookAt(c);

  // The angle is a function of the time since mount, not a sum of `dt`: PlayCanvas clamps dt to
  // 0.1 s, so an 800 ms review step turned the builder 3 degrees instead of 27. A parent that
  // drives the frames passes its own mount time, because this document's clock is not stepped.
  const t0 = typeof ctx.t0 === 'number' ? ctx.t0 : performance.now();
  const pose = (now) => thing.setLocalEulerAngles(0, ((now - t0) / 1000) * 34, 0);
  if (own) {
    app.on('update', () => pose(performance.now()));
    app.start();
    return null;
  }
  // Driven from outside: one deliberate step and one render per frame, no loop of its own (the
  // way the Studio's forge steps a PlayCanvas app).
  const tick = (now) => {
    pose(now);
    app.update(1 / 60);
    app.render();
  };
  tick(t0);
  return tick;
}

async function drawInFrame(root, ctx, name) {
  const game = new URL('../', import.meta.url).href;
  const map = { imports: { playcanvas: game + 'node_modules/playcanvas/build/playcanvas.mjs' } };
  const inner = { width: ctx.width, height: ctx.height, background: ctx.background,
                  pixelRatio: ctx.pixelRatio || 1, t0: performance.now() };
  const done = '__studioTarget_' + name;
  const tick = await new Promise((resolve, reject) => {
    window[done] = (err, fn) => {
      delete window[done];
      if (err) reject(new Error(err));
      else resolve(fn);
    };
    const end = '</' + 'script>';
    const frame = document.createElement('iframe');
    frame.style.cssText = `width:${ctx.width}px;height:${ctx.height}px;border:0;display:block`;
    frame.srcdoc = '<!doctype html><html><head><meta charset="utf-8"><script type="importmap">'
      + JSON.stringify(map) + end + '</head><body style="margin:0;overflow:hidden"><script type="module">'
      + 'import { draw } from ' + JSON.stringify(import.meta.url) + ';'
      + 'const report = parent[' + JSON.stringify(done) + '];'
      + 'draw(document.body, ' + JSON.stringify(inner) + ', ' + JSON.stringify(name) + ', false)'
      + '.then((f) => report(null, f), (e) => report(String((e && e.stack) || e)));'
      + end + '</body></html>';
    root.appendChild(frame);
  });
  const loop = (now) => {
    tick(now);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

function mount(root, ctx, name) {
  return resolves('playcanvas') ? draw(root, ctx, name, true) : drawInFrame(root, ctx, name);
}

export const targets = TARGETS.map((t) => ({
  name: t.name,
  note: t.note,
  mount: (root, ctx) => mount(root, ctx, t.name),
}));

export default targets;

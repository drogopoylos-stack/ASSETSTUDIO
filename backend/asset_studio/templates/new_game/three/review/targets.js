// review/targets.js: what the Studio's visual review can look at on its own, one builder at a time.
//
//   POST /api/review/render {"project": "<this folder>", "mode": "isolate", "target": "crate"}
//
// Each target mounts one builder on a plain backdrop, lit, framed by its bounding sphere, standing
// on a disc at y = 0 (so a builder whose feet are not on the ground is visible at once), and
// turning slowly, so the frames the review samples over time are a turntable. The review drives
// the clock: requestAnimationFrame here is stepped by the harness, never by real time.
//
// WHICH DOCUMENT IT DRAWS IN. The review loads this file into whatever page the game's server
// answers at /__studio_review__. serve.mjs answers with this game's import map, `import 'three'`
// resolves, and the builder is drawn right here. Any other server (the Studio's own static server
// serves the game when no dev server is running) answers a bare page where it cannot, and the
// headless Chrome the Studio drives (131, measured) refuses an import map added once a module has
// loaded, which this file already is. So there the builder is drawn in an iframe whose own
// document carries the map, and this document drives its frames: the review steps this
// document's clock and no other.
//
// Add one target per builder you add to src/assets.js.

const TARGETS = [
  { name: 'crate', note: 'buildCrate(): a 1 m crate, turning', build: (a) => a.buildCrate({ size: 1 }) },
  { name: 'tree', note: 'buildTree(): a 3 m tree, turning', build: (a) => a.buildTree({ height: 3 }) },
];

function resolves(name) {
  try {
    import.meta.resolve(name);
    return true;
  } catch (e) {
    return false;
  }
}

/** Draw one target into `root` and return its frame function. `own`: schedule its own frames
 *  (false when a parent document drives them). */
export async function draw(root, ctx, name, own = true) {
  const THREE = await import('three');
  const assets = await import('../src/assets.js');
  const target = TARGETS.find((t) => t.name === name);
  if (!target) throw new Error('no target ' + name);

  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(ctx.pixelRatio || 1);
  renderer.setSize(ctx.width, ctx.height);
  renderer.shadowMap.enabled = true;
  root.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(ctx.background || '#12151c');
  scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x3a3328, 1.2));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(4, 7, 5);
  key.castShadow = true;
  // The default 512 px map with no bias striped the crate's faces with its own frame's shadow.
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.normalBias = 0.02;
  scene.add(key);

  const thing = target.build(assets);
  scene.add(thing);
  const sphere = new THREE.Box3().setFromObject(thing).getBoundingSphere(new THREE.Sphere());
  const r = Math.max(sphere.radius, 0.05);

  const floor = new THREE.Mesh(
    new THREE.CircleGeometry(r * 1.6, 48),
    new THREE.MeshStandardMaterial({ color: 0x2a303c, roughness: 1 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  Object.assign(key.shadow.camera, { left: -r * 2, right: r * 2, top: r * 2, bottom: -r * 2, near: 0.1, far: r * 20 + 20 });

  const fov = 35;
  const camera = new THREE.PerspectiveCamera(fov, ctx.width / ctx.height, r / 100, r * 100);
  const dist = (r / Math.sin(THREE.MathUtils.degToRad(fov / 2))) * 1.05;
  const az = THREE.MathUtils.degToRad(35);
  const el = THREE.MathUtils.degToRad(18);
  camera.position.set(
    sphere.center.x + Math.sin(az) * Math.cos(el) * dist,
    sphere.center.y + Math.sin(el) * dist,
    sphere.center.z + Math.cos(az) * Math.cos(el) * dist,
  );
  camera.lookAt(sphere.center);

  // The angle is a function of the time since mount, not a sum of frame steps: the review samples
  // at 0, 800, 1600 ms in single steps, and a turn that waited a frame to learn its first
  // timestamp drew the first two samples at the same angle. A parent that drives the frames
  // passes its own mount time, because this document's clock is not the one being stepped.
  const t0 = typeof ctx.t0 === 'number' ? ctx.t0 : performance.now();
  const tick = (now) => {
    thing.rotation.y = ((now - t0) / 1000) * 0.6;
    renderer.render(scene, camera);
    if (own) requestAnimationFrame(tick);
  };
  renderer.render(scene, camera);           // a frame now, so t = 0 is never blank
  if (own) requestAnimationFrame(tick);
  return tick;
}

async function drawInFrame(root, ctx, name) {
  const game = new URL('../', import.meta.url).href;
  const map = { imports: { three: game + 'node_modules/three/build/three.module.js',
                           'three/addons/': game + 'node_modules/three/examples/jsm/' } };
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
  return resolves('three') ? draw(root, ctx, name, true) : drawInFrame(root, ctx, name);
}

export const targets = TARGETS.map((t) => ({
  name: t.name,
  note: t.note,
  mount: (root, ctx) => mount(root, ctx, t.name),
}));

export default targets;

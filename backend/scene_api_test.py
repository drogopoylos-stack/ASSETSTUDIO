"""The scene API — objects, edit, edits — held to the engines' own answers.

Four halves.

The FILE: an edit merges into studio.edits.json without touching anything it did not name, writes
atomically, keeps ONE .bak per backend life, and undo puts an entry back exactly — including "there
was no entry". A wrong byte here is the user's saved work.

The REQUEST: what a body may say, and the refusal for everything else, before a browser is touched.

The ORCHESTRATION: edit, save, undo and clear through `live_scene`, with the page replaced by a fake,
so the order of the live half and the disk half is tested without Chrome.

The PAGE: the script injected into the game, run in node against a REAL three.js scene (the proof
game's layout) and REAL PlayCanvas entities, and checked against three's own Box3, Frustum,
Vector3.project and Raycaster, against ops.stableKeys bundled from ops.ts, and against three's own
Euler for PlayCanvas rotations. Nothing in it is compared with a copy of itself.
"""
import copy
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import live as L                  # noqa: E402
from asset_studio import live_scene as S            # noqa: E402
from asset_studio.config import DATA_DIR            # noqa: E402

# This suite tests what the scene tools DO, with the page faked. A new PC starts with the live
# link's switch off (Settings -> Planning), and every edit here was refused before it reached the
# fake. cc_scene_edit, the switch this suite does test, is stubbed where it is tested.
L.enabled = lambda: True

ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, str(extra)[:600]))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s — %s" % (name, why))


def near(a, b, tol=1e-3):
    try:
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b)) and len(a) == len(b)
    except Exception:
        return False


# ---------------------------------------------------------------------------------------- the node harness
# The page script runs here against real engine objects. Every "want" below comes from the engine
# (three's Box3, Frustum, Vector3.project, Raycaster, Euler) or from ops.stableKeys itself.
HARNESS = r"""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const A = JSON.parse(process.argv[2]);
const T = await import(pathToFileURL(A.three).href);
const ops = await import(pathToFileURL(A.ops).href);

globalThis.window = globalThis;
globalThis.innerWidth = 1280; globalThis.innerHeight = 720;
globalThis.location = { href: 'http://127.0.0.1:9/' };
let onFrame = null;
globalThis.requestAnimationFrame = (cb) => setTimeout(() => { if (onFrame) onFrame(); cb(performance.now()); }, 1);

const R = { three: {}, pc: null, keys_tricky: {} };
const near = (a, b, tol = 1e-3) => a && b && a.length === b.length && a.every((x, i) => Math.abs(x - b[i]) <= tol);
const deg = (v) => v.map((x) => x * 180 / Math.PI);
const sameKeys = (m1, m2) => {
  const a = [...m1], b = [...m2];
  return a.length === b.length && a.every(([o, k], i) => b[i][0] === o && b[i][1] === k);
};

/* ---------------------------------------------------------------- the proof game's scene */
function crystal(color) {
  const g = new T.Group(); g.name = 'crystal';
  const body = new T.Mesh(new T.OctahedronGeometry(0.35, 0), new T.MeshStandardMaterial({ color }));
  body.name = 'crystal-body'; body.scale.set(1, 1.1 / 0.7, 1); body.position.y = 1.1 / 2 + 0.05; g.add(body);
  const base = new T.Mesh(new T.CylinderGeometry(0.28, 0.34, 0.1, 8), new T.MeshStandardMaterial({ color: 0x3a3f58 }));
  base.name = 'crystal-base'; base.position.y = 0.05; g.add(base);
  return g;
}
function pillar(height) {
  const g = new T.Group(); g.name = 'pillar';
  const m = new T.MeshStandardMaterial({ color: 0xc9c2b2 });
  const plinth = new T.Mesh(new T.BoxGeometry(0.9, 0.3, 0.9), m); plinth.name = 'pillar-plinth'; plinth.position.y = 0.15;
  const shaft = new T.Mesh(new T.CylinderGeometry(0.3, 0.34, height - 0.6, 12), m); shaft.name = 'pillar-shaft'; shaft.position.y = 0.3 + (height - 0.6) / 2;
  const cap = new T.Mesh(new T.BoxGeometry(0.8, 0.3, 0.8), m); cap.name = 'pillar-cap'; cap.position.y = height - 0.15;
  g.add(plinth, shaft, cap);
  return g;
}
function lantern() {
  const g = new T.Group(); g.name = 'lantern';
  const post = new T.Mesh(new T.CylinderGeometry(0.05, 0.06, 1.6, 8), new T.MeshStandardMaterial()); post.name = 'lantern-post'; post.position.y = 0.8;
  const glow = new T.Mesh(new T.SphereGeometry(0.16, 12, 8), new T.MeshBasicMaterial()); glow.name = 'lantern-glow'; glow.position.y = 1.7;
  const light = new T.PointLight(0xffc36b, 6, 7, 2); light.name = 'lantern-light'; light.position.y = 1.7;
  g.add(post, glow, light);
  return g;
}
function floor(size) {
  const positions = [], indices = [];
  for (let i = 0; i < size; i++) for (let j = 0; j < size; j++) {
    const x0 = -size / 2 + i, z0 = -size / 2 + j, b = positions.length / 3;
    positions.push(x0, 0, z0, x0 + 1, 0, z0, x0 + 1, 0, z0 + 1, x0, 0, z0 + 1);
    indices.push(b, b + 2, b + 1, b, b + 3, b + 2);
  }
  const geo = new T.BufferGeometry();
  geo.setAttribute('position', new T.Float32BufferAttribute(positions, 3)); geo.setIndex(indices); geo.computeVertexNormals();
  const f = new T.Mesh(geo, new T.MeshStandardMaterial()); f.name = 'floor';
  return f;
}
function courtyard() {
  const scene = new T.Scene();
  scene.add(new T.HemisphereLight(0x9fb4ff, 0x20233a, 0.9));
  const sun = new T.DirectionalLight(0xffffff, 1.4); sun.name = 'sun'; sun.position.set(5, 9, 4); scene.add(sun);
  scene.add(floor(16));
  [[-5, -5], [5, -5], [-5, 5], [5, 5]].forEach(([x, z], i) => { const p = pillar(3); p.name = 'pillar-' + (i + 1); p.position.set(x, 0, z); scene.add(p); });
  [[-2, 6], [2, 6]].forEach(([x, z], i) => { const l = lantern(); l.name = 'lantern-' + (i + 1); l.position.set(x, 0, z); scene.add(l); });
  [[0, 0], [-3, 2], [3, -2], [-4, -3], [4, 3]].forEach(([x, z], i) => { const c = crystal(i % 2 ? 0xff7ae0 : 0x6ae3ff); c.name = 'crystal-' + (i + 1); c.position.set(x, 0, z); scene.add(c); });
  const player = new T.Mesh(new T.CapsuleGeometry(0.3, 0.8, 4, 12), new T.MeshStandardMaterial()); player.name = 'player'; player.position.set(0, 0.7, 6.5); scene.add(player);
  /* A sky dome, as rot-rush has: one welded shell whose box holds everything. */
  const sky = new T.Mesh(new T.SphereGeometry(300, 32, 16), new T.MeshBasicMaterial({ side: T.BackSide })); sky.name = 'sky'; scene.add(sky);
  /* An empty in plain view: a row, with a point on screen, but nothing to look at. */
  const marker = new T.Object3D(); marker.name = 'marker'; marker.position.set(5, 1, 0); scene.add(marker);
  scene.updateMatrixWorld(true);
  return scene;
}
const scene = courtyard();
/* A camera that sees half the courtyard, so in_view has both answers to give. Like the proof
   game's, it is NOT in the scene: only the render hook can find it. */
const camera = new T.PerspectiveCamera(55, 1280 / 720, 0.1, 100); camera.name = 'camera';
camera.position.set(-1, 3, 2); camera.lookAt(6, 0.5, 0); camera.updateMatrixWorld(); camera.updateProjectionMatrix();
const renderer = { domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 720 }) } };
onFrame = () => { scene.onBeforeRender(renderer, scene, camera, null); };
globalThis.__live = { scenes: () => [scene], reach: () => ({ engine: 'three', reachable: true, at: '__live.scenes()[0]' }), pinned: null, pinnedKind: '' };
const SCRIPT = readFileSync(A.script, 'utf8');
(0, eval)(SCRIPT);
const S = globalThis.__scene;
const X = R.three;

/* ---------------------------------------------------------------- keys */
X.keys_equal = sameKeys(S.stableKeys(scene), ops.stableKeys(scene));
X.keys = [...S.stableKeys(scene)].map(([, k]) => k);
X.keys_n = X.keys.length;
if (!X.keys_equal) X.keys_diff = { mine: X.keys, theirs: [...ops.stableKeys(scene)].map(([, k]) => k) };
{
  const tr = new T.Group();
  const add = (p, n) => { const o = new T.Object3D(); o.name = n; p.add(o); return o; };
  const a1 = add(tr, 'a'); add(a1, 'a'); add(a1, '');
  const b = add(tr, 'b'); add(b, 'a'); add(b, ''); const pz = add(b, 'a'); pz.userData.pieceKey = 'base~t0-12';
  add(tr, ''); add(tr, 'a.001'); add(tr, 'x'); add(tr, 'x'); add(add(tr, ''), '');
  R.keys_tricky = { equal: sameKeys(S.stableKeys(tr), ops.stableKeys(tr)), mine: [...S.stableKeys(tr)].map(([, k]) => k) };
}

/* ---------------------------------------------------------------- rows against three */
const objOf = new Map([...ops.stableKeys(scene)].map(([o, k]) => [k, o]));
const keyOf = new Map([...ops.stableKeys(scene)].map(([o, k]) => [o, k]));
const all = await S.objects({ limit: 500 });
X.rows_n = all.rows.length; X.total = all.total;
X.camera_pos = all.camera && all.camera.pos;
const frustum = new T.Frustum().setFromProjectionMatrix(new T.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
X.box_bad = []; X.view_bad = []; X.screen_bad = []; X.in_view_n = 0; X.screen_n = 0; X.in_view_body_n = 0;
for (const row of all.rows) {
  if (row.in_view && row.min) X.in_view_body_n++;
  const o = objOf.get(row.key);
  const b = new T.Box3().setFromObject(o);
  if (row.min === null) { if (!b.isEmpty()) X.box_bad.push({ key: row.key, why: 'no box, three has one' }); }
  else if (!near(row.min, b.min.toArray()) || !near(row.max, b.max.toArray())) X.box_bad.push({ key: row.key, mine: [row.min, row.max], three: [b.min.toArray(), b.max.toArray()] });
  const wp = new T.Vector3().setFromMatrixPosition(o.matrixWorld);
  const want = b.isEmpty() ? frustum.containsPoint(wp) : frustum.intersectsBox(b);
  if (row.in_view !== want) X.view_bad.push({ key: row.key, mine: row.in_view, three: want, box: [row.min, row.max] });
  if (row.in_view) X.in_view_n++;
  const c = b.isEmpty() ? wp : b.getCenter(new T.Vector3());
  const v = c.clone().applyMatrix4(camera.matrixWorldInverse);
  const p = c.clone().project(camera);
  const s = [(p.x + 1) / 2, (1 - p.y) / 2];
  if (v.z < -camera.near && s[0] >= 0 && s[0] <= 1 && s[1] >= 0 && s[1] <= 1) {
    X.screen_n++;
    if (!near(row.screen, s, 2e-3)) X.screen_bad.push({ key: row.key, mine: row.screen, three: s });
  }
}
/* pick, against three's Raycaster (double-sided, as ours is) */
scene.traverse((o) => { if (o.material) o.material.side = T.DoubleSide; });
const rc = new T.Raycaster();
const castThree = (x, y) => {
  rc.setFromCamera(new T.Vector2(x * 2 - 1, 1 - y * 2), camera);
  const hits = rc.intersectObjects(scene.children, true).filter((h) => h.object.isMesh);
  return hits.length ? { key: keyOf.get(hits[0].object), dist: hits[0].distance } : { key: null, dist: null };
};
X.pick_bad = []; X.pick_n = 0;
const points = [];
for (let i = 1; i < 6; i++) for (let j = 1; j < 6; j++) points.push([i / 6, j / 6]);
all.rows.filter((r) => r.in_view && r.type === 'mesh' && r.screen).forEach((r) => points.push(r.screen));
for (const [x, y] of points) {
  const got = await S.objects({ pick: [x, y], limit: 1 });
  const want = castThree(x, y);
  X.pick_n++;
  if ((got.pick.hit || null) !== want.key || (want.key && Math.abs(got.pick.dist - want.dist) > 2e-3)) {
    X.pick_bad.push({ at: [x, y], mine: [got.pick.hit, got.pick.dist], three: [want.key, want.dist] });
  }
}
const mid = await S.objects({ pick: [0.5, 0.5], limit: 5 });
X.pick_mid = { hit: mid.pick.hit, want: castThree(0.5, 0.5).key, chain: mid.pick.chain };
X.chain_rows = { chain: mid.pick.chain, rows: mid.rows.map((r) => r.key) };
X.chain_rows_ok = mid.pick.chain.length > 0 && mid.pick.chain.every((k, i) => mid.rows[i] && mid.rows[i].key === k)
  && keyOf.get(objOf.get(mid.pick.chain[0])) === mid.pick.chain[0];
/* filters */
const qr = await S.objects({ q: 'crystal', limit: 500 });
X.q_info = { total: qr.total, rows: qr.rows.map((r) => r.key) };
X.q_ok = qr.total === 15 && qr.rows.length === 15 && qr.rows.every((r) => r.key.includes('crystal'));
const nr = await S.objects({ near: 'pillar-4', radius: 1.3, limit: 500 });
X.near_info = nr.rows.map((r) => r.key);
X.near_ok = JSON.stringify(nr.rows.map((r) => r.key).sort()) === JSON.stringify(['pillar-4', 'pillar-cap.003', 'pillar-plinth.003', 'pillar-shaft.003']);
const vr = await S.objects({ in_view: true, limit: 500 });
X.inview_filter_ok = vr.rows.length === X.in_view_body_n && vr.rows.every((r) => r.in_view === true && r.min !== null)
  && X.in_view_body_n < X.in_view_n;
X.inview_info = { got: vr.rows.length, bodies_in_view: X.in_view_body_n, all_in_view: X.in_view_n };
const lr = await S.objects({ limit: 3 });
X.limit_info = { rows: lr.rows.length, total: lr.total };
X.limit_ok = lr.rows.length === 3 && lr.total === all.total;

/* ---------------------------------------------------------------- edits */
const E = X.edit = {};
const one = async (spec) => (await S.edit([spec])).results[0];
let r = await one({ target: 'pillar-2', move: [1, 0, 0] });
E.move = { after_world: r.after.world, save_pos: r.save.pos, ground: r.ground };
r = await one({ target: 'pillar-cap.001', pos: [4, 2, -5], world: true });
E.world = { after_world: r.after.world, save_pos: r.save.pos, want_local: [-2, 2, 0] };
r = await one({ target: 'crystal-1', rotate: [0, 90, 0] });
E.rotate = { after_rot: r.after.rot };
r = await one({ target: 'lantern-1', rot: [10, 20, 30] });
E.rot = { after_rot: r.after.rot, save_rot: r.save.rot };
r = await one({ target: 'crystal-2', scale: 2 });
const sb = [r.before.max[0] - r.before.min[0], r.before.max[1] - r.before.min[1], r.before.max[2] - r.before.min[2]];
const sa = [r.after.max[0] - r.after.min[0], r.after.max[1] - r.after.min[1], r.after.max[2] - r.after.min[2]];
E.scale = { after_scale: r.after.scale, size_ratio: sa.map((v, i) => v / sb[i]) };
r = await one({ target: 'crystal-3', move: [0, 2, 0], drop: true });
E.drop = { min_y: r.after.min[1], ground: r.ground, notes: r.notes };
r = await one({ target: 'crystal-4', pos: [-5, 5, 5], world: true, drop: true });
E.drop_cap = { min_y: r.after.min[1], ground: r.ground, notes: r.notes };
r = await one({ target: 'pillar-1', move: [0, -0.5, 0] });
E.sink = { ground: r.ground };
r = await one({ target: 'lantern-2', move: [0, 1.25, 0] });
E.float = { ground: r.ground };
r = await one({ target: 'crystal-5', pos: [5, 0, 5], world: true });
E.overlap = { overlaps: r.overlaps };
r = await one({ target: 'crystal-1', pos: [299.9, 0, 0], world: true });
E.through_sky = { overlaps: r.overlaps };
await one({ target: 'crystal-1', pos: [0, 0, 0], world: true });
r = await one({ target: 'lantern-2', visible: false });
E.hide = { visible: r.after.visible, save: r.save };
const l2 = objOf.get('lantern-2');
const undo = r.undo;
S.restore([{ key: 'lantern-2', local: undo }]);
E.restore_exact = l2.visible === true && l2.position.toArray().every((v, i) => v === undo.pos[i])
  && [l2.rotation.x, l2.rotation.y, l2.rotation.z].every((v, i) => v === undo.rot[i]) && l2.scale.toArray().every((v, i) => v === undo.scale[i]);
if (!E.restore_exact) E.restore_diff = { pos: l2.position.toArray(), undo };
E.dup = await one({ target: 'pillar-plinth', move: [0, 0, 0] });
E.piece = await one({ target: 'floor~t0-12', move: [0, 0.1, 0] });
r = await one({ target: '__live.scenes()[0].children[5]', move: [0, 0, 0] });
E.path = { key: r.key, how: r.how };
E.unknown = await one({ target: 'crystal', move: [0, 0, 0] });
const seq = await S.edit([{ target: 'player', pos: [0, 0, 0], world: true }, { target: 'player', move: [0, 0, 3] }]);
E.seq = { world: seq.results[1].after.world };

/* ---------------------------------------------------------------- which root */
{
  const hud = new T.Scene();
  const hm = new T.Mesh(new T.BoxGeometry(1, 1, 1), new T.MeshBasicMaterial()); hm.name = 'hud-thing'; hud.add(hm);
  globalThis.__live.scenes = () => [hud, scene];
  const a = await globalThis.__scene.objects({ limit: 5, q: 'hud-thing' });
  globalThis.__live.editRoot = () => hud;
  const b = await globalThis.__scene.objects({ limit: 5, q: 'hud-thing' });
  delete globalThis.__live.editRoot;
  globalThis.__live.scenes = () => [scene];
  X.root_rule = { heuristic_found: a.total, editroot_found: b.total };
}

/* ---------------------------------------------------------------- versions and the hook */
{
  const v2 = SCRIPT.replace(/var VERSION = [^;]+;/, 'var VERSION = 999;');
  (0, eval)(v2);
  const S2 = globalThis.__scene;
  (0, eval)(v2);
  X.version_ok = S2 !== S && S2.version === 999 && globalThis.__scene === S2;
  X.version_info = { v: S2.version, same: globalThis.__scene === S2 };
  let heard = 0; const real = S2.seen;
  S2.seen = function () { heard++; return real.apply(this, arguments); };
  const sentinel = new T.PerspectiveCamera();
  scene.onBeforeRender(renderer, scene, sentinel, null);
  X.hook_once = heard === 1 && scene.__studioSeen && scene.__studioSeen.cam === sentinel;
  X.hook_info = { heard };
  S2.seen = real;
}

/* ---------------------------------------------------------------- place (three)
   The real runtime (frontend/dist/studio-runtime.js) makes every object; the page script stands
   it where it was asked; three's own Box3 and Raycaster say whether it landed there. */
const byId = (root, id, pcMark) => { const out = []; const w = (o) => { if (pcMark ? o.__studioPlaced === id : (o.userData && o.userData.studioPlaced === id)) out.push(o); (o.children || []).forEach(w); }; w(root); return out; };
if (A.runtime) {
  const PL = R.place = {};
  (0, eval)(SCRIPT);
  const SP = globalThis.__scene;
  const scene2 = courtyard();
  /* A sign whose text node's name is used once: a copy of it renumbers that node's key. */
  const signG = new T.Group(); signG.name = 'sign-1'; signG.position.set(-6, 0, 2);
  const signText = new T.Mesh(new T.BoxGeometry(1, 0.5, 0.05), new T.MeshStandardMaterial()); signText.name = 'sign-text'; signText.position.y = 1.5; signG.add(signText);
  scene2.add(signG); scene2.updateMatrixWorld(true);
  globalThis.__testThree = { crystal };
  globalThis.__live = { scenes: () => [scene2], reach: () => ({ engine: 'three', reachable: true, at: '__live.scenes()[0]' }), pinned: null, pinnedKind: '' };
  onFrame = () => { scene2.onBeforeRender(renderer, scene2, camera, null); };
  const rt = { runtime: pathToFileURL(A.runtime).href, base: pathToFileURL(A.tmp).href + '/', studio: '', project: '', retryMs: 3000 };
  const box3 = (o) => new T.Box3().setFromObject(o);
  const withId = (id) => byId(scene2, id, false);
  const item = (id, ref, extra) => Object.assign({ id, name: '', ref, pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] }, extra || {});
  const code = (exp, args) => ({ kind: 'code', file: 'builders.mjs', export: exp, args: args || [] });

  let r = await SP.place({ item: item('c6', code('buildCrystal', [{ color: 0xffd166 }]), { name: 'crystal' }), near: 'pillar-1', drop: true, rt });
  const c6 = withId('c6')[0];
  PL.near = { ok: r.ok, err: r.error, name: r.name, key: r.key, how: r.how, where: r.where, ground: r.ground, overlaps: r.overlaps,
              after: r.after, item_pos: r.item && r.item.pos };
  if (c6) {
    const b = box3(c6), pb = box3(scene2.getObjectByName('pillar-1'));
    PL.near.three = { min: b.min.toArray(), max: b.max.toArray(), meets_pillar: b.intersectsBox(pb), parent_is_scene: c6.parent === scene2,
                      pos: c6.position.toArray(), made_by_page_three: c6.children[0] instanceof T.Mesh };
  }
  PL.keys_equal = sameKeys(SP.stableKeys(scene2), ops.stableKeys(scene2));
  const rowC6 = (await SP.objects({ q: 'crystal-6', limit: 5 })).rows.find((x) => x.key === 'crystal-6');
  PL.row_placed = rowC6 ? rowC6.placed : null;
  const ed = (await SP.edit([{ target: 'crystal-6', move: [0, 0, 0.5] }])).results[0];
  PL.edit_placed = { placed: ed.placed, item_pos: ed.placed_item && ed.placed_item.pos, after: ed.after && ed.after.pos };

  r = await SP.place({ item: item('b1', { kind: 'primitive', shape: 'box', color: '#ff8800' }, { rot: [0, Math.PI / 4, 0] }), at: [2, 0, -2], rt });
  const b1 = withId('b1')[0];
  PL.at = { ok: r.ok, err: r.error, name: r.name, after_rot: r.after && r.after.rot, item_pos: r.item && r.item.pos, item_rot: r.item && r.item.rot,
            three: b1 ? { min: box3(b1).min.toArray(), max: box3(b1).max.toArray(), is_page_mesh: b1 instanceof T.Mesh } : null };

  r = await SP.place({ item: item('b2', { kind: 'primitive', shape: 'box', color: '#00ff88' }, { scale: [0.4, 0.4, 0.4] }), at: [5, 6, -5], drop: true, rt });
  PL.drop = { ok: r.ok, err: r.error, min_y: r.after && r.after.min[1], ground: r.ground, notes: r.notes };

  /* A thin post right on the middle of the view, as a player stands in front of a third-person
     camera: the default spot is the ground behind it, not the top of it. */
  const post = new T.Mesh(new T.BoxGeometry(0.2, 3, 0.2), new T.MeshStandardMaterial()); post.name = 'post'; post.position.set(2.5, 1.5, 1.0);
  scene2.add(post); scene2.updateMatrixWorld(true);
  r = await SP.place({ item: item('b3', { kind: 'primitive', shape: 'box' }, { scale: [0.3, 0.3, 0.3] }), rt });
  const b3 = withId('b3')[0];
  const rc2 = new T.Raycaster();
  rc2.setFromCamera(new T.Vector2(0, 0), camera);
  const firstHit = rc2.intersectObjects(scene2.children.filter((o) => o !== b3), true).filter((h) => h.object.isMesh)[0];
  const mid = rc2.intersectObjects(scene2.children.filter((o) => o !== b3 && o !== post), true).filter((h) => h.object.isMesh)[0];
  PL.middle = { ok: r.ok, err: r.error, where: r.where, ground: r.ground, hit: mid ? mid.point.toArray() : null,
                first_hit: firstHit ? firstHit.object.name : null,
                box: b3 ? { min: box3(b3).min.toArray(), max: box3(b3).max.toArray() } : null };
  scene2.remove(post);

  /* A MODEL WHOSE BOX LIES about its bottom, as rot-rush's skinned brainrots do: its origin stands
     on the floor, its box reaches 250 m down, inside the sky shell. "Beside it" must be put down
     from the floor under its origin — from the bottom of its box it landed on the underside of the
     world (rot-rush: a sphere at y -361). */
  const liar = new T.Group(); liar.name = 'liar-1'; liar.position.set(6.5, 0, 0.5);
  const lm = new T.Mesh(new T.BoxGeometry(2, 260, 2), new T.MeshStandardMaterial()); lm.position.y = -120; liar.add(lm);
  scene2.add(liar); scene2.updateMatrixWorld(true);
  r = await SP.place({ item: item('lz', { kind: 'primitive', shape: 'box' }, { scale: [0.4, 0.4, 0.4] }), near: 'liar-1', rt });
  const lz = withId('lz')[0];
  PL.liar = { ok: r.ok, err: r.error, where: r.where, ground: r.ground, notes: r.notes, min_y: lz ? box3(lz).min.y : null };
  if (lz) await SP.remove(r.key, rt, {});
  scene2.remove(liar); scene2.updateMatrixWorld(true);

  r = await SP.place({ item: item('l3', { kind: 'clone', of: 'lantern-1' }), at: [0, 0, 3], rt });
  const l3 = withId('l3')[0];
  PL.clone = { ok: r.ok, err: r.error, name: r.name, how: r.how, renumbered: r.renumbered || null, ref_of: r.item && r.item.ref.of,
               kids: l3 ? l3.children.length : -1, shares_geometry: l3 ? l3.children[0].geometry === scene2.getObjectByName('lantern-1').children[0].geometry : null };
  r = await SP.place({ item: item('s2', { kind: 'clone', of: 'sign-1' }), at: [-6, 0, 4], rt });
  PL.renumber = { ok: r.ok, err: r.error, name: r.name, renumbered: r.renumbered || null, n: r.renumbered_n };

  r = await SP.place({ item: item('g1', { kind: 'clone', of: 'ghost-9' }), at: [0, 0, -6], rt });
  PL.ghost = { ok: r.ok, err: r.error, made: withId('g1').length };
  r = await SP.place({ item: item('g1', { kind: 'clone', of: 'ghost-9' }), at: [0, 0, -6], wait: true, rt });
  PL.ghost_wait = { ok: r.ok, pending: r.pending, key: r.key, made: withId('g1').length };
  const ghost = new T.Group(); ghost.name = 'ghost-9';
  const gm = new T.Mesh(new T.BoxGeometry(0.5, 0.5, 0.5), new T.MeshStandardMaterial()); gm.position.y = 0.25; ghost.add(gm);
  ghost.position.set(7, 0, 7); scene2.add(ghost);
  await new Promise((res) => setTimeout(res, 1300));
  const g1 = withId('g1')[0];
  PL.ghost_after = g1 ? { pos: g1.position.toArray(), parent_is_scene: g1.parent === scene2 } : null;

  await SP.place({ item: item('dup', { kind: 'primitive', shape: 'sphere' }, { name: 'ball' }), at: [1, 0, 4], rt });
  r = await SP.place({ item: item('dup', { kind: 'primitive', shape: 'sphere' }, { name: 'ball' }), at: [-1, 0, 4], rt });
  PL.dup = { count: withId('dup').length, replaced: r.replaced, prev_pos: r.prev && r.prev.pos, name: r.name };

  r = await SP.place({ item: item('n1', { kind: 'primitive', shape: 'cone' }, { name: 'pillar-1' }), at: [6, 0, 6], name_given: true, rt });
  PL.named = { name: r.name, notes: r.notes };

  r = await SP.place({ item: item('slow', code('buildSlow', [{ ms: 300 }])), at: [3, 0, 5], timeoutMs: 80, rt });
  PL.slow = { ok: r.ok, err: r.error };
  await new Promise((res) => setTimeout(res, 500));
  PL.slow_left = withId('slow').length;
  r = await SP.place({ item: item('nil', code('buildNothing')), at: [3, 0, 5], rt });
  PL.nothing = { ok: r.ok, err: r.error, made: withId('nil').length };
  r = await SP.place({ item: item('t1', { kind: 'primitive', shape: 'box' }), near: 'pillar', rt });
  PL.near_typo = { ok: r.ok, err: r.error, like: r.like, made: withId('t1').length };

  const had = withId('c6').length;
  r = await SP.remove('crystal-6', rt, {});
  PL.remove = { ok: r.ok, id: r.id, removed: r.removed, had, left: withId('c6').length, item_name: r.item && r.item.name };
  r = await SP.remove('pillar-1', rt, {});
  PL.remove_game = { ok: r.ok, err: r.error, still: !!scene2.getObjectByName('pillar-1') };

  const back = { id: 'c6', name: 'crystal-6', ref: code('buildCrystal', [{ color: 0xffd166 }]), pos: [-5, 0, -2.5], rot: [0, 0.5, 0], scale: [1, 1, 1] };
  r = await SP.sync([{ id: 'c6', put: back }, { id: 'b1', put: null }], rt, {});
  const c6b = withId('c6')[0];
  PL.sync = { ok: r.ok, placed: r.placed, removed: r.removed, pos: c6b ? c6b.position.toArray() : null, rot_y: c6b ? c6b.rotation.y : null,
              b1_left: withId('b1').length };
  r = await SP.unplace(['c6', 'nobody'], rt, {});
  PL.unplace = { removed: r.removed, left: withId('c6').length };
  PL.keys_equal_end = sameKeys(SP.stableKeys(scene2), ops.stableKeys(scene2));
}

/* ---------------------------------------------------------------- PlayCanvas */
if (A.pc) {
  const pc = await import(pathToFileURL(A.pc).href);
  const P = R.pc = {};
  const root = new pc.Entity('Root');
  root._enabledInHierarchy = true;
  const makeMesh = (boxes) => {
    const Pp = [], I = [];
    for (const [cx, cy, cz, sx, sy, sz] of boxes) {
      const b = Pp.length / 3;
      for (let i = 0; i < 8; i++) Pp.push(cx + (i & 1 ? sx / 2 : -sx / 2), cy + (i & 2 ? sy / 2 : -sy / 2), cz + (i & 4 ? sz / 2 : -sz / 2));
      for (const [a, b1, c, d] of [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]) I.push(b + a, b + b1, b + c, b + a, b + c, b + d);
    }
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < Pp.length; i += 3) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], Pp[i + k]); hi[k] = Math.max(hi[k], Pp[i + k]); }
    const aabb = new pc.BoundingBox(new pc.Vec3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2),
                                    new pc.Vec3((hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2, (hi[2] - lo[2]) / 2));
    return { _aabbVer: 0, aabb, primitive: [{ type: 4, base: 0, count: I.length }],
             getPositions(out) { for (const v of Pp) out.push(v); return Pp.length / 3; },
             getIndices(out) { for (const v of I) out.push(v); return I.length; } };
  };
  const attach = (e, mesh) => {
    const mi = { mesh, node: e, visible: true, _box: new pc.BoundingBox() };
    Object.defineProperty(mi, 'aabb', { get() { this._box.setFromTransformedAabb(mesh.aabb, e.getWorldTransform()); return this._box; } });
    e.render = { enabled: true, meshInstances: [mi], asset: null };
  };
  const ground = new pc.Entity('ground'); attach(ground, makeMesh([[0, -0.05, 0, 40, 0.1, 40]])); root.addChild(ground);
  const crate = new pc.Entity('crate-1'); attach(crate, makeMesh([[0, 0.5, 0, 1, 1, 1]])); root.addChild(crate);
  const crate2 = new pc.Entity('crate-2'); crate2.setLocalPosition(0, 0, -6); attach(crate2, makeMesh([[0, 0.5, 0, 1, 1, 1]])); root.addChild(crate2);
  const base = new pc.Entity('base-props'); attach(base, makeMesh([[-5, 1, -6, 2, 2, 2], [5, 1, -6, 2, 2, 2]])); root.addChild(base);
  const turned = new pc.Entity('turned'); turned.setLocalPosition(10, 0, 0); turned.setLocalEulerAngles(0, 90, 0); root.addChild(turned);
  const child = new pc.Entity('child'); attach(child, makeMesh([[0, 0.25, 0, 0.5, 0.5, 0.5]])); turned.addChild(child);
  const cam = new pc.Entity('camera'); root.addChild(cam); cam.setPosition(0, 4, 10); cam.lookAt(0, 0.5, 0);
  cam.camera = { enabled: true, entity: cam, priority: 0, layers: [0], projection: 0, nearClip: 0.1, farClip: 200,
                 projectionMatrix: new pc.Mat4().setPerspective(55, 1280 / 720, 0.1, 200) };
  const app = { root, scene: {}, graphicsDevice: { canvas: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 720 }) } },
                start() {}, systems: { camera: { cameras: [cam.camera] } }, assets: { get: () => null, find: () => null } };
  globalThis.__live = { pinned: app, pinnedKind: 'playcanvas', reach: () => ({ engine: 'playcanvas', reachable: true, at: '__live.pinned' }), scenes: () => [] };
  P.keys_equal = sameKeys(S.stableKeys(root), ops.stableKeys(root));
  if (!P.keys_equal) P.keys_diff = { mine: [...S.stableKeys(root)].map(([, k]) => k), theirs: [...ops.stableKeys(root)].map(([, k]) => k) };
  const tcam = new T.PerspectiveCamera(55, 1280 / 720, 0.1, 200);
  tcam.matrixAutoUpdate = false; tcam.matrixWorld.fromArray(Array.from(cam.getWorldTransform().data)); tcam.matrixWorldInverse.copy(tcam.matrixWorld).invert();
  const tfr = new T.Frustum().setFromProjectionMatrix(new T.Matrix4().multiplyMatrices(tcam.projectionMatrix, tcam.matrixWorldInverse));
  const rows = (await S.objects({ limit: 100 })).rows;
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
  P.crate_min = byKey['crate-1'].min; P.crate_max = byKey['crate-1'].max;
  P.view_bad = [];
  for (const row of rows) {
    if (!row.min) continue;
    const b = new T.Box3(new T.Vector3(...row.min), new T.Vector3(...row.max));
    const want = tfr.intersectsBox(b);
    if (row.in_view !== want) P.view_bad.push({ key: row.key, mine: row.in_view, three: want });
    const c = b.getCenter(new T.Vector3());
    const v = c.clone().applyMatrix4(tcam.matrixWorldInverse), p = v.clone().applyMatrix4(tcam.projectionMatrix);
    const s = [(p.x + 1) / 2, (1 - p.y) / 2];
    if (v.z < -0.1 && s[0] >= 0 && s[0] <= 1 && s[1] >= 0 && s[1] <= 1 && !near(row.screen, s, 2e-3)) P.view_bad.push({ key: row.key, screen: row.screen, three: s });
  }
  const pk = await S.objects({ pick: byKey['crate-1'].screen, limit: 2 });
  P.pick = pk.pick; P.pick_hit = pk.pick.hit;
  const pone = async (spec) => (await S.edit([spec])).results[0];
  let q = await pone({ target: 'crate-1', move: [3, 0, 0] });
  const w = crate.getPosition(); P.move_world = [w.x, w.y, w.z];
  const q0 = crate2.getLocalRotation().clone(), p0 = crate2.getLocalPosition().clone();
  q = await pone({ target: 'crate-2', rot: [10, 20, 30] });
  P.rot_back = q.after.rot; P.rot_saved = q.save.rot;
  const lq = crate2.getLocalRotation();
  P.rot_three = deg(new T.Euler().setFromQuaternion(new T.Quaternion(lq.x, lq.y, lq.z, lq.w), 'XYZ').toArray().slice(0, 3));
  S.restore([{ key: 'crate-2', local: q.undo }]);
  const lq2 = crate2.getLocalRotation(), lp2 = crate2.getLocalPosition();
  P.restore_exact = lq2.x === q0.x && lq2.y === q0.y && lq2.z === q0.z && lq2.w === q0.w && lp2.x === p0.x && lp2.y === p0.y && lp2.z === p0.z;
  if (!P.restore_exact) P.restore_diff = { q: [lq2.x, lq2.y, lq2.z, lq2.w], was: [q0.x, q0.y, q0.z, q0.w] };
  q = await pone({ target: 'child', pos: [12, 1, 0], world: true });
  const cw = child.getPosition(), cl = child.getLocalPosition();
  P.child_world = [cw.x, cw.y, cw.z]; P.child_local = [cl.x, cl.y, cl.z];
  q = await pone({ target: 'crate-1', move: [0, 2, 0], drop: true });
  P.drop_min_y = q.after.min[1]; P.drop_support = q.ground.support;
  q = await pone({ target: 'crate-2', move: [0, 0, 0] });
  P.between_overlaps = q.overlaps;
  q = await pone({ target: 'crate-2', pos: [5, 0.5, -6], world: true });
  P.inside_overlaps = q.overlaps;
  q = await pone({ target: 'crate-1', visible: false });
  const off = crate._enabled === false && q.after.visible === false;
  S.restore([{ key: 'crate-1', local: q.undo }]);
  P.hide_ok = off && crate._enabled === true;
  P.hide_info = { off, now: crate._enabled };

  /* ---------------------------------------------------------------- place (PlayCanvas)
     A fresh app, so the numbers are not the edits' leftovers. */
  if (A.runtime) {
    const PP = R.place_pc = {};
    const SQ = globalThis.__scene;
    const root2 = new pc.Entity('Root');
    root2._enabledInHierarchy = true;
    const ground2 = new pc.Entity('ground'); attach(ground2, makeMesh([[0, -0.05, 0, 40, 0.1, 40]])); root2.addChild(ground2);
    const crateA = new pc.Entity('crate-1'); attach(crateA, makeMesh([[0, 0.5, 0, 1, 1, 1]])); root2.addChild(crateA);
    const cam2 = new pc.Entity('camera'); root2.addChild(cam2); cam2.setPosition(0, 4, 10); cam2.lookAt(0, 0.5, 0);
    cam2.camera = { enabled: true, entity: cam2, priority: 0, layers: [0], projection: 0, nearClip: 0.1, farClip: 200,
                    projectionMatrix: new pc.Mat4().setPerspective(55, 1280 / 720, 0.1, 200) };
    const app2 = { root: root2, scene: {}, graphicsDevice: { canvas: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 720 }) } },
                   start() {}, systems: { camera: { cameras: [cam2.camera] } }, assets: { get: () => null, find: () => null } };
    globalThis.__live = { pinned: app2, pinnedKind: 'playcanvas', reach: () => ({ engine: 'playcanvas', reachable: true, at: '__live.pinned' }), scenes: () => [] };
    globalThis.__testPc = { crate: (name, size) => { const e = new pc.Entity(name); attach(e, makeMesh([[0, size / 2, 0, size, size, size]])); return e; } };
    const rtp = { runtime: pathToFileURL(A.runtime).href, base: pathToFileURL(A.tmp).href + '/', studio: '', project: '', retryMs: 3000 };
    const crateRef = (size) => ({ kind: 'code', file: 'builders.mjs', export: 'buildCrate', args: [{ size }] });
    const aabbOf = (e) => { const b = e.render.meshInstances[0].aabb; const mn = b.getMin(), mx = b.getMax(); return [[mn.x, mn.y, mn.z], [mx.x, mx.y, mx.z]]; };
    let q = await SQ.place({ item: { id: 'k1', name: 'crate', ref: crateRef(1), pos: [0, 0, 0], rot: [0, Math.PI / 2, 0], scale: [1, 1, 1] }, at: [3, 0, 3], rt: rtp });
    const k1 = byId(root2, 'k1', true)[0];
    const lq = k1 ? k1.getLocalRotation() : null;
    PP.at = { ok: q.ok, err: q.error, name: q.name, after: q.after, item_rot: q.item && q.item.rot, box: k1 ? aabbOf(k1) : null,
              parent_is_root: k1 ? k1.parent === root2 : null,
              rot_three: lq ? deg(new T.Euler().setFromQuaternion(new T.Quaternion(lq.x, lq.y, lq.z, lq.w), 'XYZ').toArray().slice(0, 3)) : null };
    q = await SQ.place({ item: { id: 'k2', name: 'crate', ref: crateRef(0.6), pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] }, near: 'crate-1', rt: rtp });
    const k2 = byId(root2, 'k2', true)[0];
    PP.near = { ok: q.ok, err: q.error, name: q.name, where: q.where, ground: q.ground, overlaps: q.overlaps, box: k2 ? aabbOf(k2) : null };
    PP.keys_equal = sameKeys(SQ.stableKeys(root2), ops.stableKeys(root2));
    q = await SQ.remove('k1', rtp, {});
    PP.remove = { ok: q.ok, removed: q.removed, left: byId(root2, 'k1', true).length };
  }
}
/* Out at once: a runtime that is still retrying something (a clone it gave up on after the test
   moved on) holds a timer, and node would wait for it. */
process.stdout.write(JSON.stringify(R) + '\n', () => process.exit(0));
"""

BUILDERS = r"""
export function buildCrystal(opts) { return globalThis.__testThree.crystal((opts && opts.color) || 0x6ae3ff); }
export async function buildSlow(opts) {
  await new Promise((res) => setTimeout(res, (opts && opts.ms) || 300));
  return globalThis.__testThree.crystal(0xffffff);
}
export function buildNothing() { return 42; }
export function buildCrate(opts) { return globalThis.__testPc.crate('crate', (opts && opts.size) || 1); }
"""

STUDIO = Path(__file__).resolve().parent.parent
FRONT = STUDIO / "frontend"
TMP = DATA_DIR / "tmp" / ("scene_api_test-%d" % int(time.time()))
TMP.mkdir(parents=True, exist_ok=True)
PROJ = TMP / "game"
PROJ.mkdir()

try:
    # ============================================================ the file
    print("The saved edits file: merged, atomic, one backup, undone exactly")
    doc0 = {"version": 1, "asset": "courtyard", "params": {"size": 16},
            "parts": {"pillar-1": {"color": "#ff0000", "rot": [0, 0.5, 0]},
                      "floor~t0-12": {"pos": [0, 0.1, 0], "piece": {"c": [1, 2, 3], "n": 4}}},
            "placed": [{"id": "p1", "name": "rock", "ref": {"kind": "primitive", "shape": "box"},
                        "pos": [1, 0, 1], "rot": [0, 0, 0], "scale": [1, 1, 1]}],
            "world": {"background": "#101010"}, "mods": [{"op": "weld", "target": "", "args": {}}],
            "verts": [{"mesh": "floor", "at": [0, 0, 0], "to": [0, 1, 0]}], "bones": [], "pose": {},
            "clips": [], "custom": {"kept": True}}
    p = S.edits_path(str(PROJ))
    p.write_text(json.dumps(doc0), encoding="utf-8")
    d, err = S.read_doc(str(PROJ))
    check("an existing file is read", err == "" and d["asset"] == "courtyard", err)
    prev1 = S.merge_part(d, "pillar-1", {"pos": [1, 2, 3]})
    prev2 = S.merge_part(d, "pillar-2", {"pos": [4, 5, 6], "rot": [0, 1.5, 0], "hidden": True})
    check("merge keeps the fields it was not given", d["parts"]["pillar-1"] == {"color": "#ff0000", "rot": [0, 0.5, 0], "pos": [1.0, 2.0, 3.0]},
          d["parts"]["pillar-1"])
    check("merge returns the entry as it was", prev1 == {"color": "#ff0000", "rot": [0, 0.5, 0]}, prev1)
    check("…and None for a key that had no entry", prev2 is None, prev2)
    check("hidden: true is written", d["parts"]["pillar-2"].get("hidden") is True)
    S.merge_part(d, "pillar-2", {"hidden": False})
    # WRITTEN, not removed: removing the key only undid a saved hide, so an object the game's own code
    # hides came back hidden after a reload while the answer said "written: hidden false".
    check("visible again writes hidden: false", d["parts"]["pillar-2"].get("hidden") is False, d["parts"]["pillar-2"])
    info = S.write_doc(str(PROJ), d)
    bak = PROJ / "studio.edits.json.bak"
    check("the first write keeps a .bak of what was there", bak.is_file() and json.loads(bak.read_text("utf-8")) == doc0)
    check("…and says where", info.get("bak") == str(bak), info)
    back = json.loads(p.read_text("utf-8"))
    for k in ("params", "placed", "world", "mods", "verts", "custom", "asset"):
        if back.get(k) != doc0.get(k):
            check("every section it did not touch is byte-for-byte kept (%s)" % k, False, back.get(k))
            break
    else:
        check("every section it did not touch is kept as it was", True)
    check("a piece entry it did not touch keeps its check", back["parts"]["floor~t0-12"] == doc0["parts"]["floor~t0-12"])
    check("the write is stamped", isinstance(back.get("updated"), int) and back["updated"] > 1.7e12)
    check("no temp file is left beside it", not [f for f in os.listdir(PROJ) if ".tmp-" in f], os.listdir(PROJ))
    bak_bytes = bak.read_bytes()
    S.merge_part(back, "pillar-3", {"scale": [2, 2, 2]})
    info2 = S.write_doc(str(PROJ), back)
    check("the second write does not replace the .bak", bak.read_bytes() == bak_bytes and "bak" not in info2, info2)

    restored = S.restore_disk(back, [{"section": "parts", "key": "pillar-1", "prev": prev1},
                                     {"section": "parts", "key": "pillar-2", "prev": None}])
    check("undo puts an old entry back exactly", back["parts"]["pillar-1"] == prev1, back["parts"]["pillar-1"])
    check("undo removes an entry that did not exist before", "pillar-2" not in back["parts"])
    check("…newest first", restored == ["pillar-2", "pillar-1"], restored)
    # The same key edited twice in one call: undo must land on the state before the FIRST edit.
    d2 = {"parts": {"a": {"pos": [0, 0, 0]}}}
    r1 = S.merge_part(d2, "a", {"pos": [1, 0, 0]})
    r2 = S.merge_part(d2, "a", {"pos": [2, 0, 0]})
    S.restore_disk(d2, [{"key": "a", "prev": r1}, {"key": "a", "prev": r2}])
    check("two edits of one key in one call undo to the first state", d2["parts"]["a"] == {"pos": [0, 0, 0]}, d2)
    d3 = {"placed": [{"id": "x", "name": "old"}]}
    S.restore_disk(d3, [{"section": "placed", "id": "x", "prev": None}, {"section": "placed", "id": "y", "prev": {"id": "y", "name": "back"}}])
    check("the placed section undoes by id (for place, build 3)", d3["placed"] == [{"id": "y", "name": "back"}], d3)

    bad = TMP / "broken"
    bad.mkdir()
    (bad / "studio.edits.json").write_text("{ not json", encoding="utf-8")
    d, err = S.read_doc(str(bad))
    check("a broken file is refused, never written over", d is None and "not valid JSON" in err, err)
    (bad / "studio.edits.json").write_text('{"parts": [1, 2]}', encoding="utf-8")
    d, err = S.read_doc(str(bad))
    check("parts that is not an object is refused", d is None and "parts" in err, err)
    d, err = S.read_doc(str(TMP / "nothing-here"))
    check("no file is (None, no error)", d is None and err == "")

    print("\nThe summary an agent reads")
    sm = S.summarise(json.loads(p.read_text("utf-8")))
    check("rotations come out in degrees", near(sm["parts"]["pillar-1"]["rot"], [0, math.degrees(0.5), 0]), sm["parts"]["pillar-1"])
    check("every section is counted", sm["counts"]["placed"] == 1 and sm["counts"]["mods"] == 1 and sm["counts"]["verts"] == 1
          and sm["counts"]["world"] == 1 and sm["counts"]["params"] == 1, sm["counts"])
    check("placed rows carry kind and ref", sm["placed"][0]["kind"] == "primitive" and sm["placed"][0]["ref"].get("shape") == "box", sm["placed"])
    code, raw = S.edits_file(str(PROJ))
    check("the raw route hands back the document itself", code == 200 and raw.get("asset") == "courtyard", code)
    code, raw = S.edits_file(str(TMP / "nothing-here"))
    check("…404 for a folder that does not exist", code == 404, (code, raw))
    empty = TMP / "empty"
    empty.mkdir()
    code, raw = S.edits_file(str(empty))
    check("…and 404 for a folder with no file", code == 404, (code, raw))
    code, raw = S.edits_file(str(bad))
    check("…and 422 for a file it will not serve", code == 422, (code, raw))

    # ============================================================ the request
    print("\nWhat a request may say")
    sp, err, ign = S._specs({"target": "pillar-2", "move": [1, 0, 0], "drop": False, "world": False, "save": True})
    check("one edit in the body", err == "" and sp == [{"target": "pillar-2", "move": [1.0, 0.0, 0.0]}], (sp, err))
    sp, err, ign = S._specs({"target": "x", "visible": False, "drop": False})
    check("visible: false is an edit (drop: false is not)", err == "" and sp == [{"target": "x", "visible": False}], (sp, err))
    sp, err, ign = S._specs({"target": "x", "position": [1, 2, 3], "rotation": [0, 90, 0]})
    check("position and rotation are read as pos and rot", err == "" and sp[0].get("pos") == [1, 2, 3] and sp[0].get("rot") == [0, 90, 0], sp)
    sp, err, ign = S._specs({"target": "x", "scale": 2})
    check("one number scales all three axes", err == "" and sp[0]["scale"] == 2.0, sp)
    for body, word in (({"target": "x"}, "changes nothing"), ({"move": [1, 0, 0]}, "no target"),
                       ({"target": "x", "move": [1, 0]}, "three numbers"), ({"target": "x", "scale": "big"}, "scale"),
                       ({"target": "x", "rot": [0, float("nan"), 0]}, "rot"), ({}, "nothing to do"),
                       ({"edits": "pillar"}, "list")):
        sp, err, ign = S._specs(body)
        if not (sp == [] and word in err):
            check("refused before a browser is touched: %r" % (body,), False, err)
            break
    else:
        check("every malformed body is refused with its reason, before a browser is touched", True)
    sp, err, ign = S._specs({"edits": [{"target": "a", "move": [0, 1, 0], "colour": "#fff"}, {"target": "b", "drop": True}]})
    check("a list of edits, unknown fields named", err == "" and len(sp) == 2 and ign == ["colour"], (sp, ign))

    # ============================================================ place: the request
    print("\nWhat a place request may say")
    gp = TMP / "placegame"
    (gp / "src").mkdir(parents=True)
    (gp / "src" / "assets.js").write_text("export function buildCrystal() {}\n", encoding="utf-8")
    (gp / "models").mkdir()
    (gp / "models" / "rock.glb").write_bytes(b"glTF")
    (gp / "game2" / "assets").mkdir(parents=True)
    (gp / "game2" / "assets" / "tree.glb").write_bytes(b"glTF")
    ref, nm, err = S.asset_ref(str(gp), {"builder": "src/assets.js#buildCrystal", "args": [{"height": 2}]})
    check("builder -> code {file, export, args}, named for what it makes", err == "" and nm == "crystal" and ref == {
        "kind": "code", "file": "src/assets.js", "export": "buildCrystal", "args": [{"height": 2}]}, (ref, nm, err))
    ref, nm, err = S.asset_ref(str(gp), {"builder": "src/assets.js#buildCrystal", "args": {"height": 2}})
    check("…one options object is taken as the one argument", ref and ref["args"] == [{"height": 2}], ref)
    ref, nm, err = S.asset_ref(str(gp), "src/assets.js#buildCrystal")
    check("…and a string file#export is a builder", ref and ref["kind"] == "code" and ref["export"] == "buildCrystal", (ref, err))
    ref, nm, err = S.asset_ref(str(gp), {"model": "models/rock.glb"})
    check("model -> model {url}, named after the file", ref == {"kind": "model", "url": "models/rock.glb"} and nm == "rock", (ref, nm, err))
    ref, nm, err = S.asset_ref(str(gp), "assets/tree.glb")
    check("…a path from a game's own root is found in its folder of the workspace", ref and ref["url"] == "game2/assets/tree.glb", (ref, err))
    ref, nm, err = S.asset_ref(str(gp), {"model": str(gp / "models" / "rock.glb")})
    check("…and an absolute path inside the project is made project-relative", ref and ref["url"] == "models/rock.glb", (ref, err))
    ref, nm, err = S.asset_ref(str(gp), {"clone": "crystal-2"})
    check("clone -> clone {of}, named after its source", ref == {"kind": "clone", "of": "crystal-2"} and nm == "crystal-2", (ref, nm))
    ref, nm, err = S.asset_ref(str(gp), {"clone": "lantern-post.001"})
    check("…a repeated name's .001 is no part of the copy's name", nm == "lantern-post", nm)
    ref, nm, err = S.asset_ref(str(gp), {"primitive": "box", "color": 0xff8800})
    check("primitive -> {shape, color}; a number colour becomes #rrggbb", ref == {"kind": "primitive", "shape": "box", "color": "#ff8800"}, ref)
    ref, nm, err = S.asset_ref(str(gp), "torus")
    check("…and a shape's name alone is a primitive", ref and ref["kind"] == "primitive" and ref["shape"] == "torus", ref)
    for asset, word in (({"builder": "src/nope.js#x"}, "no src/nope.js"), ({"builder": "src/assets.js#1bad"}, "not the name"),
                        ({"builder": "assets#x"}, "no code file"), ({"model": "models/none.glb"}, "no models/none.glb"),
                        ({"primitive": "blob"}, "not one of"), ({"primitive": "box", "color": "red"}, "color"),
                        ({"model": "a.glb", "clone": "x"}, "exactly one"), ({}, "must be one of"), (42, "must be one of"),
                        ({"model": "src/assets.js"}, "not a .glb"), ({"model": "../outside.glb"}, "no ../outside.glb")):
        ref, nm, err = S.asset_ref(str(gp), asset)
        if not (ref is None and word in err):
            check("refused before a browser is touched: %r" % (asset,), False, err)
            break
    else:
        check("every malformed asset is refused with its reason, before a browser is touched", True)
    real_rows = S._asset_rows
    S._asset_rows = lambda project: [
        {"id": "code:src/assets.js#buildCrystal", "type": "code", "name": "buildCrystal", "file": "src/assets.js", "export": "buildCrystal"},
        {"id": "spec:src/data.js#ROTS[labubu]", "type": "spec", "name": "Labubu", "file": "src/data.js", "table": "ROTS",
         "model": "models/rock.glb", "nodes": ["Labubu"]},
        {"id": "spec:src/data.js#ROTS[plain]", "type": "spec", "name": "Plain", "file": "src/data.js", "table": "ROTS", "model": ""},
        {"id": "model:models/rock.glb", "type": "model", "name": "rock", "file": "models/rock.glb", "meshes": 2},
        {"id": "model:models/idle.glb", "type": "model", "name": "idle", "file": "models/idle.glb", "meshes": 0, "anims": 1},
        {"id": "image:ui/logo.png", "type": "image", "name": "logo", "file": "ui/logo.png"},
        {"id": "audio:sfx/pop.ogg", "type": "audio", "name": "pop", "file": "sfx/pop.ogg"}]
    try:
        ref, nm, err = S.asset_ref(str(gp), {"id": "code:src/assets.js#buildCrystal", "args": [1]})
        check("an asset id of a builder -> code, with its args", nm == "crystal" and ref == {
            "kind": "code", "file": "src/assets.js", "export": "buildCrystal", "args": [1]}, (ref, nm, err))
        ref, nm, err = S.asset_ref(str(gp), "spec:src/data.js#ROTS[labubu]")
        check("…of a table entry with a model -> that model and its nodes (the palette's own rule)",
              ref == {"kind": "model", "url": "models/rock.glb", "nodes": ["Labubu"]} and nm == "Labubu", (ref, nm, err))
        ref, nm, err = S.asset_ref(str(gp), {"id": "model:models/rock.glb"})
        check("…of a model file -> model", ref == {"kind": "model", "url": "models/rock.glb"} and nm == "rock", ref)
        ref, nm, err = S.asset_ref(str(gp), {"id": "image:ui/logo.png"})
        check("…of a picture -> image", ref == {"kind": "image", "url": "ui/logo.png"}, ref)
        for aid, word in (("spec:src/data.js#ROTS[plain]", "table entries"), ("model:models/idle.glb", "an animation and no mesh"),
                          ("audio:sfx/pop.ogg", "a sound"), ("model:models/nothere.glb", "no asset")):
            ref, nm, err = S.asset_ref(str(gp), {"id": aid})
            if not (ref is None and word in err):
                check("an asset id that places nothing is refused: %s" % aid, False, err)
                break
        else:
            check("an id that places nothing (a bare table entry, a clip, a sound, a typo) is refused with the reason", True)
    finally:
        S._asset_rows = real_rows
    check("builder names: the thing, not the verb", S._builder_name("buildCrystal") == "crystal" and S._builder_name("makeTreeLarge") == "tree-large"
          and S._builder_name("default", "src/rock_pile.js") == "rock-pile", (S._builder_name("makeTreeLarge"), S._builder_name("default", "src/rock_pile.js")))
    ps, err = S._place_spec(str(gp), {"asset": "box", "at": [1, 0, 2], "rot": [0, 90, 0], "scale": 2, "save": True})
    check("rot is degrees in and radians in the item; one scale number is all three axes",
          err == "" and near(ps["item"]["rot"], [0, math.pi / 2, 0], 1e-12) and ps["item"]["scale"] == [2.0, 2.0, 2.0] and ps["at"] == [1.0, 0.0, 2.0], (ps, err))
    check("…an id is made in the editor's shape", re.match(r"^p[0-9a-z]{7}$", ps["item"]["id"]) is not None, ps["item"]["id"])
    check("…the page gets the runtime from this Studio, and the name was not asked for",
          ps["rt"]["runtime"].endswith("/studio-runtime.js") and ps["rt"]["project"] == str(gp) and ps["name_given"] is False, ps["rt"])
    ps, err = S._place_spec(str(gp), {"asset": "box", "name": "gate", "id": "gate-1", "near": "pillar-1", "gap": 0.5})
    check("a name, an id, near and gap are handed on as given", ps["item"]["name"] == "gate" and ps["item"]["id"] == "gate-1" and ps["name_given"] is True
          and ps["near"] == "pillar-1" and ps["gap"] == 0.5, ps)
    for body, word in (({"asset": "box", "at": [0, 0, 0], "near": "pillar-1"}, "one of at"), ({"asset": "box", "near": [1, 2, 3]}, "near is a key"),
                       ({"asset": "box", "scale": [1, 0, 1]}, "must not be 0"), ({"asset": "box", "gap": -1}, "gap"),
                       ({"asset": "box", "at": [1, 2]}, "three numbers"), ({"asset": "box", "rot": "90"}, "rot"),
                       ({"asset": "box", "id": "x" * 81}, "80")):
        ps, err = S._place_spec(str(gp), body)
        if not (ps is None and word in err):
            check("a place request refused before a browser is touched: %r" % (body,), False, err)
            break
    else:
        check("every malformed place request is refused with its reason", True)
    old_port = os.environ.get("ASSET_STUDIO_PORT")
    os.environ["ASSET_STUDIO_PORT"] = "8799"
    try:
        check("the page is sent to THIS backend's port (ASSET_STUDIO_PORT), which a second backend does not share",
              S._studio_base().endswith(":8799") and S._rt(str(gp))["runtime"].startswith(S._studio_base()), S._studio_base())
    finally:
        if old_port is None:
            os.environ.pop("ASSET_STUDIO_PORT", None)
        else:
            os.environ["ASSET_STUDIO_PORT"] = old_port
    import asset_studio.config as CFG
    real_dist = CFG.FRONTEND_DIST
    CFG.FRONTEND_DIST = TMP / "no-dist"
    try:
        check("no studio-runtime.js in this Studio: place refuses and says how to make it", "build:runtime" in S._runtime_missing(), S._runtime_missing())
    finally:
        CFG.FRONTEND_DIST = real_dist
    # Both states on purpose: this used to read `== "" or not built`, which passed either way.
    built = TMP / "with-dist"
    built.mkdir(exist_ok=True)
    (built / "studio-runtime.js").write_text("export const version = 1;\n", encoding="utf-8")
    CFG.FRONTEND_DIST = built
    try:
        check("…and with it built, nothing is said", S._runtime_missing() == "", S._runtime_missing())
    finally:
        CFG.FRONTEND_DIST = real_dist

    print("\nA clear that empties the file leaves a .bak, as the first write of a backend's life does")
    # The review's reproduction: the user's file has only parts, a fresh backend is asked to clear
    # everything, and the file was deleted with no copy anywhere but the in-memory undo stack.
    cl = TMP / "clear-bak"
    cl.mkdir(exist_ok=True)
    clf = cl / "studio.edits.json"
    clf.write_text(json.dumps({"version": 1, "parts": {"pillar-1": {"pos": [1, 0, 0]}}}), encoding="utf-8")
    S._BAKED.discard(str(clf.resolve()).lower())          # as after a backend restart
    rc = S._clear(str(cl), "all")
    check("the emptied file is removed", rc.get("ok") and not clf.exists(), rc)
    check("…and its .bak holds what it had", (cl / "studio.edits.json.bak").is_file()
          and "pillar-1" in (cl / "studio.edits.json.bak").read_text(encoding="utf-8"), rc)
    check("…and the answer names the .bak", bool(rc.get("bak")), rc)
    # A copy that FAILED must not mark the file as backed up, or no later write ever makes one.
    fl = TMP / "bak-fail"
    fl.mkdir(exist_ok=True)
    flf = fl / "studio.edits.json"
    flf.write_text('{"version": 1, "parts": {"a": {"hidden": true}}}', encoding="utf-8")
    S._BAKED.discard(str(flf.resolve()).lower())
    (fl / ("studio.edits.json.bak.tmp-%d" % os.getpid())).mkdir()   # the temp copy cannot be written
    try:
        S.write_doc(str(fl), {"version": 1, "parts": {"a": {"hidden": False}}})
        wrote = True
    except Exception:
        wrote = False
    check("a failed backup copy refuses the write rather than losing the old file", not wrote
          and '"hidden": true' in flf.read_text(encoding="utf-8"))
    check("…and leaves the file unmarked, so the next write tries the backup again",
          str(flf.resolve()).lower() not in S._BAKED)

    print("\nA single edit takes `name` for its target")
    specs, err, _ign = S._specs({"target": "", "name": "pillar-2", "move": [1, 0, 0]})
    check("{name, move} with the body model's empty target is an edit of that name",
          not err and specs and specs[0]["target"] == "pillar-2", (specs, err))

    print("\nThe placed section: replaced by id, undone exactly")
    dp = {"placed": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "a", "name": "A-dup"}]}
    prev = S.merge_placed(dp, {"id": "a", "name": "A2"})
    check("merge_placed replaces by id where the first stood, drops duplicates, returns the old entry",
          dp["placed"] == [{"id": "a", "name": "A2"}, {"id": "b", "name": "B"}] and prev == {"id": "a", "name": "A"}, (dp, prev))
    prev = S.merge_placed(dp, {"id": "c", "name": "C"})
    check("…a new id is added at the end, with no old entry", prev is None and dp["placed"][-1] == {"id": "c", "name": "C"}, dp)
    check("placed_by finds by id, then by name", S.placed_by(dp, "b")["name"] == "B" and S.placed_by(dp, "C")["id"] == "c" and S.placed_by(dp, "zz") is None)
    got = S.take_placed(dp, "b")
    check("take_placed takes one out and hands it back", got == {"id": "b", "name": "B"} and [it["id"] for it in dp["placed"]] == ["a", "c"], dp)
    found, prev = S.merge_placed_fields(dp, "a", {"pos": [1, 2, 3]})
    check("merge_placed_fields changes only the transform it is given", found and prev == {"id": "a", "name": "A2"}
          and dp["placed"][0] == {"id": "a", "name": "A2", "pos": [1.0, 2.0, 3.0]}, dp)
    check("…and says when the file has no such entry", S.merge_placed_fields(dp, "zz", {"pos": [0, 0, 0]}) == (False, None))
    shown_it = S._shown_item({"id": "q", "name": "Q", "ref": {"kind": "primitive"}, "pos": [1, 2, 3], "rot": [0, math.pi, 0], "scale": [1, 1, 1]})
    check("a written item is shown in degrees", near(shown_it["rot"], [0, 180, 0]) and shown_it["id"] == "q", shown_it)

    # ============================================================ the guards
    print("\nThe switches")

    class _Off:
        def get(self, k, d=None):
            return False if k == "cc_scene_edit" else d

    real_settings = S.settings
    S.settings = _Off()
    try:
        for name, call in (("objects", lambda: S.objects(str(PROJ))), ("edit", lambda: S.edit(str(PROJ), {"target": "a", "move": [1, 0, 0]})),
                           ("edits", lambda: S.edits(str(PROJ))), ("place", lambda: S.place(str(PROJ), {"asset": "box"}))):
            r = call()
            check("cc_scene_edit off: %s refuses and names Settings" % name, r.get("ok") is False and "Settings" in r.get("error", ""), r)
    finally:
        S.settings = real_settings
    r = S._page_refusal({"ok": False, "engine": "babylon", "reachable": True})
    check("a reachable engine that is not three or PlayCanvas is read-only", r["error"] == "babylon is read-only here — use /api/live/eval", r)
    r = S._page_refusal({"ok": False, "engine": "playcanvas", "reachable": False})
    check("an engine that could not be reached says how to reach it", "window.__game" in r["error"], r)

    browser_ok, why = L.available()
    if not browser_ok:
        skipped("the orchestration half", "no browser on this machine: %s" % why)
    else:
        # ============================================================ orchestration, page faked
        print("\nEdit, save, undo and clear, with the page replaced")
        calls = []
        state = {"pos": [5.0, 0.0, -5.0]}
        page_placed: dict = {}          # what the fake page holds, by id

        def args_of(expr, head):
            return json.loads("[" + expr[len(head):-1] + "]")

        def fake_in_tab(project, expr):
            calls.append(expr)
            if expr.startswith("__scene.place("):
                spec = json.loads(expr[len("__scene.place("):expr.rindex(", {")])
                it = dict(spec["item"])
                it["pos"] = list(spec.get("at") or spec.get("pos") or [0, 0, 0])
                it["name"] = it["name"] or "placed"
                prev_it = page_placed.get(it["id"])
                page_placed[it["id"]] = it
                out = {"ok": True, "engine": "three", "key": it["name"], "name": it["name"], "id": it["id"],
                       "how": "a " + str(it["ref"].get("shape") or it["ref"]["kind"]), "where": "at", "after": {"pos": it["pos"]},
                       "ground": {"support": "floor", "gap": 0, "rests": True}, "overlaps": [], "item": it,
                       "replaced": prev_it is not None}
                if prev_it:
                    out["prev"] = prev_it
                return out
            if expr.startswith("__scene.remove("):
                what = args_of(expr, "__scene.remove(")[0]
                pid = what if what in page_placed else next((k for k, v in page_placed.items() if v["name"] == what), None)
                if not pid:
                    return {"ok": False, "engine": "three", "error": what + " was built by the game's own code: it cannot be removed, only hidden"}
                it = page_placed.pop(pid)
                return {"ok": True, "engine": "three", "id": pid, "key": it["name"], "name": it["name"], "removed": 1, "item": it}
            if expr.startswith("__scene.unplace("):
                ids = args_of(expr, "__scene.unplace(")[0]
                return {"ok": True, "engine": "three", "removed": {i: (1 if page_placed.pop(i, None) else 0) for i in ids}}
            if expr.startswith("__scene.sync("):
                placed_back, removed = [], []
                for ent in args_of(expr, "__scene.sync(")[0]:
                    if ent.get("put"):
                        page_placed[ent["id"]] = dict(ent["put"])
                        placed_back.append(ent["id"])
                    else:
                        page_placed.pop(ent["id"], None)
                        removed.append(ent["id"])
                return {"ok": True, "engine": "three", "placed": placed_back, "removed": removed, "failed": []}
            if expr.startswith("__scene.edit("):
                arg = json.loads(expr[len("__scene.edit("):expr.rindex(", {")])
                sp0 = arg[0]
                hit = next((k for k, v in page_placed.items() if v.get("name") == sp0["target"]), None)
                if hit:
                    it = page_placed[hit]
                    was = list(it["pos"])
                    it["pos"] = [was[i] + sp0["move"][i] for i in range(3)]
                    return {"ok": True, "engine": "three", "results": [{
                        "ok": True, "key": it["name"], "name": it["name"], "how": "key", "before": {"pos": was}, "after": {"pos": it["pos"]},
                        "ground": {}, "overlaps": [], "save": {"pos": it["pos"]}, "changed": ["pos"],
                        "undo": {"pos": was, "rot": [0, 0, 0], "scale": [1, 1, 1], "on": True},
                        "placed": hit, "placed_item": dict(it)}]}
                before = list(state["pos"])
                state["pos"] = [before[0] + sp0["move"][0], before[1] + sp0["move"][1], before[2] + sp0["move"][2]]
                return {"ok": True, "engine": "three", "results": [{
                    "ok": True, "key": sp0["target"], "name": sp0["target"], "how": "key",
                    "before": {"pos": before}, "after": {"pos": state["pos"]}, "ground": {"support": "floor", "gap": 0},
                    "overlaps": [], "save": {"pos": state["pos"]}, "undo": {"pos": before, "rot": [0, 0, 0], "scale": [1, 1, 1], "on": True},
                    "changed": ["pos"]}]}
            if expr.startswith("__scene.restore("):
                items = json.loads(expr[len("__scene.restore("):expr.rindex(", {")])
                for it in items:
                    hit = next((k for k, v in page_placed.items() if v.get("name") == it["key"]), None)
                    if hit:
                        page_placed[hit]["pos"] = list(it["local"]["pos"])
                    else:
                        state["pos"] = list(it["local"]["pos"])
                return {"ok": True, "engine": "three", "restored": [it["key"] for it in items], "missing": []}
            return {"ok": False}

        real_in_tab = S._in_tab
        S._in_tab = fake_in_tab
        entry = L._entry(str(PROJ))
        entry["url"] = "http://127.0.0.1:1/fake"
        try:
            p.write_text(json.dumps(doc0), encoding="utf-8")
            r = S.edit(str(PROJ), {"target": "pillar-2", "move": [-1, 0, 0], "save": True, "code": False})
            check("a saved edit answers flat, with units and the file", r.get("ok") and r.get("key") == "pillar-2" and r.get("units") == "degrees"
                  and r.get("saved") is True and r.get("file") == str(p), r)
            on_disk = json.loads(p.read_text("utf-8"))
            check("…and the file holds the new local position", on_disk["parts"]["pillar-2"] == {"pos": [4.0, 0.0, -5.0]}, on_disk["parts"])
            check("…beside everything that was there", on_disk["parts"]["pillar-1"] == doc0["parts"]["pillar-1"] and on_disk["placed"] == doc0["placed"])
            check("the page's undo record never reaches the agent", "undo" not in r or isinstance(r.get("undo"), str), r.get("undo"))
            r = S.edit(str(PROJ), {"target": "pillar-2", "move": [0, 0, 2]})
            check("an unsaved try leaves the file alone", json.loads(p.read_text("utf-8"))["parts"]["pillar-2"] == {"pos": [4.0, 0.0, -5.0]} and r.get("saved") is False, r)
            r = S.edit(str(PROJ), {"undo": True})
            check("undo of the try restores the live object only", r.get("ok") and r.get("restored") == ["pillar-2"] and "file_restored" not in r
                  and state["pos"] == [4.0, 0.0, -5.0], (r, state))
            r = S.edit(str(PROJ), {"undo": True})
            on_disk = json.loads(p.read_text("utf-8"))
            check("undo of the saved edit restores it live AND on disk", r.get("file_restored") == ["pillar-2"] and "pillar-2" not in on_disk["parts"]
                  and state["pos"] == [5.0, 0.0, -5.0], (r, on_disk["parts"], state))
            r = S.edit(str(PROJ), {"undo": True})
            check("an empty stack says so", r.get("ok") is False and "nothing to undo" in r.get("error", ""), r)
            r = S.edit(str(PROJ), {"clear": ["pillar-1", "ghost"]})
            on_disk = json.loads(p.read_text("utf-8"))
            check("clear takes named entries out of the file and names what was not there", r.get("cleared") == ["pillar-1"] and r.get("not_saved") == ["ghost"]
                  and "pillar-1" not in on_disk["parts"] and "floor~t0-12" in on_disk["parts"], r)
            check("…says the running game keeps them until a reload, and what else the file keeps", "reload" in r.get("live", "") and r.get("kept", {}).get("placed") == 1, r)
            r = S.edit(str(PROJ), {"undo": True})
            check("undo of a clear puts the entry back", json.loads(p.read_text("utf-8"))["parts"].get("pillar-1") == doc0["parts"]["pillar-1"], r)
            r = S.edit(str(PROJ), {"clear": "all"})
            after_all = json.loads(p.read_text("utf-8"))
            check("clear all empties parts and placed — what an agent writes — and nothing else", after_all["parts"] == {} and after_all.get("placed") == []
                  and all(after_all.get(k) == doc0[k] for k in ("world", "mods", "verts", "custom", "params")), (r, after_all))
            S.edit(str(PROJ), {"undo": True})
            check("…and its undo puts both back", json.loads(p.read_text("utf-8"))["placed"] == doc0["placed"]
                  and "floor~t0-12" in json.loads(p.read_text("utf-8"))["parts"])
            r = S.edit(str(PROJ), {"target": "pillar-2", "move": [1, 0, 0], "bogus": 1})
            check("an unknown field is named, not silently dropped", r.get("ignored") == ["bogus"], r.get("ignored"))
            n0 = len(calls)
            r = S.edit(str(bad), {"target": "pillar-2", "move": [1, 0, 0], "save": True})
            check("a save into a broken file is refused before the game is touched", r.get("ok") is False and len(calls) == n0, r)
            fresh = TMP / "fresh"
            fresh.mkdir()
            fe = L._entry(str(fresh))
            fe["url"] = "http://127.0.0.1:1/fake"
            fp = fresh / "studio.edits.json"
            r = S.edit(str(fresh), {"target": "pillar-2", "move": [1, 0, 0], "save": True, "code": False})
            check("a first save makes the file, in the shape the Edit tab reads", fp.is_file() and r.get("saved")
                  and set(json.loads(fp.read_text("utf-8"))) >= {"version", "parts", "params", "mods", "bones", "clips", "pose"}, r)
            check("…and keeps no .bak of a file that did not exist", not (fresh / "studio.edits.json.bak").exists() and "bak" not in r)
            r = S.edit(str(fresh), {"undo": True})
            check("undo of the call that made the file removes the file", not fp.exists() and r.get("file_removed"), r)
            S.edit(str(fresh), {"target": "pillar-2", "move": [1, 0, 0], "save": True, "code": False})
            r = S.edit(str(fresh), {"clear": "all"})
            check("a clear that leaves nothing to apply removes the file", not fp.exists() and r.get("file_removed") is True, r)
            r = S.edit(str(fresh), {"undo": True})
            check("…and its undo writes the entry back", fp.is_file() and "pillar-2" in json.loads(fp.read_text("utf-8")).get("parts", {}), r)
            fe["url"] = ""
            r = S.edits(str(PROJ))
            check("edits: the summary, with the undo depth", r.get("ok") and r.get("exists") and r["counts"]["parts"] == 2 and isinstance(r.get("undo"), int), r)
            r = S.edits(str(empty))
            check("edits on a game with no file: exists false", r.get("ok") and r.get("exists") is False and r["counts"]["parts"] == 0, r)

            # ------------------------------------------------ place, with the page replaced
            print("\nPlace, save, undo, remove and clear, with the page replaced")
            ge = L._entry(str(gp))
            ge["url"] = "http://127.0.0.1:1/fake"
            pf = gp / "studio.edits.json"
            r = S.place(str(gp), {"asset": {"primitive": "box", "color": "#ff8800"}, "at": [1, 0, 2], "rot": [0, 90, 0], "save": True, "code": False})
            doc = json.loads(pf.read_text("utf-8"))
            check("a saved place writes a PlacedItem: radians on disk, degrees in the answer",
                  r.get("ok") and r.get("saved") is True and doc["placed"][0]["rot"] == [0.0, math.pi / 2, 0.0]
                  and near(r["written"]["rot"], [0, 90, 0]) and r.get("units") == "degrees" and doc["placed"][0]["id"] == r["id"], (r, doc.get("placed")))
            check("…in the shape the Edit tab reads (every section there), and no .bak of a file that did not exist",
                  set(doc) >= {"version", "parts", "params", "mods", "bones", "clips", "pose", "placed"} and not (gp / "studio.edits.json.bak").exists(), list(doc))
            pid = r["id"]
            r = S.place(str(gp), {"undo": True})
            check("undo of a saved place takes it out live and removes the file it made",
                  r.get("ok") and r.get("unplaced") == [pid] and not pf.exists() and pid not in page_placed and r.get("file_removed"), (r, page_placed))
            S.place(str(gp), {"asset": "box", "at": [0, 0, 0], "id": "gate", "save": True, "code": False})
            r2 = S.place(str(gp), {"asset": "sphere", "at": [5, 0, 0], "id": "gate", "save": True, "code": False})
            doc = json.loads(pf.read_text("utf-8"))
            check("the same id again replaces its entry: one entry, the new recipe", len(doc["placed"]) == 1 and doc["placed"][0]["ref"]["shape"] == "sphere"
                  and r2.get("replaced"), (doc["placed"], r2))
            r = S.place(str(gp), {"undo": True})
            doc = json.loads(pf.read_text("utf-8"))
            check("…and its undo puts the old one back, live and on disk", doc["placed"][0]["ref"]["shape"] == "box" and r.get("placed_back") == ["gate"]
                  and page_placed["gate"]["ref"]["shape"] == "box", (r, doc["placed"]))
            n_calls = len(calls)
            r = S.place(str(gp), {"asset": "cone", "at": [2, 0, 2], "code": False})
            check("a try writes nothing and says so", r.get("saved") is False and "try" in r and len(json.loads(pf.read_text("utf-8"))["placed"]) == 1
                  and len(calls) == n_calls + 1, r)
            tid = r["id"]
            r = S.place(str(gp), {"remove": tid})
            check("remove takes a try out of the game; the file never had it", r.get("ok") and r.get("removed") == 1 and tid not in page_placed and "note" not in r, r)
            r = S.place(str(gp), {"undo": True})
            check("…and undo puts it back", tid in page_placed and r.get("placed_back") == [tid], r)
            r = S.place(str(gp), {"remove": "gate"})
            check("removing a saved one without save says the file still has it (it comes back at the reload)",
                  r.get("ok") and "still in studio.edits.json" in r.get("note", ""), r)
            S.place(str(gp), {"undo": True})
            r = S.place(str(gp), {"remove": "gate", "save": True})
            check("remove with save takes it out of the file too, and the file goes when nothing is left", r.get("saved") and not pf.exists(), (r, pf.exists()))
            r = S.place(str(gp), {"undo": True})
            check("…and undo writes it back and places it again", pf.exists() and json.loads(pf.read_text("utf-8"))["placed"][0]["id"] == "gate"
                  and "gate" in page_placed, r)
            depth = S.undo_depth(str(gp))
            r = S.place(str(gp), {"remove": "pillar-1"})
            check("remove refuses what the game built, and pushes nothing to undo", r.get("ok") is False and "only hidden" in r.get("error", "")
                  and S.undo_depth(str(gp)) == depth, r)
            r = S.edit(str(gp), {"clear": ["box"]})
            check("clear takes a placement by name out of the file AND out of the running game", r.get("cleared") == ["gate"] and r.get("removed_live") == ["gate"]
                  and "gate" not in page_placed and "taken out of the running game" in r.get("live", "") and r.get("file_removed"), r)
            r = S.edit(str(gp), {"undo": True})
            check("…and its undo puts it back in both", "gate" in page_placed and pf.exists() and r.get("placed_back") == ["gate"], r)
            r = S.edit(str(gp), {"target": "box", "move": [0, 0, 1], "save": True, "code": False})
            doc = json.loads(pf.read_text("utf-8"))
            check("an edit saved on a placement moves its entry in placed, and writes no part", r.get("saved") and r.get("saved_to") == "placed[gate]"
                  and doc["placed"][0]["pos"] == [0.0, 0.0, 1.0] and "box" not in doc.get("parts", {}) and "placed_item" not in r, (r, doc))
            S.edit(str(gp), {"undo": True})
            check("…and its undo puts the entry's old position back", json.loads(pf.read_text("utf-8"))["placed"][0]["pos"] == [0.0, 0.0, 0.0])
            r = S.place(str(gp), {"asset": "torus", "at": [3, 0, 3], "code": False})
            tid2 = r["id"]
            S.edit(str(gp), {"target": r["name"], "move": [1, 0, 0], "save": True, "code": False})
            doc = json.loads(pf.read_text("utf-8"))
            check("an edit saved on an UNSAVED placement saves the whole placement", any(it["id"] == tid2 and it["pos"] == [4.0, 0.0, 3.0]
                                                                                       and it["ref"]["shape"] == "torus" for it in doc["placed"]), doc["placed"])
            r = S.edit(str(gp), {"clear": "all"})
            check("clear all takes every placement out of the file and the game", not pf.exists() and set(r.get("removed_live") or []) == {"gate", tid2}
                  and "gate" not in page_placed and tid2 not in page_placed, r)
            ge["url"] = ""
            r = S.place(str(gp), {"asset": "box"})
            check("with no game open, place says to open one", r.get("ok") is False and "/api/live/open" in r.get("error", ""), r)
        finally:
            S._in_tab = real_in_tab
            entry["url"] = ""

    # ============================================================ the page, in node
    print("\nThe page script, against the engines' own answers")
    three = FRONT / "node_modules" / "three" / "build" / "three.module.js"
    pc_path = os.environ.get("STUDIO_PLAYCANVAS") or str(Path.home() / "Desktop" / "brainrot 3d game research crazygames" /
                                                          "rot-rush" / "node_modules" / "playcanvas" / "build" / "playcanvas.mjs")
    node = shutil.which("node")
    if not node or not three.is_file() or not (FRONT / "node_modules" / "esbuild").is_dir():
        skipped("the page script", "needs node, three and esbuild under frontend/node_modules")
    else:
        ops_out = TMP / "ops.mjs"
        b = subprocess.run([node, "-e", "require('esbuild').buildSync({entryPoints:['src/components/engine/edit/ops.ts'],bundle:true,"
                                        "format:'esm',platform:'node',outfile:%s,logLevel:'warning'})" % json.dumps(str(ops_out))],
                           cwd=str(FRONT), capture_output=True, text=True, timeout=120)
        check("ops.ts bundles for the key comparison", ops_out.is_file(), b.stderr[-600:])
        script = TMP / "scene_page.js"
        script.write_text(S.page_script(), encoding="utf-8")
        harness = TMP / "harness.mjs"
        harness.write_text(HARNESS, encoding="utf-8")
        # The builders a `code` placement imports: the game's own module, resolved against `base`
        # by the runtime exactly as it resolves src/assets.js against a page.
        (TMP / "builders.mjs").write_text(BUILDERS, encoding="utf-8")
        runtime_js = FRONT / "dist" / "studio-runtime.js"
        args = {"three": str(three), "ops": str(ops_out), "script": str(script),
                "pc": pc_path if Path(pc_path).is_file() else "",
                "runtime": str(runtime_js) if runtime_js.is_file() else "", "tmp": str(TMP)}
        run = subprocess.run([node, str(harness), json.dumps(args)], capture_output=True, text=True, timeout=180,
                             encoding="utf-8", errors="replace")
        try:
            R = json.loads(run.stdout.strip().splitlines()[-1])
        except Exception:
            R = None
        check("the harness ran", R is not None, (run.stdout[-1500:], run.stderr[-1500:]))
        if R:
            T = R["three"]
            print("  three.js (the proof game's layout): %d rows, %d in view, %d screen points and %d picks compared"
                  % (T["rows_n"], T["in_view_n"], T["screen_n"], T["pick_n"]))
            check("keys equal ops.stableKeys on the scene, object for object", T["keys_equal"] and T["keys_n"] > 20, T.get("keys_diff"))
            check("keys equal ops.stableKeys with repeats, unnamed nodes and a piece key", R["keys_tricky"]["equal"], R["keys_tricky"])
            check("repeats get .000 in traversal order", "pillar-plinth.000" in T["keys"] and "pillar-plinth.003" in T["keys"], T["keys"][:12])
            check("every row's world box equals three's Box3.setFromObject", T["box_bad"] == [], T["box_bad"][:4])
            check("in_view agrees with three's Frustum.intersectsBox on every row", T["view_bad"] == [], T["view_bad"][:4])
            check("…and the camera sees some of it and not all of it", 0 < T["in_view_n"] < T["rows_n"], (T["in_view_n"], T["rows_n"]))
            check("screen agrees with Vector3.project for every row whose middle is on screen", T["screen_bad"] == [] and T["screen_n"] > 5,
                  (T["screen_bad"][:4], T["screen_n"]))
            check("the camera is the one the game renders with, found through the render hook", near(T["camera_pos"], [-1, 3, 2]), T["camera_pos"])
            check("pick agrees with three's Raycaster at every tested point", T["pick_bad"] == [] and T["pick_n"] >= 8, (T["pick_bad"][:4], T["pick_n"]))
            check("pick at the middle of the view", T["pick_mid"]["hit"] == T["pick_mid"]["want"], T["pick_mid"])
            check("the pick's chain is deepest first and leads the rows", T["chain_rows_ok"], T.get("chain_rows"))
            check("q keeps only matching rows, total counts them", T["q_ok"], T.get("q_info"))
            check("near + radius keeps what is within that distance of a key", T["near_ok"], T.get("near_info"))
            check("in_view=1 keeps the things with a body that are in view (not lights or empties)", T["inview_filter_ok"], T.get("inview_info"))
            check("near measures a dome to its surface, not its box: the sky is not within 1.3 m", "sky" not in (T.get("near_info") or []), T.get("near_info"))
            check("limit bounds the rows, total does not", T["limit_ok"], T.get("limit_info"))
            E = T["edit"]
            check("move is a WORLD delta", near(E["move"]["after_world"], [6, 0, -5]) and near(E["move"]["save_pos"], [6, 0, -5]), E["move"])
            check("…and the answer says it stands on the floor", E["move"]["ground"].get("support") == "floor" and abs(E["move"]["ground"].get("gap")) < 1e-3
                  and E["move"]["ground"].get("rests") is True, E["move"]["ground"])
            check("pos with world:true lands a nested part on that world point", near(E["world"]["after_world"], [4, 2, -5]), E["world"])
            check("…and writes the LOCAL value (relative to its pillar)", near(E["world"]["save_pos"], E["world"]["want_local"]), E["world"])
            check("rotate adds degrees", near(E["rotate"]["after_rot"], [0, 90, 0]), E["rotate"])
            check("rot sets degrees; the file gets radians", near(E["rot"]["after_rot"], [10, 20, 30]) and near(E["rot"]["save_rot"], [math.radians(10), math.radians(20), math.radians(30)], 1e-5), E["rot"])
            check("scale: one number, all three axes, and the box follows", near(E["scale"]["after_scale"], [2, 2, 2]) and near(E["scale"]["size_ratio"], [2, 2, 2], 1e-3), E["scale"])
            check("drop rests it on the floor from 2 m up", abs(E["drop"]["min_y"]) < 1e-3 and E["drop"]["ground"].get("support") == "floor", E["drop"])
            check("drop onto a pillar's cap rests it on the cap", abs(E["drop_cap"]["min_y"] - 3) < 1e-3 and "pillar-cap" in str(E["drop_cap"]["ground"].get("support"))
                  and E["drop_cap"]["ground"].get("support_of") == "pillar-3", E["drop_cap"])
            check("a sunk object reports a negative gap", abs(E["sink"]["ground"].get("gap") + 0.5) < 1e-3, E["sink"]["ground"])
            check("a floating one a positive gap", abs(E["float"]["ground"].get("gap") - 1.25) < 1e-3, E["float"]["ground"])
            ov = E["overlap"]["overlaps"]
            check("a crystal moved into a pillar overlaps it, named with the pillar", any(o.get("of") == "pillar-4" and o["share"] > 0.05 for o in ov), ov)
            check("…but standing on the floor is contact, not overlap", not any(o["key"] == "floor" for o in ov), ov)
            check("…and the sky dome whose box holds it all is not an overlap", not any(o["key"] == "sky" for o in ov), ov)
            ts = E["through_sky"]["overlaps"]
            check("a crystal pushed through the dome's wall does overlap the sky", any(o["key"] == "sky" and o["share"] > 0.05 for o in ts), ts)
            check("hide: visible false, and the file would get hidden: true", E["hide"]["visible"] is False and E["hide"]["save"] == {"hidden": True}, E["hide"])
            check("restore puts every value back exactly", E["restore_exact"], E.get("restore_diff"))
            check("a name four objects share is refused with their keys", "4 objects" in E["dup"]["error"] and len(E["dup"]["keys"]) == 4, E["dup"])
            check("a piece key is refused with the reason", "piece" in E["piece"]["error"], E["piece"])
            check("a JS path resolves to its object", E["path"]["key"] == "pillar-3" and E["path"]["how"] == "path", E["path"])
            check("an unknown target suggests keys", "nothing is keyed" in E["unknown"]["error"] and "crystal-1" in E["unknown"]["like"], E["unknown"])
            check("two edits in one call act in order", near(E["seq"]["world"], [0, 0, 3]), E["seq"])
            check("with two scenes, the one drawn to the canvas is the root, not the first announced",
                  T["root_rule"]["heuristic_found"] == 0, T["root_rule"])
            check("…unless the shim names the root the saved edits are applied to (__live.editRoot)",
                  T["root_rule"]["editroot_found"] == 1, T["root_rule"])
            check("the render hook is installed once, and hears the camera", T["hook_once"], T.get("hook_info"))
            check("a script of another version is replaced; the same version is kept", T["version_ok"], T.get("version_info"))

            P = R.get("pc")
            if not P:
                skipped("PlayCanvas", "no playcanvas.mjs at %s (set STUDIO_PLAYCANVAS)" % pc_path)
            else:
                print("  PlayCanvas (real entities, a fake app)")
                check("keys equal ops.stableKeys on a PlayCanvas root", P["keys_equal"], P.get("keys_diff"))
                check("world boxes come from the mesh instances", near(P["crate_min"], [-0.5, 0, -0.5]) and near(P["crate_max"], [0.5, 1, 0.5]), P)
                check("in_view and screen agree with three's projection of the same camera", P["view_bad"] == [], P["view_bad"][:4])
                check("pick hits the crate under its own screen point", P["pick_hit"] == "crate-1", P.get("pick"))
                check("move: the entity's world position follows", near(P["move_world"], [3, 0, 0]), P["move_world"])
                check("rot in degrees round-trips through the quaternion", near(P["rot_back"], [10, 20, 30]), P["rot_back"])
                check("…and it is three's XYZ convention (checked with three's Euler)", near(P["rot_three"], [10, 20, 30]), P["rot_three"])
                check("the file gets three-convention radians", near(P["rot_saved"], [math.radians(10), math.radians(20), math.radians(30)], 1e-5), P["rot_saved"])
                check("world pos under a turned parent lands on the world point", near(P["child_world"], [12, 1, 0]), P["child_world"])
                check("…and its local value is in the parent's frame", near(P["child_local"], [0, 1, 2]), P["child_local"])
                check("restore puts the exact quaternion back", P["restore_exact"], P.get("restore_diff"))
                check("drop rests the crate on the ground", abs(P["drop_min_y"]) < 1e-3 and P["drop_support"] == "ground", P)
                check("a merged mesh's whole box does not count as an overlap", P["between_overlaps"] == [], P["between_overlaps"])
                check("…one of its welded parts does, with the triangle range", any(o["key"] == "base-props" and o.get("tris") == [12, 24] for o in P["inside_overlaps"]), P["inside_overlaps"])
                check("hide sets enabled, restore brings it back", P["hide_ok"], P.get("hide_info"))

            PL = R.get("place")
            if not PL:
                skipped("place in node", "no frontend/dist/studio-runtime.js (npm run build:runtime)")
            else:
                print("  place: the real runtime makes it; three's Box3 and Raycaster say where it landed")
                n = PL["near"]
                t3 = n.get("three") or {}
                check("a builder beside pillar-1: made by the runtime, named in the game's numbering (crystal-6)",
                      n["ok"] and n["name"] == "crystal-6" and n["key"] == "crystal-6" and "buildCrystal" in (n.get("how") or ""), n)
                check("…its box does not meet pillar-1's (three's Box3.intersectsBox)", t3.get("meets_pillar") is False, t3)
                check("…to its right as the camera sees it", "to its right" in (n.get("where") or ""), n.get("where"))
                check("…standing on the floor with nothing in the way", n["ground"].get("support") == "floor" and n["ground"].get("rests") is True
                      and n["overlaps"] == [] and abs((t3.get("min") or [0, 9, 0])[1]) < 1e-6, (n["ground"], n["overlaps"], t3.get("min")))
                check("…the answer's box is three's box", near(n["after"]["min"], t3.get("min") or [], 2e-3) and near(n["after"]["max"], t3.get("max") or [], 2e-3), (n["after"], t3))
                check("…parented to the scene, made with the page's own three, and the item's pos is its local position",
                      t3.get("parent_is_scene") and t3.get("made_by_page_three") and near(n["item_pos"], t3.get("pos") or [], 1e-6), (n["item_pos"], t3))
                check("keys stay ops.stableKeys with placements in the scene", PL["keys_equal"] and PL["keys_equal_end"], PL.get("keys_equal"))
                check("a placement's row says so (placed: its id)", PL["row_placed"] == "c6", PL["row_placed"])
                ep = PL["edit_placed"]
                check("an edit of a placement names it and hands back its whole recipe at the new place",
                      ep["placed"] == "c6" and ep["item_pos"] and near(ep["item_pos"], ep["after"], 1e-3), ep)
                a = PL["at"]
                ab = a.get("three") or {}
                check("at: the middle of its bottom lands on the point (three's box: min y 0, centre 2,-2)",
                      a["ok"] and ab and abs(ab["min"][1]) < 1e-6 and near([(ab["min"][0] + ab["max"][0]) / 2, (ab["min"][2] + ab["max"][2]) / 2], [2, -2], 1e-6), a)
                check("…turned 45 degrees: the answer says degrees, the item keeps radians", near(a["after_rot"], [0, 45, 0], 1e-3) and near(a["item_rot"], [0, math.pi / 4, 0], 1e-9), a)
                check("…a primitive of the page's own three (instanceof the scene's Mesh class)", ab.get("is_page_mesh") is True, ab)
                d = PL["drop"]
                check("drop from 6 m lands on pillar-2's cap (min y 3)", d["ok"] and abs(d["min_y"] - 3) < 1e-6 and d["ground"].get("support_of") == "pillar-2", d)
                lz = PL.get("liar") or {}
                check("beside a model whose box reaches 250 m under the floor: put down on the floor under its origin, not under the world",
                      lz.get("ok") and lz.get("min_y") is not None and abs(lz["min_y"]) < 1e-3 and (lz.get("ground") or {}).get("rests") is True, lz)
                check("…and the answer says why", any("below what it stands on" in str(x) for x in (lz.get("notes") or [])), lz.get("notes"))
                m = PL["middle"]
                ok_mid = False
                if m.get("box") and m.get("hit"):
                    bx = m["box"]
                    ok_mid = (abs((bx["min"][0] + bx["max"][0]) / 2 - m["hit"][0]) < 1e-3 and abs((bx["min"][2] + bx["max"][2]) / 2 - m["hit"][2]) < 1e-3
                              and abs(bx["min"][1] - m["hit"][1]) < 1e-3)
                check("neither at nor near: its bottom where the middle of the view meets the ground (three's Raycaster), resting",
                      m["ok"] and "middle of the view" in (m.get("where") or "") and ok_mid and m["ground"].get("rests"), m)
                check("…past a thing narrower than twice its own width (a post, a player) that stood in the way",
                      m.get("first_hit") == "post" and "past post" in (m.get("where") or ""), m)
                c = PL["clone"]
                check("a clone of lantern-1: a copy named lantern-3 that shares the source's geometry, and moves no key",
                      c["ok"] and c["name"] == "lantern-3" and "a copy of lantern-1" in (c.get("how") or "") and c["shares_geometry"] and c["renumbered"] is None and c["kids"] == 3, c)
                rn = PL["renumber"]
                # The rule changed on purpose: a placed subtree is keyed apart (stableKeys), so a copy
                # whose inner name the game used once no longer renumbers the game's key. It did —
                # 347 keys for one brainrot copy on rot-rush — and an edit made then was saved under
                # a key that meant another object after a reload.
                check("a copy whose inner name the game used once renumbers none of the game's keys",
                      rn["ok"] and not rn["renumbered"], rn)
                check("a clone of nothing is refused with the reason, and nothing is made",
                      PL["ghost"]["ok"] is False and "nothing is keyed" in (PL["ghost"].get("err") or "") and PL["ghost"]["made"] == 0, PL["ghost"])
                check("…with wait: pending, then placed when the source appears, at its origin",
                      PL["ghost_wait"]["ok"] and PL["ghost_wait"]["pending"] and PL["ghost_wait"]["made"] == 0 and PL["ghost_after"]
                      and near(PL["ghost_after"]["pos"], [0, 0, -6]) and PL["ghost_after"]["parent_is_scene"], (PL["ghost_wait"], PL["ghost_after"]))
                check("the same id twice leaves one object, and the answer carries the one it replaced",
                      PL["dup"]["count"] == 1 and PL["dup"]["replaced"] and near(PL["dup"]["prev_pos"], [1, 0.5, 4], 1e-6) and PL["dup"]["name"] == "ball", PL["dup"])
                check("an asked-for name that is taken is changed, and the answer says why",
                      PL["named"]["name"] == "pillar-5" and any("taken" in x for x in PL["named"].get("notes") or []), PL["named"])
                check("a builder slower than the deadline is refused, and what it makes later is taken out",
                      PL["slow"]["ok"] is False and "not built after" in (PL["slow"].get("err") or "") and PL["slow_left"] == 0, (PL["slow"], PL["slow_left"]))
                check("a builder that returns nothing placeable is refused with the runtime's reason",
                      PL["nothing"]["ok"] is False and "nothing that can be placed" in (PL["nothing"].get("err") or "") and PL["nothing"]["made"] == 0, PL["nothing"])
                check("near a name nothing has is refused before anything is made, with keys that look like it",
                      PL["near_typo"]["ok"] is False and PL["near_typo"]["made"] == 0 and "pillar-1" in (PL["near_typo"].get("like") or []), PL["near_typo"])
                rm = PL["remove"]
                check("remove takes a placement out, found by its name", rm["ok"] and rm["id"] == "c6" and rm["removed"] == 1 and rm["had"] == 1
                      and rm["left"] == 0 and rm["item_name"] == "crystal-6", rm)
                check("…and refuses what the game's code built, pointing at a hide", PL["remove_game"]["ok"] is False
                      and "only hidden" in (PL["remove_game"].get("err") or "") and PL["remove_game"]["still"], PL["remove_game"])
                sy = PL["sync"]
                check("sync (undo's hands) puts an exact item back and takes another out", sy["ok"] and sy["placed"] == ["c6"] and sy["removed"] == ["b1"]
                      and near(sy["pos"], [-5, 0, -2.5], 1e-9) and abs(sy["rot_y"] - 0.5) < 1e-12 and sy["b1_left"] == 0, sy)
                check("unplace counts what it took out, per id", PL["unplace"]["removed"] == {"c6": 1, "nobody": 0} and PL["unplace"]["left"] == 0, PL["unplace"])
                PP = R.get("place_pc")
                if not PP:
                    skipped("place in PlayCanvas", "no playcanvas.mjs")
                else:
                    a = PP["at"]
                    bx = a.get("box") or [[9, 9, 9], [9, 9, 9]]
                    check("PlayCanvas: a builder's entity, its bottom at the point (aabb min y 0, centre 3,3)", a["ok"] and abs(bx[0][1]) < 1e-6
                          and near([(bx[0][0] + bx[1][0]) / 2, (bx[0][2] + bx[1][2]) / 2], [3, 3], 1e-5), a)
                    check("…named crate-2 after the game's crate-1, and parented to app.root", a["name"] == "crate-2" and a["parent_is_root"], a)
                    check("…turned by quatFromEulerXYZ: three's Euler reads 90 degrees, the answer 90, the item pi/2",
                          near(a["rot_three"] or [], [0, 90, 0], 1e-4) and near(a["after"]["rot"], [0, 90, 0], 1e-3) and near(a["item_rot"], [0, math.pi / 2, 0], 1e-9), a)
                    nn = PP["near"]
                    check("…beside crate-1, on the ground, nothing in the way", nn["ok"] and nn["ground"].get("support") == "ground" and nn["ground"].get("rests")
                          and nn["overlaps"] == [] and nn.get("box") and nn["box"][0][0] > 0.5, nn)
                    check("…keys still ops.stableKeys on the PlayCanvas root", PP["keys_equal"], PP)
                    check("…and remove takes it out by id", PP["remove"]["ok"] and PP["remove"]["removed"] == 1 and PP["remove"]["left"] == 0, PP["remove"])
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)

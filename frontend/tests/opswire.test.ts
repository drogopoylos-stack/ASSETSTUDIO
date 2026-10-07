// The goblin A/B's new tools, as an agent meets them: through makeOps(THREE), on real geometry.
//
// The modules have their own tests (material.test.ts, sculpt.test.ts, hands.test.ts). This file
// proves the WIRING: that `forge-ops.js` hands the tools out, that they take and give three
// geometries, and that nothing that worked before changed its default.
//
// Run: npm run test:opswire

import * as THREE from "three";
import * as OPS from "../src/components/engine/edit/ops";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra = "") => {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
};

const ops = OPS.makeOps(THREE);

console.log("The bundle hands the new modules out");
for (const name of ["bakeFunction", "bakeMaterial", "bakeAtlas", "applyMaps", "MATERIALS", "atlasArrays"]) {
  ok("forge-ops exports " + name, name in OPS);
}
for (const name of ["bake", "bakeMaterial", "atlas", "bakeAtlas", "applyMaps", "materials"]) {
  ok("makeOps(THREE)." + name, typeof (ops as any)[name] !== "undefined");
}

console.log("\nunwrap keeps its default, and uv1 can be left out");
const sphere = new THREE.SphereGeometry(0.3, 24, 16);
const withUv1 = ops.unwrap(sphere);
ok("default: uv and uv1, as before", !!withUv1.attributes.uv && !!withUv1.attributes.uv1);
const noUv1 = ops.unwrap(sphere, { uv1: false });
ok("uv1: false: uv only", !!noUv1.attributes.uv && !noUv1.attributes.uv1);

console.log("\nA smart material bakes onto an unwrapped geometry");
const maps = ops.bakeMaterial(withUv1, "rustySteel", 128, { ao: { rays: 4 } });
ok("three maps", !!maps.baseColor && !!maps.orm && !!maps.normal);
ok("...at the asked size", maps.baseColor.width === 128 && maps.normal.height === 128, maps.baseColor.width + "x" + maps.baseColor.height);
let bad = "";
try { ops.bakeMaterial(withUv1, "nosuchmaterial", 16); } catch (e: any) { bad = String(e?.message || e); }
ok("an unknown material name says which exist", /rustySteel/.test(bad) && /skin/.test(bad), bad);
let noUv = "";
try { ops.bakeMaterial(new THREE.BoxGeometry(1, 1, 1).toNonIndexed().deleteAttribute("uv"), "steel", 16); } catch (e: any) { noUv = String(e?.message || e); }
ok("a geometry with no uv is told to unwrap first", /unwrap/.test(noUv), noUv);

console.log("\nSeveral parts share one atlas and one texture set");
const a = new THREE.SphereGeometry(0.2, 16, 12);
const b = new THREE.BoxGeometry(0.3, 0.3, 0.3); b.translate(0.5, 0, 0);
const colours = new Float32Array(b.attributes.position.count * 3).fill(0.5);
b.setAttribute("color", new THREE.Float32BufferAttribute(colours, 3));
const geos = ops.atlas([a, b], { weights: [2, 1] });
ok("one geometry out per part", geos.length === 2);
let inside = true;
for (const g of geos) { const uv = g.attributes.uv.array; for (let i = 0; i < uv.length; i++) if (uv[i] < 0 || uv[i] > 1) inside = false; }
ok("every uv inside 0..1", inside);
ok("vertex colours ride through the island split", !!geos[1].attributes.color && geos[1].attributes.color.count === geos[1].attributes.position.count);
const both = ops.bakeAtlas([{ geo: a, mat: "skin" }, { geo: b, mat: "leather" }], 128, { ao: { rays: 4 } });
ok("bakeAtlas gives the geometries to draw and one texture set", both.geos.length === 2 && both.maps.baseColor.width === 128);

console.log("\napplyMaps sets the sign that makes the forge and the GLB agree");
const mat = ops.applyMaps(new THREE.MeshStandardMaterial(), maps);
ok("a colour map, a normal map and an ORM", !!mat.map && !!mat.normalMap && !!mat.roughnessMap);
ok("normalScale.y is negative for the flipY:false map", mat.normalScale.y < 0, String(mat.normalScale.y));
ok("the colour map is sRGB", mat.map.colorSpace === THREE.SRGBColorSpace, String(mat.map.colorSpace));

console.log("\nA complete hand, placed on a handle");
ok("forge-ops exports handField and gripPlacement", "handField" in OPS && "gripPlacement" in OPS);
const handGeo = ops.hand({ side: "R", pose: "grip", gripRadius: 0.012 }, 40, { skin: "#9f9c53", nail: "#d9cfae" });
ok("a hand geometry with triangles", handGeo.index.count / 3 > 2000, String(handGeo.index.count / 3));
ok("...its own normals", !!handGeo.attributes.normal && handGeo.attributes.normal.count === handGeo.attributes.position.count);
ok("...skin and nail in vertex colours", !!handGeo.attributes.color);
ok("...and the hand frame, not enumerable (kept out of GLB extras)",
  !!handGeo.userData.hand && !Object.keys(handGeo.userData).includes("hand"));
const holes = OPS.checkArrays(handGeo.attributes.position.array as Float32Array, Uint32Array.from(handGeo.index.array));
ok("the hand is closed (no holes after a weld)", holes.holes === 0, JSON.stringify({ holes: holes.holes }));
const handMesh = new THREE.Mesh(handGeo, new THREE.MeshStandardMaterial({ vertexColors: true }));
const ha: [number, number, number] = [0.2, 0.3, 0.1], hb: [number, number, number] = [0.2, 0.5, 0.25];
const placed = ops.grip(handMesh, { a: ha, b: hb, face: [0, 0, 1] });
ok("grip moves the mesh to the placement", handMesh.position.distanceTo(new THREE.Vector3(...placed.position)) < 1e-9);
ok("...and hands back where the forearm must end", Array.isArray(placed.wrist) && placed.wrist.length === 3);
handMesh.updateMatrixWorld(true);
const fr = (handGeo.userData.hand as any).frame;
const gc = new THREE.Vector3(...(fr.gripCentre as [number, number, number])).applyMatrix4(handMesh.matrixWorld);
const P = new THREE.Vector3(...ha), D = new THREE.Vector3(...hb).sub(P).normalize();
const off = gc.clone().sub(P); const dist = off.sub(D.clone().multiplyScalar(off.dot(D))).length();
ok("the grip channel lies on the handle (within 1 mm)", dist < 1e-3, dist.toFixed(6));
let noHand = "";
try { ops.grip(new THREE.Mesh(new THREE.BoxGeometry()), { a: ha, b: hb, face: [0, 0, 1] }); } catch (e: any) { noHand = String(e?.message || e); }
ok("a mesh that is not a hand is told what grip needs", /ops\.hand/.test(noHand), noHand);

console.log("\nSculpt, then a game budget: densify, strokes, decimate");
for (const name of ["densifyArrays", "sculptArrays", "decimateArrays", "sweepArrays", "boundaryLoops", "rimArrays", "reach"]) {
  ok("forge-ops exports " + name, name in OPS);
}
for (const name of ["densify", "sculpt", "decimate", "sweep", "rim", "borders", "reach"]) {
  ok("makeOps(THREE)." + name, typeof (ops as any)[name] === "function");
}
const R = 0.3;
const ball = ops.weld(new THREE.IcosahedronGeometry(R, 3));
const ballTris = ball.index.count / 3;
const dense = ops.densify(ball, 0.01);
const denseTris = dense.index.count / 3;
ok("densify splits the long edges", denseTris > ballTris * 8, ballTris + " -> " + denseTris);
let outside = 0;
const dp = dense.attributes.position.array as Float32Array;
for (let i = 0; i < dp.length; i += 3) if (Math.hypot(dp[i], dp[i + 1], dp[i + 2]) > R + 1e-5) outside++;
ok("...and the surface does not move (nothing leaves the sphere)", outside === 0, String(outside));
ok("...with one normal per vertex", !!dense.attributes.normal && dense.attributes.normal.count === dense.attributes.position.count);
ok("...closed, as the sphere was", ops.check(new THREE.Mesh(dense)).holes === 0);

// A crease across the front: a groove under the path, nothing beyond the radius moves.
const path: [number, number, number][] = [[-0.15, 0.05, 0.26], [0, 0.07, 0.3], [0.15, 0.05, 0.26]];
const carved = ops.sculpt(dense, [{ kind: "crease", path, radius: 0.04, depth: -0.012 }]);
const cp = carved.attributes.position.array as Float32Array;
ok("sculpt keeps the triangles", carved.index.count === dense.index.count);
let deepest = 0, farMoved = 0;
for (let i = 0; i < cp.length; i += 3) {
  const moved = Math.hypot(cp[i] - dp[i], cp[i + 1] - dp[i + 1], cp[i + 2] - dp[i + 2]);
  const inward = Math.hypot(dp[i], dp[i + 1], dp[i + 2]) - Math.hypot(cp[i], cp[i + 1], cp[i + 2]);
  if (inward > deepest) deepest = inward;
  if (dp[i + 2] < 0 && moved > 0) farMoved++;
}
ok("...a crease cuts a groove about the depth asked", deepest > 0.008 && deepest < 0.02, deepest.toFixed(4));
ok("...and the back of the ball does not move at all", farMoved === 0, String(farMoved));
ok("...still closed", ops.check(new THREE.Mesh(carved)).holes === 0);
const unwrapped = ops.unwrap(ball);
const sculptedUv = ops.sculpt(unwrapped, [{ kind: "inflate", at: [0, 0, R], radius: 0.1, depth: 0.01 }]);
ok("sculpt carries uv when the triangles stay the same", !!sculptedUv.attributes.uv && sculptedUv.attributes.uv.count === unwrapped.attributes.uv.count);

const low = ops.decimate(carved, { target: 2000 });
const lowTris = low.index.count / 3;
ok("decimate comes down to the budget", lowTris <= 2000 && lowTris > 1500, String(lowTris));
ok("...closed, no hole made", ops.check(new THREE.Mesh(low)).holes === 0);
const volume = (g: any) => ops.check(new THREE.Mesh(g)).volume;
const vHigh = volume(carved), vLow = volume(low);
ok("...and the volume kept within 2%", Math.abs(vLow - vHigh) / vHigh < 0.02, (100 * (vLow - vHigh) / vHigh).toFixed(2) + "%");
ok("...with the error it made, in metres", typeof low.userData.error === "number" && low.userData.collapsed > 0);
const painted = carved.clone();
painted.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(painted.attributes.position.count * 3).fill(0.4), 3));
const lowPainted = ops.decimate(painted, { ratio: 0.2 });
ok("decimate carries vertex colours", !!lowPainted.attributes.color && lowPainted.attributes.color.count === lowPainted.attributes.position.count);
const lowUv = ops.decimate(ops.unwrap(dense), { target: 3000 });
ok("...and uv, seams kept", !!lowUv.attributes.uv && lowUv.attributes.uv.count === lowUv.attributes.position.count);

// The recipe end to end: the sculpt's detail baked onto the low mesh's own uv.
const lowBaked = ops.unwrap(low);
const recipe = ops.bakeMaterial(lowBaked, "skin", 64, { high: carved, ao: { rays: 2 } });
ok("the sculpt bakes onto the low mesh (bakeMaterial with high)", !!recipe.normal && recipe.normal.width === 64);

console.log("\nSweeps, rims and reach");
const helix: [number, number, number][] = [];
for (let i = 0; i <= 40; i++) { const a = i * 0.3; helix.push([Math.cos(a) * 0.2, i * 0.01, Math.sin(a) * 0.2]); }
const strap = ops.sweep(helix, { radius: 0.02, sides: 8 });
ok("sweep gives a tube with uv and normals", strap.index.count > 0 && !!strap.attributes.uv && !!strap.attributes.normal);
let tubeOff = 0;
const sp = strap.attributes.position.array as Float32Array;
for (let i = 0; i < sp.length; i += 3) {
  let best = Infinity;
  for (const q of helix) best = Math.min(best, Math.hypot(sp[i] - q[0], sp[i + 1] - q[1], sp[i + 2] - q[2]));
  tubeOff = Math.max(tubeOff, best);
}
ok("...every vertex near its path (within radius plus a segment)", tubeOff < 0.02 + 0.07, tubeOff.toFixed(4));
const shell = new THREE.SphereGeometry(0.3, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2);
const loops = ops.borders(shell);
ok("borders finds the one open rim of a dome", loops.length === 1 && loops[0].length >= 32, String(loops.length));
const rolled = ops.rim(shell, { radius: 0.015 });
ok("rim rolls a tube along it", rolled.index.count > 0 && !!rolled.attributes.normal);
let rimOff = 0;
const rp = rolled.attributes.position.array as Float32Array;
for (let i = 0; i < rp.length; i += 3) rimOff = Math.max(rimOff, Math.abs(Math.hypot(rp[i], rp[i + 2]) - 0.3) + Math.abs(rp[i + 1]) - 0.015);
ok("...on the rim, not somewhere else", rimOff < 0.01, rimOff.toFixed(4));
const arm = ops.reach([0, 1.2, 0], [0.35, 0.95, 0.2], [0.28, 0.26], [0, 1.0, -0.4]);
ok("reach puts the end exactly on a target in range", arm.reached && Math.hypot(arm.end[0] - 0.35, arm.end[1] - 0.95, arm.end[2] - 0.2) < 1e-6);

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

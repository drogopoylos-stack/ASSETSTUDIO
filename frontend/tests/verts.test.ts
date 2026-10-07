// A vertex you moved by hand, and the code running again underneath it.
//
// This is the test that decides whether edit mode is worth having. Blender never faces the
// problem: its mesh is stored, so vertex 4127 is vertex 4127 forever. Ours is generated. Every
// slider move rebuilds the buffer from nothing, and an index means nothing across that boundary.
//
// So the key is the vertex's rest position, and these checks are about the three things that can
// go wrong with that: a corner that is really three coincident vertices and must move as one, a
// rebuild that renumbers everything and must still find it, and a rebuild that genuinely MOVED
// the region — which must orphan the edit loudly rather than move some innocent other vertex.
//
// Run: npm run test:verts

import {
  VERT_TOL, vertKey, bindVerts, applyVertEdits, applyVerts, groupVerts, restBuffer,
  stableKeys, topoSig, type VertEdit,
} from "../src/components/engine/edit/ops";
import {
  ELEM_KINDS, buildTopology, elemGroups, groupMoved, groupsInBox, nearestEdge,
  nearestGroup, segDist2, selectionCounts, vertEditsOf, type Screen,
} from "../src/components/engine/edit/vertedit";
import { readFileSync } from "node:fs";
import { join } from "node:path";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
const eq = (name: string, got: unknown, want: unknown) =>
  ok(name, JSON.stringify(got) === JSON.stringify(want), "got " + JSON.stringify(got));
const near = (name: string, got: number, want: number, tol = 1e-6) =>
  ok(name, Math.abs(got - want) <= tol, "got " + got + ", wanted " + want);

// The eight corners of a unit cube.
const CORNERS: [number, number, number][] = [
  [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
  [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
];

/** The six faces, as corner numbers. Every corner is in exactly three of them. */
const FACES: Array<[number, number, number, number]> = [
  [0, 1, 2, 3], [4, 5, 6, 7], [0, 3, 7, 4], [1, 5, 6, 2], [0, 4, 5, 1], [3, 2, 6, 7],
];

/** A cube the way generated geometry really makes one: 24 vertices for 8 corners, because each
 *  face needs its own normals. The duplicates are SPREAD across the faces, one per face meeting
 *  at that corner — emitting them three in a row instead would make every triangle degenerate. */
function cube(scale = 1): Float32Array {
  const out: number[] = [];
  for (const f of FACES) for (const ci of f) {
    const c = CORNERS[ci];
    out.push(c[0] * scale, c[1] * scale, c[2] * scale);
  }
  return new Float32Array(out);
}

/** Two triangles per face, over those 24 vertices. */
function cubeIndex(): number[] {
  const out: number[] = [];
  for (let f = 0; f < 6; f++) out.push(4 * f, 4 * f + 1, 4 * f + 2, 4 * f, 4 * f + 2, 4 * f + 3);
  return out;
}

/** Which group ordinal holds a given corner. Ordinals fall out of buffer order, so a test that
 *  hard-codes one is testing the emission order rather than the behaviour. */
function groupOfCorner(topo: { groups: number[][]; rest: Float32Array }, ci: number): number {
  const c = CORNERS[ci];
  return topo.groups.findIndex((g) => Math.abs(topo.rest[g[0] * 3] - c[0]) < 1e-9
    && Math.abs(topo.rest[g[0] * 3 + 1] - c[1]) < 1e-9
    && Math.abs(topo.rest[g[0] * 3 + 2] - c[2]) < 1e-9);
}

/** The same cube after a code change that renumbered everything — a segment count went up
 *  somewhere else, so the builder emitted the faces in a different order. */
function shuffled(pos: Float32Array, seed = 7): Float32Array {
  const n = pos.length / 3;
  const order = Array.from({ length: n }, (_, i) => i);
  let s = seed;
  for (let i = n - 1; i > 0; i--) {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    const j = s % (i + 1);
    [order[i], order[j]] = [order[j], order[i]];
  }
  const out = new Float32Array(pos.length);
  order.forEach((src, dst) => {
    out[dst * 3] = pos[src * 3];
    out[dst * 3 + 1] = pos[src * 3 + 1];
    out[dst * 3 + 2] = pos[src * 3 + 2];
  });
  return out;
}

const at = (i: number): [number, number, number] => CORNERS[i];

// ---------------------------------------------------------------- the key itself

console.log("The key is a place, not a number");
eq("the origin", vertKey(0, 0, 0), "0,0,0");
ok("a tenth of the tolerance is the same bucket",
   vertKey(0, 0, 0) === vertKey(VERT_TOL / 10, 0, 0));
ok("ten times it is not",
   vertKey(0, 0, 0) !== vertKey(VERT_TOL * 10, 0, 0));
// A mirrored model welds its seam on the axis. If -0 and 0 keyed differently, half the seam
// would be unreachable and a mirror edit would tear down the middle.
eq("negative zero keys as zero", vertKey(-0, -0, -0), vertKey(0, 0, 0));
// Math.round takes a half upward, so 1234.5 buckets to 1235. Worth pinning: the exact
// rounding rule is what makes a key written today match one computed tomorrow.
eq("a real place", vertKey(1.2345, -0.5, 2), "1235,-500,2000");
eq("a half rounds up, consistently", vertKey(0.0015, 0.0025, -0.0015), "2,3,-1");
ok("the tolerance is a knob", vertKey(1.2345, 0, 0, 0.1) === "12,0,0");

// ---------------------------------------------------------------- a corner is three vertices

console.log("\nA corner moves as one, not as a third of itself");
{
  const pos = cube();
  const edit: VertEdit = { mesh: "m", at: at(6), to: [5, 5, 5] };
  const b = bindVerts(pos, [edit]);
  // THE ONE THAT MATTERS. Moving one of three coincident vertices opens a hole in the surface.
  eq("all three vertices at that corner are bound", b.found.size, 3);
  eq("and nothing is orphaned", b.orphans.length, 0);
  const r = applyVertEdits(pos, [edit]);
  eq("three vertices moved", r.moved, 3);
  for (const [i] of b.found) {
    near("vertex " + i + " went to the new x", r.pos[i * 3], 5);
    near("vertex " + i + " went to the new y", r.pos[i * 3 + 1], 5);
  }
  eq("the other seven corners are untouched", r.pos.length - 9,
     r.pos.filter((_, k) => !b.found.has(Math.floor(k / 3))).length);
  // Pure: the caller's buffer is not written through.
  near("the input buffer is unchanged", pos[6 * 3], CORNERS[6][0]);
}

// ---------------------------------------------------------------- the rebuild

console.log("\nThe code runs again, and the edit still lands");
{
  const edit: VertEdit = { mesh: "m", at: at(2), to: [1, 9, -1] };
  const before = applyVertEdits(cube(), [edit]);
  eq("it binds on the first build", before.moved, 3);

  // A change elsewhere in the builder renumbered every vertex. An index-keyed edit would now be
  // pointing at a random corner; a place-keyed one does not care.
  const after = applyVertEdits(shuffled(cube()), [edit]);
  eq("and again after a renumber", after.moved, 3);
  eq("...still nothing orphaned", after.orphans.length, 0);
  let raised = 0;
  for (let i = 0; i < after.pos.length; i += 3) if (after.pos[i + 1] === 9) raised++;
  eq("the same corner is the raised one", raised, 3);
}

// A rebuild that moved the vertex by less than the tolerance is a rounding wobble, not a change
// of intent. It must still bind, including across a bucket boundary.
console.log("\nA hair of drift is not a different vertex");
{
  const edit: VertEdit = { mesh: "m", at: [0.0004, 0, 0], to: [0, 3, 0] };
  // 0.0004 and 0.0006 round to different buckets, and are 0.0002 apart. The neighbour search has
  // to catch this or every edit near a bucket edge is lost on the next rebuild.
  ok("the two really are in different buckets",
     vertKey(0.0004, 0, 0) !== vertKey(0.0006, 0, 0));
  const pos = new Float32Array([0.0006, 0, 0, 5, 5, 5]);
  const b = bindVerts(pos, [edit]);
  eq("it is found across the boundary", b.found.size, 1);
  eq("...as vertex 0", [...b.found.keys()], [0]);
}

// ---------------------------------------------------------------- and when it is really gone

console.log("\nWhen the region genuinely moved, say so");
{
  // The builder's scale parameter changed, so this corner is somewhere else entirely. Binding to
  // "the nearest vertex" here would silently deform a different part of the model, which is the
  // failure that would make nobody trust the feature again.
  const edit: VertEdit = { mesh: "m", at: at(6), to: [5, 5, 5] };
  const r = applyVertEdits(cube(2), [edit]);
  eq("nothing moved", r.moved, 0);
  eq("the edit is reported orphaned", r.orphans.length, 1);
  eq("...and it is the one we saved", r.orphans[0].at, at(6));
  ok("the buffer is handed back untouched", r.pos[18] === 2 || r.pos[18] === -2);
}
{
  const empty = bindVerts(new Float32Array(0), [{ mesh: "m", at: [0, 0, 0], to: [1, 1, 1] }]);
  eq("an empty mesh orphans rather than throwing", empty.orphans.length, 1);
  eq("...and binds nothing", empty.found.size, 0);
}
eq("no edits is not an error", bindVerts(cube(), []).orphans.length, 0);

// ---------------------------------------------------------------- onto a real tree

console.log("\nOnto a tree, keyed by the mesh that holds the geometry");
function meshNode(name: string, pos: Float32Array) {
  let normals = 0, spheres = 0;
  return {
    name, children: [] as any[],
    geometry: {
      attributes: { position: { array: pos, needsUpdate: false } },
      computeVertexNormals() { normals++; },
      computeBoundingSphere() { spheres++; },
      counts: () => ({ normals, spheres }),
    },
  };
}
{
  const a = meshNode("hull", cube());
  const b = meshNode("fin", cube());
  const root = { name: "root", children: [a, b] };
  const keys = [...stableKeys(root).values()];
  eq("both meshes have a key", keys.sort(), ["fin", "hull"]);

  const out = { parts: 0, mods: 0, verts: 0, missing: [] as string[], errors: [] as string[] };
  const moved = applyVerts(root, [
    { mesh: "hull", at: at(0), to: [-4, -1, -1] },
    { mesh: "fin", at: at(7), to: [-1, 4, 1] },
    { mesh: "nose", at: at(1), to: [0, 0, 0] },
  ], out);
  eq("two meshes, three vertices each", moved, 6);
  eq("the mesh that does not exist is missing, not an error", out.missing, ["nose"]);
  eq("...and nothing else complained", out.errors, []);
  const buf = (m: ReturnType<typeof meshNode>) => m.geometry.attributes.position.array;
  const countAt = (arr: Float32Array, axis: number, v: number) => {
    let n = 0;
    for (let i = axis; i < arr.length; i += 3) if (Math.abs(arr[i] - v) < 1e-6) n++;
    return n;
  };
  eq("all three of the hull's coincident vertices moved", countAt(buf(a), 0, -4), 3);
  eq("...and all three of the fin's", countAt(buf(b), 1, 4), 3);
  ok("the buffer is marked for upload", a.geometry.attributes.position.needsUpdate === true);
  // A moved surface that keeps its old normals lights as though nothing happened, and the edit
  // looks like it did not take.
  eq("shading was recomputed", a.geometry.counts(), { normals: 1, spheres: 1 });
}
{
  const a = meshNode("hull", cube());
  const out = { parts: 0, mods: 0, verts: 0, missing: [] as string[], errors: [] as string[] };
  applyVerts({ name: "root", children: [a] }, [{ mesh: "hull", at: [9, 9, 9], to: [0, 0, 0] }], out);
  eq("an orphan inside a real tree is said out loud", out.errors.length, 1);
  ok("...and it names the place", /9\.000, 9\.000, 9\.000/.test(out.errors[0]), out.errors[0]);
}
eq("no verts field at all is fine", applyVerts({ name: "r", children: [] }, undefined), 0);

// ---------------------------------------------------------------- the order in the pipeline

console.log("\nVertices are the base mesh, so they run before the stack");
const ops = readFileSync(join(process.cwd(), "src", "components", "engine", "edit", "ops.ts"), "utf8");
{
  const applyEdits = ops.slice(ops.indexOf("export function applyEdits"));
  const v = applyEdits.indexOf("applyVerts(root");
  const m = applyEdits.indexOf("const mods: any[]");
  const p = applyEdits.indexOf("edits?.parts");
  ok("applyEdits calls it", v > 0);
  // In Blender you move vertices on the base mesh and the modifiers evaluate on top. Any other
  // order and a subdivide would silently discard the hand work underneath it.
  ok("...after the part overrides", v > p, "verts at " + v + ", parts at " + p);
  ok("...and before the modifier stack", v < m, "verts at " + v + ", mods at " + m);
}
ok("the report counts them", /verts: number/.test(ops));
ok("the sidecar carries them",
   /verts\?: VertEdit\[\]/.test(readFileSync(join(process.cwd(), "src", "components", "engine", "edit", "kit.ts"), "utf8")));

// ---------------------------------------------------------------- the corner graph

console.log("\nA corner graph, built from what the code made");
{
  // 24 vertices, 8 corners. If edit mode counted vertices it would offer you 24 dots at 8 places,
  // and dragging any one of them would open a hole in the surface.
  const topo = buildTopology(cube(), cubeIndex());
  eq("24 vertices collapse to 8 corners", topo.groups.length, 8);
  eq("...three vertices each", topo.groups.map((g) => g.length), [3, 3, 3, 3, 3, 3, 3, 3]);
  eq("every vertex knows its corner", topo.groupOf.length, 24);
  ok("...and none is left unassigned", [...topo.groupOf].every((g) => g >= 0));
  eq("the rest buffer is kept, because it is the key material", topo.rest.length, 72);
  // A cube has 12 edges. Triangulating each square face adds its diagonal, so 18 is right and 12
  // would mean the diagonals were being dropped.
  eq("twelve edges plus six triangulation diagonals", topo.edges.length, 18);
  eq("twelve triangles", topo.tris.length, 12);
  ok("no triangle is degenerate", topo.tris.every(([a, b, c]) => a !== b && b !== c && a !== c));
}
{
  // Two triangles sharing an edge: 6 positions, 4 corners, 5 edges with the shared one counted
  // once. An edge stored twice is an edge drawn twice and lit twice when it is chosen.
  const pos = new Float32Array([
    0, 0, 0, 1, 0, 0, 0, 1, 0,
    1, 0, 0, 1, 1, 0, 0, 1, 0,
  ]);
  const topo = buildTopology(pos);
  eq("four corners", topo.groups.length, 4);
  eq("two triangles", topo.tris.length, 2);
  eq("five edges, the shared one once", topo.edges.length, 5);
  ok("every edge is stored low to high", topo.edges.every(([a, b]) => a < b));
}
{
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]);
  const topo = buildTopology(pos, [0, 1, 2, 1, 3, 2]);
  eq("an index buffer is honoured", topo.tris.length, 2);
  eq("...four corners", topo.groups.length, 4);
  eq("...five edges", topo.edges.length, 5);
}
{
  // A triangle with two corners welded is a sliver. Offering its third edge would offer a thing
  // with no length to click on.
  const topo = buildTopology(new Float32Array([0, 0, 0, 1, 0, 0, 1, 0, 0]));
  eq("two corners", topo.groups.length, 2);
  eq("one real edge, not three", topo.edges.length, 1);
}

// ---------------------------------------------------------------- picking

console.log("\nPicking is what the hand means");
const S = (x: number, y: number, z = 5, behind = false): Screen => ({ x, y, z, behind });
{
  const pts = [S(10, 10), S(100, 100), S(14, 13)];
  eq("the nearest dot", nearestGroup(pts, 11, 11), 0);
  eq("nothing in reach is nothing", nearestGroup(pts, 400, 400), -1);
  eq("the radius is the radius", nearestGroup(pts, 10, 30, 12), -1);
  // (10,30) is 20px from the first dot and 17.5px from the third, so a wider radius reaches the
  // THIRD one — nearest, not first-found.
  eq("...and a wider one reaches the nearest", nearestGroup(pts, 10, 30, 25), 2);
}
// Two dots on top of each other: the one in front wins, so a click takes what you can see.
eq("depth breaks a tie", nearestGroup([S(50, 50, 90), S(50, 50, 3)], 50, 50), 1);
eq("a corner behind the camera is not clickable", nearestGroup([S(50, 50, 5, true)], 50, 50), -1);
{
  near("a point on the segment", segDist2(0, 0, 10, 0, 5, 0), 0);
  near("beside it", segDist2(0, 0, 10, 0, 5, 3), 9);
  // Past the end it clamps to the endpoint, not to the infinite line the segment sits on.
  near("past the end", segDist2(0, 0, 10, 0, 14, 0), 16);
  near("a zero-length segment is its own point", segDist2(4, 4, 4, 4, 4, 7), 9);
}
{
  const pts = [S(0, 0), S(100, 0), S(0, 100)];
  const edges: Array<[number, number]> = [[0, 1], [0, 2]];
  eq("the edge under the cursor", nearestEdge(pts, edges, 50, 2), 0);
  eq("...and the other one", nearestEdge(pts, edges, 2, 50), 1);
  eq("none when the cursor is away", nearestEdge(pts, edges, 80, 80), -1);
}
eq("box select takes what is inside",
   groupsInBox([S(5, 5), S(50, 50), S(500, 500)], { x0: 0, y0: 0, x1: 100, y1: 100 }), [0, 1]);
eq("...however the rectangle was dragged",
   groupsInBox([S(5, 5), S(500, 500)], { x0: 100, y0: 100, x1: 0, y1: 0 }), [0]);

// ---------------------------------------------------------------- one click, three meanings

console.log("\nThree kinds of click, one kind of document");
{
  const topo = buildTopology(cube(), cubeIndex());
  eq("a vertex click is one corner", elemGroups("vert", topo, 3), [3]);
  eq("an edge click is both ends", elemGroups("edge", topo, 0).length, 2);
  eq("a face click is three corners", elemGroups("face", topo, 0).length, 3);
  eq("a miss selects nothing", elemGroups("vert", topo, -1), []);
  // The kinds differ only in how many corners one click adds. Nothing downstream knows that an
  // edge exists, which is why the sidecar never had to learn about one.
  ok("every kind hands back corners", ELEM_KINDS.every((k) => Array.isArray(elemGroups(k, topo, 0))));
}
{
  const topo = buildTopology(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]));
  eq("all three corners means the face is on", selectionCounts(topo, new Set([0, 1, 2])),
     { verts: 3, edges: 3, faces: 1 });
  eq("two of three is an edge and no face", selectionCounts(topo, new Set([0, 1])),
     { verts: 2, edges: 1, faces: 0 });
  eq("one is neither", selectionCounts(topo, new Set([0])), { verts: 1, edges: 0, faces: 0 });
}

// ---------------------------------------------------------------- what gets written down

console.log("\nOnly what moved is written down");
{
  const rest = cube();
  const live = new Float32Array(rest);
  const topo = buildTopology(rest, cubeIndex());
  eq("an untouched mesh writes nothing", vertEditsOf("hull", live, topo).length, 0);

  const g5 = groupOfCorner(topo, 5);
  for (const i of topo.groups[g5]) live[i * 3 + 1] = 2.5;
  const out = vertEditsOf("hull", live, topo);
  // Forty thousand vertices and three dragged corners writes three lines, so the sidecar stays
  // something a person can read and a diff stays reviewable.
  eq("one moved corner is one entry", out.length, 1);
  eq("...named by its mesh", out[0].mesh, "hull");
  eq("...keyed by where the CODE put it", out[0].at, CORNERS[5]);
  eq("...valued at where it is now", out[0].to, [CORNERS[5][0], 2.5, CORNERS[5][2]]);
  ok("the moved corner knows it moved", groupMoved(live, topo, g5));
  ok("...and another does not", !groupMoved(live, topo, groupOfCorner(topo, 4)));

  // THE ROUND TRIP. What the editor writes is what the binder finds, on a fresh build.
  const back = applyVertEdits(cube(), out);
  eq("the written document re-applies", back.moved, 3);
  eq("...with nothing orphaned", back.orphans.length, 0);
  near("...to the same height", back.pos[topo.groups[g5][0] * 3 + 1], 2.5);
}

// ---------------------------------------------------------------- the second key

console.log("\nWhen the code moves the vertex itself, the place key cannot help");
{
  // The case the rock found. A parameter that re-displaces every vertex leaves the corner count
  // and the face count alone -- same builder, same path, same emission order -- so corner N is
  // still corner N and the hand's OFFSET belongs on top of wherever it now sits.
  const rest = cube();
  const topo = buildTopology(rest, cubeIndex());
  const g = groupOfCorner(topo, 6);
  const doc: VertEdit[] = [{
    mesh: "m", at: CORNERS[6], to: [CORNERS[6][0], CORNERS[6][1] + 0.5, CORNERS[6][2]],
    g, sig: topoSig(topo.groups.length, topo.tris.length),
  }];

  // Same shape, every vertex moved: a 1.10 scale is far outside the tolerance.
  const moved = cube(1.1);
  const noSig = applyVertEdits(moved, [{ mesh: "m", at: CORNERS[6], to: doc[0].to }], VERT_TOL, 12);
  eq("without an ordinal it orphans, as it must", noSig.moved, 0);

  const withSig = applyVertEdits(moved, doc, VERT_TOL, 12);
  eq("with a matching signature it binds by ordinal", withSig.moved, 3);
  eq("...and nothing is orphaned", withSig.orphans.length, 0);
  eq("...by the ordinal, not by the place", [withSig.byPlace, withSig.byOrdinal], [0, 3]);
  const i0 = groupVerts(moved).groups[g][0];
  // THE POINT: the offset, on the code's NEW position. Not the stale absolute.
  near("the hand's offset landed on the new position", withSig.pos[i0 * 3 + 1], CORNERS[6][1] * 1.1 + 0.5);
  ok("...which is not where it was saved", Math.abs(withSig.pos[i0 * 3 + 1] - doc[0].to[1]) > 1e-3);
}
{
  // And the guard. A different shape means the ordinal is a stranger, however plausible it looks.
  const rest = cube();
  const topo = buildTopology(rest, cubeIndex());
  const g = groupOfCorner(topo, 6);
  const doc: VertEdit[] = [{ mesh: "m", at: CORNERS[6], to: [9, 9, 9], g, sig: "8:12" }];

  eq("a face count that disagrees is not a match", applyVertEdits(cube(1.1), doc, VERT_TOL, 20).moved, 0);
  eq("...nor a corner count", applyVertEdits(new Float32Array([5, 5, 5, 6, 6, 6]), doc, VERT_TOL, 12).moved, 0);
  // -1 means the caller could not say how many faces there are, and an unknown shape is not a
  // matching one. Guessing here would deform a stranger.
  eq("an unknown shape is never a match", applyVertEdits(cube(1.1), doc, VERT_TOL, -1).moved, 0);
  eq("...and a document with no ordinal at all still works by place",
     applyVertEdits(cube(), [{ mesh: "m", at: CORNERS[6], to: [9, 9, 9] }]).moved, 3);
}
{
  const topo = buildTopology(cube(), cubeIndex());
  eq("the signature is corners and faces", topoSig(topo.groups.length, topo.tris.length), "8:12");
  const doc = vertEditsOf("m", (() => {
    const live = new Float32Array(cube());
    for (const i of topo.groups[0]) live[i * 3] += 1;
    return live;
  })(), topo);
  eq("the editor writes the ordinal", doc[0].g, 0);
  eq("...and the signature", doc[0].sig, "8:12");
}

console.log("\nApplying the document twice is applying it once");
{
  // Both keys read the rest buffer. Read the LIVE one and the ordinal path adds its offset again
  // every time, so a peak climbs a little further on every rebuild.
  const rest = cube();
  const topo = buildTopology(rest, cubeIndex());
  const g = groupOfCorner(topo, 2);
  const doc: VertEdit[] = [{
    mesh: "m", at: CORNERS[2], to: [CORNERS[2][0], CORNERS[2][1] + 0.7, CORNERS[2][2]],
    g, sig: topoSig(topo.groups.length, topo.tris.length),
  }];
  const node = meshNode("m", cube(1.1));
  const tree = { name: "root", children: [node] };
  const out1 = { parts: 0, mods: 0, verts: 0, missing: [] as string[], errors: [] as string[] };
  applyVerts(tree, doc, out1);
  const after1 = [...node.geometry.attributes.position.array];
  applyVerts(tree, doc, out1);
  const after2 = [...node.geometry.attributes.position.array];
  eq("the second pass moves nothing further", after1.every((v, k) => Math.abs(v - after2[k]) < 1e-9), true);
  ok("and the snapshot is on the object where both can find it",
     !!(node as any).userData?.__rest);
}
eq("restBuffer copies rather than aliases", (() => {
  const n = meshNode("m", cube());
  const r = restBuffer(n as any)!;
  n.geometry.attributes.position.array[0] = 99;
  return r[0] !== 99;
})(), true);

// ---------------------------------------------------------------- and it is wired in

console.log("\nWired into the editor, and behind a switch");
const world = readFileSync(join(process.cwd(), "src", "components", "engine", "edit", "world.ts"), "utf8");
const editor = readFileSync(join(process.cwd(), "src", "components", "engine", "edit", "Editor.tsx"), "utf8");
ok("the world has a third mode", /export type Mode = "object" \| "pose" \| "edit"/.test(world));
// The snapshot is in ops.ts, so the editor and the runtime applier cannot disagree about which
// buffer a key was written against. world.ts delegates to it rather than keeping a second copy.
ok("the rest snapshot is shared, not duplicated",
   /export function restBuffer/.test(ops) && /return restBuffer\(o\)/.test(world));
// Holding the stack off is not a detail. Without it you drag corners a subdivide invented, and
// the position saved is one that no rebuild can ever find again.
ok("the modifier stack is held off in edit mode",
   /modeRef\.current === "edit" \? \[\] : editsRef\.current\.mods/.test(editor));
ok("a vertex move uses the same undo as everything else",
   /kind === "vert"/.test(editor) && /verts: \[/.test(editor));
ok("Tab gets you there", /k === "Tab"\) \{ setMode/.test(editor));

// ----------------------------------------------------------------

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

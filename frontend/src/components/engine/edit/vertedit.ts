// Edit mode: the mesh below the object.
//
// Blender's edit mode is easy because its mesh is a stored document. Ours is a program's output,
// so two things have to be solved that Blender never meets.
//
// THE FIRST is that a corner is not a vertex. Generated geometry duplicates: a cube is 24
// vertices for 8 corners, because each face wants its own normal, and a sphere seam has a whole
// column of doubles. Selecting one of three coincident vertices and dragging it opens a hole. So
// the unit of selection here is not a vertex but a GROUP — every vertex sharing a place — and the
// grouping uses the same bucket rule as `bindVerts`, so what you select is exactly what gets
// saved and re-bound later. One rule, written once.
//
// THE SECOND is that the buffer you are looking at may already carry saved edits. The key for a
// vertex has to be where the CODE put it, not where a previous session dragged it, or the key
// written today matches nothing tomorrow. That is why everything here is built from a `rest`
// buffer — a snapshot taken before any edit landed — while the drawing and the dragging read the
// live one.
//
// Edges and faces are derived, never stored. An edge is selected when both its groups are, a face
// when all three are. That is how Blender's selection really works underneath, and it means the
// document only ever has to know about vertices.

import { VERT_TOL, groupVerts, topoSig, vertKey, type VertEdit } from "./ops";

export type ElemKind = "vert" | "edge" | "face";
export const ELEM_KINDS: ElemKind[] = ["vert", "edge", "face"];

export interface EditTopology {
  /** Each corner, as the buffer indices that share it. The ordinal into this array is a "group",
   *  and a group is what selection, drawing and dragging all speak in. */
  groups: number[][];
  /** Buffer index to its group ordinal. */
  groupOf: Int32Array;
  /** Undirected unique edges, by group ordinal. An edge between two indices of the SAME group is
   *  not an edge, it is a degenerate sliver, and it is dropped. */
  edges: Array<[number, number]>;
  /** Triangles by group ordinal, in buffer order, so a raycast hit maps straight back. */
  tris: Array<[number, number, number]>;
  /** Where each group was when the code built it. This is the key material. */
  rest: Float32Array;
}

/**
 * The corner graph of one mesh, built from the positions the code produced.
 *
 * `index` is the element index buffer when the geometry has one; without it the positions are
 * taken as consecutive triangles, which is what an unindexed BufferGeometry means.
 */
export function buildTopology(
  rest: Float32Array, index: ArrayLike<number> | null = null, tol = VERT_TOL,
): EditTopology {
  // The SAME grouping the binder uses. If these two ever disagreed, the corner you clicked and
  // the corner that got saved would be different corners.
  const { groups, groupOf } = groupVerts(rest, tol);

  const tris: Array<[number, number, number]> = [];
  const seen = new Set<number>();
  const edges: Array<[number, number]> = [];
  const addEdge = (a: number, b: number) => {
    if (a === b) return;                       // both ends are the same corner: not an edge
    const lo = Math.min(a, b), hi = Math.max(a, b);
    const id = lo * groups.length + hi;
    if (seen.has(id)) return;
    seen.add(id);
    edges.push([lo, hi]);
  };

  const count = index ? index.length : groupOf.length;
  for (let t = 0; t + 2 < count; t += 3) {
    const ia = index ? index[t] : t;
    const ib = index ? index[t + 1] : t + 1;
    const ic = index ? index[t + 2] : t + 2;
    const a = groupOf[ia], b = groupOf[ib], c = groupOf[ic];
    if (a < 0 || b < 0 || c < 0) continue;
    tris.push([a, b, c]);
    addEdge(a, b); addEdge(b, c); addEdge(c, a);
  }

  return { groups, groupOf, edges, tris, rest };
}

/** Where a group sits in the LIVE buffer. Every index in a group holds the same place, so the
 *  first is the answer; the average would only hide a tear rather than avoid one. */
export function groupAt(pos: Float32Array, topo: EditTopology, g: number): [number, number, number] {
  const i = topo.groups[g]?.[0] ?? 0;
  return [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]];
}

/** Where a group was when the code built it — its key. */
export function groupRest(topo: EditTopology, g: number): [number, number, number] {
  const i = topo.groups[g]?.[0] ?? 0;
  return [topo.rest[i * 3], topo.rest[i * 3 + 1], topo.rest[i * 3 + 2]];
}

/** A group has been moved when the live buffer no longer agrees with the rest buffer. */
export function groupMoved(pos: Float32Array, topo: EditTopology, g: number, eps = 1e-6): boolean {
  const i = topo.groups[g]?.[0];
  if (i === undefined) return false;
  return Math.abs(pos[i * 3] - topo.rest[i * 3]) > eps
    || Math.abs(pos[i * 3 + 1] - topo.rest[i * 3 + 1]) > eps
    || Math.abs(pos[i * 3 + 2] - topo.rest[i * 3 + 2]) > eps;
}

// ---------------------------------------------------------------------------
// Picking, in screen space
//
// Pure: the caller projects, these functions choose. Screen space rather than a raycast because
// that is what the hand means — a vertex is a dot a few pixels across, and "the dot nearest the
// cursor" is the only rule that feels right at any zoom.

/** One projected group: pixel position, distance from the camera, and whether it is behind it. */
export interface Screen { x: number; y: number; z: number; behind: boolean }

/**
 * The group nearest the cursor within `radius` pixels.
 *
 * Ties on screen distance are broken by depth, so clicking where a near corner and a far one
 * overlap picks the one you can see. Returns -1 for nothing in reach.
 */
export function nearestGroup(pts: Screen[], x: number, y: number, radius = 12): number {
  let best = -1, bestD = radius * radius, bestZ = Infinity;
  for (let g = 0; g < pts.length; g++) {
    const p = pts[g];
    if (!p || p.behind) continue;
    const dx = p.x - x, dy = p.y - y;
    const d = dx * dx + dy * dy;
    if (d > bestD) continue;
    // A clearly closer dot wins on distance; a near-tie is settled by which is in front.
    if (d < bestD - 1 || p.z < bestZ) { best = g; bestD = Math.min(bestD, d); bestZ = p.z; }
  }
  return best;
}

/** Squared distance from a point to a segment, in pixels. */
export function segDist2(ax: number, ay: number, bx: number, by: number, x: number, y: number): number {
  const vx = bx - ax, vy = by - ay;
  const len = vx * vx + vy * vy;
  const t = len > 0 ? Math.max(0, Math.min(1, ((x - ax) * vx + (y - ay) * vy) / len)) : 0;
  const px = ax + vx * t - x, py = ay + vy * t - y;
  return px * px + py * py;
}

/** The edge nearest the cursor, as its index into `topo.edges`. -1 for nothing in reach. */
export function nearestEdge(
  pts: Screen[], edges: Array<[number, number]>, x: number, y: number, radius = 10,
): number {
  let best = -1, bestD = radius * radius, bestZ = Infinity;
  for (let e = 0; e < edges.length; e++) {
    const a = pts[edges[e][0]], b = pts[edges[e][1]];
    if (!a || !b || a.behind || b.behind) continue;
    const d = segDist2(a.x, a.y, b.x, b.y, x, y);
    if (d > bestD) continue;
    const z = Math.min(a.z, b.z);
    if (d < bestD - 1 || z < bestZ) { best = e; bestD = Math.min(bestD, d); bestZ = z; }
  }
  return best;
}

/** Every group whose dot lands inside the rectangle — Blender's B, one level down. */
export function groupsInBox(
  pts: Screen[], r: { x0: number; y0: number; x1: number; y1: number },
): number[] {
  const x0 = Math.min(r.x0, r.x1), x1 = Math.max(r.x0, r.x1);
  const y0 = Math.min(r.y0, r.y1), y1 = Math.max(r.y0, r.y1);
  const out: number[] = [];
  for (let g = 0; g < pts.length; g++) {
    const p = pts[g];
    if (!p || p.behind) continue;
    if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1) out.push(g);
  }
  return out;
}

// ---------------------------------------------------------------------------
// What a click means, given the element kind
//
// Selection is always a set of GROUPS, whichever kind is active. Choosing "edge" does not change
// what is stored, only how many groups one click adds — which is exactly Blender's model and the
// reason the sidecar never has to know an edge exists.

/** The groups one click selects. Empty means the click hit nothing. */
export function elemGroups(kind: ElemKind, topo: EditTopology, hit: number, tri = -1): number[] {
  if (hit < 0 && tri < 0) return [];
  if (kind === "vert") return hit >= 0 ? [hit] : [];
  if (kind === "edge") {
    const e = topo.edges[hit];
    return e ? [e[0], e[1]] : [];
  }
  const t = topo.tris[tri >= 0 ? tri : hit];
  if (!t) return [];
  // A degenerate face — two corners welded — contributes each group once.
  return [...new Set(t)];
}

/** Whether a derived element counts as selected: an edge when both ends are, a face when all
 *  three corners are. Used only for drawing; the truth is always the group set. */
export function edgeSelected(sel: Set<number>, e: [number, number]): boolean {
  return sel.has(e[0]) && sel.has(e[1]);
}
export function faceSelected(sel: Set<number>, t: [number, number, number]): boolean {
  return sel.has(t[0]) && sel.has(t[1]) && sel.has(t[2]);
}

/** How many edges and faces the current group selection implies, for the header count. */
export function selectionCounts(topo: EditTopology, sel: Set<number>): { verts: number; edges: number; faces: number } {
  let edges = 0, faces = 0;
  for (const e of topo.edges) if (edgeSelected(sel, e)) edges++;
  for (const t of topo.tris) if (faceSelected(sel, t)) faces++;
  return { verts: sel.size, edges, faces };
}

// ---------------------------------------------------------------------------

/** The saved document for one mesh: every group that has moved away from where the code put it.
 *
 *  Only the moved ones. A mesh with 40,000 vertices and three dragged corners writes three
 *  entries, so the sidecar stays a thing a person can read and a diff stays reviewable. */
export function vertEditsOf(
  meshKey: string, pos: Float32Array, topo: EditTopology, round = 5,
): VertEdit[] {
  const r = (v: number) => Number(v.toFixed(round));
  // Both keys are written, always. The place is tried first and costs nothing to store; the
  // ordinal and the signature are what let an edit survive a parameter that moved the vertex
  // itself rather than something else.
  const sig = topoSig(topo.groups.length, topo.tris.length);
  const out: VertEdit[] = [];
  for (let g = 0; g < topo.groups.length; g++) {
    if (!groupMoved(pos, topo, g)) continue;
    const a = groupRest(topo, g), b = groupAt(pos, topo, g);
    out.push({
      mesh: meshKey,
      at: [r(a[0]), r(a[1]), r(a[2])],
      to: [r(b[0]), r(b[1]), r(b[2])],
      g, sig,
    });
  }
  return out;
}

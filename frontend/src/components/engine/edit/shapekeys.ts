// Shape keys (Blender) / morph targets (glTF, three): a mesh's other shapes, mixed in by a value.
//
// A face that smiles, an eye that closes, a muscle that bulges, a thin and a fat version of one
// character: each is the same mesh with some corners moved. A key is saved as those moves, each
// corner found BY PLACE (as everything this editor saves), so the code can build the mesh again and
// the key still finds its corners. Keys apply after the hand-moved vertices and the edit-mode steps
// and before the modifiers, which is Blender's order: a key is part of the base mesh's shape.
//
// In three a key becomes a relative morph target with the key's name, so a game animates
// `mesh.morphTargetInfluences[mesh.morphTargetDictionary.smile]`, and a GLB export carries it.
//
// Plain numbers and arrays, deterministic, no three.

type V3 = [number, number, number];

export interface ShapeKey {
  /** Stable key of the mesh (as VertEdit.mesh). */
  mesh: string;
  name: string;
  /** 0..1, how much of the key is mixed in. */
  value: number;
  /** Each moved corner: where it is in the base mesh, and how far the key moves it. */
  moves: { at: V3; d: V3 }[];
}

const TOL = 1.5e-3;

/**
 * The key's moves as a delta per buffer vertex (three's layout: 3 floats per vertex). Every copy of
 * a corner (generated meshes duplicate corners for hard edges and seams) gets the same delta, so the
 * surface does not tear. Returns how many moves found a corner.
 */
export function shapeDeltas(pos: ArrayLike<number>, moves: { at: V3; d: V3 }[], tol = TOL): { delta: Float32Array; found: number } {
  const n = Math.floor(pos.length / 3);
  const delta = new Float32Array(n * 3);
  const cell = tol * 2;
  const grid = new Map<string, number[]>();
  for (let i = 0; i < n; i++) {
    const k = Math.floor(pos[i * 3] / cell) + "," + Math.floor(pos[i * 3 + 1] / cell) + "," + Math.floor(pos[i * 3 + 2] / cell);
    const l = grid.get(k); if (l) l.push(i); else grid.set(k, [i]);
  }
  let found = 0;
  for (const mv of moves) {
    const [x, y, z] = mv.at;
    const cx = Math.floor(x / cell), cy = Math.floor(y / cell), cz = Math.floor(z / cell);
    let any = false;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      for (const i of grid.get((cx + dx) + "," + (cy + dy) + "," + (cz + dz)) || []) {
        if (Math.hypot(pos[i * 3] - x, pos[i * 3 + 1] - y, pos[i * 3 + 2] - z) > tol) continue;
        delta[i * 3] = mv.d[0]; delta[i * 3 + 1] = mv.d[1]; delta[i * 3 + 2] = mv.d[2];
        any = true;
      }
    }
    if (any) found++;
  }
  return { delta, found };
}

/** The moves that make `shaped` out of `base` (same layout): one per corner, the copies counted once. */
export function shapeMoves(base: ArrayLike<number>, shaped: ArrayLike<number>, eps = 1e-7): { at: V3; d: V3 }[] {
  const seen = new Set<string>();
  const out: { at: V3; d: V3 }[] = [];
  for (let i = 0; i + 2 < base.length; i += 3) {
    const d: V3 = [shaped[i] - base[i], shaped[i + 1] - base[i + 1], shaped[i + 2] - base[i + 2]];
    if (Math.abs(d[0]) < eps && Math.abs(d[1]) < eps && Math.abs(d[2]) < eps) continue;
    const at: V3 = [base[i], base[i + 1], base[i + 2]];
    const k = at.map((v) => Math.round(v / 1e-5)).join(",");
    if (seen.has(k)) continue;
    seen.add(k);
    const r = (v: number) => Math.round(v * 1e7) / 1e7;
    out.push({ at: [r(at[0]), r(at[1]), r(at[2])], d: [r(d[0]), r(d[1]), r(d[2])] });
  }
  return out;
}

/** Keys as they come from a file: a name, a value in 0..1 and real moves. */
export function cleanShapes(raw: any): ShapeKey[] {
  const out: ShapeKey[] = [];
  const isV3 = (v: any) => Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every((x: any) => typeof x === "number" && isFinite(x));
  const names = new Set<string>();
  for (const k of Array.isArray(raw) ? raw : []) {
    if (!k || typeof k.mesh !== "string" || typeof k.name !== "string" || !k.name.trim()) continue;
    const id = k.mesh + "\u0000" + k.name;
    if (names.has(id)) continue;
    names.add(id);
    const value = typeof k.value === "number" && isFinite(k.value) ? Math.max(0, Math.min(1, k.value)) : 0;
    const moves = (Array.isArray(k.moves) ? k.moves : []).filter((m: any) => m && isV3(m.at) && isV3(m.d))
      .map((m: any) => ({ at: m.at.slice(0, 3) as V3, d: m.d.slice(0, 3) as V3 }));
    out.push({ mesh: k.mesh, name: k.name.trim().slice(0, 64), value, moves });
  }
  return out;
}

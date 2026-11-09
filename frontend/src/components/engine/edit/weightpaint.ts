// Weight painting: a hand's corrections to the automatic skin weights.
//
// Envelope and heat weights are computed again every time the rig binds, from the bones and the
// mesh, and every automatic method leaves bad spots: an armpit pulled by the chest, a thumb web
// pulled by the index finger. Blender's answer is to paint the weights; the painting is then part
// of the mesh and lost when the mesh is made again. Here the mesh IS made again on every rebuild,
// so what is saved is only the correction: "this bone holds this corner at this weight", the
// corner found BY PLACE in the bind pose. Bind, compute the automatic weights, then lay the
// corrections on top and share what is left among the other bones.
//
// Plain numbers and arrays, deterministic, no three.

type V3 = [number, number, number];

export interface WeightPaint {
  bone: string;
  /** The corner in the bind pose, world space (where bindSkin puts the skinned geometry). */
  at: V3;
  /** 0..1, the weight this bone holds the corner at. */
  w: number;
}

const TOL = 1.5e-3;

/**
 * Set `bone`'s weight on one vertex to `w` and share 1 - w among its other bones in the
 * proportions they had. With no slot free for the bone, the weakest one gives its place up.
 */
export function setWeight(index: ArrayLike<number> & { [i: number]: number }, weight: ArrayLike<number> & { [i: number]: number },
  v: number, k: number, bone: number, w: number): void {
  w = Math.max(0, Math.min(1, w));
  let slot = -1;
  for (let j = 0; j < k; j++) if (index[v * k + j] === bone && weight[v * k + j] > 0) slot = j;
  if (slot < 0) for (let j = 0; j < k; j++) if (index[v * k + j] === bone) slot = j;
  if (slot < 0) {
    let lo = 0;
    for (let j = 1; j < k; j++) if (weight[v * k + j] < weight[v * k + lo]) lo = j;
    slot = lo;
    index[v * k + slot] = bone;
    weight[v * k + slot] = 0;
  }
  let others = 0;
  for (let j = 0; j < k; j++) if (j !== slot) others += weight[v * k + j];
  const rest = 1 - w;
  for (let j = 0; j < k; j++) {
    if (j === slot) continue;
    weight[v * k + j] = others > 1e-9 ? (weight[v * k + j] / others) * rest : 0;
  }
  weight[v * k + slot] = w;
  // A corner with nothing else to give to keeps its full weight on this bone: a vertex must sum to 1.
  if (others <= 1e-9 && rest > 0) weight[v * k + slot] = 1;
}

/** Lay saved corrections on freshly computed weights. `pos` is the bind-pose positions (3 per
 *  vertex); `k` the influences per vertex. Returns how many corrections found a corner. */
export function applyWeightPaints(pos: ArrayLike<number>, index: any, weight: any, k: number,
  boneIndex: (name: string) => number, paints: WeightPaint[], tol = TOL): number {
  const n = Math.floor(pos.length / 3);
  const cell = tol * 2;
  const grid = new Map<string, number[]>();
  for (let i = 0; i < n; i++) {
    const key = Math.floor(pos[i * 3] / cell) + "," + Math.floor(pos[i * 3 + 1] / cell) + "," + Math.floor(pos[i * 3 + 2] / cell);
    const l = grid.get(key); if (l) l.push(i); else grid.set(key, [i]);
  }
  let found = 0;
  for (const p of paints) {
    const b = boneIndex(p.bone);
    if (b < 0) continue;
    const cx = Math.floor(p.at[0] / cell), cy = Math.floor(p.at[1] / cell), cz = Math.floor(p.at[2] / cell);
    let any = false;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      for (const i of grid.get((cx + dx) + "," + (cy + dy) + "," + (cz + dz)) || []) {
        if (Math.hypot(pos[i * 3] - p.at[0], pos[i * 3 + 1] - p.at[1], pos[i * 3 + 2] - p.at[2]) > tol) continue;
        setWeight(index, weight, i, k, b, p.w);
        any = true;
      }
    }
    if (any) found++;
  }
  return found;
}

/** A bone's weight on a vertex (0 when the bone is not among its influences). */
export function weightOf(index: ArrayLike<number>, weight: ArrayLike<number>, v: number, k: number, bone: number): number {
  let s = 0;
  for (let j = 0; j < k; j++) if (index[v * k + j] === bone) s += weight[v * k + j];
  return s;
}

/** The new weight a brush gives: add pulls toward 1, subtract toward 0, set toward `value`. */
export function brushWeight(old: number, mode: "add" | "subtract" | "set", amount: number, value = 1): number {
  const a = Math.max(0, Math.min(1, amount));
  if (mode === "add") return old + (1 - old) * a;
  if (mode === "subtract") return old - old * a;
  return old + (Math.max(0, Math.min(1, value)) - old) * a;
}

/** Heat-map colour of a weight, Blender's: blue 0, green 0.5, red 1. Linear RGB 0..1. */
export function weightColor(w: number): V3 {
  const t = Math.max(0, Math.min(1, w));
  if (t < 0.25) return [0, t * 4, 1];
  if (t < 0.5) return [0, 1, 1 - (t - 0.25) * 4];
  if (t < 0.75) return [(t - 0.5) * 4, 1, 0];
  return [1, 1 - (t - 0.75) * 4, 0];
}

/** Corrections as they come from a file; the last one for a bone and a place wins. */
export function cleanWeightPaints(raw: any): WeightPaint[] {
  const isV3 = (v: any) => Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every((x: any) => typeof x === "number" && isFinite(x));
  const byKey = new Map<string, WeightPaint>();
  for (const p of Array.isArray(raw) ? raw : []) {
    if (!p || typeof p.bone !== "string" || !p.bone || !isV3(p.at) || typeof p.w !== "number" || !isFinite(p.w)) continue;
    const at = p.at.slice(0, 3) as V3;
    byKey.set(p.bone + "|" + at.map((x: number) => Math.round(x / 1e-5)).join(","), { bone: p.bone, at, w: Math.max(0, Math.min(1, p.w)) });
  }
  return [...byKey.values()];
}

/** Merge new corrections into a list: the same bone at the same place is replaced. */
export function mergeWeightPaints(old: WeightPaint[], add: WeightPaint[]): WeightPaint[] {
  return cleanWeightPaints([...old, ...add]);
}

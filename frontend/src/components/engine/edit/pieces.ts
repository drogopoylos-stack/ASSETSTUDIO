// PIECES OF A MERGED MESH.
//
// A game merges its scenery for speed: rot-rush draws its whole base — every treadmill, stall,
// railing and lamp — as one 20,812-triangle mesh called `base-props`, so there is no entity for a
// treadmill to select, and a click in the editor could only ever take all of them. The objects are
// still in there. A merged mesh is appended one object after another, so the triangles of one
// object are CONTIGUOUS and sit next to each other in space; walking the index buffer in order and
// cutting wherever the next triangle is far from the piece being built, or would make it too big,
// gives back pieces that are the objects the code emitted. Measured on rot-rush's base: 401 boxes,
// 144 pieces, median 3.8 m across — a treadmill, a stall, a sign with its letters.
//
// A piece is named by its triangle range, `mesh~t<first>-<end>`, because the game's own buffer has
// the same triangles in the same order as the mirror's copy of it: the key means the same corners
// on both sides with no second pass of this algorithm in the game. Every array here is plain, so
// it runs in node for the tests and in the game page through forge-ops.js.

export interface Piece {
  /** Triangle range in index order, first and one past the last. */
  t0: number;
  t1: number;
  /** Corners (distinct vertices) the piece owns. */
  n: number;
  /** Its middle and its box, in the mesh's own frame. */
  c: [number, number, number];
  lo: [number, number, number];
  hi: [number, number, number];
}

/** A run of consecutive pieces that sit together: a sign and its letters, a stall and its awning.
 *  The size between one piece and the whole mesh, for the second click. */
export interface PieceGroup {
  /** Indices into `pieces`, first and last, inclusive. */
  first: number;
  last: number;
}

export interface PieceMap {
  pieces: Piece[];
  groups: PieceGroup[];
  /** Median size of one connected part — a box, usually — which scales both thresholds. */
  median: number;
  /** Size of the whole mesh. */
  diag: number;
  /** Worth splitting: many pieces, each far smaller than the whole. A creature, a single prop or
   *  a floor slab is not a merged mesh, and a click on it takes the whole object as before. */
  merged: boolean;
}

export const PIECE_KEY_RE = /^(.+)~t(\d+)-(\d+)$/;

/** The key of a piece of the mesh keyed `meshKey`. */
export function pieceKey(meshKey: string, p: { t0: number; t1: number }): string {
  return meshKey + "~t" + p.t0 + "-" + p.t1;
}

/** `mesh~t12-40` back into its parts, or null for an ordinary key. */
export function parsePieceKey(key: string): { mesh: string; t0: number; t1: number } | null {
  const m = PIECE_KEY_RE.exec(key);
  if (!m) return null;
  const t0 = +m[2], t1 = +m[3];
  return t1 > t0 ? { mesh: m[1], t0, t1 } : null;
}

const vertexOf = (idx: ArrayLike<number> | null, t: number, k: number) => (idx ? idx[t * 3 + k] : t * 3 + k);

/**
 * Cut a mesh into the objects it was merged from.
 *
 * Two passes. The first finds the connected parts (a union of vertices through triangles) only to
 * measure them: their median size is the unit both thresholds are written in, so the rule means
 * the same thing for a game in metres and one in centimetres. The second walks the triangles in
 * buffer order and keeps adding to the current piece while the next triangle belongs to the same
 * part as the last one, or sits within 0.4 of a part's size of the piece and keeps it under eight
 * parts across. A wall that is itself bigger than that is always one piece; nothing joins it.
 */
export function piecesOf(pos: ArrayLike<number>, idx: ArrayLike<number> | null,
                         opts: { gap?: number; cap?: number; groupGap?: number; groupCap?: number } = {}): PieceMap {
  const nv = Math.floor(pos.length / 3);
  const nt = idx ? Math.floor(idx.length / 3) : Math.floor(nv / 3);
  const empty: PieceMap = { pieces: [], groups: [], median: 0, diag: 0, merged: false };
  if (!nt || !nv) return empty;

  // Pass 1: connected parts, by index.
  const par = new Int32Array(nv);
  for (let i = 0; i < nv; i++) par[i] = i;
  const find = (x: number) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  for (let t = 0; t < nt; t++) {
    const a = find(vertexOf(idx, t, 0)), b = find(vertexOf(idx, t, 1)), c = find(vertexOf(idx, t, 2));
    if (a !== b) par[a] = b;
    const bb = find(b);
    if (bb !== c) par[bb] = c;
  }
  const partBox = new Map<number, number[]>();
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let t = 0; t < nt; t++) {
    const r = find(vertexOf(idx, t, 0));
    let b = partBox.get(r);
    if (!b) { b = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity]; partBox.set(r, b); }
    for (let k = 0; k < 3; k++) {
      const v = vertexOf(idx, t, k);
      for (let a = 0; a < 3; a++) {
        const x = pos[v * 3 + a];
        if (x < b[a]) b[a] = x;
        if (x > b[a + 3]) b[a + 3] = x;
        if (x < lo[a]) lo[a] = x;
        if (x > hi[a]) hi[a] = x;
      }
    }
  }
  const diagOf = (b: ArrayLike<number>) => Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]);
  const sizes = [...partBox.values()].map(diagOf).sort((a, b) => a - b);
  const median = sizes[sizes.length >> 1] || 0;
  const diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
  if (!(median > 0)) return { ...empty, diag };
  const gapMax = (opts.gap ?? 0.4) * median;
  const capMax = (opts.cap ?? 8) * median;

  // SAME CORNERS, SAME OBJECT — for small things. A box with a normal per face has four corners to
  // a face and none shared, so by index every face is a part of its own, and three's BoxGeometry
  // puts the -x face a box-width after the +x face: each box of a merged row came out as two
  // pieces. Parts that meet at the very same corners count as one here, while what they make stays
  // under the cap. The median above is still the parts' own, so a level whose boxes share their
  // corners already (rot-rush's 104 pieces) is cut exactly as before; welding everything instead
  // chained its railings into long parts and cut it into 190.
  const wpar = new Map<number, number>();
  const wfind = (x: number): number => {
    let r = x;
    while (wpar.has(r) && wpar.get(r) !== r) r = wpar.get(r)!;
    let y = x;
    while (y !== r) { const n = wpar.get(y)!; wpar.set(y, r); y = n; }
    return r;
  };
  {
    let span = 0;
    for (let i = 0; i < nv * 3; i++) span = Math.max(span, Math.abs(pos[i]));
    const q = Math.max(span, 1) * 1e-6;
    const at = new Map<string, number>();
    for (let v = 0; v < nv; v++) {
      const r = find(v);
      if (!partBox.has(r)) continue;
      const k = Math.round(pos[v * 3] / q) + "," + Math.round(pos[v * 3 + 1] / q) + "," + Math.round(pos[v * 3 + 2] / q);
      const w = at.get(k);
      if (w === undefined) { at.set(k, r); continue; }
      const ra = wfind(r), rb = wfind(w);
      if (ra !== rb) wpar.set(ra, rb);
    }
  }
  const wbox = new Map<number, number[]>();
  const wn = new Map<number, number>();
  for (const [r, b] of partBox) {
    const w = wfind(r);
    wn.set(w, (wn.get(w) || 0) + 1);
    const cur = wbox.get(w);
    if (!cur) { wbox.set(w, b.slice()); continue; }
    for (let a = 0; a < 3; a++) { cur[a] = Math.min(cur[a], b[a]); cur[a + 3] = Math.max(cur[a + 3], b[a + 3]); }
  }
  const joinOf = (r: number): number => {
    const w = wfind(r);
    return (wn.get(w) || 1) > 1 && diagOf(wbox.get(w)!) <= capMax ? -2 - w : r;
  };

  // Pass 2: walk the triangles in the order the code emitted them.
  const pieces: Piece[] = [];
  let cur: { t0: number; box: number[] } | null = null;
  let lastPart = -1;
  const tb = [0, 0, 0, 0, 0, 0];
  const close = (t1: number) => {
    if (!cur) return;
    const b = cur.box;
    pieces.push({ t0: cur.t0, t1, n: 0, c: [0, 0, 0], lo: [b[0], b[1], b[2]], hi: [b[3], b[4], b[5]] });
    cur = null;
  };
  for (let t = 0; t < nt; t++) {
    tb[0] = tb[1] = tb[2] = Infinity;
    tb[3] = tb[4] = tb[5] = -Infinity;
    for (let k = 0; k < 3; k++) {
      const v = vertexOf(idx, t, k);
      for (let a = 0; a < 3; a++) {
        const x = pos[v * 3 + a];
        if (x < tb[a]) tb[a] = x;
        if (x > tb[a + 3]) tb[a + 3] = x;
      }
    }
    const part = joinOf(find(vertexOf(idx, t, 0)));
    if (cur) {
      const b: number[] = (cur as { t0: number; box: number[] }).box;
      let join = part === lastPart;
      if (!join) {
        let gap = 0;
        for (let a = 0; a < 3; a++) gap = Math.max(gap, tb[a] - b[a + 3], b[a] - tb[a + 3]);
        const u = Math.hypot(Math.max(b[3], tb[3]) - Math.min(b[0], tb[0]), Math.max(b[4], tb[4]) - Math.min(b[1], tb[1]), Math.max(b[5], tb[5]) - Math.min(b[2], tb[2]));
        join = gap <= gapMax && u <= capMax;
      }
      if (join) {
        for (let a = 0; a < 3; a++) { if (tb[a] < b[a]) b[a] = tb[a]; if (tb[a + 3] > b[a + 3]) b[a + 3] = tb[a + 3]; }
      } else close(t);
    }
    if (!cur) cur = { t0: t, box: tb.slice() };
    lastPart = part;
  }
  close(nt);

  // Corners and middles, from the distinct vertices of each piece.
  const seen = new Int32Array(nv).fill(-1);
  pieces.forEach((p, i) => {
    let n = 0, cx = 0, cy = 0, cz = 0;
    for (let t = p.t0; t < p.t1; t++) for (let k = 0; k < 3; k++) {
      const v = vertexOf(idx, t, k);
      if (seen[v] === i) continue;
      seen[v] = i;
      n++; cx += pos[v * 3]; cy += pos[v * 3 + 1]; cz += pos[v * 3 + 2];
    }
    p.n = n;
    p.c = n ? [cx / n, cy / n, cz / n] : [0, 0, 0];
  });
  const pdiag = pieces.map((p) => Math.hypot(p.hi[0] - p.lo[0], p.hi[1] - p.lo[1], p.hi[2] - p.lo[2])).sort((a, b) => a - b);
  const pmed = pdiag[pdiag.length >> 1] || 0;

  // GROUPS: consecutive pieces, joined the same way with room three times wider. Built from the
  // pieces rather than walked again, so a group boundary is always a piece boundary and a group
  // is exactly a set of whole pieces. rot-rush's "BOOST" sign is one piece a letter and one group.
  const groups: PieceGroup[] = [];
  const gGap = (opts.groupGap ?? 1.2) * median, gCap = (opts.groupCap ?? 24) * median;
  let g: { first: number; box: number[] } | null = null;
  pieces.forEach((p, i) => {
    const pb = [p.lo[0], p.lo[1], p.lo[2], p.hi[0], p.hi[1], p.hi[2]];
    if (g) {
      const b = g.box;
      let gap = 0;
      for (let a = 0; a < 3; a++) gap = Math.max(gap, pb[a] - b[a + 3], b[a] - pb[a + 3]);
      const u = Math.hypot(Math.max(b[3], pb[3]) - Math.min(b[0], pb[0]), Math.max(b[4], pb[4]) - Math.min(b[1], pb[1]), Math.max(b[5], pb[5]) - Math.min(b[2], pb[2]));
      if (gap <= gGap && u <= gCap) {
        for (let a = 0; a < 3; a++) { b[a] = Math.min(b[a], pb[a]); b[a + 3] = Math.max(b[a + 3], pb[a + 3]); }
        return;
      }
      groups.push({ first: g.first, last: i - 1 });
    }
    g = { first: i, box: pb.slice() };
  });
  if (g) groups.push({ first: (g as { first: number }).first, last: pieces.length - 1 });
  return { pieces, groups, median, diag, merged: pieces.length >= 6 && diag >= 3 * Math.max(pmed, 1e-9) };
}

/** The group a piece belongs to, as the list of its pieces. */
export function groupOf(map: PieceMap, piece: Piece): Piece[] {
  const i = map.pieces.indexOf(piece);
  if (i < 0) return [piece];
  for (const g of map.groups) if (i >= g.first && i <= g.last) return map.pieces.slice(g.first, g.last + 1);
  return [piece];
}

/** The piece that holds triangle `t`, by binary search on the ranges. */
export function pieceAt(map: PieceMap, t: number): Piece | null {
  const ps = map.pieces;
  let lo = 0, hi = ps.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const p = ps[mid];
    if (t < p.t0) hi = mid - 1;
    else if (t >= p.t1) lo = mid + 1;
    else return p;
  }
  return null;
}

/** The distinct corners of a triangle range, in first-seen order. */
export function pieceVertices(idx: ArrayLike<number> | null, t0: number, t1: number): number[] {
  const out: number[] = [];
  const seen = new Set<number>();
  for (let t = t0; t < t1; t++) for (let k = 0; k < 3; k++) {
    const v = vertexOf(idx, t, k);
    if (!seen.has(v)) { seen.add(v); out.push(v); }
  }
  return out;
}

/** three's own XYZ euler as a rotation matrix, row-major 3x3, so no engine is needed here. */
function rotationXYZ(x: number, y: number, z: number): number[] {
  const a = Math.cos(x), b = Math.sin(x), c = Math.cos(y), d = Math.sin(y), e = Math.cos(z), f = Math.sin(z);
  const ae = a * e, af = a * f, be = b * e, bf = b * f;
  return [
    c * e, -c * f, d,
    af + be * d, ae - bf * d, -b * c,
    bf - ae * d, be + af * d, a * c,
  ];
}

export interface PieceMove {
  pos?: [number, number, number];
  rot?: [number, number, number];
  scale?: [number, number, number];
  hidden?: boolean;
  piece?: { c: [number, number, number]; n: number };
}

/**
 * Move one piece of a mesh, in the mesh's own frame, from the REST arrays (what the code built) into
 * the live ones: v' = R·S·(v − c) + pos, normals by R·S⁻¹. From rest every time, so applying the
 * same sidecar twice moves nothing twice. A hidden piece collapses onto its middle.
 *
 * Refuses, and says why, when the piece is not the one the edit was made on: a different corner
 * count, or a middle more than a hundredth of the mesh away. That is what a rebuilt level looks
 * like, and moving whatever now occupies those triangles would be the worst possible answer.
 */
export function movePiece(rest: { pos: ArrayLike<number>; nor?: ArrayLike<number> | null; idx: ArrayLike<number> | null },
                          live: { pos: { [i: number]: number; length: number }; nor?: { [i: number]: number; length: number } | null },
                          t0: number, t1: number, o: PieceMove): { moved: number; error: string } {
  const verts = pieceVertices(rest.idx, t0, t1);
  if (!verts.length) return { moved: 0, error: "no triangles in that range" };
  const nv = rest.pos.length / 3;
  let cx = 0, cy = 0, cz = 0;
  for (const v of verts) {
    if (v >= nv) return { moved: 0, error: "the range points past the mesh" };
    cx += rest.pos[v * 3]; cy += rest.pos[v * 3 + 1]; cz += rest.pos[v * 3 + 2];
  }
  cx /= verts.length; cy /= verts.length; cz /= verts.length;
  const c = o.piece?.c || [cx, cy, cz];
  if (o.piece) {
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < nv; i++) for (let a = 0; a < 3; a++) {
      const x = rest.pos[i * 3 + a];
      if (x < lo[a]) lo[a] = x;
      if (x > hi[a]) hi[a] = x;
    }
    const tol = Math.max(1e-4, Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) * 0.01);
    if (o.piece.n !== verts.length) return { moved: 0, error: "that piece has " + verts.length + " corners now, not " + o.piece.n + " - the mesh was rebuilt" };
    if (Math.hypot(cx - c[0], cy - c[1], cz - c[2]) > tol) return { moved: 0, error: "that piece is somewhere else now - the mesh was rebuilt" };
  }
  const p = o.pos || c;
  const r = o.rot || [0, 0, 0];
  const s = o.hidden ? [0, 0, 0] : o.scale || [1, 1, 1];
  const R = rotationXYZ(r[0], r[1], r[2]);
  const inv = [s[0] ? 1 / s[0] : 0, s[1] ? 1 / s[1] : 0, s[2] ? 1 / s[2] : 0];
  for (const v of verts) {
    const x = (rest.pos[v * 3] - c[0]) * s[0], y = (rest.pos[v * 3 + 1] - c[1]) * s[1], z = (rest.pos[v * 3 + 2] - c[2]) * s[2];
    live.pos[v * 3] = R[0] * x + R[1] * y + R[2] * z + p[0];
    live.pos[v * 3 + 1] = R[3] * x + R[4] * y + R[5] * z + p[1];
    live.pos[v * 3 + 2] = R[6] * x + R[7] * y + R[8] * z + p[2];
    if (rest.nor && live.nor) {
      const nx = rest.nor[v * 3] * inv[0], ny = rest.nor[v * 3 + 1] * inv[1], nz = rest.nor[v * 3 + 2] * inv[2];
      const qx = R[0] * nx + R[1] * ny + R[2] * nz, qy = R[3] * nx + R[4] * ny + R[5] * nz, qz = R[6] * nx + R[7] * ny + R[8] * nz;
      const l = Math.hypot(qx, qy, qz) || 1;
      live.nor[v * 3] = qx / l; live.nor[v * 3 + 1] = qy / l; live.nor[v * 3 + 2] = qz / l;
    }
  }
  return { moved: verts.length, error: "" };
}

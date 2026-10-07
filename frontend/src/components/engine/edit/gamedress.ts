/**
 * THE GAME'S OWN VERSION OF A MODEL FILE.
 *
 * A game rarely draws a model file the way the file is. rot-rush ships thirty brainrot GLBs with no
 * material in them at all: the game paints every one with one shared palette sheet in code, shows
 * ONE of the variants each file carries (`nodes[0]` of its table entry), and fits it to 1.55 m. The
 * file placed as it was came out a grey figure of four overlapping variants, and a model authored
 * in centimetres came out 386 m tall.
 *
 * The live mirror already holds the game's version: the snapshot records which file every mesh was
 * built from (`userData.pcSrc`). So a placed file becomes, best first:
 *   1. a COPY of one the game drew — its paint, its variant and its size, exactly;
 *   2. the file PAINTED the way the game paints the other models of its folder, showing the node
 *      the game's own table names, and sized like those models;
 *   3. the file as it is, showing the node the table names.
 *
 * Pure functions over three objects, so the tests run them on fakes.
 */

/** A model file's name: no folder, no query, no extension, lower case. */
export function modelStem(url: string): string {
  const base = String(url || "").split(/[?#]/)[0].replace(/\\/g, "/").split("/").pop() || "";
  return base.replace(/\.(glb|gltf|fbx|obj)$/i, "").toLowerCase();
}

/** The name of the folder a model file sits in. */
export function modelFolder(url: string): string {
  const parts = String(url || "").split(/[?#]/)[0].replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.length >= 2 ? parts[parts.length - 2].toLowerCase() : "";
}

/** One model, whether the game names it by the path it was served from or a bundler renamed it
 *  with a hash: "labubu-Dk3a9fQ1.glb" is "labubu.glb". A hash is eight or more characters after a
 *  dash or a dot, with a digit or both cases in it — "labubu_golden" and "labubu-golden" are
 *  other files. */
export function sameModel(gameUrl: string, file: string): boolean {
  const a = modelStem(gameUrl), b = modelStem(file);
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.length <= b.length || !a.startsWith(b)) return false;
  const raw = (String(gameUrl).split(/[?#]/)[0].replace(/\\/g, "/").split("/").pop() || "").replace(/\.(glb|gltf|fbx|obj)$/i, "");
  const tail = raw.slice(b.length);
  if (!/^[-.][A-Za-z0-9_-]{8,24}$/.test(tail)) return false;
  const h = tail.slice(1);
  return /\d/.test(h) || (/[a-z]/.test(h) && /[A-Z]/.test(h));
}

/** A node name the way three's glTF loader keeps it: spaces to underscores, and the characters a
 *  property path reserves taken out. PlayCanvas keeps "Body.001"; three calls the same node
 *  "Body001", and without this the two never match. */
export function nodeName(s: string): string {
  return String(s || "").replace(/\s/g, "_").replace(/[[\]./:]/g, "");
}

const shown = (o: any, stop: any = null): boolean => {
  for (let p = o; p && p !== stop; p = p.parent) if (p.visible === false) return false;
  return true;
};

/** Culled by the game and shown by the editor: its paint is right, it is only far away. */
const culled = (o: any): boolean => {
  for (let p = o; p; p = p.parent) if (p.userData?.revealed) return true;
  return false;
};

const inside = (o: any, top: any): boolean => {
  for (let p = o.parent; p; p = p.parent) if (p === top) return true;
  return false;
};

/** Put there by the editor, not built by the game. A copy of a copy is not the game's version. */
const placedByEditor = (o: any): boolean => {
  for (let p = o; p; p = p.parent) if (p.userData?.studioPlaced) return true;
  return false;
};

/** The shape of a loaded model file, for finding the game's copies of it: how many levels each
 *  node sits under the top of a copy, and every name in it. PlayCanvas makes a copy's top the
 *  file's one root node, or a holder when the file has several — and three's scene group stands
 *  where that holder does. */
export interface FileShape { depth: Map<string, number>; names: Set<string> }

export function shapeOf(model: any): FileShape {
  const depth = new Map<string, number>();
  const names = new Set<string>(["Untitled"]);
  const single = (model?.children?.length || 0) === 1;
  model?.traverse?.((o: any) => {
    if (o === model) return;
    const n = nodeName(o.name);
    if (!n) return;
    names.add(n);
    let levels = 0;
    for (let p = o.parent; p && p !== model; p = p.parent) levels++;
    if (!depth.has(n)) depth.set(n, single ? levels : levels + 1);
  });
  return { depth, names };
}

/**
 * Every copy of `file` the game has built, by its top node. From each node the game made from the
 * file it climbs as many levels as that node sits under the file's top — exact, so a game holder
 * that happens to share a bone's name ("Root") is never swallowed. A node the file does not name
 * climbs while the parent is one the file does. The editor's own placements are not the game's.
 */
export function gameCopiesOf(root: any, file: string, shape: FileShape): any[] {
  const tops: any[] = [];
  root?.traverse?.((o: any) => {
    const src = o.userData?.pcSrc;
    if (!src || !sameModel(src, file) || placedByEditor(o)) return;
    let top = o;
    const d = shape.depth.get(nodeName(o.name));
    if (d !== undefined) {
      for (let i = 0; i < d && top.parent && top.parent !== root; i++) top = top.parent;
    } else {
      while (top.parent && top.parent !== root && shape.names.has(nodeName(top.parent.name))) top = top.parent;
    }
    if (!tops.includes(top)) tops.push(top);
  });
  return tops.filter((t) => !tops.some((u) => u !== t && inside(t, u)));
}

/** The names of the meshes a copy shows. */
function shownNames(top: any): string[] {
  const out: string[] = [];
  top.traverse((o: any) => {
    if (o.userData?.pcSrc && shown(o, top.parent)) out.push(nodeName(o.name));
  });
  return out;
}

/**
 * Which copy to take. The one showing the node the game's table names, when a table named one —
 * a file that holds three characters has three kinds of copy — then one the game is drawing now
 * over one it culled for distance, then the first. A copy the game keeps switched off (a twin, a
 * pooled spare) is never taken: nothing says its paint is the one on screen.
 */
export function pickCopy(copies: any[], nodes: string[] = []): any | null {
  const want = nodes.map(nodeName).filter(Boolean);
  const live = copies.filter((c) => shown(c) && shownNames(c).length);
  const named = want.length ? live.filter((c) => shownNames(c).some((n) => want.includes(n))) : live;
  if (want.length && !named.length) return null;
  return named.find((c) => !culled(c)) || named[0] || null;
}

/**
 * A copy of what the game drew, as a placement: the parts the game has switched off left out, its
 * world size and turn kept, and its feet on the origin, so it stands wherever it is put.
 */
export function copyAsPlacement(T: any, top: any): any {
  // The parents too: its size in the game is theirs as much as its own.
  top.updateWorldMatrix(true, true);
  const copy = top.clone(true);
  const off: any[] = [];
  copy.traverse((o: any) => { if (o !== copy && o.visible === false) off.push(o); });
  for (const o of off) o.parent?.remove(o);
  copy.traverse((o: any) => {
    o.visible = true;
    if (o.userData) { delete o.userData.revealed; delete o.userData.gameHidden; }
  });
  top.matrixWorld.decompose(copy.position, copy.quaternion, copy.scale);
  return standOnOrigin(T, copy);
}

/** The box of what is shown: a hidden variant parked five metres to the side is not the model. */
function shownBox(T: any, obj: any): any {
  const box = new T.Box3();
  obj.updateMatrixWorld(true);
  obj.traverse((m: any) => { if (m.isMesh && shown(m, obj.parent)) box.expandByObject(m); });
  return box;
}

/** Hang `obj` in a new holder with the bottom of what it shows on the holder's origin, centred. */
export function standOnOrigin(T: any, obj: any): any {
  const holder = new T.Group();
  holder.add(obj);
  holder.updateMatrixWorld(true);
  const box = shownBox(T, obj);
  if (!box.isEmpty()) {
    obj.position.x -= (box.min.x + box.max.x) / 2;
    obj.position.y -= box.min.y;
    obj.position.z -= (box.min.z + box.max.z) / 2;
  }
  return holder;
}

/**
 * Show only what a table entry names: its first node — the one the game draws, the variants after
 * it being repaints — and its extra parts. Every other mesh of the file is hidden. Returns how many
 * meshes stay shown; nothing is changed when none of the names is in the file.
 */
export function keepNodes(model: any, nodes: string[] = [], parts: string[] = []): number {
  const first = nodes.map(nodeName).filter(Boolean)[0];
  if (!first) return -1;
  const keep = new Set([first, ...parts.map(nodeName).filter(Boolean)]);
  const has = (o: any) => { for (let p = o; p && p !== model; p = p.parent) if (keep.has(nodeName(p.name))) return true; return false; };
  const meshes: any[] = [];
  model.traverse((o: any) => { if (o.isMesh) meshes.push(o); });
  if (!meshes.some(has)) return -1;
  let n = 0;
  for (const m of meshes) { m.visible = has(m); if (m.visible) n++; }
  return n;
}

/** three's glTF loader gives a primitive with no material a white, fully metal, fully rough one —
 *  which reads as dark grey. That is the mark of a file with no paint of its own. */
export function unpainted(mat: any): boolean {
  if (!mat || mat.map || mat.name) return false;
  const c = mat.color;
  return mat.metalness === 1 && mat.roughness === 1 && !!c && c.r === 1 && c.g === 1 && c.b === 1;
}

const median = (a: number[]): number => {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  return s[s.length >> 1];
};

/**
 * How the game paints the models of `file`'s folder: the one picture most of them wear, on the
 * plainest material that wears it, and their median height and width. Null unless at least two
 * other files of that folder share it, because one file's paint is that file's, not the folder's.
 *
 * BY PICTURE, NOT BY MATERIAL. The mirror makes a material per mesh — a game's per-instance tint
 * can differ on every one — but a picture exactly once, so the palette sheet is the one thing all
 * thirty brainrots of rot-rush provably share.
 */
export function folderPaint(T: any, root: any, file: string): { material: any; height: number; width: number; files: number } | null {
  const folder = modelFolder(file);
  if (!folder) return null;
  root?.updateMatrixWorld?.(true);
  const byMap = new Map<any, { files: Set<string>; mats: any[] }>();
  const heights: number[] = [];
  const widths: number[] = [];
  root?.traverse?.((o: any) => {
    const src = o.userData?.pcSrc;
    if (!src || modelFolder(src) !== folder || sameModel(src, file) || !shown(o) || placedByEditor(o)) return;
    let drew = false;
    o.traverse((m: any) => {
      if (!m.isMesh || (m !== o && m.userData?.pcSrc)) return;
      const mat = Array.isArray(m.material) ? m.material[0] : m.material;
      if (!mat?.map) return;
      let e = byMap.get(mat.map);
      if (!e) { e = { files: new Set(), mats: [] }; byMap.set(mat.map, e); }
      e.files.add(modelStem(src));
      if (!e.mats.includes(mat)) e.mats.push(mat);
      drew = true;
    });
    if (!drew) return;
    const s = new T.Box3().setFromObject(o).getSize(new T.Vector3());
    if (s.y > 0) { heights.push(s.y); widths.push(Math.max(s.x, s.z)); }
  });
  let best: { files: Set<string>; mats: any[] } | null = null;
  for (const e of byMap.values()) if (!best || e.files.size > best.files.size) best = e;
  if (!best || best.files.size < 2) return null;
  // The plainest wearer: the one whose colour is nearest white, so a rarity tint is not copied.
  const tint = (m: any) => (m.color ? Math.abs(1 - m.color.r) + Math.abs(1 - m.color.g) + Math.abs(1 - m.color.b) : 3);
  const material = best.mats.slice().sort((a, b) => tint(a) - tint(b))[0];
  return { material, height: median(heights), width: median(widths), files: best.files.size };
}

/** Paint a loaded file the way its folder is painted, and size it like its folder: inside the
 *  median height and width, the way a game fits a roster to one pedestal. */
export function paintLikeFolder(T: any, model: any, paint: { material: any; height: number; width: number }): any {
  let painted = 0;
  model.traverse((m: any) => {
    if (!m.isMesh) return;
    if (Array.isArray(m.material)) m.material = m.material.map((x: any) => (unpainted(x) ? (painted++, paint.material) : x));
    else if (unpainted(m.material)) { m.material = paint.material; painted++; }
  });
  const box = shownBox(T, model);
  if (!box.isEmpty() && paint.height > 0) {
    const s = box.getSize(new T.Vector3());
    const k = Math.min(paint.height / Math.max(1e-6, s.y), paint.width > 0 ? paint.width / Math.max(1e-6, Math.max(s.x, s.z)) : Infinity);
    if (Number.isFinite(k) && k > 0) model.scale.multiplyScalar(k);
  }
  return { painted };
}

export interface Dressed { object: any; how: "copy" | "painted" | "file"; note: string }

/**
 * The best version of a loaded model file for a placement in a mirrored game, and one sentence
 * saying which it is. `root` is the mirror; `nodes`/`parts` the table entry's, when it came from
 * one.
 */
export function dressForGame(T: any, root: any, file: string, model: any, nodes: string[] = [], parts: string[] = []): Dressed {
  const copies = root ? gameCopiesOf(root, file, shapeOf(model)) : [];
  const top = pickCopy(copies, nodes);
  if (top) return { object: copyAsPlacement(T, top), how: "copy", note: "a copy of the one the game draws, with its paint and size" };
  keepNodes(model, nodes, parts);
  const paint = root ? folderPaint(T, root, file) : null;
  let bare = false;
  model.traverse((m: any) => { if (m.isMesh && (Array.isArray(m.material) ? m.material.some(unpainted) : unpainted(m.material))) bare = true; });
  if (paint && bare) {
    paintLikeFolder(T, model, paint);
    return { object: standOnOrigin(T, model), how: "painted", note: `the game has not drawn this one yet: painted like the other ${paint.files} models in its folder` };
  }
  return { object: model, how: "file", note: bare && root ? "this file has no paint of its own, and the game has not drawn it yet" : "" };
}

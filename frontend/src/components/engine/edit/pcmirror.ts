// A PlayCanvas snapshot as a three.js tree, for the editor to hold.
//
// The mirror is faithful where it matters to editing — every entity is a node with the same name,
// transform and children, every mesh has the same vertices — and approximate where it cannot be:
// a PlayCanvas StandardMaterial becomes a MeshStandardMaterial (lit) or a MeshBasicMaterial
// (unlit) with the same colours, maps, glow and blending, which is close but not the game's own
// render. The Asset tab still shows PlayCanvas drawing it; this tab is for changing it.
//
// Two shapes are chosen for the outliner's sake. An entity that holds ONE mesh at its own node
// becomes a Mesh, so the entity is what gets picked. An entity that is only a light, or only a
// camera, becomes that light or camera, so the panels find it. Everything else is a Group.

import type { PcSnapEntity, PcSnapGeom, PcSnapMap, PcSnapMaterial, PcSnapMesh, PcSnapshot, PcSnapTexture } from "./ops";
import { unpackArr } from "./ops";
// THE SHADER AND ITS GENERATOR LIVE WITH THE EMITTER.
//
// `pcSplatSource` writes the splat material into the file `emitBuilder` produces, and it used to
// be appended by the editor's Emit button at the call site — so `emitBuilder` called any other
// way (the HTTP `code` action, a test, a script) produced a PlayCanvas file with a flat material.
// One function, two different outputs depending on the caller. It cannot be in both places: two
// `export function <fn>Material` in one emitted file is a SyntaxError.
//
// Both moved to `terrain.ts`, which imports nothing and still does. The live material below uses
// the same `PC_SPLAT_CHUNK` string, so the editor's viewport and the emitted game file cannot
// drift apart. Re-exported because three files import them from here.
import { PC_SPLAT_CHUNK, pcSplatSource } from "./terrain";

export { PC_SPLAT_CHUNK, pcSplatSource };

const IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const isIdentity = (m?: number[]) => !m || m.every((v, i) => Math.abs(v - IDENT[i]) < 1e-9);
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/**
 * A colour in three's working space.
 *
 * A material's colour is what a person typed, in sRGB, and PlayCanvas decodes it; so is it decoded
 * here. The old test for "can setRGB take a colour space" was `setRGB.length >= 4`, which is 3 on
 * every three since r152 — a default parameter does not count — so every colour went in undecoded
 * and the mirror was a shade paler than the game. A value the game set per mesh instance is a
 * shader uniform, already linear, and goes in as it is.
 */
function colour(T: any, c: [number, number, number], linear = false): any {
  const col = new T.Color();
  if (!linear && T.SRGBColorSpace) col.setRGB(c[0], c[1], c[2], T.SRGBColorSpace);
  else col.setRGB(c[0], c[1], c[2]);
  return col;
}

const WRAP = (T: any, w?: string) => (w === "clamp" ? T.ClampToEdgeWrapping : w === "mirror" ? T.MirroredRepeatWrapping : T.RepeatWrapping);

/**
 * The snapshot's pictures as three textures, each decoded once however many materials use it.
 *
 * A texture carries its own repeat, offset and colour space in three, and a PlayCanvas material
 * keeps those on the MATERIAL — so one picture drawn with two tilings is two texture objects here,
 * sharing one image. Null everywhere with no document (the tests run in node).
 */
export class PcTextureBank {
  private made = new Map<string, any>();
  private images = new Map<number, HTMLImageElement>();
  constructor(private T: any, private list: PcSnapTexture[]) {}

  private image(i: number): HTMLImageElement | null {
    const src = this.list[i];
    if (!src?.url || typeof document === "undefined" || typeof Image === "undefined") return null;
    let img = this.images.get(i);
    if (!img) { img = new Image(); img.src = src.url; this.images.set(i, img); }
    return img;
  }

  private dress(tex: any, src: PcSnapTexture, map: PcSnapMap, kind: "colour" | "data") {
    const T = this.T;
    // The game uploads without a flip and its UVs expect that; three flips by default.
    tex.flipY = false;
    tex.wrapS = WRAP(T, src.wrapU);
    tex.wrapT = WRAP(T, src.wrapV);
    if (src.nearest) {
      tex.magFilter = T.NearestFilter;
      tex.minFilter = src.mips ? T.NearestMipmapNearestFilter : T.NearestFilter;
      tex.generateMipmaps = !!src.mips;
    } else if (!src.mips) {
      tex.minFilter = T.LinearFilter;
      tex.generateMipmaps = false;
    }
    if (kind === "colour" && src.srgb && T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace;
    else if (T.NoColorSpace !== undefined && "colorSpace" in tex) tex.colorSpace = T.NoColorSpace;
    // PlayCanvas: u' = u·tx + ox, v' = v·ty + (1 − ty − oy). three: uv' = uv·repeat + offset.
    const [tx, ty] = map.tiling || [1, 1];
    const [ox, oy] = map.offset || [0, 0];
    if (tx !== 1 || ty !== 1 || ox !== 0 || oy !== 0) {
      tex.repeat.set(tx, ty);
      tex.offset.set(ox, 1 - ty - oy);
    }
    if (map.uv === 1 && "channel" in tex) tex.channel = 1;
  }

  /** The texture for a colour, glow or normal slot. */
  get(map: PcSnapMap | undefined, kind: "colour" | "data" = "colour"): any {
    if (!map) return null;
    const src = this.list[map.tex];
    const img = this.image(map.tex);
    if (!src || !img) return null;
    const key = [map.tex, kind, map.tiling || "", map.offset || "", map.uv || 0].join("|");
    const hit = this.made.get(key);
    if (hit) return hit;
    const T = this.T;
    const tex = new T.Texture(img);
    this.dress(tex, src, map, kind);
    const ready = () => { tex.needsUpdate = true; };
    if (img.complete && img.naturalWidth) ready(); else img.addEventListener("load", ready, { once: true });
    this.made.set(key, tex);
    return tex;
  }

  /**
   * An opacity map as three reads one: the GREEN channel. PlayCanvas reads whichever channel the
   * material names — the alpha of a painted canvas, nearly always — so the named channel is copied
   * into all three once the picture has decoded. Green is the picture itself.
   */
  alpha(map: PcSnapMap): any {
    const ch = map.ch || "a";
    if (ch === "g") return this.get(map, "data");
    const src = this.list[map.tex];
    const img = this.image(map.tex);
    if (!src || !img) return null;
    const key = [map.tex, "alpha", ch, map.tiling || "", map.offset || "", map.uv || 0].join("|");
    const hit = this.made.get(key);
    if (hit) return hit;
    const T = this.T;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const tex = new T.CanvasTexture(canvas);
    this.dress(tex, src, map, "data");
    const at = ({ r: 0, g: 1, b: 2, a: 3 } as Record<string, number>)[ch] ?? 3;
    const fill = () => {
      try {
        canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
        const g = canvas.getContext("2d")!;
        g.drawImage(img, 0, 0);
        const d = g.getImageData(0, 0, canvas.width, canvas.height);
        const px = d.data;
        for (let i = 0; i < px.length; i += 4) { const v = px[i + at]; px[i] = v; px[i + 1] = v; px[i + 2] = v; px[i + 3] = 255; }
        g.putImageData(d, 0, 0);
        tex.needsUpdate = true;
      } catch { /* a picture that will not read back leaves the surface opaque */ }
    };
    if (img.complete && img.naturalWidth) fill(); else img.addEventListener("load", fill, { once: true });
    this.made.set(key, tex);
    return tex;
  }
}

/**
 * The vertex colour as PlayCanvas uses it on a LIT material: on the glow as well as, or instead
 * of, the albedo. three has one switch, for the albedo, so the glow's is added to the shader —
 * after the emissive map, the same place the game multiplies it in. The cache key keeps three from
 * handing this program to a material that was never patched.
 */
function patchVertexColour(mat: any, keepDiffuse: boolean) {
  mat.vertexColors = true;
  mat.onBeforeCompile = (sh: any) => {
    let fs: string = sh.fragmentShader;
    if (!keepDiffuse) fs = fs.replace("#include <color_fragment>", "");
    fs = fs.replace("#include <emissivemap_fragment>",
      "#include <emissivemap_fragment>\n#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )\n\ttotalEmissiveRadiance *= vColor.rgb;\n#endif");
    sh.fragmentShader = fs;
  };
  mat.customProgramCacheKey = () => "pc-vertex-colour-" + (keepDiffuse ? "both" : "glow");
}

/**
 * One PlayCanvas material, for one mesh instance, as a three material.
 *
 * UNLIT is emissive plus the albedo under the ambient, and every unlit material a game writes
 * leans on one of the two: a sign is a black albedo and a painted glow, a shadow blob a black
 * albedo and a mask. The mirror draws the term that carries the picture, with no lights at all —
 * which is also why a sign now reads at the colour its canvas was painted.
 */
function material(T: any, m: PcSnapMaterial, hasColors: boolean, bank: PcTextureBank | null): any {
  const legacy = m.vcDiffuse === undefined && m.vcEmissive === undefined;
  const vcD = legacy ? hasColors : !!m.vcDiffuse && hasColors;
  const vcE = !legacy && !!m.vcEmissive && hasColors;
  const opts: any = { side: m.doubleSided ? T.DoubleSide : m.backSide ? T.BackSide : T.FrontSide };
  const emissive = m.emissive ? colour(T, m.emissive, !!m.emissiveLinear) : null;
  const k = m.emissiveIntensity ?? 1;
  const glows = !!emissive && (emissive.r + emissive.g + emissive.b) * k > 1e-4;
  const albedo = colour(T, m.color, !!m.colorLinear);
  let colourMap: PcSnapMap | undefined;
  let mat: any;
  if (m.unlit) {
    const dark = albedo.r + albedo.g + albedo.b < 1e-4;
    if (glows || (dark && m.emissiveMap)) {
      opts.color = emissive ? emissive.clone().multiplyScalar(k) : new T.Color(0, 0, 0);
      colourMap = m.emissiveMap;
      opts.vertexColors = vcE;
    } else {
      opts.color = albedo;
      colourMap = m.diffuseMap;
      opts.vertexColors = vcD;
    }
    mat = new T.MeshBasicMaterial(opts);
  } else {
    opts.color = albedo;
    opts.vertexColors = vcD;
    opts.metalness = clamp01(m.metalness);
    opts.roughness = clamp01(m.roughness);
    if (emissive) { opts.emissive = emissive; opts.emissiveIntensity = k; }
    colourMap = m.diffuseMap;
    mat = new T.MeshStandardMaterial(opts);
    const em = bank?.get(m.emissiveMap);
    if (em) mat.emissiveMap = em;
    const nm = bank?.get(m.normalMap, "data");
    if (nm) mat.normalMap = nm;
    if (vcE) patchVertexColour(mat, vcD);
  }
  const map = bank?.get(colourMap);
  if (map) mat.map = map;
  else if (!colourMap && m.map && typeof document !== "undefined" && T.TextureLoader) {
    // The first snapshot format: the diffuse map inline, as a data URL.
    try {
      const tex = new T.TextureLoader().load(m.map);
      if (T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace;
      tex.flipY = false;
      mat.map = tex;
    } catch { /* a texture that will not decode is a flat colour */ }
  }
  if (m.transparent) {
    mat.transparent = true;
    mat.opacity = m.opacity ?? 1;
    if (m.blend === "additive") mat.blending = T.AdditiveBlending;
    else if (m.blend === "multiply") { mat.blending = T.MultiplyBlending; mat.premultipliedAlpha = true; }
    else if (m.blend === "premultiplied") mat.premultipliedAlpha = true;
  } else if (legacy && m.opacity !== undefined && m.opacity < 1) {
    mat.transparent = true;
    mat.opacity = m.opacity;
  }
  if (m.alphaTest) mat.alphaTest = m.alphaTest;
  if ((m.transparent || m.alphaTest) && m.opacityMap && bank) {
    // The same picture as the colour, read by its alpha: `map` already multiplies that in, and a
    // second copy would square it. Anything else goes through a mask.
    const same = !!colourMap && colourMap.tex === m.opacityMap.tex && (m.opacityMap.ch || "a") === "a"
      && String(colourMap.tiling || "") === String(m.opacityMap.tiling || "")
      && String(colourMap.offset || "") === String(m.opacityMap.offset || "");
    if (!same) { const a = bank.alpha(m.opacityMap); if (a) mat.alphaMap = a; }
  }
  if (m.depthWrite === false) mat.depthWrite = false;
  if (m.fog === false) mat.fog = false;
  if (m.name) mat.name = m.name;
  return mat;
}

/** Typed arrays for one geometry record, decoded once however many meshes draw it. */
interface GeomArrays {
  pos: Float32Array; nor: Float32Array | null; uv: Float32Array | null; uv1: Float32Array | null;
  col: Float32Array | null; colSize: number; idx: Uint16Array | Uint32Array | null;
}

function arraysOf(g: PcSnapGeom | PcSnapMesh): GeomArrays | null {
  const pos = unpackArr(g.positions as any) as Float32Array | null;
  if (!pos || !pos.length) return null;
  const nor = unpackArr(g.normals as any) as Float32Array | null;
  const col = unpackArr(g.colors as any) as Float32Array | null;
  const size = (g as PcSnapGeom).colorSize || (col ? Math.round(col.length / (pos.length / 3)) : 0);
  return {
    pos,
    nor: nor && nor.length === pos.length ? nor : null,
    uv: unpackArr(g.uvs as any) as Float32Array | null,
    uv1: unpackArr((g as PcSnapGeom).uvs1) as Float32Array | null,
    col: col && (size === 3 || size === 4) ? col : null,
    colSize: size,
    idx: unpackArr(g.indices as any, true) as Uint16Array | Uint32Array | null,
  };
}

/** A geometry of its own for one mesh. The arrays are COPIED: the editor moves vertices in place,
 *  and two entities that happened to share a pc.Mesh must not move together. */
function geometry(T: any, a: GeomArrays): any {
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.BufferAttribute(a.pos.slice(), 3));
  if (a.nor) g.setAttribute("normal", new T.BufferAttribute(a.nor.slice(), 3));
  if (a.uv) g.setAttribute("uv", new T.BufferAttribute(a.uv.slice(), 2));
  if (a.uv1) g.setAttribute("uv1", new T.BufferAttribute(a.uv1.slice(), 2));
  if (a.col) g.setAttribute("color", new T.BufferAttribute(a.col.slice(), a.colSize));
  if (a.idx) g.setIndex(new T.BufferAttribute(a.idx.slice(), 1));
  if (!a.nor) g.computeVertexNormals();
  g.computeBoundingSphere();
  return g;
}

interface MirrorCtx { T: any; snap: PcSnapshot; bank: PcTextureBank | null; geo: Map<number, GeomArrays | null>; aspect: number }

function mesh(c: MirrorCtx, m: PcSnapMesh): any {
  const T = c.T;
  let a: GeomArrays | null = null;
  if (typeof m.geo === "number") {
    if (!c.geo.has(m.geo)) c.geo.set(m.geo, c.snap.geoms?.[m.geo] ? arraysOf(c.snap.geoms[m.geo]) : null);
    a = c.geo.get(m.geo) || null;
  } else a = arraysOf(m);
  const g = a ? geometry(T, a) : new T.BufferGeometry();
  const me = new T.Mesh(g, material(T, m.material, !!a?.col, c.bank));
  if (m.skinned) me.userData.pcSkinned = true;
  if (m.local && !isIdentity(m.local)) {
    me.matrix.fromArray(m.local);
    me.matrix.decompose(me.position, me.quaternion, me.scale);
  }
  return me;
}

function light(T: any, l: NonNullable<PcSnapEntity["light"]>): any {
  const c = colour(T, l.color);
  let out: any;
  if (l.type === "point") out = new T.PointLight(c, l.intensity, l.range);
  else if (l.type === "spot") {
    const outer = (l.outer * Math.PI) / 180;
    out = new T.SpotLight(c, l.intensity, l.range, outer, l.outer > 0 ? Math.min(1, Math.max(0, 1 - l.inner / l.outer)) : 0);
  } else out = new T.DirectionalLight(c, l.intensity);
  out.castShadow = l.shadows;
  if (out.target) {
    // PlayCanvas shines down the entity's -Y; three shines at a target, so hang one there.
    out.target.position.set(0, -1, 0);
    out.target.name = "";
    out.add(out.target);
  }
  return out;
}

function camera(T: any, c: NonNullable<PcSnapEntity["camera"]>, aspect: number): any {
  if (c.ortho) {
    const h = c.orthoHeight;
    return new T.OrthographicCamera(-h * aspect, h * aspect, h, -h, c.near, c.far);
  }
  return new T.PerspectiveCamera(c.fov, aspect, c.near, c.far);
}

function node(c: MirrorCtx, e: PcSnapEntity): any {
  const T = c.T;
  const meshes = e.meshes || [];
  let n: any;
  const onlyLight = e.light && !meshes.length && !e.camera;
  const onlyCamera = e.camera && !meshes.length && !e.light;
  if (meshes.length === 1 && !e.light && !e.camera && isIdentity(meshes[0].local)) n = mesh(c, meshes[0]);
  else if (onlyLight) n = light(T, e.light!);
  else if (onlyCamera) n = camera(T, e.camera!, c.aspect);
  else {
    n = new T.Group();
    meshes.forEach((m, i) => { const me = mesh(c, m); me.name = meshes.length > 1 ? e.name + "#" + i : ""; n.add(me); });
    if (e.light) n.add(light(T, e.light));
    if (e.camera) n.add(camera(T, e.camera, c.aspect));
  }
  n.name = e.name;
  n.position.set(e.pos[0], e.pos[1], e.pos[2]);
  n.quaternion.set(e.rot[0], e.rot[1], e.rot[2], e.rot[3]);
  n.scale.set(e.scale[0], e.scale[1], e.scale[2]);
  n.visible = e.enabled;
  n.userData.playcanvas = true;
  // Switched off BY THE GAME, as opposed to by a person in the editor. A level that culls by
  // distance switches most of itself off; see `revealPlan`.
  if (!e.enabled) n.userData.gameHidden = true;
  if (e.src) n.userData.pcSrc = e.src;
  for (const k of e.children) n.add(node(c, k));
  return n;
}

export interface PcMirror {
  root: any;
  world: { background: any; fog: any; environment: any } | null;
}

export function buildPcMirror(T: any, snap: PcSnapshot, opts: { aspect?: number } = {}): PcMirror {
  const ctx: MirrorCtx = {
    T, snap, aspect: opts.aspect || 16 / 9, geo: new Map(),
    bank: snap.textures?.length ? new PcTextureBank(T, snap.textures) : null,
  };
  const root = node(ctx, snap.root);
  // THE GAME'S OWN CAMERA, marked so the editor opens looking through it — the three mirror has
  // always done this and the PlayCanvas one never did, so a PlayCanvas level opened framed from
  // a kilometre away with every object a dot. The first camera that is switched on, and whose
  // parents are too, is the one drawing the game.
  let gameCam: any = null;
  const on = (o: any) => { for (let p = o; p; p = p.parent) if (p.visible === false) return false; return true; };
  root.traverse((o: any) => { if (!gameCam && o.isCamera && on(o)) gameCam = o; });
  if (gameCam) gameCam.userData.studioGameCamera = true;
  const w = snap.world;
  if (w?.ambient) {
    const amb = new T.AmbientLight(colour(T, w.ambient), 1);
    amb.name = "ambient";
    root.add(amb);
  }
  let world: PcMirror["world"] = null;
  if (w && (w.background || w.fog)) {
    world = { background: w.background ? colour(T, w.background) : null, fog: null, environment: null };
    if (w.fog) {
      const fc = colour(T, w.fog.color);
      world.fog = w.fog.type === "exp2" ? new T.FogExp2(fc, w.fog.density ?? 0.02) : new T.Fog(fc, w.fog.start ?? 1, w.fog.end ?? 100);
    }
  }
  return { root, world };
}

// ------------------------------------------------------------------ the parts the game switched off
//
// A level that culls by distance switches most of itself off: in rot-rush 959 of 1128 entities
// were disabled at the moment of the snapshot, every platform the runner was not standing near,
// so the editor showed three platforms of a thirty-platform line. The same flag also marks things
// that are off for a REASON — the lean twin of a far town while the full one draws, the red and
// the green version of one sign, sixty pooled sparks parked at the origin — and showing those
// stacks two objects in one place. So the rule separates the two.

export interface RevealPlan {
  /** Switched off by the game, and shown anyway: parts of the level it culled by distance. */
  reveal: any[];
  /** Switched off, and left off: twins of a visible part, pools, and loose effects. */
  keep: any[];
}

/** Name with its numbers taken out, so `platform-3` and `platform-12` are one kind of thing. */
const kindOf = (name: string) => String(name || "").replace(/\d+/g, "#");

/**
 * Which switched-off parts of a mirrored game to show.
 *
 * A part the game has switched off is shown when it is level content the game culled:
 *   - it is the same KIND as a sibling that is on (platform-3 beside platform-1), or it holds
 *     two or more meshes of its own and sits away from the origin, AND
 *   - it is not a TWIN: its box does not coincide with a sibling that is on (a LOD pair, a state
 *     pair drawn in one place), AND
 *   - it is not POOLED: two more of its kind, also switched off, are not parked on its spot.
 * Checked top down, so a part inside a shown part is judged again on its own.
 */
export function revealPlan(T: any, root: any): RevealPlan {
  const plan: RevealPlan = { reveal: [], keep: [] };
  root.updateMatrixWorld(true);
  const boxes = new Map<any, any>();
  const boxOf = (o: any) => {
    let b = boxes.get(o);
    if (!b) { b = new T.Box3().setFromObject(o); boxes.set(o, b); }
    return b;
  };
  const meshCount = (o: any) => { let n = 0; o.traverse((c: any) => { if (c.isMesh) n++; }); return n; };
  const overlap = (a: any, b: any) => {
    if (a.isEmpty() || b.isEmpty()) return 0;
    const i = a.clone().intersect(b);
    if (i.isEmpty()) return 0;
    const v = (x: any) => { const s = x.getSize(new T.Vector3()); return Math.max(s.x, 1e-3) * Math.max(s.y, 1e-3) * Math.max(s.z, 1e-3); };
    return v(i) / Math.min(v(a), v(b));
  };
  const visit = (parent: any) => {
    const kids: any[] = parent.children || [];
    for (const h of kids) {
      if (!h.userData?.gameHidden) { visit(h); continue; }
      const kind = kindOf(h.name);
      const same = kids.filter((s) => s !== h && kindOf(s.name) === kind);
      const kinOn = same.some((s) => !s.userData?.gameHidden);
      const hb = boxOf(h);
      const empty = hb.isEmpty();
      const centre = empty ? null : hb.getCenter(new T.Vector3());
      const many = meshCount(h) >= 2 && !!centre && centre.length() > 0.5;
      let twin = false;
      if (!empty) for (const s of kids) {
        if (s === h || s.userData?.gameHidden || !s.visible) continue;
        const sb = boxOf(s);
        if (overlap(hb, sb) > 0.6) {
          // Coinciding and alike in size is a twin; a small part inside a big one is not.
          const hs = hb.getSize(new T.Vector3()).length(), ss = sb.getSize(new T.Vector3()).length();
          if (Math.max(hs, ss) / Math.max(1e-6, Math.min(hs, ss)) < 3) { twin = true; break; }
        }
      }
      // Two or more switched-off siblings of its kind parked on the same spot: a pool of effects
      // waiting to be used, not a part of the level.
      let pooled = false;
      if (!empty) {
        let parked = 0;
        for (const s of same) {
          if (!s.userData?.gameHidden) continue;
          const b = boxOf(s);
          if (!b.isEmpty() && b.getCenter(new T.Vector3()).distanceTo(centre!) < 0.01) parked++;
        }
        pooled = parked >= 2;
      }
      if ((kinOn || many) && !twin && !pooled && !empty) {
        h.visible = true;
        h.userData.revealed = true;
        plan.reveal.push(h);
        visit(h);
      } else plan.keep.push(h);
    }
  };
  visit(root);
  return plan;
}

/** Put the revealed parts back the way the game has them. */
export function hideRevealed(root: any): number {
  let n = 0;
  root.traverse((o: any) => { if (o.userData?.revealed) { o.visible = false; delete o.userData.revealed; n++; } });
  return n;
}

/* ===========================================================================================
 * THE PLAYCANVAS SIDE OF THE SPLAT MATERIAL.
 *
 * three has had a real four-layer splat since round one: `TerrainView.makeMaterial` in world.ts
 * hooks `onBeforeCompile` and blends four tiled textures by the per-vertex weights. PlayCanvas
 * had nothing — a PlayCanvas game got `diffuseVertexColor = true`, which is the four layer
 * COLOURS already averaged into one number a vertex: no grain, no tiling, and no way to tell
 * grass from gravel at two metres. This is that shader, for PlayCanvas.
 *
 * THE ONE THING TO GET RIGHT, and it is the trap that shipped a cream field last round:
 *
 *   `MeshArrays.colors`  is the four layer colours ALREADY BLENDED by the weights.
 *   `MeshArrays.weights` is the raw 0..1 weights, four per vertex, summing to about 1.
 *
 * The three viewport puts `colors` in the `color` attribute and `weights` in an attribute of its
 * own, because a three geometry may carry as many attributes as it likes. PlayCanvas's standard
 * material gives a shader exactly ONE four-component vertex attribute for nothing — the colour —
 * and a second means a custom vertex chunk as well. So on this side the colour attribute carries
 * the WEIGHTS and the shader does the blend: the same arithmetic in a different place, and the
 * same pixels. Putting `colors` there instead paints every vertex 63% layer 3, because the
 * blended colour's alpha is a constant 1.0. That is the cream field, in the other direction.
 *
 * `pcTerrainEntity` asks the material which of the two it wants, so the pair cannot be set
 * independently and disagree.
 * ========================================================================================= */

/** What the splat needs from a layer. A subset of `TerrainLayer` on purpose: this is the
 *  PlayCanvas side, and it must not need the terrain engine's types to be usable. */
export interface PcSplatLayer { name?: string; colour?: string; tiling?: number; texture?: string }

export interface PcSplatOpts {
  /** One pc.Texture per layer, or null. A layer with none contributes flat colour. */
  textures?: (any | null)[];
  /** How rough the ground is — 0.96 in the three viewport. PlayCanvas counts the other way, so
   *  this is turned into `gloss` on the way in. */
  roughness?: number;
  /** Multiplied over the whole blend. White leaves it alone. */
  tint?: [number, number, number];
}

/** sRGB hex to linear 0..1 — the same conversion `terrain.ts` does for its vertex colours, so a
 *  layer painted #7d8a6a is one olive in both renderers rather than two. */
export function hexToLinear(hex: string): [number, number, number] {
  const s = String(hex || "#808080").replace("#", "");
  const n = (s.length === 3
    ? parseInt(s[0] + s[0] + s[1] + s[1] + s[2] + s[2], 16)
    : parseInt(s.slice(0, 6).padEnd(6, "0"), 16)) || 0;
  const f = (v: number) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  return [f(((n >> 16) & 255) / 255), f(((n >> 8) & 255) / 255), f((n & 255) / 255)];
}


/**
 * Install a chunk override, whichever PlayCanvas this game is built on.
 *
 * The API moved. Up to 2.6 a material carried a plain object of GLSL strings; from 2.7 it is a
 * per-language Map behind `getShaderChunks`, and the old object survives as a deprecated shim the
 * new pipeline never reads. Both are tried, newest first, and the answer says which took —
 * because a chunk that silently did not install is the difference between four blended layers
 * and a field of raw red, green and blue.
 */
export function pcSetChunk(pc: any, mat: any, name: string, src: string): boolean {
  try {
    const lang = pc?.SHADERLANGUAGE_GLSL || "glsl";
    const chunks = typeof mat.getShaderChunks === "function" ? mat.getShaderChunks(lang) : null;
    if (chunks && typeof chunks.set === "function") { chunks.set(name, src); return true; }
  } catch { /* older engine; fall through */ }
  try {
    if (mat.chunks && typeof mat.chunks === "object") {
      const api = pc?.CHUNKAPI_2_5 || pc?.CHUNKAPI_1_70 || pc?.CHUNKAPI_1_65;
      const next: any = { ...mat.chunks, [name]: src };
      if (api) next.APIVersion = api;
      mat.chunks = next;
      return true;
    }
  } catch { /* no chunk system at all */ }
  return false;
}

/** A 1x1 white pixel. A sampler2D that is declared and never bound renders black on some drivers
 *  and warns every frame on the rest — the reason the three viewport keeps one too. */
function pcWhite(pc: any, device: any): any {
  const tex = new pc.Texture(device, { width: 1, height: 1, mipmaps: false });
  try {
    const px = tex.lock();
    px[0] = 255; px[1] = 255; px[2] = 255; px[3] = 255;
    tex.unlock();
  } catch { /* a device that will not lock still binds something */ }
  return tex;
}

/**
 * The four-layer splat material.
 *
 * `userData.splat` is the contract with `pcTerrainEntity`: true means the mesh must be handed the
 * WEIGHTS as its colour attribute, false means the chunk did not install and it must be handed
 * the blended colours instead. One flag, read in one place.
 */
export function pcSplatMaterial(pc: any, app: any, layers: PcSplatLayer[], opts: PcSplatOpts = {}): any {
  const mat = new pc.StandardMaterial();
  const device = app?.graphicsDevice || app;
  mat.name = "terrain-splat";
  mat.useMetalness = false;
  mat.gloss = Math.max(0, Math.min(1, 1 - (opts.roughness ?? 0.96)));
  const tint = opts.tint || [1, 1, 1];
  try { mat.diffuse?.set?.(tint[0], tint[1], tint[2]); } catch { /* a stub material in a test */ }
  // The varying only exists when the material asks for vertex colour, and the chunk below reads
  // it. Without this the shader compiles against a name that was never declared.
  mat.diffuseVertexColor = true;

  const ok = pcSetChunk(pc, mat, "diffusePS", PC_SPLAT_CHUNK);
  mat.userData = mat.userData || {};
  mat.userData.splat = ok;
  if (!ok) {
    // Degrade to what PlayCanvas had before: the blended vertex colour. Flat and honest, rather
    // than a field of primaries.
    mat.update?.();
    return mat;
  }

  const white = pcWhite(pc, device);
  const has = [0, 0, 0, 0];
  const tile = [8, 8, 8, 8];
  for (let i = 0; i < 4; i++) {
    const l = layers[i] || {};
    const c = hexToLinear(l.colour || "#808080");
    mat.setParameter("uSplatCol" + i, [c[0], c[1], c[2]]);
    const tex = opts.textures?.[i] || null;
    mat.setParameter("uSplatTex" + i, tex || white);
    has[i] = tex ? 1 : 0;
    tile[i] = l.tiling && l.tiling > 0 ? l.tiling : 8;
  }
  mat.setParameter("uSplatHas", has);
  mat.setParameter("uSplatTile", tile);
  mat.userData.white = white;
  mat.update?.();
  return mat;
}

/** Which of the two four-component arrays a splat mesh's colour attribute takes. Exported so the
 *  choice can be checked with no graphics device anywhere near it. */
export function pcSplatColours(m: { colors: Float32Array; weights?: Float32Array }, splat: boolean): Float32Array {
  if (splat && m.weights instanceof Float32Array && m.weights.length >= m.colors.length) return m.weights;
  return m.colors;
}

/**
 * A terrain mesh and its material as one PlayCanvas entity.
 *
 * `m` is whatever `terrainMesh(t)` returned — the same arrays the three viewport draws, the GLB
 * export writes and the emitted builder carries, so the two engines cannot drift.
 */
export function pcTerrainEntity(pc: any, app: any, m: {
  positions: Float32Array; normals: Float32Array; uvs: Float32Array;
  colors: Float32Array; indices: Uint32Array; weights?: Float32Array;
}, layers: PcSplatLayer[], opts: PcSplatOpts & { name?: string; material?: any } = {}): any {
  const mat = opts.material || pcSplatMaterial(pc, app, layers, opts);
  const mesh = new pc.Mesh(app.graphicsDevice);
  mesh.setPositions(m.positions);
  mesh.setNormals(m.normals);
  mesh.setUvs(0, m.uvs);
  mesh.setColors(pcSplatColours(m, !!mat?.userData?.splat), 4);
  mesh.setIndices(m.indices);
  mesh.update(pc.PRIMITIVE_TRIANGLES);
  const mi = new pc.MeshInstance(mesh, mat, new pc.GraphNode());
  mi.receiveShadow = true;
  const entity = new pc.Entity(opts.name || "terrain");
  entity.addComponent("render", { meshInstances: [mi] });
  return entity;
}


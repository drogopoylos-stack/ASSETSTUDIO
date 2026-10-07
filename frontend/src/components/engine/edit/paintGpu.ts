// PAINT ON THE MODEL: the GPU half.
//
// Every painted pixel lives in TEXTURE space. A dab is drawn by rendering the model with its UVs as
// the screen position — each texel of the texture becomes one fragment — and the fragment asks
// where its own surface point is on the real screen (or in 3D, for a sphere dab). If that point is
// under the brush, and nothing is in front of it, the texel gets paint. That is Substance's and
// Blender's projection painting, and it is why a stroke crosses a UV seam without a mark: the two
// sides of the seam are two places in the texture but one line on the screen.
//
// What is kept per texture (a "target"):
//   layers   one render target per asset layer, RGBA8, PREMULTIPLIED, sRGB-encoded values — raw
//            bytes, so what is read back for undo and for the PNG is exactly what was drawn;
//   stroke   the coverage of the stroke in progress, half float, so a 5% airbrush builds up
//            smoothly instead of in 8-bit steps;
//   composite the base texture with every visible layer over it, raw sRGB bytes — what a PNG or
//            a GLB export gets;
//   display  the same image in an sRGB render target, which three samples with hardware decode:
//            this is the texture the materials are given, so the painted model is lit correctly.
//
// A stroke does not touch its layer until it ends: dabs add to `stroke`, the composite shows the
// layer as it WILL be, and the end of the stroke bakes it in once. That is what makes opacity a
// ceiling (a 50% brush crossing itself stays 50%) and what makes one stroke one undo.
//
// The arithmetic in the shaders is paintCore's — `falloff`, `blendChannel`, `compositeOver`,
// `bakeStroke` — written again in GLSL. The unit tests hold the TypeScript; the proof script holds
// this file, by reading pixels back from the real Edit tab.

import {
  BLEND_INDEX, PaintHistory, bytesToBase64, base64ToBytes, capStrokes, clamp, dirtyTiles, encodePNG, hexToRgb,
  addUvTriangles, buildPosGrid, buildUvGrid, layerId, lerpTri, nearestOnGrid, rgbToHex, tileRuns, tipMask, triVertex,
  triangleIslands, uvLocate, uvTriangleSetSignature, type PosGrid, type UvGrid,
  type BlendMode, type BrushSettings, type PaintDoc, type PaintLayerMeta, type PaintRecord, type PaintTargetDoc,
  type PaintTool, type Projection, type StrokeDab, type StrokeRecord, type TileData, type TipKind,
} from "./paintCore";

export interface PaintHost {
  T: any;
  renderer: any;
  camera(): any;
  /** The canvas in CSS pixels — the space pointer events and dab positions are in. */
  viewport(): { w: number; h: number };
  subject(): any;
  /** The frame "mirror" flips across: the asset's own root. */
  mirrorRoot(): any;
  /** Stable keys for meshes, the ones vertex edits use, so a logged stroke finds its mesh again
   *  after the code has rebuilt everything. */
  keyOf(obj: any): string;
  objOf(key: string): any;
  /** Told when materials changed under it, so derived stand-ins (Solid shading) are made again. */
  materialsChanged(): void;
}

export interface PaintHit {
  point: [number, number, number];
  normal: [number, number, number];
  object: any;
  key: string;
  target: string;
  /** Texture coordinates of the point (the mesh UV through the texture's own transform). */
  tex: [number, number];
  face: number;
}

/** A dab as the editor hands it over: a place on the screen, in CSS pixels, y down. */
export interface ScreenDab { x: number; y: number; size: number; alpha: number; angle: number; seed: number }

export interface StrokeOptions {
  tool: PaintTool;
  color: string;
  brush: BrushSettings;
  projection: Projection;
  frontOnly: boolean;
  /** 0 none, 1 X, 2 Y, 3 Z: the axis of the asset's own frame to mirror across. */
  mirror: 0 | 1 | 2 | 3;
}

interface Slot { obj: any; index: number; mat: any }

/** A stroke-log entry an undo step made or took away: a logged stroke, or one that could not be. */
interface LoggedStroke { t: string; s?: StrokeRecord; px?: boolean }

/** A texture's meshes as they were at one moment: what the log's texture points are read against
 *  when the layout next changes. The arrays are copies, so a mesh changed in place cannot move it. */
interface AnchorItem {
  key: string; uv: ArrayLike<number>; pos: ArrayLike<number>; nrm: ArrayLike<number> | null;
  index: ArrayLike<number> | null; count: number; toAsset: any; grid?: UvGrid;
}
interface AnchorSnap { items: AnchorItem[]; xf: number[] }

/** A point on a surface: its mesh (by key), the point and normal in the mesh's frame and in the
 *  asset's, the texture point there, its triangle, and how much the mesh's frame is scaled. */
interface SurfacePoint {
  key: string; local: [number, number, number]; nLocal: [number, number, number];
  asset: [number, number, number]; nAsset: [number, number, number]; u: [number, number] | null; tri: number; sA: number;
}

/** A rebuilt mesh, measured once per redraw. */
interface MeshCache { g: PosGrid; nrm: ArrayLike<number> | null; uv: ArrayLike<number> | null }

interface Saved { map: any; color: number | null; vertexColors: boolean | null }

/** One dab ready for the shader: a screen dab (view) or a sphere in world space. */
interface GpuDab {
  view: boolean;
  x: number; y: number; r: number;          // view: CSS px, y UP, radius in px
  p: any; n: any; R: number;                // sphere: world centre, normal, radius
  alpha: number; angle: number;
  /** A sphere dab redrawn from a screen dab: the world direction it was painted from. */
  vd?: any;
}

class Target {
  base: any = null;
  baseGamma = false;       // the base holds sRGB bytes already (a texture the Studio made)
  srgb = true;
  w = 0;
  h = 0;
  scale = 1;
  channel = 0;
  uvXf: any = null;
  slots: Slot[] = [];
  proxies: any[] = [];
  rts = new Map<string, any>();
  stroke: any = null;
  accA: any = null;
  accB: any = null;
  composite: any = null;
  display: any = null;
  mask: any = null;
  spare: any = null;
  backup: any = null;
  snapshot: any = null;
  madeRT: any = null;
  uvSig = "";
  sharedUV = false;
  dirty = true;
  live = true;
  saved = new Map<any, Saved>();
  strokes: StrokeRecord[] = [];
  unreplayable = 0;
  made: { from: "color" | "vertex"; color: string } | null = null;
  madeKey = "";
  islands = new WeakMap<any, Int32Array>();
  anchor: AnchorSnap | null = null;
  constructor(public id: string, public name: string) {}
}

// ------------------------------------------------------------------ shaders
const PACK = `
vec4 packF(float v) {
  vec4 r = fract(v * vec4(1.0, 255.0, 65025.0, 16581375.0));
  r -= r.yzww * vec4(1.0 / 255.0, 1.0 / 255.0, 1.0 / 255.0, 0.0);
  return r;
}
float unpackF(vec4 r) { return dot(r, vec4(1.0, 1.0 / 255.0, 1.0 / 65025.0, 1.0 / 16581375.0)); }
`;

const BLEND = `
float blendC1(int m, float b, float s) {
  if (m == 1) return b * s;
  if (m == 2) return 1.0 - (1.0 - b) * (1.0 - s);
  if (m == 3) return b < 0.5 ? 2.0 * b * s : 1.0 - 2.0 * (1.0 - b) * (1.0 - s);
  if (m == 4) return min(1.0, b + s);
  if (m == 5) return min(b, s);
  if (m == 6) return max(b, s);
  return s;
}
vec3 blendC(int m, vec3 b, vec3 s) { return vec3(blendC1(m, b.r, s.r), blendC1(m, b.g, s.g), blendC1(m, b.b, s.b)); }
vec4 overS(vec3 cb, float ab, vec3 cs, float as, int m) {
  float ao = as + ab * (1.0 - as);
  if (ao <= 1e-6) return vec4(0.0);
  vec3 mixed = (1.0 - ab) * cs + ab * blendC(m, cb, cs);
  return vec4((as * mixed + ab * cb * (1.0 - as)) / ao, ao);
}
vec4 overP(vec4 L, vec3 cs, float as, int m) {
  vec3 cb = L.a > 1e-6 ? L.rgb / L.a : vec3(0.0);
  vec4 o = overS(cb, L.a, cs, as, m);
  return vec4(o.rgb * o.a, o.a);
}
`;

const QUAD_VS = `
precision highp float;
attribute vec3 position;
varying vec2 vUv;
void main() { vUv = position.xy * 0.5 + 0.5; gl_Position = vec4(position.xy, 0.0, 1.0); }
`;

const rasterVS = (uvName: string, withColor = false) => `
precision highp float;
attribute vec3 position;
attribute vec3 normal;
attribute vec2 ${uvName};
${withColor ? "attribute vec3 color;\nvarying vec3 vCol;" : ""}
uniform mat4 modelMatrix;
uniform mat3 uUvXf;
uniform mat4 uViewProj;
varying vec4 vClip;
varying vec3 vWorld;
varying vec3 vNrm;
varying vec2 vTex;
void main() {
  vec2 t = (uUvXf * vec3(${uvName}, 1.0)).xy;
  vTex = t;
  vec4 w = modelMatrix * vec4(position, 1.0);
  vWorld = w.xyz;
  vNrm = mat3(modelMatrix) * normal;
  vClip = uViewProj * w;
  ${withColor ? "vCol = color;" : ""}
  gl_Position = vec4(t * 2.0 - 1.0, 0.0, 1.0);
}
`;

/** The footprint of up to 16 dabs at this texel. Shared by the coverage pass and the pixel tools. */
const FOOTPRINT = `
varying vec4 vClip;
varying vec3 vWorld;
varying vec3 vNrm;
varying vec2 vTex;
uniform vec2 uViewport;
uniform sampler2D uDepth;
uniform float uFar;
uniform mat4 uView;
uniform vec3 uEye;
uniform vec3 uFwd;
uniform float uOrtho;
uniform float uFrontOnly;
uniform int uCount;
uniform vec4 uA[16];
uniform vec4 uB[16];
uniform vec4 uC[16];
uniform vec4 uD[16];
uniform sampler2D uTip;
uniform float uUseTip;
uniform float uHard;
${PACK}
float falloffF(float r, float h) {
  if (r >= 1.0) return 0.0;
  h = clamp(h, 0.0, 0.999);
  float t = clamp((r - h) / (1.0 - h), 0.0, 1.0);
  return 1.0 - t * t * (3.0 - 2.0 * t);
}
float stampAt(vec2 q, float ang) {
  float c = cos(ang), s = sin(ang);
  vec2 rq = vec2(c * q.x + s * q.y, -s * q.x + c * q.y);
  if (uUseTip > 0.5) {
    if (abs(rq.x) >= 1.0 || abs(rq.y) >= 1.0) return 0.0;
    return texture2D(uTip, rq * 0.5 + 0.5).a;
  }
  return falloffF(length(q), uHard);
}
float footprint() {
  vec3 N = normalize(vNrm);
  bool vis = false;
  vec2 px = vec2(0.0);
  float fade = 1.0;
  if (vClip.w > 1e-6) {
    vec3 ndc = vClip.xyz / vClip.w;
    if (abs(ndc.x) <= 1.0 && abs(ndc.y) <= 1.0 && ndc.z >= -1.0 && ndc.z <= 1.0) {
      px = (ndc.xy * 0.5 + 0.5) * uViewport;
      float viewZ = -(uView * vec4(vWorld, 1.0)).z;
      float sceneZ = unpackF(texture2D(uDepth, ndc.xy * 0.5 + 0.5)) * uFar;
      vis = viewZ <= sceneZ + max(0.004 * viewZ, 0.0005);
      vec3 toEye = uOrtho > 0.5 ? -uFwd : normalize(uEye - vWorld);
      float facing = dot(N, toEye);
      if (uFrontOnly > 0.5) {
        if (facing <= 0.0) vis = false;
        fade = smoothstep(0.0, 0.25, facing);
      }
    }
  }
  float keep = 1.0;
  for (int i = 0; i < 16; i++) {
    if (i >= uCount) break;
    vec4 A = uA[i];
    vec4 B = uB[i];
    vec4 C = uC[i];
    float a = 0.0;
    if (C.y < 0.5) {
      if (!vis) continue;
      vec2 q = (px - A.xy) / A.z;
      float qq = dot(q, q);
      if (qq >= (uUseTip > 0.5 ? 2.0 : 1.0)) continue;
      a = stampAt(q, B.x) * A.w * fade;
    } else if (C.y > 1.5) {
      // A dab painted through the screen, drawn again from its log: the same footprint — a disc of
      // radius R seen along the direction it was painted from — on the surface that faced that way,
      // no deeper along that direction than the slant of its own surface reaches.
      vec3 vd = normalize(uD[i].xyz);
      vec3 d = vWorld - A.xyz;
      float along = dot(d, vd);
      vec3 dl = d - vd * along;
      float R = A.w;
      if (dot(dl, dl) >= R * R * (uUseTip > 0.5 ? 2.0 : 1.0)) continue;
      float facing = -dot(N, vd);
      if (facing <= 0.0) continue;
      float c = max(abs(dot(normalize(B.xyz), vd)), 0.05);
      if (abs(along) > R * (1.0 + sqrt(1.0 - c * c) / c)) continue;
      vec3 t0 = abs(vd.y) < 0.99 ? normalize(cross(vec3(0.0, 1.0, 0.0), vd)) : normalize(cross(vec3(1.0, 0.0, 0.0), vd));
      vec3 b0 = cross(vd, t0);
      a = stampAt(vec2(dot(dl, t0), dot(dl, b0)) / R, C.x) * B.w * smoothstep(0.0, 0.25, facing);
    } else {
      vec3 d = vWorld - A.xyz;
      float R = A.w;
      if (dot(d, d) >= R * R * (uUseTip > 0.5 ? 2.0 : 1.0)) continue;
      vec3 bn = normalize(B.xyz);
      if (dot(N, bn) < 0.15) continue;
      vec3 t0 = abs(bn.y) < 0.99 ? normalize(cross(vec3(0.0, 1.0, 0.0), bn)) : normalize(cross(vec3(1.0, 0.0, 0.0), bn));
      vec3 b0 = cross(bn, t0);
      vec2 q = vec2(dot(d, t0), dot(d, b0)) / R;
      a = stampAt(q, C.x) * B.w;
    }
    keep *= 1.0 - clamp(a, 0.0, 1.0);
  }
  return 1.0 - keep;
}
`;

const DAB_FS = `
precision highp float;
${FOOTPRINT}
void main() {
  float cover = footprint();
  if (cover <= 0.0) discard;
  gl_FragColor = vec4(cover);
}
`;

/** Smudge, blur and clone: one dab, written straight into the layer with premultiplied "over"
 *  blending, from a source that is NOT the layer (the composite, or a snapshot of it). */
const PIXEL_FS = `
precision highp float;
${FOOTPRINT}
uniform sampler2D uSrc;
uniform int uTool;
uniform vec2 uOffset;
uniform vec2 uTexel;
uniform float uBlurR;
uniform float uStrength;
void main() {
  float a = footprint() * uStrength;
  if (a <= 0.0) discard;
  vec4 s;
  if (uTool == 2) {
    vec4 acc = texture2D(uSrc, vTex) * 4.0;
    float wsum = 4.0;
    for (int i = 0; i < 12; i++) {
      float ang = float(i) * 0.5235988;
      float rr = (i < 6 ? 0.5 : 1.0) * uBlurR;
      vec2 o = vec2(cos(ang), sin(ang)) * rr * uTexel;
      float wv = i < 6 ? 2.0 : 1.0;
      acc += texture2D(uSrc, vTex + o) * wv;
      wsum += wv;
    }
    s = acc / wsum;
  } else {
    s = texture2D(uSrc, vTex + uOffset);
  }
  float k = clamp(a * s.a, 0.0, 1.0);
  gl_FragColor = vec4(s.rgb * k, k);
}
`;

const FILL_FS = `
precision highp float;
varying vec4 vClip;
varying vec3 vWorld;
varying vec3 vNrm;
varying vec2 vTex;
void main() { gl_FragColor = vec4(1.0); }
`;

/** A texture the Studio makes for a part with none: its material colour times its vertex colours,
 *  written as sRGB bytes. */
const MADE_FS = `
precision highp float;
varying vec4 vClip;
varying vec3 vWorld;
varying vec3 vNrm;
varying vec2 vTex;
varying vec3 vCol;
uniform vec3 uColor;
uniform float uUseVC;
vec3 toGamma(vec3 c) { return mix(c * 12.92, 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c)); }
void main() {
  vec3 lin = uColor * (uUseVC > 0.5 ? vCol : vec3(1.0));
  gl_FragColor = vec4(toGamma(clamp(lin, 0.0, 1.0)), 1.0);
}
`;

const DEPTH_VS = `
precision highp float;
attribute vec3 position;
uniform mat4 modelMatrix;
uniform mat4 uView;
uniform mat4 uProj;
varying float vZ;
void main() {
  vec4 v = uView * modelMatrix * vec4(position, 1.0);
  vZ = -v.z;
  gl_Position = uProj * v;
}
`;
const DEPTH_FS = `
precision highp float;
varying float vZ;
uniform float uFar;
${PACK}
void main() { gl_FragColor = packF(clamp(vZ / uFar, 0.0, 0.999999)); }
`;

const BAKE_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uLayer;
uniform sampler2D uStroke;
uniform vec3 uColor;
uniform float uOpacity;
uniform int uMode;
uniform float uErase;
${BLEND}
void main() {
  vec4 L = texture2D(uLayer, vUv);
  float k = clamp(texture2D(uStroke, vUv).a * uOpacity, 0.0, 1.0);
  gl_FragColor = uErase > 0.5 ? L * (1.0 - k) : overP(L, uColor, k, uMode);
}
`;

const INIT_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uBase;
uniform float uHasBase;
uniform float uToGamma;
vec3 toGamma(vec3 c) { return mix(c * 12.92, 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c)); }
void main() {
  if (uHasBase < 0.5) { gl_FragColor = vec4(0.0); return; }
  vec4 b = texture2D(uBase, vUv);
  gl_FragColor = vec4(uToGamma > 0.5 ? toGamma(b.rgb) : b.rgb, b.a);
}
`;

const LAYER_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uAcc;
uniform sampler2D uLayer;
uniform float uOpacity;
uniform int uMode;
uniform float uPreview;
uniform sampler2D uStroke;
uniform vec3 uSColor;
uniform float uSOpacity;
uniform int uSMode;
uniform float uSErase;
${BLEND}
void main() {
  vec4 acc = texture2D(uAcc, vUv);
  vec4 L = texture2D(uLayer, vUv);
  if (uPreview > 0.5) {
    float k = clamp(texture2D(uStroke, vUv).a * uSOpacity, 0.0, 1.0);
    L = uSErase > 0.5 ? L * (1.0 - k) : overP(L, uSColor, k, uSMode);
  }
  float as = L.a * uOpacity;
  vec3 cs = L.a > 1e-6 ? L.rgb / L.a : vec3(0.0);
  gl_FragColor = overS(acc.rgb, acc.a, cs, as, uMode);
}
`;

/** Two premultiplied layers into one (merge down): the upper over the lower, in the upper's mode. */
const MERGE_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uLow;
uniform sampler2D uHigh;
uniform float uOpacity;
uniform int uMode;
${BLEND}
void main() {
  vec4 lo = texture2D(uLow, vUv);
  vec4 hi = texture2D(uHigh, vUv);
  vec3 cb = lo.a > 1e-6 ? lo.rgb / lo.a : vec3(0.0);
  vec3 cs = hi.a > 1e-6 ? hi.rgb / hi.a : vec3(0.0);
  vec4 o = overS(cb, lo.a, cs, hi.a * uOpacity, uMode);
  gl_FragColor = vec4(o.rgb * o.a, o.a);
}
`;

const DISPLAY_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uSrc;
uniform float uToLinear;
vec3 toLinear(vec3 c) { return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }
void main() {
  vec4 c = texture2D(uSrc, vUv);
  gl_FragColor = vec4(uToLinear > 0.5 ? toLinear(c.rgb) : c.rgb, c.a);
}
`;

const COPY_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uSrc;
uniform float uPremul;
void main() {
  vec4 c = texture2D(uSrc, vUv);
  gl_FragColor = uPremul > 0.5 ? vec4(c.rgb * c.a, c.a) : c;
}
`;

/** The seam fill: a texel outside every triangle takes the value of the nearest one inside, within
 *  four texels, so a painted edge has no dark or stale rim where the texture filter reaches past it. */
const DILATE_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uSrc;
uniform sampler2D uMask;
uniform vec2 uTexel;
void main() {
  if (texture2D(uMask, vUv).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv); return; }
  for (int r = 1; r <= 4; r++) {
    float f = float(r);
    vec2 o;
    o = vec2(f, 0.0) * uTexel;  if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(-f, 0.0) * uTexel; if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(0.0, f) * uTexel;  if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(0.0, -f) * uTexel; if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(f, f) * uTexel;    if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(-f, f) * uTexel;   if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(f, -f) * uTexel;   if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
    o = vec2(-f, -f) * uTexel;  if (texture2D(uMask, vUv + o).r > 0.5) { gl_FragColor = texture2D(uSrc, vUv + o); return; }
  }
  gl_FragColor = texture2D(uSrc, vUv);
}
`;

/** Max over 8 x 8 source texels: two of these turn a stroke's coverage into one texel per 64 x 64
 *  tile, which is all the undo needs to know about where a stroke went. */
const REDUCE_FS = `
precision highp float;
uniform sampler2D uSrc;
uniform vec2 uSrcTexel;
void main() {
  vec2 base = floor(gl_FragCoord.xy) * 8.0;
  float m = 0.0;
  for (int j = 0; j < 8; j++) {
    for (int i = 0; i < 8; i++) {
      vec4 c = texture2D(uSrc, (base + vec2(float(i), float(j)) + 0.5) * uSrcTexel);
      m = max(m, max(max(c.r, c.g), max(c.b, c.a)));
    }
  }
  gl_FragColor = vec4(m);
}
`;

/** Overlap: every triangle adds a quarter. A texel two triangles cover reads a half. */
const OVERLAP_FS = `
precision highp float;
varying vec2 vTex;
void main() { gl_FragColor = vec4(0.25); }
`;

export const TILE = 64;
const MAX_SIZE = 4096;

// ------------------------------------------------------------------ the engine
export class PaintEngine {
  readonly T: any;
  private host: PaintHost;
  private r: any;
  targets: Target[] = [];
  layers: PaintLayerMeta[] = [];
  activeLayer = "";
  history = new PaintHistory();
  /** Bumped on every change a panel shows. */
  version = 0;
  note = "";
  onChange: (() => void) | null = null;
  cloneSource: { target: string; tex: [number, number] } | null = null;
  private cloneOffset: [number, number] | null = null;
  private orphans: PaintTargetDoc[] = [];
  private quadScene: any;
  private quadMesh: any;
  private cam: any;
  private rScene: any;
  private mats: Record<string, any> = {};
  private rasterMats = new Map<string, any>();
  private tips = new Map<TipKind, any>();
  private depthRT: any = null;
  private ray: any;
  private halfOK = true;
  private st: null | {
    opts: StrokeOptions; tool: PaintTool; color: [number, number, number]; erase: boolean;
    tip: any | null; hard: number; batch: GpuDab[]; log: Map<Target, StrokeDab[]>;
    prev: PaintHit | null; touched: Set<Target>; view: { vp: any; view: any; eye: any; fwd: any; ortho: boolean; far: number; vw: number; vh: number };
  } = null;

  constructor(host: PaintHost) {
    this.host = host;
    this.T = host.T;
    this.r = host.renderer;
    const T = this.T;
    this.cam = new T.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.quadScene = new T.Scene();
    this.quadMesh = new T.Mesh(new T.PlaneGeometry(2, 2), null);
    this.quadMesh.frustumCulled = false;
    this.quadScene.add(this.quadMesh);
    this.rScene = new T.Scene();
    this.ray = new T.Raycaster();
    const quad = (fs: string, uniforms: Record<string, any>) => new T.RawShaderMaterial({
      vertexShader: QUAD_VS, fragmentShader: fs, uniforms, depthTest: false, depthWrite: false,
    });
    const tex = () => ({ value: null });
    this.mats = {
      bake: quad(BAKE_FS, { uLayer: tex(), uStroke: tex(), uColor: { value: new T.Vector3() }, uOpacity: { value: 1 }, uMode: { value: 0 }, uErase: { value: 0 } }),
      init: quad(INIT_FS, { uBase: tex(), uHasBase: { value: 1 }, uToGamma: { value: 1 } }),
      layer: quad(LAYER_FS, {
        uAcc: tex(), uLayer: tex(), uOpacity: { value: 1 }, uMode: { value: 0 }, uPreview: { value: 0 }, uStroke: tex(),
        uSColor: { value: new T.Vector3() }, uSOpacity: { value: 1 }, uSMode: { value: 0 }, uSErase: { value: 0 },
      }),
      merge: quad(MERGE_FS, { uLow: tex(), uHigh: tex(), uOpacity: { value: 1 }, uMode: { value: 0 } }),
      display: quad(DISPLAY_FS, { uSrc: tex(), uToLinear: { value: 1 } }),
      copy: quad(COPY_FS, { uSrc: tex(), uPremul: { value: 0 } }),
      dilate: quad(DILATE_FS, { uSrc: tex(), uMask: tex(), uTexel: { value: new T.Vector2() } }),
      reduce: quad(REDUCE_FS, { uSrc: tex(), uSrcTexel: { value: new T.Vector2() } }),
      depth: new T.RawShaderMaterial({
        vertexShader: DEPTH_VS, fragmentShader: DEPTH_FS, side: T.DoubleSide,
        uniforms: { uView: { value: new T.Matrix4() }, uProj: { value: new T.Matrix4() }, uFar: { value: 1000 } },
      }),
      none: new T.MeshBasicMaterial({ visible: false }),
    };
    try {
      const gl = this.r.getContext();
      this.halfOK = !!(gl.getExtension("EXT_color_buffer_float") || gl.getExtension("EXT_color_buffer_half_float"));
    } catch { this.halfOK = false; }
  }

  // ---------------------------------------------------------------- GPU plumbing
  private rt(w: number, h: number, o: { half?: boolean; srgb?: boolean; depth?: boolean; mip?: boolean; linear?: boolean } = {}): any {
    const T = this.T;
    const rt = new T.WebGLRenderTarget(w, h, {
      type: o.half && this.halfOK ? T.HalfFloatType : T.UnsignedByteType,
      format: T.RGBAFormat,
      colorSpace: o.srgb ? T.SRGBColorSpace : T.NoColorSpace,
      minFilter: o.mip ? T.LinearMipmapLinearFilter : o.linear ? T.LinearFilter : T.NearestFilter,
      magFilter: o.mip || o.linear ? T.LinearFilter : T.NearestFilter,
      generateMipmaps: !!o.mip,
      depthBuffer: !!o.depth,
      stencilBuffer: false,
    });
    rt.texture.flipY = false;
    return rt;
  }

  /** Run GPU work and put the renderer back exactly as it was: its target, clear colour and
   *  autoClear. Every entry point that draws goes through here, because the viewport's own frame
   *  runs between them and must never find a paint target still bound. */
  private gl<R>(fn: () => R): R {
    const r = this.r, T = this.T;
    const prevRT = r.getRenderTarget();
    const prevAuto = r.autoClear;
    const prevColor = new T.Color();
    r.getClearColor(prevColor);
    const prevAlpha = r.getClearAlpha();
    r.autoClear = false;
    try { return fn(); } finally {
      r.setRenderTarget(prevRT);
      r.autoClear = prevAuto;
      r.setClearColor(prevColor, prevAlpha);
    }
  }

  private clear(rt: any, a = 0) {
    this.r.setRenderTarget(rt);
    this.r.setClearColor(0x000000, a);
    this.r.clear(true, !!rt?.depthBuffer, false);
  }

  private quad(mat: any, into: any) {
    this.quadMesh.material = mat;
    this.r.setRenderTarget(into);
    this.r.render(this.quadScene, this.cam);
  }

  private copy(src: any, into: any, premul = false) {
    const m = this.mats.copy;
    m.uniforms.uSrc.value = src.texture ?? src;
    m.uniforms.uPremul.value = premul ? 1 : 0;
    this.quad(m, into);
  }

  private uvName(channel: number): string { return channel > 0 ? "uv" + channel : "uv"; }

  /** The raster materials, one per (kind, uv channel), made once. */
  private rasterMat(kind: "dab" | "pixel" | "fill" | "made" | "overlap", channel: number): any {
    const k = kind + ":" + channel;
    let m = this.rasterMats.get(k);
    if (m) return m;
    const T = this.T;
    const vec4s = () => ({ value: Array.from({ length: 16 }, () => new T.Vector4()) });
    const foot = {
      uUvXf: { value: new T.Matrix3() }, uViewProj: { value: new T.Matrix4() }, uViewport: { value: new T.Vector2(1, 1) },
      uDepth: { value: null }, uFar: { value: 1000 }, uView: { value: new T.Matrix4() }, uEye: { value: new T.Vector3() },
      uFwd: { value: new T.Vector3(0, 0, -1) }, uOrtho: { value: 0 }, uFrontOnly: { value: 1 }, uCount: { value: 0 },
      uA: vec4s(), uB: vec4s(), uC: vec4s(), uD: vec4s(), uTip: { value: null }, uUseTip: { value: 0 }, uHard: { value: 0.8 },
    };
    const vs = rasterVS(this.uvName(channel), kind === "made");
    const common = { vertexShader: vs, side: T.DoubleSide, depthTest: false, depthWrite: false };
    if (kind === "dab") {
      m = new T.RawShaderMaterial({
        ...common, fragmentShader: DAB_FS, uniforms: foot, transparent: true, blending: T.CustomBlending,
        blendEquation: T.AddEquation, blendSrc: T.OneFactor, blendDst: T.OneMinusSrcAlphaFactor,
        blendSrcAlpha: T.OneFactor, blendDstAlpha: T.OneMinusSrcAlphaFactor,
      });
    } else if (kind === "pixel") {
      m = new T.RawShaderMaterial({
        ...common, fragmentShader: PIXEL_FS, transparent: true, blending: T.CustomBlending,
        blendEquation: T.AddEquation, blendSrc: T.OneFactor, blendDst: T.OneMinusSrcAlphaFactor,
        blendSrcAlpha: T.OneFactor, blendDstAlpha: T.OneMinusSrcAlphaFactor,
        uniforms: { ...foot, uSrc: { value: null }, uTool: { value: 1 }, uOffset: { value: new T.Vector2() }, uTexel: { value: new T.Vector2() }, uBlurR: { value: 2 }, uStrength: { value: 1 } },
      });
    } else if (kind === "overlap") {
      m = new T.RawShaderMaterial({
        ...common, fragmentShader: OVERLAP_FS, transparent: true, blending: T.AdditiveBlending,
        uniforms: { uUvXf: { value: new T.Matrix3() }, uViewProj: { value: new T.Matrix4() } },
      });
    } else if (kind === "made") {
      m = new T.RawShaderMaterial({
        ...common, fragmentShader: MADE_FS,
        uniforms: { uUvXf: { value: new T.Matrix3() }, uViewProj: { value: new T.Matrix4() }, uColor: { value: new T.Vector3(1, 1, 1) }, uUseVC: { value: 0 } },
      });
    } else {
      m = new T.RawShaderMaterial({
        ...common, fragmentShader: FILL_FS,
        uniforms: { uUvXf: { value: new T.Matrix3() }, uViewProj: { value: new T.Matrix4() } },
      });
    }
    this.rasterMats.set(k, m);
    return m;
  }

  /** The target's meshes in a private scene, drawn with `mat` (on the slots that use the texture
   *  only — a mesh with two materials keeps its other one out of the pass). */
  private stage(t: Target, mat: any, only?: { obj: any; geo?: any }) {
    const s = this.rScene;
    while (s.children.length) s.remove(s.children[0]);
    const T = this.T;
    const byObj = new Map<any, number[]>();
    for (const sl of t.slots) {
      if (only && sl.obj !== only.obj) continue;
      if (!byObj.has(sl.obj)) byObj.set(sl.obj, []);
      byObj.get(sl.obj)!.push(sl.index);
    }
    for (const [obj, idx] of byObj) {
      obj.updateWorldMatrix(true, false);
      let m: any = mat;
      // A geometry made for this pass (one UV island) has no groups, and three draws nothing of a
      // mesh with a material list and no groups: one material for all of it.
      if (Array.isArray(obj.material) && !only?.geo) {
        m = obj.material.map(() => this.mats.none);
        for (const i of idx) if (i >= 0 && i < m.length) m[i] = mat;
      }
      const p = new T.Mesh(only?.geo || obj.geometry, m);
      p.matrixAutoUpdate = false;
      p.matrix.copy(obj.matrixWorld);
      p.frustumCulled = false;
      s.add(p);
    }
  }

  private tip(kind: TipKind): any | null {
    if (kind === "round") return null;
    let t = this.tips.get(kind);
    if (t) return t;
    const T = this.T;
    const n = 128;
    const a = tipMask(kind, n, 7);
    const rgba = new Uint8Array(n * n * 4);
    for (let i = 0; i < n * n; i++) { rgba[i * 4] = rgba[i * 4 + 1] = rgba[i * 4 + 2] = 255; rgba[i * 4 + 3] = a[i]; }
    t = new T.DataTexture(rgba, n, n, T.RGBAFormat);
    t.minFilter = T.LinearFilter;
    t.magFilter = T.LinearFilter;
    t.needsUpdate = true;
    this.tips.set(kind, t);
    return t;
  }

  // ---------------------------------------------------------------- finding the textures
  /** The base texture a material's map stands for: our own display texture maps back to its base. */
  private baseOf(map: any): { base: any; target: Target | null } {
    for (const t of this.targets) if (t.display && map === t.display.texture) return { base: t.base, target: t };
    return { base: map, target: null };
  }

  private texSize(tex: any): [number, number] {
    const img = tex?.image;
    const w = img?.width || img?.videoWidth || tex?.source?.data?.width || 0;
    const h = img?.height || img?.videoHeight || tex?.source?.data?.height || 0;
    return [w | 0, h | 0];
  }

  private keyFor(name: string, w: number, h: number, slots: Slot[]): string {
    const keys = [...new Set(slots.map((s) => this.host.keyOf(s.obj) || s.obj.name || ""))].sort();
    return name + "|" + w + "x" + h + "|" + (keys[0] || "");
  }

  /**
   * The texture layout, as the SET of its texture triangles: not the meshes that carry them, their
   * names, or the order of their triangles. Merging parts into one mesh, splitting them again or
   * renaming them keeps the layout — and so keeps the paint as exact pixels. Only a change to the
   * triangles themselves (a segment count, a new unwrap) redraws the paint from its strokes.
   */
  private sigOf(t: Target): string {
    const set = new Set<number>();
    const seen = new Set<string>();
    for (const sl of t.slots) {
      const g = sl.obj.geometry;
      const uv = g?.attributes?.[this.uvName(t.channel)];
      if (!g || !uv || !g.attributes.position) continue;
      const id = g.uuid + ":" + sl.index;
      if (seen.has(id)) continue;
      seen.add(id);
      const arr = this.plain(uv, 2);
      const idx = g.index ? g.index.array : null;
      const vc = g.attributes.position.count;
      const groups = sl.index >= 0 && g.groups?.length ? g.groups.filter((gr: any) => (gr.materialIndex | 0) === sl.index) : null;
      if (groups) for (const gr of groups) addUvTriangles(set, arr, idx, vc, gr.start, gr.count);
      else addUvTriangles(set, arr, idx, vc);
    }
    return uvTriangleSetSignature(set);
  }

  /**
   * Find every painted texture on the subject — or find them again after the code rebuilt the
   * asset. A texture already known keeps its layers: its pixels when the layout is the same, its
   * strokes painted again when it is not. Returns what happened, for the panel to say.
   */
  scan(): { targets: number; kept: number; replayed: number; relaid: number; lost: number } {
    const T = this.T;
    const subject = this.host.subject();
    const groups = new Map<any, Slot[]>();
    subject.updateMatrixWorld(true);
    subject.traverse((o: any) => {
      if (!o.isMesh || !o.geometry?.attributes?.position || o.userData?.__shell) return;
      const list = Array.isArray(o.material) ? o.material : [o.material];
      list.forEach((m: any, i: number) => {
        if (!m || !m.map || !m.map.isTexture) return;
        const { base } = this.baseOf(m.map);
        if (!base) return;
        const ch = base.channel | 0;
        if (!o.geometry.attributes[this.uvName(ch)]) return;
        if (!groups.has(base)) groups.set(base, []);
        groups.get(base)!.push({ obj: o, index: Array.isArray(o.material) ? i : -1, mat: m });
      });
    });

    const summary = { targets: 0, kept: 0, replayed: 0, relaid: 0, lost: 0 };
    const seenTargets = new Set<Target>();
    for (const [base, slots] of groups) {
      const made = this.targets.find((t) => t.madeRT && t.madeRT.texture === base);
      if (made) { this.rebindTarget(made, base, slots, summary); seenTargets.add(made); continue; }
      const [bw, bh] = this.texSize(base);
      if (!bw || !bh) continue;
      const name = slots[0].mat.name || base.name || "texture";
      const key = this.keyFor(name, bw, bh, slots);
      let t = this.targets.find((x) => x.id === key && !seenTargets.has(x))
        || this.targets.find((x) => !seenTargets.has(x) && !x.madeRT && x.name === name && (!x.base || this.sameSize(x.base, base)))
        || this.targets.find((x) => !seenTargets.has(x) && !x.madeRT && x.name === name)
        || null;
      if (!t && this.targets.filter((x) => !x.madeRT).length === 1 && groups.size === 1) t = this.targets.find((x) => !x.madeRT) || null;
      if (!t) { t = new Target(key, name); this.targets.push(t); }
      this.rebindTarget(t, base, slots, summary);
      seenTargets.add(t);
    }
    // The textures the Studio made for untextured parts: the rebuilt mesh has no map again, so the
    // texture is made again from its new material before its layers go back on.
    for (const t of this.targets) {
      if (seenTargets.has(t) || !t.madeKey) continue;
      const obj = this.host.objOf(t.madeKey.split("#")[0]);
      if (obj && this.remake(t, obj)) { seenTargets.add(t); summary.kept++; }
    }
    for (const t of this.targets) {
      if (!seenTargets.has(t)) { if (t.live) summary.lost++; t.live = false; } else t.live = true;
    }
    summary.targets = this.targets.filter((t) => t.live).length;
    this.host.materialsChanged();
    this.touch();
    void T;
    return summary;
  }

  private sameSize(a: any, b: any): boolean {
    const [aw, ah] = this.texSize(a), [bw, bh] = this.texSize(b);
    return aw === bw && ah === bh;
  }

  private rebindTarget(t: Target, base: any, slots: Slot[], summary: { kept: number; replayed: number; relaid: number }) {
    const T = this.T;
    const hadLayers = t.rts.size > 0;
    const oldSig = t.uvSig;
    const prev = t.anchor;
    const madeBase = t.madeRT && base === t.madeRT.texture;
    t.base = base;
    t.baseGamma = !!madeBase;
    t.srgb = madeBase ? true : base.colorSpace === T.SRGBColorSpace || base.colorSpace === "srgb";
    t.channel = base.channel | 0;
    if (base.matrixAutoUpdate !== false && typeof base.updateMatrix === "function") base.updateMatrix();
    t.uvXf = (base.matrix && base.matrix.isMatrix3) ? base.matrix.clone() : new T.Matrix3();
    t.slots = slots;
    let [bw, bh] = madeBase ? [t.madeRT.width, t.madeRT.height] : this.texSize(base);
    if (!t.w) {
      // Paint at least 1024 on the long side: a 256 texture painted at its own size gives blocks.
      t.scale = madeBase ? 1 : clamp(Math.ceil(1024 / Math.max(bw, bh)), 1, 4);
    }
    const w = Math.min(MAX_SIZE, Math.round(bw * t.scale)), h = Math.min(MAX_SIZE, Math.round(bh * t.scale));
    this.ensureBuffers(t, w, h);
    t.uvSig = this.sigOf(t);
    this.gl(() => this.buildMask(t));
    if (hadLayers && oldSig && oldSig !== t.uvSig) {
      if (t.strokes.length) { this.replay(t, prev); summary.replayed++; }
      else { summary.relaid++; }
    } else if (hadLayers) summary.kept++;
    // The meshes as they are now: what the log's texture points are read against at the next
    // change of layout.
    t.anchor = t.strokes.length || t.rts.size ? this.snapshot(t) : null;
    this.attach(t);
    t.dirty = true;
    void bw; void bh;
  }

  /** Make or resize every buffer of a target. Layers are resampled, not dropped, when the size
   *  changes (a texture-size slider): paint scales with the texture it sits on. */
  private ensureBuffers(t: Target, w: number, h: number) {
    if (t.w === w && t.h === h && t.composite) return;
    const old = t.w ? { w: t.w, h: t.h } : null;
    const drop = (x: any) => { try { x?.dispose(); } catch { /* gone */ } };
    for (const k of ["stroke", "accA", "accB", "composite", "display", "mask", "spare", "backup", "snapshot"] as const) { drop((t as any)[k]); (t as any)[k] = null; }
    t.w = w; t.h = h;
    t.stroke = this.rt(w, h, { half: true });
    t.accA = this.rt(w, h);
    t.accB = this.rt(w, h);
    t.composite = this.rt(w, h, { linear: true });
    t.display = this.rt(w, h, { srgb: t.srgb, mip: true });
    t.mask = this.rt(w, h);
    t.spare = this.rt(w, h);
    t.backup = this.rt(w, h);
    t.snapshot = this.rt(w, h, { linear: true });
    if (old) {
      this.gl(() => {
        for (const [id, rt] of t.rts) {
          const next = this.rt(w, h);
          rt.texture.minFilter = this.T.LinearFilter; rt.texture.magFilter = this.T.LinearFilter; rt.texture.needsUpdate = true;
          this.copy(rt, next);
          rt.dispose();
          t.rts.set(id, next);
        }
      });
    }
  }

  private buildMask(t: Target) {
    const m = this.rasterMat("fill", t.channel);
    m.uniforms.uUvXf.value.copy(t.uvXf);
    this.clear(t.mask);
    this.stage(t, m);
    this.r.setRenderTarget(t.mask);
    this.r.render(this.rScene, this.cam);
    // Shared texture space: two triangles over one texel. Painting one then paints the other —
    // mirrored UVs, or one swatch reused by twelve spikes — and the panel should say so.
    const ov = this.rasterMat("overlap", t.channel);
    ov.uniforms.uUvXf.value.copy(t.uvXf);
    this.clear(t.spare);
    this.stage(t, ov);
    this.r.setRenderTarget(t.spare);
    this.r.render(this.rScene, this.cam);
    const red = this.reduce(t.spare, t.w, t.h);
    let peak = 0;
    for (let i = 0; i < red.px.length; i += 4) peak = Math.max(peak, red.px[i]);
    t.sharedUV = peak >= 120;
    this.clear(t.spare);
  }

  /** Give the materials the painted texture, remembering what they had. */
  private attach(t: Target) {
    const tex = t.display.texture;
    const b = t.base;
    if (b && !t.baseGamma) {
      tex.offset.copy(b.offset); tex.repeat.copy(b.repeat); tex.rotation = b.rotation; tex.center.copy(b.center);
      tex.matrixAutoUpdate = b.matrixAutoUpdate;
      if (!b.matrixAutoUpdate) tex.matrix.copy(b.matrix);
      tex.wrapS = b.wrapS; tex.wrapT = b.wrapT; tex.anisotropy = b.anisotropy || 1; tex.channel = b.channel | 0;
      tex.name = (b.name || t.name) + " (painted)";
    }
    for (const sl of t.slots) {
      const m = sl.mat;
      if (!t.saved.has(m)) t.saved.set(m, { map: m.map === tex ? t.base : m.map, color: null, vertexColors: null });
      if (m.map !== tex) {
        const hadNone = !m.map;
        m.map = tex;
        if (hadNone) m.needsUpdate = true;
      }
    }
  }

  /** Every material back to its own texture. Used when the editor leaves this asset. */
  detach() {
    for (const t of this.targets) {
      for (const [m, s] of t.saved) {
        m.map = s.map;
        if (s.color !== null && m.color) m.color.setHex(s.color);
        if (s.vertexColors !== null) m.vertexColors = s.vertexColors;
        m.needsUpdate = true;
      }
      t.saved.clear();
    }
    this.host.materialsChanged();
  }

  /** Paint finer (or coarser) than the texture: 1, 2 or 4 paint pixels per texture pixel. The
   *  layers are resampled; the undo history is cleared, because its tiles are in the old size. */
  setScale(id: string, scale: number) {
    const t = this.targets.find((x) => x.id === id);
    if (!t || t.madeRT || !t.base) return;
    const s = clamp(Math.round(scale), 1, 4);
    if (s === t.scale) return;
    t.scale = s;
    const [bw, bh] = this.texSize(t.base);
    this.ensureBuffers(t, Math.min(MAX_SIZE, bw * s), Math.min(MAX_SIZE, bh * s));
    this.gl(() => this.buildMask(t));
    this.attach(t);
    this.history.clear();
    this.host.materialsChanged();
    this.touch();
  }

  // ---------------------------------------------------------------- a texture for a part with none
  /** Parts that could be painted if they had a texture: meshes with UVs and no map. */
  untextured(): Array<{ key: string; name: string; uv: boolean }> {
    // One row per material SLOT with no map: a merged mesh can carry a painted material and a
    // plain one (the orc's body and its ink outline), and only the plain one is untextured.
    const out: Array<{ key: string; name: string; uv: boolean }> = [];
    this.host.subject().traverse((o: any) => {
      if (!o.isMesh || !o.geometry?.attributes?.position || o.userData?.__shell) return;
      const list = Array.isArray(o.material) ? o.material : [o.material];
      const mk = this.host.keyOf(o);
      list.forEach((m: any, i: number) => {
        if (!m || m.map || !("map" in m)) return;
        if (this.targets.some((t) => t.madeKey === mk + "#" + i)) return;
        const name = (o.name || mk) + (list.length > 1 ? " · " + (m.name || "material " + (i + 1)) : "");
        out.push({ key: mk + "#" + i, name, uv: !!o.geometry.attributes.uv });
      });
    });
    return out;
  }

  /** Give a part a texture of its own, made from what it looks like now, so it can be painted. */
  makeTexture(key: string, size = 1024): string {
    const [mk, si] = key.split("#");
    const obj = this.host.objOf(mk);
    if (!obj?.isMesh) return "no mesh called " + mk;
    if (!obj.geometry.attributes.uv) return "this part has no texture layout (UVs). Add the Unwrap step in the modifier stack first, then make the texture.";
    const list = Array.isArray(obj.material) ? obj.material : [obj.material];
    const i = si !== undefined && si !== "" ? Number(si) : list.findIndex((m: any) => m && !m.map && ("map" in m));
    if (!(i >= 0) || !list[i] || list[i].map) return "this part already has a texture";
    const t = new Target("made|" + mk + "#" + i, (list[i].name || obj.name || mk) + " (made)");
    t.madeKey = mk + "#" + i;
    t.scale = 1;
    this.targets.push(t);
    const s = Math.min(MAX_SIZE, Math.max(64, size | 0));
    t.madeRT = this.rt(s, s, { linear: true });
    if (!this.remake(t, obj, s)) return "the texture could not be made";
    this.host.materialsChanged();
    this.touch();
    return "";
  }

  private remake(t: Target, obj: any, size = 0): boolean {
    const list = Array.isArray(obj.material) ? obj.material : [obj.material];
    const i = Number(t.madeKey.split("#")[1] || 0);
    const m = list[i];
    if (!m || !obj.geometry.attributes.uv) return false;
    const s = size || t.madeRT?.width || 1024;
    if (!t.madeRT || t.madeRT.width !== s) { t.madeRT?.dispose(); t.madeRT = this.rt(s, s, { linear: true }); }
    const T = this.T;
    const useVC = !!(m.vertexColors && obj.geometry.attributes.color);
    const saved = t.saved.get(m);
    const colLin = saved?.color != null ? new T.Color().setHex(saved.color) : (m.color ? m.color.clone() : new T.Color(1, 1, 1));
    const vcWas = saved?.vertexColors != null ? saved.vertexColors : !!m.vertexColors;
    const slot: Slot = { obj, index: Array.isArray(obj.material) ? i : -1, mat: m };
    const tmp = new Target(t.id, t.name);
    tmp.slots = [slot]; tmp.channel = 0; tmp.uvXf = new T.Matrix3(); tmp.w = s; tmp.h = s;
    this.gl(() => {
      const mm = this.rasterMat("made", 0);
      mm.uniforms.uColor.value.set(colLin.r, colLin.g, colLin.b);
      mm.uniforms.uUseVC.value = (useVC || (vcWas && obj.geometry.attributes.color)) ? 1 : 0;
      this.clear(t.madeRT, 1);
      this.stage(tmp, mm);
      this.r.setRenderTarget(t.madeRT);
      this.r.render(this.rScene, this.cam);
      tmp.mask = this.rt(s, s);
      tmp.spare = this.rt(s, s);
      this.buildMaskOnly(tmp);
      this.dilateInto(t.madeRT, tmp.mask, tmp.spare, s, s);
      this.copy(tmp.spare, t.madeRT);
      tmp.mask.dispose(); tmp.spare.dispose();
    });
    if (!t.saved.has(m)) t.saved.set(m, { map: null, color: m.color ? m.color.getHex() : null, vertexColors: !!m.vertexColors });
    if (m.color) m.color.setRGB(1, 1, 1);
    if (m.vertexColors) { m.vertexColors = false; m.needsUpdate = true; }
    t.made = { from: (useVC || vcWas) ? "vertex" : "color", color: "#" + colLin.getHexString() };
    this.rebindTarget(t, t.madeRT.texture, [slot], { kept: 0, replayed: 0, relaid: 0 });
    return true;
  }

  private buildMaskOnly(t: Target) {
    const m = this.rasterMat("fill", t.channel);
    m.uniforms.uUvXf.value.copy(t.uvXf);
    this.clear(t.mask);
    this.stage(t, m);
    this.r.setRenderTarget(t.mask);
    this.r.render(this.rScene, this.cam);
  }

  // ---------------------------------------------------------------- where the pointer is
  private meshes(): any[] {
    const out = new Set<any>();
    for (const t of this.targets) if (t.live) for (const s of t.slots) out.add(s.obj);
    return [...out];
  }

  /** The painted surface under a viewport point, with its texture coordinates. */
  hit(ndc: { x: number; y: number }): PaintHit | null {
    const T = this.T;
    const cam = this.host.camera();
    this.ray.setFromCamera(new T.Vector2(ndc.x, ndc.y), cam);
    const objs = this.meshes();
    if (!objs.length) return null;
    let hits: any[] = [];
    try { hits = this.ray.intersectObjects(objs, false); } catch { return null; }
    for (const h of hits) {
      const o = h.object;
      const mi = h.face?.materialIndex ?? 0;
      const t = this.targets.find((x) => x.live && x.slots.some((s) => s.obj === o && (s.index < 0 || s.index === mi)));
      if (!t) continue;
      const uv = t.channel > 0 ? h["uv" + t.channel] : h.uv;
      if (!uv) continue;
      const tv = new T.Vector3(uv.x, uv.y, 1).applyMatrix3(t.uvXf);
      const n = h.face?.normal ? h.face.normal.clone().transformDirection(o.matrixWorld) : new T.Vector3(0, 1, 0);
      return {
        point: [h.point.x, h.point.y, h.point.z], normal: [n.x, n.y, n.z], object: o,
        key: this.host.keyOf(o), target: t.id, tex: [tv.x, tv.y], face: typeof h.faceIndex === "number" ? h.faceIndex : -1,
      };
    }
    return null;
  }

  /** World units per CSS pixel at a point, for turning a screen brush into a 3D one. */
  worldPerPx(p: [number, number, number]): number {
    const T = this.T;
    const cam = this.host.camera();
    const vp = this.host.viewport();
    if (cam.isOrthographicCamera) return ((cam.top - cam.bottom) / (cam.zoom || 1)) / Math.max(1, vp.h);
    const v = new T.Vector3(p[0], p[1], p[2]).applyMatrix4(cam.matrixWorldInverse);
    const d = Math.max(1e-4, -v.z);
    return (2 * d * Math.tan(((cam.fov || 50) * Math.PI) / 360)) / Math.max(1, vp.h) / (cam.zoom || 1);
  }

  // ---------------------------------------------------------------- strokes
  private camState() {
    const T = this.T;
    const cam = this.host.camera();
    cam.updateMatrixWorld(true);
    const vp = this.host.viewport();
    const view = cam.matrixWorldInverse.clone();
    const proj = cam.projectionMatrix.clone();
    const eye = cam.getWorldPosition(new T.Vector3());
    const fwd = cam.getWorldDirection(new T.Vector3());
    return { vp: new T.Matrix4().multiplyMatrices(proj, view), proj, view, eye, fwd, ortho: !!cam.isOrthographicCamera, far: cam.far || 1000, vw: vp.w, vh: vp.h };
  }

  /** The scene's depth from the camera, so a brush never paints what is behind something. */
  private renderDepth(cs: ReturnType<PaintEngine["camState"]>) {
    const T = this.T;
    const pr = Math.min(2, this.r.getPixelRatio ? this.r.getPixelRatio() : 1);
    const w = Math.max(1, Math.round(cs.vw * pr)), h = Math.max(1, Math.round(cs.vh * pr));
    if (!this.depthRT || this.depthRT.width !== w || this.depthRT.height !== h) {
      this.depthRT?.dispose();
      this.depthRT = this.rt(w, h, { depth: true });
    }
    const s = this.rScene;
    while (s.children.length) s.remove(s.children[0]);
    this.host.subject().traverseVisible((o: any) => {
      if (!o.isMesh || !o.geometry?.attributes?.position || o.isInstancedMesh) return;
      const p = new T.Mesh(o.geometry, this.mats.depth);
      p.matrixAutoUpdate = false;
      o.updateWorldMatrix(true, false);
      p.matrix.copy(o.matrixWorld);
      p.frustumCulled = false;
      s.add(p);
    });
    const m = this.mats.depth;
    m.uniforms.uView.value.copy(cs.view);
    m.uniforms.uProj.value.copy(cs.proj);
    m.uniforms.uFar.value = cs.far;
    this.r.setRenderTarget(this.depthRT);
    this.r.setClearColor(0xffffff, 1);
    this.r.clear(true, true, false);
    this.r.render(s, this.cam);
  }

  private liveTargets(): Target[] { return this.targets.filter((t) => t.live && t.composite); }

  private activeRT(t: Target): any {
    if (!this.layers.length) this.addLayerMeta("Paint");
    if (!this.layers.some((l) => l.id === this.activeLayer)) this.activeLayer = this.layers[this.layers.length - 1].id;
    let rt = t.rts.get(this.activeLayer);
    if (!rt) { rt = this.rt(t.w, t.h); this.clear(rt); t.rts.set(this.activeLayer, rt); }
    return rt;
  }

  /** Start a stroke. Returns false, with `note` saying why, when there is nothing to paint on. */
  begin(opts: StrokeOptions): boolean {
    const targets = this.liveTargets();
    if (!targets.length) { this.note = "nothing here has a texture to paint on — make one in the Texture section"; return false; }
    if (opts.tool === "picker" || opts.tool === "fill") return false;
    if (opts.tool === "clone" && !this.cloneSource) { this.note = "Alt+click where the clone brush should copy from first"; return false; }
    const T = this.T;
    const cs = this.camState();
    const tipKind = opts.brush.tip;
    this.gl(() => {
      this.renderDepth(cs);
      for (const t of targets) {
        const rt = this.activeRT(t);
        this.clear(t.stroke);
        this.copy(rt, t.backup);
        if (opts.tool === "clone") { this.composite(t); this.copy(t.composite, t.snapshot); }
      }
    });
    const c = hexToRgb(opts.color);
    this.st = {
      opts, tool: opts.tool, color: c, erase: opts.tool === "eraser", tip: this.tip(tipKind), hard: opts.brush.hardness,
      batch: [], log: new Map(), prev: null, touched: new Set(), view: cs,
    };
    this.cloneOffset = null;
    void T;
    return true;
  }

  /** Mirror a world point and normal across the asset's own frame. */
  private mirrored(p: [number, number, number], n: [number, number, number], axis: 1 | 2 | 3): { p: any; n: any } {
    const T = this.T;
    const root = this.host.mirrorRoot();
    root.updateWorldMatrix(true, false);
    const M = root.matrixWorld, Mi = M.clone().invert();
    const S = new T.Matrix4().makeScale(axis === 1 ? -1 : 1, axis === 2 ? -1 : 1, axis === 3 ? -1 : 1);
    const X = new T.Matrix4().multiplyMatrices(M, S).multiply(Mi);
    const pp = new T.Vector3(p[0], p[1], p[2]).applyMatrix4(X);
    const nn = new T.Vector3(n[0], n[1], n[2]).transformDirection(X);
    return { p: pp, n: nn };
  }

  /**
   * The dabs of the stroke so far. Each is raycast once (within a budget), which is what the stroke
   * log, the mirror, smudge and clone need to know about it.
   */
  dabs(list: ScreenDab[]) {
    const st = this.st;
    if (!st || !list.length) return;
    const T = this.T;
    const cs = st.view;
    const pixelTool = st.tool === "smudge" || st.tool === "blur" || st.tool === "clone";
    let budget = 48;
    let lastHit: PaintHit | null = st.prev;
    // SMUDGE AND BLUR READ THE PICTURE, so it is brought up to date once per pointer move — not once
    // per dab, which at 2 px spacing was a full composite of every layer a hundred times a frame.
    // And at most 24 of their dabs per move, spread evenly: each one is a pass over the texture.
    if (pixelTool) {
      this.gl(() => { if (st.tool !== "clone") for (const t of this.liveTargets()) this.composite(t); });
      if (list.length > 24) {
        const step = list.length / 24;
        list = Array.from({ length: 24 }, (_, i) => list[Math.min(list.length - 1, Math.round(i * step))]);
      }
    }
    for (const d of list) {
      const ndc = { x: (d.x / Math.max(1, cs.vw)) * 2 - 1, y: -((d.y / Math.max(1, cs.vh)) * 2 - 1) };
      let hit: PaintHit | null = null;
      if (budget-- > 0) { hit = this.hit(ndc); if (hit) lastHit = hit; } else hit = lastHit;
      const rPx = d.size / 2;
      const main: GpuDab = { view: st.opts.projection === "view", x: d.x, y: cs.vh - d.y, r: rPx, p: null, n: null, R: 0, alpha: d.alpha, angle: d.angle };
      let worldR = 0;
      if (hit) worldR = rPx * this.worldPerPx(hit.point);
      if (!main.view) {
        if (!hit) continue;
        main.p = new T.Vector3(...hit.point); main.n = new T.Vector3(...hit.normal); main.R = worldR;
      }
      const all: GpuDab[] = [main];
      if (st.opts.mirror && hit) {
        const m = this.mirrored(hit.point, hit.normal, st.opts.mirror as 1 | 2 | 3);
        all.push({ view: false, x: 0, y: 0, r: 0, p: m.p, n: m.n, R: worldR, alpha: d.alpha, angle: -d.angle });
      }
      if (hit && !pixelTool) {
        const from = main.view ? (cs.ortho ? cs.fwd.clone() : new T.Vector3(...hit.point).sub(cs.eye).normalize()) : null;
        this.logDab(hit, worldR, d, from);
      }
      if (hit && st.opts.mirror && !pixelTool) {
        const m = this.mirrored(hit.point, hit.normal, st.opts.mirror as 1 | 2 | 3);
        const mh = this.hitNear(m.p, m.n);
        if (mh) this.logDab(mh, worldR, { ...d, angle: -d.angle });
      }
      if (pixelTool) this.pixelDab(all, hit, st.prev);
      else {
        st.batch.push(...all);
        if (st.batch.length >= 16) this.flush();
      }
      if (hit) st.prev = hit;
    }
    if (!pixelTool) this.flush();
  }

  /** The surface near a 3D point (for the mirror side's log entry): a short ray back along the
   *  normal. Null when the mirror side has no surface there. */
  private hitNear(p: any, n: any): PaintHit | null {
    const T = this.T;
    const objs = this.meshes();
    const len = Math.max(0.01, p.length() * 0.05);
    const origin = p.clone().addScaledVector(n, len);
    this.ray.set(origin, n.clone().negate());
    this.ray.far = len * 2;
    let hits: any[] = [];
    try { hits = this.ray.intersectObjects(objs, false); } catch { hits = []; }
    this.ray.far = Infinity;
    for (const h of hits) {
      const o = h.object;
      const mi = h.face?.materialIndex ?? 0;
      const t = this.targets.find((x) => x.live && x.slots.some((s) => s.obj === o && (s.index < 0 || s.index === mi)));
      if (!t) continue;
      const uv = t.channel > 0 ? h["uv" + t.channel] : h.uv;
      if (!uv) continue;
      const tv = new T.Vector3(uv.x, uv.y, 1).applyMatrix3(t.uvXf);
      const nn = h.face?.normal ? h.face.normal.clone().transformDirection(o.matrixWorld) : n;
      return { point: [h.point.x, h.point.y, h.point.z], normal: [nn.x, nn.y, nn.z], object: o, key: this.host.keyOf(o), target: t.id, tex: [tv.x, tv.y], face: h.faceIndex ?? -1 };
    }
    return null;
  }

  private logDab(hit: PaintHit, worldR: number, d: ScreenDab, from: any = null) {
    const st = this.st!;
    const T = this.T;
    const t = this.targets.find((x) => x.id === hit.target);
    if (!t || !hit.key) return;
    const o = hit.object;
    o.updateWorldMatrix(true, false);
    const inv = o.matrixWorld.clone().invert();
    const lp = new T.Vector3(...hit.point).applyMatrix4(inv);
    const ln = new T.Vector3(...hit.normal).transformDirection(inv);
    const sc = new T.Vector3();
    o.matrixWorld.decompose(new T.Vector3(), new T.Quaternion(), sc);
    const s = Math.max(1e-9, (Math.abs(sc.x) + Math.abs(sc.y) + Math.abs(sc.z)) / 3);
    if (!st.log.has(t)) st.log.set(t, []);
    const r5 = (v: number) => Math.round(v * 1e5) / 1e5;
    const root = this.host.mirrorRoot();
    root.updateWorldMatrix(true, false);
    const rinv = root.matrixWorld.clone().invert();
    const wp = new T.Vector3(...hit.point).applyMatrix4(rinv);
    const wn = new T.Vector3(...hit.normal).transformDirection(rinv);
    const rsc = new T.Vector3();
    root.matrixWorld.decompose(new T.Vector3(), new T.Quaternion(), rsc);
    const rs = Math.max(1e-9, (Math.abs(rsc.x) + Math.abs(rsc.y) + Math.abs(rsc.z)) / 3);
    st.log.get(t)!.push({ k: hit.key, p: [r5(lp.x), r5(lp.y), r5(lp.z)], n: [r5(ln.x), r5(ln.y), r5(ln.z)], r: r5(worldR / s), a: r5(d.alpha), g: r5(d.angle), s: d.seed,
      w: [r5(wp.x), r5(wp.y), r5(wp.z)], wn: [r5(wn.x), r5(wn.y), r5(wn.z)], wr: r5(worldR / rs), u: [r5(hit.tex[0]), r5(hit.tex[1])],
      ...(from ? { vt: this.toSurfaceFrame(from.clone().transformDirection(inv), ln) } : {}) });
  }

  /** The frame a dab's direction is kept in: x and y along the surface, z along its normal — the
   *  same frame the shader builds, from the normal alone, so it can be built again after a rebuild. */
  private surfaceFrame(n: any): { t0: any; b0: any; n: any } {
    const T = this.T;
    const nn = n.clone().normalize();
    const t0 = (Math.abs(nn.y) < 0.99 ? new T.Vector3(0, 1, 0) : new T.Vector3(1, 0, 0)).cross(nn).normalize();
    return { t0, b0: nn.clone().cross(t0), n: nn };
  }

  private toSurfaceFrame(v: any, n: any): [number, number, number] {
    const f = this.surfaceFrame(n);
    const r5 = (x: number) => Math.round(x * 1e5) / 1e5;
    const w = v.clone().normalize();
    return [r5(w.dot(f.t0)), r5(w.dot(f.b0)), r5(w.dot(f.n))];
  }

  /** An attribute as a plain array, `size` values per vertex: an interleaved or quantised attribute
   *  (a GLB can carry either) is read through getX/getY/getZ. `copy` always gives a new array. */
  private plain(attr: any, size: number, copy = false): ArrayLike<number> {
    if (!attr.isInterleavedBufferAttribute && !attr.normalized && attr.itemSize === size && attr.array instanceof Float32Array) {
      return copy ? attr.array.slice(0, attr.count * size) : attr.array;
    }
    const out = new Float32Array(attr.count * size);
    for (let i = 0; i < attr.count; i++) {
      out[i * size] = attr.getX(i);
      if (size > 1) out[i * size + 1] = attr.getY(i);
      if (size > 2) out[i * size + 2] = attr.getZ(i);
    }
    return out;
  }

  private setFoot(m: any, t: Target, dabs: GpuDab[], cs: any, tip: any | null, hard: number, frontOnly: boolean) {
    const u = m.uniforms;
    u.uUvXf.value.copy(t.uvXf);
    u.uViewProj.value.copy(cs.vp);
    u.uViewport.value.set(cs.vw, cs.vh);
    u.uDepth.value = this.depthRT ? this.depthRT.texture : null;
    u.uFar.value = cs.far;
    u.uView.value.copy(cs.view);
    u.uEye.value.copy(cs.eye);
    u.uFwd.value.copy(cs.fwd);
    u.uOrtho.value = cs.ortho ? 1 : 0;
    u.uFrontOnly.value = frontOnly ? 1 : 0;
    u.uCount.value = Math.min(16, dabs.length);
    u.uTip.value = tip;
    u.uUseTip.value = tip ? 1 : 0;
    u.uHard.value = hard;
    for (let i = 0; i < 16; i++) {
      const d = dabs[i];
      u.uD.value[i].set(0, 0, 0, 0);
      if (!d) { u.uA.value[i].set(0, 0, 1, 0); u.uB.value[i].set(0, 0, 0, 0); u.uC.value[i].set(0, 0, 0, 0); continue; }
      if (d.view) {
        u.uA.value[i].set(d.x, d.y, Math.max(0.5, d.r), d.alpha);
        u.uB.value[i].set(d.angle, 0, 0, 0);
        u.uC.value[i].set(0, 0, 0, 0);
      } else {
        u.uA.value[i].set(d.p.x, d.p.y, d.p.z, Math.max(1e-6, d.R));
        u.uB.value[i].set(d.n.x, d.n.y, d.n.z, d.alpha);
        u.uC.value[i].set(d.angle, d.vd ? 2 : 1, 0, 0);
        if (d.vd) u.uD.value[i].set(d.vd.x, d.vd.y, d.vd.z, 0);
      }
    }
  }

  private flush() {
    const st = this.st;
    if (!st || !st.batch.length) return;
    const batch = st.batch.splice(0, st.batch.length);
    this.gl(() => {
      for (const t of this.liveTargets()) {
        for (let i = 0; i < batch.length; i += 16) {
          const m = this.rasterMat("dab", t.channel);
          this.setFoot(m, t, batch.slice(i, i + 16), st.view, st.tip, st.hard, st.opts.frontOnly);
          this.stage(t, m);
          this.r.setRenderTarget(t.stroke);
          this.r.render(this.rScene, this.cam);
        }
        t.dirty = true;
        st.touched.add(t);
      }
    });
  }

  /** Smudge, blur and clone, one dab: into the layer of the texture under the dab. */
  private pixelDab(dabs: GpuDab[], hit: PaintHit | null, prev: PaintHit | null) {
    const st = this.st!;
    if (!hit) return;
    const t = this.targets.find((x) => x.id === hit.target && x.live);
    if (!t) return;
    let offset: [number, number] = [0, 0];
    if (st.tool === "smudge") {
      if (!prev || prev.target !== hit.target) return;
      offset = [prev.tex[0] - hit.tex[0], prev.tex[1] - hit.tex[1]];
      // A jump across a seam is a jump across the texture: nothing to drag from there.
      if (Math.hypot(offset[0], offset[1]) > 0.08) return;
    } else if (st.tool === "clone") {
      const src = this.cloneSource!;
      if (!this.cloneOffset) this.cloneOffset = [src.tex[0] - hit.tex[0], src.tex[1] - hit.tex[1]];
      offset = this.cloneOffset;
    }
    const src = st.tool === "clone" ? t.snapshot : t.composite;
    this.gl(() => {
      const rt = this.activeRT(t);
      const m = this.rasterMat("pixel", t.channel);
      this.setFoot(m, t, dabs, st.view, st.tip, st.hard, st.opts.frontOnly);
      m.uniforms.uSrc.value = src.texture;
      m.uniforms.uTool.value = st.tool === "smudge" ? 1 : st.tool === "blur" ? 2 : 3;
      m.uniforms.uOffset.value.set(offset[0], offset[1]);
      m.uniforms.uTexel.value.set(1 / t.w, 1 / t.h);
      const rTex = dabs[0].view ? Math.max(1, dabs[0].r * 0.15) : 2;
      m.uniforms.uBlurR.value = clamp(rTex, 1, 8);
      m.uniforms.uStrength.value = clamp(st.opts.brush.strength, 0, 1);
      this.stage(t, m);
      this.r.setRenderTarget(rt);
      this.r.render(this.rScene, this.cam);
      // Coverage too, so the undo knows which tiles this stroke changed.
      const c = this.rasterMat("dab", t.channel);
      this.setFoot(c, t, dabs, st.view, st.tip, st.hard, st.opts.frontOnly);
      this.stage(t, c);
      this.r.setRenderTarget(t.stroke);
      this.r.render(this.rScene, this.cam);
    });
    t.dirty = true;
    st.touched.add(t);
  }

  /** End the stroke: bake it into its layer, fill the seams, and make it one undo step. */
  end(label = ""): boolean {
    const st = this.st;
    if (!st) return false;
    this.flush();
    const parts: Array<{ target: string; layer: string; tiles: TileData[] }> = [];
    // The log entries this stroke made, so an undo takes them out of the log as well as out of the
    // pixels. Without it a layout change drew undone strokes back in.
    const logged: LoggedStroke[] = [];
    let bytes = 0;
    const pixelTool = st.tool === "smudge" || st.tool === "blur" || st.tool === "clone";
    this.gl(() => {
      for (const t of st.touched) {
        const red = this.reduce(t.stroke, t.w, t.h);
        const tiles = dirtyTiles(red.px, red.tx, red.ty, 1);
        if (!tiles.length) continue;
        const layer = this.activeLayer;
        if (!pixelTool) this.bake(t, st.color, st.opts.brush.opacity, st.opts.brush.blend, st.erase);
        this.dilate(t, layer);
        const runs = tileRuns(tiles, red.tx, TILE, t.w, t.h);
        const data: TileData[] = [];
        for (const rr of runs) {
          const before = new Uint8Array(rr.w * rr.h * 4), after = new Uint8Array(rr.w * rr.h * 4);
          this.r.readRenderTargetPixels(t.backup, rr.x, rr.y, rr.w, rr.h, before);
          this.r.readRenderTargetPixels(t.rts.get(layer), rr.x, rr.y, rr.w, rr.h, after);
          data.push({ ...rr, before, after });
          bytes += before.byteLength * 2;
        }
        parts.push({ target: t.id, layer, tiles: data });
        if (pixelTool) { t.unreplayable++; logged.push({ t: t.id, px: true }); }
        else {
          const dabsLogged = st.log.get(t) || [];
          if (dabsLogged.length) {
            const rec: StrokeRecord = {
              tool: st.erase ? "eraser" : "brush", layer, color: rgbToHex(st.color), opacity: st.opts.brush.opacity,
              hardness: st.opts.brush.hardness, tip: st.opts.brush.tip, blend: st.opts.brush.blend, dabs: dabsLogged, fill: null,
            };
            t.strokes.push(rec);
            logged.push({ t: t.id, s: rec });
            if (!t.anchor) t.anchor = this.snapshot(t);
            const capped = capStrokes(t.strokes);
            t.strokes = capped.kept;
            t.unreplayable += capped.dropped;
          }
        }
        this.clear(t.stroke);
        t.dirty = true;
      }
    });
    this.st = null;
    if (parts.length) {
      this.history.push({ label: label || st.tool, target: parts[0].target, layer: this.activeLayer, op: { kind: "stroke", parts, bytes, logged } });
    }
    this.touch();
    return parts.length > 0;
  }

  /** Throw the stroke in progress away, as if it never started. */
  cancel() {
    const st = this.st;
    if (!st) return;
    this.gl(() => {
      for (const t of st.touched) {
        const rt = t.rts.get(this.activeLayer);
        if (rt) this.copy(t.backup, rt);
        this.clear(t.stroke);
        t.dirty = true;
      }
    });
    this.st = null;
    this.touch();
  }

  get stroking(): boolean { return !!this.st; }

  private bake(t: Target, color: [number, number, number], opacity: number, blend: BlendMode, erase: boolean) {
    const rt = t.rts.get(this.activeLayer);
    if (!rt) return;
    const m = this.mats.bake;
    m.uniforms.uLayer.value = rt.texture;
    m.uniforms.uStroke.value = t.stroke.texture;
    m.uniforms.uColor.value.set(color[0], color[1], color[2]);
    m.uniforms.uOpacity.value = clamp(opacity, 0, 1);
    m.uniforms.uMode.value = BLEND_INDEX[blend] ?? 0;
    m.uniforms.uErase.value = erase ? 1 : 0;
    this.quad(m, t.spare);
    t.rts.set(this.activeLayer, t.spare);
    t.spare = rt;
  }

  private dilateInto(src: any, mask: any, into: any, w: number, h: number) {
    const m = this.mats.dilate;
    m.uniforms.uSrc.value = src.texture;
    m.uniforms.uMask.value = mask.texture;
    m.uniforms.uTexel.value.set(1 / w, 1 / h);
    this.quad(m, into);
  }

  private dilate(t: Target, layer: string) {
    const rt = t.rts.get(layer);
    if (!rt) return;
    this.dilateInto(rt, t.mask, t.spare, t.w, t.h);
    t.rts.set(layer, t.spare);
    t.spare = rt;
  }

  /** A render target reduced to one RGBA8 texel per 64 x 64 tile, read back. */
  private reduce(src: any, w: number, h: number): { px: Uint8Array; tx: number; ty: number } {
    const w1 = Math.ceil(w / 8), h1 = Math.ceil(h / 8);
    const tx = Math.ceil(w1 / 8), ty = Math.ceil(h1 / 8);
    const a = this.rt(w1, h1), b = this.rt(tx, ty);
    const m = this.mats.reduce;
    m.uniforms.uSrc.value = src.texture;
    m.uniforms.uSrcTexel.value.set(1 / w, 1 / h);
    this.quad(m, a);
    m.uniforms.uSrc.value = a.texture;
    m.uniforms.uSrcTexel.value.set(1 / w1, 1 / h1);
    this.quad(m, b);
    const px = new Uint8Array(tx * ty * 4);
    this.r.readRenderTargetPixels(b, 0, 0, tx, ty, px);
    a.dispose(); b.dispose();
    return { px, tx, ty };
  }

  // ---------------------------------------------------------------- islands
  /** The triangles of one UV island of a mesh, as a geometry that shares the mesh's attributes. */
  private islandGeo(t: Target, obj: any, face: number): any {
    const T = this.T;
    const g = obj.geometry;
    const uv = g?.attributes?.[this.uvName(t.channel)];
    if (!uv || face < 0) return null;
    let isl = t.islands.get(g);
    if (!isl) {
      isl = triangleIslands(g.index ? g.index.array : null, this.plain(uv, 2), this.plain(g.attributes.position, 3), g.attributes.position.count);
      t.islands.set(g, isl);
    }
    if (face >= isl.length) return null;
    const id = isl[face];
    const idx: number[] = [];
    for (let tri = 0; tri < isl.length; tri++) {
      if (isl[tri] !== id) continue;
      if (g.index) idx.push(g.index.getX(tri * 3), g.index.getX(tri * 3 + 1), g.index.getX(tri * 3 + 2));
      else idx.push(tri * 3, tri * 3 + 1, tri * 3 + 2);
    }
    const geo = new T.BufferGeometry();
    for (const name of Object.keys(g.attributes)) geo.setAttribute(name, g.attributes[name]);
    geo.setIndex(idx);
    return geo;
  }

  // ---------------------------------------------------------------- one-click tools
  /** The paint colour under a point: the texture as painted, without the light on it. */
  pick(hit: PaintHit): string | null {
    const t = this.targets.find((x) => x.id === hit.target);
    if (!t) return null;
    const px = new Uint8Array(4);
    this.gl(() => {
      if (t.dirty) this.composite(t);
      const x = clamp(Math.floor(hit.tex[0] * t.w), 0, t.w - 1), y = clamp(Math.floor(hit.tex[1] * t.h), 0, t.h - 1);
      this.r.readRenderTargetPixels(t.composite, x, y, 1, 1, px);
    });
    return rgbToHex([px[0] / 255, px[1] / 255, px[2] / 255]);
  }

  /** Fill the whole part under the point, or only its UV island, with the brush colour. */
  fill(hit: PaintHit, opts: StrokeOptions, island: boolean): boolean {
    const t = this.targets.find((x) => x.id === hit.target && x.live);
    if (!t) return false;
    const T = this.T;
    const geo: any = island && hit.face >= 0 ? this.islandGeo(t, hit.object, hit.face) : null;
    const color = hexToRgb(opts.color);
    let parts: Array<{ target: string; layer: string; tiles: TileData[] }> = [];
    let bytes = 0;
    this.gl(() => {
      const rt = this.activeRT(t);
      this.copy(rt, t.backup);
      this.clear(t.stroke);
      const m = this.rasterMat("fill", t.channel);
      m.uniforms.uUvXf.value.copy(t.uvXf);
      this.stage(t, m, { obj: hit.object, geo });
      this.r.setRenderTarget(t.stroke);
      this.r.render(this.rScene, this.cam);
      const red = this.reduce(t.stroke, t.w, t.h);
      const tiles = dirtyTiles(red.px, red.tx, red.ty, 1);
      this.bake(t, color, opts.brush.opacity, opts.brush.blend, false);
      this.dilate(t, this.activeLayer);
      const data: TileData[] = [];
      for (const rr of tileRuns(tiles, red.tx, TILE, t.w, t.h)) {
        const before = new Uint8Array(rr.w * rr.h * 4), after = new Uint8Array(rr.w * rr.h * 4);
        this.r.readRenderTargetPixels(t.backup, rr.x, rr.y, rr.w, rr.h, before);
        this.r.readRenderTargetPixels(t.rts.get(this.activeLayer), rr.x, rr.y, rr.w, rr.h, after);
        data.push({ ...rr, before, after });
        bytes += before.byteLength * 2;
      }
      parts = [{ target: t.id, layer: this.activeLayer, tiles: data }];
      this.clear(t.stroke);
    });
    geo?.dispose?.();
    // Where it was clicked, on the mesh and in the asset: a redraw after a layout change finds the
    // island again from the point, because the triangle numbers may have moved.
    const r5 = (v: number) => Math.round(v * 1e5) / 1e5;
    const o = hit.object;
    o.updateWorldMatrix(true, false);
    const lp = new T.Vector3(...hit.point).applyMatrix4(o.matrixWorld.clone().invert());
    const root = this.host.mirrorRoot();
    root.updateWorldMatrix(true, false);
    const wp = new T.Vector3(...hit.point).applyMatrix4(root.matrixWorld.clone().invert());
    const rec: StrokeRecord = {
      tool: "fill", layer: this.activeLayer, color: rgbToHex(color), opacity: opts.brush.opacity, hardness: 1, tip: "round",
      blend: opts.brush.blend, dabs: [],
      fill: { k: hit.key, tri: hit.face, island, p: [r5(lp.x), r5(lp.y), r5(lp.z)], w: [r5(wp.x), r5(wp.y), r5(wp.z)], u: [r5(hit.tex[0]), r5(hit.tex[1])] },
    };
    t.strokes.push(rec);
    if (!t.anchor) t.anchor = this.snapshot(t);
    this.history.push({
      label: island ? "fill island" : "fill part", target: t.id, layer: this.activeLayer,
      op: { kind: "stroke", parts, bytes, logged: [{ t: t.id, s: rec }] },
    });
    t.dirty = true;
    this.touch();
    return true;
  }

  setCloneSource(hit: PaintHit) {
    this.cloneSource = { target: hit.target, tex: [hit.tex[0], hit.tex[1]] };
    this.cloneOffset = null;
    this.note = "clone source set — paint to copy from there";
    this.touch();
  }

  // ---------------------------------------------------------------- the composite
  private composite(t: Target) {
    const st = this.st;
    const init = this.mats.init;
    init.uniforms.uBase.value = t.base || null;
    init.uniforms.uHasBase.value = t.base ? 1 : 0;
    init.uniforms.uToGamma.value = t.srgb && !t.baseGamma ? 1 : 0;
    let acc = t.accA, nxt = t.accB;
    this.quad(init, acc);
    const lm = this.mats.layer;
    for (const meta of this.layers) {
      if (!meta.visible) continue;
      const rt = t.rts.get(meta.id);
      if (!rt) continue;
      const preview = !!st && meta.id === this.activeLayer && (st.tool === "brush" || st.tool === "eraser");
      lm.uniforms.uAcc.value = acc.texture;
      lm.uniforms.uLayer.value = rt.texture;
      lm.uniforms.uOpacity.value = meta.opacity;
      lm.uniforms.uMode.value = BLEND_INDEX[meta.blend] ?? 0;
      lm.uniforms.uPreview.value = preview ? 1 : 0;
      lm.uniforms.uStroke.value = t.stroke.texture;
      if (st) {
        lm.uniforms.uSColor.value.set(st.color[0], st.color[1], st.color[2]);
        lm.uniforms.uSOpacity.value = st.opts.brush.opacity;
        lm.uniforms.uSMode.value = BLEND_INDEX[st.opts.brush.blend] ?? 0;
        lm.uniforms.uSErase.value = st.erase ? 1 : 0;
      }
      this.quad(lm, nxt);
      const x = acc; acc = nxt; nxt = x;
    }
    this.copy(acc, t.composite);
    const dm = this.mats.display;
    dm.uniforms.uSrc.value = t.composite.texture;
    dm.uniforms.uToLinear.value = t.srgb ? 1 : 0;
    this.quad(dm, t.display);
    t.dirty = false;
  }

  /** Called once per frame by the viewport, before it draws: bring every changed texture up to date. */
  frame() {
    let any = false;
    for (const t of this.targets) if (t.live && t.dirty && t.composite) { any = true; break; }
    if (!any) return;
    this.gl(() => { for (const t of this.targets) if (t.live && t.dirty && t.composite) this.composite(t); });
  }

  private touch() {
    this.version++;
    for (const t of this.targets) t.dirty = true;
    try { this.onChange?.(); } catch { /* the panel's problem */ }
  }

  // ---------------------------------------------------------------- layers
  private addLayerMeta(name: string, at = this.layers.length): PaintLayerMeta {
    const meta: PaintLayerMeta = { id: layerId(this.layers.map((l) => l.id)), name, visible: true, opacity: 1, blend: "normal" };
    this.layers.splice(at, 0, meta);
    this.activeLayer = meta.id;
    return meta;
  }

  addLayer(name = ""): string {
    const at = Math.max(0, this.layers.findIndex((l) => l.id === this.activeLayer) + 1) || this.layers.length;
    const meta = this.addLayerMeta(name || "Layer " + (this.layers.length + 1), this.layers.length ? at : 0);
    this.history.push({ label: "add layer", target: "", layer: meta.id, op: { kind: "add", meta: { ...meta }, index: this.layers.indexOf(meta), bytes: 0 } });
    this.touch();
    return meta.id;
  }

  setActive(id: string) {
    if (this.layers.some((l) => l.id === id)) { this.activeLayer = id; this.touch(); }
  }

  setLayer(id: string, props: Partial<Pick<PaintLayerMeta, "name" | "visible" | "opacity" | "blend">>) {
    const meta = this.layers.find((l) => l.id === id);
    if (!meta) return;
    const before = { name: meta.name, visible: meta.visible, opacity: meta.opacity, blend: meta.blend };
    Object.assign(meta, props);
    this.history.push({ label: "layer settings", target: "", layer: id, op: { kind: "props", id, before, after: { ...before, ...props }, bytes: 0 } });
    this.touch();
  }

  moveLayer(id: string, dir: -1 | 1) {
    const i = this.layers.findIndex((l) => l.id === id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= this.layers.length) return;
    const [m] = this.layers.splice(i, 1);
    this.layers.splice(j, 0, m);
    this.history.push({ label: "move layer", target: "", layer: id, op: { kind: "move", id, from: i, to: j, bytes: 0 } });
    this.touch();
  }

  /** A layer's pixels in every texture, copied — what an undo of a delete or a clear needs back. */
  private snapshotLayer(id: string): { rts: Map<string, any>; bytes: number } {
    const rts = new Map<string, any>();
    let bytes = 0;
    this.gl(() => {
      for (const t of this.targets) {
        const rt = t.rts.get(id);
        if (!rt || !t.composite) continue;
        const c = this.rt(t.w, t.h);
        this.copy(rt, c);
        rts.set(t.id, c);
        bytes += t.w * t.h * 4;
      }
    });
    return { rts, bytes };
  }

  removeLayer(id: string) {
    const i = this.layers.findIndex((l) => l.id === id);
    if (i < 0) return;
    const meta = this.layers[i];
    const rts = new Map<string, any>();
    for (const t of this.targets) { const rt = t.rts.get(id); if (rt) { rts.set(t.id, rt); t.rts.delete(id); } }
    this.layers.splice(i, 1);
    if (this.activeLayer === id) this.activeLayer = (this.layers[Math.max(0, i - 1)] || this.layers[0])?.id || "";
    let bytes = 0;
    for (const t of this.targets) if (rts.has(t.id)) bytes += t.w * t.h * 4;
    this.history.push({
      label: "delete layer", target: "", layer: id, op: { kind: "remove", meta: { ...meta }, index: i, rts, bytes },
      free: () => { /* the pixels live on only while an undo could bring them back */ for (const rt of rts.values()) rt.dispose(); },
    });
    this.touch();
  }

  clearLayer(id: string) {
    if (!this.layers.some((l) => l.id === id)) return;
    const snap = this.snapshotLayer(id);
    this.gl(() => { for (const t of this.targets) { const rt = t.rts.get(id); if (rt) this.clear(rt); } });
    const removed: LoggedStroke[] = [];
    for (const t of this.targets) {
      for (const s of t.strokes) if (s.layer === id) removed.push({ t: t.id, s });
      t.strokes = t.strokes.filter((s) => s.layer !== id);
    }
    this.history.push({
      label: "clear layer", target: "", layer: id, op: { kind: "clear", id, rts: snap.rts, bytes: snap.bytes, removed },
      free: () => { for (const rt of snap.rts.values()) rt.dispose(); },
    });
    this.touch();
  }

  mergeDown(id: string) {
    const i = this.layers.findIndex((l) => l.id === id);
    if (i <= 0) return;
    const high = this.layers[i], low = this.layers[i - 1];
    const lowSnap = this.snapshotLayer(low.id);
    const highRts = new Map<string, any>();
    this.gl(() => {
      for (const t of this.targets) {
        const hr = t.rts.get(high.id);
        if (!hr) continue;
        let lr = t.rts.get(low.id);
        if (!lr) { lr = this.rt(t.w, t.h); this.clear(lr); t.rts.set(low.id, lr); }
        const m = this.mats.merge;
        m.uniforms.uLow.value = lr.texture;
        m.uniforms.uHigh.value = hr.texture;
        m.uniforms.uOpacity.value = high.visible ? high.opacity : 0;
        m.uniforms.uMode.value = BLEND_INDEX[high.blend] ?? 0;
        this.quad(m, t.spare);
        t.rts.set(low.id, t.spare);
        t.spare = lr;
        highRts.set(t.id, hr);
        t.rts.delete(high.id);
      }
    });
    this.layers.splice(i, 1);
    this.activeLayer = low.id;
    // Its strokes go down with its pixels, so a layout change still has them to redraw. (A redraw
    // paints them at their own opacity: the merged layer's opacity and mode are not carried.)
    const moved: LoggedStroke[] = [];
    for (const t of this.targets) for (const s of t.strokes) if (s.layer === high.id) { s.layer = low.id; moved.push({ t: t.id, s }); }
    let bytes = lowSnap.bytes;
    for (const t of this.targets) if (highRts.has(t.id)) bytes += t.w * t.h * 4;
    this.history.push({
      label: "merge down", target: "", layer: low.id, op: { kind: "merge", high: { ...high }, index: i, low: low.id, lowRts: lowSnap.rts, highRts, bytes, moved },
      free: () => { for (const rt of lowSnap.rts.values()) rt.dispose(); for (const rt of highRts.values()) rt.dispose(); },
    });
    this.touch();
  }

  // ---------------------------------------------------------------- undo
  undo(): string { const r = this.history.takeUndo(); if (!r) return ""; this.apply(r, true); this.touch(); return r.label; }
  redo(): string { const r = this.history.takeRedo(); if (!r) return ""; this.apply(r, false); this.touch(); return r.label; }

  private writeTiles(t: Target, layer: string, tiles: TileData[], useBefore: boolean) {
    const T = this.T;
    let rt = t.rts.get(layer);
    if (!rt) { rt = this.rt(t.w, t.h); this.clear(rt); t.rts.set(layer, rt); }
    for (const tile of tiles) {
      const tex = new T.DataTexture(useBefore ? tile.before : tile.after, tile.w, tile.h, T.RGBAFormat);
      tex.minFilter = T.NearestFilter; tex.magFilter = T.NearestFilter; tex.needsUpdate = true;
      rt.viewport.set(tile.x, tile.y, tile.w, tile.h);
      rt.scissor.set(tile.x, tile.y, tile.w, tile.h);
      rt.scissorTest = true;
      this.copy(tex, rt);
      rt.viewport.set(0, 0, t.w, t.h);
      rt.scissor.set(0, 0, t.w, t.h);
      rt.scissorTest = false;
      tex.dispose();
    }
  }

  private apply(r: PaintRecord, undo: boolean) {
    const op = r.op || { kind: "" };
    this.gl(() => {
      if (op.kind === "stroke") {
        for (const p of op.parts) {
          const t = this.targets.find((x) => x.id === p.target);
          if (t && t.composite) this.writeTiles(t, p.layer, p.tiles, undo);
        }
        for (const l of (op.logged || []) as LoggedStroke[]) {
          const t = this.targets.find((x) => x.id === l.t);
          if (!t) continue;
          if (l.px) t.unreplayable = Math.max(0, t.unreplayable + (undo ? -1 : 1));
          if (l.s) {
            if (undo) t.strokes = t.strokes.filter((s) => s !== l.s);
            else if (!t.strokes.includes(l.s)) t.strokes.push(l.s);
          }
        }
        return;
      }
      if (op.kind === "add") {
        if (undo) {
          const i = this.layers.findIndex((l) => l.id === op.meta.id);
          if (i >= 0) this.layers.splice(i, 1);
          for (const t of this.targets) { const rt = t.rts.get(op.meta.id); if (rt) { rt.dispose(); t.rts.delete(op.meta.id); } }
          if (this.activeLayer === op.meta.id) this.activeLayer = this.layers[this.layers.length - 1]?.id || "";
        } else {
          this.layers.splice(Math.min(op.index, this.layers.length), 0, { ...op.meta });
          this.activeLayer = op.meta.id;
        }
        return;
      }
      if (op.kind === "props") {
        const meta = this.layers.find((l) => l.id === op.id);
        if (meta) Object.assign(meta, undo ? op.before : op.after);
        return;
      }
      if (op.kind === "move") {
        const i = this.layers.findIndex((l) => l.id === op.id);
        if (i < 0) return;
        const [m] = this.layers.splice(i, 1);
        this.layers.splice(undo ? op.from : op.to, 0, m);
        return;
      }
      if (op.kind === "remove") {
        if (undo) {
          this.layers.splice(Math.min(op.index, this.layers.length), 0, { ...op.meta });
          for (const t of this.targets) {
            const src = op.rts.get(t.id);
            if (!src) continue;
            const rt = this.rt(t.w, t.h);
            this.copy(src, rt);
            t.rts.set(op.meta.id, rt);
          }
          this.activeLayer = op.meta.id;
        } else {
          const i = this.layers.findIndex((l) => l.id === op.meta.id);
          if (i >= 0) this.layers.splice(i, 1);
          for (const t of this.targets) { const rt = t.rts.get(op.meta.id); if (rt) { rt.dispose(); t.rts.delete(op.meta.id); } }
          if (this.activeLayer === op.meta.id) this.activeLayer = this.layers[this.layers.length - 1]?.id || "";
        }
        return;
      }
      if (op.kind === "clear") {
        for (const t of this.targets) {
          const rt = t.rts.get(op.id);
          if (!rt) continue;
          const src = op.rts.get(t.id);
          if (undo && src) this.copy(src, rt); else this.clear(rt);
        }
        for (const r of (op.removed || []) as LoggedStroke[]) {
          const t = this.targets.find((x) => x.id === r.t);
          if (!t || !r.s) continue;
          if (undo) { if (!t.strokes.includes(r.s)) t.strokes.push(r.s); }
          else t.strokes = t.strokes.filter((s) => s !== r.s);
        }
        return;
      }
      if (op.kind === "merge") {
        if (undo) {
          this.layers.splice(Math.min(op.index, this.layers.length), 0, { ...op.high });
          for (const t of this.targets) {
            const lowSrc = op.lowRts.get(t.id), highSrc = op.highRts.get(t.id);
            if (lowSrc) { const rt = t.rts.get(op.low) || this.rt(t.w, t.h); this.copy(lowSrc, rt); t.rts.set(op.low, rt); }
            else if (highSrc) { const rt = t.rts.get(op.low); if (rt) this.clear(rt); }
            if (highSrc) { const rt = this.rt(t.w, t.h); this.copy(highSrc, rt); t.rts.set(op.high.id, rt); }
          }
          this.activeLayer = op.high.id;
          for (const mv of (op.moved || []) as LoggedStroke[]) if (mv.s) mv.s.layer = op.high.id;
        } else {
          for (const mv of (op.moved || []) as LoggedStroke[]) if (mv.s) mv.s.layer = op.low;
          const i = this.layers.findIndex((l) => l.id === op.high.id);
          for (const t of this.targets) {
            const hr = t.rts.get(op.high.id);
            if (!hr) continue;
            let lr = t.rts.get(op.low);
            if (!lr) { lr = this.rt(t.w, t.h); this.clear(lr); t.rts.set(op.low, lr); }
            const m = this.mats.merge;
            m.uniforms.uLow.value = lr.texture; m.uniforms.uHigh.value = hr.texture;
            m.uniforms.uOpacity.value = op.high.visible ? op.high.opacity : 0; m.uniforms.uMode.value = BLEND_INDEX[op.high.blend as BlendMode] ?? 0;
            this.quad(m, t.spare);
            t.rts.set(op.low, t.spare); t.spare = lr;
            hr.dispose(); t.rts.delete(op.high.id);
          }
          if (i >= 0) this.layers.splice(i, 1);
          this.activeLayer = op.low;
        }
      }
    });
  }

  // ---------------------------------------------------------------- following the surface
  /** A texture's meshes as they are now, copied. */
  private snapshot(t: Target): AnchorSnap {
    const T = this.T;
    const root = this.host.mirrorRoot();
    root.updateWorldMatrix(true, false);
    const rinv = root.matrixWorld.clone().invert();
    const items: AnchorItem[] = [];
    const seen = new Set<any>();
    for (const sl of t.slots) {
      const o = sl.obj, g = o?.geometry;
      const uv = g?.attributes?.[this.uvName(t.channel)];
      const key = o ? this.host.keyOf(o) || "" : "";
      if (!g || !uv || !g.attributes.position || !key || seen.has(o)) continue;
      seen.add(o);
      o.updateWorldMatrix(true, false);
      items.push({
        key, uv: this.plain(uv, 2, true), pos: this.plain(g.attributes.position, 3, true),
        nrm: g.attributes.normal ? this.plain(g.attributes.normal, 3, true) : null,
        index: g.index ? Array.from(g.index.array as ArrayLike<number>) : null, count: g.attributes.position.count,
        toAsset: new T.Matrix4().multiplyMatrices(rinv, o.matrixWorld),
      });
    }
    return { items, xf: this.xfOf(t) };
  }

  private xfOf(t: Target): number[] {
    return t.uvXf ? Array.from(t.uvXf.elements as ArrayLike<number>) : [1, 0, 0, 0, 1, 0, 0, 0, 1];
  }

  /** A surface point from a triangle and its weights, on a mesh given by its arrays. */
  private surfacePoint(key: string, pos: ArrayLike<number>, nrm: ArrayLike<number> | null, uv: ArrayLike<number> | null,
    index: ArrayLike<number> | null, tri: number, b: ArrayLike<number>, toAsset: any, xf: number[]): SurfacePoint {
    const T = this.T;
    const r5 = (v: number) => Math.round(v * 1e5) / 1e5;
    const lp = lerpTri(pos, 3, index, tri, b);
    const corner = (k: number) => { const v = triVertex(index, tri, k); return new T.Vector3(pos[v * 3], pos[v * 3 + 1], pos[v * 3 + 2]); };
    const face = () => { const a = corner(0); return corner(1).sub(a).cross(corner(2).sub(a)); };
    let nl = nrm ? new T.Vector3(...lerpTri(nrm, 3, index, tri, b)) : face();
    if (nl.lengthSq() < 1e-20) nl = face();
    if (nl.lengthSq() < 1e-20) nl.set(0, 1, 0);
    nl.normalize();
    const ap = new T.Vector3(lp[0], lp[1], lp[2]).applyMatrix4(toAsset);
    const an = nl.clone().applyMatrix3(new T.Matrix3().getNormalMatrix(toAsset)).normalize();
    let u: [number, number] | null = null;
    if (uv) {
      const q = lerpTri(uv, 2, index, tri, b);
      u = [r5(xf[0] * q[0] + xf[3] * q[1] + xf[6]), r5(xf[1] * q[0] + xf[4] * q[1] + xf[7])];
    }
    const sc = new T.Vector3();
    toAsset.decompose(new T.Vector3(), new T.Quaternion(), sc);
    return {
      key, local: [r5(lp[0]), r5(lp[1]), r5(lp[2])], nLocal: [r5(nl.x), r5(nl.y), r5(nl.z)],
      asset: [r5(ap.x), r5(ap.y), r5(ap.z)], nAsset: [r5(an.x), r5(an.y), r5(an.z)], u, tri,
      sA: Math.max(1e-9, (Math.abs(sc.x) + Math.abs(sc.y) + Math.abs(sc.z)) / 3),
    };
  }

  /** Where a texture point is on a snapshot's meshes. Texture space shared by several parts (one
   *  swatch on every spike) finds several: the one on the same mesh key wins, then the one nearest
   *  where the point was. */
  private locate(snap: AnchorSnap, u: ArrayLike<number>, key: string, near: ArrayLike<number> | null): SurfacePoint | null {
    let best: SurfacePoint | null = null, bestScore = Infinity;
    for (const it of snap.items) {
      if (!it.grid) it.grid = buildUvGrid(it.uv, it.index, it.count, snap.xf);
      for (const h of uvLocate(it.grid, u[0], u[1])) {
        const sp = this.surfacePoint(it.key, it.pos, it.nrm, it.uv, it.index, h.tri, h.b, it.toAsset, snap.xf);
        const dn = near ? Math.hypot(sp.asset[0] - near[0], sp.asset[1] - near[1], sp.asset[2] - near[2]) : 0;
        const score = (it.key === key ? 0 : 1e9) + h.d * 1e3 + dn;
        if (score < bestScore) { bestScore = score; best = sp; }
      }
    }
    return best;
  }

  /** Every logged point of a target, measured again on a snapshot's meshes by its texture point. */
  private anchorAll(t: Target, snap: AnchorSnap) {
    for (const s of t.strokes) {
      for (const d of s.dabs) {
        const sp = d.u ? this.locate(snap, d.u, d.k, d.w || null) : null;
        if (!sp) continue;
        d.k = sp.key; d.p = sp.local; d.n = sp.nLocal; d.w = sp.asset; d.wn = sp.nAsset;
        if (d.wr !== undefined) d.r = Math.round((d.wr / sp.sA) * 1e5) / 1e5;
      }
      const f = s.fill;
      const sp = f && f.u ? this.locate(snap, f.u, f.k, f.w || null) : null;
      if (f && sp) { f.k = sp.key; f.p = sp.local; f.w = sp.asset; f.tri = sp.tri; }
    }
  }

  /** The surface point nearest a logged point on the meshes as they are now: on the same mesh while
   *  its key is there, else on whichever of this texture's meshes is nearest. Null when nothing is
   *  within `maxAsset` (asset units): the part was taken away, and its paint goes with it. */
  private snapPoint(t: Target, cache: Map<any, MeshCache>, rinv: any, k: string, local: ArrayLike<number> | null,
    asset: ArrayLike<number> | null, maxAsset: number): SurfacePoint | null {
    const T = this.T;
    const measure = (o: any): MeshCache | null => {
      let mc = cache.get(o);
      if (mc) return mc;
      const g0 = o.geometry, pos = g0?.attributes?.position;
      if (!pos) return null;
      const uv = g0.attributes[this.uvName(t.channel)];
      mc = {
        g: buildPosGrid(this.plain(pos, 3), g0.index ? g0.index.array : null, pos.count),
        nrm: g0.attributes.normal ? this.plain(g0.attributes.normal, 3) : null, uv: uv ? this.plain(uv, 2) : null,
      };
      cache.set(o, mc);
      return mc;
    };
    const tryObj = (o: any, lp: ArrayLike<number>) => {
      const mc = measure(o);
      if (!mc) return null;
      o.updateWorldMatrix(true, false);
      const toAsset = new T.Matrix4().multiplyMatrices(rinv, o.matrixWorld);
      const sc = new T.Vector3();
      toAsset.decompose(new T.Vector3(), new T.Quaternion(), sc);
      const sA = Math.max(1e-9, (Math.abs(sc.x) + Math.abs(sc.y) + Math.abs(sc.z)) / 3);
      const hit = nearestOnGrid(mc.g, lp, maxAsset / sA);
      return hit ? { o, mc, toAsset, hit, dAsset: hit.dist * sA } : null;
    };
    let best: ReturnType<typeof tryObj> = null;
    const keyObj = k ? this.host.objOf(k) : null;
    if (keyObj && local && t.slots.some((s) => s.obj === keyObj)) best = tryObj(keyObj, local);
    if (!best && asset) {
      const seen = new Set<any>();
      for (const sl of t.slots) {
        const o = sl.obj;
        if (!o || seen.has(o) || !this.host.keyOf(o)) continue;
        seen.add(o);
        o.updateWorldMatrix(true, false);
        const inv = new T.Matrix4().multiplyMatrices(rinv, o.matrixWorld).invert();
        const lp = new T.Vector3(asset[0], asset[1], asset[2]).applyMatrix4(inv);
        const r = tryObj(o, [lp.x, lp.y, lp.z]);
        if (r && (!best || r.dAsset < best.dAsset)) best = r;
      }
    }
    if (!best) return null;
    return this.surfacePoint(this.host.keyOf(best.o), best.mc.g.pos, best.mc.nrm, best.mc.uv, best.mc.g.index,
      best.hit.tri, best.hit.b, best.toAsset, this.xfOf(t));
  }

  // ---------------------------------------------------------------- strokes painted again
  /**
   * Paint a target's strokes again from the log, onto fresh layers: what happens when a slider
   * changed the texture layout, so the old pixels would land on the wrong part.
   *
   * Every logged point is first found where it was on the meshes just before the change (`prev`,
   * by its texture point — the layout had not changed until now, so the texture point still named
   * it, whatever sliders had done to the shape since it was painted). Then it is snapped to the
   * nearest surface of the rebuilt meshes, on the same part while its key is there, and drawn as a
   * sphere. The log keeps the new points, so the next change starts from here.
   */
  private replay(t: Target, prev: AnchorSnap | null = null) {
    const T = this.T;
    const cs = this.camState();
    if (prev) this.anchorAll(t, prev);
    const root = this.host.mirrorRoot();
    root.updateWorldMatrix(true, false);
    const rinv = root.matrixWorld.clone().invert();
    const ab = this.assetBox(t);
    const diag = ab ? Math.hypot(ab[3] - ab[0], ab[4] - ab[1], ab[5] - ab[2]) : 0;
    const cache = new Map<any, MeshCache>();
    const skip = new Set<any>();
    const whole = new Set<any>();   // whole-part fills whose part is still there under its key
    for (const s of t.strokes) {
      for (const d of s.dabs) {
        const sp = this.snapPoint(t, cache, rinv, d.k, d.p, d.w || null, Math.max(3 * (d.wr ?? d.r), 0.03 * diag));
        if (!sp) { skip.add(d); continue; }
        d.k = sp.key; d.p = sp.local; d.n = sp.nLocal; d.w = sp.asset; d.wn = sp.nAsset;
        if (sp.u) d.u = sp.u;
        if (d.wr !== undefined) d.r = Math.round((d.wr / sp.sA) * 1e5) / 1e5;
      }
      const f = s.fill;
      if (f) {
        const sp = this.snapPoint(t, cache, rinv, f.k, f.p || null, f.w || null, 0.03 * diag);
        if (!sp) { skip.add(f); continue; }
        if (!f.island && sp.key === f.k) whole.add(f);
        f.k = sp.key; f.p = sp.local; f.w = sp.asset; f.tri = sp.tri;
        if (sp.u) f.u = sp.u;
      }
    }
    this.gl(() => {
      for (const rt of t.rts.values()) this.clear(rt);
      const saveActive = this.activeLayer;
      for (const s of t.strokes) {
        if (!this.layers.some((l) => l.id === s.layer)) continue;
        this.activeLayer = s.layer;
        const rt = this.activeRT(t);
        void rt;
        this.clear(t.stroke);
        if (s.tool === "fill" && s.fill) {
          const f = s.fill;
          const obj = skip.has(f) ? null : this.host.objOf(f.k);
          if (!obj) continue;
          // An island stays an island; a whole-part fill whose part now has another key fills the
          // piece it landed on — never the whole of a merged mesh.
          const geo = whole.has(f) ? null : this.islandGeo(t, obj, f.tri);
          if (!geo && !whole.has(f)) continue;
          const m = this.rasterMat("fill", t.channel);
          m.uniforms.uUvXf.value.copy(t.uvXf);
          this.stage(t, m, { obj, geo });
          this.r.setRenderTarget(t.stroke);
          this.r.render(this.rScene, this.cam);
          geo?.dispose?.();
        } else {
          const dabs: GpuDab[] = [];
          for (const d of s.dabs) {
            const obj = skip.has(d) ? null : this.host.objOf(d.k);
            if (!obj) continue;
            obj.updateWorldMatrix(true, false);
            const p = new T.Vector3(...d.p).applyMatrix4(obj.matrixWorld);
            const n = new T.Vector3(...d.n).applyMatrix3(new T.Matrix3().getNormalMatrix(obj.matrixWorld)).normalize();
            const sc = new T.Vector3();
            obj.matrixWorld.decompose(new T.Vector3(), new T.Quaternion(), sc);
            const scale = (Math.abs(sc.x) + Math.abs(sc.y) + Math.abs(sc.z)) / 3;
            let vd: any = null;
            if (d.vt) {
              // The direction it was painted from, out of the dab's surface frame on the mesh it now
              // sits on, into the world.
              const f = this.surfaceFrame(new T.Vector3(...d.n));
              vd = f.t0.multiplyScalar(d.vt[0]).add(f.b0.multiplyScalar(d.vt[1])).add(f.n.multiplyScalar(d.vt[2]))
                .transformDirection(obj.matrixWorld);
            }
            dabs.push({ view: false, x: 0, y: 0, r: 0, p, n, R: d.r * scale, alpha: d.a, angle: d.g, ...(vd ? { vd } : {}) });
          }
          const tip = this.tip(s.tip);
          for (let i = 0; i < dabs.length; i += 16) {
            const m = this.rasterMat("dab", t.channel);
            this.setFoot(m, t, dabs.slice(i, i + 16), cs, tip, s.hardness, false);
            this.stage(t, m);
            this.r.setRenderTarget(t.stroke);
            this.r.render(this.rScene, this.cam);
          }
        }
        this.bake(t, hexToRgb(s.color), s.opacity, s.blend, s.tool === "eraser");
      }
      for (const id of [...t.rts.keys()]) this.dilate(t, id);
      this.clear(t.stroke);
      this.activeLayer = saveActive;
    });
    t.dirty = true;
  }

  /** The box of every mesh a texture is on, in the asset's own frame: [min xyz, max xyz]. */
  private assetBox(t: Target): number[] | null {
    const T = this.T;
    const root = this.host.mirrorRoot();
    root.updateWorldMatrix(true, false);
    const rinv = root.matrixWorld.clone().invert();
    const box = new T.Box3(), one = new T.Box3(), m = new T.Matrix4();
    const seen = new Set<any>();
    for (const sl of t.slots) {
      const o = sl.obj;
      if (!o?.geometry?.attributes?.position || seen.has(o)) continue;
      seen.add(o);
      o.updateWorldMatrix(true, false);
      o.geometry.computeBoundingBox();
      if (!o.geometry.boundingBox || o.geometry.boundingBox.isEmpty()) continue;
      one.copy(o.geometry.boundingBox).applyMatrix4(m.multiplyMatrices(rinv, o.matrixWorld));
      box.union(one);
    }
    if (box.isEmpty()) return null;
    return [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z];
  }

  // ---------------------------------------------------------------- the file
  /** Everything painted, as the paint file beside the asset. */
  async toDoc(asset: string): Promise<PaintDoc> {
    const targets: PaintTargetDoc[] = [];
    for (const t of this.targets) {
      if (!t.composite || !t.rts.size) continue;
      // The log measured on the meshes as they are now, so the file says where every stroke is.
      if (t.live && t.strokes.length) this.anchorAll(t, this.snapshot(t));
      const layers = [];
      for (const meta of this.layers) {
        const rt = t.rts.get(meta.id);
        if (!rt) continue;
        const px = new Uint8Array(t.w * t.h * 4);
        this.gl(() => this.r.readRenderTargetPixels(rt, 0, 0, t.w, t.h, px));
        const png = await encodePNG(px, t.w, t.h);
        layers.push({ id: meta.id, name: meta.name, visible: meta.visible, opacity: meta.opacity, blend: meta.blend, png: bytesToBase64(png), pm: 1 });
      }
      targets.push({
        key: t.id, name: t.name, size: [t.w, t.h], scale: t.scale, uvSig: t.uvSig, made: t.made,
        active: this.activeLayer, layers, strokes: t.strokes, unreplayable: t.unreplayable,
      });
    }
    for (const o of this.orphans) if (!targets.some((x) => x.key === o.key)) targets.push(o);
    return { version: 1, asset, updated: Date.now() / 1000, layers: this.layers.map((l) => ({ ...l })), targets };
  }

  /** Is there any paint to keep? (A layer with pixels in it, or a texture the Studio made.) */
  get hasPaint(): boolean { return this.targets.some((t) => t.rts.size > 0 || !!t.made) || this.orphans.length > 0; }

  private async decodeLayer(b64: string): Promise<any | null> {
    const T = this.T;
    try {
      const blob = new Blob([base64ToBytes(b64) as unknown as BlobPart], { type: "image/png" });
      const bmp = await createImageBitmap(blob, { premultiplyAlpha: "none", colorSpaceConversion: "none" } as any);
      const tex = new T.Texture(bmp);
      tex.flipY = false;
      tex.premultiplyAlpha = false;
      tex.colorSpace = T.NoColorSpace;
      tex.minFilter = T.NearestFilter;
      tex.magFilter = T.NearestFilter;
      tex.generateMipmaps = false;
      tex.needsUpdate = true;
      return tex;
    } catch { return null; }
  }

  /** Put a paint file's layers back on the textures they were painted on. */
  async loadDoc(doc: PaintDoc): Promise<{ loaded: number; replayed: number; missing: number }> {
    const out = { loaded: 0, replayed: 0, missing: 0 };
    this.layers = (doc.layers && doc.layers.length ? doc.layers : (doc.targets[0]?.layers || [])).map((l) => ({
      id: l.id, name: l.name, visible: l.visible, opacity: l.opacity, blend: l.blend,
    }));
    this.orphans = [];
    for (const td of doc.targets) {
      if (td.made && !this.targets.some((t) => t.id === td.key)) {
        const mk = td.key.replace(/^made\|/, "");
        const [key, slot] = mk.split("#");
        const obj = this.host.objOf(key);
        if (obj) {
          const t = new Target(td.key, td.name);
          t.madeKey = key + "#" + (slot || "0");
          this.targets.push(t);
          t.madeRT = this.rt(td.size[0], td.size[1], { linear: true });
          this.remake(t, obj, td.size[0]);
        }
      }
      let t = this.targets.find((x) => x.id === td.key)
        || this.targets.find((x) => x.name === td.name && x.w === td.size[0] && x.h === td.size[1])
        || this.targets.find((x) => x.name === td.name)
        || (doc.targets.length === 1 && this.targets.length === 1 ? this.targets[0] : null);
      if (!t || !t.composite) { this.orphans.push(td); out.missing++; continue; }
      t.strokes = td.strokes || [];
      t.unreplayable = td.unreplayable || 0;
      if (td.scale && td.scale !== t.scale && !t.madeRT) {
        t.scale = td.scale;
        const [bw, bh] = this.texSize(t.base);
        this.ensureBuffers(t, Math.min(MAX_SIZE, Math.round(bw * t.scale)), Math.min(MAX_SIZE, Math.round(bh * t.scale)));
        this.gl(() => this.buildMask(t!));
      }
      const layoutChanged = !!(td.uvSig && t.uvSig && td.uvSig !== t.uvSig);
      if (layoutChanged && t.strokes.length) {
        for (const l of this.layers) if (!t.rts.has(l.id)) { const rt = this.rt(t.w, t.h); this.gl(() => this.clear(rt)); t.rts.set(l.id, rt); }
        this.replay(t);
        t.anchor = this.snapshot(t);
        out.replayed++;
        continue;
      }
      for (const ld of td.layers) {
        const tex = await this.decodeLayer(ld.png);
        if (!tex) continue;
        const rt = this.rt(t.w, t.h);
        this.gl(() => {
          if (tex.image && (tex.image.width !== t!.w || tex.image.height !== t!.h)) { tex.minFilter = this.T.LinearFilter; tex.magFilter = this.T.LinearFilter; }
          this.copy(tex, rt, ld.pm !== 1);
        });
        tex.dispose();
        try { tex.image?.close?.(); } catch { /* a bitmap already closed */ }
        const old = t.rts.get(ld.id);
        old?.dispose();
        t.rts.set(ld.id, rt);
      }
      if (t.strokes.length) t.anchor = this.snapshot(t);
      out.loaded++;
    }
    const firstActive = doc.targets[0]?.active;
    this.activeLayer = this.layers.some((l) => l.id === firstActive) ? firstActive! : (this.layers[this.layers.length - 1]?.id || "");
    this.history.clear();
    this.host.materialsChanged();
    this.touch();
    return out;
  }

  // ---------------------------------------------------------------- export
  /**
   * Swap every painted texture for a plain canvas copy of it, for GLTFExporter, and hand back the
   * function that puts everything back. A texture made from vertex colours takes the colour
   * attribute off its mesh for the export too: glTF multiplies vertex colours in whatever the
   * material says, and the colours are already in the texture.
   */
  exportSwap(): () => void {
    const T = this.T;
    const restore: Array<() => void> = [];
    this.frame();
    for (const t of this.targets) {
      if (!t.live || !t.composite) continue;
      const px = new Uint8Array(t.w * t.h * 4);
      this.gl(() => { if (t.dirty) this.composite(t); this.r.readRenderTargetPixels(t.composite, 0, 0, t.w, t.h, px); });
      const canvas = document.createElement("canvas");
      canvas.width = t.w; canvas.height = t.h;
      const ctx = canvas.getContext("2d")!;
      const img = ctx.createImageData(t.w, t.h);
      img.data.set(px);
      ctx.putImageData(img, 0, 0);
      const tex = new T.CanvasTexture(canvas);
      tex.flipY = false;
      tex.colorSpace = t.srgb ? T.SRGBColorSpace : T.NoColorSpace;
      const d = t.display.texture;
      tex.offset.copy(d.offset); tex.repeat.copy(d.repeat); tex.rotation = d.rotation; tex.center.copy(d.center);
      tex.wrapS = d.wrapS; tex.wrapT = d.wrapT; tex.channel = d.channel | 0;
      tex.name = (t.base?.name || t.name).replace(/ \(painted\)$/, "") || "painted";
      for (const sl of t.slots) {
        const m = sl.mat, prev = m.map;
        m.map = tex;
        restore.push(() => { m.map = prev; });
      }
      if (t.made?.from === "vertex") {
        for (const sl of t.slots) {
          const g = sl.obj.geometry;
          const attr = g.attributes.color;
          if (!attr) continue;
          g.deleteAttribute("color");
          restore.push(() => g.setAttribute("color", attr));
        }
      }
      restore.push(() => tex.dispose());
    }
    return () => { for (const f of restore.reverse()) { try { f(); } catch { /* keep going */ } } };
  }

  // ---------------------------------------------------------------- what the panel shows
  info() {
    return {
      version: this.version,
      targets: this.targets.map((t) => ({
        id: t.id, name: t.name, w: t.w, h: t.h, scale: t.scale, live: t.live, sharedUV: t.sharedUV, made: !!t.made,
        strokes: t.strokes.length, unreplayable: t.unreplayable, layers: [...t.rts.keys()],
      })),
      layers: this.layers.map((l) => ({ ...l })),
      active: this.activeLayer,
      undo: this.history.depth, redo: this.history.redoDepth, undoLabel: this.history.topLabel, redoLabel: this.history.redoLabel,
      cloneSource: !!this.cloneSource,
      orphans: this.orphans.length,
      note: this.note,
    };
  }

  /** The painted image of a target as a data URL, for a thumbnail or a proof. */
  snapshotURL(id = "", max = 256): string {
    const t = this.targets.find((x) => x.id === id) || this.targets.find((x) => x.live && x.composite);
    if (!t) return "";
    const px = new Uint8Array(t.w * t.h * 4);
    this.gl(() => { if (t.dirty) this.composite(t); this.r.readRenderTargetPixels(t.composite, 0, 0, t.w, t.h, px); });
    const full = document.createElement("canvas");
    full.width = t.w; full.height = t.h;
    const fctx = full.getContext("2d")!;
    const img = fctx.createImageData(t.w, t.h);
    img.data.set(px);
    fctx.putImageData(img, 0, 0);
    const s = Math.min(1, max / Math.max(t.w, t.h));
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(t.w * s)); c.height = Math.max(1, Math.round(t.h * s));
    c.getContext("2d")!.drawImage(full, 0, 0, c.width, c.height);
    return c.toDataURL("image/png");
  }

  /** Read one texel of the composite (straight sRGB bytes), for a check. */
  texel(id: string, x: number, y: number): number[] {
    const t = this.targets.find((q) => q.id === id) || this.targets.find((q) => q.live && q.composite);
    if (!t) return [];
    const px = new Uint8Array(4);
    this.gl(() => { if (t.dirty) this.composite(t); this.r.readRenderTargetPixels(t.composite, clamp(x | 0, 0, t.w - 1), clamp(y | 0, 0, t.h - 1), 1, 1, px); });
    return [...px];
  }

  /** How many composite texels differ from the base alone — the size of the paint, as a number. */
  paintedTexels(id = ""): number {
    const t = this.targets.find((q) => q.id === id) || this.targets.find((q) => q.live && q.composite);
    if (!t) return 0;
    let n = 0;
    this.gl(() => {
      if (t.dirty) this.composite(t);
      const a = new Uint8Array(t.w * t.h * 4), b = new Uint8Array(t.w * t.h * 4);
      this.r.readRenderTargetPixels(t.composite, 0, 0, t.w, t.h, a);
      const init = this.mats.init;
      init.uniforms.uBase.value = t.base || null;
      init.uniforms.uHasBase.value = t.base ? 1 : 0;
      init.uniforms.uToGamma.value = t.srgb && !t.baseGamma ? 1 : 0;
      this.quad(init, t.accA);
      this.r.readRenderTargetPixels(t.accA, 0, 0, t.w, t.h, b);
      for (let i = 0; i < a.length; i += 4) {
        if (Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]) > 6) n++;
      }
    });
    return n;
  }

  dispose() {
    this.detach();
    this.history.clear();
    for (const t of this.targets) {
      for (const rt of t.rts.values()) rt.dispose();
      for (const k of ["stroke", "accA", "accB", "composite", "display", "mask", "spare", "backup", "snapshot", "madeRT"] as const) {
        try { (t as any)[k]?.dispose(); } catch { /* gone */ }
      }
    }
    this.targets = [];
    this.depthRT?.dispose();
    for (const tex of this.tips.values()) tex.dispose();
    for (const m of this.rasterMats.values()) m.dispose();
    for (const m of Object.values(this.mats)) m?.dispose?.();
    this.quadMesh.geometry.dispose();
  }
}

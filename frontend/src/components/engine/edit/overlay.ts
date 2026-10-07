// What the editor draws on top of the asset: the floor, the axes, the selection, the bones, the
// normals, and the shading passes that answer a question a lit render cannot.
//
// These are Blender's overlays, and they are here for the same reason Blender has them: a lit
// render is a picture of the surface, and most of what goes wrong in a procedural asset is not on
// the surface. Inverted winding, a part that never got welded, a limb pointing backwards — none
// of that shows in a beauty shot and all of it shows in one of these.
//
// Nothing imports three. The module arrives at runtime from the project that owns the asset, so
// every entry point takes `T` and everything engine-shaped is `any` on purpose.

export type V3 = [number, number, number];

/** Blender's own palette, so the two look like relatives rather than strangers. */
export const THEME = {
  axisX: 0xf1424f,
  axisY: 0x8bd44a,
  axisZ: 0x3b7fe0,
  grid: 0x6b7482,
  select: 0xed9e5c,
  active: 0xffd08a,
  bone: 0x9aa4b2,
  boneSel: 0x4fd0e0,
  boneActive: 0xffffff,
  normal: 0x2f6fe0,
  faceFront: 0x2f6fe0,
  faceBack: 0xd8383f,
};

const AXIS_COLORS = [THEME.axisX, THEME.axisY, THEME.axisZ];
export const axisColor = (i: number) => AXIS_COLORS[i] ?? 0xffffff;

// ------------------------------------------------------------------ the floor
//
// An adaptive infinite grid rather than a fixed GridHelper. A fixed grid is either a postage
// stamp when you zoom out or a solid grey sheet when you zoom in, and a procedural asset gets
// looked at across four orders of magnitude in the same session — a whole creature, then one
// claw. The subdivision follows the camera the way Blender's does, so there are always about
// ten to a hundred cells across the view whatever the scale.

const GRID_VS = `
varying vec3 vWorld;
void main() {
  vec4 w = modelMatrix * vec4(position, 1.0);
  vWorld = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}
`;

const GRID_FS = `
precision highp float;
varying vec3 vWorld;
uniform vec3 uCam;
uniform float uFine;
uniform float uCoarse;
uniform float uFineAlpha;
uniform float uFade;
uniform float uOpacity;
uniform vec3 uColor;
uniform vec3 uXColor;
uniform vec3 uZColor;
uniform float uAxes;

// Coverage of the nearest grid line, in pixels: the derivative is what keeps a line one pixel
// wide at any zoom instead of turning into moire when the cells fall below a pixel.
float lineAt(vec2 p, float scale) {
  vec2 c = p / scale;
  vec2 d = fwidth(c);
  vec2 g = abs(fract(c - 0.5) - 0.5) / max(d, vec2(1e-8));
  return 1.0 - min(min(g.x, g.y), 1.0);
}

void main() {
  vec2 p = vec2(vWorld.x, vWorld.z);
  float dist = length(vWorld - uCam);
  float fade = 1.0 - smoothstep(uFade * 0.3, uFade, dist);
  if (fade <= 0.002) discard;

  float a = max(lineAt(p, uCoarse), lineAt(p, uFine) * 0.45 * uFineAlpha);
  vec3 col = uColor;

  if (uAxes > 0.5) {
    vec2 d = fwidth(p);
    // The line through x = 0 runs along Z and is blue; the line through z = 0 runs along X and
    // is red. Getting these the wrong way round is the classic way to mislabel a whole viewport.
    float onZ = 1.0 - min(abs(p.x) / max(d.x, 1e-8), 1.0);
    float onX = 1.0 - min(abs(p.y) / max(d.y, 1e-8), 1.0);
    if (onZ > 0.0) { col = mix(col, uZColor, onZ); a = max(a, onZ); }
    if (onX > 0.0) { col = mix(col, uXColor, onX); a = max(a, onX); }
  }

  float alpha = a * uOpacity * fade;
  if (alpha <= 0.002) discard;
  gl_FragColor = vec4(col, alpha);
}
`;

export interface Grid {
  mesh: any;
  /** Cell size in world units, for the readout that says what one square means. */
  cell: number;
  update(camPos: any, dist: number): void;
  setAxes(on: boolean): void;
  dispose(): void;
}

export function makeGrid(T: any): Grid {
  const uniforms: any = {
    uCam: { value: new T.Vector3() },
    uFine: { value: 0.1 },
    uCoarse: { value: 1 },
    uFineAlpha: { value: 1 },
    uFade: { value: 100 },
    // A grid line is one pixel wide on a dark backdrop. At 0.55 it was drawn correctly and could
    // not be seen, which comes to the same thing as not drawing it.
    uOpacity: { value: 0.9 },
    uColor: { value: new T.Color(THEME.grid) },
    uXColor: { value: new T.Color(THEME.axisX) },
    uZColor: { value: new T.Color(THEME.axisZ) },
    uAxes: { value: 1 },
  };
  const mat = new T.ShaderMaterial({
    uniforms, vertexShader: GRID_VS, fragmentShader: GRID_FS,
    transparent: true, depthWrite: false, side: T.DoubleSide,
    toneMapped: false,
  });
  try { (mat as any).extensions = { derivatives: true }; } catch { /* always on since r152 */ }
  const mesh = new T.Mesh(new T.PlaneGeometry(1, 1), mat);
  mesh.rotation.x = -Math.PI / 2;
  mesh.frustumCulled = false;
  mesh.renderOrder = -1;
  mesh.name = "__grid";
  const grid: Grid = {
    mesh,
    cell: 1,
    update(camPos: any, dist: number) {
      const d = Math.max(1e-4, dist);
      const log = Math.log10(d);
      const decade = Math.pow(10, Math.floor(log));
      uniforms.uCoarse.value = decade;
      uniforms.uFine.value = decade / 10;
      // Through the decade the fine lines fade out, so a zoom never pops from one grid to another.
      uniforms.uFineAlpha.value = 1 - (log - Math.floor(log));
      uniforms.uFade.value = d * 14;
      uniforms.uCam.value.copy(camPos);
      mesh.position.set(camPos.x, 0, camPos.z);
      mesh.scale.setScalar(Math.max(1, d * 60));
      grid.cell = decade;
    },
    setAxes(on: boolean) { uniforms.uAxes.value = on ? 1 : 0; },
    dispose() { try { mesh.geometry.dispose(); mat.dispose(); } catch { /* gone */ } },
  };
  return grid;
}

// ------------------------------------------------------------ shading passes
//
// Each of these replaces every material in the scene for one render. That is the point: colour
// and lighting are exactly what hide a shape, so the passes that judge a shape take them away.

/** Blender's face-orientation overlay: blue where you are looking at the front of a face, red
 *  where you are looking at its back. Inverted winding is invisible in every other pass. */
export function facesMaterial(T: any): any {
  const front = new T.Color(THEME.faceFront);
  const back = new T.Color(THEME.faceBack);
  return new T.ShaderMaterial({
    uniforms: { uFront: { value: front }, uBack: { value: back } },
    vertexShader: `
      varying vec3 vN;
      void main() {
        vN = normalize(normalMatrix * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uFront;
      uniform vec3 uBack;
      varying vec3 vN;
      void main() {
        vec3 base = gl_FrontFacing ? uFront : uBack;
        // A flat fill reads as a silhouette, so a little normal shading keeps the form legible
        // while the colour still answers the only question this pass is asked.
        float l = 0.55 + 0.45 * abs(normalize(vN).z);
        gl_FragColor = vec4(base * l, 1.0);
      }
    `,
    side: T.DoubleSide,
    toneMapped: false,
  });
}

/** Surface direction as colour. Two parts that look joined but face different ways show as two
 *  colours, which is how a seam that will crack under a light is found before it is lit. */
export function normalsMaterial(T: any): any {
  return new T.ShaderMaterial({
    vertexShader: `
      varying vec3 vN;
      void main() {
        vN = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 vN;
      void main() { gl_FragColor = vec4(normalize(vN) * 0.5 + 0.5, 1.0); }
    `,
    side: T.DoubleSide,
    toneMapped: false,
  });
}

/** A flat fill: the silhouette, which is the single most honest read on whether a thing is
 *  recognisable. If it is not readable in black, no amount of material work will save it. */
export function silhouetteMaterial(T: any, color = 0x0b0d11): any {
  return new T.MeshBasicMaterial({ color, side: T.DoubleSide, toneMapped: false });
}

/**
 * The Solid-view stand-in for one real material: its base colour and its colour map, and nothing
 * else.
 *
 * Metalness, emissive, every other map and any custom shader are dropped on purpose. That is what
 * makes Solid a working view rather than a second Render, and it is what Blender's own
 * "Solid + Colour: Material" does.
 *
 * Why it exists at all: without it Solid paints the whole subject one grey, and for these assets
 * the colour IS the design. A chest built from thirteen materials — near-black core, indigo
 * planks, warm gold, mint gems — opened as a single lump of clay, and there was no way to tell
 * from the viewport that any of the colour work had happened.
 */
export function solidColorMaterial(
  T: any, src: any, kind: "mesh" | "line" | "points" = "mesh", flat = false,
): any {
  const color = src && src.color ? src.color.clone() : new T.Color(0xb9c3d0);
  const map = (src && src.map) || null;
  const side = src && src.side !== undefined ? src.side : T.FrontSide;
  let m: any;
  if (kind === "line") m = new T.LineBasicMaterial({ color, toneMapped: false });
  else if (kind === "points") m = new T.PointsMaterial({ color, size: src?.size ?? 4, sizeAttenuation: src?.sizeAttenuation ?? false, toneMapped: false });
  else if (flat) m = new T.MeshBasicMaterial({ color, map, side, toneMapped: false });
  else m = new T.MeshStandardMaterial({ color, map, side, roughness: 0.62, metalness: 0.02 });
  // A part the code made see-through stays see-through. A glass dome drawn opaque hides the very
  // thing it was put there to show, and the user would read that as a modelling fault.
  if (src && src.transparent) {
    m.transparent = true;
    m.opacity = src.opacity;
    m.depthWrite = src.depthWrite;
  }
  if (src && src.vertexColors) m.vertexColors = true;
  if (src && src.alphaTest) m.alphaTest = src.alphaTest;
  return m;
}

export function wireMaterial(T: any, color = 0x8fa0b8, opacity = 0.35): any {
  return new T.MeshBasicMaterial({
    color, wireframe: true, transparent: opacity < 1, opacity, depthTest: true, toneMapped: false,
  });
}

/** A studio sphere baked into a texture and sampled by the view normal. Colour and lighting stop
 *  competing with shape, which is why sculptors work in matcap and why form reads best here. */
export function matcapTexture(T: any): any {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  g.fillStyle = "#0d0f14";
  g.fillRect(0, 0, 256, 256);
  const grad = g.createRadialGradient(96, 78, 8, 128, 128, 168);
  grad.addColorStop(0.0, "#ffffff");
  grad.addColorStop(0.18, "#dfe6ef");
  grad.addColorStop(0.45, "#98a6bb");
  grad.addColorStop(0.72, "#4e5a70");
  grad.addColorStop(1.0, "#1b2029");
  g.beginPath();
  g.arc(128, 128, 127, 0, Math.PI * 2);
  g.fillStyle = grad;
  g.fill();
  // A cool rim on the lower right, the way a real matcap carries a bounce light.
  const rim = g.createRadialGradient(176, 190, 4, 168, 182, 96);
  rim.addColorStop(0, "rgba(150,190,255,0.55)");
  rim.addColorStop(1, "rgba(150,190,255,0)");
  g.globalCompositeOperation = "lighter";
  g.beginPath();
  g.arc(128, 128, 127, 0, Math.PI * 2);
  g.fillStyle = rim;
  g.fill();
  const tex = new T.CanvasTexture(c);
  if (T.SRGBColorSpace && "colorSpace" in tex) tex.colorSpace = T.SRGBColorSpace;
  return tex;
}

export function matcapMaterial(T: any): any {
  const matcap = matcapTexture(T);
  if (T.MeshMatcapMaterial) return new T.MeshMatcapMaterial({ matcap, flatShading: false });
  // An engine old enough to lack the material still gets the look.
  return new T.ShaderMaterial({
    uniforms: { uMap: { value: matcap } },
    vertexShader: `
      varying vec3 vN;
      void main() {
        vN = normalize(normalMatrix * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform sampler2D uMap;
      varying vec3 vN;
      void main() { gl_FragColor = texture2D(uMap, normalize(vN).xy * 0.5 + 0.5); }
    `,
    toneMapped: false,
  });
}

// ------------------------------------------------------------ selection outline
//
// The hull is pushed out along the vertex normal rather than scaled. Scaling looks right only
// when the origin of the object happens to be its centre, and for a procedural part it almost
// never is: a scaled outline slides off a limb whose origin sits at the hip.

export function outlineMaterial(T: any, color = THEME.select): any {
  const m = new T.ShaderMaterial({
    uniforms: { uColor: { value: new T.Color(color) }, uThick: { value: 0.02 } },
    vertexShader: `
      uniform float uThick;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vec3 n = normalize(normalMatrix * normal);
        // Thickness in view space, scaled by depth, so the outline is the same number of pixels
        // on a claw two centimetres across and on a body four metres long.
        mv.xyz += n * uThick * -mv.z;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      uniform vec3 uColor;
      void main() { gl_FragColor = vec4(uColor, 1.0); }
    `,
    side: T.BackSide,
    depthWrite: false,
    toneMapped: false,
  });
  return m;
}

// ------------------------------------------------------------------ normals
/** Face normals as short lines, the way Blender draws them in edit mode. */
export function normalLines(T: any, root: any, length: number, color = THEME.normal): any {
  const pts: number[] = [];
  const a = new T.Vector3(), b = new T.Vector3(), c = new T.Vector3();
  const mid = new T.Vector3(), n = new T.Vector3(), e1 = new T.Vector3(), e2 = new T.Vector3();
  let budget = 24000;
  root.traverse((o: any) => {
    if (!o.isMesh || !o.geometry || budget <= 0) return;
    const g = o.geometry;
    const pos = g.attributes?.position;
    if (!pos) return;
    const idx = g.index;
    const count = idx ? idx.count : pos.count;
    // A dense mesh gets sampled rather than skipped: a hundred honest arrows say more than
    // nothing at all, and drawing forty thousand would stall the viewport.
    const step = Math.max(3, Math.ceil(count / Math.min(budget, 6000)) * 3);
    for (let i = 0; i + 2 < count; i += step) {
      const i0 = idx ? idx.getX(i) : i, i1 = idx ? idx.getX(i + 1) : i + 1, i2 = idx ? idx.getX(i + 2) : i + 2;
      a.fromBufferAttribute(pos, i0).applyMatrix4(o.matrixWorld);
      b.fromBufferAttribute(pos, i1).applyMatrix4(o.matrixWorld);
      c.fromBufferAttribute(pos, i2).applyMatrix4(o.matrixWorld);
      mid.copy(a).add(b).add(c).multiplyScalar(1 / 3);
      e1.subVectors(b, a); e2.subVectors(c, a);
      n.crossVectors(e1, e2);
      if (n.lengthSq() < 1e-16) continue;
      n.normalize().multiplyScalar(length);
      pts.push(mid.x, mid.y, mid.z, mid.x + n.x, mid.y + n.y, mid.z + n.z);
      budget--;
      if (budget <= 0) break;
    }
  });
  const geo = new T.BufferGeometry();
  geo.setAttribute("position", new T.Float32BufferAttribute(pts, 3));
  const lines = new T.LineSegments(geo, new T.LineBasicMaterial({ color, toneMapped: false }));
  lines.name = "__normals";
  lines.frustumCulled = false;
  return lines;
}

// ------------------------------------------------------------------ bones
//
// Blender's octahedral bone: a point at the head, a square collar a tenth of the way along, and
// a point at the tail. It is the shape it is because it tells you the roll and the direction of
// the bone at a glance, which a cylinder cannot.

export function boneGeometry(T: any, length: number, width = 0): any {
  const L = Math.max(1e-5, length);
  const w = width > 0 ? width : Math.max(L * 0.1, 1e-6);
  const y = L * 0.1;
  const v: number[] = [];
  const head: V3 = [0, 0, 0];
  const tail: V3 = [0, L, 0];
  const collar: V3[] = [[w, y, 0], [0, y, w], [-w, y, 0], [0, y, -w]];
  const tri = (p: V3, q: V3, r: V3) => { v.push(...p, ...q, ...r); };
  for (let i = 0; i < 4; i++) {
    const c0 = collar[i], c1 = collar[(i + 1) % 4];
    tri(head, c1, c0);
    tri(c0, c1, tail);
  }
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(v, 3));
  g.computeVertexNormals();
  return g;
}

/** Orient a bone mesh so its local +Y runs from head to tail. */
export function aimBone(T: any, obj: any, head: V3, tail: V3) {
  const h = new T.Vector3(head[0], head[1], head[2]);
  const t = new T.Vector3(tail[0], tail[1], tail[2]);
  const dir = t.clone().sub(h);
  const len = dir.length() || 1e-5;
  obj.position.copy(h);
  obj.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), dir.normalize());
  obj.userData.boneLength = len;
}

// ------------------------------------------------------------------ helpers
// ------------------------------------------------------------------ extras
//
// Blender's "extras": the icons that stand in for a thing with no surface — a light, a camera.
// Without them a scene's lights are invisible and unselectable, which makes them uneditable.
// A sprite with size attenuation off keeps the same pixel size at any distance, like the gizmo.

export type ExtraKind = "point" | "spot" | "directional" | "hemisphere" | "ambient" | "rect" | "camera" | "light";

export function extraKind(o: any): ExtraKind | "" {
  if (o?.isCamera) return "camera";
  if (!o?.isLight) return "";
  if (o.isPointLight) return "point";
  if (o.isSpotLight) return "spot";
  if (o.isDirectionalLight) return "directional";
  if (o.isHemisphereLight) return "hemisphere";
  if (o.isAmbientLight) return "ambient";
  if (o.isRectAreaLight) return "rect";
  return "light";
}

const iconCache = new Map<string, any>();

/** A crisp 64px glyph for an extra, drawn once per kind and colour. */
export function extraIcon(T: any, kind: ExtraKind, color: string): any {
  const k = kind + "|" + color;
  const hit = iconCache.get(k);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  g.clearRect(0, 0, 64, 64);
  g.lineWidth = 3.2;
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineCap = "round";
  const ring = (r: number) => { g.beginPath(); g.arc(32, 32, r, 0, Math.PI * 2); g.stroke(); };
  const dot = (r: number) => { g.beginPath(); g.arc(32, 32, r, 0, Math.PI * 2); g.fill(); };
  const rays = (n: number, r0: number, r1: number) => {
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      g.beginPath(); g.moveTo(32 + Math.cos(a) * r0, 32 + Math.sin(a) * r0); g.lineTo(32 + Math.cos(a) * r1, 32 + Math.sin(a) * r1); g.stroke();
    }
  };
  switch (kind) {
    case "point": ring(13); dot(4); rays(8, 18, 24); break;
    case "spot": ring(9); dot(3); g.beginPath(); g.moveTo(32, 32); g.lineTo(14, 58); g.moveTo(32, 32); g.lineTo(50, 58); g.stroke();
      g.beginPath(); g.ellipse(32, 56, 18, 5, 0, 0, Math.PI * 2); g.stroke(); break;
    case "directional": ring(10); rays(12, 16, 26); break;
    case "hemisphere": g.beginPath(); g.arc(32, 34, 16, Math.PI, 0); g.closePath(); g.stroke(); g.beginPath(); g.moveTo(12, 40); g.lineTo(52, 40); g.stroke(); break;
    case "ambient": ring(14); g.globalAlpha = 0.35; dot(11); g.globalAlpha = 1; break;
    case "rect": g.strokeRect(14, 20, 36, 24); g.beginPath(); g.moveTo(14, 20); g.lineTo(50, 44); g.moveTo(50, 20); g.lineTo(14, 44); g.stroke(); break;
    case "camera": g.beginPath(); g.roundRect?.(10, 22, 30, 22, 3); if (!g.roundRect) g.rect(10, 22, 30, 22); g.stroke();
      g.beginPath(); g.moveTo(40, 30); g.lineTo(56, 22); g.lineTo(56, 44); g.lineTo(40, 36); g.closePath(); g.stroke(); break;
    default: ring(12); dot(3);
  }
  const tex = new T.CanvasTexture(c);
  if (T.SRGBColorSpace) tex.colorSpace = T.SRGBColorSpace;
  tex.needsUpdate = true;
  iconCache.set(k, tex);
  return tex;
}

/** The wire outline of a camera in its own space, looking down -Z: a pyramid to the frame at
 *  distance `d`, and Blender's triangle on the top edge so which way is up is never in doubt. */
export function cameraOutline(T: any, cam: any, d: number, color: number): any {
  const pts: number[] = [];
  let hw: number, hh: number;
  if (cam.isOrthographicCamera) {
    hw = (cam.right - cam.left) / 2 / (cam.zoom || 1);
    hh = (cam.top - cam.bottom) / 2 / (cam.zoom || 1);
  } else {
    hh = d * Math.tan(((cam.fov || 50) * Math.PI) / 360) / (cam.zoom || 1);
    hw = hh * (cam.aspect || 1);
  }
  const c = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]];
  const z = -d;
  for (let i = 0; i < 4; i++) {
    const a = c[i], b = c[(i + 1) % 4];
    pts.push(a[0], a[1], z, b[0], b[1], z);                          // the frame
    if (cam.isOrthographicCamera) pts.push(a[0], a[1], 0, a[0], a[1], z);   // a box: parallel edges
    else pts.push(0, 0, 0, a[0], a[1], z);                             // a pyramid to the eye
  }
  // The up triangle.
  pts.push(-hw * 0.5, hh * 1.1, z, hw * 0.5, hh * 1.1, z, hw * 0.5, hh * 1.1, z, 0, hh * 1.7, z, 0, hh * 1.7, z, -hw * 0.5, hh * 1.1, z);
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(pts, 3));
  const m = new T.LineBasicMaterial({ color, transparent: true, opacity: 0.85, depthTest: true });
  const l = new T.LineSegments(g, m);
  l.frustumCulled = false;
  return l;
}

/** The direction of a spot or sun, in the light's own space: a line down -Z, and for a spot the
 *  cone it lights, to the distance `d`. */
export function lightOutline(T: any, light: any, d: number, color: number): any {
  const pts: number[] = [0, 0, 0, 0, 0, -d];
  if (light.isSpotLight) {
    const r = d * Math.tan(light.angle || 0.5);
    const n = 24;
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2;
      pts.push(Math.cos(a0) * r, Math.sin(a0) * r, -d, Math.cos(a1) * r, Math.sin(a1) * r, -d);
      if (i % 6 === 0) pts.push(0, 0, 0, Math.cos(a0) * r, Math.sin(a0) * r, -d);
    }
  }
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(pts, 3));
  const m = new T.LineBasicMaterial({ color, transparent: true, opacity: 0.7 });
  const l = new T.LineSegments(g, m);
  l.frustumCulled = false;
  return l;
}

export function disposeTree(o: any) {
  if (!o) return;
  const kill = (n: any) => {
    try { n.geometry?.dispose?.(); } catch { /* already gone */ }
    const ms = Array.isArray(n.material) ? n.material : n.material ? [n.material] : [];
    for (const m of ms) {
      try {
        for (const k of Object.keys(m)) { const v = (m as any)[k]; if (v && v.isTexture) v.dispose?.(); }
        m.dispose?.();
      } catch { /* already gone */ }
    }
  };
  if (o.traverse) o.traverse(kill); else kill(o);
}

/** Remove a child and free it, without caring whether it was ever added. */
export function drop(parent: any, child: any) {
  if (!child) return;
  try { parent.remove(child); } catch { /* not ours */ }
  disposeTree(child);
}

// The transform gizmo: drag a handle, or press G / R / S the way Blender does.
//
// Written here rather than imported. Three's TransformControls lives in examples/, which is
// version-locked to the core beside it, and the engine in this window comes from whichever
// project owns the asset — pairing them would mean an editor that works on one project and
// silently fails on the next. It is also the reason this file, like the rest of the editor,
// takes `T` as an argument and never imports anything.
//
// The maths is all closest-point-on-a-line and ray-plane, in world space, against the pointer
// ray. Screen-space dragging feels right until the camera is near the axis you are dragging, at
// which point it inverts; the ray version never does.

export type GizmoMode = "move" | "rotate" | "scale";
export type Axis = "" | "x" | "y" | "z" | "xy" | "yz" | "xz" | "view" | "all";
export type V3 = [number, number, number];

export interface DragDelta {
  mode: GizmoMode;
  axis: Axis;
  /** World translation to add to the pivot. */
  move?: V3;
  /** World axis and the angle about it, in radians. */
  rotate?: { axis: V3; angle: number };
  /** Per-axis multiplier, 1 where untouched. */
  scale?: V3;
  /** What the header shows while the drag is live: Blender's own readout. */
  text: string;
  /** True while a typed number is driving the value instead of the pointer. */
  typed: boolean;
}

export interface GizmoOpts {
  onChange(d: DragDelta): void;
  onCommit(d: DragDelta): void;
  onCancel(): void;
  /** Grid cell, so Ctrl snaps to something meaningful at the current zoom. */
  snapStep(): number;
}

const AXIS_VEC: Record<string, V3> = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const COL = { x: 0xf1424f, y: 0x8bd44a, z: 0x3b7fe0, view: 0xc8ccd4, all: 0xe8ecf2, hot: 0xffe14d };
const SNAP_ROT = Math.PI / 36;   // five degrees, Blender's own rotation increment
const DEG = 180 / Math.PI;

const fmt = (n: number, dp = 4) => {
  const v = Math.abs(n) < 1e-9 ? 0 : n;
  return v.toFixed(dp).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
};

export class Gizmo {
  readonly root: any;
  mode: GizmoMode = "move";
  space: "global" | "local" = "global";
  enabled = true;
  hovered: Axis = "";
  dragging = false;

  private T: any;
  private opts: GizmoOpts;
  private groups: Record<GizmoMode, any> = {} as any;
  private pickers: Record<GizmoMode, any> = {} as any;
  private ray: any;
  private pivot: any;
  private quat: any;
  private mats: any[] = [];

  // Live drag state.
  private axis: Axis = "";
  private startRay: any = null;
  private startScalar = 0;
  private startPoint: any = null;
  private startAngle = 0;
  private accAngle = 0;
  private startScreenDist = 1;
  private refine: { at: number; raw: number } | null = null;
  private typed = "";
  private last: DragDelta | null = null;
  private modal = false;

  constructor(T: any, opts: GizmoOpts) {
    this.T = T;
    this.opts = opts;
    this.ray = new T.Raycaster();
    this.pivot = new T.Vector3();
    this.quat = new T.Quaternion();
    this.root = new T.Group();
    this.root.name = "__gizmo";
    this.root.renderOrder = 999;
    this.root.visible = false;
    for (const m of ["move", "rotate", "scale"] as GizmoMode[]) {
      const g = new T.Group();
      const p = new T.Group();
      p.visible = false;                       // proxies are for the raycaster, never for the eye
      this.build(m, g, p);
      g.visible = m === "move";
      p.visible = false;
      this.groups[m] = g;
      this.pickers[m] = p;
      this.root.add(g);
      this.root.add(p);
    }
  }

  // ------------------------------------------------------------------ building
  private mat(color: number, opacity = 1): any {
    const m = new this.T.MeshBasicMaterial({
      color, transparent: true, opacity, depthTest: false, depthWrite: false, toneMapped: false,
      side: this.T.DoubleSide,
    });
    m.userData.base = color;
    this.mats.push(m);
    return m;
  }

  private lineMat(color: number, opacity = 1): any {
    const m = new this.T.LineBasicMaterial({
      color, transparent: true, opacity, depthTest: false, depthWrite: false, toneMapped: false,
    });
    m.userData.base = color;
    this.mats.push(m);
    return m;
  }

  private tag(o: any, axis: Axis, kind: GizmoMode) {
    o.userData.axis = axis;
    o.userData.kind = kind;
    o.renderOrder = 999;
    o.frustumCulled = false;
    return o;
  }

  private build(mode: GizmoMode, g: any, p: any) {
    const T = this.T;
    const axes: Array<"x" | "y" | "z"> = ["x", "y", "z"];
    const dirOf = (a: string) => new T.Vector3(...AXIS_VEC[a]);

    if (mode === "rotate") {
      for (const a of axes) {
        const ring = new T.Mesh(new T.TorusGeometry(1, 0.006, 4, 96), this.mat(COL[a], 0.9));
        orientRing(T, ring, dirOf(a));
        g.add(this.tag(ring, a, mode));
        const hit = new T.Mesh(new T.TorusGeometry(1, 0.055, 4, 48), this.mat(0xffffff, 0));
        orientRing(T, hit, dirOf(a));
        p.add(this.tag(hit, a, mode));
      }
      // The view-aligned ring: Blender's outer circle, which rotates about whatever you are
      // looking down. It is the one people reach for most and the easiest to leave out.
      const view = new T.Mesh(new T.TorusGeometry(1.22, 0.006, 4, 96), this.mat(COL.view, 0.55));
      view.userData.billboard = true;
      g.add(this.tag(view, "view", mode));
      const vhit = new T.Mesh(new T.TorusGeometry(1.22, 0.05, 4, 48), this.mat(0xffffff, 0));
      vhit.userData.billboard = true;
      p.add(this.tag(vhit, "view", mode));
      return;
    }

    for (const a of axes) {
      const d = dirOf(a);
      const shaft = new T.Mesh(new T.CylinderGeometry(0.012, 0.012, 0.78, 8), this.mat(COL[a]));
      aimY(T, shaft, d);
      shaft.position.copy(d.clone().multiplyScalar(0.5));
      g.add(this.tag(shaft, a, mode));

      const cap = mode === "move"
        ? new T.Mesh(new T.ConeGeometry(0.055, 0.19, 14), this.mat(COL[a]))
        : new T.Mesh(new T.BoxGeometry(0.1, 0.1, 0.1), this.mat(COL[a]));
      aimY(T, cap, d);
      cap.position.copy(d.clone().multiplyScalar(mode === "move" ? 0.95 : 0.9));
      g.add(this.tag(cap, a, mode));

      const hit = new T.Mesh(new T.CylinderGeometry(0.075, 0.075, 1.05, 6), this.mat(0xffffff, 0));
      aimY(T, hit, d);
      hit.position.copy(d.clone().multiplyScalar(0.52));
      p.add(this.tag(hit, a, mode));
    }

    // Plane handles: the small squares that move or scale in two axes at once.
    const planes: Array<[Axis, V3, number]> = [
      ["xy", [0, 0, 1], COL.z], ["yz", [1, 0, 0], COL.x], ["xz", [0, 1, 0], COL.y],
    ];
    for (const [name, n, col] of planes) {
      const quad = new T.Mesh(new T.PlaneGeometry(0.19, 0.19), this.mat(col, 0.45));
      const nv = new T.Vector3(...n);
      quad.quaternion.setFromUnitVectors(new T.Vector3(0, 0, 1), nv);
      quad.position.copy(offsetFor(T, name).multiplyScalar(0.36));
      g.add(this.tag(quad, name, mode));
      const edge = new T.LineSegments(squareEdges(T, 0.19), this.lineMat(col, 0.9));
      edge.quaternion.copy(quad.quaternion);
      edge.position.copy(quad.position);
      g.add(this.tag(edge, name, mode));
      const hit = quad.clone();
      hit.material = this.mat(0xffffff, 0);
      hit.scale.setScalar(1.25);
      p.add(this.tag(hit, name, mode));
    }

    // The centre: screen-space move, or uniform scale.
    const centre = mode === "move"
      ? new T.Mesh(new T.SphereGeometry(0.07, 16, 12), this.mat(COL.view, 0.4))
      : new T.Mesh(new T.BoxGeometry(0.12, 0.12, 0.12), this.mat(COL.all, 0.75));
    g.add(this.tag(centre, mode === "move" ? "view" : "all", mode));
    const chit = new T.Mesh(new T.SphereGeometry(0.11, 12, 10), this.mat(0xffffff, 0));
    p.add(this.tag(chit, mode === "move" ? "view" : "all", mode));
  }

  // ------------------------------------------------------------------ placing
  setMode(m: GizmoMode) {
    this.mode = m;
    for (const k of ["move", "rotate", "scale"] as GizmoMode[]) this.groups[k].visible = k === m;
  }

  place(pos: V3, quat?: any) {
    this.pivot.set(pos[0], pos[1], pos[2]);
    if (quat) this.quat.copy(quat); else this.quat.identity();
    this.root.position.copy(this.pivot);
    this.root.quaternion.copy(this.space === "local" ? this.quat : new this.T.Quaternion());
  }

  show(on: boolean) { this.root.visible = on && this.enabled; }

  /** Constant size on screen, and an axis pointing at the camera fades rather than lying. */
  update(camera: any, height: number) {
    if (!this.root.visible) return;
    const T = this.T;
    const camPos = camera.getWorldPosition(new T.Vector3());
    const dist = camPos.distanceTo(this.pivot);
    const fov = (camera.fov || 45) * Math.PI / 180;
    const perPixel = (2 * dist * Math.tan(fov / 2)) / Math.max(1, height);
    const s = perPixel * 92;
    this.root.scale.setScalar(s);
    this.root.quaternion.copy(this.space === "local" ? this.quat : new T.Quaternion());

    const view = camPos.clone().sub(this.pivot).normalize();
    const world = new T.Quaternion();
    this.root.getWorldQuaternion(world);
    for (const kind of ["move", "rotate", "scale"] as GizmoMode[]) {
      for (const child of this.groups[kind].children) {
        const axis = child.userData.axis as Axis;
        if (child.userData.billboard) {
          // The view ring must face the camera, in the local frame of a rotated gizmo.
          child.quaternion.copy(world).invert().multiply(camera.quaternion);
        }
        const m = child.material;
        if (!m || m.userData.base === undefined) continue;
        const hot = axis === this.hovered || (this.dragging && axis === this.axis);
        m.color.setHex(hot ? COL.hot : m.userData.base);
        let a = m.userData.baseOpacity ?? (m.userData.baseOpacity = m.opacity);
        if (AXIS_VEC[axis]) {
          const d = new T.Vector3(...AXIS_VEC[axis]).applyQuaternion(world);
          const along = Math.abs(d.dot(view));
          // Nearly end-on, a handle is a dot that drags in an unpredictable direction. Fade it
          // out rather than let it be grabbed, which is what Blender does for the same reason.
          const fade = kind === "rotate" ? 1 - Math.pow(along, 6) : 1 - Math.pow(along, 8);
          a *= Math.max(0.08, fade);
        }
        m.opacity = hot ? Math.max(a, 0.95) : a;
      }
    }
  }

  // ------------------------------------------------------------------ picking
  private pickAt(ndc: { x: number; y: number }, camera: any): Axis {
    if (!this.root.visible) return "";
    this.ray.setFromCamera(ndc as any, camera);
    const hits = this.ray.intersectObjects(this.pickers[this.mode].children, false);
    if (!hits.length) return "";
    // A plane handle sits in front of the shafts it shares a corner with; nearest wins, which is
    // what the depth order already says.
    return (hits[0].object.userData.axis as Axis) || "";
  }

  hover(ndc: { x: number; y: number }, camera: any): Axis {
    if (this.dragging) return this.axis;
    this.hovered = this.pickAt(ndc, camera);
    return this.hovered;
  }

  // ------------------------------------------------------------------ dragging
  begin(ndc: { x: number; y: number }, camera: any, size: { w: number; h: number }, axis?: Axis): boolean {
    const a = axis ?? this.pickAt(ndc, camera);
    if (!a) return false;
    this.axis = a;
    this.dragging = true;
    this.modal = !!axis;
    this.typed = "";
    this.refine = null;
    this.accAngle = 0;
    this.last = null;
    this.ray.setFromCamera(ndc as any, camera);
    this.startRay = this.ray.ray.clone();
    const T = this.T;

    if (this.mode === "rotate") {
      const n = this.axisWorld(a, camera);
      const p = this.hitPlane(this.startRay, this.pivot, n);
      this.startPoint = p || this.pivot.clone();
      this.startAngle = this.angleOn(this.startPoint, n, camera);
      return true;
    }
    if (this.mode === "scale") {
      const s = project(T, this.pivot, camera, size);
      const px = ((ndc.x + 1) / 2) * size.w, py = ((1 - ndc.y) / 2) * size.h;
      this.startScreenDist = Math.max(8, Math.hypot(px - s.x, py - s.y));
      return true;
    }
    if (a === "x" || a === "y" || a === "z") {
      this.startScalar = this.alongAxis(this.startRay, this.axisWorld(a, camera));
      return true;
    }
    const n = this.planeNormal(a, camera);
    this.startPoint = this.hitPlane(this.startRay, this.pivot, n) || this.pivot.clone();
    return true;
  }

  drag(ndc: { x: number; y: number }, camera: any, size: { w: number; h: number },
       mod: { snap: boolean; fine: boolean }): DragDelta | null {
    if (!this.dragging) return null;
    this.ray.setFromCamera(ndc as any, camera);
    const r = this.ray.ray.clone();
    const step = Math.max(1e-6, this.opts.snapStep());
    const d = this.mode === "rotate" ? this.dragRotate(r, camera, mod, step)
      : this.mode === "scale" ? this.dragScale(ndc, camera, size, mod)
        : this.dragMove(r, camera, mod, step);
    if (d) { this.last = d; this.opts.onChange(d); }
    return d;
  }

  private dragMove(r: any, camera: any, mod: { snap: boolean; fine: boolean }, step: number): DragDelta {
    const T = this.T;
    const a = this.axis;
    if (a === "x" || a === "y" || a === "z") {
      const dir = this.axisWorld(a, camera);
      let raw = this.alongAxis(r, dir) - this.startScalar;
      raw = this.applyFine(raw, mod.fine);
      if (this.typed) raw = parseFloat(this.typed) || 0;
      else if (mod.snap) raw = Math.round(raw / step) * step;
      const v = dir.clone().multiplyScalar(raw);
      return { mode: "move", axis: a, move: [v.x, v.y, v.z], typed: !!this.typed,
        text: "D " + a.toUpperCase() + ": " + fmt(raw) };
    }
    const n = this.planeNormal(a, camera);
    const p = this.hitPlane(r, this.pivot, n);
    if (!p) return this.last || { mode: "move", axis: a, move: [0, 0, 0], text: "D: 0", typed: false };
    const v = p.clone().sub(this.startPoint);
    if (mod.fine) { const s = this.applyFineVec(v); v.copy(s); }
    if (mod.snap) { v.x = Math.round(v.x / step) * step; v.y = Math.round(v.y / step) * step; v.z = Math.round(v.z / step) * step; }
    if (a !== "view") {
      // A plane handle must not leak into its normal, whatever the ray intersection says.
      const nv = new T.Vector3(...(a === "xy" ? [0, 0, 1] : a === "yz" ? [1, 0, 0] : [0, 1, 0]));
      nv.applyQuaternion(this.frame());
      v.sub(nv.multiplyScalar(v.dot(nv)));
    }
    return { mode: "move", axis: a, move: [v.x, v.y, v.z], typed: false,
      text: "D: " + fmt(v.length()) + "  (" + fmt(v.x, 2) + ", " + fmt(v.y, 2) + ", " + fmt(v.z, 2) + ")" };
  }

  private dragRotate(r: any, camera: any, mod: { snap: boolean; fine: boolean }, _step: number): DragDelta {
    const n = this.axisWorld(this.axis, camera);
    const p = this.hitPlane(r, this.pivot, n);
    if (!p) return this.last || { mode: "rotate", axis: this.axis, rotate: { axis: [n.x, n.y, n.z], angle: 0 }, text: "R: 0", typed: false };
    const now = this.angleOn(p, n, camera);
    // Unwrap, so dragging past half a turn keeps going instead of flipping sign.
    let d = now - this.startAngle;
    while (d > Math.PI) d -= Math.PI * 2;
    while (d < -Math.PI) d += Math.PI * 2;
    this.accAngle += d;
    this.startAngle = now;
    let ang = this.applyFine(this.accAngle, mod.fine);
    if (this.typed) ang = (parseFloat(this.typed) || 0) / DEG;
    else if (mod.snap) ang = Math.round(ang / SNAP_ROT) * SNAP_ROT;
    return { mode: "rotate", axis: this.axis, rotate: { axis: [n.x, n.y, n.z], angle: ang }, typed: !!this.typed,
      text: "R " + (this.axis === "view" ? "view" : this.axis.toUpperCase()) + ": " + fmt(ang * DEG, 2) + "°" };
  }

  private dragScale(ndc: { x: number; y: number }, camera: any, size: { w: number; h: number },
                    mod: { snap: boolean; fine: boolean }): DragDelta {
    const s = project(this.T, this.pivot, camera, size);
    const px = ((ndc.x + 1) / 2) * size.w, py = ((1 - ndc.y) / 2) * size.h;
    let f = Math.hypot(px - s.x, py - s.y) / this.startScreenDist;
    f = this.applyFine(f - 1, mod.fine) + 1;
    if (this.typed) f = parseFloat(this.typed) || 1;
    else if (mod.snap) f = Math.round(f * 10) / 10;
    f = Math.max(0.001, f);
    const a = this.axis;
    const v: V3 = a === "x" ? [f, 1, 1] : a === "y" ? [1, f, 1] : a === "z" ? [1, 1, f]
      : a === "xy" ? [f, f, 1] : a === "yz" ? [1, f, f] : a === "xz" ? [f, 1, f] : [f, f, f];
    return { mode: "scale", axis: a, scale: v, typed: !!this.typed,
      text: "S" + (a && a !== "all" && a !== "view" ? " " + a.toUpperCase() : "") + ": " + fmt(f, 3) };
  }

  /** Shift refines: everything past the moment it was pressed counts for a tenth. */
  private applyFine(raw: number, on: boolean): number {
    if (!on) { this.refine = null; return raw; }
    if (!this.refine) this.refine = { at: raw, raw };
    return this.refine.at + (raw - this.refine.raw) * 0.1;
  }

  private applyFineVec(v: any): any {
    const len = v.length();
    const scaled = this.applyFine(len, true);
    return len > 1e-9 ? v.clone().multiplyScalar(scaled / len) : v;
  }

  /** A typed number replaces the pointer entirely, exactly as it does in Blender. */
  key(ch: string): boolean {
    if (!this.dragging) return false;
    if (ch === "Backspace") { this.typed = this.typed.slice(0, -1); return true; }
    if (/^[0-9.]$/.test(ch)) { this.typed += ch; return true; }
    if (ch === "-") { this.typed = this.typed.startsWith("-") ? this.typed.slice(1) : "-" + this.typed; return true; }
    return false;
  }

  end(commit: boolean) {
    if (!this.dragging) return;
    this.dragging = false;
    const d = this.last;
    this.axis = "";
    this.last = null;
    this.typed = "";
    this.modal = false;
    if (commit && d) this.opts.onCommit(d);
    else this.opts.onCancel();
  }

  get isModal() { return this.modal; }
  get axisNow(): Axis { return this.axis; }

  /** Blender's modal transform re-aims mid-drag: press X during a move and it becomes an X move
   *  from the original position, not from wherever the pointer happens to be. */
  reaim(axis: Axis, ndc: { x: number; y: number }, camera: any, size: { w: number; h: number }) {
    if (!this.dragging) return;
    this.opts.onCancel();
    const m = this.modal;
    this.dragging = false;
    this.begin(ndc, camera, size, axis);
    this.modal = m;
  }

  dispose() {
    for (const m of this.mats) { try { m.dispose(); } catch { /* gone */ } }
    this.mats = [];
    for (const k of ["move", "rotate", "scale"] as GizmoMode[]) {
      for (const g of [this.groups[k], this.pickers[k]]) {
        for (const c of g.children) { try { c.geometry?.dispose?.(); } catch { /* gone */ } }
      }
    }
  }

  // ------------------------------------------------------------------ maths
  private frame(): any {
    return this.space === "local" ? this.quat : new this.T.Quaternion();
  }

  private axisWorld(a: Axis, camera: any): any {
    const T = this.T;
    if (a === "view" || a === "all" || !AXIS_VEC[a]) {
      return camera.getWorldDirection(new T.Vector3()).negate();
    }
    return new T.Vector3(...AXIS_VEC[a]).applyQuaternion(this.frame()).normalize();
  }

  private planeNormal(a: Axis, camera: any): any {
    const T = this.T;
    if (a === "xy") return new T.Vector3(0, 0, 1).applyQuaternion(this.frame());
    if (a === "yz") return new T.Vector3(1, 0, 0).applyQuaternion(this.frame());
    if (a === "xz") return new T.Vector3(0, 1, 0).applyQuaternion(this.frame());
    return camera.getWorldDirection(new T.Vector3()).negate();
  }

  /** Closest point on the pointer ray to the axis line, as a distance along that axis. */
  private alongAxis(ray: any, dir: any): number {
    const w0 = this.pivot.clone().sub(ray.origin);
    const b = dir.dot(ray.direction);
    const den = 1 - b * b;
    if (Math.abs(den) < 1e-7) return this.startScalar;   // looking straight down the axis
    const d = dir.dot(w0), e = ray.direction.dot(w0);
    return (b * e - d) / den;
  }

  private hitPlane(ray: any, point: any, normal: any): any | null {
    const denom = normal.dot(ray.direction);
    if (Math.abs(denom) < 1e-7) return null;
    const t = point.clone().sub(ray.origin).dot(normal) / denom;
    if (t < 0) return null;
    return ray.origin.clone().add(ray.direction.clone().multiplyScalar(t));
  }

  private angleOn(p: any, n: any, camera: any): number {
    const T = this.T;
    // A stable pair of in-plane axes: anything not parallel to the normal will do, and the
    // camera up is the choice that keeps the readout matching what the hand is doing.
    let u = new T.Vector3(0, 1, 0);
    if (Math.abs(u.dot(n)) > 0.95) u = new T.Vector3(1, 0, 0);
    const e1 = u.clone().sub(n.clone().multiplyScalar(u.dot(n))).normalize();
    const e2 = n.clone().cross(e1).normalize();
    const v = p.clone().sub(this.pivot);
    const sign = camera ? 1 : 1;
    return Math.atan2(v.dot(e2) * sign, v.dot(e1));
  }
}

// ------------------------------------------------------------------ small builders
function aimY(T: any, o: any, dir: any) {
  o.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), dir.clone().normalize());
}

function orientRing(T: any, o: any, normal: any) {
  o.quaternion.setFromUnitVectors(new T.Vector3(0, 0, 1), normal.clone().normalize());
}

function offsetFor(T: any, plane: Axis): any {
  if (plane === "xy") return new T.Vector3(1, 1, 0).normalize();
  if (plane === "yz") return new T.Vector3(0, 1, 1).normalize();
  return new T.Vector3(1, 0, 1).normalize();
}

function squareEdges(T: any, size: number): any {
  const h = size / 2;
  const p = [-h, -h, 0, h, -h, 0, h, -h, 0, h, h, 0, h, h, 0, -h, h, 0, -h, h, 0, -h, -h, 0];
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(p, 3));
  return g;
}

/** World point to pixels, for the screen-space scale factor. */
export function project(T: any, p: any, camera: any, size: { w: number; h: number }) {
  const v = p.clone().project(camera);
  return { x: ((v.x + 1) / 2) * size.w, y: ((1 - v.y) / 2) * size.h, behind: v.z > 1 };
}

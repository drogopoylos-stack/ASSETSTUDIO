// Orbit, dolly and pan for the viewport, written here rather than imported: neither engine is a
// dependency of this frontend, so neither engine's controls can be either. Spherical coordinates
// round a target with Y up, which both engines share, applied to the camera by whoever owns it.

import type { V3 } from "./studio";

const norm = (v: V3): V3 => { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; };
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

export class Orbit {
  target: V3 = [0, 0, 0];
  /** Azimuth round Y. */
  theta = 0.8;
  /** Polar angle from +Y, kept off the poles so lookAt never degenerates. */
  phi = 1.05;
  dist = 5;
  fov = 38;
  private drag: { mode: "rotate" | "pan"; x: number; y: number } | null = null;
  private off: (() => void)[] = [];

  constructor(private el: HTMLElement, private onChange: () => void, onFrame?: () => void) {
    const on = <K extends keyof HTMLElementEventMap>(type: K, fn: (e: HTMLElementEventMap[K]) => void,
                                                     opts?: AddEventListenerOptions) => {
      el.addEventListener(type, fn as EventListener, opts);
      this.off.push(() => el.removeEventListener(type, fn as EventListener, opts));
    };
    on("pointerdown", (e) => {
      if (e.button > 2) return;
      // Left drag orbits; the middle or right button — or shift, for a trackpad — pans.
      this.drag = { mode: e.button === 0 && !e.shiftKey ? "rotate" : "pan", x: e.clientX, y: e.clientY };
      try { el.setPointerCapture(e.pointerId); } catch { /* capture is a nicety */ }
      e.preventDefault();
    });
    on("pointermove", (e) => {
      if (!this.drag) return;
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      this.drag.x = e.clientX; this.drag.y = e.clientY;
      if (this.drag.mode === "rotate") {
        this.theta -= dx * 0.008;
        this.phi = Math.min(Math.PI - 0.02, Math.max(0.02, this.phi - dy * 0.008));
      } else {
        this.pan(dx, dy);
      }
      this.onChange();
    });
    const up = (e: PointerEvent) => {
      if (!this.drag) return;
      this.drag = null;
      try { el.releasePointerCapture(e.pointerId); } catch { /* never captured */ }
    };
    on("pointerup", up);
    on("pointercancel", up);
    on("wheel", (e) => {
      e.preventDefault();
      // Multiplicative, so a scroll step is the same fraction of the distance at any scale.
      this.dist = Math.max(1e-5, this.dist * Math.exp(e.deltaY * 0.0012));
      this.onChange();
    }, { passive: false });
    on("contextmenu", (e) => e.preventDefault());
    if (onFrame) on("dblclick", () => onFrame());
  }

  /** Adopt a camera someone else placed — the framing pass, or a view preset. */
  setFrom(pos: V3, target: V3) {
    const d: V3 = [pos[0] - target[0], pos[1] - target[1], pos[2] - target[2]];
    this.target = [target[0], target[1], target[2]];
    this.dist = Math.max(1e-6, Math.hypot(d[0], d[1], d[2]));
    this.phi = Math.acos(Math.min(1, Math.max(-1, d[1] / this.dist)));
    this.theta = Math.atan2(d[0], d[2]);
  }

  position(): V3 {
    const s = Math.sin(this.phi);
    return [
      this.target[0] + this.dist * s * Math.sin(this.theta),
      this.target[1] + this.dist * Math.cos(this.phi),
      this.target[2] + this.dist * s * Math.cos(this.theta),
    ];
  }

  // Pan moves the TARGET across the screen plane by exactly the pixels the pointer moved, at the
  // target's depth — so the subject stays under the cursor whatever the zoom.
  private pan(dx: number, dy: number) {
    const h = this.el.clientHeight || 1;
    const per = 2 * this.dist * Math.tan(this.fov * Math.PI / 360) / h;
    const p = this.position();
    const f = norm([this.target[0] - p[0], this.target[1] - p[1], this.target[2] - p[2]]);
    const r = norm(cross(f, [0, 1, 0]));
    const u = cross(r, f);
    for (let i = 0; i < 3; i++) this.target[i] += (-dx * r[i] + dy * u[i]) * per;
  }

  dispose() {
    this.off.forEach((f) => f());
    this.off = [];
    this.drag = null;
  }
}

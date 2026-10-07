// What the editor re-renders, and when.
//
// Orbiting a mirrored game (Dino Smash: 578 objects, 151k triangles) felt laggy, and the frame
// times said why: the camera's change callback was a state bump, so EVERY pointer move of an orbit
// re-rendered the whole editor — 2,900 lines of panels plus a 578-row outliner — and a 125 Hz
// mouse asked for 125 of those a second (worst frame 33 ms). A gizmo drag did the same through its
// readout, a box select through its rectangle, and a 500 ms beat re-rendered everything again for
// a frame-rate number.
//
// Only a few small things on screen follow those changes. Each one now listens to a `Signal` and
// re-renders alone, at most once per animation frame; the editor itself re-renders only when
// something it shows has really changed. Kept free of React and the DOM so it is tested in node.

export interface Signal {
  subscribe(fn: () => void): () => void;
  /** Increases once per delivered change; what useSyncExternalStore compares. */
  version(): number;
  /** Something changed. Delivered on the next frame, once, however often it is called before. */
  emit(): void;
}

export function makeSignal(schedule: (cb: () => void) => unknown = (cb) => requestAnimationFrame(cb)): Signal {
  const subs = new Set<() => void>();
  let v = 0;
  let armed = false;
  const deliver = () => {
    armed = false;
    v++;
    for (const fn of [...subs]) {
      try { fn(); } catch { /* one listener's fault is not everyone's */ }
    }
  };
  return {
    subscribe(fn) { subs.add(fn); return () => { subs.delete(fn); }; },
    version: () => v,
    emit() {
      if (armed) return;
      armed = true;
      schedule(deliver);
    },
  };
}

/** The parts-list fields a row of the outliner or a size label shows. */
export interface PartView {
  key: string; name: string; type: string; depth: number; parent: string;
  meshes: number; triangles: number; visible: boolean;
  px: number; at: { x: number; y: number; behind: boolean };
}

const SMALL = 24;

/**
 * Whether a fresh parts list would draw the same as the one on screen.
 *
 * With the size labels off, a part's pixel size shows only as the outliner's "too small" badge, so
 * a change in size matters only when it crosses that line. With them on, every label moves with
 * the camera, so the size and the position (to the pixel) count too.
 */
export function samePartsView(a: readonly PartView[], b: readonly PartView[], withSizes: boolean): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const p = a[i], q = b[i];
    if (p.key !== q.key || p.name !== q.name || p.visible !== q.visible || p.depth !== q.depth
        || p.parent !== q.parent || p.type !== q.type || p.triangles !== q.triangles || p.meshes !== q.meshes) return false;
    const ps = p.px > 0 && p.px < SMALL, qs = q.px > 0 && q.px < SMALL;
    if (ps !== qs || (ps && p.px !== q.px)) return false;
    if (withSizes) {
      if (p.px !== q.px || p.at.behind !== q.at.behind) return false;
      if (Math.round(p.at.x) !== Math.round(q.at.x) || Math.round(p.at.y) !== Math.round(q.at.y)) return false;
    }
  }
  return true;
}

/** The part of the scene statistics that changes only when the SCENE changes — not the frame rate
 *  or the draw count, which change every frame and are read live by the status bar. */
export function statsShape(s: {
  objects: number; meshes: number; triangles: number; materials: number;
  bbox: number[] | null; error?: string; drawn?: boolean;
} | null): string {
  if (!s) return "";
  return [s.objects, s.meshes, s.triangles, s.materials, (s.bbox || []).join(","), s.error || "", s.drawn ? 1 : 0].join("|");
}

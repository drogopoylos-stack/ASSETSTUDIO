// review/targets.js: what the Studio's visual review can look at on its own, one builder at a time.
//
//   POST /api/review/render {"project": "<this folder>", "mode": "isolate", "target": "crate"}
//
// Each target draws one builder on a plain backdrop, standing on a line (so a builder whose feet are
// not on the bottom edge is visible at once), and leaning slowly from side to side, so the frames
// the review samples over time differ. The review drives the clock: requestAnimationFrame here is
// stepped by the harness, never by real time.
//
// Builders are plain canvases, so this needs no engine and no import map.
//
// Add one target per builder you add to src/assets.js.
import { buildCrate, buildTree } from '../src/assets.js';

const TARGETS = [
  { name: 'crate', note: 'buildCrate(): a 72 px crate', build: () => buildCrate({ size: 72 }) },
  { name: 'tree', note: 'buildTree(): a 170 px tree', build: () => buildTree({ height: 170 }) },
];

function draw(root, ctx, target) {
  const dpr = ctx.pixelRatio || 1;
  const c = document.createElement('canvas');
  c.width = ctx.width * dpr;
  c.height = ctx.height * dpr;
  c.style.cssText = `width:${ctx.width}px;height:${ctx.height}px;display:block`;
  root.appendChild(c);
  const g = c.getContext('2d');
  const art = target.build();
  // Framed: the picture fills most of the height, whatever size the builder made it.
  const k = Math.min(ctx.width / art.width, ctx.height / art.height) * 0.7;
  const baseY = ctx.height * 0.86;
  // The angle is a function of the time since mount, not a sum of frame steps, so the review's
  // samples at 0, 800 and 1600 ms each show a different frame.
  const t0 = typeof ctx.t0 === 'number' ? ctx.t0 : performance.now();
  const tick = (now) => {
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = ctx.background || '#12151c';
    g.fillRect(0, 0, ctx.width, ctx.height);
    g.strokeStyle = '#2a303c';
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(0, baseY);
    g.lineTo(ctx.width, baseY);
    g.stroke();
    g.translate(ctx.width / 2, baseY);
    g.rotate(Math.sin((now - t0) / 1000) * 0.08);
    g.scale(k, k);
    g.drawImage(art, -art.width / 2, -art.height);
    requestAnimationFrame(tick);
  };
  tick(t0);                                 // a frame now, so t = 0 is never blank
  return tick;
}

export const targets = TARGETS.map((t) => ({
  name: t.name,
  note: t.note,
  mount: async (root, ctx) => draw(root, ctx, t),
}));

export default targets;

// Builders: the things this game is made of, written as code.
//
// Each one takes a single options object (every option has a default) and returns a canvas with a
// name and its feet on the bottom edge. That shape is the whole contract: it needs no engine, so the
// game turns it into a texture, the Library lists it, and review/targets.js shows it alone.
//
// To add one: export `buildThing({ ...options } = {})` from this file, give the canvas a name, keep
// its lowest point on the bottom edge, and add a target for it in review/targets.js.

const shade = (hex, f) => {
  const c = (n) => Math.round(((hex >> n) & 255) * f);
  return `rgb(${c(16)},${c(8)},${c(0)})`;
};

function canvasOf(name, width, height) {
  const c = document.createElement('canvas');
  c.width = Math.ceil(width);
  c.height = Math.ceil(height);
  c.dataset.name = name;
  return c;
}

/** A wooden crate with a darker frame. `size` is its width in pixels. */
export function buildCrate({ size = 72, color = 0xb5844c } = {}) {
  const c = canvasOf('crate', size, size);
  const g = c.getContext('2d');
  const frame = Math.max(3, size * 0.11);
  g.fillStyle = shade(color, 1);
  g.fillRect(0, 0, size, size);
  g.strokeStyle = shade(color, 0.6);
  g.lineWidth = frame;
  g.strokeRect(frame / 2, frame / 2, size - frame, size - frame);
  g.lineWidth = frame * 0.7;
  g.beginPath();
  g.moveTo(frame, frame);
  g.lineTo(size - frame, size - frame);
  g.stroke();
  return c;
}

/** A simple tree: a trunk and two triangles. `height` is its height in pixels. */
export function buildTree({ height = 170, leaves = 0x3f8a4a, trunk = 0x6e4a2b } = {}) {
  const width = height * 0.62;
  const c = canvasOf('tree', width, height);
  const g = c.getContext('2d');
  const mid = width / 2;
  g.fillStyle = shade(trunk, 1);
  g.fillRect(mid - height * 0.045, height * 0.72, height * 0.09, height * 0.28);
  const layer = (top, bottom, half, f) => {
    g.fillStyle = shade(leaves, f);
    g.beginPath();
    g.moveTo(mid, top);
    g.lineTo(mid + half, bottom);
    g.lineTo(mid - half, bottom);
    g.closePath();
    g.fill();
  };
  layer(height * 0.18, height * 0.78, width / 2, 0.82);
  layer(0, height * 0.52, width * 0.38, 1);
  return c;
}

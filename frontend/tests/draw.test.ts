// Draw (draw.ts): the 2D sprite tools, in node — pixels in, pixels out.
import {
  DrawHistory, Stroke, addFrame, addLayer, atlasJson, docFromJson, docToJson, drawShape, flatten, floodFill, mergeDown,
  moveLayer, newDoc, pickColour, pixelLine, removeFrame, removeLayer, shiftCel, spriteSheet,
} from "../src/components/engine/edit/draw";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const px = (c: Uint8ClampedArray, w: number, x: number, y: number) => Array.from(c.subarray((y * w + x) * 4, (y * w + x) * 4 + 4));
const count = (c: Uint8ClampedArray) => { let n = 0; for (let i = 3; i < c.length; i += 4) if (c[i] > 0) n++; return n; };

{
  const pts: string[] = [];
  pixelLine(0, 0, 4, 2, (x, y) => pts.push(x + "," + y));
  ok("a pixel line has both ends and no gaps", pts[0] === "0,0" && pts[pts.length - 1] === "4,2" && pts.length === 5, pts);
}
{
  const d = newDoc(16, 16);
  ok("a small canvas is pixel art", d.pixel && d.layers.length === 1 && d.frames[0][0].length === 16 * 16 * 4);
  const s = new Stroke(d.frames[0][0], 16, 16, { tool: "pencil", colour: "#ff0000", size: 1, opacity: 1 });
  s.add([{ x: 2.5, y: 2.5, pressure: 1 }], true);
  s.add([{ x: 9.5, y: 2.5, pressure: 1 }]);
  const c = d.frames[0][0];
  ok("the pencil sets whole pixels, full colour, along the drag", count(c) === 8 && px(c, 16, 5, 2).join() === "255,0,0,255", count(c));
  ok("...and nothing next to them (no smoothing)", px(c, 16, 5, 3)[3] === 0);
  ok("...and says which rectangle changed", JSON.stringify(s.dirty) === "[2,2,9,2]", s.dirty);
}
{
  const d = newDoc(16, 16);
  new Stroke(d.frames[0][0], 16, 16, { tool: "pencil", colour: "#00ff00", size: 1, opacity: 1, mirrorX: true }).add([{ x: 2, y: 5, pressure: 1 }], true);
  ok("mirror X draws the pixel on the other side too", px(d.frames[0][0], 16, 13, 5)[1] === 255);
}
{
  const d = newDoc(64, 64, { pixel: false });
  const s = new Stroke(d.frames[0][0], 64, 64, { tool: "brush", colour: "#0000ff", size: 10, opacity: 0.4, hardness: 1 });
  s.add([{ x: 10, y: 32, pressure: 1 }], true);
  s.add([{ x: 54, y: 32, pressure: 1 }, { x: 10, y: 32, pressure: 1 }, { x: 54, y: 32, pressure: 1 }]);
  const a = px(d.frames[0][0], 64, 32, 32)[3];
  ok("a 40% stroke that crosses itself stays 40%", Math.abs(a - 102) <= 2, a);
  const e = new Stroke(d.frames[0][0], 64, 64, { tool: "eraser", colour: "#000000", size: 12, opacity: 1, hardness: 1 });
  e.add([{ x: 32, y: 32, pressure: 1 }], true);
  ok("the eraser takes it away", px(d.frames[0][0], 64, 32, 32)[3] === 0 && px(d.frames[0][0], 64, 12, 32)[3] > 0);
}
{
  const d = newDoc(10, 10);
  drawShape(d.frames[0][0], 10, 10, "rect", 1, 1, 8, 8, { colour: "#ffffff" });
  const c = d.frames[0][0];
  ok("a rectangle outline is one pixel wide", px(c, 10, 1, 1)[3] === 255 && px(c, 10, 4, 4)[3] === 0 && count(c) === 28, count(c));
  const n = floodFill(c, 10, 10, 4, 4, "#ff0000");
  ok("a fill inside the outline fills the inside only", n === 36 && px(c, 10, 4, 4).join() === "255,0,0,255" && px(c, 10, 0, 0)[3] === 0, n);
  const m = floodFill(c, 10, 10, 0, 0, "#0000ff", { contiguous: false });
  ok("a global fill takes every matching pixel", m === 100 - 28 - 36, m);
  ok("the picker reads the colour", pickColour(c, 10, 4, 4) === "#ff0000" && pickColour(newDoc(2, 2).frames[0][0], 2, 0, 0) === "");
}
{
  const d = newDoc(21, 21);
  drawShape(d.frames[0][0], 21, 21, "ellipse", 0, 0, 20, 20, { colour: "#ffffff", filled: true });
  const c = d.frames[0][0];
  ok("a filled pixel ellipse: the middle in, the corners out, symmetric",
    px(c, 21, 10, 10)[3] === 255 && px(c, 21, 0, 0)[3] === 0 && px(c, 21, 3, 10).join() === px(c, 21, 17, 10).join());
  const s = newDoc(21, 21, { pixel: false });
  drawShape(s.frames[0][0], 21, 21, "line", 2, 2, 18, 18, { colour: "#ffffff", smooth: true, thickness: 2 });
  const edge = Array.from({ length: 21 }, (_, x) => px(s.frames[0][0], 21, x, 10)[3]);
  ok("a smooth line has soft edges", edge.some((a) => a > 0 && a < 255), edge);
}
{
  const d = newDoc(4, 4);
  d.frames[0][0].set([255, 0, 0, 255], 0);
  const top = addLayer(d);
  d.frames[0][top].set([0, 0, 255, 128], 0);
  const flat = flatten(d, 0);
  ok("layers composite bottom first", flat[0] > 100 && flat[2] > 100 && flat[3] === 255, Array.from(flat.subarray(0, 4)));
  d.layers[top].visible = false;
  ok("a hidden layer is not drawn", flatten(d, 0)[2] === 0);
  d.layers[top].visible = true;
  moveLayer(d, top, 0);
  ok("moving a layer moves its cels in every frame", d.frames[0][0][2] === 255 && d.frames[0][1][0] === 255);
  moveLayer(d, 0, 1);
  ok("merge down keeps the look", mergeDown(d, 1) && d.layers.length === 1 && d.frames[0][0][2] > 100 && d.frames[0][0][0] > 100);
  ok("the last layer cannot be removed", !removeLayer(d, 0));
}
{
  const d = newDoc(4, 4);
  d.frames[0][0].set([255, 255, 255, 255], 0);
  const f = addFrame(d);
  ok("a new frame copies the one before, as its own copy", f === 1 && d.frames[1][0][0] === 255 && d.frames[1][0] !== d.frames[0][0]);
  d.frames[1][0].set([0, 0, 0, 0], 0);
  const on = flatten(d, 1, { onion: 0.5 });
  ok("onion skin shows the frame before, faint and tinted", on[3] > 0 && on[3] < 255 && on[0] > on[1], Array.from(on.subarray(0, 4)));
  ok("the last frame cannot be removed, others can", removeFrame(d, 1) && !removeFrame(d, 0));
}
{
  const d = newDoc(8, 8);
  const h = new DrawHistory();
  h.cel(d, 0, 0);
  d.frames[0][0][3] = 255;
  const u = h.undo(d);
  ok("undo puts a cel back", u.frames[0][0][3] === 0 && h.canRedo);
  ok("...and redo again", h.redo(u).frames[0][0][3] === 255);
  h.doc(d);
  addLayer(d);
  const back = h.undo(d);
  ok("undo of a structure change gives the drawing before it", back.layers.length === 1 && d.layers.length === 2);
}
{
  const d = newDoc(8, 6, { fps: 12 });
  addFrame(d); addFrame(d);
  d.frames[2][0].set([0, 255, 0, 255], 0);
  const sh = spriteSheet(d, 0, 1);
  ok("three frames make a 2 x 2 sheet with padding", sh.w === 17 && sh.h === 13 && sh.frames[2].x === 0 && sh.frames[2].y === 7, sh.frames);
  ok("...each frame where it says", sh.data[(7 * 17 + 0) * 4 + 1] === 255);
  const j = atlasJson("hero", "hero.png", sh, d.fps);
  ok("the atlas names every frame, the image, the animation and the frame time",
    Object.keys(j.frames).length === 3 && j.frames.hero_2.frame.y === 7 && j.meta.image === "hero.png" && j.animations.hero.length === 3 && j.frames.hero_0.duration === 83, j);
}
{
  const d = newDoc(3, 2);
  addLayer(d);
  d.layers[1].name = "ink";
  d.frames[0][1].set([1, 2, 3, 4], 4);
  const enc = (c: Uint8ClampedArray) => Buffer.from(c).toString("base64");
  const dec = (s: string) => new Uint8ClampedArray(Buffer.from(s, "base64"));
  const j = JSON.parse(JSON.stringify(docToJson(d, enc)));
  const back = docFromJson(j, dec)!;
  ok("the file keeps size, layers and pixels", back.w === 3 && back.layers[1].name === "ink" && back.frames[0][1][5] === 2);
  ok("a file that is not a drawing is refused", docFromJson({ format: "x" }, dec) === null);
  const bad = docFromJson({ ...j, frames: [["nonsense"]] }, () => null)!;
  ok("a cel that cannot be read is blank, not an error", bad.frames[0].length === 2 && bad.frames[0][0].every((v) => v === 0));
}
{
  const c = new Uint8ClampedArray(4 * 4 * 4);
  c.set([9, 9, 9, 255], 0);
  const s = shiftCel(c, 4, 4, 2, 1);
  ok("move shifts the cel by whole pixels", s[(1 * 4 + 2) * 4] === 9 && s[0] === 0);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

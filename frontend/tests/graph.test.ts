// The graph editor's arithmetic (graph.ts) and the keys a clip keeps (kit.ts).
import { autoSlope, curvePoints, dragHandle, graphBounds, handlePoints, moveKey, sampleKeys, type GraphKey } from "../src/components/engine/edit/graph";
import { insertKey, normaliseEdits, sampleTrack } from "../src/components/engine/edit/kit";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-6) => Math.abs(a - b) < e;

{
  const k: GraphKey[] = [{ t: 0, v: 0, ease: "bezier" }, { t: 10, v: 1, ease: "bezier" }];
  ok("bezier passes through its keys", near(sampleKeys(k, 0), 0) && near(sampleKeys(k, 10), 1));
  ok("...and with flat end handles it eases in and out", near(sampleKeys(k, 5), 0.5) && sampleKeys(k, 1) < 0.1 && sampleKeys(k, 9) > 0.9, [sampleKeys(k, 1), sampleKeys(k, 9)]);
  ok("it holds before the first key and after the last", sampleKeys(k, -5) === 0 && sampleKeys(k, 99) === 1);
}
{
  // A peak: auto clamped lies flat on it, so the curve never goes over it.
  const k: GraphKey[] = [{ t: 0, v: 0, ease: "bezier" }, { t: 10, v: 1, ease: "bezier" }, { t: 20, v: 0, ease: "bezier" }];
  ok("auto clamped is flat on a peak", autoSlope(k, 1) === 0);
  const top = Math.max(...curvePoints(k, 0, 20, 8).map((p) => p[1]));
  ok("...so the curve does not overshoot it", top <= 1 + 1e-9, top);
  const rise: GraphKey[] = [{ t: 0, v: 0, ease: "bezier" }, { t: 10, v: 1, ease: "bezier" }, { t: 20, v: 2, ease: "bezier" }];
  ok("on a steady rise the slope is the line through the neighbours", near(autoSlope(rise, 1), 0.1));
  const steps: GraphKey[] = [{ t: 0, v: 0, ease: "bezier" }, { t: 1, v: 1, ease: "bezier" }, { t: 20, v: 1.01, ease: "bezier" }];
  const pts = curvePoints(steps, 0, 20, 8);
  ok("a fast rise then a slow one does not overshoot the slow key (Fritsch-Carlson)", pts.every((p) => p[1] <= 1.01 + 1e-9), Math.max(...pts.map((p) => p[1])));
}
{
  const lin: GraphKey[] = [{ t: 0, v: 0, ease: "linear" }, { t: 4, v: 2 }];
  ok("linear is a straight line", near(sampleKeys(lin, 1), 0.5));
  ok("constant holds until the next key", sampleKeys([{ t: 0, v: 3, ease: "constant" }, { t: 4, v: 7 }], 3.9) === 3);
  ok("a key with no ease named is linear, as before", near(sampleKeys([{ t: 0, v: 0 }, { t: 4, v: 2 }], 1), 0.5));
  const back = sampleKeys([{ t: 0, v: 0, ease: "back" }, { t: 10, v: 1 }], 1);
  ok("back goes a little behind the start first", back < 0, back);
  const el = sampleKeys([{ t: 0, v: 0, ease: "elastic" }, { t: 10, v: 1 }], 2);
  ok("elastic goes past the end and settles", el > 1, el);
  const bo = [0, 2, 4, 6, 8, 10].map((t) => sampleKeys([{ t: 0, v: 0, ease: "bounce" }, { t: 10, v: 1 }], t));
  ok("bounce stays between its keys and ends there", bo.every((v) => v >= -1e-9 && v <= 1 + 1e-9) && near(bo[5], 1), bo);
}
{
  const k: GraphKey[] = [{ t: 0, v: 0, ease: "bezier" }, { t: 9, v: 0, ease: "bezier" }, { t: 18, v: 0, ease: "bezier" }];
  const h = handlePoints(k, 1);
  ok("handles sit a third of the way to each neighbour", h.in![0] === 6 && h.out![0] === 12);
  const s = dragHandle(k, 1, "out", [12, 3]);
  ok("dragging a handle up makes the slope, and both sides stay in line", near(s.to, 1) && near(s.ti, 1), s);
  k[1].ti = s.ti; k[1].to = s.to;
  ok("...and the curve follows the handle", sampleKeys(k, 10) > 0.5, sampleKeys(k, 10));
  const b = dragHandle(k, 1, "in", [6, 0], true);
  ok("a broken handle moves one side alone", near(b.ti, 0) && near(b.to, 1), b);
}
{
  const k: GraphKey[] = [{ t: 0, v: 0 }, { t: 5, v: 1 }, { t: 10, v: 2 }];
  const i = moveKey(k, 0, 7, 4);
  ok("a key moved past another keeps the keys in time order", i === 1 && k.map((x) => x.t).join() === "5,7,10", k);
  moveKey(k, 1, 10, 4);
  ok("a key never lands on another key's frame", new Set(k.map((x) => x.t)).size === 3, k);
  const g = graphBounds([[{ t: 0, v: 0, ease: "back" }, { t: 10, v: 1 }]], 0, 10);
  ok("frame all takes in an overshoot, with a margin", g.v0 < -0.1 && g.v1 > 1, g);
}
{
  const e = normaliseEdits({ clips: [{ name: "wave", tracks: [{ target: "arm", prop: "rot", axis: 2, keys: [
    { t: 10, v: 1, ease: "bezier", ti: 0.2, to: "x" }, { t: 0, v: 0, ease: "zigzag" }] }] }] });
  const keys = e.clips[0].tracks[0].keys;
  ok("the file keeps the new eases and the handle slopes, sorted, and drops what is not one",
    keys[0].ease === "linear" && keys[1].ease === "bezier" && keys[1].ti === 0.2 && keys[1].to === undefined, keys);
  const clip = { name: "c", fps: 24, start: 0, end: 24, loop: true, tracks: [] as any[] };
  insertKey(clip, "arm", "pos", 1, 0, 0);
  insertKey(clip, "arm", "pos", 1, 12, 1);
  ok("a new key is Bezier, as in Blender", clip.tracks[0].keys.every((k: any) => k.ease === "bezier"));
  ok("sampleTrack is the graph's own curve", near(sampleTrack(clip.tracks[0], 6), sampleKeys(clip.tracks[0].keys, 6)));
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

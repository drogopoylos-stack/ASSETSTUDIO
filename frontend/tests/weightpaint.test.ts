// Weight painting (weightpaint.ts): corrections laid on automatic weights, every vertex summing to 1.
import { applyWeightPaints, brushWeight, cleanWeightPaints, mergeWeightPaints, setWeight, weightColor, weightOf } from "../src/components/engine/edit/weightpaint";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number, e = 1e-6) => Math.abs(a - b) < e;
const sum = (w: Float32Array, v: number, k: number) => { let s = 0; for (let j = 0; j < k; j++) s += w[v * k + j]; return s; };

{
  // one vertex held 0.6 by bone 0 and 0.4 by bone 1
  const index = new Uint16Array([0, 1, 0, 0]), weight = new Float32Array([0.6, 0.4, 0, 0]);
  setWeight(index, weight, 0, 4, 1, 0.8);
  ok("set: the bone gets its weight", near(weightOf(index, weight, 0, 4, 1), 0.8));
  ok("...the others share the rest in their old proportion", near(weightOf(index, weight, 0, 4, 0), 0.2) && near(sum(weight, 0, 4), 1), Array.from(weight));
  setWeight(index, weight, 0, 4, 2, 0.5);
  ok("a bone not among the influences takes a free slot", near(weightOf(index, weight, 0, 4, 2), 0.5) && near(sum(weight, 0, 4), 1), [Array.from(index), Array.from(weight)]);
}
{
  const index = new Uint16Array([0, 1, 2, 3]), weight = new Float32Array([0.4, 0.3, 0.2, 0.1]);
  setWeight(index, weight, 0, 4, 7, 0.5);
  ok("with no slot free, the weakest bone gives its place up", index[3] === 7 && near(weightOf(index, weight, 0, 4, 7), 0.5) && near(sum(weight, 0, 4), 1), [Array.from(index), Array.from(weight)]);
}
{
  const index = new Uint16Array([3, 0, 0, 0]), weight = new Float32Array([1, 0, 0, 0]);
  setWeight(index, weight, 0, 4, 3, 0.3);
  ok("a corner with one bone keeps summing to 1", near(sum(weight, 0, 4), 1));
}
{
  // three vertices, two copies of one corner
  const pos = new Float32Array([0, 0, 0, 1, 0, 0, 1, 0, 0]);
  const index = new Uint16Array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]), weight = new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]);
  const names = ["root", "arm"];
  const n = applyWeightPaints(pos, index, weight, 4, (s) => names.indexOf(s), [{ bone: "arm", at: [1, 0, 0], w: 0.75 }, { bone: "nobody", at: [0, 0, 0], w: 1 }]);
  ok("a correction finds every copy of its corner", n === 1 && near(weightOf(index, weight, 1, 4, 1), 0.75) && near(weightOf(index, weight, 2, 4, 1), 0.75));
  ok("...and leaves the other corner, and an unknown bone, alone", near(weightOf(index, weight, 0, 4, 0), 1));
}
{
  ok("add pulls toward 1", near(brushWeight(0.5, "add", 0.5), 0.75));
  ok("subtract toward 0", near(brushWeight(0.5, "subtract", 0.5), 0.25));
  ok("set toward the value", near(brushWeight(0.5, "set", 1, 0.2), 0.2));
  ok("the heat map: blue at 0, red at 1", weightColor(0).join() === "0,0,1" && weightColor(1).join() === "1,0,0");
}
{
  const a = cleanWeightPaints([{ bone: "arm", at: [1, 2, 3], w: 0.2 }, { bone: "arm", at: [1, 2, 3], w: 0.9 }, { bone: "", at: [0, 0, 0], w: 1 }, { bone: "leg", at: [0, 0], w: 1 }]);
  ok("a file's corrections: the last for a bone and place wins, bad rows go", a.length === 1 && a[0].w === 0.9, a);
  const m = mergeWeightPaints(a, [{ bone: "arm", at: [1, 2, 3], w: 0.1 }, { bone: "leg", at: [1, 2, 3], w: 0.5 }]);
  ok("merge replaces the same bone at the same place, keeps the others", m.length === 2 && m.find((p) => p.bone === "arm")!.w === 0.1, m);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

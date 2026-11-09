// The game hand (handrig.ts), held to numbers measured against hands.ts's own field.
//
// The field is the anatomy; the rig is what a game can animate. These checks say the rig keeps the
// anatomy: the rest mesh lies on the field, no weight crosses from one finger to another, and the
// correctives bring a posed hand closer to that pose's own surface than bones alone do. The
// thresholds are the measured values (2026-11-09) with room, so a change that makes the hand worse
// fails here before anybody has to see it.
import { handField } from "../src/components/engine/edit/hands";
import { layout, restOpts, rigHand } from "../src/components/engine/edit/handrig";
import { isosurfaceArrays } from "../src/components/engine/edit/ops";
import { decimateArrays } from "../src/components/engine/edit/decimate";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};

function build(opts: any, triangles = 2400) {
  const rest = handField(restOpts(opts));
  const iso = isosurfaceArrays(rest.field, rest.min, rest.max, rest.grid(64), 0);
  const d = decimateArrays(iso.pos, iso.idx, { target: triangles });
  return { d, rig: rigHand(opts, d.pos, d.idx) };
}

const t0 = performance.now();
const { d, rig } = build({ side: "R" });
const ms = performance.now() - t0;
ok("a hand is built and rigged in under 2 s", ms < 2000, Math.round(ms) + " ms");
ok("2400 triangles, as asked", d.idx.length / 3 <= 2400 && d.idx.length / 3 > 2000, d.idx.length / 3);
ok("20 bones: root, 4 x (metacarpal + 3), thumb x 3", rig.bones.length === 20, rig.bones.map((b) => b.name));
ok("parents come before children", rig.bones.every((b, i) => b.parent < i));
ok("the correctives have their names", rig.morphs.map((m) => m.name).join() === "index_curl,middle_curl,ring_curl,little_curl,thumb_curl,palm_curl,grip");

// ---- weights
{
  let notOne = 0, twoFingers = 0;
  for (let v = 0; v < rig.parts.length; v++) {
    let s = 0;
    const digits = new Set<string>();
    for (let j = 0; j < 4; j++) {
      const w = rig.skinWeight[v * 4 + j];
      s += w;
      const p = rig.bones[rig.skinIndex[v * 4 + j]].part;
      if (w > 0.05 && p !== "palm") digits.add(p);
    }
    if (Math.abs(s - 1) > 1e-4) notOne++;
    if (digits.size > 1) twoFingers++;
  }
  ok("every vertex's weights sum to 1", notOne === 0, notOne);
  ok("no vertex is weighted to two different digits", twoFingers === 0, twoFingers);
}

// ---- the rest pose and the poses
{
  const rest = rig.fit({ pose: "open", spread: undefined } as any, true);
  ok("posed back to rest, the mesh lies on the field (mean under 0.15 mm)", rest.meanMm < 0.15, rest);
  const gripBones = rig.fit({ pose: "grip" }, false), gripFixed = rig.fit({ pose: "grip" }, true);
  ok("the grip's corrective at least halves the distance to the grip's surface", gripFixed.meanMm < gripBones.meanMm * 0.5, [gripBones, gripFixed]);
  ok("...and the volume the bones lose", gripFixed.sunk < gripBones.sunk * 0.5, [gripBones.sunk, gripFixed.sunk]);
  const fistBones = rig.fit({ pose: "fist" }, false), fistFixed = rig.fit({ pose: "fist" }, true);
  ok("the fist's correctives bring it closer to the fist", fistFixed.meanMm < fistBones.meanMm * 0.85, [fistBones, fistFixed]);
}

// ---- what a pose asks the morphs for
{
  const fist = rig.pose({ pose: "fist" }).morphs, grip = rig.pose({ pose: "grip" }).morphs, open = rig.pose({ pose: "open" }).morphs;
  ok("a fist turns every curl corrective fully on", ["index_curl", "middle_curl", "ring_curl", "little_curl", "palm_curl"].every((k) => fist[k] > 0.99), fist);
  ok("a grip uses its own corrective and no curl", grip.grip === 1 && grip.index_curl === 0, grip);
  ok("the open hand uses none", Object.values(open).every((w) => w < 0.15), open);
  const one = rig.pose({ pose: "open", curl: [1, 0, 0, 0] }).morphs;
  ok("one curled finger turns on its own corrective, and not the others", one.index_curl > 0.95 && one.middle_curl < 0.15, one);
}

// ---- determinism and the left hand
{
  const again = build({ side: "R" }).rig;
  ok("the same options make the same weights", again.skinWeight.every((w, i) => w === rig.skinWeight[i]));
  const L = layout(handField(restOpts({ side: "L" }))).bones, R = layout(handField(restOpts({ side: "R" }))).bones;
  ok("the left skeleton is the right one mirrored", L.every((b, i) => Math.abs(b.head[0] + R[i].head[0]) < 1e-9 && Math.abs(b.head[2] - R[i].head[2]) < 1e-9));
  const three = build({ side: "R", fingers: 3, chunk: 0.9 }).rig;
  ok("a three-finger hand has 16 bones and its own curl names", three.bones.length === 16 && three.morphs.some((m) => m.name === "little_curl") && !three.morphs.some((m) => m.name === "ring_curl"));
}

// ---- deform agrees with pose
{
  const p = rig.deform({ pose: "point" }, true);
  ok("a deformed mesh has no NaN", p.every((x) => isFinite(x)));
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

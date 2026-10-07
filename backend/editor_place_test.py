# -*- coding: utf-8 -*-
"""PLACING, DUPLICATING AND REMOVING — proved against real three.js, with no browser.

The editor's placement layer is plain TypeScript over a three.js scene, so it can be bundled and
run in Node against the same r169 build the forge uses. That is worth doing rather than clicking:
the three operations an engine editor has are exactly the ones whose bugs are silent — an object
that lands at the origin instead of under the camera, a duplicate that shares a transform with its
original, a placement that does not survive the trip through the document on disk.

    python editor_place_test.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"
# A three.js build to run the editor's own code against. `data/` is not in the repository, so
# the second candidate is the one a fresh checkout has: npm puts three under the frontend the
# moment `npm install` runs. Neither present means SKIPPED, not failed - this test proves the
# editor, and a missing fixture is not the editor being broken.
def _three() -> Path:
    for c in (ROOT / "data" / "ab" / "harness" / "three.module.js",
              FRONT / "node_modules" / "three" / "build" / "three.module.js"):
        if c.exists():
            return c
    return ROOT / "data" / "ab" / "harness" / "three.module.js"


THREE = _three()

ok = fail = 0


def check(name: str, cond: bool, got=None) -> None:
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, "" if got is None else repr(got)[:200]))


SCRIPT = r"""
import * as THREE from "%(three)s";
import { applyPlaced, makePrimitive, placedIdOf, stableKeys } from "%(ops)s";
import { normaliseEdits, emptyEdits, isEmpty, countEdits } from "%(kit)s";

const out = {};
const scene = new THREE.Group();
scene.name = "world";
const rock = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
                            new THREE.MeshStandardMaterial({ color: 0x888888 }));
rock.name = "rock";
scene.add(rock);

// what the game itself built has no mark
out.gameObjectUnmarked = placedIdOf(rock) === "";

// 1. a primitive, placed where the camera was looking
const items = [
  { id: "p1", name: "block", ref: { kind: "primitive", shape: "box", color: "#88aa44" },
    pos: [3, 0, -2], rot: [0, 0.5, 0], scale: [2, 1, 1] },
  { id: "p2", name: "lamp", ref: { kind: "primitive", shape: "pointLight", color: "#ffddaa" },
    pos: [0, 4, 0], rot: [0, 0, 0], scale: [1, 1, 1] },
];
const r1 = await applyPlaced(scene, items, THREE, async () => null);
out.added = r1.added;
out.errors1 = r1.errors;
const block = scene.children.find((c) => c.name === "block");
out.blockThere = !!block;
out.blockPos = block ? [block.position.x, block.position.y, block.position.z] : null;
out.blockScale = block ? [block.scale.x, block.scale.y, block.scale.z] : null;
out.blockMarked = block ? placedIdOf(block) : "";
out.lampIsLight = !!scene.children.find((c) => c.name === "lamp" && c.isLight);

// a child of a placed object is still "placed" — deleting has to know that
const child = new THREE.Mesh(new THREE.SphereGeometry(0.2, 8, 6), new THREE.MeshBasicMaterial());
block.add(child);
out.childMarked = placedIdOf(child);

// 2. "another one of that" — a copy of an object the GAME built, which the document cannot hold
const key = [...stableKeys(scene)].find(([o]) => o === rock)?.[1] || "";
const r2 = await applyPlaced(scene, [
  { id: "p3", name: "rock copy", ref: { kind: "clone", of: key },
    pos: [-4, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] },
], THREE, async () => null);
out.cloneAdded = r2.added;
const copy = scene.children.find((c) => c.name === "rock copy");
out.cloneThere = !!copy;
out.cloneIsMesh = !!(copy && copy.isMesh);
out.cloneSharesGeometry = !!(copy && copy.geometry === rock.geometry);
out.cloneMovedAlone = !!(copy && copy.position.x === -4 && rock.position.x === 0);

// 3. a builder from the project's own code, through the caller's resolver
const r3 = await applyPlaced(scene, [
  { id: "p4", name: "from code", ref: { kind: "code", file: "src/props.js", export: "buildTree" },
    pos: [1, 0, 1], rot: [0, 0, 0], scale: [1, 1, 1] },
], THREE, async (ref) => {
  out.askedFor = ref.file + "#" + ref.export;
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.ConeGeometry(0.5, 2, 8), new THREE.MeshStandardMaterial()));
  return g;
});
out.codeAdded = r3.added;

// 4. one bad entry costs that entry and nothing else
const r4 = await applyPlaced(scene, [
  { id: "bad", name: "nope", ref: { kind: "code", file: "missing.js" }, pos: [0, 0, 0], rot: [0, 0, 0], scale: [1, 1, 1] },
  { id: "p5", name: "after the bad one", ref: { kind: "primitive", shape: "sphere" }, pos: [0, 0, 5], rot: [0, 0, 0], scale: [1, 1, 1] },
], THREE, async (ref) => { if (!ref.export) throw new Error("no export named"); return null; });
out.badErrors = r4.errors.length;
out.goodStillAdded = r4.added;

// 5. the document survives disk: written, read back, still the same placements
const doc = { ...emptyEdits("thing"), placed: items };
const round = normaliseEdits(JSON.parse(JSON.stringify(doc)));
out.roundTrip = (round.placed || []).length;
out.roundTripPos = (round.placed || [])[0]?.pos;
out.roundTripKind = (round.placed || [])[0]?.ref?.kind;
out.countsAsAnEdit = countEdits(round) >= 2 && !isEmpty(round);
// junk is dropped, the rest is kept
const dirty = normaliseEdits({ placed: [
  { id: "x", ref: { kind: "wat" }, pos: [0, 0, 0] },
  { ref: { kind: "primitive" } },
  { id: "y", name: "keep", ref: { kind: "primitive", shape: "cone" }, pos: [1, 2, 3] },
] });
out.junkDropped = (dirty.placed || []).length;
out.junkKeptGood = (dirty.placed || [])[0]?.name;
out.defaultScale = (dirty.placed || [])[0]?.scale;

// 6. a primitive is a real object, and a light is a real light
out.boxIsMesh = makePrimitive(THREE, "box").isMesh === true;
out.dirIsLight = makePrimitive(THREE, "dirLight").isDirectionalLight === true;
out.planeLiesDown = Math.abs(makePrimitive(THREE, "plane").rotation.x + Math.PI / 2) < 1e-6;

console.log(JSON.stringify(out));
"""


def main() -> int:
    if not THREE.exists():
        print("SKIP - no three.js build here. Run `npm install` in frontend/, or put one at")
        print("       " + str(THREE))
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="place-test-"))
    ops_js, kit_js = tmp / "ops.mjs", tmp / "kit.mjs"
    for src, dest in ((FRONT / "src/components/engine/edit/ops.ts", ops_js),
                      (FRONT / "src/components/engine/edit/kit.ts", kit_js)):
        r = subprocess.run(["npx", "esbuild", str(src), "--bundle", "--format=esm",
                            "--platform=neutral", "--log-level=warning", "--outfile=" + str(dest)],
                           cwd=str(FRONT), capture_output=True, text=True, shell=True)
        if r.returncode != 0:
            print("could not bundle", src.name, r.stderr[-800:])
            return 1

    def url(p: Path) -> str:
        return p.resolve().as_uri()

    run = tmp / "run.mjs"
    run.write_text(SCRIPT % {"three": url(THREE), "ops": url(ops_js), "kit": url(kit_js)},
                   encoding="utf-8")
    r = subprocess.run(["node", str(run)], capture_output=True, text=True, shell=True)
    if r.returncode != 0:
        print("node failed:\n", r.stderr[-2000:])
        return 1
    got = json.loads(r.stdout.strip().splitlines()[-1])

    print("\nPlacing things the game's code did not make")
    check("a primitive is placed", got["blockThere"] and got["added"] == 2, got.get("errors1"))
    check("...where it was told, at the size it was told",
          got["blockPos"] == [3, 0, -2] and got["blockScale"] == [2, 1, 1],
          [got["blockPos"], got["blockScale"]])
    check("...and is marked as the editor's own, not the game's",
          got["blockMarked"] == "p1" and got["gameObjectUnmarked"], got["blockMarked"])
    check("...including everything under it", got["childMarked"] == "p1", got["childMarked"])
    check("a light is placed as a light", got["lampIsLight"] and got["dirIsLight"])
    check("a plane lies down, so it reads as ground", got["planeLiesDown"])
    check("a box is a mesh", got["boxIsMesh"])

    print("\nAnother one of that — copying what the game itself built")
    check("the clone is made", got["cloneAdded"] == 1 and got["cloneThere"])
    check("...as real geometry", got["cloneIsMesh"])
    check("...sharing the original's geometry, so a hundred cost one",
          got["cloneSharesGeometry"])
    check("...and moving alone, not with the original", got["cloneMovedAlone"])

    print("\nThe project's own builders")
    check("the resolver is asked for the right export",
          got["askedFor"] == "src/props.js#buildTree", got.get("askedFor"))
    check("...and what it returns goes in", got["codeAdded"] == 1)
    check("one bad entry costs that entry alone",
          got["badErrors"] == 1 and got["goodStillAdded"] == 1,
          [got["badErrors"], got["goodStillAdded"]])

    print("\nThe document that survives a reload")
    check("placements round-trip through JSON", got["roundTrip"] == 2, got["roundTrip"])
    check("...with their transform and their kind",
          got["roundTripPos"] == [3, 0, -2] and got["roundTripKind"] == "primitive",
          [got["roundTripPos"], got["roundTripKind"]])
    check("...and count as something to save", got["countsAsAnEdit"])
    check("a hand-written file cannot break the scene",
          got["junkDropped"] == 1 and got["junkKeptGood"] == "keep",
          [got["junkDropped"], got["junkKeptGood"]])
    check("...and a missing scale defaults to 1", got["defaultScale"] == [1, 1, 1],
          got["defaultScale"])

    print("\n  %d passed, %d failed" % (ok, fail))
    return 0 if not fail else 2


if __name__ == "__main__":
    raise SystemExit(main())

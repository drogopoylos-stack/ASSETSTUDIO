---
name: threejs-asset-forge
description: "Build a game-ready three.js asset in code: a lit studio, a camera that frames it, and renders to judge it by. Use for procedural props, creatures and set pieces that must ship as JavaScript rather than as a model file."
category: studio-game
disable-model-invocation: true
metadata:
  created: 2026-07-18
  updated: 2026-07-18
---

# THREEJS-ASSET-FORGE — building top-tier 3D models in pure code

Drop this into any Three.js project. **Claude: when creating a 3D model/asset in code (from a
reference image or a description), follow this document.** It upgrades freestyle primitive-stacking
into a disciplined pipeline (the img2threejs method) plus the full trick arsenal. Pairs with
THREE-CINEMATIC.md (scene/lighting); this file is about the OBJECTS themselves.

Code-first is the DEFAULT for props, architecture, environment and stylized gameplay objects:
reviewable, parametric (seed → variations), destruction-ready (every part is a named mesh), and
phone-friendly (you control every triangle). Characters/organic-detailed hero pieces are still
better from Meshy/Blender — export GLB and load those.

---

## 1) THE PROCESS (non-negotiable — this is what separates good from toy)

Never free-style a model. Run these stages, **screenshotting and judging after each** (use the
project's headless browser or a quick viewer page; one stage per iteration):

1. **DETAIL INVENTORY** — before ANY code, list from the reference: every component (count them:
   "2 pillars × 5 stones, 9 arch wedges, 2 carved discs, base = 3 slabs…"), proportions as ratios
   (opening width : pillar height…), palette (4–6 sampled colors), surface story (worn? mossy?
   metallic?), and which parts must be SEPARATE for gameplay (destruction/animation).
2. **BLOCKOUT** — plain boxes at final proportions. Judge ONLY silhouette vs reference. Wrong
   proportions here poison every later stage.
3. **STRUCTURE** — real part breakdown (each stone/plank/panel its own mesh in a named Group).
4. **FORM** — shape language: bevels, tapers, coherent-noise displacement, CSG cuts.
5. **MATERIAL** — palette, roughness zones, per-part variation.
6. **SURFACE** — grime, edge wear, cracks, decals (shader injection + canvas textures).
7. **DRESSING** — moss/vines/rubble/props that sell age and story.
8. **OPTIMIZE** — merge-until-destroyed, instancing, triangle audit (`renderer.info`).

Ship as a **factory function**: `createRuinArch({ seed, scale, opening, ruinLevel })` returning a
`THREE.Group` with named children plus metadata:
```js
group.userData = { parts: { pillarL, pillarR, arch: voussoirs }, sockets: { top: v3, door: v3 },
                   colliders: [{ type: "box", size, offset }], destructible: voussoirs.map(m => m.name) };
```

## 2) GEOMETRY ARSENAL (in rough order of how often it's the answer)

- **RoundedBoxGeometry** (`three/addons/geometries/RoundedBoxGeometry.js`) — instant "worn block".
- **CSG booleans** (`three-bvh-csg`, npm) — THE unlock for real shapes: subtract a cylinder from a
  wall = arched doorway; subtract random spheres from a block edge = chipped damage; union stones
  into one manifold. `const result = evaluator.evaluate(brushA, brushB, SUBTRACTION);`
- **ExtrudeGeometry + THREE.Shape** — draw the 2D silhouette (with holes!), extrude with bevel:
  moldings, brackets, gears, crowns, flat-pack anything. The most underused geometry in Three.
- **LatheGeometry** — profile → revolve: vases, columns, bottles, shields, bells, tree trunks.
- **TubeGeometry along a CatmullRomCurve3** — ropes, vines, branches, pipes, horns.
- **Coherent-noise displacement** — organic irregularity WITHOUT tearing seams. NEVER jitter
  vertices with independent random offsets (co-located seam vertices get different offsets →
  visible cracks/holes). This applies to EVERY duplicated-vertex geometry: Box/RoundedBox
  duplicate verts at every edge, Extrude per triangle — index-based `rng()` jitter WILL tear
  them. Failure signature in renders: bright hairline diagonal slashes across flat faces /
  background peeking through edge slivers. NEVER classify those as "stylized chisel marks" in a
  screenshot review — they are open mesh tears; fix the deformation, don't style-excuse it.
  Key the offset to POSITION so coincident verts move identically:
  ```js
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const n = noise3(x * 3.1, y * 3.1, z * 3.1) * amp;      // simplex/value noise, NOT Math.random()
    p.setXYZ(i, x + n, y + n * 0.6, z + n);
  }
  geo.computeVertexNormals();
  ```
- **mergeVertices** (`BufferGeometryUtils`) before displacement/smoothing; `toNonIndexed()` when
  you WANT faceted flat-shading.
- **MarchingCubes** (addon) — metaball blobs: moss clumps, slime, clouds, melted things. Far more
  organic than displaced spheres.
- **Recursive/L-system builders** — trees/plants: branch = cylinder + k children scaled ~0.7,
  rotated by golden-angle-ish offsets; leaves = instanced planes/cones at terminal nodes.
- **Capsule-chain characters** — stylized bodies from CapsuleGeometry limbs in a jointed Group
  hierarchy (animate by rotating joints). Blocky-cute works; realistic humans: use GLB instead.

## 3) SURFACE & MATERIAL TRICKS (where "way better looking" lives)

- **Canvas-painted textures** — paint albedo (mottling, speckle, cracks, planks, runes) on a
  512–1024 canvas at build time; clone with random offset/rotation per part. Zero downloads,
  infinite variety. Also paint ROUGHNESS maps (dark = shiny worn centers, light = dusty edges).
- **Triplanar mapping via onBeforeCompile** — texture ANY procedural geometry with no UVs and no
  stretching (project the texture from X/Y/Z and blend by normal). The cure for "my generated
  mesh has garbage UVs".
- **World-space shader aging** (onBeforeCompile): grime rising from ground
  (`smoothstep` on world Y), large weather patches + fine grain (value-noise in fragment),
  per-part hue jitter. GLSL GOTCHA: `patch`, `sample`, `filter` are reserved words — don't use
  them as variable names.
- **Vertex-color AO/gradients** — darken vertices near the ground, inside crevices, under
  overhangs at build time (you KNOW the structure — exploit it). Cheapest "baked lighting" there is.
- **MatCap materials** (`MeshMatcapMaterial`) — a whole lighting look baked into one small
  texture; phones render it for free; perfect for stylized props.
- **Emissive accents** (windows, crystals, runes, eyes) with `emissiveIntensity` 2–6 → the bloom
  pass (THREE-CINEMATIC §6) turns them into magic.
- **Palette discipline** — sample 4–6 colors from the reference, desaturate ~10%, and reuse them
  across parts, lights and fog. Cohesion IS the "pro look".

## 4) EFFICIENCY RULES (bad-phone budget)

- **Merged-until-destroyed**: intact asset = ONE merged mesh (1 draw call); swap to the parted
  Group only at the destruction moment, apply physics to parts, fade them out.
- **InstancedMesh** for anything repeated ≥3× (stones, planks, leaves, grass) with per-instance
  color/scale jitter (uniform clones scream "fake").
- LOD by parameter: regenerate with fewer segments/parts for distance tiers — a luxury only
  parametric assets have.
- Budgets: props 300–3k tris · hero structures 3–10k · flora instance 30–150.
  Audit with `renderer.info.render.calls` (< ~200) and `.triangles`.
- Prefer vertex colors / canvas textures ≤1024² / triplanar over big image textures.

## 5) REUSE & EXPORT

A code asset isn't trapped in code: `GLTFExporter` (addon) serializes the built Group to GLB —
name parts first and the exported file keeps the structure (destructibility survives). House
rule: **author in code, export GLB for cross-project reuse, keep the factory as source of truth.**

## 6) SELF-JUDGING CHECKLIST (per stage screenshot)

- Silhouette matches reference at squint distance? (blockout stage kills or saves everything)
- Any component from the inventory missing? Count them.
- Do parts intersect/float? Seams torn? (→ coherent noise, mergeVertices)
- One palette, or programmer-art rainbow? Same roughness everywhere (lazy) or zoned?
- Does it read as ONE material story (age, wear, dirt agree with each other)?
- Triangle/draw-call audit inside budget?
- Would knocking a piece off work? (structure check — parts + names + userData)

## 7) WHEN NOT TO CODE-BUILD

Organic hero detail (realistic characters, creatures, faces, cloth), photo-real scans, or any
case where a fixed mesh from Meshy/Blender/the CC0 library is already good and doesn't need
destruction/variation — load the GLB (GLTFLoader, 5 lines) and spend your effort on the scene.

---
name: stoneforge
description: "Build weathered, textureless, ONE-draw-call hard-surface 3D assets in three.js - stone, rock, ruins, masonry, cliffs, statues. Vertex colours and merged geometry, no texture files."
category: studio-game
disable-model-invocation: true
metadata:
  created: 2026-07-19
  updated: 2026-07-19
  bundles: [stoneforge.js, STONEFORGE_GUIDE.md, stoneforge-demo.html, sf_demo.png]
---

# STONEFORGE — textureless procedural hard-surface assets in three.js

A tiny, dependency-light toolkit for building **weathered, realistic, TEXTURELESS** hard-surface
props entirely in code. One material, no textures, no UV unwrap, **one draw call per asset**.
Overgrowth (moss/snow/rust/sand/lichen) is a shader layer with **zero extra triangles**.

Drop `stoneforge.js` into any three.js project (r150+). Pairs with **threejs-asset-forge** (the
disciplined build pipeline) and **three-cinematic** (scene/lighting) — this is the *material+kit* layer.

## Bundled with this skill
- `stoneforge.js` — the reusable module (copy into your project).
- `STONEFORGE_GUIDE.md` — the complete guide: full API, worked examples, tuning, perf/LOD, gotchas,
  and the full asset-applicability table. **Read it for anything beyond the summary below.**
- `stoneforge-demo.html` — runnable demo: columns·moss, brick wall·snow, arch·moss, boulder·lichen.
- `sf_demo.png` — the demo render.

## Use it in 3 lines
```js
import { StoneKit, addStack, makeStoneMaterial, OVERGROWTH } from './stoneforge.js';
const kit = new StoneKit(); addStack(kit,{ base:new THREE.Vector3(), count:6, taper:0.12 });
const { material } = makeStoneMaterial({ overgrowth: OVERGROWTH.moss });
scene.add(new THREE.Mesh(kit.build(), material)); // one draw call, no textures
```
**Serving:** ES-module `import` does NOT run from a double-clicked `file://` (browser CORS). Use a
static server while developing (`python -m http.server`, `npx serve`) or a bundler (Vite/webpack/Next).

## The recipe (what the module encodes)
parametric block-kit → merged to one mesh → per-block vertex-attribute variation → UV-free
3D-noise PBR + edge wear + derivative bump → optional shader overgrowth → GTAO.

Four reusable ideas: **①modular kit** (beveled boxes + extruded voussoirs, placed with tiny jitter),
**②merge to one mesh** (one draw call), **③per-piece vertex attributes** (`aLocalPos/aLocalNormal/
aHalf/aSeed` stamped before merge → one shader still shades each block individually), **④UV-free
procedural PBR** (3D world-space simplex/fbm → colour/veins/stains, seamless on every face; edge wear
from local coords; Mikkelsen derivative bump = relief with no normal map; overgrowth painted on
up-faces + dripped down edges).

## Why it never cracks (the mesh-tear rule)
StoneForge only jitters **whole rigid pieces** (per-block position/rotation) — it NEVER index-jitters
vertices. Irregularity comes from 3D world-space noise + per-block attributes, so merged blocks never
tear open. This is the correct pattern from the mesh-tear rule (bright hairline slashes / holes come
from moving duplicated edge/triangle verts independently — see threejs-asset-forge / img2threejs).

## API summary (full signatures in STONEFORGE_GUIDE.md §3)
- `new StoneKit({extras})` · `kit.add(geo,{position,quaternion,scale,size,seed,attrs})` · `kit.build()` → merged geo.
- Primitives (centered): `beveledBox(w,h,d,{bevel,seg})`, `voussoir(innerW,outerW,thick,depth,{bevel})`.
- Layout helpers: `addStack(kit,{base,w,h,d,count,gap,jitter,taper})`, `addArch(kit,{count,innerR,thick,depth,center,bevel,jitter})`, `addBrickWall(kit,{origin,cols,rows,w,h,d,gap,jitter})`.
- `makeStoneMaterial({colorA,colorB,roughness,noiseScale,edgeWear,bump,damp,warm,envMapIntensity,overgrowth})` → `{material,uniforms}` (tweak/animate via `uniforms`).
- `OVERGROWTH` presets: `moss·snow·rust·sand·lichen`, each `{dark,mid,lite,up,cover,drip,scale,amount,fuzz}`. Pass a custom object for verdigris/algae/etc.
- `quickStudio(renderer,scene)` — optional 3-point soft lighting + shadow + tone-mapping for fast previews.
- `SF_NOISE_GLSL` — the exported simplex/fbm GLSL for custom shaders.

## Where it works well (sweet spot = modular, hard-surface, weathered, inorganic)
★★★★★ walls · ruins · castles/dungeons/temples · pillars/columns/obelisks · arches/bridges/aqueducts/gates · cobblestone/paving/stairs · gravestones/plinths/wells/fountains
★★★★☆ rocks/boulders/cliffs/rubble (3D noise = no UV seams) · concrete (bunkers/barriers/curbs) · metal girders/hull/containers (use `rust` — drips from edges/bolts exactly right) · wood beams/planks/crates/fences (stretched noise for grain)
★★★☆☆ tree **bark/wood** only (trunk, boughs, roots, logs, stumps) — leaves/canopy are a separate technique (instanced alpha cards) · ice/crystal (add transmission)
✗ foliage/grass/leaves · organic skin/creatures · cloth/ropes/fluids — different shading problems.

**Overgrowth layer alone** is reusable on anything that ages outdoors: snow on roofs/ledges, rust on
any metal, moss/lichen on stone & bark, sand in desert ruins, verdigris on copper, algae at a waterline.

## Tuning (cheat-sheet — full version in guide §5)
Too CG-clean → raise `edgeWear`, lower `noiseScale`, add jitter. Too busy → raise `noiseScale`, lower
`bump`. Moss flat/painted → raise preset `fuzz`+`drip`, add GTAO. Overgrowth everywhere/nowhere → tune
`up` (higher = tops only) + `cover`. Metal → `material.metalness↑ roughness↓`, `rust` overgrowth.

## Performance
One draw call per merged asset; you control tris via `seg`/`bevel`/block count (the mossy archway is
~25k tris, moss = 0 tris). The material is fbm-heavy → fill-rate bound; for many on-screen instances
bake to textures or drop octaves. Same merged geo+material works in `InstancedMesh`. LOD: build a
low-`seg`/low-block version for distance; overgrowth is shader-side so LODs stay consistent.

## Not in the core (see guide §9)
Engraving/carved reliefs (runes/inscriptions) is deliberately left out (asset-specific). The recipe:
put a glyph height-field into the bump `H`, darken albedo in the groove (AO), settle grime/overgrowth
in it — same derivative-bump path, so it reads as carved *into* the stone. Can be folded back as an
optional `engrave:{...}` material layer on request.

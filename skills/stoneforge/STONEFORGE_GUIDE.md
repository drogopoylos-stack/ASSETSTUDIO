# StoneForge — Complete Guide

A tiny, dependency-light toolkit for building **weathered, realistic, textureless hard‑surface
assets in code** for three.js (r150+). Extracted from the mossy‑archway study.

One material. No textures. No UV unwrapping. One draw call per asset. Moss/snow/rust/etc. as a
shader layer with **zero extra triangles**.

- `stoneforge.js` — the reusable module (drop into any project)
- `stoneforge-demo.html` — runnable demo (columns, brick wall, arch, boulder)
- this guide

---

## Table of contents
1. [Quick start](#1-quick-start)
2. [The four ideas (why it looks good)](#2-the-four-ideas)
3. [API reference](#3-api-reference)
4. [Worked examples](#4-worked-examples)
5. [Tuning cheat‑sheet](#5-tuning-cheat-sheet)
6. [Performance & LOD](#6-performance--lod)
7. [Gotchas & limits](#7-gotchas--limits)
8. [**Where it works well (asset guide)**](#8-where-it-works-well)
9. [Extending it](#9-extending-it)

---

## 1. Quick start

```js
import * as THREE from 'three';
import { StoneKit, beveledBox, addStack, makeStoneMaterial, OVERGROWTH } from './stoneforge.js';

// 1) assemble pieces -> one geometry
const kit = new StoneKit();
addStack(kit, { base:new THREE.Vector3(0,0,0), w:0.8,h:0.5,d:0.8, count:6, taper:0.12 });
const geo = kit.build();                       // ONE merged BufferGeometry (1 draw call)

// 2) procedural material (here: mossy stone)
const { material, uniforms } = makeStoneMaterial({
  colorA: 0x33322f, colorB: 0x8b8579,          // dark & light stone
  overgrowth: OVERGROWTH.moss,                 // or .snow / .rust / .sand / .lichen / null
});

// 3) mesh
const mesh = new THREE.Mesh(geo, material);
mesh.castShadow = mesh.receiveShadow = true;
scene.add(mesh);
```

> **Serving:** ES‑module `import` does **not** work from `file://` (browser CORS). Use any static
> server while developing: `python -m http.server` or `npx serve`, then open `http://localhost:...`.
> In a bundler (Vite/webpack/Next) just `import` it normally.

**Requires** `three`, plus two addons the module imports: `RoundedBoxGeometry` and
`BufferGeometryUtils` (both ship with three's `examples/jsm`). No other deps.

---

## 2. The four ideas

These are the reusable tricks — the reason the result reads as real, aged stone instead of CG.

### ① Modular kit
Assets are built from a few **primitive pieces** placed by code — beveled boxes (`beveledBox`) and
extruded wedges (`voussoir`) — each nudged by a tiny random **jitter** so nothing is machine‑perfect.
Weathering + irregular stacking is 90% of "this looks real".

### ② One mesh (merge)
Every piece is baked to world space and **merged into a single `BufferGeometry`**. The whole prop is
**one draw call**, regardless of how many blocks — great for game budgets.

### ③ Per‑piece vertex attributes (the enabler)
Merging normally destroys the ability to shade each block individually. StoneForge fixes that:
**before** baking each piece it stamps constant vertex attributes onto it —

| attribute | meaning | used for |
|---|---|---|
| `aLocalPos` | vertex position in the block's *own* space | which face am I on, where on the face |
| `aLocalNormal` | the block's own normal | face selection |
| `aHalf` | block half‑size (x,y,z) | normalize local coords, edge detection |
| `aSeed` | random per‑block number | unique noise so no two blocks match |

Because the block's *local* frame travels with it inside the merged mesh, one shared shader can still
do **per‑block edge wear**, **face‑relative effects**, and **per‑block colour variation** — with a
single draw call. This is the linchpin that makes ① + ② + ④ coexist.

### ④ UV‑free procedural PBR
A `MeshStandardMaterial` customized via `onBeforeCompile`:
- **3D world‑space simplex/fbm noise** → base colour, veins, stains, speckle. Because it's 3D noise
  sampled at the world position, it is **seamless on every face with no UV unwrap and no texture files**.
- **Edge wear** from `aLocalPos` vs `aHalf` — corners/edges lighten (chipped, exposed stone).
- **Derivative bump** (Mikkelsen `dFdx/dFdy` of a noise height field) → surface relief with **no normal map**.
- **Overgrowth layer** (optional) — moss/snow/rust/sand painted on up‑facing areas + dripped down
  edges, with fake volume via shading (bright grain tips, dark valleys) + a fuzzy bump. **0 triangles.**

It stays a standard PBR material, so shadows, environment lighting and tone mapping "just work".

Add **GTAO** (screen‑space AO) in post and the mortar gaps get deep contact shadowing — that's what
makes stacked blocks read as separate heavy stones.

---

## 3. API reference

### `new StoneKit({ extras })`
Accumulates tagged pieces, then merges.
- `extras` *(optional)* — declare custom per‑piece attributes as `{name:itemSize}`, e.g. `{aWet:1}`;
  pass values per piece via `add(geo,{attrs:{aWet:1}})`. Read them in a custom shader.

**`kit.add(geo, { position, quaternion, scale, size, seed, attrs })`** → `kit` (chainable)
Tags a **centered** local geometry and bakes it into world space. `size` (full size `Vector3`) is used
for edge‑wear normalization; defaults to the bounding box. `geo` is consumed (mutated).

**`kit.build()`** → `THREE.BufferGeometry` — the single merged geometry. Resets the kit.

### Primitive builders (return centered geometries)
- **`beveledBox(w,h,d,{bevel=0.04, seg=2})`** — the workhorse (drums, bricks, steps, tiles, rough rocks).
- **`voussoir(innerW,outerW,thick,depth,{bevel=0.04})`** — a beveled trapezoidal wedge (arch stone / taper).

### Layout helpers
- **`addStack(kit,{base,w,h,d,count,gap,jitter,taper})`** — vertical stack (columns, wall courses).
- **`addArch(kit,{count,innerR,thick,depth,center,bevel,jitter})`** — semicircular voussoir arch.
- **`addBrickWall(kit,{origin,cols,rows,w,h,d,gap,jitter})`** — running‑bond brick wall (XY plane).

### `makeStoneMaterial(opts)` → `{ material, uniforms }`
| option | default | meaning |
|---|--:|---|
| `colorA` | `0x2a2a2c` | darker base stone |
| `colorB` | `0x857f74` | lighter base stone |
| `roughness` | `0.9` | base roughness |
| `noiseScale` | `0.55` | colour‑blotch frequency (bigger = smaller blotches) |
| `edgeWear` | `0.6` | 0..1 chipped‑light edges |
| `bump` | `0.03` | surface relief (world units) |
| `damp` | `0.3` | darken toward `y=0` (wet base) |
| `warm` | `true` | warm mineral veins |
| `envMapIntensity` | `0.75` | reflection strength |
| `overgrowth` | `null` | an `OVERGROWTH` preset or custom object (below), or `null` for bare stone |

`uniforms` is returned so you can live‑tweak (`uniforms.uEdgeWear.value = 0.9`) or animate.

### `OVERGROWTH` presets
`moss`, `snow`, `rust`, `sand`, `lichen`. Each is `{dark,mid,lite,up,cover,drip,scale,amount,fuzz}`:
- `dark/mid/lite` — three colours (valleys → tips).
- `up` — how up‑facing a surface must be before it grows (0 = anywhere, 0.5 = only near‑horizontal).
- `cover` — overall coverage, `drip` — how far it streaks down edges, `scale` — patch size,
  `amount` — master opacity, `fuzz` — micro‑grain frequency (the fake‑volume texture).

### `quickStudio(renderer, scene)` → `{key,fill,hemi,amb}`
Convenience 3‑point soft lighting + shadow + tone‑mapping setup for fast previews. Optional.

---

## 4. Worked examples

**Weathered column (pillar)**
```js
const kit = new StoneKit();
kit.add(beveledBox(1.15,0.26,1.15), { position:new THREE.Vector3(0,0.13,0) });   // base
addStack(kit,{ base:new THREE.Vector3(0,0.44,0), w:0.74,h:0.44,d:0.74, count:5, taper:0.12 });
kit.add(beveledBox(1.18,0.22,1.18), { position:new THREE.Vector3(0,2.9,0) });    // capital
const { material } = makeStoneMaterial({ colorA:0x33322f, colorB:0x8b8579, overgrowth:OVERGROWTH.moss });
scene.add(new THREE.Mesh(kit.build(), material));
```

**Snowy brick wall**
```js
const kit = new StoneKit();
addBrickWall(kit,{ origin:new THREE.Vector3(0,0,0), cols:9, rows:11, w:0.42,h:0.24,d:0.32, gap:0.02 });
const { material } = makeStoneMaterial({ colorA:0x4a3a30, colorB:0xa08a70, overgrowth:OVERGROWTH.snow });
scene.add(new THREE.Mesh(kit.build(), material));
```

**Voussoir arch on piers**
```js
const kit = new StoneKit();
addStack(kit,{ base:new THREE.Vector3(-1.02,0,0), w:0.7,h:0.5,d:0.8, count:4 });
addStack(kit,{ base:new THREE.Vector3( 1.02,0,0), w:0.7,h:0.5,d:0.8, count:4 });
addArch(kit,{ count:11, innerR:0.72, thick:0.6, depth:0.8, center:new THREE.Vector3(0,2.0,0) });
const { material } = makeStoneMaterial({ colorA:0x323234, colorB:0x878075, overgrowth:OVERGROWTH.moss });
scene.add(new THREE.Mesh(kit.build(), material));
```
(All three, plus a lichen boulder, are in `stoneforge-demo.html`.)

---

## 5. Tuning cheat‑sheet
- **Too "CG‑clean"** → raise `edgeWear`, lower `noiseScale` (bigger blotches), add jitter.
- **Too noisy/busy** → raise `noiseScale`, lower `bump`.
- **Moss looks flat/painted** → raise the preset's `fuzz` and `drip`; add GTAO in post.
- **Overgrowth everywhere / nowhere** → tune preset `up` (higher = only tops) and `cover`.
- **Wrong material feel** → this is diffuse‑dominant stone; for metal raise `metalness` on the returned
  `material` and drop `roughness`, use the `rust` overgrowth.
- **Blocks look glued** (no gaps) → add GTAO, or increase `gap` in the layout helpers.

---

## 6. Performance & LOD
- **Draw calls:** one per merged asset (plus ground/lights). Merge related props together for even fewer.
- **Triangles:** you control it via primitive `seg`/`bevel` and block count. The mossy archway is ~25k
  tris as *pure* stone (moss = 0 tris). Bricks/tiles at `seg:1` are cheapest.
- **Shader cost:** the material is fbm‑heavy (many noise octaves) — it's fill‑rate bound, not vertex
  bound. For lots of on‑screen instances: bake the material to textures once (render to a texture atlas)
  or reduce octaves. For a few hero props it's fine as‑is.
- **LOD:** because it's one mesh you can generate a low `seg`/low‑block version for distance, or bake
  the near version to a normal+albedo texture and swap. The overgrowth being shader‑side means LODs
  stay consistent.
- **Instancing:** the same merged geo + material works in an `InstancedMesh` for repeated props (walls,
  gravestones) — `aSeed` is baked per‑vertex so all instances share it; vary per‑instance look with a
  small per‑instance colour or a custom instanced attribute.

---

## 7. Gotchas & limits
- **Pieces must be centered** local geometries and (for merging) **indexed** — three's built‑ins are.
- **Non‑uniform `scale`** in `add()` will skew world normals slightly; prefer building at final size.
- **Edge wear needs `aHalf`** — it only triggers on pieces added through a `StoneKit` (guarded, so it
  no‑ops safely on untagged geometry).
- **Overgrowth `up`** is world‑space: it grows on world‑up faces. If you rotate the whole asset after
  building, the moss rotates with it (grows on the *new* top) — usually what you want.
- **This is hard‑surface / mineral.** It is not a cloth, skin, foliage, or fluid material.

---

## 8. Where it works well

The sweet spot: **modular, hard‑surface, weathered, inorganic assets that you want textureless and
cheap.** The `overgrowth` layer ("stuff settles on top and drips down edges") generalizes across all
of them — same math, different colours.

| Asset | Fit | How to use |
|---|:--:|---|
| **Walls, ruins, castles, dungeons, temples** | ★★★★★ | `addBrickWall` / stacked `beveledBox`; moss or snow overgrowth. The flagship use. |
| **Pillars / columns / obelisks** | ★★★★★ | `addStack` with `taper`; base + capital boxes; moss. |
| **Arches, bridges, aqueducts, gates** | ★★★★★ | `addArch` voussoirs on stacked piers. |
| **Cobblestone / paving / floors, stairs** | ★★★★★ | scattered flat `beveledBox` tiles with jitter; moss in the gaps. |
| **Gravestones, statue plinths, wells, fountains** | ★★★★★ | a few boxes; moss/lichen. Cheap and characterful. |
| **Rocks, boulders, cliffs, rubble** | ★★★★☆ | rotated `beveledBox` with big `bevel`; raise `noiseScale`; lichen/moss/snow. 3D noise = no UV seams (rock's usual pain). |
| **Concrete: bunkers, barriers, curbs** | ★★★★☆ | boxes; lower `warm`; `rust` streaks from edges or `sand`. |
| **Metal: girders, hull plates, containers, machinery** | ★★★★☆ | boxes; set `material.metalness↑`, `roughness↓`; **`rust` overgrowth drips down from edges/bolts** — exactly the drip logic. |
| **Wood: beams, planks, crates, fences, docks** | ★★★★☆ | boxes; use a **stretched** noise for grain (see §9); edge wear = worn plank edges; moss/lichen on the shaded side. |
| **Tree trunks, branches, roots, logs, stumps (BARK)** | ★★★☆☆ | build the woody parts from tapered boxes/cylinders; anisotropic noise for bark grain; moss/lichen on the north/up side. **Great for wood — not for leaves.** |
| **Ice / crystal / gems** | ★★★☆☆ | boxes/wedges; `material.transmission`/low roughness; `snow` or none. |
| **Foliage, grass, leaves, canopies** | ✗ | different problem — use alpha cards / instanced blades. (That's the 3D moss we *removed*.) |
| **Organic: skin, muscle, creatures** | ✗ | needs SSS/other shading. Hard bits only (bone, horn, shell, carapace, armor plate) can adapt the noise. |
| **Cloth, banners, ropes, fluids** | ✗ | not a hard‑surface material. |

**Trees, specifically (since you asked):** use StoneForge for the **bark and wood** — trunk, boughs,
exposed roots, stumps, fallen logs — and it looks excellent (bark grain via stretched noise, moss on
one side, worn edges at branch collars). The **canopy/leaves are a separate technique** (instanced
cards/points), so a full tree = StoneForge wood + a foliage system on top.

**The overgrowth trick alone** is worth reusing anywhere something ages outdoors: **snow** on roofs,
ledges, rocks; **rust** on any metal (streaks down from rivets/edges); **moss/lichen** on stone, wood,
bark, statues; **sand** drifting into desert ruins; **verdigris** on copper (custom green preset);
**algae** at a waterline (set `up` low, mask by height).

---

## 9. Extending it

**Custom overgrowth** — just pass your own object:
```js
makeStoneMaterial({ overgrowth:{ dark:0x0a3b2e, mid:0x1f7a4d, lite:0x54c98a,   // verdigris
                                 up:0.15, cover:0.9, drip:1.2, scale:1.2, amount:0.9, fuzz:16 }});
```

**Anisotropic wood grain** — in a custom material, sample noise on a squashed axis so the grain runs
along the plank: `fbm(wpos * vec3(2.0, 0.35, 12.0))` (long along Z, tight across).

**Engraving (carved reliefs)** — deliberately *not* in the core module (it's asset‑specific), but the
recipe is: put a height‑field pattern (rings/glyphs) into the bump `H`, darken the albedo where the
groove is deep (AO), and settle a little grime/overgrowth in it. Same derivative‑bump path — the
grooves then read as genuinely carved *into* the stone, not stuck on. Ask if you want it folded back in
as an optional `engrave:{...}` layer.

**Per‑instance variation** — declare an extra attribute (`new StoneKit({extras:{aTint:3}})`), stamp a
colour per piece, and `mix()` it into `albedo` in a custom fork of the shader.

---

*StoneForge = parametric block‑kit → merged to one mesh → per‑block vertex‑attribute variation →
UV‑free 3D‑noise PBR + edge wear + derivative bump → optional shader overgrowth → GTAO.*

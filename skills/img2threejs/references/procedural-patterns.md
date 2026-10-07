# Procedural Three.js Object Patterns

Use this reference only when implementing a model.

## Geometry Choices

- box: flat machinery, furniture, panels, blockout masses
- sphere/ellipsoid: fruit, knobs, organic joints, rounded stones
- cylinder/cone/capsule: trunks, pipes, limbs, handles, bottles, rockets
- torus: rings, tires, loops, trim, cable coils
- shape extrude: logos, flat ornamental plates, blades, keys, leaves
- lathe: vases, bottles, bowls, lamps, wheels
- tube along curve: cables, roots, branches, straps, hoses
- instanced mesh: screws, rivets, leaves, needles, scales, pebbles, repeated ornaments
- plane cards: thin leaves, feathers, labels, cloth strips, decals

## Material Recipes

- wood: brown base, vertical grain normal, roughness variation, darker creases, lighter worn edges
- stone: mottled albedo, high roughness, bump/normal noise, lichen/dirt patches
- metal: lower roughness, metalness, edge scratches, anisotropic-looking streaks via texture
- plastic: controlled roughness, subtle color variation, bevels to catch highlights
- leaf/plant: alpha cards or thin shape geometry, green hue variation, central vein, translucent-ish bright rim
- water/glass: transparent material only if needed; add environment/reflection cues or it reads as a flat sheet

## Material Layer Fields

For each material, prefer a layered description:

- `baseColor`: dominant sampled color.
- `colorVariation`: palette, mottling pattern, amplitude, regional masks.
- `roughness`: base value, variation amount, map/pattern source.
- `metalness`: base value and local changes.
- `normal`: procedural pattern, strength, scale.
- `bump`: amplitude and scale for small tactile relief.
- `displacement`: only for silhouette-visible or close-up relief.
- `wear`: edge wear, scratches, chips, polish, exposed underlayer.
- `dirt`: amount, cavity bias, color, vertical streaking, contact staining.
- `localOverrides`: named regions where color/roughness/bump differs from the base.

Local overrides should answer: where, what changes, how strong, and which image evidence supports it.

## Local Feature Types

Use `component.localFeatures` for details that matter to recognizability:

- raised ridge
- recessed groove
- seam line
- screw or rivet
- chip or dent
- scratch cluster
- stain or dirt patch
- decal or label area
- hole or socket
- bevel highlight
- fabric stitch
- leaf vein or serrated edge

Each feature should include placement, approximate size, orientation, material effect, geometry effect, and confidence.

## Detail Recipes

Concrete Three.js material/geometry approach per `detailInventory` kind. Cross-reference
`references/detail-inventory.md` for the full taxonomy and the evidence/mapping rule.

- gloss: `MeshPhysicalMaterial` with a low-`roughness` localOverride (0.05-0.2) sized to the
  hotspot region; use `clearcoat`/`clearcoatRoughness` for a lacquer layer over a rougher
  base, `anisotropy`/`anisotropyRotation` for brushed/streaked highlights.
- bevel: real geometry, not a normal map - `edgeTreatment.type = chamfer`, `bevelRadius`
  object-relative (0.02-0.08), `segments` 2-4 for a soft catch-light rim, 1 for a hard edge.
- fastener: `InstancedMesh` for the repeated part; `count` + spacing pattern (linear, radial,
  grid) + head shape (hemisphere/flat/hex) + recess (raised vs countersunk); low-roughness
  metal material on the head crown.
- linework: pick engraved groove (real recessed geometry along a path, catches shadow),
  painted line/decal (canvas-texture localOverride, color contrast only, no relief), or
  panel-line (thin dark AO/roughness localOverride along a seam, no depth) - match whichever
  the reference evidence shows; do not default to decal for something that casts a shadow.
- stain: `material.localOverrides` region with `dirtAmount`, `cavityBias` (concentrate in
  crevices), `streak` (directional, usually gravity-down), `patinaColor` for oxidation hue
  shift, or a `fadedMask` (lighter, desaturated) for sun-bleaching - the inverse of dirt.

## Character Geometry And Material Recipes

Use these when `objectClass.primaryDomain` is `character` or `hybrid`. Pair with
`references/character-reconstruction.md` for proportion/landmark data.

- head: sphere or ellipsoid scaled to the measured head-unit, then displaced/tapered toward
  the reference face shape (jaw width, chin point, cheek fullness) rather than left spherical.
- limbs: capsule or tapered cylinder per segment (upper arm, forearm, thigh, shin); taper
  ratio and length come from `anatomy.proportions`; capsules keep joints visually continuous.
- hands: simplified capsule-cluster (palm block + finger capsules) at low segment count;
  do not attempt per-knuckle detail unless the reference is close-up and complexity is ultra.
- hair: hair cards (alpha-mapped planes layered in clumps) for stylized/low-complexity, or a
  tube-along-curve per lock for wavy/flowing hair with visible strand structure; prefer cards
  by default - hair is the classic single-image failure mode, so favor legible clumps over
  many thin strands that swim or alias.
- face feature placement: position eyes, brows, nose, mouth using `anatomy.faceLandmarks`
  normalized coordinates (eyeLine, eyeSpacing, noseBase, mouthLine, hairline); never eyeball
  placement freehand once landmarks exist.
- eyes: glossy sphere (low roughness, slight clearcoat) plus an iris decal/texture; a correct
  catchlight (small bright localOverride matching the key light) sells more realism than
  extra geometry.
- clothing: extrude or plane panels per garment piece, with fold normals (a normal-map or
  displacement pattern following expected gravity/pose creases) rather than a flat shell;
  reuse Track A detail machinery (seam, stitch, decal, stain) for prints, buttons, wear.
- skin: approximate subsurface scattering, not true SSS - warm base albedo, soft/lower
  roughness variation (skin is not uniformly matte), and a rim or backlight to fake light
  passing through thin tissue (ears, nose edge). Avoid pure-Lambertian flat skin.

## Verification Cues

A procedural object is usually failing when:

- silhouette reads wrong even before material
- every edge is perfectly sharp or perfectly smooth
- material has one flat color and no roughness variation
- lighting hides the form instead of explaining it
- repeated details are too evenly spaced
- close-up details add triangles but not recognizability

## HARD RULE: never jitter raw vertices of unwelded geometry (mesh-tear cracks)

**Failure signature:** bright hairline slashes / diagonal "cut" lines across flat faces, edge
slivers that flash white or show the background through the surface. They look like painted
cracks but are literal open holes in the mesh.

**Root cause:** most Three.js geometries duplicate vertices wherever normals or UVs differ —
`BoxGeometry`/`RoundedBoxGeometry` duplicate at every edge (one copy per face), `ExtrudeGeometry`
is largely non-indexed (copies per triangle). A naive per-vertex jitter loop
(`p.setXYZ(i, x+rnd(), ...)`) moves each COPY independently, so faces that met at an edge tear
apart. `computeVertexNormals()` afterwards adds shading creases on top. Strong key light /
envMap then highlights every torn seam as a white slash.

**Never do:** `for (i) p.setXYZ(i, p.getX(i)+rng(-a,a), ...)` on any geometry you did not weld.

**Do instead (pick one):**
1. **Position-hashed offsets** (cheapest, keeps hard edges): derive the offset from the vertex
   POSITION, not the index, so all copies of the same corner move identically and the mesh stays
   closed: `const h=(x,y,z,s)=>fract(sin(x*127.1+y*311.7+z*74.7+s)*43758.5)-0.5;`
   `dx=h(qx,qy,qz,seed1)*amp` with `q*=Math.round(p/1e-4)` quantized position.
2. **Weld first:** `BufferGeometryUtils.mergeVertices(geo)` → jitter → `computeVertexNormals()`
   (loses hard-edge normals — re-split or accept smooth shading).
3. **Smooth noise field deformation:** displace along a coherent 3D noise of world position
   (same value for co-located copies by construction) — best for organic warps.

**QA gate (mandatory when any vertex deformation is used):** render a grazing-light CLOSE-UP and
explicitly look for bright hairline diagonals or background peeking through seams. Treat them as
mesh tears, NOT as "stylized chisel marks" — that misread is exactly how this ships. A
programmatic check: group vertices by quantized original position and assert all copies received
identical displacement.

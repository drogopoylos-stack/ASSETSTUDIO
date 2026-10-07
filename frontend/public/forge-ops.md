# forge-ops — the modelling toolbox, and which tool to use when

One import, both engines, no build step:

```js
const m = await import('http://127.0.0.1:8777/forge-ops.js');
const ops = m.makeOps(THREE);            // three.js: every op takes and returns a BufferGeometry
const ops = m.makeOpsPc(pc, app.graphicsDevice);   // PlayCanvas: takes and returns a pc.Mesh
```

**Everything here is optional.** Plain three.js code is always a fine way to build an asset; use a
tool when it saves you work, or when you are asked to.

Every op takes a geometry (or, where it says *object*, a whole Object3D / Entity tree, which it merges
first) and returns a new geometry. Nothing is mutated. `check(object)` returns the defects as
numbers — read it after every step; a render shows none of them.

## The recipe

Most game assets go through the same six steps. Do them in this order.

| step | call | what it gives you |
| --- | --- | --- |
| 1 build | your code: primitives, lofts, `blobs([...])`, `hull(points)`, `boolean(a, b, 'subtract')` | the rough form, usually a pile of pieces |
| 2 one surface | `skin(object, { tol, angle })` | merge + weld + smooth by angle. A pile becomes ONE surface. `check` should now say `holes: 0` |
| 3 smooth it | `subsurf(geo, levels, { creaseAngle })` | Catmull-Clark. Set `creaseAngle` (e.g. 40) to keep hard edges hard; leave it 0 for an organic body |
| 4 round the edges | `bevel(geo, { width, segments, angle })` | rounded edges catch light; `angle` picks which edges (default sharper than 30°) |
| 5 unwrap | `unwrap(geo, { angle, margin })` | UVs: islands by facing, packed with a margin, uniform texel density. Sets `uv` and `uv1` |
| 6 bake | `bakeAO(geo, 256)`, `bakeCurvature(geo, 256)`, `bakeNormalMap(low, high, 512)` | maps in those UVs; `texture(baked)` makes an engine texture to put on the material |

Skip steps that do not apply: a stylized low-poly prop wants 1, 2, 5 and maybe 6; a smooth creature wants
1, 2, 3 (no crease), 5, 6; a hard-surface machine wants 1, 2, 3 (creased), 4, 5, 6.

## The high → low chain: what Blender did better, in code

A goblin was built twice from one picture, once in Blender and once in the forge.
Blender won on the face, the materials and the value range, and every one of those came from ONE
chain: sculpt a dense surface, decimate it to a game budget, bake the sculpt and a painted material
onto the low mesh, and give every part one texture set. That chain is here now, as calls:

```js
const m = await import('http://127.0.0.1:8777/forge-ops.js');
const ops = m.makeOps(THREE);
// 1 the mass, one closed surface
let head = ops.blobs([{ p: [0, 0, 0], r: 0.12 }, { p: [0, -0.05, 0.06], r: 0.07 }], { res: 64 });
// 2 vertices for the brushes to move: edges under a third of the smallest brush radius
head = ops.densify(head, 0.004);
// 3 the sculpt, as data, in order
const high = ops.sculpt(head, [
  { kind: 'clay',   path: [[0.03, 0.04, 0.10], [0.07, 0.035, 0.08]], radius: 0.02, depth: 0.006, mirror: 'x' },
  { kind: 'crease', path: [[0.02, 0.00, 0.11], [0.06, -0.01, 0.09]], radius: 0.008, depth: -0.003, taper: [1, 0.2], mirror: 'x' },
  { kind: 'noise',  radius: 0.2, depth: 0.0015, freq: 90 },             // no path: the whole surface
]);
// 4 the game budget: the silhouette stays, the detail moves into the normal map
const low = ops.unwrap(ops.decimate(high, { target: 3000 }), { uv1: false });
// 5 the paint, from curvature, occlusion and noise, with the sculpt baked on
const maps = ops.bakeMaterial(low, ops.materials.skin({ color: '#c8a07a' }), 1024, { high });
const mesh = new THREE.Mesh(low, ops.applyMaps(new THREE.MeshStandardMaterial(), maps));
```

For a character of many parts, give each part its material and let them share ONE texture set:

```js
const { geos, maps } = ops.bakeAtlas([
  { geo: headLow,   mat: ops.materials.skin({ color: '#c8a07a' }), high: headHigh, weight: 3 },
  { geo: helmetLow, mat: 'rustySteel', weight: 2 },
  { geo: beltLow,   mat: 'leather' },
], 2048);
const mat = ops.applyMaps(new THREE.MeshStandardMaterial(), maps);
geos.forEach((g) => root.add(new THREE.Mesh(g, mat)));   // one material, one draw call per part
```

`weight` is a part's share of the texels (the face gets the most). Vertex colours on a part ride
through `atlas` into the geometries it returns.

## Which tool, for what

**Making form**
- `blobs([{p:[x,y,z], r}], { res })` — spheres blended into one organic body (metaballs skinned by
  surface nets, the table-free marching cubes). The fastest way to a creature's mass.
- `hull(points | object)` — the convex wrapping. Collision shapes, crystals, low-poly rocks.
- `boolean(a, b, 'union' | 'subtract' | 'intersect')` — cut a window, join a handle. Both closed.
- `isosurface(field, min, max, res)` — any scalar field (< 0 inside) as a skin.
- `mirror(geo, axis)`, `array(geo, count, offset)`, `deform(geo, 'twist' | 'bend' | 'taper' | 'bulge', amount)`,
  `displace(geo, amp, freq)`, `solidify(geo, thickness)` — the usual modifiers.

**Making it clean**
- `weld(geo, tol)` — vertices on top of each other become one. Needed before anything that walks the surface.
- `skin(object)` — weld + merge + smooth, in one call.
- `remesh(geo, { res })` — one even closed surface from the shape alone; every seam gone. The sculptor's reset after booleans or blobs.
- `relax(geo, { iterations })` — jaggedness out, volume kept, boundary fixed.
- `simplify(geo, cell)` — fewer triangles.
- `flip(geo)` — when `check` says `inverted: true`.
- `smooth(geo, angle)` — shading only: soft below the angle, hard above.

**Making it smooth or sharp**
- `subsurf(geo, levels, { creaseAngle, sharpness })` — Catmull-Clark on recovered quads. Boundaries keep
  their outline. `sharpness` is how many levels a crease holds (default: always).
- `subdivide(geo, levels)` — Loop, on triangles. Rounder; use `subsurf` for quads and creases.
- `bevel(geo, { width, segments, angle })` — closed meshes; width is clamped so no face turns inside out.

**Textures**
- `unwrap(geo, { angle: 66, seamAngle: 66, margin: 0.02 })` — Smart projection. Splits vertices at island borders.
- `bakeAO(geo, size, { rays, distance })` — sky visibility. Onto `material.aoMap` (uses `uv1`).
- `bakeCurvature(geo, size)` — convex bright, concave dark. The mask for edge wear and dirt in your shader.
- `bakeNormalMap(low, high, size, { distance })` — `high` may be `subsurf(low, 2)`: the smooth look at the low-poly price. Onto `material.normalMap`.
- `texture(baked, { srgb })` — a DataTexture (three) or pc.Texture from a bake. `uv(geo, 'box' | 'planar' | 'cylinder', scale)` is the quick projection when no unwrap is needed.

**Rigging**
- `heatWeights(geo, bones, { maxBones: 4 })` — bone-heat skin weights (Blender's automatic weights):
  smooth over the surface, and a limb's skin stays off the other limb. Returns `skinIndex`/`skinWeight` arrays.
- `ik(joints, target)` — FABRIK on a chain of points.

**Judging**
- `check(object)` — `holes` (open edges that survive a weld: a real hole), `inverted`, `volume`, `duplicates`, `nonManifoldEdges`, `degenerate`, and plain-sentence `notes`.
- `sceneReport(root)` — lights, cameras, shadows, names used twice, what sits below the floor, what floats, what is paper-thin.

**Sculpting** — see *Sculpt strokes* below
- `densify(geo, maxEdge)` — split every edge longer than `maxEdge` metres. The surface does not move. Do it before `sculpt`.
- `sculpt(geo, strokes, { angle })` — brush strokes as data: crease, ridge, inflate, pinch, smooth, flatten, noise, move, clay. Same triangles and uv, new positions.

**A game budget**
- `decimate(geo, { target | ratio, boundary, featureAngle, maxError })` — quadric edge collapse. Keeps creases, open borders, UV seams and vertex colours; `userData.error` is the worst move in metres. 255k → 7k in about a second.

**Materials** — see *Smart materials* below
- `bakeMaterial(geo, 'rustySteel' | ops.materials.x({...}) | yourLayers, size, { high })` — base colour, ORM and a normal map in the geometry's own uv.
- `bakeAtlas([{ geo, mat, high?, weight? }], size)` — many parts, ONE texture set; returns `{ geos, maps }`.
- `applyMaps(material, maps)` — onto a three material with the right colour spaces and the normal sign that makes the forge and the exported GLB agree.
- `bake(geo, size, texel => [r, g, b])` — any colour function of the surface (point, normal, curvature, occlusion).
- `atlas(geos, { weights })` — the atlas unwrap on its own.

**Characters** — see *Hands that hold things* below
- `hand({ side, pose, size, fingers, gripRadius, chunk }, res, { skin, nail })` — a whole stylised hand as ONE closed surface.
- `grip(handMesh, { a, b, face })` — the hand ON a handle from `a` to `b`, knuckles toward `face`. Returns `wrist`, where the forearm must end.
- `reach(shoulder, wrist, [upper, fore], pole)` — where the elbow goes so the arm ends exactly on the wrist.

**Tubes and rims**
- `sweep(path, { radius, sides, profile, closed, caps, smooth })` — a tube or any closed profile along a path, frames that never flip: straps, belts, cords, wrapped grips, horns.
- `rim(shellGeo, { radius, inset, lift })` — a rolled tube along every open border of a shell: the rim of a helmet, a pauldron, a shield.
- `borders(geo)` — the open borders as loops of points.

**Modular assets** — a building whose width adds window bays, a fence that gains posts, a GLB kit pack
- `kit(source)`, `facade(kit, opts, pick)`, `repeat(piece, at)`, `bays(length, bay)`, `perimeter(w, d, step)` — see *Modular kits* below.

## Sculpt strokes

A stroke is data, so a sculpt is code you can read, diff and run again:

| kind | what it does | the numbers |
| --- | --- | --- |
| `crease` | a groove under `path`: wrinkles, eyelids, the line between plates | `depth` < 0 (default -0.35 × radius), `profile` 'v' (default), 'u' or 'round' |
| `ridge` | a raised line: brows, veins, a rolled edge | `depth` > 0, `profile` 'u' by default |
| `clay` | builds up to a flat top `depth` above the surface; strokes stack | brow masses, cheek pads, lips |
| `inflate` / `pinch` | swell along the normals / pull toward the path | `strength` 0..1 for pinch |
| `flatten` | toward the plane under the brush (or normal to `dir`) | helmet planes, a nose bridge |
| `smooth` | lumps and grooves about the brush's size out, volume kept | `strength` 0..1 |
| `noise` | coherent 3D noise along the normals; `dir` stretches it into streaks | skin, scruff, dents, hammered metal |
| `move` | shifts the region by `dir` metres, fading out | pull an ear tip, a lip |

Every stroke has `radius` (metres; nothing farther moves) and takes `path` (a polyline, laid onto
the surface first), `at` (one dab), or neither (the whole surface: inflate, noise, smooth, move).
`taper: [1, 0.2]` fades a wrinkle out along its path, `curve: true` follows a smooth curve through
the points, `mirror: 'x'` does the other side too (exact only if the mesh is mirror-symmetric), and
`mask(p, n)` weights it per vertex. Densify first: edges under a third of the smallest radius, or
the groove is jagged. The sculpt is the `high` of a bake; do not ship it.

## Smart materials

`ops.materials` has `steel`, `rustySteel`, `paintedSteel`, `leather`, `wood`, `cloth`, `skin`,
`bone`, `gold` and `stone`. Call one for options: every one takes `color`, `scale`, `seed`, `dirt`
and (not skin) `wear`; `rustySteel` also `rust`, `paintedSteel` `paint` and `metal`, `skin` `flush`
and `cavity`, `stone` `moss`. A name as a string is the preset with no options.

A material of your own is layers over a base, each weighted by a mask:

```js
const rusty = { base: { color: '#8d8b86', roughness: 0.45, metal: 0.7 }, layers: [
  { name: 'rust', color: '#7a3b1c', roughness: 0.9, metal: 0,
    mask: { mul: [{ max: [{ ao: { lo: 0.4, hi: 0.8 } }, { curvature: { lo: -0.1, hi: -0.5 } }] }, { noise: { freq: 40 } }] } },
  { name: 'edge wear', color: '#d8d6d2', roughness: 0.25, mask: { curvature: { lo: 0.15, hi: 0.5 } } },
] };
```

Masks: `noise`, `voronoi` (spots or cracks), `streaks` (runs, grain), `curvature` (edges > 0,
cavities < 0), `ao` (dirt in the occluded places), `up` (dust on top), `region(p, n)`; `mul` needs
all, `max` takes any, `invert` flips. That is Substance's smart-material idea, baked on the CPU.
**The darks are the point:** the forge goblin lost the value range to Blender because nothing put
dark into its cavities. `ao` and `curvature` masks do exactly that.

## Hands that hold things

A hand made of spheres never closes on anything. `hand()` builds a whole hand — palm, three-segment
fingers, thumb, knuckles, nails, a wrist stub — as one smooth closed surface, in a pose (`fist`,
`grip`, `open`, `relaxed`, `point`), for either side, with 3 or 4 fingers. In `grip` the fingers
close round a channel of `gripRadius`; `grip()` then puts that channel ON the handle:

```js
const hand = new THREE.Mesh(ops.hand({ side: 'R', pose: 'grip', size: 0.05, gripRadius: 0.012 }, 56,
  { skin: '#c8a07a', nail: '#d9cfae' }), skinMat);
const p = ops.grip(hand, { a: pommel, b: guard, face: [0, 0, 1] });   // a -> b: the blade leaves on the thumb side
const arm = ops.reach(shoulder, p.wrist, [0.28, 0.26], [0.3, 1.0, -0.4]);   // elbow out and back
```

The hand frame: origin at the wrist, fingers along +Z when open, the back of the hand +Y, the palm
-Y; a right hand's thumb is on +X, a left hand is the mirror image. `grip` answers `wrist` (where the
forearm must end) and `forearm` (the way it must run for a straight wrist).

## One picture to one model: the checklist

What went wrong in the goblin test, each with the tool that catches it:

1. **The camera.** A silhouette is the same from the front and the back; a sweep picked the wrong
   side twice. Give `aim` three or more `anchors` — `{"at": {"part": "head", "where": "top"},
   "px": [200, 40]}`, pixels on the reference — and the camera is SOLVED. `snap` in the answer is
   the move that puts each part on its pixel.
2. **Centred parts stay centred.** A belt, a buckle, a nose, a beard sit on x = 0. In a
   three-quarter picture the buckle LOOKS off-centre; that is the view, not the model. If it misses
   its pixel, the camera is wrong. `snap` moves a centred part up, down, forward or back — sideways
   only back onto x = 0 — and SYMMETRY says when one has drifted.
3. **Twins mirror.** Build one side and `mirror(geo, 0)` it, or build both from one function with
   `side = ±1`. Name them `handL`/`handR` (or `hand_left`, `L_hand`): the detail review, `focus` and
   SYMMETRY find twins by those names.
4. **Hands close on what they hold.** `hand` + `grip` + `reach`, above. GAP says when a held prop
   floats free of the hand.
5. **Holes only where the design has them.** HOLE reports an open border you can see from outside;
   a surface that ends inside another part is not reported.
6. **The darks.** CONTRAST says when the render's darks or lights are much flatter than the
   reference's: bake `ao` and cavity masks into the material (above).
7. **Every side.** Look at `front`, `back`, `left` and `right` before you stop, not only the
   reference's angle. The goblin's back had floating shoulder plates that its front never showed.

## Modular kits: bays are repeated, never stretched

A configurator asset is a KIT plus RULES, not one mesh: a wall bay, a window bay, a shop front, a door,
each made once, and a rule that places them. Widen the building and it gains window bays; the ones it
had keep their size. Scale a mesh instead and every window gets fatter — the one thing a modular
asset must never do. Worked example, every part of this section in one file:
`data/ab/modular_shop.js` (open it: `/engine?edit=<absolute path>`).

**One convention for every piece**, so pieces from different sources snap together: metres, y up,
the piece's FRONT faces +z, and it is authored on the kit's grid (5 cm here). A wall runs along its
own +x; bay 0 is on the left of someone standing outside looking at it. A wall at yaw 0 faces +z,
at yaw `Math.PI / 2` faces +x. Name every piece — the kit finds them by name.

**1. The kit.** `ops.kit(source, { anchor, grid })` takes a GLB's scene (its named children are the
pieces; `match: /^wall_/` searches deeper) or a record of objects built in code, and returns
`{ names, pieces, get(name), has(name), notes }`. Each piece is measured (`size`, `min`, `max`,
`triangles`) and its pivot moved to ONE anchor, because a pack's pivots are wherever its modeller
left them and a facade built from them has every other window floating:

| anchor | pivot | for |
| --- | --- | --- |
| `bottom` | foot centred on y = 0 | props, posts, furniture |
| `back` | back face on z = 0, foot on y = 0 | doors, shop fronts |
| `wall` | back face on z = 0, height KEPT | windows: the sill stays 0.95 m up |
| `corner` / `centre` / `origin` | min corner / box centre / untouched | floor tiles / — / pivots set by hand |

Per piece: `anchors: { door: 'back' }`; per axis: `[0.5, null, 0]` (null keeps that axis). With
`grid`, `kit.notes` names any piece whose width is not a whole number of cells ("window is 1.190
wide: -0.010 off the 0.2 grid, so a row of them drifts that much per piece") — empty is the goal.
Round, free-form props go in a second kit without a grid. `kit.get('walll')` throws and lists the
names it has.

**2. The rule.** `ops.bays(length, bay, { margin, gap, min, max, spread, origin })` is the whole
number of bays that fit — n bays, n − 1 minimum gaps and two margins — with the leftover SHARED
OUT as space. 5.8 m of 1.4 m bays with 0.35 m margins is 3; 9.8 m is 6. It returns `count`,
`centres`, `gap`, `pitch`, `end`, `leftover`, `fits`, and `spaces` (the stretches that are not a
bay). The count carries a hair of slack: `4.8 / 1.6` is 2.9999999999999996, and a bare floor loses
a stall on exactly the lengths people type. `spread`:

| spread | leftover goes | use |
| --- | --- | --- |
| `even` (default) | equal gaps at both ends and between | windows |
| `around` | each bay centred in an equal slot | columns on a grid |
| `between` | between the bays, ends flush | posts from corner to corner |
| `centre` | both ends; bays packed at `gap` | a ribbon window, a row of shops |

**3. The facade.** `ops.facade(kit, opts, pick)` runs the rule on every floor of one wall and asks
`pick(slot)` what goes in each (floor, bay). `bay`, `floorHeight` take a function of the floor and
`perFloor(f)` any bay option, because a shop floor of wide bays sits under narrow window bays:

```js
const ops = (await import('http://127.0.0.1:8777/forge-ops.js')).makeOps(THREE);
const kit = ops.kit({ window: makeWindow(), shop: makeShop(), door: makeDoor() }, { anchor: 'wall', grid: 0.05 });
const front = ops.facade(kit, {
  name: 'front', length: width, floors: 3, floorHeight: 3, margin: 0.3,
  bay: (f) => (f === 0 ? 1.6 : 1.4),
  perFloor: (f) => (f === 0 ? { spread: 'centre' } : null),
  at: [0, 0, depth / 2], yaw: 0, instance: 'always',
}, (s) => (s.floor > 0 ? 'window' : s.bay === 0 ? { piece: 'shop', span: 2 } : 'door'));
root.add(front);
front.userData.facade;   // { floors: [{ count, pitch, gap, leftover, fits }], placed, empty, missing }
```

A slot carries `floor, bay, count, first, last, top, x, y, width, height`, its world `position`
and `yaw`. `pick` answers a piece name, `null` for an empty bay, or `{ piece, span, flip, offset,
yaw, scale }`: `span: 2` centres a double shop front on two bays and skips asking about the second;
`flip` mirrors (a door hinged the other way); `offset` is in the wall's frame — `[0, 0, 1.2]` stands a
street display out in front, and a second facade call over the same slots is how a shop gets its
crates. A name the kit lacks lands in `missing`, never in a throw. `fill: 'plain_wall'` stretches
one featureless piece over every stretch no piece covers — the ONLY thing ever scaled.
`m.facadeSlots(opts)` and `m.facadePlan(opts, pick, widthOf)` are the same thing as plain data.

**4. Round a rectangle.** `ops.perimeter(width, depth, spacing, { corner, inset, at })` gives
`edges` (every side its own bay layout, every slot facing OUTWARD) and four `corners` (corner k ends
side k: front, right, back, left). Dentils under a cornice, balusters, parapet merlons, corner posts:
`ops.repeat(kit.get('dentil'), ring.edges)`.

**5. Copies.** `ops.repeat(piece, placements, { instance, minInstances, name })` draws a single-mesh
piece as ONE InstancedMesh (a hundred dentils, one draw) and clones anything else; clones share
geometry and materials. `instance: 'always'` instances each mesh of a multi-part piece too — one draw
per part, not per window. A placement is a slot, `{ position, yaw, rotation, scale, flip }`, a bare
`[x, y, z]` or a 4x4; mirrored copies get a draw of their own with the winding right. Bounding
boxes are computed over every copy, so culling and framing see them all. PlayCanvas (`makeOpsPc`)
takes the same calls and clones.

**6. The manifest the Edit tab reads.** Parameters grouped, a choice, switches, colours, advanced
settings, `night` (the DAY/NIGHT switch sets it), presets with tag chips, arrows you drag:

```js
export const manifest = {
  params: {
    type:   { value: 'shop', options: ['shop', 'house', 'service'], label: 'Building type', group: 'building' },
    width:  { value: 5.8, min: 3.6, max: 12, step: 0.1, label: 'Width', group: 'building' },
    floors: { value: 3, min: 1, max: 5, step: 1, label: 'Floors', group: 'building' },
    bay:    { value: 1.4, min: 1.25, max: 2.2, step: 0.05, label: 'Window bay', group: 'facade', advanced: true },
    night:  { value: false, label: 'Night' },
  },
  presets: [{ name: 'Twin market', tags: ['shop'], values: { width: 9.8 }, note: 'two shop fronts' }],
  handles: [
    { param: 'width', axis: 'x', side: 'max', scale: 2 },              // centred on x: the face moves half
    { param: 'floors', axis: 'y', side: 'max', scale: 1 / 3, snap: 1 }, // 3 m a storey, whole floors
  ],
};
export default function build(THREE, p = {}) { /* kit, facades, return a Group */ }
```

**What bit, so it does not bite again**
- A pane 1.5 cm in front of its wall z-fights once a camera's near plane is small: the forge's is
  1 mm, which leaves ~5 cm of depth precision at 30 m. Give layered surfaces (panes, curtains, sign
  faces) `polygonOffset` (factor and units −1, −2 for the layer above), and keep anything meant to
  show in front of one ≥ 10 cm clear — one offset unit is itself ~5 cm out there.
- A tab keeps the first copy of a module it ever imported. An asset that imports forge-ops.js
  statically should use a query it owns (`forge-ops.js?kit=1`), and a forge run should import the
  asset with a fresh `?v=<now>`.
- Prove the rule with numbers, then pictures: `userData.facade` counts at two widths (bays went up,
  nothing scaled), `kit.notes` empty, triangles and draws in budget; then render narrow, wide and one
  other type and say whether each reads like the reference.

## Ground: the terrain engine

`ops.terrain` is a heightfield with brushes, four paintable layers and scatter. Plain arrays, no
engine, so the same code runs in the editor's viewport, in a node test and inside a forge call.

```js
const T = ops.terrain;
const t = T.makeTerrain({ size: 512, res: 257, maxHeight: 64, seed: 1 });
T.applyBrush(t, { kind: 'raise', radius: 90, strength: 0.6, falloff: 1 }, 256, 256, 1);
T.applyBrush(t, { kind: 'erode', radius: 90, strength: 1, falloff: 0.5 }, 256, 256, 2);
const m = T.terrainMesh(t);   // positions, normals, uvs, RGBA vertex colour, Uint32 indices
T.report(t);                  // slope bands, layer coverage, walkable share, triangles
```

Nine brushes, all `applyBrush(t, brush, x, z, dt)`. **dt is SECONDS**, so a held stroke is
frame-rate independent and one call with `dt: 1` means a whole second of that brush.

| brush | what it does |
| --- | --- |
| `raise` / `lower` | strength x maxHeight per second, clamped to the field's range |
| `smooth` | toward the 3x3 average; sub-steps at 1/60 internally, so the frame rate cannot change a stroke |
| `flatten` | to `target` in world units, or to the ground under the cursor when there is none |
| `noise` | fbm keyed to WORLD position, so going over the same ground twice deepens the same bumps instead of averaging them away |
| `erode` | droplet hydraulic erosion — the one Unity, Godot and Blender do not have — plus a talus pass, so ground steeper than `talus` degrees (default 42) slides. Water alone carves the gullies between columns and never knocks a column down; 319,000 droplets over a 200-unit island came out as a field of two-cell needles until the slide went in. ~1 droplet per m² per second: a 32-unit brush at full strength is 3,217 droplets in 19 ms, 0.4 ms at the dt an editor passes. Mass is conserved exactly, so holding it does not sink the field |
| `paint` | adds to `layer` and takes from the other three in proportion. The four bytes sum to 255 at every sample, always |
| `scatter` | `density` items per m² per second, refused above `maxSlope` degrees or outside `minHeight`..`maxHeight`, never closer than `spacing`, seeded off the terrain seed so the same stroke twice gives the same trees. Each item records the limit it was planted under, and `report(t, maxSlope)` warns when that is not the limit you asked it about |
| `erase` | removes scatter under the brush; with `asset` set, only that kind. O(items in range), not O(items on the field) |
| `river` | cuts a channel along the drainage the erosion already found. Needs a flow map: with none it changes nothing and says so in `stats.note`, because a silent no-op is the worst answer an agent with no picture can get |

Every call returns a `Patch` — the before-image of the sample rectangle it touched. `undoPatch(t,
patch)` puts it back bit for bit, trees included. `patch.stats` carries the milliseconds, the
samples written and the droplet count, which is how an agent with no picture knows a stroke landed.

`terrainMesh(t, { lod, region })` rebuilds only the rectangle a brush touched (0.6 ms against 13 ms
for a whole 513² field) and its normals and UVs come from the whole field, so a patch and its
neighbour meet with no seam. Vertex colours come out ALREADY BLENDED — the layer colours mixed by
the splat — so a material with nothing but `vertexColors: true` already shows the ground. Do not
multiply by a layer colour again: a caller who did got a pure white first render. `mesh.weights`
has the raw 0..1 weights for a shader that samples four textures.

### The flow map — where the water ran

Every `erode` stroke records its drainage on `t.flow`, accumulating across strokes. Read it with
`flowField(t)`, which is null until something has been eroded.

```js
const f = T.flowField(t);        // { flow, cut, droplets, strokes, peak, rev, box } or null
T.flowAt(t, x, z);               // the 0..1 share at one world position
T.flowLayers(t, { channel: 0.3 })  // Int8Array: which layer each sample wants, -1 = leave alone
T.applyBrush(t, { kind: 'paint', radius: 90, strength: 1, falloff: 0.5, flow: true }, x, z, 1);
T.applyBrush(t, { kind: 'river', radius: 90, strength: 1, falloff: 0.4, depth: 5 }, x, z, 1);
```

**`flow` is STREAM POWER, not a water-volume count** — discharge times the height the water fell
through, summed over the downhill steps. That is not pedantry, it is the whole feature: a volume
tally peaks where the droplets *stop*, which is the low flats where the sediment settles. Measured
on four eroded fields, the samples above twice the mean volume averaged `cut = -5.3` (ground laid
DOWN) against `+1.3` for the quietest — so `flowLayers`, `paint {flow:true}` and `river` were all
aimed at the silt. Weighted by the drop, the same measurement is `+2.8` against `+0.03`.

**`cut` is the negative of the height change**, in world units: a sample that fell by 2 has
`cut = +2`. Checked sample by sample against the field before and after, 4,629 of 4,629 agreeing.

**`channel` is a rank, not a level.** It picks the wettest `0.10 * 0.1^channel` of the ground the
water crossed *in view*: 0 is the wettest tenth, the default 0.30 the wettest 5%, 1 the wettest 1%.
Lower carves more, higher keeps to the main stems. It has to be a rank because stream power is
heavy-tailed — "0.30 of the peak" selected between 0.4% and 4.6% of four different fields.

`flowLayers` uses **both** signals, and needs both: `channel` says what is a channel, `cut` says
what a channel is made of. Rock where the water stripped the ground, silt where it dropped its
load, the dry layer between, `-1` where no water ever ran. A slope threshold cannot do this — 93%
of what the drainage calls channel stands on ground a 30-degree slope test calls flat.

The flow map survives `serialize`/`deserialize`: 16-bit, cropped to the rectangle that has ever
received water, so a brush-sized erode costs a few KB (58 KB of a 232 KB save on a 129² field) and
only a whole-field erode costs real money. `serialize(t, { flow: false })` leaves it out. It costs
two Float32Arrays — 2 MB on a 513², 8 MB on a 1025² — and `erode` with `flow: false` declines to
pay. Undo restores it exactly, which is what lets `plan` roll a stroke back without leaving
channels behind on ground it no longer cut.

### Chunks — so a frustum has something to reject

```js
const grid = T.chunkGrid(t, 32);                       // ChunkRef[], no meshes built
const lod  = T.pickLod(grid[0], eyeXYZ, { near: 3 });  // 1, 2, 4, 8
const c    = T.terrainChunk(t, cx, cz, { chunk: 32, lod });   // .mesh, .lod, .skirt, .centre, .radius
T.chunksFor(t, patch.x0, patch.z0, patch.w, patch.h, 32);     // what a stroke dirtied
```

A 1025² field is 2.1 M triangles in one draw call no frustum can reject. Cut into 32-sample
chunks, a 70-degree cone from the middle of a 257² field rejects 72% of them; at 16 samples, 79%.
Each chunk's bounding sphere takes its Y from the real heights in its own region, which makes it
28% smaller than one built from the field's overall range at 16 samples (9% at 32, 2% at 64 —
below that the horizontal diagonal swamps Y and it stops mattering).

Regions are **inclusive of the shared row and column**, so two neighbours evaluate the same height
on the seam: measured, every one of 65 seam vertices matches to the last bit. Normals still come
from the whole field, so a chunk's normals are identical to the whole-field mesh's, rim included.

`chunksFor` grows the rectangle by one sample before testing, because a height changed at sample
63 moves the normal at 64, which belongs to the next chunk along.

**The skirt** hangs a rim of geometry down from the chunk's edge so a lod boundary shows a wall
instead of the sky. Its default is the measured deviation of the real ground from this chunk's own
coarse edges, floored at one cell: on a 38-unit step terrain that is 28.8 units at lod 4 where one
cell is 8, and a one-cell skirt would leave the join open. Rendered bare against skirted from one
camera, 2,824 pixels of sky closed. It costs triangles — 21,120 against 15,872 for 64 mixed-lod
chunks — so pass `skirt: 0` for chunks whose neighbours are all at the same level.

### Scatter at forest size

```js
const batches = T.scatterBatches(t);   // one per asset: { asset, count, matrices, index }
T.scatterNear(t, x, z, 20);            // indices into t.scatter, O(items in range)
```

`matrices` is `count * 16` column-major 4x4, the layout `InstancedMesh.setMatrixAt` and
PlayCanvas's instancing buffer both take, composed the same way `emitBuilder` poses a cloned node
so switching from clones to instances does not move a tree. `scatterNear` is a uniform grid kept
beside the terrain and rebuilt only when the list moves — a pure append extends it instead. On
6,581 items a query is 0.002 ms against 0.016 ms for the scan it replaces, and it answers exactly
what that scan does over 60 random queries.

### plan — search strokes until the report matches

```js
const r = T.plan(t, { walkable: 0.85, relief: [20, 30], coverage: { rock: 0.35 },
                      budget: 80, ms: 8000, seed: 5 });
// r.steps, r.patches, r.before, r.after, r.tried, r.ms, r.met, r.notes
```

`report()` is already a fitness function, so a goal can be searched. Every candidate is applied,
scored, and kept only if the distance to the goal fell; a rejected one is undone with the patch it
returned and leaves the field **bit-identical**, drainage included — checksummed in the suite.
Measured: relief 0 to 25 units in 1 stroke, `rock` coverage to 0.33 in 3, walkable 0.566 to 0.830
in 63 kept out of 114 tried and 260 ms. `met` is false when it stops short and `notes` always says
what it could not reach and why — including "that band is outside the field's own range", which is
the answer you want when you asked for 500 units of relief on a 64-unit field.

**Getting it out of the Studio.** `emitBuilder(t, 'buildIsland', 'three' | 'playcanvas', { lod })`
writes a standalone module: no imports, 16-bit heights, run-length-coded splat, the scatter as a
list. It exports `buildIsland(THREE)` / `buildIsland(pc, app)`, plus `heightAt(x, z)` and
`normalAt(x, z)` so the game can stand a character on the ground — as named exports AND hung off
the builder itself (`buildIsland.heightAt(x, z)`), so one import is enough. Pass `{ place: (item) => node }`
and it parents your asset at every scattered point. That is how ground ships here — as code the
game calls, not a binary only the editor can read.

The emitted module carries the **four-layer splat material** and uses it by default, on both
engines: `buildIsland(THREE)` and `buildIsland(pc, app)` draw the four layers blended per pixel,
each tiled in world space, each able to take a texture. It patches one chunk of the engine's own
shader, so the game's lights, shadows, fog and tone mapping are untouched. Layer texture PATHS
travel too (`buildIsland.layers[i].texture`) — the module loads nothing, so load them yourself and
pass them back as `{ textures: [grass, dirt, rock, sand] }`.

One thing to know before you read the geometry: on the splat path the **colour attribute carries
the raw 0..1 weights, not the blended colour**, because the shader does the blend and needs the
four weights to do it. Hand those to a plain `vertexColors: true` material and layer 0 paints red.
`{ splat: false }` swaps the attribute back to the blend and hands you a stock material, and so
does bringing your own `{ material }`. `userData.terrain.weighted` says which one is in there, and
`buildIslandWeights(lod)` is the weights on their own — one implementation, out of `arrays()`, so
the vertex order cannot drift between the two.

## Saving edits made in the Studio's editor

The Edit tab writes a sidecar beside the asset (`<asset>.edits.json`, or `studio.edits.json` at a
game's root). Apply it after you build, and the moves, hides, colours, lights, world and modifier
stack made by hand come back:

```js
const root = build(THREE);
m.applyEdits(root, await (await fetch('./scene.edits.json')).json(), ops, THREE);   // three.js
m.pcApply(entity, edits, m.makeOpsPc(pc, device), pc);                              // PlayCanvas
```

Keys are object NAMES (`stableKeys`), so name every part you might want to grab later.

## Vertices moved by hand, and why they survive a rebuild

The sidecar's `verts` array holds corners the Edit tab moved directly. This is the one thing
Blender has no equivalent for, and the reason is worth understanding before you write any.

Blender's mesh is a stored document, so vertex 4127 is vertex 4127 forever. Ours is a program's
output: the code runs again on every parameter change and the buffer is rebuilt from nothing, so
an index names nothing across that boundary. An entry therefore carries **two** keys:

```json
{ "mesh": "hull", "at": [0.41, 1.2, -0.03], "to": [0.41, 1.55, -0.03], "g": 47, "sig": "92:180" }
```

- `at` — where the CODE put the vertex. The first key.
- `to` — where the hand put it.
- `g` — which corner it was, in emission order. The second key.
- `sig` — corners:faces when the edit was made, which says when `g` may be believed.

```js
const root = build(THREE);
m.applyEdits(root, edits, ops, THREE);       // runs applyVerts for you, before the stack
m.applyVerts(root, edits.verts, report);     // or on its own
```

**Why two.** One key is not enough, and a real rock proved it. A parameter you changed *elsewhere*
leaves this vertex bit-identical, so the place matches and the saved absolute position is restored.
But a parameter that re-displaces this very vertex — noise amplitude, radius, a warp — moves it far
past any sane tolerance while leaving the mesh the same shape. Widening the tolerance to catch that
would start binding to the wrong corner. So instead: if `sig` still matches, the builder ran the
same path and emitted the same corners in the same order, `g` is trustworthy, and the hand's
OFFSET is carried onto wherever the code has now put that corner. The peak you pulled up stays
pulled up. If `sig` does not match, the mesh is a different mesh and the edit is orphaned.

Four rules, each of which was a bug before it was a rule:

- **Before the modifier stack, never after.** Hand-moved vertices are the base mesh and the stack
  evaluates over them, which is Blender's order too. Reversed, a `subsurf` silently discards them.
- **A corner is not a vertex.** Generated geometry duplicates: a box is 24 vertices for 8 corners,
  a sphere seam is a whole column of doubles. Everything within `VERT_TOL` (1e-3) of a place moves
  together, or you tear a hole. `groupVerts` does this; do not hand-roll it.
- **Both keys read the REST buffer**, the geometry as the code built it — `restBuffer(mesh)` caches
  it on the object. Read the live buffer instead and applying the document twice adds the offset
  twice, so a peak climbs further on every rebuild.
- **An orphan is reported, never guessed.** No place, no trustworthy ordinal, and the edit lands in
  `report.errors` naming the position it could not find. Snapping to "the nearest vertex" would
  deform an innocent other part of the model, which is the failure nobody would forgive.

`vertKey`, `groupVerts`, `bindVerts`, `topoSig` and `restBuffer` are all exported if you want to
write moves from code. Only moved corners are stored, so 40,000 vertices with three dragged corners
is three lines of JSON.

## Limits worth knowing

- `boolean` is a BSP: correct, not pretty at the cuts — follow with `weld` and `smooth`, and keep inputs under a few thousand faces each.
- `bevel` and `subsurf` recover quads from triangle pairs first; on a mesh that was never quads they still work, less regularly.
- The isosurface is surface nets, not the marching-cubes table algorithm: same job, smoother output, no tables.
- Bakes run on the CPU: 256² AO with 24 rays takes about a second; 512² about four.
- `sculpt` on 200k triangles with 20 strokes takes about 0.3 s; `densify` 20k → 200k about 0.1 s; `decimate` 255k → 7k about 1 s. `densify` refuses past 20 million triangles.
- `bakeMaterial` at 1024² with its high mesh (91k triangles onto 3k) takes about 3 s; `bakeAtlas` of three parts at 2048², about 7 s. Bake once, at the end.
- `sculpt`, `densify` and `decimate` drop nothing silently: `decimate` carries uv and vertex colours, `sculpt` keeps both, `densify` keeps neither (densify before you unwrap or paint).

## Placing things a game's code did not make

An engine editor has three operations a viewer does not: add, duplicate, remove. In the Studio's
Edit tab they are `Shift+A`, `Shift+D` and `X`, and everything they do is kept in the same sidecar
that already holds "move this part, hide that one".

The reason it is a sidecar and not the scene: a procedural game has no scene file. The world is a
function, it runs again on the next reload, and anything added to the scene is gone. So a
placement is a RECIPE, applied after the code has finished building:

```json
{ "placed": [
  { "id": "p1", "name": "block", "ref": { "kind": "primitive", "shape": "box" },
    "pos": [3, 0, -2], "rot": [0, 0, 0], "scale": [2, 1, 1] },
  { "id": "p2", "name": "another rock", "ref": { "kind": "clone", "of": "world/rock" },
    "pos": [-4, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1] },
  { "id": "p3", "name": "tree", "ref": { "kind": "code", "file": "src/props.js", "export": "buildTree" },
    "pos": [1, 0, 1], "rot": [0, 0, 0], "scale": [1, 1, 1] }
] }
```

Four kinds of reference: `primitive` (box, sphere, cylinder, cone, plane, torus, pointLight,
spotLight, dirLight), `code` (the game's own builder, called by name), `model` and `image` (a file
the game ships), and `clone` — another one of an object the game itself built, which is the only
way to copy something whose recipe lives in the game's code rather than in this document.

**Delete means delete for something the editor placed, and HIDE for something the game built.**
The code will build its own object again next run; a document that claimed to have deleted it
would be lying to you on every reload.

To load the same document in the running game, after its own world is built:

```js
const ops = await import('http://127.0.0.1:8777/forge-ops.js');
const doc = await (await fetch('/studio-edits.json')).json();
await ops.applyPlaced(scene, doc.placed, THREE, async (ref) => {
  const m = await import('/' + ref.file);
  return (m[ref.export] || m.default)();
});
ops.applyEdits(scene, doc);          // the moves, the hides, the world settings
```

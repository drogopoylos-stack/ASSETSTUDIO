---
name: modular-asset-kit
description: "Use when an asset must change with its size or type instead of stretching - a building whose width adds window bays and a second shop, a fence that gains posts, a bridge that gains spans - or when a GLB kit pack must be assembled in code. Builds a kit of named pieces placed by the bay rule, instanced, with a manifest of grouped params, presets and drag handles for the Studio's Edit tab, and proves it with the forge."
disable-model-invocation: true
metadata:
  category: 3d
---

# Modular asset kit

**A kit plus rules, not one mesh.** Widen a modular building and it gains window bays; the bays it
had keep their size. Scale a mesh instead and every window gets fatter — the one thing a modular
asset must never do. This is the pattern behind every "configurator" demo (the 3d-asset.com corner
shop: dragging its width from 5.8 m to 9.8 m adds window bays and widens the shop floor — here,
past 8.6 m, the ground floor splits into two shops).

The Studio ships the whole toolkit in its modelling library, and a worked example that uses every
part of it: `data/ab/modular_shop.js` in the Studio repository (open it in the Edit tab at
`/engine?edit=<absolute path>`). The reference for every call: `curl -s http://127.0.0.1:8777/forge-ops.md`,
section *Modular kits*. The exact signatures: `curl -s "http://127.0.0.1:8777/api/live/api?name=Ops"`.

## When — and when not

Use it when a parameter should ADD or REMOVE parts: bays, storeys, posts, spans, shelves, stalls.
Also for any GLB pack of pieces ("wall_01", "window_02", …) that has to be assembled into objects.

Do not use it for a one-off prop with no size parameter, or for organic forms — a creature does not
gain bays. Use `forge-director` to choose the path first; this skill is the `forge` path for
modular subjects.

## 1. Write down the module before any code

```
module    : <bay width> m bays, <storey height> m storeys, grid <cell> m
pieces    : wall_bay, window_bay, door, shop_front(span 2), corner_post, roof_trim, …
params    : what the user changes (width, depth, floors, type, colours) and which are advanced
threshold : what appears past which size (a second shop past 8.6 m)
budget    : <N> triangles, <M> draw calls at the largest preset
```

## 2. One convention for every piece

Metres, y up, the piece's FRONT faces +z, its back on z = 0, its foot (or its storey's floor) on
y = 0, authored on the grid. A wall runs along its own +x; bay 0 is on the left of someone outside
looking at it. Name every piece and every part of it: the kit, the outliner, `focus` and edits all
find things by name.

Make each piece ONCE. Merge a piece's primitives that share a material into one mesh with
`ops.merge(group)` so a piece is a few meshes, not thirty.

## 3. Register the kit

```js
import * as forgeOps from 'http://127.0.0.1:8777/forge-ops.js?kit=1';  // a query this asset owns
const ops = forgeOps.makeOps(THREE);
const kit = ops.kit({ window_bay, door, shop_2, dentil }, { anchor: 'wall', grid: 0.05 });
// or: ops.kit(gltf.scene, { anchor: 'wall', grid: 0.6 })   — a pack's named root nodes
```

`anchor`: `wall` (back on z = 0, modelled height kept: a window's sill stays up), `back` (doors,
shop fronts: foot on the floor), `bottom` (props), per piece in `anchors`, per axis with null.
**Read `kit.notes`.** It names every piece off the grid, with no mesh, or named twice. Empty is the
goal; put round free-form props in a second kit without a grid.

## 4. Lay it out with the rule

- One wall: `ops.facade(kit, { length, bay, floors, floorHeight, margin, spread, perFloor, at, yaw, name, instance: 'always' }, pick)`.
  `pick(slot)` returns a name, `null`, or `{ piece, span, flip, offset }`. Give each wall a `name`
  so its piece sets are named apart.
- The ground floor decides the pieces: ask `ops.bays(width, stall, { margin })` how many stalls fit
  FIRST, plan shops / doors from that count, then build only the pieces the plan needs.
- A second pass over the same slots with `offset: [0, 0, 1.2]` stands props out in the street.
- Round a rectangle: `ops.perimeter(width, depth, spacing, { corner, at })` — `edges` face outward,
  four `corners`.
- Anything placed by hand: `ops.repeat(kit.get('pipe_segment'), placements)`.
- The leftover between whole bays is SPACE, never stretch. Only a featureless filler may be scaled,
  via the facade's `fill` option.

## 5. The manifest

Grouped `params` (`group`), a `type` choice (`options`), switches, colours, `advanced: true` on the
settings most people never touch, `night` for lit windows, 8+ `presets` with `tags`, and `handles`:
`scale: 2` for a model centred on that axis, `scale: 1 / storeyHeight, snap: 1` for floors. Keep
`build(THREE, p)` synchronous and returning a Group; read every param once, clamped, with the
manifest's value as the default.

## 6. Prove it — numbers first, then pictures

Forge it, importing the asset under a fresh query each time (a tab keeps the first copy of a
module): `const m = await import('/api/workspace/raw?path=' + encodeURIComponent(file) + '&v=' + Date.now()); add(m.default(THREE, params));`
with `"numbers":true` after every edit. Report:

- `kit.notes` (empty), triangles and draw calls against the budget
- the facade counts at a narrow and a wide width — bays went UP, nothing was scaled
  (`group.userData.facade.floors`, `userData.repeat.count`)
- then pictures of at least three presets (narrow, wide, another type), each judged against the
  reference in a sentence, and one night render

## What bit before

- **Z-fighting on layered surfaces.** A pane 1.5 cm in front of its wall fights once a camera's
  near plane is small (the forge's is 1 mm: ~5 cm of depth precision at 30 m). Give panes,
  curtains and sign faces `polygonOffset` (factor and units −1; −2 for the layer above) and keep
  anything meant to show in front of one ≥ 10 cm clear.
- **Near-white cloth** blows out under a studio key light and its folds read as hatching; use a
  grey-beige and about one fold per 12 cm.
- **A side wall needs its `yaw`.** Forget it and the "side" windows march along the front.
- **`9.6 / 1.6` is 5.999999999999999.** `ops.bays` has the slack; a hand-written floor does not.

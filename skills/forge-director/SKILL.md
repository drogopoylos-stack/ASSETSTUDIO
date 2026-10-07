---
name: forge-director
description: "Use FIRST for any request to make or improve a 3D asset - prop, creature, character, building, vehicle, weapon. Picks which of the five ways to build it, sets a triangle budget before any code, and ends on measured numbers."
disable-model-invocation: true
metadata:
  category: 3d
---

# Forge director

**Decide, then build. Not the other way round.**

The Studio has five ways to make a 3D asset and nothing that chooses between them. Measured on
this machine: **0 of 622** recorded generations imported the modelling library, and the catalogue
of which tool to use when has been one `curl` away the whole time. The gap is not documentation.
It is that nothing makes the decision explicit before the code starts.

This skill is that decision. Work through it in order. Do not skip to the code.

---

## 1. Write the brief down first

Four lines, in your reply, before any tool call:

```
subject   : creature | character | building | prop | vehicle | environment | weapon | ui | effect
budget    : <N> triangles, <M> draw calls
reference : <path to an image, or "none">
path      : reuse | forge | hunyuan | kiln | img2threejs
```

**The budget is not decoration.** A crate for a phone browser is 300 triangles, a hero creature
is 3,000–8,000, a background building is 800. Writing the number down is what stops a 40,000
triangle prop, and the last step checks against it. If the user gave no number, choose one from
the subject and say so.

**Ask for a reference image when the subject is a real or named thing.** Without one, `aim` has
nothing to score against and you are judging your own work by eye — which the A/B on this
machine measured as worse than judging by number.

---

## 2. Pick the path

Take the first row that fits. Do not build what the project already has.

| Path | When | How |
|---|---|---|
| **reuse** | The project may already own it | `curl -s --get 'http://127.0.0.1:8777/api/engine/assets' --data-urlencode 'project=<abs path>' --data-urlencode 'q=<word>'` — builders, spec tables, model files and images the project already carries. **Always check this first.** |
| **forge** | Procedural, stylised, parameterised, needs to match a game's look | `POST /api/live/forge` — the default for this user's games |
| **hunyuan** | A photo or a single clear reference image of a real object | Hunyuan3D through ComfyUI, locally. No cloud, no HuggingFace Space |
| **kiln** | Needs real modelling: booleans on a complex body, sculpt, retopo, UV by hand | the `blender-kiln` skill + Blender MCP |
| **img2threejs** | A reference image, and the result must be CODE rather than a mesh file | the `img2threejs` skill |

**This user prefers procedural three.js over GLB.** `forge` is the default. Choose another path
only for a stated reason, and say the reason.

---

## 3. Name every part

Every mesh gets a name that says what it is: `head`, `leftClaw`, `barrelBand`, `roofTile`.

This is not tidiness. Four things read those names and none of them can work without:

- `"focus":["claw"]` frames one part, so a 12-pixel detail becomes 500
- `placement.parts` reports where each part landed, as numbers
- the editor's outliner and its transform gizmo select by name
- `SINCE YOUR LAST SHOT` names what moved or vanished between two runs

An unnamed part shows as `(unnamed)` and is invisible to every one of them.

---

## 4. The loop, in this order

1. **Build.** `POST /api/live/forge` with `js`, a `label`, and `tags` naming the subject.
2. **Measure, with no picture.** `"numbers":true`. Findings, every part's size in pixels, what
   touches the ground, how many surfaces the body is. Hundreds of tokens, not thousands.
   **Do this after every edit.**
3. **Aim, if there is a reference.** `POST /api/live/aim` with `ref` once. 36 angles swept, the
   best angle and the proportion errors returned as numbers. Never judge a render against a
   picture taken from a different angle.
4. **Look, only when the numbers stop moving.** `"views":["3q"]`, then `/api/live/look` to walk
   round it without rebuilding — 0.6s for four more angles against 3s for a rebuild.
5. **Read `findings` FIRST, every time.** `NOT CHECKED` names the parts too small to have been
   judged. `BUILD` counts your surfaces and the ones that interpenetrate.

---

## 5. Use the modelling library

**This is the step that gets skipped.** One import, in the asset's own code:

```js
const ops = (await import('http://127.0.0.1:8777/forge-ops.js')).makeOps(THREE);
```

Reach for it when the numbers say to:

| The numbers say | Reach for |
|---|---|
| BUILD counts many interpenetrating surfaces, and it should be one skin | `skin(object)` — merge, weld, smooth |
| Faceted where it should be round | `subsurf` (Catmull-Clark, crease angle) |
| Edges read as paper-thin | `bevel` |
| It needs a texture at all | `unwrap`, then `bakeAO` / `bakeNormalMap` |
| Over the triangle budget | `remesh`, or `simplify` |
| Two solids should become one shape | `boolean`, `hull` |
| A soft organic body from parts | `blobs` |
| It should be symmetric and is not | `mirror` |
| A flat plane needs thickness | `solidify` |
| A mesh must follow a skeleton | `heatWeights` |

`check(object)` returns the defects as **numbers** — loose vertices, non-manifold edges,
degenerate faces. The full catalogue of when to use what:
`curl -s http://127.0.0.1:8777/forge-ops.md`

---

## 6. Close on numbers, not on an opinion

Before you say it is done, report these, measured:

- triangles, against the budget from step 1
- `check()` defects
- `score.overlap` against the reference, if there was one
- what `SINCE YOUR LAST SHOT` says changed

**If a number is worse than the budget, say so and fix it.** "It looks good" is not a finding.
A silhouette score is not the headline either: on the chest A/B here, `overlap` called two very
different chests equal, and what actually improved had no number at all. Report the numbers AND
say what you judged by eye.

---

## What this replaces

Nothing. It orders what already exists. The forge, the modelling library, `aim`, `look`,
Hunyuan3D and the kiln all worked before; what was missing was a step that chose between them and
a last step that checked. Both the official Unity plugin and the 94-skill Blender pack put a
router first for the same reason.

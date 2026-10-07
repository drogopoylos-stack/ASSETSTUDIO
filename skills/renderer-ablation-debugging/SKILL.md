---
name: renderer-ablation-debugging
description: Use when a rendered scene looks wrong and you cannot tell which term causes it (washed colour, missing shadows, a surface you cannot identify) — find it by ablating one term at a time and probing the running app, instead of reasoning about the shading pipeline.
disable-model-invocation: true
metadata:
  category: game-dev
  created: 2026-08-17
  updated: 2026-08-17
  confidence: verified
  source: experience
---

# Renderer debugging by ablation and live probing

A lit frame is a product of a dozen terms. Deriving which one is wrong from the shading model
is slow and frequently wrong — the pipeline has more places to hide than you can hold in your
head, and source comments describe intent, not the running value.

**Ablate, then probe. Do not reason.**

## 1. Ablation: remove one term, shoot the same frame

Build a harness that renders a fixed, repeatable frame with one term disabled, driven by a
query parameter. Then run it once per suspect.

Real example — a pop field measured 0.780 median saturation against a 0.935 target. Three
suspects. Shot back to back at a matched camera:

| ablation | result |
|---|---|
| specular → 0 | 0.780 → 0.785 (nothing) |
| emissive → 0 | 0.780 → 0.778 (nothing) |
| **diffuse map unbound** | 0.780 → **0.870** |

The texture was the cause and the two "obvious" suspects were noise. Deriving that from the
shading equations had already failed twice.

**The harness pattern** (browser game, headless capture):

```js
// shot.js — appended to the page, driven by ?m=<mutation>
const m = new URLSearchParams(location.search).get('m') || '';
if (m.includes('nospec'))  { mats.button.specular.set(0,0,0);  mats.button.update(); }
if (m.includes('noicon'))  { mats.button.diffuseMap = null;    mats.button.update(); }
if (m.includes('nofog'))   { pin(() => app.scene.fog.type = 'none'); }
```

Then `for M in none nospec noicon nofog; do capture "?m=$M"; done` and compare with one metric
script. One evening of harness pays for itself in the first real bug.

### Pin anything the frame loop rewrites

If the update loop rewrites the value every frame, setting it once does nothing. Pin it:

```js
const pin = (fn) => { fn(); setInterval(fn, 8); };
pin(() => app.scene.ambientLight.set(0.12, 0.12, 0.13));
```

This is also how you discover that a value is being rewritten at all.

### Tint buckets instead of hiding them

To identify *which system draws that thing*, **recolour** rather than remove. Deleting mesh
instances mutates arrays other code is iterating and can break the scene (in one case the
capture came back framed on a different level entirely, wasting two runs).

```js
mats.glow.emissive.set(1,0,0);      mats.glow.emissiveVertexColor = false;
mats.paint.emissive.set(0,0,1);     mats.paint.emissiveVertexColor = false;
mats.character.emissive.set(1,1,1); mats.character.emissiveVertexColor = false;
// one screenshot: the mystery object names its own bucket
```

## 2. Probing: ask the running app, never the source

Source tells you what someone intended. The running app tells you what is true. In any codebase
with a tuning history these diverge, and the comment is usually the confident one.

Cases found this way that source reading had missed or actively misdirected:

- **A constant set in two places.** Build-time set `0.22 + sky*0.20`; the per-frame update set
  `0.36 + sky*0.40` and won within a second of boot. The build-time value — and the long comment
  arguing for it — never reached a single pixel. A one-line probe printed the live value.
- **A material that was not the one in the code.** The hero character was assumed to use the
  project's tuned material. Probing showed it kept the glTF's own material, so every tweak to the
  project material had done nothing to it.
- **A dead assignment.** `specular = 0.1` on a material with `useMetalness = true`, where specular
  is derived and the field is ignored. Silent.

**Probe shape:**

```js
const out = [];
const walk = (e, path) => {
  if (!e.enabled) return;
  for (const mi of e.render?.meshInstances ?? []) {
    const m = mi.material;
    out.push({ path: path + '/' + e.name, mat: m?.name,
               amb: m?.ambient && [m.ambient.r, m.ambient.g, m.ambient.b],
               emis: m?.emissive && [m.emissive.r, m.emissive.g],
               map: !!m?.diffuseMap, metal: m?.useMetalness });
  }
  for (const k of e.children) walk(k, path + '/' + e.name);
};
walk(app.root, '');
return out;
```

Dump material state, light `forward` vectors, scene ambient, per-frame counters. Print the
**derived** value (a light's forward vector), never the authored one (its euler angles) — sign
conventions are the classic silent failure.

## 3. Hook a function to prove a chain end to end

When the question is "does A actually reach B", wrap B and count:

```js
const orig = world.popDome.bind(world);
let armed = [];
world.popDome = (p, i) => { armed.push(i); return orig(p, i); };
// drive the scenario, then read `armed`
```

This turned "I believe the fix works" into `firstArrival [275,296] / return [275,296]` — the same
cells re-armed, proven in the shipping build rather than in a unit test's idea of it.

## Ordering rule

1. **Reproduce** on a fixed frame (same camera, same seed) so two runs are comparable.
2. **Measure** — get a number before you touch anything.
3. **Ablate** the two or three suspects, one run each.
4. **Probe** the winner's live state to confirm the mechanism.
5. **Fix, then re-measure the same number.**

Skipping (1) is what makes people distrust their own measurements. Skipping (2) means you cannot
tell a fix from a regression — several times a change that "looked better" measured worse.

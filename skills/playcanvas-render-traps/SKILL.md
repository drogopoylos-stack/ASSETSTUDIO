---
name: playcanvas-render-traps
description: Use when a PlayCanvas (or similar WebGL StandardMaterial) scene renders washed out, flat, blown to white, or has shadows/unlit surfaces that behave wrongly — a list of silent traps with the exact fix for each.
disable-model-invocation: true
metadata:
  category: game-dev
  created: 2026-08-17
  updated: 2026-08-17
  confidence: verified
  source: experience
---

# PlayCanvas / StandardMaterial render traps

Each of these produced a visible defect, took a measurement to find, and has a one-line fix.
Verified on PlayCanvas 2.x, WebGL2. Most apply to any `StandardMaterial`-style forward renderer.

## `useLighting = false` does NOT remove ambient

The standard shader still adds `ambientLight * diffuse`, and **`diffuse` defaults to white**. So
every material that declares itself unlit and stops there is floored at the scene ambient.

Worst case seen: a contact-shadow blob with `emissive = (0,0,0)` on purpose. With a white diffuse
the *entire thing it rendered was the ambient* — a pale blue-grey blob at 42% opacity that
**lightened** the ground it existed to darken. Every attempt to tune it darker was tuning an
emissive that was already zero.

```js
const unlit = (m) => {
  m.useLighting = false;
  m.diffuse  = new pc.Color(0, 0, 0);   // <- the actual fix
  m.specular = new pc.Color(0, 0, 0);
  return m;
};
```

Apply it to **every** unlit material — signage, sky domes, shadow blobs, additive FX, billboards.
An additive material with a white floor adds it to the frame outright.

## An sRGB texture multiplier is a hidden global exposure change

A greyscale map bound as `diffuseMap` composes multiplicatively with the encode at the far end,
so a sheet painted at 0.824 does not darken a little — it renders **every pixel of that surface
at 82% of its authored colour**, before any light. Three tuning passes went looking for the
missing brightness in the light, the ramp and the ambient.

It also rotates hues: 82% of a clean tangerine at a shaded ring is mud.

**Rule:** a multiplier map's background value is a global exposure dial. Keep it at or near 1.0
and put relief in the *carve depth*, not in the base level.

## `emissiveIntensity` multiplies before the ceiling

`emissive * emissiveIntensity` clips at 1.0. At intensity 1.25 any value authored above ~0.80
clips to pure white — and a cloud's entire ramp lives above 0.80. Result: a three-tone ramp
rendered as fifty consecutive rows of `#ffffff`, with every authored value discarded at the last
step. Three rounds of re-authoring the ramp moved nothing.

**Check:** if a surface renders flat white, print its final value — not its authored one.

## Ambient is a MULTIPLY; the sRGB encode is what greys your shadows

`ambient * albedo` cannot desaturate anything — it scales all channels together. The intuition
that "an undirected near-white fill must cost chroma" is wrong, and acting on it makes things
worse: cutting ambient 44% moved a field's median saturation **down** 0.870 → 0.706.

What actually greys a surface is the **sRGB encode**, which is concave — crushing a surface
toward black in linear space compresses the gap between its channels on the way out. So a low
ambient means a deep key-to-fill ratio means every averted face is crushed, and crushed faces
are grey faces.

**Consequence:** flat, high-ambient lighting *preserves* chroma. Buy your dark end from geometry
(contact shadows, a near-black trim) rather than by shading colours into the floor.

## The metalness workflow ignores `specular`

glTF materials arrive with `useMetalness = true`. In that workflow a dielectric's specular is
derived from metalness and albedo, and the `specular` field does nothing. A line setting it can
sit there for months looking meaningful. Reach for `gloss` (and `metalness`) instead.

## Imported glTF materials are never re-lit for your scene

`instantiateRenderEntity()` keeps the exporter's material, including `ambient = 1.0`. If your
scene runs a tuned ambient response on its own characters, an imported one is lit by another
program's defaults — usually much flatter. Symptom: re-aiming your key light changes it by
almost nothing.

```js
for (const rc of inst.findComponents('render')) {
  for (const mi of rc.meshInstances) {
    const m = mi.material;
    if (!m?.update) continue;
    m.ambient = new pc.Color(/* your scene's response */);
    m.update();
  }
}
```

## An emissive keyed to the albedo is a hue-preserving floor; a flat one is a hole

`emissive = 0.5` with `emissiveMap = <the albedo>` adds **half the character's own texture as
light it emits itself**. Emissive is undirected by definition, so half the model bypasses the key
entirely and no aiming of that key can model it. Measured: left/right thirds differing by 2.9
luminance levels where a well-lit reference differs by ~37.

Keep it a hint (0.08–0.12), and always key it to the map so it lifts in the surface's own hue.
A flat neutral emissive is an additive grey floor — it destroys shadow hue and blocks true black.

## Bevels are clamped, so non-cubic boxes keep flat faces

Mesh builders typically clamp a chamfer to a fraction (e.g. 0.7) of the **smallest** half-extent.
Asking for 0.92 on a lobe measuring 1.15 × 0.95 × 1.05 yields 0.665 and leaves flat faces across
the two longer axes — the classic "those are rectangles, not clouds" stairstep.

**Fix:** make the lobe **cubic**. A cube spends the whole clamped bevel on all three axes at once
and comes out as a rounded ball. The request was never the problem; the aspect ratio was.

## Author values on geometry the camera can actually see

A cloud deck at y 52 seen from a camera at y 3 shows its **belly**. Bright crown lobes at the top
of each puff were fully occluded by the wide middle ring in front of them, so three rounds of
colour work never reached a pixel. A vertical walk down the puff moved four luminance levels
against a reference's sixty-one.

Before authoring a value ramp, work out **which part of the object is on screen from the game's
actual viewpoint** and put the range there. This is not a physical lighting model; it is the
lighting model for the one camera the game is played from — which is the only one that matters.

## Fog is a lerp, so it is chroma off every distant surface

EXP2 fog at a density that reads as "light haze" still takes ~25–40% of the chroma off
architecture 60–200 m out. Depth cue and saturated distant colour are bought from the same
budget. Either author distant surfaces **above** their target screen chroma by roughly what the
fog takes, or exempt specific layers.

**Never fog the clouds.** Aerial perspective is the atmosphere between the eye and a thing; a
cloud is not in front of the atmosphere, it is made of it. Fogged clouds cannot reach white, and
a daylit frame whose brightest object cannot reach white reads as overcast.

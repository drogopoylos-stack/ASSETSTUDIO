---
name: stylised-3d-reference-matching
description: Use when art-directing a stylised 3D game scene to match or beat a competitor reference screenshot — the value/chroma structure that makes stylised art read, what to measure, and how to run a grading loop that gives honest scores.
disable-model-invocation: true
metadata:
  category: game-dev
  created: 2026-08-17
  updated: 2026-08-17
  confidence: verified
  source: experience
---

# Matching a stylised 3D reference

Rebuilding a scene to match a reference screenshot. What follows is measured off shipped
stylised games and validated by moving a real frame toward them.

## The value structure that reads

**Dark saturated masses under bright caps.** Eyedropped off a shipped stylised street:

```
brick wall      #248b76   L 0.34   HSV sat 0.74
kiosk face      #697e2d   L 0.34   HSV sat 0.64
purple coping   #8d5dd5   L 0.60   HSV sat 0.56
honey cornice   #ecbb71   L 0.68   HSV sat 0.52
lavender parapet #e598fb   L 0.79   HSV sat 0.39
```

A clean split: **walls in the low 0.3s, everything that CAPS a wall between 0.60 and 0.79.**
That step is what gives a skyline its silhouette. Getting it backwards — one dark value doing
every coping, awning and trim — flattens the whole band into a single mass, and it is the most
common way stylised architecture fails.

Note the second axis: **purity falls as value rises.** Walls 0.64–0.74, caps 0.39–0.56. Full
purity on a large flat surface reads as a coloured card, not as painted material.

## Silhouette details that carry the style

- **Cornices are rows of discrete blocks with visible gaps, not one extruded bar.** A continuous
  bar has one silhouette, one highlight and no rhythm; at 50 m long it reads as a coloured rule
  drawn across the frame. Alternate the blocks by ~7% of VALUE, not by hue — alternating two
  colours reads as bunting.
- **Put a face on something.** Character-genre games press eyes and a mouth into a building.
  It is the piece everyone looks at and the theme stated in one object. Cost: ~6 boxes.
- **Overlapping capsules, not stacked boxes.** For organic masses (clouds, foliage, balloon
  envelopes), overlap consecutive elements by ~90% of their pitch. At 25% overlap they stay
  separate lozenges arranged in a circle — reviewers reliably call that "broken geometry" or
  "placeholder", and recolouring it never fixes it because it is a topology problem.
- **Taper anything inflated.** A ring of equal boxes is a drum. Three courses at falling radius
  makes a balloon. Add rigging: ropes are what say the basket HANGS.
- **Pair a checker against a lighter version of the same hue, not against white.** Half the
  gores at pure white averages the object toward neutral before fog even touches it.

## Contact shadows

Measured on a shipped reference: the band under a prop sits at **luminance 0.23 against a floor
at 0.63** — about 37% of its floor, not 8% — and it is **tight**, recovering to full floor within
about 12 screen pixels, monotonically.

Two independent knobs, and passes routinely trade them the wrong way:

- **darkness** — how deep the core goes
- **width** — how far the falloff runs

A shadow whose dark core sits under the silhouette of the thing casting it is invisible however
dark it is; one smeared over 6× the area reads as grime. And a non-monotonic falloff (dark, light,
dark again) does not read as a shadow at all — a reviewer called an accidental bright intermediate
band "the single most artificial-looking element in the foreground".

Make it a **deep version of the floor's own hue**, not black and not grey. Black is the scarcest
signal in a bright scene; spend it on hazards and frame edges, not on a thousand repeated props.

## Making a character more colourful

**Multiplies cannot add chroma.** Every material lever — `diffuse`, `ambient`, `emissive`, the
light itself — is a multiply, and a multiply cannot add chroma to an albedo that has none. If a
character measures half the chroma of the props around it, the colour has to come from the texture.

Push it **in HSV, holding V exactly**: move each channel away from the strongest one (that is the
definition of raising S) and leave the strongest where the value ramp put it.

```js
// per texel: mx = max(r,g,b); v = valueCurve[mx]; k = satMul * v / mx
r' = clamp(v - (mx - r) * k)   // and likewise g, b
```

This matters because a character's whole form — scales, shading, baked occlusion — is stored in
the texture as **value**. Saturating in HSL, or plain multiplying, flattens the sculpt to buy the
colour. Holding V keeps every bit of it.

**The two knobs fight.** Luminance is a weighted sum of all three channels, so pushing the weak
ones down to buy chroma necessarily costs brightness. Expect to pay it back with a midtone curve
in the same pass. (Real numbers: chroma 79 → 127 dropped the median 127 → 122; a gamma of 0.74
brought it to 137.)

## Running a grading loop

Spawn two independent graders per round with an explicit calibration, and score against a fixed
rubric (colour / form / texture / lighting / composition).

**State the calibration in the prompt** and keep it identical across rounds:

```
10 = as good as or better than the reference
8  = genuine peer quality; nobody would call ours the budget version
6  = competent but a visible notch below
4  = amateur beside the reference
Use the full scale; do not cluster at 7.
```

Four things that make the loop worth its cost:

1. **Tell graders the measurement rules** (HSV not HLS; count per-channel clipping). A grader with
   the wrong metric reaches a confident wrong conclusion, and you will chase it.
2. **Make them produce crops.** A ranked fix with a tight close-up of the defect is actionable; a
   sentence is not.
3. **Scores are NOT comparable across calibrations.** Adding "do not cluster at 7" to the prompt
   moves scores ~2 points on an unchanged image. If you change the rubric, say so when reporting,
   or you will look like you made the art worse.
4. **Verify their measurements before acting.** Graders make real measurement errors — one put a
   character's left/right modelling delta at 2.9 using a fixed pixel box; segmenting the character
   by hue gave 23.0. The defect was real, the magnitude was not.

**Use a blind A/B to know whether you actually improved anything.** Hand one grader the old and new
frames unlabelled, ask which is closer and — critically — *what the newer one made worse*. That
question surfaces regressions your own metrics were not watching.

## Where the score actually lives

On a frame with a strong foreground and a weak background, expect an attribution like:
**~70–75% of the shortfall outside the hero surface.** The near field's failures are tuning-level
(the geometry is right, the chroma is present, it is 15% hot); the background's are *missing
feature classes* — no cloud form, no wall texture, no depth attenuation, no skyline silhouette.

Tuning a surface that already measures close moves the score very little. Check the area-weighted
attribution before choosing what to work on next.

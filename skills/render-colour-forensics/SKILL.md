---
name: render-colour-forensics
description: Use when judging or tuning a rendered frame's colour against a reference image (game art direction, look-dev, screenshot matching) — the metric set that actually detects washing, clipping and hue rotation, and the two standard metrics that silently lie.
disable-model-invocation: true
metadata:
  category: game-dev
  created: 2026-08-17
  updated: 2026-08-17
  confidence: verified
  source: experience
---

# Rendered-frame colour forensics

Measuring a rendered frame against a reference. The headline: **HLS saturation and HLS
lightness cannot see the two failure modes you are most likely to have.** Tuning against
them wastes rounds while the image visibly degrades.

## The two lies

### 1. HLS saturation is blind to whitening

`colorsys.rgb_to_hls` returns **S = 1.0 for every colour on the surface of the RGB cube**.
A hue whose weak channel has been lifted from 0.10 to 0.37 — visibly washed toward white —
keeps HLS S = 1.0 while losing a third of its chroma.

```
(255,  26,  26)  HLS S = 1.00   chroma 229   vivid red
(255, 128, 128)  HLS S = 1.00   chroma 127   pink
```

Same number. Half the colour.

**Use instead:** HSV saturation `(max-min)/max`, or absolute chroma `(max-min)`.
Report both — HSV S is scale-free, chroma tells you the absolute gap.

### 2. Nothing in HLS can see clipping

Clipping two of three channels is a **hue rotation**, and both HLS L and HLS S report it as
an ordinary bright colour:

```
authored amber  (253,206, 62)  hue  39°
clipped         (255,255, 79)  hue  60°   <- acid lemon, and HLS calls this fine
```

**Use instead:** count pixels with **two or more channels at ≥250**. That single number
catches over-exposure that every perceptual metric hides. A typical stylised reference sits
near 1.5–2.5% of frame; past ~5% you are destroying colour, not adding brightness.

## The metric set

Track all of these per region, never one:

| metric | what it catches |
|---|---|
| HSV sat median | washing toward white |
| chroma median `(max-min)` | absolute colourfulness |
| L median | overall exposure |
| L p10 / p90 | whether the value RANGE matches, not just the middle |
| ≥2-channel-clip % | over-exposure and hue rotation |
| per-hue-family chroma | one or two families being neon while the rest are fine |

**The whole-frame median hides per-family blowouts.** A field measuring 0.761 against a
target 0.711 looks like a small overshoot and can in fact be two hue families a long way
out (purple +53%, cyan +74%) while the rest sit inside. Always break chroma down by hue
family before concluding the palette is fine.

## Sample many, not one

A single silhouette in a perspective frame carries the depth it happened to be at. Comparing
one object from each image measured a 46% proportion error that did not exist; the median of
22 auto-located instances per frame put the two within 0.004.

Locate instances programmatically (scan rows, flood-fill by hue) and take the median.

## Compare like regions

A hue filter tuned to one image is not a fair comparison. Example: filtering "deck gold" by
hue excluded the reference's warm contact shadows (hue ~10°) while including ours (hue ~45°),
so the same filter measured two different populations and reported a gap that was an artefact.

Check region composition first — e.g. what fraction of each crop is sky vs geometry — and only
compare once those match.

## Runnable

```python
"""Compare a rendered frame against a reference. python cmp.py ref.png shot.png"""
import sys, colorsys, statistics as st
from PIL import Image

def stats(path, box=None):
    im = Image.open(path).convert('RGB'); px = im.load(); w, h = im.size
    x0, y0, x1, y1 = box or (0, 0, w, h)
    S, C, L = [], [], []
    clip = tot = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            tot += 1
            if ((r >= 250) + (g >= 250) + (b >= 250)) >= 2:
                clip += 1
            if mx < 30:
                continue
            hh, ll, ls = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if ls < 0.25 or ll < 0.15 or ll > 0.97:   # skip greys and near-white
                continue
            S.append((mx - mn) / mx); C.append(mx - mn); L.append(ll)
    L.sort()
    return dict(hsv=st.median(S), chroma=st.median(C), medL=st.median(L),
                p10=L[len(L)//10], p90=L[len(L)*9//10], clip=100*clip/tot)

for p in sys.argv[1:]:
    s = stats(p)
    print(f"{p:24s} HSVsat={s['hsv']:.3f} chroma={s['chroma']:5.1f} "
          f"medL={s['medL']:.3f} p10={s['p10']:.3f} p90={s['p90']:.3f} clip={s['clip']:.2f}%")
```

## Reading the result

- **medL matches, p10/p90 do not** → your range is wrong, not your exposure. Do not scale the
  whole image; move the ends independently (ramp the highlights, deepen the contact shadows).
- **HSV sat low, chroma low, medL low together** → a hidden multiply somewhere in the pipeline
  (texture, tint, ambient), not a palette problem. Ablate to find it.
- **clip% high and hue shifted** → back the exposure off; no amount of palette work survives it.
- **HSV sat fine, chroma low** → the image is correctly *proportioned* but dim. Multiply up.

## Two levers, different prices

When a surface needs to be brighter there are usually two ways, and they do not cost the same.
Measure both before choosing:

- a **multiply** (light intensity, ambient response, material tint) — cheap in chroma, because
  it scales all channels together
- a **lightness ramp** (authoring the colour lighter, an HSL value push) — expensive, because
  moving lightness toward white lifts the weak channels and that IS desaturation

Measured on one field, for the same +0.020 median lightness: multiply cost 0.004 chroma, ramp
cost 0.008. Buy brightness with light; keep authored ramps near 1.0.

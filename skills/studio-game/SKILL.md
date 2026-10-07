---
name: studio-game
description: Validated game-dev patterns, engine/API facts, and gotchas accumulated across projects (gameplay, physics, performance, tooling). Auto-captured when Auto-learn is on.
metadata:
  category: game-dev
  updated: 2026-06-17
  auto: true
disable-model-invocation: true
---

# Studio · Game Dev

Validated game-development learnings — each a dated, sourced `## entry` you can edit or remove.
Auto-captured when **Auto-learn** (the 🎓 toggle in the chat box) is on.

<!-- learnings are appended below -->

## Sliced-sprite white-halo / fringe removal (de-matte) — 2026-06-17
Sprites sliced from art sheets exported over a white/light background keep a pale **halo on anti-aliased edges** (and white bleeding into concave gaps) — invisible on white, but glows on a dark game background. Cause: edge pixels are semi-transparent yet still carry white RGB (a "white matte").
**Fix = un-multiply the white matte, edge-only.** For every pixel with `0 < alpha < 255`: `F = (C - 255*(1-a)) / a` per RGB channel (`a = alpha/255`), clamp 0..255, keep alpha. Recovers the pixel's true color so it blends into any background.
- Touches ONLY partial-alpha (edge) pixels → solid silver blades / white gems (alpha 255) are preserved. Do **not** naively "delete near-white pixels" — it eats legitimate silver/white art.
- **Not idempotent** — never run twice on the same file (second pass over-subtracts → black/dirty edges). Bake it into the slice step, or always process from the original source.
- Optional erode pass for a stubborn near-opaque white ring: zero the alpha of near-white (`min(rgb)>185`, `max-min<26`) edge pixels that are adjacent to a transparent pixel; use sparingly (can cause jaggies). De-matte alone usually suffices.
- Verify zoomed, BEFORE/AFTER, composited on the actual dark bg. Reusable tool: `tools/defringe.js` (Playwright canvas: PNG → getImageData → de-matte → toDataURL). Run slicers/processors with `PW_PATH=<abs path to a playwright install>`.

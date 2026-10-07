---
name: game-hud-over-playfield
description: Use when a game's HUD chips, prompts or world nameplates cover the character or the playfield, overlap each other, or balloon in size as the player approaches — placement and sizing rules with the CSS/3D fixes.
disable-model-invocation: true
metadata:
  category: ui-ux
  created: 2026-08-17
  updated: 2026-08-17
  confidence: verified
  source: experience
---

# HUD and world labels over a playfield

Two failures cover most "I can't see the game" reports.

## 1. A bottom-anchored column grows UP into the play area

A flex column pinned to `bottom` stacks upward from its anchor. Put status chips above a speed
dial and they grow out of the top of it — straight onto the character's feet and the nearest row
of interactive ground. The HUD ends up covering the game with information about the game.

**Move transient status to the top**, keep the persistent instrument (dial, level bar) at the
bottom. Sky is the cheapest real estate in a third-person game; the ground under the character
is the most expensive.

## 2. Chips that are individually positioned WILL overlap

Absolutely positioning each chip guarantees that two of them collide the moment one carries long
text. Put every chip in **one flex column** and let `display: none` do the layout:

```css
.statuswrap {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  max-width: min(60vw, 760px);   /* clear of the top-left and top-right cards */
  pointer-events: none;          /* a wide invisible box must not eat taps */
  z-index: 3;
}
.hidden { display: none !important; }
```

A hidden chip collapses and the rest move up, so **no two can ever draw through each other**
however long their text runs. Verify by forcing every chip visible with its longest string and
reading back the bounding boxes — they should have gaps, not intersections:

```js
const r = (id) => { const b = document.getElementById(id).getBoundingClientRect();
                    return [Math.round(b.top), Math.round(b.bottom)]; };
// boost [14,60]  gap [62,112]  prompt [118,181]   <- no overlap
```

`max-width` is what keeps a long prompt clear of the corner cards on a narrow window.

## 3. A world-space billboard grows without limit

A 3D nameplate placed at a fixed **world** size has a screen size proportional to `1/distance`.
Walking up to a collectible makes its label fill the frame and stack on its neighbours'.

Shrinking the constant is the wrong fix — it trades legibility at range for survivability up
close and solves neither. **Scale the quad with distance** so its screen size stays constant:

```ts
const d = Math.max(NEAR, distanceToCamera(x, z));
const s = Math.min(2.0, Math.max(0.5, d / REF_DISTANCE));   // REF ≈ 13 m
label.place(x, y + BASE_H + LIFT * s, z, W * s, H * s);
```

Clamp both ends: nothing under half size (a distant name must stay readable), nothing over double
(a label across the arena must not become a banner). Note the **anchor height scales too**, or a
shrunken label drifts down into the object it names.

## Watch for capture harnesses hiding the problem

A screenshot harness that clears props near the camera for a "clean" shot will hide label crowding
entirely — and worse, will make reviewers mark the frame down for an empty midground the harness
itself emptied. Keep automated captures representative: clear only what is literally between the
lens and the subject (a couple of metres), not a wide box.

## Checklist

- [ ] Transient status at the top; persistent instrument at the bottom.
- [ ] Every chip in one flex column; `display: none` for hidden, never `visibility`/opacity.
- [ ] `max-width` set so the longest string clears the corner cards.
- [ ] `pointer-events: none` on any full-width overlay.
- [ ] World labels scale with distance, clamped, with the anchor height scaling too.
- [ ] Verified by forcing the worst case (all chips, longest text) and reading bounding boxes.

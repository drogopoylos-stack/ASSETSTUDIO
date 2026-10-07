---
name: animation-vs-gameplay-clocks
description: Use when an interactive object stops responding some of the time — a button that will not re-press, a pickup that will not re-highlight, a door that will not re-open — usually because its animation is gated on a gameplay cooldown instead of its own duration.
disable-model-invocation: true
metadata:
  category: game-dev
  created: 2026-08-17
  updated: 2026-08-17
  confidence: verified
  source: experience
---

# Two clocks on one object

Any object that both **animates** and **has gameplay state** carries two independent durations,
and they are almost never the same length:

| clock | question it answers | typical |
|---|---|---|
| **animation** | how long the squash / flash / open takes | 0.2–0.5 s |
| **gameplay** | how long before it pays / triggers / can be used again | 1–30 s |

**Gate each on its own clock.** Gating the visual on the gameplay clock produces a bug with a
signature worth memorising: **the object works, then stops working, then works again** — and it
fails only inside the window between the two durations, which is exactly the interval a player
spends turning around and coming back.

## The shape of the bug

A real one. A floor button that pressed down when stepped on:

- the sim re-stamped `lastTouched` on **every** contact tick (timing source correct)
- but it only emitted the `pressed` **event** after the 3 s economy cooldown
- the event was the **only** thing that put the cell on the renderer's animation list
- the renderer **retired** a cell from that list after 0.35 s

So between **0.35 s and 3 s** the object was known by the sim to be underfoot, had its timestamp
refreshed, and never moved. Walking back over your own footprints pressed nothing. Standing still
worked (never retired), and returning after 3 s worked (event fired) — which is why it read as
intermittent rather than broken.

## The fix

Emit the visual trigger on the **animation** clock; keep the credit on the gameplay clock.

```ts
const since = now - lastTouched[i];
const canPayAgain = since >= ECONOMY_COOLDOWN;   // 3 s
lastTouched[i] = now;

if (since >= ANIM_DURATION) emitVisualTrigger(i);  // 0.35 s — re-arms the animation
if (!canPayAgain) return;
credit();                                          // untouched
```

Note this also stops the spam you might fear: standing still re-stamps every tick, so `since` is
one frame and reaches **neither** threshold. The event fires only on a genuine re-arrival.

## Make the constants underivable

The failure mode is silent: if the renderer's retire time ever exceeds the sim's re-fire
threshold, a band of objects quietly stops responding and nothing errors. Define the duration
**once**, on the sim side, and derive the render-side split from it:

```ts
// sim
export const PRESS_DURATION = 0.35;
// render
const HOLD   = 0.10;
const SPRING = PRESS_DURATION - HOLD;   // never type 0.25 here
```

The invariant is `renderRetireTime <= simReFireThreshold`. Deriving makes it equal by
construction.

## Testing it

Assert **both halves**, or the next person will "simplify" the split away:

```ts
// 1. the animation re-fires inside the gameplay cooldown
// 2. the gameplay reward still does NOT
step onto it            -> expect a visual trigger
step off, wait 1.0 s    -> (> ANIM_DURATION, < ECONOMY_COOLDOWN)
step back on            -> expect a visual trigger for the SAME id
                        -> expect NO credit
```

And prove it in the running build, not only in the sim, by hooking the call the renderer actually
receives and checking the same ids come back:

```js
const orig = world.arm.bind(world);
let armed = [];
world.arm = (i) => { armed.push(i); return orig(i); };
// firstArrival [275,296] / return [275,296]  <- what "seamless" looks like as data
```

## Where else this shows up

Same bug, different costume: pickup highlight vs respawn timer; door open animation vs lock
cooldown; hit flash vs invulnerability window; damage number vs attack rate. Whenever you find a
"sometimes it doesn't react" report, list the object's durations first and check which one the
*visual* is gated on.

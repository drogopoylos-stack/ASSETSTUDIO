# Deterministic visual review

A way for an AI agent to look at a game and actually judge what it sees.

Written for a developer with no context on this codebase. Everything below is implemented and
measured; the numbers are from a real run on the author's machine (Windows 11, RTX 2070 SUPER).

---

## 1. The problem

An agent asked to review a spell effect, a character or a UI screen does the obvious thing: it
launches a headless browser, loads the game, and takes a screenshot. That screenshot is the wrong
frame almost every time, for four separate reasons:

| | |
|---|---|
| **time** | an impact effect peaks for roughly 200 ms; one frame at an arbitrary moment misses the peak |
| **state** | a page load shows the loading bar or the title screen, not the pose, the cast or the boss |
| **scale** | a 40 px effect inside a 1280×720 frame is not visible to the reviewer at all |
| **chance** | seeds and physics differ per run, so no two reviews compare and no A/B is possible |

None of these is a screenshot-quality problem, so none of them is fixed by a better screenshot
tool. Three of the four are fixed by controlling *when* the frame happens.

## 2. The core idea

**Replace the page's clock, then step it by hand.**

Before any page script runs, `performance.now`, `Date`, `requestAnimationFrame` and `Math.random`
are replaced. The page's frame callbacks are collected in a `Map` instead of being scheduled. The
driver then advances a counter and invokes those callbacks itself.

```js
let now = 0;
const rafs = new Map();

performance.now = () => now;
window.requestAnimationFrame = (fn) => { const id = rafId++; rafs.set(id, fn); return id; };

step(ms) {
  now += ms;
  const due = Array.from(rafs.values());
  rafs.clear();
  for (const fn of due) { try { fn(now); } catch (e) { errors.push(String(e)); } }
}
```

Consequences worth spelling out:

- Ask for t = 0, 250, 500 ms and you get **exactly** those frames.
- Two runs produce **byte-identical** PNGs, because the only inputs are the clock and a seeded PRNG.
- The frame is **complete by construction**. The callback returns, *then* the capture happens.
  There is no race with a real frame loop, so no tearing and no half-drawn buffer.
- **Background-tab throttling cannot reach it.** Chrome throttles `requestAnimationFrame` in
  background tabs; here there is no browser-scheduled rAF to throttle.
- The engine needs no cooperation, no plugin and no build flag. It never learns the clock is fake.
  Verified on 2D canvas, Three.js/WebGL and DOM.

`Math.random` is replaced with mulberry32 seeded from the request, so particle scatter and spawn
positions repeat exactly.

`Date` is replaced with a **subclass**, not a `Proxy`, so `instanceof Date` still holds:

```js
class FakeDate extends RealDate {
  constructor(...a) { if (a.length === 0) super(EPOCH + now); else super(...a); }
  static now() { return EPOCH + now; }
}
```

## 3. Architecture

```
FastAPI route  ──►  review.render(project, spec)
                         │
                         │  1. find an origin for the project
                         │     dev_server.running_servers()  → the game's own dev server
                         │     preview_server.serve_dir()    → else a throwaway static server
                         │
                         │  2. one long-lived headless Chrome, reused across reviews
                         │     (launching is the cost; a tab is nearly free)
                         │
                         ▼
                   CDP over a websocket
                         │
                         ├─ Page.addScriptToEvaluateOnNewDocument   ← the shim, before page JS
                         ├─ Page.navigate
                         ├─ warm-up: step 16 ms, real-sleep 8 ms, repeat
                         ├─ per sample time: Input.dispatchKeyEvent / step / grab
                         └─ Runtime.evaluate("__review.grab()")     ← canvas.toDataURL
                         │
                         ▼
                   Pillow + numpy
                         │
                         ├─ contact sheet  (labelled grid + gameplay-scale strip)
                         ├─ metrics        (per frame)
                         └─ peak frame     (full capture resolution, separate file)
```

### Warm-up: the non-obvious part

A game boots asynchronously. It fetches atlases, decodes audio, and walks a loading state machine
that only advances **on a frame tick**. Both must happen before the first sampled frame, and they
need each other:

- real time alone leaves the state machine frozen — its frame callbacks belong to the driver now
- stepping alone gives the network no chance to answer

So the warm-up steps in small beats with a real pause between them:

```python
while done < warm:
    await cdp.js("__review.step(16)", sid, wait=False)
    done += 16
    await asyncio.sleep(0.008)
```

Without this, the first real test produced eight photographs of a loading bar. The metrics
correctly flagged it `STATIC`, which is how the bug was found.

Inputs with a **negative** `at` fire during warm-up — that is how you get past a title screen:

```json
"input": [{"at": -400, "type": "key", "key": " ", "code": "Space", "keyCode": 32}]
```

### WebGL

WebGL discards its drawing buffer after compositing unless asked not to, so a capture taken after
the frame returns **solid black**. The shim patches context creation:

```js
const realGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (type, attrs) {
  if (String(type).indexOf('webgl') === 0)
    attrs = Object.assign({}, attrs, { preserveDrawingBuffer: true });
  return realGetContext.call(this, type, attrs);
};
```

Every 3D review depends on that one line.

## 4. Two modes

**`scene`** — loads the real game and drives a scripted input sequence. Needs no per-project setup,
so it works in any workspace immediately. Answers *"does this read in play?"*

**`isolate`** — mounts one asset on a known backdrop, from a file the project owns:

```js
// <project>/review/targets.js
export const targets = [
  {
    name: 'frostbolt',
    note: 'impact burst, ~1.2s',
    mount(root, ctx) {
      // ctx = { width, height, background, pixelRatio, random }
      // draw into `root`; honour ctx.pixelRatio or the 2x capture is wasted
    },
  },
];
```

Written once per project, reused by every later review. `GET /api/review/targets` lists them.

Isolate mode navigates to a **same-origin 404** rather than the game's index page, then clears the
document — the module import resolves, and there is no game to tear down. `__review.reset()` also
clears the pending rAF map, or the game keeps animating underneath the thing being reviewed.

## 4b. Triggering the subject

An effect that only plays on a trigger is invisible to any reviewer that merely loads the page —
however good its timing is. Measured on a PlayCanvas scene whose fireball only fires when cast:
without a trigger, `ink` is **0.000 on every frame** and the tool reports `NOTHING VISIBLE`.

Three ways in, weakest cooperation first:

| | needs from the game | use when |
|---|---|---|
| `"js": "pc.app.fire('vfx:cast')"` | **nothing** | first contact; the agent already has the code graph and can write the expression |
| `"action": "fireball"` | a registry entry | the effect is worth a stable name |
| `"input": [{"at": 200, "type": "eval", "js": "…"}]` | nothing | the trigger has to land at a specific moment |

```js
window.__review.actions = {
  fireball: { note: 'impact burst at origin, ~1.15s', run: () => app.fire('vfx:cast') },
  reset:    { note: 'clear any live effect',          run: () => app.fire('sandbox:reset') },
};
```

### How much does the trigger actually buy you?

Only for a **trigger-gated** effect — and the fair test matters, because the obvious demo stacks
the deck. Same PlayCanvas scene, same tool, two builds:

| effect | fixed times, no trigger | with the trigger |
|---|---|---|
| fires only when cast | `ink 0.000` every frame, `NOTHING VISIBLE` | full arc, peak 24.9 % at t=0.40 s |
| loops on its own | **already caught it** — peak 18.8 % at t=1.00 s | peak 16.0 % at t=1.06 s |

So the trigger is essential for a spell and worth nothing for an ambient loop. Most game effects
are the first kind, which is why it is worth building — but a before/after shown only on the first
kind overstates the case.

That second row also exposed a bug: `auto_plate` derives the plate by dropping the trigger, which
is silently wrong when the effect is **not** trigger-gated — the "clean" run still contains the
fireball, so diffing cancels part of what is being measured and coverage reads low (16.0 % against
a probe reading of 23.7 %). Plate self-motion separates the two cases cleanly — 0.000 when the
effect is gated, 0.032 when it auto-plays — so a plate that moves is now discarded, with a
`PLATE DISCARDED` finding rather than a quietly wrong number.

**Why this beats `isolate` for a heavy runtime.** PlayCanvas, Babylon and Unity WebGL cannot be
rebuilt cheaply in a bare harness, and rebuilding them reviews the asset in a vacuum anyway — no
game camera, no post-processing, no real lighting. Firing the effect where it lives is cheaper and
more faithful. For a 2D canvas effect that mounts in twenty lines, `isolate` is still simpler and
gives a genuinely clean backdrop.

### The warm tab

`"session": "<key>"` keeps the page alive between reviews. With a `reset` action the loop becomes
reset → fire → probe → capture, with no reload and no warm-up:

```
cold  (reload + warm-up each time)   2.20s   1.35s   1.34s
warm  (session key, reset + refire)  1.34s   0.36s   0.37s
```

That is the loop you live in while tuning one effect. Tabs are keyed, idle-reaped after 15 minutes,
and die with the browser.

### The clip

`"clip": true` writes a deterministic animated WebP beside the sheet.

**It is for a person, not for the reviewer.** A model reads images; handing it a video only means
decoding back to frames, which is what the sheet already is. What the clip adds is that a human can
watch the effect — and because the clock is driven, two runs produce identical files, which no
real-time screen capture can offer. WebP because it is 24-bit (a fireball's gradients survive) and
needs no new dependency; GIF would band it to 256 colours, MP4 would need ffmpeg.

The clip and the sheet come from **one pass**: capture densely, encode all of it, pick the sheet
frames out of the same set. Measured: 42 frames, 181 KB, no extra boot.

## 5. Output

### The contact sheet

One image per review: a labelled grid of frames, then the **same frames again at 96 px** — the size
they actually ship at.

That second row is the reason the sheet exists. Art that reads beautifully at 600 px routinely
disappears at gameplay scale, and no single hero render will ever say so.

### Metrics (`numpy`, per frame)

| field | meaning |
|---|---|
| `fill` | fraction of pixels differing from the page's own **backdrop** — *what is drawn* |
| `ink` | fraction of pixels differing from the **plate** — *what changed* |
| `lum_mean`, `lum_max` | Rec. 709 luminance |
| `bbox` | normalised silhouette box `[x, y, w, h]` |
| `palette` | top 4 colours, quantised to a 32-step grid |
| `motion` | mean absolute difference against the previous frame, measured **inside the drawn region** |

#### Why there are two coverage numbers

`ink` answers *what changed*, which is the right question for an effect and the wrong one for a
scene. On a still camera the per-pixel median plate contains the entire world, so `ink` collapses
to near zero however good the picture is. The first version of this tool reported that as
`NOTHING VISIBLE — check the target actually renders`, on a frame that was 21 % full of ships.
An agent reading that goes hunting a rendering bug that does not exist.

`fill` measures against the backdrop the browser really composites the canvas onto — the shim
walks up from the canvas to the first ancestor with an opaque `background-color` and reports it in
`shape.backdrop`. It does not care whether anything moves, so it is the number that says *yes,
this renders*. Measured on a Three.js space scene: `fill 0.213`, `ink 0.0016`.

`motion` is averaged over the union of the drawn regions of this frame and the last, not over the
whole frame. Four ships drifting a few pixels across a black 1280×800 screen move a vanishing
fraction of all pixels; whole-frame averaging reported that real movement as `0.0005` and tripped
the `STATIC` verdict. Region-averaged it reads `0.0021`, above the threshold, and the false
verdict is gone.

### The plate

`ink` is only as good as what it is compared against. Reading the backdrop from pixel (1,1) is
right in isolate mode, where the backdrop was chosen, and close to worthless in a real scene: on a
game whose corner is sky and whose field is a static painted backdrop, `ink` measures *how much of
this picture is not sky* — a large number that barely moves.

Three sources, best first:

1. **An explicit plate.** Pass `plate` — a set of overrides merged onto the same spec — and it is
   rendered first: the same scenario with the weapon holstered, the effect target swapped out. The
   real run is then diffed against a scene that genuinely lacks the thing under review.
2. **Per-pixel median** across the sampled frames. Whatever persists is background, whatever is
   transient is signal. Needs no cooperation from the game.
3. **Corner pixel**, only when there are fewer than three frames.

**A scrolling scene has no plate.** When everything moves, the median is a blur and coverage reads
low for real effects. The tool says so rather than reporting a number it cannot stand behind, and
points at `motion` instead.

**A still scene has the opposite problem**: the median *is* the scene, so `ink` reads ~0 by
construction. That is not a failure to detect anything — it is the plate working exactly as
designed on a question it was not built for. The tool now says `STILL SCENE`, quotes `fill`, and
names the two ways to get a real effect measurement (`action`/`js` to fire it, or
`plate`/`auto_plate` for a genuine clean shot).

### Canvas or page

The canvas is preferred where the canvas IS the picture: it is the real framebuffer, needs no
compositor round trip, and keeps its own resolution. That assumption breaks on a game whose
interface is HTML laid over a canvas — grabbing the canvas there returns the map with every
panel, button and menu missing, and the reviewer never learns what the player sees.

So the shim measures it. `domShare()` samples a grid of viewport points with
`elementFromPoint` and reports the fraction that land on something other than the canvas.
Above 0.1 the page is photographed instead, which composites HTML and canvas together;
at or below it the canvas is grabbed as before. The figure is returned as `shape.dom_share`,
so the decision is visible rather than mysterious. Measured: a PlayCanvas effect scene reports
`0` and keeps the canvas path with its metrics unchanged; a DOM strategy game reports `1` and
is photographed whole.

A canvas that is **blank** — fully transparent, or one flat colour — is also skipped, because an
empty canvas layer behind an HTML interface produced a pure black frame while `fill` called it
100 % drawn. A frame that is black in every sample now says `BLANK` outright.

### Cell size, and why the UI used to be unreadable

Every frame shares one pixel budget, so asking for more moments makes each one smaller. That is
the right trade for an effect and the wrong one for an interface: a HUD label at half size is a
smear, and the reviewer then judges art it cannot actually read.

`_CELL_MAX` used to cap a cell at 700 px, so even a one-frame sheet showed a 1280 px capture at
roughly half size — and the vision model then shrank the whole sheet again. Two downscales before
anyone looked at it. The cap is now 1600; a cell is still never upscaled past its own frame, and
the pixel budget still bounds the sheet, so this only stops a single frame producing an absurd
canvas. Measured for a 1280x800 capture:

| frames | quality | cell | vs native |
|---|---|---|---|
| 1 | normal | 1280 px | 1.0x |
| 2 | normal | 990 px | 1.3x |
| 3 | normal | 780 px | 1.6x |
| 6 | normal | 570 px | 2.2x |

The six-frame effect layout is unchanged. When the shrink reaches 1.4x the run says
`SMALL IN THE SHEET`, quotes both numbers, and names the three ways out: fewer `times`,
`quality: high`, or the `peak` file which is always written at full size.

### Missing files

A stylesheet or script that fails to load changes everything about how a page renders and is
invisible in the picture — the page simply looks wrong. One `error` listener in the shim records
them, and the run reports `MISSING FILE: the page asked for ui.css and did not get it`. Found on
a real project whose `index.html` linked a stylesheet that had never been written.

### The cache

The browser outlives a single review. Chrome heuristically caches ES modules served without
cache headers, so a second review of the same file could be handed the first run's code and
report that an edit changed nothing. Every review session sets
`Network.setCacheDisabled(true)` — a review must look at what is on disk now.

### Findings (plain sentences)

- `peak coverage 33.2% at t=0.85s`
- `NOTHING VISIBLE — under 0.5% of the frame is drawn at any sampled time` (decided by `fill`)
- `STILL SCENE — the frame is 21.3% drawn, but little changes between samples …` (a good picture
  with a still camera; says to read `fill`, and how to measure an effect if that is the intent)
- `STATIC — consecutive frames are near-identical`
- `still going at the last sampled time — extend times to see it finish`
- `the scene never rests … trust motion here, or pass plate to diff against a clean shot`
- `the canvas is still 300x150, the browser's default size — raise warmup_ms`

**Read these first.** They catch a dead or static effect for zero image tokens.

### Peak frame

The busiest frame, kept at full capture resolution as a separate file, and only written when the
sheet actually had to shrink it. The sheet answers *does the arc work*; judging craft — edge
quality, banding, a font that does not hint — needs the pixels the sheet gave up.

## 5b. Finding the moment instead of guessing it

Fixed sample times mean hoping the explosion lands in one of six frames. `auto_times` scouts first:

```js
probe(dt, n) {        // steps the clock, measures in-page, transfers no image
  const ref = snap(); // the resting scene: a clean plate, for free
  for (let i = 0; i < n; i++) { this.step(dt); out.push({ t, ink, motion }); }
}
```

A full frame costs ~9 ms to read back over CDP. Measuring inside the page at 96 px costs a fraction
of a millisecond, so 60 samples are affordable where 60 captures are not. The driver then finds the
peak, finds the event's **extent** (where activity rises past a quarter of peak and falls back), and
spends the expensive captures across that window with the peak guaranteed to be one of them.

The probe advances the clock, so the page is reloaded and warmed again before the real captures.
That doubles the warm-up — measured 8.3 s versus 5.5 s — which is why it is opt-in.

Measured on a scrolling 2D game: probe found the peak at t=1.74 s and the event spanning
0.21–1.80 s; the chosen times were `[0, 210, 528, 846, 1164, 1482, 1740]` and coverage peaked at
23.1 % on exactly the frame the probe predicted.

## 5c. Readiness, and whose clock steps

**The game may raise a hand.** A fixed warm-up is a guess, and a short guess photographs a loading
bar. A game that sets `window.__reviewReady = true` — or a function returning true — when its assets
are in and the first real frame is drawable removes the guess. The warm-up polls it and stops early;
a game that never opts in returns `null` and rides the fixed wait out. Zero-config still works.

**The game may step itself.** If the page exposes `window.__reviewStep(ms)`, the driver calls that
before draining the frame queue. A game that already has a pause and a debug hook knows its own
update order, and covers timers this shim deliberately does not fake — a `setInterval`-driven
subsystem steps correctly through its own stepper and would not through the clock shim alone.

This is a **preference, not a replacement**. The clock shim stays the default, because a Studio-wide
tool has to work in a workspace on day one with no game cooperation at all — that is what makes
14/14 workspaces render today. When the game does offer a stepper, using it is strictly better.

## 6. Sizing model

The first version used a fixed 300 px cell. It downscaled a 720 px capture by 2.4× into a 1250 px
sheet — half the resolution a vision model can use. It paid for a small image **and** threw the
craft away.

Three rules now:

1. **Capture at 2× device pixels, present at 1×.** Real antialiasing instead of a soft resize.
2. **Never upscale.** A cell is never wider than the frame; upscaling costs pixels and invents
   detail that was never rendered.
3. **Budget by area, not by long edge.** A vision model bills an image by pixel count, so a tall
   sheet and a wide one of the same area cost the same. Capping the long edge punished portrait
   layouts for no reason and let a 2-column sheet quietly cost twice a 4-column one.

```python
QUALITY = {"draft": (600_000, 1), "normal": (1_700_000, 2), "high": (3_300_000, 2)}
```

Layout search picks the widest cell whose sheet fits the area budget, preferring more columns on a
tie — plus an aspect rule, because maximising cell width alone collapsed the sheet into a single
unreadable 590×2395 column. The sheet must be at least as wide as it is tall; if nothing satisfies
that, the rule is dropped rather than failing.

Measured, 5 frames of a 420×300 request:

| quality | capture | sheet | ≈ image tokens |
|---|---|---|---|
| draft | 420×300 | 880×664 | 780 |
| normal | 840×600 | 1630×1020 | 2 200 |
| high | 840×600 | 2140×1264 | 3 600 |

N reviewers share one sheet, and a shared sheet sits in the cached prompt prefix, so `normal` is
the honest default.

## 7. Renderer choice

Chrome, headless, driven over raw CDP. **No Playwright, no Puppeteer, no browser download.**

`websockets` and `httpx` were already installed for the API; `Pillow` and `numpy` were already
there for asset work. The Chrome path comes from a resolver the app already had. Net new
dependencies: **zero.**

One browser is kept alive across reviews and reaped after 10 minutes idle. Concurrency is capped at
2 tabs.

### On forcing the GPU

A common recommendation is to pass `--enable-gpu --ignore-gpu-blocklist`, and to force
`--use-angle=swiftshader` when there is no GPU. Measured here:

```
current flags        ANGLE (NVIDIA, GeForce RTX 2070 SUPER)   draw 0.4ms  grab  9.0ms
+ forced GPU flags   ANGLE (NVIDIA, GeForce RTX 2070 SUPER)   draw 0.4ms  grab  8.7ms
+ angle+swiftshader  ANGLE (Google, Vulkan SwiftShader)       draw 0.3ms  grab 98.0ms
```

Headless Chrome already picks the real GPU. `--enable-unsafe-swiftshader` is a **permission** to
fall back to software, not an instruction to use it — it is what stops a 3D review returning black
on a machine with no usable GPU. Forcing software is **11× slower** on frame read-back.

Note also that **read-back dominates**: 0.4 ms to draw, 9 ms to `toDataURL`. A 30 fps interactive
loop has a 33 ms budget, so the capture mechanism is not the constraint — the model call is.

Software rendering is exposed as a switch anyway, for one real case: leaving the GPU free while a
local model is resident.

## 8. Integration

**HTTP** (`backend/asset_studio/routers/review.py`)

```
GET  /api/review/status
POST /api/review/render
GET  /api/review/targets?project=<abs path>
POST /api/review/stop
```

**The prompt.** The capability is useless if the agent does not know it exists, so a note is
appended to the session system prompt **and to every subagent's system prompt**. Subagents do not
inherit the session prompt, and a delegated reviewer that does not know about this silently spawns
its own browser — which is the exact fan-out the feature is meant to remove.

The note carries a runnable `curl` with the project path baked in, forward-slashed because the path
goes inside JSON where a Windows backslash is an escape.

It is gated twice: on the user setting **and** on Chrome actually being present. Describing a tool
the agent cannot run wastes tokens and invites it to try anyway.

**Settings → Planning & review**

- `cc_review` — on/off
- `cc_review_quality` — draft / normal / high
- `cc_review_gpu` — GPU or software (changing it relaunches the browser, since flags are fixed at
  launch)

## 9. Evidence

- **14 of 14 game workspaces render**, ~3.3 s each, no engine-specific code: 2D canvas, Three.js,
  PixiJS-style loops, and one DOM-only site.
- **Determinism**: 2D and WebGL both produce 4/4 distinct frames, byte-identical across two runs.
- **The tool caught three of the author's own bugs**: the loading-screen capture (found by the
  `STATIC` finding), black WebGL output (`preserveDrawingBuffer`), and the 2.4× downscale.

## 10. Known limits — where to attack this

Listed because a reviewer should not have to find them.

1. **`setTimeout` / `setInterval` are not driven.** A game whose main loop is `setInterval`-based
   will not step. Faking them was considered and rejected: it deadlocks page loads that await real
   timers. A safe opt-in flag is the obvious next move.
2. **Web Workers and `OffscreenCanvas` are not covered.** `addScriptToEvaluateOnNewDocument`
   applies to documents and frames, not worker global scopes. A game that renders from a worker
   gets the screenshot fallback at best, and `toDataURL` on a transferred canvas throws.
3. **`crypto.getRandomValues` is not seeded.** Only `Math.random` is. A game using the crypto API
   for spawn variance stays non-deterministic.
4. **The network is real.** A game fetching remote data is only as reproducible as that endpoint.
5. **Warm-up is still a heuristic when the game does not opt in.** `window.__reviewReady` removes
   the guess for cooperating games; everything else rides a fixed 1200 ms.
6. **A scrolling scene has no clean plate.** The median is a blur when everything moves, so
   coverage reads low. Mitigated — the tool detects it and says so — but not solved. `plate` solves
   it when the caller can produce one.
7. **Isolate mode needs a mountable asset.** Effects coupled to global game state resist, and the
   `ctx` stub only goes so far.
8. **No audio, no game feel, no balance.** This reviews what is drawn. Judging whether a fight is
   fun needs play, not frames.
9. **Concurrency is in-process** (`threading.Semaphore(2)`). Two backends would fight over one
   browser port range.
10. **Cross-machine determinism is not guaranteed.** Same machine, yes. GPU drivers differ, so a
    sheet from another machine may differ by a pixel. SwiftShader mode is the deterministic one, at
    11× the read-back cost.
11. **`times` is capped at 16 samples** per review.
12. **Putting the right thing in front of the camera is not solved here, and cannot be.** Freezing
    time photographs the wrong subject very accurately. If the enemies already reached the wall,
    there is no arrow in flight to photograph at any moment. The scenario — a test range that
    stages the situation — is game-specific work. `review/targets.js` is the hook for it: a target
    is a *scenario*, not just an asset, and `mount` may set up whatever situation the review needs.
13. **Still no closed-loop mode.** The warm tab plus `js`/`action` gets close — an agent can fire,
    look, adjust and fire again in ~0.4 s — but inputs within a single review are still scripted up
    front. A true look-decide-act loop remains a session API.
14. **`js` runs arbitrary JavaScript** in the page. Fine for a game you own; not something to point
    at a URL you do not control. Inputs are scripted up front. An agent that wants to *play* — look,
    decide, act, look again — is not served. The numbers in §7 say the mechanism supports it; it is
    roughly a session API (open / step / input / grab / close) on the existing browser.

## 10b. Credit

Points 1–4 in the limits list above were sharpened by an outside review that independently found
the corner-pixel background and the fixed warm-up, and proposed the probe-then-capture pass and the
game-supplied stepper. All four are now implemented. That review also flushed out a real bug: the
probe pass reloaded the page and warmed it again **without replaying the inputs that skip the title
screen**, so `auto_times` was quietly reviewing the menu. Warm-up is now a single helper used by
both passes.

## 11. File map

| file | lines | what |
|---|---|---|
| `backend/asset_studio/review.py` | 817 | shim, browser lifecycle, CDP client, capture, metrics, sheet |
| `backend/asset_studio/routers/review.py` | 56 | the four endpoints |
| `backend/asset_studio/cc_session.py` | +50 | `_review_note`, `_review_on`, session + subagent wiring |
| `backend/asset_studio/config.py` | +12 | three settings |
| `backend/asset_studio/main.py` | +2 | router mount |
| `frontend/src/pages/Settings.tsx` | +45 | the controls |
| `frontend/src/api/client.ts` | +3 | status type |

Dependencies used, all pre-existing: `websockets` 14.1, `httpx` 0.28.1, `Pillow` 11.1.0,
`numpy` 2.2.1.

## 12. Running it

```bash
curl -s -X POST http://127.0.0.1:8777/api/review/render \
  -H 'content-type: application/json' \
  -d '{"project":"C:/path/to/game","mode":"scene","label":"boss fight",
       "warmup_ms":2500,"times":[0,200,500,900,1400],
       "input":[{"at":-400,"type":"key","key":" ","code":"Space","keyCode":32}]}'
```

Returns:

```json
{
  "ok": true,
  "sheet": "…/data/review/<project>/boss-fight-<ts>.png",
  "peak": "…/boss-fight-peak-t900ms.png",
  "capture": [1120, 680],
  "times": [0, 200, 500, 900, 1400],
  "findings": ["peak coverage 33.2% at t=0.90s"],
  "metrics": [{ "ink": 0.33, "lum_mean": 84.9, "bbox": [], "palette": [], "motion": 0.02 }],
  "notes": ["the running game, driven by a stepped clock", "warmed up 2500ms …"]
}
```

Read `findings` and `metrics` first. Then open `sheet` as an image. Open `peak` only when the sheet
raises a question that detail can settle.

---
name: web-build-weight
description: "Use when a web game loads slowly or must be cut to fit a portal size limit (CrazyGames, Poki, a playable ad). Measures the build and the frame first, then applies the one fix the numbers point at."
disable-model-invocation: true
metadata:
  category: game
---

# What the build weighs

**Measure first. Every time.** The most common mistake here is compressing the wrong thing: an
agent Dracos a 60 KB mesh while a single 4096×4096 PNG holds 90% of the download.

Unity ships a first-party skill for exactly this job on their engine. The method transfers; the
tools do not. These are ours.

---

## 1. What does it actually weigh?

```bash
# Biggest files in the build, largest first
find dist -type f -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -20

# What one model is made of — geometry vs textures vs animation, per mesh
gltf-transform inspect path/to/model.glb
```

`inspect` is the important one. It reports whether the scene is geometry-heavy or texture-heavy,
and how many draw calls it will cost. Read that before touching anything.

**Write the starting numbers down.** Total bytes, biggest single file, triangles, draw calls.
Without them you cannot say whether you helped.

---

## 2. What does the frame cost?

The game must be running. Then:

```bash
curl -s --get 'http://127.0.0.1:8777/api/live/perf' --data-urlencode 'project=<abs path>'
```

Frame cost, **draw calls**, triangles and GPU objects, read out of the live page. Works for
PlayCanvas, three.js, Babylon, Phaser, PixiJS, Cocos and plain canvas 2D.

Draw calls matter more than triangles on a phone. 200 objects of 100 triangles is slower than
1 object of 20,000.

---

## 3. Fix the thing the numbers named

Take the row that matches what you measured. **One change, then measure again.**

| The numbers say | Do this |
|---|---|
| One texture is most of the download | Resize it. 2048 is plenty for a hero, 512 for a prop, 256 for a background object |
| Many textures, many materials | `gltf-transform palette in.glb out.glb` — merges materials into a palette texture |
| Too many draw calls | `gltf-transform join in.glb out.glb`, and `instance` for anything repeated |
| Geometry-heavy | `gltf-transform simplify in.glb out.glb --ratio 0.5 --error 0.001` |
| Geometry still heavy after simplify | `gltf-transform meshopt in.glb out.glb` — better than Draco for load time, and needs no WASM decoder in most engines |
| Unsure, and it is not a hero asset | `gltf-transform optimize in.glb out.glb` — all methods, then check it still looks right |
| Duplicate meshes and textures | `gltf-transform dedup in.glb out.glb` |
| Unreferenced junk from an export | `gltf-transform prune in.glb out.glb` |
| Precision far beyond what is visible | `gltf-transform quantize in.glb out.glb` |

**Caution: compression can break an engine that was not set up to decode it.** The Studio's
own `/api/engine/model` endpoint takes Draco and meshopt back out again for exactly this reason.
If the game is a PlayCanvas or three.js build that has no decoder configured at startup, the
model arrives as a WebAssembly error about a magic word. Prefer `meshopt`, and test the running
game before you keep the change.

---

## 4. Audio is usually second

Audio is the file type people forget. Music at 320 kbps stereo in a browser game is waste.

- Music and long ambience: mono or joint stereo, 96–128 kbps, streamed rather than decoded whole
- Short effects: keep them short, 64–96 kbps, decoded once and reused
- Never ship WAV in a web build

---

## 5. Prove it, do not claim it

After the change, run **the same two measurements** from steps 1 and 2 and report both:

```
before : 14.2 MB, biggest 6.1 MB (atlas.png), 214 draw calls, 41 fps
after  :  4.8 MB, biggest 1.4 MB (atlas.png),  38 draw calls, 60 fps
```

Then **look at it**, because a size win that ruined the art is not a win:

```bash
curl -s -X POST http://127.0.0.1:8777/api/review/render -H 'content-type: application/json' \
  -d '{"project":"<abs path>","mode":"scene","label":"after the size pass","times":[0,600,1400]}'
```

Read `findings` and `metrics` first. Open the sheet only if a number raises a question.

---
name: three-cinematic
description: The Three.js cinematic art-pass playbook — renderer flags, lighting rigs, fog+sky, contact shadows, bloom, instancing, wind sway, with exact values. Use when building or polishing visuals in any Three.js game.
category: studio-game
disable-model-invocation: true
metadata:
  created: 2026-07-18
  updated: 2026-07-18
---

# THREE-CINEMATIC — the art-pass playbook for vanilla Three.js

Drop this file into any Three.js project. **Claude: when building or polishing visuals in this
project, follow this document.** It ports the "react-three-fiber / drei / postprocessing look"
(the modern webgl-art aesthetic) to plain Three.js — no React required. Every rule here changes
the rendered result; apply them all unless the game's style explicitly conflicts.

Assume Three r150+ (`three/addons/…` import paths). Target: browser games (Poki-class perf).

---

## 0) The order of operations (the "art pass")

Do these in order — each multiplies the ones before:

1. Renderer flags (tone mapping + color space + shadows) — 30 seconds, biggest single jump
2. Lighting rig (hemisphere + key + environment) — makes materials believable
3. Fog + sky as ONE system — instant depth and atmosphere
4. Shadow quality tuning — kills the "floating objects" look
5. Material discipline (palette, roughness, vertex colors) — cohesion
6. Post-processing (bloom + vignette) — the "finished" feel
7. Detail density via instancing (grass/rocks/props) + motion (wind sway)
8. Screenshot → self-judge → iterate (see §9)

---

## 1) Renderer — non-negotiable flags

Without these, nothing else in this file looks right (colors wash out, lights blow out):

```js
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));   // >2 wastes GPU for zero visible gain
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;      // THE cinematic curve
renderer.toneMappingExposure = 1.1;                      // 1.0–1.3; raise before adding lights
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;        // soft edges, no extra cost worth caring
```

Gotcha: with ACES, colors you pick look slightly darker/desaturated → author colors ~10% brighter
than the target. Never "fix" washed-out colors by disabling tone mapping.

## 2) Lighting rig — the 3-part recipe

One `AmbientLight` = flat and dead. Use this rig every time:

```js
// A. Hemisphere: sky tint from above, ground bounce from below — free "GI feel"
const hemi = new THREE.HemisphereLight(0xbfd6ff /*sky*/, 0x4a3f35 /*ground*/, 0.6);
scene.add(hemi);

// B. Key light (the sun): the ONLY shadow caster in most scenes
const sun = new THREE.DirectionalLight(0xfff2df, 2.2);
sun.position.set(18, 30, 12);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
const s = 28;                                            // fit to playfield! tight = crisp shadows
sun.shadow.camera.left = -s; sun.shadow.camera.right = s;
sun.shadow.camera.top = s;   sun.shadow.camera.bottom = -s;
sun.shadow.camera.near = 1;  sun.shadow.camera.far = 90;
sun.shadow.bias = -0.0001;
sun.shadow.normalBias = 0.02;                            // fixes shadow acne on lowpoly
scene.add(sun);

// C. Environment map: what drei's <Environment> does — makes PBR materials come alive
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
// r163+: scene.environmentIntensity = 0.55;   older: per-material material.envMapIntensity = 0.55
```

Optional rim/fill: a second `DirectionalLight` (0.4 intensity, opposite side, `castShadow=false`)
separates characters from the background. Never give two lights shadows unless you must.

Even better: replace RoomEnvironment with a real HDRI (`RGBELoader` + a 2k .hdr from Poly Haven,
CC0) as `scene.environment` — biggest material-quality jump available for one file.

## 3) Fog + sky = one system (the cheapest depth you'll ever buy)

The trick: **fog color must equal the horizon color** so geometry melts into the sky.

```js
const horizon = new THREE.Color(0xcfd9e8);       // pick from your palette!
scene.fog = new THREE.Fog(horizon, 35, 170);     // linear: start past the playfield
// dense/moody alternative: new THREE.FogExp2(horizon, 0.012)

// Gradient sky dome (drei <Sky>'s cheap cousin) — top color ≠ horizon color
const sky = new THREE.Mesh(
  new THREE.SphereGeometry(400, 24, 12),
  new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: new THREE.Color(0x5e8fd8) }, bot: { value: horizon } },
    vertexShader: `varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `uniform vec3 top,bot; varying vec3 vP;
      void main(){ float h=normalize(vP).y*.5+.5; gl_FragColor=vec4(mix(bot,top,pow(max(h,0.),.7)),1.); }`,
  })
);
scene.add(sky);
```

## 4) Grounding objects — contact shadows

Objects "float" without a dark anchor. Two tiers:

- **Cheap blob (always do this under characters/props):** a radial-gradient canvas texture on a
  ground-hugging plane — `MeshBasicMaterial({ map: blobTex, transparent: true, opacity: 0.35,
  depthWrite: false })`, `rotation.x = -Math.PI/2`, `position.y = 0.01`. Scale ≈ object footprint × 1.4.
- **Real ground shadow:** ground mesh with `receiveShadow = true` and a `MeshStandardMaterial`
  (NOT Basic — Basic ignores light). If ground must be unlit-styled, add a `ShadowMaterial`
  plane on top: `new THREE.ShadowMaterial({ opacity: 0.3 })`.

## 5) Material discipline

- **Palette first:** pick 4–6 colors before modeling (e.g. via a palette from a game you admire).
  Slightly desaturate everything (pure #ff0000-class colors scream "programmer art"). Feed the same
  palette into hemi light tints, fog/horizon and sky.
- **Lowpoly style:** `MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.85, metalness: 0 })`
  — one material, many colors via vertex colors = 1 draw call per instanced batch.
- **Roughness defaults:** matte props 0.8–0.95 · skin/cloth 0.9 · metal 0.25 rough + 1.0 metal ·
  water/glass via `transmission`. Never leave everything at the default 1.0/0.0.
- **Emissive accents:** tiny emissive bits (windows, crystals, eyes) `emissiveIntensity 2–6` — these
  are what bloom (§6) turns into magic.

## 6) Post-processing — the finish

Vanilla addons chain (no new deps):

```js
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass }     from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass }     from 'three/addons/postprocessing/OutputPass.js';

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.35, 0.5, 0.85);
composer.addPass(bloom);                    // strength, radius, threshold — start EXACTLY here
composer.addPass(new OutputPass());
// render loop: composer.render() instead of renderer.render()
```

Rules: threshold ≥ 0.85 so only emissives/highlights bloom (full-screen glow = mud). Add a subtle
vignette (the `postprocessing` npm package's `VignetteEffect`, or a fullscreen shader) — 0.25
darkness reads as "finished". SSAO (N8AO) only on desktop-class GPUs; skip for Poki default.
If GPU-poor: drop composer entirely — §1–§5 still carry the look.

## 7) Density + motion (what makes screenshots look "expensive")

- **InstancedMesh for everything repeated** — grass tufts, rocks, trees, fences:

```js
const grass = new THREE.InstancedMesh(bladeGeo, bladeMat, 800);
const m = new THREE.Matrix4(), c = new THREE.Color();
for (let i = 0; i < 800; i++) {
  m.compose(scatterPos(i), randomYRotation(), randomScale(0.8, 1.3));
  grass.setMatrixAt(i, m);
  grass.setColorAt(i, c.setHSL(0.29 + Math.random() * 0.04, 0.5, 0.35 + Math.random() * 0.12));
}
grass.castShadow = true;
```
  Per-instance color/scale/rotation jitter is MANDATORY — uniform clones read as fake instantly.

- **Wind sway** (foliage/grass) via `onBeforeCompile` on the shared material:

```js
mat.onBeforeCompile = (sh) => {
  sh.uniforms.uTime = uniforms.uTime;      // update in the render loop
  sh.vertexShader = `uniform float uTime;\n` + sh.vertexShader.replace(
    '#include <begin_vertex>',
    `#include <begin_vertex>
     float sway = sin(uTime * 1.6 + position.x * 0.5 + position.z * 0.7) * 0.06;
     transformed.x += sway * smoothstep(0.0, 1.5, position.y);`);  // only the tops move
};
```

- **Idle camera life:** a barely-there sway (`camera.position.x += sin(t*0.3)*0.02`) or slow drift
  makes menus/screenshots feel alive.

## 8) Performance guardrails (Poki-class)

- Pixel ratio clamp 2 (§1) · shadow map ≤ 2048 (1024 on mobile) · ONE shadow-casting light
- Instancing/merging until draw calls < ~200 (`renderer.info.render.calls` — check it!)
- Fog is free · bloom ~1–2 ms · SSAO expensive (opt-in only)
- Textures ≤ 1024², power of two; prefer vertex colors over textures for lowpoly

## 9) Verify like an artist, not a compiler

After the pass, **screenshot the real game and judge it** — don't trust "it runs":

- In Asset Studio projects: `python "<STUDIO>\backend\asset_studio\tools\browse.py" http://localhost:PORT --out shot.png --wait 2500 --console` (`<STUDIO>` is wherever the Asset Studio folder sits on this PC) — then READ the image and check: Does it have depth (fog)? Are objects grounded (shadows)? Is there one palette? Does anything glow? Any console errors?
- Iterate: one change per screenshot. Typical fixes, in likelihood order: fog missing/wrong color →
  exposure too low → shadows absent on ground → colors clashing → everything same roughness.

## 10) No-go list

- `AmbientLight` alone · default tone mapping (`NoToneMapping`) · pure-saturated default colors
- `MeshBasicMaterial` for anything lit (it ignores ALL of §2)
- Two+ shadow-casting lights without a reason · bloom threshold < 0.7 · pixelRatio unclamped
- Shadows enabled but ground not `receiveShadow` (the #1 "why does it float" bug)

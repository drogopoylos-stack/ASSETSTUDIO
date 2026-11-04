---
name: local-image-to-3d
description: "Use when the user asks to turn a picture into a 3D model / GLB with the free local models - Pixal3D or TRELLIS.2 (ComfyUI), 'like MiniMax/Meshy but local', 'image to 3D', 'make a 3D model of this image', or to find / add a model in our own 3D model library (Catalog). One Studio job: reference image in, textured PBR GLB out, filed by category."
metadata:
  category: 3d
  created: 2026-10-15
  updated: 2026-10-21
disable-model-invocation: true
---

# Local image → 3D (Pixal3D / TRELLIS.2 in ComfyUI)

**One image in, one textured GLB out, through one Studio job.** Asset Studio ships the ComfyUI
template "Pixal3D & TRELLIS.2: Image to Model" as two presets of its `comfyui-3d` provider.
The job starts ComfyUI by itself. Free, local, on the RTX 4070 Ti SUPER (16 GB).

Use it ONLY when the user asks for a generated 3D model from an image. A request to build or
improve an asset in code is not such a request.

## Run it

```sh
# 1. start the job (the image path must be absolute)
curl -s -X POST http://127.0.0.1:8777/api/jobs -H 'content-type: application/json' -d '{
  "stage": "gen3d", "provider_id": "comfyui-3d",
  "params": {"preset": "pixal3d", "faces": 200000, "texture_size": "2048", "seed": -1},
  "inputs": ["D:/path/to/reference.png"],
  "label": "Viking rune axe", "category": "weapon",
  "owner": "<this session id, as the Studio note gives it>"}'
# 2. poll until "status":"succeeded" (or "failed": read "error")
curl -s http://127.0.0.1:8777/api/jobs/<job id>
# 3. the GLB is outputs[0].path; outputs[0].preview_path is a PNG render of it
```

ALWAYS send `label` (a short name for the model) and `category`. The Studio files every generated
model in its library, `{{STUDIO_ROOT}}\data\library\models\<category>\<name>-<asset id>.glb`,
and the Catalog page lists it on that shelf. Categories: `main-character`, `character` (NPCs),
`enemy`, `creature`, `weapon`, `armor`, `item`, `prop`, `environment`, `building`, `vehicle`,
`other`. Plurals and "Main characters" also work; a new word makes a new shelf. Without a
category the model goes to `unsorted/`.

To find an earlier model: `GET http://127.0.0.1:8777/api/assets?type=model&category=weapon&search=axe`
(newest first; `limit`, `offset`). To add a model made OUTSIDE a job (Blender, a download):
`POST http://127.0.0.1:8777/api/library/import` with `{"path": "<abs .glb>", "name": "...",
"category": "...", "preview_path": "<abs .png, optional>"}`. It copies the file into the library.

| param | values | meaning |
| --- | --- | --- |
| `preset` | `pixal3d` (default choice) · `trellis2` | Pixal3D follows the picture's camera, so the shape matches the reference closely. TRELLIS.2 is the general model. Same pipeline otherwise. |
| `faces` | 5000 – 2000000, default 700000 | faces after decimation. A game prop: 20000–200000. |
| `texture_size` | `"1024"` · `"2048"` · `"4096"` (default) | baked base colour / metal / rough, normal and AO maps |
| `seed` | -1 = random | the same seed and image give the same model |

The pipeline removes the background (BiRefNet) and crops to the object itself, so a plain photo
or concept art works. Best input: ONE object, whole, centred, no cut-off parts.

## Timing (measured 2026-10-15, RTX 4070 Ti SUPER)

- TRELLIS.2, 200k faces, 2048 texture: 125 s with a cold model load, 25.5 MB GLB (194k faces, 4 PBR maps)
- Pixal3D, 200k faces, 2048 texture: 107 s, 24.8 MB GLB. Its mesh keeps the orientation of the picture.
- The first job after a ComfyUI start also loads ~13 GB of weights.
- TRELLIS.2 with a `4096` texture fills the 16 GB card (15.1/16.4 GB) and runs 30–40 min. Use `2048` for trellis2. Pixal3D at 4096 is fine (~14 min at 150k faces).
- A running job blocks the queue. To stop one: `POST /api/jobs/<id>/cancel`.

Poll every 15–30 s; the job allows 40 minutes.

## When it fails

- `value not in list` for a `*.safetensors` name: a model file is missing. All seven must exist
  under `{{STUDIO_ROOT}}\data\vendor\ComfyUI\models\` (both presets validate every loader):
  `diffusion_models/pixal3d_int8_convrot.safetensors` (Comfy-Org/Pixal3D),
  `diffusion_models/trellis_2_int8_convrot.safetensors` (Comfy-Org/TRELLIS.2),
  `vae/trellis_2_shape_vae_bf16.safetensors`, `vae/trellis_2_texture_vae_bf16.safetensors`,
  `clip_vision/dino_v3_L_naf_fp32.safetensors` (Comfy-Org/Pixal3D),
  `geometry_estimation/moge_2_vitl_normal_fp16.safetensors` (Comfy-Org/MoGe),
  `background_removal/birefnet.safetensors` (Comfy-Org/BiRefNet).
  URL: `https://huggingface.co/<repo>/resolve/main/<dir>/<file>`.
- Out of GPU memory: lower `texture_size` first, then `faces`. Do not run two 3D jobs at once.
  TRELLIS.2 needs ~7.4 GiB free on top of its weights ("Currently allocated 19.35 GiB, Requested 7.39 GiB"):
  close Blender / Cycles and other GPU users before the job, then send it again (verified 2026-10-21).
- `ComfyUI reported an error`: read `GET http://127.0.0.1:8777/api/services/comfyui/logs?lines=60`.

## Where it lives (for a change)

- Presets: `backend/asset_studio/providers/comfy_workflows/{pixal3d,trellis2}.json` (API format,
  tokens `%image%`, `%seed%`, `%faces%`, `%texture_size%`). A file of the same name in
  `data/comfy_workflows/` overrides the shipped one.
- Provider: `backend/asset_studio/providers/threed/comfy_workflow.py`; catalog:
  `providers/comfy_common.py`.
- Library filing: `backend/asset_studio/library.py` (shelves, aliases, moves), called from
  `jobs/queue.py` when a job saves its assets; the Catalog routes are in `routers/catalog.py`.
- To rebuild a preset from a ComfyUI UI-format template: load the ComfyUI page headless and call
  `await app.loadGraphData(wf, true, true, 'x'); (await app.graphToPrompt()).output`. ComfyUI's
  own frontend does the conversion, including dynamic combos (`sign_mode.qef`) and widget order.

## After the job: finish a character GLB (verified 2026-10-15, Pixal3D, Blender 5.1 headless)

- Run 2–3 seeds and compare front/back/face renders; seeds differ most in eyes and emblems. Pixal3D beat TRELLIS.2 on face likeness for a full-body character sheet.
- The baked base colour comes out darker and more saturated than the reference (mustard #A89654 → #88792B, teal-grey → neutral grey). Fix: k-means the texture, pair each cluster with the reference colour, and do a soft Lab shift per pixel (`w = exp(-d²/2σ²)`, σ≈14, fade far from every cluster). Write the map back as sRGB, `pack()`, re-export.
- Metal/rough map: G = roughness, B = metallic. Workbench makes a matte suit look glossy; judge in EEVEE, view transform "Standard", area lamps about 45/15/20 W.
- Blender MCP may be down: use `blender.exe -b --factory-startup -P script.py -- args`. Ortho turntable camera around Z-up: `(target - cam).to_track_quat("-Z", "Y")`.
- Finish: apply transforms, feet at 0, centre, scale to real height (1.80 m). LOD: Decimate collapse to ~60k faces + `image.scale(2048, 2048)` → 17 MB vs 36 MB, visually the same at full-body size.
- Add finished files to the library with `/api/library/import`.

## Face fidelity: a head-only pass on an existing body (verified 2026-10-20, Pixal3D, Blender 5.1)

The full-body job gives a weak face (the face is ~5% of the picture). Fix: a second job on a HEAD-ONLY crop of the same reference, then graft it.
1. Crop the head + a little collar. Run Pixal3D with 3–4 seeds; pick by a front render of the face.
2. Fit: trimmed ICP with a similarity (Umeyama, scale+rot+trans) of the head mesh onto the body's head region (verts above the shoulders), BVH `find_nearest`, drop the worst 25% of pairs each step (suit and invented hair), 60 iterations. Init: scale to the crop height, top-centre on the body head top.
3. Cut both meshes at one collar plane (tilted to follow the collar); keep head-mesh faces above it, body faces below; drop small loose islands.
4. Pixal3D hair often comes out grey and METALLIC: recolour the hair mask in the texture and set metallic 0. Do a Lab colour transfer of the head skin to the body skin.
5. Do NOT decimate, voxel-remesh or bake the Pixal head. Its mesh is a thin DOUBLE SHEET (40% of faces have an opposite face < 0.8 mm away); only ~20% of faces are visible. The rasterizer hides this, but Cycles bakes give black holes and a voxel remesh comes out inside-out in patches. Decimate also stalls on the welded mesh, and a weld breaks the shading.
   Fix: keep only the visible faces. Per face, cast rays from outside (11 directions in a cone around the normal), pass through back-facing hits (≤ 8), keep the face if the first front-facing hit is that face. 147k → 29k faces, own UVs and texture kept, no bake:
   ```python
   tree = BVHTree.FromBMesh(bm)
   dirs = [(0,0)] + [(r*math.cos(a), r*math.sin(a)) for r in (0.5,1.2) for a in (0,1.257,2.513,3.770,5.027)]
   for f in bm.faces:
       n=f.normal; c=f.calc_center_median(); t=n.orthogonal().normalized(); b2=n.cross(t); ok=False
       for u,w in dirs:
           d=(n+t*u+b2*w).normalized(); o=c+d*0.6
           for _ in range(8):
               loc,hn,idx,dist=tree.ray_cast(o,-d)
               if idx is None: break
               if idx==f.index: ok=True; break
               if bm.faces[idx].normal.dot(d)<0: o=loc-d*1e-5; continue
               break
           if ok: break
       if not ok: drop.append(f)
   ```
   This visible-faces head still shows cracks, red rim-lit back faces and black ink shards in a real-time scene; the
   CLOSED head (next-but-one section) replaces it.
6. Result: head 29k + body 45k = 74k faces, 8 MB (JPEG images, 2048). Limits: invented back hair, eye whites read too bright.
- xatlas has no wheel for Python 3.14: make a venv from a 3.12 Python and `pip install --only-binary :all: xatlas`. (Not needed for the route above.)

## Face likeness: a clean drawn reference + projection back onto the mesh (verified 2026-10-21)

The generator texture of a face reads weak (wide white eyes, speckles), even on a good head mesh. Fix: make a CLEAN reference of the head, generate the mesh FROM IT, then project that same reference back onto the mesh texture.
1. Reference: GPT image with the canon image as input. Ask for head and neck only, the same face, soft even light, no hard shadows, dark irises, off-white (not white) eye whites, transparent background (else #FF00FF), 1024². If the Studio `openai-image` provider has no key, use Codex CLI (image_gen feature on):
   `(cat p_common.txt; cat p_view.txt) | codex exec --skip-git-repo-check -s workspace-write -C <outdir> -i <canon.png> -i <head_crop.png> -`
   It writes the PNG into `<outdir>`; exit code 1 and sandbox errors in its log are harmless — check the file.
2. Pixal3D on that reference (it follows the picture camera, so the mesh lines up with the image). TRELLIS.2 on a busy comic image gave a shattered texture; prefer Pixal3D here.
3. Fit a front ORTHOGRAPHIC camera, glTF metres → source pixels: `u = s*x + bu`, `v = -s*y + bv`. Start from a silhouette bbox match, then search a 2D similarity warp (scale 0.80–1.25, shift ±15%) of ONE render of the mesh by NCC against the reference — coarse at 1/4 size, fine at 1/2. Do not re-rasterise per candidate (1000× slower). NCC ≈ 0.5 is a good fit.
4. Project into the UV texture: for each texel, get the 3D point (barycentric in UV space), project with the camera, bilinear-sample the reference. Weight = `clamp((n_z - 0.10)/(0.50 - 0.10))` × visible (z-buffer of the same camera, tolerance 1% of depth) × reference alpha (eroded 7 px, blurred 2 px). Keep the highest weight per texel; blend over the generator texture. Back and sides keep the generator texture.
5. Generator hair may come out lilac: hue mask (B > G and R > G), replace with the reference hair median (mid-tone pixels only, ink lines excluded) × a luminance factor.
- LIMIT: projection is exact from the projection view and stretches at grazing angles. On a mesh that did NOT come from the same image (an older head), features double from other angles — do not do that.
- A 3/4 reference gives a 3/4 head; its face smears when seen from the face's own front. For a game camera that looks at the front, generate from a FRONT reference.

## Closed head: a clean, light head from a generator head (verified 2026-10-21, Blender 5.1 + three.js)

A generator head is a double sheet with a dark inner sheet, cracks, thin flaps and mixed winding. In three.js it shows
red triangles (a rim light on reversed faces), and an inverted-hull ink grown from it pokes through as black shards.
A plain voxel REMESH keeps the cracks as deep pockets, and a Cycles bake from the generator head gives a black jaw.
Fix = close the shape, then move the colour by nearest point. Result: ~38k head faces, 85k with the body, ~5.8 MB.
1. Morphological closing in Geometry Nodes: Distribute Points on Faces (density 1.5e6/m²) → Points to Volume
   (Resolution Mode "Size" — a menu socket, set by name; voxel ≈ 1.3 mm; radius = close_r 3 mm; density 1) →
   Volume to Mesh ("Grid", threshold 0.3). Keep the biggest shell.
2. Shrinkwrap NEAREST_SURFACEPOINT back onto the high mesh (undoes the 3 mm swell), then relax, decimate to ~40k, xatlas UVs.
3. Colour: transfer from the VISIBLE faces only (the ray-cast filter above), never from the full double sheet.
   Per low texel: 3D point → nearest of 10 barycentric samples per high triangle (KD-tree, k=1) → project onto that
   triangle, clamp the barycentrics → exact UV → bilinear sample. Texels > 2.5 mm from the high surface take the colour
   of the nearest good texel in 3D. EDT-pad the UV gutters. (Sparse samples without the exact projection = blurry.)
4. Project the drawn reference over the front texels again (the previous section). The raw→fit transform is an exact
   affine: least squares on the triangle centroids of the raw and the fitted mesh (same triangle order), error ~0.
5. Ink hull from the closed head, with a view-ray push in the vertex shader so the hull sits behind the skin and only
   the silhouette shows:
   ```glsl
   // after #include <project_vertex>
   mvPosition.xyz *= 1.0 + inkPush / length(mvPosition.xyz);   // inkW 4.5 mm out, inkPush 4 mm (8 mm for deep eye folds)
   gl_Position = projectionMatrix * mvPosition;
   ```
- Rejected: a low head made from the visible sheet only (holes); recomputed winding normals (more red faces);
  a shrunk voxel proxy as the hull (black slivers in front of the skin).

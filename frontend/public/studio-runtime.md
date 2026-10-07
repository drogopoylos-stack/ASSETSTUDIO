# studio-runtime.js: shipping the edits made in the Studio

The Studio's Edit tab and its agents save into `studio.edits.json` beside the game: part overrides
(move, turn, scale, hide, recolour) by stable key, the world (background, fog), and `placed`,
the things put in that the game's code did not make. `studio-runtime.js` applies that file in the
game itself: one ESM file, no imports, about 39 KB (15 KB gzipped). The Studio serves it at
`http://127.0.0.1:8777/studio-runtime.js`. To ship it, copy that file and `studio.edits.json` next to
your game.

```js
import { loadStudioEdits, applyStudioEdits } from './studio-runtime.js';
const edits = await loadStudioEdits();              // ./studio.edits.json; null when there is none
await applyStudioEdits(scene, edits);               // three.js: the Scene you render
await applyStudioEdits(app, edits);                 // PlayCanvas: the app (keys count from app.root)
```

Call it after the game has built its world. What is not built yet is retried for `retryMs`.

**Options** (third argument): `base` (the URL files resolve against, default the page's folder) ·
`GLTFLoader` (three only, for `model` placements: your loader class or a configured instance) ·
`placed: false` (parts only) · `retryMs` (default 60000, 0 = no retries) ·
`url(path, kind)` (map a project path to a URL yourself) · `onSettle(report)` (nothing left pending).

**Report**: `{parts, placed, pending, missing, errors}`. It is the same object while retries run:
`pending` counts down and `placed` up as late clones are made. `missing` lists part keys with no
object. Every refusal is a sentence in `errors`.

**One placement now**: `placeStudioItem(target, {id, name, ref, pos, rot, scale})` returns
`{ok, object, key, how}`. An object that already has that id is replaced. `unplaceStudioItem(target, id)`
removes it again. `studioKeys(root)` and `findByKey(root, key)` are the editor's own keys.

## Rules the file follows

- **Keys** are `ops.stableKeys`: the name if it is unique, `name.000`, `name.001`… in traversal
  order if it repeats, the child path (`/3/0`) if there is no name. A merged mesh's piece is
  `mesh~t<first>-<end>` and moves from the corners the code built, but only if its `piece`
  check still matches.
- **Radians.** `rot` is an XYZ euler in radians, in the file and on the wire; PlayCanvas gets
  the same rotation as a quaternion. Positions are LOCAL to the parent.
- **Placements** are re-applied by id and never doubled. One the file no longer lists is
  removed on the next apply. Each is marked `userData.studioPlaced` (three) or `__studioPlaced`
  (PlayCanvas) and parented to the root. Give each a unique `name`: the name is its key.
- **Kinds.** `primitive {shape, color}`: box, sphere, cylinder, cone, plane, torus with three's
  own vertices in both engines, plus pointLight, spotLight and dirLight. `clone {of: key}`: a copy of
  the game's object (PlayCanvas: without its scripts, physics, camera and sound); it waits if the
  source is not spawned yet. `model {url}`: in PlayCanvas, a copy of one the game already drew
  (its paint and size) before the raw file. `code {file, export, args}`: your own builder, called.
  `image {url}`: an upright picture.
- **Classes come from your game**: nothing imports three or PlayCanvas, so no second copy loads.
  Not applied: the modifier stack (it needs forge-ops.js; `errors` says so), params, bones, clips.

## In the Studio

A game opened live gets its `studio.edits.json` applied by the live link, three.js or
PlayCanvas, with no code in the game: when it opens, when a scene appears, when the app is
pinned, and after the page reloads itself if the game exposes a handle (`window.__game`). The
same file on the same root is never applied twice. Look with `/api/live/eval`:
`__live.sidecarState()` gives the url, where it was read from, and the live report.
`__live.editRoot()` is the root the keys count from. A project the Studio has not opened is
read from the game's own server.

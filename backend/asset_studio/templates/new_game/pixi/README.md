# __GAME_TITLE__

A PixiJS game made by the Asset Studio. It runs as it is:

```
npm install
npm run dev        # prints  Local:   http://127.0.0.1:<port>/
```

A and D or the arrows move the player, Space jumps.

## What is where

| File | What it is |
|---|---|
| `index.html` | The page, with the import map that makes `import 'pixi.js'` work without a bundler. |
| `src/main.js` | The game, its scene, the loop, input, and the Studio's handles. |
| `src/assets.js` | Builders: functions that return a named canvas with its feet on the bottom edge. |
| `src/studio-runtime.js` | The Studio's runtime: applies `studio.edits.json` before the game starts. |
| `review/targets.js` | One isolate target per builder, for the Studio's visual review. |
| `serve.mjs` | The dev server: static files, correct MIME types, no dependencies. It reloads the open page when a code file changes. |
| `studio.game.json` | What the Studio needs to know: engine, entry, builders, runtime. |
| `studio.edits.json` | Appears once something is moved, hidden or placed in the Studio. Keep it. |

## For agents

- The running game: `window.__game = { app, PIXI }`. Saved edits are keyed by name (`crate-1`), so give new objects unique
  names: `new PIXI.Sprite(...)`, with `thing.label = 'unique-name'`.
- A new builder: export `buildThing({ ...options } = {})` from `src/assets.js`, return a named
  canvas, and add a target for it in `review/targets.js`.
- `studio.edits.json` is written by the Studio's scene tools, with rotations in radians. Change it
  through them, not by hand, while the Studio has the game open.
- Review actions live on `window.__review.actions` (`jump` is the first). Add one for each moment
  worth reviewing in play, and merge into the object: never assign `window.__review` itself.
- `src/studio-runtime.js` is a copy of the Studio's runtime. If its first line says STUB, replace it
  with the Studio's `/studio-runtime.js`.

## Shipping

The game is static files: `index.html`, `src/`, `studio.edits.json`, whatever assets you add, and
the one engine file the import map points at (`node_modules/pixi.js/dist/pixi.min.mjs`). Copy those to any static host and point the
import map at the copy.

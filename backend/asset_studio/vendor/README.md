# Vendored three.js example modules

Four files, copied unchanged from the three.js `examples/jsm` folder (release r185.1):

| File | Upstream path |
| --- | --- |
| `GLTFLoader.js` | `three/examples/jsm/loaders/GLTFLoader.js` |
| `BufferGeometryUtils.js` | `three/examples/jsm/utils/BufferGeometryUtils.js` |
| `SkeletonUtils.js` | `three/examples/jsm/utils/SkeletonUtils.js` |
| `GLTFExporter.js` | `three/examples/jsm/exporters/GLTFExporter.js` |

three.js is MIT licensed, copyright the three.js authors
(<https://github.com/mrdoob/three.js/blob/dev/LICENSE>).

## Why they are copied rather than imported

`engine.loader_source()` serves these to the browser with two rewrites: `from 'three'` becomes
the endpoint that serves **the project's own** three, and the loader's two relative imports come
back here. Both matter. A second copy of three produces meshes the first copy does not recognise,
so a model loaded by an unrelated three cannot join the scene the editor is already showing.

They are copied because half the games the Studio opens do not install three from npm at all —
they drop a single `three.module.js` into a `vendor/` folder, and a package that is not installed
has no `examples/` directory to read a loader out of. Vendoring three small files is what makes a
GLB open in every project rather than only in the ones with a full node_modules.

The exporter is served the same way, for `/api/live/export`: the forge page imports it with
`three=` set to its own engine URL, so what it writes is the bench's own copy of three.

Do not edit them. To update, copy the four files again from the same three.js release and
re-run `python glb_test.py` in `backend/`.

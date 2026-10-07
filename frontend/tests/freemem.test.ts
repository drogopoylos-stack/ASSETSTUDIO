// FREE NOW IS A BUTTON, SO IT HAS TO BE A BUTTON.
//
// The status bar's VRAM and RAM readings became the way to hand cached model memory back — the
// reason they are worth clicking is in docs/MiniMax Video.md: a finished MiniMax H3 clip leaves
// ComfyUI holding 13.8 GB of VRAM and a 32B text encoder's worth of host RAM, in ITS OWN process,
// where nothing in this one can free it.
//
// This file exists because every way that control can be silently dead still typechecks:
//
//   * `Metric` keeps its signature and renders a <span>, so `onClick` is accepted and ignored —
//     the number looks clickable and isn't;
//   * `freeNow` calls a method the API client does not have (or one pointed at a path the backend
//     does not serve), which is a 404 at the moment the user needs it;
//   * the pill is wired but the handler never reaches `api.freeGpu` at all.
//
// So the assertions below are about the CALLS, not about strings being present: the same shape as
// tests/engineparity.test.ts, and for the same reason — a wiring mistake that compiles.
//
// The backend half (that ComfyUI is actually asked, that a running prompt is left alone, and that
// all five ComfyUI-backed providers implement `unload`) is pinned by backend/video_test.py.
//
// Run: npm run test:freemem
import { readFileSync } from "node:fs";
import { join } from "node:path";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra?: unknown) {
  if (cond) { pass++; return; }
  fails.push(name + (extra === undefined ? "" : "  <- " + JSON.stringify(extra)));
}
const section = (t: string) => console.log("\n" + t);

const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");
const bar = src("components/StatusBar.tsx");
const client = src("api/client.ts");

section("the reading is the control");
ok("the VRAM pill frees", /case "vram":[\s\S]*?onClick=\{freeNow\}/.test(bar),
   "the VRAM reading must carry onClick={freeNow}");
ok("the RAM pill frees", /case "ram":[\s\S]*?onClick=\{freeNow\}/.test(bar),
   "the RAM reading is where a video's encoder shows up, so it frees too");
ok("...and a handler turns the reading into a real button, not a span that ignores onClick",
   /if \(!onClick\) return <span[\s\S]*?<button type="button" onClick=\{onClick\}/.test(bar));

section("the handler reaches the backend");
ok("freeNow calls api.freeGpu()", /async function freeNow\(\)[\s\S]*?await api\.freeGpu\(\)/.test(bar));
ok("...and reports what came back instead of claiming success", /toast\(parts\.length \?/.test(bar));
ok("...and refreshes the bar so the drop is visible now", /useStore\.setState\(\{ stats: await api\.systemStats\(\) \}\)/.test(bar));

section("the API client points that at a route the backend serves");
ok("freeGpu POSTs /api/system/free-gpu", /freeGpu: \(\) => req<[\s\S]*?>\(\"\/api\/system\/free-gpu\", \{ method: \"POST\"/.test(client));
ok("the response type carries ComfyUI's own before/after",
   /comfyui\?: \{[\s\S]*?before\?: \{ vram_total: number; vram_free: number; ram_free: number \};/.test(client));

section("the backend actually has that route and that method");
const sys = readFileSync(join(process.cwd(), "..", "backend", "asset_studio", "routers", "system.py"), "utf8");
const comfy = readFileSync(join(process.cwd(), "..", "backend", "asset_studio", "providers", "comfy_common.py"), "utf8");
ok('the route is @router.post("/free-gpu")', /@router\.post\("\/free-gpu"\)/.test(sys));
ok("...and it asks ComfyUI too, not just this process's torch cache",
   /out\["comfyui"\] = free_comfy\(comfy_base\(\), force=True\)/.test(sys));
ok("free_comfy posts ComfyUI's own unload request",
   /http\.post\(f"\{base\}\/free", json=\{"unload_models": True, "free_memory": True\}\)/.test(comfy));
ok("...but never over a render that is running", /if running:/.test(comfy) && /queue_running/.test(comfy));

console.log(`\n  ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

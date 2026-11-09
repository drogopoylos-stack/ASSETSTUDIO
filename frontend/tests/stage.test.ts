// The 2D stage, held to its contract with engines made of plain objects: the keys, what a saved
// document does to a game, the state two copies of the runtime share, and the rule an HTML game
// gets. The real Phaser, PixiJS and HTML games are driven through the live routes by hand; this is
// what must keep holding between those runs.
(globalThis as any).window = { innerWidth: 1000, innerHeight: 500 };
(globalThis as any).document = { baseURI: "http://game.test/" };

const { applyStage, cleanVals, domRule, stageCall, stageEngineOf } = await import("../src/components/engine/edit/stage");
const { dragEdit, hitRow } = await (async () => {
  // Stage2D.tsx needs a browser to load, so its two pure functions are read from the source and run.
  const { readFileSync } = await import("node:fs");
  const { join } = await import("node:path");
  const src = readFileSync(join(process.cwd(), "src", "components", "engine", "Stage2D.tsx"), "utf8");
  const take = (name: string) => {
    const from = src.indexOf("export function " + name);
    const to = src.indexOf("\n}\n", from) + 2;
    return src.slice(from, to).replace("export ", "")
      .replace(/: StageRow\[\]/g, "").replace(/: StageRow \| null/g, "").replace(/: StageRow/g, "").replace(/: Drag/g, "")
      .replace(/: number/g, "").replace(/: Record<string, unknown> \| null/g, "");
  };
  // eslint-disable-next-line no-new-func
  return new Function(take("hitRow") + take("dragEdit") + "return { hitRow, dragEdit };")() as any;
})();

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: unknown) => {
  if (cond) pass++;
  else { fails.push(name); console.log("  FAIL  " + name, extra ?? ""); }
};
const near = (a: number, b: number) => Math.abs(a - b) < 1e-6;

// ------------------------------------------------------------------ fake engines

function sprite(props: Record<string, unknown> = {}) {
  const o: any = {
    x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1, alpha: 1, visible: true, type: "Image", name: "", width: 20, height: 10,
    getBounds() { return { x: this.x - 10, y: this.y - 5, width: 20, height: 10 }; },
    ...props,
  };
  return o;
}

function phaser(list: any[]) {
  const scene: any = {
    scene: { key: "Main", pause() { scene.sys.paused = true; }, resume() { scene.sys.paused = false; } },
    sys: { settings: { visible: true, status: 5 }, paused: false, isPaused() { return this.paused; } },
    cameras: { main: { x: 0, y: 0, width: 800, height: 400, scrollX: 0, scrollY: 0, zoom: 1 } },
    children: { list },
  };
  for (const o of list) o.scene = scene;
  return {
    scene: { scenes: [scene] },
    renderer: {},
    config: { width: 800, height: 400 },
    scale: { width: 800, height: 400 },
    // The canvas is shown at twice its size, 100 px from the left: the case every box must survive.
    canvas: { getBoundingClientRect: () => ({ left: 100, top: 0, width: 1600, height: 800 }) },
  };
}

function pixi(children: any[]) {
  const stage: any = { children, worldTransform: { a: 1, b: 0, c: 0, d: 1 } };
  for (const c of children) {
    c.parent = stage;
    c.scale = { x: 1, y: 1, set(x: number, y: number) { this.x = x; this.y = y; } };
  }
  return {
    stage,
    ticker: { started: true, stop() { this.started = false; }, start() { this.started = true; } },
    renderer: { screen: { width: 800, height: 400 } },
    canvas: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 400 }) },
    render() { (this as any).drawn = ((this as any).drawn || 0) + 1; },
  };
}

// ------------------------------------------------------------------ which engine

ok("a Phaser game is known", stageEngineOf(phaser([])) === "phaser");
ok("a PixiJS app is known", stageEngineOf(pixi([])) === "pixi");
ok("a document is known", stageEngineOf({ nodeType: 9 }) === "dom");
ok("anything else is not", stageEngineOf({}) === "" && stageEngineOf(null) === "" && stageEngineOf({ isObject3D: true }) === "");

// ------------------------------------------------------------------ keys

{
  const named = sprite({ name: "hero" });
  const a = sprite({ texture: { key: "coin" } });
  const b = sprite({ texture: { key: "coin" } });
  const blank = sprite({ getBounds: undefined, width: undefined, type: "Graphics" });
  const v = await stageCall(phaser([named, a, b, blank]), "list", {});
  const keys = v.rows.map((r: any) => r.key);
  ok("a name used once is the key, under its scene", keys[0] === "Main/hero", keys);
  ok("a repeated name reads .000 and .001, by texture when it has no name", keys[1] === "Main/coin.000" && keys[2] === "Main/coin.001", keys);
  ok("no name and no texture falls to its place in the list", keys[3] === "Main/3", keys);
  ok("a thing with no size gets a marker, and says so", v.rows[3].point === true && v.rows[3].box[2] === 16);
}

// ------------------------------------------------------------------ boxes and moves

{
  const hero = sprite({ name: "hero", x: 100, y: 50 });
  const game = phaser([hero]);
  const v = await stageCall(game, "list", {});
  ok("a box is in page pixels, through the canvas's size and place", v.rows[0].box.join() === "280,90,40,20", v.rows[0].box);
  const r = await stageCall(game, "edit", { specs: [{ target: "Main/hero", move: [40, 20] }] });
  ok("a move in page pixels moves half as far in a canvas shown at twice its size", hero.x === 120 && hero.y === 60, [hero.x, hero.y]);
  ok("the answer has before and after", r.results[0].before.pos[0] === 100 && r.results[0].after.pos[0] === 120);
  await stageCall(game, "restore", { items: [{ key: "Main/hero", vals: r.results[0].undo }] });
  ok("undo values put it back", hero.x === 100 && hero.y === 50);
  game.scene.scenes[0].cameras.main.zoom = 2;
  await stageCall(game, "edit", { specs: [{ target: "hero", move: [40, 0] }] });
  ok("the camera's zoom counts, and a name finds what has one", near(hero.x, 110), hero.x);
  const miss = await stageCall(game, "edit", { specs: [{ target: "nobody", move: [1, 0] }] });
  ok("a name nothing has is refused with a reason", miss.results[0].ok === false && /nobody/.test(miss.results[0].error));
  const none = await stageCall(game, "edit", { specs: [{ target: "hero" }] });
  ok("an edit that changes nothing is refused", none.results[0].ok === false);
}

{
  const a = sprite({ name: "twin" });
  const b = sprite({ name: "twin" });
  const c = await stageCall(phaser([a, b]), "edit", { specs: [{ target: "twin", visible: false }] });
  ok("a name two objects share is refused, and the keys are offered", c.results[0].ok === false && /twin\.000/.test(c.results[0].error));
}

// ------------------------------------------------------------------ the physics body

{
  const seen: number[][] = [];
  const hero = sprite({ name: "hero", body: { reset(x: number, y: number) { seen.push([x, y]); } } });
  await stageCall(phaser([hero]), "edit", { specs: [{ target: "hero", pos: [5, 6] }] });
  ok("a physics body moves with its sprite, or it writes the old place back", seen[0]?.join() === "5,6", seen);
}

// ------------------------------------------------------------------ a saved document

{
  const late = sprite({ name: "late" });
  const hero = sprite({ name: "hero" });
  const game = phaser([hero]);
  const rep = await applyStage(game, {
    parts: {
      "Main/hero": { pos: [7, 8, 0], rot: [0, 0, 0.5], scale: [2, 3, 1], alpha: 0.25, hidden: true },
      "Main/late": { pos: [9, 9, 0] },
    },
  }, { retryMs: 0 });
  ok("position, turn, scale, alpha and hide are applied", hero.x === 7 && hero.y === 8 && hero.rotation === 0.5
    && hero.scaleX === 2 && hero.scaleY === 3 && hero.alpha === 0.25 && hero.visible === false);
  ok("the report counts it and names what is not there yet", rep.parts === 1 && rep.missing.join() === "Main/late", rep);
  game.scene.scenes[0].children.list.push(late);
  late.scene = game.scene.scenes[0];
  await new Promise((r) => setTimeout(r, 1100));
  ok("an object the game makes later still gets its edit", late.x === 9, late.x);
  hero.x = 500;
  await new Promise((r) => setTimeout(r, 1100));
  ok("an object is edited once: the game may move it afterwards", hero.x === 500, hero.x);
  await applyStage(game, { parts: {} }, { retryMs: 0 });
}

{
  // A scene that makes its objects again must get the value after the undo, not the one undone.
  const hero = sprite({ name: "hero" });
  const game = phaser([hero]);
  await applyStage(game, { parts: {} }, { retryMs: 0 });
  const r = await stageCall(game, "edit", { specs: [{ target: "hero", pos: [50, 50], save: true }] });
  await stageCall(game, "restore", { items: [{ key: "Main/hero", vals: r.results[0].undo }] });
  const again = sprite({ name: "hero" });
  again.scene = game.scene.scenes[0];
  game.scene.scenes[0].children.list = [again];
  await new Promise((done) => setTimeout(done, 1100));
  ok("an undone value does not come back on a new object", again.x === 0, again.x);
  await applyStage(game, { parts: {} }, { retryMs: 0 });
}

{
  // The Studio's runtime and the game's own copy are two modules in one page. Both find one state.
  const app = pixi([sprite({ name: "hero" })]);
  await applyStage(app, { parts: {} }, { retryMs: 0 });
  const state = (app as any).__studioStage;
  ok("the job lives on the target, where a second copy of this file finds it", !!state?.job);
  ok("it does not show up when a tool walks the app's keys", !Object.keys(app).includes("__studioStage"));
  await applyStage(app, { parts: { hero: { pos: [1, 2, 0] } } }, { retryMs: 0 });
  ok("a second apply reuses it", (app as any).__studioStage.job === state.job);
}

// ------------------------------------------------------------------ PixiJS

{
  const hero: any = sprite({ label: "hero", name: undefined, x: 10, y: 20, texture: { label: "art/hero.png" } });
  const app: any = pixi([hero]);
  const v = await stageCall(app, "list", {});
  ok("a PixiJS label is the key", v.rows[0].key === "hero");
  await stageCall(app, "edit", { specs: [{ target: "hero", scale: 2, rot: 1 }] });
  ok("scale goes through scale.set", hero.scale.x === 2 && hero.scale.y === 2 && hero.rotation === 1);
  const p = await stageCall(app, "pause", { on: true });
  ok("pause stops the ticker", p.paused === true && app.ticker.started === false);
  const before = app.drawn || 0;
  await stageCall(app, "edit", { specs: [{ target: "hero", alpha: 0.5 }] });
  ok("a change while paused asks for its frame: a stopped ticker draws nothing", (app.drawn || 0) > before);
  await stageCall(app, "pause", { on: false });
  ok("resume starts it again", app.ticker.started === true);
}

// ------------------------------------------------------------------ HTML

ok("no change writes no rule", domRule("#a", {}) === "" && domRule("#a", { pos: [0, 0], scale: [1, 1], alpha: 1, hidden: false }) === "");
{
  const rule = domRule("#a", { pos: [5, -6], rot: [0, 0, 0.5], scale: [2, 1], alpha: 0.5, hidden: true });
  ok("a rule uses translate, rotate and scale, so it adds to a transform the game animates",
    /translate:5px -6px !important/.test(rule) && /rotate:0\.5rad !important/.test(rule) && /scale:2 1 !important/.test(rule) && !/transform/.test(rule), rule);
  ok("alpha and hide are in it", /opacity:0\.5 !important/.test(rule) && /visibility:hidden !important/.test(rule), rule);
}

// ------------------------------------------------------------------ a file edited by hand

{
  const v = cleanVals({ pos: ["a", 2], rot: [0, 0, 1], scale: [2, 2], alpha: 9, hidden: "yes", junk: 1 });
  ok("only numbers and real fields pass", !v.pos && v.rot?.[2] === 1 && v.scale?.[0] === 2 && v.alpha === 1 && !("hidden" in v) && !("junk" in v), v);
}

// ------------------------------------------------------------------ the Edit tab's arithmetic

{
  const rows = [
    { key: "bg", visible: true, box: [0, 0, 100, 100], scale: [1, 1] },
    { key: "top", visible: true, box: [10, 10, 20, 20], scale: [1, 1] },
    { key: "ghost", visible: false, box: [10, 10, 20, 20], scale: [1, 1] },
  ];
  ok("the object drawn last wins, and a hidden one cannot be hit", hitRow(rows, 15, 15).key === "top");
  ok("outside everything is nothing", hitRow(rows, 500, 500) === null);
  const d = { handle: "move", key: "top", x0: 0, y0: 0, x: 3, y: 4, box: [10, 10, 20, 20] };
  ok("a drag is a move", dragEdit(d, rows[1]).move.join() === "3,4");
  ok("a click with no move changes nothing", dragEdit({ ...d, x: 0, y: 0 }, rows[1]) === null);
  const sc = dragEdit({ ...d, handle: "scale", x: 20, y: 0 }, { ...rows[1], scale: [2, 1] });
  ok("a resize multiplies the scale the object has", near(sc.scale[0], 4) && near(sc.scale[1], 1), sc);
  const turn = dragEdit({ handle: "rotate", key: "top", x0: 20, y0: 0, x: 40, y: 20, box: [10, 10, 20, 20] }, rows[1]);
  ok("a turn is the angle around the middle of the box", near(turn.rotate[2], 90), turn);
}

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

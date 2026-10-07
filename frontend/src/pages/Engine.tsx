import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Camera,
  Box, Image as ImageIcon, Loader2, PanelBottomClose, PanelBottomOpen, PanelLeftClose,
  PanelLeftOpen, PanelRight, Radio,
} from "lucide-react";
import ModelViewer from "../components/ModelViewer";
import { api } from "../api/client";
import type { EngineGame, EngineGen, EngineProject, LibraryAsset } from "../types";
import { cls, pollWhileVisible, useSticky } from "../components/ui";
import { fmtTok } from "../components/SubAgentCard";
import { GameRail, normPath } from "../components/engine/GameRail";
import { engineInUse, tabInUse } from "../components/engine/inUse";
import { AssetViewport, type LiveInfo, type Replay } from "../components/engine/Viewport";
import { GameFrame, type ProjectDetail } from "../components/engine/GameFrame";
import { Inspector, type GenDetail } from "../components/engine/Inspector";
import { HistoryStrip, keyOf, type TypeFilter } from "../components/engine/HistoryStrip";
import { MODULE_FILES, type EngineKind } from "../components/engine/studio";
import Editor, { type EditorSource } from "../components/engine/edit/Editor";
import { FilePicker, type Picked } from "../components/engine/edit/FilePicker";
import { cachePrefs, mergePrefs, readCachedPrefs, watchPrefs, type EnginePrefs } from "../components/engine/prefs";
import { Library } from "../components/engine/Library";
import { editEngineFor } from "../components/engine/edit/assetOpen";

// The Studio Engine window: a viewer over what the agents are making, in its own native window.
//
// It is read-only by design. Nothing here starts a browser, a dev server or a forge — the window
// polls what exists and re-runs recorded code in its own renderer. That is also why every failure
// mode is spelled out in place: a viewer that can do nothing about a problem must at least name it.

type EngineState = Awaited<ReturnType<typeof api.engineState>>;
type Mode = "asset" | "edit" | "game" | "library";

/** The live scene an agent is working on, as the window needs it. */
interface BenchState {
  project: string;
  sig: string;
  code: string;
  label: string;
  view: string;
  tris: number;
  meshes: number;
  /** The engine that built it — "" from a backend older than the field. */
  engine: string;
  /** When the agent last changed it, in epoch seconds (0 when the backend did not say). */
  ts: number;
  /** The GLB files /glb put on the bench; the code only names them in a comment. */
  models: { name: string; path: string; v?: number }[];
}

/** PlayCanvas code, by what only PlayCanvas code calls. The fallback when a bench carries no
 *  engine field; the same test the editor itself uses. */
const PC_CODE = /\bpc\.(create[A-Z]\w*|Entity|StandardMaterial|MeshInstance|Application|Color|Mesh|Texture)\b/;

const RESTART = "Restart the backend to use this — the engine endpoints are newer than the running process.";

/**
 * THE STUDIO'S OWN THREE, LAST, ON EVERY LIST.
 *
 * The editor's viewport is three whatever the game is built with. Half the games here are
 * PlayCanvas, Phaser or Pixi and have no three anywhere, so every URL found beside the project
 * 404s, `loadEngine` throws, and opening a .glb ends in a paragraph about node_modules — for a
 * file that needs nothing from the game at all.
 *
 * Last, so a three.js game still gets its OWN build: a mesh made by one copy of three does not
 * belong in a scene held by another, and that failure is silent.
 */
function withStudioThree(urls: string[]): string[] {
  const own = "/vendor/three/three.module.js";
  return urls.includes(own) ? urls : [...urls, own];
}
const is404 = (e: any) => /\b404\b/.test(String(e?.message || ""));
const slug = (s: string) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
/** How long a generation made while this window was open stays marked as news. */
const JUST_MADE_S = 600;

// Forge records carry the project path; review sheets carry only the folder name the reviewer
// slugged from it. Match whichever the item has — path against path and root, name against name
// and the game's subfolder (a review of `arena` belongs to "fight strength brainrots").
function projectOf(g: { project: string; project_name: string }, projects: EngineProject[]): EngineProject | undefined {
  if (g.project) {
    const k = normPath(g.project);
    return projects.find((p) => normPath(p.path) === k || normPath(p.root) === k);
  }
  const s = slug(g.project_name);
  if (!s) return undefined;
  return projects.find((p) => slug(p.name) === s || (!!p.sub && slug(p.sub) === s));
}

/**
 * THE THREE LINES THAT OPEN A MODEL FILE.
 *
 * A .glb can be reached three ways — a card in the Library, a row in the file picker, a
 * `?edit=` link — and each of them used to hold its own idea of what opening one means. One of
 * the three wrote nothing at all. So it is written once, here.
 *
 * The Studio serves the file with its compression already taken out, and the editor loads it with
 * a loader bound to the same three the viewport uses. From there it is an ordinary object.
 */
function modelCode(name: string, file: string, url: string): string {
  return `// ${name} — ${file}\n`
    + `const obj = await loadGLB(${JSON.stringify(url)});\n`
    + `if (!obj) throw new Error("the model loaded but held no scene");\nadd(obj);\n`;
}

/**
 * THE GLB FILES ON THE BENCH, as code the editor runs.
 *
 * `/api/live/glb` leaves only `/* GLB: orc.glb *\/` in the bench's code, so the live view rebuilt
 * an empty scene while the agent judged its own export and the person watching saw nothing. Each
 * file is opened the way the Library opens one, in a block of its own so two of them do not both
 * declare `obj`. `v` is the file's time: a GLB written again under the same name is fetched again.
 */
function benchModelCode(models: { name: string; path: string; v?: number }[]): string {
  return models.map((m) => "{\n" + modelCode(m.name, m.path,
    api.engineModelPath(m.path) + (m.v ? "&v=" + m.v : "")) + "}\n").join("");
}

/** A bench whose code is nothing but the comments `/glb` leaves: every part of it is a file. */
function onlyGlbComments(code: string): boolean {
  return !code.replace(/\/\* GLB: [^*]*\*\//g, "").replace(/\/\/ ---- and then ----/g, "")
    .replace(/[{}\s]/g, "");
}

export default function Engine() {
  const [state, setState] = useState<EngineState | null>(null);
  const [apiErr, setApiErr] = useState("");
  const [projects, setProjects] = useState<EngineProject[]>([]);
  const [projLoading, setProjLoading] = useState(true);
  const [items, setItems] = useState<EngineGen[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(200);
  const [histLoading, setHistLoading] = useState(true);

  // The tab the window opens on is a setting (Settings → Studio engine), read from the cached
  // copy because the first render cannot wait for a request. A deep link overrides it below.
  const [mode, setMode] = useState<Mode>(() => {
    const p = readCachedPrefs();
    let last = "";
    try { last = localStorage.getItem("engine.lastMode") || ""; } catch { /* first visit */ }
    const want = p.open_tab === "last" ? last : p.open_tab;
    return want === "edit" || want === "game" || want === "asset" || want === "library" ? want : "asset";
  });
  const [prefs, setPrefs] = useState<EnginePrefs>(() => readCachedPrefs());
  const [selProject, setSelProject] = useState("");
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selected, setSelected] = useState<EngineGen | null>(null);
  const [gen, setGen] = useState<GenDetail | null>(null);
  const [genLoading, setGenLoading] = useState(false);
  const [genErr, setGenErr] = useState("");
  /** The dev URL of the generation's project; null until looked up, so a replay never starts twice. */
  const [genDev, setGenDev] = useState<string | null>(null);
  // The shelves: what kind of thing (3D asset, scene, picture, review sheet), what it depicts, and
  // a search. The backend answers all of them over the whole history, so a chip counts everything
  // ever made, not only the page that happens to be loaded.
  const [typeF, setTypeF] = useState<TypeFilter>("");
  const [subjectF, setSubjectF] = useState("");
  const [query, setQuery] = useState("");
  const [dq, setDq] = useState("");
  const [facets, setFacets] = useState<{ by_type: Record<string, number>; by_subject: Record<string, number>; by_project: Record<string, number> }>(
    { by_type: {}, by_subject: {}, by_project: {} });
  useEffect(() => { const t = window.setTimeout(() => setDq(query), 250); return () => window.clearTimeout(t); }, [query]);
  const [follow, setFollow] = useState(true);
  // WHAT THE AGENT HAS ON THE BENCH, polled while watching. `sig` changes when the scene changes,
  // and only then; the camera changes far more often and must not cost a rebuild.
  const [bench, setBench] = useState<BenchState | null>(null);
  const [inspector, setInspector] = useState(true);
  const [live, setLive] = useState<LiveInfo | null>(null);
  /** An asset opened from its own source file. Only this can be written back to. */
  const [editFile, setEditFile] = useState<(Picked & { asset?: LibraryAsset }) | null>(null);
  /** Every game in the selected workspace, each with its own dev server. */
  const [workGames, setWorkGames] = useState<EngineGame[]>([]);
  /** Which one of them the window is showing — its folder inside the workspace, or "" for all. */
  const [gameSub, setGameSub] = useState("");
  /** A RUNNING game opened in the editor through the live link: its scene mirrored by name. */
  const [liveProject, setLiveProject] = useState("");
  const [liveNote, setLiveNote] = useState("");
  const [railOpen, setRailOpen] = useSticky("engine.rail", true);
  const [stripOpen, setStripOpen] = useSticky("engine.strip", true);
  // Stand where the agent stood. On by default while watching: seeing the same angle it
  // saw is most of what makes a decision it made legible. Orbit at any time — this only
  // sets the camera when a run arrives, it never holds it there.
  const [matchCam, setMatchCam] = useSticky("engine.matchCam", true);
  // Which shelf the bottom row is. Kept across sessions by hand, because the sticky helper holds
  // switches and this is a choice of two.
  const [stripMode, setStripMode] = useState<"runs" | "assets">(
    () => (localStorage.getItem("engine.stripMode") === "assets" ? "assets" : "runs"));
  useEffect(() => { localStorage.setItem("engine.stripMode", stripMode); }, [stripMode]);

  // `/engine?edit=<path>` opens a file in the editor straight away. An agent that has just
  // written a scene can hand the person that link, and the review harness can open it too.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    // `/engine?live=<project>` mirrors a running game straight away — the link an agent hands over
    // after it has opened the game on the live link.
    const lp = q.get("live");
    if (lp) { setLiveProject(lp); setSelProject(lp); setMode("edit"); return; }
    // `/engine?game=<project>` opens that game's Game tab — Play, or the agent's live view if that
    // is what the person chose for it last time.
    const gp = q.get("game");
    if (gp) { setSelProject(gp); setMode("game"); return; }
    const p = q.get("edit");
    if (!p) return;
    const proj = q.get("project");
    if (proj) setSelProject(proj);
    // ONE WAY A PATH BECOMES AN EDITOR SOURCE. This read the file itself and defaulted its
    // text to "", which is how a link to a .glb opened an editor over nothing at all.
    openRef.current(p);
  }, []);

  // The settings themselves: fetched once, cached for the next boot, and fetched again whenever
  // another window saves settings or this one comes back into focus.
  useEffect(() => {
    let dead = false;
    const load = () => api.settings()
      .then((st) => { if (dead) return; const p = mergePrefs(st?.engine); cachePrefs(p); setPrefs(p); })
      .catch(() => { /* the cached copy stands */ });
    load();
    const stop = watchPrefs(load);
    return () => { dead = true; stop(); };
  }, []);
  useEffect(() => { try { localStorage.setItem("engine.lastMode", mode); } catch { /* ignore */ } }, [mode]);

  // The deep-link effect runs once, on mount, and must not re-run when the opener is rebuilt.
  const openRef = useRef<(p: string) => void>(() => {});
  const openedAt = useRef(Date.now() / 1000);
  /** Set once the first run has been picked; the window's first automatic pick must not change the tab. */
  const picked = useRef(false);
  const seenLatest = useRef<string | null>(null);
  const genReq = useRef(0);
  const detailReq = useRef(0);
  const limitRef = useRef(limit);
  limitRef.current = limit;
  const followRef = useRef(follow);
  followRef.current = follow;
  const projectsRef = useRef(projects);
  projectsRef.current = projects;
  const selProjectRef = useRef(selProject);
  selProjectRef.current = selProject;
  const devCache = useRef(new Map<string, { url: string; at: number }>());

  // ---- polling ---------------------------------------------------------------------------
  const fail = useCallback((e: any, what: string) => {
    setApiErr(is404(e) ? RESTART : (e?.message || `could not read ${what}`));
  }, []);
  const loadState = useCallback(async () => {
    try { setState(await api.engineState()); setApiErr(""); } catch (e) { fail(e, "the engine state"); }
  }, [fail]);
  const loadProjects = useCallback(async () => {
    try { setProjects((await api.engineProjects()).projects || []); } catch (e) { fail(e, "the project list"); }
    finally { setProjLoading(false); }
  }, [fail]);
  const loadHistory = useCallback(async () => {
    try {
      const r = await api.engineHistory(selProject, limitRef.current, "", { type: typeF, subject: subjectF, q: dq });
      setItems(r.items || []);
      setTotal(r.total || 0);
      setFacets({ by_type: r.by_type || {}, by_subject: r.by_subject || {}, by_project: r.by_project || {} });
    } catch (e) { fail(e, "the history"); }
    finally { setHistLoading(false); }
  }, [fail, selProject, typeF, subjectF, dq]);
  // A file from the Library opens in the Edit tab, the same way a deep link does.
  const openInEditor = useCallback(async (p: string) => {
    try {
      const f = await api.wsFile(p);
      // A MODEL IS NOT TEXT, AND `f.text || ""` MADE ONE LOOK LIKE EMPTY TEXT.
      //
      // `/api/workspace/file` answers a .glb with its kind and its size and no `text` field —
      // correctly, because there is no text in it. Defaulting that to "" opened the editor on an
      // empty source: the header named the file, the viewport stayed empty, and the only thing on
      // screen was "the asset did not build". That is every report of a model opening to nothing.
      // A model is opened the way the Library's own button opens one instead.
      const name = p.replace(/\\/g, "/").split("/").pop() || p;
      if (f.kind === "model") {
        setEditFile({
          path: p,
          // A synthetic index row. The editor reads `model` off it to know that this IS a model,
          // and a model belongs to NO engine: it loads into the editor's own three whatever the
          // game around it happens to be built with.
          asset: { id: p, name, type: "model", model: "", file: name, path: p, line: 0,
                   export: "", table: "", key: "", index: -1, engine: "", subject: "other",
                   tags: [], size: f.size || 0, mtime: f.mtime || 0 },
          // `convert` means it is not glTF at all — an .obj or an .stl — and only the
          // workspace endpoint can turn one into something a loader reads.
          code: modelCode(name, name, f.convert ? api.wsModel(p) : api.engineModelPath(p)),
        });
        setMode("edit");
        return;
      }
      if (typeof f.text !== "string") {
        // Name the file and say what it is, rather than opening an empty editor over it.
        setApiErr(`${name} has nothing to edit — the workspace reads it as ${f.kind}.`);
        return;
      }
      setEditFile({ path: p, code: f.text });
      setMode("edit");
    } catch (e) { fail(e, "the file"); }
  }, [fail]);
  openRef.current = openInEditor;

  // ONE ENTRY OF THE LIBRARY, BUILT AND LOOKED AT — not the file that happens to contain it.
  //
  // A game keeps thirty creatures in one table and one function that builds any of them. Opening
  // the FILE shows you the table; it does not show you the creature. So this writes the three
  // lines that a person would write by hand — import the module, take that entry, call the
  // builder with it — and hands them to the editor, which runs them in its own scene. The two
  // call shapes are tried in order because a WebGL builder needs a device and a three.js one does
  // not, and which of those a project is cannot be read off the name of the export.
  // Written into every generated snippet. A project-relative path and a dev server rooted at a
  // subfolder of that project are both ordinary, and the difference is invisible from here — so
  // try the whole path, then drop one leading folder at a time, and report every attempt if none
  // of them load. Cheap: a module that resolves is cached by the browser after the first hit.
  const IMPORT_HELPER = `const __studioImport = async (rel) => {\n`
    + `  const parts = String(rel).replace(/\\\\/g, "/").replace(/^\\/+/, "").split("/");\n`
    + `  const tried = [];\n`
    + `  for (let i = 0; i < parts.length; i++) {\n`
    + `    const u = "/" + parts.slice(i).join("/");\n`
    + `    tried.push(u);\n`
    + `    try { return await import(/* @vite-ignore */ u); } catch (e) {}\n`
    + `  }\n`
    + `  throw new Error("could not import " + rel + " — tried " + tried.join(", "));\n`
    + `};\n`;

  const openAsset = useCallback((a: any) => {
    if (!a) return;
    // A MODEL IS OPENED, NOT BUILT. An entry whose recipe is a file has nothing to call: the
    // Studio serves that file with its compression already removed, and the editor loads it with
    // a loader bound to its own three. From there it is an ordinary object — orbit it, move it,
    // scale it, duplicate it, put things beside it.
    const modelFile = a.model || (a.type === "model" ? a.file : "");
    if (modelFile) {
      const url = api.engineModelUrl(a.project || selProject, modelFile);
      setEditFile({
        path: a.path,
        asset: a,
        code: modelCode(a.name, modelFile, url),
      });
      setMode("edit");
      return;
    }
    if (a.type !== "code" && a.type !== "spec") { openInEditor(a?.path || ""); return; }
    // EVERYTHING ELSE IS ONE CALL. Which file, which entry, which builder and which arguments are
    // all decided by assetOpen.ts, in the one place the thumbnail renderer decides them too. The
    // snippet stays readable, and stays editable — change the seed here and rebuild.
    const ref = {
      type: a.type, name: a.name, file: String(a.file || "").replace(/\\/g, "/"),
      root: a.root || "", export: a.export || "", table: a.table || "",
      key: a.key || "", index: Number(a.index) >= 0 ? Number(a.index) : -1,
      deps: a.deps || [],
    };
    const code = `// ${a.name} — ${ref.file}\n`
      + `const asset = ${JSON.stringify(ref, null, 2)};\n`
      + `const obj = await buildAsset(asset);\n`
      + `add(obj);\n`;
    setEditFile({ path: a.path, code, asset: a });
    setMode("edit");
  }, [openInEditor, selProject]);
  // The person corrected a shelf in the Inspector: show it in the strip at once, then let the
  // next load bring the counts in line.
  const onTagged = useCallback((row: EngineGen) => {
    setItems((is) => is.map((g) => (g.id === row.id ? { ...g, ...row } : g)));
    setSelected((s) => (s && s.id === row.id ? { ...s, ...row } : s));
    loadHistory();
  }, [loadHistory]);

  // WHAT THE FORGE IS DOING THIS SECOND. A build is milliseconds and a sweep of 36 angles is
  // five seconds; at a 2s heartbeat both can finish between two polls and the window never shows
  // that anything happened. While something is running, ask faster.
  const acts: any[] = (state as any)?.activity || [];
  const forgeBusy = acts.some((a) => a?.running);
  useEffect(() => pollWhileVisible(loadState, forgeBusy ? 700 : 2000), [loadState, forgeBusy]);
  useEffect(() => pollWhileVisible(loadProjects, 20000), [loadProjects]);
  useEffect(() => pollWhileVisible(loadHistory, 10000), [loadHistory]);
  const firstLimit = useRef(true);
  useEffect(() => {
    if (firstLimit.current) { firstLimit.current = false; return; }
    loadHistory();
  }, [limit, loadHistory]);

  // ---- the selected project (rail) ---------------------------------------------------------
  const loadDetail = useCallback(async (path: string) => {
    const my = ++detailReq.current;
    if (!path) { setDetail(null); setDetailLoading(false); return; }
    setDetailLoading(true);
    try {
      const d = await api.engineProject(path);
      if (my !== detailReq.current) return;
      setDetail(d);
      devCache.current.set(normPath(path), { url: d.dev_url || "", at: Date.now() });
    } catch (e) {
      if (my !== detailReq.current) return;
      setDetail(null);
      if (is404(e)) setApiErr(RESTART);
    } finally {
      if (my === detailReq.current) setDetailLoading(false);
    }
  }, []);
  useEffect(() => { setDetail(null); loadDetail(selProject); }, [selProject, loadDetail]);
  // Dev servers start and stop under the window; while the game is on screen, ask again.
  useEffect(() => {
    if (mode !== "game" || !selProject) return;
    return pollWhileVisible(() => loadDetail(selProject), 15000, { immediate: false });
  }, [mode, selProject, loadDetail]);

  // ---- the selected generation ---------------------------------------------------------------
  const selectItem = useCallback(async (g: EngineGen, manual = true) => {
    if (manual) setFollow(false);
    setSelected(g);
    setLive(null);
    // Picking a run while editing swaps what is being edited; it must not throw you back to the
    // viewer. A sheet-only item has nothing to edit, so that one does go back.
    // The first automatic pick is the window opening on its newest run; it must not override the
    // tab the person chose to open on. Every later follow, and every click, behaves as before.
    const first = !picked.current;
    picked.current = true;
    // WHERE A FOLLOWED RUN LANDS. Clicking a run is browsing, so it goes to the viewer unless
    // you were already editing. Following is watching: it goes to the 3D viewport whenever the
    // run can be replayed there, because a contact sheet is a photograph of a moment and the
    // point of watching is to orbit the thing the agent just changed. Runs that drew no picture
    // at all — the cheap half of the agent's loop — have nowhere else to go.
    const replayable = g.kind === "forge" && g.replayable;
    if (!manual) setMode((m) => (replayable ? "edit" : m === "edit" ? "asset" : m));
    else if (!first || manual) setMode((m) => (m === "edit" && replayable ? "edit" : "asset"));
    const my = ++genReq.current;
    if (g.kind !== "forge" || !g.replayable) { setGen(null); setGenErr(""); setGenLoading(false); return; }
    setGenLoading(true);
    setGenErr("");
    try {
      const d = await api.engineGeneration(g.id);
      if (my !== genReq.current) return;
      if (!d.ok) throw new Error(d.error || "no such generation");
      setGen(d);
    } catch (e: any) {
      if (my !== genReq.current) return;
      setGen(null);
      setGenErr(is404(e) ? RESTART : (e?.message || "could not read the generation"));
    } finally {
      if (my === genReq.current) setGenLoading(false);
    }
  }, []);

  // The dev URL of the generation's OWN project, looked up once per generation. It is what lets
  // a split three.js build load and what `import()` inside the code is pointed back at.
  useEffect(() => {
    if (!gen) { setGenDev(null); return; }
    setGenDev(null);
    const p = projectOf({ project: gen.project, project_name: "" }, projectsRef.current);
    const path = p?.path || gen.project;
    const key = normPath(path);
    const hit = devCache.current.get(key);
    if (hit && Date.now() - hit.at < 20000) { setGenDev(hit.url); return; }
    let stale = false;
    api.engineProject(path)
      .then((d) => {
        if (stale) return;
        devCache.current.set(key, { url: d.dev_url || "", at: Date.now() });
        setGenDev(d.dev_url || "");
      })
      .catch(() => { if (!stale) setGenDev(""); });
    return () => { stale = true; };
  }, [gen?.id, gen?.project]);  // eslint-disable-line react-hooks/exhaustive-deps

  // A selection made from the live pill is a stub until the next history load brings the record.
  useEffect(() => {
    if (!selected) return;
    const k = keyOf(selected);
    const real = items.find((g) => keyOf(g) === k);
    if (real && real !== selected) setSelected(real);
  }, [items]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- live: a new generation appears while the window is open ------------------------------
  useEffect(() => {
    if (!state) return;
    const l = state.latest;
    const id = l?.id || "";
    // What existed when the window opened is history, not news.
    if (seenLatest.current === null) { seenLatest.current = id; return; }
    if (!id || id === seenLatest.current) return;
    seenLatest.current = id;
    loadHistory();
    if (!followRef.current || !l) return;
    const stub: EngineGen = {
      id, kind: "forge", ts: l.ts, project: "", project_name: l.project_name, label: l.label,
      engine: l.engine, sheet: l.sheet, ok: l.ok, views: [], replayable: true,
    };
    // A project filter that would hide the new one is dropped, otherwise "show it immediately"
    // shows a strip in which it does not appear.
    const p = projectOf(stub, projectsRef.current);
    if (selProjectRef.current && (!p || normPath(p.path) !== normPath(selProjectRef.current))) setSelProject("");
    selectItem(stub, false);
  }, [state?.latest?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- the bench: what the agent is working on, right now ------------------------------------
  //
  // Polled rather than pushed, for the same reason the rest of this window polls: a socket that
  // has to be reconnected after every backend restart is a socket that is down exactly when
  // something interesting is happening. Only while watching, and only for one project.
  useEffect(() => {
    if (!follow) { setBench(null); return; }
    // An empty project means "whichever one is being worked on", which is what the window shows
    // when it opens on All projects — and that is exactly when a person is watching.
    const proj = selProject;
    let dead = false;
    const load = () => api.liveBench(proj)
      .then((b) => {
        if (dead) return;
        if (!b?.ok || !b.open || !b.code) { setBench(null); return; }
        setBench({
          // The bench's OWN project. On "All projects" `proj` is empty, and an empty project
          // gave the editor nowhere to look for the engine that built it.
          project: b.project || proj, sig: b.sig || "", code: b.code, label: b.label || "on the bench",
          view: b.at?.view || "",
          tris: b.stats?.triangles || 0, meshes: b.stats?.meshes || 0,
          engine: b.engine || "",
          ts: b.ts || 0,
          models: Array.isArray(b.models) ? b.models.filter((m) => m && m.path) : [],
        });
      })
      .catch(() => { /* the tab may have been reaped; the next poll will say so */ });
    const stop = pollWhileVisible(load, 1200);
    return () => { dead = true; stop(); };
  }, [follow, selProject]);   // eslint-disable-line react-hooks/exhaustive-deps

  // SHOW THE BENCH WHILE AN AGENT WORKS. The bench is drawn only on the Edit tab, and the window
  // opens on Runs, whose empty viewport said "Pick a generation" while an agent built an orc one
  // tab away: the person saw nothing live. So the first live bench of this window takes an EMPTY
  // Runs view to the 3D viewport, once. A person who picked a run, opened a file or is on another
  // tab stays where they are; the live chip takes them there on a click. "Working" is any agent
  // still running — one that is quiet is thinking through a long turn, not stopped — or a bench
  // changed in the last ten minutes; an old bench with nobody at it does not pull the window over.
  const benchShown = useRef(false);
  useEffect(() => {
    if (!follow || !bench?.code || benchShown.current) return;
    const working = (state?.agents || []).length > 0;
    const recent = bench.ts > 0 && Date.now() / 1000 - bench.ts < 600;
    if (!working && !recent) return;
    if (mode !== "asset" || selected || editFile || liveProject) return;
    benchShown.current = true;
    setMode("edit");
  }, [follow, bench?.sig, bench?.ts, state?.agents, mode, selected, editFile, liveProject]);  // eslint-disable-line react-hooks/exhaustive-deps

  /** The live chip: to the bench when it is not on screen, else watching on and off as before. */
  const onLiveChip = () => {
    if (follow && bench?.code && (mode !== "edit" || editFile || liveProject)) {
      // An explicit click on "live" means the bench: a file or a mirrored game open in the
      // editor would otherwise stay in front of it.
      setEditFile(null);
      setLiveProject("");
      setMode("edit");
      return;
    }
    setFollow((v) => !v);
  };

  // WHICH GAME OWNS WHAT. A workspace here holds rot-haul on 5178 and rot-rush on 5179. Opening
  // one of rot-rush's four hundred assets means asking 5179; asking the workspace's first server
  // returns a 404 that reads exactly like a missing file, which is how thirty brainrots and
  // fifty-five props came to look like broken assets.
  useEffect(() => {
    if (!selProject) { setWorkGames([]); return; }
    let dead = false;
    const load = () => api.engineGames(selProject)
      .then((r) => { if (!dead) setWorkGames(r.games || []); })
      .catch(() => { if (!dead) setWorkGames([]); });
    load();
    return () => { dead = true; };
  }, [selProject]);
  /** Where one asset's code can be fetched from, best first: its own game, then everything else. */
  const basesFor = useCallback((root: string): string[] => {
    const want = String(root || "").replace(/\\/g, "/").toLowerCase();
    const urls: string[] = [];
    const push = (u?: string) => { if (u && !urls.includes(u)) urls.push(u); };
    const owner = workGames.find((g) => (g.sub || "").toLowerCase() === want);
    push(owner?.dev_url);
    for (const g of workGames) push(g.dev_url);
    push(detail?.dev_url || "");
    return urls.map((u) => u.replace(/\/+$/, ""));
  }, [workGames, detail?.dev_url]);

  // WHICH ONE YOU ARE LOOKING AT. Remembered per workspace, and cleared when the games change
  // under it — a folder that no longer exists is a filter that quietly shows nothing.
  useEffect(() => {
    if (!workGames.length) return;
    const subs = workGames.map((g) => g.sub).filter(Boolean);
    setGameSub((cur) => (cur && !subs.includes(cur) ? "" : cur));
  }, [workGames]);
  useEffect(() => {
    try { localStorage.setItem("engine.game." + normPath(selProject), gameSub); } catch { /* private mode */ }
  }, [gameSub, selProject]);
  useEffect(() => {
    try { setGameSub(localStorage.getItem("engine.game." + normPath(selProject)) || ""); }
    catch { setGameSub(""); }
  }, [selProject]);

  // HOW MUCH THE GAME HAS, on the tab itself. Without a number there, a person who cannot find
  // their thirty brainrots has no way to tell "this project has none" from "I am on the wrong
  // tab", and both look identical.
  const [assetCount, setAssetCount] = useState(0);
  useEffect(() => {
    if (!selProject) { setAssetCount(0); return; }
    let dead = false;
    api.engineAssets(selProject, { root: gameSub })
      .then((r: any) => { if (!dead) setAssetCount(r.total || 0); })
      .catch(() => { if (!dead) setAssetCount(0); });
    return () => { dead = true; };
  }, [selProject, gameSub]);

  // ---- derived -----------------------------------------------------------------------------
  const selP = useMemo(() => projects.find((p) => normPath(p.path) === normPath(selProject)) || null, [projects, selProject]);
  // The backend already filtered by project, type, subject and search; the strip shows what came back.
  const visible = items;
  // Per-project counts come from the backend too, over everything ever made. Forge runs are
  // counted under the project's folder name; review sheets under the slug they were rendered as.
  const railCounts = useMemo(() => {
    const m: Record<string, number> = {};
    const bp = facets.by_project;
    for (const p of projects) {
      const s = slug(p.name);
      const n = (bp[p.name] || 0) + (s !== p.name ? (bp[s] || 0) : 0);
      if (n) m[normPath(p.path)] = n;
    }
    return m;
  }, [projects, facets.by_project]);
  const openPaths = useMemo(() => new Set((state?.tabs || []).map((t) => normPath(t.project))), [state?.tabs]);
  const isJustMade = useCallback((g: EngineGen) =>
    g.kind === "forge" && g.ts > openedAt.current && Date.now() / 1000 - g.ts < JUST_MADE_S, []);

  // THE ANGLE THE AGENT RENDERED FROM. The record keeps what was asked for, which is not always
  // what the panels were labelled: a sheet's first panel is the reference and a focus panel is
  // named after the part, so the asked-for list is the honest one. A preset name is skipped —
  // the viewport has its own idea of "3q", and pretending otherwise would move the camera to a
  // place the agent never stood.
  // The angle the agent is looking from. The bench knows it exactly and updates as the agent
  // moves; a finished run only has the view names it asked for, which is the best that can be
  // read from a record after the fact.
  const agentAim = useMemo<string>(() => {
    if (bench?.view) return bench.view;
    const g: any = gen || {};
    const all: string[] = [...(g.asked_views || []), ...(g.views || [])];
    return all.find((v) => typeof v === "string" && v.includes("az=")) || "";
  }, [gen, bench?.view]);

  const replay = useMemo<Replay | null>(() => gen
    ? { id: gen.id, label: gen.label, code: gen.code, engine: gen.engine, project: gen.project }
    : null, [gen]);
  const moduleUrls = useMemo(() => {
    if (!gen) return [];
    const k: EngineKind | "" = gen.engine === "three" || gen.engine === "playcanvas" ? gen.engine : "";
    const urls: string[] = [];
    const push = (p?: string) => {
      if (!p) return;
      const u = api.engineModuleUrl(p, k);
      if (!urls.includes(u)) urls.push(u);
    };
    // The record's own path first; then the game root the rail knows, because a forge called
    // with the workspace path has its node_modules one folder down.
    push(gen.project);
    const p = projectOf({ project: gen.project, project_name: "" }, projects);
    push(p?.root);
    push(p?.path);
    // Vite rewrites three's `./three.core.js` to a URL on its own origin, which is why a running
    // dev server can serve a split build that the backend endpoint cannot.
    if (genDev && k) urls.push(`${genDev.replace(/\/+$/, "")}/node_modules/${MODULE_FILES[k]}`);
    // THE STUDIO'S THREE ON EVERY LIST, PLAYCANVAS INCLUDED. The editor's viewport is three
    // whatever built the asset; a PlayCanvas asset is built in a hidden PlayCanvas app and
    // mirrored into it. Leaving three off this list for PlayCanvas runs meant the viewport found
    // nothing to draw with, and every PlayCanvas asset in the Edit tab read "the asset did not
    // build ... loaded, but it is not three".
    return withStudioThree(urls);
  }, [gen, projects, genDev]);

  // The bench's own engine list. The bench can belong to another project than the selected run,
  // so it cannot borrow `moduleUrls`: a PlayCanvas bench beside a three.js run found no
  // PlayCanvas to build with.
  // A bench of GLB files only is THREE'S whatever the project is built with: a model belongs to
  // no engine, and the viewport's own loader opens it (the rule the Library already follows).
  const benchGlbOnly = !!bench?.models?.length && onlyGlbComments(bench.code);
  const benchEngine: EngineKind = benchGlbOnly ? "three"
    : bench?.engine === "playcanvas" || bench?.engine === "three"
    ? bench.engine : (bench?.code && PC_CODE.test(bench.code) ? "playcanvas" : "three");
  // A string, so the source below is rebuilt when the FILES change and not on every poll. A
  // PlayCanvas bench builds in a hidden PlayCanvas app, which has no loadGLB; its code is kept.
  const benchGlbCode = benchEngine === "three" && bench?.models?.length ? benchModelCode(bench.models) : "";
  const benchUrls = useMemo(() => {
    if (!bench?.project) return moduleUrls;
    const urls: string[] = [];
    const push = (p?: string) => {
      if (!p) return;
      const u = api.engineModuleUrl(p, benchEngine);
      if (!urls.includes(u)) urls.push(u);
    };
    push(bench.project);
    const p = projectOf({ project: bench.project, project_name: "" }, projects);
    push(p?.root);
    push(p?.path);
    // Any project that has this engine at all, last: a bench in a folder with no node_modules
    // of its own still has to be built with SOMETHING of the right kind.
    for (const q of projects) {
      if (q.module === benchEngine || new RegExp(benchEngine, "i").test(q.engine)) { push(q.path); break; }
    }
    return withStudioThree(urls);
  }, [bench?.project, benchEngine, projects, moduleUrls]);

  // What the Edit tab is pointed at. A source FILE wins over the last recorded run, because only
  // a file can be written back to — and the two can come from different projects, so the engine
  // for a file is looked for beside the file rather than beside the generation.
  const editSource = useMemo<EditorSource | null>(() => {
    if (liveProject) {
      const p = projects.find((x) => x.path === liveProject);
      const name = p?.name || liveProject.split(/[\\/]/).filter(Boolean).pop() || "game";
      // The viewport is three whatever the game is, so a three.js build must be found: the game's
      // own when it has one, else any three.js project the Studio knows. The game's scene itself
      // crosses over as data, snapshotted inside its page.
      const urls = [api.engineModuleUrl(liveProject, "three")];
      if (p?.root && p.root !== p.path) urls.push(api.engineModuleUrl(p.root, "three"));
      for (const q of projects) if (q.module === "three" || /three/i.test(q.engine)) { const u = api.engineModuleUrl(q.path, "three"); if (!urls.includes(u)) urls.push(u); break; }
      return {
        kind: "live", code: "", path: "", project: liveProject, label: name + " · live",
        devUrl: p?.dev_url || "", moduleUrls: withStudioThree(urls), engine: "three",
      };
    }
    if (editFile) {
      const file = editFile.path.replace(/\\/g, "/");
      const lower = file.toLowerCase();
      // The project the file sits in, whatever the rail points at.
      const home = projects.find((p) => lower.startsWith(p.path.replace(/\\/g, "/").toLowerCase() + "/"));
      const proj = selP?.path || home?.path || gen?.project || "";
      const urls: string[] = [];
      const push = (p?: string) => {
        if (!p) return;
        const u = api.engineModuleUrl(p, "three");
        if (!urls.includes(u)) urls.push(u);
      };
      // THE OWNING GAME'S OWN ENGINE FIRST. rot-haul and rot-rush each install their own
      // PlayCanvas; the workspace has none. Picking either one at random was picking the wrong
      // one half the time.
      const asset = editFile.asset;
      const assetRoot = String(asset?.root || "");
      const owner = workGames.find((g) => (g.sub || "").toLowerCase() === assetRoot.toLowerCase());
      push(owner?.root);
      push(proj);
      push(selP?.root);
      push(home?.path);
      push(home?.root);
      // A build kept beside the file itself: the A/B harness keeps three.module.js next to its page.
      push(file.replace(/\/[^/]*$/, ""));
      if (detail?.dev_url) urls.push(`${detail.dev_url.replace(/\/+$/, "")}/node_modules/${MODULE_FILES.three}`);
      // Last, any three.js project's engine. A file that takes THREE as an argument is not tied to
      // one install, and refusing to open it because the rail happens to point at a PlayCanvas game
      // would be refusing for no reason the person can see.
      for (const p of projects) if (p.module === "three" || /three/i.test(p.engine)) { push(p.path); break; }
      // And PlayCanvas, for an asset that takes pc: its own project's build first, then any.
      const isPc = (p?: EngineProject | null) => !!p && (p.module === "playcanvas" || /playcanvas/i.test(p.engine));
      const pcProject = isPc(home) ? home : isPc(selP) ? selP : null;
      const pcUrls: string[] = [];
      if (pcProject) pcUrls.push(api.engineModuleUrl(pcProject.path, "playcanvas"));
      for (const p of projects) if (isPc(p) && p !== pcProject) { pcUrls.push(api.engineModuleUrl(p.path, "playcanvas")); break; }
      for (const u of pcUrls) if (!urls.includes(u)) urls.push(u);
      // AND THE STUDIO'S OWN, LAST. This is the list a .glb in a PlayCanvas game arrives on, and
      // without it the model reached the editor and then stopped on "could not load the project's
      // own engine" — true of the GAME, and nothing to do with the file being opened.
      // ONE ROW OF THE ASSET INDEX runs the way a recorded run does: it is a snippet that CALLS
      // the game's code, not the game's code. Imported as a module it would run its top-level
      // await at import time and then export nothing to build with — which is exactly what
      // "the asset did not build" was, a true answer to the wrong question.
      return {
        kind: asset ? "asset" : "file", code: editFile.code, path: editFile.path, project: proj,
        label: asset ? asset.name : editFile.path.replace(/\\/g, "/").split("/").pop() || editFile.path,
        devUrl: owner?.dev_url || detail?.dev_url || "", moduleUrls: withStudioThree(urls),
        // A MODEL IS NEITHER ENGINE'S. `loadGLB` returns a three object, and in a PlayCanvas
        // project this said "playcanvas" for it too - so the editor built it in a hidden
        // PlayCanvas studio, which cannot run loadGLB at all and cannot hold what it returns.
        // Every .glb in every PlayCanvas game opened to an empty grid because of this one word.
        // A file is a file: it loads into the editor's own viewport whatever the game is built
        // with, and shares nothing with the game by doing so.
        // EVERY LIBRARY ENTRY IS THREE'S, not only models. See `editEngineFor`: this line said
        // "playcanvas" for a spec or code row of a PlayCanvas game, and the Library's snippet then
        // ran in a studio with no `buildAsset` - an empty Edit tab with no error, for every
        // procedural asset of every PlayCanvas game.
        engine: editEngineFor(asset, !!pcProject),
        assetRoot, bases: basesFor(assetRoot),
        assetKey: asset ? (asset.key || asset.export || asset.name) : "",
      };
    }
    // THE BENCH WINS OVER THE LAST FINISHED RUN, while watching. A finished run is a photograph
    // of a moment that has already passed; the bench is what the agent has in front of it.
    if (bench?.code) {
      return {
        // The GLB files first: the code of a `clear:false` build was run on top of them.
        kind: "generation", code: benchGlbCode + bench.code, path: "", project: bench.project,
        label: bench.label + " · live", devUrl: genDev || "", moduleUrls: benchUrls,
        // The engine that BUILT it, not a guess. "three" was hard-coded here, so every
        // PlayCanvas bench asked the viewport for a three module and was refused.
        engine: benchEngine,
      };
    }
    if (gen?.code) {
      return {
        kind: "generation", code: gen.code, path: "", project: gen.project,
        label: gen.label || gen.id, devUrl: genDev || "", moduleUrls, engine: gen.engine,
      };
    }
    return null;
  }, [editFile, gen, genDev, moduleUrls, selP, detail, projects, liveProject, workGames, basesFor,
      bench?.sig, bench?.code, bench?.project, bench?.label, benchEngine, benchUrls, benchGlbCode]);

  // The Game tab into the Edit tab: open the game in the shared live tab, and if it is a three.js
  // game whose scene the shim can reach, mirror that scene in the editor.
  const editLive = async () => {
    if (!selP) return;
    setLiveNote("opening " + selP.name + " for editing…");
    try {
      const r = await api.liveOpen(selP.path);
      if (r.error) { setLiveNote(r.error); return; }
      if (r.engine !== "three" && r.engine !== "playcanvas") {
        setLiveNote("Live editing mirrors a three.js or PlayCanvas scene; " + selP.name + " is " + (r.engine || "neither") + ". The live link still reads it.");
        return;
      }
      if (!r.reachable) { setLiveNote(r.hint || "the game's scene is not reachable from the page"); return; }
      setEditFile(null);
      setLiveProject(selP.path);
      setMode("edit");
      setLiveNote("");
    } catch (e: any) {
      setLiveNote(String(e?.message || e).slice(0, 200));
    }
  };

  const notReplayable = !!selected && (selected.kind !== "forge" || !selected.replayable);
  const notice = genLoading ? { text: "reading the generation…" }
    : genErr ? { text: genErr, error: true }
    : gen && genDev === null ? { text: "finding the project's dev server…" }
    : null;
  const games = projects.filter((p) => p.game).length;
  // IN USE means an agent called the tab lately, not that the tab is still open. Every open tab used
  // to be named here, so games whose agents had stopped a day earlier were listed as "in use".
  const busy = engineInUse(state);
  const tabNames = (state?.tabs || []).filter(tabInUse)
    .map((t) => t.project.split(/[\\/]/).filter(Boolean).pop() || t.project);

  return (
    <div className="h-full w-full flex flex-col bg-bg text-text">
      {/* NOTHING LEAVES THE BAR. A narrow window used to push the status pills off the right edge
          and wrap the title into two lines over the panel below it. The tabs and the game switch
          never shrink; everything else is allowed to. */}
      <header className="h-11 shrink-0 flex items-center gap-3 px-3 border-b border-line bg-panel overflow-hidden">
        <Box size={16} className="text-brand shrink-0" />
        <span className="font-semibold text-sm shrink-0 whitespace-nowrap">Studio Engine</span>
        <div className="flex items-center gap-0.5 ml-1 shrink-0">
          <button className={cls("tab", mode === "asset" && "tab-active")} onClick={() => setMode("asset")}
            title="What agents have made: forge renders and review sheets. Pictures of past runs, not the game's own assets.">Runs</button>
          <button className={cls("tab", mode === "edit" && "tab-active")} onClick={() => setMode("edit")}
            title="Move around it, change its parameters, rig it and pose it">Edit</button>
          <button className={cls("tab", mode === "game" && "tab-active")} onClick={() => setMode("game")}>Game</button>
          <button className={cls("tab", mode === "library" && "tab-active")} onClick={() => setMode("library")}
            title="What the game itself has: the species and props its code builds, its model files, its textures, its sprites">
            Assets{assetCount ? <span className="ml-1 text-[10px] text-muted">{assetCount}</span> : null}</button>
        </div>
        {/* WHICH GAME. A workspace here holds rot-haul, rot-rush and the folder around them, and
            they share nothing but a parent: different engines, different ports, different assets.
            Everything below this switch — the shelves, the bottom row, what opens in the editor —
            is that one game. The dot says whether its own server is up, because that is what
            decides whether its code can be fetched at all. */}
        {workGames.filter((g) => g.sub).length > 1 && (
          <div className="flex items-center gap-0.5 rounded-md bg-bg/60 ring-1 ring-line px-0.5 py-0.5 shrink-0">
            <button onClick={() => setGameSub("")}
              title="Every game in this workspace at once"
              className={cls("px-2 py-0.5 rounded text-[11px] whitespace-nowrap",
                gameSub === "" ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}>all</button>
            {workGames.filter((g) => g.sub).map((g) => (
              <button key={g.sub} onClick={() => setGameSub(g.sub)}
                title={g.dev_url ? `${g.root}\nserved at ${g.dev_url}` : `${g.root}\nno dev server running — its code cannot be fetched, so its assets will not build`}
                className={cls("px-2 py-0.5 rounded text-[11px] whitespace-nowrap flex items-center gap-1",
                  gameSub === g.sub ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}>
                <span className={cls("w-1.5 h-1.5 rounded-full", g.dev_url ? "bg-ok" : "bg-muted/40")} />
                {g.sub}
              </button>
            ))}
          </div>
        )}
        {mode === "edit" && (
          <FilePicker project={selP?.path || gen?.project || ""} current={editFile?.path || ""}
            onPick={(p) => { setEditFile(p); if (p) setLiveProject(""); }} />
        )}
        {mode === "edit" && liveProject && (
          <button onClick={() => setLiveProject("")} className="chip text-ok border-ok/40 bg-ok/10"
            title="The editor mirrors the running game. Click to stop.">
            ● live · {projects.find((x) => x.path === liveProject)?.name || "game"} ×
          </button>
        )}
        {mode === "game" && selP?.game && (
          <button onClick={editLive} className="chip" title="Open this running game in the Edit tab: its objects by name, its lights and camera, edits pushed back live">
            Edit live
          </button>
        )}
        {liveNote && <span className="chip text-warn border-warn/40 max-w-[28rem] truncate" title={liveNote}>{liveNote}</span>}

        {state && !apiErr && (
          busy ? (
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] text-ok bg-ok/10 ring-1 ring-ok/30"
              title="An agent is using the engine now">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full rounded-full bg-ok opacity-70 animate-ping" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-ok" />
              </span>
              in use{tabNames.length ? ` · ${tabNames.join(", ")}` : ""}
              {state.recent_generations > 0 && ` · ${state.recent_generations} made in the last 5 min`}
            </span>
          ) : !state.available ? (
            <span className="chip" title={state.why}>engine unavailable</span>
          ) : (
            <span className="chip">idle</span>
          )
        )}

        {/* WHAT THE FORGE IS DOING. Not what it made — what it is making. The measuring half of
            an agent's loop draws no picture on purpose, so without this line a minute of real
            work looks exactly like a minute of nothing. */}
        {acts.slice(0, 2).map((a: any) => (
          <span key={a.project + a.since}
            className={cls("chip flex items-center gap-1.5 max-w-[26rem]",
              a.running ? "text-accent border-accent/40 bg-accent/10" : "text-muted")}
            title={`${a.label || ""}\n${a.detail || ""}\n${a.project_name || ""}`}>
            <Loader2 size={11} className={a.running ? "animate-spin" : ""} />
            <span className="truncate">
              {a.phase}{a.label ? ` · ${a.label}` : ""}{a.detail ? ` · ${a.detail}` : ""}
            </span>
            <span className="text-muted/60 font-mono shrink-0">{a.seconds}s</span>
          </span>
        ))}

        {/* WHO IS WORKING. The window used to show only what had been rendered, so an agent
            modelling for half an hour — in Blender, or thinking through a long turn — left it
            looking completely idle. `moving` is "it wrote in the last 90 seconds"; quiet is not
            stopped, because a long turn writes nothing until the whole message lands. */}
        {(state?.agents || []).map((a) => (
          <span key={a.agent_id}
            className={cls("chip flex items-center gap-1.5 max-w-[24rem]",
              a.moving ? "text-brand border-brand/40 bg-brand/10" : "text-muted")}
            title={`${a.description}\n${a.model || ""}\n${a.tools} tool calls · ${fmtTok(a.tokens)} tokens`}>
            <Loader2 size={11} className={a.moving ? "animate-spin" : ""} />
            <span className="truncate">{a.description || a.agent_id.slice(0, 8)}</span>
            <span className="text-muted/60 font-mono shrink-0">
              {a.phase === "generating" ? "writing" : (a.tool ? a.tool.replace(/^mcp__\w+__/, "") : "")}
            </span>
          </span>
        ))}

        <div className="ml-auto flex items-center gap-2 text-[11px] text-muted">
          <span className="chip">{games} game{games === 1 ? "" : "s"}</span>
          <span className="chip">{total} generation{total === 1 ? "" : "s"}</span>
          <button onClick={onLiveChip}
            title={bench?.code && (mode !== "edit" || editFile || liveProject)
              ? "An agent's bench is live: click to watch it in the 3D viewport, where you can orbit it"
              : "Watch the agent work: every run it makes opens here the moment it happens, in the 3D viewport, where you can orbit it. Click to stop or start watching."}
            className={cls("chip transition-colors", follow ? "text-ok border-ok/40 bg-ok/10" : "hover:text-text")}>
            <Radio size={11} /> {bench ? "live · " + (bench.tris ? bench.tris.toLocaleString() + " tris" : "empty")
              : follow ? "watching" : "watch agent"}
          </button>
          <button onClick={() => setMatchCam(!matchCam)}
            title="Open each run at the angle the agent rendered it from, instead of this window's default view. Orbiting is still yours."
            className={cls("chip transition-colors", matchCam ? "text-brand border-brand/40 bg-brand/10" : "hover:text-text")}>
            <Camera size={11} /> {matchCam ? "agent's angle" : "own angle"}
          </button>
          <button onClick={() => setInspector((v) => !v)} title={inspector ? "Hide the inspector" : "Show the inspector"}
            className={cls("p-1 rounded-md hover:bg-panel2", inspector ? "text-text" : "text-muted")}>
            <PanelRight size={14} />
          </button>
        </div>
      </header>

      {apiErr && <div className="px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30 shrink-0">{apiErr}</div>}
      {state && !state.enabled && (
        <div className="px-3 py-1.5 text-xs text-warn bg-warn/10 border-b border-warn/30 shrink-0">
          The live link is switched off (cc_live), and the engine rides it — nothing new will appear here until it is on.
        </div>
      )}

      <div className="flex-1 min-h-0 flex">
        {railOpen ? (
          <aside className="w-60 shrink-0 border-r border-line bg-panel overflow-y-auto relative">
            <button onClick={() => setRailOpen(false)} title="Fold the project list away"
              className="absolute top-1.5 right-1.5 z-10 p-0.5 rounded text-muted hover:text-text hover:bg-panel2">
              <PanelLeftClose size={13} />
            </button>
            <GameRail projects={projects} selected={selProject} onSelect={setSelProject} counts={railCounts}
              openPaths={openPaths} total={total} loading={projLoading} />
          </aside>
        ) : (
          <button onClick={() => setRailOpen(true)} title="Show the project list"
            className="w-6 shrink-0 border-r border-line bg-panel text-muted hover:text-text flex items-start justify-center pt-2">
            <PanelLeftOpen size={13} />
          </button>
        )}

        <main className="flex-1 min-w-0 relative bg-bg overflow-hidden">
          {mode === "game" ? (
            <GameFrame project={selP} detail={detail} loading={detailLoading} onRefresh={() => loadDetail(selProject)} />
          ) : mode === "library" ? (
            <Library project={selP} game={gameSub} onOpenFile={openInEditor} onOpenAsset={openAsset} />
          ) : mode === "edit" ? (
            <Editor source={editSource} prefs={prefs}
              aim={matchCam && editSource?.kind === "generation" ? agentAim : undefined}
              notice={editSource?.kind === "generation"
                ? "Editing a recorded run: it has no file, so parameter changes are live only. Open the source to save them."
                : ""} />
          ) : notReplayable && selected ? (
            <SheetView item={selected} />
          ) : (
            <AssetViewport replay={genDev === null ? null : replay} moduleUrls={moduleUrls} devUrl={genDev || ""}
              justMade={selected ? isJustMade(selected) : false} notice={notice} onLive={setLive} />
          )}
        </main>

        {inspector && mode !== "edit" && mode !== "library" && (
          <aside className="w-[22rem] shrink-0 border-l border-line bg-panel overflow-y-auto">
            <Inspector item={selected} gen={gen} live={live} loading={genLoading} error={genErr} onTagged={onTagged}
              justMade={selected ? isJustMade(selected) : false} />
          </aside>
        )}
      </div>

      {/* The strip is a lot of screen. In the editor most of it is in the way, so it folds — and
          stays folded, because the choice is remembered. */}
      {/* A BAR OF ITS OWN, rather than two controls floated over the shelf. The shelf switch used
          to sit at the centre of the footer and the fold button at its right, both on top of a row
          that already had a filter bar and a count in exactly those places — so which shelf you
          were on was written across "Review sheets 31". Nothing overlaps now because nothing is
          floated: the bar owns its line, the shelf owns the rest. */}
      <footer className={cls("shrink-0 border-t border-line bg-panel flex flex-col", stripOpen ? "h-56" : "h-7")}>
        <div className="h-6 shrink-0 flex items-center gap-1 px-2 border-b border-line/60">
          {stripOpen ? (
            <>
              {(["runs", "assets"] as const).map((m) => (
                <button key={m} onClick={() => setStripMode(m)}
                  title={m === "runs" ? "Renders and review sheets agents have made"
                                      : "What the game itself has: its species, builders, models and art"}
                  className={cls("px-2 py-0.5 rounded text-[10px] uppercase tracking-wide",
                    stripMode === m ? "bg-brand/15 text-brand" : "text-muted hover:text-text")}>
                  {m}
                </button>
              ))}
              {stripMode === "assets" && gameSub && (
                <span className="text-[10px] text-muted/70 ml-1">{gameSub}</span>
              )}
            </>
          ) : (
            <button onClick={() => setStripOpen(true)}
              className="flex items-center gap-2 text-[11px] text-muted hover:text-text min-w-0">
              <span className="font-semibold tracking-wide">{stripMode === "assets" ? "ASSETS" : "HISTORY"}</span>
              <span>{visible.length} of {total}</span>
              {selected && <span className="truncate opacity-70">· {selected.label}</span>}
            </button>
          )}
          <button onClick={() => setStripOpen(!stripOpen)}
            title={stripOpen ? "Fold the shelf away" : "Show the shelf"}
            className="ml-auto p-0.5 rounded text-muted hover:text-text hover:bg-panel2">
            {stripOpen ? <PanelBottomClose size={13} /> : <PanelBottomOpen size={13} />}
          </button>
        </div>
        {stripOpen && (
          <div className="flex-1 min-h-0">
            {stripMode === "assets" ? (
              <AssetStrip project={selP} game={gameSub} onOpen={openAsset} />
            ) : (
              <HistoryStrip items={visible} total={total} selectedKey={selected ? keyOf(selected) : ""}
                onSelect={(g) => selectItem(g, true)} type={typeF} onType={setTypeF} typeCounts={facets.by_type}
                subject={subjectF} onSubject={setSubjectF} subjectCounts={facets.by_subject} query={query} onQuery={setQuery}
                projectName={selP?.name || ""} onClearProject={() => setSelProject("")} isJustMade={isJustMade}
                canMore={items.length < total && limit < 500} onMore={() => setLimit(500)} loading={histLoading} />
            )}
          </div>
        )}
      </footer>
    </div>
  );
}

/** THE GAME'S OWN THINGS, along the bottom.
 *
 *  The same list the Assets tab shows, in one scrolling row, so it can sit under the viewport
 *  while you build a scene: find a creature, click it, and it opens in the editor. Pictures come
 *  from the same cache the Assets tab fills, so a shelf rendered once is rendered everywhere. */
function AssetStrip({ project, game = "", onOpen }: {
  project: { path: string; name: string } | null;
  /** The game inside the workspace this row is showing, or "" for all of them. */
  game?: string;
  onOpen: (a: any) => void;
}) {
  const [rows, setRows] = useState<any[]>([]);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!project?.path) { setRows([]); return; }
    let dead = false;
    setBusy(true);
    api.engineAssets(project.path, { q, root: game })
      .then((r: any) => { if (!dead) setRows(r.items || []); })
      .catch(() => { if (!dead) setRows([]); })
      .finally(() => { if (!dead) setBusy(false); });
    return () => { dead = true; };
  }, [project?.path, q, game]);

  // Only the ones already rendered: this row must never start a browser on its own, or scrolling
  // past a shelf would cost a dev server. Rendering is always something you ask for — which is
  // why the button below exists, because a row that can only show what something else made would
  // never show anything at all.
  useEffect(() => {
    if (!project?.path || !rows.length) return;
    let dead = false;
    api.engineThumbs(project.path, [], 288)
      .then((r: any) => { if (!dead) setThumbs(r.thumbs || {}); })
      .catch(() => {});
    return () => { dead = true; };
  }, [project?.path, rows.length]);

  const [rendering, setRendering] = useState(false);
  const [note, setNote] = useState("");
  const pending = rows.filter((a: any) => (a.type === "code" || a.type === "spec") && !thumbs[a.id]);
  const render = async () => {
    if (!project?.path || rendering || !pending.length) return;
    setRendering(true);
    setNote(`rendering ${Math.min(40, pending.length)}…`);
    try {
      const r = await api.engineThumbs(project.path, pending.slice(0, 40).map((a: any) => a.id));
      if (!r.ok) { setNote(r.error || "could not render"); return; }
      setThumbs((t) => ({ ...t, ...(r.thumbs || {}) }));
      const left = pending.length - Object.keys(r.thumbs || {}).length;
      setNote(`${Object.keys(r.thumbs || {}).length} rendered`
        + (left > 0 ? ` · ${left} to go` : "")
        + (Object.keys(r.errors || {}).length ? ` · ${Object.keys(r.errors || {}).length} could not be drawn` : ""));
    } catch (e: any) {
      setNote(e?.message || "could not render");
    } finally {
      setRendering(false);
    }
  };

  if (!project) {
    return <div className="h-full flex items-center px-3 text-[11px] text-muted">Pick a game on the left to see its assets.</div>;
  }
  return (
    <div className="h-full flex flex-col">
      <div className="h-7 shrink-0 flex items-center gap-2 px-3 text-[11px] text-muted border-b border-line">
        <span className="font-semibold tracking-wide">ASSETS</span>
        <span className="truncate max-w-[14rem] text-text">{project.name}</span>
        <span>{rows.length}</span>
        {busy && <Loader2 size={11} className="animate-spin" />}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="find an asset…"
          className="ml-2 bg-transparent border-b border-line focus:border-brand/60 outline-none text-[11px] px-1 w-56" />
        {!!pending.length && (
          <button onClick={render} disabled={rendering}
            title="Render a picture of each one by calling the project's own builders. Kept, so this is a one-time cost per shelf."
            className={cls("chip flex items-center gap-1 ml-1", rendering ? "opacity-60" : "hover:text-text")}>
            {rendering ? <Loader2 size={11} className="animate-spin" /> : <Camera size={11} />}
            {rendering ? "rendering" : `render ${Math.min(40, pending.length)} previews`}
          </button>
        )}
        {note && <span className="text-[10px] text-muted truncate max-w-[18rem]" title={note}>{note}</span>}
      </div>
      <div className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden flex items-center gap-2 px-3">
        {rows.slice(0, 300).map((a: any) => (
          <button key={a.id} onClick={() => onOpen(a)} title={`${a.name}\n${a.file}`}
            className="w-28 shrink-0 rounded-lg border border-line hover:border-muted/60 bg-panel2 overflow-hidden text-left">
            <div className="h-14 bg-black/40 flex items-center justify-center overflow-hidden">
              {thumbs[a.id]
                ? <img src={api.engineSheetUrl(thumbs[a.id])} loading="lazy" decoding="async" alt="" className="w-full h-full object-contain" />
                : a.model || a.type === "model"
                ? <ModelViewer src={api.engineModelUrl(project.path, String(a.model || a.file))}
                    className="h-full w-full" />
                : a.type === "image" || a.type === "texture"
                ? <img src={api.wsRaw(a.path, project.path)} loading="lazy" decoding="async" alt="" className="w-full h-full object-cover" />
                : <Box size={14} className="text-muted" />}
            </div>
            <div className="px-1.5 py-1">
              <div className="truncate text-[11px] text-text">{a.name}</div>
              <div className="truncate text-[9px] text-muted">{a.subject}</div>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// A review sheet, or a forge run recorded without its code: a picture is what there is, so the
// viewport shows the picture rather than failing to replay it.
function SheetView({ item }: { item: EngineGen }) {
  return (
    <div className="absolute inset-0 flex flex-col">
      <div className="h-8 shrink-0 flex items-center gap-2 px-3 border-b border-line bg-panel text-[11px] text-muted">
        <ImageIcon size={12} className="shrink-0" />
        <span className="truncate">
          {item.kind === "review"
            ? "A review is a picture of the running game, not code — it cannot be replayed. This is its sheet."
            : "This forge run was recorded without its code, so it cannot be replayed. This is its sheet."}
        </span>
      </div>
      <div className="flex-1 min-h-0 flex items-center justify-center p-3 bg-black/30">
        {item.sheet
          ? <img src={api.engineSheetUrl(item.sheet)} alt={item.label} className="max-w-full max-h-full object-contain rounded-lg border border-line" />
          : <span className="text-xs text-muted">No sheet was kept for this one.</span>}
      </div>
    </div>
  );
}

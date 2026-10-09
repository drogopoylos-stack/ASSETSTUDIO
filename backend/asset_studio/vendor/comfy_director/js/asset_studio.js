const { app } = window.comfyAPI.app;
const params = new URLSearchParams(location.search);
const embedded = params.get("asset-studio-director") === "1" && window.parent !== window;
const studioOrigin = params.get("studio-origin");
const storageKey = "asset-studio:minimax-director:v1";
let graphReady = false;
let opening = false;
let opened = false;

function send(type, data = {}) {
  if (embedded && studioOrigin) window.parent.postMessage({ type: `asset-studio-director:${type}`, ...data }, studioOrigin);
}

function save() {
  if (!opened || opening || !app.graph?._nodes?.some(n => n.type === "MuseMinimaxDirectorV1_4")) return;
  try { localStorage.setItem(storageKey, JSON.stringify(app.graph.serialize())); }
  catch (error) { console.warn("Could not autosave the Asset Studio Director timeline", error); }
}

async function open(workflow, fresh) {
  if (!graphReady || opening || (opened && !fresh)) return;
  opening = true;
  try {
    if (!fresh) {
      try { workflow = JSON.parse(localStorage.getItem(storageKey)) || workflow; }
      catch { /* A damaged autosave falls back to the supplied starter. */ }
    }
    await app.loadGraphData(workflow);
    opened = true;
    const info = await fetch("/object_info").then(r => r.json());
    const missing = [];
    for (const node of app.graph._nodes || []) {
      for (const [loader, field] of [["UNETLoader", "unet_name"], ["CLIPLoader", "clip_name"], ["VAELoader", "vae_name"]]) {
        if (node.type !== loader) continue;
        const value = node.widgets?.find(w => w.name === field)?.value;
        const choices = info[loader]?.input?.required?.[field]?.[0] || [];
        if (!choices.includes(value)) missing.push(value);
      }
    }
    send("loaded", { missing });
  } catch (error) { send("error", { message: `Director could not open: ${error.message}` }); }
  finally { opening = false; save(); }
}

app.registerExtension({
  name: "AssetStudio.MiniMaxDirector",
  setup() {
    if (!embedded || !studioOrigin) return;
    window.addEventListener("message", event => {
      if (event.source !== window.parent || event.origin !== studioOrigin) return;
      if (event.data?.type === "asset-studio-director:open") void open(event.data.workflow, false);
      if (event.data?.type === "asset-studio-director:new") void open(event.data.workflow, true);
    });
    window.addEventListener("beforeunload", save);
    setInterval(save, 2000);
  },
  afterConfigureGraph() {
    if (!embedded || opening || opened) return;
    graphReady = true;
    send("ready");
  },
});

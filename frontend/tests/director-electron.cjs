// Exercise the real embedded ComfyUI + Director in an isolated, hidden window.
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
if (!process.versions.electron) {
  const { spawn } = require("node:child_process");
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "studio-director-test-"));
  const run = spawn(require("electron"), [__filename, profile], {
    env, stdio: "inherit", windowsHide: true,
  });
  const timer = setTimeout(() => { console.error("Director test timed out"); run.kill(); }, 100_000);
  run.on("error", error => { console.error(error); process.exit(1); });
  run.on("exit", code => { clearTimeout(timer); process.exit(code ?? 1); });
} else {
  const { app, BrowserWindow } = require("electron");
  const assert = require("node:assert/strict");
  app.setPath("userData", process.argv[2]);
  app.whenReady().then(async () => {
    const win = new BrowserWindow({ show: false, width: 1600, height: 1000,
      webPreferences: { backgroundThrottling: false, contextIsolation: true } });
    win.webContents.on("console-message", (_event, _level, message) => {
      if (/Director|AssetStudio|Error|error/.test(message)) console.log("browser:", message);
    });
    const domReady = new Promise(resolve => win.webContents.once("dom-ready", resolve));
    void win.loadURL("http://127.0.0.1:8777/video").catch(console.error);
    await domReady;
    console.log("Loaded Asset Studio VIDEO.");
    const waitConnected = async () => {
      const deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        if (await win.webContents.executeJavaScript("document.body.innerText.includes('Connected')")) return;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
      throw new Error(await win.webContents.executeJavaScript("document.body.innerText.slice(0, 1500)"));
    };
    await waitConnected();
    console.log("Director handshake connected.");
    const embedded = win.webContents.mainFrame.frames.find(f => f.url.startsWith("http://127.0.0.1:8188/"));
    assert.ok(embedded, "ComfyUI iframe loaded");
    console.log("Inspecting native timeline and API prompt.");
    const result = await embedded.executeJavaScript(`(async () => {
      const app = window.comfyAPI.app.app;
      const director = app.graph._nodes.find(n => n.type === 'MuseMinimaxDirectorV1_4');
      const values = Object.fromEntries(director.widgets.map(w => [w.name, w.value]));
      return { editor: !!director._museMinimaxEditor, values, prompt: await app.graphToPrompt(),
        nodeCount: app.graph._nodes.length, saved: !!localStorage.getItem('asset-studio:minimax-director:v1') };
    })()`);
    assert.ok(result.editor, "Native Director timeline editor mounted");
    assert.equal(result.values.steps, 20);
    assert.equal(result.values.two_stage_sampling, false);
    assert.equal(result.values.sampler_name, "res_multistep");
    assert.ok(result.saved, "Director has its own autosave");
    const output = path.resolve(__dirname, "../../data/tmp");
    fs.writeFileSync(path.join(output, "director-prompt.json"), JSON.stringify(result.prompt, null, 2));
    fs.writeFileSync(path.join(output, "director-ui.png"), (await win.webContents.capturePage()).toPNG());
    win.webContents.reload();
    await new Promise(resolve => win.webContents.once("dom-ready", resolve));
    await waitConnected();
    console.log("PASS: Director iframe, native timeline, loader connections, defaults, autosave and reload.");
    console.log(JSON.stringify({ nodeCount: result.nodeCount, values: {
      mode: result.values.mode, megapixels: result.values.megapixels, duration: result.values.duration_seconds,
    } }));
    app.exit(0);
  }).catch(error => { console.error(error); app.exit(1); });
}

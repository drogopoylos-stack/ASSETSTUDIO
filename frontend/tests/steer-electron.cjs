// Separate hidden Electron session: no user profile or live backend is touched.
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");
if (!process.versions.electron) {
  const { spawnSync } = require("node:child_process");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "studio-steer-ui-"));
  const html = path.join(dir, "test.html");
  const bundle = path.resolve(__dirname, "../../data/tmp/steer.test.js");
  fs.writeFileSync(html, '<div id="root"></div><script src="' + require("node:url").pathToFileURL(bundle).href + '"></script>');
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const run = spawnSync(require("electron"), [__filename, html, dir], { env, encoding: "utf8", timeout: 30_000, windowsHide: true });
  process.stdout.write(run.stdout || "");
  process.stderr.write(run.stderr || "");
  if (run.error) console.error(run.error);
  process.exit(run.status ?? 1);
} else {
  const { app, BrowserWindow } = require("electron");
  app.setPath("userData", path.join(process.argv[3], "profile"));
  app.whenReady().then(async () => {
    const win = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false, contextIsolation: true } });
    await win.loadFile(process.argv[2]);
    const result = await win.webContents.executeJavaScript("window.__steerDone");
    console.log(result.ok ? result.result : result.error);
    app.exit(result.ok ? 0 : 1);
  }).catch((error) => { console.error(error); app.exit(1); });
}

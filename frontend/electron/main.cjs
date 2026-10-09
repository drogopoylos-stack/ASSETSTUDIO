// Asset Studio — desktop launcher (localhost backend + native window + system tray).
// Keeps the local FastAPI server architecture, but wraps it in a real program:
//   • starts the Python backend on launch (no terminal)
//   • shows it in a native window + a system-tray icon
//   • close → hides to tray (keeps running); Quit from the tray stops everything
//   • optional LAN access so other devices can reach the studio
//   • optional start-on-login
const { app, BrowserWindow, Tray, Menu, shell, dialog, nativeImage, Notification, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");
const os = require("os");
const fs = require("fs");

const BACKEND_PORT = process.env.ASSET_STUDIO_PORT || 8777;
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;
const DEV_URL = process.env.ELECTRON_START_URL;
const ICON_PNG = path.join(__dirname, "icon.png");
const ICON_ICO = path.join(__dirname, "icon.ico");
const TRAY_PNG = path.join(__dirname, "tray.png");

let backendProc = null;
let win = null;
let tray = null;
let isQuitting = false;
let restarting = false;      // true during an intentional restart (tray / LAN toggle / in-app)
let backendPid = 0;          // the pid that really serves — NOT always backendProc.pid
let backendPidAt = 0;        // when that pid was last confirmed, so a stale one is never killed
let downSince = 0;           // when the watchdog first saw the backend unreachable
let watchdog = null;
let quickDeaths = 0;         // backends in a row that died soon after starting (see spawnBackend)

// WHAT THE SHELL DID, AND WHY. The backend log ends mid-line when the backend is killed or crashes,
// so it cannot tell "the watchdog killed it" from "it crashed" from "the disk vanished" — and on
// 2026-10-09 it was the last of these (an NVMe drive dropping off the bus), which looked exactly
// like an unstable app. Every kill, exit and respawn decision is written here with its reason.
function shellLog(msg) {
  try {
    const dir = path.join(path.resolve(__dirname, "..", ".."), "data", "logs");
    fs.mkdirSync(dir, { recursive: true });
    const f = path.join(dir, "shell.log");
    try { if (fs.statSync(f).size > 1_000_000) fs.truncateSync(f, 0); } catch { /* ignore */ }
    fs.appendFileSync(f, `${new Date().toISOString()}  ${msg}\n`);
  } catch { /* logging must never take the shell down */ }
}

// Stable Windows app identity. The taskbar groups a running window under a pinned
// shortcut ONLY when their AppUserModelIDs match — so we set the same id here that the
// shortcut carries (see "Create Desktop Shortcut.ps1"). Without it, Windows spawns a
// separate, un-pinnable electron.exe button; with it, "Pin to taskbar" works and the
// pin relaunches the full app.
const APP_ID = "AssetStudio";
if (process.platform === "win32") app.setAppUserModelId(APP_ID);

// ---- persisted launcher prefs (LAN / start-on-login) ----------------------
function cfgPath() { return path.join(app.getPath("userData"), "launcher.json"); }
function loadCfg() {
  try { return JSON.parse(fs.readFileSync(cfgPath(), "utf-8")); } catch { return { lan: false, openAtLogin: false }; }
}
function saveCfg(c) { try { fs.writeFileSync(cfgPath(), JSON.stringify(c)); } catch { /* ignore */ } }
let cfg = { lan: false, openAtLogin: false };

function ping(url) {
  return new Promise((resolve) => {
    const r = http.get(url, () => resolve(true));
    r.on("error", () => resolve(false));
    r.setTimeout(800, () => { r.destroy(); resolve(false); });
  });
}

// WHICH PROCESS IS REALLY SERVING — and why we have to ask.
//
// On Windows `.venv/Scripts/python.exe` is a virtualenv REDIRECTOR. It launches the real
// interpreter as a child and lives on as its parent. We spawn the redirector, so `backendProc`
// holds the STUB's pid, and the process that owns port 8777 is one level down. Killing the stub
// left the real backend running: /api/health still answered — the survivor answered it — so
// ensureBackend() concluded "already up" and started nothing. Restart was a no-op that reported
// success, and the old code kept serving. /api/health now names the pid that is really serving.
function backendHealth() {
  return new Promise((resolve) => {
    const r = http.get(`${BACKEND_ORIGIN}/api/health`, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => {
        try {
          const j = JSON.parse(body);
          if (j && j.pid) { backendPid = j.pid; backendPidAt = Date.now(); }
          resolve(j || null);
        } catch { resolve(null); }
      });
    });
    r.on("error", () => resolve(null));
    // 2.5 s, not 0.8: the route answers in milliseconds, but a machine busy with a game build or
    // a big graph load can stall a probe for a second, and one slow answer is not a hang.
    r.setTimeout(2500, () => { r.destroy(); resolve(null); });
  });
}

// Signal ONE process. Never a tree kill: the Claude sessions are children of the backend, and
// they are meant to outlive it — spawned breakaway, with their stdout on a log file rather than
// a pipe, so the next start-up adopts them still running. `taskkill /T` here would end every
// agent the user has going, which is the one thing a restart must not do.
function killPid(pid) {
  if (!pid) return;
  try { process.kill(pid); } catch { /* already gone */ }
}

function backendDir() { return path.join(path.resolve(__dirname, "..", ".."), "backend"); }
function pythonExe() {
  const b = backendDir();
  return process.platform === "win32"
    ? path.join(b, ".venv", "Scripts", "python.exe")
    : path.join(b, ".venv", "bin", "python");
}

function backendLogFd() {
  // Capture the backend's stdout+stderr so a crash is actually diagnosable (it used to be
  // discarded with stdio:"ignore", so we never knew WHY it died).
  try {
    const dir = path.join(path.resolve(__dirname, "..", ".."), "data");
    fs.mkdirSync(dir, { recursive: true });
    const p = path.join(dir, "backend.log");
    try { if (fs.statSync(p).size > 2_000_000) fs.truncateSync(p, 0); } catch { /* ignore */ }
    return fs.openSync(p, "a");
  } catch { return null; }
}

function spawnBackend() {
  if (backendProc) return;                        // never double-spawn
  const env = { ...process.env, ASSET_STUDIO_PORT: String(BACKEND_PORT) };
  if (cfg.lan) env.ASSET_STUDIO_HOST = "0.0.0.0"; // serve to the local network
  try {
    const fd = backendLogFd();
    const proc = spawn(pythonExe(), ["-m", "asset_studio.main"], {
      cwd: backendDir(), env,
      stdio: fd != null ? ["ignore", fd, fd] : "ignore",
      windowsHide: true,
    });
    backendProc = proc;
    const startedAt = Date.now();
    proc.on("exit", (code, signal) => {
      if (backendProc === proc) backendProc = null;   // only clear if it's still the current one
      const lived = Date.now() - startedAt;
      shellLog(`backend exited: code=${code} signal=${signal} after ${Math.round(lived / 1000)}s`
        + (isQuitting || restarting ? " (intended)" : ""));
      if (isQuitting || restarting) return;
      // It stopped on its own → bring it back. A backend that dies within 15 s of starting, again and
      // again, is not going to be fixed by starting it faster: on 2026-10-09 it was the disk the app
      // lives on vanishing, and the old fixed 4 s retry crashed it five times in thirty seconds. So
      // the wait doubles each time, up to a minute, and resets once one survives.
      quickDeaths = lived < 15000 ? quickDeaths + 1 : 0;
      const delay = quickDeaths ? Math.min(60000, 2000 * 2 ** quickDeaths) : 500;
      if (quickDeaths >= 3) shellLog(`backend died ${quickDeaths} times in a row soon after start — `
        + "check data/backend.log and the Windows System log (disk errors?)");
      shellLog(`respawning in ${Math.round(delay / 1000)}s`);
      setTimeout(async () => {
        if (isQuitting || restarting || backendProc) return;
        // NEVER ADD A BACKEND TO ONE THAT IS ALREADY SERVING.
        //
        // Our child can die while a DIFFERENT backend holds the port and is perfectly healthy —
        // it happens whenever a restart is started from somewhere else, and the virtualenv stub
        // makes it likelier still, because the stub can exit while the interpreter beneath it
        // lives on. Respawning then produced a process that could only ever fail to bind, whose
        // exit brought us straight back here: a loop that ran twenty-six times, and reloaded the
        // window on every pass. If something healthy is answering, there is nothing to do.
        if (await backendHealth()) return;
        spawnBackend();
        reloadWhenHealthy();
      }, delay);
    });
  } catch (e) {
    console.error("Failed to spawn backend:", e);
  }
}

async function reloadWhenHealthy() {
  // Wait for the (re)spawned backend to answer, then refresh the window so the SPA reconnects
  // cleanly. The conversation lives on disk, so this is just your Ctrl+R — automated.
  //
  // RELOAD THE PAGE, DO NOT NAVIGATE TO THE ORIGIN. `loadURL(BACKEND_ORIGIN)` goes to "/", and
  // the tab is read from the path — so every recovery threw the user from wherever they were
  // back to the Dashboard. With a backend flapping, that happened again and again, which is
  // precisely how it looked: taken to the Dashboard repeatedly, for no reason on screen.
  for (let i = 0; i < 60; i++) {
    if (await backendHealth()) {
      if (win && !win.isDestroyed()) {
        try { await win.webContents.session.clearCache(); } catch { /* ignore */ }
        try {
          const url = win.webContents.getURL() || "";
          if (url.startsWith(BACKEND_ORIGIN)) win.reload();     // keeps /workspace, /mission, …
          else await win.loadURL(BACKEND_ORIGIN);               // splash or error page → go home
        } catch { /* ignore */ }
      }
      return true;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function startWatchdog() {
  if (watchdog) return;
  // Catch HANGS too (process alive but the event loop is blocked, so on('exit') never fires):
  // if /api/health is unreachable for ~6s, hard-recover. With on('exit') auto-respawn, the
  // backend can never stay down on its own — no manual restart, no Ctrl+R.
  watchdog = setInterval(async () => {
    if (isQuitting || restarting) return;
    // backendHealth rather than ping: the same 3s poll keeps the real serving pid current, so a
    // restart always has an up-to-date target and never has to guess.
    if (await backendHealth()) { downSince = 0; return; }
    if (!downSince) { downSince = Date.now(); return; }
    // 20 s, not 6. The old window was two missed probes: a backend parsing a big transcript or
    // loading a large code graph for a few seconds was killed as "hung" — and every agent turn in
    // flight with it. A real hang is still caught; a busy moment no longer is.
    if (Date.now() - downSince >= 20000) {
      shellLog(`watchdog: no health answer for ${Math.round((Date.now() - downSince) / 1000)}s — `
        + `killing backend (stub ${backendProc ? backendProc.pid : 0}, serving pid ${backendPid})`);
      downSince = 0;
      // killBackend, not backendProc.kill(): the child we hold is the virtualenv STUB, and killing
      // only the stub left a hung interpreter holding port 8777, so the respawn could not bind.
      killBackend();
      spawnBackend();
      reloadWhenHealthy();
    }
  }, 3000);
}

async function ensureBackend() {
  // backendHealth, not ping: answering is not enough — we also need to know WHO answered, or a
  // later restart has no target. stopBackend() has already made sure nothing is left serving,
  // so "already up" here means someone else legitimately started it.
  if (await backendHealth()) return true;
  spawnBackend();
  for (let i = 0; i < 50; i++) {
    if (await backendHealth()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function killBackend() {
  const stub = backendProc ? backendProc.pid : 0;
  if (backendProc) { try { backendProc.kill(); } catch { /* ignore */ } backendProc = null; }
  // Only a pid confirmed in the last 30s. Windows recycles pids, and killing a number we last
  // saw an hour ago could end something else entirely. stopBackend() re-reads health anyway, so
  // the fresh answer is what does the real work; this is the head start.
  const fresh = backendPid && Date.now() - backendPidAt < 30000;
  if (fresh && backendPid !== stub) killPid(backendPid);        // the one that owns the port
  backendPid = 0;
  backendPidAt = 0;
}

// Stop it, and KEEP GOING UNTIL NOTHING ANSWERS. Whoever is still serving names itself in
// /api/health, so a survivor cannot hide: it is asked, then ended, then asked again.
async function stopBackend(maxMs = 8000) {
  killBackend();
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    const h = await backendHealth();
    if (!h) return true;                       // the port is quiet — it is really down
    if (h.pid) killPid(h.pid);                 // still answering: end that one too
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

async function restartBackend() {
  restarting = true;                           // suppresses BOTH the exit-respawn and the watchdog
  let ok = false;
  try {
    await stopBackend();
    await new Promise((r) => setTimeout(r, 400));   // let Windows release the listening socket
    ok = await ensureBackend();
  } catch { /* fall through to the reload decision */ } finally {
    restarting = false;
  }
  // Only reload on success. Reloading onto a backend that never came back just replaces one
  // confusing screen with a blank one.
  if (ok && win && !win.isDestroyed()) win.reload();
  return ok;
}

function lanAddress() {
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const ni of nets[name] || []) {
      if (ni.family === "IPv4" && !ni.internal) return ni.address;
    }
  }
  return null;
}

// ---- window + tray --------------------------------------------------------
const SPLASH = "data:text/html," + encodeURIComponent(
  `<body style="margin:0;height:100vh;background:#0b0d12;color:#e6e9f0;font-family:'Segoe UI',system-ui,sans-serif;display:flex;align-items:center;justify-content:center">
   <div style="text-align:center"><div style="font-size:24px;font-weight:600;letter-spacing:.3px">Asset Studio</div>
   <div style="margin-top:12px;color:#8b93a7">Starting…</div></div></body>`);
const ERROR_HTML = "data:text/html," + encodeURIComponent(
  `<body style="background:#0b0d12;color:#e6e9f0;font-family:sans-serif;padding:40px">
   <h2>Backend didn't start</h2><p style="color:#8b93a7">Check that backend/.venv exists (run setup.ps1).</p></body>`);

// force the window to the FOREGROUND — a background-launched process can't normally
// steal focus on Windows, so briefly pin always-on-top to pull it up un-minimized.
function focusFront() {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.setAlwaysOnTop(true);
  win.focus();
  win.setAlwaysOnTop(false);
  win.moveTop();
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1480, height: 940, minWidth: 1100, minHeight: 700,
    backgroundColor: "#0b0d12", title: "Asset Studio",
    icon: fs.existsSync(ICON_ICO) ? ICON_ICO : ICON_PNG,
    autoHideMenuBar: true, show: false,
    webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload.cjs") },
  });
  // Local single-user app on its own origin — grant permissions (microphone for voice input, etc.).
  try {
    const ses = win.webContents.session;
    ses.setPermissionRequestHandler((_wc, _permission, cb) => cb(true));
    if (ses.setPermissionCheckHandler) ses.setPermissionCheckHandler(() => true);
  } catch { /* ignore */ }
  win.webContents.setWindowOpenHandler(({ url }) => {
    // same-origin app routes (e.g. a detached Workspace) open as a real native window;
    // anything external goes to the system browser.
    const internal = url.startsWith(BACKEND_ORIGIN) || (DEV_URL && url.startsWith(DEV_URL));
    if (internal) {
      // The engine is a viewport with a project rail, an inspector and a history strip beside it.
      // At the detached-Workspace size those four columns have nowhere to go, so it gets its own
      // geometry and its own name in the taskbar — it reads as a separate program, which is what
      // it is.
      const isEngine = /\/engine(\?|#|$)/.test(url);
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          width: isEngine ? 1560 : 1240, height: isEngine ? 980 : 840,
          minWidth: isEngine ? 980 : 820, minHeight: isEngine ? 640 : 560,
          backgroundColor: "#0b0d12",
          title: isEngine ? "Studio Engine" : "Asset Studio",
          autoHideMenuBar: true,
          icon: fs.existsSync(ICON_ICO) ? ICON_ICO : ICON_PNG,
          webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload.cjs") },
        },
      };
    }
    shell.openExternal(url); return { action: "deny" };
  });
  win.on("close", (e) => { if (!isQuitting) { e.preventDefault(); win.hide(); } });
  clearFlashOnFocus();

  // show the full window immediately (maximized + focused) with a splash, THEN load
  await win.loadURL(SPLASH);
  win.maximize();
  focusFront();

  if (DEV_URL) {
    await win.loadURL(DEV_URL);
    win.webContents.openDevTools({ mode: "detach" });
  } else {
    const ok = await ensureBackend();
    // flush any stale HTTP cache so a rebuilt UI loads immediately (no manual Ctrl+R needed)
    try { await win.webContents.session.clearCache(); } catch { /* ignore */ }
    await win.loadURL(ok ? BACKEND_ORIGIN : ERROR_HTML);
    startWatchdog();   // keep the backend alive for the whole session — never let it stay down
  }
  if (!win.isVisible() || win.isMinimized()) focusFront();
}

function showWindow() {
  if (!win || win.isDestroyed()) { createWindow(); return; }
  focusFront();
}

// --- turn-end notifications -------------------------------------------------
// A run can take ten minutes and the whole point of the app is that you go and do something
// else. In-app toasts (Toasts.tsx) only exist while you are looking at the window, which is
// exactly when you did not need telling. This reaches the OS instead.
//
// The renderer decides WHETHER to notify (it owns the setting and the busy->idle edge); this
// side only decides HOW, because Notification and flashFrame are main-process APIs.
// The in-app "Restart backend" button. It used to POST to the backend, which re-exec'd ITSELF —
// and that fought this supervisor: the real process ending made the virtualenv stub exit, the
// exit handler respawned a backend with `restarting` false, and the 6s health watchdog could add
// a third. Several processes then raced for port 8777 and the card sat on its spinner. Going
// through here instead sets `restarting`, which silences both recovery paths for the duration.
ipcMain.handle("studio:restart-backend", async () => {
  try {
    const ok = await restartBackend();
    return { ok, error: ok ? "" : "The backend did not come back." };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
});

ipcMain.handle("studio:notify", (_evt, payload) => {
  const { title, body } = payload || {};
  if (!title) return false;
  const focused = !!win && !win.isDestroyed() && win.isFocused();
  try {
    if (Notification.isSupported()) {
      const n = new Notification({
        title: String(title).slice(0, 120),
        body: String(body || "").slice(0, 400),
        icon: fs.existsSync(ICON_PNG) ? ICON_PNG : undefined,
        silent: false,
      });
      // Clicking it should land you back in the app, not just dismiss the toast.
      n.on("click", () => { try { showWindow(); } catch { /* window went away */ } });
      n.show();
    }
  } catch { /* a failed toast must never break the caller's turn */ }
  try {
    // Only flash when the window is NOT focused. Flashing a window the user is already
    // looking at is pure noise, and on Windows it keeps blinking until focus changes.
    if (!focused && win && !win.isDestroyed()) win.flashFrame(true);
  } catch { /* ignore */ }
  return true;
});

// Stop the taskbar blinking the moment the user actually looks at the window.
function clearFlashOnFocus() {
  if (!win || win.isDestroyed()) return;
  win.on("focus", () => { try { win.flashFrame(false); } catch { /* ignore */ } });
}

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { label: "Open Asset Studio", click: showWindow },
    { label: "Open in browser", click: () => shell.openExternal(BACKEND_ORIGIN) },
    { type: "separator" },
    {
      label: "Allow access from my network (LAN)", type: "checkbox", checked: cfg.lan,
      click: async (item) => {
        cfg.lan = item.checked; saveCfg(cfg);
        await restartBackend();
        const ip = lanAddress();
        if (cfg.lan && ip) {
          dialog.showMessageBox({
            type: "info", title: "Network access on",
            message: "Other devices on your network can now open the studio at:",
            detail: `http://${ip}:${BACKEND_PORT}`,
          });
        }
        refreshTray();
      },
    },
    {
      label: "Start when I log in", type: "checkbox", checked: cfg.openAtLogin,
      click: (item) => {
        cfg.openAtLogin = item.checked; saveCfg(cfg);
        app.setLoginItemSettings({ openAtLogin: cfg.openAtLogin });
      },
    },
    { type: "separator" },
    { label: "Restart backend", click: () => restartBackend() },
    { label: "Quit Asset Studio", click: () => { isQuitting = true; killBackend(); app.quit(); } },
  ]);
}
function refreshTray() {
  if (tray) tray.setContextMenu(buildTrayMenu());
}

function createTray() {
  const img = nativeImage.createFromPath(fs.existsSync(TRAY_PNG) ? TRAY_PNG : ICON_PNG);
  tray = new Tray(img);
  tray.setToolTip("Asset Studio");
  tray.setContextMenu(buildTrayMenu());
  tray.on("click", showWindow);
  tray.on("double-click", showWindow);
}

// ---- app lifecycle (single instance) --------------------------------------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", showWindow); // launching again just focuses the window

  // native right-click menu (cut/copy/paste/select-all) in any text field — the chat
  // box, rename inputs, search, etc. Applies to the main window AND every detached one.
  app.on("web-contents-created", (_e, contents) => {
    contents.on("context-menu", (_ev, params) => {
      const f = params.editFlags || {};
      const tmpl = [];
      if (params.isEditable) {
        tmpl.push(
          { role: "cut", enabled: !!f.canCut },
          { role: "copy", enabled: !!f.canCopy },
          { role: "paste", enabled: !!f.canPaste },
          { type: "separator" },
          { role: "selectAll" },
        );
      } else if (params.selectionText && params.selectionText.trim()) {
        tmpl.push({ role: "copy" }, { type: "separator" }, { role: "selectAll" });
      }
      if (tmpl.length) {
        const w = BrowserWindow.fromWebContents(contents);
        Menu.buildFromTemplate(tmpl).popup(w ? { window: w } : {});
      }
    });
  });

  app.whenReady().then(() => {
    cfg = loadCfg();
    if (process.platform === "win32" || process.platform === "darwin") {
      app.setLoginItemSettings({ openAtLogin: !!cfg.openAtLogin });
    }
    createTray();
    createWindow();
    if (Notification.isSupported()) {
      new Notification({ title: "Asset Studio is running", body: "It lives in your system tray — click the icon anytime." }).show();
    }
  });

  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); else showWindow(); });
  app.on("before-quit", () => { isQuitting = true; killBackend(); });
  app.on("window-all-closed", () => { /* keep running in tray; quit only from the tray */ });
}

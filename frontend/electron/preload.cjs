// Minimal, safe bridge for the renderer. Electron 32+ removed File.path, so we expose
// webUtils.getPathForFile to resolve a dropped file/folder's real disk path — that lets
// the Workspace drag-and-drop do an efficient server-side copy (folders included).
const { contextBridge, webUtils, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("studioBridge", {
  getPathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file) || "";
    } catch {
      return "";
    }
  },
  // Native OS toast + taskbar flash when a turn finishes. The renderer owns the decision
  // (setting on/off, busy->idle edge); this only carries it across, because Notification and
  // flashFrame live in the main process. Resolves false rather than throwing if anything is
  // wrong, so a caller can treat "no notification" as an ordinary outcome — and in a plain
  // browser window.studioBridge is simply undefined, which callers must handle anyway.
  notify: (title, body) => {
    try {
      return ipcRenderer.invoke("studio:notify", { title, body });
    } catch {
      return Promise.resolve(false);
    }
  },
  // Restart the backend through the LAUNCHER rather than through the backend itself.
  //
  // The backend cannot restart itself cleanly here: it would have to end the process the
  // launcher is supervising, and the launcher's own recovery — respawn on exit, plus a 6s health
  // watchdog — would then start a competing backend while the replacement was still binding the
  // port. Doing it from the main process sets a flag that silences both, so exactly one backend
  // comes back. The window is reloaded on success, which is what makes Ctrl+R unnecessary.
  //
  // Resolves { ok, error }. Undefined in a plain browser, where callers fall back to the HTTP
  // endpoint — there is no supervisor there to race with.
  restartBackend: () => {
    try {
      return ipcRenderer.invoke("studio:restart-backend");
    } catch {
      return Promise.resolve({ ok: false, error: "the launcher did not answer" });
    }
  },
});

---
name: electron-native-file-ops
description: Use when adding native file/folder features to an Electron app — OS drag-and-drop, detachable same-origin windows, and native right-click cut/copy/paste menus (with Electron 32+ caveats).
metadata:
  category: backend
  updated: 2026-06-17
  confidence: verified
  source: experience + Electron docs
disable-model-invocation: true
---

# Electron Native File Operations (v32+)

Patterns for OS-integrated file features in an Electron + web-UI app, with the **Electron 32+ breaking changes** baked in.

## Drag-and-drop from the OS — get the real disk path
**Electron 32 removed `File.path`.** A dropped `File` no longer exposes its path, so a naive `file.path` is `undefined`. Resolve it with `webUtils.getPathForFile()` exposed through a **preload bridge**:

```js
// preload.cjs
const { contextBridge, webUtils } = require("electron");
contextBridge.exposeInMainWorld("bridge", {
  getPathForFile: (f) => { try { return webUtils.getPathForFile(f) || ""; } catch { return ""; } },
});
```
```js
// main: BrowserWindow webPreferences
webPreferences: { contextIsolation: true, preload: path.join(__dirname, "preload.cjs") }
```
```ts
// renderer onDrop
const srcPath = window.bridge?.getPathForFile?.(file) || (file as any).path /* <32 */ || "";
```
With a real path, have the **backend copy it server-side** (works for folders too, recursively). Without a path you only get file *bytes* in the browser — fine for files, but folders can't be read that way.

## Detachable / multi-window — open app routes as native windows
By default `setWindowOpenHandler` is often wired to send everything to the external browser. To let `window.open('/route')` spawn a **native** child window for same-origin routes (while external links still go to the browser):

```js
win.webContents.setWindowOpenHandler(({ url }) => {
  if (url.startsWith(APP_ORIGIN)) {
    return { action: "allow", overrideBrowserWindowOptions: { /* width, height, webPreferences w/ preload */ } };
  }
  shell.openExternal(url); return { action: "deny" };
});
```
The opener window is untouched, so you get independent detached windows.

## Native right-click clipboard menu (cut / copy / paste)
Electron shows **no context menu by default**, so text fields feel broken. Add one for every window (main *and* detached) via `web-contents-created`:

```js
app.on("web-contents-created", (_e, contents) => {
  contents.on("context-menu", (_ev, p) => {
    const t = [];
    if (p.isEditable) {
      t.push({ role: "cut", enabled: p.editFlags.canCut },
             { role: "copy", enabled: p.editFlags.canCopy },
             { role: "paste", enabled: p.editFlags.canPaste },
             { type: "separator" }, { role: "selectAll" });
    } else if (p.selectionText && p.selectionText.trim()) {
      t.push({ role: "copy" }, { type: "separator" }, { role: "selectAll" });
    }
    if (t.length) Menu.buildFromTemplate(t).popup({ window: BrowserWindow.fromWebContents(contents) });
  });
});
```

## Reminder
Changes to the Electron **main process** (preload, window handlers, menus) require a full **app restart** — a renderer reload (Ctrl+R) is not enough.

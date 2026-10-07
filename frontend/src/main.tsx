import React from "react";
import ReactDOM from "react-dom/client";
import "@google/model-viewer";
import App from "./App";
import "./index.css";
import { connectWS } from "./api/ws";
import { initSkin, initTheme } from "./theme";
import { mirrorUiPrefs, seedUiPrefs } from "./uiPrefs";

initTheme();
initSkin();
connectWS();
// A new PC takes the UI preferences its settings brought with it; the desktop app keeps them
// current in settings.json. See uiPrefs.ts.
seedUiPrefs();
mirrorUiPrefs();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

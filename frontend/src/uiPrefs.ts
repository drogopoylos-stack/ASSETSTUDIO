// THE UI'S OWN SETTINGS TRAVEL WITH settings.json.
//
// The theme, the skin, the fonts, the status bar and the composer's defaults are kept in
// localStorage, and localStorage never leaves the PC it was written on. The installer carries
// settings.json to a new PC as its first-run seed - so a new PC used to start with the Studio's
// settings and none of these. The desktop app now mirrors them into settings.json as `ui_prefs`;
// the first start on a new PC writes them back into localStorage and reloads once, before anyone
// has touched anything.
//
// ONLY THE DESKTOP APP MIRRORS. The Studio's own headless browser loads this UI too (the reviews
// photograph it) with an empty localStorage, and its defaults must never overwrite the user's.
import { api } from "./api/client";

// Preferences, not state: no open tabs, drafts, pins or per-folder choices (their keys hold paths
// that mean nothing on another PC).
const EXACT = new Set([
  "asset-studio-theme", "asset-studio-skin",
  "mc-font", "ws-font", "show-statusbar", "status-items", "status-items-known", "status-size",
  "studio-enabled-tabs", "mc-notify", "mc-auto",
  "cc-agent", "cc-fork", "cc-thinking", "cc-output-style", "cc-output-style-last",
  "cc-autolearn", "cc-memory", "cc-graphify", "cc-web-tools", "cc-voice-task", "cc-companions",
  "cc-boost",
  "cc-maxh-card", "cc-maxh-panel", "askai-model",
  "engine.rail", "engine.strip", "engine.matchCam", "engine.stripMode",
]);
// An agent's own default - `cc-model-claude`, `cc-mode-codex` - and never a folder's, whose key
// carries the folder's id and so has more dashes.
const PER_AGENT = /^cc-[a-z]+-[a-z0-9]+$/;
const SEEDED = "ui-prefs-seeded";

export function isUiPref(key: string): boolean {
  return EXACT.has(key) || PER_AGENT.test(key);
}

function snapshot(): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (!k || !isUiPref(k)) continue;
    const v = localStorage.getItem(k);
    if (v !== null) out[k] = v;
  }
  return out;
}

/** First start on a PC: take the preferences the settings brought with them. Once.
 *
 *  FROM THE INSTALLER, THE SEED WINS OVER WHAT THIS FIRST LOAD WROTE. initTheme() and initSkin()
 *  store the default ("midnight", "studio") before this answer arrives, so "only fill what is
 *  missing" kept the default theme on a new PC - found by installing the .exe into an empty
 *  profile: the fonts and the status bar came across, the theme did not.
 *  ONLY from the installer (its seed carries ui_prefs_install_seed): anywhere else ui_prefs was
 *  mirrored from a window that may have changed since, and overwriting would take back a choice
 *  the user made after the last mirror. There it only fills what is missing.
 *  Returns how many values it changed. */
export async function seedUiPrefs(
  load: () => Promise<any> = () => api.settings(),
  store: Storage = localStorage,
  reload: () => void = () => location.reload(),
): Promise<number> {
  try {
    if (store.getItem(SEEDED)) return 0;
    const st = await load();
    store.setItem(SEEDED, "1");
    const seed = st && typeof st.ui_prefs === "object" ? st.ui_prefs : null;
    if (!seed) return 0;
    const fromInstaller = !!st.ui_prefs_install_seed;
    let wrote = 0;
    for (const [k, v] of Object.entries(seed)) {
      if (!isUiPref(k) || typeof v !== "string") continue;
      const cur = store.getItem(k);
      if (cur === v || (cur !== null && !fromInstaller)) continue;
      store.setItem(k, v);
      wrote++;
    }
    // The store read localStorage when it loaded, before this answer came back.
    if (wrote) reload();
    return wrote;
  } catch { return 0; /* the next start tries again */ }
}

/** Keep settings.json's copy of them current - from the desktop app only. */
export function mirrorUiPrefs(): void {
  if (!(window as any).studioBridge) return;
  let last = "";
  const push = () => {
    try {
      const snap = snapshot();
      const s = JSON.stringify(snap);
      if (s === last || s === "{}") return;
      last = s;
      api.updateSettings({ ui_prefs: snap }).catch(() => { last = ""; });
    } catch { /* private mode or a closing window */ }
  };
  setTimeout(push, 8000);
  setInterval(push, 30000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") push(); });
}

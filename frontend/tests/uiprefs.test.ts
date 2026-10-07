// A new PC starts with the window of the PC the installer was built on: theme, skin, fonts, the
// bottom bar, each agent's model. They travel in settings.json as ui_prefs and are written into
// localStorage on the first start.
//
// The rule this holds: THE SEED WINS OVER WHAT THE FIRST LOAD WROTE. initTheme() stores the
// default theme before the settings answer arrives, and the first version only filled keys that
// were missing - so on a real install the fonts and the status bar came across and the theme
// stayed "midnight". Found by installing the .exe into an empty profile, not by a test.
//
// Run: npm run test:uiprefs

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { isUiPref, seedUiPrefs } from "../src/uiPrefs";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

function storage(init: Record<string, string> = {}): Storage {
  const m = new Map<string, string>(Object.entries(init));
  return {
    get length() { return m.size; },
    clear: () => m.clear(),
    getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    removeItem: (k: string) => { m.delete(k); },
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
  } as Storage;
}

const SEED = {
  ui_prefs_install_seed: true,               // build_installer.py marks the seed it ships
  ui_prefs: {
    "asset-studio-theme": "black", "asset-studio-skin": "studio", "ws-font": "15",
    "status-items": "[\"cpu\",\"gpu\"]", "cc-model-claude": "claude-opus-5-5", "cc-mode-claude": "bypassPermissions",
    "cc-draft-c--Users-me-game": "a half-written message", "workspace-last": "C:\\Users\\me\\game",
  },
};

(async () => {
  console.log("The first start on a new PC");
  // What the first load has already written before the answer comes: initTheme()'s default.
  let s = storage({ "asset-studio-theme": "midnight", "asset-studio-skin": "studio" });
  let reloads = 0;
  let n = await seedUiPrefs(async () => SEED, s, () => reloads++);
  ok("the carried theme replaces the default the first load wrote", s.getItem("asset-studio-theme") === "black",
     String(s.getItem("asset-studio-theme")));
  ok("fonts, the bottom bar and each agent's model and mode come too",
     s.getItem("ws-font") === "15" && s.getItem("status-items") === "[\"cpu\",\"gpu\"]"
     && s.getItem("cc-model-claude") === "claude-opus-5-5" && s.getItem("cc-mode-claude") === "bypassPermissions");
  ok("a value that is already right is not counted as a change", n === 5, String(n));
  ok("the window reloads once, so the store reads them", reloads === 1, String(reloads));
  ok("a folder's own key or a draft never travels",
     s.getItem("cc-draft-c--Users-me-game") === null && s.getItem("workspace-last") === null);
  ok("...by the same rule the mirror uses", !isUiPref("cc-draft-c--Users-me-game") && isUiPref("cc-mode-claude"));

  console.log("\nOnce, and never again");
  s.setItem("asset-studio-theme", "slate");                       // the user changes it afterwards
  n = await seedUiPrefs(async () => SEED, s, () => reloads++);
  ok("a later start leaves the user's own choice alone", s.getItem("asset-studio-theme") === "slate" && n === 0);
  ok("...and does not reload", reloads === 1, String(reloads));

  console.log("\nNot from the installer: a mirror of a window that may have changed since");
  const { ui_prefs_install_seed: _drop, ...MIRRORED } = SEED;
  s = storage({ "asset-studio-theme": "slate", "ws-font": "13" });  // this window's own, newer choices
  reloads = 0;
  n = await seedUiPrefs(async () => MIRRORED, s, () => reloads++);
  ok("an existing choice is never taken back by an older mirror",
     s.getItem("asset-studio-theme") === "slate" && s.getItem("ws-font") === "13",
     String(s.getItem("asset-studio-theme")) + " / " + String(s.getItem("ws-font")));
  ok("...while what the window does not have yet is filled in", s.getItem("cc-model-claude") === "claude-opus-5-5"
     && s.getItem("status-items") === "[\"cpu\",\"gpu\"]" && s.getItem("asset-studio-skin") === "studio"
     && n === 4 && reloads === 1, "n=" + n);                     // skin, status-items, model, mode

  console.log("\nNothing carried");
  s = storage();
  reloads = 0;
  n = await seedUiPrefs(async () => ({ cc_live: true }), s, () => reloads++);
  ok("settings with no ui_prefs: nothing written, no reload, marked done", n === 0 && reloads === 0
     && s.getItem("ui-prefs-seeded") === "1");
  s = storage({ "asset-studio-theme": "black" });
  n = await seedUiPrefs(async () => ({ ui_prefs: { "asset-studio-theme": "black" } }), s, () => reloads++);
  ok("everything already as carried: no reload", n === 0 && reloads === 0);
  s = storage();
  n = await seedUiPrefs(async () => { throw new Error("backend not up yet"); }, s, () => reloads++);
  ok("the backend not answering: nothing marked, so the next start tries again",
     n === 0 && s.getItem("ui-prefs-seeded") === null);

  console.log("\nWhat only the desktop app does");
  const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");
  ok("only the desktop app mirrors its preferences into settings.json",
     /if \(!\(window as any\)\.studioBridge\) return;/.test(src("uiPrefs.ts")));
  ok("only the desktop app opens the Claude login console by itself - never the headless browser",
     /needs_login && autoRan\.current !== s\.since && \(window as any\)\.studioBridge/.test(src("components/ClaudeLoginModal.tsx")),
     "the installed copy, opened headless on an empty profile, put a login console on the desktop");

  console.log("\n  " + pass + " passed, " + fails.length + " failed");
  for (const f of fails) console.log("  FAIL  " + f);
  process.exit(fails.length ? 1 : 0);
})();

export const THEMES = [
  { id: "midnight", label: "Midnight" },
  { id: "black", label: "Black (darker)" },
  { id: "contrast", label: "High Contrast" },
  { id: "slate", label: "Slate" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

// How the studio is drawn. Not a theme — a theme changes the colours, a skin changes the
// shape. "cli" draws the SAME session the way Claude Code draws it in a console window:
// monospace, square edges, the Windows console palette, a ⏺ per action. The conversation,
// the project and every control are untouched, so you can switch mid-turn.
export const SKINS = [
  { id: "studio", label: "Studio", hint: "Cards, colour and rounded edges." },
  { id: "cli", label: "Claude Code CLI", hint: "The console look, same chat." },
] as const;

export type SkinId = (typeof SKINS)[number]["id"];

const KEY = "asset-studio-theme";
const SKIN_KEY = "asset-studio-skin";
// Which workspaces have switched THEMSELVES to the console look, by project id. The look is a
// property of the workspace you are reading, not of the window: one project can be a console
// while the one next to it stays a studio, and switching between them moves nothing else.
// Kept as one map, not a key per project, because the store has to hold it all to stay reactive
// — the toggle is in the chat box but the feed beside it has to redraw.
const PROJECT_SKIN_KEY = "asset-studio-cli-projects";

export function getStoredTheme(): ThemeId {
  const t = (typeof localStorage !== "undefined" && localStorage.getItem(KEY)) as ThemeId | null;
  return t && THEMES.some((x) => x.id === t) ? t : "midnight";
}

export function applyTheme(id: ThemeId) {
  document.documentElement.dataset.theme = id;
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* ignore */
  }
}

export function getStoredSkin(): SkinId {
  const s = (typeof localStorage !== "undefined" && localStorage.getItem(SKIN_KEY)) as SkinId | null;
  return s && SKINS.some((x) => x.id === s) ? s : "studio";
}

export function applySkin(id: SkinId) {
  // Written on <html> next to data-theme. The CLI rules are selected as html[data-skin="cli"],
  // which outranks every [data-theme] block whatever order they end up in.
  document.documentElement.dataset.skin = id;
  try {
    localStorage.setItem(SKIN_KEY, id);
  } catch {
    /* ignore */
  }
}

export function getStoredProjectCli(): Record<string, boolean> {
  try {
    const raw = typeof localStorage !== "undefined" && localStorage.getItem(PROJECT_SKIN_KEY);
    const m = raw ? JSON.parse(raw) : null;
    return m && typeof m === "object" && !Array.isArray(m) ? m : {};
  } catch {
    return {};
  }
}

export function storeProjectCli(m: Record<string, boolean>) {
  try {
    localStorage.setItem(PROJECT_SKIN_KEY, JSON.stringify(m));
  } catch {
    /* ignore */
  }
}

export function initTheme() {
  applyTheme(getStoredTheme());
}

export function initSkin() {
  applySkin(getStoredSkin());
}

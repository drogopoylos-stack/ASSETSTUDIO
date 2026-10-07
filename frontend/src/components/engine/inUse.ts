// Is an agent USING the engine? One answer for the Engine window and the status-bar pill.
//
// It used to be "a game tab is open". But a tab outlives its agent: it is kept so the next call
// finds the game as it was left, and nothing closed it, so a game an agent had finished with a day
// earlier went on reading as "in use". Now a tab counts while an agent has called it in the last
// five minutes — the same window a generation gets (the backend's _BUSY_WINDOW) — and the backend
// closes one nobody has called for a while (Settings → Studio engine).

/** Seconds since an agent's last call on a tab, under which the tab counts as in use. */
export const IN_USE_S = 300;

export interface TabLike {
  /** The backend's own verdict; missing from a backend older than it. */
  active?: boolean;
  /** Seconds since an agent last called the tab. */
  idle_s?: number;
}

/** Whether an agent has used this tab lately. A backend that does not say is judged by `idle_s`,
 *  which every backend reports, so the window is right before the backend is restarted too. */
export function tabInUse(t: TabLike): boolean {
  if (typeof t.active === "boolean") return t.active;
  return typeof t.idle_s === "number" && t.idle_s < IN_USE_S;
}

/** Whether an agent is using the engine: it made something, or called a game tab, lately. */
export function engineInUse(st: { recent_generations?: number; tabs?: TabLike[] } | null | undefined): boolean {
  if (!st) return false;
  return (st.recent_generations || 0) > 0 || (st.tabs || []).some(tabInUse);
}

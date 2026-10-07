// What it means to drop a folder on the Workspaces list.
//
// Dragging a folder onto the workspaces panel used to do nothing at all — worse than nothing in
// the desktop app, where a drop no handler claims makes the window navigate to `file://…` and the
// Studio disappears. The gesture is obvious enough that it should just work: the folder becomes a
// workspace and you land in it.
//
// The decision lives here rather than in the component because it has real cases — a plain
// browser tab cannot know where a folder is on disk, a file is not a folder, and several folders
// can arrive at once — and each of those needs the right sentence, not a silent no-op.
//
// Run: npm run test:dropfolder

/** One thing the OS handed over, resolved as far as the page is able. */
export interface Dropped {
  /** What to call it in a message. */
  name: string;
  /**
   * Its real path on disk, or "" when the page cannot know it.
   *
   * Electron 32 removed `File.path`; the desktop app resolves it through
   * `studioBridge.getPathForFile` (webUtils). A browser tab has neither, and no amount of
   * reading the File gives it — which is why that case gets a sentence of its own.
   */
  path: string;
  /**
   * true folder, false file, null when the page could not tell.
   *
   * `webkitGetAsEntry().isDirectory` answers this synchronously during the drop. When it is
   * missing, null means "try it": the backend checks `is_dir` before it opens anything, so a
   * guess here can only cost one refusal, never a wrong workspace.
   */
  isDir: boolean | null;
}

/** Open these folders, or say why there is nothing to open. */
export type OpenPlan =
  | { ok: true; paths: string[]; skipped: number }
  | { ok: false; why: string };

const BROWSER_WHY =
  "A browser tab cannot see where a folder lives on disk. Use “Open a folder…”, " +
  "or drag it into the Asset Studio window instead.";

/** Name a thing the way a person would read it back. */
function quoted(items: Dropped[]): string {
  const names = items.map((i) => `“${i.name}”`);
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}

/**
 * Which of the dropped things are folders worth opening.
 *
 * Order matters. "Nothing arrived" and "this page cannot know paths" are conditions of the drop
 * itself and are reported before anything is inspected; only then does a file get told it is a
 * file. Reversing those two would answer a browser drop with "that is a file", which is both
 * wrong and unactionable.
 */
export function planOpen(items: Dropped[]): OpenPlan {
  if (!items.length) return { ok: false, why: "Nothing was dropped." };
  if (items.every((i) => !i.path)) return { ok: false, why: BROWSER_WHY };

  // isDir === false is the only certain "no". null is unknown, and the backend is the real gate.
  const folders = items.filter((i) => i.path && i.isDir !== false);
  if (!folders.length) {
    const files = items.filter((i) => i.isDir === false);
    return {
      ok: false,
      why: files.length === 1
        ? `${quoted(files)} is a file, not a folder. Drop the folder that holds it.`
        : `${quoted(files)} are files, not folders. Drop the folder that holds them.`,
    };
  }
  // Duplicates are what dragging a multi-selection sometimes produces; opening one twice is a
  // no-op on the backend but it would double every count in the message.
  const seen = new Set<string>();
  const paths: string[] = [];
  for (const f of folders) {
    // The same normalisation the workspace list itself uses (wsNorm), plus a trailing separator:
    // Windows hands the same folder back as C:/games/a or C:\Games\A\ depending on where the drag
    // started, and opening it twice would double every count in the message.
    const key = f.path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    paths.push(f.path);
  }
  return { ok: true, paths, skipped: items.length - folders.length };
}

/** What to say after the folders have been through the backend. */
export function openedMessage(opened: string[], already: string[], failed: string[],
                              skipped: number): { text: string; tone: "ok" | "warn" | "danger" } {
  const bits: string[] = [];
  if (opened.length) bits.push(opened.length === 1 ? `Opened ${opened[0]}` : `Opened ${opened.length} folders`);
  if (already.length) {
    bits.push(already.length === 1 && !opened.length
      ? `${already[0]} is already a workspace`
      : `${already.length} already open`);
  }
  if (skipped) bits.push(`${skipped} not a folder`);
  if (failed.length) bits.push(failed.length === 1 ? failed[0] : `${failed.length} could not be opened`);
  const tone: "ok" | "warn" | "danger" =
    failed.length && !opened.length ? "danger" : (failed.length || skipped) ? "warn" : "ok";
  return { text: bits.join(" · ") || "Nothing to open.", tone };
}

/**
 * Is this drag carrying files from the OS, rather than something the Studio itself is dragging?
 *
 * A project row dragged out of the explorer, a tab dragged between panes and a pinned row being
 * reordered all travel as drags too, and every one of them would otherwise light up the folder
 * drop target. `types` is the only part of a DataTransfer readable during a dragover — getData
 * is deliberately blank until the drop — so this is the check that can actually run in time.
 */
export function carriesFiles(types: readonly string[] | undefined, ours: string[] = []): boolean {
  const t = Array.from(types || []);
  if (ours.some((o) => t.includes(o))) return false;
  return t.includes("Files");
}

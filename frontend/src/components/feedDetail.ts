// How much of a tool's OUTPUT the chat shows.
//
// The chat has always shown the first ten lines of whatever a command printed, then "… show N
// more lines". That is right for reading along, and wrong at both ends: a page of test output
// you have already read pushes the answer off the screen, and a stack trace you are chasing
// makes you click for every screenful.
//
// So three modes, and this file is the whole rule. It is pure on purpose — no React, no store —
// because "what does the chat show" is the kind of thing that should be answerable by a test
// rather than by a screenshot.
//
// WHAT THIS DOES NOT TOUCH: a file write. An edit's diff is the same in all three modes. The
// diff is the WORK; a command's output is a note about the work, and only the note is folded.

export const FEED_DETAILS = [
  {
    id: "minimal",
    label: "Minimal",
    hint: "Only the line saying what ran. The output is one click away.",
  },
  {
    id: "normal",
    label: "Normal",
    hint: "The first ten lines, then a button for the rest.",
  },
  {
    id: "full",
    label: "Full",
    hint: "The whole output, every time.",
  },
] as const;

export type FeedDetail = (typeof FEED_DETAILS)[number]["id"];

/** The middle mode's window. Ten lines is about what fits without pushing the answer off screen. */
export const RESULT_CLAMP = 10;

const KEY = "asset-studio-feed-detail";

/** The one-line summary a control shows under its name. */
export const detailSummary = (d: FeedDetail): string =>
  FEED_DETAILS.find((x) => x.id === d)?.hint || "";

export function getStoredDetail(): FeedDetail {
  try {
    const d = localStorage.getItem(KEY) as FeedDetail | null;
    return d && FEED_DETAILS.some((x) => x.id === d) ? d : "normal";
  } catch {
    return "normal";                       // private mode, or no storage at all
  }
}

export function storeDetail(d: FeedDetail): void {
  try { localStorage.setItem(KEY, d); } catch { /* ignore */ }
}

export interface ResultView {
  /** The lines to draw. */
  shown: string[];
  /** How many are not drawn. */
  hidden: number;
  /** The label for the button that reveals them — empty when there is no button. */
  expand: string;
  /** Whether a "collapse" button belongs under it. */
  collapse: boolean;
}

/**
 * What to draw for one command's output.
 *
 * `open` is the reader's own decision and beats the mode: once you have asked to see something,
 * changing the mode must not take it away again.
 */
export function resultView(lines: string[], detail: FeedDetail, open: boolean): ResultView {
  const n = lines.length;
  if (n === 0) return { shown: [], hidden: 0, expand: "", collapse: false };

  // ASKED FOR, SO SHOWN. Also the "full" mode, which is the same thing said in advance.
  if (open || detail === "full") {
    return { shown: lines, hidden: 0, expand: "", collapse: open && detail !== "full" };
  }

  // MINIMAL — nothing at all until it is asked for. The button says how much there is, because
  // "show more" tells you nothing about whether it is worth the click.
  if (detail === "minimal") {
    return { shown: [], hidden: n, expand: `show ${n} line${n === 1 ? "" : "s"}`, collapse: false };
  }

  // NORMAL — the first ten, then the rest on request. Under the clamp there is nothing to hide,
  // and a button that reveals nothing is worse than no button.
  const shown = lines.slice(0, RESULT_CLAMP);
  const hidden = n - shown.length;
  return {
    shown,
    hidden,
    expand: hidden > 0 ? `… show ${hidden} more line${hidden === 1 ? "" : "s"}` : "",
    collapse: false,
  };
}

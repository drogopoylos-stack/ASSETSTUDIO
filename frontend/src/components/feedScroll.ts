// Where a conversation feed's view belongs after its content changed.
//
// A chat has exactly three reading positions and they are decided in this order:
//
//   1. You asked for earlier history. A page is about to appear ABOVE you, so hold the distance
//      from the bottom and the message you were reading stays under the cursor.
//   2. You are at the bottom, watching it happen. Follow.
//   3. You are reading back. Hold the distance from the bottom, so an answer being written below
//      cannot move the line you are on.
//
// Two and three are the same measurement — distance from the BOTTOM — because that is the edge
// new content arrives at. Measuring from the top works only for case one and fails the moment a
// tool block renders below the fold.
//
// Kept out of the component so it can be tested. Getting this wrong is not a crash; it is a page
// that slides while you read it, which is the kind of fault that never gets a bug report because
// it is hard to describe.

export interface FeedView {
  /** The scrollable height AFTER the content changed. */
  scrollHeight: number;
  /** Distance from the bottom to restore, set when earlier history was asked for. */
  restore: number | null;
  /** The reader is pinned to the bottom and wants to follow. */
  stick: boolean;
  /** Distance from the bottom the reader was holding, while reading back. */
  anchor: number | null;
}

/** The scrollTop the view belongs at, or null to leave it exactly where it is. */
export function nextScrollTop(v: FeedView): number | null {
  if (v.restore != null) return Math.max(0, v.scrollHeight - v.restore);
  if (v.stick) return v.scrollHeight;
  if (v.anchor != null) return Math.max(0, v.scrollHeight - v.anchor);
  return null;
}

/** How close to the bottom still counts as "following". */
export const AT_BOTTOM_PX = 60;

/** Is the reader at the bottom, given the three numbers a scroll event carries? */
export function atBottom(scrollHeight: number, scrollTop: number, clientHeight: number,
                         slack = AT_BOTTOM_PX): boolean {
  return scrollHeight - scrollTop - clientHeight < slack;
}

/** The widest window asked for so far, and the most that came back for it. */
export interface Widest { limit: number; lines: number }

/** Nothing asked yet. `lines: -1` is what makes the first answer un-comparable, and therefore
 *  never a reason to hide the button. */
export const NOTHING_ASKED: Widest = { limit: 0, lines: -1 };

/**
 * Has the whole conversation arrived?
 *
 * NOT `lines < asked`, which is what this used to say and what hid the button entirely. The
 * feed's `limit` is a budget over transcript RECORDS, and what comes back is the rendered events
 * after grouping — so asking for 150 legitimately returns 125 while 3,155 are available.
 * Measured against this machine's own transcript:
 *
 *     asked  150 ->  125 lines        asked 1200 -> 1200
 *     asked  400 ->  395              asked 2000 -> 2000
 *     asked  650 ->  650              asked 8000 -> 3155   <- everything there is
 *
 * The only sound test is the one a person would use: ask for MORE, and see whether more arrives.
 * Until a wider window has actually been tried, the answer is "don't know", and "don't know"
 * must offer the button — pressing it either brings more or settles the question, and both are
 * better than a button that quietly removes itself.
 */
export function haveEverything(seen: Widest, asked: number, lines: number): boolean {
  if (seen.lines < 0) return false;      // nothing to compare against yet
  if (asked <= seen.limit) return false; // not a wider window, so it proves nothing
  return lines <= seen.lines;
}

/** The widest window tried, after this answer. Live conversations grow, so both are maxima. */
export function widen(seen: Widest, asked: number, lines: number): Widest {
  return { limit: Math.max(seen.limit, asked), lines: Math.max(seen.lines, lines) };
}

/** Is there earlier history left to fetch? */
export function offerEarlier(gotAll: boolean, limit: number, max: number): boolean {
  return !gotAll && limit < max;
}

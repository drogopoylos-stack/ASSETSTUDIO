// The one rule behind the "who needs you" reading, kept apart from the component that draws it
// so it can be tested without a browser — the pill imports the store, and the store reads
// localStorage at module load.

/** The three states the backend reports, and no more. A fourth would have to be guessed. */
export type AttentionState = "blocked" | "working" | "idle";

/** Is there anything to say?
 *
 *  NOTHING TO REPORT, NOTHING DRAWN. This was a line at the top of the workspace first: always on
 *  screen, and most of the time saying "All quiet" — a permanent row of chrome whose message was
 *  that there was no message. A status line that is always there stops being read. The reading
 *  appearing IS the signal, so it exists only while something is blocked or working.
 */
export function worthShowing(rows: { state: string }[]): boolean {
  return rows.some((r) => r.state === "blocked" || r.state === "working");
}

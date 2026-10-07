import type { DiffNote } from "../store/useStore";

// COMMENTS ON A DIFF, WRITTEN THE WAY AN AGENT CAN ACT ON THEM.
//
// The one thing this gets right, and the reason it is a module with a test rather than a
// template string in the composer: A LINE NUMBER IS NOT ALWAYS A FILE LINE.
//
// A feed card for an `Edit` shows the diff of `old_string` against `new_string`, so its numbers
// count from the start of the edit — line 3 of a four-line replacement, not line 3 of the file.
// A checkpoint diff compares whole files, so its numbers are real. Printing the first kind as
// "src/foo.ts:3" would send the agent to the wrong place with total confidence.
//
// So the CODE is always quoted and the NUMBER is only printed when `lineIsFile` says it means
// what it looks like. A quoted line is something the agent can search for; a wrong number is not.

const SIDE: Record<DiffNote["side"], string> = {
  add: "the line it added",
  del: "the line it removed",
  ctx: "this line",
};

/** The batch, as one message. "" when there is nothing to say. */
export function notesToPrompt(notes: DiffNote[]): string {
  const rows = notes.filter((n) => (n.note || "").trim());
  if (!rows.length) return "";

  const out: string[] = [
    rows.length === 1
      ? "A comment on the change you just made:"
      : `${rows.length} comments on the changes you just made:`,
    "",
  ];

  // Grouped by file, in the order the first comment on each file was written — which is the
  // order the user read them in.
  const order: string[] = [];
  const byFile = new Map<string, DiffNote[]>();
  for (const n of rows) {
    const key = n.file || "(no file)";
    let list = byFile.get(key);
    if (!list) { list = []; byFile.set(key, list); order.push(key); }
    list.push(n);
  }

  for (const file of order) {
    out.push(file);
    for (const n of byFile.get(file) || []) {
      const where = n.lineIsFile && n.line ? `line ${n.line}` : SIDE[n.side] || "this line";
      out.push(`  ${where}:`);
      out.push("    " + (n.code || "").trim());
      for (const line of n.note.trim().split("\n")) out.push("  > " + line);
    }
    out.push("");
  }
  return out.join("\n").trimEnd();
}

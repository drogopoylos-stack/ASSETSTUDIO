// Comments on a diff, as the agent reads them.
//
// The one rule this file exists to hold: A LINE NUMBER IS NOT ALWAYS A FILE LINE. A feed card for
// an `Edit` shows the diff of `old_string` against `new_string`, so its numbers count from the
// start of the edit; a checkpoint diff compares whole files, so its numbers are real. Printing
// the first kind as "src/foo.ts:3" would send the agent somewhere with total confidence and be
// wrong. So the code is always quoted, and the number is printed only when `lineIsFile` is set.
//
// Run: npm run test:diffnotes

import { notesToPrompt } from "../src/components/diffNotes";
import type { DiffNote } from "../src/store/useStore";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

let seq = 1;
const note = (p: Partial<DiffNote>): DiffNote => ({
  id: seq++, projectId: "p", file: "src/a.ts", code: "const x = 1;", side: "add",
  note: "say something", ...p,
});

console.log("Nothing to say");
ok("no notes is an empty string", notesToPrompt([]) === "");
ok("a blank comment is not a comment", notesToPrompt([note({ note: "   " })]) === "");
const mixed = notesToPrompt([note({ note: " " }), note({ note: "this" })]);
ok("...even mixed in with a real one: one comment, counted as one",
   (mixed.match(/^ {2}> /gm) || []).length === 1 && mixed.startsWith("A comment on"), mixed);

console.log("\nThe line number is only cited when it is a file line");
const fromEdit = notesToPrompt([note({ line: 3, lineIsFile: false, note: "wrong name" })]);
ok("an edit's diff never prints a number", !/line 3/.test(fromEdit) && !/:3/.test(fromEdit), fromEdit);
ok("...it says which side of the diff instead", /the line it added/.test(fromEdit), fromEdit);
ok("...and quotes the code, which is searchable", /const x = 1;/.test(fromEdit), fromEdit);

const fromCkpt = notesToPrompt([note({ line: 42, lineIsFile: true, note: "wrong name" })]);
ok("a checkpoint diff does print the number", /line 42/.test(fromCkpt), fromCkpt);
ok("...and still quotes the code", /const x = 1;/.test(fromCkpt), fromCkpt);

const removed = notesToPrompt([note({ side: "del", code: "old()", note: "keep this" })]);
ok("a removed line says removed", /the line it removed/.test(removed), removed);
const ctx = notesToPrompt([note({ side: "ctx", note: "here" })]);
ok("an unchanged line says 'this line'", /this line:/.test(ctx), ctx);

console.log("\nGrouped by file, in reading order");
const many = notesToPrompt([
  note({ file: "b.ts", code: "b1", note: "one" }),
  note({ file: "a.ts", code: "a1", note: "two" }),
  note({ file: "b.ts", code: "b2", note: "three" }),
]);
ok("each file is named once", (many.match(/^b\.ts$/gm) || []).length === 1, many);
ok("the first file seen comes first", many.indexOf("b.ts") < many.indexOf("a.ts"), many);
ok("both of that file's comments are under it",
   many.indexOf("b1") < many.indexOf("a.ts") && many.indexOf("b2") < many.indexOf("a.ts"), many);
ok("the count is in the opening line", many.startsWith("3 comments on the changes you just made:"),
   many.split("\n")[0]);
ok("one comment is singular",
   notesToPrompt([note({})]).startsWith("A comment on the change you just made:"));

console.log("\nEvery word of the comment survives");
const multi = notesToPrompt([note({ note: "first line\nsecond line" })]);
ok("a two-line comment keeps both lines, each marked as a comment",
   /^ {2}> first line$/m.test(multi) && /^ {2}> second line$/m.test(multi), multi);
ok("no trailing blank line", !/\n\s*$/.test(multi), JSON.stringify(multi.slice(-12)));

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

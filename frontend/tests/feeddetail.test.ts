// Three modes for a command's output, and the one rule behind all three.
//
// The chat has always shown the first ten lines of what a command printed. That is one answer to
// a question with three: a page of test output you have already read pushes the answer off the
// screen, and a stack trace you are chasing makes you click for every screenful.
//
// The rule lives in feedDetail.ts as a pure function precisely so it can be checked here rather
// than by squinting at a screenshot. The cases that matter are the edges: nothing to show, less
// than the clamp, and what happens to a block the reader has ALREADY opened when the mode moves
// under them.
//
// Run: npm run test:feeddetail

import { FEED_DETAILS, RESULT_CLAMP, resultView, detailSummary, type FeedDetail } from "../src/components/feedDetail";
import { readFileSync } from "node:fs";
import { join } from "node:path";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
const eq = (name: string, got: unknown, want: unknown) =>
  ok(name, JSON.stringify(got) === JSON.stringify(want), "got " + JSON.stringify(got));

const lines = (n: number) => Array.from({ length: n }, (_, i) => "line " + (i + 1));

// ---------------------------------------------------------------- there are three

console.log("Three modes, no more and no fewer");
eq("their ids", FEED_DETAILS.map((d) => d.id), ["minimal", "normal", "full"]);
ok("each has a label and a sentence",
   FEED_DETAILS.every((d) => d.label.length > 2 && d.hint.length > 20));
ok("the summary reads off the same list",
   detailSummary("minimal") === FEED_DETAILS[0].hint);

// ---------------------------------------------------------------- minimal

console.log("\nMinimal says nothing until it is asked");
{
  const v = resultView(lines(24), "minimal", false);
  eq("no lines are drawn", v.shown, []);
  eq("all of them are hidden", v.hidden, 24);
  eq("and the button says how many there are", v.expand, "show 24 lines");
  ok("with nothing to collapse yet", v.collapse === false);
}
eq("one line is singular", resultView(lines(1), "minimal", false).expand, "show 1 line");
{
  const v = resultView(lines(24), "minimal", true);
  eq("opened, it shows everything", v.shown.length, 24);
  eq("...with nothing left hidden", v.hidden, 0);
  eq("...no expander", v.expand, "");
  ok("...and a way back", v.collapse === true);
}

// ---------------------------------------------------------------- normal, which is what it did before

console.log("\nNormal is what the chat did before");
{
  const v = resultView(lines(24), "normal", false);
  eq("the first ten", v.shown.length, RESULT_CLAMP);
  eq("...starting at the top", v.shown[0], "line 1");
  eq("the rest are counted", v.hidden, 14);
  eq("and offered", v.expand, "… show 14 more lines");
}
{
  // UNDER THE CLAMP THERE IS NOTHING TO HIDE. A button that reveals nothing is worse than none.
  const v = resultView(lines(4), "normal", false);
  eq("a short result is shown whole", v.shown.length, 4);
  eq("...with no button", v.expand, "");
  eq("...and nothing hidden", v.hidden, 0);
}
eq("exactly the clamp needs no button", resultView(lines(RESULT_CLAMP), "normal", false).expand, "");
eq("one over it does", resultView(lines(RESULT_CLAMP + 1), "normal", false).expand, "… show 1 more line");

// ---------------------------------------------------------------- full

console.log("\nFull never asks");
{
  const v = resultView(lines(400), "full", false);
  eq("every line", v.shown.length, 400);
  eq("nothing hidden", v.hidden, 0);
  eq("no expander", v.expand, "");
  ok("and no collapse either — there is nothing to collapse TO", v.collapse === false);
}

// ---------------------------------------------------------------- the edges

console.log("\nThe edges");
for (const d of FEED_DETAILS.map((x) => x.id as FeedDetail)) {
  const v = resultView([], d, false);
  eq("empty output draws nothing in " + d, v.shown, []);
  eq("...and offers nothing in " + d, v.expand, "");
}

// THE READER'S DECISION BEATS THE MODE. Having asked to see something, switching the mode must
// not take it away again — that is the one behaviour that would feel like a bug.
console.log("\nWhat you asked to see stays visible");
for (const d of FEED_DETAILS.map((x) => x.id as FeedDetail)) {
  const v = resultView(lines(50), d, true);
  eq("open beats " + d, v.shown.length, 50);
  eq("...with nothing hidden in " + d, v.hidden, 0);
}

// ---------------------------------------------------------------- a write is not an output

console.log("\nA file write is the same in all three");
const feed = readFileSync(join(process.cwd(), "src", "components", "SessionFeed.tsx"), "utf8");
// The mode is read in exactly one place, and that place is the block that draws a command's
// OUTPUT. A diff is drawn by DiffView from e.diff, which never consults it.
const uses = (feed.match(/s\.feedDetail/g) || []).length;
ok("the mode is consulted once", uses === 1, String(uses));
ok("...inside ResultBlock",
   feed.indexOf("s.feedDetail") > feed.indexOf("function ResultBlock")
   && feed.indexOf("s.feedDetail") < feed.indexOf("function DiffView"),
   "it must sit between ResultBlock and DiffView");
// Read the parameter list rather than matching the whole signature: DiffView has since gained
// the file and the project id, so that comments can be written on its lines, and a literal match
// would have failed for a change that has nothing to do with the mode. The RULE is what is
// checked — the detail mode is not one of its inputs.
const diffSig = (feed.match(/function DiffView\(\{([^}]*)\}/) || ["", ""])[1];
ok("DiffView takes no mode",
   /hunks/.test(diffSig) && /total/.test(diffSig) && !/detail|mode/i.test(diffSig),
   "an edit's diff is the work, not a note about the work: " + diffSig.trim());

// ---------------------------------------------------------------- and you can change it

console.log("\nBoth controls write the one field");
const composer = readFileSync(join(process.cwd(), "src", "components", "ChatComposer.tsx"), "utf8");
const settings = readFileSync(join(process.cwd(), "src", "pages", "Settings.tsx"), "utf8");
ok("the composer menu has the switch", /FEED_DETAILS\.map/.test(composer) && /setFeedDetail\(d\.id\)/.test(composer));
ok("Settings has it too", /FEED_DETAILS\.map/.test(settings) && /setFeedDetail\(d\.id\)/.test(settings));
ok("and it is findable by search", /label: "Command output", pane: "general"/.test(settings));

// ----------------------------------------------------------------

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

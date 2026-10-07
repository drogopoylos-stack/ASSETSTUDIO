// "Who needs you": it lives in the bottom bar, and it takes no space when there is no news.
//
// Both rules came from the user, and both are easy to lose in a refactor:
//
//   1. NOTHING TO REPORT, NOTHING DRAWN. It was a line at the top of the workspace first, always
//      on screen, most of the time saying "All quiet" — a permanent row of chrome to report that
//      there was nothing to report. The pill appearing IS the signal.
//   2. IT IS A BOTTOM-BAR ITEM. That is where the user already chooses which readings deserve a
//      permanent place (the gear at the right-hand end), so the on/off control they asked for is
//      the one that is already there. The Settings switch is the second, different question:
//      whether the feature exists at all.
//
// Run: npm run test:needsyou

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { worthShowing } from "../src/components/attention";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");

console.log("Nothing to report, nothing drawn");
ok("no rows at all", worthShowing([]) === false);
ok("every project idle", worthShowing([{ state: "idle" }, { state: "idle" }]) === false);
ok("one blocked is news", worthShowing([{ state: "idle" }, { state: "blocked" }]) === true);
ok("one working is news", worthShowing([{ state: "idle" }, { state: "working" }]) === true);
ok("a state nobody has heard of is not news", worthShowing([{ state: "paused" }]) === false,
   "three states and no more — a fourth would have to be guessed");

console.log("\nIt is a bottom-bar item, with the other readings");
const store = src("store/useStore.ts");
const bar = src("components/StatusBar.tsx");
ok("the bar offers it", /\{ id: "needs", label: "Who needs you" \}/.test(store),
   "it must be in STATUS_ITEMS or the gear cannot switch it on");
ok("...and it is drawn only when it is in the list",
   /statusItems\.includes\("needs"\)/.test(bar) && /\{showNeeds && <NeedsYouPill/.test(bar));
ok("...and never twice, once by name and once by the generic map",
   /id !== "needs"/.test(bar), "the filtered map must skip it");
ok("the pill is the only place it is drawn",
   !/NeedsYou[ >/]/.test(src("pages/Workspace.tsx")),
   "the old line at the top of the workspace must be gone");

console.log("\nThe feature switch is a different question from the bar item");
const engine = src("pages/SettingsEngine.tsx");
ok("Settings still owns the capability", /key: "needs_you"/.test(engine));
ok("...and says where the bar's own control is",
   /gear at the right-hand end/.test(engine),
   "a user who wants it off will look at the bar first");
ok("switched off, it is not polled either",
   /not polled either/.test(engine) && /OFF_POLL_MS/.test(src("components/NeedsYouPill.tsx")));

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

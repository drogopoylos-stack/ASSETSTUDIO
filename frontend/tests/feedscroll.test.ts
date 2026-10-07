// Where the conversation feed's view belongs when its content changes.
//
// The fault this locks down was reported like this: "when i am trying to find something into the
// conversation when you answer or write bash etc it should stay stable for me not moving the
// chat down if i had scroll up". The feed knew only two positions — pinned to the bottom, or a
// one-shot restore after "load earlier". There was no third, so anything arriving below moved the
// line the reader was on.
//
// Run: npm run test:feedscroll

import {
  atBottom, haveEverything, nextScrollTop, offerEarlier, widen, AT_BOTTOM_PX, NOTHING_ASKED,
} from "../src/components/feedScroll";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
function eq(name: string, got: unknown, want: unknown) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  ok(name, a === b, "got " + a + ", want " + b);
}

const view = (o: Partial<Parameters<typeof nextScrollTop>[0]> = {}) => ({
  scrollHeight: 1000, restore: null, stick: false, anchor: null, ...o,
});

// ---------------------------------------------------------------- reading back

// The whole point. 400px from the bottom, then 600px of answer arrives below.
eq("reading back: an answer arriving below does not move you",
   nextScrollTop(view({ scrollHeight: 1600, anchor: 400 })), 1200);
ok("...and the distance from the bottom is exactly what it was",
   1600 - nextScrollTop(view({ scrollHeight: 1600, anchor: 400 }))! === 400);

// Repeatedly, because a streamed answer changes the content on every token.
{
  let h = 1000;
  const holds: number[] = [];
  for (const grow of [120, 300, 40, 900]) { h += grow; holds.push(h - nextScrollTop(view({ scrollHeight: h, anchor: 400 }))!); }
  eq("...however many times it grows", holds, [400, 400, 400, 400]);
}

ok("a feed shorter than the anchor clamps rather than going negative",
   nextScrollTop(view({ scrollHeight: 100, anchor: 400 })) === 0);

// ---------------------------------------------------------------- following

eq("at the bottom: follow", nextScrollTop(view({ scrollHeight: 1600, stick: true })), 1600);
eq("...even with an anchor left over, because following wins",
   nextScrollTop(view({ scrollHeight: 1600, stick: true, anchor: 400 })), 1600);

// ---------------------------------------------------------------- earlier history

// 2000px of history is prepended. You were 300 from the bottom and must stay 300 from the bottom.
eq("load earlier: the message you were reading stays put",
   nextScrollTop(view({ scrollHeight: 3000, restore: 300 })), 2700);
eq("...and it beats both of the others",
   nextScrollTop(view({ scrollHeight: 3000, restore: 300, stick: true, anchor: 999 })), 2700);

// ---------------------------------------------------------------- say nothing

eq("no anchor, not following, nothing asked for: leave the view alone",
   nextScrollTop(view()), null);

// ---------------------------------------------------------------- at the bottom

ok("exactly at the bottom counts", atBottom(1000, 800, 200));
ok("...and so does within the slack", atBottom(1000, 800 - (AT_BOTTOM_PX - 1), 200));
ok("...but not beyond it", !atBottom(1000, 800 - (AT_BOTTOM_PX + 1), 200));
ok("a page shorter than its viewport is at the bottom", atBottom(200, 0, 400));
ok("the slack can be tightened", !atBottom(1000, 780, 200, 10));

// ---------------------------------------------------------------- more to fetch
//
// This is the "load earlier does nothing" half. The button used to be shown by comparing the
// line count against the CURRENT limit — so a response that landed after the limit had already
// grown (150 lines against a limit of 650) hid the button that had just been pressed.

// THE LADDER, measured against this machine's own transcript on 2026-09-09. These are the
// numbers that make `lines < asked` wrong: asking for 150 returns 125 while 3,155 exist.
const LADDER: Array<[number, number]> = [
  [150, 125], [400, 395], [650, 650], [1200, 1200], [2000, 2000], [8000, 3155],
];

{
  // Walking the ladder must never conclude "we have everything" until it actually stops growing.
  let seen = { ...NOTHING_ASKED };
  const said: boolean[] = [];
  for (const [asked, lines] of LADDER) {
    said.push(haveEverything(seen, asked, lines));
    seen = widen(seen, asked, lines);
  }
  eq("no step of the real ladder claims the end early", said, [false, false, false, false, false, false]);
  eq("...and the widest tried is remembered", seen, { limit: 8000, lines: 3155 });
}

{
  // Asking for more and getting no more IS the end.
  const seen = widen({ ...NOTHING_ASKED }, 8000, 3155);
  ok("a wider window returning no more means there is no more",
     haveEverything(seen, 9000, 3155));
  ok("...and fewer counts too, because a conversation can be compacted",
     haveEverything(seen, 9000, 2900));
}

ok("the very first answer never hides the button",
   !haveEverything({ ...NOTHING_ASKED }, 150, 125));
ok("...nor does the same window answered again",
   !haveEverything(widen({ ...NOTHING_ASKED }, 650, 650), 650, 650));
ok("a live conversation growing at the same window is not the end",
   !haveEverything(widen({ ...NOTHING_ASKED }, 650, 650), 650, 662));
ok("the widest is a high-water mark, not the latest",
   widen({ limit: 2000, lines: 2000 }, 650, 650).lines === 2000);

ok("there is more while the last load came back full", offerEarlier(false, 150, 8000));
ok("...still, after the window has grown", offerEarlier(false, 650, 8000));
ok("nothing more once a load came back short", !offerEarlier(true, 650, 8000));
ok("and nothing more at the ceiling", !offerEarlier(false, 8000, 8000));
ok("the ceiling is per feed", !offerEarlier(false, 2000, 2000));

// ---------------------------------------------------------------- the window can grow
//
// The fault, twice reported as "load earlier does nothing": the loader effect had `limit` among
// its own dependencies AND reset `limit` on its first line. Clicking widened the window, the
// effect re-ran because the window had changed, and it immediately narrowed it again. The same
// 150 lines came back every time.
//
// There is no DOM here to click a button in, so this asserts the SHAPE in the source: no effect
// may write a piece of state that it also depends on.

import { readFileSync } from "node:fs";
import { join } from "node:path";

// From the working directory, not from `import.meta.url`: esbuild bundles this test into
// data/tmp/, so the file's own path no longer points anywhere near the source. Every test here
// is run by npm from `frontend/`.
const feedSrc = readFileSync(join(process.cwd(), "src", "components", "SessionFeed.tsx"), "utf8");

/** The dependency array an effect ends with, given a position inside it. Plain string work —
 *  a hand-rolled brace walker trips over the JSX further down this file. */
function depsAfter(src: string, from: number): string {
  const close = src.indexOf("}, [", from);
  if (close < 0) return "";
  const end = src.indexOf("]", close);
  return end < 0 ? "" : src.slice(close + 3, end + 1);
}

// 1. THE LOADER EFFECT MUST NOT TOUCH THE WINDOW. It is the effect that fetches, and `limit` is
//    one of its dependencies, so anything it writes to `limit` re-triggers it and is undone.
const fetchAt = feedSrc.indexOf("api.missionFeed(");
ok("the loader can be found", fetchAt > 0, String(fetchAt));
const loaderStart = feedSrc.lastIndexOf("useEffect(", fetchAt);
const loaderDeps = depsAfter(feedSrc, fetchAt);
const loaderBody = feedSrc.slice(loaderStart, feedSrc.indexOf("}, [", fetchAt));
ok("...and it does depend on the window", /\blimit\b/.test(loaderDeps), loaderDeps);
ok("the loader never resets the window it depends on",
   !/setLimit\s*\(/.test(loaderBody),
   (loaderBody.match(/setLimit\s*\([^)]*\)/) || ["?"])[0]);

// 2. The intention the reset existed for is still kept, keyed on the conversation instead.
const resetAt = feedSrc.indexOf("setLimit(FEED_LIMIT)");
ok("a new conversation still starts on the narrow window", resetAt > 0, String(resetAt));
const resetDeps = depsAfter(feedSrc, resetAt);
ok("...keyed on the conversation", /\bid\b/.test(resetDeps) && /\bsession\b/.test(resetDeps), resetDeps);
ok("...and NOT on the window it is setting", !/\blimit\b/.test(resetDeps), resetDeps);
ok("...and it is the only such reset",
   feedSrc.split("setLimit(FEED_LIMIT)").length - 1 === 1,
   String(feedSrc.split("setLimit(FEED_LIMIT)").length - 1));

// The rule that hid the button: deciding the end by comparing a count against the number asked
// for. It must never come back.
ok("the end is not decided by subtracting the request from the answer",
   !/lines\.length\s*<\s*asked/.test(feedSrc));
ok("...it is decided by asking for more", /haveEverything\(/.test(feedSrc));

// The buttons must ask for more than the feed currently holds, or pressing them is a no-op.
ok("load earlier widens the window", /setLimit\(\(l\) => Math\.min\(l \+ 500/.test(feedSrc));
ok("full history opens it all the way", /setLimit\(8000\)/.test(feedSrc));

// ---------------------------------------------------------------- report
if (fails.length) {
  console.error("\nFAILED " + fails.length + " of " + (pass + fails.length));
  for (const f of fails) console.error("  x " + f);
  process.exit(1);
}
console.log("feed scroll: " + pass + " checks pass");

// What an agent's token number means, everywhere it is shown: NEW tokens (input + cache writes +
// output, every API call once), with the cache reads apart. The card once said "tokens across
// the wire" and showed 77.9M for an agent whose new tokens were 747k - the backend added every
// transcript line, and each line repeats its call's whole usage (backend/agent_usage.py).
//
// Run: npm run test:agenttokens

import { readFileSync } from "node:fs";
import { join } from "node:path";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");

console.log("The card");
const card = src("components/SubAgentCard.tsx");
ok("its number is called new tokens, not everything across the wire",
   /new tokens:/.test(card) && !/tokens across the wire/.test(card));
ok("the cache reads have a line of their own", /read again from the cache/.test(card));
ok("...and the sum of both is offered, named for what it is", /label="all together"/.test(card));
ok("calls, not turns", /label="calls"/.test(card) && !/label="turns"/.test(card));

console.log("\nThe bottom-bar pill");
const pill = src("components/SubAgentPill.tsx");
ok("its total says new tokens and shows the cache reads beside it",
   /new tokens/.test(pill) && /from cache/.test(pill) && /cache_read/.test(pill));

console.log("\nThe Workflows tab and the turn bar");
const wf = src("pages/Workflows.tsx");
ok("the run and each agent say new tokens", (wf.match(/new tok/g) || []).length >= 3);
ok("the turn bar names the subagents' figure the same way", /their new tokens/.test(src("components/SessionFeed.tsx")));

console.log("\nThe type says what the field is");
const types = src("types.ts");
ok("SubAgent.tokens is documented as new tokens, and `wire` exists",
   /NEW tokens: input \+ cache writes \+ output/.test(types) && /wire\?: number/.test(types));

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

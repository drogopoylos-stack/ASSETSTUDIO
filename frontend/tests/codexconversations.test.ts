// A BUSY CODEX IS NOT A SIGNED-OUT CODEX, AND A 251 MB CONVERSATION NEEDS A DOOR OUT.
//
// One Codex conversation's rollout file grew to 251 MB. Reopening it blocks Codex's app-server for
// minutes, and the app-server answers one request at a time — so the tiny account/read the chat box
// uses to decide "signed in" times out behind it. The UI then announced a sign-out that never
// happened, and offered no way to get rid of the conversation that caused it.
//
// The frontend half of the fix is three small things, and every one of them fails silently if it is
// wrong: the API call can be pointed at a route the backend does not serve (a 404 exactly when the
// user needs it), the panel can read `busy` as "not signed in" again, and the menu can show a size
// and a warning that never render. None of that is visible to tsc, so it is pinned here the way
// tests/feeddetail.test.ts and tests/freemem.test.ts pin theirs: against the source text.
//
// The backend half (that the app-server is actually busy, that rollout_bytes is Codex's own file,
// and that forget moves it) is the colleague's change to codex_app.py, checked there.
//
// Run: npm run test:codexconversations

import { readFileSync } from "node:fs";
import { join } from "node:path";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");
const client = src("api/client.ts");
const panel = src("components/CodexPanel.tsx");
const ws = src("pages/Workspace.tsx");
const types = src("types.ts");

// ---------------------------------------------------------------- the contract, in the types

console.log("The two new fields are declared where the backend sends them");
ok("CodexStatus carries busy", /interface CodexStatus \{[\s\S]*?busy\?: boolean;/.test(types));
ok("Session carries the rollout's size and whether it is too big",
   /interface Session \{[\s\S]*?rollout_bytes\?: number;[\s\S]*?too_big\?: boolean;/.test(types));

// ---------------------------------------------------------------- the API call

console.log("\nThe client declares the forget call, on the route the backend serves");
ok("codexForgetConversation takes project, thread and rollout",
   /codexForgetConversation: \(project: string, thread: string, rollout: boolean\)/.test(client));
ok("...and POSTs /api/codex/conversations/forget",
   /codexForgetConversation:[\s\S]{0,400}?"\/api\/codex\/conversations\/forget"[\s\S]{0,300}?method: "POST"/.test(client),
   "a path or a method that is off is a 404 at the moment the user needs it");
ok("...sending exactly the body the backend reads",
   /codexForgetConversation:[\s\S]{0,600}?JSON\.stringify\(\{ project, thread, rollout \}\)/.test(client));
ok("...and its result carries ok, error, freed bytes and whether the file moved",
   /codexForgetConversation:[\s\S]{0,200}?\{ ok: boolean; error\?: string; freed_bytes: number; rollout_moved: boolean \}/.test(client));

// ---------------------------------------------------------------- busy is not signed out

console.log("\nBusy never reads as a sign-out");
ok("codexWho says Codex is busy, not 'not signed in', without an account",
   /if \(status\.busy && !status\.signed_in\) return "Codex is busy";/.test(panel),
   "the backend keeps the last account while busy, so this is the never-known case");
// The sign-in card is drawn by three callers, so the guard has to live INSIDE it — otherwise
// ChatComposer and UsageBar each re-open the same hole. It must sit before the login branches.
ok("CodexSignIn answers busy before it offers any sign-in",
   /if \(status\.busy\) \{[\s\S]*?Codex is busy[\s\S]*?const lg = status\.login;/.test(panel),
   "the busy card must replace the prompt, not sit beside it");

// ---------------------------------------------------------------- the menu row

console.log("\nThe conversation menu shows the size, flags the danger, and can forget");
ok("the row shows Codex's rollout size when it is known",
   /s\.rollout_bytes != null[\s\S]{0,80}?humanBytes\(s\.rollout_bytes\)/.test(ws));
ok("a too_big row is marked with a warning and words",
   /s\.too_big && \([\s\S]{0,200}?AlertTriangle[\s\S]{0,120}?too large to reopen/.test(ws));
ok("every saved row offers Forget",
   /onClick=\{\(\) => forget\(s\)\}[\s\S]{0,200}?Forget/.test(ws));
ok("a too_big row offers to archive the file too",
   /s\.too_big \? "Forget \+ archive file" : "Forget"/.test(ws));
ok("...which asks for rollout: true (so Codex's own file is moved)",
   /api\.codexForgetConversation\(feedId, s\.id, !!s\.too_big\)/.test(ws));
ok("...and forgets the CURRENT conversation into 'new' so the chip cannot point at a ghost",
   /if \(isCurrent\(s\)\) setMode\(\{ type: "new" \}\);/.test(ws));
ok("...and refetches the list after it succeeds",
   /await api\.missionSessions\(feedId\)\.then\(\(x\) => x\.sessions\)/.test(ws));
ok("...and reports a failure through the existing toast, not a silent no-op",
   /toast\(r\.error \|\| "could not forget that conversation", "danger"\)/.test(ws)
   && /const toast = useStore\(\(s\) => s\.toast\);/.test(ws));

// The row is a div wrapper with the switch and the forget as SIBLINGS: nesting one button in the
// other is invalid HTML the browser un-nests, which breaks switching right where the user cannot
// see why. Both the wrapper being a div and the order are what this checks.
console.log("\nThe row is a div with two sibling buttons");
const rows = ws.slice(ws.indexOf("{sessions.map((s) => ("), ws.indexOf("no saved conversations yet"));
ok("the row opens with a div, not a button", /<div key=\{s\.id\} className=\{cls\(/.test(rows));
ok("the switch button comes first, then the forget button",
   /<button className="min-w-0 flex-1 text-left px-2 py-1\.5"[\s\S]*?setMode\(\{ type: "session", id: s\.id \}\)[\s\S]*?<\/button>[\s\S]*?onClick=\{\(\) => forget\(s\)\}/.test(rows));
ok("the forget button is not inside the switch button",
   !/setMode\(\{ type: "session", id: s\.id \}\)[\s\S]{0,300}?onClick=\{\(\) => forget\(s\)\}[\s\S]{0,300}?<\/button>\s*<\/button>/.test(rows));

// ----------------------------------------------------------------

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

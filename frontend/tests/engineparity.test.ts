// THE WORKBENCH BELONGS TO THE ENGINE YOU PICKED, NOT TO CLAUDE.
//
// Every main-UI button that reads per-engine data has to be given the agent-prefixed FEED id
// ("codex--<folder>", "deepseek-harness--<folder>"), never the bare folder id — and the panel that
// opens from it has to be told which engine it is showing. Both halves are one-line mistakes that
// typecheck perfectly: the app compiles, the button works, and it quietly shows the wrong chat.
// That is exactly what had happened, so the wiring is pinned here.
//
// The JSX itself cannot be rendered in node (`Workspace` pulls the zustand store, which reads
// localStorage at import), so this reads the source the way `models_test.py` reads `cc_session.py`:
// the assertion is that the two names appear TOGETHER on the same call, not that a string exists
// somewhere in the file.
//
// Run: npm run test:engines
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { feedAgent, folderAgent, paneAgent, writePaneAgent } from "../src/components/sendPrefs";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra?: unknown) {
  if (cond) { pass++; return; }
  fails.push(name + (extra === undefined ? "" : "  <- " + JSON.stringify(extra)));
}
const section = (t: string) => console.log("\n" + t);

const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");
const ws = src("pages/Workspace.tsx");
const mc = src("pages/MissionControl.tsx");
const composer = src("components/ChatComposer.tsx");

section("the id helpers agree on which engine a feed belongs to");
// The whole app's engine routing is this pair: an "<engine>--" prefix on the way out, and the
// engine read back off it. If they disagree, every panel downstream is off by one engine.
ok("a Claude feed is the folder", feedAgent("d--x", "d--x") === "claude");
ok("a Codex feed names Codex", feedAgent("codex--d--x", "d--x") === "codex");
ok("a DeepSeek feed names DeepSeek", feedAgent("deepseek-harness--d--x", "d--x") === "deepseek-harness");
// The prefix only counts when the WHOLE folder id is the suffix: a Codex feed read against a
// different folder is Claude's (the id does not belong to that folder at all).
ok("a feed for another folder is not read as this one's",
   feedAgent("codex--d--other", "d--x") === "claude");
ok("the folder's own pin wins", (() => {
  const s = new Map<string, string>();
  const st = { getItem: (k: string) => s.get(k) ?? null, setItem: (k: string, v: string) => void s.set(k, v) } as any;
  writePaneAgent("p1", "d--x", "codex", st);
  return paneAgent("p1", "d--x", st) === "codex";
})());

section("the Workspace hands every per-engine panel the FEED id");
const prompts = ws.slice(ws.indexOf('<PromptHistory'), ws.indexOf("/>", ws.indexOf('<PromptHistory')));
ok("Prompt history gets feedId", prompts.includes("feedId={feedOf(activeRoot, paneId)}"), prompts);
ok("...and is told the engine's name", prompts.includes("agentName={nameOfAgent(paneAgent)}"), prompts);
ok("Prompt history jumps by feedId, not root.id",
   !/jumpToPrompt\(root\.id/.test(ws) && /jumpToPrompt\(feedId/.test(ws));
ok("Phases gets feedId", /<TodosPanel projectId=\{feedId\}/.test(ws));
const ckpt = ws.slice(ws.indexOf('<CheckpointsPanel'), ws.indexOf("/>", ws.indexOf('<CheckpointsPanel')));
ok("Checkpoints is told the selected engine", ckpt.includes("agent={paneAgent}"), ckpt);
ok("...and can name the engine that wrote an older row", ckpt.includes("resolveAgentName={nameOfAgent}"), ckpt);
const sk = ws.slice(ws.indexOf('<SkillsPanel'), ws.indexOf("/>", ws.indexOf('<SkillsPanel')));
ok("Skills is told the selected engine", sk.includes("agent={paneAgent}"), sk);
ok("Skills gets the prefixed feed id, not the bare folder (each engine has its own home)",
   sk.includes("feedId={feedOf(activeRoot, paneId)}"), sk);
ok("the subagent pane uses ITS pane's engine", ws.includes("feedOf(root, pane.id)"));
ok("the activity bar's tooltips name the focused pane's engine",
   ws.includes("const activeAgentName = nameOfAgent(activeAgent)"), true);

section("the activity bar no longer says Claude whatever is selected");
ok("Phases names the engine", !ws.includes("the plan Claude is working through"));
ok("Checkpoints names the engine", !ws.includes("review & undo what Claude changed"));
ok("Skills names the engine", !ws.includes("enable/disable Claude skills"));

section("a manual checkpoint carries the engine it was taken under");
ok("CheckpointsPanel sends it", /ckptCreate\(root\.id, "manual snapshot", agent\)/.test(ws));
ok("and the API client accepts it",
   src("api/client.ts").includes('ckptCreate: (projectId: string, label = "", agent = "")'),
   "the client must forward the engine or the label is stored empty");

section("Mission Control follows its card's engine");
ok("the feed id carries the engine", mc.includes("const cardFeed = cardAgent && cardAgent !== \"claude\""), true);
ok("the feed is given it", mc.includes("<SessionFeed id={cardFeed}"), true);
ok("quick answers go to that engine", /sendSettings\(p\.id, cardAgent\)/.test(mc));
ok("...and the send names it", /agent: cardAgent/.test(mc));
ok("the composer is not disabled by Claude's absence alone",
   mc.includes('disabled={cardAgent === "claude" ? !claudeOk : false}'), true);

section("commands that only one engine can run are not offered to the others");
// /subtask and /fork are CLI-only, and the handoff resumes the CLAUDE conversation. Fired from a
// Codex or DeepSeek pane it used to switch the engine the user had chosen, mid-sentence.
ok("the composer checks the engine before handing off",
   composer.includes('if (agentId !== "claude") {') && composer.includes("only runs in a Claude Code terminal"), true);
ok("and the workspace handler refuses a non-Claude pane too",
   ws.includes('if (agentOf(rid, pane.id) !== "claude") return;'), true);
ok("/compact is left to the backend, which knows what it means per engine",
   ws.includes("r.compacted") && ws.includes("compactNow"));

section("the DeepSeek live key is the feed's, so the turn bar finds its own rows");
// The ledger is read per conversation (`mission._turn_rows` -> `turns.for_project(feedId)`); the
// backend test pins the writer. This pins the frontend half: the feed polls the prefixed id.
ok("the workspace feed id is prefixed for a non-Claude pane",
   ws.includes("function altFeedPrefix") && ws.includes('`${agentId}--`'));

console.log("\n" + (fails.length ? fails.map((f) => "  FAIL  " + f).join("\n") : ""));
console.log("\n  " + pass + " passed, " + fails.length + " failed");
process.exit(fails.length ? 1 : 0);

// Which model, effort and mode does a message go out with?
//
// Opus 5 was picked in "opus tsifas" and the answers came from Opus 5.5. The chat box sent Opus 5
// (the folder's own pin); the three edit-and-resends sent the AGENT-WIDE key, which held Opus 5.5
// because that was picked last in another folder - and each one restarted the session on it.
// In STUDIO the header said "Opus 5.5 · high" above a chat box set to max, for the same reason.
// Every sender now reads through components/sendPrefs.ts, and this test holds it there.
//
// Run: npm run test:sendprefs

import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";
import {
  agentKey, effortKey, feedAgent, folderAgent, folderIds, modeFallback, pinnedAgent, prefKey,
  readEffort, readPref, sendSettings, writeEffort, writePref,
} from "../src/components/sendPrefs";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

class Mem {
  m = new Map<string, string>();
  constructor(init: Record<string, string> = {}) { for (const [k, v] of Object.entries(init)) this.m.set(k, v); }
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, String(v)); }
}

// The values as they were read from this PC's Studio window on 2026-09-28.
const TSIFAS = "c--Users-Administrator-Downloads-opus-tsifas";
const STUDIO = "c--Users-Administrator-Downloads-STUDIO";
const real = () => new Mem({
  "cc-model": "opus",                               // the pre-per-agent key, written once long ago
  "cc-model-claude": "claude-opus-5-5",             // picked last, in kappow
  "cc-effort-claude": "high",                       // picked last, in opus tsifas
  "cc-agent": "claude",
  "cc-thinking": "1",
  [`cc-model:${TSIFAS}:claude`]: "claude-opus-5",
  [`cc-effort:${TSIFAS}:claude`]: "high",
  [`cc-effort:${TSIFAS}:claude:claude-opus-5`]: "high",
  [`cc-agent:${TSIFAS}`]: "claude",
  "cc-agent:C--Users-Administrator-Downloads-opus-tsifas": "claude",
  [`cc-model:${STUDIO}:claude`]: "claude-opus-5-5",
  [`cc-effort:${STUDIO}:claude`]: "max",
  [`cc-effort:${STUDIO}:claude:claude-opus-5`]: "max",
  [`cc-effort:${STUDIO}:claude:claude-fable-5-1`]: "medium",
  [`cc-agent:${STUDIO}`]: "claude",
});

// ---- the fault itself
{
  const s = real();
  const t = sendSettings(TSIFAS, "claude", s);
  ok("opus tsifas sends its own pin, Opus 5, not the model picked last anywhere", t.model === "claude-opus-5", t.model);
  ok("...at that model's own effort", t.effort === "high", t.effort);
  ok("the agent-wide key holds Opus 5.5 (the old reader was wrong, not the data)", s.getItem("cc-model-claude") === "claude-opus-5-5");
  const st = sendSettings(STUDIO, "claude", s);
  ok("STUDIO sends Opus 5.5 at max, the level its chat box shows", st.model === "claude-opus-5-5" && st.effort === "max", JSON.stringify(st));
  ok("not the agent-wide 'high' the header showed", readEffort(STUDIO, "claude", "claude-opus-5-5", s) !== s.getItem("cc-effort-claude"));
  ok("STUDIO on Fable 5.1 would send Fable's own level", readEffort(STUDIO, "claude", "claude-fable-5-1", s) === "medium");
  ok("thinking and fork follow the chat box's switches", t.thinking === true && t.fork === false);
  ok("mode with no pin is the Claude default", t.permission_mode === "acceptEdits", t.permission_mode);
}

// ---- a folder nobody set up takes the agent-wide default, as the chat box always did
{
  const s = real();
  const t = sendSettings("c--Users-Administrator-Downloads-new-one", "claude", s);
  ok("a folder with no pin takes the agent-wide model", t.model === "claude-opus-5-5", t.model);
  ok("...and the agent-wide effort", t.effort === "high", t.effort);
  ok("the pre-per-agent 'cc-model' key is never read", sendSettings("x", "claude", new Mem({ "cc-model": "opus" })).model === "default");
  ok("nothing stored: default, default, acceptEdits, no fork, no thinking",
    JSON.stringify(sendSettings("x", "claude", new Mem())) ===
    JSON.stringify({ model: "default", effort: "default", permission_mode: "acceptEdits", fork: false, thinking: false }));
  ok("Codex keeps its own mode default", sendSettings("x", "codex", new Mem()).permission_mode === "full" && modeFallback("codex") === "full");
  ok("fork on in the chat box is fork on here", sendSettings("x", "claude", new Mem({ "cc-fork": "1" })).fork === true);
  ok("no storage at all is not a crash", sendSettings("x", "claude", null).model === "default" && folderAgent("x", null) === "claude");
}

// ---- the effort belongs to the model
{
  const s = new Mem();
  writeEffort("p", "claude", "claude-opus-5", "max", s);
  writePref("model", "p", "claude", "claude-opus-5", s);
  ok("writeEffort keeps the model's own level", s.getItem(effortKey("p", "claude", "claude-opus-5")) === "max");
  ok("...and the folder's and the agent's fallback", s.getItem(prefKey("effort", "p", "claude")) === "max" && s.getItem("cc-effort-claude") === "max");
  writeEffort("p", "claude", "claude-opus-5-5", "medium", s);
  ok("a second model keeps its own level", readEffort("p", "claude", "claude-opus-5-5", s) === "medium");
  ok("and the first keeps its own", readEffort("p", "claude", "claude-opus-5", s) === "max");
  ok("a model with no level of its own takes the folder's last one", readEffort("p", "claude", "claude-fable-5-1", s) === "medium");
  ok("the model 'default' has a key of its own", effortKey("p", "claude", "") === "cc-effort:p:claude:default");
  const g = new Mem();
  writePref("model", "", "claude", "claude-opus-5", g);
  ok("no folder: only the agent-wide key is written", g.m.size === 1 && g.getItem("cc-model-claude") === "claude-opus-5");
}

// ---- one folder, two ids
{
  ok("a lower-case drive id has its upper-case twin", JSON.stringify(folderIds("c--x")) === JSON.stringify(["c--x", "C--x"]));
  ok("and the other way round", JSON.stringify(folderIds("C--x")) === JSON.stringify(["C--x", "c--x"]));
  ok("an id with no drive letter has only itself", JSON.stringify(folderIds("-home-me-game")) === JSON.stringify(["-home-me-game"]));
  ok("an engine-prefixed id is not a drive id", JSON.stringify(folderIds("kimi--c--x")) === JSON.stringify(["kimi--c--x"]));
  ok("no id, no keys", folderIds("").length === 0);
  const s = real();
  const MC = "C--Users-Administrator-Downloads-opus-tsifas";   // how Mission Control lists it
  ok("Mission Control's id for the same folder reads the Workspace's pin",
    sendSettings(MC, "claude", s).model === "claude-opus-5", sendSettings(MC, "claude", s).model);
  s.setItem(`cc-model:${MC}:claude`, "claude-fable-5-1");
  ok("but a pin under its own id wins", sendSettings(MC, "claude", s).model === "claude-fable-5-1");
  ok("and the Workspace id still reads its own", sendSettings(TSIFAS, "claude", s).model === "claude-opus-5");
  ok("an empty value counts as unset", readPref("model", "p", "claude", "fb", new Mem({ [prefKey("model", "p", "claude")]: "" })) === "fb");
}

// ---- the agent
{
  const s = new Mem({ "cc-agent": "codex", [agentKey("c--a")]: "kimi" });
  ok("a folder's own agent wins", folderAgent("c--a", s) === "kimi");
  ok("the other drive-letter id reads it too", folderAgent("C--a", s) === "kimi" && pinnedAgent("C--a", s) === "kimi");
  ok("a folder with none takes the one picked last", folderAgent("c--b", s) === "codex" && pinnedAgent("c--b", s) === "");
  ok("nothing at all is Claude", folderAgent("c--b", new Mem()) === "claude");
}

// ---- whose settings a feed's conversation runs on
{
  ok("a Kimi feed runs on Kimi's settings", feedAgent("kimi--c--x", "c--x") === "kimi");
  ok("a custom engine's feed on its own", feedAgent("openrouter--c--x", "c--x") === "openrouter");
  ok("the plain feed is the folder's Claude conversation", feedAgent("c--x", "c--x") === "claude");
  ok("no folder given: Claude", feedAgent("kimi--c--x", "") === "claude");
  ok("another folder's feed is not taken for an engine", feedAgent("c--y", "c--x") === "claude");
  const s = new Mem({ [prefKey("model", "c--x", "kimi")]: "kimi-k3", [prefKey("model", "c--x", "claude")]: "claude-opus-5" });
  ok("so a Kimi resend sends Kimi's model", sendSettings("c--x", feedAgent("kimi--c--x", "c--x"), s).model === "kimi-k3");
}

// ---- no sender reads the keys on its own
{
  const direct = /getItem\(\s*["'`]cc-(model|effort|mode|agent)(?![a-z])|ccClaudePref|cc-\$\{kind\}/;
  ok("the guard sees a direct read", direct.test('localStorage.getItem("cc-model-claude")')
    && direct.test("localStorage.getItem(`cc-agent:${pid}`)") && direct.test('localStorage.getItem("cc-mode")'));
  ok("and lets a write through", !direct.test('localStorage.setItem("cc-agent", a)')
    && !direct.test("localStorage.getItem(`cc-${key}-${cid}`)"));
  const root = join(process.cwd(), "src");
  const files: string[] = [];
  const walk = (d: string) => {
    for (const n of readdirSync(d)) {
      const p = join(d, n);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(ts|tsx)$/.test(n)) files.push(p);
    }
  };
  walk(root);
  const bad: string[] = [];
  for (const f of files) {
    if (/[\\/]components[\\/]sendPrefs\.ts$/.test(f)) continue;
    readFileSync(f, "utf8").split(/\r?\n/).forEach((line, i) => { if (direct.test(line)) bad.push(`${f.slice(root.length + 1)}:${i + 1}`); });
  }
  ok("every sender reads model, effort, mode and agent through sendPrefs.ts",
    files.length > 50 && bad.length === 0, bad.join(", ") || `${files.length} files`);
}

console.log(fails.length ? `\n  ${pass} passed, ${fails.length} FAILED\n  FAIL  ${fails.join("\n  FAIL  ")}` : `\n  ${pass} passed`);
if (fails.length) process.exit(1);

// Settings: one control per setting, and every one of them explained.
//
// Photographed, the Studio engine pane opened on two dropdowns with its switches scattered three
// screens down among the tuning, one per section. The first question anybody has — "is the forge
// on?" — took six sections to answer. And the Skills switch was not in Settings at all; it lives
// in the Workspace behind a wand icon, which is findable once you know and not before.
//
// The rules below are what keep both fixed. The sharpest one is the third: a switch that appears
// in two places is a second place to be wrong, and the two drift the moment somebody edits one.
//
// Run: npm run test:settingsswitches

import { readFileSync } from "node:fs";
import { join } from "node:path";

// Run from `frontend/`, like every other test here.
const src = (rel: string) => readFileSync(join(process.cwd(), "src", rel), "utf8");
const back = (rel: string) => readFileSync(join(process.cwd(), "..", "backend", rel), "utf8");

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

const engine = src("pages/SettingsEngine.tsx");
const settings = src("pages/Settings.tsx");

// ---------------------------------------------------------------- what is on

console.log("The Studio engine pane says what is on, first");

ok("there is a Switchboard", /function Switchboard\(/.test(engine));
ok("...and the pane opens on it",
   engine.indexOf("<Switchboard rows={switches} />") > 0
   && engine.indexOf("<Switchboard rows={switches} />") < engine.indexOf('<Section title="Engine window"'),
   "it must come before the first Section");
ok("a row can say the machine cannot do it",
   /blocked\?: string/.test(engine) && /r\.blocked \? "unavailable"/.test(engine),
   "no Chrome must read as unavailable, not as off");
ok("...and what it costs to leave on", /~\{r\.cost\} tokens a turn/.test(engine));

// ---------------------------------------------------------------- one control per setting

console.log("\nOne control per setting");

// The keys the switchboard writes. Read out of the array itself, so adding a switch adds a test.
const arr = engine.slice(engine.indexOf("const switches: Switch[] = ["),
                         engine.indexOf("  ];", engine.indexOf("const switches: Switch[] = [")));
const keys = Array.from(arr.matchAll(/key: "([a-z_]+)"/g)).map((m) => m[1]);
ok("the switchboard carries every capability", keys.length >= 6, keys.join(", "));

for (const k of keys) {
  // A flat key is written by setKey("<key>", …); an engine pref by setPref({ <key>: … }).
  const writes = (engine.match(new RegExp('setKey\\("' + k + '"', "g")) || []).length
               + (engine.match(new RegExp('setPref\\(\\{ ' + k + ':', "g")) || []).length;
  ok(k + " is written from exactly one place", writes === 1, String(writes));
}

console.log("\nEvery switch is explained");
const whys = Array.from(arr.matchAll(/why: "([^"]{20,})"/g)).map((m) => m[1]);
ok("every row has a sentence saying what it does", whys.length === keys.length,
   whys.length + " explanations for " + keys.length + " switches");

// ---------------------------------------------------------------- the keys are real

console.log("\nEvery key the switchboard writes is one the backend reads");
// Read the whole backend package once; a key nobody reads is a switch that does nothing.
// Every module that READS a setting. A key missing from all of them is a switch that does
// nothing, which is worse than no switch at all.
const py = ["asset_studio/cc_session.py", "asset_studio/live.py", "asset_studio/review.py",
            "asset_studio/engine.py", "asset_studio/config.py", "asset_studio/main.py",
            "asset_studio/window_sweeper.py"]
  .map((f) => { try { return back(f); } catch { return ""; } }).join("\n");
for (const k of keys) {
  if (k === "live_sidecar") {           // an `engine` sub-key, read through the prefs object
    ok(k + " is read by the engine prefs", /live_sidecar/.test(src("components/engine/prefs.ts")));
    continue;
  }
  ok(k + " is read by the backend", py.includes(k), "no mention in the modules that read settings");
}

// ---------------------------------------------------------------- skills, where you look for them

console.log("\nSkills is in Settings");

ok("there is a Skills section", /function SkillsSection\(/.test(settings));
ok("...listed in the rail", /id: "skills", label: "Skills"/.test(settings));
ok("...that reads the real list", /api\.skillsList\(\)/.test(settings));
ok("...and toggles the real switch", /api\.skillToggle\(sk\.id, !sk\.enabled\)/.test(settings));
ok("every skill shows its own description", /\{sk\.description\}/.test(settings),
   "the description IS the explanation");
ok("it says a new skill arrives off", /arrives <span[^>]*>off<\/span>/.test(settings));

// Pane ids must stay unique or the rail selects the wrong one.
const ids = Array.from(settings.matchAll(/^\s{4}id: "([a-z]+)", label:/gm)).map((m) => m[1]);
ok("every pane id is unique", new Set(ids).size === ids.length, ids.join(", "));
ok("both new panes are reachable", ids.includes("skills") && ids.includes("engine"), ids.join(", "));

// ---------------------------------------------------------------- finding one

console.log("\nEvery setting can be found by typing a word");

ok("there is a search box", /placeholder="Find a setting/.test(settings));
ok("...that lands on the row itself", /function landOn\(label: string\)/.test(settings)
   && /data-setting="' \+ id \+ '"/.test(settings));
ok("...using the same slug the rows carry",
   /import \{ settingSlug \} from "\.\.\/components\/ui"/.test(settings)
   && /settingSlug/.test(src("pages/SettingsEngine.tsx")),
   "a second slug function is a second way to miss");
ok("every tuning row carries its name", /data-setting=\{settingSlug\(label\)\}/.test(engine));
ok("...and every switch does too", /data-setting=\{settingSlug\(r\.label\)\}/.test(engine));

// The map itself.
const findBlock = settings.slice(settings.indexOf("const FIND: Find[] = ["),
                                 settings.indexOf("\n];", settings.indexOf("const FIND: Find[] = [")));
const entries = Array.from(findBlock.matchAll(/\{ label: "([^"]+)", pane: "([a-z]+)"/g))
  .map((m) => ({ label: m[1], pane: m[2] }));
ok("the map has entries", entries.length >= 40, String(entries.length));
ok("no setting is listed twice", new Set(entries.map((e) => e.pane + "/" + e.label)).size === entries.length);

const paneIds = new Set(ids);
for (const e of entries) {
  if (!paneIds.has(e.pane)) fails.push('"' + e.label + '" points at a pane that does not exist: ' + e.pane);
}
ok("every entry points at a real pane",
   entries.every((e) => paneIds.has(e.pane)), entries.filter((e) => !paneIds.has(e.pane)).map((e) => e.pane).join(", "));

// EVERY SWITCH IS FINDABLE. Read out of the switchboard, so adding one and forgetting the map
// fails here rather than when somebody cannot find it.
const switchLabels = Array.from(arr.matchAll(/label: "([^"]+)"/g)).map((m) => m[1]);
const findable = new Set(entries.map((e) => e.label));
for (const l of switchLabels) {
  ok('the switch "' + l + '" is findable', findable.has(l), "add it to FIND in Settings.tsx");
}

// ...AND EVERY AGENT NOTE. Same rule, other catalogue.
const noteLabels = Array.from(settings.matchAll(/\{ key: "cc_[a-z_]+", label: "([^"]+)", group:/g)).map((m) => m[1]);
ok("the note catalogue was found", noteLabels.length >= 8, String(noteLabels.length));
for (const l of noteLabels) {
  ok('the note "' + l + '" is findable', findable.has(l), "add it to FIND in Settings.tsx");
}

// AND NOTHING IS NAMED THAT DOES NOT EXIST. An entry for the engine pane must match a real row
// or a real switch, or the search scrolls to nowhere.
const engineRows = new Set(Array.from(engine.matchAll(/<Row label="([^"]+)"/g)).map((m) => m[1]));
for (const l of switchLabels) engineRows.add(l);
const enginePane = entries.filter((e) => e.pane === "engine");
const ghosts = enginePane.filter((e) => !engineRows.has(e.label)).map((e) => e.label);
ok("no engine entry names a row that is not there", ghosts.length === 0, ghosts.join(", "));

// ----------------------------------------------------------------

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

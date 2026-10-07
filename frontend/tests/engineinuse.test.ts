// Is an agent USING the engine, or has it only left a game tab open?
//
// Four game tabs were open and the Engine window named all four as "in use": two of their agents
// had stopped 26 and 38 hours earlier. A tab now counts while an agent called it in the last five
// minutes, and the backend closes one nobody has called for `live_tab_idle_min` minutes.
//
// Run: npm run test:engineinuse

import { IN_USE_S, engineInUse, tabInUse } from "../src/components/engine/inUse";
import { ENGINE_DEFAULTS, mergePrefs } from "../src/components/engine/prefs";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

// The four tabs as they were found (seconds since each agent's last call).
const found = [
  { project: "brainrot 3d game research crazygames", idle_s: 9.8 },
  { project: "kappow", idle_s: 3943.5 },
  { project: "fight strength brainrots/arena", idle_s: 93874.2 },
  { project: "fight strength brainrots/wt-world4", idle_s: 136431.4 },
];

ok("five minutes is the window", IN_USE_S === 300);
ok("the tab an agent called ten seconds ago is in use", tabInUse(found[0]));
ok("a tab quiet for an hour, a day or two days is not", found.slice(1).every((t) => !tabInUse(t)));
ok("of the four found, one is in use", found.filter(tabInUse).length === 1);
ok("the backend's own verdict wins when it gives one", tabInUse({ active: false, idle_s: 1 }) === false
  && tabInUse({ active: true, idle_s: 99999 }) === true);
ok("a tab with no times at all is not in use", tabInUse({}) === false);

ok("the engine is in use while one tab is", engineInUse({ recent_generations: 0, tabs: found }));
ok("and not when every open tab is old", engineInUse({ recent_generations: 0, tabs: found.slice(1) }) === false);
ok("a generation in the last five minutes is use too", engineInUse({ recent_generations: 2, tabs: [] }));
ok("no state is not use", engineInUse(null) === false && engineInUse(undefined) === false && engineInUse({}) === false);

ok("a game tab closes after ten idle minutes by default", ENGINE_DEFAULTS.live_tab_idle_min === 10);
ok("a setting is kept", mergePrefs({ live_tab_idle_min: 25 }).live_tab_idle_min === 25);
ok("and clamped to a minute..a day", mergePrefs({ live_tab_idle_min: 0 }).live_tab_idle_min === 1
  && mergePrefs({ live_tab_idle_min: 99999 }).live_tab_idle_min === 1440);
ok("junk falls back to ten", mergePrefs({ live_tab_idle_min: "soon" }).live_tab_idle_min === 10);
ok("the browser's own limit is untouched", mergePrefs({}).browser_idle_min === ENGINE_DEFAULTS.browser_idle_min);

console.log(fails.length ? `\n  ${pass} passed, ${fails.length} FAILED\n  FAIL  ${fails.join("\n  FAIL  ")}` : `\n  ${pass} passed`);
if (fails.length) process.exit(1);

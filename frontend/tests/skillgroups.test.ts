// The list of skills must stay findable, and a skill must never fall out of it. An unknown group
// or a backend that sends no group at all still has to show every skill.
//
// Run: npm run test:skillgroups
import { byState, searchSkills, sectionsOf, toSwitch } from "../src/components/skillGroups";
import type { Skill, SkillGroup } from "../src/types";

let pass = 0;
const fails: string[] = [];
function eq(name: string, got: unknown, want: unknown) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a === b) { pass++; return; }
  fails.push(name + "  <- got " + a + ", want " + b);
}

const sk = (id: string, group: string | undefined, enabled = false, description = ""): Skill =>
  ({ id, name: id, description, scope: "personal", enabled, state: enabled ? "on" : "off", group });
const GROUPS: SkillGroup[] = [
  { id: "game", label: "Game design & development" },
  { id: "video", label: "Video & montage" },
  { id: "documents", label: "Documents" },
  { id: "other", label: "Other" },
];
const LIST = [
  sk("zeta-cut", "video", false, "Cut a timeline"),
  sk("alpha-cut", "video", true, "Trim clips"),
  sk("hud", "game", false, "A fusion of panels"),
  sk("fusion-kit", "video", false, "Titles"),
  sk("odd", "no-such-group"),
  sk("older", undefined),
];

const secs = sectionsOf(LIST, GROUPS);
eq("the groups keep the backend's order", secs.map((s) => s.group.id), ["game", "video", "other"]);
eq("an empty group is not shown", secs.some((s) => s.group.id === "documents"), false);
eq("no skill is lost", secs.reduce((n, s) => n + s.skills.length, 0), LIST.length);
eq("an unknown group and no group land in Other", secs[2].skills.map((s) => s.id), ["odd", "older"]);
eq("on comes first in a group", secs[1].skills.map((s) => s.id), ["alpha-cut", "fusion-kit", "zeta-cut"]);
eq("the on count is right", secs[1].on, 1);
eq("Other exists even if the backend sent no groups", sectionsOf(LIST, []).map((s) => s.skills.length), [LIST.length]);
eq("Other has a label with no groups", sectionsOf(LIST, [])[0].group.label, "Other");

const ids = (q: string) => searchSkills(LIST, GROUPS, q).map((s) => s.id);
eq("no search, no hits", ids("  "), []);
eq("a search reads the description", ids("timeline"), ["zeta-cut"]);
eq("a search reads the group name", ids("montage").length, 3);
eq("a hit in the name comes first", ids("fusion"), ["fusion-kit", "hud"]);
eq("every word must match", ids("montage trim"), ["alpha-cut"]);
eq("a search ignores case", ids("FUSION-KIT"), ["fusion-kit"]);
eq("nothing is nothing", ids("zzz"), []);

eq("a group switch skips what is already right", toSwitch(LIST, true).length, LIST.length - 1);
eq("...in the other direction too", toSwitch(LIST, false).map((s) => s.id), ["alpha-cut"]);
eq("byState sorts on first, then the name", [sk("b", "x"), sk("c", "x", true), sk("a", "x")].sort(byState).map((s) => s.id), ["c", "a", "b"]);

console.log(`skillgroups: ${pass} passed, ${fails.length} failed`);
if (fails.length) {
  for (const f of fails) console.log("FAIL " + f);
  process.exit(1);
}

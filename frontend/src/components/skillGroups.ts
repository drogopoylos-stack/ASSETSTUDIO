// What the skills screens show, with no React in it: which skills a search finds, and the groups
// they fall under. The backend decides the group of a skill (`skills.group_of`). This file must
// survive a backend that has not sent one yet, because a build can be newer than the running
// backend until the next restart. Such a skill lands in Other and it is not lost.
import type { Skill, SkillGroup } from "../types";

export const OTHER = "other";
export const groupOf = (s: Skill): string => s.group || OTHER;

export interface SkillSection {
  group: SkillGroup;
  skills: Skill[];
  on: number;
}

/** The enabled ones first, because "what is switched on" is the first question. */
export function byState(a: Skill, b: Skill): number {
  return Number(b.enabled) - Number(a.enabled) || a.name.localeCompare(b.name);
}

/** Every group that has a skill, in the backend's order. An unknown group id goes to Other. */
export function sectionsOf(skills: Skill[], groups: SkillGroup[]): SkillSection[] {
  const known = new Set(groups.map((g) => g.id));
  const all = known.has(OTHER) ? groups : [...groups, { id: OTHER, label: "Other" }];
  const home = (s: Skill) => (known.has(groupOf(s)) ? groupOf(s) : OTHER);
  return all
    .map((group) => {
      const inside = skills.filter((s) => home(s) === group.id).sort(byState);
      return { group, skills: inside, on: inside.filter((s) => s.enabled).length };
    })
    .filter((s) => s.skills.length > 0);
}

/** A search: every word must appear, in the name, the text, or the name of the group. A hit in the
 *  name comes first, so that "fusion" starts with the Fusion skills and not with a passing mention. */
export function searchSkills(skills: Skill[], groups: SkillGroup[], query: string): Skill[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const label = new Map(groups.map((g) => [g.id, g.label.toLowerCase()]));
  const hits = skills.filter((s) => {
    const text = [s.name, s.description, s.category || "", label.get(groupOf(s)) || ""].join(" ").toLowerCase();
    return words.every((w) => text.includes(w));
  });
  const inName = (s: Skill) => Number(words.some((w) => s.name.toLowerCase().includes(w)));
  return hits.sort((a, b) => inName(b) - inName(a) || byState(a, b));
}

/** The skills that a group switch must change. A skill already in that state needs no request. */
export const toSwitch = (skills: Skill[], enabled: boolean): Skill[] => skills.filter((s) => s.enabled !== enabled);

// One short name for a model id, wherever the Studio shows one.
//
// It lived inside ModelBadge.tsx until Opus 5.5 shipped and showed why it belongs in a file with a
// test: the rules are SUBSTRING rules read in order, so `opus-5` also matches `claude-opus-5-5` and
// the badge called the new model "Opus 5". Longer ids come first now, and `models.test.ts` walks
// every id the picker can send, with and without the `[1m]` suffix the backend adds.
//
// It also owns the DeepSeek picker's rows, at the bottom of this file, for the same reason: a name
// the user READS is a name that has to be tested, and that menu shipped an older model's name once.

export function friendlyModel(id: string): string {
  const m = (id || "").toLowerCase();
  if (!m) return "";
  // DeepSeek Harness. Three ids are served by ONE model, V4.1 Flash — `deepseek-flash` is its
  // real id, and the V4-Flash names are aliases the API still routes there — so those rows say V4.1
  // Flash and the id in brackets is what tells them apart. `deepseek-v4-pro` is a separate model. Calling the first one plain "DeepSeek
  // Flash" is what once hid the model from the picker; the versions must not be collapsed again.
  if (m === "deepseek-flash") return "DeepSeek V4.1 Flash";
  if (m === "deepseek-v4-flash-vision-exp") return "DeepSeek V4.1 Flash (vision-exp id)";
  if (m === "deepseek-v4-flash") return "DeepSeek V4.1 Flash (v4-flash id)";
  // NOT an alias: its own model (DeepSeek-V4-Pro-0813) at about four times the Flash price.
  if (m === "deepseek-v4-pro") return "DeepSeek V4 Pro";
  if (m.includes("opus-5-5") || m.includes("opus-5.5")) return "Opus 5.5";
  if (m.includes("opus-5")) return "Opus 5";
  if (m.includes("opus-4-8") || m.includes("opus-4.8")) return "Opus 4.8";
  if (m.includes("opus")) return "Opus";
  if (m.includes("sonnet-5")) return "Sonnet 5";
  if (m.includes("sonnet-4-6") || m.includes("sonnet-4.6")) return "Sonnet 4.6";
  if (m.includes("sonnet")) return "Sonnet";
  if (m.includes("haiku")) return "Haiku";
  if (m.includes("fable-5-1") || m.includes("fable-5.1")) return "Fable 5.1";
  if (m.includes("mythos-5-1")) return "Mythos 5.1";
  if (m.includes("mythos")) return "Mythos 5";
  if (m.includes("fable")) return "Fable 5";
  if (m.includes("kimi-k3")) return "Kimi K3";
  if (m.includes("kimi-k2")) return "Kimi K2";
  if (m.includes("kimi")) return "Kimi";
  // Codex: gpt-6.1-sol -> GPT-6.1 Sol, gpt-6-astra -> GPT-6 Astra, gpt-5.5 -> GPT-5.5
  const g = /^gpt-(\d+(?:\.\d+)?)(?:-(.+))?$/.exec(m);
  if (g) return `GPT-${g[1]}${g[2] ? " " + g[2].split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") : ""}`;
  return id.replace(/^claude-/, "").replace(/-/g, " ");
}

// THE DEEPSEEK MENU, and why it lives in this file rather than beside the other engines' lists in
// ChatComposer: this menu is the one that was WRONG, and a menu is only correct where the user reads
// it. `friendlyModel` above can be perfect while the picker shows something else, which is exactly
// what happened — `deepseek-flash` has been DeepSeek V4.1 Flash since 2026-09-10, the Studio called
// that row "DeepSeek Flash", and the report was "V4.1 Flash is missing from the model bar" while the
// row sat there, sendable, the whole time. Nothing was broken but the name.
//
// ChatComposer imports this list, so the names below are the ones rendered. They read the name from
// `friendlyModel` rather than spelling it again, so the version has ONE source and this file's test
// covers the menu as well as the badge. A second hardcoded copy of a model's name is how the menu
// came to advertise an older model than the one answering.
//
// `default` is what the picker sends when nobody opens the menu, and it is server-side MODELS[0], so
// it has to be the vision row. WHICH ROW SEES is a separate question from which model answers and it
// is the runtime's: only the rows it catalogues `["text","image"]` carry a picture, and it drops an
// attached image before the request for the others — hence "vision" vs "text only" in the suffixes.
export const DEEPSEEK_MODELS: [string, string][] = [
  ["default", `${friendlyModel("deepseek-flash")} · vision`],
  ["deepseek-flash", `${friendlyModel("deepseek-flash")} · vision (pinned)`],
  ["deepseek-v4-flash-vision-exp", `${friendlyModel("deepseek-v4-flash-vision-exp")} · vision`],
  ["deepseek-v4-flash", `${friendlyModel("deepseek-v4-flash")} · text only`],
  ["deepseek-v4-pro", `${friendlyModel("deepseek-v4-pro")} · text only`],
];

// One name per model id, and the trap that made this file: the rules are substring rules in
// order, so `opus-5` also matches `claude-opus-5-5`. Every id the picker can send is walked here,
// bare and with the `[1m]` suffix the backend adds to any Opus pick.
//
// Run: npm run test:models
import { friendlyModel, DEEPSEEK_MODELS } from "../src/components/modelLabel";

let pass = 0;
const fails: string[] = [];
function eq(name: string, got: unknown, want: unknown) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a === b) { pass++; return; }
  fails.push(name + "  <- got " + a + ", want " + b);
}
const section = (t: string) => console.log("\n" + t);

section("the Opus family, newest first");
eq("Opus 5.5", friendlyModel("claude-opus-5-5"), "Opus 5.5");
eq("Opus 5.5 with the 1M suffix", friendlyModel("claude-opus-5-5[1m]"), "Opus 5.5");
eq("Opus 5.5 with a date", friendlyModel("claude-opus-5-5-20260601"), "Opus 5.5");
eq("Opus 5.5 on Bedrock", friendlyModel("us.anthropic.claude-opus-5-5"), "Opus 5.5");
eq("Opus 5.5 written with a dot", friendlyModel("claude-opus-5.5"), "Opus 5.5");
eq("Opus 5 is still Opus 5", friendlyModel("claude-opus-5"), "Opus 5");
eq("Opus 5 with the 1M suffix", friendlyModel("claude-opus-5[1m]"), "Opus 5");
eq("Opus 4.8", friendlyModel("claude-opus-4-8"), "Opus 4.8");
eq("the moving alias", friendlyModel("opus"), "Opus");

section("nothing else moved");
eq("GLM 5.3 row", friendlyModel("glm-5.3"), "GLM 5.3");
eq("GLM 5.3 Flash row", friendlyModel("glm-5.3-flash"), "GLM 5.3 Flash");
eq("GLM 5.3 as RIVER bills it", friendlyModel("GLM-5.3"), "GLM 5.3");
eq("GLM 5.3 Flash as RIVER bills it", friendlyModel("zai-org/GLM-5.3-Flash"), "GLM 5.3 Flash");
eq("Sonnet 5.5", friendlyModel("claude-sonnet-5-5"), "Sonnet 5.5");
eq("Sonnet 5.5 with context suffix", friendlyModel("claude-sonnet-5-5[1m]"), "Sonnet 5.5");
eq("Sonnet 5.5 with a date", friendlyModel("claude-sonnet-5-5-20260928"), "Sonnet 5.5");
eq("Sonnet 5.5 on Bedrock", friendlyModel("us.anthropic.claude-sonnet-5-5"), "Sonnet 5.5");
eq("Sonnet 5.5 written with a dot", friendlyModel("claude-sonnet-5.5"), "Sonnet 5.5");
eq("Sonnet 5", friendlyModel("claude-sonnet-5"), "Sonnet 5");
eq("Sonnet 4.6", friendlyModel("claude-sonnet-4-6"), "Sonnet 4.6");
eq("the Sonnet alias", friendlyModel("sonnet"), "Sonnet");
eq("Haiku", friendlyModel("claude-haiku-4-5-20251001"), "Haiku");
eq("Fable 5.1", friendlyModel("claude-fable-5-1"), "Fable 5.1");
eq("Fable 5", friendlyModel("claude-fable-5"), "Fable 5");
eq("Mythos 5.1", friendlyModel("claude-mythos-5-1"), "Mythos 5.1");
eq("Mythos 5", friendlyModel("claude-mythos-5"), "Mythos 5");
eq("Kimi K3", friendlyModel("kimi-k3"), "Kimi K3");
// DeepSeek Harness: ONE model answers on all four ids — V4.1 Flash — and the two rows that can SEE
// must not collapse into the two that cannot. The old label, "DeepSeek Flash", hid the version
// entirely, which is how V4.1 came to be reported as missing from the picker while its row sat there.
eq("the default row is V4.1 Flash", friendlyModel("deepseek-flash"), "DeepSeek V4.1 Flash");
eq("the legacy vision id", friendlyModel("deepseek-v4-flash-vision-exp"), "DeepSeek V4.1 Flash (vision-exp id)");
eq("the plain V4 id is the same model", friendlyModel("deepseek-v4-flash"), "DeepSeek V4.1 Flash (v4-flash id)");
eq("the Pro id is its own model, not a Flash alias", friendlyModel("deepseek-v4-pro"), "DeepSeek V4 Pro");

// AND THE MENU ITSELF, read out of the composer rather than retyped here. `friendlyModel` can be
// perfect while the picker shows something else — which is precisely what happened: the row existed,
// was sendable, and read as an older unversioned model, so V4.1 Flash was reported as absent from a
// menu that listed it all along. A label is only correct where the user reads it.
section("the DeepSeek menu the picker actually shows");
const rows = new Map(DEEPSEEK_MODELS);
eq("six ids, plus the default row", DEEPSEEK_MODELS.length, 7);
for (const id of ["deepseek-flash", "deepseek-v4-flash-vision-exp", "deepseek-v4-flash", "deepseek-v4-pro", "glm-5.3", "glm-5.3-flash"]) {
  eq("the picker can still send " + id, rows.has(id), true);
}
// V4 Pro is its own model (DeepSeek-V4-Pro-0813) at its own price, the GLM rows are GLM on RIVER.AI,
// and every other id is V4.1 Flash.
const answers: Record<string, string> = { "deepseek-v4-pro": "DeepSeek V4 Pro", "glm-5.3": "GLM 5.3 ·", "glm-5.3-flash": "GLM 5.3 Flash" };
for (const [id, label] of DEEPSEEK_MODELS) {
  eq(id + " names the model that answers", label.startsWith(answers[id] ?? "DeepSeek V4.1 Flash"), true);
}
eq("the GLM rows say they need RIVER.AI and take text only",
   ["glm-5.3", "glm-5.3-flash"].every((id) => /RIVER\.AI · text only$/.test(String(rows.get(id)))), true);
// Vision and text-only must stay visibly apart: the runtime drops an attached picture on a
// text-only row, so a user who cannot tell them apart picks the wrong one and loses the image.
eq("the default row offers vision", rows.get("default"), "DeepSeek V4.1 Flash · vision");
eq("the pinned row too", rows.get("deepseek-flash"), "DeepSeek V4.1 Flash · vision (pinned)");
eq("the legacy vision row still says vision",
   String(rows.get("deepseek-v4-flash-vision-exp")).includes("vision"), true);
eq("the legacy text-only row says so",
   String(rows.get("deepseek-v4-flash")).includes("text only"), true);
eq("and so does the Pro row", String(rows.get("deepseek-v4-pro")).includes("text only"), true);
eq("nothing at all", friendlyModel(""), "");
eq("an id nobody listed", friendlyModel("claude-zephyr-9"), "zephyr 9");

section("Codex models, as Codex 0.159 lists them");
eq("GPT-6.1 Sol", friendlyModel("gpt-6.1-sol"), "GPT-6.1 Sol");
eq("GPT-6 Astra", friendlyModel("gpt-6-astra"), "GPT-6 Astra");
eq("GPT-5.6 Terra", friendlyModel("gpt-5.6-terra"), "GPT-5.6 Terra");
eq("GPT-5.5", friendlyModel("gpt-5.5"), "GPT-5.5");
eq("a two-word suffix", friendlyModel("gpt-5.4-mini-fast"), "GPT-5.4 Mini Fast");
eq("gpt inside another id is not a GPT label", friendlyModel("claude-gpt-x"), "gpt x");

console.log("\n" + pass + " passed, " + fails.length + " failed");
if (fails.length) {
  for (const f of fails) console.log("  FAIL " + f);
  process.exit(1);
}

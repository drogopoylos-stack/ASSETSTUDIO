// Mount the real composer in Electron; only the backend is replaced.
import React from "react";
import { createRoot } from "react-dom/client";
import { api } from "../src/api/client";
import { ChatComposer } from "../src/components/ChatComposer";
import { useStore } from "../src/store/useStore";

const calls: any[] = [];
const messages: string[] = [];
let fail = false;
let busy = true;
let passed = 0;
function check(label: string, value: unknown) {
  if (!value) throw new Error(label);
  passed++;
}
const settle = () => new Promise((r) => setTimeout(r, 50));
const defaults = { agents: [], commands: [], providers: [], jobs: [], styles: [], models: [], statuses: {} };
for (const key of Object.keys(api)) (api as any)[key] = async () => defaults;
(api as any).settings = async () => ({ cc_autolearn: false, cc_graphify: false, cc_web_tools: false });
(api as any).missionAgents = async () => ({ agents: [
  { id: "claude", name: "Claude", available: true }, { id: "codex", name: "Codex", available: true },
  { id: "deepseek-harness", name: "DeepSeek", available: true },
] });
(api as any).codexStatus = async () => ({ installed: true, ready: true });
(api as any).sessionSending = async () => ({ sending: busy });
(api as any).sessionSend = async (pid: string, body: any) => {
  calls.push({ pid, ...body });
  return fail ? { ok: false, error: "turn finished" }
    : { ok: true, steer_mode: body.steer ? "live" : "", steer_live: body.steer && body.agent === "claude" };
};
useStore.setState({ toast: (message: string) => { messages.push(message); } });

async function run() {
  const host = document.getElementById("root")!;
  let root = createRoot(host);
  async function mount(agent: string, variant: "card" | "panel" = "panel") {
    root.unmount();
    root = createRoot(host);
    root.render(<ChatComposer projectId="d--fixture" rootPath="D:/fixture" selectedAgent={agent} variant={variant} />);
    await settle();
  }
  async function type(text: string) {
    const field = host.querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, text);
    field.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
  }
  const steer = () => host.querySelector<HTMLButtonElement>('button[aria-label="Steer"]')!;
  for (const agent of ["claude", "codex"]) {
    await mount(agent, agent === "codex" ? "card" : "panel");
    check(`${agent}: button is visible`, steer());
    check(`${agent}: empty steer is disabled`, steer().disabled);
    await type("Use a blue theme");
    check(`${agent}: busy + text enables steer`, !steer().disabled);
    steer().click();
    await settle();
    const call = calls.at(-1);
    check(`${agent}: sends steer to the selected engine`, call.agent === agent && call.steer === true);
    check(`${agent}: preserves correction text`, call.message === "Use a blue theme");
    check(`${agent}: does not spawn companions or fork`, call.companions.length === 0 && call.fork === false);
    check(`${agent}: confirms acceptance`, messages.at(-1)?.includes("Steer accepted"));
    check(`${agent}: input clears after accepted send`, host.querySelector("textarea")!.value === "");

    await type("/steer Use green instead");
    host.querySelector<HTMLButtonElement>(".composer-send")!.click();
    await settle();
    check(`${agent}: slash command uses the same steer request`, calls.at(-1).steer && calls.at(-1).message === "Use green instead");

    fail = true;
    await type("Keep this correction");
    steer().click();
    await settle();
    check(`${agent}: failed steer restores input`, host.querySelector("textarea")!.value === "Keep this correction");
    check(`${agent}: failed steer is reported`, messages.at(-1) === "turn finished");
    fail = false;
  }
  busy = false;
  await mount("claude");
  await type("idle correction");
  check("idle turn disables steer", steer().disabled);
  host.querySelector<HTMLButtonElement>(".composer-send")!.click();
  await settle();
  check("ordinary Send remains ordinary", calls.at(-1).steer === false);
  await mount("deepseek-harness");
  check("unsupported engine does not offer Steer", !steer());
  root.unmount();
  return `${passed} composer steering checks passed`;
}
(window as any).__steerDone = run().then((result) => ({ ok: true, result }), (error) => ({ ok: false, error: String(error.stack || error) }));

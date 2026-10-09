import React from "react";
import { createRoot } from "react-dom/client";
import { api } from "../src/api/client";
import { ContextMeter } from "../src/components/ContextMeter";

let passed = 0;
const check = (label: string, ok: unknown) => { if (!ok) throw new Error(label); passed++; };
const settle = (ms = 60) => new Promise((r) => setTimeout(r, ms));
async function run() {
  const host = document.getElementById("root")!;
  const root = createRoot(host);
  let clicks = 0;
  const base = { ctx_used: 140000, ctx_max: 200000, ctx_pct: 70, ctx_remaining: 24, model: "claude-opus-5-5" };
  for (const cli of [false, true]) {
    root.render(<ContextMeter data={base} cli={cli} onCompact={() => clicks++} />);
    await settle();
    check("Claude ctx is visible", host.textContent?.includes("70%"));
    const btn = host.querySelector("button")!;
    check("idle compact is available", !btn.disabled);
    btn.click();
    check("compact callback fires", clicks === (cli ? 2 : 1));
    root.render(<ContextMeter data={{ ...base, working: true }} cli={cli} onCompact={() => clicks++} />);
    await settle();
    check("running turn cannot be interrupted by compact", host.querySelector("button")?.disabled);
    root.render(<ContextMeter data={{ compacting: true }} cli={cli} onCompact={() => clicks++} />);
    await settle();
    check("compaction visible even before a context reading", host.textContent?.includes("Compacting"));
    check("no second compact while compacting", !host.querySelector("button"));
  }
  let backend: any = base;
  api.missionContext = async () => backend;
  const mount = (refreshSignal: number) => root.render(<ContextMeter projectId="d--fixture" folderId="d--fixture"
    refreshSignal={refreshSignal} onCompact={() => clicks++} />);
  mount(0);
  await settle();
  check("poll loads Claude reading", host.textContent?.includes("70%"));
  backend = { ...base, compacting: true };
  mount(1);
  await settle(300);
  check("refresh shows compact in progress", host.textContent?.includes("Compacting"));
  backend = { ...base, ctx_used: 30000, ctx_pct: 15, just_compacted: true, compact_trigger: "manual" };
  mount(2);
  await settle(300);
  check("refresh drops meter without a new chat message", host.textContent?.includes("~15%"));
  check("finished status is visible", host.textContent?.includes("compacted"));
  check("old compacting status is gone", !host.textContent?.includes("Compacting"));
  backend = { ...base, ctx_used: 40000, ctx_pct: 20, just_compacted: false };
  mount(3);
  await settle(300);
  check("next real turn replaces estimate", host.textContent?.includes("20%") && !host.textContent?.includes("~"));
  root.unmount();
  return `${passed} Claude context meter checks passed`;
}
(window as any).__steerDone = run().then((result) => ({ ok: true, result })).catch((e) => ({ ok: false, error: e.stack }));

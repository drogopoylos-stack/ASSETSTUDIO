import { useEffect, useState } from "react";
import { Cpu } from "lucide-react";
import { api } from "../api/client";
import { cls, pollWhileVisible } from "./ui";
import { feedAgent, readEffort, readPref } from "./sendPrefs";
import { friendlyModel } from "./modelLabel";

const EFFORT_LABEL: Record<string, string> = {
  low: "low", medium: "medium", high: "high", xhigh: "x-high", max: "max",
  ultracode: "ultracode · x-high",
  // Codex's own levels
  ultra: "ultra", minimal: "minimal", none: "none",
};

// Shows the model (and the selected effort/ultracode) for a Claude Code session —
// e.g. "Opus 4.8 · ultracode". Model comes from the transcript (what actually ran);
// effort comes from the composer's current selection. Pass `model` (mission card)
// or `projectId` to self-poll (Workspace header).
export function ModelBadge({ model: modelProp, projectId, folderId, className, cli }: {
  model?: string; projectId?: string; className?: string;
  /** The folder the chat box keeps its settings under (the pane's project id). `projectId` is the
   *  FEED's id, which carries an "<engine>--" prefix for an alternate engine. */
  folderId?: string;
  /** the console look: monospace, a ✻ instead of a chip icon, the id rather than a title */
  cli?: boolean;
}) {
  const [model, setModel] = useState(modelProp || "");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (modelProp !== undefined) { setModel(modelProp); return; }
    if (!projectId) return;
    setModel("");
    let alive = true;
    const stop = pollWhileVisible(() => api.missionContext(projectId).then((c) => alive && setModel(c.model || "")).catch(() => {}), 10000);
    return () => { alive = false; stop(); };
  }, [modelProp, projectId]);

  // re-read the composer's effort/model selection periodically (no store dependency)
  useEffect(() => {
    const iv = window.setInterval(() => { if (!document.hidden) setTick((t) => t + 1); }, 3000);
    return () => window.clearInterval(iv);
  }, []);

  // THIS pane's settings, the ones its own chat box sends: the folder's model and that model's own
  // effort. This read the agent-wide keys - the last choice made in ANY folder - so the header said
  // "high" above a chat box set to max. The engine is the feed's (a "kimi--" feed shows Kimi's).
  const folder = folderId || projectId || "";
  const agentSel = feedAgent(projectId || folder, folder);
  const selModel = readPref("model", folder, agentSel, "default");
  const fallbackName = agentSel === "kimi" ? "Kimi K3" : agentSel === "codex" ? "Codex" : agentSel === "deepseek-harness" ? "DeepSeek V4.1 Flash" : "Claude";
  const name = friendlyModel(model) || (selModel !== "default" ? friendlyModel(selModel) || selModel : fallbackName);
  const eff = readEffort(folder, agentSel, selModel);
  const effLabel = EFFORT_LABEL[eff] || "";
  // The name is the model that answered last; the chat box can be set to another one since.
  const next = selModel !== "default" ? friendlyModel(selModel) || selModel : "";
  const nextNote = next && model && friendlyModel(model) && next !== friendlyModel(model)
    ? `\nThe chat box is set to ${next}; the next message uses it.` : "";
  void tick; // forces re-read of localStorage every few seconds

  return (
    <span className={cls("inline-flex items-center gap-1 text-[11px] whitespace-nowrap",
      cli && "font-mono", className)}
      title={`Model: ${name}${effLabel ? ` · effort: ${effLabel}` : ""}${nextNote}`}>
      {/* the console prints the star, not a chip icon */}
      {cli ? <span className="text-brand shrink-0 select-none">✻</span>
           : <Cpu size={11} className="text-brand shrink-0" />}
      <span className={cls(cli ? "text-text/85" : "text-text/85 font-medium")}>
        {cli ? name.toLowerCase().replace(/\s+/g, "-") : name}
      </span>
      {effLabel && <span className="text-muted/70">· {cli ? effLabel.toLowerCase() : effLabel}</span>}
    </span>
  );
}

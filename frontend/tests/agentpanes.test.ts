import assert from "node:assert/strict";
import { paneAgent, writePaneAgent, feedAgent, folderAgent, modeFallback } from "../src/components/sendPrefs";
import { PRESETS, splitChatPane, valid } from "../src/components/paneLayout";

class Memory {
  data = new Map<string, string>([["cc-agent", "claude"]]);
  getItem(key: string) { return this.data.get(key) || null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
}
const store = new Memory();
writePaneAgent("left", "d--workspace", "claude", store);
writePaneAgent("right", "d--workspace", "codex", store);
assert.equal(paneAgent("left", "D--workspace", store), "claude");
assert.equal(paneAgent("right", "d--workspace", store), "codex");
writePaneAgent("right", "d--workspace", "deepseek-harness", store);
assert.equal(paneAgent("left", "d--workspace", store), "claude");
assert.equal(paneAgent("right", "d--workspace", store), "deepseek-harness");
assert.equal(paneAgent("right", "d--other", store), "claude");
assert.equal(feedAgent("deepseek-harness--d--workspace", "d--workspace"), "deepseek-harness");
assert.equal(modeFallback("deepseek-harness"), "full");

// THE PANE THE SUBAGENTS BUTTON OPENS. Clicking an agent in the rail turns a spare EDITOR pane into
// an agent pane, and that pane is addressed by `feedOf(root, pane.id)` — the pane's stored engine
// plus the folder. An editor pane has no stored engine, so it fell through to `folderAgent`: the
// folder's pin, else whatever engine was last picked anywhere. In a folder whose chat box is Claude
// — the default, and exactly the trap set below — a Codex agent therefore opened in a pane that
// asked CLAUDE's transcript layout for it, and the rail showed 2 beside a pane reading "nothing".
// The engine is known when the pane is made, so it is written; this pins that the write is the same
// lookup the pane will do when it renders.
const rail = new Memory();
assert.equal(folderAgent("d--game", rail), "claude");
writePaneAgent("pane-3", "d--game", "codex", rail);
assert.equal(paneAgent("pane-3", "d--game", rail), "codex");
assert.equal(feedAgent("codex--d--game", "d--game"), paneAgent("pane-3", "d--game", rail));
// ...and a pane with no engine of its own still follows the folder, as every other pane does.
assert.equal(paneAgent("pane-4", "d--game", rail), "claude");

// Every preset can split any pane: cells remain covered exactly once and unrelated
// editors retain their identities and references rather than being replaced.
for (const preset of PRESETS) {
  const original = preset.build([]);
  for (const target of original.panes) {
    const layout = splitChatPane(original, target.id, "d--workspace");
    assert(valid(layout));
    assert.equal(layout.panes.length, original.panes.length + 1);
    assert.equal(layout.panes.find((p) => p.id === target.id)?.ref, "d--workspace");
    for (let row = 0; row < layout.rows.length; row++) for (let col = 0; col < layout.cols.length; col++) {
      assert.equal(layout.panes.filter((p) => row >= p.row && row < p.row + p.rowSpan && col >= p.col && col < p.col + p.colSpan).length, 1);
    }
    for (const p of original.panes.filter((p) => p.id !== target.id)) {
      assert.equal(layout.panes.find((n) => n.id === p.id)?.kind, p.kind);
      assert.equal(layout.panes.find((n) => n.id === p.id)?.ref, p.ref);
    }
  }
}
console.log("Agent pane isolation and all preset split geometries passed");

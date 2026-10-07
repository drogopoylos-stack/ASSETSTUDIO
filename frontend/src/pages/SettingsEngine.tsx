// Settings → Studio engine: every setting of the Engine window, the editor, the live game link,
// the forge and the headless browser, in one pane.
//
// Two kinds of key live here. The nested `engine` object (see components/engine/prefs.ts) is read
// by the window and the editor, so a change applies to an open editor at once. A handful of flat
// keys — cc_live, cc_forge, cc_ops, cc_review*, cc_engine_view, sweep_browser_windows — already
// existed and are read by the backend; they are the same setting wherever they are shown.

import { useEffect, useState, type ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import { api } from "../api/client";
import { Section, cls, settingSlug } from "../components/ui";
import { ENGINE_DEFAULTS, VIEW_PRESETS, mergePrefs, type EnginePrefs } from "../components/engine/prefs";
import type { BoostStatus } from "../types";

/** The forge's cameras, in step with `_VIEWS` in live.py. */
const FORGE_VIEWS: { key: string; label: string }[] = [
  { key: "3q", label: "three-quarter" }, { key: "front", label: "front" }, { key: "back", label: "back" },
  { key: "side", label: "side" }, { key: "left", label: "left" }, { key: "top", label: "top" },
  { key: "bottom", label: "bottom" }, { key: "low", label: "low" }, { key: "hero", label: "hero" },
  { key: "back3q", label: "back three-quarter" },
];

const RESTART = "The backend reads this after its next restart.";

function Row({ label, hint, children }: { label: string; hint?: string; children?: ReactNode }) {
  return (
    <div data-setting={settingSlug(label)}
      className="flex items-start justify-between gap-4 py-1.5 border-b border-line/40 last:border-0 scroll-mt-24">
      <div className="min-w-0">
        <div className="text-sm">{label}</div>
        {hint && <div className="text-xs text-muted mt-0.5 max-w-lg">{hint}</div>}
      </div>
      <div className="shrink-0 flex items-center gap-2 pt-0.5">{children}</div>
    </div>
  );
}

function Sel<T extends string>({ value, options, onChange }: { value: T; options: { v: T; l: string }[]; onChange: (v: T) => void }) {
  return (
    <select className="input !py-1 text-xs min-w-[9rem]" value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  );
}

function Num({ value, min, max, step, unit, onChange }: {
  value: number; min: number; max: number; step: number; unit?: string; onChange: (n: number) => void;
}) {
  return (
    <label className="flex items-center gap-1.5 text-xs">
      <input type="number" className="input !py-1 text-xs w-24" value={value} min={min} max={max} step={step}
        onChange={(e) => { const n = parseFloat(e.target.value); if (Number.isFinite(n)) onChange(Math.min(max, Math.max(min, n))); }} />
      {unit && <span className="text-muted">{unit}</span>}
    </label>
  );
}

function Check({ on, label, disabled, onChange }: { on: boolean; label: string; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className={cls("flex items-center gap-1.5 select-none", disabled ? "opacity-50" : "cursor-pointer")}>
      <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="text-xs">{label}</span>
    </label>
  );
}

const onOff = (v: boolean) => (v ? "On" : "Off");

/** One capability: what it is, whether it is on, what leaving it on costs, and what stops it. */
interface Switch {
  key: string;
  label: string;
  on: boolean;
  why: string;
  /** Tokens on every turn, when this puts a paragraph in the agent's prompt. 0 = costs nothing. */
  cost?: number;
  /** Non-empty when this machine cannot do it — the row says so instead of failing later. */
  blocked?: string;
  set: (v: boolean) => void;
}

/**
 * WHAT IS ON, IN ONE PLACE.
 *
 * The pane used to open on two dropdowns, with the switches scattered three screens down among
 * the tuning — so the first question anybody has, "is the forge on?", took six sections to
 * answer. These are the switches themselves, not a summary of them: a summary beside a control
 * is a second place to be wrong.
 *
 * Everything below this section is HOW a capability behaves. This is WHETHER it exists.
 */
function Switchboard({ rows }: { rows: Switch[] }) {
  const on = rows.filter((r) => r.on && !r.blocked).length;
  const cost = rows.reduce((n, r) => n + (r.on && !r.blocked ? (r.cost || 0) : 0), 0);
  return (
    <Section title="What is on"
      desc="Every capability of the engine, with its own switch. Below this section is how each one behaves; here is whether it exists at all."
      right={<div className="text-xs text-muted text-right">
        <div><span className="text-text tabular-nums">{on}</span> of {rows.length} on</div>
        {cost > 0 && <div className="tabular-nums">~{cost.toLocaleString()} tokens a turn</div>}
      </div>}>
      <div className="-mx-1">
        {rows.map((r) => (
          <button key={r.key} type="button" disabled={!!r.blocked}
            data-setting={settingSlug(r.label)}
            onClick={() => r.set(!r.on)}
            title={r.blocked || (r.on ? "Switch off" : "Switch on")}
            className={cls("w-full text-left flex items-start gap-3 px-1 py-2 rounded-lg transition-colors scroll-mt-24",
              r.blocked ? "opacity-60 cursor-not-allowed" : "hover:bg-panel2/60 cursor-pointer")}>
            {/* The dot is the answer at a glance: lit means an agent can use this right now. */}
            <span className={cls("mt-1.5 h-2 w-2 rounded-full shrink-0",
              r.blocked ? "bg-warn" : r.on ? "bg-ok" : "bg-line")} />
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2 flex-wrap">
                <span className={cls("text-sm", !r.on && !r.blocked && "text-muted")}>{r.label}</span>
                <span className={cls("text-[10px] uppercase tracking-wide tabular-nums",
                  r.blocked ? "text-warn" : r.on ? "text-ok" : "text-muted/60")}>
                  {r.blocked ? "unavailable" : r.on ? "on" : "off"}
                </span>
                {!!r.cost && !r.blocked && (
                  <span className="text-[10px] text-muted/60 tabular-nums">~{r.cost} tokens a turn</span>
                )}
              </span>
              <span className="block text-xs text-muted mt-0.5 max-w-2xl">{r.blocked || r.why}</span>
            </span>
            <span className={cls("shrink-0 mt-0.5 flex items-center h-5 w-9 rounded-full px-0.5 transition-colors",
              r.blocked ? "bg-line/50 justify-start" : r.on ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
              <span className="h-4 w-4 rounded-full bg-white shadow" />
            </span>
          </button>
        ))}
      </div>
    </Section>
  );
}

export function EnginePane() {
  const [p, setP] = useState<EnginePrefs>(ENGINE_DEFAULTS);
  const [flat, setFlat] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState("");
  const [liveWhy, setLiveWhy] = useState("");
  const [reviewWhy, setReviewWhy] = useState("");
  // BOOST's recommendations, read from the backend rather than hard-coded here: the reason a switch
  // is recommended and the price of changing it are the backend's to state, and duplicating them in
  // the UI would be a second place to be wrong.
  const [boostSt, setBoostSt] = useState<BoostStatus | null>(null);
  const reloadBoost = () => api.boostStatus().then(setBoostSt).catch(() => {});

  useEffect(() => {
    api.settings().then((st) => { setP(mergePrefs(st?.engine)); setFlat(st || {}); }).catch(() => {});
    reloadBoost();
    // Both switches are honest about the machine: with no Chrome there is nothing to turn on.
    api.reviewStatus().then((r) => setReviewWhy(r?.ok ? "" : r?.error || "")).catch(() => {});
    api.liveGameStatus().then((r) => setLiveWhy(r?.available ? "" : r?.why || "")).catch(() => {});
  }, []);

  const fail = (e: any) => setSaved("could not save: " + String(e?.message || e));
  /** One field of the nested `engine` object. `restart` marks the keys only new backend code reads. */
  const setPref = (patch: Partial<EnginePrefs>, note: string, restart = false) => {
    setP((cur) => ({ ...cur, ...patch }));
    api.updateSettings({ engine: patch }).then(() => setSaved(note + (restart ? " " + RESTART : ""))).catch(fail);
  };
  /** A flat key the backend already reads; it takes effect at once. */
  const setKey = (key: string, value: any, note: string) => {
    setFlat((f) => ({ ...f, [key]: value }));
    api.updateSettings({ [key]: value }).then(() => setSaved(note)).catch(fail);
  };
  /** One of BOOST's RECOMMENDED switches — the ones BOOST will not move by itself. It re-reads the
   *  status afterwards, because whether the switch now matches the recommendation is the backend's
   *  answer to give (and `now` on those rows is read from the backend, not guessed here). */
  const setBoostKey = (key: string, value: boolean, label: string) => {
    setFlat((f) => ({ ...f, [key]: value }));
    api.updateSettings({ [key]: value })
      .then(() => { setSaved(label + " " + onOff(value).toLowerCase() + "."); reloadBoost(); })
      .catch(fail);
  };

  const view = flat.cc_engine_view || { w: 1280, h: 720, label: "720p" };
  const viewKey = `${view.w}x${view.h}`;
  const toggleView = (k: string) => {
    const has = p.forge_views.includes(k);
    const next = has ? p.forge_views.filter((v) => v !== k) : [...p.forge_views, k];
    if (!next.length) return;                       // a forge call with no camera renders nothing
    setPref({ forge_views: next }, "Forge views: " + next.join(", ") + ".", true);
  };
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

  // EVERY SWITCH IN THE PANE, in the order somebody would ask about them. The token costs are
  // the measured ones from Planning & review, where the same notes are listed; a capability that
  // adds nothing to the prompt has no number rather than a zero.
  const switches: Switch[] = [
    // BOOST first: it is the only switch here about the BILL, and it is the one somebody comes
    // to Settings looking for after a long session. The backend applies a profile with it and
    // remembers what it replaced, so switching it off is never a one-way door.
    { key: "boost", label: "BOOST: spend less per prompt", on: !!flat.boost, cost: 0,
      why: "Send fewer tokens for the same answer, using this PC instead of the API. On, a prompt goes through four local economies: the message is compressed on your CPU before it leaves (ANSI noise, a run of identical log lines, blank runs — never a reworded sentence, and never inside a code fence); the project's own code graph is asked about the symbols you named, so the agent does not spend a turn grepping to find them; one short constant note tells it to work that way; and two switches that are free to move — the phase archive and the system-prompt snapshot — go on. Everything it moved is put back when you switch it off. What BOOST deliberately will NOT move are the two switches that ride the system prompt itself: flipping one of those makes the next send re-send the whole conversation under a new prefix, so a cache READ at ~0.1x becomes a cache WRITE at ~1.25x — on a long session that single line costs more than every note it could ever remove. Those two are listed under this one as recommendations, each with what it buys and what changing it costs, for you to flip between conversations where it is free. The message is the part BELOW the provider's cache breakpoint, which is why rewriting it cannot cost you a cache miss; the system prompt above it is never touched. What BOOST deliberately does not do: no semantic answer cache and no per-turn prompt rewriting, both of which cost more than they save on a coding session.",
      set: (v) => setKey("boost", v, v ? "BOOST on: prompts are compressed here first, symbols are answered from the code graph when one exists, and the two free switches are applied. Anything it moved is put back when you switch it off. Takes effect on the next message." : "BOOST off: prompts go as written and the switches it moved are back where they were.") },
    { key: "cc_live", label: "Live game link", on: flat.cc_live !== false, blocked: liveWhy, cost: 0,
      why: "Agents question and change the running game — its scene, its console, its frame cost — and the Edit tab mirrors it by name.",
      set: (v) => setKey("cc_live", v, "Live link " + onOff(v).toLowerCase() + ".") },
    { key: "cc_forge", label: "The forge", on: flat.cc_forge !== false,
      why: "Build an asset in code, then look at it and measure it in a lit studio. Off: agents are never told it exists, and the endpoint refuses.",
      set: (v) => setKey("cc_forge", v, "Forge note " + onOff(v).toLowerCase() + ".") },
    { key: "cc_ops", label: "Mesh tools", on: flat.cc_ops !== false,
      why: "Optional. On: agents are told about the modelling library and may use it when it saves work — skin, subsurf, bevel, unwrap, bakes, heat weights, remesh, booleans and check(), and Blender's own chain in code: sculpt strokes, decimation to a game budget, smart materials in one texture set, complete hands that close on a handle, sweeps and rolled rims. Off: agents build in plain code and are not told about it; ask an agent to use the Studio's tools and it can still find them.",
      set: (v) => setKey("cc_ops", v, "Mesh tools note " + onOff(v).toLowerCase() + ".") },
    { key: "cc_mcp", label: "Studio tools over MCP", on: flat.cc_mcp !== false, cost: 0,
      why: "Every agent session also gets the Studio engine as MCP tools: forge, look, aim, the live game link, scene edit, review, the code graph and web fetch — only the ones whose switches above are on. A tool is the same call as the curl command in the agent's notes, so the agent uses whichever it prefers. What changes: typed arguments instead of JavaScript quoted inside a shell command, and a render's picture in the same answer instead of a second step. Needs a new session to take effect. Off: curl only, as before.",
      set: (v) => setKey("cc_mcp", v, v ? "New sessions get the Studio tools over MCP." : "New sessions use curl only.") },
    { key: "cc_vertedit", label: "Edit mode: the vertices", on: !!flat.cc_vertedit, cost: 322,
      why: "Move vertices, edges and faces by hand in the Edit tab, and keep the move when the code runs again. Blender cannot do this — its mesh is stored, so it has nothing to rebuild against.",
      set: (v) => setKey("cc_vertedit", v, "Edit mode note " + onOff(v).toLowerCase() + ".") },
    { key: "cc_animate", label: "Animation review", on: !!flat.cc_animate, blocked: reviewWhy, cost: 274,
      why: "Run a cycle and photograph every frame on ONE fixed camera, with the feet measured against a ground that does not move. Says which foot floats or slides while it is planted, and when. Blender, Unity and Godot all hand back a screenshot and leave the judging to your eyes.",
      set: (v) => setKey("cc_animate", v, "Animation review " + onOff(v).toLowerCase() + ".") },
    { key: "cc_navigate", label: "Navigate the running game", on: !!flat.cc_navigate, blocked: reviewWhy, cost: 386,
      why: "Go to a place in the running game and look at it: a camera the Studio owns that the game's follow-camera cannot fight, the named things in view with their distances, and locate — a screenshot you pasted turned into a camera pose by rendering candidate views and scoring them against it. After a code change, the same view again with how much of the picture changed.",
      set: (v) => setKey("cc_navigate", v, "Navigate " + onOff(v).toLowerCase() + ".") },
    { key: "cc_scene_edit", label: "Scene edit for agents", on: flat.cc_scene_edit !== false, blocked: liveWhy, cost: 715,
      why: "The running game's objects as an agent's own tools: each one's key, world size and the code line that named it; move, turn, scale, hide and place the game's own assets, with the answer saying whether the thing now floats, sinks or overlaps; undo; save a change to studio.edits.json so the Edit tab and the next reload see it; and the asset sheet, the whole shelf in one picture. Without it an agent writes its own eval, and the move is gone at the next reload. It rides the live game link.",
      set: (v) => setKey("cc_scene_edit", v, "Scene edit " + onOff(v).toLowerCase() + ".") },
    { key: "cc_new_game", label: "New game template", on: flat.cc_new_game !== false, cost: 189,
      why: "In a folder with no game in it yet, the agent is told it can start one that is Studio-ready from the first minute — the app reachable, saved edits applied, review targets, a dev server — instead of wiring its own screenshots and test pages. Costs nothing in a folder that already has a game.",
      set: (v) => setKey("cc_new_game", v, "New game note " + onOff(v).toLowerCase() + ".") },
    { key: "cc_debugger", label: "Debugger", on: !!flat.cc_debugger, blocked: reviewWhy, cost: 163,
      why: "Stop asset code where it threw and read every variable that was in scope, with real line numbers. The page is paused for a moment and always resumed.",
      set: (v) => setKey("cc_debugger", v, "Debugger " + onOff(v).toLowerCase() + ".") },
    { key: "cc_code_tools", label: "Code tools and tests", on: !!flat.cc_code_tools, cost: 241,
      why: "A file's fingerprint without reading it, a typecheck, a narrow edit that refuses when the file moved under you, and the test suites as a job with pass and fail counts.",
      set: (v) => setKey("cc_code_tools", v, "Code tools " + onOff(v).toLowerCase() + ".") },
    { key: "cc_review", label: "Visual review", on: !!flat.cc_review, blocked: reviewWhy, cost: 687,
      why: "Contact sheets of the running game on a stepped clock, with findings and metrics, so an effect is judged from the frames it actually has.",
      set: (v) => setKey("cc_review", v, "Visual review " + onOff(v).toLowerCase() + ".") },
    { key: "scene_look", label: "A picture with every scene change", on: p.scene_look,
      why: "When an agent moves or places something in the running game, the answer carries a picture: the object framed from a Studio camera, before beside after, with its outline and what changed written under each. Without it the agent reads numbers about the move and works half blind, or asks for a picture in a second call. It costs about half a second per change, and the game's own camera is handed back at once. An agent can still ask for none, or for the player's view.",
      set: (v) => setPref({ scene_look: v }, v ? "Every scene change answers with a picture." : "Scene changes answer with numbers only.", true) },
    { key: "live_sidecar", label: "Apply saved edits when a game opens", on: p.live_sidecar,
      why: "A project's studio.edits.json, made in the Edit tab, is applied by name to the running game as it opens.",
      set: (v) => setPref({ live_sidecar: v }, v ? "Saved edits are applied when a game opens." : "Saved edits are not applied at open.", true) },
    { key: "forge_detail", label: "Detail review of characters", on: p.forge_detail,
      why: "A character built with a reference gets its face, head, hair and torso photographed close up, each beside the same crop of the reference, and each checked as built, upside down and mirrored. A face that matches best upside down is reported as upside down, with the texture that did it. Pictures of four sides are made only when a part is asked for or its numbers are wrong. Off: only the parts an agent names.",
      set: (v) => setPref({ forge_detail: v }, v ? "Characters get a detail review against the reference." : "Detail review only for parts an agent names.", true) },
    { key: "forge_colours", label: "Colour and size of every part", on: p.forge_colours,
      why: "Beside each part's close-up, the answer carries the part's own colour, the reference's colour in the same place, and how tall and wide the part is as a share of the figure's height. That is how \u201cthe blue shirt should have been longer\u201d and \u201cthe hair is too red\u201d become numbers instead of opinions. Measured from frames already taken, so it costs no extra render. Off: close-ups and the upside-down check only.",
      set: (v) => setPref({ forge_colours: v }, v ? "Every part reports its colour and size against the reference." : "No colour or size lines.", true) },
    { key: "forge_checks", label: "Symmetry, holes, gaps and contrast", on: p.forge_checks,
      why: "Four checks the goblin test against Blender asked for. A part that belongs on the middle line — a belt, a buckle, a nose — is reported when it sits to one side, and so are left and right twins of different sizes. A hole a person can see is reported; a surface that ends inside another part is not. A part that floats free of the rest is reported with the gap, and a held prop that no hand touches is named as that. When the render's darks or lights are much flatter than the reference's, the answer says which end is missing. Measured from frames and boxes already taken. Off: none of these lines.",
      set: (v) => setPref({ forge_checks: v }, v ? "The forge reports off-centre parts, holes, gaps and flat contrast." : "No symmetry, hole, gap or contrast lines.", true) },
    { key: "sweep_browser_windows", label: "Sweep leaked browser windows", on: flat.sweep_browser_windows !== false,
      why: "A review browser that outlived its backend is closed at boot, rather than holding memory until somebody notices it.",
      set: (v) => setKey("sweep_browser_windows", v, "Sweep " + onOff(v).toLowerCase() + ".") },
    { key: "perf_fast", label: "Instant window", on: flat.perf_fast !== false, cost: 0,
      why: "The backend answers from memory while the files behind an answer have not changed: the project list, the phase files, the agent-spawn count, which AI CLIs are installed, the engine state and the browser count. Measured before it: a workspace switch waited 662 ms for the file tree, 680 ms for the change list and 796 ms for the agent list, and an idle poll of the context meter reached 1,343 ms, all of it work already done. Each cache is keyed on the file dates it describes, so a change is always seen. Off: every request reads from disk again — slower, and impossible to be stale.",
      set: (v) => setKey("perf_fast", v, v ? "Answers come from memory until the files change." : "Every request reads from disk.") },
    { key: "needs_you", label: "Who needs you", on: flat.needs_you !== false, cost: 0,
      why: "A reading in the bottom bar, beside the CPU: how many projects stopped to ask you a question, and how many are still working — across every project, not only the open one. A session parked on a question is otherwise invisible until you happen to click on it. It draws NOTHING while they are all quiet, and clicking it lists them and jumps to one. Choose whether the bar carries it with the gear at the right-hand end of the bar; this switch decides whether the feature exists at all, and off it is not polled either. It reads the project overview the Studio already keeps, so it starts no scan of its own.",
      set: (v) => setKey("needs_you", v, v ? "The bottom bar says who is waiting on you." : "Nothing is watching the other projects.") },
    { key: "live_pick", label: "Send a page element", on: flat.live_pick !== false, cost: 0,
      why: "Click an element in the running page — the Inspect button in the workspace rail — and the page answers with what is under that point: the element, the computed style that actually applies, and a cropped picture of it, ready to send to the agent. Saying the same thing in words costs the agent three guesses. It needs the live game link, which has its own switch, and costs nothing until the tab is opened.",
      set: (v) => setKey("live_pick", v, v ? "Clicking the page sends the element to the agent." : "The Inspect tab is off.") },
    { key: "diff_notes", label: "Comment on a diff", on: flat.diff_notes !== false, cost: 0,
      why: "Write a remark on one line of a diff — in the chat feed or in a checkpoint — collect several, and hand them to the agent in one message with the code quoted. The quote is the point: a feed card for an Edit shows the diff of two strings, so its line numbers are not the file's, and only a checkpoint diff ever cites a number. Costs nothing until a comment is written.",
      set: (v) => setKey("diff_notes", v, v ? "Diff lines can be commented on." : "No comment buttons on a diff.") },
  ];

  // BOOST'S RECOMMENDATIONS, as rows somebody can actually flip.
  //
  // These are the switches BOOST refuses to move on your behalf, and the refusal is the economy:
  // each one puts text into the system prompt, so turning it on or off mid-conversation makes the
  // next send re-send the whole context under a new prefix — a cache READ at ~0.1x becomes a cache
  // WRITE at ~1.25x, and on a long session that one line costs more than every note the switch
  // could ever remove. Shown here, the decision costs nothing; flipped silently by BOOST, it would
  // be the most expensive thing in the app. The backend supplies the reason and the price, so this
  // list cannot drift from what BOOST actually does.
  const BOOST_LABELS: Record<string, string> = {
    cc_graphify: "Code graph: answer symbols locally",
    cc_autolearn: "Auto-learn skills",
  };
  const boostRows: Switch[] = (boostSt?.recommend || []).map((r) => ({
    key: "boost-rec-" + r.key,
    label: (BOOST_LABELS[r.key] || r.key) + " — BOOST recommends " + onOff(r.want).toLowerCase(),
    on: r.now,
    why: r.why + ". " + r.cost + ". BOOST leaves this one to you: it is not in BOOST's own profile, "
      + "so switching BOOST on or off never touches it — and neither does it ever move it silently.",
    set: (v) => setBoostKey(r.key, v, BOOST_LABELS[r.key] || r.key),
  }));

  return (
    <>
      <Switchboard rows={[...switches, ...boostRows]} />

      <Section title="Engine window" desc="The Studio Engine is a window of its own: Asset, Edit and Game tabs over every project.">
        <Row label="Open on" hint="The tab the window shows when it opens. A link from an agent still opens the Edit tab.">
          <Sel value={p.open_tab}
            options={[{ v: "asset", l: "Asset" }, { v: "edit", l: "Edit" }, { v: "game", l: "Game" }, { v: "library", l: "Library" }, { v: "last", l: "Last used" }]}
            onChange={(v) => setPref({ open_tab: v }, v === "last" ? "The window opens on the last used tab." : "The window opens on the " + cap(v) + " tab.")} />
        </Row>
        <Row label="Game view size" hint="The frame the Game tab shows a game in. Agents open a game on the live link at the same size, and a phone size gives them the touch layout.">
          <select className="input !py-1 text-xs min-w-[9rem]" value={viewKey}
            onChange={(e) => { const s = VIEW_PRESETS.find((x) => `${x.w}x${x.h}` === e.target.value); if (s) setKey("cc_engine_view", s, "Game view: " + s.label + "."); }}>
            {!VIEW_PRESETS.some((x) => `${x.w}x${x.h}` === viewKey) && <option value={viewKey}>{view.label || viewKey}</option>}
            {VIEW_PRESETS.map((s) => <option key={s.label} value={`${s.w}x${s.h}`}>{s.label} · {s.w}×{s.h}</option>)}
          </select>
        </Row>
      </Section>

      <Section title="Watching the agent" desc="The Game tab can show the agent's own game tab, live, while it works. These are spent only while someone watches.">
        <Row label="Frames a second" hint="How smooth the live view is. The browser only sends a frame when the game repaints.">
          <Num value={p.live_watch_fps} min={1} max={30} step={1}
            onChange={(n) => setPref({ live_watch_fps: Math.round(n) }, "Live view at " + Math.round(n) + " frames a second.")} />
        </Row>
        <Row label="Picture quality" hint="JPEG quality of each frame. Lower is lighter on the machine.">
          <Num value={p.live_watch_quality} min={30} max={95} step={5}
            onChange={(n) => setPref({ live_watch_quality: Math.round(n) }, "Live view quality " + Math.round(n) + ".")} />
        </Row>
        <Row label="Live view width" hint="The widest frame sent, in pixels.">
          <Num value={p.live_watch_width} min={320} max={1920} step={40} unit="px"
            onChange={(n) => setPref({ live_watch_width: Math.round(n) }, "Live view up to " + Math.round(n) + " px wide.")} />
        </Row>
      </Section>

      <Section title="Editor" desc="How the Edit tab opens. Anything here can still be changed inside the editor for the session.">
        <Row label="Terrain" hint={"Sculpt, paint and scatter ground in the Edit tab: nine brushes, four blended layers, and the project's own trees and rocks planted by the same builders the game calls. It costs one extra look for a .terrain.json beside every asset you open, and a 513-sample field is 524,288 triangles in the viewport and about 1.4 MB on disk. Off: the mode is not offered and nothing is read."}>
          <Check on={p.terrain} label={onOff(p.terrain)}
            onChange={(v) => setPref({ terrain: v }, v ? "Terrain mode is available in the Edit tab." : "Terrain mode is off.")} />
        </Row>
        <Row label="Chunked ground" hint={"One mesh per 64-sample square of the field instead of one mesh for the whole thing, each with its own bounding sphere so the frustum can reject it, and its own detail level chosen by distance. Automatic turns it on above 257 samples a side — a 513² field and up, where a single draw is half a million triangles that nothing can cull. Below that it is pure overhead: a 129² field is faster as one mesh, because sixteen draw calls cost more than culling a field that is entirely on screen anyway saves. Only the chunks a stroke touches are rebuilt either way."}>
          <Sel value={p.terrain_chunks}
            options={[{ v: "auto" as const, l: "Automatic" }, { v: "on" as const, l: "Always" }, { v: "off" as const, l: "Never" }]}
            onChange={(v) => setPref({ terrain_chunks: v },
              v === "auto" ? "Ground is chunked above 257 samples a side."
                : v === "on" ? "Ground is always chunked." : "Ground is always one mesh.")} />
        </Row>
        <Row label="Scatter as instances" hint={"Scattered trees and rocks drawn as one instanced draw call per asset instead of one cloned sub-tree each. A clone is a whole branch of the scene graph that three walks every frame, which is what capped the preview at 3,000 items; an instance is a 64-byte matrix, and the cap goes to 100,000. Clicking one still names the item it is. Off: clones, and the preview thins out past 3,000 — the field, the saved file and the emitted builder always hold every item either way."}>
          <Check on={p.terrain_instances} label={onOff(p.terrain_instances)}
            onChange={(v) => setPref({ terrain_instances: v },
              v ? "Scattered items are drawn as instances." : "Scattered items are drawn as clones.")} />
        </Row>
        <Row label="Shading">
          <Sel value={p.shading}
            options={[{ v: "wire", l: "Wireframe" }, { v: "solid", l: "Solid" }, { v: "material", l: "Material preview" }, { v: "rendered", l: "Rendered" }]}
            onChange={(v) => setPref({ shading: v }, "The editor opens in " + v + " shading.")} />
        </Row>
        <Row label="Solid lighting" hint="Studio is a three-point rig. Matcap shows form without colour. Flat drops the shading and leaves the colour alone.">
          <Sel value={p.solid_light}
            options={[{ v: "studio", l: "Studio" }, { v: "matcap", l: "Matcap" }, { v: "flat", l: "Flat" }]}
            onChange={(v) => setPref({ solid_light: v }, "Solid lighting: " + v + ".")} />
        </Row>
        <Row label="Solid colour" hint="Material keeps each part's own base colour and colour map. Single paints the whole subject one grey, which is Blender's default and shows form with nothing else competing.">
          <Sel value={p.solid_color}
            options={[{ v: "material", l: "Material" }, { v: "single", l: "Single grey" }]}
            onChange={(v) => setPref({ solid_color: v }, v === "material" ? "Solid keeps each part's own colour." : "Solid paints one grey.")} />
        </Row>
        <Row label="Transform space" hint="Global moves along the world's axes; local along the part's own.">
          <Sel value={p.space} options={[{ v: "global", l: "Global" }, { v: "local", l: "Local" }]}
            onChange={(v) => setPref({ space: v }, "Transforms in " + v + " space.")} />
        </Row>
        <Row label="Skin weights" hint="Envelope is fast and rough. Bone heat follows the surface, the way Blender's automatic weights do.">
          <Sel value={p.weights} options={[{ v: "envelope", l: "Envelope" }, { v: "heat", l: "Bone heat" }]}
            onChange={(v) => setPref({ weights: v }, "Skin weights by " + (v === "heat" ? "bone heat" : "envelope") + ".")} />
        </Row>
        <Row label="Overlays at open">
          <div className="flex flex-wrap gap-x-3 gap-y-1 justify-end max-w-sm">
            <Check on={p.grid} label="Grid" onChange={(v) => setPref({ grid: v }, "Grid " + onOff(v).toLowerCase() + " at open.")} />
            <Check on={p.axes} label="Axes" onChange={(v) => setPref({ axes: v }, "Axes " + onOff(v).toLowerCase() + " at open.")} />
            <Check on={p.outline} label="Selection outline" onChange={(v) => setPref({ outline: v }, "Selection outline " + onOff(v).toLowerCase() + " at open.")} />
            <Check on={p.bones} label="Bones" onChange={(v) => setPref({ bones: v }, "Bones " + onOff(v).toLowerCase() + " at open.")} />
            <Check on={p.extras} label="Light and camera icons" onChange={(v) => setPref({ extras: v }, "Light and camera icons " + onOff(v).toLowerCase() + " at open.")} />
          </div>
        </Row>
        <Row label="Bake size" hint="The texture size of an ambient occlusion, curvature or normal bake.">
          <Sel value={String(p.bake_size)} options={[128, 256, 512, 1024, 2048].map((n) => ({ v: String(n), l: n + " px" }))}
            onChange={(v) => setPref({ bake_size: parseInt(v, 10) }, "Bakes at " + v + " px.")} />
        </Row>
        <Row label="Occlusion rays" hint="More rays give a smoother shade and a slower bake.">
          <Sel value={String(p.bake_rays)} options={[16, 24, 48, 96].map((n) => ({ v: String(n), l: String(n) }))}
            onChange={(v) => setPref({ bake_rays: parseInt(v, 10) }, v + " occlusion rays.")} />
        </Row>
        <Row label="Re-bake delay" hint="After a parameter slider settles, the maps are baked again this long after the last change.">
          <Num value={p.rebake_ms} min={0} max={10000} step={100} unit="ms"
            onChange={(n) => setPref({ rebake_ms: Math.round(n) }, "Re-bake " + Math.round(n) + " ms after the last change.")} />
        </Row>
        <Row label="Undo steps">
          <Num value={p.undo_max} min={1} max={1000} step={10}
            onChange={(n) => setPref({ undo_max: Math.round(n) }, Math.round(n) + " undo steps.")} />
        </Row>
      </Section>

      <Section title="Viewport" desc="The editor's canvas. These apply at once to an open editor.">
        <Row label="Pixel ratio" hint="Device pixels per CSS pixel, at most. Lower is faster on a 4K screen.">
          <Sel value={String(p.pixel_ratio)}
            options={[{ v: "1", l: "1×" }, { v: "1.5", l: "1.5×" }, { v: "2", l: "2× (default)" }, { v: "0", l: "The screen's own" }]}
            onChange={(v) => setPref({ pixel_ratio: parseFloat(v) }, v === "0" ? "Pixel ratio: the screen's own." : "Pixel ratio capped at " + v + "×.")} />
        </Row>
        <Row label="Shadows" hint="A scene that asks for shadows gets them. The studio rig casts none either way.">
          <Check on={p.shadows} label={onOff(p.shadows)} onChange={(v) => setPref({ shadows: v }, "Shadows " + onOff(v).toLowerCase() + ".")} />
        </Row>
        <Row label="Field of view">
          <Num value={p.fov} min={10} max={120} step={1} unit="°" onChange={(n) => setPref({ fov: n }, "Field of view " + n + "°.")} />
        </Row>
        <Row label="Orbit speed">
          <Num value={p.orbit_speed} min={0.1} max={5} step={0.1} unit="×" onChange={(n) => setPref({ orbit_speed: n }, "Orbit speed " + n + "×.")} />
        </Row>
        <Row label="Zoom speed">
          <Num value={p.zoom_speed} min={0.1} max={5} step={0.1} unit="×" onChange={(n) => setPref({ zoom_speed: n }, "Zoom speed " + n + "×.")} />
        </Row>
        <Row label="Invert orbit">
          <Check on={p.invert_orbit} label={p.invert_orbit ? "Inverted" : "Normal"} onChange={(v) => setPref({ invert_orbit: v }, v ? "Orbit inverted." : "Orbit normal.")} />
        </Row>
        <Row label="Mouse" hint="Blender and Godot: middle drag orbits, Shift+middle pans. Unity: Alt+left orbits, middle drag pans, Alt+right zooms. In both, hold the right button to fly with W A S D, and F frames the selection.">
          <Sel value={p.nav_scheme} options={[{ v: "blender", l: "Blender / Godot" }, { v: "unity", l: "Unity" }]}
            onChange={(v) => setPref({ nav_scheme: v }, v === "unity" ? "The mouse works like Unity." : "The mouse works like Blender and Godot.")} />
        </Row>
        <Row label="Wheel zooms toward" hint="The cursor: the wheel goes straight into the thing you point at, so you can get close to one object in a whole level.">
          <Check on={p.zoom_to_cursor} label={p.zoom_to_cursor ? "The cursor" : "The centre"}
            onChange={(v) => setPref({ zoom_to_cursor: v }, v ? "The wheel zooms toward the cursor." : "The wheel zooms toward the centre of the view.")} />
        </Row>
      </Section>

      <Section title="Live game link" desc="Agents question and change the running game through the shared headless browser; the Edit tab mirrors it by name.">
        <Row label="Both switches are at the top of this pane"
          hint="The live link, and whether a project's saved edits are applied to a game as it opens." />
      </Section>

      <Section title="New games" desc="A game started from the Workspaces panel, or by an agent, is Studio-ready from the first minute.">
        <Row label="Engine for new games" hint="The engine a new game starts on when nobody names one.">
          <Sel value={(flat.new_game_engine || "three") as string}
            options={[{ v: "three", l: "three.js" }, { v: "playcanvas", l: "PlayCanvas" }]}
            onChange={(v) => setKey("new_game_engine", v, "New games start on " + (v === "three" ? "three.js" : "PlayCanvas") + ".")} />
        </Row>
        <Row label="Where new games go" hint="A folder. Empty puts a new game beside the project that is open.">
          <input className="input !py-1 text-xs w-72" placeholder="beside the open project"
            defaultValue={(flat.new_game_parent || "") as string}
            key={"ngp-" + String(flat.new_game_parent || "")}
            onBlur={(e) => { const v = e.target.value.trim(); if (v !== (flat.new_game_parent || "")) setKey("new_game_parent", v, v ? "New games go in " + v + "." : "New games go beside the open project."); }} />
        </Row>
        <Row label="Install packages at once" hint="Runs npm install in the background as the game is made, so it can be opened a minute later. Off: the game is made and nothing is downloaded.">
          <Check on={flat.new_game_install !== false} label={onOff(flat.new_game_install !== false)}
            onChange={(v) => setKey("new_game_install", v, v ? "New games install their packages at once." : "New games are made without installing.")} />
        </Row>
      </Section>

      <Section title="Forge and mesh tools"
        desc="The forge renders asset code in a lit studio and hands back numbers. The mesh tools are the library agents import over HTTP."
        right={<a className="text-xs text-brand inline-flex items-center gap-1 hover:underline" href="/forge-ops.md" target="_blank" rel="noreferrer">the catalogue <ExternalLink size={12} /></a>}>
        <Row label="Default quality" hint="Draft is fast; high is for judging craft. A call can still ask for another.">
          <Sel value={p.forge_quality} options={[{ v: "draft", l: "Draft" }, { v: "normal", l: "Normal" }, { v: "high", l: "High" }]}
            onChange={(v) => setPref({ forge_quality: v }, "Forge quality: " + v + ".", true)} />
        </Row>
        <Row label="Framing" hint="Empty space kept around the subject. 1.0 fills the panel edge to edge; a call is widened automatically if it would clip.">
          <Num value={p.forge_margin} min={1} max={2} step={0.02} unit="×"
            onChange={(n) => setPref({ forge_margin: n }, "Forge framing " + n + "x.", true)} />
        </Row>
        <Row label="Default views" hint="The cameras a forge call renders when it names none. At least one stays on. A call can also aim anywhere: az=35,el=12,zoom=2.">
          <div className="flex flex-wrap gap-x-3 gap-y-1 justify-end max-w-sm">
            {FORGE_VIEWS.map((v) => <Check key={v.key} on={p.forge_views.includes(v.key)} label={v.label} onChange={() => toggleView(v.key)} />)}
          </div>
        </Row>
      </Section>

      <Section title="Visual review and the headless browser" desc="Contact sheets of the running game, and the one Chrome that the forge, the live link and the review share.">
        {!!flat.cc_review && (
          <Row label="Tell subagents too" hint="About 700 tokens for every agent fanned out.">
            <Check on={!!flat.cc_review_subagents} label={onOff(!!flat.cc_review_subagents)}
              onChange={(v) => setKey("cc_review_subagents", v, v ? "Subagents are told about visual review too." : "Subagents no longer carry the visual-review note.")} />
          </Row>
        )}
        <Row label="Review quality" hint="Draft is fast; high is for judging craft.">
          <Sel value={(flat.cc_review_quality || "normal") as string}
            options={[{ v: "draft", l: "Draft" }, { v: "normal", l: "Normal" }, { v: "high", l: "High" }]}
            onChange={(v) => setKey("cc_review_quality", v, "Review quality: " + v + ".")} />
        </Row>
        <Row label="Use the GPU" hint="Off renders in software: slower, but it works on a machine with no graphics driver.">
          <Check on={flat.cc_review_gpu !== false} label={onOff(flat.cc_review_gpu !== false)}
            onChange={(v) => setKey("cc_review_gpu", v, v ? "The browser uses the GPU." : "The browser renders in software.")} />
        </Row>
        <Row label="Close the browser after" hint="A browser nobody has used for this long is closed to free memory. The next call starts it again.">
          <Num value={p.browser_idle_min} min={1} max={1440} step={1} unit="min"
            onChange={(n) => setPref({ browser_idle_min: Math.round(n) }, "The browser closes after " + Math.round(n) + " idle minutes.", true)} />
        </Row>
        <Row label="Close a game tab after" hint="A game tab no agent has called for this long is closed, so a finished agent's game stops running and no longer shows as in use. A tab you are watching in the Game tab stays open.">
          <Num value={p.live_tab_idle_min} min={1} max={1440} step={1} unit="min"
            onChange={(n) => setPref({ live_tab_idle_min: Math.round(n) }, "A game tab closes after " + Math.round(n) + " minutes with no agent call.", true)} />
        </Row>
      </Section>

      {!!saved && <div className="text-xs text-ok">{saved}</div>}
    </>
  );
}

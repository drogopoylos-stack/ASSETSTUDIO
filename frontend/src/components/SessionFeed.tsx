import { createContext, Fragment, memo, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Bot, Brain, Check, ChevronDown, ChevronRight, ChevronUp, Clock, Copy, CornerDownRight, FileText, MessageSquarePlus, Network as NetworkIcon, FolderOpen, Globe, HelpCircle, Image as ImageIcon, ListChecks, Loader2, Pencil, Play, RotateCcw, Search, Terminal, Wrench, X } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import { SubAgentCard } from "./SubAgentCard";
import { resultView } from "./feedDetail";
import Prism from "prismjs";
import "prismjs/components/prism-typescript";
import "prismjs/components/prism-jsx";
import "prismjs/components/prism-tsx";
import "prismjs/components/prism-python";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-json";
import "prismjs/components/prism-yaml";
import type { FeedDiffLine, FeedEvent, FeedTodo } from "../types";

// prismjs was already a dependency but never wired up — fenced code blocks rendered as plain
// monospace. Aliases map the fence tag people actually type onto Prism's grammar names.
const LANG_ALIAS: Record<string, string> = {
  ts: "typescript", js: "javascript", py: "python", sh: "bash", shell: "bash",
  bash: "bash", yml: "yaml", yaml: "yaml", json: "json", tsx: "tsx", jsx: "jsx",
  typescript: "typescript", javascript: "javascript", python: "python", html: "markup", xml: "markup",
};
function highlightCode(code: string, lang: string): string | null {
  const g = Prism.languages[LANG_ALIAS[(lang || "").toLowerCase()] || ""];
  try { return g ? Prism.highlight(code, g, lang) : null; } catch { return null; }
}
import { cls, useSetting } from "./ui";
import { feedAgent, sendSettings } from "./sendPrefs";
import { atBottom, haveEverything, nextScrollTop, offerEarlier, widen, NOTHING_ASKED }
  from "./feedScroll";
import { WorkingPulse } from "./WorkingPulse";

const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

// stable empty ref — a fresh [] from a zustand selector loops useSyncExternalStore
const EMPTY_INFLIGHT: { full: string; images: string[] }[] = [];
// keep byte-identical with STEER_PREFIX in cc_session.py — a message sent mid-turn is
// tagged so Claude treats it as steering; we strip the tag from the visible bubble.
const STEER_PREFIX = "↪ Steering update (sent while you were working)";
const BTW_PREFIX = "↪ Side-note (by the way — sent while you work)";   // keep byte-identical with cc_session.py
const isSteer = (t: string) => t.startsWith(STEER_PREFIX) || t.startsWith(BTW_PREFIX);
function stripSteer(t: string): string {
  if (isSteer(t)) { const nl = t.indexOf("\n\n"); t = nl >= 0 ? t.slice(nl + 2) : t; }
  return t.replace(/^\/btw\b[ \t]*/i, "");   // also hide a bare "/btw " the user typed (idle send)
}
const firstLine = (s: string) => (s.split("\n").find((l) => l.trim()) || "").trim().slice(0, 120);

// Has the just-streamed live answer now LANDED as a persisted transcript message? If so we swap
// from the live overlay to the real message in ONE frame — no blink, no content shrink (the shrink
// is what made the answer flash out for ~2s AND jerked the pinned view upward at the end of a turn).
function _answerLanded(eventText: string, live: string): boolean {
  const norm = (s: string) => s.replace(/\s+/g, " ").trim();
  const e = norm(eventText), l = norm(live);
  if (!l) return true;
  if (!e) return false;                       // nothing persisted yet → keep showing the live answer
  if (e === l) return true;
  // the persisted message is the COMPLETED answer; the streamed text is a growing prefix of it,
  // so it has "landed" once the persisted answer starts with (or contains) the streamed text.
  const key = l.length > 60 ? l.slice(0, 60) : l;
  return e.startsWith(key) || e.includes(key);
}

// THE LAST ANSWER FOR EACH CONVERSATION, KEPT IN THE WINDOW.
//
// Switching workspace blanked the feed until the new fetch landed — measured at 430 ms for this
// project, and longer while the backend is busy. The wipe itself is deliberate and stays (see the
// chat-bleed fix below): the previous project's messages must never sit under another project's
// name. Keeping the last answer PER CONVERSATION gives both things at once: the conversation you
// are opening paints immediately from its own last answer, and the fresh response replaces it a
// moment later.
//
// Nothing is merged and nothing is invented: the worst case is that you see, for a few hundred
// milliseconds, exactly what this same conversation showed you last time. If the fetch fails you
// keep that instead of an empty pane, with the error banner over it — which is the honest state.
const lastFeed = new Map<string, FeedEvent[]>();
const LAST_FEED_KEEP = 6;          // a handful of conversations; each is a reference, not a copy

// Live Claude Code conversation timeline (thinking, messages, edits w/ diffs,
// commands, todos, results, questions). Shared by Mission Control + Workspace.
export function SessionFeed({ id, rootPath = "", active, fast, onAnswer, className, session = "", fresh = false, font: fontProp, cliLook, onOpenAgent, notesProjectId = "", folderId = "" }: {
  // `rootPath` is this project's folder on disk. It scopes every file link in the feed to its own
  // project, which is what stops a bare name resolving to a same-named file in another workspace.
  id: string; rootPath?: string; active?: boolean; fast?: boolean; onAnswer?: (t: string) => void; className?: string;
  session?: string; fresh?: boolean; font?: number;
  /** The look of the pane this feed sits in. Pass it only where the pane also carries
   *  data-skin, or the rows would be drawn CLI-style with studio colours. Left out, the feed
   *  follows the studio-wide setting — which is right for Mission Control and the chat tab. */
  cliLook?: boolean;
  /** Show one subagent in a pane of its own. Absent, the card still opens in place. */
  onOpenAgent?: (agentId: string) => void;
  /** The key a diff comment is filed under, and the composer it is sent to. Deliberately NOT
   *  `id`: the feed id can carry an agent prefix ("kimi--…") while the composer beside it is
   *  keyed on the plain project id, and a comment filed under the wrong one goes nowhere.
   *  Left out, the diff shows no comment buttons at all. */
  notesProjectId?: string;
  /** The folder the chat box keeps its model, effort and mode under — the plain project id. An
   *  edit-and-resend must send exactly those. Left out, `id` is the folder. */
  folderId?: string;
}) {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [working, setWorking] = useState(false);
  const [agents, setAgents] = useState(0);
  // the answer Claude is typing RIGHT NOW, streamed live from the session (not the transcript,
  // which only lands when a message COMPLETES) — so you watch it being written, token by token.
  const [liveText, setLiveText] = useState("");
  const [liveWorking, setLiveWorking] = useState(false);
  const [agent, setAgent] = useState("");
  const [agentOut, setAgentOut] = useState("");
  const [companions, setCompanions] = useState<Record<string, { text: string; ts: number; running: boolean }>>({});
  // The live window. It only ever grew: "load earlier" adds 500, the prompt-history jump sets
  // 2000, "full history" sets 8000 — and nothing ever brought it back down, not even switching to
  // another conversation. Measured against this 100 MB transcript: 150 costs 82 KB and 0.04s,
  // 8000 costs 3.5 MB and 0.74s — and that is what every poll paid, every few seconds, for the
  // rest of the page's life. On a loaded machine it stops finishing between ticks, which is the
  // blank feed. Reloading fixed it because `limit` is component state and a reload resets it.
  const FEED_LIMIT = 150;
  // Past this, a load in flight is presumed lost and a new one may take the latch. Kept just
  // above the request's own 30s deadline so the deadline normally does the work, and this only
  // catches a promise that somehow never settles at all.
  const STALE_LOAD_MS = 35000;
  const [limit, setLimit] = useState(FEED_LIMIT);
  // You pressed "full history" or "load earlier". An explicit request is never undone for you.
  const wideOnPurpose = useRef(false);
  // Which way to draw these events. It changes only the DRAWING: the same array feeds both
  // renderers, so switching mid-turn keeps every message and every streamed token where it was.
  const studioCli = useStore((s) => s.skin) === "cli";
  const cli = cliLook ?? studioCli;
  const missionFont = useStore((s) => s.missionFont);
  const font = fontProp ?? missionFont;
  const feedJump = useStore((s) => s.feedJump);
  // messages already fired into the live session but not yet echoed back by the
  // transcript — render them as optimistic "sent/queued" bubbles so you can SEE a
  // 2nd message landed. Dedupe against real user events so they don't double up.
  const inflight = useStore((s) => s.inflight[id] || EMPTY_INFLIGHT);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const programmatic = useRef(false);   // true while WE auto-scroll, so onScroll doesn't mistake it for the user
  const lastUp = useRef(0);             // last time the user wheeled UP — blocks snap-back re-engagement
  const restore = useRef<number | null>(null);
  // HOW FAR YOU ARE FROM THE BOTTOM, while you are reading back. Held across every content
  // change, so an answer being written below cannot move the line you are on. Distance from the
  // BOTTOM rather than the top, because that is the edge new content arrives at — and it is the
  // same measure "load earlier" already used, so prepending a page of history holds too.
  const anchor = useRef<number | null>(null);
  const rowEls = useRef<Record<string, HTMLDivElement | null>>({});
  const [hlKey, setHlKey] = useState<string | null>(null);
  const handledJump = useRef<number>(-1);
  const rewinding = useRef(false);   // suppress feed reloads while a rewind truncates + restarts
  // One transcript load at a time, and whatever it returns is applied. See the loader below.
  const convKey = useRef("");
  const inFlight = useRef(false);
  const inFlightAt = useRef(0);      // when it started — a stalled load must be takeable over
  const loadGen = useRef(0);         // which load owns the latch; a late one must not free it
  const again = useRef(false);
  const workingRef = useRef(false);
  const liveWorkingRef = useRef(false);
  const [feedErr, setFeedErr] = useState("");
  // "Nothing here" and "nothing YET" are different answers, and printing the first while the
  // second is true is what made a slow load read as a wiped conversation.
  const [loaded, setLoaded] = useState(false);
  // The last load came back SHORT of what it asked for, so there is nothing earlier to fetch.
  // This replaces comparing the line count against the current `limit`: a response that arrives
  // after the limit has already grown made that comparison hide the button it should have kept.
  const [gotAll, setGotAll] = useState(false);
  // The widest window asked for so far and the most it returned. "Is there more?" is answered by
  // asking for more and seeing whether more arrives — never by comparing a count against the
  // number requested, which is a budget over transcript records and not a promise.
  const widest = useRef({ ...NOTHING_ASKED });
  const loadRef = useRef<() => void>(() => {});   // lets the live-stream poll pull the transcript NOW
  const prevLiveWorking = useRef(false);          // detect the working→done edge of a turn
  const handoffTimer = useRef<number | null>(null);
  const burstUntil = useRef(0);                   // send-burst window: poll the live state HOT until this ts
  const pokeLive = useRef<() => void>(() => {});  // forces the live loop to run an iteration right now
  const toast = useStore((s) => s.toast);
  // live state PUSHED over WS as Claude streams — zero polling latency. While pushes are
  // fresh the HTTP live poll below stands down entirely (it stays as the fallback).
  const ccPush = useStore((s) => s.ccLive[id]);
  const wsAt = useRef(0);                         // when the last WS push for this project landed
  const prevHadText = useRef(false);              // detect a mid-turn message completing (text resets)

  // Apply a live-state snapshot (same shape from WS push or HTTP poll) to the overlay.
  // GOLDEN RULE: never blank the streamed answer to "" while a turn is live. The overlay stays
  // visible (sticky) until its persisted twin renders and _answerLanded() hides it in the SAME
  // frame — otherwise the answer vanishes for a beat and then pops back in whole (the "lag").
  function applyLive(s: any) {
    if (s.working) {
      if (handoffTimer.current) { window.clearTimeout(handoffTimer.current); handoffTimer.current = null; }
      if (s.text) {
        setLiveText(s.text);                 // only ever SET a non-empty value while streaming
      } else if (prevHadText.current) {
        // a message just completed mid-turn (backend cleared its live text): keep the streamed
        // text on screen and pull the transcript so the saved copy lands and the overlay swaps
        // to it seamlessly — do NOT blank here (that was the disappear/reappear flicker)
        loadRef.current();
      }
      // Back to the cheap window when a new turn starts and you are watching the bottom — but
      // ONLY if the window grew on its own. The jump-to-an-old-prompt path widens it to 2000
      // behind your back, and that is fair game to undo.
      //
      // "Full history" and "load earlier" are NOT. Pressing one and having the next turn quietly
      // throw the history away is the app overruling a thing you asked for, which is worse than
      // the cost it was avoiding — and the cost turned out not to be the real problem anyway.
      // The blank feed was the rail poll saturating the connection pool, fixed at its source; a
      // full-history load measures 0.49s here, not the seconds this was guarding against.
      if (!prevLiveWorking.current && stick.current && !wideOnPurpose.current)
        setLimit((l) => (l > FEED_LIMIT ? FEED_LIMIT : l));
      setLiveWorking(true);
    } else {
      // Turn ended: KEEP the streamed answer visible and pull the transcript NOW so the persisted
      // message replaces it in one frame — no blink, no upward scroll jump. The render hides the
      // overlay the instant the message lands; this timer is only a safety net.
      if (prevLiveWorking.current) {
        loadRef.current();
        if (handoffTimer.current) window.clearTimeout(handoffTimer.current);
        handoffTimer.current = window.setTimeout(() => setLiveText(""), 5000);
      }
      setLiveWorking(false);
    }
    prevLiveWorking.current = !!s.working;
    prevHadText.current = !!s.text;
  }
  const applyLiveRef = useRef(applyLive);
  applyLiveRef.current = applyLive;

  // WS push → apply instantly (the moment Claude types, not a poll-interval later)
  useEffect(() => {
    if (!id || fresh || !ccPush) return;
    wsAt.current = ccPush.at;
    applyLiveRef.current(ccPush.state);
  }, [ccPush, id, fresh]);

  // edit a past prompt & restart from there (like editing a message on the web): truncate
  // the conversation at that message and resume from the edited text. Optionally roll files back.
  async function rewindFrom(uuid: string, text: string, restoreFiles: boolean) {
    if (!uuid) { toast("This message can't be rewound (no id yet — try again in a moment)", "danger"); return; }
    // What the chat box in THIS folder sends, for the engine this conversation runs on. This read
    // the agent-wide keys - the last choice made in ANY folder - so a folder pinned to Opus 5 was
    // resent on Opus 5.5, and the session restarted on it (sendPrefs.ts).
    const folder = folderId || id;
    const { model, effort, permission_mode: mode, fork, thinking } = sendSettings(folder, feedAgent(id, folder));
    stick.current = true;
    rewinding.current = true;   // hold reloads so the poll can't flash the old (full) transcript
    setEvents((evs) => { const i = evs.findIndex((x) => x.kind === "user" && x.id === uuid); return i >= 0 ? evs.slice(0, i) : evs; });
    setWorking(true);
    try {
      const r = await api.sessionRewind(id, { uuid, message: text, model, permission_mode: mode, fork, effort, thinking, session, path: rootPath, restore_files: restoreFiles });
      if (!r.ok) { toast(r.error || "rewind failed", "danger"); setWorking(false); rewinding.current = false; return; }
      const rev = r.reverted_files;
      toast(rev && rev.restored != null ? `Rewound · reverted ${rev.restored} file(s)` : "Rewound — restarting from your edit", "ok");
    } catch (e: any) { toast(e.message || "rewind failed", "danger"); setWorking(false); rewinding.current = false; return; }
    rewinding.current = false;   // success → resume polling, which now loads the truncated convo + new turn
  }

  // jump to a prompt picked in the Prompt History panel. ONE-SHOT per click: once we
  // land on the target we record the nonce and stop, so later feed polls don't keep
  // yanking the view back (which would fight the user scrolling away). If the prompt
  // isn't loaded yet we pull more history and retry on the next events update.
  useEffect(() => {
    if (!feedJump || feedJump.id !== id) return;
    if (handledJump.current === feedJump.nonce) return; // already landed — leave scroll alone
    stick.current = false;                              // don't auto-stick to bottom while seeking
    const k = feedJump.key;                             // already prefixed (u:prompt / a:answer)
    const el = rowEls.current[k];
    if (el) {
      handledJump.current = feedJump.nonce;
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      setHlKey(k);
      const t = window.setTimeout(() => setHlKey(null), 2200);
      return () => window.clearTimeout(t);
    }
    if (limit < 2000) setLimit(2000);  // older prompt not loaded — fetch more, retry on update
  }, [feedJump, events, id, limit]);

  // CHAT-BLEED FIX: switching project/session must wipe the old conversation IMMEDIATELY.
  // The loader below is async and swallows errors, so without this the previous workspace's
  // messages stay rendered until the new fetch resolves — and if that fetch fails, or the
  // project has no transcript yet, they stay forever. That is the "chat leaking into other
  // workspaces" bug. Keyed ONLY on id/session: the loader effect re-runs on working/limit/…
  // too, and clearing there would blank the feed mid-turn.
  useEffect(() => {
    // Seeded from THIS conversation's own last answer when there is one (see `lastFeed` above),
    // empty otherwise. Either way the previous project's messages are gone in the same frame.
    setEvents(lastFeed.get(`${id}|${session}`) || []);
    setWorking(false); setAgents(0); setAgentOut(""); setCompanions({}); setLiveText("");
  }, [id, session]);

  // THE WINDOW IS RESET WHEN THE CONVERSATION CHANGES — AND ONLY THEN.
  //
  // This used to live at the top of the loader effect below, which has `limit` among its own
  // dependencies. So pressing "load earlier" set the limit to 650, the effect re-ran because the
  // limit had changed, and its first line put the limit straight back to 150. The window could
  // never grow, the same 150 lines came back, and the button appeared to do nothing — which is
  // exactly what it did.
  //
  // Keyed on the conversation, the intention is kept ("a new conversation must not inherit the
  // last one's window") without the effect undoing the thing that triggered it.
  useEffect(() => {
    setLimit(FEED_LIMIT);
    wideOnPurpose.current = false;   // ...nor inherit the request that widened it
    widest.current = { ...NOTHING_ASKED };
    setGotAll(false);
  }, [id, session, fresh]);

  useEffect(() => {
    convKey.current = `${id}|${session}`;
    setLoaded(false);
    if (!id || fresh) { setEvents([]); setWorking(false); setAgents(0); setAgentOut(""); setCompanions({}); return; }
    // THE BLANK-FEED BUG.
    //
    // This used to close over an `alive` flag and drop any response that landed after the effect
    // re-ran. The deps included `working` and `liveWorking`, which flip repeatedly DURING a turn —
    // so every re-run invalidated whatever request was in flight. That was harmless while the
    // fetch was quick. It stopped being harmless once this transcript reached 105 MB: a poll now
    // takes up to 5s and returns 1.2 MB, so the turn state reliably flipped before the response
    // arrived, every response was thrown away, and `events` — cleared by the effect above on the
    // way in — stayed empty. A blank conversation, with a healthy backend, until a reload.
    //
    // Two changes. A response is now discarded only if a NEWER request has started or the
    // conversation itself changed, never because an unrelated dependency moved. And the turn
    // state is read from refs rather than deps, so a live turn no longer rebuilds this effect at
    // all — the cadence still follows it, through the tick below.
    inFlight.current = false;
    inFlightAt.current = 0;
    again.current = false;
    const load = (): void => {
      // ONE AT A TIME, and the answer always lands.
      //
      // `applyLive` pokes this every time a message completes mid-turn — which, in a turn with
      // many tool calls, is constantly. Each poke used to start another request, and a guard that
      // preferred the newest meant the previous one was thrown away. With a 105 MB transcript a
      // poll takes up to 5s, so the next poke always arrived first and NOTHING was ever applied.
      // Starving the feed like that is what emptied it.
      //
      // So a poke during a load does not start a second request; it asks for one more when this
      // one finishes. The response is applied on arrival, however long it took, and is only
      // discarded if the CONVERSATION itself changed underneath it.
      // ...AND NEVER FOREVER. This latch is what turned a slow moment into a dead conversation.
      //
      // `fetch` had no timeout, so a request queued behind a saturated connection pool never
      // settled — and `.finally()` is the only thing that reopens the latch. One such request
      // left `inFlight` shut for the life of the page: every later poll returned here at the
      // first line, the feed kept the empty array it was cleared to on the way in, and the only
      // cure was Ctrl+R, which builds fresh refs. That is the bug the user hit, in every pane at
      // once, on a machine doing nothing.
      //
      // Two guarantees now. A load older than the request's own deadline is abandoned rather
      // than waited on. And only the load that currently OWNS the latch may release it, so one
      // returning late cannot open the door on a newer one still inside.
      if (inFlight.current && Date.now() - inFlightAt.current < STALE_LOAD_MS) {
        again.current = true;
        return;
      }
      const mine = ++loadGen.current;
      inFlight.current = true;
      inFlightAt.current = Date.now();
      const key = `${id}|${session}`;
      // The limit THIS request is asking for, captured. `limit` may have grown by the time the
      // answer lands, and comparing the two is how the feed decided whether there is more.
      const asked = limit;
      api.missionFeed(id, asked, session).then((r) => {
        // Superseded: a newer window owns the feed now, and this answer describes the old one.
        if (mine !== loadGen.current) return;
        if (key !== convKey.current || rewinding.current) return;
        const lines = r.lines || [];
        setEvents(lines);
        if (lines.length) {         // remembered so that coming back to this chat paints at once
          lastFeed.delete(key);
          lastFeed.set(key, lines);
          while (lastFeed.size > LAST_FEED_KEEP) lastFeed.delete(lastFeed.keys().next().value as string);
        }
        setGotAll(haveEverything(widest.current, asked, lines.length));
        widest.current = widen(widest.current, asked, lines.length);
        setWorking(!!r.working);
        setAgents(r.agents_active || 0);
        setAgent(r.agent || "");
        // non-claude agents (Codex/Gemini/…) stream raw stdout — show it inline,
        // correctly attributed to whichever agent actually last ran in this project.
        setAgentOut((r.agent_log || "").trim());
        setCompanions(r.companions || {});
        setFeedErr("");
        setLoaded(true);
      }).catch((e: any) => {
        // Never silent. A swallowed error left an empty feed that looked like an empty
        // conversation, which is the one thing it must not be confused with.
        if (key === convKey.current) setFeedErr(e?.message || "could not load the conversation");
      }).finally(() => {
        if (mine !== loadGen.current) return;   // superseded: a newer load holds the latch now
        inFlight.current = false;
        const wanted = again.current;
        again.current = false;
        // Only chase the queued reload if it is still THIS conversation being asked about.
        if (wanted && key === convKey.current) load();
      });
    };
    loadRef.current = load;
    load();
    // One steady tick; the cadence is decided per tick from refs, so `working`/`liveWorking`
    // changing no longer tears this effect down and kills the request it had in flight.
    // liveWorking counts too: the in-memory stream flags a turn ~seconds before the transcript's
    // own `working` flips, so without it the first tool events of a fresh turn poll at idle cadence.
    let last = Date.now();
    const iv = window.setInterval(() => {
      if (document.hidden) return;
      const hotMs = useStore.getState().wsConnected ? 900 : 300;
      // A wide window costs more per poll, so it polls slower — but now that "full history"
      // STAYS open across turns, 8s of it while a turn runs reads as a frozen feed. Measured:
      // one full-history load is 0.49s here, so 3s while working is affordable, and the loader
      // cannot stack requests anyway. Idle stays cheap.
      const delay = limit > 800 ? ((workingRef.current || liveWorkingRef.current) ? 3000 : 8000)
        : (workingRef.current || liveWorkingRef.current) ? hotMs
        : fast ? 900 : active ? 2500 : 12000;
      if (Date.now() - last < delay) return;
      last = Date.now();
      load();
    }, 250);
    const onVis = () => { if (!document.hidden) { last = 0; load(); } };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(iv);
      document.removeEventListener("visibilitychange", onVis);
      // THIS EFFECT IS BEING REPLACED — the conversation changed, or you pressed "load earlier".
      //
      // Retire its generation and open the latch. Without this, a request this closure left in
      // flight came back, found it still owned the latch, released it and re-ran ITS OWN `load`
      // with the limit it had captured — so "load earlier" fetched the same 150 lines again, and
      // could keep doing it, swallowing the new window's polls into `again` each time.
      loadGen.current++;
      inFlight.current = false;
      again.current = false;
    };
  }, [id, active, fast, limit, session, fresh]);

  // The cadence reads these; they must never be effect dependencies (see above).
  workingRef.current = working;
  liveWorkingRef.current = liveWorking;

  // Every run of finished look/run tool calls folds into ONE dim line, the way the console does
  // it. Grouped here rather than inside the row so a fold keeps its open/closed state across
  // re-renders — and singles fold too, because "Read 1 file" is exactly the line the console
  // prints and the reason it stays readable through a long turn.
  const cliRows = useMemo<{ e?: FeedEvent; run?: FeedEvent[]; i: number }[]>(() => {
    const out: { e?: FeedEvent; run?: FeedEvent[]; i: number }[] = [];
    if (!cli) return events.map((e, i) => ({ e, i }));
    let run: FeedEvent[] = [];
    let at = 0;
    const flush = () => {
      if (!run.length) return;
      out.push({ run, i: at });
      run = [];
    };
    events.forEach((e, i) => {
      if (isFoldableTool(e)) { if (!run.length) at = i; run.push(e); return; }
      flush();
      out.push({ e, i });
    });
    flush();
    return out;
  }, [events, cli]);

  // Live stream poll: the visible answer being written THIS instant + whether a turn is live.
  // Reads the in-memory session (not the transcript) so the text appears as Claude types it,
  // and keeps polling while idle so a new turn's first words show up the moment they start.
  useEffect(() => {
    if (!id || fresh) { setLiveText(""); setLiveWorking(false); prevLiveWorking.current = false; return; }
    let alive = true;
    let timer = 0;
    let gen = 0;   // a poke() supersedes any in-flight iteration so two chains never run at once
    const loop = async () => {
      const g = ++gen;
      // hidden window → no HTTP at all; WS pushes still land via the store when they matter
      if (document.hidden) { timer = window.setTimeout(loop, 1500); return; }
      // WS is streaming this session right now → stand down (the push effect renders it with
      // zero latency); this loop silently takes over again the moment pushes stop coming.
      if (Date.now() - wsAt.current < 2500) { timer = window.setTimeout(loop, 1200); return; }
      const s = await api.liveState(id).catch(() => null);
      if (!alive || g !== gen) return;
      if (s) applyLiveRef.current(s);
      // cadence: writing → 5 fps; just sent (burst window) → hot, so the first words of the answer
      // appear the instant they exist; idle → snappy enough that a turn started elsewhere shows fast
      // (relaxed when WS is connected, since a new turn announces itself with a pushed event).
      const hot = Date.now() < burstUntil.current;
      timer = window.setTimeout(loop, s && s.working ? 200 : hot ? 150 : useStore.getState().wsConnected ? 2000 : 700);
    };
    pokeLive.current = () => { window.clearTimeout(timer); loop(); };
    loop();
    return () => { alive = false; gen++; window.clearTimeout(timer); if (handoffTimer.current) window.clearTimeout(handoffTimer.current); };
  }, [id, fresh]);

  // The instant a message is fired from the composer, go HOT: pull the transcript now (your bubble
  // lands immediately) and burst-poll the live stream so Claude's first tokens show with no lag.
  useEffect(() => {
    if (!inflight.length || fresh) return;
    burstUntil.current = Date.now() + 4000;
    loadRef.current();
    pokeLive.current();
  }, [inflight.length, fresh]);

  // a freshly-opened conversation (switched workspace/session) starts pinned to the bottom
  useEffect(() => {
    stick.current = true;
    anchor.current = null;
    restore.current = null;
    setGotAll(false);
  }, [id, session, fresh]);

  // WHERE THE VIEW GOES WHEN THE CONTENT CHANGES. Three cases, in order.
  //
  // A layout effect, not an effect: it runs before the browser paints, so the correction is
  // never visible. As a plain effect the old position was painted for one frame first, which is
  // the flick you saw when an answer landed.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const want = nextScrollTop({
      scrollHeight: el.scrollHeight,
      restore: restore.current,
      stick: stick.current,
      anchor: anchor.current,
    });
    if (want == null) return;                 // nothing to say: leave the view exactly as it is
    // Asking for earlier history hands the anchor over, so the position keeps being held after
    // the page of history has landed.
    if (restore.current != null) { anchor.current = restore.current; restore.current = null; }
    programmatic.current = true;
    el.scrollTop = want;
    if (stick.current) {
      // Following the bottom needs three goes: now, after layout, and after late content — an
      // image or a code block that measures itself only once it has painted.
      const pin = () => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; };
      requestAnimationFrame(pin);
      window.setTimeout(() => { pin(); programmatic.current = false; }, 120);
    } else {
      window.setTimeout(() => { programmatic.current = false; }, 0);
    }
  }, [events, agentOut, working, inflight, liveText]);

  function loadEarlier() {
    const el = ref.current;
    restore.current = el ? el.scrollHeight - el.scrollTop : 0;
    wideOnPurpose.current = true;   // you asked for it; nothing may take it back
    setLimit((l) => Math.min(l + 500, 8000));
  }
  function loadAll() {
    const el = ref.current;
    restore.current = el ? el.scrollHeight - el.scrollTop : 0;
    wideOnPurpose.current = true;
    setLimit(8000); // the studio keeps the whole transcript — pull the full conversation
  }
  // Whether there is more to fetch is decided by what the LAST response actually returned
  // against what it asked for — never by the current `limit`, which may already have grown.
  const mightHaveMore = offerEarlier(gotAll, limit, 8000);

  // optimistic bubbles: inflight messages whose text hasn't shown up in the
  // transcript yet (compare on first line, with any steering tag stripped).
  const landed = new Set(events.filter((e) => e.kind === "user").map((e) => firstLine(stripSteer(e.text || ""))));
  const pending = inflight.filter((q) => !landed.has(firstLine(stripSteer(q.full))));
  const nothing = events.length === 0 && pending.length === 0 && !agentOut && !working;

  // Keep the streamed answer visible until its persisted transcript message replaces it (same
  // frame), so the answer never blinks out and the pinned view never jumps up at turn-end.
  let lastAnswer = "";
  for (let i = events.length - 1; i >= 0; i--) { const e = events[i]; if (e.kind === "text" && e.text) { lastAnswer = e.text; break; } }
  // Show the streamed overlay whenever we have live text that hasn't landed in the transcript yet.
  // (Dropping the old `liveWorking ||` is what lets a completed mid-turn message hand off to its
  // persisted copy WITHOUT a blank gap, while a still-streaming next message shows normally since
  // it isn't a prefix of the last saved answer.)
  const showLive = !!liveText && !_answerLanded(lastAnswer, liveText);
  // stable object — a fresh literal each render would re-render every FileLink in the feed
  const feedCtx = useMemo(() => ({ id, path: rootPath, notes: notesProjectId }), [id, rootPath, notesProjectId]);

  if (!id)
    return <div className="flex-1 flex items-center justify-center text-muted/60 text-sm">No Claude Code session for this folder yet.</div>;

  return (
    <FeedProject.Provider value={feedCtx}>
    <div
      ref={ref}
      // Wheel fires ONLY for real user input (never our auto-scroll), so an upward wheel
      // reliably means "I'm reading back — stop following" even mid-stream when the scroll
      // event would be swallowed by our own auto-pin. This is what keeps the view where you
      // left it while a new answer is still growing below.
      onWheel={(e) => {
        if (e.deltaY < 0) {
          stick.current = false;
          lastUp.current = Date.now();
          // Take the anchor NOW, on the input itself, so the very next content change already
          // has somewhere to hold. Waiting for the scroll event loses the first arrival.
          const el = ref.current;
          if (el) anchor.current = el.scrollHeight - el.scrollTop;
        }
      }}
      onScroll={(e) => {
        if (programmatic.current) return;   // ignore our own auto-scroll; only the user unsticks the view
        const el = e.currentTarget;
        const bottom = atBottom(el.scrollHeight, el.scrollTop, el.clientHeight);
        if (!bottom) {
          stick.current = false;
          // However you got here — wheel, scrollbar drag, Page Up, a find — this is the place
          // to hold. Keyboard and scrollbar scrolling fire no wheel event, so this is the only
          // hook that sees them.
          anchor.current = el.scrollHeight - el.scrollTop;
          return;
        }
        // re-engage auto-follow only once you've settled at the bottom (not mid up-scroll),
        // so a small wheel-up near the bottom doesn't immediately snap you back down
        if (Date.now() - lastUp.current > 500) { stick.current = true; anchor.current = null; }
      }}
      // translateZ(0) keeps the scroller on one stable GPU layer so text AA doesn't
      // flip subpixel↔grayscale mid-scroll (the "letters get darker" flicker on a
      // monitor with a custom DPI scale); backfaceVisibility hardens it further.
      data-feed-scroll
      // `overflowAnchor: none` because the position is held by hand above. The browser's own
      // scroll anchoring picks its own element and fights the correction, which reads as a jitter
      // when both move the view in the same frame.
      style={{ fontSize: font, lineHeight: 1.5, transform: "translateZ(0)", backfaceVisibility: "hidden",
               overflowAnchor: "none" }}
      className={cls("flex-1 min-h-0 overflow-y-auto bg-bg", cli ? "px-3 py-2" : "p-2", className)}
    >
      {mightHaveMore && (
        // break out of the container's padding so the strip sits flush under the header bar,
        // full-width, with the two controls merged into one seamless bar (no gaps)
        <div className={cls("sticky -top-2 z-10 -mt-2 mb-2 flex border-b border-line bg-panel", cli ? "-mx-3" : "-mx-2")}>
          {/* THE COUNT, ON THE BUTTON. The view is deliberately held where it was when history
              arrives above it, so a press that worked looked exactly like a press that did not —
              which is how a dead button went unnoticed twice. This number moves. */}
          <button onClick={loadEarlier}
            className="flex-1 py-1 text-[10px] text-muted hover:text-text hover:bg-panel2/70 transition-colors">
            ↑ load earlier{events.length ? ` · ${events.length.toLocaleString()} shown` : ""}</button>
          <button onClick={loadAll} title="Load the entire conversation (kept on disk — VS Code can't show this)"
            className="px-3 py-1 text-[10px] text-muted hover:text-text hover:bg-panel2/70 border-l border-line transition-colors">full history</button>
        </div>
      )}
      {/* A load that failed must say so. An empty feed and a failed fetch look identical, and
          confusing the two is what made this read as "the conversation vanished". */}
      {feedErr && !fresh && (
        <div className="my-1 flex items-center gap-2 text-[11px] text-warn">
          <span className="truncate">Could not load the conversation — {feedErr}</span>
          <button className="underline decoration-dotted hover:text-text shrink-0"
            onClick={() => { setFeedErr(""); loadRef.current(); }}>retry</button>
        </div>
      )}
      {(fresh && pending.length === 0) || nothing ? (
        cli ? (
          <CliWelcome path={rootPath} fresh={fresh && pending.length === 0} loading={!loaded && !feedErr} />
        ) : fresh && pending.length === 0 ? (
          <div className="text-muted/60 flex items-center gap-2 text-sm py-2"><Terminal size={13} /> New conversation — send a message below to start.</div>
        ) : (
          <div className="text-muted/50 flex items-center gap-1"><Terminal size={12} /> no recent activity</div>
        )
      ) : (
        <div className="relative">
          {!cli && <div className="absolute left-[4px] top-1 bottom-1 w-px bg-line/70" />}
          {cliRows.map(({ e, run, i }) => {
            if (run) return <div key={i}><CliToolRun run={run} /></div>;
            const ev = e as FeedEvent;
            const k = ev.kind === "user" ? `u:${(ev.text || "").slice(0, 120)}`
              : ev.kind === "text" ? `a:${(ev.text || "").slice(0, 120)}` : "";
            return (
              <div key={i} ref={k ? (el) => { rowEls.current[k] = el; } : undefined}
                className={cls(k && hlKey === k && "rounded-md ring-2 ring-brand/70 bg-brand/5 transition-all")}>
                {cli ? <CliRow e={ev} onAnswer={onAnswer} onRewind={rewindFrom} onOpenAgent={onOpenAgent} />
                     : <EventRow e={ev} onAnswer={onAnswer} onRewind={rewindFrom} onOpenAgent={onOpenAgent} />}
              </div>
            );
          })}
          {/* your just-sent messages, confirmed in the chat before Claude echoes them */}
          {pending.map((q, i) => (
            cli
              ? <CliPendingRow key={`p:${i}`} text={firstLine(stripSteer(q.full))} queued={i > 0 || working} />
              : <PendingRow key={`p:${i}`} text={firstLine(stripSteer(q.full))} queued={i > 0 || working} />
          ))}
        </div>
      )}
      {agentOut && (
        <div className="mt-2 rounded-lg border border-accent/40 bg-accent/5 overflow-hidden">
          <div className="px-2.5 py-1.5 text-[0.82em] text-accent font-semibold uppercase tracking-wide flex items-center gap-1.5 border-b border-accent/20 bg-accent/10">
            <Bot size={13} /> {agent || "agent"}
            {working ? (
              <span className="flex items-center gap-1 normal-case font-normal text-accent/80">
                <Loader2 size={11} className="animate-spin" /> working live…
              </span>
            ) : (
              <span className="normal-case font-normal text-muted">· finished</span>
            )}
          </div>
          <pre className="px-2.5 py-2 max-h-[60vh] overflow-auto whitespace-pre-wrap break-words text-text/85 font-mono text-[0.92em] leading-relaxed">{agentOut}</pre>
        </div>
      )}
      {/* co-agents (dual-agent mode): each one's review of Claude's turn, in the same chat */}
      {Object.entries(companions).map(([cid, c]) => (c && c.text ? (
        <div key={cid} className="mt-2 rounded-md border border-accent/40 bg-accent/5">
          <div className="px-2 py-1 text-[0.82em] text-accent font-semibold uppercase tracking-wide flex items-center gap-1">
            <Bot size={12} /> {cid} · co-agent {c.running && <Loader2 size={11} className="animate-spin" />}
          </div>
          <pre className="px-2 pb-2 overflow-x-auto whitespace-pre-wrap break-words text-text/85 font-mono">{c.text}</pre>
        </div>
      ) : null))}
      {/* the answer being written THIS moment — streamed live, before it lands in the transcript.
          (Thinking stays hidden: the CLI redacts it; you still see its live token count below.) */}
      {showLive && (cli ? (
        <div className="flex gap-[1ch] mt-1" title={liveWorking ? "Answer being written live" : "Answer"}>
          <span className="shrink-0 select-none text-brand">{CLI_BULLET}</span>
          {/* the same orange rail as a landed answer, so the handoff from streaming to persisted
              changes nothing you can see */}
          <div className="flex-1 min-w-0 text-text/90 border-l-2 border-brand/70 pl-2">
            <StreamingMarkdown text={liveText} />
            {liveWorking && <span className="inline-block w-[0.55em] h-[1.05em] bg-text/80 ml-0.5 align-text-bottom animate-pulse" />}
          </div>
        </div>
      ) : (
        <div className="relative pl-5 py-[3px]">
          <span className={cls("absolute left-0 top-[7px] h-2.5 w-2.5 rounded-full ring-2 ring-bg bg-brand", liveWorking && "animate-pulse")} />
          <div className="border-l-2 border-brand/60 pl-2 text-text/90" title={liveWorking ? "Answer being written live" : "Answer"}>
            <StreamingMarkdown text={liveText} />
            {liveWorking && <span className="inline-block w-[3px] h-[1.05em] bg-brand/80 ml-0.5 align-text-bottom animate-pulse" />}
          </div>
        </div>
      ))}
      {(working || liveWorking || agents >= 1) && (
        <div className={cls("mt-1.5 flex items-center gap-[1ch] flex-wrap", cli ? "" : "pl-1")}>
          {/* the CLI prints this star while it works; WorkingPulse's own dot is hidden under
              the skin (see index.css) so there is one spinner, not two */}
          {cli && <span className="text-brand select-none animate-pulse">{CLI_STAR}</span>}
          {/* projectId → real-time activity + live tokens (ticks DURING thinking) + elapsed + agents */}
          <WorkingPulse working={working || liveWorking} projectId={id} />
        </div>
      )}
    </div>
    </FeedProject.Provider>
  );
}

// A "watch live" command Claude mentions (e.g. `/workflows`). You can't type slash-commands in
// the Studio like a terminal, so clicking this scrolls the feed to the work running now — where
// the live tokens, the answer being written, and the active-agent count all update in real time.
function WatchChip({ label }: { label: string }) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        const sc = e.currentTarget.closest("[data-feed-scroll]") as HTMLElement | null;
        sc?.scrollTo({ top: sc.scrollHeight, behavior: "smooth" });
      }}
      title="Watch live — jump to the work running now (live tokens, the answer being written, and active agents update below)"
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand/15 hover:bg-brand/25 ring-1 ring-brand/30 text-brand font-mono text-[0.9em] align-baseline transition-colors"
    >
      <span className="relative flex h-1.5 w-1.5 shrink-0">
        <span className="absolute inline-flex h-full w-full rounded-full bg-brand opacity-60 animate-ping" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-brand" />
      </span>
      {label}
    </button>
  );
}

const URL_RE = /(https?:\/\/[^\s<>")]+)/g;

// A URL in prose swallows trailing sentence punctuation ("open http://127.0.0.1:3888." → the "."
// lands inside the href and it won't load). Peel trailing .,;:!?'… and unbalanced ] } back out as
// plain text. (`) " >` already terminate URL_RE, so in practice this fixes the trailing dot/comma.)
function peelUrl(u: string): [string, string] {
  let end = u.length;
  while (end > 0 && ".,;:!?'’…".includes(u[end - 1])) end--;
  for (const [open, close] of [["[", "]"], ["{", "}"]] as const) {
    while (end > 0 && u[end - 1] === close &&
      u.slice(0, end).split(open).length <= u.slice(0, end).split(close).length) end--;
  }
  return [u.slice(0, end), u.slice(end)];
}

// A clickable URL that keeps trailing sentence punctuation OUT of the link (rendered as text after).
// `label` is set for a markdown [text](url), where the visible text is not the address.
function UrlAnchor({ url, label }: { url: string; label?: string }) {
  const [href, tail] = label ? [url, ""] : peelUrl(url);
  return (
    <>
      <a href={href} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
        className="text-brand underline decoration-brand/40 hover:decoration-brand break-all"
        title={label ? href : undefined}>{label || href}</a>
      {tail}
    </>
  );
}

// which project this feed belongs to — file links resolve relative paths against it
// Which project this feed belongs to. The ROOT PATH matters as much as the id: a chat link is
// often a bare name ("final.png"), and without the owning project the backend resolved it against
// whichever open project came first and had that name — showing an unrelated image under a
// correct label. Menu actions already scoped themselves this way; now the thumbnail does too.
const FeedProject = createContext<{ id: string; path: string; notes: string }>(
  { id: "", path: "", notes: "" });

/** The Edit/Write card's diff, with the file and the comment key taken from the feed's own
 *  context. A card is drawn deep inside the feed and passing two more props down every
 *  level of it would be two more places to forget one. */
function FeedDiff({ e }: { e: FeedEvent }) {
  const { notes } = useContext(FeedProject);
  if (!e.diff || !e.diff.hunks || e.diff.hunks.length === 0) return null;
  return <DiffView hunks={e.diff.hunks} total={e.diff.added + e.diff.removed}
                   file={e.subtitle || ""} projectId={notes} />;
}

// path-looking tokens with a known file extension (".studio-uploads/x.png", "src/App.tsx",
// "C:\proj\file.md", bare "package.json") become clickable → open in the Workspace editor
const FILE_EXTS = "png|jpe?g|gif|webp|svg|ico|bmp|glb|gltf|fbx|obj|stl|blend|mp4|webm|mp3|wav|ogg|md|json|jsonl|tsx|ts|jsx|js|mjs|cjs|py|css|scss|html?|txt|ya?ml|toml|ps1|bat|sh|csv|sql|gd|cs|cpp|hpp|glsl|vue|svelte|zip";

// A folder name with a space is normal on Windows ("brainrot 3d game research crazygames"), but a
// space cannot simply be added to the segment class: scanning prose, it would swallow the words
// around a filename, and a Bash subtitle like `npm run build && cp a.js b.js` would become one
// giant fake link. So a space is only allowed when the path is ANCHORED — it starts with a drive
// letter or ./ — which is exactly the case that was failing.
const SEG_STRICT = String.raw`[\w.\-()&]`;                 // no spaces: safe anywhere in prose
const SEG_LOOSE = String.raw`[^\\/:*?"<>|\r\n]`;           // spaces allowed, anchored paths only
const ANCHOR = String.raw`(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])`;
// The final segment is LAZY so the match stops at the FIRST extension instead of running on to a
// later one in the same sentence ("C:\a\b file.png and more.png" is one path, not one blob).
const PATH_ANCHORED = String.raw`${ANCHOR}(?:${SEG_LOOSE}*[\\/])*${SEG_LOOSE}*?\.(?:${FILE_EXTS})\b`;
const PATH_PLAIN = String.raw`${ANCHOR}?(?:${SEG_STRICT}+[\\/])*${SEG_STRICT}+\.(?:${FILE_EXTS})\b`;
const FILE_RE = new RegExp(`${PATH_ANCHORED}|${PATH_PLAIN}`, "g");
const FILE_ONE = new RegExp(`^(?:${PATH_ANCHORED}|${PATH_PLAIN})$`);

// extensions "Run" knows how to launch (keep in sync with RUNNABLE_EXTS in workspace.py). HTML is
// served on a local http server + opened (so ES-module imports work); scripts run in a console.
const RUNNABLE = new Set(["bat", "cmd", "ps1", "py", "pyw", "exe", "msi", "com", "lnk", "html", "htm", "sh"]);
const runLabel = (ext: string) =>
  ext === "html" || ext === "htm" ? "Run (serve & open in browser)"
    : ext === "ps1" || ext === "py" || ext === "pyw" || ext === "sh" ? "Run in a console"
      : "Run";

function FileLink({ path }: { path: string }) {
  const { id: pid, path: projectPath } = useContext(FeedProject);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const ext = (path.split(/[\\/]/).pop() || "").split(".").pop()?.toLowerCase() || "";
  const runnable = RUNNABLE.has(ext);
  const isImage = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif", "tiff", "tif"].includes(ext);

  // relative paths (".studio-uploads/x.png") → absolute, resolved against this feed's project root
  // The path AS WRITTEN, plus the project it was written in. Joining it onto the root here was
  // wrong: a bare `final.png` that lives in a subfolder became `<root>/final.png`, which does not
  // exist, so "open" and "show in folder" failed or fell back to a whole-disk name search and
  // found another project's copy. The backend already resolves a name inside a given project
  // correctly — it just needed to be told which project.
  function target(): { path: string; root: string } {
    const p = path.replace(/\\/g, "/");
    if (/^([A-Za-z]:\/|\/)/.test(p)) return { path: p, root: "" };   // absolute: nothing to resolve
    return { path, root: projectPath || "" };
  }
  const toast = (m: string, k?: "ok" | "danger") => useStore.getState().toast(m, k);
  const openIt = () => { setMenu(null); useStore.getState().openFileInWorkspace(path, pid || undefined); };
  const openImageIt = async () => {
    setMenu(null);
    // OS default viewer via the backend — works for images ANYWHERE, incl. renders/outputs that
    // live outside the project (the in-editor path is browse-root-bound and rejects those).
    try { const t = target(); await api.wsOpen(t.path, t.root); }
    catch (e: any) { toast(`Open failed: ${e.message}`, "danger"); }
  };
  const revealIt = async () => {
    setMenu(null);
    try { const t = target(); await api.wsReveal(t.path, t.root); }
    catch (e: any) { toast(`Show in folder failed: ${e.message}`, "danger"); }
  };
  const runIt = async () => {
    setMenu(null);
    try {
      const r = await api.wsRun(target().path);
      if (r.url) toast(`Serving → ${r.url}`, "ok");
      else toast(`Running ${path.split(/[\\/]/).pop()}…`, "ok");
    } catch (e: any) { toast(`Run failed: ${e.message}`, "danger"); }
  };
  const copyIt = async (v: string) => {
    setMenu(null);
    try { await navigator.clipboard.writeText(v); toast("Path copied", "ok"); } catch { /* ignore */ }
  };
  const item = "w-full text-left px-2 py-1.5 rounded text-xs flex items-center gap-2 hover:bg-panel2 text-text/90";

  return (
    <>
      <button
        onClick={(ev) => { ev.stopPropagation(); (isImage ? openImageIt() : openIt()); }}
        onContextMenu={(ev) => { ev.preventDefault(); ev.stopPropagation(); setMenu({ x: ev.clientX, y: ev.clientY }); }}
        title={isImage
          ? `${path} · left-click: open image · right-click: show in folder / copy`
          : runnable
            ? `${path} · left-click: open in editor · right-click: run / show in folder / copy`
            : `Open ${path} in the editor · right-click for more (show in folder, copy path)`}
        className="text-accent underline decoration-accent/40 hover:decoration-accent break-all font-mono text-[0.95em] align-baseline">
        {path}
      </button>
      {/* Inline thumbnail: seeing a generated render beats reading its filename. /raw resolves the
          same way the links do, so bare names and out-of-project outputs display too; a path that
          can't be resolved just hides itself instead of showing a broken-image icon. */}
      {isImage && (
        <img src={`/api/workspace/raw?path=${encodeURIComponent(path)}`
          + (projectPath ? `&root=${encodeURIComponent(projectPath)}` : "")} alt={path} loading="lazy"
          title="Click to open full size"
          onClick={(ev) => { ev.stopPropagation(); openImageIt(); }}
          onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.display = "none"; }}
          className="block mt-1 mb-0.5 max-h-44 max-w-full rounded border border-line cursor-zoom-in hover:border-accent/60" />
      )}
      {menu && createPortal(
        // Portal to <body> so the fixed menu escapes the feed's `translateZ(0)` container
        // (a transformed ancestor makes `position:fixed` resolve against IT, not the viewport —
        // which put the menu off-screen right and spawned a horizontal scrollbar).
        <>
          <div className="fixed inset-0 z-[1200]" onClick={() => setMenu(null)}
            onContextMenu={(e) => { e.preventDefault(); setMenu(null); }} />
          <div className="fixed z-[1210] card p-1 w-60 shadow-card"
            style={{ left: Math.min(menu.x, window.innerWidth - 250), top: Math.min(menu.y, window.innerHeight - 230) }}>
            <div className="px-2 py-1 text-[10px] text-muted/70 font-mono truncate border-b border-line mb-1" title={path}>{path}</div>
            {runnable && (
              <button className={cls(item, "text-ok font-medium")} onClick={runIt}>
                <Play size={13} /> {runLabel(ext)}
              </button>
            )}
            {isImage
              ? <button className={cls(item, "text-ok font-medium")} onClick={openImageIt}><ImageIcon size={13} /> Open image</button>
              : <button className={item} onClick={openIt}><FileText size={13} /> Open in editor</button>}
            <button className={item} onClick={revealIt}><FolderOpen size={13} /> Show in folder (Explorer)</button>
            <button className={item} onClick={async () => { const t = target(); copyIt(t.root ? await api.wsResolve(t.path, t.root).then((r) => r.path).catch(() => t.path) : t.path); }}><Copy size={13} /> Copy full path</button>
            <button className={item} onClick={() => copyIt(path)}><Copy size={13} /> Copy as written</button>
          </div>
        </>,
        document.body,
      )}
    </>
  );
}

// "Node.js" / "three.js" in prose are library names, not files — don't linkify bare mentions
const LIB_NOT_FILE = /^(node|three|pdf|vue|next|nuxt|p5|d3|chart|fabric|konva|babylon|matter|howler|tone|socket\.io|angular|react|ember|backbone)\.js$/i;

// split plain text into [string | <FileLink/>] nodes; `prefix` keeps keys unique in the parent array
function fileLinkNodes(text: string, prefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0, j = 0;
  FILE_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = FILE_RE.exec(text)) !== null) {
    const tok = m[0];
    if (!/[\\/]/.test(tok) && LIB_NOT_FILE.test(tok)) continue;   // plain library name in prose
    if (m.index > last) nodes.push(text.slice(last, m.index));
    nodes.push(<FileLink key={`${prefix}-${j++}`} path={tok} />);
    last = FILE_RE.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function Linkify({ text }: { text: string }) {
  const parts = (text || "").split(URL_RE);
  return (
    <>
      {parts.map((p, i) =>
        /^https?:\/\//.test(p) ? (
          <UrlAnchor key={i} url={p} />
        ) : (
          <span key={i}>{fileLinkNodes(p, `f${i}`)}</span>
        )
      )}
    </>
  );
}

function parseInline(text: string): React.ReactNode[] {
  const re = /(`[^`]+`)|(\[[^\]\n]+\]\((?:https?:\/\/|mailto:)[^\s)]+\))|(\*\*[^*]+?\*\*)|(https?:\/\/[^\s<>")]+)|(\*[^*\n]+?\*|_[^_\n]+?_)/g;
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(...fileLinkNodes(text.slice(last, m.index), `p${k++}`));
    const tok = m[0];
    if (m[1]) {
      const inner = tok.slice(1, -1);
      // a backticked "watch live" command (e.g. `/workflows`) becomes a real clickable chip that
      // jumps to the running work below — since in the Studio you can't type it like in a terminal.
      if (/^\/(workflows?|agents)\b/i.test(inner.trim()))
        nodes.push(<WatchChip key={k++} label={inner} />);
      else if (FILE_ONE.test(inner.trim()))
        // a backticked file path (`.studio-uploads/x.png`) opens right in the editor
        nodes.push(<code key={k++} className="px-1 py-0.5 rounded bg-panel2 font-mono text-[0.92em]"><FileLink path={inner.trim()} /></code>);
      else
        nodes.push(<code key={k++} className="px-1 py-0.5 rounded bg-panel2 text-accent font-mono text-[0.92em]">{inner}</code>);
    }
    else if (m[2]) {
      // [text](url) — the visible text is the label, not the address. This is what makes a
      // link inside a TABLE cell clickable: cells go through parseInline, and until now the
      // only thing it recognised was a bare URL, so a markdown link showed as its brackets.
      const cut = tok.indexOf("](");
      nodes.push(<UrlAnchor key={k++} url={tok.slice(cut + 2, -1)} label={tok.slice(1, cut)} />);
    }
    else if (m[3]) nodes.push(<strong key={k++} className="font-semibold text-text">{parseInline(tok.slice(2, -2))}</strong>);
    else if (m[4]) nodes.push(<UrlAnchor key={k++} url={tok} />);
    else if (m[5]) nodes.push(<em key={k++}>{tok.slice(1, -1)}</em>);
    last = re.lastIndex;
  }
  if (last < text.length) nodes.push(...fileLinkNodes(text.slice(last), `p${k++}`));
  return nodes;
}

// Block-level markdown. Hand-rolled (no runtime dependency) but real: fenced code, GFM
// tables, indent-aware nested lists, blockquotes and rules — so an answer's structure
// survives into the feed instead of collapsing into flat lines. Inline spans (code, bold,
// links, file paths) still go through parseInline().
function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const html = highlightCode(code, lang);
  return (
    <div className="my-1 rounded-md border border-line overflow-hidden group/code">
      <div className="flex items-center gap-2 px-2 py-0.5 bg-panel2 border-b border-line">
        <span className="text-[10px] uppercase tracking-wide text-muted">{lang}</span>
        <button className="ml-auto text-[10px] text-muted hover:text-text opacity-0 group-hover/code:opacity-100 transition-opacity"
          title="Copy this block"
          onClick={(e) => {
            e.stopPropagation();
            navigator.clipboard?.writeText(code).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); });
          }}>{copied ? "copied ✓" : "copy"}</button>
      </div>
      <pre className="px-2 py-1.5 overflow-x-auto font-mono text-[0.9em] leading-snug text-text/90">
        {html ? <code dangerouslySetInnerHTML={{ __html: html }} /> : <code>{code}</code>}
      </pre>
    </div>
  );
}

const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const cells = (s: string) => s.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
type MdAlign = "left" | "center" | "right";
const alignOf = (s: string): MdAlign =>
  /^\s*:-+:\s*$/.test(s) ? "center" : /^\s*-+:\s*$/.test(s) ? "right" : "left";

const Markdown = memo(function Markdown({ text }: { text: string }) {
  const src = (text || "").split("\n");
  const out: React.ReactNode[] = [];
  let i = 0, k = 0;
  while (i < src.length) {
    const line = src[i];

    // ``` fenced code ```
    const fence = line.match(/^\s*```+\s*([\w+#.-]*)\s*$/);
    if (fence) {
      const lang = fence[1] || "";
      const body: string[] = [];
      i++;
      while (i < src.length && !/^\s*```+\s*$/.test(src[i])) body.push(src[i++]);
      i++;
      out.push(<CodeBlock key={k++} lang={lang} code={body.join("\n")} />);
      continue;
    }

    // GFM table: header row immediately followed by a |---|:--:| separator
    if (line.includes("|") && line.trim() !== "" && i + 1 < src.length && TABLE_SEP.test(src[i + 1])) {
      const head = cells(line);
      const al = cells(src[i + 1]).map(alignOf);
      i += 2;
      const rows: string[][] = [];
      while (i < src.length && src[i].includes("|") && src[i].trim() !== "") rows.push(cells(src[i++]));
      out.push(
        <div key={k++} className="my-1.5 overflow-x-auto">
          <table className="text-[0.95em] border-collapse">
            <thead><tr className="bg-panel2">
              {head.map((h, c) => (
                <th key={c} style={{ textAlign: al[c] || "left" }}
                  className="border border-line px-2 py-1 font-semibold text-text">{parseInline(h)}</th>))}
            </tr></thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className={ri % 2 ? "bg-panel2/40" : ""}>
                  {head.map((_, c) => (
                    <td key={c} style={{ textAlign: al[c] || "left" }}
                      className="border border-line px-2 py-1 align-top">{parseInline(r[c] ?? "")}</td>))}
                </tr>))}
            </tbody>
          </table>
        </div>);
      continue;
    }

    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { out.push(<div key={k++} className="my-1.5 border-t border-line" />); i++; continue; }

    const head = line.match(/^(#{1,6})\s+(.*)/);
    if (head) {
      out.push(<div key={k++} className={cls("font-bold text-text", head[1].length <= 2 ? "text-[1.08em] mt-2" : "mt-1")}>{parseInline(head[2])}</div>);
      i++; continue;
    }

    const quote = line.match(/^\s*>\s?(.*)/);
    if (quote) {
      out.push(<div key={k++} className="border-l-2 border-accent/50 pl-2 my-0.5 text-text/80">{parseInline(quote[1])}</div>);
      i++; continue;
    }

    // lists — indentation drives nesting depth
    const b = line.match(/^(\s*)[-*+]\s+(.*)/);
    const n = line.match(/^(\s*)(\d+)[.)]\s+(.*)/);
    if (b || n) {
      const depth = Math.min(4, Math.floor((b ? b[1] : n![1]).replace(/\t/g, "  ").length / 2));
      out.push(
        <div key={k++} className="flex gap-1.5" style={{ paddingLeft: depth * 14 }}>
          <span className={cls("select-none shrink-0 text-accent", !b && "font-mono")}>{b ? (depth % 2 ? "◦" : "•") : `${n![2]}.`}</span>
          <span className="flex-1 min-w-0">{parseInline(b ? b[2] : n![3])}</span>
        </div>);
      i++; continue;
    }

    if (line.trim() === "") { out.push(<div key={k++} className="h-1" />); i++; continue; }
    out.push(<div key={k++}>{parseInline(line)}</div>);
    i++;
  }
  return <div className="space-y-0.5">{out}</div>;
});

function Expandable({ text, max = 240, maxLines = 0, className }:
  { text: string; max?: number; maxLines?: number; className?: string }) {
  const [open, setOpen] = useState(false);
  const full = text || "";
  // Clamp by LINES as well as characters: a long Bash/Read result cut at N characters lands
  // mid-line and reads as garbage, where "first N lines" stays legible.
  let cut = full.length > max ? full.slice(0, max).trimEnd() : full;
  if (maxLines > 0) {
    const ls = cut.split("\n");
    if (ls.length > maxLines) cut = ls.slice(0, maxLines).join("\n");
  }
  const long = cut.length < full.length;
  const shown = open || !long ? full : cut + "…";
  return (
    <span className={cls("whitespace-pre-wrap break-words", className)}>
      <Linkify text={shown} />
      {long && (
        <button className="text-[0.82em] text-muted hover:text-text ml-1 align-baseline"
          onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}>{open ? "less" : "more"}</button>
      )}
    </span>
  );
}

function kindColor(e: FeedEvent): string {
  if (e.kind === "question") return "rgb(var(--c-warn))";
  if (e.kind === "thinking") return "rgb(var(--c-accent))";
  if (e.kind === "graph") return "rgb(var(--c-ok))";
  if (e.kind === "user") return "rgb(var(--c-warn))";
  if (e.kind === "result") return e.ok === false ? "rgb(var(--c-danger))" : "rgb(var(--c-muted))";
  if (e.kind === "tool")
    return e.icon === "edit" ? "rgb(var(--c-ok))" : e.icon === "terminal" ? "rgb(var(--c-warn))" : "rgb(var(--c-accent))";
  return "rgb(var(--c-brand))";
}

function toolIcon(icon?: string) {
  return icon === "edit" ? Pencil : icon === "terminal" ? Terminal : icon === "search" ? Search
    : icon === "check" ? ListChecks : icon === "bot" ? Bot : icon === "globe" ? Globe : Wrench;
}

// Your prompt — hover to reveal a ✎ that opens an inline editor. Submitting rewinds the
// conversation to this message and restarts from the edited text (everything below is removed).
function UserEvent({ e, onRewind, cli }: { e: FeedEvent; onRewind?: (uuid: string, text: string, restoreFiles: boolean) => void; cli?: boolean }) {
  const steered = !!e.steer || isSteer(e.text || "");
  const clean = stripSteer(e.text || "");   // hide the steering tag from the visible bubble
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(clean);
  const [revert, setRevert] = useState(false);
  const canEdit = !!e.id && !!onRewind;
  const cancel = () => { setEditing(false); setDraft(clean); setRevert(false); };
  const submit = () => { const t = draft.trim(); if (!t || !e.id) return; onRewind?.(e.id, t, revert); setEditing(false); setRevert(false); };

  if (editing)
    return (
      <div className="rounded-md border border-warn/60 bg-warn/[0.07] px-2 py-1.5 my-0.5">
        <div className="flex items-center gap-1 mb-1 text-[10px] text-warn/90 font-medium uppercase tracking-wide">
          <Pencil size={10} /> edit &amp; retry from here
        </div>
        <textarea autoFocus value={draft} onChange={(ev) => setDraft(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); submit(); }
            else if (ev.key === "Escape") { ev.preventDefault(); cancel(); }
          }}
          className="w-full resize-y min-h-[56px] rounded bg-bg border border-line px-2 py-1 text-sm text-text/90 focus:border-warn/60 outline-none font-sans" />
        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
          <button onClick={submit} disabled={!draft.trim()}
            className="inline-flex items-center gap-1 rounded bg-warn/90 hover:bg-warn text-bg px-2 py-1 text-xs font-medium disabled:opacity-50">
            <RotateCcw size={12} /> Retry from here</button>
          <button onClick={cancel} className="inline-flex items-center gap-1 rounded border border-line hover:bg-panel2 text-muted px-2 py-1 text-xs">
            <X size={12} /> Cancel</button>
          <label className="flex items-center gap-1 text-[11px] text-muted cursor-pointer ml-auto"
            title="Also roll the project's files back to how they were when you first sent this message">
            <input type="checkbox" checked={revert} onChange={(ev) => setRevert(ev.target.checked)} /> also undo file changes
          </label>
        </div>
        <p className="text-[10px] text-muted/60 mt-1 leading-snug">Removes every message below this one and restarts from your edit. Ctrl/⌘+Enter to submit · Esc to cancel · the old transcript is backed up first.</p>
      </div>
    );

  // The console prints your prompt on a bare "> " line, with a blank line above it. Editing
  // still works — the ✎ opens the very same editor, so a rewind is a rewind in either look.
  if (cli)
    return (
      <div className="group flex gap-[1ch] mt-3 mb-1" title={`your prompt · ${e.ts}`}>
        <span className="text-warn/80 shrink-0 select-none">&gt;</span>
        {/* Light yellow for what YOU said, terracotta for what came back. The Studio look leans on
            the same pair of rails to separate the two at a glance, and a console loses nothing by
            keeping them — it is one column of colour, not a card. */}
        <div className="flex-1 min-w-0 text-text/75 border-l-2 border-warn/50 pl-2">
          {steered && (
            <div className="text-accent/80 text-[0.85em]"
              title="You sent this while I was working — I picked it up as a correction/refinement">
              [steering · folded into the work]
            </div>
          )}
          <Markdown text={clean} />
        </div>
        {canEdit && (
          <button onClick={() => { setDraft(clean); setEditing(true); }}
            className="opacity-0 group-hover:opacity-100 shrink-0 self-start text-muted/70 hover:text-warn transition-opacity"
            title="Edit & retry from here — removes everything below and restarts from your edit">
            <Pencil size={12} />
          </button>
        )}
      </div>
    );

  return (
    <div className="group rounded-md border border-warn/45 bg-warn/[0.06] px-2 py-1 my-0.5 flex gap-1.5">
      <span className="text-warn font-bold shrink-0" title="Your prompt">›</span>
      <div className="flex-1 min-w-0 text-text/90">
        {steered && (
          <span className="inline-flex items-center gap-1 text-[10px] text-accent/90 mb-0.5"
            title="You sent this while I was working — I picked it up as a correction/refinement">
            <CornerDownRight size={10} /> steering · folded into the work
          </span>
        )}
        <Markdown text={clean} />
      </div>
      {canEdit && (
        <button onClick={() => { setDraft(clean); setEditing(true); }}
          className="opacity-0 group-hover:opacity-100 shrink-0 self-start mt-0.5 text-muted/70 hover:text-warn transition-opacity"
          title="Edit & retry from here — removes everything below and restarts from your edit">
          <Pencil size={12} />
        </button>
      )}
    </div>
  );
}

// Your message after you hit send, before the transcript echoes it back — so a 2nd
// (queued) message is visibly confirmed in the chat. Replaced by the real bubble on arrival.
function PendingRow({ text, queued }: { text: string; queued: boolean }) {
  return (
    <div className="relative pl-5 py-[3px]">
      <span className="absolute left-0 top-[7px] h-2.5 w-2.5 rounded-full ring-2 ring-bg bg-warn/50" />
      <div className="rounded-md border border-warn/25 bg-warn/[0.04] px-2 py-1 my-0.5 flex gap-1.5 items-center">
        <span className="text-warn/60 font-bold shrink-0" title="Your prompt">›</span>
        <div className="flex-1 min-w-0 text-text/70 truncate">{text}</div>
        <span className="shrink-0 inline-flex items-center gap-1 text-[10px] text-muted/70">
          <Loader2 size={10} className="animate-spin" />
          {queued ? "queued · I'll pick this up next" : "sending…"}
        </span>
      </div>
    </div>
  );
}

// Skip re-rendering a row whose content is unchanged — the feed replaces the WHOLE events array
// every ~300ms poll while Claude works, which was re-parsing every message's markdown each time.
// diff/todos/options are fresh objects per poll so those rows still update; the heavy text/answer
// rows (the bulk) are skipped. Callbacks are behaviourally stable within a session, so ignore them.
function sameEvent(a: { e: FeedEvent }, b: { e: FeedEvent }) {
  const x = a.e, y = b.e;
  return x.kind === y.kind && x.ts === y.ts && x.text === y.text && x.tokens === y.tokens
    && x.ok === y.ok && x.id === y.id && x.tool === y.tool && x.title === y.title
    && x.subtitle === y.subtitle && x.command === y.command && x.icon === y.icon
    && x.diff === y.diff && x.todos === y.todos && x.options === y.options
    // A running subagent's card changes without any other field moving — its tokens tick up.
    && x.agent === y.agent;
}

/** Render a list of feed lines with the SAME rows the live feed uses.
 *
 *  Exported so a subagent's own timeline is drawn by this code and not by a second, lesser copy
 *  of it — its diffs, its commands and its thinking then look exactly like the parent's, because
 *  they are the same component. */
export function FeedLines({ lines, cli, className }: {
  lines: FeedEvent[]; cli?: boolean; className?: string;
}) {
  return (
    <div className={cls(cli ? "font-mono text-[13px] px-3 py-2" : "px-3 py-2 space-y-0.5", className)}>
      {lines.map((e, i) => (cli
        ? <CliRow key={i} e={e} />
        : <EventRow key={i} e={e} />))}
      {!lines.length && <div className="text-xs text-muted/60 py-6 text-center">nothing recorded yet</div>}
    </div>
  );
}

const EventRow = memo(function EventRow({ e, onAnswer, onRewind, onOpenAgent }: { e: FeedEvent; onAnswer?: (t: string) => void; onRewind?: (uuid: string, text: string, restoreFiles: boolean) => void; onOpenAgent?: (id: string) => void }) {
  // A subagent gets the full width of the row. It is not one line of output; it is a whole
  // second conversation, with a bill.
  if (e.kind === "agent" && e.agent) {
    return (
      <div className="relative pl-5 py-1">
        <span className="absolute left-0 top-[10px] h-2.5 w-2.5 rounded-full ring-2 ring-bg"
          style={{ background: e.agent.running ? "rgb(var(--c-brand))" : "rgb(var(--c-accent))" }} />
        <Ts ts={e.ts} />
        <SubAgentCard agent={e.agent} onOpen={onOpenAgent} />
      </div>
    );
  }
  return (
    <div className="relative pl-5 py-[3px]">
      <span className="absolute left-0 top-[7px] h-2.5 w-2.5 rounded-full ring-2 ring-bg" style={{ background: kindColor(e) }} />
      <Ts ts={e.ts} />
      <EventBody e={e} onAnswer={onAnswer} onRewind={onRewind} />
    </div>
  );
}, sameEvent);

/* ═══════════════════════════════════════════════════════════════════════════════
   THE CLI LOOK

   The console marks, in the console's own meaning:
     ⏺  one action — a message Claude wrote, or a tool it called
     ⎿  what that action produced, folded under it
     ✻  Claude is thinking, or working
     >  your prompt

   These components read the SAME FeedEvent list the Studio rows read and share the
   same Markdown, FileLink, DiffView, TodoView and ResultBlock underneath, so a code
   block, a file link and a diff behave identically in both looks. Nothing here
   fetches, sends or stores anything: the look is drawing, not plumbing.
   ═════════════════════════════════════════════════════════════════════════════ */
// U+25CF BLACK CIRCLE, and it must NOT be U+23FA.
//
// The console's own bullet is U+23FA, and copying it here was a mistake a terminal hides. This
// skin's stack is "JetBrains Mono", Consolas, "Cascadia Mono", "Courier New" — and NONE of them
// contains U+23FA. Chrome therefore fell out of the stack entirely and resolved it from Segoe UI
// Emoji, which draws a colour bitmap. A colour bitmap ignores `color`, so every bullet came out
// the same blue whatever kindColor() asked for — edit, shell, search and answer all identical —
// and oversized, because an emoji glyph is not built to a monospace advance.
//
// U+25CF is in the mono fonts themselves, so there is no fallback: it takes the colour it is
// given and the width it should. The corner and star below already resolve to text glyphs, which
// is why only the bullet was wrong.
const CLI_BULLET = "●";   // U+25CF — in the mono fonts, so no emoji fallback
const CLI_CORNER = "⎿";   // U+23BF
const CLI_STAR = "✻";     // U+273B

/** A tool call: `⏺ Edit(src/App.tsx)`, with its diff / todos / command under a ⎿. */
function CliTool({ e }: { e: FeedEvent }) {
  const [showCmd, setShowCmd] = useState(false);
  const sub = (e.subtitle || "").trim();
  // The console puts the COMMAND in the parentheses for a shell call, which is the line you
  // actually want to read; the Studio's plain-English description follows it, dimmed.
  const cmdHead = (e.command || "").split("\n")[0].trim();
  const paren = cmdHead || sub;
  const tail = cmdHead && sub ? sub : "";
  // A one-line command is already printed in full inside the parentheses, so there is nothing
  // left to unfold — and a ⎿ with nothing under it reads as output that failed to arrive.
  const moreCmd = !!e.command && e.command.trim() !== cmdHead;
  const hasFold = moreCmd || !!e.diff?.hunks?.length || !!e.todos?.length;
  return (
    <div className="mt-1" title={e.ts}>
      <div className="flex gap-[1ch]">
        <span className="shrink-0 select-none" style={{ color: kindColor(e) }}>{CLI_BULLET}</span>
        <span className="flex-1 min-w-0">
          <span className="text-text">{e.title}</span>
          {paren && (
            <span className="text-muted break-all" title={paren}>
              ({!cmdHead && FILE_ONE.test(paren) ? <FileLink path={paren} /> : paren})
            </span>
          )}
          {tail && <span className="text-muted/60"> — {tail}</span>}
          {/* nowrap: these tags broke mid-bracket at the end of a long command line */}
          {e.bg && (
            <span className="text-accent/80 ml-[1ch] whitespace-nowrap" title="Started in the background — it keeps running past this line">
              [background]
            </span>
          )}
          {!e.bg && e.slow ? (
            <span className="text-muted/60 ml-[1ch] whitespace-nowrap" title={`Allowed up to ${e.slow} minutes — this one is expected to take a while`}>
              [up to {e.slow}m]
            </span>
          ) : null}
          {e.diff && (e.diff.added > 0 || e.diff.removed > 0) && (
            <span className="ml-[1ch] text-[0.9em]">
              {e.diff.added > 0 && <span className="text-ok">+{e.diff.added}</span>}
              {e.diff.removed > 0 && <span className="text-danger ml-1">−{e.diff.removed}</span>}
            </span>
          )}
        </span>
      </div>
      {hasFold && (
        <div className="flex gap-[1ch] mt-0.5">
          <span className="shrink-0 select-none text-muted/50 pl-[2ch]">{CLI_CORNER}</span>
          <div className="flex-1 min-w-0">
            {moreCmd && (showCmd ? (
              <>
                <pre className="whitespace-pre-wrap break-words text-text/80">
                  <span className="text-ok select-none">$ </span><Linkify text={e.command || ""} />
                </pre>
                <button onClick={(ev) => { ev.stopPropagation(); setShowCmd(false); }}
                  className="text-[0.85em] text-muted/70 hover:text-text">hide the full command</button>
              </>
            ) : (
              <button onClick={(ev) => { ev.stopPropagation(); setShowCmd(true); }} title={e.command}
                className="text-[0.85em] text-muted/70 hover:text-text">show the full command</button>
            ))}
            <FeedDiff e={e} />
            {e.todos && e.todos.length > 0 && <TodoView todos={e.todos} />}
          </div>
        </div>
      )}
    </div>
  );
}

// A tool call that only LOOKED or RAN, and carries nothing you would lose by folding it.
//
// The console's rule, read off a real transcript: everything between two answers that merely
// read or ran collapses into one dim line — "Read 1 file, ran 1 shell command" — while anything
// that CHANGED something keeps its own bullet, its ⎿ and its preview. So `edit` is excluded, and
// so is anything carrying a diff, a todo list, a [background] tag or a long-runner allowance,
// because each of those says something the summary line cannot.
function isFoldableTool(e: FeedEvent): boolean {
  if (e.kind !== "tool" || e.bg || e.slow) return false;
  if (e.diff?.hunks?.length || e.todos?.length) return false;
  return e.icon === "terminal" || e.icon === "search" || e.icon === "tool";
}

/** Which bucket a tool call is counted in, and how that bucket is said. */
function foldKey(e: FeedEvent): string {
  const t = e.tool || "";
  if (t === "Bash" || t === "PowerShell") return "shell";
  if (t === "Read" || t === "NotebookRead") return "read";
  if (t === "Grep" || t === "Glob") return "search";
  if (t === "WebFetch" || t === "WebSearch") return "web";
  return "tool";
}

function foldPhrase(key: string, n: number): string {
  const s = n === 1 ? "" : "s";
  if (key === "shell") return `ran ${n} shell command${s}`;
  if (key === "read") return `read ${n} file${s}`;
  if (key === "search") return `searched ${n} time${s}`;
  if (key === "web") return `fetched ${n} page${s}`;
  return `ran ${n} tool call${s}`;
}

/** "Read 1 file, ran 2 shell commands" — counted per kind, in the order they happened. */
function foldSummary(run: FeedEvent[]): string {
  const order: string[] = [];
  const n: Record<string, number> = {};
  for (const e of run) {
    const k = foldKey(e);
    if (!(k in n)) { n[k] = 0; order.push(k); }
    n[k] += 1;
  }
  const s = order.map((k) => foldPhrase(k, n[k])).join(", ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Finished tool calls, folded the way the console folds them. */
function CliToolRun({ run }: { run: FeedEvent[] }) {
  const [open, setOpen] = useState(false);
  if (open) return <>{run.map((e, i) => <CliTool key={i} e={e} />)}</>;
  // Dim, at the text column, no bullet — a turn with fourteen greps in it was fourteen loud
  // lines and is one quiet one. Nothing is lost: the line opens back into the real calls, and
  // whatever is running RIGHT NOW is shown by the live overlay above the composer anyway.
  return (
    <div className="mt-1 pl-[2ch]">
      <button onClick={(ev) => { ev.stopPropagation(); setOpen(true); }}
        className="text-muted/70 hover:text-text text-left"
        title="Show each call">
        {foldSummary(run)}
      </button>
    </div>
  );
}

/** A message you sent that the transcript has not echoed back yet. */
function CliPendingRow({ text, queued }: { text: string; queued: boolean }) {
  return (
    <div className="flex gap-[1ch] mt-3 mb-1 text-muted/70">
      <span className="shrink-0 select-none">&gt;</span>
      <span className="flex-1 min-w-0 truncate">{text}</span>
      <span className="shrink-0 text-[0.85em] text-muted/60">
        {queued ? "queued · picked up next" : "sending…"}
      </span>
    </div>
  );
}

/** What the console prints when a conversation has nothing in it yet. */
function CliWelcome({ path, fresh, loading }: { path: string; fresh: boolean; loading?: boolean }) {
  return (
    <div className="py-1">
      <div className="cli-prompt-box inline-block border border-brand/40 px-4 py-2 leading-relaxed">
        <div><span className="text-brand">{CLI_STAR}</span> <span className="text-text">Welcome to Claude Code</span></div>
        <div className="text-muted mt-2">type below to start · @ for files · / for commands</div>
        {path && <div className="text-muted mt-1">cwd: <span className="text-text/80 break-all">{path}</span></div>}
      </div>
      <div className="mt-2 text-muted/60">
        {fresh ? "New conversation — send a message below to start."
          : loading ? "loading the conversation…" : "no recent activity"}
      </div>
    </div>
  );
}

const CliRow = memo(function CliRow({ e, onAnswer, onRewind, onOpenAgent }: { e: FeedEvent; onAnswer?: (t: string) => void; onRewind?: (uuid: string, text: string, restoreFiles: boolean) => void; onOpenAgent?: (id: string) => void }) {
  if (e.kind === "user") return <UserEvent e={e} onRewind={onRewind} cli />;
  if (e.kind === "result") return <ResultBlock text={e.text || ""} ok={e.ok} cli />;
  // A delegated task keeps its card in the console look too. Its numbers are the point, and a
  // console line cannot hold a token breakdown and a list of files without becoming a wall.
  if (e.kind === "agent" && e.agent)
    return <div className="mt-1 pl-[2ch]"><SubAgentCard agent={e.agent} cli onOpen={onOpenAgent} /></div>;
  if (e.kind === "turn") return <div className="pl-[2ch]"><TurnBar e={e} /></div>;
  if (e.kind === "tool") return <CliTool e={e} />;
  // A question needs real buttons to answer it, so it keeps its card — squared and
  // recoloured by the skin. Losing the buttons would cost you the answer, not a look.
  if (e.kind === "question") return <div className="mt-1 pl-[2ch]"><QuestionEvent e={e} onAnswer={onAnswer} /></div>;
  if (e.kind === "thinking")
    return (
      <div className="flex gap-[1ch] mt-1 text-muted" title={e.ts}>
        <span className="shrink-0 select-none text-brand/70">{CLI_STAR}</span>
        <span className="flex-1 min-w-0 italic">
          {e.text ? <Expandable text={e.text} max={200} className="text-muted" /> : <>Thinking{!e.tokens && "…"}</>}
          {e.tokens ? <span className="not-italic text-muted/60 ml-[1ch]">({fmtTokens(e.tokens)} tokens)</span> : null}
        </span>
      </div>
    );
  if (e.kind === "graph")
    return (
      <div className="mt-1" title={e.ts}>
        <div className="flex gap-[1ch]">
          <span className="shrink-0 select-none text-ok">{CLI_BULLET}</span>
          <span className="flex-1 min-w-0">
            <span className="text-text">code graph</span>
            {e.symbol && <span className="text-muted">({e.symbol})</span>}
          </span>
        </div>
        <div className="flex gap-[1ch] mt-0.5">
          <span className="shrink-0 select-none text-muted/50 pl-[2ch]">{CLI_CORNER}</span>
          <span className="flex-1 min-w-0 text-muted"><Linkify text={e.text || ""} /></span>
        </div>
      </div>
    );
  return (
    <div className="flex gap-[1ch] mt-1" title={e.ts}>
      <span className={cls("shrink-0 select-none", e.cut ? "text-warn" : "text-brand")}>{CLI_BULLET}</span>
      {/* The bar STAYS. The real console has no rule down the left of an answer, and it was
          removed once on that reasoning — wrongly. It is one of the Studio's own additions and
          it earns its place: it marks where an answer starts and ends in a long scroll, and it
          turns warn-coloured when the model stopped at its output limit, which is a thing the
          console cannot tell you at all. Fidelity to the console is not the goal on its own. */}
      <div className={cls("flex-1 min-w-0 text-text/90 border-l-2 pl-2",
        e.cut ? "border-warn/70" : "border-brand/70")}>
        <Markdown text={e.text || ""} />
        {e.cut && (
          <div className="text-warn text-[0.9em] mt-0.5"
            title="The model reached its max output tokens for this turn. Ask it to continue — the Studio received everything it sent.">
            answer hit the output limit — ask it to continue
          </div>
        )}
      </div>
    </div>
  );
}, sameEvent);

function Ts({ ts }: { ts: string }) {
  return <span className="text-muted/40 text-[0.82em] mr-1 select-none font-mono">{ts}</span>;
}

function EventBody({ e, onAnswer, onRewind }: { e: FeedEvent; onAnswer?: (t: string) => void; onRewind?: (uuid: string, text: string, restoreFiles: boolean) => void }) {
  if (e.kind === "question")
    return <QuestionEvent e={e} onAnswer={onAnswer} />;
  if (e.kind === "thinking")
    return (
      <span className="text-muted italic">
        <Brain size={12} className="inline mr-1 -mt-0.5 text-accent" />
        {e.text ? <Expandable text={e.text} max={200} className="text-muted" /> : <span className="text-muted/70">Thinking{!e.tokens && "…"}</span>}
        {e.tokens ? <span className="text-muted/60 not-italic ml-1 font-mono text-[0.85em]">· {fmtTokens(e.tokens)} tokens</span> : null}
      </span>
    );
  if (e.kind === "graph")
    // The code graph answered a search. It used to do this invisibly: the hook fed the answer
    // straight into the model and the person watching had no way to know the graph had run at
    // all, or whether it was worth keeping. Now it says so, with the location it gave.
    return (
      <span className="text-[0.92em]">
        <NetworkIcon size={11} className="inline mr-1 -mt-0.5 text-ok" />
        <span className="text-ok/90">code graph</span>
        {e.symbol && <code className="mx-1 px-1 py-0.5 rounded bg-panel2 font-mono text-[0.92em]">{e.symbol}</code>}
        <span className="text-muted">→ </span>
        <span className="text-muted"><Linkify text={e.text || ""} /></span>
      </span>
    );
  if (e.kind === "user")
    return <UserEvent e={e} onRewind={onRewind} />;
  if (e.kind === "text")
    return (
      <div className={cls("border-l-2 pl-2 text-text/90", e.cut ? "border-warn/70" : "border-brand/60")} title="My answer">
        <Markdown text={e.text || ""} />
        {/* the MODEL hit its output ceiling — say so, so a genuinely cut answer is never
            mistaken for the Studio dropping text */}
        {e.cut && (
          <div className="mt-1 inline-flex items-center gap-1 text-[11px] text-warn"
            title="The model reached its max output tokens for this turn. Ask it to continue — the Studio received everything it sent.">
            <AlertTriangle size={11} /> answer hit the output limit — ask it to continue
          </div>
        )}
      </div>
    );
  if (e.kind === "result") return <ResultBlock text={e.text || ""} ok={e.ok} />;
  if (e.kind === "turn") return <TurnBar e={e} />;
  return <ToolEvent e={e} />;
}

/** How long, in the shortest form that is still exact enough to act on. */
function fmtSecs(sec: number): string {
  if (!sec || sec < 0) return "";
  if (sec < 60) return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)}s`;
  const m = Math.floor(sec / 60), r = Math.round(sec % 60);
  if (m < 60) return `${m}m ${r}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** The line under a finished answer: what it took, in the order a person asks it.
 *
 *  A FIELD THAT IS NOT KNOWN IS NOT DRAWN. The backend sends only what it has — the running turn
 *  has not been priced yet, and a conversation older than the banking has no price at all — so a
 *  missing token count means "not known", never "zero". Making one of these numbers up would
 *  spoil the only ones worth reading.
 *
 *  The subagent figures are a SLICE of the turn, not an addition to it: the CLI prices the whole
 *  turn on its own rate card and a Task the turn spawned is part of that turn. The word "of" in
 *  the expanded view is doing real work.
 */
function TurnBar({ e }: { e: FeedEvent }) {
  const [open, setOpen] = useState(false);
  const t = e as any;
  const bits: string[] = [];
  if (t.tools) bits.push(`${t.tools} tool${t.tools === 1 ? "" : "s"}`);
  if (t.files) bits.push(`${t.files} file${t.files === 1 ? "" : "s"}`);
  if (t.tokens) bits.push(`${fmtTokens(t.tokens)} tok`);
  if (t.agents) bits.push(`${t.agents} agent${t.agents === 1 ? "" : "s"}`);
  if (t.wall_s) bits.push(fmtSecs(t.wall_s));
  if (!bits.length) return null;

  const rows: Array<[string, string, string]> = [];
  if (t.tools) rows.push(["tool calls", String(t.tools), ""]);
  if (t.files) rows.push(["files written", `${t.files}`,
    [t.added ? `+${t.added}` : "", t.removed ? `−${t.removed}` : ""].filter(Boolean).join(" ")]);
  if (t.tokens) rows.push(["tokens out", t.tokens.toLocaleString(), t.model || ""]);
  if (t.cost) rows.push(["cost", `$${Number(t.cost).toFixed(4)}`, "the CLI's own price, subagents included"]);
  if (t.gen_s) rows.push(["generating", fmtSecs(t.gen_s), t.wall_s ? `of ${fmtSecs(t.wall_s)} wall` : ""]);
  else if (t.wall_s) rows.push(["took", fmtSecs(t.wall_s), t.wall_from === "cli" ? "" : "first to last event"]);
  if (t.agents) {
    rows.push(["subagents", String(t.agents), t.agent_tools ? `${t.agent_tools} tool calls` : ""]);
    if (t.agent_tokens) rows.push(["their new tokens", t.agent_tokens.toLocaleString(),
      "input + output, each call once; part of the cost above, not extra"]);
    if (t.agent_s) rows.push(["their time", fmtSecs(t.agent_s), "summed; they overlap"]);
  }

  return (
    <div className="mt-1">
      <button onClick={() => setOpen((v) => !v)}
        title={open ? "Hide the detail" : "What this answer took"}
        className="w-full flex items-center gap-2 px-2 py-1 rounded bg-panel2/50 hover:bg-panel2
                   text-[11px] font-mono text-muted/70 hover:text-muted transition-colors">
        <Clock size={11} className="shrink-0 text-muted/50" />
        <span className="truncate text-left flex-1">{bits.join(" · ")}</span>
        {t.compacting && <span className="shrink-0 text-warn/70">compacted</span>}
        <ChevronRight size={12} className={cls("shrink-0 transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        <div className="mt-1 ml-2 pl-2 border-l border-line/60 grid grid-cols-[auto_auto_1fr] gap-x-3 gap-y-0.5
                        text-[11px] font-mono">
          {rows.map(([k, v, note]) => (
            <Fragment key={k}>
              <span className="text-muted/60">{k}</span>
              <span className="text-text/80 text-right tabular-nums">{v}</span>
              <span className="text-muted/40">{note}</span>
            </Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

// Question card with the option buttons AND an "Other…" free-text answer, so you can
// always type a custom reply right where the question is (like this very chat does).
function QuestionEvent({ e, onAnswer }: { e: FeedEvent; onAnswer?: (t: string) => void }) {
  const [other, setOther] = useState("");
  const [showOther, setShowOther] = useState(false);
  const submit = () => { const t = other.trim(); if (t) { onAnswer?.(t); setOther(""); setShowOther(false); } };
  const opts = e.options || [];
  const hasDesc = opts.some((o) => o.description);
  return (
    <div className="rounded-md border border-warn/50 bg-warn/10 p-2 my-0.5 space-y-1.5">
      <div className="flex items-center gap-1.5 text-warn font-semibold"><HelpCircle size={13} /> Needs your answer</div>
      <div className="text-text/90"><Markdown text={e.text || ""} /></div>
      <div className={cls("pt-0.5", hasDesc ? "flex flex-col gap-1" : "flex flex-wrap gap-1.5")}>
        {opts.map((o, i) => (
          <button key={i} title={hasDesc ? undefined : o.description} onClick={() => onAnswer?.(o.label)}
            className={cls("rounded-md border border-warn/50 bg-panel2 hover:bg-warn/20 transition-colors text-text",
              hasDesc ? "text-left px-2.5 py-1.5" : "px-2 py-1 text-xs font-medium")}>
            <div className={hasDesc ? "text-xs font-medium" : ""}>{o.label}</div>
            {hasDesc && o.description && <div className="text-muted text-[11px] mt-0.5 leading-snug">{o.description}</div>}
          </button>
        ))}
        <button onClick={() => setShowOther((v) => !v)}
          className={cls("rounded-md border border-warn/50 bg-panel2 hover:bg-warn/20 text-text text-xs font-medium inline-flex items-center gap-1",
            hasDesc ? "px-2.5 py-1.5 self-start" : "px-2 py-1")}>
          <Pencil size={11} /> Other…
        </button>
      </div>
      {showOther ? (
        <div className="flex items-end gap-1.5">
          <textarea autoFocus rows={2} value={other} onChange={(ev) => setOther(ev.target.value)}
            onKeyDown={(ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); submit(); } }}
            placeholder="Type your own answer… (Enter to send)"
            className="input text-xs flex-1 resize-none" />
          <button className="btn-primary !px-2 !py-1 text-xs shrink-0" onClick={submit}>Send</button>
        </div>
      ) : (
        <div className="text-[10px] text-muted/70">click an option, “Other…”, or type in the chat box below ↓</div>
      )}
    </div>
  );
}

function ToolEvent({ e }: { e: FeedEvent }) {
  const Icon = toolIcon(e.icon);
  // The command is collapsed by default. The TITLE and the plain-English subtitle already say
  // what is happening; the shell line underneath is the part you only want when you are
  // checking or copying it, and a run of them turns the feed into a wall of quoting and pipes.
  const [showCmd, setShowCmd] = useState(false);
  // NOTHING DESCRIBES IT, SO THE COMMAND DOES. Codex sends no plain-English line with a command,
  // and a card that said only "PowerShell" hid what ran behind a click. Its first line takes the
  // subtitle's place, the way the console look already prints it in the parentheses; the rest is
  // still one click away when there is more.
  const head = (e.command || "").split("\n")[0].trim();
  const cmdSub = !e.subtitle && head ? (head.length > 160 ? head.slice(0, 157) + "…" : head) : "";
  const moreCmd = !!e.command && (!cmdSub || e.command.trim() !== cmdSub);
  return (
    <div className="inline-block align-top w-full">
      <span className="inline-flex items-center gap-1.5 flex-wrap">
        <Icon size={12} className="text-muted shrink-0" />
        <span className="font-semibold text-text/90">{e.title}</span>
        {e.subtitle && (FILE_ONE.test(e.subtitle.trim())
          ? <FileLink path={e.subtitle.trim()} />
          : <span className="text-muted font-mono break-all" title={e.subtitle}>{e.subtitle}</span>)}
        {cmdSub && <span className="text-muted font-mono break-all" title={e.command}>{cmdSub}</span>}
        {e.bg && (
          <span className="chip text-[9px] text-accent shrink-0" title="Started in the background — it keeps running past this card">
            background
          </span>
        )}
        {!e.bg && e.slow ? (
          <span className="chip text-[9px] text-muted shrink-0" title={`Allowed up to ${e.slow} minutes — this one is expected to take a while`}>
            up to {e.slow}m
          </span>
        ) : null}
        {e.diff && (e.diff.added > 0 || e.diff.removed > 0) && (
          <span className="font-mono text-[0.82em]">
            {e.diff.added > 0 && <span className="text-ok">+{e.diff.added}</span>}
            {e.diff.removed > 0 && <span className="text-danger ml-1">−{e.diff.removed}</span>}
          </span>
        )}
      </span>
      {moreCmd && (showCmd ? (
        <div className="mt-1">
          <pre className="bg-panel2 border border-line rounded px-2 py-1 overflow-x-auto font-mono text-text/90 whitespace-pre-wrap break-words">
            <span className="text-ok select-none">$ </span><Linkify text={e.command || ""} />
          </pre>
          <button onClick={(ev) => { ev.stopPropagation(); setShowCmd(false); }}
            className="mt-0.5 text-[10px] text-muted hover:text-text inline-flex items-center gap-0.5">
            <ChevronUp size={10} /> hide command
          </button>
        </div>
      ) : (
        <button onClick={(ev) => { ev.stopPropagation(); setShowCmd(true); }}
          title={e.command}
          className="mt-0.5 text-[10px] text-muted hover:text-text inline-flex items-center gap-0.5">
          <ChevronDown size={10} /> show command
        </button>
      ))}
      <FeedDiff e={e} />
      {e.todos && e.todos.length > 0 && <TodoView todos={e.todos} />}
    </div>
  );
}

// Shows a PREVIEW of the change inline (enough to see what happened) and expands to
// the rest on click — like the BridgeMind/Claude feed. Not collapsed-to-nothing, not
// the whole file dumped. PREVIEW lines are shown by default; the rest is one click away.
const DIFF_PREVIEW = 14;
// Tool OUTPUT (bash stdout, file reads, grep hits): monospace with indentation preserved,
// because this is console/code text — the prose font made it unreadable. Line-numbered output
// (Read, grep -n) gets a real gutter so the numbers stop competing with the content.
const NUMBERED = /^\s*(\d+)([:\t ])(.*)$/;
function ResultBlock({ text, ok, cli }: { text: string; ok?: boolean; cli?: boolean }) {
  const [open, setOpen] = useState(false);
  // WHICH OF THE THREE MODES. `open` is the reader's own decision and beats it, so changing the
  // mode never takes back something you have already asked to see.
  const detail = useStore((s) => s.feedDetail);
  const lines = (text || "").replace(/\s+$/, "").split("\n");
  const { shown, hidden, expand, collapse } = resultView(lines, detail, open);
  const nums = shown.map((l) => l.match(NUMBERED));
  // only treat it as numbered when MOST rows agree — one stray "3 files" must not shift the rest
  const gutter = shown.length > 2 && nums.filter(Boolean).length >= Math.ceil(shown.length * 0.7);
  const btn = cli
    ? "text-left text-muted/70 hover:text-text"
    : "w-full text-left px-2 py-0.5 text-muted hover:text-text bg-panel2/70 border-t border-line/60";
  // Same clamp, same gutter, same expander in both looks — only the frame round it differs.
  const body = (
    <>
      {/* MINIMAL DRAWS NO BOX AT ALL. An empty bordered strip above a "show 24 lines" button
          reads as a broken result rather than a folded one. */}
      {shown.length > 0 && (
      <div className={cls("overflow-x-auto", cli ? "" : "px-2 py-1")}>
        {shown.map((l, i) => {
          const m = gutter ? nums[i] : null;
          return (
            <div key={i} className="flex">
              {gutter && <span className="select-none opacity-30 w-9 shrink-0 text-right pr-2 tabular-nums">{m ? m[1] : ""}</span>}
              <span className={cls("flex-1 min-w-0 whitespace-pre-wrap break-words",
                ok === false ? "text-danger/90" : cli ? "text-muted" : "text-text/70")}>
                <Linkify text={m ? m[3] : l} />
              </span>
            </div>
          );
        })}
      </div>
      )}
      {!!expand && (
        <button className={btn} onClick={(e) => { e.stopPropagation(); setOpen(true); }}>{expand}</button>
      )}
      {collapse && (
        <button className={btn} onClick={(e) => { e.stopPropagation(); setOpen(false); }}>collapse</button>
      )}
    </>
  );
  if (cli)
    return (
      <div className="flex gap-[1ch] mt-0.5">
        <span className={cls("shrink-0 select-none pl-[2ch]", ok === false ? "text-danger/70" : "text-muted/50")}>{CLI_CORNER}</span>
        <div className="min-w-0 flex-1 text-[0.95em] leading-[1.5]">{body}</div>
      </div>
    );
  return (
    <div className="flex gap-1 mt-0.5">
      <CornerDownRight size={11} className="mt-1 shrink-0 opacity-40" />
      <div className={cls("min-w-0 flex-1 rounded border bg-panel2/40 overflow-hidden font-mono text-[0.86em] leading-[1.5]",
        ok === false ? "border-danger/40" : "border-line/60")}>
        {body}
      </div>
    </div>
  );
}

/** Shared prefix/suffix of a rewritten line → [common head, changed middle, common tail]. */
function splitChange(a: string, b: string): [string, string, string] | null {
  const max = Math.min(a.length, b.length);
  let s = 0;
  while (s < max && a[s] === b[s]) s++;
  let e = 0;
  while (e < max - s && a[a.length - 1 - e] === b[b.length - 1 - e]) e++;
  if (s === 0 && e === 0) return null;            // nothing in common — plain highlight is clearer
  return [a.slice(0, s), a.slice(s, a.length - e), a.slice(a.length - e)];
}

// A DIFF YOU CAN WRITE ON.
//
// The correction a person actually wants to make is "not that line, this one" — and today that
// means scrolling back up, finding the line, retyping it into the composer and hoping the agent
// matches it to the right place. Here the line is clicked, the note is typed beside it, and the
// batch goes down to the composer with the code quoted.
//
// THE LINE NUMBER IS NOT ALWAYS A FILE LINE, and that is why the code is quoted rather than the
// number cited. A feed card for an `Edit` shows the diff of two STRINGS — `old_string` against
// `new_string` — so its numbers count from the start of the edit. A checkpoint diff compares
// whole files, so its numbers are real. `lineIsFile` is what tells the two apart, and only the
// second kind ever puts a number in front of the agent.
export function DiffView({ hunks, total, file = "", projectId = "", lineIsFile = false }: {
  hunks: FeedDiffLine[];
  total: number;
  file?: string;
  projectId?: string;
  lineIsFile?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [writing, setWriting] = useState(-1);
  const canNote = useSetting("diff_notes", true) && !!projectId && !!file;
  const addNote = useStore((s) => s.addDiffNote);
  const notes = useStore((s) => s.diffNotes);
  const shown = open ? hunks : hunks.slice(0, DIFF_PREVIEW);
  const moreLoaded = hunks.length - shown.length;          // sent but hidden in preview
  const truncated = Math.max(0, total - hunks.length);     // beyond what the backend sent
  return (
    <div className="mt-1 rounded border border-line overflow-hidden font-mono">
      {shown.map((h, i) => {
        if (h.t === "gap")
          return <div key={i} className="px-2 text-muted/50 select-none bg-panel2/40 border-y border-line/40">⋯</div>;
        const num = h.t === "del" ? h.n : (h.m ?? h.n);
        // word-level: a del immediately followed by an add is a rewrite of the same line —
        // dim the shared prefix/suffix so only what actually changed reads as changed.
        const partner = h.t === "del" ? shown[i + 1] : h.t === "add" ? shown[i - 1] : undefined;
        const paired = partner && partner.t !== h.t && (partner.t === "add" || partner.t === "del");
        const seg = paired ? splitChange(h.s || "", partner!.s || "") : null;
        const mine = canNote ? notes.filter((n) => n.projectId === projectId && n.file === file && n.code === (h.s || "")) : [];
        return (
          <div key={i}>
            <div className={cls("group flex px-1 whitespace-pre-wrap break-words",
              h.t === "add" ? "bg-ok/10 text-ok" : h.t === "del" ? "bg-danger/10 text-danger" : "text-muted/70")}>
              <span className="select-none opacity-40 w-9 shrink-0 text-right pr-1.5 tabular-nums">{num ?? ""}</span>
              <span className="select-none opacity-60 w-3 shrink-0">{h.t === "add" ? "+" : h.t === "del" ? "−" : " "}</span>
              <span className="flex-1 min-w-0">
                {seg
                  ? <><span className="opacity-45">{seg[0]}</span><span className={cls("rounded px-0.5", h.t === "add" ? "bg-ok/25" : "bg-danger/25")}>{seg[1]}</span><span className="opacity-45">{seg[2]}</span></>
                  : (h.s || " ")}
              </span>
              {canNote && (
                <button type="button" title="Comment on this line — it goes to the agent with the code quoted"
                  onClick={(ev) => { ev.stopPropagation(); setWriting(writing === i ? -1 : i); }}
                  className={cls("shrink-0 self-start ml-1 px-1 rounded text-muted/50 hover:text-brand",
                    writing === i || mine.length ? "opacity-100 text-brand" : "opacity-0 group-hover:opacity-100")}>
                  <MessageSquarePlus size={11} />
                </button>
              )}
            </div>
            {mine.map((n) => (
              <div key={n.id} className="flex gap-1.5 px-2 py-0.5 bg-brand/5 border-l-2 border-brand/50 text-text/80 font-sans">
                <MessageSquarePlus size={11} className="mt-0.5 shrink-0 text-brand/70" />
                <span className="flex-1 min-w-0 break-words">{n.note}</span>
              </div>
            ))}
            {writing === i && (
              <NoteBox onCancel={() => setWriting(-1)} onSave={(text) => {
                addNote({ projectId, file, code: h.s || "", side: h.t === "gap" ? "ctx" : h.t,
                          line: num, lineIsFile, note: text });
                setWriting(-1);
              }} />
            )}
          </div>
        );
      })}
      {!open && (moreLoaded > 0 || truncated > 0) && (
        <button className="w-full text-left px-2 py-0.5 text-[0.82em] text-muted hover:text-text bg-panel2 border-t border-line"
          onClick={(e) => { e.stopPropagation(); setOpen(true); }}>
          … show {moreLoaded + truncated} more {moreLoaded + truncated === 1 ? "line" : "lines"}
        </button>
      )}
      {open && hunks.length > DIFF_PREVIEW && (
        <button className="w-full text-left px-2 py-0.5 text-[0.82em] text-muted hover:text-text bg-panel2 border-t border-line"
          onClick={(e) => { e.stopPropagation(); setOpen(false); }}>collapse</button>
      )}
      {open && truncated > 0 && <div className="px-2 py-0.5 text-muted/60 bg-panel2">… {truncated} more lines (truncated)</div>}
    </div>
  );
}

/** One comment being written. Enter saves, Escape abandons — the same keys as the composer. */
function NoteBox({ onSave, onCancel }: { onSave: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  const save = () => { const t = text.trim(); if (t) onSave(t); else onCancel(); };
  return (
    <div className="flex gap-1.5 px-2 py-1 bg-panel2/60 border-l-2 border-brand font-sans">
      <textarea ref={ref} rows={2} value={text} onChange={(e) => setText(e.target.value)}
        placeholder="What is wrong with this line?"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
          if (e.key === "Escape") { e.preventDefault(); onCancel(); }
        }}
        className="input flex-1 min-w-0 !py-1 !text-[12px] resize-none" />
      <div className="flex flex-col gap-1">
        <button className="chip hover:text-brand" title="Save (Enter)" onClick={save}><Check size={11} /></button>
        <button className="chip hover:text-text" title="Cancel (Esc)" onClick={onCancel}><X size={11} /></button>
      </div>
    </div>
  );
}

// While an answer streams, `text` changes on every chunk, so a single <Markdown> re-parses the
// WHOLE answer per token — the cost grows with the answer. Split at the last safe block boundary:
// the head is a stable string, so the memoized <Markdown> for it is skipped, and only the last two
// blocks are re-parsed. Never splits inside an open code fence, or the fence would render as text.
function splitStable(text: string): [string, string] {
  if ((text.match(/^```/gm) || []).length % 2 === 1) {   // a fence is still open — freeze before it
    const i = text.lastIndexOf("\n```");
    return i > 0 ? [text.slice(0, i + 1), text.slice(i + 1)] : ["", text];
  }
  let cut = text.length;
  for (let n = 0; n < 2; n++) {                          // keep the last two blocks live
    const i = text.lastIndexOf("\n\n", cut - 1);
    if (i <= 0) return ["", text];
    cut = i;
  }
  return [text.slice(0, cut), text.slice(cut)];
}

function StreamingMarkdown({ text }: { text: string }) {
  const [head, tail] = splitStable(text);
  return <>{head && <Markdown text={head} />}<Markdown text={tail} /></>;
}

function TodoView({ todos }: { todos: FeedTodo[] }) {
  const ic = (s: string) => (s === "completed" ? "✓" : s === "in_progress" ? "◐" : "○");
  const col = (s: string) => (s === "completed" ? "text-ok" : s === "in_progress" ? "text-brand" : "text-muted");
  return (
    <div className="mt-1 space-y-0.5">
      {todos.map((t, i) => (
        <div key={i} className={cls("flex gap-1.5", col(t.status))}>
          <span className="select-none shrink-0">{ic(t.status)}</span>
          <span className={cls(t.status === "completed" && "line-through opacity-70")}>{t.content}</span>
        </div>
      ))}
    </div>
  );
}

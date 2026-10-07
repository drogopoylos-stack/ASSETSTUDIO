import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square } from "lucide-react";
import { api } from "../api/client";
import { cls } from "./ui";

type St = "idle" | "setup" | "recording" | "transcribing";

/** What the composer needs to say out loud about dictation (see the note under the button). */
export interface VoiceState { st: St; hint: string; ready: boolean; engine: string; task: string }

// DICTATION, IN THE PROMPT BAR, WITH ITS LANGUAGE NEXT TO IT.
//
// Two things used to keep people from finding this. The button drew a **download arrow** until the
// local model was resident — so the one control that starts voice input read as "download
// something", which is exactly when you first go looking for it. And the only language control
// lived in the settings popover, a menu away from the words it changes.
//
// So: the mic is ALWAYS a mic (a small amber dot says "one click still sets it up"), and `ΕΛ | EN`
// sits beside it, in the bar:
//
//   ΕΛ — keep the spoken language: μιλάς ελληνικά, γράφεται ελληνικά (Whisper `transcribe`).
//   EN — whatever you speak is written in English, Greek included (Whisper `translate`).
//
// One click starts listening. If voice is not set up yet the same click sets it up and *then*
// starts listening, so the first use is still a single click and not "click, wait, click again".
export function VoiceButton({ onText, task, onTask, onStatus, className, compact }: {
  onText: (t: string) => void;
  task: string;
  onTask?: (task: string) => void;
  onStatus?: (s: VoiceState) => void;
  className?: string;
  compact?: boolean;
}) {
  const [st, setSt] = useState<St>("idle");
  const [ready, setReady] = useState(false);
  const [hint, setHint] = useState("");
  // Which engine is answering. "ready" means two different things: for the local engine it is
  // "the worker has the model loaded", for Groq it is "a key is saved". Reading only the local
  // flag left the button showing the Download icon forever on Groq — nothing was missing, and
  // clicking it started installing faster-whisper, which the cloud engine never uses.
  const [engine, setEngine] = useState("local");
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const poll = useRef<number | null>(null);
  /** The click that kicks off setup also meant "listen", so listen the moment it is ready. */
  const autoRec = useRef(false);

  /** Is this engine usable right now? Local needs a loaded worker; Groq needs a saved key. */
  function usable(s: { engine?: string; ready?: boolean; groq_key?: boolean }) {
    return (s.engine || "local").toLowerCase() === "groq" ? !!s.groq_key : !!s.ready;
  }

  // Report state upward. The callback rides a ref so a parent that passes a fresh arrow function
  // every render cannot restart the sync interval below or loop the effect.
  const report = useRef(onStatus);
  useEffect(() => { report.current = onStatus; });
  useEffect(() => {
    report.current?.({ st, hint, ready, engine, task });
  }, [st, hint, ready, engine, task]);

  useEffect(() => {
    // Re-read on focus as well as on mount: switching the engine in Settings happens in another
    // pane, and without this the button kept the readiness it was born with until a reload.
    const sync = () => api.voiceStatus().then((s) => {
      setEngine((s.engine || "local").toLowerCase());
      setReady(usable(s));
    }).catch(() => {});
    sync();
    window.addEventListener("focus", sync);
    const iv = window.setInterval(sync, 10000);
    return () => {
      window.removeEventListener("focus", sync);
      window.clearInterval(iv);
      if (poll.current) window.clearInterval(poll.current);
      stream.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  function stopPoll() { if (poll.current) { window.clearInterval(poll.current); poll.current = null; } }
  function pollReady() {
    if (poll.current) return;
    const tick = async () => {
      const s = await api.voiceStatus().catch(() => null);
      if (!s) return;
      setEngine((s.engine || "local").toLowerCase());
      if ((s.engine || "local").toLowerCase() === "groq") {
        // Nothing installs for the cloud engine — it is ready or it is missing a key.
        if (s.groq_key) { setReady(true); setHint(""); setSt("idle"); }
        else { autoRec.current = false; setHint("No Groq key — add one in Settings → General → Voice input."); }
        stopPoll();
        return;
      }
      if (s.installing) setHint("installing voice…");
      else if (s.loading) setHint("downloading model (one-time)…");
      if (s.install_error || s.worker_error) {
        autoRec.current = false;
        setHint(s.install_error || s.worker_error); setSt("idle"); stopPoll(); return;
      }
      if (s.ready) {
        setReady(true); setHint(""); setSt("idle"); stopPoll();
        if (autoRec.current) { autoRec.current = false; startRec(); }
      }
    };
    tick();
    poll.current = window.setInterval(tick, 2500);
  }

  async function startRec() {
    autoRec.current = false;
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = s; chunks.current = [];
      const mr = new MediaRecorder(s);
      mr.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
      mr.onstop = async () => {
        stream.current?.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks.current, { type: mr.mimeType || "audio/webm" });
        setSt("transcribing");
        const r = await api.voiceTranscribe(blob, task).catch(() => null);
        setSt("idle");
        if (r?.text) { setHint(""); onText(r.text.trim()); }
        else if (r?.error) setHint(r.error);
        else setHint("Nothing was heard — try again a little closer to the mic.");
      };
      rec.current = mr; mr.start(); setSt("recording"); setHint("");
    } catch {
      setSt("idle"); setHint("microphone blocked — allow mic access");
    }
  }

  function click() {
    if (st === "recording") { rec.current?.stop(); return; }
    if (st === "transcribing" || st === "setup") return;
    if (!ready) {
      // Only the local engine has anything to install. Calling voiceEnable() on Groq would start
      // a multi-GB faster-whisper download to fix a missing API key — the opposite of the point.
      if (engine === "groq") {
        setHint("No Groq key — add one in Settings → General → Voice input.");
        return;
      }
      autoRec.current = true;                 // set up, then listen: the first use is one click
      setSt("setup"); setHint("setting up voice…");
      api.voiceEnable().catch(() => {});
      pollReady();
      return;
    }
    startRec();
  }

  const langIsEn = task !== "transcribe";     // "translate" (default) → English; anything else keeps it
  const langLabel = langIsEn ? "EN" : "ΕΛ";
  const langTitle = langIsEn
    ? "Voice language: English — ό,τι πεις (ελληνικά ή αγγλικά) γράφεται στα αγγλικά.\nΚλικ για Ελληνικά (κρατάει ό,τι λες)."
    : "Voice language: Ελληνικά — κρατάει τη γλώσσα που μιλάς.\nClick for English (translates whatever you say).";

  const title = st === "recording" ? "Recording — μίλα τώρα · click to stop & write it in"
    : st === "transcribing" ? "Transcribing…"
    : st === "setup" ? (hint || "Setting up voice…")
    : ready ? `Dictate — click to record (${langIsEn ? "→ English" : "Ελληνικά / as spoken"})`
              + ` via ${engine === "groq" ? "Groq" : "local Whisper"}`
    : engine === "groq" ? (hint || "Voice needs a Groq key — Settings → General → Voice input")
    : (hint || "Dictate — the first click sets up local Whisper, then it starts listening");

  // ALWAYS a mic. The setup case is the amber dot, not a different icon: an arrow here read as
  // "download", which is what hid this control from the people who went looking for it.
  const Icon = st === "recording" ? Square : (st === "transcribing" || st === "setup") ? Loader2 : Mic;

  return (
    <>
      <button type="button" onClick={click} title={title} aria-label="voice input"
        className={cls(className, "relative",
          st === "recording" && "!text-danger !border-danger/50 !bg-danger/10",
          (st === "transcribing" || st === "setup") && "!text-brand")}>
        <Icon size={15} className={cls(
          (st === "transcribing" || st === "setup") && "animate-spin",
          st === "recording" && "animate-pulse")} />
        {!ready && st === "idle" && (
          <span className="absolute -top-0.5 -right-0.5 h-1.5 w-1.5 rounded-full bg-warn" />
        )}
      </button>
      {onTask && (
        <button type="button" onClick={() => onTask(langIsEn ? "transcribe" : "translate")}
          title={langTitle} aria-label={`voice language: ${langIsEn ? "English" : "Greek"}`}
          className={cls("inline-flex items-center justify-center rounded-lg border font-semibold shrink-0",
            "text-[10px] tracking-wide transition-colors",
            compact ? "h-[34px] px-1.5" : "h-[36px] px-1.5",
            langIsEn ? "border-brand/50 bg-brand/10 text-brand"
                     : "border-line bg-panel2 text-muted hover:bg-line hover:text-text")}>
          {langLabel}
        </button>
      )}
    </>
  );
}

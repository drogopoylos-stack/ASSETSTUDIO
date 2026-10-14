import { useEffect, useRef, useState } from "react";
import { ClipboardPaste, Loader2, MessageSquarePlus, Send, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { WorkspaceRoot } from "../types";
import { cls } from "./ui";

// A SCREENSHOT, MARKED UP, HANDED TO WHICHEVER WORKSPACE IT IS ABOUT.
//
// Ctrl+V into the prompt box already attached a screenshot — to the workspace you were in, with
// no way to say anything about it except in prose. "The button is misaligned" then has to name
// which button, in words, about a picture that is right there.
//
// So: paste the image here, click the spots you mean, and type a line per spot. The pins are
// burned into the picture together with a numbered legend, so the agent reads the note beside the
// pixel it is about. Then pick the workspace — any of them, not only the open one — and either
// keep the image as a reference to send later, or send the notes as the prompt now.
//
// Nothing is saved by hand. The flattened PNG is uploaded into the target project the same way a
// pasted image always was.

type Pin = { x: number; y: number; text: string };   // x/y are fractions of the image

/** Merge an image and a note into a workspace's stored draft, so a composer that is not mounted
    still finds them when it opens. The composer owns this key; it reads it on a project change. */
function mergeDraft(projectId: string, text: string, image: string) {
  try {
    const key = `cc-draft-${projectId}`;
    const d = JSON.parse(localStorage.getItem(key) || "null") || {};
    const had = typeof d.msg === "string" ? d.msg : "";
    const msg = text ? (had.trim() ? had.replace(/\s*$/, "\n\n") + text : text) : had;
    const images = Array.from(new Set([...(Array.isArray(d.images) ? d.images : []), image]));
    localStorage.setItem(key, JSON.stringify({ msg, files: Array.isArray(d.files) ? d.files : [], images }));
  } catch { /* out of quota — the store feed below still reaches an open composer */ }
}

export function PasteBoard({ roots, current, onClose, onJump }: {
  roots: WorkspaceRoot[];
  current: WorkspaceRoot | null;
  onClose: () => void;
  /** Bring a workspace to the front, so its composer is mounted to receive the turn. */
  onJump: (r: WorkspaceRoot) => void;
}) {
  const [url, setUrl] = useState("");                 // object URL of the pasted image
  const [dim, setDim] = useState({ w: 0, h: 0 });
  const [pins, setPins] = useState<Pin[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [note, setNote] = useState("");               // the overall comment
  const [target, setTarget] = useState(current?.id || "");
  const [busy, setBusy] = useState("");
  const imgEl = useRef<HTMLImageElement | null>(null);
  const toast = useStore((s) => s.toast);
  const feedComposer = useStore((s) => s.feedComposer);

  const root = roots.find((r) => r.id === target) || null;

  function load(blob: Blob) {
    const next = URL.createObjectURL(blob);
    const im = new Image();
    im.onload = () => {
      setDim({ w: im.naturalWidth, h: im.naturalHeight });
      imgEl.current = im;
      setUrl((old) => { if (old) URL.revokeObjectURL(old); return next; });
      setPins([]); setSel(null);
    };
    im.onerror = () => { URL.revokeObjectURL(next); toast("That clipboard item is not an image.", "warn"); };
    im.src = next;
  }

  // Ctrl+V anywhere on the board, which is how a screenshot arrives. Capture phase, so the
  // textareas inside do not swallow it while the user is writing a note.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const it = Array.from(e.clipboardData?.items || []).find((i) => i.type.startsWith("image/"));
      if (!it) return;
      e.preventDefault();
      const b = it.getAsFile();
      if (b) load(b);
    };
    window.addEventListener("paste", onPaste, true);
    return () => window.removeEventListener("paste", onPaste, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => { if (url) URL.revokeObjectURL(url); /* eslint-disable-next-line */ }, [url]);

  /** The Paste button, for people who do not know the shortcut. Needs clipboard-read permission. */
  async function readClipboard() {
    try {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        const type = it.types.find((t) => t.startsWith("image/"));
        if (type) { load(await it.getType(type)); return; }
      }
      toast("No image on the clipboard — take a screenshot first (Win+Shift+S).", "warn");
    } catch {
      toast("The browser would not read the clipboard. Press Ctrl+V instead.", "warn");
    }
  }

  function addPin(e: React.MouseEvent<HTMLDivElement>) {
    if (!url) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    setPins((p) => [...p, { x, y, text: "" }]);
    setSel(pins.length);
  }

  const notes = () => {
    const lines = pins.map((p, i) => `${i + 1}. ${p.text.trim()}`).filter((l) => l.length > 3);
    const head = note.trim();
    return [head, lines.join("\n")].filter(Boolean).join("\n\n");
  };

  /** The picture the agent sees: the screenshot, the numbered pins on it, the notes under it. */
  function flatten(): Promise<Blob> {
    const im = imgEl.current;
    const lines = pins.map((p, i) => `${i + 1}. ${p.text.trim() || "(no note)"}`);
    const head = note.trim();
    const w = dim.w || 800;
    const fs = Math.max(15, Math.round(w / 70));          // legible at the image's own scale
    const lh = Math.round(fs * 1.5);
    const pad = Math.round(fs * 1.2);
    const cv = document.createElement("canvas");
    const probe = cv.getContext("2d")!;
    probe.font = `${fs}px ui-sans-serif, Segoe UI, system-ui, sans-serif`;

    // Wrap to the image width first: the strip's height depends on how the text breaks.
    const wrapped: { t: string; bold: boolean }[] = [];
    for (const src of [...(head ? head.split("\n") : []), ...lines]) {
      const bold = !!head && src === head.split("\n")[0];
      let line = "";
      for (const word of src.split(/\s+/)) {
        const t = line ? line + " " + word : word;
        if (probe.measureText(t).width > w - pad * 2 && line) { wrapped.push({ t: line, bold }); line = word; }
        else line = t;
      }
      wrapped.push({ t: line, bold });
    }
    const strip = wrapped.length ? pad * 2 + wrapped.length * lh : 0;

    cv.width = w;
    cv.height = (dim.h || 600) + strip;
    const c = cv.getContext("2d")!;
    c.fillStyle = "#0b0e13";
    c.fillRect(0, 0, cv.width, cv.height);
    if (im) c.drawImage(im, 0, 0, w, dim.h);

    const r = Math.max(13, Math.round(w / 58));
    pins.forEach((p, i) => {
      const cx = p.x * w, cy = p.y * dim.h;
      c.beginPath(); c.arc(cx, cy, r, 0, Math.PI * 2);
      c.fillStyle = "#f59e0b"; c.fill();
      c.lineWidth = Math.max(2, r / 6); c.strokeStyle = "#111827"; c.stroke();
      c.fillStyle = "#111827";
      c.font = `bold ${Math.round(r * 1.25)}px ui-sans-serif, Segoe UI, system-ui, sans-serif`;
      c.textAlign = "center"; c.textBaseline = "middle";
      c.fillText(String(i + 1), cx, cy + 1);
    });

    c.textAlign = "left"; c.textBaseline = "alphabetic";
    let y = dim.h + pad + fs;
    for (const l of wrapped) {
      c.fillStyle = l.bold ? "#e5e7eb" : "#cbd5e1";
      c.font = `${l.bold ? "bold " : ""}${fs}px ui-sans-serif, Segoe UI, system-ui, sans-serif`;
      c.fillText(l.t, pad, y);
      y += lh;
    }
    return new Promise((res, rej) =>
      cv.toBlob((b) => (b ? res(b) : rej(new Error("could not render the marked-up image"))), "image/png"));
  }

  async function deliver(send: boolean) {
    if (!url) { toast("Paste a screenshot first — Ctrl+V.", "warn"); return; }
    if (!root) { toast("Pick the workspace this is about.", "warn"); return; }
    setBusy(send ? "send" : "attach");
    try {
      const blob = await flatten();
      const name = `note-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}.png`;
      const up = await api.wsUploadImage(root.path, blob, name);
      const text = notes();
      if (send && !text) { toast("Write a note first — it is the prompt.", "warn"); setBusy(""); return; }
      // The draft FIRST, so a workspace whose composer is not mounted has it waiting; then the
      // live channel, for the one that is. A mounted composer rewrites the draft from its own
      // state a moment later, which is the same content either way. The note goes into the draft
      // even when the turn is meant to be sent: if that engine turns out to need a key or a
      // sign-in, the writing is still in the box rather than lost with the refusal.
      mergeDraft(root.id, text, up.path);
      if (send && current?.id !== root.id) onJump(root);
      feedComposer(root.id, text, [up.path], send);
      toast(send ? `Sent to ${root.name}` : `Attached to ${root.name} — the note is in its prompt box`, "ok");
      onClose();
    } catch (e: any) {
      toast(`Could not hand it over: ${e.message}`, "danger");
    } finally {
      setBusy("");
    }
  }

  const ready = !!url;

  return (
    <>
      <div className="fixed inset-0 z-[80] bg-black/60" onClick={onClose} />
      <div className="fixed inset-6 z-[81] card p-0 flex flex-col overflow-hidden shadow-card">
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-line bg-panel text-xs">
          <ClipboardPaste size={14} className="text-brand" />
          <span className="font-semibold text-text">Paste &amp; mark</span>
          <span className="text-muted">· Ctrl+V a screenshot, click the spots, write a line each</span>
          <button className="ml-auto chip hover:border-brand hover:text-brand" onClick={readClipboard}>
            <ClipboardPaste size={11} /> Paste
          </button>
          <button className="p-1 rounded text-muted hover:text-text" onClick={onClose}><X size={15} /></button>
        </div>

        <div className="flex-1 min-h-0 flex">
          {/* the picture */}
          <div className="flex-1 min-w-0 overflow-auto bg-bg p-3 flex items-start justify-center">
            {!ready ? (
              <div className="m-auto max-w-sm text-center text-xs text-muted/70 leading-relaxed">
                Take a screenshot (Win+Shift+S) and press <span className="text-text">Ctrl+V</span> here.
                Nothing has to be saved to disk. Click the picture to drop a numbered pin, type what
                is wrong beside it, then hand it to any workspace.
              </div>
            ) : (
              <div className="relative inline-block cursor-crosshair" onClick={addPin}
                title="Click the spot you mean">
                <img src={url} alt="pasted screenshot" className="block max-w-full"
                  style={{ maxHeight: "calc(100vh - 14rem)" }} />
                {pins.map((p, i) => (
                  <span key={i}
                    className={cls("absolute -translate-x-1/2 -translate-y-1/2 h-5 w-5 rounded-full",
                      "flex items-center justify-center text-[10px] font-bold border",
                      sel === i ? "bg-brand text-white border-white/70" : "bg-warn text-black border-black/60")}
                    style={{ left: `${p.x * 100}%`, top: `${p.y * 100}%` }}
                    onClick={(e) => { e.stopPropagation(); setSel(i); }}>
                    {i + 1}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* the comments and where they go */}
          <div className="w-80 shrink-0 border-l border-line bg-panel/40 flex flex-col min-h-0">
            <div className="flex-1 min-h-0 overflow-auto p-2 space-y-2">
              <div>
                <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1">The comment</div>
                <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3}
                  placeholder="What is this screenshot about? This becomes the prompt."
                  className="w-full resize-y rounded-md bg-bg border border-line px-2 py-1.5 text-[13px]
                             outline-none focus:border-brand/60" />
              </div>
              {pins.length === 0 && ready && (
                <div className="text-[11px] text-muted/70 flex items-center gap-1">
                  <MessageSquarePlus size={12} /> Click the picture to mark a spot.
                </div>
              )}
              {pins.map((p, i) => (
                <div key={i} className={cls("rounded-md border p-1.5",
                  sel === i ? "border-brand/60 bg-brand/5" : "border-line bg-bg/40")}
                  onClick={() => setSel(i)}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="h-4 w-4 rounded-full bg-warn text-black text-[9px] font-bold
                                     flex items-center justify-center">{i + 1}</span>
                    <span className="text-[10px] text-muted">pin {i + 1}</span>
                    <button className="ml-auto p-0.5 rounded text-muted hover:text-danger"
                      title="Remove this pin"
                      onClick={(e) => { e.stopPropagation(); setPins((ps) => ps.filter((_, j) => j !== i)); setSel(null); }}>
                      <Trash2 size={11} />
                    </button>
                  </div>
                  <textarea value={p.text} rows={2} autoFocus={sel === i}
                    onChange={(e) => setPins((ps) => ps.map((q, j) => j === i ? { ...q, text: e.target.value } : q))}
                    placeholder="what is wrong here"
                    className="w-full resize-y rounded bg-bg border border-line px-1.5 py-1 text-[12px]
                               outline-none focus:border-brand/60" />
                </div>
              ))}
            </div>

            <div className="shrink-0 border-t border-line p-2 space-y-2">
              <div>
                <div className="text-[10px] uppercase tracking-wide text-muted/70 mb-1">Hand it to</div>
                <select value={target} onChange={(e) => setTarget(e.target.value)}
                  className="w-full rounded-md bg-bg border border-line px-2 py-1 text-[12px] outline-none">
                  <option value="">— pick a workspace —</option>
                  {roots.map((r) => (
                    <option key={r.id} value={r.id}>{r.name}{r.id === current?.id ? " (open)" : ""}</option>
                  ))}
                </select>
              </div>
              <div className="flex gap-1.5">
                <button className="chip flex-1 justify-center hover:border-brand hover:text-brand disabled:opacity-40"
                  disabled={!ready || !root || !!busy} onClick={() => deliver(false)}
                  title="Attach the marked-up image to that workspace's prompt box, to send when you are ready">
                  {busy === "attach" ? <Loader2 size={11} className="animate-spin" /> : <ClipboardPaste size={11} />}
                  As reference
                </button>
                <button className="chip flex-1 justify-center !border-brand/60 !text-brand hover:!bg-brand/10 disabled:opacity-40"
                  disabled={!ready || !root || !!busy} onClick={() => deliver(true)}
                  title="Send the comment as the prompt, with the marked-up image attached">
                  {busy === "send" ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}
                  Send as prompt
                </button>
              </div>
              <div className="text-[10px] text-muted/60 leading-snug">
                The pins and notes are drawn into the picture, so the agent reads each note beside
                the pixel it is about.
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

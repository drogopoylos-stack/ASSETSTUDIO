// The skills, findable. One long list stopped working at about fifty skills, so both screens now
// share this: a search, an "on only" filter, and the groups the backend sorts the skills into.
//
//   * `rail` is for the wide Settings page: the groups stand in a column and one group fills the rest.
//   * `sections` is for the narrow pane beside the code: the groups fold, one under the other.
//
// A row stays one line until it is opened. The description is the only thing that tells two skills
// apart, so it is not hidden, but fifty full paragraphs are the problem this file exists to solve.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen, Bot, Box, ChevronDown, ChevronRight, Clapperboard, Download, FileText, FolderSync, Gamepad2,
  Image as ImageIcon, LayoutTemplate, Plug, Shapes, Upload,
} from "lucide-react";
import { api } from "../api/client";
import type { Skill, SkillGroup, SkillPackExport } from "../types";
import { cls } from "./ui";
import { groupOf, searchSkills, sectionsOf, toSwitch } from "./skillGroups";

const ICONS: Record<string, typeof Box> = {
  game: Gamepad2, "3d": Box, video: Clapperboard, images: ImageIcon, ui: LayoutTemplate, agents: Bot,
  integrations: Plug, documents: FileText, notes: BookOpen, other: Shapes,
};
const iconOf = (id: string) => ICONS[id] || Shapes;

export function useSkills(feedId = "", agent = "", notify?: (msg: string, tone: "ok" | "danger") => void) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [groups, setGroups] = useState<SkillGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const load = () => {
    setLoading(true);
    return api.skillsList(feedId, agent)
      .then((r) => { setSkills(r.skills || []); setGroups(r.groups || []); setErr(""); })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [feedId, agent]);

  const apply = async (sk: Skill, enabled: boolean) => {
    const r = await api.skillToggle(sk.id, enabled, feedId, agent);
    setSkills((ss) => ss.map((x) => (x.id === sk.id ? { ...x, enabled: r.enabled, state: r.state } : x)));
    return r.enabled;
  };
  const fail = (e: any) => {
    const msg = String(e?.message || e);
    setErr(msg);
    notify?.(msg, "danger");
  };
  const toggle = async (sk: Skill) => {
    setBusy(sk.id);
    try {
      const on = await apply(sk, !sk.enabled);
      notify?.(`${sk.name} ${on ? "enabled" : "disabled"}`, "ok");
    } catch (e) { fail(e); } finally { setBusy(""); }
  };
  // One after the other: each toggle rewrites settings.json, and two at once would lose one.
  const setMany = async (list: Skill[], enabled: boolean, label: string) => {
    const todo = toSwitch(list, enabled);
    if (!todo.length) return;
    setBusy("group:" + label);
    try {
      for (const sk of todo) await apply(sk, enabled);
      notify?.(`${label}: ${todo.length} ${enabled ? "enabled" : "disabled"}`, "ok");
    } catch (e) { fail(e); } finally { setBusy(""); }
  };
  return { skills, groups, loading, busy, err, load, toggle, setMany };
}
export type SkillsLib = ReturnType<typeof useSkills>;

function SkillRow({ sk, lib, open, onOpen, groupLabel }: {
  sk: Skill; lib: SkillsLib; open: boolean; onOpen: () => void; groupLabel?: string;
}) {
  const dead = lib.busy === sk.id || lib.busy.startsWith("group:");
  return (
    <div className={cls("flex items-start gap-2 px-1.5 py-1 rounded-md hover:bg-panel2/60", open && "bg-panel2/40")}>
      <button type="button" onClick={onOpen} aria-expanded={open}
        className="min-w-0 flex-1 text-left rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className={cls("text-[13px] font-medium", !sk.enabled && "text-muted")}>{sk.name}</span>
          {groupLabel && <span className="chip !py-0 text-[9px] text-muted">{groupLabel}</span>}
          {sk.origin === "local" && (
            <span className="chip !py-0 text-[9px] text-muted"
              title="This skill is not in the Studio download. An exported pack carries it to another computer.">this PC only</span>
          )}
          {sk.needs?.map((n) => (
            <span key={n} className="chip !py-0 text-[9px] text-warn"
              title={`This skill calls the MCP server "${n}", and it is not set up on this computer.`}>needs {n}</span>
          ))}
          {sk.scope && !["personal", "shared"].includes(sk.scope) && (
            <span className="chip !py-0 text-[9px] text-muted">{sk.scope}</span>
          )}
        </span>
        <span className={cls("block text-[11px] text-muted leading-snug mt-0.5", !open && "truncate")}>{sk.description}</span>
        {open && (sk.created || sk.updated) && (
          <span className="block text-[10px] text-muted/80 mt-1">
            {sk.created && <>added {sk.created}</>}
            {sk.updated && sk.updated !== (sk.created || "").slice(0, 10) && <> · updated {sk.updated}</>}
          </span>
        )}
      </button>
      <button type="button" role="switch" aria-checked={sk.enabled} aria-label={`${sk.name}: ${sk.enabled ? "on" : "off"}`}
        disabled={dead} onClick={() => lib.toggle(sk)} title={sk.enabled ? "Switch off" : "Switch on"}
        className="shrink-0 mt-0.5 rounded-full disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">
        <span className={cls("flex items-center h-5 w-9 rounded-full px-0.5 transition-colors",
          sk.enabled ? "bg-brand-600 justify-end" : "bg-line justify-start")}>
          <span className="h-4 w-4 rounded-full bg-white shadow" />
        </span>
      </button>
    </div>
  );
}

function GroupSwitches({ skills, label, lib }: { skills: Skill[]; label: string; lib: SkillsLib }) {
  const anyOff = toSwitch(skills, true).length > 0;
  const anyOn = toSwitch(skills, false).length > 0;
  const btn = "text-[10px] text-muted hover:text-text disabled:opacity-40 px-1 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-brand";
  return (
    <span className="shrink-0 flex items-center gap-0.5">
      <button type="button" className={btn} disabled={!anyOff || !!lib.busy} onClick={() => lib.setMany(skills, true, label)}
        aria-label={`Switch on every skill in ${label}`}>all on</button>
      <button type="button" className={btn} disabled={!anyOn || !!lib.busy} onClick={() => lib.setMany(skills, false, label)}
        aria-label={`Switch off every skill in ${label}`}>all off</button>
    </span>
  );
}

export function SkillsBrowser({ lib, layout, empty }: {
  lib: SkillsLib;
  layout: "rail" | "sections";
  empty: React.ReactNode;
}) {
  const [q, setQ] = useState("");
  const [onlyOn, setOnlyOn] = useState(false);
  const [picked, setPicked] = useState("all");
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set());
  const [opened, setOpened] = useState("");

  const pool = useMemo(() => (onlyOn ? lib.skills.filter((s) => s.enabled) : lib.skills), [lib.skills, onlyOn]);
  const sections = useMemo(() => sectionsOf(pool, lib.groups), [pool, lib.groups]);
  const everyGroup = useMemo(() => sectionsOf(lib.skills, lib.groups), [lib.skills, lib.groups]);
  const hits = useMemo(() => searchSkills(pool, lib.groups, q), [pool, lib.groups, q]);
  const searching = q.trim().length > 0;
  const labelOf = (id: string) => lib.groups.find((g) => g.id === id)?.label || "Other";
  const on = lib.skills.filter((s) => s.enabled).length;

  const row = (sk: Skill, withGroup = false) => (
    <SkillRow key={sk.id} sk={sk} lib={lib} open={opened === sk.id} onOpen={() => setOpened(opened === sk.id ? "" : sk.id)}
      groupLabel={withGroup ? labelOf(groupOf(sk)) : undefined} />
  );

  const nothing = <div className="text-xs text-muted py-3 px-1.5">Nothing matches that.</div>;
  const body = () => {
    if (searching) return hits.length ? hits.map((s) => row(s, true)) : nothing;
    if (layout === "rail" && picked !== "all") {
      const sec = sections.find((s) => s.group.id === picked);
      if (!sec) return nothing;
      return (
        <>
          <div className="flex items-center gap-2 px-1.5 pb-1 mb-1 border-b border-line">
            <span className="text-xs font-medium flex-1 min-w-0 truncate">{sec.group.label}</span>
            <GroupSwitches skills={sec.skills} label={sec.group.label} lib={lib} />
          </div>
          {sec.skills.map((s) => row(s))}
        </>
      );
    }
    if (!sections.length) return nothing;
    return sections.map((sec) => {
      const Icon = iconOf(sec.group.id);
      // With "on only" there are few rows and the point is to read them, so nothing is folded.
      const open = onlyOn || unfolded.has(sec.group.id);
      const flip = () => setUnfolded((f) => {
        const n = new Set(f);
        if (n.has(sec.group.id)) n.delete(sec.group.id); else n.add(sec.group.id);
        return n;
      });
      return (
        <div key={sec.group.id} className="border-b border-line/60 last:border-b-0">
          <div className="flex items-center gap-1">
            <button type="button" onClick={flip} aria-expanded={open}
              className="min-w-0 flex-1 flex items-center gap-1.5 py-1.5 px-1 text-left text-xs rounded hover:bg-panel2/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand">
              {open ? <ChevronDown size={12} className="shrink-0 text-muted" /> : <ChevronRight size={12} className="shrink-0 text-muted" />}
              <Icon size={13} className={cls("shrink-0", sec.on ? "text-brand" : "text-muted")} />
              <span className="font-medium truncate">{sec.group.label}</span>
              <span className="ml-auto pl-2 shrink-0 tabular-nums text-[10px] text-muted">
                {onlyOn ? sec.on : `${sec.on}/${sec.skills.length}`}
              </span>
            </button>
            {open && <GroupSwitches skills={sec.skills} label={sec.group.label} lib={lib} />}
          </div>
          {open && <div className="pb-1.5">{sec.skills.map((s) => row(s))}</div>}
        </div>
      );
    });
  };

  if (lib.loading && !lib.skills.length) return <div className="text-xs text-muted py-3 px-1">loading…</div>;
  if (!lib.skills.length) return <div className="text-xs text-muted py-3 px-1 leading-relaxed">{empty}</div>;

  const tools = (
    <div className="flex items-center gap-2 mb-2">
      <input className="input flex-1 min-w-0 text-sm" value={q} placeholder="Search name, purpose or category"
        aria-label="Search skills" onChange={(e) => setQ(e.target.value)} />
      <button type="button" aria-pressed={onlyOn} onClick={() => setOnlyOn((v) => !v)}
        className={cls("chip shrink-0 tabular-nums focus:outline-none focus-visible:ring-2 focus-visible:ring-brand",
          onlyOn ? "!bg-brand-600 !border-brand-600 text-white" : "hover:text-text")}
        title="Show only the skills that are on">On · {on}</button>
    </div>
  );
  const errLine = lib.err && <div className="text-xs text-danger mb-2">{lib.err}</div>;

  if (layout === "sections") {
    return <>{tools}{errLine}<div>{body()}</div></>;
  }

  const railBtn = (id: string, label: string, count: string, Icon: typeof Box, lit: boolean) => (
    <button key={id} type="button" aria-current={!searching && picked === id ? "true" : undefined}
      onClick={() => { setPicked(id); setQ(""); }}
      className={cls("w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-brand",
        !searching && picked === id ? "bg-panel2 text-text" : "text-muted hover:bg-panel2/60 hover:text-text")}>
      <Icon size={14} className={cls("shrink-0", lit && "text-brand")} />
      <span className="min-w-0 flex-1 leading-snug">{label}</span>
      <span className="shrink-0 tabular-nums text-[10px]">{count}</span>
    </button>
  );
  return (
    <div className="flex gap-4 items-start">
      <nav aria-label="Skill categories" className="w-52 shrink-0 space-y-0.5">
        {railBtn("all", "All skills", `${on}/${lib.skills.length}`, Shapes, on > 0)}
        {everyGroup.map((s) => railBtn(s.group.id, s.group.label, `${s.on}/${s.skills.length}`, iconOf(s.group.id), s.on > 0))}
      </nav>
      <div className="min-w-0 flex-1">
        {tools}
        {errLine}
        <div>{body()}</div>
      </div>
    </div>
  );
}

// Settings only: the block that answers "what happens on another computer".
export function SkillsTransfer({ lib }: { lib: SkillsLib }) {
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [pack, setPack] = useState<SkillPackExport | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  const travel = lib.skills.filter((s) => s.origin === "bundled").length;
  const local = lib.skills.filter((s) => s.origin === "local").length;
  const run = async (what: string, job: () => Promise<void>) => {
    setBusy(what); setErr(""); setMsg("");
    try { await job(); lib.load(); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(""); }
  };
  const parts = (r: Record<string, any>, keys: [string, string][]) =>
    keys.map(([k, word]) => (r[k]?.length ? `${r[k].length} ${word}` : "")).filter(Boolean).join(", ") || "nothing to do";

  const sync = () => run("sync", async () => {
    const r = await api.skillsSync();
    const failed = Object.keys(r.failed || {});
    setMsg(parts(r, [["installed", "installed"], ["updated", "updated"], ["kept", "kept with your edits"], ["waiting", "waiting"]])
      + (failed.length ? `. Failed: ${failed.join(", ")}` : "."));
  });
  const exportPack = () => run("export", async () => {
    const r = await api.skillsPackExport();
    setPack(r);
    const skipped = Object.keys(r.skipped || {});
    setMsg(`${r.skills.length} skills are in the pack.`
      + (skipped.length ? ` Left out because they hold a secret: ${skipped.join(", ")}.` : ""));
  });
  const importPack = (file: File) => run("import", async () => {
    const r = await api.skillsPackImport(file, overwrite);
    setMsg(parts(r, [["installed", "installed"], ["replaced", "replaced"], ["kept", "kept as they are"], ["current", "already the same"]]) + ".");
  });

  const btn = "chip hover:text-text inline-flex items-center gap-1.5 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand";
  return (
    <div className="mt-4 pt-3 border-t border-line">
      <div className="text-xs font-medium mb-1">On another computer</div>
      <p className="text-[11px] text-muted leading-relaxed mb-2 max-w-3xl">
        {travel + local > 0 && <><span className="text-text tabular-nums">{travel}</span> skills arrive with the Studio by themselves.{" "}
          <span className="text-text tabular-nums">{local}</span> are on this computer only, and a pack carries them.{" "}</>}
        A pack is private: it holds the notes learned from your own sessions, so keep it off any public place.
        MCP servers and <span className="font-mono">~/.claude/CLAUDE.md</span> are not in it.
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <button type="button" className={btn} disabled={!!busy} onClick={sync}
          title="Install or update the skills of this Studio version. Your own edits stay.">
          <FolderSync size={12} /> {busy === "sync" ? "Syncing…" : "Sync now"}
        </button>
        <button type="button" className={btn} disabled={!!busy} onClick={exportPack}>
          <Download size={12} /> {busy === "export" ? "Packing…" : "Export pack"}
        </button>
        <button type="button" className={btn} disabled={!!busy} onClick={() => picker.current?.click()}>
          <Upload size={12} /> {busy === "import" ? "Importing…" : "Import pack"}
        </button>
        <label className="text-[11px] text-muted inline-flex items-center gap-1.5">
          <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
          replace skills that differ
        </label>
        <input ref={picker} type="file" accept=".zip,application/zip" className="hidden" aria-label="Skill pack file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) importPack(f);
          }} />
      </div>
      <div aria-live="polite">
        {msg && (
          <div className="text-[11px] text-muted mt-2">
            {msg}{" "}
            {pack && (
              <a className="text-brand hover:underline" href={`/api/skills/pack/file/${encodeURIComponent(pack.file)}`} download={pack.file}>
                Download {pack.file}
              </a>
            )}
          </div>
        )}
        {err && <div className="text-[11px] text-danger mt-2">{err}</div>}
      </div>
    </div>
  );
}

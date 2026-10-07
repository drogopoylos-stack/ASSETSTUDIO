import { useEffect, useState } from "react";
import { Check, ExternalLink, Gauge, KeyRound, Loader2, LogIn, RefreshCw, Trash2, X } from "lucide-react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";
import type { UsageAccount, UsageSummary } from "../types";
import { CodexSignIn, useCodex } from "./CodexPanel";
import { cls, Meter, meterFill, meterTone, pollWhileVisible } from "./ui";

const fmtDur = (s: number) => {
  const minutes = Math.max(1, Math.ceil(s / 60));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`;
};
const shortLabel = (key: string) =>
  key === "session" ? "Session (5h)" : key === "weekly" ? "Weekly (7d)"
    : key === "weekly_opus" ? "Opus (7d)" : key === "weekly_sonnet" ? "Sonnet (7d)" : key;
const tinyLabel = (key: string) =>
  key === "session" ? "5h" : key === "weekly" ? "wk" : shortLabel(key);
const money = (n: number, cur: string) =>
  cur === "USD" ? `$${n.toFixed(2)}` : cur === "CNY" ? `¥${n.toFixed(2)}` : `${n.toFixed(2)} ${cur}`;

const LS_PROVIDER = "asset-studio-usage-provider";

/** The accounts the picker offers before the backend has answered, so the menu is never empty. */
const FALLBACK_ACCOUNTS: UsageAccount[] = [
  { id: "claude", name: "Claude", kind: "plan", connect: "claude", connected: true },
  { id: "codex", name: "Codex (ChatGPT)", kind: "plan", connect: "codex", connected: true },
];

/** What a stored key also switches on, said where the key is typed. */
const KEY_ALSO: Record<string, string> = {
  deepseek: "The same key turns on the DeepSeek Harness chat agent.",
  kimi: "The same key turns on the Kimi K3 chat agent.",
  openrouter: "To chat with OpenRouter models too, add OpenRouter in Settings → Models.",
};

/** One meter, whichever list it came from. */
interface Row { key: string; label: string; short: string; percent: number; reset_seconds: number }

/** Every limit this account has, in one list.
 *
 *  `windows` carries the shared buckets — the 5h session and the weekly total. The PER-MODEL
 *  ones are only in `limits`, and a Fable allowance is exactly that: a separate weekly pot,
 *  half the plan's usage on some accounts, which the bar never showed at all.
 *
 *  It used to be readable only from a pill in the far top-right corner, and that pill did not
 *  show Fable either — it took whichever bucket was FULLEST, so with the session at 9% and
 *  Fable at 0% it read "5h 9%". A meter named after one thing and showing another is worse than
 *  no meter. The scoped buckets are drawn here instead, beside the two they belong with.
 *
 *  A scoped bucket is kept even at 0%: an untouched allowance is a fact worth seeing, and
 *  hiding it is how it became invisible in the first place. */
function rowsOf(usage: UsageSummary | null, now: number): Row[] {
  const wins = (usage?.windows || []).filter((w) => w.key === "session" || w.key === "weekly" || w.percent > 0);
  const out: Row[] = wins.map((w) => ({
    key: w.key, label: ["session", "weekly", "weekly_opus", "weekly_sonnet"].includes(w.key) ? shortLabel(w.key) : w.label,
    short: ["session", "weekly"].includes(w.key) ? tinyLabel(w.key) : w.label,
    percent: w.percent, reset_seconds: w.resets_at
      ? Math.max(0, (new Date(w.resets_at).getTime() - now) / 1000) : w.reset_seconds,
  }));
  const seen = new Set(out.map((r) => r.label.toLowerCase()));
  for (const l of usage?.limits || []) {
    if (!l.model) continue;                       // shared buckets are already above
    const label = `${l.model} (7d)`;
    if (seen.has(label.toLowerCase())) continue;  // the window list already had this model
    seen.add(label.toLowerCase());
    // `windows` gets its countdown from the backend; a scoped bucket carries only the timestamp.
    const secs = l.resets_at ? Math.max(0, (new Date(l.resets_at).getTime() - now) / 1000) : 0;
    out.push({ key: `scoped:${l.model}`, label, short: l.model.toLowerCase(),
               percent: l.percent, reset_seconds: secs });
  }
  return out;
}

/** "Claude Pro", "Codex Plus", "DeepSeek API": the plan, with the account's name when the plan
 *  alone does not say whose it is. */
function planLabel(usage: UsageSummary, accounts: UsageAccount[]): string {
  const plan = usage.plan || "";
  const name = (accounts.find((a) => a.id === usage.provider)?.name || "").replace(/\s*\(.*\)$/, "");
  if (!name || plan.toLowerCase().includes(name.toLowerCase())) return plan;
  return plan ? `${name} ${plan}` : name;
}

/** The sign-in or the API key for one account, inline under the bar. */
function ConnectPanel({ account, connected, onDone, onClose }: {
  account: UsageAccount; connected: boolean; onDone: () => void; onClose: () => void;
}) {
  const toast = useStore((s) => s.toast);
  const [busy, setBusy] = useState("");
  const [key, setKey] = useState("");
  const [opened, setOpened] = useState(false);
  const codex = useCodex(account.connect === "codex");
  const codexReady = !!codex.status?.ready;
  useEffect(() => { if (account.connect === "codex" && codexReady && !connected) onDone(); }, [codexReady]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try { await fn(); } catch (e: any) { toast(e?.message || "The request failed", "danger"); }
    setBusy("");
  }
  const btn = "inline-flex items-center gap-1 rounded-md border border-line bg-panel2 px-2 py-1 hover:bg-line disabled:opacity-50";
  const primary = "inline-flex items-center gap-1 rounded-md bg-brand text-white px-2 py-1 hover:brightness-110 disabled:opacity-50";

  return (
    <div className="mt-1.5 max-w-xl rounded-lg border border-line bg-panel2/60 px-2.5 py-2 text-xs space-y-1.5 font-sans">
      <div className="flex items-center gap-2">
        <span className="font-medium text-text">{connected ? `${account.name}: change the connection` : `Connect ${account.name}`}</span>
        <button className="ml-auto text-muted hover:text-text" title="Close" onClick={onClose}><X size={13} /></button>
      </div>
      {account.hint && <div className="text-muted leading-snug">{account.hint}</div>}

      {account.connect === "claude" && (
        <div className="flex flex-wrap items-center gap-1.5">
          <button className={primary} disabled={!!busy} onClick={() => act("login", async () => {
            const r = await api.claudeLogin();
            if (!r.ok) throw new Error(r.error || "Could not open the Claude login");
            setOpened(true);
          })}>{busy === "login" ? <Loader2 size={12} className="animate-spin" /> : <LogIn size={12} />} Sign in to Claude</button>
          <button className={btn} disabled={!!busy} onClick={() => act("check", async () => {
            const r = await api.claudeRecheck();
            if (r.logged_in && !r.needs_login) { toast("Claude is signed in", "ok"); onDone(); }
            else toast(r.reason || "Claude is not signed in yet", "danger");
          })}>{busy === "check" ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} I have signed in</button>
          {opened && <span className="text-muted">Finish the login in the browser window, then press "I have signed in".</span>}
        </div>
      )}

      {account.connect === "codex" && (
        !codex.status ? <div className="text-muted flex items-center gap-1"><Loader2 size={11} className="animate-spin" /> asking Codex…</div>
          : codexReady ? (
            <div className="text-muted">
              Codex is signed in{codex.status.email ? ` as ${codex.status.email}` : ""}. To sign out or change the account,
              open Settings → Coding agents.
            </div>
          ) : <CodexSignIn status={codex.status} />
      )}

      {account.connect === "key" && account.key && (
        <>
          <div className="flex items-center gap-1.5">
            <input type="password" autoFocus value={key} onChange={(e) => setKey(e.target.value)}
              placeholder={`${account.name} API key`} className="input !py-1 text-xs flex-1 min-w-0"
              onKeyDown={(e) => { if (e.key === "Enter" && key.trim()) (e.currentTarget.nextSibling as HTMLButtonElement | null)?.click(); }} />
            <button className={primary} disabled={!key.trim() || !!busy} onClick={() => act("save", async () => {
              await api.setKey(account.key!, key.trim());
              setKey("");
              toast(`${account.name} key saved`, "ok");
              onDone();
            })}>{busy === "save" ? <Loader2 size={11} className="animate-spin" /> : <KeyRound size={11} />} Save key</button>
            {connected && (
              <button className={btn} disabled={!!busy} title="Delete the stored key" onClick={() => act("delete", async () => {
                if (!confirm(`Delete the ${account.name} API key?`)) return;
                await api.deleteKey(account.key!);
                toast(`${account.name} key deleted`, "info");
                onDone();
              })}><Trash2 size={11} /> Delete</button>
            )}
          </div>
          <div className="text-muted leading-snug">
            The key is stored in the Windows keychain. The Studio uses it only to read the balance
            and for the chat agent. {KEY_ALSO[account.id] || ""}
            {account.key_url && <> <a className="inline-flex items-center gap-0.5 underline text-text" href={account.key_url}
              target="_blank" rel="noreferrer">Get a key <ExternalLink size={10} /></a></>}
          </div>
        </>
      )}
    </div>
  );
}

// Self-contained plan-usage bar: every account in one menu, refreshed every 60s.
export function UsageLimitsBar({ className, cli }: { className?: string; cli?: boolean }) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [accounts, setAccounts] = useState<UsageAccount[]>(FALLBACK_ACCOUNTS);
  const [provider, setProvider] = useState(() => {
    try { return localStorage.getItem(LS_PROVIDER) || "auto"; } catch { return "auto"; }
  });
  const [connectOpen, setConnectOpen] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [now, setNow] = useState(Date.now);

  const loadAccounts = () => api.usageAccounts().then((r) => {
    if (r.accounts?.length) setAccounts(r.accounts);
  }).catch(() => {});
  useEffect(() => { void loadAccounts(); }, []);

  useEffect(() => {
    let alive = true;
    let first = nonce > 0;              // after a connect, read past the backend's cache once
    setUsage(null);
    const stop = pollWhileVisible(() => {
      const force = first;
      first = false;
      return api.missionUsage(provider, force).then((data) => {
        if (alive) { setUsage(data); setNow(Date.now()); }
      }).catch((error) => {
        if (alive) setUsage((prev) => ({ ...(prev || { available: false, windows: [] }),
          stale: true, error: String(error.message || error) }));
      });
    }, 60000);
    return () => { alive = false; stop(); };
  }, [provider, nonce]);
  useEffect(() => pollWhileVisible(() => setNow(Date.now()), 30000), []);

  // A stored choice that no longer exists falls back to Auto.
  useEffect(() => {
    if (provider !== "auto" && accounts !== FALLBACK_ACCOUNTS && !accounts.some((a) => a.id === provider)) pick("auto");
  }, [accounts]);

  function pick(next: string) {
    setProvider(next);
    setConnectOpen(false);
    try { localStorage.setItem(LS_PROVIDER, next); } catch { /* private mode */ }
  }
  function connected() {
    setConnectOpen(false);
    void loadAccounts();
    setNonce((n) => n + 1);
  }

  const shownId = provider === "auto" ? usage?.provider || "" : provider;
  const shown = accounts.find((a) => a.id === shownId) || null;
  const needsConnect = !!usage && !usage.available && !!usage.needs_connect;
  const plans = accounts.filter((a) => a.kind === "plan");
  const balances = accounts.filter((a) => a.kind === "balance");
  const opt = (a: UsageAccount) => (
    <option key={a.id} value={a.id}>{a.name}{a.connected ? "" : " · not connected"}</option>
  );

  const accountPicker = (
    <select aria-label="Plan usage account" title="Choose which account's usage to show"
      className="bg-panel text-muted border border-line rounded px-1 py-0.5 text-[11px]"
      value={provider} onFocus={() => { void loadAccounts(); }} onChange={(event) => pick(event.target.value)}>
      <option value="auto">Auto</option>
      <optgroup label="Plans (5h / weekly)">{plans.map(opt)}</optgroup>
      {balances.length > 0 && <optgroup label="API keys (balance)">{balances.map(opt)}</optgroup>}
    </select>
  );

  // Connect (when the account is not signed in) or change the key (when it is one).
  const connectButton = shown && (needsConnect || (shown.connect === "key" && shown.connected)) ? (
    <button className={cls("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]",
      needsConnect ? "border-brand text-brand hover:bg-brand/10" : "border-line text-muted hover:text-text")}
      title={needsConnect ? `Connect ${shown.name}` : `Change the ${shown.name} API key`}
      onClick={() => setConnectOpen((v) => !v)}>
      {needsConnect ? <LogIn size={11} /> : <KeyRound size={11} />}
      {needsConnect ? (shown.connect === "key" ? "Add API key" : "Sign in") : "Key"}
    </button>
  ) : null;
  const refreshButton = (
    <button className="text-muted hover:text-text" title="Read the usage again now"
      onClick={() => { void loadAccounts(); setNonce((n) => n + 1); }}><RefreshCw size={11} /></button>
  );
  const panel = connectOpen && shown ? (
    <ConnectPanel account={shown} connected={shown.connected && !needsConnect} onDone={connected} onClose={() => setConnectOpen(false)} />
  ) : null;

  const wins = rowsOf(usage, now);
  const bal = usage?.available ? usage.balance : undefined;

  // The console rendering: one row of characters, no chrome. A shaded <div> in a terminal is the
  // one thing that gives the skin away, and this is the same information without it.
  if (cli) {
    return (
      <div className={cls("px-3 py-1 bg-panel border-b border-line text-[11px] font-mono", className)}>
        <div className="flex items-center gap-x-4 gap-y-0.5 flex-wrap">
          <span className="text-brand shrink-0">✻ usage</span>
          {accountPicker}
          {usage?.available && usage.plan && <span className="text-muted/70">{planLabel(usage, accounts).toLowerCase()}</span>}
          {!usage ? <span className="text-muted">reading…</span>
            : !usage.available ? (
              <span className="text-muted truncate" title={usage.error || ""}>
                {needsConnect ? `${usage.error || "not connected"}` : "unavailable — retrying"}
              </span>
            ) : bal ? (
              <span className="whitespace-nowrap" title={bal.detail || ""}>
                <span className="text-muted">balance </span>
                {bal.limit != null && bal.limit > 0 && <Meter pct={(bal.amount / bal.limit) * 100} tone={meterTone(100 - (bal.amount / bal.limit) * 100)} />}
                <span className="text-text"> {usage.balance_unknown ? `${money(bal.used || 0, bal.currency)} used` : `${money(bal.amount, bal.currency)} left`}</span>
                {bal.ok === false && <span className="text-danger"> · empty</span>}
              </span>
            ) : usage.note && !wins.length ? (
              <span className="text-muted truncate" title={usage.note}>{usage.note}</span>
            ) : wins.map((w) => {
              const pct = Math.max(0, Math.min(100, w.percent));
              return (
                <span key={w.key} className="whitespace-nowrap"
                  title={`${w.label} — ${w.percent}% used${w.reset_seconds > 0 ? `, resets in ${fmtDur(w.reset_seconds)}` : ""}`}>
                  <span className="text-muted">{w.short} </span>
                  <Meter pct={100 - pct} tone={meterTone(pct)} />
                  <span className="text-text"> {Number((100 - pct).toFixed(1))}% left</span>
                  {w.reset_seconds > 0 && <span className="text-muted/60"> · resets {fmtDur(w.reset_seconds)}</span>}
                </span>
              );
            })}
          {usage?.stale && <span className="text-muted/60" title={usage.error || ""}>[cached]</span>}
          {connectButton}
          {refreshButton}
        </div>
        {panel}
      </div>
    );
  }

  return (
    <div className={cls("px-3 py-1.5 bg-panel border-b border-line", className)}>
      <div className="flex items-center gap-x-5 gap-y-1 flex-wrap text-xs">
        <span className="font-semibold flex items-center gap-1.5">
          <Gauge size={14} className="text-brand" /> Plan usage
          {accountPicker}
          {usage?.available && usage.plan && <span className="chip ml-0.5">{planLabel(usage, accounts)}</span>}
          {usage?.available && usage.stale && <span className="chip text-muted/70" title={usage.error || "rate-limited — showing last known values"}>cached</span>}
          {refreshButton}
          {connectButton}
        </span>
        {!usage ? (
          <span className="text-muted">loading…</span>
        ) : !usage.available ? (
          <span className="text-muted truncate" title={usage.error || ""}>
            {needsConnect ? (usage.error || `${shown?.name || "This account"} is not connected.`)
              : "usage temporarily unavailable — retrying in the background"}
          </span>
        ) : bal ? (
          <div className="flex items-center gap-2" title={bal.detail || ""}>
            <span className="text-muted whitespace-nowrap">Balance</span>
            {bal.limit != null && bal.limit > 0 && (() => {
              const usedPct = Math.max(0, Math.min(100, 100 - (bal.amount / bal.limit) * 100));
              return (
                <div className="w-16 h-2.5 rounded-sm bg-panel2 overflow-hidden shrink-0" title={`${Math.round(usedPct)}% used`}>
                  <div className={cls("h-full transition-all", meterFill(usedPct))} style={{ width: `${100 - usedPct}%` }} />
                </div>
              );
            })()}
            <span className="font-mono text-text whitespace-nowrap">
              {usage.balance_unknown ? `${money(bal.used || 0, bal.currency)} used` : `${money(bal.amount, bal.currency)} left`}
            </span>
            {bal.limit != null && !usage.balance_unknown && <span className="text-muted/60 whitespace-nowrap">of {money(bal.limit, bal.currency)}</span>}
            {bal.ok === false && <span className="chip text-danger">empty — top up</span>}
            {usage.balance_unknown && bal.detail && <span className="text-muted/60 truncate">{bal.detail}</span>}
          </div>
        ) : usage.note && !wins.length ? (
          <span className="text-muted truncate" title={usage.note}>{usage.note}</span>
        ) : (
          wins.map((w) => {
            const pct = Math.max(0, Math.min(100, w.percent));
            return (
              <div key={w.key} className="flex items-center gap-2">
                <span className="text-muted whitespace-nowrap">{w.label}</span>
                {/* Shorter and chunkier than it was (112x6 -> 64x10), which is how the console
                    look draws it: at six pixels tall a bar reads as a hairline and its colour
                    barely registers, and 112px of track for a two-digit number was width the
                    row could not spare once there were three of them. */}
                <div className="w-16 h-2.5 rounded-sm bg-panel2 overflow-hidden shrink-0"
                  title={`${pct}% used`}>
                  <div className={cls("h-full transition-all", meterFill(pct))} style={{ width: `${100 - pct}%` }} />
                </div>
                <span className="font-mono text-text whitespace-nowrap">{Number((100 - pct).toFixed(1))}% left</span>
                {w.reset_seconds > 0 && <span className="text-muted/60 whitespace-nowrap">· resets {fmtDur(w.reset_seconds)}</span>}
              </div>
            );
          })
        )}
      </div>
      {panel}
    </div>
  );
}

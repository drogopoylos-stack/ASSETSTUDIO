// Four components, one question, one request.
//
// Counted on the running app across two workspace switches: 26 requests for the context of a
// single project, 24 for the agent list, 51 for live-status. Not a loop — ContextMeter,
// ModelBadge, WorkingPulse and the bottom bar each poll for their own meter, on their own timer.
//
// A browser allows six connections per origin, so duplicates are not free even when each is fast:
// they push the file tree and the conversation behind them in the queue. A GET already in flight
// is therefore shared, and its answer handed to anyone asking again inside a 900ms window —
// shorter than the fastest of those timers, so nothing refreshes any less often.
//
// The rules that matter are all about what must NOT be shared: another project, another endpoint,
// a POST, and a failure.
//
// Run: npm run test:pollshare

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}

// ---------------------------------------------------------------- a browser, near enough

const calls: string[] = [];
let failNext = false;

(globalThis as any).window = {
  setTimeout: () => 0,
  clearTimeout: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
};
(globalThis as any).fetch = async (path: string) => {
  calls.push(String(path));
  if (failNext) { failNext = false; throw new Error("network down"); }
  return { ok: true, status: 200, json: async () => ({ seen: calls.length }) };
};

const { api } = await import("../src/api/client");

const count = (needle: string) => calls.filter((c) => c.includes(needle)).length;

// ---------------------------------------------------------------- what IS shared

console.log("Four askers, one request");

await Promise.all([api.liveStatus(), api.liveStatus(), api.liveStatus(), api.liveStatus()]);
ok("four callers at once make one request", count("/api/mission/live-status") === 1,
   String(count("/api/mission/live-status")));

const a = await api.missionContext("proj-one");
const b = await api.missionContext("proj-one");
ok("a second ask inside the window is answered from the first",
   count("proj-one") === 1, String(count("proj-one")));
ok("...with the same answer", JSON.stringify(a) === JSON.stringify(b));

await api.missionAgents();
await api.missionAgents();
ok("the agent list is shared too", count("/api/mission/agents") === 1,
   String(count("/api/mission/agents")));

await api.engineState();
await api.engineState();
ok("and the engine pill", count("/api/engine/state") === 1, String(count("/api/engine/state")));

// ---------------------------------------------------------------- what is NOT

console.log("\nAnd what must never be");

await api.missionContext("proj-two");
ok("ANOTHER PROJECT is its own question", count("proj-two") === 1 && count("proj-one") === 1,
   "one=" + count("proj-one") + " two=" + count("proj-two"));

const before = calls.length;
await api.systemStats();
await api.systemStats();
await api.systemStats();
ok("an endpoint that is not on the list is never shared", calls.length - before === 3,
   String(calls.length - before));

const beforePost = calls.length;
await api.reloadProviders().catch(() => {});
await api.reloadProviders().catch(() => {});
ok("a POST is an action, and two of them are two", calls.length - beforePost === 2,
   String(calls.length - beforePost));

console.log("\nA failure is not remembered");
// PAST THE WINDOW FIRST. Inside it the next ask is answered by the promise already in hand
// and never reaches fetch at all, which would arm the failure for whatever asked next.
await new Promise((r) => setTimeout(r, 1000));
failNext = true;
await api.liveStatus().catch(() => {});
const afterFail = count("/api/mission/live-status");
await api.liveStatus();
ok("the next ask after a failure goes out again",
   count("/api/mission/live-status") === afterFail + 1,
   afterFail + " then " + count("/api/mission/live-status"));

// ---------------------------------------------------------------- the window closes

console.log("\nThe window is a poll window, not a cache");
const n0 = count("/api/engine/state");
await new Promise((r) => setTimeout(r, 1000));
await api.engineState();
ok("after the window it asks again", count("/api/engine/state") === n0 + 1,
   n0 + " then " + count("/api/engine/state"));

// ----------------------------------------------------------------

console.log("\n  " + pass + " passed, " + fails.length + " failed");
for (const f of fails) console.log("  FAIL  " + f);
process.exit(fails.length ? 1 : 0);

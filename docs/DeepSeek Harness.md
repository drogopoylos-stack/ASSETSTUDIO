# DeepSeek Harness and independent agent panes

The native official `dsh` runtime is installed with `deepseek-harness-sdk==0.1.5rc1`
in the backend venv. It is a separate agent (`deepseek-harness`), not the older
DeepSeek provider preset that sends requests through Claude Code.

## Use

1. Settings → API Keys → **DeepSeek Harness**: save the DeepSeek API key.
2. In Workspace, use the robot button by the prompt box to select **DeepSeek Harness**.
3. The adjacent settings button offers four rows, and **all four are DeepSeek V4.1 Flash**:
   `deepseek-flash` (vision, and the default), its legacy vision id, and the text-only legacy and
   Pro ids. See [Which model answers](#which-model-answers) for why they all carry that name.
4. Use the **+** button in an agent pane's header to open another agent in the same
   folder. The new pane starts with Codex, or Claude when the original pane is Codex.
   Each pane's robot button chooses its own agent. Resize with the divider.

Pane choices persist per pane and folder. Feeds, pending prompts, saved-conversation
selection, busy polling and Stop target the chosen engine. Codex and Claude can work
in the same folder simultaneously; their edits still affect the same files.

## Runtime behavior

Credentials use the Studio keychain (`deepseek`), with compatibility for an existing
`chat:deepseek` key and `DEEPSEEK_API_KEY`. They are never returned to the frontend.
The SDK currently runs in **Full access**: commands and edits use the user's own
permissions. The Studio does not expose unsupported approval modes for this connection.
Co-run companion checkboxes are unavailable for this native adapter; use another pane.
Text prompts and file references are supported, and so are **images — on the right row.**

### Which model answers

**`deepseek-flash` is DeepSeek V4.1 Flash**, and that is read from the runtime's own catalogue
rather than recalled. `@deepseek-ai/dsh-llm-deepseek` declares its `DEFAULT_MODELS` with that row's
`name` set to `DeepSeek-V41-Flash`, and the release notice of 2026-09-10 says "Set your model to
`deepseek-flash`" — V4.1-Flash has no id of its own on the API. The release also retires V4-Flash and
V4-Flash-Vision-Exp and begins phasing out V4-Pro (from 04:00 UTC 2026-09-14), routing all of them
to V4.1-Flash upstream. So every row in the picker is one model, and the names now say so; the ids
are what tell the rows apart.

The Studio called that row "DeepSeek Flash" until 2026-10-05, which hid the version completely —
V4.1 Flash was reported as *missing from the model bar* while its row had been there all along.
`backend/deepseek_test.py::test_05b` now pins the label against the runtime binary's catalogue, so a
rename upstream fails the test instead of silently making the picker lie.

| row | name in the picker | input |
|---|---|---|
| `deepseek-flash` | DeepSeek V4.1 Flash · vision | text + image |
| `deepseek-v4-flash-vision-exp` | DeepSeek V4.1 Flash · legacy vision id | text + image |
| `deepseek-v4-flash` | DeepSeek V4.1 Flash · legacy id, text only | text only |
| `deepseek-v4-pro` | DeepSeek V4.1 Flash · Pro id, text only | text only |

**Which row SEES is a different question from which model answers**, and it is the runtime's: only
the rows it catalogues with `inputModalities: ["text","image"]` may carry a picture, because it drops
an attached image (`model.input.includes("image")`) before the request for the others. The picker
used to offer only the text-only pair — which is the whole reason the paperclip looked broken.
`default` is now `deepseek-flash`, so a screenshot simply works; a text-only row is refused with a
sentence naming the fix instead of spending a turn on a placeholder. Measured blind on 2026-10-04 —
image inlined, empty working directory, no path named, no tool used, so nothing but vision could
answer: `deepseek-flash` → "A blue square and a red circle, with the number 42"; the legacy vision id
matched it; both text-only rows reported the image as omitted. The capability list lives in one
constant, `deepseek_session.VISION_MODELS`.

Re-confirmed 2026-10-05 through the Studio's OWN adapter rather than a hand-built request, because
"the paperclip works" is a claim about the composer's path: `deepseek_session.send(pid, …,
model="default", images=[png])` with the picture inlined, an empty throwaway CWD and no path named
answered *"A blue square on the left and a red circle on the right, with the number 42 between
them."* So the picker's **default** row — what a pane sends when nobody opens the menu — needs no
switch to a "vision" entry: that switch IS the default. The only way to lose it is to pin one of the
two rows the menu labels `text only`, which is refused with the sentence that names the vision row.
Assistant messages and tool events appear as they commit, rather than token by token.

DSH logs and profiles live under `data/deepseek-harness-home/runtime/`. A compatible
timeline lives under `data/deepseek-harness-home/projects/`. Live runtimes are reused
between turns. SDK 0.1.5 cannot attach an existing durable session id after its process
exits. After a backend restart, Stop, or model change, the adapter creates a fresh
internal SDK session and supplies the previous visible messages and tool results as
explicit conversation context. The Studio's conversation id remains stable. Reasoning
is not included in this restoration. New conversation starts without that context.

Optional session-log uploads and telemetry are disabled in the integration. Normal
DeepSeek requests still include the conversation and tools needed for the user's task.

## The workbench follows the engine

Every main-UI control used to assume Claude: the activity bar's panels read `~/.claude`, sent to
Claude's session, or switched the pane to **Claude Code**. On a Codex or DeepSeek pane they showed
the wrong conversation or nothing at all. All of it is now keyed on the engine the pane is set to.

| control | what it reads | what was wrong |
|---|---|---|
| Prompt history | that engine's conversation (`<engine>--<folder>`) | passed the bare folder id, so a DeepSeek pane listed **Claude's** chat |
| Checkpoints | the project folder — one list, every engine | **never worked at all** (below) |
| Phases | `todo_write` for DeepSeek, `TodoWrite`/`TaskCreate` for Claude, plan for Codex | a DeepSeek pane showed an empty plan while its transcript held 14 phase writes |
| Skills | the shared library, mounted into DSH | read `~/.claude/skills` for every engine and said "Claude" |
| Subagents | the pane's own engine | resolved the folder's engine, so two panes on one folder crossed |
| `/compact` | `deepseek_session._compact` | sent the literal words "/compact" to the model |
| `/subtask` · `/fork` | Claude only, and now says so | **silently switched the pane to Claude Code** |
| editing a past message | truncate + resend through the DeepSeek adapter | truncated the DSH transcript, then drove the **Claude CLI** at DeepSeek's session home |
| Mission Control card | the card's engine | feed pinned to Claude; quick answers always sent to Claude |

**Checkpoints had never worked — for any engine.** `checkpoints._proj_root` matched the id the
Workspace uses against the project index with an exact string compare, and the Workspace lowercases
the drive letter (`workspace._claude_id`) while the index keeps the casing Claude Code wrote. On
Windows the two never met, so `create()` returned *"no project folder for this session"* into a
background thread nobody read. `data/checkpoints/` held no project directory at all after weeks of
turns, and the panel was empty because there was never anything in it. The lookup now case-folds
and falls back to the Studio's own folder list, so a project only Codex or DeepSeek has touched
snapshots too. The pre-turn snapshot also moved to ONE door, above the engine dispatch in
`cc_session.send` — it used to live inside the Claude branch and, separately, the Codex one, so
DeepSeek's adapter (which returns before Claude's line) never took one. Each checkpoint now records
which engine was about to write, and the panel shows it.

**Phases reach further back than the feed's byte budget.** `todo_write` is the DSH runtime's
whole-list phase tool (its own catalog: `ask_user_question, bash, cordis_*, edit, glob, grep,
present, read, read_image, skill, todo_write, web_fetch, web_search, write`), and it carries the
same `{content, status}` shape as Claude's `TodoWrite`. Only Claude's spelling was parsed, so a
DeepSeek pane showed an empty plan — and because the plan is written once and the turn keeps
appending, the last one sat 4.4 MB behind the end of a real transcript, outside the 512 KB tail the
panel reads. `_last_phase_call` scans back over a byte budget and parses only the lines that name
the tool, which costs ~12 ms on that file.

**Skills are one library, mounted twice.** The DSH runtime discovers `<root>/<name>/SKILL.md` (one
level deep) from, in rank order: `<project>/.dsh/skills`, `<project>/.agents/skills`,
`customSkillDirs`, `<dshHome>/skills`, `<agentsHome>/skills`, and a bundled root — read out of the
installed `@deepseek-ai/dsh-skill-filesystem`, not guessed. The Studio passes its own
`~/.claude/skills` as a `customSkillDirs` root in the `--patch` overlay (`_patch_text`), and its
on/off switch is the same `disable-model-invocation` frontmatter key the runtime reads (it surfaces
as `modelInvocable`). So one set of files, one switch, both engines. Codex has no per-task skill
mechanism and the panel says so instead of listing somebody else's.

Proven with a real turn, 2026-10-05: one `deepseek-flash` request in a throwaway directory with no
other skill root in reach, asked to list its skill catalog, answered *"graphify"* — and `graphify`
is the one skill enabled in the shared library. `backend/engine_parity_test.py` pins the wiring
without a model call.

**`/compact`** is the one place the runtime cannot be asked. DSH *has* a real compaction
(`@deepseek-ai/dsh-command-compact` → `ctx.compaction.compactNow`, a model-written summary), but it
is a **client** slash command: the SDK plane this adapter talks to exposes `session/prompt` and
little else (`skills/list` is already "unknown method"). The adapter therefore does what it already
did on a model switch or a Stop — close the live runtime and let the next send rebuild the thread
from `_history`, which is bounded at `_HISTORY_CHARS` (~30k tokens). That is the half of compaction
the bill notices. It reports the number and leaves a line in the transcript rather than pretending.

**The turn ledger is keyed by the feed.** `_bank_turn` wrote the bare folder id while the feed reads
`turns.for_project` under `deepseek-harness--<slug>`, so every DeepSeek answer was banked and never
matched: no tokens, no model and no cost bar under it, while `data/turns.jsonl` grew. Both sides now
use the feed id.

## Verification and activation

`backend/deepseek_test.py` uses the installed official runtime against a local API
fixture: Flash and Pro, continuation, fresh conversation, restart recovery, actual
file-writing tool execution, validation, missing credentials, busy rejection and
actual cancellation. It makes no vendor requests.

`npm.cmd run test:agentpanes` checks isolated choices and split geometry for all
layout presets. `npm.cmd run test:sendprefs` verifies the shared preference readers.

`backend/engine_parity_test.py` (42 checks) and `npm.cmd run test:engines` (28 checks) cover the
workbench half of this document: that the checkpoint folder resolves for every id spelling, that the
pre-turn snapshot sits above the engine dispatch, that both phase-tool spellings parse and that a
real DSH transcript's plan is found outside the tail, that the skill roots and the shared mount are
what each engine actually reads, that `/compact` answers without contacting a model, that a DeepSeek
rewind resends through the DeepSeek adapter, that the ledger key is the feed id — and, on the
frontend side, that the prompt/checkpoint/skills panels are handed the agent-prefixed feed id and the
engine that owns them, that the activity bar's tooltips name that engine, and that `/subtask` cannot
switch a non-Claude pane to Claude Code.

The running installation is `D:\Asset Studio`, separate from the source workspace
`D:\UserFiles\Desktop\kapow\Asset Studio`. Scoped changes were merged into both,
preserving the newer native Codex app-server integration in the installed copy and
the legacy Codex runner in this checkout. The legacy runner uses a separate process
and stdout feed key so its Stop cannot terminate the Claude pane.

The verified installed-copy frontend build is staged in
`D:\Asset Studio\data\tmp\deepseek-panes-dist`. Activation copies this build into
`frontend/dist` and restarts the desktop backend. At preparation time the primary
backend reported active turns and two unkeepered sessions, so activation is pending
user approval instead of interrupting those agents. Source build/typecheck passed.
The two already-unreferenced legacy components, `AgentOutput.tsx` and `CodexAccount.tsx`, whose APIs
were obsolete before this change, were **deleted** on 2026-10-05 — nothing imported either of them
(they had been replaced by `CodexPanel.tsx` and `AgentTerminal`), so they were bundled into nothing
while still failing `tsc` and breaking the `npm run build` gate on every update.

No real DeepSeek request had been made up to 2026-10-05 — a key was not present at installation. One
was made that day, and only one, to prove the skill mount: a single `deepseek-flash` turn in a
throwaway directory, no tools allowed, answered "graphify" (see above). `deepseek_test.py` still
makes no vendor requests, and `engine_parity_test.py` needs no key at all.


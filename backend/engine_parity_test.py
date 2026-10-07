"""One workbench, three engines: the buttons that were Claude's own.

Every check here is a bug that was reported or found while fixing one — a control that worked on a
Claude pane and did nothing (or something WRONG) on a Codex or DeepSeek pane:

  * Checkpoints: `_proj_root` compared the workspace's id with the project index's id as STRINGS.
    The workspace lowercases the drive letter, the index keeps the CLI's casing, so on Windows the
    two never met and every pre-turn snapshot silently failed — for every engine, since the feature
    existed. `data/checkpoints/` held no project directory at all.
  * Checkpoints were only ever taken inside the Claude branch and, separately, the Codex one. The
    DeepSeek adapter returns from `send()` before Claude's line, so its Checkpoints tab was empty
    forever. `_snapshot_before_turn` is now the one door.
  * Phases: Claude writes `TodoWrite`; the DeepSeek runtime writes `todo_write` (read out of the
    installed runtime's own tool catalog). Only the first was parsed, so a DeepSeek pane showed an
    empty plan while its transcript held 14 phase writes — and the plan outlived the feed's byte
    budget, so the lookup has to reach further back than the tail.
  * Skills: the panel read `~/.claude/skills` and said so. The DSH runtime discovers `SKILL.md`
    from its own roots and the Studio mounts that same library into it, so one switch now covers
    both engines; Codex has no such mechanism and says so.
  * `/compact` on a DeepSeek pane used to send the literal words to the model.
  * Editing a past DeepSeek message truncated its transcript and then drove the CLAUDE CLI at
    DeepSeek's session home.

Reads settings; writes nothing except one temporary checkpoint store it cleans up.
Run from backend/:  python engine_parity_test.py
"""
import io
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session, checkpoints, deepseek_session, mission, skills   # noqa: E402

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + label)
    else:
        fail += 1
        print("  FAIL  " + label + "   " + str(extra))


SLUG = "d--UserFiles-Desktop-kapow-Asset-Studio"

print("Checkpoints find the folder, whichever way the id is spelled")
# The four spellings the app actually produces for one folder: the workspace's id, Mission
# Control's, a Codex feed id and a DeepSeek feed id.
spellings = [SLUG, SLUG[0].upper() + SLUG[1:],
             "codex--" + SLUG, "deepseek-harness--" + SLUG]
roots = {}
for pid in spellings:
    roots[pid] = checkpoints._proj_root(pid)
    check("resolves: " + pid, bool(roots[pid]), checkpoints._proj_root(pid))
found = {str(v).lower() for v in roots.values() if v}
check("all four name the SAME folder", len(found) == 1, found)
dirs = {checkpoints._dir(pid).name.lower() for pid in spellings}
check("and one snapshot directory", len(dirs) == 1, dirs)
check("the store is keyed by the bare folder, not the feed",
      "codex--" not in checkpoints._dir("codex--" + SLUG).name
      and "deepseek" not in checkpoints._dir("deepseek-harness--" + SLUG).name,
      checkpoints._dir("deepseek-harness--" + SLUG).name)

print()
print("The pre-turn snapshot is taken at one door, for every engine")
# The call site, not a call: taking a real snapshot walks the whole project. What matters is that
# it sits ABOVE the per-engine dispatch in `send`, so no engine can return past it.
src = open("asset_studio/cc_session.py", encoding="utf-8").read()
snapshot_at = src.index("_snapshot_before_turn(project_id, message, engine")
deepseek_at = src.index('if agent == "deepseek-harness":', src.index("def send("))
codex_at = src.index('if agent == "codex":', src.index("def send("))
check("the snapshot runs before the DeepSeek branch", snapshot_at < deepseek_at)
check("and before the Codex branch", snapshot_at < codex_at)
check("the old per-engine call sites are gone",
      src.count("threading.Thread(target=checkpoints.create") == 1,
      src.count("threading.Thread(target=checkpoints.create"))
check("it is labelled with the engine that is about to write", '"agent": agent or ""' in src)
check("the money guard is above the dispatch too (it used to be dead for both)",
      src.index("over = spend.over_cap()", src.index("def send(")) < deepseek_at)
check("checkpoints record the engine", checkpoints.create.__code__.co_varnames[:4] == ("project_id", "label", "kind", "agent"),
      checkpoints.create.__code__.co_varnames[:4])

print()
print("Phases: both spellings of the whole-list phase tool")
check("Claude's TodoWrite", mission._phases_from_block(
    [{"type": "tool_use", "name": "TodoWrite",
      "input": {"todos": [{"content": "a", "status": "completed"}]}}]) is not None)
ds_rows = mission._phases_from_block(
    [{"type": "tool_use", "name": "todo_write",
      "input": {"todos": [{"content": "b", "status": "in_progress"}]}}])
check("DeepSeek's todo_write", ds_rows is not None and ds_rows[0]["content"] == "b", ds_rows)
check("a message with no phase tool is not a phase list",
      mission._phases_from_block([{"type": "text", "text": "hi"}]) is None)
check("the constant lists both", set(mission._PHASE_LIST_TOOLS) == {"TodoWrite", "todo_write"},
      mission._PHASE_LIST_TOOLS)
check("the feed renders todo_write as a Phases card",
      mission._tool_event({"type": "tool_use", "name": "todo_write",
                           "input": {"todos": [{"content": "x", "status": "pending"}]}}, "00:00:00")
      .get("title") == "Phases")

# A real DeepSeek conversation, if one is on this machine: the plan has to be found even though the
# last phase call is megabytes behind the end of the transcript.
ds_projects = sorted(glob.glob(str(deepseek_session.HOME / "projects" / "*" / "*.jsonl")),
                     key=os.path.getsize, reverse=True)
if ds_projects:
    big = ds_projects[0]
    rows, _iso = mission._last_phase_call(Path(big))
    has_call = '"todo_write"' in open(big, encoding="utf-8", errors="ignore").read()
    check("a real DSH transcript holds phase calls", has_call, big)
    check("and the deep scan finds the last one", bool(rows), "%d rows" % len(rows))
    tail_rows = []
    for o in mission._tail_entries_cached(Path(big), 512):
        r = mission._phases_from_block((o.get("message") or {}).get("content"))
        if r:
            tail_rows = r
    check("(the 512 KB tail alone did not — that is why the scan exists)",
          not tail_rows or len(tail_rows) <= len(rows), "%d tail vs %d deep" % (len(tail_rows), len(rows)))
else:
    print("  SKIP  no DeepSeek transcript on this machine")

print()
print("Skills: the library follows the engine")
claude_roots = dict((s, d) for s, d in skills._roots_for(SLUG, "claude"))
ds_roots = dict((s, d) for s, d in skills._roots_for("deepseek-harness--" + SLUG, "deepseek-harness"))
shared = skills._claude_home() / "skills"
check("Claude reads its own home", "personal" in claude_roots, list(claude_roots))
check("DeepSeek reads its project roots",
      "project-dsh" in ds_roots and "project-agents" in ds_roots, list(ds_roots))
check("Codex reads nothing, on purpose", skills._roots_for("codex--" + SLUG, "codex") == [])
# Kimi and Qwen run the SAME claude CLI with their own CLAUDE_CONFIG_DIR (`cc_session._alt_env`), so
# their skills live in their own home. Handed the bare folder id they showed Claude's library.
kimi = dict((s, d) for s, d in skills._roots_for("kimi--" + SLUG, "kimi"))
check("an alternate engine reads ITS home, not Claude's",
      str(kimi.get("personal", "")) != str(claude_roots.get("personal", "")) and "kimi-home" in str(kimi.get("personal")),
      kimi.get("personal"))
if shared.is_dir():
    check("DeepSeek also reads the mounted shared library", "shared" in ds_roots, list(ds_roots))
    check("and it is the SAME folder as Claude's personal library",
          str(claude_roots.get("personal")) == str(ds_roots.get("shared")),
          (claude_roots.get("personal"), ds_roots.get("shared")))
    check("so one listing serves both engines",
          [x["id"] for x in skills.list_skills("deepseek-harness--" + SLUG, "deepseek-harness")]
          == [x["id"] for x in skills.list_skills(SLUG, "claude")])
else:
    print("  SKIP  no ~/.claude/skills library on this machine")
check("a toggle finds the file by walking those roots, not by assuming ~/.claude/skills/<id>",
      "_find_skill_md" in skills.set_skill.__code__.co_names
      or "_find_skill_md" in open("asset_studio/skills.py", encoding="utf-8").read())
# The panel's own switch is the frontmatter flag BOTH engines read.
check("the flag is the one the DSH runtime honours (disable-model-invocation)",
      "disable-model-invocation" in open("asset_studio/skills.py", encoding="utf-8").read())

print()
print("The DeepSeek runtime is handed that library, and nothing else is broken")
patch = deepseek_session._patch_text()
check("the session-log row survives", "session-log-deepseek" in patch)
check("the skill-filesystem row is there", "dsh-skill-filesystem" in patch and "customSkillDirs" in patch)
check("with a quoted Windows path (single quotes: YAML does not unescape them)", "- '" in patch, patch)
check("the runtime home is per project", deepseek_session.runtime_home("a--x") != deepseek_session.runtime_home("b--x"))
check("and stable for one", deepseek_session.runtime_home("a--x") == deepseek_session.runtime_home("a--x"))

print()
print("/compact is a command, not a prompt")
# A project id that does not exist, so nothing is written anywhere: the point is that the compact
# path answers BEFORE a runtime is spawned. If it fell through to a real turn this call would need
# the network and a session, so ok=True here is the proof.
NO_SUCH = "deepseek-harness--d--UserFiles-Desktop-no-such-project-for-tests"
res = deepseek_session.send(NO_SUCH, "/compact", path=".")
check("answers without contacting a model", res.get("ok") and res.get("compacted"),
      {k: v for k, v in res.items() if k != "rebuilt_chars"})
check("and reports what the next turn will carry", "rebuilt_chars" in res, res)
check("no transcript is created for it", not (deepseek_session.HOME / "projects"
      / "d--UserFiles-Desktop-no-such-project-for-tests").exists())

print()
print("Rewind names the engine that owns the conversation")
rsrc = open("asset_studio/cc_session.py", encoding="utf-8").read()
check("a DeepSeek feed id resends through the DeepSeek adapter",
      'resend_agent = "deepseek-harness" if deepseek else "claude"' in rsrc)
check("and the resends use it, not the word claude",
      rsrc.count("agent=resend_agent") == 2, rsrc.count("agent=resend_agent"))
check("the DSH runtime is closed so the truncated file is re-read",
      "deepseek_session.cancel(project_id)" in rsrc)

print()
print("The turn ledger is read and written under the same key")
check("banked under the feed id the feed polls",
      "feed = PREFIX + pid" in open("asset_studio/deepseek_session.py", encoding="utf-8").read())
check("turns.for_project is asked for the feed id in the feed builder",
      "_turns.for_project(project_id)" in open("asset_studio/mission.py", encoding="utf-8").read())

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

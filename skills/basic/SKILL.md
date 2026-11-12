---
name: basic
description: Use when the user types /basic - run the task with the active Opus agent as the ORCHESTRATOR, Codex agents (GPT via the Codex CLI) for the coding, with a clear written brief each, and a Sonnet agent for any research.
metadata:
  created: 2026-11-11
  updated: 2026-11-11
disable-model-invocation: true
---

# /basic: Opus orchestrates, Codex codes, Sonnet researches

You, the active Opus agent, are the orchestrator for the task after `/basic`. You plan, decide, write the
briefs, check every result and report. The user authorizes, for this task only: Codex CLI agents for the
coding, and the `studio-sonnet` agent for research.

## Who does what

| Work | Who | How |
| --- | --- | --- |
| Understand the task, plan, split it, choose the design, integrate, review, test, git | **Opus (you)** | yourself |
| Code with a clear spec: a module, a function, a bug fix, a refactor in named files, tests | **Codex, `gpt-6.1-terra`, effort `high`** | `codex exec` (below) |
| Code that a first Codex run got wrong twice, or a hard bug across modules | **Codex, `gpt-6.1-sol`, effort `xhigh`** | the same command with `-m gpt-6.1-sol` |
| Research: web, documentation, an API, a library, a file format, a survey of a large codebase | **Sonnet** | Agent tool, `subagent_type: "studio-sonnet"` (do not set `model`) |
| A change of a few lines, a lookup you can do in one or two tool calls | **you** | no helper |

Do not send code work to Sonnet or research to Codex in this mode. Ask the user before you use another engine.

## The order of work

1. **Research first, when it is needed.** If the task depends on facts you do not have (an API, a format, how
   the code works now), send Sonnet one clear question per topic. Start independent questions in one message.
   Wait for the answers before you write the coding briefs.
2. **Plan.** Split the coding into tasks. Give every task its own files: two Codex jobs must never change the
   same file. A task that needs the result of another task waits for it.
3. **Brief and start Codex.** Write one brief per task (template below) and start the independent jobs at the
   same time, each as a background Bash task.
4. **Check every result yourself.** Read `report.md`, read the diff, run the tests the brief names. Never
   trust a "done".
5. **Fix.** A small defect: fix it yourself. A larger one: send the same job a new brief that names the exact
   failure (the command, the output, the file:line). After two failed tries, use `gpt-6.1-sol` or do it yourself.
6. **Finish.** Run the project's own checks. Report which helper did which part, and what you checked.

## Codex: how to run a coding job

One job folder per task, OUTSIDE the repository, so the repository stays clean:
`C:/tmp/basic-jobs/<yyyymmdd-hhmm>-<task-id>/` (on another OS, any temporary folder).

```sh
J="C:/tmp/basic-jobs/<run>-<task-id>"; mkdir -p "$J"     # write the brief to $J/brief.md first
codex exec --skip-git-repo-check --sandbox workspace-write -C "<absolute repository or worktree path>" \
  -m gpt-6.1-terra -c model_reasoning_effort=high \
  -o "$J/report.md" - < "$J/brief.md" > "$J/codex.log" 2>&1
```

- Run it with `run_in_background: true`. You are told when it ends. `codex.log` ends with `tokens used N`.
- `-C` is the folder Codex may change. With `workspace-write` it cannot write outside it.
- Network is off in the sandbox. Only when the task needs it (for example `npm install`), add
  `-c sandbox_workspace_write.network_access=true`, and say so in the brief.
- Parallel jobs that change files near each other: give each its own git worktree
  (`git worktree add <path> -b <branch>`), then merge the branches yourself.
- At most four Codex jobs at the same time.
- Never stop `codex.exe app-server` or any process by name. Stop only a job you started, by its pid.

## The Codex brief (write all of it, every time)

```md
# Task <id>: <one line: what is true when the task is done>

Repository: <absolute path>, branch <name>. Work only in this folder.

## Goal
<What the change must do, in plain words. The user-visible result, if there is one.>

## Files
- Change only: <exact paths>
- Read for context: <exact paths, or file:line>
- Do not touch any other file.

## Requirements
1. <one requirement per line: names, signatures, data shapes, edge cases, error messages>
2. ...

## Acceptance
- `<exact test or build command>` passes.
- <an observable result that you can check, for example a function's output for a given input>

## Rules
- <the project rules from AGENTS.md / CLAUDE.md that apply, copied here: Codex does not read them for you>
- Keep the change small and in the style of the code around it. Add no dependency.
- Do not commit, push, delete files or change configuration unless this brief says so.
- Find code with the code graph before you search with grep:
  curl -s --get 'http://127.0.0.1:8777/api/graphify/query' --data-urlencode 'root=<absolute repo path>' --data-urlencode 'q=<symbol>'

## Report
End with: the files you changed, the commands you ran with their results, and anything you could not do.
```

A good brief has no "etc.", no "as discussed", no "the usual way": Codex knows only what the brief says.

## Sonnet: how to run a research question

Agent tool, `subagent_type: "studio-sonnet"`, with a prompt that holds:

- The question, in one or two sentences, and why you need the answer.
- The scope: which sources (web, the docs of a library, these folders of the repository) and what is out of scope.
- The tools it needs, because a subagent does not inherit your notes:
  - code graph: `curl -s --get 'http://127.0.0.1:8777/api/graphify/query' --data-urlencode 'root=<path>' --data-urlencode 'q=<symbol>'`
  - a page that blocks robots: `curl -s -X POST http://127.0.0.1:8777/api/web/fetch -H 'content-type: application/json' -d '{"url":"<url>","chars":20000}'`
  - a web search with no key: `curl -s --get http://127.0.0.1:8777/api/web/search --data-urlencode 'q=<query>'`
- Read only: it changes no file.
- The answer format: the findings, each with its source (URL or file:line), what is certain and what is not,
  and the open questions. A short answer, not a file dump.

## Rules

- Decisions, architecture, integration, the final check and every git action stay with you.
- The project's rules (AGENTS.md, CLAUDE.md) apply to every helper. Copy the relevant ones into each brief.
- Check each result before you build on it: open the files, run the tests, look at a screenshot for UI work.
- At the end, give the user a table: task, helper (Codex Terra / Codex Sol / Sonnet / you), result, how you checked it.

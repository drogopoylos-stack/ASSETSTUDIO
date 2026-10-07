---
name: studio-backend
description: Validated backend/API/infra patterns, SDK facts, and gotchas accumulated across projects (servers, APIs, data, deploy). Auto-captured when Auto-learn is on.
metadata:
  category: backend
  updated: 2026-06-19
  auto: true
disable-model-invocation: true
---

# Studio · Backend

Validated backend / API learnings — each a dated, sourced `## entry` you can edit or remove.
Auto-captured when **Auto-learn** (the 🎓 toggle in the chat box) is on.

<!-- learnings are appended below -->

## Live workspace filesystem sync — watcher + version change-feed (2026-06-19)

**Problem:** a file explorer/editor that loads the tree once and only refreshes on the
UI's own ops won't show files an AGENT (or build/external editor) creates or edits —
they appear only after a manual refresh. Per-file mtime polling is slow and wasteful.

**Pattern (cheap + near-instant):**
- Backend watcher module using **`watchfiles`** (Rust-backed, native OS notifications;
  already ships with uvicorn so no new dep). Run `watch(*roots, stop_event, watch_filter)`
  in a daemon thread; on each batch bump a global monotonic `version` and append
  `(version, path)` to a bounded deque. Filter out noise dirs (`.git`, `node_modules`,
  `dist`, `.venv`, `__pycache__`, …) or the feed floods.
- Watch only the root(s) the UI is actually viewing: `ensure_watching(root)` records
  `root -> now`; a TTL set (≈45s) drops roots the UI stopped polling, and the watcher
  thread is **restarted only when the root set changes** (so multiple/detached windows
  don't thrash it). time.time() is fine in backend (the no-`Date` rule is Workflow-only).
- Endpoint `GET /changes?root=&since=` → `ensure_watching(root)` (guard it's within
  browse roots) + return `{version, paths:[changed-since]}`. `since < 0` returns no paths
  so the client can prime a baseline on first poll without a spurious refresh.

**Frontend:** poll `/changes` ~1s. Keep `fsVer` ref = -1 on root switch (prime), then on
a version bump with paths: bump the tree's `reloadKey` (which must cascade into expanded
folders via a `nonce` effect so deep folders refetch too — collapsed/`open` state persists
because React reconciles TreeNodes by path key), and **reload only non-dirty** open editors
whose path is in the changed set (mark dirty ones "changed on disk", never clobber unsaved
edits). **Source:** STUDIO `fswatch.py`, `routers/workspace.changes`, `Workspace.tsx`.

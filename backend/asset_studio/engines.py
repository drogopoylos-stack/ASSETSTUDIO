"""The engines the Studio drives — Claude Code, OpenAI Codex, DeepSeek Harness — behind ONE interface.

WHY THIS FILE EXISTS. Every engine was added as a branch: `if project_id.startswith("codex--")` in
the feed reader, `if agent == "deepseek-harness"` in the send path, a third copy in the busy check,
a fourth in cancel. There were about fifty of them in ten files, and each one was a place where a
new engine could be forgotten. The bugs that came out of that were all the same bug — a button
that worked for Claude and silently did nothing for the others: DeepSeek had no checkpoints
because its branch returned before the line that took them; Codex was not covered by the spend cap
because the guard sat below its branch; a DeepSeek edit-and-resend left the model's memory alone
because only `cancel` was called, and `cancel` does nothing when idle.

So the question "which engine is this, and how do I ask it X" now has one answer, here:

    engines.for_feed("codex--d--proj")   -> CODEX        (a feed id, as the UI polls it)
    engines.for_agent("deepseek-harness") -> DEEPSEEK    (an agent id, as the chat box sends it)
    engines.bare_folder("kimi--d--proj")  -> "d--proj"   (the folder, whatever engine prefix)

Every engine answers the same questions — is a turn running, stop it, what is it doing right now,
forget your in-memory conversation — and an engine that keeps its own log (Codex) also answers the
feed readers itself. The implementations stay where they were (`cc_session`, `codex_app`,
`deepseek_session`); this module only decides who is asked.

THE ALTERNATE ENGINES (Kimi, Qwen, user-added providers) are Claude Code pointed at another API.
They run through Claude's own plumbing under an "<engine>--" prefix, so they are the Claude engine
here; `bare_folder` strips their prefix like any other.
"""
from __future__ import annotations

from typing import Optional


class Engine:
    """One engine. The defaults are the Claude-transcript behaviour; an engine overrides what differs."""

    id = ""                 # the agent id the chat box sends
    name = ""               # what a person calls it
    prefix = ""             # the feed-id prefix its conversations live under
    # Does this engine keep its own log, so the feed, sessions, context, todos and subagent readers
    # must ask IT instead of parsing a Claude-format transcript?
    native_feed = False
    # Can the transcript NOT say whether a turn is running (no "assistant is replying" rows until
    # the end), so the feed must ask the engine's runtime instead?
    reports_working = False

    def feed_id(self, bare: str) -> str:
        return self.prefix + bare

    # --- the questions every engine answers ---------------------------------------------------
    def start_turn(self, project_id: str, message: str, **opts) -> dict:
        """The engine-specific half of a send. The shared half — money guard, checkpoint, BOOST —
        runs first, in `cc_session.send`, for every engine."""
        raise NotImplementedError

    def is_sending(self, project_id: str) -> bool:
        raise NotImplementedError

    def cancel(self, project_id: str) -> dict:
        raise NotImplementedError

    def live_state(self, project_id: str) -> dict:
        raise NotImplementedError

    def live_rows(self) -> list[tuple[str, bool, int]]:
        """(bare folder id, busy, running subagents) for every conversation this engine holds."""
        return []

    def reset(self, project_id: str) -> None:
        """Forget the in-memory conversation, so the next send rebuilds it from the transcript on
        disk. Needed after the transcript is edited (edit-and-resend)."""

    def skill_roots(self, project_id: str) -> list:
        return []

    # --- native feed readers (only when `native_feed`) ---------------------------------------
    def feed(self, project_id: str, limit: int = 150, session: str = "", kinds: str = "") -> dict:
        raise NotImplementedError

    def sessions(self, project_id: str) -> list[dict]:
        raise NotImplementedError

    def context(self, project_id: str) -> dict:
        raise NotImplementedError

    def todos(self, project_id: str) -> dict:
        raise NotImplementedError

    def subagents(self, project_id: str) -> dict:
        raise NotImplementedError

    def subagent_detail(self, project_id: str, agent_id: str, limit: int = 400) -> dict:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<engine {self.id}>"


class _Claude(Engine):
    id, name, prefix = "claude", "Claude", ""

    def start_turn(self, project_id: str, message: str, **opts) -> dict:
        # Claude's send IS the body of cc_session.send after the shared half; nothing dispatches here.
        raise RuntimeError("the Claude turn is started inline by cc_session.send")

    def is_sending(self, project_id: str) -> bool:
        from . import cc_session
        return cc_session._claude_is_sending(project_id)

    def cancel(self, project_id: str) -> dict:
        from . import cc_session
        return cc_session._claude_cancel(project_id)

    def live_state(self, project_id: str) -> dict:
        from . import cc_session
        return cc_session._claude_live_state(project_id)

    def reset(self, project_id: str) -> None:
        from . import cc_session
        live = cc_session._live.pop(project_id, None)
        if live is not None:
            cc_session._kill_live(live)

    def skill_roots(self, project_id: str) -> list:
        from . import skills
        return skills._claude_roots(project_id)


class _Codex(Engine):
    id, name, prefix = "codex", "Codex", "codex--"
    native_feed = True

    def start_turn(self, project_id: str, message: str, **opts) -> dict:
        from . import cc_session
        return cc_session._send_codex(project_id, message, opts.get("model", "default"),
                                      opts.get("permission_mode", ""), opts.get("images"),
                                      opts.get("effort", "default"), bool(opts.get("new_session")),
                                      opts.get("session", ""), opts.get("path", ""), bool(opts.get("steer")))

    def is_sending(self, project_id: str) -> bool:
        from . import codex_app
        return codex_app.is_sending(project_id)

    def cancel(self, project_id: str) -> dict:
        from . import codex_app
        return codex_app.cancel(project_id)

    def live_state(self, project_id: str) -> dict:
        from . import codex_app
        return codex_app.live_state(project_id)

    def live_rows(self) -> list[tuple[str, bool, int]]:
        from . import codex_app
        return [(b, bool(busy), int(n or 0)) for b, busy, n in codex_app.live_rows()]

    # Codex's skills equivalent is AGENTS.md, read as standing instructions — see skills._roots_for.

    def feed(self, project_id, limit=150, session="", kinds=""):
        from . import codex_app
        return codex_app.feed(project_id, limit, session, kinds)

    def sessions(self, project_id):
        from . import codex_app
        return codex_app.sessions(project_id)

    def context(self, project_id):
        from . import codex_app
        return codex_app.context(project_id)

    def todos(self, project_id):
        from . import codex_app
        return codex_app.todos(project_id)

    def subagents(self, project_id):
        from . import codex_app
        return codex_app.subagents(project_id)

    def subagent_detail(self, project_id, agent_id, limit=400):
        from . import codex_app
        return codex_app.subagent_detail(project_id, agent_id, limit)


class _DeepSeek(Engine):
    id, name, prefix = "deepseek-harness", "DeepSeek", "deepseek-harness--"
    # It writes a Claude-format transcript (so the shared readers work), but the user row is
    # written at send time and nothing else lands until the model answers: only the runtime knows.
    reports_working = True

    def start_turn(self, project_id: str, message: str, **opts) -> dict:
        from . import deepseek_session
        return deepseek_session.send(project_id, message, model=opts.get("model", "default"),
                                     permission_mode=opts.get("permission_mode", "full"),
                                     images=opts.get("images"), effort=opts.get("effort", "default"),
                                     new_session=bool(opts.get("new_session")),
                                     session=opts.get("session", ""), path=opts.get("path", ""))

    def is_sending(self, project_id: str) -> bool:
        from . import deepseek_session
        return deepseek_session.is_sending(project_id)

    def cancel(self, project_id: str) -> dict:
        from . import deepseek_session
        return deepseek_session.cancel(project_id)

    def live_state(self, project_id: str) -> dict:
        from . import deepseek_session
        return deepseek_session.live_state(project_id)

    def live_rows(self) -> list[tuple[str, bool, int]]:
        from . import deepseek_session
        return [(b, bool(busy), 0) for b, busy in deepseek_session.live_rows()]

    def reset(self, project_id: str) -> None:
        from . import deepseek_session
        deepseek_session.reset(project_id)

    def skill_roots(self, project_id: str) -> list:
        from . import skills
        return skills._dsh_roots(project_id)


CLAUDE = _Claude()
CODEX = _Codex()
DEEPSEEK = _DeepSeek()
ALL: tuple[Engine, ...] = (CLAUDE, CODEX, DEEPSEEK)
# The engines with a prefix of their own, longest prefix first so no prefix can swallow another.
_PREFIXED: tuple[Engine, ...] = tuple(sorted((e for e in ALL if e.prefix), key=lambda e: -len(e.prefix)))


def for_agent(agent: str) -> Engine:
    """The engine behind an agent id from the chat box. Alternate engines (Kimi, Qwen, custom
    providers) are Claude Code under another API, so they are the Claude engine."""
    for e in ALL:
        if e.id == agent:
            return e
    return CLAUDE


def for_feed(feed_id: str) -> Engine:
    """The engine a feed id belongs to: "codex--x" is Codex, "deepseek-harness--x" DeepSeek, and
    anything else — a bare folder or an alternate engine's "<engine>--x" — is Claude's plumbing."""
    for e in _PREFIXED:
        if feed_id.startswith(e.prefix):
            return e
    return CLAUDE


def _alt_prefix(project_id: str) -> str:
    try:
        from . import mission
        return mission.alt_prefix(project_id)
    except Exception:
        return ""


def bare_folder(feed_id: str) -> str:
    """The folder id under any engine's prefix — "codex--d--x", "kimi--d--x" and "d--x" are the
    same folder. Engine prefixes first, then an alternate engine's."""
    bare = feed_id
    e = for_feed(bare)
    if e.prefix:
        bare = bare[len(e.prefix):]
    p = _alt_prefix(bare)
    return bare[len(p):] if p else bare


def name_of(feed_id: str) -> str:
    """A person's name for the engine behind a feed id: "Claude", "Codex", "DeepSeek", "kimi"…"""
    e = for_feed(feed_id)
    if e is not CLAUDE:
        return e.name
    p = _alt_prefix(feed_id)
    return p[:-2] if p else CLAUDE.name


def folder_feeds(bare: str) -> list[str]:
    """Every feed id one folder can have a conversation under: each engine's, and each alternate
    engine's. Deduplicated — an engine prefix that is also registered as an alternate home (the
    DeepSeek home is, for its transcripts) is listed once."""
    ids = [e.feed_id(bare) for e in ALL]
    try:
        from . import mission
        ids += [p + bare for p in mission.alt_homes()]
    except Exception:
        pass
    return list(dict.fromkeys(ids))


def busy_feeds(feed_id: str, exclude: str = "") -> list[str]:
    """Every feed with a turn RUNNING in this feed's folder, whichever engine. `exclude` is the
    caller's own feed (one it has already stopped)."""
    out: list[str] = []
    for fid in folder_feeds(bare_folder(feed_id)):
        if fid == exclude:
            continue
        try:
            if for_feed(fid).is_sending(fid):
                out.append(fid)
        except Exception:
            pass
    return out


def native(feed_id: str) -> Optional[Engine]:
    """The engine that answers the feed readers itself for this id, or None for a Claude-format
    transcript (Claude, the alternate engines and DeepSeek)."""
    e = for_feed(feed_id)
    return e if e.native_feed else None

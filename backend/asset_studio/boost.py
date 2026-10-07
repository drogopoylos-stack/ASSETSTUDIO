"""BOOST — run the cheap work on THIS PC before a token is spent.

One switch (``settings["boost"]``, the ON/OFF button beside the prompt). ON means a
message sent to Claude Code, OpenAI Codex or DeepSeek Harness goes through the local
economy below first, and the engine is told — once, in its system prompt — to use this
machine's own index instead of re-discovering the repository.

WHAT IT DOES, and why each one is where it is
---------------------------------------------
Every saving here is applied to the bytes the STUDIO owns, and never to the cached
prefix. That distinction is the whole design. A coding CLI's prompt is
``tools -> system -> static context -> [cache breakpoint] -> your message``: the part
above the breakpoint is billed at about a tenth of the input price when it is
byte-identical to last turn, and rewriting it turns a cache READ into a full-price
WRITE of the entire conversation. So:

1. COMPRESS — the outgoing user MESSAGE. It sits BELOW the breakpoint, so rewriting it
   cannot invalidate anything above it. Purely local CPU work, no model, no key.
   It is *lossless in intent*: ANSI colour is stripped, a run of identical log lines is
   folded to one line and a count, blank runs are collapsed, trailing whitespace goes.
   The user's own sentences are never reworded and no identifier is ever dropped —
   the research on LLMLingua-style rewriting (2-5x on QA benchmarks) does not transfer
   to code, where the identifiers and the exact stack trace ARE the question.

2. PREFETCH — the project's own code graph, on this PC, answers the symbols the message
   names before it is sent, and the few-hundred-token answer rides with the prompt. This
   is a COST, not a saving, and the ledger records it as one: it buys the turn the agent
   would otherwise spend grepping and reading files to find what this machine already
   knows. It is measured as a lookup, never claimed as tokens saved.

3. DIRECTIVE — one short, CONSTANT system-prompt note telling the agent the local-first
   habits that shorten a turn. Constant is the point: a note that changes between turns
   moves the cached prefix and costs far more than it saves.

4. PROFILE — the switches that were already one click away, turned on together: the code
   graph, the phase archive and the system-prompt snapshot ON; auto-learn OFF, because it
   is ~1900 tokens on every single turn for a background miner. The values that were
   there before BOOST are saved and put back the moment it is switched off.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No semantic/response cache. The honest ceiling for repeated prompts is about a third of
traffic on chat, and a coding turn is near-unique once the files have moved — a stale
cached answer is worse than a paid fresh one. No per-turn compression of the system
prompt or the tool definitions: the first is a live cache hit at ~0.1x, and rewriting
either is exactly the mistake that makes a session MORE expensive. Both are left out on
purpose, not for want of trying.

Every character removed is banked in ``data/boost.jsonl`` and totalled for the button,
so a saving that is not real is never claimed.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from . import fsutil
from .config import DATA_DIR, settings

LEDGER = DATA_DIR / "boost.jsonl"
# The switch values BOOST replaced. NOT a settings key: settings.update() DEEP-MERGES, so a
# patch of `{}` cannot clear a dict — the previous cycle's values would survive and then be
# restored over a newer state on the next OFF. BOOST owns this file, and rewrites it whole.
_SAVED = DATA_DIR / "boost_saved.json"
_LEDGER_MAX = 4000          # lines kept; a saving ledger older than a few thousand sends is noise
_lock = threading.Lock()

# The profile BOOST applies, and the reason for each entry, in one place.
#
# EVERY KEY HERE IS ONE THAT CANNOT MOVE THE REQUEST PREFIX, and that is the economics of the
# button rather than a style choice. A switch that rides the system prompt (`cc_graphify`,
# `cc_autolearn`) makes the next Claude send re-send the whole conversation under a new request
# prefix: a cache READ at about 0.1x becomes a cache WRITE at about 1.25x, and on a long session
# that one line costs more than every note it removes. A button called BOOST must never be the
# most expensive thing in the app, so the switches that would do that are RECOMMENDED — with
# their price — instead of being flipped behind the user's back. See `_RECOMMEND`.
_PROFILE: dict[str, bool] = {
    # The CLI repeats the whole task list in a reminder every few tool calls; one session
    # here had grown to 554 finished tasks, about 10k tokens a reminder. Mission-side: this
    # never changes the text a session is given.
    "cc_phase_archive": True,
    # Record the system prompt once per conversation and replay it verbatim, so the cached
    # prefix stops moving between launches. cc_session._snapshot_flag still sends "off" for
    # the one launch whose notes changed, so a toggle keeps working. A LAUNCH flag, not a
    # note: changing it does not respawn anything.
    "cc_prompt_snapshot": True,
}

# What BOOST would switch, and deliberately will NOT switch for you. Each entry names what it
# buys and what flipping it costs, because the cost is the reason it is not automatic: these two
# are the switches whose value rides the system prompt. The UI shows them on the BOOST row; the
# user flips them between conversations, where the change costs nothing.
_RECOMMEND: list[dict] = [
    {"key": "cc_graphify", "on": True,
     "why": "the local code graph answers a symbol search from this PC instead of a grep plus a "
            "file read — it also powers BOOST's own prefetch",
     "cost": "changing it mid-conversation re-sends the whole context as a cache miss"},
    {"key": "cc_autolearn", "on": False,
     "why": "the auto-learn note costs about 1900 tokens on EVERY turn, for a background miner",
     "cost": "changing it mid-conversation re-sends the whole context as a cache miss"},
]

_MIN_SAVE_CHARS = 80       # below this the rewrite is churn, not a saving: keep the original
_MAX_PREFETCH_SYMBOLS = 3
_MAX_PREFETCH_CHARS = 1200


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------
def enabled() -> bool:
    """Is BOOST on? Read at each send, so the button takes effect on the next message."""
    return bool(settings.get("boost", False))


def status() -> dict:
    """Everything the button and the settings row need, in one call."""
    st = stats()
    return {
        "on": enabled(),
        "directive_tokens": _tokens(directive()),
        "compress": bool(settings.get("boost_compress", True)),
        "prefetch": bool(settings.get("boost_prefetch", True)),
        # The prefetch can only answer from a graph that exists. Saying so here is the difference
        # between "BOOST is on and saving nothing" and "BOOST is on, waiting for the graph switch".
        "prefetch_ready": bool(settings.get("cc_graphify")),
        "level": str(settings.get("boost_level", "safe")),
        # The tool-output caps in force (0 = the engine's own default). See section 5.
        "tool_output": {"claude_bash_chars": bash_output_chars(), "codex_tool_tokens": codex_tool_tokens()},
        "profile": {k: settings.get(k) for k in _PROFILE},
        "changed": _changes(),
        # The switches BOOST will NOT touch, each with the reason and the price. The UI shows
        # them, so the economy is the user's decision instead of a silent cache re-write.
        "recommend": [{"key": r["key"], "want": r["on"], "now": bool(settings.get(r["key"])),
                       "matches": bool(settings.get(r["key"])) == r["on"],
                       "why": r["why"], "cost": r["cost"]} for r in _RECOMMEND],
        **st,
    }


def _changes() -> dict:
    """What BOOST actually moved — {key: {on, applied, changed, from}}.

    Measured against the values it REPLACED, not against what it wants: comparing with the
    target says "nothing changed" the instant the profile lands, which is exactly when the user
    needs to be told which of their switches moved. BOOST must never be a switch that quietly
    turns something off, so this is what it did, in a form the row can say out loud."""
    saved = _read_saved()
    out: dict = {}
    for k in _PROFILE:
        cur = bool(settings.get(k))
        has_before = k in saved
        out[k] = {"on": cur, "applied": bool(_PROFILE[k]),
                  "changed": has_before and bool(saved.get(k)) != cur,
                  "from": bool(saved.get(k)) if has_before else cur}
    return out


def _read_saved() -> dict:
    try:
        d = json.loads(_SAVED.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_saved(d: dict) -> None:
    try:
        _SAVED.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SAVED.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        fsutil.replace(tmp, _SAVED)
    except OSError:
        pass


def set_enabled(on: bool) -> dict:
    """Turn BOOST on or off, saving and restoring the switches it touches.

    IDEMPOTENT, and it has to be. The Settings switchboard writes `boost` through the ordinary
    settings endpoint, which saves the key BEFORE this runs — so a version that applied the
    profile only on a false→true transition turned the button on and none of the economy behind
    it. Applying it whenever it is on costs nothing and cannot be skipped by the caller.

    Restoring matters more than applying. A profile that turned auto-learn off and could not
    put it back would be a switch that silently edits the user's settings forever."""
    on = bool(on)
    prev = _read_saved()
    if on:
        # Recorded once, on the way in. Keyed off the FILE rather than off the current value,
        # because by the time anyone asks, the setting may already say True.
        if not prev:
            _write_saved({k: settings.get(k) for k in _PROFILE})
        settings.update(_PROFILE)
        settings.update({"boost": True})
    else:
        restore = {k: v for k, v in prev.items() if v is not None}
        if restore:
            settings.update(restore)
        settings.update({"boost": False})
        _write_saved({})
    _ledger({"kind": "toggle", "on": on})
    return status()


# ---------------------------------------------------------------------------
# 5. tool-output caps — the saving the ledger above never saw
# ---------------------------------------------------------------------------
# The biggest repeat cost in a coding turn is a long command output: a test run or a build log
# lands in the context once and is then re-sent with every later request. Both caps act on tool
# RESULTS, which sit below the cache breakpoint, so they cannot move the cached prefix.
_BASH_CHARS = 12000          # Claude: inline chars; the rest is saved to a file (lossless)
_CODEX_TOOL_TOKENS = 8000    # Codex: tokens of one tool output kept in the context


def bash_output_chars() -> int:
    """Claude's `bashOutputMaxChars` while BOOST is on, else 0 (= the CLI default, 30000).

    Lossless: the CLI saves the output past the limit to a file and gives the model the path."""
    if not enabled() or not settings.get("boost_tool_output", True):
        return 0
    try:
        n = int(settings.get("boost_bash_chars", _BASH_CHARS))
    except (TypeError, ValueError):
        n = _BASH_CHARS
    return max(4000, min(128000, n))


def codex_tool_tokens() -> int:
    """Codex's `tool_output_token_limit` while BOOST is on, else 0 (= Codex's own default).

    Codex keeps the head and tail of a longer output and drops the middle, so this one IS lossy;
    the limit stays well above a normal command's output for that reason."""
    if not enabled() or not settings.get("boost_tool_output", True):
        return 0
    try:
        n = int(settings.get("boost_codex_tool_tokens", _CODEX_TOOL_TOKENS))
    except (TypeError, ValueError):
        n = _CODEX_TOOL_TOKENS
    return max(2000, n)


# ---------------------------------------------------------------------------
# 3. the directive — CONSTANT text, on purpose
# ---------------------------------------------------------------------------
def directive() -> str:
    """The one note BOOST adds to an agent's system prompt.

    Kept byte-identical every turn. A note that is re-worded between turns moves the
    cached prefix, and a moved prefix reprices the whole conversation — the saving would
    be negative. Everything in it is a habit that shortens a turn; nothing in it removes
    a capability, so an agent that ignores it loses nothing but the saving.
    """
    if not settings.get("boost_directive", True):
        return ""
    # The graph line is only true when the graph switch is on. Telling an agent to "ask the code
    # graph" when no graph is built, and no command to reach it was given, sends it looking for a
    # tool that does not exist. `cc_graphify` already rides the system prompt, so a change to it
    # moves the prefix anyway — this line adds no extra cache miss.
    graph = (
        "- A code graph of this project is live and answers for a few hundred tokens what grep "
        "plus a file read costs thousands for. Ask it a symbol before a broad text search, and "
        "do not read graphify-out/graph.json itself.\n"
    ) if settings.get("cc_graphify") else ""
    return (
        "BUDGET MODE (BOOST). This machine runs a local index of this project; use it before "
        "you spend a turn finding things out.\n"
        + graph +
        "- Do not re-read a file you have already read this session, and do not re-derive a fact "
        "you already established. Say what you know and act on it.\n"
        "- Batch: make every edit a file needs in one pass instead of one pass per edit.\n"
        "- Answer with the work — the diff, the command, the result — not a narration of it. "
        "No preamble, no restating the plan, no summary of what you just did.\n"
        "- Stop when the task is done. Unrequested refactors, extra tests and tidy-ups are not "
        "free: they are billed, and they are not what was asked for."
    )


# ---------------------------------------------------------------------------
# 1. the compressor
# ---------------------------------------------------------------------------
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_FENCE = re.compile(r"^\s*(```|~~~)")
# A payload nobody reads: a base64 blob or a data: URI. Only elided in "hard", and only
# when it is big enough that the model was never going to read it line by line anyway.
_DATAURI = re.compile(r"data:([A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+);base64,[A-Za-z0-9+/=\s]{2000,}")
_B64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{2000,}={0,2}(?![A-Za-z0-9+/])")
_REPEAT_MIN = 3            # two identical lines happen in real output; three is a loop
_MARK = "  ... [BOOST folded %d identical lines]"


def _fold_repeats(lines: list[str]) -> tuple[list[str], int]:
    """Fold a run of identical non-blank lines into one line plus a count.

    This is the compression that pays on a real message: a pasted build log, a test run,
    a stack trace repeated per frame. It is also the one that has to be careful — inside a
    fenced code block a run of identical lines can be data, so fences are left alone.

    AND IT HAS TO ACTUALLY PAY. A marker a model can understand is ~40 characters, so
    folding a run of four-character lines ("err", "  0") makes the message LONGER — a
    "compression" that adds tokens is worse than none. The fold is therefore taken only
    when the run it replaces is worth more than the sentence that replaces it.

    Fenced regions are copied through untouched. The marker is explicit rather than silent
    so the agent can tell that something was folded and ask for it if it matters."""
    out: list[str] = []
    folded = 0
    fenced = False
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if _FENCE.match(raw):
            fenced = not fenced
            out.append(raw)
            i += 1
            continue
        if fenced or not stripped:
            out.append(raw)
            i += 1
            continue
        j = i + 1
        while j < n and lines[j].strip() == stripped and not _FENCE.match(lines[j]):
            j += 1
        run = j - i
        marker = _MARK % (run - 1)
        if run >= _REPEAT_MIN and (run - 1) * (len(stripped) + 1) > len(marker) + 1:
            out.append(stripped)
            out.append(marker)
            folded += run - 1
        else:
            out.extend(lines[i:j])
        i = j
    return out, folded


def compress(text: str) -> dict:
    """Rewrite a message on this PC before it is sent. Never rewords, never drops meaning.

    Returns ``{"text", "saved_chars", "saved_tokens", "reasons", "changed"}``. When the
    rewrite is not worth it (short message, nothing redundant) the ORIGINAL is returned
    with ``changed: False`` — a rewrite that saves nothing only makes the transcript
    differ from what the user typed.
    """
    original = text or ""
    out: dict = {"text": original, "saved_chars": 0, "saved_tokens": 0,
                 "reasons": {}, "changed": False, "before_chars": len(original),
                 "after_chars": len(original)}
    if not original.strip() or not settings.get("boost_compress", True):
        return out

    reasons: dict[str, int] = {}
    work = original.replace("\r\n", "\n").replace("\r", "\n")

    cleaned = _ANSI.sub("", work)
    if len(cleaned) != len(work):
        reasons["ansi"] = len(work) - len(cleaned)
        work = cleaned

    hard = str(settings.get("boost_level", "safe")).lower() == "hard"
    if hard:
        work, n = _DATAURI.subn(lambda m: "data:%s;base64,<BOOST elided %d chars>"
                                % (m.group(1), len(m.group(0))), work)
        if n:
            reasons["data_uri"] = n
        work, n = _B64.subn(lambda m: "<BOOST elided base64 blob, %d chars>" % len(m.group(0)), work)
        if n:
            reasons["base64"] = n

    lines = [ln.rstrip() for ln in work.split("\n")]
    lines, folded = _fold_repeats(lines)
    if folded:
        reasons["repeated_lines"] = folded

    # A run of blank lines is a run of newline tokens for nothing. One blank line still
    # separates paragraphs, which is the only job the second and third ones had.
    collapsed: list[str] = []
    blanks = 0
    for ln in lines:
        if ln.strip():
            blanks = 0
            collapsed.append(ln)
        else:
            blanks += 1
            if blanks <= 1:
                collapsed.append("")
    if len(collapsed) != len(lines):
        reasons["blank_runs"] = len(lines) - len(collapsed)

    result = "\n".join(collapsed).strip("\n")
    saved = len(original) - len(result)
    out.update({"reasons": reasons, "after_chars": len(result)})
    if saved < _MIN_SAVE_CHARS:
        return out                     # not worth making the transcript differ
    out.update({"text": result, "saved_chars": saved,
                "saved_tokens": _tokens_of(saved), "changed": True})
    return out


# ---------------------------------------------------------------------------
# 2. the local index, asked before the agent has to
# ---------------------------------------------------------------------------
# An identifier, as a person writes one in a sentence: snake_case, camelCase, or a name in
# backticks. Plain lowercase English ("this", "and", "refresh") is left out on purpose —
# the graph is asked only about names the message is clearly POINTING at.
_IDENT_BACKTICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{2,60})`")
_IDENT_SHAPED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,60})\b")
_STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "when", "then", "than",
    "have", "has", "had", "was", "were", "are", "is", "be", "been", "not", "but", "you",
    "your", "please", "should", "would", "could", "make", "made", "add", "fix", "use",
    "using", "need", "want", "like", "just", "only", "also", "here", "there", "what",
    "why", "how", "where", "which", "while", "some", "any", "all", "can", "will", "does",
    "run", "see", "get", "set", "new", "old", "one", "two", "let", "its", "it", "to",
    "of", "in", "on", "at", "by", "or", "if", "so", "as", "an", "a", "do", "did", "done",
}


def _looks_like_symbol(tok: str) -> bool:
    if len(tok) < 3 or tok.lower() in _STOP:
        return False
    if "_" in tok:
        return True
    if re.search(r"[a-z][A-Z]", tok):        # camelCase / PascalCase
        return True
    return False


def _symbols(message: str) -> list[str]:
    """The symbols a message is pointing at, most likely first, at most a handful.

    Backticked names win, because a person who writes `` `refresh` `` means that symbol.
    The rest has to be identifier-SHAPED — snake_case or camelCase — because asking the
    graph about every English word in a sentence returns noise that costs tokens instead
    of saving them."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _IDENT_BACKTICK.finditer(message or ""):
        t = m.group(1)
        if t not in seen and len(t) >= 3 and t.lower() not in _STOP:
            seen.add(t)
            out.append(t)
    if len(out) < _MAX_PREFETCH_SYMBOLS:
        for m in _IDENT_SHAPED.finditer(message or ""):
            t = m.group(1)
            if t in seen or not _looks_like_symbol(t):
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= _MAX_PREFETCH_SYMBOLS:
                break
    return out[:_MAX_PREFETCH_SYMBOLS]


def prefetch(message: str, cwd: str = "") -> dict:
    """Ask this PC's code graph about the symbols in a message, to send with it.

    A COST, deliberately: the block costs tokens and the ledger says so. What it buys is
    the grep and the file read the agent would have spent a whole turn on. Only run when
    a graph actually exists for this project — the query endpoint STARTS a build when it
    finds none, which is right for a person asking and wrong in front of a send."""
    blank = {"block": "", "lookups": 0, "chars": 0, "tokens": 0}
    if not message or not cwd or not settings.get("boost_prefetch", True):
        return blank
    if not settings.get("cc_graphify", True):
        return blank
    try:
        from . import graphify_index
        if not Path(cwd).is_dir() or not graphify_index._graph_path(cwd).is_file():
            return blank
    except Exception:
        return blank

    symbols = _symbols(message)
    if not symbols:
        return blank

    lines: list[str] = []
    for sym in symbols:
        try:
            res = graphify_index.query(cwd, q=sym, limit=3, scan=False)
        except Exception:
            continue
        if not res.get("ok"):
            continue
        want = sym.lower()
        exact = [m for m in res.get("matches") or []
                 if str(m.get("name") or "").strip().lower().rstrip("()") == want]
        if not exact:
            continue
        where = "; ".join(str(m.get("at") or "") for m in exact[:2] if m.get("at"))
        users = ", ".join(str(u.get("name") or "") for u in (res.get("used_by") or [])[:4])
        line = "  %s — %s" % (sym, where)
        if users:
            line += " (called by %s)" % users
        lines.append(line)
        if sum(len(x) for x in lines) > _MAX_PREFETCH_CHARS:
            break

    if not lines:
        return blank
    block = ("[BOOST local index] This project's code graph on this machine already answered "
             "these, so do not grep or read to find them:\n" + "\n".join(lines))
    return {"block": block, "lookups": len(lines), "chars": len(block),
            "tokens": _tokens(block)}


# ---------------------------------------------------------------------------
# prepare — the one call the send path makes
# ---------------------------------------------------------------------------
def prepare(project_id: str, message: str, engine: str = "", cwd: str = "") -> dict:
    """Everything BOOST does to one outgoing message, in the order it does it.

    Called once per send, before the message reaches any engine. Returns the message to
    actually send plus what happened, so the send response can tell the user what this
    prompt saved instead of leaving them to guess."""
    original = message or ""
    if not enabled() or not original:
        return {"message": original, "applied": False, "saved_tokens": 0, "saved_chars": 0}

    comp = compress(original)
    text = comp["text"]

    # A SLASH COMMAND MUST STAY FIRST. The block goes IN FRONT of the message, so on "/btw …" or
    # "/review some_func" the command was no longer the first thing the engine saw: /btw arrived as a
    # plain steer and a skill command as prose. Commands are short and name their own target, so
    # they skip the lookup instead of moving it.
    if text.lstrip().startswith("/"):
        pre = {"block": "", "lookups": 0, "chars": 0, "tokens": 0}
    else:
        pre = prefetch(text, cwd)
    if pre["block"]:
        text = pre["block"] + "\n\n" + text

    saved_tokens = int(comp["saved_tokens"])
    saved_chars = int(comp["saved_chars"])
    if saved_chars or pre["lookups"]:
        _ledger({"kind": "send", "project": project_id, "engine": engine,
                 "saved_chars": saved_chars, "saved_tokens": saved_tokens,
                 "reasons": comp["reasons"],
                 "prefetch_lookups": pre["lookups"], "prefetch_tokens": pre["tokens"]})
    return {
        "message": text, "applied": True,
        "saved_tokens": saved_tokens, "saved_chars": saved_chars,
        "compressed": bool(comp["changed"]), "reasons": comp["reasons"],
        "prefetch_lookups": pre["lookups"], "prefetch_tokens": pre["tokens"],
    }


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------
def _tokens(text: str) -> int:
    """The same estimate the Settings page already uses for its note prices (chars // 4).

    One convention for the whole app, so a BOOST number and a note price can be compared
    without either being wrong in a different direction."""
    return len(text or "") // 4


def _tokens_of(chars: int) -> int:
    return int(chars) // 4


def _ledger(row: dict) -> None:
    row = {"at": time.time(), **row}
    try:
        with _lock:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return
    _sweep()


def _sweep() -> None:
    """Keep the ledger bounded. At most once a minute, and only when it is over the cap."""
    try:
        if not LEDGER.is_file():
            return
        if time.time() - _sweep.at < 60:
            return
        _sweep.at = time.time()
        lines = LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) <= _LEDGER_MAX:
            return
        tmp = LEDGER.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines[-_LEDGER_MAX:]) + "\n", encoding="utf-8")
        fsutil.replace(tmp, LEDGER)
    except OSError:
        pass


_sweep.at = 0.0        # type: ignore[attr-defined]


def stats() -> dict:
    """The banked total, read from the ledger.

    Tokens are counted ONLY where text was actually removed, and a prefetch is counted as
    a lookup rather than as a saving. A number on a button is a claim, and this one has to
    survive being questioned."""
    saved_tokens = saved_chars = messages = prefetched = 0
    since = 0.0
    try:
        text = LEDGER.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "toggle":
            continue
        saved_tokens += int(row.get("saved_tokens") or 0)
        saved_chars += int(row.get("saved_chars") or 0)
        if row.get("saved_chars"):
            messages += 1
        prefetched += int(row.get("prefetch_lookups") or 0)
        at = float(row.get("at") or 0.0)
        if at and (not since or at < since):
            since = at
    return {"saved_tokens": saved_tokens, "saved_chars": saved_chars,
            "messages": messages, "prefetched": prefetched, "since": since}


def forget() -> dict:
    """Throw the running total away — the button's Reset, for a number that starts again."""
    try:
        LEDGER.unlink(missing_ok=True)
    except OSError:
        pass
    return stats()

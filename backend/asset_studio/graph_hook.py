"""PreToolUse hook: when a Grep looks for a SYMBOL, hand back the graph's answer.

The instruction in the system prompt is advisory, and advisory loses — dozens of greps went by
this session without anything noticing. This is the part the harness executes rather than the
model chooses.

It does not block. Blocking would be wrong: the graph is AST-derived, so it holds declarations
and misses local closures inside a function, CSS classes and dynamic dispatch — and in a file
built largely from local closures, grep is the correct tool. So the grep always runs, and this
just adds what the graph already knows, which costs ~60 tokens and often saves a read.

It stays quiet unless it can actually help: only for identifier-shaped patterns (a real regex is
a text search, not a symbol lookup), only when a graph exists, and only when the graph has a hit.

It watches Bash as well as Grep, and that is not belt-and-braces. Measured over one 8.7 MB
session: 283 shell calls and ZERO calls to the Grep tool, because that session was configured to
search with `grep` in Bash. A hook matched only on Grep therefore fired exactly no times. An
enforcement layer that the configuration can switch off without saying so is worse than none,
since it reads as protection while providing nothing.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

# A symbol lookup, not a text search. Anything with regex metacharacters, spaces or quotes is
# someone searching CONTENT (an error string, a TODO), which the graph cannot answer.
_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,63}$")
_TIMEOUT = 2.5          # a hook sits in front of a tool call; never make the user wait
# grep / rg / ag / ack anywhere in a pipeline, and whatever follows it up to the next segment
_GREPPY = re.compile(r"(?:^|(?P<sep>\|\||&&|[|;&(])\s*)(?P<xargs>xargs\s+(?:-\S+\s+)*)?(?:grep|egrep|fgrep|rg|ag|ack)\b(?P<rest>[^|;&\n)]*)")
# A grep or rg that reads ANOTHER command's output (`ls | grep -c paint`, `git log | grep fix`) is
# filtering text, not looking for code. Measured: such filters were one note in five, all noise.
# It still counts when it names files or recurses (`... | xargs grep`, `| grep -r x .`).
_RECURSE = {"-r", "-R", "--recursive", "-rn", "-Rn", "-rl", "-Rl", "-rni", "-rin"}
_REDIRECT = re.compile(r"^\d*[<>]")
# The OTHER ways of looking for something. Measured over one session: 21 searches went through
# grep and 16 did not — `find -name` ten times, a sed address five, an awk pattern once. A hook
# that only reads grep therefore covered 57% of the searches while looking like a rule.
_FINDY = re.compile(r"(?:^|[|;&(]\s*)find\b[^|;&\n)]*?-i?name\s+(?P<pat>\S+)")
_SEDDY = re.compile(r"(?:^|[|;&(]\s*)sed\b[^|;&\n)]*?-n\s*['\"]?/(?P<pat>[^/'\"]+)/")
# The pattern must open the QUOTED program: `awk '/name/ {print}'`. Without the quote,
# `awk -F: '{print $2}' data/tmp/summary.txt` gave the symbol 'tmp' out of the file's path.
_AWKY = re.compile(r"(?:^|[|;&(]\s*)awk\b[^|;&\n)]*?['\"]\s*/(?P<pat>[^/\s'\"]+)/")
_PSSTR = re.compile(r"Select-String\b[^|;&\n)]*?-Pattern\s+(?P<pat>\S+)", re.I)


def _bare(tok: str) -> str:
    """A quoted, globbed or extensioned token reduced to the identifier inside it, or ""."""
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1]
    # Take the identifier the token STARTS with. Stripping the glob first and the extension
    # second got "WorkingPulse*.tsx" wrong, because the star sits in the middle: strip() only
    # touches the ends. Reading forward from the front handles every shape at once —
    # "WorkingPulse*", "WorkingPulse.tsx", "WorkingPulse*.tsx" — and "*.tsx" correctly yields
    # nothing, because a search for every file of a type is not a search for a symbol.
    m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", t)
    t = m.group(0) if m else ""
    return t if _SYMBOL.match(t) else ""


def _symbol_from_bash(command: str) -> str:
    """The identifier a shell command is searching for, or "".

    Deliberately narrow. The first non-flag word after grep/rg is the pattern, and it only
    counts when it is a bare identifier — `grep -rn "handleClick" src/` asks about a symbol,
    `grep -rn "TODO: fix" .` and `grep -E "a|b"` do not, and the graph has nothing to say
    about either. A wrong guess here costs a confusing note on an unrelated command."""
    for m in _GREPPY.finditer(command or ""):
        rest = m.group("rest").strip()
        # Split the way a SHELL does. A plain .split() tears a quoted pattern apart at its
        # spaces, and then only the first word survives: `grep -n "def _settings_file|def
        # _hook_settings"` came through as the symbol `def`, and the graph cheerfully answered
        # about DefaultProvidersSection. posix=False keeps the quotes on the token and leaves
        # Windows backslashes alone, which the posix lexer would eat.
        try:
            toks = shlex.split(rest, posix=False)
        except ValueError:
            toks = rest.split()
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.startswith("-"):
                # a flag that takes a value: skip its argument too
                if t in ("-e", "-m", "--include", "--exclude", "--glob", "-g", "--type", "-t"):
                    i += 2
                    continue
                i += 1
                continue
            cand = t
            if len(cand) >= 2 and cand[0] == cand[-1] and cand[0] in "\"'":
                cand = cand[1:-1]
            if _SYMBOL.match(cand):
                piped = m.group("sep") == "|" and not m.group("xargs")   # xargs hands it files
                operands = [x for x in toks[i + 1:] if not x.startswith("-") and not _REDIRECT.match(x)]
                recursive = any(x in _RECURSE or x.startswith("--recursive") for x in toks)
                if piped and not operands and not recursive:
                    break    # filtering another command's output: not a code search
                return cand
            break            # the first pattern word decides; do not hunt through paths
    # the non-grep ways of searching. Same strictness: an identifier or nothing.
    for rx in (_FINDY, _SEDDY, _AWKY, _PSSTR):
        m = rx.search(command or "")
        if m:
            if rx is _PSSTR and not (
                    re.search(r"-(?:Literal)?Path\b", m.group(0), re.I)
                    or re.search(r"\b(?:Get-ChildItem|gci|dir|ls)\b[^|;\n]*\|\s*Select-String", command, re.I)):
                continue     # Select-String over another command's output: a text filter
            got = _bare(m.group("pat"))
            if got:
                return got
    return ""


def _graph_note(pattern: str, where: str, more: str = "", behind: int = 0) -> str:
    """The note the model reads. Short on purpose: it rides on every symbol search, and the
    GRAPHIFY system note already says how to query the graph. "That came from" stays: the feed
    reads the note with mission._GRAPH_RE, and old transcripts carry the long form."""
    warn = (" The graph is %ds behind your edits, so a line may have moved." % behind) if behind else ""
    return (f"[code graph] '{pattern}' is declared at: {where}{more}. That came from the code "
            f"graph (declarations only); ask it for callers with q={pattern}.{warn}")


def _settings_port() -> tuple[str, int, bool]:
    """(host, port, graphify_enabled) from the Studio's settings file."""
    try:
        data = json.loads((Path(__file__).resolve().parents[2] / "data" / "settings.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "127.0.0.1", 8777, True
    host = str(data.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return host, int(data.get("port") or 8777), bool(data.get("cc_graphify", True))


def _deny_reading_the_graph(payload: dict, host: str, port: int) -> int:
    """Refuse a Read of graph.json — the one case where blocking is right.

    The file is megabytes; the Read tool puts ALL of it in context (~475k tokens here, half a
    window) and there is never a reason to want that: every question it could answer, the query
    endpoint answers in a few hundred. Deny returns this message to the model instead of the
    file, which is the whole point — a warning would arrive alongside the damage, not instead
    of it. Bash is deliberately NOT covered: parsing the file in a script only puts what you
    PRINT into context, which is cheap and legitimate."""
    ti = payload.get("tool_input") or {}
    target = str(ti.get("file_path") or "").replace("\\", "/").lower()
    if not target.endswith("graphify-out/graph.json"):
        return 0
    size = ""
    try:
        size = f" ({Path(str(ti.get('file_path'))).stat().st_size / 1024 / 1024:.1f} MB)"
    except OSError:
        pass
    root = str(payload.get("cwd") or "").strip()
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason":
            f"Reading graph.json{size} would put the whole graph in your context for nothing. "
            f"Ask it instead: curl -s --get 'http://{host}:{port}/api/graphify/query' "
            f"--data-urlencode 'root={root}' --data-urlencode 'q=<symbol>' — that returns "
            f"file:line, callers and callees in a few hundred tokens. Omit q for a map of the "
            f"codebase (node/link/file counts, biggest files, most-connected symbols). If you "
            f"genuinely need the raw structure, parse it in Bash and print only what you need.",
    }}))
    return 0




# ---------------------------------------------------------------------------
# Never let one agent kill every browser on the machine
# ---------------------------------------------------------------------------
# This is not hypothetical. Three subagents were building a game side by side, each taking its
# own screenshots, and one of them wrote itself this:
#
#     # Always a fresh headless Chrome: the long-lived one exits unpredictably.
#     taskkill //F //IM chrome.exe //T
#
# `/IM` kills by IMAGE NAME, so that one line kills EVERY chrome.exe on the machine: the user's
# own browser with all their tabs, the Studio's review browser, and the other two agents' Chromes
# mid-screenshot. The comment it wrote is the proof of the feedback loop — "the long-lived one
# exits unpredictably" is what its siblings' taskkill looked like from the inside, and it
# concluded the fix was to do the same thing first. All three then did it to each other.
#
# It cost the user their browser about twenty times, and left no trace in the event log, because
# a taskkill is a clean termination and never writes a crash entry.
#
# Blocking is the right response rather than a warning: by the time a warning is read the tabs
# are already gone. The rule is one line — KILL WHAT YOU STARTED, BY PID — and killing by pid is
# never touched here, so an agent tidying up after itself is unaffected.

# Processes the whole machine shares. Killing one of these by name reaches other agents, the
# Studio, and the user.
_SHARED_IMAGES = (
    "chrome", "chromium", "msedge", "brave", "firefox", "opera", "vivaldi",
    "node", "python", "pythonw", "claude", "electron",
)

# taskkill /IM <name>   ·   //IM under Git Bash   ·   -IM
_TASKKILL = re.compile(r"\btaskkill\b[^\n|;&]*?[/-]{1,2}IM\s+\"?([A-Za-z0-9_.\-]+)", re.I)
# pkill chrome · killall -9 Google Chrome
_POSIX_KILL = re.compile(r"\b(?:pkill|killall)\b[^\n|;&]*?([A-Za-z][A-Za-z0-9_.\- ]*)", re.I)
# Stop-Process -Name chrome · Get-Process chrome | Stop-Process
_PS_KILL = re.compile(r"(?:Stop-Process[^\n|;&]*?-Name\s+\"?([A-Za-z0-9_.\-]+)"
                      r"|Get-Process\s+\"?([A-Za-z0-9_.\-]+)\"?[^\n]*\|\s*Stop-Process)", re.I)


def _mass_kill_target(command: str) -> str:
    """The shared process this command would kill BY NAME, or "" if it is safe.

    Only mass kills are caught. `taskkill /F /PID 1234`, `kill 1234` and `psutil.Process(pid)`
    all name one process the caller knows about, which is exactly the correct way to stop a
    browser you launched.
    """
    for rx in (_TASKKILL, _POSIX_KILL, _PS_KILL):
        for m in rx.finditer(command or ""):
            name = next((g for g in m.groups() if g), "")
            stem = name.strip().strip('"').lower()
            for suffix in (".exe", ".app"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
            stem = stem.replace("google ", "").strip()
            if stem in _SHARED_IMAGES:
                return stem
    return ""




# A command that RUNS A SCRIPT hides whatever the script does.
#
# This is not a theoretical bypass — it is how the real one got through. The agent wrote the
# taskkill into /tmp/shotter/go.sh once, and every screenshot after that was `bash
# /tmp/shotter/go.sh <url> <out.png>`, which reveals nothing. Checking the command text alone
# would have blocked the day it was written and waved through the two hundred runs afterwards.
#
# So: if the command names a script that exists, read it. Bounded to small files, because a
# helper script is small and nobody hides a taskkill in a 2 MB bundle.
# SHELL scripts only. A shell script's text IS the list of commands it will run, so reading it
# is reading what it does. A .py or .js file's text is mostly not: it holds strings, tests and
# documentation that merely MENTION a command. Including them produced an immediate false
# positive — this guard's own test file, which naturally contains every pattern it tests for,
# and then the guard's own source.
_SCRIPT_ARG = re.compile(r"[^\s'\"|;&<>]+\.(?:sh|bash|ps1|bat|cmd)\b")
_SCRIPT_MAX = 64 * 1024


def _script_mass_kill(command: str) -> tuple:
    """(victim, script path) for the first invoked script that mass-kills, else ("", "")."""
    for m in _SCRIPT_ARG.finditer(command or ""):
        raw = m.group(0)
        for cand in _as_paths(raw):
            try:
                p = Path(cand)
                if not p.is_file() or p.stat().st_size > _SCRIPT_MAX:
                    continue
                victim = _mass_kill_target(
                    _uncommented(p.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
            if victim:
                return victim, str(p)
    return "", ""


def _uncommented(text: str) -> str:
    """Drop comment lines before matching.

    Required, not tidiness: the corrected version of the very script that caused this quotes the
    old taskkill in a comment, to explain what went wrong and why it is gone. A guard that cannot
    tell a warning from an instruction would block the fix and preserve the bug.
    """
    out = []
    for line in (text or "").splitlines():
        t = line.lstrip()
        if t.startswith("#") or t[:2] == "::" or t[:4].lower() == "rem ":
            continue
        out.append(line)
    return "\n".join(out)


def _as_paths(raw: str) -> list:
    """The same argument as Windows and as Git Bash see it — /tmp/x and /c/Users/... both."""
    out = [raw]
    if raw.startswith("/tmp/"):
        out.append(str(Path(tempfile.gettempdir()) / raw[len("/tmp/"):]))
    if len(raw) > 3 and raw[0] == "/" and raw[2] == "/":
        out.append(raw[1] + ":" + raw[2:])           # /c/Users/... -> c:/Users/...
    return out


def _deny_mass_kill(payload: dict, host: str, port: int) -> int:
    """Refuse to kill a shared program by name, and say what to do instead."""
    ti = payload.get("tool_input") or {}
    command = str(ti.get("command") or "")
    victim = _mass_kill_target(command)
    via = ""
    if not victim:
        victim, via = _script_mass_kill(command)
    if not victim:
        return 0                       # falsy: nothing was denied, carry on
    browser = victim in ("chrome", "chromium", "msedge", "brave", "firefox", "opera", "vivaldi")
    instead = (
        f"Take the screenshot without owning a browser at all: "
        f"curl -s -X POST http://{host}:{port}/api/review/render -H 'content-type: application/json' "
        f"-d '{{\"project\":\"<abs path>\",\"mode\":\"scene\",\"times\":[0,300,900]}}' — it drives one "
        f"shared headless Chrome on a stepped clock and returns a contact sheet plus metrics. "
        f"If you must run your own, give it its own profile (--user-data-dir=<a temp dir>) and "
        f"stop it BY PID, which is the pid Popen/`$!` already handed you."
        if browser else
        "Stop the process you started, by pid — the pid your own launch returned."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason":
            (f"Refused: {via} kills EVERY {victim} on the machine, by image name. " if via else "")
            + (f"Refused: this kills EVERY {victim} on the machine, by image name. " if not via else "")
            + f"That is not your "
            f"{victim} — it is also the user's (with every tab they had open), the Studio's own, "
            f"and any other agent's running right now. Three agents doing this to each other is "
            f"what it looks like when screenshots start failing 'unpredictably'. {instead}",
    }}))
    return 1                           # truthy: denied, and the decision is already on stdout


def _web_hint(payload: dict, host: str, port: int) -> bool:
    """Say ONCE per session that a refusal is not the end of the road.

    A NUDGE, NOT A BLOCK: WebFetch is instant and free for most of the web, and routing every
    page through a local stealth browser to rescue the walled minority would be a bad trade.

    AND ONCE, NOT EVERY TIME. Firing on every call put ~110 tokens of the same advice into the
    conversation for each web read — twenty reads, twenty repeats, none of it cached, all of it
    said already in the system prompt. A model that has been told once has been told. The marker
    is a file per session id under the OS temp dir, so it dies with the machine's next clean-up
    and needs no state of ours.

    Silent if the fetcher is not installed: advice you cannot follow is worse than none.
    """
    sid = str(payload.get("session_id") or "")
    seen = None
    if sid:
        import tempfile
        seen = Path(tempfile.gettempdir()) / "studio-web-hint" / re.sub(r"[^A-Za-z0-9._-]", "_", sid)
        if seen.exists():
            return False
    try:
        import urllib.request
        with urllib.request.urlopen("http://%s:%d/api/web/status" % (host, port), timeout=1.5) as r:
            if not json.load(r).get("available"):
                return False
    except Exception:
        return False
    if seen is not None:
        try:
            seen.parent.mkdir(parents=True, exist_ok=True)
            seen.write_text("1", encoding="utf-8")
        except OSError:
            pass
    base = "http://%s:%d/api/web" % (host, port)
    msg = ("[studio] If a page comes back 403, empty, or as a challenge ('Just a moment…', "
           "'verify you are human'), the site refuses robots — it is not down. Retry with the "
           "Studio's stealth fetcher: curl -s -X POST %s/fetch -H 'content-type: application/json' "
           "-d '{\"url\":\"<url>\"}'. Keyless search: curl -s --get %s/search "
           "--data-urlencode 'q=<query>'. Said once per session." % (base, base))
    print(json.dumps({
        "suppressOutput": True,
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg},
    }))
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = str(payload.get("tool_name") or "")
    host, port, enabled = _settings_port()
    # BEFORE the graphify gate. This guard has nothing to do with the code graph — it only
    # shares the hook — and it must not go quiet because someone turned graphify off. Losing
    # every browser tab on the machine is not a feature of an optional index.
    if tool in ("Bash", "PowerShell") and _deny_mass_kill(payload, host, port):
        return 0
    # Also before the graphify gate, and also only sharing the hook: a blocked web page has
    # nothing to do with the code graph.
    if tool in ("WebFetch", "WebSearch") and _web_hint(payload, host, port):
        return 0
    if not enabled:
        return 0
    if tool == "Read":
        return _deny_reading_the_graph(payload, host, port)
    ti = payload.get("tool_input") or {}
    if tool == "Grep":
        pattern = str(ti.get("pattern") or "").strip()
    elif tool in ("Bash", "PowerShell"):
        pattern = _symbol_from_bash(str(ti.get("command") or ""))
    elif tool == "Glob":
        # "**/WorkingPulse*.tsx" is a search for a file, and the graph indexes files by name.
        pattern = _bare(str(ti.get("pattern") or "").rsplit("/", 1)[-1])
    else:
        return 0
    if not _SYMBOL.match(pattern):
        return 0

    root = str(payload.get("cwd") or "").strip()
    if not root:
        return 0
    # NO early return when the graph is missing. The endpoint starts a build when it finds none,
    # so asking is what brings a new project into the index: the first search there is silent and
    # every one after it answers. Bailing out here instead meant a project nobody had ever
    # queried stayed unindexed for ever — the hook sat waiting for a graph that only a query
    # could have created. TSILIOWNER had six source files and no graph for exactly that reason.
    # scan=0: the endpoint normally cross-checks its answer against a text scan, and here that
    # would be the second copy of a grep the harness is about to run anyway.
    # via=hook: tells the backend this read is the model working, so the live pulse can show
    # "Reading the code graph" next to Thinking and Writing.
    qs = urllib.parse.urlencode({"root": root, "q": pattern, "limit": 4, "scan": 0,
                                 "via": "hook"})
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/graphify/query?{qs}",
                                    timeout=_TIMEOUT) as r:
            res = json.loads(r.read())
    except Exception:
        return 0
    hits = res.get("matches") or []
    # EXACT declarations only. The query endpoint matches substrings, which is right for a
    # person exploring and wrong for an unsolicited note: asking about `def` returned
    # DefaultProvidersSection, DEFAULT_PINS and useDefineForClassFields — three real symbols,
    # none of them named `def`. A hook that volunteers information has to be sure of it.
    want = pattern.lower()
    hits = [h for h in hits
            if str(h.get("name") or "").strip().lower().rstrip("()") == want]
    if not hits:
        return 0                      # a local/CSS/text symbol — grep is the right tool, be quiet

    where = "; ".join(f"{h['name']} at {h['at']}" for h in hits[:3])
    more = f" (+{len(hits) - 3} more)" if len(hits) > 3 else ""
    # A STALE location is the one way this note can make an answer worse instead of better.
    # Everything else it can get wrong is caught by the search that runs anyway; a line number
    # that moved since the last build is a confident pointer at the wrong place, and nothing
    # downstream would question it. The endpoint already works this out — the hook has to ask.
    st = res.get("stale") or {}
    behind = int(st.get("seconds_behind") or 0) if isinstance(st, dict) else 0
    # The "[code graph]" prefix is a CONTRACT, not decoration: the Studio's feed looks for it in
    # the hook attachment the transcript records, and turns it into a visible line. Change the
    # marker here and the feed goes quiet without any error anywhere.
    msg = _graph_note(pattern, where, more, behind)
    print(json.dumps({
        "suppressOutput": True,
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg},
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)          # a hook must never break the tool call it sits in front of

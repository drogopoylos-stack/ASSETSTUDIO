"""Which shell commands are code searches, and the note the code graph adds to one.

Measured over one long session: the hook added 161 code-graph notes (73,906 chars). About 30 of
them answered a question nobody asked — a grep that filtered another command's output
(`ls ... | grep -c paint`), and a PATH read as an awk pattern (`awk -F: '{..}' data/tmp/x` gave
'tmp'). And every note repeated ~330 chars of instructions the GRAPHIFY system note already has.

Run: python graph_hook_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio.graph_hook import (_graph_note, _symbol_from_bash as sym,  # noqa: E402
                                     _symbol_from_pattern as pat)
from asset_studio.mission import _GRAPH_RE                                   # noqa: E402

passed = failed = 0


def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL", label, extra)


# ---- searches over code: the graph can help
for cmd, want in [
    ('grep -rn "handleClick" src/', "handleClick"),
    ("grep -n handleClick frontend/src/App.tsx", "handleClick"),
    ("rg handleClick", "handleClick"),
    ("cd x && grep -rn resolvePhases backend/", "resolvePhases"),
    ("ls || grep -n resolvePhases mission.py", "resolvePhases"),       # || is OR, not a pipe
    ("git ls-files | grep -r resolvePhases .", "resolvePhases"),       # recursive: files
    ("find . -type f | xargs grep -n resolvePhases", "resolvePhases"),  # grep over files via xargs
    ("awk '/resolvePhases/ {print}' mission.py", "resolvePhases"),
    ("sed -n '/resolvePhases/p' mission.py", "resolvePhases"),
    ("Select-String -Path *.py -Pattern resolvePhases", "resolvePhases"),
    ("Get-ChildItem -Recurse *.py | Select-String -Pattern resolvePhases", "resolvePhases"),
    ("find . -name 'WorkingPulse*.tsx'", "WorkingPulse"),
]:
    check("a code search: %s" % cmd, sym(cmd) == want, "got %r" % sym(cmd))

# ---- not searches over code: the hook stays quiet
for cmd in [
    "ls frontend/src/components/engine/edit/ | grep -c paint",          # the 'paint' note
    "git log --oneline | grep release",
    "cat data/tmp/x.txt | grep -v tmp",
    "npm test 2>&1 | grep passed",
    "ls data | grep paint 2>/dev/null",
    "awk -F'::' '{print $2}' data/tmp/suites-local/summary.txt",          # the 'tmp' note
    "tail -4 data/tmp/suites-local/summary.txt | awk '{print $1}'",
    "Get-Process | Select-String -Pattern chrome",
    'grep -rn "TODO: fix" .',
    'grep -E "a|b" file.txt',
    "sed -n 1,40p backend/asset_studio/mission.py",
    # The quoted pattern holds the first `|`, so the command reads as cut short at `"strip`.
    # The first branch of an alternation is not the symbol the search is for.
    'sed -n 110,240p src/ui/screens/screens.css; rg -n "strip|title__menu" src/ui/screens/screens.css | head -40',
    'rg -n "menu|ranked|Rank" src/ui/screens/TitleScreen.tsx | sed -n 1,80p',
    'npm test > /tmp/t.log 2>&1; rg -n "Successfully|failed" /tmp/t.log | head -5',
]:
    check("not a code search: %s" % cmd, sym(cmd) == "", "got %r" % sym(cmd))

# ---- a DECLARATION PHRASE is a symbol lookup. Measured in KAPOW: twelve shell searches in one
# session, and the two that the graph could answer outright were phrases. Both got no note,
# so the agent guessed a directory and grepped it while the graph held the exact file:line.
for cmd, want in [
    ('rg -n "export function RankEmblem" -A20 src/ui/ranked/RankEmblem.tsx', "RankEmblem"),
    ('cd /g/KAPOW && rg -n "export function rankName" -A8 src/ranked/*.ts | head -12', "rankName"),
    ('grep -rn "export const NEWS_ENTRIES" src/', "NEWS_ENTRIES"),
    ("rg -n 'class SceneRunner' src", "SceneRunner"),
    ('rg -n "interface RankEmblemProps" src/', "RankEmblemProps"),
    ('rg -n "def _pcm(" backend/', "_pcm"),
    (r'rg -n "export\s+function\s+rankName" src/', "rankName"),      # the escaped-space form
    ('rg -n "RankEmblem()" src/', "RankEmblem"),                      # dressed as a call
    ("rg -A 8 handleClick src/", "handleClick"),                      # -A takes the 8, not it
]:
    check("a declaration search: %s" % cmd, sym(cmd) == want, "got %r" % sym(cmd))

# ---- the phrases that are still text, not a symbol
for p in [
    "menu|ranked|Rank",                 # an alternation: a text search over one file
    r"^\.kbtn",                         # a CSS class: the graph says so itself
    "strip|title__menu",
    "export function (ranked|casual)",  # no identifier after the keyword
    "TODO: fix",
    "type: string",                     # the keyword needs whitespace after it
]:
    check("still a text search: %s" % p, pat(p) == "", "got %r" % pat(p))
# A phrase that only CONTAINS a keyword may yield a candidate — main() then keeps nothing,
# because it holds the note back unless the graph has an exact declaration of that name.
check("a loose phrase is left to the exact-declaration filter", pat("function returns a value") in ("", "returns"))

# ---- the note: shorter, and still read by the feed
where = "resolve_phases() at asset_studio/mission.py:1895; resolve_phases() at x.py:3"
note = _graph_note("resolve_phases", where)
check("the note names the symbol and where it is", "'resolve_phases' is declared at: " + where in note, note)
check("the note is short (the GRAPHIFY system note has the rest)", len(note) - len(where) < 170, len(note) - len(where))
m = _GRAPH_RE.search(note)
check("the feed still reads the symbol out of it", bool(m) and m.group(1) == "resolve_phases", note)
check("...and where it is", bool(m) and m.group(2) == where, m and m.group(2))
stale = _graph_note("resolve_phases", where, behind=45)
check("a stale graph says so", "45s behind" in stale and _GRAPH_RE.search(stale) is not None, stale)
old = ("[code graph] 'x' is declared at: x() at a.py:1. That came from the project's symbol graph, "
       "not this search. For callers/callees: curl ...")
check("the feed still reads the old long form in old transcripts", _GRAPH_RE.search(old).group(1) == "x")

print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)

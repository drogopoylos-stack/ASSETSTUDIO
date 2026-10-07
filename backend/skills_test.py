# -*- coding: utf-8 -*-
"""Every skill this repository ships, held to the rules that make one work.

A skill that is malformed does not fail loudly. It simply never triggers, or it triggers and
names a tool that does not exist. Both are silent, so they are checked here.

The four rules:

  1. The FOLDER NAME AND THE FRONTMATTER NAME AGREE. Claude Code matches a skill by its
     frontmatter name; the Studio's Skills tab keys its switch on the folder. When the two
     disagree the switch toggles an id nothing answers to. `blender-kiln` shipped for weeks
     calling itself `kiln`.
  2. THERE IS A DESCRIPTION, AND IT IS BOUNDED. The description of every installed skill is in
     context for every turn of every session, whether the skill is used or not — it is the only
     thing the model reads when deciding to load one. No description means the skill is dead
     weight; an essay means it is expensive weight.
  3. `disable-model-invocation: true`. This is what keeps the BODY out of context until the
     user switches the skill on in the Studio. Without it a 16 KB skill is loaded whenever the
     model feels like it.
  4. EVERY MESH OP A SKILL NAMES REALLY EXISTS. A skill that tells an agent to call
     `ops.bevel()` is worse than no skill at all if there is no `bevel`. The list is read out of
     ops.ts, so the two cannot drift apart.

Run:  python skills_test.py     (from backend/)
"""
import io
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
OPS_TS = ROOT / "frontend" / "src" / "components" / "engine" / "edit" / "ops.ts"

ok = fail = skip = 0


def check(name, cond, got=None):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, repr(got)[:200]))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s - %s" % (name, why))


def frontmatter(text: str) -> dict:
    """The YAML block, without a yaml dependency. Only scalar keys; that is all a skill has."""
    out: dict = {}
    if not text.lstrip().startswith("---"):
        return out
    body = text.split("---", 2)
    if len(body) < 3:
        return out
    for line in body[1].split("\n"):
        m = re.match(r"\s*([A-Za-z_-]+):\s*(.*)", line)
        if m:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


# ---------------------------------------------------------------------------
folders = sorted(p for p in SKILLS.iterdir() if p.is_dir()) if SKILLS.is_dir() else []
print("Skills this repository ships (%d)" % len(folders))
check("there are skills to ship at all", len(folders) >= 20, len(folders))

DESC_MAX = 700          # in context every turn; an essay here is paid for on every message
skills: dict = {}
for d in folders:
    md = d / "SKILL.md"
    if not md.is_file():
        check("%s has a SKILL.md" % d.name, False, "missing")
        continue
    fm = frontmatter(md.read_text(encoding="utf-8", errors="replace"))
    skills[d.name] = (md, fm)

print("\nRule 1 — the folder and the frontmatter agree on the name")
for name, (md, fm) in skills.items():
    check("%s" % name, fm.get("name") == name, fm.get("name"))

print("\nRule 2 — there is a description, and it is bounded")
for name, (md, fm) in skills.items():
    desc = fm.get("description") or ""
    check("%s has one" % name, len(desc) > 20, desc[:40])
    check("...under %d chars" % DESC_MAX, len(desc) <= DESC_MAX, len(desc))

print("\nRule 3 — the body costs nothing until the switch is on")
for name, (md, fm) in skills.items():
    check("%s" % name, str(fm.get("disable-model-invocation", "")).lower() == "true",
          fm.get("disable-model-invocation"))

# ---------------------------------------------------------------------------
print("\nRule 4 — every mesh op a skill names really exists")
if not OPS_TS.is_file():
    skipped("the ops library is readable", "no ops.ts at %s" % OPS_TS)
else:
    src = OPS_TS.read_text(encoding="utf-8", errors="replace")
    i = src.find("interface Ops")
    j = src.find("\n}", i) if i >= 0 else -1
    real = set(re.findall(r"^\s{2}([a-zA-Z0-9_]+)[?(:]", src[i:j], re.M)) if j > 0 else set()
    check("ops.ts declares its operations", len(real) > 20, len(real))

    # Only the ops a skill actually tells an agent to CALL. Written as `ops.name(` or in a
    # backtick like `skin(object)` — the two shapes the skills use.
    called: dict = {}
    for name, (md, fm) in skills.items():
        t = md.read_text(encoding="utf-8", errors="replace")
        hits = set(re.findall(r"ops\.([a-zA-Z0-9_]+)\(", t))
        hits |= set(re.findall(r"`([a-zA-Z0-9_]+)\((?:object|obj)\)`", t))
        if hits:
            called[name] = hits
    check("at least one skill names the ops at all", bool(called), sorted(called))
    for name, hits in sorted(called.items()):
        bad = sorted(h for h in hits if h not in real)
        check("%s names only real ops" % name, not bad, bad)

# ---------------------------------------------------------------------------
print("\nThe two skills written from the Unity and Blender research")
for want, must_say in (("forge-director", "forge-ops.js"), ("web-build-weight", "gltf-transform")):
    d = SKILLS / want / "SKILL.md"
    check("%s ships" % want, d.is_file(), str(d))
    if d.is_file():
        t = d.read_text(encoding="utf-8", errors="replace")
        check("...and names the tool it depends on", must_say in t, must_say)

# THE ROUTER'S WHOLE JOB is to choose, so it has to name every path it chooses between. A path
# that is missing here is a path the agent will never take.
fd = SKILLS / "forge-director" / "SKILL.md"
if fd.is_file():
    t = fd.read_text(encoding="utf-8", errors="replace")
    for path in ("reuse", "forge", "hunyuan", "kiln", "img2threejs"):
        check("the director offers the %s path" % path, path in t)
    check("...and every path it names is a skill or an endpoint we have",
          "/api/live/forge" in t and "/api/engine/assets" in t)

# ---------------------------------------------------------------------------
print("\nWhat the setup script installs")
setup = ROOT / "setup.ps1"
if not setup.is_file():
    skipped("setup.ps1 installs the bundled skills", "no setup.ps1")
else:
    t = setup.read_text(encoding="utf-8", errors="replace")
    check("it copies the bundled skills to ~/.claude/skills",
          "skills" in t and ".claude\\skills" in t)

# ---------------------------------------------------------------------------
print("\nThe switch in the Skills tab")
# THE REAL ~/.claude IS NEVER TOUCHED. `skills` reads CLAUDE_CONFIG_DIR on every call, so a
# temporary home gives the module a whole world of its own to toggle in.
import shutil                                                              # noqa: E402
import tempfile                                                            # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="studio-skills-"))
_was = os.environ.get("CLAUDE_CONFIG_DIR")
os.environ["CLAUDE_CONFIG_DIR"] = str(TMP)
try:
    from asset_studio import skills as sk                                  # noqa: E402
    d = TMP / "skills" / "a-test-skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: a-test-skill\ndescription: a skill that exists only for this test\n---\n\nbody\n",
        encoding="utf-8")

    # First sight seeds "seen", so this one keeps whatever state it has rather than being
    # defaulted off — that rule is for skills that appear LATER.
    first = {x["id"]: x for x in sk.list_skills()}
    check("the new skill is listed", "a-test-skill" in first, sorted(first))

    def dmi() -> bool:
        return "disable-model-invocation: true" in (d / "SKILL.md").read_text(encoding="utf-8")

    sk.set_skill("a-test-skill", False)
    sk.invalidate()
    off = {x["id"]: x for x in sk.list_skills()}["a-test-skill"]
    check("switched off, the tab says so", off["enabled"] is False, off["enabled"])
    check("...and the body is kept out of context", dmi(), "no disable-model-invocation")

    sk.set_skill("a-test-skill", True)
    sk.invalidate()
    on = {x["id"]: x for x in sk.list_skills()}["a-test-skill"]
    check("switched on, the tab says so", on["enabled"] is True, on["enabled"])
    check("...and the body is available again", not dmi(), "still disabled")

    # A SECOND SKILL, APPEARING LATER, MUST DEFAULT OFF. This is the rule that stops a new
    # install quietly adding cost to every turn.
    d2 = TMP / "skills" / "a-later-skill"
    d2.mkdir(parents=True, exist_ok=True)
    (d2 / "SKILL.md").write_text(
        "---\nname: a-later-skill\ndescription: appeared after the first look\n---\n\nbody\n",
        encoding="utf-8")
    sk.invalidate()
    later = {x["id"]: x for x in sk.list_skills()}
    check("a skill that appears later defaults OFF",
          later.get("a-later-skill", {}).get("enabled") is False,
          later.get("a-later-skill", {}).get("enabled"))
    check("...and the one already on is left alone", later["a-test-skill"]["enabled"] is True,
          later["a-test-skill"]["enabled"])
finally:
    if _was is None:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = _was
    shutil.rmtree(TMP, ignore_errors=True)

print("\n  %d passed, %d failed, %d skipped" % (ok, fail, skip))
raise SystemExit(0 if not fail else 2)

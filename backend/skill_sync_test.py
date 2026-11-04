# -*- coding: utf-8 -*-
"""Skills travel to another computer, and no local work is lost on the way.

Everything happens in temporary folders. CLAUDE_CONFIG_DIR points away from the real library, and
the real `~/.claude/skills` is never read or written here.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP = Path(tempfile.mkdtemp(prefix="skill-sync-test-"))
os.environ["CLAUDE_CONFIG_DIR"] = str(TMP / "claude")
os.environ["ASSET_STUDIO_DATA"] = str(TMP / "data")

from asset_studio import skill_sync as ss  # noqa: E402
from asset_studio import skills  # noqa: E402

FAILS = 0
COUNT = 0


def check(name, cond, got=None):
    global FAILS, COUNT
    COUNT += 1
    if not cond:
        FAILS += 1
        print(f"FAIL {name}" + (f": {got!r}" if got is not None else ""))


ROOT = TMP / "studio root"
HOME = TMP / "home"
DEST = TMP / "claude" / "skills"
BUNDLE = TMP / "bundle"
FAKE_KEY = "sk-" + "a1B2" * 8     # built here so that this file itself holds no key shape


def write_skill(top: Path, name: str, body: str = "body", flag: bool | None = True, extra: dict | None = None):
    d = top / name
    d.mkdir(parents=True, exist_ok=True)
    head = f"---\nname: {name}\ndescription: test {name}\n"
    if flag:
        head += "disable-model-invocation: true\n"
    (d / "SKILL.md").write_text(head + "---\n" + body + "\n", encoding="utf-8", newline="\n")
    for rel, data in (extra or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return d


def manifest(**kw):
    BUNDLE.mkdir(parents=True, exist_ok=True)
    (BUNDLE / "manifest.json").write_text(json.dumps(kw), encoding="utf-8")
    ss._manifest_cache.clear()


def reset():
    for p in (DEST, BUNDLE, TMP / "claude" / "settings.json"):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    DEST.mkdir(parents=True)
    manifest()


def run(**kw):
    return ss.sync(dest=DEST, bundle=BUNDLE, root=ROOT, home=HOME, **kw)


def settings():
    return skills._load_settings()


# ---------------------------------------------------------------- paths travel
r, h = ROOT.as_posix(), HOME.as_posix()
raw = f"run {h}/x and {r}/backend, also {r.replace('/', chr(92))}\\tmp".encode()
neutral = ss.neutral(raw, ROOT, HOME)
check("no path of this computer stays", r.encode() not in neutral and h.encode() not in neutral
      and r.replace("/", "\\").encode() not in neutral, neutral)
check("the tokens are written", ss.ROOT_TOKEN.encode() in neutral and ss.HOME_TOKEN.encode() in neutral)
other = TMP / "other pc"
back = ss.render(neutral, other, other / "home").decode()
check("another computer gets its own paths", other.as_posix() in back and r not in back, back)
inside_home = HOME / "Studio"
n2 = ss.neutral(inside_home.as_posix().encode() + b"/x", inside_home, HOME)
check("a root inside the home folder keeps the root token", n2 == ss.ROOT_TOKEN.encode() + b"/x", n2)

# ---------------------------------------------------------------- a new computer
reset()
manifest(defaults_on=["on-one"], user_only=["typed"])
write_skill(BUNDLE, "on-one", "use {{STUDIO_ROOT}}/x and {{USER_HOME}}/y", extra={"tool.py": "print('{{STUDIO_ROOT}}')\n"})
write_skill(BUNDLE, "off-one")
write_skill(BUNDLE, "typed")
rep = run()
check("all arrive", sorted(rep["installed"]) == ["off-one", "on-one", "typed"], rep)
md = (DEST / "on-one" / "SKILL.md").read_text(encoding="utf-8")
check("paths are rendered in SKILL.md", f"{r}/x" in md and f"{h}/y" in md and "{{" not in md, md)
check("paths are rendered in a tool", r in (DEST / "on-one" / "tool.py").read_text(encoding="utf-8"))
st = settings()
check("a default-on skill is on", st["skillOverrides"]["on-one"] == "on"
      and not skills._dmi_disabled(skills._frontmatter((DEST / "on-one" / "SKILL.md").read_text(encoding="utf-8"))))
check("any other skill is off", st["skillOverrides"]["off-one"] == "off"
      and skills._dmi_disabled(skills._frontmatter((DEST / "off-one" / "SKILL.md").read_text(encoding="utf-8"))))
check("a typed-only skill answers but is not picked", st["skillOverrides"]["typed"] == "on"
      and skills._dmi_disabled(skills._frontmatter((DEST / "typed" / "SKILL.md").read_text(encoding="utf-8"))))
check("all are seen", {"on-one", "off-one", "typed"} <= set(st["studioSkillsSeen"]))
rep = run()
check("a second run has nothing to do", rep["current"] == 3 and not rep["installed"] and not rep["updated"], rep)

# ---------------------------------------------------------------- a skill of this computer keeps its state
reset()
write_skill(DEST, "mine", flag=False)
manifest(defaults_on=[])
write_skill(BUNDLE, "new-one")
run()
st = settings()
check("a first sync does not turn a local skill off", "off" != st["skillOverrides"].get("mine"), st)
check("the local skill counts as seen", "mine" in st["studioSkillsSeen"], st)
listed = {s["id"]: s for s in skills.list_skills("", "")}
check("the local skill is still on in the list", listed["mine"]["enabled"] is True, listed["mine"])

# ---------------------------------------------------------------- update, or keep
reset()
write_skill(BUNDLE, "kit", "v1", extra={"tool.py": "one\n"})
write_skill(BUNDLE, "edited", "v1")
write_skill(BUNDLE, "toggled", "v1")
write_skill(BUNDLE, "crlf", "v1")
run()
data = DEST / "kit" / "correction_luts"
data.mkdir()
(data / "mine.cube").write_text("user data", encoding="utf-8")
(DEST / "edited" / "SKILL.md").write_text("---\nname: edited\n---\nmy own words\n", encoding="utf-8")
skills._set_dmi_flag(DEST / "toggled" / "SKILL.md", False)
p = DEST / "crlf" / "SKILL.md"
p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
rep = run()
check("a switch is not an edit", "toggled" not in rep["kept"], rep)
check("a line ending is not an edit", "crlf" not in rep["kept"], rep)
check("a real edit is kept", rep["kept"] == ["edited"], rep)

write_skill(BUNDLE, "kit", "v2", extra={"tool.py": "two\n"})
write_skill(BUNDLE, "edited", "v2")
write_skill(BUNDLE, "toggled", "v2")
rep = run()
check("an untouched skill updates", "kit" in rep["updated"], rep)
check("the new content is there", "v2" in (DEST / "kit" / "SKILL.md").read_text(encoding="utf-8")
      and (DEST / "kit" / "tool.py").read_text(encoding="utf-8") == "two\n")
check("the user's data survives an update", (data / "mine.cube").is_file())
check("a local edit survives the update", "my own words" in (DEST / "edited" / "SKILL.md").read_text(encoding="utf-8"))
check("a switched skill updates", "toggled" in rep["updated"], rep)
check("the user's switch survives the update",
      not skills._dmi_disabled(skills._frontmatter((DEST / "toggled" / "SKILL.md").read_text(encoding="utf-8"))))
(BUNDLE / "kit" / "tool.py").unlink()
rep = run()
check("a file gone from the bundle goes", not (DEST / "kit" / "tool.py").exists(), rep)

shutil.rmtree(DEST / "kit")
rep = run()
check("a skill the user deleted stays deleted", rep["removed"] == ["kit"] and not (DEST / "kit").exists(), rep)

# ---------------------------------------------------------------- a junction is never written through
reset()
target = TMP / "junction-target"
if target.exists():
    shutil.rmtree(target)
write_skill(target.parent, target.name, "from the checkout")
write_skill(BUNDLE, "linked-one", "from the bundle")
made = False
if os.name == "nt":
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(DEST / "linked-one"), str(target)],
                          capture_output=True).returncode == 0
if made:
    rep = run()
    check("a junction is reported", rep["linked"] == ["linked-one"], rep)
    check("nothing is written through it", "from the checkout" in (target / "SKILL.md").read_text(encoding="utf-8"))
    os.rmdir(DEST / "linked-one")
else:
    print("SKIP junction: no mklink here")

# ---------------------------------------------------------------- a skill that lives upstream
reset()
runtime = ROOT / "runtime" / "fu" / "skills" / "from-runtime"
write_skill(runtime.parent, "from-runtime", "local checkout")
repo = TMP / "upstream.git"
if repo.exists():
    shutil.rmtree(repo, ignore_errors=True)
write_skill(repo / "skills", "from-git", "fetched")
git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
subprocess.run(git + ["add", "."], cwd=repo, check=True)
subprocess.run(git + ["commit", "-qm", "x"], cwd=repo, check=True)
sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
manifest(defaults_on=["from-git"], upstream={
    "from-runtime": {"repo": "https://invalid.invalid/x", "commit": "0" * 40, "path": "skills/from-runtime",
                     "runtime": "runtime/fu/skills/from-runtime"},
    "from-git": {"repo": repo.as_uri(), "commit": sha, "path": "skills/from-git"},
    "no-such": {"repo": repo.as_uri(), "commit": sha, "path": "skills/no-such"},
})
rep = run(fetch=False)
check("no network, no fetch", "from-git" in rep["waiting"] and not (DEST / "from-git").exists(), rep)
check("a local checkout needs no network", "from-runtime" in rep["installed"], rep)
rep = run()
check("the pinned commit is fetched", "from-git" in rep["installed"], rep)
check("the fetched skill is real", "fetched" in (DEST / "from-git" / "SKILL.md").read_text(encoding="utf-8"))
check("a failure is reported, not raised", "no-such" in rep["failed"], rep)
check("no .git folder comes along", not (DEST / "from-git" / ".git").exists())
check("its state follows the manifest", settings()["skillOverrides"]["from-git"] == "on")

# ---------------------------------------------------------------- refresh: the last gate before a commit
reset()
src = TMP / "src-skills"
if src.exists():
    shutil.rmtree(src)
root_real, home_real = ss._paths()
write_skill(src, "clean", f"see {root_real}/x", flag=False, extra={"__pycache__/a.pyc": b"x", "tmp/big.bin": b"x"})
write_skill(src, "leaky", "key " + FAKE_KEY)
write_skill(src, "personal", "ask " + Path.home().name.upper() + " about it")
write_skill(src, "private-one")
write_skill(src, "up-one")
write_skill(src, "synced")
manifest(private=["private-one"], upstream={"up-one": {}})
out = ss.refresh(["clean", "leaky", "personal", "private-one", "up-one", "synced", "absent"], src=src, bundle=BUNDLE)
check("a clean skill is copied", out["copied"] == ["clean"], out)
check("a secret blocks", "leaky" in out["blocked"], out)
check("a blocked skill reaches nothing", not (BUNDLE / "leaky").exists())
check("a personal term blocks", "personal" in out["blocked"], out)
check("private, upstream, managed and absent are refused",
      set(out["refused"]) == {"private-one", "up-one", "synced", "absent"}, out)
copied = (BUNDLE / "clean" / "SKILL.md").read_text(encoding="utf-8")
check("a path is neutral in the bundle", ss.ROOT_TOKEN in copied and root_real not in copied, copied)
check("the bundle holds a switched-on skill as off", skills._dmi_disabled(skills._frontmatter(copied)), copied)
check("scratch stays behind",not (BUNDLE / "clean" / "tmp").exists() and not (BUNDLE / "clean" / "__pycache__").exists())
check("the same again changes nothing", ss.refresh(["clean"], src=src, bundle=BUNDLE)["same"] == ["clean"])

# ---------------------------------------------------------------- the private pack
reset()
write_skill(DEST, "learned-x", "private words", flag=False, extra={"correction_luts/a.cube": "lut"})
write_skill(DEST, "off-x")
skills._save_settings({"studioSkillsSeen": ["learned-x", "off-x"], "skillOverrides": {"off-x": "off"}})
write_skill(DEST, "has-key", "key " + FAKE_KEY)
write_skill(DEST, "synced")
(DEST / "studio").mkdir()
(DEST / "studio" / "gen.sh").write_text("#!/bin/sh\n", encoding="utf-8")
out = ss.export_pack(out_dir=TMP / "packs", src=DEST)
zp = Path(out["path"])
with zipfile.ZipFile(zp) as z:
    names = z.namelist()
check("a private skill is in the pack", "skills/learned-x/SKILL.md" in names, names)
check("its data is in the pack", "skills/learned-x/correction_luts/a.cube" in names, names)
check("a folder with no SKILL.md is in the pack", "skills/studio/gen.sh" in names, names)
check("a secret is not", not any("has-key" in n for n in names) and "has-key" in out["skipped"], out)
check("the managed folder is not", not any("/synced/" in n for n in names), names)

reset()
write_skill(DEST, "off-x", "the version here", flag=False)
rep = ss.import_pack(zp, dest=DEST, root=ROOT, home=HOME)
check("a new skill installs", set(rep["installed"]) == {"learned-x", "studio"}, rep)
check("a different skill is kept", rep["kept"] == ["off-x"], rep)
check("the local version is untouched", "the version here" in (DEST / "off-x" / "SKILL.md").read_text(encoding="utf-8"))
check("the data arrives", (DEST / "learned-x" / "correction_luts" / "a.cube").is_file())
check("the on state arrives", settings()["skillOverrides"]["learned-x"] == "on", settings())
rep = ss.import_pack(zp, dest=DEST, root=ROOT, home=HOME, overwrite=True)
check("overwrite replaces", rep["replaced"] == ["off-x"], rep)
check("overwrite changes the words", "the version here" not in (DEST / "off-x" / "SKILL.md").read_text(encoding="utf-8"))
check("overwrite keeps the local switch",
      not skills._dmi_disabled(skills._frontmatter((DEST / "off-x" / "SKILL.md").read_text(encoding="utf-8"))))

write_skill(BUNDLE, "learned-x", "the bundle has this too")
rep = run()
check("a sync does not replace an imported skill", rep["kept"] == ["learned-x"], rep)

# A new computer: setup installs the short public copy, then the pack brings the long private one.
reset()
manifest()
write_skill(BUNDLE, "learned-x", "the short public copy")
run()
rep = ss.import_pack(zp, dest=DEST, root=ROOT, home=HOME)
check("a pack replaces an untouched bundled copy", rep["replaced"] == ["learned-x"], rep)
check("the private words are there", "private words" in (DEST / "learned-x" / "SKILL.md").read_text(encoding="utf-8"))
rep = run()
check("the next sync keeps the pack's copy", rep["kept"] == ["learned-x"], rep)

# A pack is a file from elsewhere: it must not reach outside the library.
bad = TMP / "bad.zip"
outside = TMP / "claude" / "escaped.txt"
with zipfile.ZipFile(bad, "w") as z:
    z.writestr("pack.json", json.dumps({"version": 1, "state": {"good": "on"}}))
    z.writestr("skills/good/SKILL.md", "---\nname: good\n---\nx\n")
    z.writestr("skills/../escaped.txt", "x")
    z.writestr("skills/good/../../../escaped.txt", "x")
    z.writestr("../../escaped.txt", "x")
    z.writestr("skills/C:/escaped.txt", "x")
    z.writestr("skills/synced/SKILL.md", "x")
    link = zipfile.ZipInfo("skills/lnk/SKILL.md")
    link.external_attr = 0o120777 << 16
    z.writestr(link, "x")
reset()
rep = ss.import_pack(bad, dest=DEST, root=ROOT, home=HOME)
check("the good entry installs", rep["installed"] == ["good"], rep)
check("nothing escapes", not outside.exists() and not (DEST.parent.parent / "escaped.txt").exists())
check("the bad entries are counted", rep["ignored"] >= 5, rep)
check("no managed folder arrives", not (DEST / "synced").exists())
check("no symlink arrives", not (DEST / "lnk").exists())

plain = TMP / "plain.zip"
with zipfile.ZipFile(plain, "w") as z:
    z.writestr("readme.txt", "x")
try:
    ss.import_pack(plain, dest=DEST)
    check("a zip with no index is refused", False)
except ValueError:
    check("a zip with no index is refused", True)
check("a download takes a pack name only", ss.pack_file("../../secret.zip") is None and ss.pack_file("x.zip") is None)

# ---------------------------------------------------------------- what a skill is for
check("a curated id wins", skills.group_of("resolve-edit", "ui", "a game") == "video")
check("a category is second", skills.group_of("unknown-a", "game-dev", "video") == "game")
check("the words are third", skills.group_of("unknown-b", "", "Cut a montage from footage on a timeline") == "video")
check("a name counts more than prose", skills.group_of("godot-tools", "", "a shader and a mesh") == "game")
check("a word must be whole", skills.group_of("unknown-c", "", "guide the building of a required quilt") == "other")
check("nothing to go on is other", skills.group_of("unknown-d") == "other")
ids = [g["id"] for g in skills.GROUPS]
check("every target is a group", set(skills._GROUP_OF_ID.values()) | set(skills._GROUP_OF_CATEGORY.values())
      | set(skills._GROUP_WORDS) <= set(ids))

# ---------------------------------------------------------------- what the screen says about a skill
reset()
manifest(needs={"needy": ["no-such-server"]}, upstream={"up-one": {}})
write_skill(BUNDLE, "bundled-one")
ss.bundle_dir, _orig = (lambda: BUNDLE), ss.bundle_dir
items = [{"id": "bundled-one", "scope": "personal"}, {"id": "up-one", "scope": "personal"},
         {"id": "here-only", "scope": "personal"}, {"id": "needy", "scope": "personal"},
         {"id": "of-project", "scope": "project"}]
ss.annotate(items)
ss.bundle_dir = _orig
by = {i["id"]: i for i in items}
check("a bundled skill travels", by["bundled-one"]["origin"] == "bundled", by)
check("an upstream skill travels", by["up-one"]["origin"] == "bundled", by)
check("a skill of this computer says so", by["here-only"]["origin"] == "local", by)
check("a skill of a project is not called local", by["of-project"]["origin"] == "", by)
check("a missing server is named", by["needy"]["needs"] == ["no-such-server"], by)

shutil.rmtree(TMP, ignore_errors=True)
print(f"skill_sync: {COUNT - FAILS}/{COUNT} passed")
sys.exit(1 if FAILS else 0)

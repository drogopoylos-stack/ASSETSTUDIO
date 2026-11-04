"""Skills travel with the Studio: a bundle for every computer, a private pack for the rest.

The library lives in the user's home (`~/.claude/skills`), so a fresh download of the Studio used to
arrive with an old copy of a part of it. `setup.ps1` copied a skill only if the folder was missing,
nothing ever updated one, and a path of this computer inside a skill went along unchanged.

Three ways in, one rule:

* `skills/` in the repository is the BUNDLE. `sync()` runs on every start of the primary backend and
  from `setup.ps1`, so a `git pull` and a restart are enough.
* A skill with no right to be copied around is fetched from its own repository at a pinned commit.
* A PACK is a zip the user carries by hand. It holds what must not go in a public repository: the
  skills mined from the user's own sessions, for example.

THE RULE: a skill is replaced only if it is still byte for byte what the Studio installed. The state
file records that hash. Anything else is a local edit and it stays. The on/off switch writes into
SKILL.md, so that one line is left out of the hash, or every toggle would read as an edit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from . import config, fsutil, skills

MANIFEST = "manifest.json"
STATE = ".studio-sync.json"
PACK_INDEX = "pack.json"

ROOT_TOKEN = "{{STUDIO_ROOT}}"
HOME_TOKEN = "{{USER_HOME}}"

# Never part of a skill: a checkout, scratch space, compiled files.
_SKIP_DIRS = {".git", "tmp", "__pycache__", "node_modules", ".venv"}
_SKIP_SUFFIX = {".bak", ".pyc", ".tmp"}
# The user's own output inside a skill folder. It stays out of the bundle and survives an update,
# and a pack, which is private, carries it.
_DATA_DIRS = {"correction_luts"}
# Managed by the Claude harness for the account. A copy would fight with it.
_NEVER = {"synced"}

_TEXT = {".md", ".py", ".sh", ".ps1", ".json", ".txt", ".yaml", ".yml", ".toml", ".cmd", ".bat",
         ".js", ".ts", ".html", ".css", ".csv"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
_DMI_LINE = re.compile(rb"^[ \t]*disable-model-invocation:.*\n?", re.I | re.M)

# A secret must never reach a repository or a pack. Each pattern is a known key shape.
_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:api[_-]?key|secret|token|password)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-/+]{24,}[\"']",
    re.I)

_PACK_MAX_FILES = 20000
_PACK_MAX_BYTES = 1024 * 1024 * 1024

_lock = threading.Lock()
_last: dict = {}
_manifest_cache: dict = {}


def bundle_dir() -> Path:
    return Path(config.ROOT_DIR) / "skills"


def dest_dir() -> Path:
    return skills._claude_home() / "skills"


def _is_text(name: str) -> bool:
    return Path(name).suffix.lower() in _TEXT


def _is_link(p: Path) -> bool:
    """A symlink or a junction. The Fusion skills here are junctions into `runtime/`, and writing
    through one would change the checkout it points at."""
    try:
        if p.is_symlink() or os.path.isjunction(p):
            return True
    except (OSError, AttributeError):
        pass
    return False


def _walk(top: Path, keep_data: bool = False):
    """(path, relative posix name) for every file that belongs to a skill, in a stable order."""
    skip = _SKIP_DIRS if keep_data else _SKIP_DIRS | _DATA_DIRS
    for base, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        for f in sorted(files):
            if Path(f).suffix.lower() in _SKIP_SUFFIX:
                continue
            full = Path(base) / f
            yield full, full.relative_to(top).as_posix()


def _paths(root: Path | None = None, home: Path | None = None) -> tuple[str, str]:
    r = Path(root or config.ROOT_DIR).as_posix().rstrip("/")
    h = Path(home or Path.home()).as_posix().rstrip("/")
    return r, h


def render(data: bytes, root: Path | None = None, home: Path | None = None) -> bytes:
    """A bundled file as this computer needs it."""
    r, h = _paths(root, home)
    return (data.replace(ROOT_TOKEN.encode(), r.encode("utf-8"))
                .replace(HOME_TOKEN.encode(), h.encode("utf-8")))


def neutral(data: bytes, root: Path | None = None, home: Path | None = None) -> bytes:
    """A file of this computer as any computer can read it. The Studio root first: it can be inside
    the home folder. A backslash form in a command is also written, and read back as forward slashes,
    which every shell and tool here accepts."""
    r, h = _paths(root, home)
    out = data
    for real, token in ((r, ROOT_TOKEN), (h, HOME_TOKEN)):
        back = real.replace("/", "\\")
        for form in (real, back, back.replace("\\", "\\\\")):
            out = out.replace(form.encode("utf-8"), token.encode())
    return out


def _normal(name: str, data: bytes) -> bytes:
    """The bytes that count for "has this changed": no line-ending noise, no on/off flag."""
    if _is_text(name):
        data = data.replace(b"\r\n", b"\n")
    if name == "SKILL.md":
        data = _DMI_LINE.sub(b"", data)
    return data


def tree_hash(top: Path, rendered: bool = False, root: Path | None = None, home: Path | None = None) -> str:
    h = hashlib.sha256()
    for full, rel in _walk(top):
        try:
            data = full.read_bytes()
        except OSError:
            continue
        if rendered and _is_text(rel):
            data = render(data, root, home)
        h.update(rel.encode("utf-8") + b"\0" + _normal(rel, data) + b"\0")
    return h.hexdigest()


def load_manifest(bundle: Path | None = None) -> dict:
    p = (bundle or bundle_dir()) / MANIFEST
    try:
        mt = p.stat().st_mtime
        hit = _manifest_cache.get(str(p))
        if hit and hit[0] == mt:
            return hit[1]
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    got = got if isinstance(got, dict) else {}
    _manifest_cache[str(p)] = (mt, got)
    return got


def _read_state(dest: Path) -> dict:
    try:
        got = json.loads((dest / STATE).read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(dest: Path, state: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / (STATE + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    fsutil.replace(tmp, dest / STATE)


def _flag_of(md: Path) -> bool:
    try:
        return skills._dmi_disabled(skills._frontmatter(md.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def _write_tree(src: Path, target: Path, root: Path | None = None, home: Path | None = None,
                transform: bool = True, keep_data: bool = False) -> None:
    """Make `target` hold what `src` holds. File by file, so the user's data folders stay.
    `keep_data` also copies those folders from `src`: a pack carries them, the bundle does not."""
    wanted = {rel: full for full, rel in _walk(src, keep_data=keep_data)}
    if target.exists():
        for full, rel in list(_walk(target)):
            if rel not in wanted:
                try:
                    full.unlink()
                except OSError:
                    pass
    for rel, full in wanted.items():
        data = full.read_bytes()
        if transform and _is_text(rel):
            data = render(data, root, home)
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".synctmp")
        tmp.write_bytes(data)
        fsutil.replace(tmp, out)


def _set_default(dest: Path, names_state: dict) -> None:
    """The first on/off state of skills that have just arrived, in the form `skills.py` reads.

    `{name: "on" | "off" | "user"}`. "user" is a skill only the user may start: the override is on,
    so a typed /name works, and the frontmatter flag stays, so the model cannot pick it by itself.
    """
    if not names_state:
        return
    data = skills._load_settings()
    if "studioSkillsSeen" in data:
        seen = set(data.get("studioSkillsSeen") or [])
    else:
        # This write creates the key, and then `skills.py` no longer sees a first run. The skills
        # that were here before this sync keep their state only if they are named now.
        seen = {p.name for p in dest.iterdir() if p.is_dir()} if dest.exists() else set()
    ov = dict(data.get("skillOverrides") or {})
    for name, want in names_state.items():
        seen.add(name)
        ov[name] = "off" if want == "off" else "on"
        md = dest / name / "SKILL.md"
        if md.is_file():
            skills._set_dmi_flag(md, want != "on")
    data["studioSkillsSeen"] = sorted(seen)
    data["skillOverrides"] = ov
    skills._save_settings(data)


def _default_of(manifest: dict, name: str) -> str:
    if name in (manifest.get("user_only") or []):
        return "user"
    return "on" if name in (manifest.get("defaults_on") or []) else "off"


def _fetch_repo(repo: str, commit: str, into: Path) -> None:
    """One commit of a repository, with no history. Nothing in it is run."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for args in (["init", "-q"], ["fetch", "-q", "--depth", "1", repo, commit], ["checkout", "-q", "FETCH_HEAD"]):
        r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=into, env=env, creationflags=flags,
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "git failed").strip()[:300])


def _install_upstream(manifest: dict, dest: Path, report: dict, fresh: dict, fetch: bool,
                      root: Path | None) -> None:
    wanted = {n: u for n, u in (manifest.get("upstream") or {}).items()
              if isinstance(u, dict) and not (dest / n).exists() and _SAFE_NAME.match(n)}
    repos: dict = {}
    try:
        for name, u in wanted.items():
            try:
                # The Resolve setup may already hold the checkout, and that needs no network.
                local = Path(root or config.ROOT_DIR) / str(u.get("runtime") or "\0")
                if u.get("runtime") and local.is_dir():
                    src = local
                else:
                    if not fetch:
                        report["waiting"].append(name)
                        continue
                    key = (u["repo"], u["commit"])
                    if key not in repos:
                        tmp = Path(tempfile.mkdtemp(prefix="skill-fetch-"))
                        repos[key] = tmp
                        _fetch_repo(u["repo"], u["commit"], tmp)
                    src = repos[key] / u.get("path", "")
                if not (src / "SKILL.md").is_file():
                    raise RuntimeError("no SKILL.md at %s" % u.get("path", ""))
                # Copied as it is: an upstream skill holds no placeholder, and it is not ours to edit.
                _write_tree(src, dest / name, transform=False)
                fresh[name] = _default_of(manifest, name)
                report["installed"].append(name)
            except Exception as e:
                report["failed"][name] = str(e)[:300]
    finally:
        for tmp in repos.values():
            shutil.rmtree(tmp, ignore_errors=True)


def sync(dest: Path | None = None, bundle: Path | None = None, fetch: bool = True,
         root: Path | None = None, home: Path | None = None) -> dict:
    """Bring this computer's library up to the bundle, and keep every local edit."""
    dest = dest or dest_dir()
    bundle = bundle or bundle_dir()
    report: dict = {"installed": [], "updated": [], "kept": [], "linked": [], "removed": [], "current": 0,
                    "waiting": [], "failed": {}, "at": time.time()}
    with _lock:
        manifest = load_manifest(bundle)
        state = _read_state(dest)
        fresh: dict = {}
        try:
            names = sorted(p.name for p in bundle.iterdir() if (p / "SKILL.md").is_file())
        except OSError:
            names = []
        for name in names:
            src, target = bundle / name, dest / name
            try:
                if _is_link(target):
                    report["linked"].append(name)
                    continue
                want = tree_hash(src, rendered=True, root=root, home=home)
                if not target.exists():
                    if name in state:           # installed before, so the user deleted it
                        report["removed"].append(name)
                        continue
                    _write_tree(src, target, root, home)
                    state[name] = want
                    fresh[name] = _default_of(manifest, name)
                    report["installed"].append(name)
                    continue
                have = tree_hash(target)
                if have == want:
                    state[name] = want
                    report["current"] += 1
                elif state.get(name) == have:
                    flag = _flag_of(target / "SKILL.md")
                    _write_tree(src, target, root, home)
                    skills._set_dmi_flag(target / "SKILL.md", flag)     # the user's switch stays
                    state[name] = want
                    report["updated"].append(name)
                else:
                    report["kept"].append(name)
            except Exception as e:
                report["failed"][name] = str(e)[:300]
        _install_upstream(manifest, dest, report, fresh, fetch, root)
        try:
            _set_default(dest, fresh)
            _write_state(dest, state)
        except Exception as e:
            report["failed"]["(state)"] = str(e)[:300]
        skills.invalidate()
        global _last
        _last = report
    return report


def start_background() -> None:
    """On a start of the primary backend. In a thread: a first install fetches over the network."""
    def run():
        try:
            r = sync()
            done = {k: len(r[k]) for k in ("installed", "updated", "kept", "linked") if r[k]}
            print(f"[skills] sync: {done or 'nothing to do'}"
                  + (f" failed: {sorted(r['failed'])}" if r["failed"] else ""), flush=True)
        except Exception as e:
            print(f"[skills] sync failed: {e}", flush=True)
    threading.Thread(target=run, daemon=True, name="skill-sync").start()


def last_report() -> dict:
    return dict(_last)


# ---------------------------------------------------------------------------
# What the Skills screen shows beside a skill
# ---------------------------------------------------------------------------
def _mcp_servers() -> set:
    out: set = set()
    home = skills._claude_home()
    for p in (Path.home() / ".claude.json", home / ".claude.json"):
        try:
            got = json.loads(p.read_text(encoding="utf-8"))
            out |= set((got.get("mcpServers") or {}).keys())
        except (OSError, ValueError, AttributeError):
            continue
    return out


def annotate(items: list[dict]) -> None:
    """`origin` says whether a skill arrives by itself on another computer, and `needs` names a
    server the skill calls and this computer does not have."""
    manifest = load_manifest()
    travels = set(manifest.get("upstream") or {}) | set(manifest.get("setup") or [])
    try:
        travels |= {p.name for p in bundle_dir().iterdir() if (p / "SKILL.md").is_file()}
    except OSError:
        pass
    needs = manifest.get("needs") or {}
    servers = _mcp_servers() if needs else set()
    for it in items:
        it["origin"] = ("bundled" if it.get("id") in travels
                        else "local" if it.get("scope") in ("personal", "shared") else "")
        missing = [s for s in (needs.get(it.get("id")) or []) if s not in servers]
        if missing:
            it["needs"] = missing


# ---------------------------------------------------------------------------
# Refresh the bundle from this computer (a developer step, before a commit)
# ---------------------------------------------------------------------------
def _own_terms() -> list[str]:
    """Words that identify this user, read from this computer so none is written in a public file."""
    terms = [Path.home().name]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    for key in ("user.email", "user.name"):
        try:
            r = subprocess.run(["git", "config", key], capture_output=True, text=True, timeout=10,
                               creationflags=flags, cwd=str(config.ROOT_DIR))
            if r.stdout.strip():
                terms.append(r.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return [t for t in terms if len(t) >= 3]


def _scan(name: str, data: bytes, terms: list[str]) -> list[str]:
    if not _is_text(name):
        return []
    text = data.decode("utf-8", "ignore")
    found = []
    if _SECRET.search(text):
        found.append("a secret")
    for t in terms:
        if t.lower() in text.lower():
            found.append("the term %r" % t)
    return found


def _shipped_off(data: bytes) -> bytes:
    """The bundle holds every SKILL.md switched off. `sync` decides the state on each computer."""
    text = data.decode("utf-8", "ignore").replace("\r\n", "\n")
    m = re.match(r"\s*---\n(.*?)\n---", text, re.S)
    if not m:
        return data
    head = [ln for ln in m.group(1).split("\n") if not ln.strip().lower().startswith("disable-model-invocation:")]
    head.append("disable-model-invocation: true")
    return (text[:m.start(1)] + "\n".join(head) + text[m.end(1):]).encode("utf-8")


def refresh(names: list[str] | None = None, src: Path | None = None, bundle: Path | None = None) -> dict:
    """Copy this computer's skills into `skills/`, with its paths made neutral.

    A skill with a secret or a word that identifies the user is not copied. The repository is public
    and this is the last check before a commit.
    """
    src = src or dest_dir()
    bundle = bundle or bundle_dir()
    manifest = load_manifest(bundle)
    upstream = set(manifest.get("upstream") or {})
    private = set(manifest.get("private") or [])
    if names is None:
        names = sorted(p.name for p in bundle.iterdir() if (p / "SKILL.md").is_file())
    terms = _own_terms()
    out: dict = {"copied": [], "same": [], "blocked": {}, "refused": []}
    for name in names:
        top = src / name
        if name in _NEVER or name in private or name in upstream or not (top / "SKILL.md").is_file():
            out["refused"].append(name)
            continue
        rows = []
        hits = []
        for full, rel in _walk(top):
            data = full.read_bytes()
            if _is_text(rel):
                data = neutral(data)
            if rel == "SKILL.md":
                data = _shipped_off(data)
            for why in _scan(rel, data, terms):
                hits.append("%s: %s" % (rel, why))
            rows.append((rel, data))
        if hits:
            out["blocked"][name] = hits
            continue
        target = bundle / name
        wanted = {rel for rel, _ in rows}
        if target.exists():
            for full, rel in list(_walk(target, keep_data=True)):
                if rel not in wanted:
                    full.unlink()
        changed = False
        for rel, data in rows:
            p = target / rel
            old = p.read_bytes() if p.exists() else None
            if old is None or _normal(rel, old) != _normal(rel, data):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                changed = True
        out["copied" if changed else "same"].append(name)
    return out


# ---------------------------------------------------------------------------
# A private pack: the whole library in one zip
# ---------------------------------------------------------------------------
def export_pack(out_dir: Path | None = None, src: Path | None = None) -> dict:
    """Write a zip of every skill here. A skill that holds a secret stays behind, and it is named."""
    src = src or dest_dir()
    out_dir = out_dir or (Path(config.DATA_DIR) / "skill-packs")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "asset-studio-skills-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
    target = out_dir / name
    index: dict = {"version": 1, "created": time.strftime("%Y-%m-%d %H:%M"), "state": {}, "skills": []}
    skipped: dict = {}
    overrides = dict(skills._load_settings().get("skillOverrides") or {})
    tmp = target.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for top in sorted(p for p in src.iterdir() if p.is_dir()):
            if top.name in _NEVER or not _SAFE_NAME.match(top.name):
                continue
            rows = []
            hits = []
            for full, rel in _walk(top, keep_data=True):
                try:
                    data = full.read_bytes()
                except OSError:
                    continue
                if _is_text(rel):
                    data = neutral(data)
                    if _SECRET.search(data.decode("utf-8", "ignore")):
                        hits.append(rel)
                rows.append((rel, data))
            if hits:
                skipped[top.name] = hits
                continue
            for rel, data in rows:
                zf.writestr("skills/%s/%s" % (top.name, rel), data)
            index["skills"].append(top.name)
            md = top / "SKILL.md"
            if md.is_file():
                flag = _flag_of(md)
                index["state"][top.name] = ("off" if overrides.get(top.name) == "off"
                                            else "user" if flag else "on")
        index["skipped"] = skipped
        zf.writestr(PACK_INDEX, json.dumps(index, indent=1))
    fsutil.replace(tmp, target)
    return {"file": name, "path": str(target), "size": target.stat().st_size,
            "skills": index["skills"], "skipped": skipped}


def pack_file(name: str) -> Path | None:
    """The path of an exported pack, for its download. Only a plain file name of that folder."""
    if not re.fullmatch(r"asset-studio-skills-[0-9-]+\.zip", name or ""):
        return None
    p = Path(config.DATA_DIR) / "skill-packs" / name
    return p if p.is_file() else None


def _safe_member(info: zipfile.ZipInfo) -> tuple[str, str] | None:
    """(skill, relative path) of one zip entry, or None. The zip is untrusted input."""
    raw = info.filename
    if info.is_dir() or "\\" in raw or raw.startswith("/") or ":" in raw:
        return None
    parts = raw.split("/")
    if len(parts) < 3 or parts[0] != "skills" or any(x in ("", ".", "..") for x in parts):
        return None
    if (info.external_attr >> 16) & 0o170000 == 0o120000:      # a symlink
        return None
    if not _SAFE_NAME.match(parts[1]) or parts[1] in _NEVER:
        return None
    if parts[2] in _SKIP_DIRS or parts[-1] == STATE:
        return None
    return parts[1], "/".join(parts[2:])


def import_pack(zip_path: Path, overwrite: bool = False, dest: Path | None = None,
                root: Path | None = None, home: Path | None = None) -> dict:
    """Install the skills of a pack. A skill that exists and differs stays, unless `overwrite`."""
    dest = dest or dest_dir()
    report: dict = {"installed": [], "replaced": [], "kept": [], "current": [], "linked": [], "ignored": 0}
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > _PACK_MAX_FILES or sum(i.file_size for i in infos) > _PACK_MAX_BYTES:
            raise ValueError("this pack is too large")
        try:
            index = json.loads(zf.read(PACK_INDEX).decode("utf-8"))
        except (KeyError, ValueError):
            raise ValueError("this file is not a skill pack") from None
        states = index.get("state") if isinstance(index, dict) else {}
        states = states if isinstance(states, dict) else {}
        with tempfile.TemporaryDirectory(prefix="skill-pack-") as td:
            work = Path(td)
            names: set = set()
            for info in infos:
                got = _safe_member(info)
                if got is None:
                    report["ignored"] += info.filename != PACK_INDEX and not info.is_dir()
                    continue
                skill, rel = got
                out = work / skill / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(info))
                names.add(skill)
            with _lock:
                state = _read_state(dest)
                fresh: dict = {}
                for skill in sorted(names):
                    src, target = work / skill, dest / skill
                    if _is_link(target):
                        report["linked"].append(skill)
                        continue
                    existed = target.exists()
                    if existed and tree_hash(target) == tree_hash(src, rendered=True, root=root, home=home):
                        report["current"].append(skill)
                        continue
                    # What the bundle installed and nobody touched is not local work. On a new
                    # computer the pack must win over the shorter public copy of the same skill.
                    if existed and not overwrite and state.get(skill) != tree_hash(target):
                        report["kept"].append(skill)
                        continue
                    flag = _flag_of(target / "SKILL.md") if existed else False
                    _write_tree(src, target, root, home, keep_data=True)
                    # Not installed by the bundle, so the next sync must treat it as a local edit.
                    state.pop(skill, None)
                    if existed:
                        skills._set_dmi_flag(target / "SKILL.md", flag)
                        report["replaced"].append(skill)
                    else:
                        report["installed"].append(skill)
                        if (src / "SKILL.md").is_file():
                            want = states.get(skill)
                            fresh[skill] = want if want in ("on", "off", "user") else "off"
                _set_default(dest, fresh)
                _write_state(dest, state)
    skills.invalidate()
    return report


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        print(json.dumps(sync(), indent=1))
    elif cmd == "refresh":
        print(json.dumps(refresh(sys.argv[2:] or None), indent=1))
    else:
        sys.exit("usage: python -m asset_studio.skill_sync [sync | refresh [name ...]]")

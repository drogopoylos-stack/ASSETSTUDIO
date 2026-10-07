"""Claude Code skills — list what's installed, toggle each on/off, track when added.

Personal skills (~/.claude/skills/<name>/SKILL.md) are available in EVERY project
and in the studio's headless `claude -p` runs. Enable/disable is enforced two ways:

* ``disable-model-invocation: true`` in the SKILL.md frontmatter — the Claude-native
  way to keep a skill from auto-loading (so it costs no context until enabled), and
* the ``skillOverrides`` map in ~/.claude/settings.json ("on" | "off") for the studio UI.

NEW skills start DISABLED: the studio remembers which skill ids it has already seen
(``studioSkillsSeen``); anything that appears later defaults to off so the user opts
it in from the Skills tab. Existing skills are seeded as "seen" on first run so they
keep their current state.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from . import engines, fsutil


def _claude_home() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) if base else (Path.home() / ".claude")


def _settings_path() -> Path:
    return _claude_home() / "settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    fsutil.replace(tmp, p)


def _fmt_ts(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def _frontmatter(text: str) -> dict:
    """Pull fields from a SKILL.md YAML frontmatter block (no yaml dep). Works for both
    top-level keys and ones nested under ``metadata:`` (indentation is stripped first)."""
    out: dict = {}
    if not text.lstrip().startswith("---"):
        return out
    body = text.lstrip()[3:]
    end = body.find("\n---")
    block = body[:end] if end != -1 else body
    keys = ("name", "description", "created", "updated", "category", "disable-model-invocation")
    for line in block.splitlines():
        s = line.strip()
        for key in keys:
            if s.lower().startswith(key + ":") and key not in out:
                out[key] = s.split(":", 1)[1].strip().strip("\"'")
    return out


def _dmi_disabled(fm: dict) -> bool:
    """True if the frontmatter marks the skill user-invoked-only (not auto-loaded)."""
    return str(fm.get("disable-model-invocation", "")).strip().lower() in ("true", "yes", "1")


def _set_dmi_flag(md: Path, disabled: bool) -> bool:
    """Add/remove `disable-model-invocation: true` inside a SKILL.md frontmatter block.
    This is what actually keeps a skill from auto-loading (zero context cost) until the
    user enables it. Returns True on a successful write."""
    try:
        text = md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lines = text.splitlines(keepends=True)
    start = end = None
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            if start is None:
                start = i
            else:
                end = i
                break
    if start is None or end is None:
        return False
    block = [ln for ln in lines[start + 1:end]
             if not ln.strip().lower().startswith("disable-model-invocation:")]
    if disabled:
        block.append("disable-model-invocation: true\n")
    try:
        md.write_text("".join(lines[:start + 1] + block + lines[end:]), encoding="utf-8")
        return True
    except OSError:
        return False


def _entry(sk_dir: Path, md: Path, scope: str) -> dict:
    fm: dict = {}
    try:
        fm = _frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
    try:
        ctime = md.stat().st_ctime   # creation time on Windows
    except OSError:
        ctime = 0.0
    return {
        "id": sk_dir.name, "name": fm.get("name") or sk_dir.name,
        "description": fm.get("description", ""), "scope": scope,
        "category": fm.get("category", ""),
        "created": fm.get("created") or _fmt_ts(ctime),
        "created_ts": ctime,
        "updated": fm.get("updated", ""),
        "_dmi": _dmi_disabled(fm),
        "_md": str(md),
    }


def _scan_dir(dir_path: Path, scope: str) -> list[dict]:
    out: list[dict] = []
    if not dir_path.exists():
        return out
    try:
        children = sorted(dir_path.iterdir(), key=lambda x: x.name.lower())
    except OSError:
        return out
    for sk in children:
        md = sk / "SKILL.md"
        if sk.is_dir() and md.exists():
            out.append(_entry(sk, md, scope))
    return out


def _project_cwd(project_id: str) -> str:
    """The folder a session is in. Tried prefixed first (so an engine's own universe is asked),
    then bare — a folder that only DeepSeek or Codex has run in has no Claude project dir under the
    prefixed id, and answering "" there would take the project roots away with it."""
    from . import cc_session
    bare = engines.bare_folder(project_id)
    for pid in ([project_id, bare] if bare != project_id else [project_id]):
        try:
            cwd, _ = cc_session._resolve(pid)
            if cwd:
                return cwd
        except Exception:
            continue
    return ""


# --- the DeepSeek Harness runtime's skill roots -----------------------------------------------
#
# THE SAME LIBRARY, MOUNTED TWICE. The Skills button used to be a Claude-only control: it read
# `~/.claude/skills` and said so, so on a DeepSeek pane it either listed another engine's skills or
# nothing at all. The DSH runtime discovers `<root>/<name>/SKILL.md` one level deep from these roots
# (rank order, read out of the installed runtime's `skill-filesystem` package):
#
#     100  project-dsh     <projectRoot>/.dsh/skills
#     200  project-agents  <projectRoot>/.agents/skills
#     300  custom          Config.customSkillDirs — where `deepseek_session` mounts ~/.claude/skills
#     400  user-dsh        <dshHome>/skills        (per-project runtime dir; see runtime_home)
#     500  user-agents     <agentsHome>/skills     ($DSH_AGENTS_HOME or ~/.agents)
#
# and its on/off switch is the SAME frontmatter key Claude uses, `disable-model-invocation`
# (surfaced as `modelInvocable` on the runtime's skills API). So the toggle needs no second
# mechanism: flip the flag in the file and both engines obey it.
#
# PROVEN, not assumed: a live `deepseek-flash` turn in a throwaway directory with no other root in
# reach, asked to list its catalog, answered "graphify" — and `graphify` is the one skill enabled in
# the shared library. See docs/DeepSeek Harness.md.
_FLAG_SCOPES = ("personal", "shared", "project-dsh", "project-agents", "user-dsh", "user-agents")


def _dsh_roots(project_id: str) -> list[tuple[str, Path]]:
    """(scope, dir) for every root the DeepSeek runtime scans, in its own rank order."""
    out: list[tuple[str, Path]] = []
    cwd = _project_cwd(project_id) if project_id else ""
    if cwd:
        base = Path(cwd)
        out.append(("project-dsh", base / ".dsh" / "skills"))
        out.append(("project-agents", base / ".agents" / "skills"))
    shared = _claude_home() / "skills"
    if shared.is_dir():
        out.append(("shared", shared))
    try:                       # the per-project dshHome, plus the shared user root
        from . import deepseek_session
        out.append(("user-dsh", deepseek_session.runtime_home(engines.bare_folder(project_id)) / "skills"))
    except Exception:
        pass
    agents_home = os.environ.get("DSH_AGENTS_HOME") or str(Path.home() / ".agents")
    out.append(("user-agents", Path(agents_home) / "skills"))
    return out


def _claude_roots(project_id: str) -> list[tuple[str, Path]]:
    """(scope, dir) for the Claude Code engine: personal home + plugins + this project's own.

    The home is per ENGINE, not always `~/.claude`: Kimi and Qwen run the same CLI with their own
    `CLAUDE_CONFIG_DIR` (see `cc_session._alt_env`), so their skills live in their own home and
    listing Claude's there would be wrong in both directions.
    """
    home = _claude_home()
    if project_id:
        try:
            from . import mission
            alt = mission.claude_home(project_id)
            if alt and str(alt) != str(home):
                home = alt
        except Exception:
            pass
    out: list[tuple[str, Path]] = [("personal", home / "skills")]
    plugins = home / "plugins"
    if plugins.exists():
        out.append(("plugin", plugins))
    cwd = _project_cwd(project_id) if project_id else ""
    if cwd:
        out.append(("project", Path(cwd) / ".claude" / "skills"))
    return out


def _roots_for(project_id: str, agent: str) -> list[tuple[str, Path]]:
    """Which skill library the panel is showing, chosen by the engine the pane is set to.

    Codex answers with nothing ON PURPOSE: it has no per-task skill mechanism (its equivalent is
    AGENTS.md, which the agent reads as standing instructions rather than selecting a skill), and an
    empty list plus the panel's own sentence is the honest answer. Listing Claude's skills there
    would be a lie the user could not see through until one silently failed to load.
    """
    return engines.for_agent(agent).skill_roots(project_id)


_LIST_CACHE: dict = {}          # project_id -> (expires_at, result)
_LIST_TTL = 20.0


def invalidate() -> None:
    """Skills changed (a toggle, an install) — drop the cache so the next read is honest."""
    _LIST_CACHE.clear()


def list_skills(project_id: str = "", agent: str = "") -> list[dict]:
    """Cached briefly. Building this walks ~/.claude/plugins for SKILL.md and parses the
    frontmatter of every hit — measured ~400 ms — and it is fetched whenever the composer or the
    Skills panel mounts. Skills change when the user toggles one, which invalidates the cache,
    so the only staleness is a file edited by hand outside the Studio.

    `agent` picks WHOSE library: Claude's own home, DeepSeek's runtime roots, or nothing at all for
    Codex. Cached per (project, agent) — one cache entry for two engines showed the second one the
    first one's list.
    """
    import time as _t
    key = (project_id, agent)
    hit = _LIST_CACHE.get(key)
    if hit and hit[0] > _t.monotonic():
        return hit[1]
    out = _list_skills_uncached(project_id, agent)
    _LIST_CACHE[key] = (_t.monotonic() + _LIST_TTL, out)
    return out


def _list_skills_uncached(project_id: str = "", agent: str = "") -> list[dict]:
    data = _load_settings()
    overrides = dict(data.get("skillOverrides") or {})
    items: list[dict] = []
    seen_dirs: set = set()
    for scope, d in _roots_for(project_id, agent):
        try:
            k = str(d).lower()
        except Exception:
            continue
        if k in seen_dirs:      # the shared library and a project root can name the same dir
            continue
        seen_dirs.add(k)
        if scope == "plugin":   # plugin skills (best-effort, bounded)
            if d.exists():
                try:
                    for md in list(d.rglob("SKILL.md"))[:300]:
                        items.append(_entry(md.parent, md, "plugin"))
                except OSError:
                    pass
            continue
        items += _scan_dir(d, scope)

    # NEW skills default to OFF. Seed "seen" with everything present on first run so
    # existing skills keep their state; only ids that appear later default disabled.
    seen = set(data.get("studioSkillsSeen") or [])
    first_run = "studioSkillsSeen" not in data
    changed = False
    if first_run:
        seen = {it["id"] for it in items}
        data["studioSkillsSeen"] = sorted(seen)
        changed = True
    else:
        for it in items:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            overrides[it["id"]] = "off"                       # default the newcomer off
            if it["scope"] == "personal" and not it["_dmi"]:  # real enforcement on our skills
                if _set_dmi_flag(Path(it["_md"]), True):
                    it["_dmi"] = True
            changed = True
        if changed:
            data["studioSkillsSeen"] = sorted(seen)
    if changed:
        data["skillOverrides"] = overrides
        _save_settings(data)

    # de-dupe by id; disabled if the frontmatter flag is set OR the override is "off"
    out: list[dict] = []
    dedupe: set[str] = set()
    for it in items:
        if it["id"] in dedupe:
            continue
        dedupe.add(it["id"])
        disabled = it["_dmi"] or overrides.get(it["id"], "on") == "off"
        # make a disabled choice REAL on our own skills: the frontmatter flag actually keeps the
        # engine from auto-loading it (token savings), vs. the studio-only override map. Read by
        # BOTH engines — Claude and the DeepSeek runtime — so one switch covers a shared skill.
        if disabled and not it["_dmi"] and it["scope"] in _FLAG_SCOPES and it.get("_md"):
            if _set_dmi_flag(Path(it["_md"]), True):
                it["_dmi"] = True
        it["enabled"] = not disabled
        it["state"] = "off" if disabled else "on"
        it.pop("_dmi", None)
        it.pop("_md", None)
        out.append(it)
    out.sort(key=lambda x: x.get("created_ts") or 0, reverse=True)  # newest added first
    return out


def set_skill(skill_id: str, enabled: bool, project_id: str = "", agent: str = "") -> dict:
    invalidate()          # a toggle must show up immediately, not after the cache expires
    data = _load_settings()
    ov = dict(data.get("skillOverrides") or {})
    ov[skill_id] = "on" if enabled else "off"
    data["skillOverrides"] = ov
    seen = set(data.get("studioSkillsSeen") or [])
    seen.add(skill_id)                       # a user decision counts as "seen"
    data["studioSkillsSeen"] = sorted(seen)
    _save_settings(data)
    # Real enforcement: flip the frontmatter flag on the SKILL.md itself. Found by walking the same
    # roots the list came from rather than assuming `~/.claude/skills/<id>` — a DeepSeek pane's
    # skills live in the DSH roots, a project skill in the project, and a shared one in the Studio
    # library, which is exactly the case a hardcoded path turned into a switch that did nothing.
    md = _find_skill_md(skill_id, project_id, agent)
    if md is not None:
        _set_dmi_flag(md, not enabled)
    return {"ok": True, "id": skill_id, "enabled": enabled, "state": ov[skill_id],
            "path": str(md) if md else ""}


def _find_skill_md(skill_id: str, project_id: str = "", agent: str = "") -> "Path | None":
    """The SKILL.md behind an id, searched in this engine's own rank order (first match wins,
    which is also the order the engine itself resolves a shadowed name)."""
    safe = "".join(c for c in skill_id if c.isalnum() or c in "-_. ")
    if not safe or safe != skill_id:
        return None
    for scope, d in _roots_for(project_id, agent):
        if scope == "plugin":
            continue
        md = d / safe / "SKILL.md"
        try:
            if md.is_file():
                return md
        except OSError:
            continue
    return None

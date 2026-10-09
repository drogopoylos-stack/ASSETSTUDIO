"""Feature toggles — what the Studio actually loads.

Every optional part of the app is one entry here with a stable id, and Settings →
Plugins flips it. Two kinds:

``tab``
    A top-bar tab. Turning it off hides it AND stops the page from being mounted —
    the pages are ``lazy()``-imported, so a disabled tab's JS chunk is never even
    fetched. That is the real cost saving, not just a hidden button.

``service``
    A background helper or a prompt overlay. Most of these already had a settings
    key of their own (``cc_graphify``, ``cc_autolearn``…); those entries carry
    ``setting`` and keep using it, so a toggle never has two sources of truth.

Adding a feature = one entry. Nothing else in the codebase needs to know.
"""
from __future__ import annotations

from typing import Any

from .config import settings

# Turning these off would lock the user out of the app (no file access, no way back
# into Settings), so they are always on and render without a switch.
CORE = {"workspace", "settings"}

PLUGINS: list[dict[str, Any]] = [
    # ── Core ────────────────────────────────────────────────────────────────
    {"id": "workspace", "kind": "tab", "group": "Core", "label": "Workspace",
     "desc": "The file explorer, editor and the Claude chat that works inside a project."},
    {"id": "settings", "kind": "tab", "group": "Core", "label": "Settings",
     "desc": "This screen."},

    # ── AI agents ───────────────────────────────────────────────────────────
    {"id": "mission", "kind": "tab", "group": "AI agents", "label": "Mission Control",
     "desc": "One live card per project: several agents working at the same time, each with its own feed."},
    {"id": "workflows", "kind": "tab", "group": "AI agents", "label": "Workflows",
     "desc": "Live view of a Workflow run — every phase, every subagent, tokens and stalls."},
    {"id": "chat", "kind": "tab", "group": "AI agents", "label": "Ask AI",
     "desc": "A plain chat with an AI model, outside any project folder."},
    {"id": "plans", "kind": "tab", "group": "AI agents", "label": "Plans",
     "desc": "Every plan an agent wrote in plan mode, with its build phases. Read it, then send it to build."},

    # ── Asset generation ────────────────────────────────────────────────────
    {"id": "dashboard", "kind": "tab", "group": "Asset generation", "label": "Dashboard",
     "desc": "Overview of recent assets, jobs and disk use."},
    {"id": "image", "kind": "tab", "group": "Asset generation", "label": "Image",
     "desc": "Text to image with the local ComfyUI presets or a paid API."},
    {"id": "video", "kind": "tab", "group": "Asset generation", "label": "Video",
     "desc": "Direct videos with MiniMax H3: timeline, CUTs and image, video or audio references in local ComfyUI."},
    {"id": "studio2d", "kind": "tab", "group": "Asset generation", "label": "2D Studio",
     "desc": "Clean up a 2D image: cut out the background, upscale, SAM2 masks."},
    {"id": "studio3d", "kind": "tab", "group": "Asset generation", "label": "3D Studio",
     "desc": "Image to 3D mesh (Hunyuan3D, TRELLIS, Tripo, Meshy)."},
    {"id": "texture", "kind": "tab", "group": "Asset generation", "label": "Texture",
     "desc": "Paint textures onto an untextured mesh."},
    {"id": "rig", "kind": "tab", "group": "Asset generation", "label": "Rig & Animate",
     "desc": "Auto-rig a character mesh and apply animations."},
    {"id": "pipeline", "kind": "tab", "group": "Asset generation", "label": "Pipeline",
     "desc": "Chain the stages together: prompt to a finished, optimised game asset."},
    {"id": "catalog", "kind": "tab", "group": "Asset generation", "label": "Catalog",
     "desc": "Everything the Studio has generated, searchable, with tags and licences."},
    {"id": "compare", "kind": "tab", "group": "Asset generation", "label": "Compare",
     "desc": "Put two results side by side and pick the better one."},

    # ── System ──────────────────────────────────────────────────────────────
    {"id": "jobs", "kind": "tab", "group": "System", "label": "Jobs",
     "desc": "The generation queue: what is running, what failed, what it cost."},
    {"id": "servers", "kind": "tab", "group": "System", "label": "Servers",
     "desc": "Start and stop local model servers (ComfyUI, TRELLIS, Hunyuan3D)."},

    # ── Chat helpers (prompt overlays sent with every message) ───────────────
    {"id": "cc_streaming", "kind": "service", "group": "Chat helpers", "setting": "cc_streaming",
     "label": "Live streaming chat",
     "desc": "Keep one agent process alive per project so answers stream in as they are written. "
             "Off = one process per message, which is slower to start and loses the live feed."},
    {"id": "cc_graphify", "kind": "service", "group": "Chat helpers", "setting": "cc_graphify",
     "label": "Graphify code graph",
     "desc": "Build a queryable map of the project and tell the agent to read it instead of grepping "
             "the whole repo. The single biggest token saving on a large codebase."},
    {"id": "cc_memory", "kind": "service", "group": "Chat helpers", "setting": "cc_memory",
     "label": "Auto-memory",
     "desc": "Give the agent the notes it saved about you and this project in earlier sessions."},
    {"id": "cc_autolearn", "kind": "service", "group": "Chat helpers", "setting": "cc_autolearn",
     "label": "Auto-learn skills",
     "desc": "Watch finished work for reusable build patterns and save them as skills. Runs on a "
             "local model, so it costs no API tokens."},
    {"id": "cc_1m", "kind": "service", "group": "Chat helpers", "setting": "cc_1m",
     "label": "1M context window",
     "desc": "Ask for Claude's 1M-token window instead of 200k. Included on Max plans for Opus."},
    {"id": "cc_fable_efficient", "kind": "service", "group": "Chat helpers", "setting": "cc_fable_efficient",
     "label": "Token-efficiency directive",
     "desc": "On Fable sessions, add a short instruction to act instead of re-surveying the repo. "
             "Changes style only, never reasoning depth."},
    {"id": "cc_prompt_snapshot", "kind": "service", "group": "Chat helpers", "setting": "cc_prompt_snapshot",
     "label": "Reuse the system prompt",
     "desc": "Record the system prompt once per conversation and replay it word for word, so the "
             "prompt cache keeps its prefix across resumes and day boundaries. The Studio still "
             "sends the new text on the one launch where you change a setting above. Needs CLI "
             "2.1.257 or newer."},
    {"id": "studio_tools_prompt", "kind": "service", "group": "Chat helpers", "setting": "studio_tools_prompt",
     "label": "Studio generator API",
     "desc": "Tell the agent it can generate images and 3D models by calling the Studio's own "
             "API. Only applies when at least one generator tab (Image, 2D, 3D, Texture, Rig, "
             "Pipeline) is on — with all of them off the agent is never told the API exists."},

    # ── Background services ─────────────────────────────────────────────────
    {"id": "voice_enabled", "kind": "service", "group": "Background services", "setting": "voice_enabled",
     "label": "Voice input",
     "desc": "The microphone button in the chat box. Runs Whisper locally; the model is downloaded "
             "on first use and holds GPU memory while loaded."},
    {"id": "sweep_browser_windows", "kind": "service", "group": "Background services",
     "setting": "sweep_browser_windows", "label": "Stray-window sweeper",
     "desc": "Move automation browser windows that open themselves off your main screen. Turn it off "
             "if you want test scripts to open in front of you."},
    {"id": "auto_start_services", "kind": "service", "group": "Background services", "setting": "auto_start_services",
     "label": "Auto-start local servers",
     "desc": "Start a local model server by itself when a job needs it."},
    {"id": "telemetry", "kind": "service", "group": "Background services", "setting": "telemetry",
     "label": "Telemetry", "default": False,
     "desc": "Off by default. Nothing leaves this machine while it is off."},
]

_BY_ID = {p["id"]: p for p in PLUGINS}


def _tab_state() -> dict[str, bool]:
    st = settings.get("plugins") or {}
    return st if isinstance(st, dict) else {}


def enabled(pid: str) -> bool:
    """Is this feature on? Unknown ids read as on, so a plugin added by a newer build
    never disappears because an older settings file has not heard of it."""
    p = _BY_ID.get(pid)
    if not p:
        return True
    if pid in CORE:
        return True
    default = bool(p.get("default", True))
    if p["kind"] == "service":
        return bool(settings.get(str(p["setting"]), default))
    return bool(_tab_state().get(pid, default))


# The surfaces that actually GENERATE. Dashboard, Catalog and Compare only look at results, so
# having them on says nothing about wanting an image made.
GENERATORS = {"image", "studio2d", "studio3d", "texture", "rig", "pipeline"}


def generation_available() -> bool:
    """Is any asset generator switched on in Settings?

    The system-prompt note describing the generator API was gated only on its own toggle, which
    defaults to on. So a user who had turned off every generation tab was still told the API
    existed — and the agent used it, in about nine requests out of ten on a game project. A
    capability that is not in the app should not be in the prompt either: if it is switched off,
    the agent should not know it is there at all, rather than know and be asked not to."""
    return any(enabled(p) for p in GENERATORS)


def enabled_tabs() -> list[str]:
    return [p["id"] for p in PLUGINS if p["kind"] == "tab" and enabled(p["id"])]


def list_plugins() -> list[dict]:
    out = []
    for p in PLUGINS:
        out.append({
            "id": p["id"], "kind": p["kind"], "group": p["group"], "label": p["label"],
            "desc": p.get("desc", ""), "core": p["id"] in CORE,
            "enabled": enabled(p["id"]),
            "setting": p.get("setting", ""),
        })
    return out


def set_enabled(patch: dict[str, bool]) -> dict[str, Any]:
    """Apply a {plugin_id: bool} patch. Tabs go into ``settings["plugins"]``; services
    write the settings key they already own. Returns the settings patch that was applied
    so the caller can run the same side effects a normal settings write would."""
    tabs: dict[str, bool] = {}
    applied: dict[str, Any] = {}
    for pid, val in (patch or {}).items():
        p = _BY_ID.get(pid)
        if not p or pid in CORE:
            continue      # unknown, or core — refuse to lock the user out
        if p["kind"] == "service":
            applied[str(p["setting"])] = bool(val)
        else:
            tabs[pid] = bool(val)
    if tabs:
        applied["plugins"] = {**_tab_state(), **tabs}
    if applied:
        settings.update(applied)
    return applied

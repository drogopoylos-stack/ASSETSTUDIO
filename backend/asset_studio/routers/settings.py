"""App settings + API-key management (keys live in the OS keychain, never on disk
in plaintext, and are never returned to the client — only their presence)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .. import keychain
from ..config import settings
from ..providers import registry

router = APIRouter(prefix="/api", tags=["settings"])

# key_name -> label/help shown in the UI
KNOWN_KEYS = {
    "deepseek": "DeepSeek Harness (native dsh; V4.1 Flash)",
    "fal": "fal (MiniMax H3 Max video; paid API)",
    "tripo": "Tripo 3D",
    "meshy": "Meshy",
    "gemini": "Google Gemini / nanobanana",
    "openai": "OpenAI (gpt-image-1, vision judge)",
    "anthropic": "Anthropic (optional judge)",
    "replicate": "Replicate (optional)",
    "huggingface": "HuggingFace (hosted TRELLIS Space token)",
    "moonshot": "Moonshot / Kimi (enables the Kimi K3 chat agent)",
    "dashscope": "Alibaba DashScope / Qwen (enables the Qwen chat agent)",
}


@router.get("/settings/agent-notes")
def agent_notes(cwd: str = ""):
    """Every note a session is told, each with its own switch and the tokens it costs per turn."""
    from .. import cc_session
    return cc_session.agent_notes_catalog(cwd)


@router.get("/settings/engine-notes")
def engine_notes(cwd: str = ""):
    """The full engine notes, for a subagent that needs the engine (plain text, gated like the notes).

    Subagents get one line pointing here instead of ~11k chars of notes they rarely use; see
    cc_session._engine_pointer_note."""
    from fastapi.responses import PlainTextResponse
    from .. import cc_session
    notes = cc_session._engine_subagent_notes(cwd)
    return PlainTextResponse("\n\n".join(notes) if notes else
                             "No engine notes: every engine switch is off, or this folder has no page a browser can open.")


@router.get("/settings")
def get_settings():
    return settings.all()


class SettingsPatch(BaseModel):
    patch: dict[str, Any]


def _side_effects(patch: dict[str, Any]) -> None:
    """Make a toggle that was just turned ON actually work, now — not on the next restart.
    Shared by the settings and the plugins routes so both behave the same."""
    # Kick off the Studio-owned install right away (background; no-op if already present) so a
    # fresh PC doesn't sit there with Graphify on and never working.
    if patch.get("cc_graphify"):
        try:
            from .. import graphify_index
            graphify_index.ensure_installed()
        except Exception:
            pass
    # Same for the web tools: turning it on with nothing installed would be a switch that does
    # nothing, which is indistinguishable from a broken feature.
    if patch.get("cc_web_tools"):
        try:
            from .. import web_tools
            web_tools.ensure_installed()
        except Exception:
            pass
    # The forge, the review and the live link render in a headless browser; a PC with neither
    # Chrome nor Edge gets one fetched the moment one of them is switched on.
    if patch.get("cc_forge") or patch.get("cc_live") or patch.get("cc_review"):
        try:
            from .. import browser_install
            browser_install.ensure_installed()
        except Exception:
            pass
    # Same for auto-learn: flipping it ON immediately kicks a scan (and the local-LLM
    # install if needed) so the first learned skills appear without waiting for the loop.
    if patch.get("cc_autolearn"):
        try:
            from .. import autolearn
            autolearn.run_now()
        except Exception:
            pass
    # Picking an output style must find the file there — write the bundled ones if they're missing.
    if patch.get("cc_output_style"):
        try:
            from .. import output_styles
            output_styles.ensure_installed()
        except Exception:
            pass
    # The sweeper threads start at boot and re-read the setting every tick, so turning it OFF
    # takes effect at once. Turning it ON after a boot where it never started needs this.
    if patch.get("sweep_browser_windows"):
        try:
            from .. import window_sweeper
            window_sweeper.start()
        except Exception:
            pass
    # BOOST applied as a plain settings write (the Settings page, or any other caller) must move
    # the profile with it and save what it replaced — otherwise `boost: true` alone would turn on
    # the button and none of the economy behind it, which reads as a feature that does nothing.
    if "boost" in patch:
        try:
            from .. import boost
            boost.set_enabled(bool(patch["boost"]))
        except Exception:
            pass


@router.get("/boost")
def boost_status():
    """Everything the BOOST button shows: whether it is on, what it has saved, and what it moved."""
    from .. import boost
    return boost.status()


class BoostBody(BaseModel):
    on: bool


@router.put("/boost")
def set_boost(body: BoostBody):
    """The ON/OFF button. Saving and restoring the switches it touches happens in boost.py."""
    from .. import boost
    return boost.set_enabled(body.on)


class BoostPreview(BaseModel):
    text: str


@router.post("/boost/preview")
def boost_preview(body: BoostPreview):
    """What BOOST would do to THIS message, before it is sent — so the saving is visible and the
    rewrite is never something that happened to the user's words without them seeing it."""
    from .. import boost
    return boost.compress(body.text)


@router.post("/boost/forget")
def boost_forget():
    """Reset the running total, for a number that starts counting again."""
    from .. import boost
    return boost.forget()


@router.put("/settings")
def update_settings(body: SettingsPatch):
    result = settings.update(body.patch)
    _side_effects(body.patch)
    return result


@router.get("/plugins")
def list_plugins():
    """Every optional part of the Studio and whether it is on. A disabled tab is not
    mounted at all, so its code is never even downloaded."""
    from .. import plugins
    return {"plugins": plugins.list_plugins()}


class PluginPatch(BaseModel):
    patch: dict[str, bool]


@router.put("/plugins")
def update_plugins(body: PluginPatch):
    from .. import plugins
    applied = plugins.set_enabled(body.patch)
    _side_effects(applied)          # a helper turned on here must start working immediately
    return {"plugins": plugins.list_plugins(), "applied": applied}


@router.get("/output-styles")
def list_output_styles():
    """Installed output styles (~/.claude/output-styles/*.md) + the active one, for the chat
    toggle. Drop a new .md in that folder and it shows up here — no code change needed."""
    from .. import output_styles
    return {"active": (settings.get("cc_output_style") or "").strip(),
            "styles": output_styles.list_styles()}


@router.get("/graphify/query")
def graphify_query(root: str, q: str = "", to: str = "", limit: int = 12, path: str = "",
                   scan: int = 1, via: str = ""):
    """Ask the code graph a question and get an answer small enough to work with.

    This exists because reading graph.json is ~475k tokens on a real project — half a context
    window — so the graph was technically available and practically unusable. A query costs a
    few hundred tokens: where a symbol is, what it uses, what uses it, or a path between two."""
    from .. import graphify_index
    res = graphify_index.query(root, q=q, to=to, limit=limit, path=path, scan=bool(scan))
    # via=hook means the graph hook asked, on the model's behalf, before a search ran. Only then
    # does this touch the live indicator — a query you or the UI made is not the model working,
    # and labelling it as such would make the pulse lie.
    if via == "hook" and q and res.get("matches"):
        try:
            from .. import cc_session
            cc_session.note_graph_read(root, q)
        except Exception:
            pass          # the answer matters; the label is a nicety
    return res


@router.get("/graphify/status")
def graphify_status():
    """Is graphify usable, and is a background install running / did it fail — for the toggle UI."""
    from .. import graphify_index
    return graphify_index.install_status()


class GraphifyPrebuild(BaseModel):
    path: str


@router.post("/graphify/prebuild")
def graphify_prebuild(body: GraphifyPrebuild):
    """Pre-build a workspace's code graph the moment it's OPENED (before the first message), so the
    graph is ready to use. No-op unless the Graphify toggle is on; installs graphify if missing;
    fire-and-forget (returns instantly, builds in the background, rate-limited per workspace)."""
    if not settings.get("cc_graphify"):
        return {"scheduled": False}
    from .. import graphify_index
    return {"scheduled": bool(graphify_index.refresh(body.path))}


@router.get("/keys")
def list_keys():
    # also surface any key_names declared by providers / custom specs
    names = dict(KNOWN_KEYS)
    for p in registry.all_providers():
        if p.key_name:
            names.setdefault(p.key_name, p.name)
    return {
        "backend": keychain.backend_name(),
        "keys": [{"name": n, "label": label, "present": keychain.has_key(n)}
                 for n, label in names.items()],
    }


class KeyBody(BaseModel):
    value: str


@router.put("/keys/{name}")
def set_key(name: str, body: KeyBody):
    keychain.set_key(name, body.value)
    from .. import agents
    agents.invalidate()
    registry.reload()  # availability may change
    return {"ok": True, "present": keychain.has_key(name)}


@router.delete("/keys/{name}")
def delete_key(name: str):
    keychain.delete_key(name)
    registry.reload()
    return {"ok": True, "present": keychain.has_key(name)}

"""Pluggable AI coding-agent backends.

Claude Code is the primary agent — it writes a rich transcript that Mission
Control tails live. Other agents (OpenAI Codex, Gemini CLI, Cursor agent) are
optional: auto-detected on PATH and runnable headlessly inside a project folder.
The Studio doesn't bundle them; it detects what's installed and shows a one-line
install command for what's missing, so adding a new AI to the chat is trivial.

Adding a new agent = one entry in :data:`AGENTS` (id, name, detect, arg builder).
That's the whole "future-proof / install other AIs" surface.
"""
from __future__ import annotations

import shutil
from typing import Callable, Optional


def _claude_path() -> Optional[str]:
    # reuse the VSCode-extension-aware locator
    from . import cc_session
    return cc_session.find_claude()


def _which(*names: str) -> Callable[[], Optional[str]]:
    def f() -> Optional[str]:
        for n in names:
            p = shutil.which(n)
            if p:
                return p
        return None
    return f


# Each agent: how to detect it and how to build a one-shot headless command.
# ``streams`` = writes into a Claude-Code-style transcript the feed already tails.
AGENTS: list[dict] = [
    {
        "id": "deepseek-harness", "name": "DeepSeek Harness", "color": "#4d6bfe",
        "streams": True, "detect": lambda: __import__(__package__ + ".deepseek_session", fromlist=["installed"]).installed(),
        "install_cmd": "Add the DeepSeek API key in Settings > API Keys > DeepSeek Harness",
        "install_url": "https://platform.deepseek.com/api_keys",
        "note": "Native official dsh harness. DeepSeek V4.1 Flash (the V4 ids still work as aliases); its own persistent conversations. Requires a DeepSeek API key. SDK mode uses Full access.",
    },
    {
        "id": "claude",
        "name": "Claude Code",
        "color": "#d97757",
        "streams": True,
        "detect": _claude_path,
        "install_cmd": "npm install -g @anthropic-ai/claude-code",
        "install_url": "https://docs.claude.com/en/docs/claude-code",
        "note": "Primary agent — full live transcript, model/effort/permission controls.",
    },
    {
        "id": "kimi",
        "name": "Kimi K3",
        "color": "#6a5cff",
        "streams": True,   # full Claude-Code-style streaming: own sessions, context meter, effort low→max
        "detect": None,    # replaced below — needs the claude CLI + a Moonshot API key
        "install_cmd": "Get an API key at platform.moonshot.ai, then paste it in Settings → API Keys → 'Moonshot / Kimi'",
        "install_url": "https://platform.moonshot.ai",
        "note": "Moonshot Kimi K3 (1M context) through the Claude Code engine — separate sessions & context "
                "from Claude, same live chat/effort/permission controls. Needs the Moonshot API key.",
    },
    {
        "id": "qwen",
        "name": "Qwen",
        "color": "#615ced",
        "streams": True,   # full Claude-Code-style streaming: own sessions, context meter, effort low→max
        "detect": None,    # replaced below — needs the claude CLI + a DashScope API key
        "install_cmd": "Get an API key at dashscope-intl.console.aliyun.com, then paste it in Settings → API Keys → 'Alibaba DashScope / Qwen'",
        "install_url": "https://dashscope-intl.console.aliyun.com",
        "note": "Alibaba Qwen (qwen3.8-max) through the Claude Code engine — separate sessions & context "
                "from Claude, same live chat/effort/permission controls. Needs the DashScope API key.",
    },
    {
        "id": "codex",
        "name": "OpenAI Codex",
        "color": "#10a37f",
        "streams": True,    # through the Codex app-server: its own conversation, streamed into the feed
        "detect": None,     # replaced below - codex_app finds the .cmd shim first, and npm's folder
        "install_cmd": "npm install -g @openai/codex",
        "install_url": "https://developers.openai.com/codex/cli",
        "note": "OpenAI's coding agent: sign in with ChatGPT or an API key, pick any model it offers, "
                "and watch its commands, edits and subagents in the feed.",
    },
    {
        "id": "gemini",
        "name": "Gemini CLI",
        "color": "#4285f4",
        "streams": False,
        "detect": _which("gemini", "gemini.cmd"),
        "install_cmd": "npm install -g @google/gemini-cli",
        "install_url": "https://github.com/google-gemini/gemini-cli",
        "note": "Runs `gemini -p` headless in the project folder.",
    },
    {
        "id": "cursor",
        "name": "Cursor Agent",
        "color": "#9ca3af",
        "streams": False,
        "detect": _which("cursor-agent", "cursor-agent.cmd"),
        "install_cmd": "curl https://cursor.com/install -fsS | bash",
        "install_url": "https://docs.cursor.com/en/cli/overview",
        "note": "Runs `cursor-agent -p --force` headless in the project folder. Flags are best-effort (verify per CLI version).",
    },
]

def _kimi_detect() -> Optional[str]:
    """Kimi runs through the claude CLI against Moonshot's endpoint — 'installed' means
    the claude CLI exists AND the Moonshot API key is set."""
    exe = _claude_path()
    if not exe:
        return None
    try:
        from . import keychain
        return exe if keychain.has_key("moonshot") else None
    except Exception:
        return None


def _qwen_detect() -> Optional[str]:
    """Qwen runs through the claude CLI against DashScope's Anthropic-compatible endpoint —
    'installed' means the claude CLI exists AND the DashScope API key is set."""
    exe = _claude_path()
    if not exe:
        return None
    try:
        from . import keychain
        return exe if keychain.has_key("dashscope") else None
    except Exception:
        return None


def _codex_detect() -> Optional[str]:
    try:
        from . import codex_app
        return codex_app.find_codex().get("shim") or None
    except Exception:
        return None


_BY_ID = {a["id"]: a for a in AGENTS}
_BY_ID["kimi"]["detect"] = _kimi_detect
_BY_ID["qwen"]["detect"] = _qwen_detect
_BY_ID["codex"]["detect"] = _codex_detect


def spec(agent_id: str) -> Optional[dict]:
    return _BY_ID.get(agent_id)


def detect(agent_id: str) -> Optional[str]:
    a = _BY_ID.get(agent_id)
    if not a:
        return None
    try:
        return a["detect"]()
    except Exception:
        return None


def _custom_agents() -> list[dict]:
    """Providers the user added in Settings → Models. Each runs through the claude CLI
    against its own endpoint, so 'installed' means the CLI exists and — unless the
    endpoint is on this machine — a key is stored."""
    try:
        from . import chat_providers
        specs = chat_providers.all_providers()
    except Exception:
        return []
    exe = _claude_path()
    out = []
    for p in specs:
        pid = str(p.get("id", ""))
        if not pid:
            continue
        local = str(p.get("base_url", "")).startswith(
            ("http://127.0.0.1", "http://localhost", "http://0.0.0.0"))
        try:
            keyed = local or chat_providers.has_key(pid)
        except Exception:
            keyed = False
        model = p.get("default_model") or "a model of your choice"
        where = "on this PC" if local else str(p.get("vendor") or p.get("name") or pid)
        out.append({
            "id": pid, "name": str(p.get("name") or pid), "color": str(p.get("color") or "#8b8b8b"),
            "available": bool(exe and keyed), "path": (exe or "") if keyed else "",
            "streams": True, "custom": True, "protocol": str(p.get("protocol", "anthropic")),
            "note": f"{model} ({where}) through the Claude Code engine — separate sessions & context, "
                    f"same live chat/effort/permission controls.",
            "install_cmd": (f"Add an API key in Settings → Models → {p.get('name') or pid}"
                            if not local else "Start the local server, then pick a model."),
            "install_url": str(p.get("key_url") or ""),
        })
    return out


_LIST_TTL = None       # built on first use, so importing this module stays free


def invalidate() -> None:
    """Forget which CLIs are installed — called right after an install or an update."""
    if _LIST_TTL is not None:
        _LIST_TTL.drop()


def list_agents() -> list[dict]:
    """Which AI CLIs are installed, remembered for 30 seconds.

    Every entry runs a PATH search (`shutil.which`, which tries each folder of PATH against each
    extension) and Claude's own locator on top. Four parts of the window ask for this list, the
    workspace switch asks again, and it was measured at 796 ms during a switch and 355 ms while
    idle — for an answer that changes when you install something. An install calls `invalidate()`.
    """
    global _LIST_TTL
    if _LIST_TTL is None:
        from . import perf
        _LIST_TTL = perf.Ttl(30.0, limit=4)
    return _LIST_TTL.get("all", _list_agents_build)


def _list_agents_build() -> list[dict]:
    out = []
    for a in AGENTS:
        try:
            path = a["detect"]()
        except Exception:
            path = None
        out.append({
            "id": a["id"], "name": a["name"], "color": a["color"],
            "available": bool(path), "path": path or "",
            "streams": a["streams"], "note": a.get("note", ""), "custom": False,
            "install_cmd": a["install_cmd"], "install_url": a["install_url"],
            **({"needs_key": not bool(__import__(__package__ + ".deepseek_session", fromlist=["api_key"]).api_key())}
               if a["id"] == "deepseek-harness" else {}),
        })
    out.extend(_custom_agents())
    return out


# Codex reasoning-effort values its CLI accepts (-c model_reasoning_effort=...).
# The levels current Codex models list (see codex_app.fit_effort). xhigh and max used to be
# folded down to high here, so the terminal and the one-shot runs never got the effort the chat
# box showed.
_CODEX_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def _codex_effort(effort: str) -> str:
    """Map the shared effort vocabulary onto Codex's reasoning levels ('' = leave default)."""
    e = (effort or "default").lower()
    if e in _CODEX_EFFORTS:
        return e
    if e == "ultracode":
        return "max"
    return ""


def _codex_approval(mode: str) -> list[str]:
    """Map an approval/permission choice onto Codex sandbox + approval flags."""
    m = (mode or "").lower()
    if m in ("read-only", "readonly", "plan"):
        return ["-s", "read-only"]
    if m in ("yolo", "bypasspermissions", "danger-full-access"):
        return ["--dangerously-bypass-approvals-and-sandbox"]
    # Default: edit files in the workspace, low friction. This is `--full-auto`'s replacement —
    # the codex CLI now warns "`--full-auto` is deprecated; use `--sandbox workspace-write`".
    return ["-s", "workspace-write"]


def build_args(agent_id: str, exe: str, message: str, cwd: str,
               model: str = "default", effort: str = "default",
               permission_mode: str = "default") -> list[str]:
    """Build a headless one-shot command for a *non-claude* agent.

    Claude's command is built in :mod:`cc_session` (it has resume/effort/etc).
    Codex gets the same controls the chat exposes: model, reasoning effort, and an
    approval/sandbox mode.
    """
    if agent_id == "codex":
        args = [exe, "exec"]
        if model and model != "default":
            args += ["-m", model]
        eff = _codex_effort(effort)
        if eff:
            args += ["-c", f'model_reasoning_effort="{eff}"']
        args += _codex_approval(permission_mode)  # sandbox/approval (default: workspace-write)
        # Codex refuses to start outside a Git repo ("Not inside a trusted directory and
        # --skip-git-repo-check was not specified"), which would break every Studio project
        # folder that isn't a repo. Harmless inside one — it still uses git when present.
        args += ["--skip-git-repo-check", "-C", cwd, message]
        return args
    if agent_id == "gemini":
        args = [exe]
        if model and model != "default":
            args += ["-m", model]
        args += ["-y", "-p", message]  # -y = auto-approve tool calls
        return args
    if agent_id == "cursor":
        args = [exe, "-p", message, "--force"]
        if model and model != "default":
            args += ["-m", model]
        return args
    # unknown → best effort: pass the prompt as the sole arg
    return [exe, message]

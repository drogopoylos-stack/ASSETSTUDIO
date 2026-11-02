"""Optional local MCP servers shared by Studio's Claude and Codex sessions.

data/mcp/extra-servers.json uses the usual mcpServers object. ${workspace} is
expanded per session so installed tools follow the project being worked on.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import DATA_DIR


def servers(cwd: str = "", *, config_path: Path | None = None) -> dict:
    path = config_path or DATA_DIR / "mcp" / "extra-servers.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        workspace = str(Path(cwd).resolve()) if cwd else str(Path.cwd().resolve())

        def expand(value):
            if isinstance(value, str):
                return value.replace("${workspace}", workspace)
            if isinstance(value, list):
                return [expand(item) for item in value]
            if isinstance(value, dict):
                return {key: expand(item) for key, item in value.items()}
            return value

        result = {}
        for name, server in data.get("mcpServers", {}).items():
            if name == "studio" or not isinstance(server, dict) or server.get("enabled") is False:
                continue
            value = expand(server)
            value.pop("enabled", None)
            if value.get("command"):
                result[name] = value
        return result
    except (OSError, ValueError, AttributeError, TypeError):
        logging.getLogger(__name__).exception("Could not read extra MCP servers: %s", path)
        return {}

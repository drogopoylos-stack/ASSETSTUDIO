# MCP server

Asset Studio ships an MCP server so MCP-aware agents (Claude Desktop, etc.) can drive it.

```powershell
cd backend
./.venv/Scripts/pip.exe install mcp        # optional dependency
# start the backend first (the MCP server is a thin client over its HTTP API):
./.venv/Scripts/python.exe -m asset_studio.main
# then, configured by your MCP client, launch:
./.venv/Scripts/python.exe -m asset_studio.mcp_server
```

### Claude Desktop config example
```json
{
  "mcpServers": {
    "asset-studio": {
      "command": "c:/Users/you/Downloads/STUDIO/backend/.venv/Scripts/python.exe",
      "args": ["-m", "asset_studio.mcp_server"],
      "env": { "ASSET_STUDIO_BASE": "http://127.0.0.1:8777" }
    }
  }
}
```

### Tools exposed
| Tool | Purpose |
|---|---|
| `list_providers(stage?)` | discover backends + availability |
| `system_stats()` | CPU/RAM/disk/GPU/VRAM |
| `generate(stage, provider_id, params, inputs)` | submit a job and wait; returns outputs |
| `get_job(job_id)` | poll a job |
| `list_assets(stage?, limit)` / `get_asset(id)` | browse the catalog |
| `qa_turntable(model, frames)` | render a turntable so the agent can *see* a 3D asset |
| `run_pipeline(steps, inputs)` | chain stages |
| `run_agent(stage, prompt, …)` | autonomous generate→judge→iterate |
| `compare(stage, prompt, a, b)` | A/B two providers, judged |

The agent loop pattern: `generate` → `qa_turntable`/read the image → judge → re-`generate` with a new
prompt/provider, or call `compare` to pick the better of two backends.

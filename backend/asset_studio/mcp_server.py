"""MCP server — exposes Asset Studio to MCP-aware agents (Claude Desktop, etc.).

It is a thin client over the running backend's HTTP API, so the heavy lifting
(queue, GPU, providers) stays in one place. Start the backend first, then run:

    python -m asset_studio.mcp_server          # stdio transport

Configure your MCP client to launch that command. Requires the optional ``mcp``
package: ``pip install mcp``.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

BASE = os.environ.get("ASSET_STUDIO_BASE", "http://127.0.0.1:8777").rstrip("/")

try:
    from mcp.server.fastmcp import FastMCP
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "The 'mcp' package is required for the MCP server. "
        "Install it with: pip install mcp\n"
        f"(import error: {e})"
    )

mcp = FastMCP("Asset Studio")


def _c() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=120)


def _wait(c: httpx.Client, job_id: str, timeout: float = 1800) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "canceled"):
            return job
        time.sleep(0.4)
    return c.get(f"/api/jobs/{job_id}").json()


@mcp.tool()
def list_providers(stage: Optional[str] = None) -> list[dict]:
    """List available generation/processing providers, optionally filtered by
    stage (image2d, process2d, gen3d, texture, rig, optimize, qa)."""
    with _c() as c:
        return c.get("/api/providers", params={"stage": stage} if stage else None).json()


@mcp.tool()
def system_stats() -> dict:
    """Return live CPU/RAM/disk/GPU/VRAM stats."""
    with _c() as c:
        return c.get("/api/system/stats").json()


@mcp.tool()
def generate(stage: str, provider_id: str, params: dict[str, Any] | None = None,
             inputs: list[str] | None = None) -> dict:
    """Submit a job and wait for completion. Returns the finished job including
    its output assets (ids, paths, sizes, poly counts, cost)."""
    with _c() as c:
        job = c.post("/api/jobs", json={
            "stage": stage, "provider_id": provider_id,
            "params": params or {}, "inputs": inputs or [],
        }).json()
        return _wait(c, job["id"])


@mcp.tool()
def get_job(job_id: str) -> dict:
    """Fetch a job's current status/progress/outputs."""
    with _c() as c:
        return c.get(f"/api/jobs/{job_id}").json()


@mcp.tool()
def list_assets(stage: Optional[str] = None, limit: int = 50) -> list[dict]:
    """List catalog assets (newest first)."""
    with _c() as c:
        return c.get("/api/assets", params={"stage": stage, "limit": limit}).json()


@mcp.tool()
def get_asset(asset_id: str) -> dict:
    """Get one asset's full metadata (path, preview, provider, prompt, seed, license, cost)."""
    with _c() as c:
        return c.get(f"/api/assets/{asset_id}").json()


@mcp.tool()
def qa_turntable(model: str, frames: int = 8) -> dict:
    """Render a QA turntable (montage PNG + GIF) of a 3D asset so you can see it.
    `model` is an asset id or file path. Returns the job with render outputs."""
    return generate("qa", "turntable", {"frames": frames}, [model])


@mcp.tool()
def run_pipeline(steps: list[dict], inputs: list[str] | None = None) -> dict:
    """Run a multi-stage pipeline. `steps` = [{stage, provider_id, params}], each
    step's outputs feed the next. Returns job ids + final asset ids."""
    with _c() as c:
        return c.post("/api/pipeline", json={"steps": steps, "inputs": inputs or []},
                      timeout=3600).json()


@mcp.tool()
def run_agent(stage: str, prompt: str = "", provider_id: Optional[str] = None,
              max_iterations: int = 3, accept_threshold: float = 0.7,
              inputs: list[str] | None = None) -> dict:
    """Run the autonomous generate→judge→iterate loop and return the run with the
    best asset and per-iteration judge scores."""
    with _c() as c:
        return c.post("/api/agent/run", json={
            "stage": stage, "prompt": prompt, "provider_id": provider_id,
            "max_iterations": max_iterations, "accept_threshold": accept_threshold,
            "inputs": inputs or [],
        }, timeout=3600).json()


@mcp.tool()
def compare(stage: str, prompt: str, provider_a: str, provider_b: str) -> dict:
    """A/B two providers on one prompt; the judge picks a winner. Returns the run."""
    with _c() as c:
        return c.post("/api/agent/compare", json={
            "stage": stage, "prompt": prompt,
            "provider_a": provider_a, "provider_b": provider_b,
        }, timeout=3600).json()


def main():
    mcp.run()


if __name__ == "__main__":
    main()

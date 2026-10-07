"""Asset Studio CLI — headless control for humans and AI agents.

Talks to the running backend over HTTP (start it with ``asset-studio serve``).
Everything the GUI can do is scriptable here, so an agent can generate, read
outputs, run QA turntables, judge, and iterate entirely from the shell.

Examples:
    asset-studio serve
    asset-studio providers --stage gen3d
    asset-studio gen image2d placeholder-image -p prompt="a brass key" -p seed=7
    asset-studio pipeline pipeline.json
    asset-studio agent gen3d --prompt "low-poly barrel" --max-iter 3
    asset-studio compare image2d --prompt "coin" --a placeholder-image --b nanobanana
    asset-studio qa <asset_id>
    asset-studio key set tripo <API_KEY>
"""
from __future__ import annotations

import json
import time
from typing import Optional

import httpx
import typer
from rich import print_json
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, help="Asset Studio headless control")
key_app = typer.Typer(help="API key management")
app.add_typer(key_app, name="key")
console = Console()

DEFAULT_BASE = "http://127.0.0.1:8777"


def _base(base: Optional[str]) -> str:
    return (base or DEFAULT_BASE).rstrip("/")


def _parse_params(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _client(base: str) -> httpx.Client:
    return httpx.Client(base_url=base, timeout=120)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8777):
    """Start the backend server (REST + WebSocket)."""
    import uvicorn

    from .config import settings

    settings.update({"host": host, "port": port})
    uvicorn.run("asset_studio.main:app", host=host, port=port, reload=False)


@app.command()
def stats(base: Optional[str] = None):
    """Show live CPU/RAM/disk/GPU stats."""
    with _client(_base(base)) as c:
        print_json(data=c.get("/api/system/stats").json())


@app.command()
def providers(stage: Optional[str] = None, base: Optional[str] = None):
    """List providers (optionally filtered by stage)."""
    with _client(_base(base)) as c:
        data = c.get("/api/providers", params={"stage": stage} if stage else None).json()
    table = Table("id", "stage", "kind", "available", "cost", "name")
    for p in data:
        table.add_row(p["id"], p["stage"], p["kind"],
                      "yes" if p["available"] else "NO", p["cost_hint"], p["name"])
    console.print(table)


def _wait_job(c: httpx.Client, job_id: str, quiet: bool = False):
    last = -1.0
    while True:
        job = c.get(f"/api/jobs/{job_id}").json()
        if not quiet and job["progress"] != last:
            console.print(f"[{job['status']}] {job['progress']*100:5.1f}%  {job['step']}")
            last = job["progress"]
        if job["status"] in ("succeeded", "failed", "canceled"):
            return job
        time.sleep(0.4)


@app.command()
def gen(
    stage: str,
    provider: str,
    param: list[str] = typer.Option(None, "-p", "--param", help="key=value (JSON or string)"),
    input: list[str] = typer.Option(None, "-i", "--input", help="asset id or file path"),
    base: Optional[str] = None,
    wait: bool = True,
):
    """Submit a single job and (by default) wait for the result."""
    body = {"stage": stage, "provider_id": provider,
            "params": _parse_params(param), "inputs": input or []}
    with _client(_base(base)) as c:
        job = c.post("/api/jobs", json=body).json()
        console.print(f"submitted job [bold]{job['id']}[/bold]")
        if wait:
            job = _wait_job(c, job["id"])
    print_json(data=job)


@app.command()
def job(job_id: str, base: Optional[str] = None):
    """Fetch a job by id."""
    with _client(_base(base)) as c:
        print_json(data=c.get(f"/api/jobs/{job_id}").json())


@app.command()
def assets(stage: Optional[str] = None, limit: int = 25, base: Optional[str] = None):
    """List catalog assets."""
    with _client(_base(base)) as c:
        data = c.get("/api/assets", params={"stage": stage, "limit": limit}).json()
    table = Table("id", "stage", "type", "name", "provider", "cost", "commercial")
    for a in data:
        table.add_row(a["id"], a["stage"], a["type"], a["name"][:30],
                      a["provider_id"], str(a["cost"]),
                      {True: "ok", False: "NO", None: "?"}[a["commercial_ok"]])
    console.print(table)


@app.command()
def pipeline(spec: str, base: Optional[str] = None):
    """Run a multi-stage pipeline from a JSON file: {steps:[...], inputs:[...]}."""
    body = json.loads(open(spec, encoding="utf-8").read())
    with _client(_base(base)) as c:
        r = c.post("/api/pipeline", json=body, timeout=3600).json()
    print_json(data=r)


@app.command()
def agent(
    stage: str,
    prompt: str = "",
    provider: Optional[str] = None,
    max_iter: int = 3,
    threshold: float = 0.7,
    input: list[str] = typer.Option(None, "-i", "--input"),
    base: Optional[str] = None,
):
    """Run the autonomous generate→judge→iterate loop."""
    goal = {"stage": stage, "prompt": prompt, "provider_id": provider,
            "max_iterations": max_iter, "accept_threshold": threshold, "inputs": input or []}
    with _client(_base(base)) as c:
        r = c.post("/api/agent/run", json=goal, timeout=3600).json()
    print_json(data=r)


@app.command()
def compare(stage: str, prompt: str, a: str, b: str, base: Optional[str] = None):
    """A/B two providers on one prompt; judge picks the winner."""
    body = {"stage": stage, "prompt": prompt, "provider_a": a, "provider_b": b}
    with _client(_base(base)) as c:
        r = c.post("/api/agent/compare", json=body, timeout=3600).json()
    print_json(data=r)


@app.command()
def qa(model: str, frames: int = 8, base: Optional[str] = None):
    """Render a QA turntable of a 3D asset (asset id or path)."""
    body = {"stage": "qa", "provider_id": "turntable",
            "params": {"frames": frames}, "inputs": [model]}
    with _client(_base(base)) as c:
        job = c.post("/api/jobs", json=body).json()
        job = _wait_job(c, job["id"])
    print_json(data=job)


@key_app.command("set")
def key_set(name: str, value: str, base: Optional[str] = None):
    """Store an API key in the OS keychain."""
    with _client(_base(base)) as c:
        print_json(data=c.put(f"/api/keys/{name}", json={"value": value}).json())


@key_app.command("list")
def key_list(base: Optional[str] = None):
    """List which keys are present (values never shown)."""
    with _client(_base(base)) as c:
        print_json(data=c.get("/api/keys").json())


if __name__ == "__main__":
    app()

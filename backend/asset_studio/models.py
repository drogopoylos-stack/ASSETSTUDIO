"""Pydantic contracts shared across the whole backend, the CLI, the MCP server,
and (mirrored in TypeScript) the frontend. THIS IS THE CANONICAL SCHEMA.

If you change anything here, update ``frontend/src/types.ts`` to match.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class StageType(str, Enum):
    image2d = "image2d"      # text/image -> 2D image
    video = "video"          # text/image -> video with optional audio
    process2d = "process2d"  # bg removal, upscale, slice, atlas, webp
    gen3d = "gen3d"          # image/text -> 3D mesh
    texture = "texture"      # mesh -> textured mesh
    rig = "rig"              # mesh -> rigged/animated mesh
    optimize = "optimize"    # mesh -> web-optimized GLB/FBX
    qa = "qa"                # render turntable / inspect


class ProviderKind(str, Enum):
    local = "local"  # runs on this machine (GPU/CPU); no key required
    api = "api"      # remote paid/free API; may need a key


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class AssetType(str, Enum):
    image = "image"
    video = "video"
    model = "model"
    texture = "texture"
    atlas = "atlas"
    animation = "animation"
    render = "render"
    other = "other"


# ---------------------------------------------------------------------------
# Provider description (what the UI renders as dropdowns + param forms)
# ---------------------------------------------------------------------------
class ProviderParam(BaseModel):
    name: str
    label: str
    type: Literal["string", "text", "int", "float", "bool", "select", "seed", "file"] = "string"
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: Optional[list[str]] = None
    description: str = ""
    group: str = "General"


class ProviderInfo(BaseModel):
    id: str
    name: str
    stage: StageType
    kind: ProviderKind
    requires_key: bool = False
    key_name: Optional[str] = None       # keychain credential id
    available: bool = True               # is it runnable right now?
    available_reason: str = ""           # why not, if unavailable
    description: str = ""
    license_note: str = ""               # commercial-use guidance
    commercial_ok: Optional[bool] = None
    cost_hint: str = "free"              # e.g. "free", "~$0.04/img"
    homepage: str = ""
    params: list[ProviderParam] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Assets (catalog rows)
# ---------------------------------------------------------------------------
class Asset(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    stage: StageType
    type: AssetType
    path: str                              # absolute path on disk
    preview_path: Optional[str] = None     # png/webp thumbnail
    size_bytes: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)  # width,height,poly,format,...
    tags: list[str] = Field(default_factory=list)
    target_game: str = ""
    provider_id: str = ""
    prompt: str = ""
    seed: Optional[int] = None
    license: str = ""
    commercial_ok: Optional[bool] = None
    cost: float = 0.0
    job_id: Optional[str] = None
    parent_id: Optional[str] = None        # lineage (e.g. mesh from this image)
    created_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class JobRequest(BaseModel):
    stage: StageType
    provider_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)  # asset ids or absolute file paths
    label: str = ""
    target_game: str = ""
    tags: list[str] = Field(default_factory=list)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    stage: StageType
    provider_id: str
    status: JobStatus = JobStatus.queued
    progress: float = 0.0           # 0..1
    step: str = ""                  # human-readable current step
    eta_seconds: Optional[float] = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    label: str = ""
    target_game: str = ""
    tags: list[str] = Field(default_factory=list)
    outputs: list[Asset] = Field(default_factory=list)
    cost: float = 0.0
    error: str = ""
    error_hint: str = ""        # friendly, actionable one-liner (raw error kept in `error`)
    logs: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Live progress events (WebSocket payloads)
# ---------------------------------------------------------------------------
class WSEventType(str, Enum):
    hello = "hello"
    job_update = "job_update"
    job_log = "job_log"
    asset_created = "asset_created"
    stats = "stats"
    agent_update = "agent_update"
    cc_live = "cc_live"          # live Claude-session progress, pushed as it streams


class ProgressEvent(BaseModel):
    type: WSEventType
    job_id: Optional[str] = None
    status: Optional[JobStatus] = None
    progress: Optional[float] = None
    step: Optional[str] = None
    eta_seconds: Optional[float] = None
    message: Optional[str] = None
    asset: Optional[Asset] = None
    job: Optional[Job] = None
    data: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# System stats (status bar)
# ---------------------------------------------------------------------------
class GPUStat(BaseModel):
    index: int
    name: str
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    util_percent: float = 0.0
    temperature_c: Optional[float] = None
    power_w: Optional[float] = None


class DiskStat(BaseModel):
    path: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class SystemStats(BaseModel):
    cpu_percent: float = 0.0
    cpu_cores: int = 0
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_percent: float = 0.0
    disk: Optional[DiskStat] = None
    gpus: list[GPUStat] = Field(default_factory=list)
    headless_browsers: int = 0   # automation browsers running right now (status pill, not a mystery window)
    timestamp: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
class AgentGoal(BaseModel):
    stage: StageType
    prompt: str = ""
    provider_id: Optional[str] = None       # None => use default / let agent pick
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    max_iterations: int = 3
    accept_threshold: float = 0.7           # judge score 0..1 to accept
    judge: str = "auto"                     # "auto" | "heuristic" | provider id
    target_game: str = ""
    compare_providers: list[str] = Field(default_factory=list)  # optional A/B set


class JudgeResult(BaseModel):
    score: float = 0.0          # 0..1
    accept: bool = False
    reasoning: str = ""
    suggestions: str = ""


class AgentIteration(BaseModel):
    index: int
    provider_id: str
    job_id: Optional[str] = None
    asset_id: Optional[str] = None
    judge: Optional[JudgeResult] = None


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    goal: AgentGoal
    status: JobStatus = JobStatus.queued
    iterations: list[AgentIteration] = Field(default_factory=list)
    best_asset_id: Optional[str] = None
    best_score: float = 0.0
    error: str = ""
    created_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None

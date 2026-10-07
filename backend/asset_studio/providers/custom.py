"""User-defined providers — "add a new AI" without writing code.

A custom provider is a JSON spec stored in ``settings['custom_providers']`` and
edited from Settings → Providers. It describes an HTTP endpoint, how to template
the request from job params, and how to extract the resulting file from the
response. This makes the studio open-ended: any image/3D REST API can be wired in
from the UI.

Spec shape (all fields except id/name/stage/endpoint are optional)::

    {
      "id": "my-api", "name": "My API", "stage": "image2d", "kind": "api",
      "requires_key": true, "key_name": "my-api",
      "endpoint": "https://api.example.com/v1/images",
      "method": "POST",
      "headers": {"Authorization": "Bearer {key}", "Content-Type": "application/json"},
      "body": {"prompt": "{prompt}", "size": "{size}"},
      "query": {},
      "timeout": 120,
      "output": {"mode": "url_in_json", "json_path": "data.0.url", "ext": ".png"},
      "result_type": "image",
      "cost_per_call": 0.04,
      "license_note": "Check vendor terms", "commercial_ok": true,
      "params": [{"name": "prompt", "label": "Prompt", "type": "text"},
                 {"name": "size", "label": "Size", "type": "select",
                  "options": ["1024x1024"], "default": "1024x1024"}]
    }
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from ..models import AssetType, ProviderKind, ProviderParam, StageType
from .base import JobContext, Provider

_TYPE_EXT = {"image": ".png", "model": ".glb", "texture": ".png", "other": ".bin"}


def _subst(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        out = value
        for k, v in ctx.items():
            out = out.replace("{" + k + "}", str(v))
        return out
    if isinstance(value, dict):
        return {k: _subst(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, ctx) for v in value]
    return value


def _dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class GenericHTTPProvider(Provider):
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.id = spec["id"]
        self.name = spec.get("name", spec["id"])
        self.stage = StageType(spec.get("stage", "image2d"))
        self.kind = ProviderKind(spec.get("kind", "api"))
        self.requires_key = bool(spec.get("requires_key", False))
        self.key_name = spec.get("key_name") or spec["id"]
        self.description = spec.get("description", "User-defined provider")
        self.license_note = spec.get("license_note", "")
        self.commercial_ok = spec.get("commercial_ok")
        self.cost_hint = spec.get("cost_hint") or (
            f"~${spec.get('cost_per_call', 0):.3f}/call" if spec.get("cost_per_call") else "custom"
        )
        self.homepage = spec.get("homepage", "")
        self.params = [ProviderParam(**p) for p in spec.get("params", [])]

    def run(self, ctx: JobContext) -> list:
        spec = self.spec
        tmpl_ctx: dict[str, Any] = dict(ctx.params)
        if self.requires_key:
            tmpl_ctx["key"] = ctx_get_key(self.key_name)
        # attach a base64 of the first input image/mesh if referenced
        if ctx.first_input():
            tmpl_ctx["input_b64"] = base64.b64encode(Path(ctx.first_input()).read_bytes()).decode()
            tmpl_ctx["input_path"] = ctx.first_input()

        method = spec.get("method", "POST").upper()
        url = _subst(spec["endpoint"], tmpl_ctx)
        headers = _subst(spec.get("headers", {}), tmpl_ctx)
        query = _subst(spec.get("query", {}), tmpl_ctx)
        body = _subst(spec.get("body", {}), tmpl_ctx)
        timeout = float(spec.get("timeout", 120))

        ctx.progress(0.15, f"calling {self.name}")
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                resp = client.get(url, headers=headers, params=query)
            else:
                resp = client.request(method, url, headers=headers, params=query, json=body)
            resp.raise_for_status()
            ctx.progress(0.6, "downloading result")
            out = spec.get("output", {"mode": "binary"})
            ext = out.get("ext") or _TYPE_EXT.get(spec.get("result_type", "image"), ".bin")
            dest = ctx.out_path(f"{self.id}-{ctx.job.id}{ext}")
            mode = out.get("mode", "binary")
            if mode == "binary":
                dest.write_bytes(resp.content)
            else:
                payload = resp.json()
                val = _dig(payload, out.get("json_path", ""))
                if val is None:
                    raise RuntimeError(f"json_path '{out.get('json_path')}' not found in response")
                if mode == "url_in_json":
                    dest.write_bytes(client.get(val, timeout=timeout).content)
                elif mode == "b64_in_json":
                    if "," in val:
                        val = val.split(",", 1)[1]
                    dest.write_bytes(base64.b64decode(val))
                else:
                    raise RuntimeError(f"unknown output mode '{mode}'")

        ctx.add_cost(float(spec.get("cost_per_call", 0)))
        ctx.progress(0.95, "saving asset")
        atype = {
            "image": AssetType.image,
            "model": AssetType.model,
            "texture": AssetType.texture,
        }.get(spec.get("result_type", "image"), AssetType.other)
        return [
            ctx.make_asset(
                path=dest,
                type=atype,
                prompt=ctx.param("prompt", ""),
                license=self.license_note,
                commercial_ok=self.commercial_ok,
            )
        ]


def ctx_get_key(key_name: str) -> str:
    from .. import keychain

    return keychain.get_key(key_name) or ""


def build_custom_providers() -> list[Provider]:
    specs = settings.get("custom_providers", []) or []
    out: list[Provider] = []
    for spec in specs:
        try:
            out.append(GenericHTTPProvider(spec))
        except Exception:
            continue
    return out

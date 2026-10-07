# Adding a new AI / provider

There are **two** ways — no code, and code.

## 1. No code — from the Settings tab ("Add a new AI")

Settings → **Add a new AI** registers a generic HTTP provider described by JSON. Example for an
OpenAI-style images endpoint:

```jsonc
{
  "id": "my-image-api",
  "name": "My Image API",
  "stage": "image2d",
  "kind": "api",
  "requires_key": true,
  "key_name": "my-image-api",
  "endpoint": "https://api.example.com/v1/images",
  "method": "POST",
  "headers": { "Authorization": "Bearer {key}", "Content-Type": "application/json" },
  "body": { "prompt": "{prompt}", "size": "{size}" },
  "output": { "mode": "b64_in_json", "json_path": "data.0.b64_json", "ext": ".png" },
  "result_type": "image",
  "cost_per_call": 0.04,
  "license_note": "Check vendor terms",
  "commercial_ok": true,
  "params": [
    { "name": "prompt", "label": "Prompt", "type": "text" },
    { "name": "size", "label": "Size", "type": "select", "options": ["1024x1024"], "default": "1024x1024" }
  ]
}
```

Templating: any `{param}` in `endpoint`/`headers`/`body`/`query` is replaced with the job's param
value; `{key}` is the stored API key; `{input_b64}` / `{input_path}` reference the first input asset.

Output modes:
- `binary` — the HTTP response body *is* the file.
- `url_in_json` — `json_path` points to a URL, which is downloaded.
- `b64_in_json` — `json_path` points to base64 (optionally a `data:` URI).

The key is stored in the OS keychain under `key_name`. Custom providers persist in
`data/settings.json` and reload instantly.

## 2. Code — a first-class adapter

Drop a `Provider` subclass into the right sub-package and add one line to `providers/registry.py`.

```python
# backend/asset_studio/providers/image/my_thing.py
from ...models import AssetType, ProviderKind, ProviderParam, StageType
from ..base import JobContext, Provider

class MyThingProvider(Provider):
    id = "my-thing"
    name = "My Thing"
    stage = StageType.image2d
    kind = ProviderKind.api
    requires_key = True
    key_name = "mything"
    cost_hint = "~$0.02/img"
    commercial_ok = True
    params = [ProviderParam(name="prompt", label="Prompt", type="text")]

    def is_available(self):
        ok, why = super().is_available()      # checks the key
        return (ok, why)

    def run(self, ctx: JobContext):
        import httpx                            # heavy imports stay LAZY
        from ... import keychain
        key = keychain.get_key(self.key_name)
        ctx.progress(0.2, "calling API")
        # ... do the work, save bytes to ctx.out_path("out.png") ...
        ctx.add_cost(0.02)
        return [ctx.make_asset(path=ctx.out_path("out.png"),
                               type=AssetType.image, prompt=ctx.param("prompt"))]
```

```python
# backend/asset_studio/providers/registry.py  → add to _SPECS
("image.my_thing", "MyThingProvider"),
```

Rules: import heavy/optional deps **inside** methods (not at module top), return `(False, "actionable
reason")` from `is_available()` when the tool/key is missing, and build outputs with
`ctx.make_asset(...)`. The registry guards every import, so a broken adapter is skipped (and reported
in Settings → Providers overload errors) rather than crashing the app.

### JobContext quick reference
`ctx.param(name, default)`, `ctx.inputs`, `ctx.input_assets`, `ctx.first_image()`, `ctx.first_mesh()`,
`ctx.tool(name, default)` (settings → tools), `ctx.progress(frac, "step")`, `ctx.log(msg)`,
`ctx.add_cost(usd)`, `ctx.out_path("file.ext")`, `ctx.make_asset(path, type, name=…, prompt=…, seed=…,
meta=…, license=…, commercial_ok=…, preview_path=…, parent_id=…)`.

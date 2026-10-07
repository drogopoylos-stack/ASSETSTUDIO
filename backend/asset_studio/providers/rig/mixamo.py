"""Mixamo — documented manual handoff (Adobe has no public Mixamo API).

Mixamo auto-rigs and animates humanoid meshes for free and royalty-free, but
Adobe never shipped a public API, and automating the site violates its terms. So
this provider is honest about that: it surfaces the exact manual steps instead of
pretending to call a nonexistent endpoint.

Workflow:

1. Upload your mesh (FBX/OBJ) at https://www.mixamo.com/.
2. Use Auto-Rigger to place markers and rig it.
3. Pick/preview animations, then download the rigged FBX (with skin).
4. Import that FBX back into the studio (as a new rig-stage asset).
"""
from __future__ import annotations

from ...models import ProviderKind, StageType
from ..base import JobContext, Provider

_GUIDANCE = (
    "Mixamo has no public API. Upload your mesh at mixamo.com, auto-rig, then "
    "download the rigged FBX and import it back."
)


class MixamoProvider(Provider):
    """Manual Mixamo handoff — surfaces steps; does not call a (nonexistent) API."""

    id = "mixamo"
    name = "Mixamo (manual handoff)"
    stage = StageType.rig
    kind = ProviderKind.api
    requires_key = False
    key_name = None
    description = (
        "Royalty-free auto-rigging + animation via Adobe Mixamo. There is no public API, "
        "so this is a documented manual step: rig on mixamo.com and re-import the FBX."
    )
    license_note = "Mixamo assets are royalty-free for commercial use (Adobe)."
    commercial_ok = True
    cost_hint = "free"
    homepage = "https://www.mixamo.com/"

    def is_available(self) -> tuple[bool, str]:
        return (
            False,
            _GUIDANCE,
        )

    def run(self, ctx: JobContext) -> list:
        raise RuntimeError(
            f"{_GUIDANCE} This is a documented manual step — Mixamo cannot be automated here."
        )

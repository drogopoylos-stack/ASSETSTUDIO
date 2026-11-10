"""/gamemodel: the Studio Engine's modelling tools, told to an agent only when the person asks.

The person wants the art tools (Poly Haven references, the modelling functions, curves, texture
sets, animation eases, the sprite editor) used ONLY for a message that starts with `/gamemodel`,
and says in that message whether the work is 2D or 3D. So none of it is in the always-on notes:
`/gamemodel` is taken off the message here, before any engine sees it (a CLI would read a slash
command as its own), and the instructions for that one request are put in front of the text.

    /gamemodel 3d a short sword with a wrapped grip
    /gamemodel make a 2d coin that spins, 6 frames

No "2d" or "3d" in the message: the agent asks which, before it builds anything.
"""
from __future__ import annotations

import re
from typing import Optional

_CMD = re.compile(r"^\s*/gamemodel\b[ \t]*", re.I)
_2D = re.compile(r"(?<![\w.])2\s*-?\s*d(?![\w])", re.I)
_3D = re.compile(r"(?<![\w.])3\s*-?\s*d(?![\w])", re.I)


def dimension(text: str) -> str:
    """"2d", "3d", "both" when the message names both, or "" when it names neither."""
    two, three = bool(_2D.search(text or "")), bool(_3D.search(text or ""))
    return "both" if two and three else "2d" if two else "3d" if three else ""


def _base() -> str:
    try:
        from .live_scene import _studio_base
        return _studio_base()
    except Exception:
        return "http://127.0.0.1:8777"


def note(dim: str, base: str = "") -> str:
    b = base or _base()
    three = (
        "[/gamemodel 3D] Make this a 3D model for the game with the Studio Engine tools (for this request only).\n"
        "- Build it in code: a builder in the game (export function buildX(opts) returning a named THREE.Group, feet on y=0), "
        "checked on the forge bench as the Forge note says (render, compare with a reference, read the numbers).\n"
        f"- Reference first: GET {b}/api/polyhaven/search?type=models&q=<word> (CC0, with real size dimensions_m); "
        f"POST {b}/api/polyhaven/download {{project,id,type:\"models\"|\"textures\"|\"hdris\",res:\"1k\"}} writes a .glb, "
        "a clay render (references.reference_clay, the shape alone) and LICENSE.txt into the project. Build to dimensions_m and aim the forge at the clay render. "
        "Poly Haven has props, furniture, plants, rocks and statues, and NO humans or creatures: do not search it for those.\n"
        f"- Humans at real size (CC0): GET {b}/api/refs/search?q=<words> lists bodies, heads, hands, feet, eyes, jaws and skulls "
        "(realistic and stylized, male and female) with dimensions_m, model_path, front_path and side_path; "
        f"POST {b}/api/refs/human {{height_m, gender 0..1 (1 male), age_years, muscle 0..1, weight 0..1, proportions 0..1, "
        "rig:\"\"|\"game_engine\"|\"mixamo\", project?}} makes a human at exactly that height in about 5 s (MPFB2), rigged when asked "
        "(\"mixamo\" bone names take Mixamo animations); with project it is copied into the game's assets/references/. "
        "Aim the forge at front_path or side_path and build to dimensions_m; a person's own reference sheet still decides the look.\n"
        f"- Creatures, characters and stylised game models (Poly Pizza, many rigged and animated, e.g. Quaternius's animals): "
        f"GET {b}/api/polypizza/search?q=<word>&animated=true&licence=CC0 then POST {b}/api/polypizza/download {{project,id}} "
        "writes the .glb, front.png/side.png, LICENSE.txt and its animations into the game's assets/polypizza/<id>/. Its size is "
        "the file's, not the real animal's: scale it to the real size. A CC-BY model needs the returned credit in the game's credits; "
        "prefer CC0.\n"
        f"- The modelling functions: GET {b}/api/live/api?q=<word> lists them with parameters (hand, riggedHand, sweep, curve, sculpt, "
        f"boolean, array, deform, decimate, bevel, subsurf, unwrap, bake...); import makeOps from {b}/forge-ops.js and call makeOps(THREE).\n"
        "- Edits that survive a rebuild go in <file>.edits.json beside the builder (applyEdits runs them): mods "
        "[{op:\"array\"|\"deform\"|\"decimate\"|\"boolean\"|\"bevel\"|\"subsurf\"|..., target, args}], topo (edit-mode steps), "
        "shapes (shape keys), weights, bones with constraints (ik, track, copyRotation, limitRotation), clips whose keys take "
        "ease \"bezier\"|\"linear\"|\"constant\"|\"back\"|\"bounce\"|\"elastic\" and handle slopes ti/to, parts.<key>.maps "
        "{baseColor,normal,arm,...,tile} for a downloaded Poly Haven texture set, and placed curves "
        "{ref:{kind:\"curve\",curve:{points:[[x,y,z],...],radius,profile:\"round\"|\"square\"|\"flat\",smooth,taper,closed}}}.\n"
        f"- In a running game: POST {b}/api/live/place {{project, asset:{{\"curve\":[[x,y,z],...],\"radius\":0.05}}}} puts a tube in (cables, rails, horns).\n"
        f"- Give the person the editor to look and finish by hand: {b}/engine?edit=<builder file>&project=<game folder> "
        "(edit mode, sculpt, shape keys, weights, rig, curves, material, graph editor, Export GLB/OBJ, Import OBJ/FBX)."
    )
    two = (
        "[/gamemodel 2D] Make this 2D art for a 2D game (Phaser, PixiJS or an HTML page), for this request only.\n"
        "- A sprite is a PNG in the game: public/assets/... in a Vite game, assets/... otherwise. Make it with the image tools, "
        "or draw it in code on a canvas and save the PNG. An animation is a sprite sheet with a TexturePacker \"JSON Hash\" atlas "
        "({frames:{name_0:{frame:{x,y,w,h},sourceSize,duration},...}, animations:{name:[...]}, meta:{image,size}}), which "
        "Phaser's load.atlas and PixiJS's Assets.load read.\n"
        f"- Put it into the running game: POST {b}/api/live/place {{project, asset:\"<project-relative .png>\", save:true}}; "
        f"move, scale, turn or hide it with POST {b}/api/live/edit; the objects with their boxes: GET {b}/api/live/stage?project=<game folder>.\n"
        f"- Textures (CC0): GET {b}/api/polyhaven/search?type=textures&q=<word>, then POST {b}/api/polyhaven/download.\n"
        f"- Give the person the sprite editor to finish by hand: {b}/engine?draw=<game folder> (pencil, brush, fill, shapes, "
        "layers, frames, onion skin). Its Save writes <name>.png and <name>.draw.json, and with frames <name>.sheet.png and "
        "<name>.sheet.json, into assets/drawn/."
    )
    if dim == "3d":
        return three
    if dim == "2d":
        return two
    if dim == "both":
        return three + "\n\n" + two
    return ("[/gamemodel] The person did not say 2D or 3D. Ask which one in one short question, and build "
            "nothing until they answer. When they answer, use the matching instructions below.\n\n" + three + "\n\n" + two)


def apply(message: str, base: str = "") -> tuple[str, Optional[str]]:
    """The message as the engine gets it, and "2d" / "3d" / "both" / "" when it was a /gamemodel
    request (None when it was not)."""
    m = _CMD.match(message or "")
    if not m:
        return message, None
    rest = (message or "")[m.end():].strip()
    dim = dimension(rest)
    return note(dim, base) + "\n\n---\n\n" + (rest or "(no request text)"), dim

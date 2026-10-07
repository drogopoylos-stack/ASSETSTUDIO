# -*- coding: utf-8 -*-
"""Look closely at the parts that decide whether a character reads — beside the reference.

WHAT WENT WRONG. A roblox-boy came out of the forge with its face upside down: the mouth above
the eyes. The forge had the pixels — the agent's own `focus` panels show it plainly — and nothing
in the answer said so. Three things were missing, and each is here:

  1. ANGLES. `focus` framed a part from ONE angle, the sheet's first. Four focus names gave four
     near-identical front close-ups of the same hair. A part is judged from several sides, so a
     detail target is photographed from the reference's angle, 40 degrees round, the side and
     the back.
  2. THE REFERENCE, CROPPED TO THE SAME PART. The target panel shows the whole body at the size
     the whole body needs, so its face is a smudge — and the agent wrote its own Python to cut
     the reference into face, hair and torso tiles. The crop is computed here, registered through
     the silhouette with the same rule the score uses: scale by height, align the centres.
  3. A NUMBER FOR WHICH WAY UP. The close-up is compared with the reference crop as built,
     turned upside down, and mirrored. A face that matches best upside down IS upside down, and
     that is a finding with the texture named, not something to notice.

Cheap by default: one silhouette frame and one close-up per target, numbers only. The other
angles and the picture are made when a part is asked for by name, or when its numbers say
something is wrong — the evidence arrives exactly when there is something to see.
"""
from __future__ import annotations

import json
import math
import re
from typing import Optional

# The forge's preset cameras, in step with DIRS in live_forge.py.
_DIRS = {"3q": (1, 0.62, 1.15), "front": (0, 0, 1), "back": (0, 0, -1), "side": (1, 0, 0),
         "left": (-1, 0, 0), "top": (0, 1, 0.0001), "bottom": (0, -1, 0.0001),
         "low": (0.8, -0.32, 1), "hero": (0.55, 0.28, 1), "back3q": (-1, 0.5, -1)}

# What a character is judged by, in priority order. Each group takes the first name present.
GROUPS = (("face", ("face", "eyes", "mouth")),
          ("head", ("head", "skull")),
          ("hair", ("hair", "fringe", "bangs")),
          ("torso", ("torso", "chest", "shirt", "jacket", "body")),
          # Added after the A/B: every fault the user could see was in one of these — the seam down
          # the front of the pants, the jacket pockets, the hands, and a shirt that should have
          # been longer. A part nobody photographs is a part nobody fixes.
          ("legs", ("pants", "leg", "jeans", "trousers", "skirt")),
          ("shoes", ("shoe", "boot", "sneaker", "foot")),
          ("hands", ("hand", "glove", "paw", "claw")),
          ("arms", ("arm", "sleeve")),
          # Round two of the same lesson: the pocket the reference has and the build has not,
          # and the drawstrings, were both invisible because nothing photographed them.
          ("pockets", ("pocket", "kangaroo", "flap")),
          ("trim", ("hood", "collar", "drawstring", "lace", "cuff")))
CANDIDATES = [n for _g, names in GROUPS for n in names]
_CHARACTER_TAGS = {"character", "creature", "person", "npc", "avatar", "humanoid", "animal",
                   "hero", "enemy", "mascot"}
MAX_TARGETS = 8
# Context kept round a part's box on both sides of the comparison, as a share of its size.
PAD = 0.12

# Calibrated on the roblox-boy: see detail_test.py for the cases these thresholds decide.
FLIP_GAIN = 0.10      # how much better upside down has to score to BE upside down. The real
                      # face was 0.16 clear; a shoe at 0.07 was a coin toss, and a coin toss
                      # that names a texture costs an agent a rebuild for nothing.
MIRROR_GAIN = 0.08
FLOOR = 0.45          # a flipped score this low is two strangers, not one part the wrong way up
FAR = 0.40            # below this, the part does not look like the reference's at all
# A part that is its own mirror cannot be called upside down: two dark trouser legs matched their
# own flip 0.84 on the real boy, and the answer accused a texture that was never the problem. Above
# this self-similarity the orientation test says nothing and is not run.
SYMMETRY = 0.82
# And a part with nothing IN it cannot be judged either: the boy's trouser legs are two near-flat
# dark tubes, and they "matched" their own flip 0.84 because two flat patches agree perfectly by
# construction. Grey levels of variation inside the part, measured on its own pixels.
STRUCTURE_MIN = 12.0
# This much better alone than as seen: something stands in front of it. Calibrated on the
# roblox-boy, whose hair strands hang across its face: 0.63 alone against 0.51 as seen.
COVER_GAP = 0.10
# The sheet's close-ups keep this much of what is round the part; the numbers use PAD.
SHOT_PAD = 0.35
# Two names that frame the same box are one picture taken twice. The container goes first.
DUP_IOU = 0.9
_CONTAINERS = {"head", "skull", "body"}


def az_el(view: str) -> tuple:
    """The azimuth and elevation a view asks for — a spec's own numbers, or a preset's."""
    s = str(view or "")
    if "=" in s:
        az = el = 0.0
        for kv in s.replace(";", ",").replace(" ", ",").split(","):
            p = kv.split("=")
            if len(p) != 2:
                continue
            k = p[0].strip().lower()
            try:
                v = float(p[1])
            except ValueError:
                continue
            if k in ("az", "azimuth"):
                az = v
            elif k in ("el", "elev", "elevation"):
                el = v
        return round(az % 360.0, 1), round(max(-89.0, min(89.0, el)), 1)
    d = _DIRS.get(s, _DIRS["3q"])
    az = math.degrees(math.atan2(d[0], d[2])) % 360.0
    el = math.degrees(math.atan2(d[1], math.hypot(d[0], d[2])))
    return round(az, 1), round(el, 1)


def angles_for(view: str, extra: Optional[list] = None) -> list:
    """[(spec, label)] — the reference's own angle first, because only that one can be compared."""
    if extra:
        rest = [str(v).strip() for v in extra if str(v).strip() and str(v).strip() != view]
        return [(view, view)] + [(v, v) for v in rest][:4]
    az, el = az_el(view)

    def spec(a: float, e: float) -> str:
        return "az=%g,el=%g" % (round(a % 360.0, 1), round(e, 1))
    return [(view, view), (spec(az + 40, el), "+40°"), (spec(az + 90, 0), "side"),
            (spec(az + 180, 0), "back")]


def pick_targets(explicit: Optional[list], present: Optional[list], tags: Optional[list],
                 has_ref: bool) -> list:
    """Which parts to look at closely. Named parts always; otherwise only a character with a target.

    Automatic only when there is a reference to hold the parts against AND the subject is a
    character — by its tags, or because it has a face or a head at all. A crate needs none of it.
    """
    if explicit:
        return [str(t).strip() for t in explicit if str(t).strip()][:MAX_TARGETS]
    have = [str(p).lower() for p in (present or []) if isinstance(p, str)]
    tagset = {str(t).lower() for t in (tags or [])}
    charish = bool(tagset & _CHARACTER_TAGS) or any(n in have for n in ("face", "head", "eyes"))
    if not (has_ref and charish):
        return []
    out = []
    for _group, names in GROUPS:
        for n in names:
            if n in have:
                out.append(n)
                break
    return out[:MAX_TARGETS]


# ---------------------------------------------------------------- twins
# ONE HAND IS NOT BOTH HANDS. The detail targets are words, and a word is a substring: `hand` found
# handL AND handR (and, on the forge goblin, shieldHandle), so the review judged one merged box and
# OFFSET read "39 px up and 60 px right" about a pair of fists nobody could place from it. A target
# that reaches both sides of a pair is split into the two, each by its exact name.
_SIDE_PATTERNS = (
    (r"^(.*[a-z0-9])(Left|Right)$", 1, 2),                              # handLeft
    (r"^(.+?)[_.\- ](left|right|Left|Right|LEFT|RIGHT)$", 1, 2),        # hand_left, Hand.Right
    (r"^(.*[a-z0-9])([LR])$", 1, 2),                                    # handL, earR
    (r"^(.+?)[_.\- ]([lLrR])$", 1, 2),                                  # hand_l, hand.R
    (r"^(left|right)([A-Z0-9_].*)$", 2, 1),                             # leftHand
    (r"^(left|right|Left|Right|LEFT|RIGHT)[_.\- ](.+)$", 2, 1),         # Left_Hand
    (r"^([lLrR])[_.\- ](.+)$", 2, 1),                                   # L_hand
)


def side_key(name: str) -> Optional[tuple]:
    """(stem, side) for a name that marks a side or a number - handL, hand_r, Hand.Left, leftHand,
    L_hand, arm.001 - else None. Case decides it where it must: `girl` is not a left `gir`."""
    s = str(name or "").strip()
    for pat, stem_g, side_g in _SIDE_PATTERNS:
        m = re.match(pat, s)
        if m:
            stem = m.group(stem_g).strip("_.- ").lower()
            if stem:
                return stem, m.group(side_g)[0].upper()
    m = re.match(r"^(.+?)[_.\- ]?(\d{1,3})$", s)
    if m and m.group(1).strip("_.- "):
        return m.group(1).strip("_.- ").lower(), "#%d" % int(m.group(2))
    return None


def split_twins(targets: list, matches: dict, cap: int = 12) -> list:
    """The targets, with every one that reaches a left AND a right (or exactly two numbered
    copies) replaced by those two, as exact names ("=handL"). Order kept, at most `cap`."""
    out = []
    for t in targets or []:
        names = [n for n in ((matches or {}).get(t) or []) if isinstance(n, str) and n.strip()]
        groups: dict = {}
        for n in names:
            k = side_key(n)
            if k:
                groups.setdefault(k[0], {}).setdefault(k[1], n)
        twins = []
        for sides in groups.values():
            if "L" in sides and "R" in sides:
                twins += [sides["L"], sides["R"]]
            elif len(sides) == 2 and all(s.startswith("#") for s in sides):
                twins += [sides[s] for s in sorted(sides, key=lambda x: int(x[1:]))]
        if twins:
            out.extend("=" + n for n in twins)
        else:
            out.append(t)
    seen, res = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            res.append(t)
    return res[:cap]


def label_of(q: str) -> str:
    """What a person calls a target: the name, without the exact-match mark."""
    return str(q)[1:] if str(q).startswith("=") else str(q)


def bbox_of_mask(m) -> Optional[tuple]:
    import numpy as np
    if m is None:
        return None
    ys, xs = np.where(m)
    if not len(ys):
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def register_box(box_norm, panel_size, sil_bbox, ref_bbox) -> tuple:
    """A part's box in the render's panel, carried into the reference picture's pixels.

    Height-normalised and centre-aligned — `_compare_masks`'s own registration, so the part lands
    where the score already believes the two silhouettes line up.
    """
    W, H = panel_size
    sx0, sy0, sx1, sy1 = sil_bbox
    rx0, ry0, rx1, ry1 = ref_bbox
    s = max(1.0, ry1 - ry0) / max(1.0, sy1 - sy0)
    scx, rcx = (sx0 + sx1) / 2.0, (rx0 + rx1) / 2.0
    x0, y0 = box_norm[0] * W, box_norm[1] * H
    x1, y1 = box_norm[2] * W, box_norm[3] * H
    return (rcx + (x0 - scx) * s, ry0 + (y0 - sy0) * s, rcx + (x1 - scx) * s, ry0 + (y1 - sy0) * s)


def pad_px(box, pad: float, size) -> tuple:
    x0, y0, x1, y1 = box
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    x0, x1 = x0 - w * pad, x1 + w * pad
    y0, y1 = y0 - h * pad, y1 + h * pad
    W, H = size
    x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
    x1, y1 = min(W, int(round(x1))), min(H, int(round(y1)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return (x0, y0, x1, y1)


def crop_norm(im, box_norm, pad: float = PAD):
    W, H = im.size
    b = pad_px((box_norm[0] * W, box_norm[1] * H, box_norm[2] * W, box_norm[3] * H), pad, im.size)
    return im.crop(b) if b else None


def crop_box(im, box_norm, pad: float = PAD):
    """The pixel box crop_norm cuts, so a mask can be cut to the same rectangle."""
    W, H = im.size
    return pad_px((box_norm[0] * W, box_norm[1] * H, box_norm[2] * W, box_norm[3] * H), pad, im.size)


def part_mask(frame, box):
    """Where the part is drawn inside `box`: every pixel that is not the backdrop.

    Read from the frame where the part stands ALONE, so it is the part's own outline. The backdrop
    colour is read off the frame's border rather than assumed, because a forge call can set one."""
    import numpy as np
    a = np.asarray(frame.convert("RGB"), dtype=np.int16)
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(edge, axis=0)
    m = np.abs(a - bg).max(axis=2) > 24
    x0, y0, x1, y1 = box
    return m[y0:y1, x0:x1]


# A patch whose grey varies less than this has no structure to compare. Normalising it would
# divide noise by almost nothing, and leaving it at zeros scored a plain grey square 0.41 against
# a face — "close enough" for a part with no eyes and no mouth.
FLAT_SD = 4.0
# And below this much contrast there is not much structure to compare either. Two near-uniform
# patches, each normalised to unit variance, compare their own noise; the best of 147 windows then
# finds a lucky match, which is how the boy's two dark trouser tubes "matched" their own flip 0.84
# and the answer accused a texture that was never the problem. Structure gets the weight its
# evidence deserves, and colour takes the rest.
CONTRAST_FULL = 25.0


def _prep(im, mask=None):
    """Grey at 32x32 and colour at 8x8, with the mask at both sizes (all True when there is none)."""
    import numpy as np
    from PIL import Image
    g = np.asarray(im.convert("L").resize((32, 32), Image.BILINEAR), dtype=np.float32)
    c = np.asarray(im.convert("RGB").resize((8, 8), Image.BILINEAR), dtype=np.float32)
    if mask is None:
        return g, c, np.ones((32, 32), bool), np.ones((8, 8), bool)
    mi = Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255)
    return (g, c, np.asarray(mi.resize((32, 32), Image.BILINEAR)) >= 128,
            np.asarray(mi.resize((8, 8), Image.BILINEAR)) >= 128)


def _score(a, b) -> float:
    """0..1 over the part's own pixels: structure (brightness-normalised grey) carries most of it,
    colour the rest. `a` is the render; its mask decides which pixels count on both sides.

    The mask exists because of the roblox-boy's face alone: a third of its crop was the studio
    backdrop, identical in every orientation, and it drowned the eyes and the mouth."""
    import numpy as np
    ga, ca, m32, m8 = a
    gb, cb = b[0], b[1]
    if int(m32.sum()) < 24:
        m32 = np.ones_like(m32)
    if int(m8.sum()) < 4:
        m8 = np.ones_like(m8)
    va, vb = ga[m32], gb[m32]
    sa, sb = float(va.std()), float(vb.std())
    fa, fb = sa < FLAT_SD, sb < FLAT_SD
    if fa and fb:
        # NEITHER has structure. They agree about that, and it is worth nothing: two plain patches
        # of different colours are not a match, so the structure term claims no weight at all.
        sg, w = 1.0, 0.0
    elif fa or fb:
        sg, w = 0.0, 1.0                      # plain against detail IS a mismatch, and a certain one
    else:
        sg = max(0.0, 1.0 - float(np.abs((va - va.mean()) / sa - (vb - vb.mean()) / sb).mean()) / 1.2)
        w = min(1.0, min(sa, sb) / CONTRAST_FULL)
    sc = max(0.0, 1.0 - float(np.abs(ca[m8] - cb[m8]).mean()) / 128.0)
    return (0.65 * w) * sg + (1.0 - 0.65 * w) * sc


def _turns(render_crop, mask):
    """The render as built, upside down and mirrored, each with its mask turned the same way."""
    import numpy as np
    from PIL import ImageOps
    m = None if mask is None else np.asarray(mask, bool)
    return {"match": _prep(render_crop, m),
            "upside_down": _prep(ImageOps.flip(render_crop), None if m is None else m[::-1]),
            "mirrored": _prep(ImageOps.mirror(render_crop), None if m is None else m[:, ::-1])}


def compare(render_crop, ref_crop, mask=None) -> dict:
    """The close-up against one reference crop: as built, upside down, and mirrored."""
    r = _prep(ref_crop)
    return {k: round(_score(v, r), 4) for k, v in _turns(render_crop, mask).items()}


# How far round the registered box the reference is searched, as a share of the box, and at which
# scales. Registration through the silhouette is only as good as the proportions: the roblox-boy's
# eyes sat 15% higher on its head than the reference's, and a fixed crop compared them with the
# reference's fringe.
SHIFT, STEP, SCALES = 0.18, 0.06, (0.9, 1.0, 1.12)


def search(render_crop, ref_img, rbox, mask=None) -> tuple:
    """(numbers, the reference box the part matched best as built).

    Each orientation gets its own best window, so a part is called upside down only when no nearby
    crop of the reference explains it the right way up. 147 windows, under a tenth of a second."""
    x0, y0, x1, y1 = rbox
    w, h = float(x1 - x0), float(y1 - y0)
    W, H = ref_img.size
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    n = int(round(SHIFT / STEP))
    cands = []
    for s in SCALES:
        for iy in range(-n, n + 1):
            for ix in range(-n, n + 1):
                ccx, ccy = cx + ix * STEP * w, cy + iy * STEP * h
                b = (int(round(ccx - w * s / 2)), int(round(ccy - h * s / 2)),
                     int(round(ccx + w * s / 2)), int(round(ccy + h * s / 2)))
                if b[0] < 0 or b[1] < 0 or b[2] > W or b[3] > H or b[2] - b[0] < 8 or b[3] - b[1] < 8:
                    continue
                cands.append((b, _prep(ref_img.crop(b))))
    if not cands:
        b = (int(x0), int(y0), int(x1), int(y1))
        cands = [(b, _prep(ref_img.crop(b)))]
    turns = _turns(render_crop, mask)
    # How like its own mirror the part is, and how much there is in it at all. A shape that IS its
    # own flip cannot be judged by which way up it matches, and neither can a flat one — both name
    # a texture that did nothing wrong.
    import numpy as _np
    _g, _c, _m32, _m8 = turns["match"]
    _vals = _g[_m32] if int(_m32.sum()) >= 24 else _g
    out = {"sym_v": round(_score(turns["upside_down"], (turns["match"][0], turns["match"][1])), 4),
           "sym_h": round(_score(turns["mirrored"], (turns["match"][0], turns["match"][1])), 4),
           "structure": round(float(_vals.std()), 1)}
    best_box = cands[0][0]
    for k, v in turns.items():
        best, bb = -1.0, cands[0][0]
        for b, pr in cands:
            s = _score(v, pr)
            if s > best:
                best, bb = s, b
        out[k] = round(best, 4)
        if k == "match":
            best_box = bb
    return out, best_box


def verdict(n: Optional[dict]) -> str:
    if not n:
        return ""
    m, u, r = float(n.get("match") or 0), float(n.get("upside_down") or 0), float(n.get("mirrored") or 0)
    sym_v, sym_h = float(n.get("sym_v") or 0), float(n.get("sym_h") or 0)
    told = float(n.get("structure", 99.0)) >= STRUCTURE_MIN
    if told and u >= m + FLIP_GAIN and u >= FLOOR and sym_v < SYMMETRY:
        return "upside_down"
    if told and r >= m + MIRROR_GAIN and r >= FLOOR and sym_h < SYMMETRY:
        return "mirrored"
    seen = n.get("seen")
    if seen is not None and m >= FAR and m - float(seen) >= COVER_GAP:
        return "covered"
    if m < FAR:
        return "far"
    return ""


def _texture_hint(tex: list, engine: str) -> str:
    # A TEXTURE A glTF LOADER MADE is flipY:false by the glTF convention - its first row is v=0 on
    # purpose - so "set flipY = true" would turn a right texture upside down. The page marks them
    # (`gltf`: three's GLTFLoader stamps userData.mimeType; anything under a GLB the bench loaded).
    bad = [t for t in (tex or []) if isinstance(t, dict) and t.get("flipY") is False
           and t.get("source") in ("canvas", "image", "bitmap") and not t.get("gltf")]
    if not bad:
        return ""
    t = bad[0]
    name = "%s.%s" % (t.get("material") or "?", t.get("slot") or "map")
    if str(engine or "").lower() == "playcanvas":
        return ("Its texture %s is a %s stored with flipY:false - PlayCanvas samples the %s's TOP "
                "row at v=0, so art drawn for three.js's v-up lands upside down. Pass flipY:true to "
                "new pc.Texture(...), or use v = 1 - v in that part's UVs."
                % (name, t.get("source"), t.get("source")))
    return ("Its texture %s has flipY:false, so the image's first row sits at v=0. Set flipY = true "
            "(three's own default for images and canvases), or use v = 1 - v in its UVs." % name)


# What can actually be stored the wrong way up: a picture the code made or loaded itself - a
# canvas, an image, a bitmap, or texels it wrote (a DataTexture) - and never one a glTF file
# brought, which is the right way up by definition.
_FLIPPABLE = ("canvas", "image", "bitmap", "data")


def flip_doubtful(row: dict) -> str:
    """Why an "upside down" verdict cannot be about a texture, or "" when it can.

    UPSIDE DOWN ONLY WHERE IT CAN BE TRUE. The goblin A/B produced four UPSIDE DOWN lines - legs, a
    fist - on parts that carry no texture at all, only vertex colours: nothing on them could be
    upside down, and the likelier cause was the reference search, which had settled on another
    region of the picture. The textures are only looked up for a flagged part, so a row that never
    had them looked up (`textures` absent) is left as it was."""
    if not isinstance(row, dict) or row.get("verdict") != "upside_down" or "textures" not in row:
        return ""
    tex = [t for t in (row.get("textures") or []) if isinstance(t, dict)]
    if any(t.get("source") in _FLIPPABLE and not t.get("gltf") for t in tex):
        return ""
    if tex and all(t.get("gltf") for t in tex):
        return ("its textures came from a glTF file, which keeps them the right way up by "
                "definition")
    if tex:
        return "none of its textures is a picture that could be stored the other way up"
    return "it has no texture at all - its colour is the material's, or vertex colours"


def _luma(h: str) -> float:
    try:
        p = [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return 0.0
    return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]


def _scaled(h: str, gain: float) -> str:
    """A colour at another render's brightness, so paint can be compared with paint."""
    try:
        p = [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return h
    return "#" + "".join("%02x" % max(0, min(255, int(round(v * gain)))) for v in p)


def chroma_gap(a: str, b: str) -> int:
    """How far apart two colours are AT THE SAME BRIGHTNESS.

    Scaling one colour up to the other's exposure clips: a skin tone at #ead8b1 multiplied by 1.26
    saturates to white and reads 173 away from a skin tone. Brightness is a lamp and belongs in the
    LEVELS line; this is the part that is paint."""
    try:
        pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return 0
    la, lb = _luma(a), _luma(b)
    if la < 8 or lb < 8:
        return 0                       # a black region has no colour to be wrong about
    ka, kb = 128.0 / la, 128.0 / lb
    return int(round(sum(abs(x * ka - y * kb) for x, y in zip(pa, pb))))


def _hex_gap(a: str, b: str) -> int:
    """How far apart two hex colours are, summed over R, G and B."""
    try:
        pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, IndexError):
        return 0
    return sum(abs(x - y) for x, y in zip(pa, pb))


def findings(rows: list, engine: str, view: str) -> list:
    """Problems first, one line each; then one line of every part's number."""
    out = []
    scored = [r for r in rows if r.get("numbers")]
    for r in rows:
        n = r.get("numbers") or {}
        t, v = r.get("target"), r.get("verdict")
        why_not = flip_doubtful(r)
        if why_not:
            out.append("DETAIL: `%s` scores %.2f turned upside down against %.2f as built, seen "
                       "from %s - but nothing on it can be stored upside down: %s. The likelier "
                       "cause is the reference search, which settled on another region of the "
                       "picture (a neighbouring part, or this one at another height); compare the "
                       "two crops in `detail_sheet`. Only if the part itself is built inverted is "
                       "it its rotation."
                       % (t, n.get("upside_down", 0.0), n.get("match", 0.0), view, why_not))
        elif v == "upside_down":
            hint = _texture_hint(r.get("textures") or [], engine)
            out.append("UPSIDE DOWN: `%s` matches the reference %.2f as built and %.2f turned upside "
                       "down, seen from %s - it is upside down. %s See `detail_sheet`."
                       % (t, n["match"], n["upside_down"], view,
                          hint or "Check its rotation and the V of its UVs."))
        elif v == "mirrored":
            out.append("MIRRORED: `%s` matches the reference %.2f mirrored against %.2f as built - "
                       "its left and right are swapped. See `detail_sheet`."
                       % (t, n["mirrored"], n["match"]))
        elif v == "covered":
            out.append("COVERED: `%s` matches the reference %.2f on its own but %.2f as seen from %s - "
                       "another part stands in front of it. `detail_sheet` shows it alone and as seen."
                       % (t, n["match"], n["seen"], view))
        elif v == "far":
            out.append("DETAIL: `%s` matches the reference only %.2f from %s - its shape or colour "
                       "there is off. `detail_sheet` puts the two side by side, with three more "
                       "angles." % (t, n["match"], view))

    # COLOUR AND SIZE, the two numbers an agent used to get by writing its own swatch and
    # measure scripts. Only the parts that are actually off are named.
    #
    # A render that is uniformly darker than the reference is a LAMP, not paint, and saying
    # "your jacket is too dark" about the exposure teaches an agent to repaint what was right.
    # So one brightness ratio is taken from the parts themselves, reported once, and every colour
    # is judged after it.
    pairs = [(r.get("colour"), r.get("ref_colour")) for r in rows]
    ratios = sorted(_luma(rc) / max(6.0, _luma(c)) for c, rc in pairs if c and rc)
    gain = ratios[len(ratios) // 2] if ratios else 1.0
    if ratios and (gain > 1.18 or gain < 0.85):
        out.append("LEVELS: everything renders %.2f\u00d7 %s than the reference. Change "
                   "\"exposure\" or \"env\", or read the colours below as relative."
                   % (gain if gain > 1 else 1.0 / gain, "darker" if gain > 1 else "brighter"))
    for r in rows:
        t = r.get("target")
        # AT MOST THREE LINES PER PART, most decisive first. The first run against the real build
        # produced forty, and forty lines is not a report - it is a haystack with the answer in it.
        mine: list = []
        c, rc = r.get("colour"), r.get("ref_colour")
        flat = float((r.get("numbers") or {}).get("structure") or 0.0) <= STRUCTURE_FLAT
        if c and rc and flat and chroma_gap(c, rc) >= COLOUR_GAP:
            mine.append("COLOUR: `%s` renders %s where the reference's is %s \u2014 %d apart at the "
                        "same brightness, so it is the paint and not the lamp."
                        % (t, c, rc, chroma_gap(c, rc)))
        tone, rtone = r.get("tone"), r.get("ref_tone")
        if tone and rtone:
            # After the lamp, not before it: LEVELS above already said the whole render is N times
            # off, and repeating that per part teaches an agent to repaint what was right.
            lt, rlt = float(tone["light"]), float(rtone["light"]) * gain
            bk, rbk = float(tone.get("black") or 0), float(rtone.get("black") or 0)
            if rbk >= BLACK_SHARE and bk <= max(0.02, rbk * 0.25):
                mine.append("TONE: `%s` never gets dark - %d%% of the reference's is near-black and "
                            "%d%% of yours is. The shadow BETWEEN the parts of a mass is what makes "
                            "them read as separate; with none they merge into one shape, however "
                            "many pieces you modelled." % (t, round(rbk * 100), round(bk * 100)))
            elif lt > 8 and rlt / lt >= TONE_FLAT:
                mine.append("TONE: `%s` never catches the light - its brightest is %d where the "
                            "reference's is %d." % (t, round(lt), round(rlt)))
        sz, rsz = r.get("size"), r.get("ref_size")
        if sz and rsz:
            said = []
            for i, what in ((1, "tall"), (0, "wide")):
                a, b = float(sz[i]), float(rsz[i])
                if b > 0.01 and abs(a - b) / b >= SIZE_GAP:
                    said.append("%.2f of the figure's height %s against %.2f (%+d%%)"
                                % (a, what, b, round(100.0 * (a - b) / b)))
            if said:
                mine.append("SIZE: `%s` is %s." % (t, ", and ".join(said)))
        mk, rmk = r.get("marks"), r.get("ref_marks")
        if rmk:
            mk = mk or []
            if len(mk) != len(rmk):
                mine.append("FEATURES: the reference's `%s` carries %d dark marks and yours carries "
                            "%d - one is missing, merged into another, or covered by something in "
                            "front of it. `detail_sheet` shows both." % (t, len(rmk), len(mk)))
            else:
                names = (["an eye", "an eye", "the mouth"] if len(rmk) == 3
                         else ["mark %d" % (i + 1) for i in range(len(rmk))])
                marks = []
                for i, (a, b) in enumerate(zip(mk, rmk)):
                    aw, ah = a["box"][2] - a["box"][0], a["box"][3] - a["box"][1]
                    bw, bh = b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]
                    ay = (a["box"][1] + a["box"][3]) / 2.0
                    by = (b["box"][1] + b["box"][3]) / 2.0
                    bits = []
                    if bw > 0.01 and abs(aw - bw) / bw >= MARK_GAP:
                        bits.append("%+d%% wide" % round(100.0 * (aw - bw) / bw))
                    if bh > 0.01 and abs(ah - bh) / bh >= MARK_GAP:
                        bits.append("%+d%% tall" % round(100.0 * (ah - bh) / bh))
                    if abs(ay - by) >= 0.05:
                        bits.append("sitting %.2f of the part too %s"
                                    % (abs(ay - by), "low" if ay > by else "high"))
                    if bits:
                        marks.append("FEATURES: %s printed on `%s` is %s against the reference's."
                                     % (names[i] if i < len(names) else "mark %d" % (i + 1), t,
                                        ", ".join(bits)))
                mine.extend(marks[:2])
        if tone and rtone:
            b2, rb2 = float(tone.get("busy") or 0), float(rtone.get("busy") or 0)
            if rb2 > 4 and b2 / max(rb2, 0.1) >= BUSY_GAP:
                mine.append("BUSY: `%s` carries %.1f times the edge detail of the reference's - a "
                            "seam, a panel or a crease the reference has not."
                            % (t, b2 / max(rb2, 0.1)))
            elif b2 > 3 and rb2 / max(b2, 0.1) >= BUSY_GAP:
                mine.append("PLAIN: `%s` carries %.1f times less edge detail than the reference's - "
                            "a pocket, a seam or a fold it has and you have not."
                            % (t, rb2 / max(b2, 0.1)))
        off = r.get("offset")
        if off and (abs(off[0]) >= OFFSET_GAP or abs(off[1]) >= OFFSET_GAP):
            say = []
            if abs(off[1]) >= OFFSET_GAP:
                say.append("%.3f of the figure's height too %s"
                           % (abs(off[1]), "high" if off[1] > 0 else "low"))
            if abs(off[0]) >= OFFSET_GAP:
                say.append("%.3f too far %s" % (abs(off[0]), "left" if off[0] > 0 else "right"))
            mine.append("OFFSET: `%s` is %s - %d px %s and %d px %s on the reference itself."
                        % (t, " and ".join(say), abs(round(off[3])),
                           "up" if off[3] > 0 else "down", abs(round(off[2])),
                           "left" if off[2] > 0 else "right"))
        out.extend(mine[:3])

    def one(r):
        m, s = r["numbers"]["match"], r["numbers"].get("seen")
        if s is not None and abs(m - s) >= 0.05:
            return "%s %.2f (%.2f as seen)" % (r["target"], m, s)
        return "%s %.2f" % (r["target"], m)
    if scored:
        out.append("DETAIL at %s, close-up against the same crop of the reference: %s."
                   % (view, ", ".join(one(r) for r in scored)))
    return out



# Two parts of the same near-black are one region to a colour match, so a size read from a colour
# region is only reported when that region is about the size of the part that asked for it.
# A region this much bigger than the part is two parts of one colour, not the part: the boy's
# near-black legs grew into the whole dark figure and the answer read "the reference's is 0.90".
SIZE_SLACK = 1.8
# A swatch describes a part that is one colour. Above this the part is a PICTURE - the boy's face
# is an alpha decal whose opaque pixels are two eyes and a mouth, so its median is the mouth's
# red and the answer read "304 apart" about skin that was fine.
STRUCTURE_FLAT = 25.0
# No single named garment part is this much of a character's height. Two parts of one near-black
# join into one region: the trousers found the trousers AND the hoodie, 0.81 of the figure tall,
# and the answer read "-59%". A shirt genuinely half as long as it should be still reports.
REF_PART_MAX = 0.7
SIZE_FLOOR = 0.5
# How far a pixel may be from the part's own colour, summed over R, G and B.
COLOUR_NEAR = 90
# A mark printed on a face has to be at least this much of the face to be a feature rather than
# a shadow, and this much darker than the plate it is printed on.
MARK_MIN = 0.004
MARK_DARK = 0.62
# A printed feature this much wider, taller or further down than the reference's is worth a line.
MARK_GAP = 0.22
# Worth saying out loud: a fifth of the figure's height, or a colour this far off.
SIZE_GAP = 0.15
COLOUR_GAP = 26
# A part whose darkest pixel is this many times the reference's darkest never gets dark. The
# re-run's hair: darkest 65 against the reference's 9, and 0.0% of it below 40 where the
# reference is 31.9% below 40. Deep shadow between the strands is what separates them.
TONE_GAP = 1.7
# ...and a part this much flatter than the reference is under-lit rather than over-lit.
TONE_FLAT = 1.7
# Edge energy inside a part against the reference's: a seam the reference has not, or a pocket
# it has. Under this ratio it is texture noise, not a feature.
BUSY_GAP = 2.0
# A mass reads as separate parts because of the dark BETWEEN them. This is the share of a part
# that is near-black: the reference boy's hair is 0.32 of it and the re-run's is 0.00, and that
# one number is the whole difference between separate strands and one glossy lump.
BLACK_SHARE = 0.12
# Where the part sits against where the reference puts it, as a share of the figure's height.
# Two pixels on a 520 px reference is 0.004, so this is five of them.
OFFSET_GAP = 0.02


def _hex(rgb) -> str:
    return "#" + "".join("%02x" % max(0, min(255, int(round(v)))) for v in rgb[:3])


def part_colour(im, mask=None):
    """The part's own colour: the median of the pixels that are the part, never the mean.

    The median ignores a specular highlight and a dark seam; the mean splits the difference
    between them and reports a colour that is nowhere in the picture."""
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    if mask is not None:
        m = np.asarray(mask, bool)
        if m.shape == a.shape[:2] and int(m.sum()) >= 16:
            return [int(v) for v in np.median(a[m], axis=0)]
    return [int(v) for v in np.median(a.reshape(-1, 3), axis=0)]


def ref_patch_colour(ref_img, box, mask=None, subject=None):
    """The reference's OWN colour where the part registered.

    Through the part's own mask, so the two medians are taken over the same shape: an alpha face
    decal whose opaque pixels are two eyes and a mouth is compared against the reference's eyes
    and mouth, not against the skin around them.
    """
    import numpy as np
    from PIL import Image
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    a = np.asarray(ref_img.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.int16)
    keep = np.ones(a.shape[:2], bool)
    if mask is not None:
        m = np.asarray(mask, bool)
        if m.shape != a.shape[:2]:
            m = np.asarray(Image.fromarray((m * 255).astype("uint8"))
                           .resize((a.shape[1], a.shape[0]), Image.NEAREST)) > 127
        if m.any():
            keep = keep & m
    if subject is not None:
        s = np.asarray(subject, bool)
        if s.shape[0] >= y1 and s.shape[1] >= x1:
            keep = keep & s[y0:y1, x0:x1]
    if int(keep.sum()) < 16:
        keep = np.ones(a.shape[:2], bool)
    return [int(v) for v in np.median(a[keep], axis=0)]


def colour_region(ref_img, rgb, near_box, subject=None):
    """Where that colour lives in the reference, near where the part was registered.

    This is the measure that answers "the blue shirt should have been longer": the reference's
    blue region is found, its box is measured, and the two heights are compared as a share of the
    figure. Two parts of the same near-black cannot be told apart this way, so the caller drops a
    region that is far bigger than the part that asked for it."""
    import numpy as np
    a = np.asarray(ref_img.convert("RGB"), dtype=np.int16)
    H, W = a.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in near_box]
    w, h = max(4, x1 - x0), max(4, y1 - y0)
    # A window round the registered box: enough to see a part that is longer than it should be.
    wx0, wy0 = max(0, int(x0 - w * 0.8)), max(0, int(y0 - h * 0.8))
    wx1, wy1 = min(W, int(x1 + w * 0.8)), min(H, int(y1 + h * 0.8))
    if wx1 - wx0 < 4 or wy1 - wy0 < 4:
        return None
    win = a[wy0:wy1, wx0:wx1]
    near = np.abs(win - np.asarray(rgb, dtype=np.int16)).sum(axis=2) < COLOUR_NEAR
    if subject is not None:
        s = np.asarray(subject, bool)
        if s.shape == a.shape[:2]:
            near = near & s[wy0:wy1, wx0:wx1]
    if int(near.sum()) < 24:
        return None
    try:
        from scipy import ndimage
        near = ndimage.binary_closing(near, np.ones((3, 3), bool))
        lab, n = ndimage.label(near)
        if n > 1:
            # The piece that covers the registered box, not the biggest piece in the window.
            cy, cx = int((y0 + y1) / 2 - wy0), int((x0 + x1) / 2 - wx0)
            cy = max(0, min(lab.shape[0] - 1, cy))
            cx = max(0, min(lab.shape[1] - 1, cx))
            pick = int(lab[cy, cx])
            if pick == 0:
                sizes = ndimage.sum(near, lab, range(1, n + 1))
                pick = int(np.argmax(sizes)) + 1
            near = lab == pick
    except Exception:
        pass
    ys, xs = np.where(near)
    if not len(ys):
        return None
    return (int(xs.min()) + wx0, int(ys.min()) + wy0, int(xs.max()) + 1 + wx0, int(ys.max()) + 1 + wy0)


def patch_tone(im, mask=None, box=None, subject=None):
    """A part's darkest, middle and brightest pixel, and how busy it is.

    Four numbers, and each answers a fault one median cannot. A mop of separate strands is dark
    between them; a mop with a lit under-shell has the same median and no gaps at all. Edge
    energy is the same idea for detail: a seam that should not be there, or a pocket that should.
    """
    import numpy as np
    from PIL import Image
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    if box:
        a = a[int(box[1]):int(box[3]), int(box[0]):int(box[2])]
    if a.shape[0] < 3 or a.shape[1] < 3:
        return None
    g = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    keep = np.ones(g.shape, bool)
    if mask is not None:
        m = np.asarray(mask, bool)
        if m.shape != g.shape:
            m = np.asarray(Image.fromarray((m * 255).astype("uint8"))
                           .resize((g.shape[1], g.shape[0]), Image.NEAREST)) > 127
        if m.any():
            keep = m
    if subject is not None and box:
        s = np.asarray(subject, bool)
        if s.shape[0] >= int(box[3]) and s.shape[1] >= int(box[2]):
            keep = keep & s[int(box[1]):int(box[3]), int(box[0]):int(box[2])]
    if int(keep.sum()) < 24:
        return None
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    v = g[keep]
    return {"black": round(float((v < 40).mean()), 3),
            "dark": round(float(np.percentile(v, 4)), 1),
            "mid": round(float(np.percentile(v, 50)), 1),
            "light": round(float(np.percentile(v, 96)), 1),
            "busy": round(float(np.hypot(gx, gy)[keep].mean()), 1)}


def features(im):
    """The dark marks printed on a pale plate: two eyes and a mouth, as boxes in 0..1 of the crop.

    Sorted top to bottom, so the caller can call the top pair eyes and the bottom one a mouth.
    Returns [] when the picture is not a plate with marks on it, which is the honest answer for
    a face that is geometry rather than a decal.
    """
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    if a.shape[0] < 8 or a.shape[1] < 8:
        return []
    g = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    plate = float(np.percentile(g, 70))
    if plate < 90:
        return []                      # no pale plate: nothing is "printed on" anything
    m = g < plate * MARK_DARK
    if not m.any() or float(m.mean()) > 0.35:
        return []                      # a mass of dark strands is not a plate with marks on it
    rest = g[~m]
    if rest.size < 32 or float(rest.std()) > 26.0:
        return []                      # ...and neither is anything whose plate is not uniform
    try:
        from scipy import ndimage
        m = ndimage.binary_opening(m, np.ones((2, 2), bool))
        lab, n = ndimage.label(m)
    except Exception:
        return []
    h, w = g.shape
    out = []
    for i in range(1, n + 1):
        ys, xs = np.where(lab == i)
        if len(ys) < max(6, MARK_MIN * h * w):
            continue
        out.append({"box": [round(float(xs.min()) / w, 3), round(float(ys.min()) / h, 3),
                            round(float(xs.max() + 1) / w, 3), round(float(ys.max() + 1) / h, 3)],
                    "area": round(float(len(ys)) / (h * w), 4)})
    out.sort(key=lambda d: d["box"][1])
    # Two eyes and a mouth. More than four and it is a texture, not a set of features.
    return out if 2 <= len(out) <= 4 else []


def measure(row, part_im, panel, sil_bbox, ref_img, ref_bbox, ref_subject, mask=None):
    """Fill in the part's colour and its size, and the reference's, as shares of figure height.

    THE REFERENCE'S COLOUR IS READ, NOT HUNTED. This used to look for the RENDER's colour in the
    reference, which can only ever confirm a colour that is already right: a mud-tan face and a
    mid-grey jacket exist nowhere in the reference, so nothing was found and nothing was said,
    while four blind graders named both. The window the search already chose is where the part
    is, so the reference's colour is read there and then used as the key for the extent.
    """
    out = {}
    col = row.get("colour_rgb")
    if col:
        out["colour"] = _hex(col)
    bx = row.get("full_box")
    if bx and sil_bbox and panel:
        W, H = panel
        fh = max(1.0, float(sil_bbox[3] - sil_bbox[1]))
        out["size"] = [round((bx[2] - bx[0]) * W / fh, 3), round((bx[3] - bx[1]) * H / fh, 3)]
    rb = row.get("ref_box")
    if rb and ref_img is not None and ref_bbox:
        ref_col = ref_patch_colour(ref_img, rb, mask, ref_subject)
        if ref_col:
            out["ref_colour"] = _hex(ref_col)
            got = colour_region(ref_img, ref_col, rb, ref_subject)
            if got:
                rh = max(1.0, float(ref_bbox[3] - ref_bbox[1]))
                gw, gh = got[2] - got[0], got[3] - got[1]
                box_w, box_h = max(1, rb[2] - rb[0]), max(1, rb[3] - rb[1])
                # Both ways: a region far bigger than the part is two parts of one colour, and one
                # far smaller is a sliver that happened to match. The boy's legs found 0.02 of the
                # figure where the part is 0.31 of it, and the answer read "+1729%".
                if (box_w * SIZE_FLOOR <= gw <= box_w * SIZE_SLACK
                        and box_h * SIZE_FLOOR <= gh <= box_h * SIZE_SLACK
                        and gh <= rh * REF_PART_MAX and gw <= rh * REF_PART_MAX):
                    out["ref_size"] = [round(gw / rh, 3), round(gh / rh, 3)]
        # HOW DARK IT GETS AND HOW BUSY IT IS, both sides through the same mask.
        if part_im is not None:
            tone = patch_tone(part_im, mask)
            rtone = patch_tone(ref_img, mask, rb, ref_subject)
            if tone:
                out["tone"] = tone
            if rtone:
                out["ref_tone"] = rtone
        # WHERE IT SITS. `reg_box` is where the part would be if the model matched the reference;
        # `ref_box` is where the search actually found it. The difference is the error, and it is
        # already computed - it was simply never reported.
        reg = row.get("reg_box")
        if reg and len(reg) == 4:
            rh2 = max(1.0, float(ref_bbox[3] - ref_bbox[1]))
            dx = ((rb[0] + rb[2]) - (reg[0] + reg[2])) / 2.0
            dy = ((rb[1] + rb[3]) - (reg[1] + reg[3])) / 2.0
            out["offset"] = [round(dx / rh2, 4), round(dy / rh2, 4), round(dx, 1), round(dy, 1)]
    return out


def _fit_up(im, w: int, h: int):
    """Fit inside w x h, upscaling too: a reference face is often 100px, and a cell is 280."""
    from PIL import Image
    s = min(w / max(1, im.width), h / max(1, im.height), 4.0)
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)


def compose(rows: list, title: str, sub: str, budget: int = 1_150_000):
    """One row per part: the reference crop, then the close-ups. None when there is nothing."""
    from PIL import Image, ImageDraw
    from . import review as _review
    lines = []
    for r in rows:
        cells = []
        if r.get("ref_crop") is not None:
            cells.append(("REFERENCE · %s" % r["target"], r["ref_crop"], True))
        n = r.get("numbers") or {}
        for i, (lab, im) in enumerate(r.get("shots") or []):
            cap = "%s · %s" % (r["target"], lab)
            if n and lab == "alone":
                cap += "  %.2f" % n["match"]
            elif n and i == 0:
                cap += "  %.2f" % n.get("seen", n["match"])
            cells.append((cap, im, False))
        if cells:
            lines.append(cells)
    if not lines:
        return None
    cols, nrows = max(len(c) for c in lines), len(lines)
    pad, lab, head = 10, 22, 56
    cell = max(140, int(min(300, math.sqrt(budget / float(cols * nrows)) * 0.82)))
    W = pad + cols * (cell + pad)
    H = head + nrows * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), (14, 16, 22))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 12), title, font=_review._font(19), fill=(232, 236, 244))
    d.text((pad, 36), sub, font=_review._font(13), fill=(126, 138, 158))
    f_l = _review._font(14)
    for ri, cells in enumerate(lines):
        for ci, (cap, im, is_ref) in enumerate(cells):
            x = pad + ci * (cell + pad)
            y = head + ri * (cell + lab + pad)
            th = _fit_up(im, cell, cell)
            d.rectangle([x - 1, y - 1, x + cell, y + cell],
                        outline=(120, 132, 168) if is_ref else (38, 43, 54))
            sheet.paste(th, (x + (cell - th.width) // 2, y + (cell - th.height) // 2))
            d.text((x + 2, y + cell + 4), cap[:44], font=f_l,
                   fill=(214, 222, 240) if is_ref else (150, 162, 182))
    return sheet


async def run(live, targets: list, view: str, margin: float, ref_path: str, mode: str,
              angles: Optional[list] = None, colours: bool = True) -> dict:
    """Photograph each target close up and, with a reference, hold it against the same crop.

    `mode` "all" pictures every target from every angle; "auto" pictures only the targets whose
    numbers say something is wrong. The camera is put back on the whole subject at `view`.
    """
    from PIL import Image
    from . import live as L
    rows: list = []
    ref_img = ref_bbox = sil_bbox = sil_size = None
    full: dict = {}
    if ref_path:
        try:
            ref_img = Image.open(ref_path).convert("RGB")
            ref_bbox = bbox_of_mask(L._mask_of_photo(ref_img))
        except Exception:
            ref_img = ref_bbox = None
    ref_subject = None
    if ref_img is not None and ref_bbox:
        try:
            ref_subject = L._mask_of_photo(ref_img)
        except Exception:
            ref_subject = None
        # THE REGISTRATION FRAME: the whole subject at the reference's angle, as a silhouette —
        # the kind of picture the score is measured on — with every target's box read in the
        # same camera.
        sil = None
        if await live.ask("__forge.setPass('silhouette')"):
            png = await live.raw("__forge.view(%s,%s)" % (json.dumps(view), float(margin)))
            for t in targets:
                full[t] = await live.ask("__forge.boxOf(%s)" % json.dumps(t), depth=4)
            sil = L._decode_shot(png)
        await live.raw("__forge.clearPass()")
        if sil is not None:
            sil_bbox, sil_size = bbox_of_mask(L._mask_of_render(sil)), sil.size
        if not sil_bbox:
            # No override pass on this page: the lit frame, read against its own backdrop.
            png = await live.raw("__forge.view(%s,%s)" % (json.dumps(view), float(margin)))
            lit = L._decode_shot(png)
            if lit is not None:
                for t in targets:
                    full[t] = await live.ask("__forge.boxOf(%s)" % json.dumps(t), depth=4)
                sil_bbox, sil_size = bbox_of_mask(L._mask_of_photo(lit)), lit.size

    # One picture per box: `head` holding the hair framed the hair's box, and was shot twice.
    dup: dict = {}
    if full:
        targets, dup = dedupe(targets, full)
    for t in targets:
        # `t` is what the page is asked ("=handL" is that exact name); the row carries the name.
        png = await live.raw("__forge.view(%s,%s,0,%s)" % (json.dumps(view), float(margin), json.dumps(t)))
        close = L._decode_shot(png)
        cbox = await live.ask("__forge.boxOf(%s)" % json.dumps(t), depth=4)
        # The same camera with the part alone. What decides "upside down" must be the part, not
        # the hair hanging in front of it.
        alone = None
        try:
            solo = await live.ask("__forge.solo(%s)" % json.dumps(t), depth=3)
            if isinstance(solo, dict) and solo.get("hidden"):
                alone = L._decode_shot(await live.raw("__forge.shot()"))
        finally:
            await live.ask("__forge.solo(null)")
        row = {"target": label_of(t), "q": t, "shots": [], "numbers": {}}
        box = cbox.get("box") if isinstance(cbox, dict) else None
        if not box:
            row["missing"] = True
        # The pictures are cropped round the part, so it fills its cell instead of a third of it.
        if close is not None:
            row["shots"].append((view, (crop_norm(close, box, SHOT_PAD) if box else None) or close))
        if alone is not None:
            row["shots"].append(("alone", (crop_norm(alone, box, SHOT_PAD) if box else None) or alone))
        fb = full.get(t)
        if (close is not None and ref_img is not None and sil_bbox and ref_bbox
                and isinstance(fb, dict) and fb.get("box") and box):
            rbox = pad_px(register_box(fb["box"], sil_size, sil_bbox, ref_bbox), PAD, ref_img.size)
            cb = crop_box(close, box, PAD)
            if rbox and cb:
                seen = close.crop(cb)
                # Only the part's own pixels count, read off the frame where it stands alone, and
                # the reference is searched round the registered box for each orientation.
                mask = part_mask(alone, cb) if alone is not None else None
                row["numbers"], best = search(alone.crop(cb) if alone is not None else seen,
                                              ref_img, rbox, mask)
                row["ref_crop"] = ref_img.crop(best)
                row["ref_box"] = list(best)
                if alone is not None:
                    row["numbers"]["seen"] = round(_score(_prep(seen, mask), _prep(row["ref_crop"])), 4)
                # The part's own colour, and its size beside the reference's. This is the swatch
                # strip and the ratio ladder an agent wrote by hand, done from frames already taken.
                row["reg_box"] = list(rbox)
                # WHAT IS PRINTED ON IT. Read from the reference first, so a plain garment costs
                # nothing: no marks there, no comparison here.
                rmk = features(row["ref_crop"])
                if len(rmk) >= 2:
                    row["ref_marks"] = rmk
                    row["marks"] = features(seen)
                if colours:
                    part_im = alone.crop(cb) if alone is not None else seen
                    row["colour_rgb"] = part_colour(part_im, mask)
                    row["full_box"] = list(fb["box"])
                    row.update(measure(row, part_im, sil_size, sil_bbox, ref_img, ref_bbox,
                                       ref_subject, mask))
        row["verdict"] = verdict(row["numbers"])
        rows.append(row)
    rows += [{"target": label_of(d), "same_as": label_of(twin), "shots": [], "numbers": {}, "verdict": ""}
             for d, twin in dup.items()]

    want = angles_for(view, angles)
    pictured = [r for r in rows if r.get("shots") and not r.get("missing")
                and (mode == "all" or r["verdict"])]
    for r in pictured:
        q = r.get("q") or r["target"]
        for spec, lab in want[1:]:
            png = await live.raw("__forge.view(%s,%s,0,%s)"
                                 % (json.dumps(spec), float(margin), json.dumps(q)))
            im = L._decode_shot(png)
            if im is None:
                continue
            bx = await live.ask("__forge.boxOf(%s)" % json.dumps(q), depth=4)
            if isinstance(bx, dict) and bx.get("box"):
                im = crop_norm(im, bx["box"], SHOT_PAD) or im
            r["shots"].append((lab, im))
        if r["verdict"] in ("upside_down", "mirrored"):
            r["textures"] = await live.ask("__forge.texFacts(%s)" % json.dumps(q), depth=6) or []
    # Back on the whole subject, so the bench and the window following it show what the sheet did.
    await live.raw("__forge.view(%s,%s)" % (json.dumps(view), float(margin)))
    return {"rows": rows, "view": view, "pictured": [r["target"] for r in pictured],
            "registered": bool(sil_bbox and ref_bbox)}


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(targets: list, boxes: dict) -> tuple:
    """(kept, {dropped: its twin}). Names whose boxes overlap DUP_IOU or more are one picture; a
    container ("head" holding the hair) gives way to what it holds, otherwise the later name goes."""
    def box(n):
        b = boxes.get(n)
        return b.get("box") if isinstance(b, dict) else None
    kept: list = []
    dropped: dict = {}
    for t in targets:
        b = box(t)
        twin = next((k for k in kept if b and box(k) and _iou(b, box(k)) >= DUP_IOU), None)
        if twin is None:
            kept.append(t)
        elif twin in _CONTAINERS and t not in _CONTAINERS:
            kept[kept.index(twin)] = t
            dropped[twin] = t
        else:
            dropped[t] = twin
    return kept, dropped


def summary(rows: list) -> list:
    """The JSON half of the rows: numbers and verdicts, no pictures."""
    out = []
    for r in rows or []:
        n = r.get("numbers") or {}
        row = {"target": r.get("target"), "verdict": r.get("verdict") or ""}
        why_not = flip_doubtful(r)
        if why_not:
            # Not "upside_down": an agent reading the JSON acts on this word as well.
            row["verdict"] = "doubtful_flip"
            row["why"] = why_not
        row.update({k: n[k] for k in ("match", "upside_down", "mirrored", "seen") if k in n})
        if float(n.get("sym_v") or 0) >= SYMMETRY or float(n.get("sym_h") or 0) >= SYMMETRY:
            row["symmetric"] = True        # why no orientation verdict was given
        if "structure" in n:
            row["structure"] = n["structure"]          # how much there is in the part at all
        if float(n.get("structure", 99.0)) < STRUCTURE_MIN:
            row["featureless"] = True      # and why no orientation verdict could be given
        for k in ("colour", "ref_colour", "size", "ref_size", "tone", "ref_tone", "offset",
                  "marks", "ref_marks"):
            if r.get(k):
                row[k] = r[k]
        if r.get("missing"):
            row["missing"] = True
        if r.get("same_as"):
            row["same_as"] = r["same_as"]
        if r.get("textures"):
            row["textures"] = r["textures"]
        out.append(row)
    return out

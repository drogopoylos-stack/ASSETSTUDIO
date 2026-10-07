# -*- coding: utf-8 -*-
"""The detail review, and the three faults found on the same day beside it.

  * a roblox-boy's face came out UPSIDE DOWN and the forge said nothing — the detail review has to
    catch that as a number, name the texture, and show the reference's face beside the render's;
  * the Engine window said "building · probe2 · 3q" for 27 minutes — a call that opens an activity
    row has to close it on every path;
  * /api/live/look answered HTTP 500 with a reference set and views=["left"] — `_score_against` has
    to survive an empty silhouette.

Everything runs without a browser. The browser half was proved on the real character by hand.

Run:  backend/.venv/Scripts/python.exe backend/detail_test.py
"""
import asyncio
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw, ImageOps  # noqa: E402

from asset_studio import live as L                     # noqa: E402
from asset_studio import live_detail as D              # noqa: E402
from asset_studio import live_forge as LF              # noqa: E402

passed = 0
fails = []


def ok(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
        return
    fails.append(name + ("   <- " + str(extra) if extra else ""))
    print("  FAIL  %s   %s" % (name, extra))


# ---------------------------------------------------------------- angles
print("Which way the close-ups look")
ok("front is az 0", D.az_el("front") == (0.0, 0.0), D.az_el("front"))
ok("side is az 90", D.az_el("side")[0] == 90.0)
ok("back is az 180", D.az_el("back")[0] == 180.0)
ok("left is az 270", D.az_el("left")[0] == 270.0)
ok("a spec keeps its own numbers", D.az_el("az=35,el=12,zoom=2") == (35.0, 12.0))
a = D.angles_for("front")
ok("four angles, the reference's own first", [x[0] for x in a] == ["front", "az=40,el=0", "az=90,el=0", "az=180,el=0"], a)
ok("...labelled for a person", [x[1] for x in a] == ["front", "+40°", "side", "back"])
ok("angles turn from the reference's angle, not from 0", D.angles_for("az=100,el=10")[1][0] == "az=140,el=10")
ok("asked-for angles replace the defaults", [x[0] for x in D.angles_for("front", ["top", "front", "low"])] == ["front", "top", "low"])

# ---------------------------------------------------------------- targets
print("\nWhich parts are looked at")
ok("named parts always", D.pick_targets(["hand", "hair"], [], [], False) == ["hand", "hair"])
ok("a character with a reference: face, head, hair, torso",
   D.pick_targets([], ["head", "face", "hair", "torso", "jacket"], ["character"], True) == ["face", "head", "hair", "torso"])
ok("one name per group, the first one present", D.pick_targets([], ["jacket", "body"], ["character"], True) == ["jacket"])
ok("no reference: nothing automatic", D.pick_targets([], ["head", "face"], ["character"], False) == [])
ok("a crate is not a character", D.pick_targets([], ["lid", "body"], ["prop"], True) == [])
ok("a face makes it a character even untagged", D.pick_targets([], ["face", "hair"], [], True) == ["face", "hair"])
ok("junk in the list is ignored, not a crash", D.pick_targets([], [None, 3, "head"], ["npc"], True) == ["head"])

# ---------------------------------------------------------------- registration
print("\nA part's box carried into the reference")
rb = D.register_box((0.4, 50 / 300, 0.6, 100 / 300), (400, 300), (150, 50, 250, 250), (50, 0, 150, 400))
ok("height-normalised and centre-aligned, like the score", tuple(round(v, 3) for v in rb) == (20.0, 0.0, 180.0, 100.0), rb)
ok("padding grows the box", D.pad_px((20, 20, 60, 60), 0.25, (200, 200)) == (10, 10, 70, 70))
ok("...and is clamped to the picture", D.pad_px((0, 0, 40, 40), 0.5, (30, 30)) == (0, 0, 30, 30))
ok("a box too small to judge is refused", D.pad_px((10, 10, 11, 11), 0.1, (100, 100)) is None)
blob = Image.new("L", (40, 30), 0)
ImageDraw.Draw(blob).rectangle([5, 4, 14, 20], fill=255)
import numpy as np  # noqa: E402
ok("a mask's box", D.bbox_of_mask(np.asarray(blob) > 0) == (5, 4, 15, 21))
ok("an empty mask has none", D.bbox_of_mask(np.zeros((5, 5), bool)) is None)


# ---------------------------------------------------------------- the check itself
print("\nAs built, upside down, mirrored")


def face(flip=False, shift=0, mark=False, size=200):
    im = Image.new("RGB", (size, size), (253, 217, 179))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([60 + shift, 48, 80 + shift, 92], 6, fill=(0, 0, 0))
    d.rounded_rectangle([120 + shift, 48, 140 + shift, 92], 6, fill=(0, 0, 0))
    d.rounded_rectangle([50 + shift, 118, 150 + shift, 176], 20, fill=(0, 0, 0))
    d.rectangle([60 + shift, 118, 140 + shift, 131], fill=(255, 255, 255))
    d.rounded_rectangle([62 + shift, 150, 138 + shift, 176], 14, fill=(242, 24, 51))
    if mark:
        d.rectangle([0, 0, 66, size], fill=(20, 90, 240))
    return ImageOps.flip(im) if flip else im


ref = face()
n_up = D.compare(face(flip=True), ref)
ok("an upside-down face is called upside down", D.verdict(n_up) == "upside_down", n_up)
ok("...by a clear margin", n_up["upside_down"] - n_up["match"] > 0.15, n_up)
n_ok = D.compare(face(shift=5).resize((190, 205)), ref)
ok("an upright face, slightly off, is fine", D.verdict(n_ok) == "", n_ok)
ok("...and scores well", n_ok["match"] > 0.7, n_ok)
n_mir = D.compare(ImageOps.mirror(face(mark=True)), face(mark=True))
ok("a mirrored asymmetric part is called mirrored", D.verdict(n_mir) == "mirrored", n_mir)
n_far = D.compare(Image.new("RGB", (200, 200), (128, 128, 128)), ref)
ok("a part that is nothing like the reference is far", D.verdict(n_far) == "far", n_far)
ok("no numbers, no verdict", D.verdict({}) == "")
# The roblox-boy: its hair strands crossed its face, so the face as seen said nothing either way.
ok("better alone than as seen: something stands in front of it",
   D.verdict({"match": 0.62, "upside_down": 0.40, "mirrored": 0.55, "seen": 0.29}) == "covered")
ok("...but a part that is off on its own is far, not covered",
   D.verdict({"match": 0.30, "upside_down": 0.25, "mirrored": 0.28, "seen": 0.10}) == "far")
ok("...and upside down outranks covered",
   D.verdict({"match": 0.40, "upside_down": 0.62, "mirrored": 0.35, "seen": 0.20}) == "upside_down")
# The boy's trouser legs: two near-flat dark tubes that matched their own flip 0.84, and the answer
# accused a texture that was never the problem. Nothing in the part, nothing to be upside down.
ok("a part with nothing in it is never called upside down",
   D.verdict({"match": 0.52, "upside_down": 0.84, "mirrored": 0.38, "structure": 6.0}) == "")
ok("...and if it is also nothing like the reference, it is far and not flipped",
   D.verdict({"match": 0.36, "upside_down": 0.84, "mirrored": 0.38, "structure": 6.0}) == "far")
ok("...nor mirrored", D.verdict({"match": 0.40, "upside_down": 0.41, "mirrored": 0.62, "structure": 5.0}) == "")
ok("a part that IS its own mirror is not called mirrored either",
   D.verdict({"match": 0.50, "upside_down": 0.40, "mirrored": 0.70, "structure": 40.0,
              "sym_h": 0.9}) == "")
ok("...but a face with real features still is",
   D.verdict({"match": 0.40, "upside_down": 0.63, "mirrored": 0.41, "structure": 38.0,
              "sym_v": 0.4}) == "upside_down")

# THE PART ALONE, ON THE STUDIO BACKDROP. The roblox-boy's face alone was a third backdrop, the
# same in every orientation: 0.28 as built, 0.29 turned over. Only the part's pixels may count.
print("\nOnly the part's own pixels count")


def alone(flip=False, pad=20):
    im = Image.new("RGB", (200 + 2 * pad, 200 + 2 * pad), (26, 30, 38))     # the forge's backdrop
    im.paste(face(flip=flip), (pad, pad))
    m = np.zeros((im.height, im.width), bool)
    m[pad:pad + 200, pad:pad + 200] = True
    return im, m


_ref_ctx = Image.new("RGB", (240, 240), (253, 217, 179))   # the reference: skin round the face
_ref_ctx.paste(face(), (20, 20))
_im, _m = alone(flip=True)
_bare, _masked = D.compare(_im, _ref_ctx), D.compare(_im, _ref_ctx, _m)
ok("a part alone and upside down: with its mask the flip is plain",
   D.verdict(_masked) == "upside_down"
   and _masked["upside_down"] - _masked["match"] > _bare["upside_down"] - _bare["match"], (_bare, _masked))
_im, _m = alone()
ok("...and the right way up it is not called upside down", D.verdict(D.compare(_im, _ref_ctx, _m)) == "",
   D.compare(_im, _ref_ctx, _m))
# Two near-uniform patches: the structure term must not carry a score it has no evidence for.
_dim1 = Image.new("RGB", (120, 200), (30, 31, 34))
_dim2 = Image.new("RGB", (120, 200), (96, 99, 104))
_np2 = np.asarray(_dim1).copy()
_np2[::7, ::5] = (34, 35, 38)                       # a whisper of noise, not structure
_noisy = Image.fromarray(_np2)
ok("two near-uniform patches of different colours do not score as a match",
   D.compare(_noisy, _dim2)["match"] < 0.55, D.compare(_noisy, _dim2))
ok("...and a real pattern against itself still does",
   D.compare(face(), face())["match"] > 0.95, D.compare(face(), face()))
ok("a mask with nothing in it falls back to the whole crop",
   isinstance(D.compare(_im, _ref_ctx, np.zeros((240, 240), bool))["match"], float))
_frame = Image.new("RGB", (300, 300), (26, 30, 38))
_frame.paste(face(), (50, 50))
_pm = D.part_mask(_frame, (40, 40, 260, 260))
ok("the part's mask is read off the frame, backdrop from its border",
   _pm.shape == (220, 220) and _pm[110, 110] and not _pm[2, 2], (_pm.shape, _pm[110, 110], _pm[2, 2]))

# THE REFERENCE, SEARCHED ROUND THE REGISTERED BOX. The boy's eyes sat 15% higher on its head.
print("\nThe reference is searched round the registered box")
_big = Image.new("RGB", (600, 600), (40, 40, 40))
_big.paste(face(), (200, 230))                    # the face is 30px lower than registration says
_rbox = (200, 200, 400, 400)
_n, _best = D.search(face(), _big, _rbox)
ok("the search finds the face the registration missed",
   _n["match"] > D.compare(face(), _big.crop(_rbox))["match"] + 0.05, (_n, D.compare(face(), _big.crop(_rbox))))
ok("...and says where it found it: its centre, since a window can also be smaller",
   abs((_best[0] + _best[2]) / 2 - 300) <= 15 and abs((_best[1] + _best[3]) / 2 - 330) <= 15, _best)
_n2, _ = D.search(face(flip=True), _big, _rbox)
ok("an upside-down face is still upside down after the search", D.verdict(_n2) == "upside_down", _n2)
ok("a box at the picture's edge still gets an answer",
   isinstance(D.search(face(), _big, (0, 0, 200, 200))[0]["match"], float))

# ---------------------------------------------------------------- what the agent reads
print("\nWhat the agent is told")
rows = [{"target": "face", "numbers": n_up, "verdict": "upside_down",
         "textures": [{"material": "face", "slot": "diffuseMap", "flipY": False, "source": "canvas"}]},
        {"target": "hair", "numbers": {"match": 0.31, "upside_down": 0.2, "mirrored": 0.3}, "verdict": "far"},
        {"target": "torso", "numbers": n_ok, "verdict": ""}]
lines = D.findings(rows, "playcanvas", "front")
ok("the upside-down part is named", lines and lines[0].startswith("UPSIDE DOWN: `face`"), lines[:1])
ok("...with the texture that did it and the fix", "face.diffuseMap" in lines[0] and "flipY:true" in lines[0], lines[0])
ok("...in PlayCanvas's own terms", "PlayCanvas samples the canvas's TOP row" in lines[0])
ok("three gets three's advice", "three's own default" in D.findings(rows, "three", "front")[0])
ok("no texture to blame: say where to look", "rotation" in D.findings([dict(rows[0], textures=[])], "playcanvas", "front")[0])
ok("a far part says so", any(x.startswith("DETAIL: `hair`") for x in lines))
ok("every part's number, on one line, last", lines[-1].startswith("DETAIL at front") and "torso" in lines[-1], lines[-1])
sheet = D.compose([dict(rows[0], ref_crop=ref, shots=[("front", face(flip=True)), ("+40°", face()), ("side", face()), ("back", face())])],
                  "boy · detail", "sub")
ok("the detail sheet draws", sheet is not None and sheet.width > 600, sheet and sheet.size)
ok("...inside the picture budget", sheet.width * sheet.height <= 1_400_000, sheet.size)
ok("nothing to draw is None, not a blank picture", D.compose([{"target": "x"}], "t", "s") is None)
_cov = D.findings([{"target": "face", "verdict": "covered",
                    "numbers": {"match": 0.62, "upside_down": 0.4, "mirrored": 0.55, "seen": 0.29}}],
                  "playcanvas", "front")
ok("a covered part says so, with both numbers",
   _cov[0].startswith("COVERED: `face`") and "0.62" in _cov[0] and "0.29" in _cov[0], _cov[:1])
ok("...and the last line gives the number as seen too", "0.29 as seen" in _cov[-1], _cov[-1])
_sm = D.summary([{"target": "face", "verdict": "covered",
                  "numbers": {"match": 0.62, "upside_down": 0.4, "mirrored": 0.55, "seen": 0.29}},
                 {"target": "head", "same_as": "hair"}])
ok("the JSON half carries the number as seen, and the twin",
   _sm[0].get("seen") == 0.29 and _sm[1].get("same_as") == "hair", _sm)

# ---------------------------------------------------------------- two names, one box
# The roblox-boy's head holds its hair, and the hair is bigger than the head: `head` and `hair`
# framed the same box and came back with the same three numbers.
print("\nTwo names that frame one box are one picture")
_kept, _dup = D.dedupe(["face", "head", "hair", "torso"],
                       {"face": {"box": [0.45, 0.2, 0.55, 0.3]}, "head": {"box": [0.35, 0.05, 0.65, 0.4]},
                        "hair": {"box": [0.35, 0.05, 0.66, 0.41]}, "torso": {"box": [0.3, 0.4, 0.7, 0.7]}})
ok("a head that holds the hair frames the hair's box: the hair is kept", _kept == ["face", "hair", "torso"], _kept)
ok("...and the head is named as its twin", _dup == {"head": "hair"}, _dup)
_kept, _dup = D.dedupe(["torso", "shirt"], {"torso": {"box": [0.3, 0.4, 0.7, 0.7]},
                                            "shirt": {"box": [0.3, 0.4, 0.7, 0.71]}})
ok("two plain names on one box: the later one goes", _kept == ["torso"] and _dup == {"shirt": "torso"}, (_kept, _dup))
ok("no boxes: nothing is dropped", D.dedupe(["face", "hair"], {})[0] == ["face", "hair"])
summ = D.summary(rows)
ok("the JSON half carries numbers and verdicts", summ[0]["verdict"] == "upside_down" and "match" in summ[0])

# ---------------------------------------------------------------- the switch
print("\nThe switch, and the ways a call can ask")
ok("left out: automatic", L._detail_mode(None) == ([], "auto"))
ok("a list names the parts and pictures them", L._detail_mode(["face", "hair"]) == (["face", "hair"], "all"))
ok("true pictures the automatic parts", L._detail_mode(True) == ([], "all"))
ok("false is off", L._detail_mode(False) == ([], ""))
ok("\"off\" is off", L._detail_mode("off") == ([], ""))
ok("a comma list works too", L._detail_mode("face, hair") == (["face", "hair"], "all"))
_orig = L._engine_pref
L._engine_pref = lambda k, d=None: False if k == "forge_detail" else _orig(k, d)
try:
    ok("the switch off: nothing automatic", L._detail_mode(None) == ([], ""))
    ok("...but a part asked for by name still is", L._detail_mode(["face"]) == (["face"], "all"))
finally:
    L._engine_pref = _orig

# ---------------------------------------------------------------- colour and size
#
# The user could see every one of these faults and the bench reported none of them: the hair too
# red, the blue shirt too short, a seam down the front of the pants. A colour and a size per part
# is what turns each of those into a number an agent can act on.
print("\nWhat colour it is, and how big")
_ref_body = Image.new("RGB", (200, 400), (24, 24, 26))
ImageDraw.Draw(_ref_body).rectangle([85, 90, 115, 250], fill=(1, 84, 203))     # a long blue shirt
_mine = Image.new("RGB", (60, 80), (24, 24, 26))
ImageDraw.Draw(_mine).rectangle([20, 10, 40, 60], fill=(22, 90, 202))          # a short one
_m = np.zeros((80, 60), bool)
_m[10:61, 20:41] = True
_col = D.part_colour(_mine, _m)
ok("a part's colour is the median of its own pixels, not of the frame", D._hex(_col) == "#165aca",
   D._hex(_col))
_reg = D.colour_region(_ref_body, _col, (80, 85, 120, 255))
ok("...and the same colour is found in the reference", _reg is not None and _reg[1] <= 92 and _reg[3] >= 248, _reg)
ok("a colour nothing in the reference wears is not found",
   D.colour_region(_ref_body, [250, 10, 250], (80, 85, 120, 255)) is None)
_row = {"target": "shirt", "colour_rgb": _col, "full_box": [0.4, 0.3, 0.6, 0.5],
        "ref_box": (80, 85, 120, 255)}
_got = D.measure(_row, None, (200, 400), (20, 20, 180, 380), _ref_body, (20, 20, 180, 380), None)
ok("the part's size comes back as a share of the figure's height", "size" in _got, _got)
ok("...and the reference's, from its own colour region", "ref_size" in _got and _got["ref_size"][1] > 0.3, _got)
# THE KEY COMES FROM THE REFERENCE, NOT FROM THE RENDER. Hunting the render's colour can only
# ever confirm a colour that is already right: on the real boy the render's jacket was mid-grey
# and its tee navy, neither exists in the reference, so neither was reported and four blind
# graders named both.
_wrong = {"target": "shirt", "colour_rgb": [90, 90, 95], "full_box": [0.4, 0.3, 0.6, 0.5],
          "ref_box": (80, 85, 120, 255)}
_gotw = D.measure(_wrong, None, (200, 400), (20, 20, 180, 380), _ref_body, (20, 20, 180, 380), None)
ok("a part rendered in a colour the reference never wears still gets the reference's colour",
   _gotw.get("ref_colour") == "#0154cb", _gotw)
ok("...and its size, so a wrong colour no longer silences the size too",
   "ref_size" in _gotw, _gotw)
ok("the reference's colour is read through the part's own mask",
   callable(getattr(D, "ref_patch_colour", None)))
_face_mask = np.zeros((160, 40), bool)
_face_mask[0:80, :] = True
ok("...so half a box reads only that half",
   D._hex(D.ref_patch_colour(_ref_body, (85, 90, 115, 250), _face_mask)) == "#0154cb",
   D._hex(D.ref_patch_colour(_ref_body, (85, 90, 115, 250), _face_mask)))
# One swatch cannot describe a part that is a picture. The boy's face is an alpha decal whose
# opaque pixels are two eyes and a mouth, so its median is the mouth's red.
_picture = D.findings([{"target": "face", "verdict": "", "numbers": {"match": 0.7, "structure": 40.0},
                        "colour": "#d52d3a", "ref_colour": "#202021"}], "three", "front")
ok("a part that is a picture, not a swatch, gets no colour line",
   not any(x.startswith("COLOUR: `face`") for x in _picture), _picture)
_swatch = D.findings([{"target": "shirt", "verdict": "", "numbers": {"match": 0.7, "structure": 8.0},
                       "colour": "#2956b0", "ref_colour": "#0352bc"}], "three", "front")
ok("...and a flat one still does", any(x.startswith("COLOUR: `shirt`") for x in _swatch), _swatch)
# Two parts of one near-black join into one region: the trousers found the trousers AND the
# hoodie, 0.81 of the figure tall, and the answer read "-59%".
_tall = Image.new("RGB", (200, 400), (24, 24, 26))
ImageDraw.Draw(_tall).rectangle([70, 40, 130, 350], fill=(26, 30, 31))
_dark = {"target": "pants", "colour_rgb": [26, 30, 31], "full_box": [0.35, 0.55, 0.65, 0.9],
         "ref_box": (68, 200, 132, 350)}
_gotd = D.measure(_dark, None, (200, 400), (20, 20, 180, 380), _tall, (20, 20, 180, 380), None)
ok("a reference region most of the figure tall is two parts, not one",
   "ref_size" not in _gotd, _gotd)
_lines = D.findings([{"target": "shirt", "verdict": "", "numbers": {"match": 0.7},
                      "colour": "#165aca", "ref_colour": "#0154cb",
                      "size": [0.3, 0.2], "ref_size": [0.3, 0.42]}], "three", "front")
ok("a part that is the wrong size says so, with both numbers",
   any(x.startswith("SIZE: `shirt`") and "0.42" in x for x in _lines), _lines)
ok("...and a colour that is off is named as a colour",
   any(x.startswith("COLOUR: `shirt`") for x in _lines), _lines)
ok("two colours a few levels apart are not worth a line", D._hex_gap("#1a1a19", "#1c1f20") < D.COLOUR_GAP)
# A render that is simply darker is a lamp, and saying "your skin is wrong" about the exposure
# teaches an agent to repaint what was right. The real boy: #ead8b1 against #e1b797, both skin.
ok("the same colour under a dimmer lamp is not a colour fault",
   D.chroma_gap("#ead8b1", "#e1b797") < D.COLOUR_GAP, D.chroma_gap("#ead8b1", "#e1b797"))
ok("...but a redder hair at the same brightness is",
   D.chroma_gap("#984430", "#a55e34") >= D.COLOUR_GAP, D.chroma_gap("#984430", "#a55e34"))
ok("a black region has no colour to be wrong about", D.chroma_gap("#3f4246", "#000000") == 0)
ok("a jacket rendered grey where the reference is black is", D._hex_gap("#242424", "#1a1a19") >= 26)

# ---------------------------------------------------------------- tone, busy, offset, features
#
# Every one of these came from a fault the user could see and the bench could not say. The
# reference boy's hair is 32% near-black - the gaps between its strands - and the re-run's was
# 0.0%, its darkest pixel 65 against the reference's 9. Same renderer for both, so it is the mesh.
print("\nHow dark it gets, how busy it is, and where it sits")
_flat = Image.new("RGB", (80, 120), (150, 120, 90))
_gappy = Image.new("RGB", (80, 120), (150, 120, 90))
_dg = ImageDraw.Draw(_gappy)
for _x in range(4, 78, 10):
    _dg.rectangle([_x, 0, _x + 4, 119], fill=(6, 5, 4))
_t_flat = D.patch_tone(_flat)
_t_gap = D.patch_tone(_gappy)
ok("a part's tone is five numbers", sorted(_t_flat) == ["black", "busy", "dark", "light", "mid"],
   _t_flat)
ok("a mass with gaps between its parts is part near-black", _t_gap["black"] > 0.25, _t_gap)
ok("...and one without gaps is none of it", _t_flat["black"] == 0.0, _t_flat)
ok("...and the gappy one is the busier of the two", _t_gap["busy"] > _t_flat["busy"] * 3,
   (_t_gap["busy"], _t_flat["busy"]))
_ln = D.findings([{"target": "hair", "verdict": "", "numbers": {"match": 0.7, "structure": 20.0},
                   "colour": "#a64b2f", "ref_colour": "#9f4118",
                   "tone": {"black": 0.0, "dark": 65.0, "mid": 126.0, "light": 195.0,
                            "busy": 18.3},
                   "ref_tone": {"black": 0.319, "dark": 9.0, "mid": 58.0, "light": 167.0,
                                "busy": 24.4}}],
                 "three", "front")
ok("a part that never gets dark says so, as the two shares",
   any(x.startswith("TONE: `hair` never gets dark - 32% of the reference's") for x in _ln), _ln)
ok("...and names the reason a mass reads as one shape", any("read as separate" in x for x in _ln),
   _ln)
ok("...but a white sneaker beside a dark floor is not accused of it",
   not any(x.startswith("TONE:") for x in D.findings(
       [{"target": "shoe", "verdict": "", "numbers": {"match": 0.7},
         "tone": {"black": 0.0, "dark": 122.0, "mid": 200.0, "light": 240.0, "busy": 9.0},
         "ref_tone": {"black": 0.04, "dark": 26.0, "mid": 210.0, "light": 246.0,
                      "busy": 12.0}}], "three", "front")))
ok("never more than three lines about one part",
   len([x for x in D.findings(
       [{"target": "hair", "verdict": "", "numbers": {"match": 0.7, "structure": 8.0},
         "colour": "#a64b2f", "ref_colour": "#0352bc", "size": [0.2, 0.2],
         "ref_size": [0.4, 0.4], "offset": [0.09, -0.09, 40.0, -40.0],
         "tone": {"black": 0.0, "dark": 65.0, "mid": 126.0, "light": 195.0, "busy": 30.0},
         "ref_tone": {"black": 0.32, "dark": 9.0, "mid": 58.0, "light": 167.0, "busy": 5.0}}],
       "three", "front")
       if not x.startswith("DETAIL at") and not x.startswith("LEVELS:")]) == 3)
_ln2 = D.findings([{"target": "pants", "verdict": "", "numbers": {"match": 0.7, "structure": 6.0},
                    "tone": {"black": 0.3, "dark": 20.0, "mid": 40.0, "light": 70.0,
                             "busy": 22.0},
                    "ref_tone": {"black": 0.31, "dark": 18.0, "mid": 38.0, "light": 66.0,
                                 "busy": 8.0}}],
                  "three", "front")
ok("a seam the reference has not is named", any(x.startswith("BUSY: `pants`") for x in _ln2), _ln2)
_ln3 = D.findings([{"target": "jacket", "verdict": "", "numbers": {"match": 0.7, "structure": 6.0},
                    "tone": {"black": 0.3, "dark": 20.0, "mid": 40.0, "light": 70.0,
                             "busy": 5.0},
                    "ref_tone": {"black": 0.31, "dark": 18.0, "mid": 38.0, "light": 66.0,
                                 "busy": 19.0}}],
                  "three", "front")
ok("a pocket the reference has and the build has not is named",
   any(x.startswith("PLAIN: `jacket`") for x in _ln3), _ln3)
_ln4 = D.findings([{"target": "handR", "verdict": "", "numbers": {"match": 0.7},
                    "offset": [0.001, -0.031, 0.4, -16.0]}], "three", "front")
ok("a part two pixels out of place says which way",
   any(x.startswith("OFFSET: `handR`") and "too low" in x for x in _ln4), _ln4)
ok("...in the reference's own pixels too", any("16 px down" in x for x in _ln4), _ln4)
_mk = D.features(face())
ok("the marks printed on a face are found", len(_mk) == 3, _mk)
ok("...top to bottom, so the last one is the mouth", _mk[2]["box"][1] > _mk[0]["box"][1], _mk)
ok("a plain garment has nothing printed on it",
   D.features(Image.new("RGB", (60, 60), (30, 32, 34))) == [])
_wide = D.findings([{"target": "face", "verdict": "", "numbers": {"match": 0.7},
                     "marks": [{"box": [0.2, 0.2, 0.3, 0.3], "area": 0.01},
                               {"box": [0.6, 0.2, 0.7, 0.3], "area": 0.01},
                               {"box": [0.15, 0.55, 0.85, 0.8], "area": 0.1}],
                     "ref_marks": [{"box": [0.2, 0.2, 0.3, 0.3], "area": 0.01},
                                   {"box": [0.6, 0.2, 0.7, 0.3], "area": 0.01},
                                   {"box": [0.28, 0.55, 0.72, 0.8], "area": 0.06}]}],
                   "three", "front")
ok("a mouth the wrong shape is named as the mouth",
   any(x.startswith("FEATURES: the mouth") and "wide" in x for x in _wide), _wide)
_gone = D.findings([{"target": "face", "verdict": "", "numbers": {"match": 0.7},
                     "marks": [{"box": [0.2, 0.5, 0.8, 0.8], "area": 0.1}],
                     "ref_marks": [{"box": [0.2, 0.2, 0.3, 0.3], "area": 0.01},
                                   {"box": [0.6, 0.2, 0.7, 0.3], "area": 0.01},
                                   {"box": [0.28, 0.55, 0.72, 0.8], "area": 0.06}]}],
                   "three", "front")
ok("an eye hidden behind the hair is named as a missing mark",
   any(x.startswith("FEATURES: the reference's `face` carries 3") for x in _gone), _gone)

print("\nThe parts a character is judged by")
ok("pants, shoes, hands and arms are in the list now",
   all(n in D.CANDIDATES for n in ("pants", "leg", "shoe", "hand", "arm", "sleeve")))
ok("eight parts at most, automatically", D.MAX_TARGETS == 8)
ok("...and pockets and the trim are in the list, because every fault this round was on one",
   all(n in D.CANDIDATES for n in ("pocket", "hood", "drawstring", "lace")))
ok("a character with legs, shoes and hands gets all of them",
   D.pick_targets([], ["face", "hair", "torso", "leg", "shoe", "hand"], ["character"], True)
   == ["face", "hair", "torso", "leg", "shoe", "hand"],
   D.pick_targets([], ["face", "hair", "torso", "leg", "shoe", "hand"], ["character"], True))
ok("...and never more than eight without asking",
   len(D.pick_targets([], ["face", "head", "hair", "torso", "leg", "shoe", "hand",
                           "arm", "pocket", "hood"], ["character"], True)) <= 8,
   D.pick_targets([], ["face", "head", "hair", "torso", "leg", "shoe", "hand", "arm",
                       "pocket", "hood"], ["character"], True))

# ---------------------------------------------------------------- the page script
print("\nThe page script has what the pass calls, and says which version it is")
ok("boxOf, present, texFacts and solo are in it",
   all(("F.%s = function" % f) in LF.FORGE for f in ("boxOf", "present", "texFacts", "solo")))
ok("...and the studio, the levels, the GLB pair and the movable key light",
   all(("F.%s = " % f) in LF.FORGE for f in ("studio", "levels", "glb", "exportGlb", "keyAt")))
ok("the rig has presets, and one of them keeps black black",
   "var RIGS" in LF.FORGE and "reference:" in LF.FORGE and "amb: 0.10" in LF.FORGE)
ok("the camera can be orthographic, in both engines",
   "OrthographicCamera" in LF.FORGE and "PROJECTION_ORTHOGRAPHIC" in LF.FORGE)
ok("...and under ortho the fit loop grows the frame instead of backing away",
   "var grow = function (k) { if (ortho) half *= k; else dist *= k; };" in LF.FORGE)
ok("solo can hide the named part instead of everything else, which is what an A/B needs",
   "F.solo = function (q, invert, exact)" in LF.FORGE and "if (inKeep(o) !== flip) return;" in LF.FORGE)
ok("the version is stamped in", ("F.version = %d;" % LF.FORGE_VERSION) in LF.FORGE and "__FORGE_VERSION__" not in LF.FORGE)
# A page remembers a failed import: the same URL rejects at once, even after the file exists.
# Proven on the roblox-boy proof page, where the plain URL failed and "?retry=1" loaded.
ok("a failed engine import gets one fresh try, and the record keeps the plain URL",
   "'forge=' + Date.now()" in LF.FORGE and "url: urls[i]" in LF.FORGE)


class FakeLive:
    def __init__(self, ver):
        self.ver, self.calls = ver, []

    async def raw(self, expr, wait=True):
        self.calls.append(str(expr)[:40])
        if str(expr).startswith("window.__forge ? "):
            return self.ver
        return None


fl = FakeLive(LF.FORGE_VERSION)
ok("a current script is left alone", asyncio.run(L._forge_script(fl)) == "current" and len(fl.calls) == 1)
fl = FakeLive(-1)
ok("no script: installed", asyncio.run(L._forge_script(fl)) == "installed" and not any("dispose" in c for c in fl.calls))
fl = FakeLive(0)
ok("an old script is disposed of and replaced", asyncio.run(L._forge_script(fl)) == "replaced"
   and any("dispose" in c for c in fl.calls), fl.calls)

# ---------------------------------------------------------------- activity never sticks
print("\nAn activity row always closes")


@L._settles
def fake_call(project, how):
    L._doing(project, "building", "3q", "probe")
    if how == "bad":
        return {"ok": False, "error": "could not find a way to serve the project"}
    if how == "raise":
        raise RuntimeError("boom")
    if how == "done":
        L._done(project, "rendered", "front")
    return {"ok": True}


P = str(Path(tempfile.gettempdir()) / "detail-test-project")
row = lambda: L._ACTIVITY.get(L._slug(P)) or {}  # noqa: E731
fake_call(P, "bad")
ok("an early error return closes it as failed", row().get("phase") == "failed" and row().get("ended"), row())
ok("...with the reason", "serve the project" in row().get("detail", ""))
try:
    fake_call(P, "raise")
except RuntimeError:
    pass
ok("a throw closes it as failed, and still throws", row().get("phase") == "failed" and "boom" in row().get("detail", ""))
fake_call(P, "done")
ok("a call that closed its own row keeps its own words", row().get("phase") == "rendered" and row().get("detail") == "front")
L._ACTIVITY[L._slug(P)] = {"project": P, "project_name": "x", "phase": "building", "detail": "",
                           "label": "probe2", "since": time.time() - 900}
act = [a for a in L.activity() if a.get("project") == P]
ok("a row running for 15 minutes is reported as no answer", act and act[0].get("phase") == "no answer"
   and act[0].get("running") is False, act)
L._ACTIVITY.pop(L._slug(P), None)

# ---------------------------------------------------------------- the 500 from /look
print("\nAn empty silhouette is not a crash")
with tempfile.TemporaryDirectory() as td:
    rp = os.path.join(td, "ref.png")
    im = Image.new("RGB", (120, 200), (0, 0, 0))
    ImageDraw.Draw(im).rectangle([40, 20, 80, 180], fill=(250, 120, 20))
    im.save(rp)
    blank = Image.new("RGB", (320, 240), (240, 240, 240))
    try:
        got = L._score_against(rp, blank, "left", drawn=True)
        ok("nothing drawn at that angle: no score, no exception", got == {}, got)
    except Exception as ex:
        ok("nothing drawn at that angle: no score, no exception", False, repr(ex))

# ---------------------------------------------------------------- the bench says which engine
print("\nThe bench says which engine built it")
L._bench_put(P, "const e = new pc.Entity('x'); add(e);", True, {}, {"engine": "playcanvas", "triangles": 12}, "boy")
b = L._bench.get(L.key_for(P)) or {}
ok("the engine is kept beside the code", b.get("engine") == "playcanvas", b)
L._bench_put(P, "", False, {}, {"triangles": 12}, "look")
ok("...and a look that does not say keeps it", (L._bench.get(L.key_for(P)) or {}).get("engine") == "playcanvas")
L._bench.pop(L.key_for(P), None)

# ---------------------------------------------------------------- the routes accept it
print("\nThe routes take the new fields")
from asset_studio.routers import live as R  # noqa: E402
ok("forge takes detail as a list", R.ForgeBody(project="x", js="y", detail=["face"]).detail == ["face"])
ok("...or true", R.LookBody(project="x", detail=True).detail is True)
ok("...and leaves it unset by default", R.ForgeBody(project="x", js="y").detail is None)
ok("...and detail_views", R.LookBody(project="x", detail_views=["top"]).detail_views == ["top"])

print("\n  %d passed, %d failed" % (passed, len(fails)))
for f in fails:
    print("  FAIL  " + f)
sys.exit(1 if fails else 0)

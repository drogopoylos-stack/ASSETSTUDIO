# -*- coding: utf-8 -*-
"""The agent sees every change: a picture of it, framed on what changed, before beside after.

The asks this answers, in the order an agent meets them:

  "I moved pillar-2. Show me."                         -> the `look` on /api/live/edit and /place
  "Show me crate-1 from the side, right now."          -> shot(target="crate-1", angle="side")
  "Press Space and show me what happens for 2 s."      -> watch(input=[Space], seconds=2)
  "Is the page even running the code I just wrote?"    -> `stale`, on all three

WHY IT EXISTS. In the running game `edit` and `place` answered with numbers only — bounds, a ground
gap, overlaps. To SEE the change an agent needed a second call (`/goto`, which wants a name and camera
arguments and is off by default on a new PC) and then had to open the PNG. So in a scene it worked
half blind, and an agent does not watch video: it looks, acts, looks. Here every change answers with
its own picture, framed on what changed, before beside after; motion comes as a short strip of frames.

THREE RULES, each one a way the picture could lie.

  1. ONE CAMERA FOR BOTH FRAMES, framed on the union of the object's box before and after (a place:
     the new object's box). Two cameras would show two framings, and "it moved" would be a camera
     move. The camera is the navigator's Studio camera (live_navigate.py), so a follow-rig cannot
     drag it, and its angles are relative to the GAME camera's yaw: "front" is as the player faces it.

  2. THE GAME IS LEFT EXACTLY AS THE EDIT LEFT IT. The edit is applied first (by live_scene); the look
     photographs it, puts the old values back for the "before" frame, and puts the edit's own values
     back again — only the fields the edit touched, bit for bit, so a crystal the game spins keeps
     spinning. The put-back runs in a `finally`: a look that fails half way never leaves the before.

  3. THE VIEW GOES BACK. The game's camera is never touched, and the Studio camera is handed back at
     the end, even on an error: released, or — when an agent had stood it somewhere with /goto —
     put back exactly where it was. Every answer carries `game_pose_same`, measured, not assumed.

Everything here runs in the route's worker thread through `live._run`, never on the main loop.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

from . import live as L

# ---------------------------------------------------------------------------------------- words
# Degrees added to the GAME camera's yaw, and the pitch. The contract's table; the navigator's yaw
# convention (0 looks down -Z, 90 down -X), so "front" looks the way the game camera looks.
ANGLES = {"front": (0.0, -12.0), "3q": (35.0, -25.0), "side": (90.0, -12.0),
          "back": (180.0, -12.0), "top": (0.0, -89.0)}
LOOK_WORDS = tuple(ANGLES) + ("player",)
DEFAULT_FOV = 50.0
MARGIN = 1.15                 # the whole box, and a little room round it
LOOK_BUDGET_S = 20.0          # the captures of one look; the put-back and release run after it regardless
WATCH_GROW = 2.0              # a watch frames twice the target's radius: what is watched moves
SLOW_LOOK_MS = 300            # a look slower than this says where its time went (`split_ms`)

_LOOK_ERR = 'look is true, false or one of "3q", "front", "side", "back", "top", "player"'


def look_request(value, batch: bool = False) -> tuple[Optional[str], str]:
    """(the angle to photograph from, or None for no picture; an error for a word that means nothing).

    Left out, a single edit or place takes a picture when the setting `scene_look` says so (on by
    default: the picture IS the answer); a batch `edits:[…]` takes one only when asked, because a
    script of twenty moves wants its numbers fast and its picture once, at the end."""
    if value is None or value == "":
        if batch:
            return None, ""
        return ("3q" if bool(L._engine_pref("scene_look", True)) else None), ""
    if value is True:
        return "3q", ""
    if value is False:
        return None, ""
    if isinstance(value, str) and value.strip().lower() in LOOK_WORDS:
        return value.strip().lower(), ""
    return None, _LOOK_ERR


# ---------------------------------------------------------------------------------------- camera math
def forward(yaw: float, pitch: float) -> list:
    """The navigator's forward vector: yaw 0 looks down -Z, pitch below zero looks down."""
    y, p = math.radians(yaw), math.radians(pitch)
    return [-math.sin(y) * math.cos(p), math.sin(p), -math.cos(y) * math.cos(p)]


def box_of(side: Optional[dict], pad: float = 0.3) -> Optional[tuple]:
    """(lo, hi) of a shown state {min, max, world}; a thing with no body is a small cube round its origin."""
    if not isinstance(side, dict):
        return None
    lo, hi = side.get("min"), side.get("max")
    try:
        if lo and hi and len(lo) == 3 and len(hi) == 3:
            return [float(v) for v in lo], [float(v) for v in hi]
        w = side.get("world") or side.get("pos")
        if w and len(w) == 3:
            w = [float(v) for v in w]
            return [v - pad for v in w], [v + pad for v in w]
    except (TypeError, ValueError):
        return None
    return None


def union(boxes) -> Optional[tuple]:
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    n = 0
    for b in boxes or []:
        if not b:
            continue
        for i in range(3):
            if not (math.isfinite(b[0][i]) and math.isfinite(b[1][i])):
                break
        else:
            n += 1
            for i in range(3):
                lo[i] = min(lo[i], b[0][i])
                hi[i] = max(hi[i], b[1][i])
    return (lo, hi) if n else None


def frame(lo, hi, game_yaw: float = 0.0, angle: str = "3q", fov: float = DEFAULT_FOV, aspect: float = 16 / 9,
          near: float = 0.1, far: float = 1e6, distance: Optional[float] = None,
          horizontal_fov: bool = False, grow: float = 1.0) -> dict:
    """The camera that holds this box whole: centre c, radius r (half the diagonal), and the distance
    d = r / sin(fov/2) x 1.15. The narrower of the two fields of view decides, so a phone held upright
    frames by its width. Where the camera's near plane would cut the box, `near` says how far the
    Studio camera's own near plane must come in (the distance stays the formula's); never past the far
    plane. `grow` widens the radius for a watch: a thing framed tight jumps straight out of its own
    frame (measured: the proof player left the top of the picture for half of its jump)."""
    c = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    r = max(0.05, 0.5 * math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))) * max(1.0, float(grow or 1.0))
    fov = float(fov) if isinstance(fov, (int, float)) and math.isfinite(fov) and 1.0 < float(fov) < 179.0 else DEFAULT_FOV
    aspect = float(aspect) if isinstance(aspect, (int, float)) and math.isfinite(aspect) and aspect > 0.05 else 16 / 9
    if horizontal_fov:
        hf = fov
        vf = math.degrees(2 * math.atan(math.tan(math.radians(hf) / 2) / aspect))
    else:
        vf = fov
        hf = math.degrees(2 * math.atan(math.tan(math.radians(vf) / 2) * aspect))
    eff = min(vf, hf)
    near = float(near) if isinstance(near, (int, float)) and near > 0 else 0.1
    far = float(far) if isinstance(far, (int, float)) and far > near else 1e6
    notes = []
    if distance is not None and isinstance(distance, (int, float)) and math.isfinite(distance) and distance > 0:
        d = float(distance)
    else:
        d = r / math.sin(math.radians(eff) / 2.0) * MARGIN
    if d > far * 0.9:
        d = far * 0.9
        notes.append("the box is too big for the camera's far plane (%.0f m); the frame is cut there" % far)
    # THE STUDIO CAMERA'S NEAR PLANE, not the distance, gives way. It is a clone of the game's, and
    # rot-rush's near clip is 4.2 m: keeping the camera outside that pushed a 1.5 m block 10 m away
    # (22% of the frame). The look lowers its own camera's near plane instead; the game's is untouched.
    studio_near = near
    if d - r < near * 1.2:
        studio_near = max(0.02, (d - r) * 0.5)
    dyaw, pitch = ANGLES.get(angle, ANGLES["3q"])
    yaw = (float(game_yaw or 0.0) + dyaw + 180.0) % 360.0 - 180.0
    f = forward(yaw, pitch)
    pos = [c[i] - f[i] * d for i in range(3)]
    rnd = lambda v: round(v, 4)
    return {"look_at": [rnd(v) for v in c], "distance": rnd(d), "yaw": rnd(yaw), "pitch": pitch,
            "radius": rnd(r), "fov": round(eff, 2), "pos": [rnd(v) for v in pos], "near": rnd(studio_near),
            "notes": notes}


# ---------------------------------------------------------------------------------------- stale
# Source files of the served game, scanned once per two seconds at most and never past 2,000 files.
# os.scandir hands back each file's mtime with the listing on Windows, so a walk is one pass.
_SRC_EXT = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx", ".html", ".htm", ".css", ".json"}
_SKIP_DIRS = {"node_modules", "dist", "graphify-out", "__pycache__"}
# Only used when the page's resource list is full and a file's use cannot be read off it: a lock file
# or a build config changing is not the page running old code.
_NOT_PAGE = ("package.json", "package-lock.json", "npm-shrinkwrap.json", "jsconfig.json", "serve.mjs")
_SCAN: dict = {}
_SCAN_TTL = 2.0
_SCAN_MAX = 2000
_RESOURCE_BUFFER = 250        # Chrome's default resource timing buffer: at this count entries may be missing
_STALE_NOTE = ("the page shows code older than your edit — POST /api/live/open {\"reload\": true} (the same tab"
               " reloads; unsaved tries are lost, so save:true what you keep first)")
_MAYBE_NOTE = ("the page's list of the files it loaded is full, so it cannot say whether it runs these; if the"
               " picture does not show your change, POST /api/live/open {\"reload\": true}")


def scan_sources(root: Path) -> tuple[dict, bool]:
    """{project-relative posix path: mtime} for the game's source files, and whether the cap cut it."""
    key = str(root).lower()
    now = time.monotonic()
    hit = _SCAN.get(key)
    if hit and now - hit[0] < _SCAN_TTL:
        return hit[1], hit[2]
    files: dict = {}
    truncated = False
    stack = [str(root)]
    while stack and not truncated:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for de in it:
                name = de.name
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if not name.startswith(".") and name not in _SKIP_DIRS:
                        stack.append(de.path)
                    continue
                if os.path.splitext(name)[1].lower() not in _SRC_EXT or name.startswith("studio.edits.json"):
                    continue
                try:
                    m = de.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                files[os.path.relpath(de.path, str(root)).replace("\\", "/")] = m
                if len(files) >= _SCAN_MAX:
                    truncated = True
                    break
    _SCAN[key] = (now, files, truncated)
    return files, truncated


def url_paths(rel: str) -> list:
    """Where a served file is asked for: /src/x.js; a Vite public/ file from the root."""
    rel = rel.lstrip("/")
    out = ["/" + rel]
    if rel.lower().startswith("public/"):
        out.append("/" + rel[7:])
    return out


def _split_stale(files: dict, origin_ms: float, fetched: dict, doc_path: str) -> tuple[list, list]:
    """([(rel, mtime)] the page surely runs an older copy of, [(rel, mtime)] changed after the page
    loaded that its resource list does not mention). Both newest first. Pure."""
    sure, unseen = [], []
    doc = doc_path if doc_path not in ("", "/") else "/index.html"
    for rel, m in (files or {}).items():
        mm = float(m) * 1000.0
        if mm <= float(origin_ms) + 1.0:
            continue
        paths = url_paths(rel)
        got = max([float(fetched.get(p) or 0.0) for p in paths] + [0.0])
        if doc in paths or (doc_path in ("", "/") and rel.lower() == "index.html"):
            got = max(got, float(origin_ms))
        if got > 0:
            if got + 1.0 < mm:
                sure.append((rel, m))
            continue
        name = Path(rel).name
        if name not in _NOT_PAGE and not name.startswith(("tsconfig", "vite.config", "vitest.config")):
            unseen.append((rel, m))
    sure.sort(key=lambda x: -x[1])
    unseen.sort(key=lambda x: -x[1])
    return sure, unseen


def stale_files(files: dict, origin_ms: float, fetched: dict, entries: int = 0, doc_path: str = "/") -> list:
    """[(rel, mtime)] of the sources the page is running an older copy of, newest first. Pure.

    Changed after the page loaded is not enough on its own: Vite hot-swaps a module without a reload
    (the page re-fetches it, `?t=` and all), and a test file or a tool script that the page never
    loaded changes nothing it runs. So the page's own resource list decides: a file it fetched, and
    that changed after that fetch, is stale; one it never fetched is not. When that list is full it
    cannot vouch either way, and those files are `maybe_stale`, never counted here: counting them
    reported every changed file of a big game as stale, and each report asked for a reload."""
    return _split_stale(files, origin_ms, fetched, doc_path)[0]


def maybe_stale(files: dict, origin_ms: float, fetched: dict, full: bool, doc_path: str = "/") -> list:
    """The files a FULL resource list cannot vouch for: changed after the page loaded, and not in
    the list. Empty when the list is whole (the shim raises it to 5,000 at document start). Pure."""
    return _split_stale(files, origin_ms, fetched, doc_path)[1] if full else []


def _stale_answer(stale: list, origin_ms: float, maybe: Optional[list] = None) -> Optional[dict]:
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(origin_ms) / 1000.0))
    if stale:
        return {"files": [rel for rel, _m in stale[:5]], "n": len(stale), "since": since, "note": _STALE_NOTE}
    if maybe:
        return {"files": [rel for rel, _m in maybe[:5]], "n": len(maybe), "since": since, "maybe": True,
                "note": _MAYBE_NOTE}
    return None


def _served_root(project: str, url: str) -> Path:
    try:
        rel = L._serving_root(project, url) if url else ""
    except Exception:                                   # noqa: BLE001
        rel = ""
    return Path(project) / rel if rel else Path(project)


# The page's side of it: when it loaded, and when it last fetched each path asked about.
_STALE_JS = r"""(function (paths) {
  var want = {}, i;
  for (i = 0; i < paths.length; i++) want[paths[i]] = 1;
  var o = performance.timeOrigin, f = {}, n = 0, doc = '/';
  try { doc = decodeURIComponent(location.pathname || '/'); } catch (e) {}
  try {
    var es = performance.getEntriesByType('resource');
    n = es.length;
    for (i = 0; i < es.length; i++) {
      var p;
      try { p = decodeURIComponent(new URL(es[i].name, location.href).pathname); } catch (e) { continue; }
      if (!want[p]) continue;
      var t = o + es[i].startTime;
      if (!(f[p] >= t)) f[p] = t;
    }
  } catch (e) {}
  /* Whole, or not: the shim raises the list to 5,000 at document start and marks it when it fills.
     A page with no shim mark keeps Chrome's 250. */
  var lv = window.__live || {}, full = !!lv.rtFull || n >= (lv.rtMax || 250);
  return { origin: o, fetched: f, entries: n, full: full, doc: doc, scheme: location.protocol };
})(%s)"""


def _candidates(project: str, e: dict) -> tuple[dict, list]:
    """Sources changed since the tab's record was made (the page cannot be older than that), and the
    URL paths to ask the page about."""
    files, _trunc = scan_sources(_served_root(project, str(e.get("url") or "")))
    floor_ms = float(e.get("opened") or 0.0) * 1000.0 - 2000.0
    cand = {r: m for r, m in files.items() if float(m) * 1000.0 > floor_ms}
    paths = sorted({p for r in cand for p in url_paths(r)})
    return cand, paths[:4000]


async def stale_async(live, project: str, e: dict) -> Optional[dict]:
    """`stale` for a call that already holds a session on the tab: one evaluate, when anything changed."""
    cand, paths = _candidates(project, e)
    if not cand:
        return None
    got = await live.raw(_STALE_JS % json.dumps(paths))
    if not isinstance(got, dict) or not str(got.get("scheme") or "").startswith("http"):
        return None
    origin, fetched, doc = float(got.get("origin") or 0.0), got.get("fetched") or {}, str(got.get("doc") or "/")
    full = bool(got.get("full")) if "full" in got else int(got.get("entries") or 0) >= _RESOURCE_BUFFER
    return _stale_answer(stale_files(cand, origin, fetched, int(got.get("entries") or 0), doc), origin,
                         maybe_stale(cand, origin, fetched, full, doc))


def stale_now(project: str, e: dict) -> Optional[dict]:
    """`stale` for the plain /shot, which has no session of its own left: a session only when some
    source file is newer than the tab's record, which is the uncommon case."""
    cand, _paths = _candidates(project, e)
    if not cand or not e.get("target"):
        return None

    async def go():
        ws, live = await L._session(e)
        try:
            return await stale_async(live, project, e)
        finally:
            await ws.close()

    return L._run(go)


# ---------------------------------------------------------------------------------------- pictures
AMBER = (255, 181, 36)
GREEN = (61, 222, 132)
MAGENTA = (255, 70, 205)
INK = (232, 236, 242)
BG = (15, 17, 22)
BAR = (28, 32, 40)
SHEET_W = 1568
SHEET_PX = 1_150_000
GAP = 8


def _font(size: int):
    from .review import _font as rf
    return rf(size)


def _text_w(draw, text: str, font) -> float:
    try:
        return draw.textlength(text, font=font)
    except Exception:                                   # noqa: BLE001
        return len(text) * font.size * 0.6 if hasattr(font, "size") else len(text) * 7


def fit_text(draw, text: str, font, width: float) -> str:
    """The caption, cut with an ellipsis to the width it has — one line, never a wrap."""
    text = str(text or "")
    if _text_w(draw, text, font) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(draw, text[:mid] + "…", font) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip(" ·,;") + "…"


def outline(draw, box01, x: int, y: int, w: int, h: int, color, label: str = "", font=None, width: int = 3) -> None:
    """A screen box (0..1 of the frame) drawn on a panel at (x, y, w, h): a dark rim under the colour,
    so the mark reads on a white wall and on a black sky alike."""
    if not box01:
        return
    x0 = x + max(0.0, min(1.0, float(box01[0]))) * w
    y0 = y + max(0.0, min(1.0, float(box01[1]))) * h
    x1 = x + max(0.0, min(1.0, float(box01[2]))) * w
    y1 = y + max(0.0, min(1.0, float(box01[3]))) * h
    if x1 - x0 < 6:
        cx = (x0 + x1) / 2
        x0, x1 = cx - 3, cx + 3
    if y1 - y0 < 6:
        cy = (y0 + y1) / 2
        y0, y1 = cy - 3, cy + 3
    draw.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], outline=(0, 0, 0), width=width + 2)
    draw.rectangle([x0, y0, x1, y1], outline=color, width=width)
    if label and font is not None:
        tw = _text_w(draw, label, font)
        ty = y0 - font.size - 6 if y0 - font.size - 6 > y else min(y + h - font.size - 4, y1 + 3)
        tx = min(max(x, x0), x + w - tw - 4)
        draw.rectangle([tx - 2, ty - 1, tx + tw + 2, ty + font.size + 3], fill=(0, 0, 0))
        draw.text((tx, ty), label, fill=color, font=font)


def pair_panel_size(vw: int, vh: int) -> tuple[int, int]:
    """The width and height of each of the two panels: as wide as the sheet allows, within 1.15 Mpx."""
    aspect = max(0.2, float(vw) / max(1.0, float(vh)))
    pw = (SHEET_W - 3 * GAP) // 2
    while pw > 120:
        ph = int(round(pw / aspect))
        if (2 * pw + 3 * GAP) * (ph + 30 + 28 + 2 * GAP) <= SHEET_PX:
            break
        pw -= 8
    return pw, max(1, int(round(pw / aspect)))


def compose_pair(before, after, boxes_before: list, boxes_after: list, cap_before: str, cap_after: str,
                 title: str = ""):
    """Before | after, side by side, one caption line under each, the target outlined in each (before
    amber, after green). `boxes_*` are [(label, [x0,y0,x1,y1] | None)]; a label is drawn only when
    more than one thing is outlined."""
    from PIL import Image, ImageDraw
    pw, ph = pair_panel_size(after.width, after.height)
    title_h, cap_h = 28, 30
    W = 2 * pw + 3 * GAP
    H = GAP + title_h + ph + cap_h + GAP
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    ft, fc, fl = _font(15), _font(14), _font(12)
    d.text((GAP, GAP + 5), fit_text(d, title, ft, W - 2 * GAP), fill=INK, font=ft)
    y = GAP + title_h
    many = len(boxes_after) > 1 or len(boxes_before) > 1
    for i, (img, boxes, color, cap) in enumerate(((before, boxes_before, AMBER, cap_before),
                                                  (after, boxes_after, GREEN, cap_after))):
        x = GAP + i * (pw + GAP)
        im = img.convert("RGB")
        if im.size != (pw, ph):
            im = im.resize((pw, ph), Image.LANCZOS)
        sheet.paste(im, (x, y))
        for label, b in boxes:
            outline(d, b, x, y, pw, ph, color, label if many else "", fl)
        d.rectangle([x, y + ph, x + pw, y + ph + cap_h - 4], fill=BAR)
        d.rectangle([x, y + ph, x + 5, y + ph + cap_h - 4], fill=color)
        d.text((x + 12, y + ph + 6), fit_text(d, cap, fc, pw - 20), fill=INK, font=fc)
    return sheet


def plan_grid(n: int, aspect: float, label_h: int = 24, title_h: int = 30) -> tuple[int, int, int]:
    """(columns, cell width, cell height) for n frames: the biggest cells whose sheet stays within
    1568 px wide and 1.15 Mpx, landscape-or-square first."""
    aspect = max(0.2, float(aspect))
    best = None
    for min_aspect in (0.95, 0.0):
        for cols in range(1, max(1, min(n, 6)) + 1):
            rows = (n + cols - 1) // cols
            cw = (SHEET_W - GAP * (cols + 1)) // cols
            while cw >= 80:
                ch = int(round(cw / aspect))
                W = cols * cw + GAP * (cols + 1)
                H = title_h + rows * (ch + label_h + GAP) + GAP
                if W * H <= SHEET_PX and W >= H * min_aspect:
                    break
                cw -= 8
            if cw < 80:
                continue
            if best is None or cw > best[1]:
                best = (cols, cw, int(round(cw / aspect)))
        if best:
            return best
    return (min(n, 4), 160, int(round(160 / aspect)))


def compose_grid(frames: list, labels: list, title: str, target_box=None, motion_boxes: Optional[list] = None,
                 mark: int = -1, target_label: str = ""):
    """Frames in reading order, a label under each; the target's box in green on every frame, where
    each frame changed from the one before in magenta, and the frame the input went in on marked."""
    from PIL import Image, ImageDraw
    n = len(frames)
    aspect = frames[0].width / max(1, frames[0].height)
    label_h, title_h = 24, 30
    cols, cw, ch = plan_grid(n, aspect, label_h, title_h)
    rows = (n + cols - 1) // cols
    W = cols * cw + GAP * (cols + 1)
    H = title_h + rows * (ch + label_h + GAP) + GAP
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    ft, fl, fs = _font(15), _font(13), _font(11)
    d.text((GAP, 7), fit_text(d, title, ft, W - 2 * GAP), fill=INK, font=ft)
    for i, im in enumerate(frames):
        r, c = divmod(i, cols)
        x = GAP + c * (cw + GAP)
        y = title_h + r * (ch + label_h + GAP)
        sheet.paste(im.convert("RGB").resize((cw, ch), Image.LANCZOS), (x, y))
        if target_box:
            outline(d, target_box, x, y, cw, ch, GREEN, target_label, fs, width=2)
        mb = (motion_boxes or [None] * n)[i] if motion_boxes else None
        if mb:
            outline(d, mb, x, y, cw, ch, MAGENTA, "", None, width=2)
        d.rectangle([x, y + ch, x + cw, y + ch + label_h - 3], fill=BAR)
        if i == mark:
            d.rectangle([x, y + ch, x + 5, y + ch + label_h - 3], fill=MAGENTA)
        d.text((x + 9, y + ch + 4), fit_text(d, labels[i] if i < len(labels) else "", fl, cw - 14), fill=INK, font=fl)
    return sheet


# ---------------------------------------------------------------------------------------- captions
def _m(v: float) -> str:
    return "%.2f m" % abs(float(v))


def gap_words(g: Optional[dict]) -> str:
    """What a ground answer says in three words: rests on floor / floats 0.42 m / sinks 0.30 m."""
    if not isinstance(g, dict) or g.get("gap") is None:
        return "nothing below"
    gap = float(g["gap"])
    if abs(gap) <= 0.01:
        return "rests on %s" % (g.get("support_of") or g.get("support") or "the ground")
    return ("floats " if gap > 0 else "sinks ") + _m(gap)


def _dist(a, b) -> Optional[float]:
    try:
        return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))
    except Exception:                                   # noqa: BLE001
        return None


def change_words(res: dict) -> list:
    """What one edit did, in the words a person would use looking at the two frames."""
    b, a = res.get("before") or {}, res.get("after") or {}
    ch = set(res.get("changed") or [])
    out = []
    mv = _dist(b.get("world"), a.get("world"))
    if mv is not None and mv > 0.005:
        out.append("moved " + _m(mv))
    elif "pos" in ch:
        out.append("not moved")
    if "rot" in ch and b.get("rot") and a.get("rot"):
        dr = [round(float(a["rot"][i]) - float(b["rot"][i]), 1) for i in range(3)]
        dr = [((v + 180.0) % 360.0) - 180.0 for v in dr]
        nz = [(ax, v) for ax, v in zip("xyz", dr) if abs(v) >= 0.05]
        if len(nz) == 1:
            out.append("turned %g° about %s" % (round(nz[0][1], 1), nz[0][0]))
        elif nz:
            out.append("turned " + " ".join("%s%+g°" % (ax, round(v, 1)) for ax, v in nz))
    if "scale" in ch and b.get("scale") and a.get("scale"):
        bs, as_ = [float(v) for v in b["scale"]], [float(v) for v in a["scale"]]
        if max(as_) - min(as_) < 1e-6 and max(bs) - min(bs) < 1e-6:
            out.append("scaled %g → %g" % (round(bs[0], 3), round(as_[0], 3)))
        else:
            out.append("scale %s → %s" % ([round(v, 3) for v in bs], [round(v, 3) for v in as_]))
    if "hidden" in ch:
        out.append("hidden" if a.get("visible") is False else "shown")
    return out


def ground_words(before_g: Optional[dict], after_g: Optional[dict]) -> str:
    """"ground gap 0.00 → floats 0.42 m" when the footing changed; "rests on floor" when it did not."""
    ga = after_g if isinstance(after_g, dict) else None
    gb = before_g if isinstance(before_g, dict) else None
    if gb is None:
        return gap_words(ga) if ga else ""
    b_gap, a_gap = gb.get("gap"), (ga or {}).get("gap")
    same_support = (gb.get("support") == (ga or {}).get("support"))
    if b_gap is None and a_gap is None:
        return "nothing below"                      # not "nothing below → nothing below"
    if b_gap is not None and a_gap is not None and abs(float(b_gap) - float(a_gap)) <= 0.005 and same_support:
        return gap_words(ga)
    left = ("ground gap %.2f" % float(b_gap)) if b_gap is not None else "nothing below"
    return "%s → %s" % (left, gap_words(ga))


def overlap_words(ov) -> str:
    rows = [o for o in (ov or []) if isinstance(o, dict) and o.get("key")]
    if not rows:
        return ""
    names = [str(o.get("of") or o["key"]) if o.get("of") and o.get("of") != o["key"] else str(o["key"]) for o in rows[:3]]
    names = list(dict.fromkeys(names))
    return "overlaps: " + ", ".join(names) + (" +%d" % (len(rows) - 3) if len(rows) > 3 else "")


def edit_captions(results: list) -> tuple[str, str, str]:
    """(title, before caption, after caption) for one edit or a batch of them."""
    if len(results) == 1:
        r = results[0]
        key = str(r.get("key") or "?")
        what = change_words(r)
        after = ["after"] + what
        gw = ground_words(r.get("ground_before"), r.get("ground"))
        if gw:
            after.append(gw)
        ow = overlap_words(r.get("overlaps"))
        if ow:
            after.append(ow)
        before = ["before", key]
        if isinstance(r.get("ground_before"), dict):
            before.append(gap_words(r["ground_before"]))
        return key + " — " + (", ".join(what) or "edited"), " · ".join(before), " · ".join(after)
    keys = list(dict.fromkeys(str(r.get("key") or "?") for r in results))
    parts = []
    for r in results:
        w = change_words(r)
        parts.append("%s %s" % (r.get("key"), ", ".join(w) or "edited"))
    hits = [overlap_words(r.get("overlaps")) for r in results]
    hits = [h for h in hits if h]
    after = "after · " + "; ".join(parts) + ((" · " + hits[0]) if hits else "")
    return ("%d objects — %s" % (len(keys), ", ".join(keys[:4]) + (" …" if len(keys) > 4 else "")),
            "before · %d objects" % len(keys), after)


def place_captions(res: dict) -> tuple[str, str, str]:
    name = str(res.get("key") or res.get("name") or "?")
    after = ["after", "placed " + name]
    gw = gap_words(res.get("ground")) if isinstance(res.get("ground"), dict) else ""
    if gw:
        after.append(gw)
    ow = overlap_words(res.get("overlaps"))
    if ow:
        after.append(ow)
    return (name + " — placed" + (" (" + str(res.get("how")) + ")" if res.get("how") else ""),
            "before · %s not there yet (hidden for this frame)" % name, " · ".join(after))


# ---------------------------------------------------------------------------------------- the page, driven
_SPEED = {"ok": True}


async def capture(live, fmt: str = "jpeg", quality: int = 90, scale: Optional[float] = None,
                  vw: int = 0, vh: int = 0, full: bool = False) -> bytes:
    """One frame of the tab. JPEG unless asked otherwise: 20 ms against 38 for a PNG of the same
    1280x720 frame, measured on this machine, and the look takes two."""
    params: dict = {"format": fmt}
    if fmt == "jpeg":
        params["quality"] = int(quality)
    if scale and vw and vh and abs(scale - 1.0) > 1e-3:
        params["clip"] = {"x": 0, "y": 0, "width": int(vw), "height": int(vh), "scale": float(scale)}
    if full:
        params["captureBeyondViewport"] = True
    if _SPEED["ok"]:
        params["optimizeForSpeed"] = True
    try:
        got = await live.call("Page.captureScreenshot", params)
    except RuntimeError as ex:
        if "optimizeForSpeed" not in params:
            raise
        _SPEED["ok"] = False                         # an older Chrome: ask again without it, once
        params.pop("optimizeForSpeed", None)
        got = await live.call("Page.captureScreenshot", params)
        del ex
    data = base64.b64decode(got.get("data") or "")
    if not data:
        raise RuntimeError("the browser returned no image")
    return data


async def _page(live, expr: str) -> Any:
    from .live_scene import page
    return await page(live, expr)


async def _prepare(live, e: dict) -> None:
    """The scene script and the navigator, both on the page."""
    from .live_scene import _prepared
    from .live_navigate import ensure_nav
    await _prepared(live, e)
    await ensure_nav(live)


async def begin_view(live) -> dict:
    """The navigator's state saved, and the game camera's pose. Asked again two frames later when the
    game's camera has not been heard yet (a page's first look): the render hook hears it on the next
    frame the game draws, and without it "front" has no heading and `game_pose_same` no baseline."""
    b = await _page(live, "__scene.viewBegin()") or {}
    if isinstance(b, dict) and b.get("ok") and not b.get("game"):
        await _page(live, "__scene.frames(3)")
        b = await _page(live, "__scene.viewBegin()") or b
    return b if isinstance(b, dict) else {}


async def aim(live, begin: dict, lo, hi, angle: str, distance: Optional[float] = None, grow: float = 1.0) -> dict:
    """Put the Studio camera where `frame` says, and wait until it is the camera drawing."""
    game = begin.get("game") if isinstance(begin.get("game"), dict) else {}
    fr = frame(lo, hi, game_yaw=float(game.get("yaw") or 0.0), angle=angle,
               fov=game.get("fov") or DEFAULT_FOV, aspect=begin.get("aspect") or 16 / 9,
               near=begin.get("near") or 0.1, far=begin.get("far") or 1e6, distance=distance,
               horizontal_fov=bool(begin.get("horizontal_fov")), grow=grow)
    req = json.dumps({"look_at": fr["look_at"], "distance": fr["distance"], "yaw": fr["yaw"],
                      "pitch": fr["pitch"], "limit": 8})
    res = await live.ask("__nav.goto(%s)" % req, depth=6) or {}
    if isinstance(res, dict) and res.get("error") == "the game has no camera yet":
        # The scene's render hook hears the camera on the next frame the game draws.
        await asyncio.sleep(0.3)
        res = await live.ask("__nav.goto(%s)" % req, depth=6) or {}
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("the Studio camera could not be placed: %s" % (
            (res or {}).get("error") if isinstance(res, dict) else res))
    if fr.get("near") and float(fr["near"]) < float(begin.get("near") or 0.1) - 1e-9:
        await _page(live, "__scene.viewNear(%s)" % json.dumps(float(fr["near"])))
    drawing = False
    for _ in range(12):
        await _page(live, "__scene.frames(1)")
        if await _page(live, "__scene.viewDrawing()"):
            drawing = True
            break
    return {"frame": fr, "pose": res.get("pose"), "drawing": drawing,
            "in_view": [str(t.get("name")) for t in (res.get("in_view") or []) if isinstance(t, dict)][:8]}


def _same_pose(a: Optional[dict], b: Optional[dict], tol: float = 2e-3) -> Optional[bool]:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    try:
        pa, pb = a.get("pos") or [], b.get("pos") or []
        if len(pa) != 3 or len(pb) != 3:
            return None
        if any(abs(float(pa[i]) - float(pb[i])) > tol for i in range(3)):
            return False
        for k in ("yaw", "pitch"):
            if a.get(k) is not None and b.get(k) is not None:
                dv = abs(((float(a[k]) - float(b[k])) + 180.0) % 360.0 - 180.0)
                if dv > 0.05:
                    return False
        return True
    except (TypeError, ValueError):
        return None


def _out_dir(project: str) -> Path:
    d = L._LIVE_DIR / L._slug(project)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cam_answer(angle: str, shot: Optional[dict], game: Optional[dict]) -> dict:
    if angle == "player" or not shot:
        g = game if isinstance(game, dict) else {}
        return {"angle": "player", "pos": g.get("pos"), "look_at": None, "yaw": g.get("yaw"),
                "pitch": g.get("pitch"), "fov": g.get("fov")}
    fr, pose = shot.get("frame") or {}, shot.get("pose") if isinstance(shot.get("pose"), dict) else {}
    return {"angle": angle, "pos": pose.get("pos") or fr.get("pos"), "look_at": fr.get("look_at"),
            "yaw": pose.get("yaw", fr.get("yaw")), "pitch": pose.get("pitch", fr.get("pitch")),
            "distance": fr.get("distance"), "fov": pose.get("fov")}


def _union01(boxes: list) -> Optional[list]:
    got = [b for b in boxes if b]
    if not got:
        return None
    return [round(min(b[0] for b in got), 3), round(min(b[1] for b in got), 3),
            round(max(b[2] for b in got), 3), round(max(b[3] for b in got), 3)]


# ---------------------------------------------------------------------------------------- the look
_FIELDS = {"pos": ("pos",), "rot": ("rot", "order", "q"), "scale": ("scale",), "hidden": ("on",)}


def partial_local(local: Optional[dict], changed) -> dict:
    """Only the fields an edit touched: putting back a whole transform would also wind back what the
    game itself animates on that object (the proof game spins its crystals every frame)."""
    if not isinstance(local, dict):
        return {}
    keep = set()
    for c in changed or []:
        keep |= set(_FIELDS.get(c, ()))
    # Nothing touched is nothing to put back. Falling back to the whole transform wrote an object's
    # edit-time pose back twice — a failed drop wound the game's own spin back, and the drift check
    # then blamed the game for an object nobody had changed.
    return {k: v for k, v in local.items() if k in keep}


def drifted(want: Optional[dict], got: Optional[dict], tol_pos: float = 0.01, tol_deg: float = 0.5,
            tol_scale: float = 1e-3) -> list:
    """The fields an edit set that the object no longer has when the after frame is taken — the game
    drove them back. Pure. Rotations compare as quaternions when both sides have one (PlayCanvas),
    else as XYZ euler with wrap-around (three), so 359 and -1 degrees are the same turn."""
    out: list = []
    if not isinstance(want, dict) or not isinstance(got, dict):
        return out

    def far(a, b, tol):
        try:
            return max(abs(float(x) - float(y)) for x, y in zip(a, b)) > tol
        except Exception:                               # noqa: BLE001
            return False

    if want.get("pos") and got.get("pos") and far(want["pos"], got["pos"], tol_pos):
        out.append("pos")
    if want.get("q") and got.get("q"):
        try:
            dot = abs(sum(float(a) * float(b) for a, b in zip(want["q"], got["q"])))
            if math.degrees(2 * math.acos(min(1.0, dot))) > tol_deg:
                out.append("rot")
        except Exception:                               # noqa: BLE001
            pass
    elif want.get("rot") and got.get("rot"):
        try:
            dr = [abs(((float(a) - float(b)) + math.pi) % (2 * math.pi) - math.pi) for a, b in zip(want["rot"], got["rot"])]
            if math.degrees(max(dr)) > tol_deg:
                out.append("rot")
        except Exception:                               # noqa: BLE001
            pass
    if want.get("scale") and got.get("scale") and far(want["scale"], got["scale"], tol_scale):
        out.append("scale")
    if "on" in want and got.get("on") is not None and bool(want["on"]) != bool(got["on"]):
        out.append("visible")
    return out


SETTLE_S = 1.5               # how long a place look waits for the engine to have drawn its new thing


async def _look_session(live, project: str, e: dict, keys: list, lo, hi, angle: str,
                        before_js: str, after_js: str, opts: dict, place: bool = False) -> dict:
    """The captures of one look, in one session. The sheet is drawn afterwards, off the browser."""
    out: dict = {"errors": [], "notes": [], "split": {}}
    mark = [time.monotonic()]

    def lap(name: str) -> None:
        now = time.monotonic()
        out["split"][name] = int((now - mark[0]) * 1000)
        mark[0] = now

    await _prepare(live, e)
    lap("prepare")
    begin = await begin_view(live)
    lap("begin")
    if not isinstance(begin, dict) or not begin.get("ok"):
        out["errors"].append((begin or {}).get("error") if isinstance(begin, dict) else "the view could not start")
        return out
    out["begin"] = begin
    kjs, ojs = json.dumps(keys), json.dumps(opts or {})
    flags = {"before": False, "back": False}

    async def shoot():
        if angle == "player":
            await _page(live, "__scene.viewGame()")
        else:
            out["shot"] = await aim(live, begin, lo, hi, angle)
            if not out["shot"].get("drawing"):
                out["notes"].append("the Studio camera was not seen drawing within 12 frames; the frames may be the game's view")
        lap("aim")
        out["revealed"] = await _page(live, "__scene.reveal(%s, %s)" % (kjs, ojs)) or []
        if place:
            await shoot_place()
            return
        await _page(live, "__scene.frames(2)")
        out["after"] = await capture(live)
        out["after_boxes"] = await _page(live, "__scene.screenBoxes(%s, %s)" % (kjs, ojs)) or {}
        lap("after")
        flags["before"] = True                     # before the call: a timeout mid-call still puts it back
        got = await _page(live, before_js) or {}
        if isinstance(got, dict) and got.get("missing"):
            out["notes"].append("could not put back for the before frame: %s" % ", ".join(map(str, got["missing"][:5])))
        await _page(live, "__scene.frames(2)")
        out["before"] = await capture(live)
        out["before_boxes"] = await _page(live, "__scene.screenBoxes(%s, %s)" % (kjs, ojs)) or {}
        lap("before")
        await _page(live, after_js)
        flags["back"] = True

    async def shoot_place():
        # A PLACEMENT IS PHOTOGRAPHED HIDDEN FIRST, then shown until the engine has really drawn it.
        # PlayCanvas links a new material's shader in parallel and skips the mesh until it has;
        # measured on rot-rush: two frames after a place only the new box's SHADOW was in the picture,
        # and a "did its box change" pixel test passed on the shadow alone. So the page is asked
        # (__scene.drawReady: every shader the mesh made is linked) for up to 1.5 s. three.js draws a
        # new mesh on its first frame and answers ready at once.
        flags["before"] = True
        await _page(live, before_js)
        await _page(live, "__scene.frames(2)")
        out["before"] = await capture(live)
        out["before_boxes"] = await _page(live, "__scene.screenBoxes(%s, %s)" % (kjs, ojs)) or {}
        lap("before")
        await _page(live, after_js)
        flags["back"] = True
        await _page(live, "__scene.frames(2)")
        end, polls, ready = time.monotonic() + SETTLE_S, 0, {}
        while True:
            ready = await _page(live, "__scene.drawReady(%s, %s)" % (kjs, ojs)) or {}
            if not isinstance(ready, dict) or ready.get("ready") or time.monotonic() > end:
                break
            await _page(live, "__scene.frames(1)")
            polls += 1
        if polls:
            await _page(live, "__scene.frames(1)")    # the frame that linked it drew it; one more to be sure
            out["settle_frames"] = polls
        if isinstance(ready, dict) and not ready.get("ready"):
            out["notes"].append("the new thing was still not drawn after %.1f s (%s shader(s) not linked yet) — "
                                "the after frame may show only its shadow" % (SETTLE_S, ready.get("pending")))
        out["after"] = await capture(live)
        out["after_boxes"] = await _page(live, "__scene.screenBoxes(%s, %s)" % (kjs, ojs)) or {}
        lap("after")

    try:
        await asyncio.wait_for(shoot(), LOOK_BUDGET_S)
    except asyncio.TimeoutError:
        out["errors"].append("the look took longer than %d s and was stopped" % LOOK_BUDGET_S)
    except Exception as ex:                             # noqa: BLE001
        out["errors"].append(str(ex)[:300])
    finally:
        # THE PUT-BACK AND THE RELEASE RUN WHATEVER HAPPENED ABOVE, in this order: the change first
        # (the game must be left as the edit left it), then the reveals, then the camera.
        if flags["before"] and not flags["back"]:
            try:
                await _page(live, after_js)
                flags["back"] = True
            except Exception as ex:                     # noqa: BLE001
                out["errors"].append("the change could not be put back after the before frame (%s) — "
                                     "the object may be showing its old state; repeat the edit" % str(ex)[:160])
        try:
            await _page(live, "__scene.unreveal()")
        except Exception as ex:                         # noqa: BLE001
            out["errors"].append("unreveal: %s" % str(ex)[:120])
        try:
            out["end"] = await _page(live, "__scene.viewEnd()") or {}
        except Exception as ex:                         # noqa: BLE001
            out["errors"].append("the view could not be handed back: %s" % str(ex)[:160])
        lap("release")
    try:
        out["stale"] = await stale_async(live, project, e)
    except Exception:                                   # noqa: BLE001
        out["stale"] = None
    lap("stale")
    return out


def _look(project: str, e: dict, keys: list, boxes: list, angle: str, before_js: str, after_js: str,
          opts: dict, captions: tuple, kind: str, expect: Optional[dict] = None) -> dict:
    place = kind == "place"
    t0 = time.time()
    if not e.get("target"):
        return {"ok": False, "error": "no live tab to photograph"}
    lh = union(boxes)
    if lh is None:
        return {"ok": False, "error": "nothing here has a place to frame"}
    lo, hi = lh

    async def go():
        t_s = time.monotonic()
        ws, live = await L._session(e)
        try:
            got = await _look_session(live, project, e, keys, lo, hi, angle, before_js, after_js, opts, place)
            got.setdefault("split", {})["session"] = int((time.monotonic() - t_s) * 1000) - sum(got["split"].values())
            return got
        finally:
            await ws.close()

    try:
        raw = L._run(go) or {}
    except Exception as ex:                             # noqa: BLE001
        return {"ok": False, "error": "the look could not reach the tab: %s" % str(ex)[:300],
                "ms": int((time.time() - t0) * 1000)}
    return _assemble_look(project, raw, keys, angle, captions, kind, t0, expect)


def _assemble_look(project: str, raw: dict, keys: list, angle: str, captions: tuple, kind: str, t0: float,
                   expect: Optional[dict] = None) -> dict:
    """The sheet and the answer, from what the session brought back."""
    from PIL import Image
    errors, notes = list(raw.get("errors") or []), list(raw.get("notes") or [])
    begin, end = raw.get("begin") or {}, raw.get("end") or {}
    look: dict = {}
    if not raw.get("after") or not raw.get("before"):
        look = {"ok": False, "error": "; ".join(errors) or "no frames were taken"}
    else:
        stamp = int(time.time() * 1000)
        d = _out_dir(project)
        bpath, apath, spath = d / ("look-%d-before.jpg" % stamp), d / ("look-%d-after.jpg" % stamp), d / ("look-%d.png" % stamp)
        bpath.write_bytes(raw["before"])
        apath.write_bytes(raw["after"])
        b_img, a_img = Image.open(io.BytesIO(raw["before"])), Image.open(io.BytesIO(raw["after"]))
        bb = (raw.get("before_boxes") or {}).get("items") or {}
        ab = (raw.get("after_boxes") or {}).get("items") or {}
        per = {k: {"before": (bb.get(k) or {}).get("box"), "after": (ab.get(k) or {}).get("box")} for k in keys}
        title, cap_b, cap_a = captions
        # DID IT STICK? What the edit set, against what the object holds at the after frame. A game
        # that drives an object every frame puts it straight back, and then the two frames match
        # while the numbers say "moved 1.00 m" — the caption says which of the two to believe.
        back = {}
        for k, want in (expect or {}).items():
            fields = drifted(want, (ab.get(k) or {}).get("local"))
            if fields:
                back[k] = fields
        if back:
            cap_a = "after · the game put %s back at once (its code drives %s) · the edit said: %s" % (
                ", ".join(back), "/".join(sorted({f for v in back.values() for f in v})),
                cap_a[len("after · "):] if cap_a.startswith("after · ") else cap_a)
        who = "the game's own camera" if angle == "player" else "Studio camera, %s" % angle
        sheet = compose_pair(b_img, a_img, [(k, per[k]["before"]) for k in keys], [(k, per[k]["after"]) for k in keys],
                             cap_b, cap_a, "%s   ·   %s   ·   before | after" % (title, who))
        sheet.save(spath, optimize=False, compress_level=3)
        look = {"ok": True, "sheet": str(spath), "before": str(bpath), "after": str(apath),
                "camera": _cam_answer(angle, raw.get("shot"), begin.get("game")),
                "box": {"before": _union01([per[k]["before"] for k in keys]), "after": _union01([per[k]["after"] for k in keys])},
                "in_frame": any(per[k]["after"] for k in keys)}
        if len(keys) > 1:
            look["boxes"] = per
        if back:
            look["drift"] = back
            notes.append("the game put %s back right after the edit (%s): its own code sets it every frame, so the "
                         "edit does not stick in the running game and the after frame shows where the game keeps "
                         "it — change the code that drives it, or edit a parent it does not drive" % (
                             ", ".join(back), "; ".join("%s: %s" % (k, "/".join(v)) for k, v in back.items())))
        if not look["in_frame"]:
            notes.append(("%s is off screen in the player's view — look:\"3q\" frames it with the Studio camera"
                          if angle == "player" else "%s did not land in the frame") % ", ".join(keys[:3]))
        if errors:
            notes.extend(errors)
    if raw.get("revealed"):
        notes.append("switched on for the picture and back off after: %s (the game had it off)" % ", ".join(raw["revealed"][:4]))
    same = _same_pose((begin or {}).get("game"), (end or {}).get("game"))
    look["game_pose_same"] = same
    if same is False:
        notes.append("the game's own camera moved during the look (a follow camera tracking a moving player does)")
    if end.get("view"):
        look["view"] = end["view"]
    if raw.get("stale"):
        look["stale"] = raw["stale"]
    if notes:
        look["note"] = "; ".join(dict.fromkeys(str(n) for n in notes if n))
    look["ms"] = int((time.time() - t0) * 1000)
    # Where the time went, when there was a lot of it: a slow look should say which step was slow.
    if look["ms"] > SLOW_LOOK_MS and raw.get("split"):
        split = dict(raw["split"])
        split["sheet"] = max(0, look["ms"] - sum(split.values()))
        look["split_ms"] = split
    return look


def look_edit(project: str, e: dict, applied: list, angle: str, scene: Optional[int] = None) -> dict:
    """The picture of an edit (or a batch of them) that live_scene has just applied."""
    rows = [r for r in applied if isinstance(r, dict) and r.get("ok") and r.get("key")]
    if not rows:
        return {"ok": False, "error": "nothing was edited"}
    if any(r.get("redo") is None or r.get("undo") is None for r in rows):
        return {"ok": False, "error": "the page did not hand back the states to photograph (an older page script)"}
    if not e.get("target"):
        return {"ok": False, "error": "no live tab to photograph"}
    # ONLY WHAT CHANGED IS PHOTOGRAPHED. A row can succeed and change nothing (a drop with nothing
    # below it); put back and "re-applied", it wound back what the game animates on that object.
    rows = [r for r in rows if r.get("changed")]
    if not rows:
        return {"ok": False, "error": "nothing changed, so there is no before and after to show (see the edit's note) — "
                                      "POST /api/live/shot {project, target} shows it as it is"}
    keys = list(dict.fromkeys(str(r["key"]) for r in rows))
    boxes = [box_of(r.get("before")) for r in rows] + [box_of(r.get("after")) for r in rows]
    undo = [{"key": r["key"], "local": partial_local(r["undo"], r.get("changed"))} for r in reversed(rows)]
    redo = [{"key": r["key"], "local": partial_local(r["redo"], r.get("changed"))} for r in rows]
    opts = {"scene": int(scene)} if scene is not None else {}
    expect = {}
    for it in redo:                                     # in order: a key edited twice ends as its last edit
        expect[str(it["key"])] = it["local"]
    return _look(project, e, keys, boxes, angle,
                 "__scene.restore(%s, %s)" % (json.dumps(undo), json.dumps(opts)),
                 "__scene.restore(%s, %s)" % (json.dumps(redo), json.dumps(opts)),
                 opts, edit_captions(rows), "edit", expect)


def look_place(project: str, e: dict, res: dict, angle: str, scene: Optional[int] = None) -> dict:
    """The picture of a placement: the new object, and the same view with it hidden."""
    key = res.get("key")
    if not key:
        return {"ok": False, "error": "the placement has no key to frame"}
    after = res.get("after") or {}
    box = box_of(after)
    if box is None:
        return {"ok": False, "error": "the placement has no place to frame"}
    shown_ = bool(after.get("visible", True))
    opts = {"scene": int(scene)} if scene is not None else {}
    return _look(project, e, [str(key)], [box], angle,
                 "__scene.restore(%s, %s)" % (json.dumps([{"key": key, "local": {"on": False}}]), json.dumps(opts)),
                 "__scene.restore(%s, %s)" % (json.dumps([{"key": key, "local": {"on": shown_}}]), json.dumps(opts)),
                 opts, place_captions(res), "place")


# ---------------------------------------------------------------------------------------- /shot
def _num_size(size) -> tuple[Optional[tuple], str]:
    if size in (None, [], ""):
        return None, ""
    try:
        w, h = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError):
        return None, "size is [width, height] in pixels"
    if not (64 <= w <= 4096 and 64 <= h <= 4096):
        return None, "size is [width, height], each 64..4096 pixels"
    return (w, h), ""


def cover(img, box01, w: int, h: int):
    """The frame scaled to cover w x h and cut to it from the middle, and the box moved with it.
    Cut rather than squeezed: a squeezed game picture lies about every proportion in it."""
    from PIL import Image
    W, H = img.size
    s = max(w / float(W), h / float(H))
    nw, nh = max(w, int(round(W * s))), max(h, int(round(H * s)))
    ox, oy = (nw - w) / 2.0, (nh - h) / 2.0
    out = img.resize((nw, nh), Image.LANCZOS).crop((int(ox), int(oy), int(ox) + w, int(oy) + h))
    nb = None
    if box01:
        cl = lambda v: max(0.0, min(1.0, v))
        nb = [round(cl((box01[0] * nw - ox) / w), 3), round(cl((box01[1] * nh - oy) / h), 3),
              round(cl((box01[2] * nw - ox) / w), 3), round(cl((box01[3] * nh - oy) / h), 3)]
        if nb[2] <= nb[0] or nb[3] <= nb[1]:
            nb = None
    return out, nb


def shot(project: str, path: str = "", full: bool = False, target: str = "", angle: str = "",
         distance: Optional[float] = None, box: bool = True, size=None, scene: Optional[int] = None) -> dict:
    """/api/live/shot. Without `target`, exactly the shot it always was (plus `stale` when the page
    runs old code). With one, a Studio camera framed on its world box from `angle`, the capture, the
    box drawn, and the camera handed back."""
    target = str(target or "").strip()
    if not target:
        # Under the game's view lock, like a look: taken while a look had the Studio camera up, the
        # plain shot showed the look's camera, or the state before the edit.
        from .live_scene import _lock_for
        lk = _lock_for(project)
        if not lk.acquire(timeout=60):
            return {"ok": False, "error": "another call is using this game's camera (a look, a shot or a watch) — "
                                          "try again in a moment"}
        try:
            res = L.shot(project, path, full)
            if isinstance(res, dict) and res.get("ok"):
                try:
                    st = stale_now(project, L._entry(project))
                    if st:
                        res["stale"] = st
                except Exception:                       # noqa: BLE001
                    pass
            return res
        finally:
            lk.release()
    bad = L._guard(project)
    if bad:
        return bad
    angle = str(angle or "3q").strip().lower()
    if angle not in LOOK_WORDS:
        return {"ok": False, "error": 'angle is one of "3q", "front", "side", "back", "top", "player"'}
    if distance is not None:
        try:
            distance = float(distance)
        except (TypeError, ValueError):
            return {"ok": False, "error": "distance is metres from the target's middle"}
        if not (math.isfinite(distance) and distance > 0):
            return {"ok": False, "error": "distance is metres, more than 0"}
    wh, err = _num_size(size)
    if err:
        return {"ok": False, "error": err}
    e = L._entry(project)
    if not e.get("url"):
        return {"ok": False, "error": "no game is open for this project — POST /api/live/open first"}
    t0 = time.time()
    opts = {"scene": int(scene)} if scene is not None else {}
    ojs = json.dumps(opts)

    async def go():
        ws, live = await L._session(e)
        out: dict = {"errors": [], "notes": []}
        try:
            await _prepare(live, e)
            got = await _page(live, "__scene.boxes(%s, %s)" % (json.dumps([target]), ojs)) or {}
            if not got.get("ok"):
                from .live_scene import _page_refusal
                out["refusal"] = got if got.get("error") else _page_refusal(got)
                return out
            item = (got.get("items") or [{}])[0]
            if item.get("error"):
                out["refusal"] = {"ok": False, "error": item["error"], "like": item.get("like"), "keys": item.get("keys")}
                return out
            key = str(item["key"])
            out["key"] = key
            lh = box_of(item)
            if lh is None:
                out["refusal"] = {"ok": False, "error": "%s has no place to frame (no body and no position)" % key}
                return out
            begin = await begin_view(live)
            if not begin.get("ok"):
                out["refusal"] = {"ok": False, "error": begin.get("error") or "the view could not start"}
                return out
            out["begin"] = begin
            try:
                async def shoot():
                    if angle == "player":
                        await _page(live, "__scene.viewGame()")
                    else:
                        out["shot"] = await aim(live, begin, lh[0], lh[1], angle, distance)
                    out["revealed"] = await _page(live, "__scene.reveal(%s, %s)" % (json.dumps([key]), ojs)) or []
                    await _page(live, "__scene.frames(2)")
                    out["png"] = await capture(live, fmt="png", full=full)
                    out["boxes"] = await _page(live, "__scene.screenBoxes(%s, %s)" % (json.dumps([key]), ojs)) or {}
                    got_iv = await _page(live, "__scene.inView(8, %s)" % ojs)
                    out["in_view"] = [str(k) for k in got_iv][:8] if isinstance(got_iv, list) else []
                await asyncio.wait_for(shoot(), LOOK_BUDGET_S)
            except Exception as ex:                     # noqa: BLE001
                out["errors"].append(str(ex)[:300] or type(ex).__name__)
            finally:
                try:
                    await _page(live, "__scene.unreveal()")
                except Exception:                       # noqa: BLE001
                    pass
                try:
                    out["end"] = await _page(live, "__scene.viewEnd()") or {}
                except Exception as ex:                 # noqa: BLE001
                    out["errors"].append("the view could not be handed back: %s" % str(ex)[:160])
            try:
                out["stale"] = await stale_async(live, project, e)
            except Exception:                           # noqa: BLE001
                pass
            return out
        finally:
            await ws.close()

    from .live_scene import _lock_for
    try:
        with _lock_for(project):
            raw = L._run(go) or {}
    except Exception as ex:                             # noqa: BLE001
        return {"ok": False, "error": str(ex)[:600]}
    if raw.get("refusal"):
        return raw["refusal"]
    if not raw.get("png"):
        return {"ok": False, "error": "; ".join(raw.get("errors") or []) or "no frame was taken",
                "target": raw.get("key")}
    from PIL import Image, ImageDraw
    img = Image.open(io.BytesIO(raw["png"])).convert("RGB")
    b01 = (((raw.get("boxes") or {}).get("items") or {}).get(raw["key"]) or {}).get("box")
    if wh:
        img, b01 = cover(img, b01, wh[0], wh[1])
    if box and b01:
        outline(ImageDraw.Draw(img), b01, 0, 0, img.width, img.height, GREEN, raw["key"], _font(13))
    out_path = Path(path) if path else _out_dir(project) / ("shot-%d.png" % int(time.time() * 1000))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, optimize=False, compress_level=3)
    begin, end = raw.get("begin") or {}, raw.get("end") or {}
    ans: dict = {"ok": True, "path": str(out_path), "bytes": out_path.stat().st_size, "target": raw["key"],
                 "camera": _cam_answer(angle, raw.get("shot"), begin.get("game")),
                 "box": b01, "in_view": raw.get("in_view") or [], "size": [img.width, img.height],
                 "game_pose_same": _same_pose(begin.get("game"), end.get("game"))}
    notes = list(raw.get("errors") or [])
    if not b01:
        notes.append("%s is not in the frame" % raw["key"] + (" of the player's view" if angle == "player" else ""))
    if raw.get("revealed"):
        notes.append("switched on for the picture and back off after: %s" % ", ".join(raw["revealed"][:4]))
    if end.get("view"):
        ans["view"] = end["view"]
    if raw.get("stale"):
        ans["stale"] = raw["stale"]
    if notes:
        ans["note"] = "; ".join(notes)
    ans["ms"] = int((time.time() - t0) * 1000)
    return ans


# ---------------------------------------------------------------------------------------- /watch
def motion(a, b, thresh: int = 22) -> tuple[float, Optional[list]]:
    """(percent of the frame that changed, the box it changed in, 0..1) between two frames.

    The percent is the navigator's own `changed_pct` (64x36 grey, >14 levels), so a watch and a
    `goto pose:"last"` mean the same thing by it. The box comes from a finer 160x90 grid with an
    opening (min then max filter), so one flickering pixel of noise draws no box."""
    from PIL import Image, ImageChops, ImageFilter
    from .live_navigate import changed_pct
    pct = changed_pct(a, b)
    ga = a.convert("L").resize((160, 90), Image.BILINEAR)
    gb = b.convert("L").resize((160, 90), Image.BILINEAR)
    m = ImageChops.difference(ga, gb).point(lambda v: 255 if v > thresh else 0)
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    bb = m.getbbox()
    if not bb:
        return pct, None
    return pct, [round(bb[0] / 160.0, 3), round(bb[1] / 90.0, 3), round(bb[2] / 160.0, 3), round(bb[3] / 90.0, 3)]


def _f2(v: float) -> str:
    s = "%.2f" % float(v)
    return s[1:] if s.startswith("0.") else s


def watch_findings(times: list, changes: list, boxes: list, input_at: Optional[float] = None,
                   target_box: Optional[list] = None, target: str = "", still: float = 0.5) -> list:
    """What a strip of frames says, in the words an agent acts on. Pure. `changes[i]` and `boxes[i]`
    compare frame i with frame i-1 (the first is 0 and None)."""
    n = len(times)
    # A frame moved when the noise-filtered mask found a region, or when enough of the frame changed.
    # The percent alone missed the proof player at the top of its jump in the game's wide view: 0.1%
    # of the frame, and plainly in the air.
    moving = [i for i in range(1, n) if (changes[i] or 0) >= still or (i < len(boxes) and boxes[i])]
    out = []
    peak = max(range(n), key=lambda i: changes[i] or 0) if n else 0
    span = times[-1] if times else 0
    if not moving:
        out.append("STILL: nothing moved in %.1f s (largest change %.1f%%)" % (span, max(changes or [0])))
        if input_at is not None:
            out.append("NO RESPONSE: nothing changed after the input at t=%.2f s" % input_at)
        return out
    ub = _union01([boxes[i] for i in moving])
    first = moving[0]
    where = ("in the box x %s–%s, y %s–%s" % (_f2(ub[0]), _f2(ub[2]), _f2(ub[1]), _f2(ub[3]))) if ub else "across the frame"
    out.append("moved %s from t=%.2f s (peak %.1f%% at t=%.2f s)" % (where, times[first], changes[peak], times[peak]))
    if max(changes) > 60:
        big = [i for i in range(1, n) if changes[i] > 60]
        out.append("the whole view changed at t=%.2f s (a cut, a reload, or the camera moved)" % times[big[0]])
    elif ub and ub[2] - ub[0] >= 0.9 and ub[3] - ub[1] >= 0.6 and not target_box:
        # Measured on rot-rush: after Space its follow camera bobbed with the runner, and every frame
        # "changed" from edge to edge — one thing's motion drowned in the camera's.
        out.append("the change spans the whole picture: the game's own camera is moving (a follow camera) — "
                   "give target to watch from a Studio camera that holds still")
    if input_at is not None:
        pre = [i for i in moving if times[i] <= input_at + 1e-6]
        post = [i for i in moving if times[i] > input_at + 1e-6]
        if pre:
            out.append("already moving before the input (t=%.2f s): the game animates there by itself" % times[pre[0]])
        if post:
            out.append("first change after the input at t=%.2f s: t=%.2f s" % (input_at, times[post[0]]))
        else:
            out.append("NO RESPONSE: nothing new changed after the input at t=%.2f s" % input_at)
    if target_box and ub:
        ix0, iy0 = max(ub[0], target_box[0]), max(ub[1], target_box[1])
        ix1, iy1 = min(ub[2], target_box[2]), min(ub[3], target_box[3])
        inside = ix1 > ix0 and iy1 > iy0
        out.append("the motion %s %s's box" % ("overlaps" if inside else "is outside", target or "the target"))
    return out


def watch(project: str, seconds: float = 2.0, frames: int = 6, target: str = "", angle: str = "",
          input=None, at_ms: int = 0, scene: Optional[int] = None) -> dict:
    """/api/live/watch: frames of the LIVE tab over real time — the agent's own unsaved state — after
    framing the camera on `target` and sending `input` at `at_ms`. One sheet, change per frame, and
    findings in words; the camera is handed back at the end."""
    bad = L._guard(project)
    if bad:
        return bad
    try:
        seconds = float(seconds)
        frames = int(frames)
        at_ms = int(at_ms or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "seconds, frames and at_ms are numbers"}
    if not (math.isfinite(seconds) and 0.2 <= seconds <= 10.0):
        return {"ok": False, "error": "seconds is 0.2 to 10"}
    if not 2 <= frames <= 16:
        return {"ok": False, "error": "frames is 2 to 16"}
    if not 0 <= at_ms <= int(seconds * 1000):
        return {"ok": False, "error": "at_ms is when the input goes in: 0 to seconds x 1000"}
    events = input if isinstance(input, list) else ([] if input in (None, "", {}) else None)
    if events is None or any(not isinstance(ev, dict) for ev in events):
        return {"ok": False, "error": "input is a list of events, the shape /api/live/input takes"}
    if len(events) > 200:
        return {"ok": False, "error": "at most 200 input events in one watch"}
    target = str(target or "").strip()
    angle = str(angle or ("3q" if target else "")).strip().lower()
    if target and angle not in LOOK_WORDS:
        return {"ok": False, "error": 'angle is one of "3q", "front", "side", "back", "top", "player"'}
    e = L._entry(project)
    if not e.get("url"):
        return {"ok": False, "error": "no game is open for this project — POST /api/live/open first"}
    dev = L._device_of(e.get("device") or "desktop", e.get("width") or 0, e.get("height") or 0)
    t_all = time.time()
    opts = {"scene": int(scene)} if scene is not None else {}
    ojs = json.dumps(opts)
    times_want = [seconds * i / (frames - 1) for i in range(frames)]

    async def go():
        ws, live = await L._session(e)
        ws2 = None
        out: dict = {"errors": [], "caps": []}
        begun = False
        try:
            await _prepare(live, e)
            vp = await live.raw("[innerWidth, innerHeight, devicePixelRatio || 1]") or [1280, 720, 1]
            vw, vh, dpr = int(vp[0] or 1280), int(vp[1] or 720), float(vp[2] or 1)
            scale = min(1.0, 640.0 / max(1.0, vw * dpr))
            key = ""
            if target:
                got = await _page(live, "__scene.boxes(%s, %s)" % (json.dumps([target]), ojs)) or {}
                if not got.get("ok"):
                    from .live_scene import _page_refusal
                    out["refusal"] = got if got.get("error") else _page_refusal(got)
                    return out
                item = (got.get("items") or [{}])[0]
                if item.get("error"):
                    out["refusal"] = {"ok": False, "error": item["error"], "like": item.get("like"), "keys": item.get("keys")}
                    return out
                key = str(item["key"])
                out["key"] = key
                if box_of(item) is None:
                    out["refusal"] = {"ok": False, "error": "%s has no place to frame (no body and no position)" % key}
                    return out
                begin = await begin_view(live)
                if not begin.get("ok"):
                    out["refusal"] = {"ok": False, "error": begin.get("error") or "the view could not start"}
                    return out
                begun = True
                out["begin"] = begin
                lh = box_of(item)
                if angle == "player":
                    await _page(live, "__scene.viewGame()")
                else:
                    # Twice the target's radius: what is watched usually moves, and a thing framed
                    # tight leaves its own frame (the proof player's jump did, for half of it).
                    out["shot"] = await aim(live, begin, lh[0], lh[1], angle, grow=WATCH_GROW)
                out["revealed"] = await _page(live, "__scene.reveal(%s, %s)" % (json.dumps([key]), ojs)) or []
                await _page(live, "__scene.frames(2)")
                out["boxes"] = await _page(live, "__scene.screenBoxes(%s, %s)" % (json.dumps([key]), ojs)) or {}
            if events:
                # A second connection to the same tab, so a held key does not hold up the camera: one
                # CDP socket answers one call at a time (review._Cdp).
                ws2, live2 = await L._session(e)
            else:
                live2 = None
            t0 = time.monotonic()

            async def shoot():
                for t in times_want:
                    wait = t0 + t - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    ts = time.monotonic() - t0
                    out["caps"].append((ts, await capture(live, quality=82, scale=scale, vw=vw, vh=vh)))

            async def feed():
                wait = t0 + at_ms / 1000.0 - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                out["input_at"] = time.monotonic() - t0
                for ev in events:
                    await L._one_input(live2, ev, dev)
                out["input_done"] = time.monotonic() - t0

            jobs = [shoot()] + ([feed()] if events else [])
            got = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), seconds + 20.0)
            for g in got:
                if isinstance(g, BaseException):
                    out["errors"].append(str(g)[:300] or type(g).__name__)
        except Exception as ex:                         # noqa: BLE001
            out["errors"].append(str(ex)[:300] or type(ex).__name__)
        finally:
            if ws2 is not None:
                try:
                    await ws2.close()
                except Exception:                       # noqa: BLE001
                    pass
            if begun:
                try:
                    await _page(live, "__scene.unreveal()")
                except Exception:                       # noqa: BLE001
                    pass
                try:
                    out["end"] = await _page(live, "__scene.viewEnd()") or {}
                except Exception as ex:                 # noqa: BLE001
                    out["errors"].append("the view could not be handed back: %s" % str(ex)[:160])
            try:
                out["stale"] = await stale_async(live, project, e)
            except Exception:                           # noqa: BLE001
                pass
            await ws.close()
        return out

    from .live_scene import _lock_for
    try:
        with _lock_for(project):
            raw = L._run(go) or {}
    except Exception as ex:                             # noqa: BLE001
        return {"ok": False, "error": str(ex)[:600]}
    if raw.get("refusal"):
        return raw["refusal"]
    caps = raw.get("caps") or []
    if len(caps) < 2:
        return {"ok": False, "error": "; ".join(raw.get("errors") or []) or "fewer than two frames were taken"}
    from PIL import Image
    stamp = int(time.time() * 1000)
    d = _out_dir(project)
    imgs, paths, times = [], [], []
    for i, (ts, data) in enumerate(caps):
        p = d / ("watch-%d-%02d.jpg" % (stamp, i))
        p.write_bytes(data)
        paths.append(str(p))
        times.append(round(ts, 3))
        imgs.append(Image.open(io.BytesIO(data)).convert("RGB"))
    changes, mboxes = [0.0], [None]
    for i in range(1, len(imgs)):
        pct, mb = motion(imgs[i - 1], imgs[i])
        changes.append(pct)
        mboxes.append(mb)
    key = raw.get("key") or ""
    tbox = (((raw.get("boxes") or {}).get("items") or {}).get(key) or {}).get("box") if key else None
    input_at = raw.get("input_at")
    mark = -1
    if input_at is not None:
        after_in = [i for i, t in enumerate(times) if t >= input_at - 1e-6]
        mark = after_in[0] if after_in else len(times) - 1
    labels = ["t=%.2f s · %s" % (t, ("Δ %.1f%%" % changes[i]) if i else "start") +
              (" · input" if i == mark else "") for i, t in enumerate(times)]
    findings = watch_findings(times, changes, mboxes, input_at, tbox, key)
    what = ("watch · %s · %s" % (key, "the game's camera" if angle == "player" else "Studio camera, " + angle)) if key \
        else "watch · the game's own view"
    title = "%s · %.1f s, %d frames%s · %smagenta: changed since the frame before" % (
        what, seconds, len(imgs), (" · input at t=%.2f s" % input_at) if input_at is not None else "",
        ("green: %s at t=0, " % key) if key else "")
    sheet = compose_grid(imgs, labels, title, tbox, mboxes, mark, key)
    spath = d / ("watch-%d.jpg" % stamp)
    sheet.save(spath, quality=90)
    ans: dict = {"ok": True, "sheet": str(spath), "frames": paths, "times": times,
                 "changes": [round(c, 1) for c in changes], "findings": findings}
    if key:
        begin, end = raw.get("begin") or {}, raw.get("end") or {}
        ans["target"] = key
        ans["camera"] = _cam_answer(angle, raw.get("shot"), begin.get("game"))
        ans["box"] = tbox
        ans["game_pose_same"] = _same_pose(begin.get("game"), end.get("game"))
        if end.get("view"):
            ans["view"] = end["view"]
    if input_at is not None:
        ans["input_at"] = round(input_at, 3)
    notes = list(raw.get("errors") or [])
    if raw.get("revealed"):
        notes.append("switched on for the watch and back off after: %s" % ", ".join(raw["revealed"][:4]))
    if len(caps) < frames:
        notes.append("%d of %d frames were taken" % (len(caps), frames))
    if notes:
        ans["note"] = "; ".join(notes)
    if raw.get("stale"):
        ans["stale"] = raw["stale"]
    ans["ms"] = int((time.time() - t_all) * 1000)
    return ans

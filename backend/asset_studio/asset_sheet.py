"""One picture of a project's assets, instead of one picture per asset.

`/api/engine/assets` lists what a game HAS - builders, spec rows, models, images - and
`/api/engine/thumbs` draws them, one PNG each. An agent choosing what to place, or checking what a
builder really makes, then has to open N images, and every one of them is a read it pays for. A
sheet is ONE image: a numbered, labelled grid, each tile the asset's own thumbnail with its name
and file under it, and an asset that could not be drawn is a tile that says why in words - a
blank square would read as "draws nothing", which is a different fault.

The pictures come from the same `live.thumbs` the Library uses, cached on the builder file's
mtime, so a thumbnail made for one is reused by the other and a changed builder is redrawn. The
sheet is sized to what a vision model keeps whole - 1568 px on the long side and 1.15 Mpx - because
a sheet it scales down is a sheet whose labels it can no longer read. Tiles shrink to fit the
budget before any asset is dropped.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

SHEET_MAX = 1568              # the long edge a vision model keeps without scaling
SHEET_AREA = 1_150_000        # and the area; past either, the model sees a smaller sheet
MAX_CELLS = 64
MIN_TILE = 64
PAD = 8
HEAD = 48                     # title and subtitle
LABEL = 36                    # name and file under each tile
MIN_WIDTH = 440               # room for the title and subtitle, however few tiles there are

BG = (14, 16, 22)
TILE_BG = (22, 26, 34)
FAIL_BG = (48, 24, 28)
INK = (232, 236, 244)
DIM = (126, 138, 158)
FAIL_INK = (255, 150, 150)
EDGE = (38, 43, 54)

_DRAWABLE = ("code", "spec", "model", "image", "texture")


def _font(size: int):
    try:
        from .review import _font as rf
        return rf(size)
    except Exception:                                    # pragma: no cover - defensive
        from PIL import ImageFont
        return ImageFont.load_default()


def _slug(project: str) -> str:
    try:
        from .live import _slug as ls
        return ls(project)
    except Exception:                                    # pragma: no cover - defensive
        return hashlib.sha1(str(project).lower().encode()).hexdigest()[:12]


def _ids_of(ids) -> list[str]:
    """`ids` as a list: a JSON array, or ids split on commas or newlines."""
    if isinstance(ids, (list, tuple)):
        return [str(i).strip() for i in ids if str(i).strip()]
    s = str(ids or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            return [str(i).strip() for i in json.loads(s) if str(i).strip()]
        except ValueError:
            pass
    return [p.strip() for p in s.replace("\n", ",").split(",") if p.strip()]


def plan(n: int, want: int) -> tuple[int, int]:
    """(columns, tile px) - the biggest tile, no bigger than asked, whose sheet fits the budget.

    Weighed against the SHAPE, not only the tile: the first version took a 4-wide, 1448-tall strip
    for twenty-four assets because its tiles were 4 px bigger than a 6 x 4 grid's. A sheet near 4:3
    is the one that reads as a grid; a few pixels of tile are not worth a column of scrolling."""
    n = max(1, int(n))
    want = max(MIN_TILE, int(want))
    best = None
    for cols in range(1, n + 1):
        rows = (n + cols - 1) // cols
        t = want
        while t >= MIN_TILE:
            w = PAD + cols * (t + PAD)
            h = HEAD + rows * (t + LABEL + PAD) + PAD
            if w <= SHEET_MAX and h <= SHEET_MAX and w * h <= SHEET_AREA:
                break
            t -= 4
        if t < MIN_TILE:
            continue
        w = PAD + cols * (t + PAD)
        h = HEAD + rows * (t + LABEL + PAD) + PAD
        score = (t * (1.0 - 0.12 * abs(math.log((w / float(h)) / 1.33))), t)
        if best is None or score > best[0]:
            best = (score, cols, t)
    if best is None:
        cols = max(1, int(math.ceil(math.sqrt(n))))
        return cols, MIN_TILE
    return best[1], best[2]


def _fit_text(d, text: str, font, width: int, tail: bool = False) -> str:
    """The text, cut with an ellipsis to fit `width` - from the end, or from the start (`tail`)
    for a path, whose file name is the part worth keeping."""
    text = str(text or "")
    if d.textlength(text, font=font) <= width:
        return text
    ell = "…"
    if tail:
        lo = 0
        while lo < len(text) and d.textlength(ell + text[lo:], font=font) > width:
            lo += 1
        return ell + text[lo:]
    hi = len(text)
    while hi > 0 and d.textlength(text[:hi] + ell, font=font) > width:
        hi -= 1
    return text[:hi] + ell


def _wrap(d, text: str, font, width: int, lines: int) -> list[str]:
    words = str(text or "").replace("\n", " ").split(" ")
    out: list[str] = []
    cur = ""
    for w in words:
        if not w:
            continue
        cand = (cur + " " + w).strip()
        if d.textlength(cand, font=font) <= width:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = w
        while d.textlength(cur, font=font) > width and len(cur) > 1:
            cut = len(cur)
            while cut > 1 and d.textlength(cur[:cut], font=font) > width:
                cut -= 1
            out.append(cur[:cut])
            cur = cur[cut:]
        if len(out) >= lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    if len(out) >= lines:
        out = out[:lines]
        out[-1] = _fit_text(d, out[-1] + " …", font, width)
    return out


def compose(cells: list[dict], tile: int, cols: int, title: str, sub: str):
    """The sheet itself. Each cell: {n, name, file, ok, reason?, img?: PIL image}."""
    from PIL import Image, ImageDraw
    n = max(1, len(cells))
    rows = (n + cols - 1) // cols
    # Never narrower than the title needs: one or two tiles made a 208 px sheet whose header was
    # cut to "1 drawn, 0 not · 1 rendered …", which is the line that says what happened.
    w = max(MIN_WIDTH, PAD + cols * (tile + PAD))
    h = HEAD + rows * (tile + LABEL + PAD) + PAD
    sheet = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(sheet)
    f_title, f_sub = _font(17), _font(12)
    f_name, f_file, f_num, f_why = _font(13), _font(11), _font(11), _font(11)
    d.text((PAD, 8), _fit_text(d, title, f_title, w - 2 * PAD), font=f_title, fill=INK)
    d.text((PAD, 29), _fit_text(d, sub, f_sub, w - 2 * PAD), font=f_sub, fill=DIM)
    for i, c in enumerate(cells):
        r, k = divmod(i, cols)
        x = PAD + k * (tile + PAD)
        y = HEAD + r * (tile + LABEL + PAD)
        ok = bool(c.get("ok")) and c.get("img") is not None
        d.rectangle([x, y, x + tile - 1, y + tile - 1], fill=TILE_BG if ok else FAIL_BG)
        if ok:
            im = c["img"].convert("RGB")
            if im.width > tile or im.height > tile:
                im = im.copy()
                im.thumbnail((tile, tile), Image.LANCZOS)
            sheet.paste(im, (x + (tile - im.width) // 2, y + (tile - im.height) // 2))
        else:
            d.text((x + 8, y + 22), "not drawn", font=f_name, fill=FAIL_INK)
            lines = _wrap(d, c.get("reason") or "no reason was given", f_why, tile - 16,
                          max(2, (tile - 48) // 14))
            for j, line in enumerate(lines):
                d.text((x + 8, y + 42 + j * 14), line, font=f_why, fill=(236, 206, 206))
        d.rectangle([x - 1, y - 1, x + tile, y + tile], outline=EDGE)
        # THE NUMBER, so a reader can say "tile 7" and the answer's `cells` says which asset that is.
        num = str(c.get("n") or i + 1)
        bw = int(d.textlength(num, font=f_num)) + 8
        d.rectangle([x, y, x + bw, y + 15], fill=(0, 0, 0))
        d.text((x + 4, y + 1), num, font=f_num, fill=INK)
        d.text((x, y + tile + 4), _fit_text(d, c.get("name") or c.get("id") or "?", f_name, tile),
               font=f_name, fill=INK)
        d.text((x, y + tile + 21), _fit_text(d, c.get("file") or "", f_file, tile, tail=True),
               font=f_file, fill=DIM)
    return sheet


def _select(project: str, root: str, type_: str, q: str, ids, limit: int) -> tuple[list, int, list]:
    """(rows, how many matched, ids asked for that do not exist)."""
    from . import assets_index
    want = _ids_of(ids)
    if want:
        listing = assets_index.list_assets(project)
        by_id = {r["id"]: r for r in (listing.get("items") or [])}
        rows, missing = [], []
        for i in want:
            if i in by_id:
                rows.append(by_id[i])
            else:
                missing.append(i)
                rows.append({"id": i, "name": i.split("#")[-1].split("/")[-1], "file": "",
                             "type": "missing"})
        return rows[:limit], len(want), missing
    listing = assets_index.list_assets(project, type_ or "", "", q or "", False, root or "")
    if not listing.get("ok", True):
        raise RuntimeError(listing.get("error") or "could not list the project's assets")
    items = listing.get("items") or []
    return items[:limit], int(listing.get("total") or len(items)), []


def _images_without_browser(rows: list, size: int) -> dict:
    """Pictures for image and texture rows, when the browser half is refused: a PNG needs none."""
    from PIL import Image
    got = {}
    for r in rows:
        if r.get("type") not in ("image", "texture"):
            continue
        try:
            im = Image.open(str(r.get("path") or ""))
            im.thumbnail((size, size), Image.LANCZOS)
            plate = Image.new("RGB", im.size, TILE_BG)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                plate.paste(im, (0, 0), im)
            else:
                plate.paste(im.convert("RGB"), (0, 0))
            got[r["id"]] = plate
        except Exception:
            continue
    return got


def sheet(project: str, root: str = "", type: str = "", q: str = "", ids="", limit: int = 24,
          size: int = 192, fresh: bool = False) -> dict:
    """Render what is missing, compose one labelled grid PNG, and say what is in each tile."""
    if not project or not Path(project).is_dir():
        return {"ok": False, "error": "no such folder: %s" % project}
    limit = max(1, min(MAX_CELLS, int(limit or 24)))
    size = max(MIN_TILE, min(512, int(size or 192)))
    try:
        rows, total, missing = _select(project, root, type, q, ids, limit)
    except Exception as ex:
        return {"ok": False, "error": "could not read the project's assets: %s" % str(ex)[:300]}
    if not rows:
        return {"ok": False, "error": "no assets matched (root=%r type=%r q=%r ids=%r)"
                % (root, type, q, ids), "total": total}

    t0 = time.monotonic()
    ask = [r["id"] for r in rows if r.get("type") in _DRAWABLE]
    thumbs, errors, rendered, cached, extra = {}, {}, 0, 0, {}
    if ask:
        from . import live
        got = live.thumbs(project, ask, size, limit=len(ask)) or {}
        if got.get("ok"):
            thumbs = got.get("thumbs") or {}
            errors = got.get("errors") or {}
            rendered = int(got.get("rendered") or 0)
            cached = int(got.get("cached") or 0)
            for k in ("heal", "note"):
                if got.get(k):
                    extra[k] = got[k]
        else:
            # The browser half was refused - the forge switched off, no Chrome. Say so on every
            # tile it would have drawn, and still draw the pictures that need no browser at all.
            why = str(got.get("error") or "the thumbnails could not be rendered")
            errors = {i: why for i in ask}
            extra["error_thumbs"] = why
    loose = _images_without_browser([r for r in rows if r["id"] not in thumbs], size) \
        if extra.get("error_thumbs") else {}

    from PIL import Image
    cells, out_cells = [], []
    cols, tile = plan(len(rows), size)
    for i, r in enumerate(rows):
        rid = r["id"]
        c = {"n": i + 1, "id": rid, "name": r.get("name") or rid, "file": r.get("file") or "",
             "type": r.get("type") or "", "row": i // cols, "col": i % cols}
        img = None
        if rid in thumbs:
            try:
                img = Image.open(thumbs[rid])
                img.load()
            except Exception as ex:
                c["reason"] = "the thumbnail file could not be read: %s" % str(ex)[:120]
        elif rid in loose:
            img = loose[rid]
        if img is not None:
            c["ok"] = True
        else:
            c["ok"] = False
            if "reason" not in c:
                if r.get("type") == "missing":
                    c["reason"] = "no asset with this id in the project - list them with /api/engine/assets"
                elif r.get("type") == "audio":
                    c["reason"] = "an audio file: there is nothing to draw"
                elif r.get("type") not in _DRAWABLE:
                    c["reason"] = "a %s has no picture" % (r.get("type") or "row")
                else:
                    c["reason"] = str(errors.get(rid) or "the renderer returned no picture")
        cells.append(dict(c, img=img))
        out_cells.append({k: v for k, v in c.items() if k != "img"})

    drawn = sum(1 for c in out_cells if c["ok"])
    name = Path(project).name + ("/" + root if root else "")
    what = ", ".join(x for x in (type and "type=" + type, q and "q=" + q,
                                  _ids_of(ids) and "%d ids" % len(_ids_of(ids))) if x)
    title = "%s - %d asset%s%s" % (name, len(rows), "" if len(rows) == 1 else "s",
                                    " (%s)" % what if what else "")
    sub = "%d drawn, %d not · %d rendered now, %d from cache · %d px tiles%s" % (
        drawn, len(rows) - drawn, rendered, cached, tile,
        " · first %d of %d" % (len(rows), total) if total > len(rows) else "")

    # NAMED BY WHAT IS IN IT: the same assets with the same pictures make the same file, so asking
    # twice costs nothing, and a redrawn thumbnail (a changed builder) makes a new one.
    stamp = []
    for c in out_cells:
        p = thumbs.get(c["id"], "")
        try:
            m = os.path.getmtime(p) if p else 0
        except OSError:
            m = 0
        stamp.append([c["id"], p, int(m), c.get("reason", "")])
    key = hashlib.sha1(json.dumps([stamp, tile, cols, title], sort_keys=True).encode("utf-8")) \
        .hexdigest()[:16]
    folder = DATA_DIR / "live" / _slug(project) / "sheets"
    dst = folder / ("sheet-%s.png" % key)
    reused = dst.is_file() and not fresh
    if not reused:
        im = compose(cells, tile, cols, title, sub)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".png.part")
        im.save(tmp, format="PNG")
        os.replace(tmp, dst)
    from urllib.parse import quote
    try:
        with Image.open(dst) as done:
            wh = list(done.size)
    except Exception:
        wh = []
    res = {"ok": True, "sheet": str(dst), "url": "/api/engine/sheet?path=" + quote(str(dst)),
           "size": wh, "tile": tile, "cols": cols, "cells": out_cells, "rendered": rendered,
           "cached": cached, "drawn": drawn, "failed": len(rows) - drawn, "total": total,
           "reused": reused, "ms": int((time.monotonic() - t0) * 1000)}
    if total > len(rows):
        res["note"] = ("%d assets matched and the sheet holds the first %d; narrow it with "
                       "type/q/root, or pass ids" % (total, len(rows)))
    if missing:
        res["missing"] = missing
    res.update(extra)
    return res

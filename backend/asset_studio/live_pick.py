# -*- coding: utf-8 -*-
"""Click a thing in the running page, and hand the agent what it is.

The ask: "this button is the wrong colour" / "this panel overlaps on a phone". Saying that in
words costs the agent three round trips — find the component, guess which element it renders,
guess which rule wins. A click answers all three at once, because the PAGE knows: which element
is under that point, which CSS declarations actually apply, and what it looks like.

WHY A SCREENSHOT AND NOT AN IFRAME. The Studio cannot reach into a dev server in an iframe: it is
another origin, so `contentDocument` is null and no listener of ours can run inside it. The live
link already drives a real Chrome over CDP, and that Chrome can read anything. So the user clicks
a PICTURE of the page and the coordinate is sent back through CDP — the same route the review
harness takes, with no change to the project and no extension to install.

COORDINATES ARE FRACTIONS, NEVER PIXELS. The frame was captured at the emulated device's pixel
ratio and is then drawn at whatever width the pane happens to be, so a pixel coordinate would
carry two scale factors and would be wrong the first time either changed. `x` and `y` are 0..1 of
the frame; the page multiplies them by its own `innerWidth`/`innerHeight`, which is the only place
both numbers are known to be right.

A CANVAS GAME IS ONE ELEMENT, and that is not a failure. When the point lands on a canvas the
answer says so and adds the coordinate INSIDE the canvas, in the canvas's own backing-store
pixels, which is what a game's own hit test uses.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from . import live as L
from .config import settings

# The declarations worth reading back. The whole computed style is ~340 properties and most of
# them are the initial value; a wall of `border-block-start-color: rgb(0,0,0)` buries the one
# line that matters. These are the ones a person points at something to ask about.
WANT = ("display", "position", "inset", "width", "height", "min-width", "max-width",
        "margin", "padding", "box-sizing", "overflow",
        "flex", "flex-direction", "align-items", "justify-content", "gap",
        "grid-template-columns", "grid-template-rows",
        "font-family", "font-size", "font-weight", "line-height", "letter-spacing",
        "text-align", "text-transform", "white-space", "color",
        "background-color", "background-image", "border", "border-radius",
        "box-shadow", "text-shadow", "opacity", "transform", "filter", "z-index",
        "cursor", "pointer-events", "visibility")

PICK_JS = r"""
(() => {
  const W = Math.max(1, window.innerWidth), H = Math.max(1, window.innerHeight);
  const px = Math.max(0, Math.min(W - 1, Math.round(__FX__ * W)));
  const py = Math.max(0, Math.min(H - 1, Math.round(__FY__ * H)));
  const el = document.elementFromPoint(px, py);
  if (!el) return JSON.stringify({ ok: false, error: 'nothing is drawn at that point' });

  // A selector you can paste into the console. `id` ends the walk because an id is unique; past
  // that it is tag + position among same-tag siblings, which survives a class rename.
  const sel = (n) => {
    const parts = [];
    let cur = n;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      if (cur.id) { parts.unshift('#' + cur.id); break; }
      let t = cur.tagName.toLowerCase();
      const p = cur.parentElement;
      if (p) {
        const same = Array.prototype.filter.call(p.children, (c) => c.tagName === cur.tagName);
        if (same.length > 1) t += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(t);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };

  const cs = getComputedStyle(el);
  const css = {};
  for (const k of __WANT__) {
    let v = '';
    try { v = cs.getPropertyValue(k); } catch (e) { v = ''; }
    v = (v || '').trim();
    // Drop the initial values: they are noise, and every one of them buries a real line.
    if (!v || v === 'none' || v === 'normal' || v === 'auto' || v === '0px' || v === 'visible'
        || v === 'static' || v === 'rgba(0, 0, 0, 0)' || v === 'baseline') continue;
    css[k] = v.length > 180 ? v.slice(0, 180) + '…' : v;
  }

  const r = el.getBoundingClientRect();
  const attrs = {};
  for (const a of Array.prototype.slice.call(el.attributes || [], 0, 24)) {
    attrs[a.name] = String(a.value || '').slice(0, 200);
  }

  let html = '';
  try { html = el.outerHTML || ''; } catch (e) { html = ''; }
  if (html.length > 2000) html = html.slice(0, 2000) + '\n… (' + html.length + ' chars)';

  // The canvas case. The point is inside a drawing surface, so the useful answer is where inside
  // it — in the backing store's own pixels, which is what a game's hit test compares against.
  let canvas = null;
  if (el.tagName === 'CANVAS' && r.width > 0 && r.height > 0) {
    canvas = { width: el.width, height: el.height,
               x: Math.round((px - r.left) / r.width * el.width),
               y: Math.round((py - r.top) / r.height * el.height) };
  }

  const par = el.parentElement;
  return JSON.stringify({
    ok: true,
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    classes: (el.className && el.className.baseVal !== undefined
              ? el.className.baseVal : String(el.className || '')).split(/\s+/).filter(Boolean).slice(0, 12),
    selector: sel(el),
    parent: par ? sel(par) : '',
    text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 300),
    children: el.children ? el.children.length : 0,
    attrs: attrs,
    css: css,
    html: html,
    canvas: canvas,
    at: { x: px, y: py, w: W, h: H },
    // Page coordinates for the crop: the rect is viewport-relative and CDP clips the document.
    box: { x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
           w: Math.round(r.width), h: Math.round(r.height) },
    title: document.title || '',
    url: location.href
  });
})()
"""


def enabled() -> bool:
    """False = the Inspect tab and this endpoint are off (Settings → "Send a page element")."""
    return settings.get("live_pick", True) is not False


def _crop(data: str, out: Path) -> str:
    """Write the element's own picture, and answer with the path. '' when there is nothing to cut."""
    if not data:
        return ""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(data))
        return str(out)
    except (OSError, ValueError):
        return ""


def pick(project: str, x: float, y: float, pad: int = 8, shot: bool = True) -> dict:
    """What is under that fraction of the frame: the element, the CSS that applies, a picture.

    `x`, `y` are 0..1 of the frame the user clicked on. `pad` widens the crop so the element is
    seen in its surroundings rather than cut to its own edge.
    """
    if not enabled():
        return {"ok": False, "error": 'Sending a page element is off. Turn on '
                                     '"Send a page element" in Settings → Studio engine.'}
    bad = L._guard(project)
    if bad:
        return bad
    try:
        fx = min(1.0, max(0.0, float(x)))
        fy = min(1.0, max(0.0, float(y)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "x and y must be numbers between 0 and 1"}

    e = L._entry(project)
    js = (PICK_JS.replace("__FX__", repr(fx)).replace("__FY__", repr(fy))
                 .replace("__WANT__", json.dumps(list(WANT))))
    pad = max(0, min(200, int(pad or 0)))

    async def go():
        ws, live = await L._session(e)
        try:
            raw = await live.raw(js)
            got = json.loads(raw) if raw else {}
            if not got.get("ok"):
                return got, ""
            crop = ""
            b = got.get("box") or {}
            if shot and int(b.get("w") or 0) > 0 and int(b.get("h") or 0) > 0:
                clip = {"x": max(0, int(b["x"]) - pad), "y": max(0, int(b["y"]) - pad),
                        "width": min(4096, int(b["w"]) + pad * 2),
                        "height": min(4096, int(b["h"]) + pad * 2), "scale": 1}
                try:
                    shotted = await live.call("Page.captureScreenshot",
                                              {"format": "png", "clip": clip,
                                               "captureBeyondViewport": True})
                    crop = shotted.get("data") or ""
                except Exception:
                    crop = ""            # a zero-area or off-screen element: the facts still stand
            return got, crop
        finally:
            await ws.close()

    try:
        got, crop_data = L._run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:800]}
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error") or "the page did not answer"}

    out = L._LIVE_DIR / L._slug(project) / ("pick-%d.png" % int(time.time() * 1000))
    got["crop"] = _crop(crop_data, out)
    # Written HERE and not in the window, so the Studio's Inspect tab and an agent calling this
    # endpoint by hand get the same words. One format, one place to improve it.
    got["prompt"] = as_prompt(got, Path(project).name)
    return got


def as_prompt(got: dict, project_name: str = "") -> str:
    """The pick, written the way it should reach an agent: what, where, and how it is styled.

    The crop is named as a PATH rather than pasted: every CLI the Studio drives can read an image
    off disk, and a base64 blob in the prompt would cost thousands of tokens for a 60x24 button.
    """
    if not got or not got.get("ok"):
        return ""
    bits = []
    head = "<" + got.get("tag", "?")
    if got.get("id"):
        head += " id=\"%s\"" % got["id"]
    cls = got.get("classes") or []
    if cls:
        head += " class=\"%s\"" % " ".join(cls[:8])
    head += ">"
    bits.append("I clicked this element in the running page%s:" %
                (" of " + project_name if project_name else ""))
    bits.append("")
    bits.append("  " + head)
    if got.get("selector"):
        bits.append("  selector: " + got["selector"])
    b = got.get("box") or {}
    if b:
        bits.append("  box: %sx%s at (%s, %s)" % (b.get("w"), b.get("h"), b.get("x"), b.get("y")))
    if got.get("text"):
        bits.append("  text: " + got["text"][:200])
    c = got.get("canvas")
    if c:
        bits.append("  this page draws into ONE canvas — the point is at (%s, %s) of the "
                    "%sx%s backing store" % (c.get("x"), c.get("y"), c.get("width"), c.get("height")))
    css = got.get("css") or {}
    if css:
        bits.append("")
        bits.append("Computed style that applies:")
        for k, v in list(css.items())[:28]:
            bits.append("  %s: %s" % (k, v))
    if got.get("html"):
        bits.append("")
        bits.append("Its HTML:")
        bits.append("```html")
        bits.append(got["html"])
        bits.append("```")
    if got.get("crop"):
        bits.append("")
        bits.append("A picture of it: " + got["crop"])
    if got.get("url"):
        bits.append("")
        bits.append("Page: " + got["url"])
    return "\n".join(bits)

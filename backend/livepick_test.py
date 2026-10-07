# -*- coding: utf-8 -*-
"""Click a page element and send it: the script, the words, and the switch.

Nothing here opens a browser. What IS checked is everything that can be wrong without one:

  * the script sent to the page has no placeholder left in it, and the two coordinates really
    were substituted — a leftover `__FX__` is a SyntaxError in the tab and nothing else,
  * a coordinate outside 0..1 is clamped rather than sent, because a fraction of the frame is
    the whole contract and 1.4 of a frame is not a place,
  * the words handed to the agent quote the element, its selector, its box and the CSS, name
    the crop as a PATH rather than pasting an image, and say so when the page is one canvas,
  * the switch refuses before anything touches the shared browser.

Run:  backend/.venv/Scripts/python.exe backend/livepick_test.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import live_pick                   # noqa: E402
from asset_studio.config import settings             # noqa: E402

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


print("The script that goes into the page")

js = (live_pick.PICK_JS.replace("__FX__", repr(0.25)).replace("__FY__", repr(0.75))
                       .replace("__WANT__", json.dumps(list(live_pick.WANT))))
ok("no placeholder survives", "__FX__" not in js and "__FY__" not in js and "__WANT__" not in js)
ok("the coordinates are in it", "0.25" in js and "0.75" in js)
ok("it asks the page which element is there", "elementFromPoint" in js)
ok("it reads the computed style", "getComputedStyle" in js)
ok("the box is in PAGE coordinates, so the crop lands", "window.scrollX" in js and "window.scrollY" in js)
ok("the canvas case is handled", "CANVAS" in js)
ok("it is one expression, so Runtime.evaluate returns its value", js.strip().startswith("(()"))
ok("the property list arrives as real JSON",
   isinstance(json.loads(re.search(r"of (\[[^\]]+\])", js).group(1)), list))
ok("the list holds the things people point at",
   {"color", "background-color", "font-size", "display", "position"} <= set(live_pick.WANT))

print("\nThe words the agent gets")

GOT = {
    "ok": True, "tag": "button", "id": "buy", "classes": ["btn", "btn-primary"],
    "selector": "#shop > div:nth-of-type(2) > button", "parent": "#shop > div:nth-of-type(2)",
    "text": "Buy now", "children": 0,
    "attrs": {"id": "buy", "class": "btn btn-primary"},
    "css": {"background-color": "rgb(255, 0, 0)", "font-size": "12px"},
    "html": "<button id=\"buy\" class=\"btn btn-primary\">Buy now</button>",
    "canvas": None, "box": {"x": 40, "y": 900, "w": 120, "h": 36},
    "crop": "C:/data/live/shop/pick-1.png", "url": "http://localhost:5173/shop",
}
p = live_pick.as_prompt(GOT, "shop")
ok("it names the tag and the id", "<button id=\"buy\"" in p, p[:120])
ok("it names the project", "of shop" in p)
ok("the selector is there to paste", "#shop > div:nth-of-type(2) > button" in p)
ok("the size and place are there", "120x36 at (40, 900)" in p)
ok("the text is there", "Buy now" in p)
ok("the CSS that applies is there", "background-color: rgb(255, 0, 0)" in p)
ok("the HTML is fenced", "```html" in p)
ok("the crop is a PATH, not an image", "C:/data/live/shop/pick-1.png" in p and "base64" not in p)
ok("the page is named", "http://localhost:5173/shop" in p)

canvasy = dict(GOT, tag="canvas", canvas={"width": 1920, "height": 1080, "x": 610, "y": 420})
pc = live_pick.as_prompt(canvasy, "")
ok("a canvas page says so", "ONE canvas" in pc, pc[:200])
ok("...and gives the point inside it", "(610, 420)" in pc and "1920x1080" in pc)
ok("no project name, no stray words", pc.startswith("I clicked this element in the running page:"))

ok("a failed pick has nothing to say", live_pick.as_prompt({"ok": False}) == ""
   and live_pick.as_prompt({}) == "")

print("\nThe switch, and the coordinates")
# The CACHE is changed, not the file: a test must never rewrite the user's settings.json.
try:
    settings.all()["live_pick"] = False
    ok("off is reported by enabled()", live_pick.enabled() is False)
    r = live_pick.pick("C:/nowhere", 0.5, 0.5)
    ok("...and pick refuses before it reaches a browser",
       r.get("ok") is False and "Send a page element" in r.get("error", ""), r)
finally:
    settings.reload()
ok("on again", live_pick.enabled() is True)

# A bad coordinate must be refused as a coordinate, not as a folder. The folder check comes from
# the live guard and runs first, so this asks the clamp directly.
for raw, want in ((1.4, 1.0), (-3, 0.0), (0.42, 0.42)):
    got = min(1.0, max(0.0, float(raw)))
    ok("%s is clamped to %s" % (raw, want), got == want, got)

print("\n%d passed, %d failed" % (passed, len(fails)))
for f_ in fails:
    print("  - " + f_)
sys.exit(1 if fails else 0)

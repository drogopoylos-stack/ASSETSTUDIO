"""Run the forge from the command line, so it can be used before the backend restarts.

`/api/live/forge` does not exist on the running backend until it is restarted, and restarting it
while agents are working strands them. This calls the same code directly, so the loop — write,
look, change, look — is available now.

    python forge_cli.py <asset.js> [--views 3q,front] [--turntable 8]
                        [--passes silhouette,wireframe] [--label "..."]
                        [--variants '[{"scale":1},{"scale":2}]'] [--size 640x640]
                        [--ref reference.png] [--focus claw,sail]

<asset.js> is the body of an async function. These names are in scope:

    THREE   the three.js module the harness page loaded — the real one, not a copy
    add()   hand it whatever you built: a Mesh, a Group, an array, or a canvas/texture
    params  the current variant's parameters, {} when not sweeping
    root    the subject node; scene, camera, renderer are there too
    log()   a line that comes back in the answer

It prints FINDINGS first, then the sheet path. READ THE FINDINGS, then read the PNG.

--ref sets the target image. Pass it ONCE per project: it is remembered, and from then on every
sheet carries the reference as its first panel. Comparing a render against a picture you read
twenty tool calls ago is comparing against a memory, which is where proportions and colour drift.

--focus frames the camera on ONE named part instead of the whole subject. Every ordinary view
fits the entire asset, so a claw on a three-unit creature lands on about ten pixels and cannot be
judged at all. The findings name the parts that were too small to have been seen; feed those names
straight back in as --focus.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# A Windows console is cp1252, and a refusal that says "Settings → Studio engine → Forge" has an
# arrow in it. Without this the tool died with a UnicodeEncodeError while printing the reason it
# was refusing, which reads to whoever ran it as a broken tool rather than as a switch being off.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                    # pragma: no cover - not a real terminal
        pass

# The page the forge loads when no project is named. It is a scratch folder under data/, which
# a fresh checkout has not got - so its absence must read as "name a project", not as a crash
# inside the browser three steps later.
HARNESS = str(Path(__file__).resolve().parent.parent / "data" / "ab" / "harness")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render an asset you built in code.")
    ap.add_argument("file", help="a .js file: the body of an async function")
    ap.add_argument("--project", default=HARNESS, help="folder whose page loads the engine")
    ap.add_argument("--views", default="3q",
                    help="3q front back side left top bottom low hero back3q, or an angle "
                         "of your own: az=35,el=12,zoom=2 . Separate several with ;")
    ap.add_argument("--turntable", type=int, default=0, help="N steps around, one framing")
    ap.add_argument("--passes", default="", help="comma list: silhouette wireframe normals")
    ap.add_argument("--variants", default="", help="JSON list of parameter objects")
    ap.add_argument("--ref", default="", help="the target image; remembered per project")
    ap.add_argument("--focus", default="", help="comma list of part or material names to frame on")
    ap.add_argument("--label", default="")
    ap.add_argument("--size", default="640x640")
    ap.add_argument("--ground", action="store_true")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="how much empty space around the subject; 1.0 is edge to edge")
    ap.add_argument("--numbers", action="store_true",
                    help="findings and measurements only, no sheet and no image tokens")
    ap.add_argument("--aim", action="store_true",
                    help="sweep the sphere and print the angle that matches --ref best")
    ap.add_argument("--steps", type=int, default=12, help="azimuths in the aim sweep")
    ap.add_argument("--els", default="-5,10,25", help="elevations in the aim sweep")
    ap.add_argument("--edits", default="",
                    help='move parts by name, no rebuild: '
                         '[{"target":"wing","rotate":[0,-20,0]}]')
    a = ap.parse_args()

    if a.project == HARNESS and not Path(HARNESS).is_dir():
        print("No --project given, and the scratch harness is not on this machine:")
        print("    " + HARNESS)
        print("Point --project at a folder whose page loads three.js or PlayCanvas - your game.")
        return 2

    code = Path(a.file).read_text(encoding="utf-8")
    try:
        w, h = (int(x) for x in a.size.lower().split("x"))
    except Exception:
        w, h = 640, 640

    def split_views(t: str) -> list:
        """`3q,side` is two views; `az=35,el=12` is ONE. Semicolons separate several specs."""
        t = (t or "").strip()
        if ";" in t:
            return [x.strip() for x in t.split(";") if x.strip()]
        if "=" in t:
            return [t]
        return [x.strip() for x in t.split(",") if x.strip()]

    from asset_studio import live, review
    opened = live.open_(a.project, wait_ms=3000)
    if not opened.get("ok"):
        print("could not open the harness:", opened.get("error"))
        review.shutdown()
        return 1

    if a.aim:
        try:
            got = live.aim(a.project, code, steps=a.steps,
                           els=[float(x) for x in a.els.split(",") if x.strip()],
                           ref=a.ref)
        finally:
            try:
                review.shutdown()
            except Exception:
                pass
        for f in (got.get("findings") or []):
            print("!", f)
        if not got.get("ok"):
            print("error:", got.get("error"))
            return 2
        print("scored %d angles against %s" % (got.get("scored", 0), got.get("reference", "")))
        for r in (got.get("ranked") or []):
            print("   %-16s overlap %.3f" % (r["view"], r["overlap"]))
        print("bands (model width / reference width, top to bottom)")
        for b in (got.get("bands") or []):
            print("   %3d-%3d%%   model %4dpx  ref %4dpx  ratio %s"
                  % (b["from"] * 100, b["to"] * 100, b["model_px"], b["ref_px"], b["ratio"]))
        return 0

    try:
        got = live.forge(
            a.project, code,
            views=split_views(a.views),
            margin=a.margin,
            numbers=a.numbers,
            edits=json.loads(a.edits) if a.edits else None,
            width=w, height=h, label=a.label or Path(a.file).stem,
            ground=a.ground,
            turntable=a.turntable,
            passes=[p for p in a.passes.split(",") if p],
            variants=json.loads(a.variants) if a.variants else None,
            ref=a.ref,
            focus=[f for f in a.focus.split(",") if f.strip()],
        )
    finally:
        # ALWAYS, and this is not tidiness. This process starts its own headless Chrome and then
        # exits; without this the browser is orphaned and nothing ever comes back for it. Measured
        # after a session of runs: 44 browsers, 102 processes, 7.2 GB, on a machine with 31 GB —
        # which slows down every agent on the box, including the one that ran this.
        try:
            review.shutdown()
        except Exception:
            pass
    stats = got.get("stats") or {}
    # FIRST, before the picture. A finding is the thing the sheet cannot say for itself: which
    # parts were too small to have been judged, and what the asset is actually standing on.
    for f in (got.get("findings") or []):
        print("!", f)
    print(json.dumps({k: got.get(k) for k in ("ok", "engine", "views", "error", "hint")},
                     ensure_ascii=False))
    print("stats:", json.dumps({k: stats.get(k) for k in
                                ("triangles", "meshes", "materials", "material_names",
                                 "bbox_size", "framed", "log")}, ensure_ascii=False)[:600])
    if stats.get("stance"):
        print("stance:", json.dumps(stats["stance"], ensure_ascii=False)[:400])
    if stats.get("parts"):
        # `px` is ONE of them, not the box around all of them. That distinction is the whole
        # point: twelve claws span 101px together and each one is 12, and only the second number
        # answers "could I have seen this".
        print("parts   (px = the size of ONE, in the wide framing)")
        for pt in stats["parts"][:14]:
            px = pt.get("px")
            flag = "  <- too small to judge" if isinstance(px, (int, float)) and px < 24 else ""
            print("   %-18s x%-3s %7s tris  %4s px%s" % (
                str(pt.get("name"))[:18], pt.get("meshes"), pt.get("tris"),
                px if px is not None else "?", flag))
    if got.get("edits"):
        print("edits applied (write these into the builder, nothing was saved)")
        for e in got["edits"]:
            print("   %-18s x%-3s pos %s  rot %s  scale %s"
                  % (str(e.get("target"))[:18], e.get("found"), e.get("position"),
                     e.get("rotation"), e.get("scale")))
    if got.get("nodes"):
        print("nodes you can move by name")
        for nd in got["nodes"][:20]:
            print("   %-18s x%-3s pos %s  rot %s  size %s"
                  % (str(nd.get("name"))[:18], nd.get("count"), nd.get("position"),
                     nd.get("rotation"), nd.get("size")))
    if got.get("console"):
        print("console:", json.dumps(got["console"][:6], ensure_ascii=False)[:500])
    if got.get("numbers_only"):
        print("(numbers only - no sheet was drawn and no image tokens were spent)")
    else:
        print("SHEET:", got.get("sheet") or "(none)")
    return 0 if got.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

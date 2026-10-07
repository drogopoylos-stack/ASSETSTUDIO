"""THE SHEET HAS TO SAY WHAT IT DID NOT CHECK.

Three faults came out of one real run, and all three were the same fault wearing different
clothes: the forge frames the WHOLE subject, so anything small is priced out of the review and
nothing said so.

  * the claws pointed the wrong way after twelve inspections. They render at twelve pixels.
  * the sail's rib grooves read as noise. A few pixels of shading.
  * the creature stood flat on four feet where the reference holds its front hands up. Nothing
    in a picture of your own work tells you the pose is wrong; only the reference does, and the
    reference was an image read twenty tool calls earlier.

So the sheet now carries the reference, the findings name the parts that were too small to judge,
and the stance is stated as a number. None of that needs a browser, which is why it is tested
here rather than in live_test.py.
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import live                      # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


TMP = Path(tempfile.mkdtemp(prefix="forge-review-"))
PROJECT = str(TMP / "a-project")
try:
    from PIL import Image

    print("The reference is set once and then it is simply always there")
    img = TMP / "target.png"
    Image.new("RGB", (400, 260), (200, 40, 40)).save(img)

    check("a missing file is refused, not remembered",
          live.reference(PROJECT, str(TMP / "nope.png")).get("ok") is False)
    check("nothing is remembered yet", live.reference(PROJECT).get("reference") == "")

    got = live.reference(PROJECT, str(img))
    check("setting it works", got.get("ok") and got.get("remembered"), got)
    check("and it is remembered for the next run",
          live.reference(PROJECT).get("reference") == str(img.resolve()),
          live.reference(PROJECT))

    print("\nIt is letterboxed to the render's own panel, so the sheet lays out evenly")
    frame, why = live._ref_frame(PROJECT, "", (560, 560))
    check("a frame comes back", frame is not None, why)
    check("at exactly the render's size", frame is not None and frame.size == (560, 560),
          getattr(frame, "size", None))
    # A 400x260 target in a 560x560 panel must keep its shape, not be stretched to square.
    # Sampled OFF the quarter marks: the panel is ruled now, and x=140/280/420 are grid lines
    # over both the letterbox and the artwork, so reading one measures the ruler, not the paste.
    px = frame.load()
    check("the picture is not stretched to fill",
          px[330, 8] == (18, 21, 28) and px[330, 280] != (18, 21, 28),
          (px[330, 8], px[330, 280]))

    frame2, why2 = live._ref_frame(PROJECT, str(img), (200, 200))
    check("passing it again re-sets it and still works", frame2 is not None, why2)
    check("a project with no reference gets no frame and no complaint",
          live._ref_frame(str(TMP / "other"), "", (100, 100)) == (None, ""))

    big = TMP / "huge.png"
    big.write_bytes(b"\x89PNG" + b"0" * (live._REF_MAX + 10))
    f3, why3 = live._ref_frame(PROJECT, str(big), (100, 100))
    check("an oversized file is refused with a reason, not read",
          f3 is None and "larger than" in why3, why3)

    print("\nThe findings name what the framing priced out of the review")
    stats = {
        "parts": [
            {"name": "skin", "meshes": 29, "tris": 13032, "px": 38},
            {"name": "sail", "meshes": 2, "tris": 5404, "px": 180},
            {"name": "claw", "meshes": 12, "tris": 192, "px": 14},
            {"name": "tooth", "meshes": 34, "tris": 408, "px": 6},
            {"name": "helper", "meshes": 1, "tris": 0, "px": 3},
        ],
        "stance": {"count": 6, "front": 2, "rear": 4},
    }
    f = live._findings(stats, True, [])
    joined = " ".join(f)
    check("the small parts are named", "claw (14px x12)" in joined and "tooth (6px" in joined,
          joined[:200])
    check("the big ones are not", "sail" not in joined.split("STANCE")[0], joined[:200])
    check("a part with no geometry is not reported as unchecked",
          "helper" not in joined, joined[:200])
    check("it says what to do about it", "--focus" in joined, joined[:200])
    check("the stance is stated as a number", "6 points touch the ground" in joined, joined)
    check("and it admits what it cannot know",
          "cannot read the reference" in joined, joined)

    print("\nA part that WAS focused is not then reported as unchecked")
    f2 = live._findings(stats, True, ["claw"])
    check("the focused one drops out", "claw (" not in " ".join(f2), f2)
    check("the others stay", "tooth" in " ".join(f2), f2)

    print("\nWith no reference, that is the first thing said")
    f3 = live._findings({"parts": [], "stance": {}}, False, [])
    check("it asks for one", any("NO REFERENCE" in x for x in f3), f3)
    check("...and not when there is one", not any("NO REFERENCE" in x
                                                  for x in live._findings({}, True, [])))

    print("\nThe threshold is a real one, not a placeholder")
    check("24px is small enough to be honest about", live._TOO_SMALL_PX >= 16)
    edge = {"parts": [{"name": "edge", "meshes": 1, "tris": 10,
                       "px": live._TOO_SMALL_PX}], "stance": {}}
    check("a part exactly at the threshold passes",
          not any("edge" in x for x in live._findings(edge, True, [])))

    print("\nThe forge signature carries them through")
    import inspect                                    # noqa: E402
    sig = inspect.signature(live.forge).parameters
    check("forge takes a reference", "ref" in sig)
    check("forge takes a focus list", "focus" in sig)
    src = inspect.getsource(live.forge)
    check("the reference is inserted FIRST, so it is read first",
          "frames.insert(0, rf)" in src)
    check("the focus views render after stats is taken",
          src.index("__forge.stats()") < src.index("focus: %s"), "order")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

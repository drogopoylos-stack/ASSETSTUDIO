"""Does the Engine window see a GLB that an agent wrote with its own tools?

An agent edited a GLB with numpy and rendered it with a Playwright page of its own. It never
called /api/live/glb, so the window showed its one forge call — a probe that built an empty
group — as "live · empty" for the whole job. The session hook now notes every model file a tool
call names, before the call runs; the bench answers with the newest one that was written after
it was named.

No hook process and no browser here: the hook's half is called directly, and the forge's bench is
a stand-in.
"""
import io
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import agent_glb as ag         # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s %s" % (name, extra))


def glb(path: Path, tris: int, nodes: int = 1) -> None:
    """A minimal GLB: one indexed mesh, used by `nodes` nodes. The buffer is never read."""
    j = {"asset": {"version": "2.0"},
         "accessors": [{"count": 3, "type": "VEC3", "componentType": 5126},
                       {"count": tris * 3, "type": "SCALAR", "componentType": 5125}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
         "materials": [{}, {}],
         "nodes": [{"mesh": 0} for _ in range(nodes)]}
    js = json.dumps(j).encode()
    js += b" " * ((-len(js)) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js))
                     + struct.pack("<I4s", len(js), b"JSON") + js)


def reset(data: Path) -> None:
    ag._data_dir = lambda: data
    ag._offset[0] = -1
    ag._seen.clear()
    ag._counts_cache.clear()


W = os.name == "nt"
print("\n-- which files a tool call names --")
cwd = "G:\\KAPOW" if W else "/g/KAPOW"
got = ag.paths_in({"command": "cd /c/tmp/next/jhy && python rebuild.py in/a.glb geo1 shape1.glb"
                              " && node view.mjs C:/tmp/next/jhy/shape2.glb out"}, cwd)
if W:
    check("a relative name is resolved in the folder the command moved into",
          "C:\\tmp\\next\\jhy\\shape1.glb" in got, got)
    check("an absolute Windows path is kept", "C:\\tmp\\next\\jhy\\shape2.glb" in got, got)
    check("and in the session's own folder too, as a second guess",
          "G:\\KAPOW\\shape1.glb" in got, got)
got = ag.paths_in({"code": 'bpy.ops.export_scene.gltf(filepath=r"D:/Asset Studio/out dir/ring post.glb")'}, cwd)
check("a quoted path with spaces is one path",
      os.path.normpath("D:/Asset Studio/out dir/ring post.glb") in got, got)
got = ag.paths_in({"command": "ls *.glb; cp $OUT/a.glb x/; echo {name}.glb; curl https://x.org/m.glb"}, cwd)
check("a glob, a variable, a template and a URL name no file", got == [], got)
got = ag.paths_in({"command": "node node_modules/three/examples/models/a.glb"}, cwd)
check("nothing inside node_modules", got == [], got)
check("a call that names no model costs nothing", ag.paths_in({"file_path": "src/a.ts"}, cwd) == [])
got = ag.paths_in({"command": "D=/g/KAPOW-concepts/x; mkdir -p $D/renders && cp /c/tmp/a.glb $D/fixed.glb"
                              " && cp /c/tmp/b.glb ${D}/b.glb"}, cwd)
if W:
    check("a folder the command set in a variable is put in where it is used",
          "G:\\KAPOW-concepts\\x\\fixed.glb" in got and "G:\\KAPOW-concepts\\x\\b.glb" in got, got)
got = ag.paths_in({"content": 'run("cd /c/tmp && python rebuild.py in/a.glb shape1.glb")\nx = "cp a.glb b.glb"'}, cwd)
check("a command quoted inside a script is not one path with spaces in it",
      not any(" " in p for p in got), got)

print("\n-- the hook's line --")
tmp = Path(tempfile.mkdtemp())
data = tmp / "data"
pre = {"hook_event_name": "PreToolUse", "cwd": str(tmp), "tool_name": "Bash",
       "tool_input": {"command": "python make.py out/a.glb"}}
ag.record(pre, data)
ag.record(dict(pre, hook_event_name="PostToolUse"), data)
ag.record(dict(pre, tool_input={"command": "ls"}), data)
lines = ag.feed_file(data).read_text(encoding="utf-8").splitlines()
check("one line for the one PreToolUse call that names a model", len(lines) == 1, lines)
check("the line holds the resolved path",
      json.loads(lines[0])["paths"] == [os.path.normpath(str(tmp / "out/a.glb"))], lines)
ag.record({"hook_event_name": "PreToolUse", "tool_input": object()}, data)
check("a payload it cannot read is ignored, never raised", True)

print("\n-- which one the window shows --")
reset(data)
work = tmp / "work"
(work / "in").mkdir(parents=True)
now = time.time()
src, out1, out2 = work / "in" / "src.glb", work / "shape1.glb", work / "shape2.glb"
glb(src, 10)
os.utime(src, (now - 60, now - 60))                 # the input: there before it was named
feed = ag.feed_file(data)
feed.write_text(json.dumps({"ts": now - 30, "cwd": str(work), "paths": [str(src), str(out1), str(out2)]})
                + "\n", encoding="utf-8")
check("nothing written since it was named: nothing to show", ag.latest() is None)
glb(out1, 1200)
os.utime(out1, (now - 20, now - 20))
b = ag.bench()
check("a file written after it was named is shown", b and b["file"] == str(out1), b)
check("in the shape the window takes: a GLB comment and the file as a model",
      b and b["code"] == "/* GLB: shape1.glb */" and b["models"][0]["path"] == str(out1), b)
check("with its triangles, read from the JSON alone",
      b and b["stats"]["triangles"] == 1200 and b["stats"]["materials"] == 2, b and b["stats"])
glb(out2, 900, nodes=2)
os.utime(out2, (now - 10, now - 10))
b = ag.bench()
check("the newest written wins", b and b["file"] == str(out2), b)
check("a mesh used by two nodes is counted twice", b and b["stats"]["triangles"] == 1800,
      b and b["stats"])
sig = b["sig"]
os.utime(out2, (now - 5, now - 5))
check("written again, it is a new scene for the window", ag.bench()["sig"] != sig)
glb(out1, 5)                                        # being written this instant
check("a file written this instant waits until it has settled", ag.bench()["file"] == str(out2))
os.utime(out1, (now - 3 * 3600, now - 3 * 3600))
check("a file older than two hours is nobody's work now", ag.bench()["file"] == str(out2))

print("\n-- one project's window --")
check("the folder the agent worked in shows it", ag.bench(str(work))["file"] == str(out2))
check("a folder holding that folder shows it", ag.bench(str(tmp))["file"] == str(out2))
other = tmp / "other"
other.mkdir()
check("another project does not", ag.bench(str(other)) is None)

print("\n-- the notes survive a backend restart --")
reset(data)
check("the feed is read again from its tail", ag.bench() and ag.bench()["file"] == str(out2))
with open(feed, "a", encoding="utf-8") as fh:
    fh.write('{"ts": %f, "cwd": "", "paths": ["%s"' % (now, "x"))   # a half-written line
check("a half-written last line is left for the next read", ag.bench()["file"] == str(out2))

print("\n-- the newer of the bench and the file --")
from asset_studio import live                    # noqa: E402
real = live._forge_bench
try:
    live._forge_bench = lambda p: {"ok": True, "open": True, "code": "{ add(probe) }", "ts": now - 30,
                                   "sig": "1:1", "project": str(work), "models": []}
    check("a file written after the forge's last call wins", live.bench("").get("file") == str(out2))
    live._forge_bench = lambda p: {"ok": True, "open": True, "code": "{ add(probe) }", "ts": now,
                                   "sig": "1:1", "project": str(work), "models": []}
    check("a forge call after the file wins", live.bench("").get("code") == "{ add(probe) }")
    live._forge_bench = lambda p: {"ok": True, "open": False, "sig": "", "code": "", "at": {}, "stats": {}}
    check("with no forge bench at all, the file", live.bench("").get("file") == str(out2))
finally:
    live._forge_bench = real
check("the batch call still answers with the forge's own bench",
      live._batch_ops()["bench"] is live._forge_bench)

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

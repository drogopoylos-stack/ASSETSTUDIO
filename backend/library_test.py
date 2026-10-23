"""The model library (library.py) and the Catalog shelves, against a throwaway ASSET_STUDIO_DATA.

Run: backend/.venv/Scripts/python.exe backend/library_test.py
"""
import os, shutil, sys, tempfile, sqlite3
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="libtest-"))
os.environ["ASSET_STUDIO_DATA"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import db, library
from asset_studio.config import JOBS_DIR
from asset_studio.models import Asset, AssetType, StageType, Job, JobRequest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from asset_studio.routers import catalog

app = FastAPI(); app.include_router(catalog.router); c = TestClient(app)
ok = 0
def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1

conn = db._connect()
check(conn.execute("PRAGMA user_version").fetchone()[0] == 2, "schema v2")
check("category" in {r[1] for r in conn.execute("PRAGMA table_info(assets)")}, "category column")

# a finished job's model in its scratch folder
job = Job(stage=StageType.gen3d, provider_id="comfyui-3d", label="Viking rune axe",
          category="Weapons", params={})
wd = Path(JOBS_DIR) / job.id; wd.mkdir(parents=True)
glb = wd / f"comfyui3d-{job.id}.glb"; glb.write_bytes(b"glTF" + b"\0" * 100)
prev = wd / f"comfyui3d-{job.id}.preview.png"; prev.write_bytes(b"png")
a = Asset(name=glb.name, stage=job.stage, type=AssetType.model, path=str(glb),
          preview_path=str(prev), job_id=job.id)
# what queue.py does
cat = library.normalize_category(job.category)
a.category = cat
library.name_from_job(a, job, 0)
a = library.file_asset(a); db.save_asset(a)
check(cat == "weapon", "alias Weapons->weapon")
check(a.name == "Viking rune axe", a.name)
check(Path(a.path).parent == library.MODELS_DIR / "weapon", a.path)
check(Path(a.path).name == f"viking-rune-axe-{a.id}.glb", a.path)
check(Path(a.path).is_file() and not glb.exists(), "moved, not copied")
check(Path(a.preview_path).name == f"viking-rune-axe-{a.id}.preview.png" and not prev.exists(), a.preview_path)

# UI label is not a name; the prompt is
job2 = Job(stage=StageType.gen3d, provider_id="x", label="Generate 3D · ComfyUI 3D", params={})
b = Asset(name="out.glb", stage=job2.stage, type=AssetType.model, path=str(wd / "out.glb"), prompt="goblin  archer")
library.name_from_job(b, job2); check(b.name == "goblin archer", b.name)

# list / filter / counts
r = c.get("/api/assets", params={"type": "model", "category": "weapon", "limit": 24}).json()
check(len(r) == 1 and r[0]["id"] == a.id, r)
check(c.get("/api/assets", params={"category": "unsorted"}).json() == [], "unsorted empty")
cats = c.get("/api/catalog/categories", params={"type": "model"}).json()
byid = {x["id"]: x["count"] for x in cats["categories"]}
check(byid["weapon"] == 1 and byid["enemy"] == 0 and byid["unsorted"] == 0 and cats["total"] == 1, cats)

# recategorize + rename -> the file moves
u = c.patch(f"/api/assets/{a.id}", json={"category": "enemies", "name": "Goblin axe"}).json()
check(u["category"] == "enemy", u)
check(Path(u["path"]) == library.MODELS_DIR / "enemy" / f"goblin-axe-{a.id}.glb" and Path(u["path"]).is_file(), u["path"])
check(Path(u["preview_path"]).is_file(), u["preview_path"])
check(not (library.MODELS_DIR / "weapon").exists(), "empty shelf pruned")
# a tag-only patch does not move
u2 = c.patch(f"/api/assets/{a.id}", json={"tags": ["x"]}).json(); check(u2["path"] == u["path"], "no move")
# custom shelf shows up
u3 = c.patch(f"/api/assets/{a.id}", json={"category": "Vegetation"}).json()
cats = c.get("/api/catalog/categories").json()
check(any(x["id"] == "vegetation" and x["count"] == 1 for x in cats["categories"]), cats)
# unsorted
u4 = c.patch(f"/api/assets/{a.id}", json={"category": "unsorted"}).json()
check(u4["category"] == "" and Path(u4["path"]).parent.name == "unsorted", u4["path"])

# import (copy) an outside file
ext = tmp / "outside.glb"; ext.write_bytes(b"glTF" + b"\0" * 50)
imp = c.post("/api/library/import", json={"path": str(ext), "name": "Hero knight", "category": "main characters"}).json()
check(imp["category"] == "main-character" and ext.exists() and Path(imp["path"]).is_file(), imp)

# multi-file gltf keeps its sidecars and moves as a folder
job3 = Job(stage=StageType.gen3d, provider_id="x", params={"name": "Crate"}, category="prop")
wd3 = Path(JOBS_DIR) / job3.id; wd3.mkdir(parents=True)
(wd3 / "m.gltf").write_text("{}"); (wd3 / "m.bin").write_bytes(b"1"); (wd3 / "tex.png").write_bytes(b"2")
g = Asset(name="m.gltf", stage=job3.stage, type=AssetType.model, path=str(wd3 / "m.gltf"), job_id=job3.id, category="prop")
library.name_from_job(g, job3); g = library.file_asset(g); db.save_asset(g)
gp = Path(g.path)
check(gp.parent.name == f"crate-{g.id}" and (gp.parent / "m.bin").is_file() and (gp.parent / "tex.png").is_file(), g.path)
g2 = c.patch(f"/api/assets/{g.id}", json={"category": "building"}).json()
check(Path(g2["path"]).is_file() and Path(g2["path"]).parent.parent.name == "building" and not gp.parent.exists(), g2["path"])
c.delete(f"/api/assets/{g.id}", params={"delete_file": True})
check(not Path(g2["path"]).parent.exists(), "folder deleted")

# backfill files a stray model left in a job folder
job4 = Job(stage=StageType.gen3d, provider_id="x"); wd4 = Path(JOBS_DIR) / job4.id; wd4.mkdir(parents=True)
(wd4 / "z.glb").write_bytes(b"glTF")
db.save_asset(Asset(name="Old one", stage=job4.stage, type=AssetType.model, path=str(wd4 / "z.glb"), job_id=job4.id))
check(library.backfill() == 1 and library.backfill() == 0, "backfill once")

# JobRequest carries category
check(JobRequest(stage="gen3d", provider_id="x", category="enemy").category == "enemy", "request")
print(f"{ok} passed, 0 failed")
db._conn.close() if getattr(db, "_conn", None) else None
shutil.rmtree(tmp, ignore_errors=True)

"""SAM2 segmentation worker — runs inside the dedicated sam2 venv (torch-cpu + ultralytics),
NOT the backend venv. Invoked per job by Sam2CutoutProvider with a JSON spec file.

Protocol on stdout (line-based, parsed by the provider):
    P <0..1> <message>      progress
    LOG <message>           informational
    DONE <json>             {"files": [{"path", "index", "area_pct", "bbox"}...]}
Any exception → non-zero exit with the error on stderr.
"""
import json
import os
import sys
from pathlib import Path


def p(v, msg):
    print(f"P {v:.2f} {msg}", flush=True)


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    img_path = spec["image"]
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    model_name = spec.get("model", "sam2.1_b.pt")
    mode = spec.get("mode", "points")
    max_masks = int(spec.get("max_masks", 20))
    min_area_pct = float(spec.get("min_area_pct", 0.05))
    crop = bool(spec.get("crop", True))
    pad = int(spec.get("pad", 4))

    # checkpoints live under data/models/sam2 — ultralytics downloads bare asset names to CWD
    weights_dir = Path(spec["weights_dir"])
    weights_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(weights_dir)
    if not (weights_dir / model_name).exists():
        p(0.05, f"downloading {model_name} (one-time)")

    p(0.10, "loading SAM2 model")
    import numpy as np
    from PIL import Image
    from ultralytics import SAM

    model = SAM(model_name)

    img = Image.open(img_path).convert("RGBA")
    W, H = img.size
    rgb = img.convert("RGB")

    kwargs = {}
    if mode == "points":
        pts = spec.get("points") or []
        neg = spec.get("neg_points") or []
        if not pts:
            raise SystemExit("points mode needs at least one click — give 'points' like 120,340")
        for q in pts + neg:
            if not (0 <= q[0] < W and 0 <= q[1] < H):
                raise SystemExit(f"click {int(q[0])},{int(q[1])} is outside the {W}x{H} image")
        kwargs["points"] = [list(map(float, q)) for q in pts + neg]
        kwargs["labels"] = [1] * len(pts) + [0] * len(neg)
    elif mode == "box":
        box = spec.get("box")
        if not box or len(box) != 4:
            raise SystemExit("box mode needs 'box' as x1,y1,x2,y2")
        kwargs["bboxes"] = [list(map(float, box))]
    # mode == "auto": no prompts → SAM segments everything it finds

    p(0.35, "segmenting" if mode != "auto" else "segmenting every object (auto)")
    results = model(rgb, verbose=False, **kwargs)

    masks = []
    r = results[0]
    if r.masks is not None:
        m = r.masks.data.cpu().numpy()          # (N, h, w) float/bool
        for i in range(m.shape[0]):
            masks.append(m[i] > 0.5)
    if not masks:
        raise SystemExit("SAM2 found no mask for that prompt — try a different click point")

    # rank by area, drop empties + specks, cap count
    total = float(W * H)
    scored = sorted(((float(mk.sum()) / total * 100.0, mk) for mk in masks),
                    key=lambda t: t[0], reverse=True)
    scored = [(a, mk) for a, mk in scored if mk.any()]
    if not scored:
        raise SystemExit("SAM2 produced only empty masks — try a different click point")
    keep = [(a, mk) for a, mk in scored if a >= min_area_pct][:max_masks]
    if not keep:
        keep = scored[:1]

    p(0.7, f"cutting {len(keep)} PNG(s)")
    src_arr = np.asarray(img)                    # (H, W, 4)
    stem = Path(img_path).stem
    files = []
    for i, (area, mk) in enumerate(keep):
        if mk.shape != (H, W):                   # ultralytics letterboxes on some sizes
            mk_img = Image.fromarray((mk * 255).astype(np.uint8)).resize((W, H), Image.NEAREST)
            mk = np.asarray(mk_img) > 127
            if not mk.any():
                continue
        out = src_arr.copy()
        out[..., 3] = np.where(mk, src_arr[..., 3], 0)
        ys, xs = np.where(mk)
        y1, y2, x1, x2 = ys.min(), ys.max(), xs.min(), xs.max()
        bbox = [int(x1), int(y1), int(x2), int(y2)]
        piece = Image.fromarray(out, "RGBA")
        if crop:
            piece = piece.crop((max(0, x1 - pad), max(0, y1 - pad),
                                min(W, x2 + 1 + pad), min(H, y2 + 1 + pad)))
        name = f"{stem}-cut{i + 1}.png" if len(keep) > 1 else f"{stem}-cut.png"
        piece.save(out_dir / name, "PNG")
        files.append({"path": str(out_dir / name), "index": i, "area_pct": round(area, 2), "bbox": bbox})

    if not files:
        raise SystemExit("all masks came back empty after rescale — try a different prompt")
    print("DONE " + json.dumps({"files": files}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

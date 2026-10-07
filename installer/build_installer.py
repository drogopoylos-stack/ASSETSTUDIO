"""Build AssetStudio-Setup-<version>.exe: one installer with everything in it.

    python installer/build_installer.py [--version 2026.9.24] [--no-compile]

What it stages (in data/installer-build/stage, then compiles with Inno Setup 6):
  - the app, exactly as git would carry it (tracked + untracked-not-ignored files)
  - frontend/dist (the built UI) and frontend/node_modules (Electron and every npm package)
  - runtime/python: a private CPython 3.12 (python-build-standalone), checksum-verified
  - runtime/wheelhouse: every Python package the backend, graphify and Scrapling need, as wheels
  - runtime/seed-settings.json: THIS PC's settings minus what only means something here (the
    folders it has open, program paths, API keys never live in settings.json anyway)

What it never stages: data/ (settings with this PC's paths, history, copies of other projects),
backend/.venv (a venv only works on the PC that made it), .git.

Run `npm run build` in frontend/ first when the UI changed: the script checks dist is newer than
src and refuses a stale one.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "data" / "installer-build"
STAGE = BUILD / "stage"
OUT = BUILD / "Output"
PBS_TAG = "20260901"
PBS_FILE = "cpython-3.12.14+%s-x86_64-pc-windows-msvc-install_only_stripped.tar.gz" % PBS_TAG
PBS_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/%s/%s" % (
    PBS_TAG, PBS_FILE.replace("+", "%2B"))
PBS_SUMS = "https://github.com/astral-sh/python-build-standalone/releases/download/%s/SHA256SUMS" % PBS_TAG
EXTRA_PACKAGES = ["graphifyy", "scrapling[fetchers]", "pip", "setuptools", "wheel"]
ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def log(msg: str) -> None:
    print("==> " + msg, flush=True)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def robocopy(src: Path, dst: Path) -> None:
    """Mirror a big tree fast. robocopy's exit codes 0-7 all mean success."""
    r = subprocess.run(["robocopy", str(src), str(dst), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS",
                        "/NC", "/NS", "/NP", "/MT:16", "/R:1", "/W:1"], capture_output=True, text=True)
    if r.returncode >= 8:
        sys.exit("robocopy failed (%d) for %s: %s" % (r.returncode, src, r.stdout[-400:]))


# ---------------------------------------------------------------------------------------------
def stage_source() -> int:
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                           capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    n = 0
    for f in files:
        if not f or f.startswith(("data/", "runtime/", "bin/")):
            continue
        s = ROOT / f
        if s.is_file():
            d = STAGE / f
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            n += 1
    return n


def check_ui_fresh() -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if not dist.is_file():
        sys.exit("frontend/dist is missing - run `npm run build` in frontend/ first")
    newest_src = max(p.stat().st_mtime for p in (ROOT / "frontend" / "src").rglob("*") if p.is_file())
    if dist.stat().st_mtime < newest_src:
        sys.exit("frontend/dist is older than frontend/src - run `npm run build` in frontend/ first")


def ensure_python() -> Path:
    py = BUILD / "runtime" / "python" / "python.exe"
    if py.is_file():
        return py.parent
    dl = BUILD / "dl"
    dl.mkdir(parents=True, exist_ok=True)
    tgz = dl / PBS_FILE
    if not tgz.is_file():
        log("downloading " + PBS_FILE)
        urllib.request.urlretrieve(PBS_URL, tgz)
    sums = urllib.request.urlopen(PBS_SUMS, timeout=60).read().decode("utf-8")
    want = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(PBS_FILE)), "")
    if not want or sha256(tgz) != want:
        sys.exit("the Python download does not match its published SHA-256")
    (BUILD / "runtime").mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz) as t:
        t.extractall(BUILD / "runtime")
    return py.parent


def ensure_wheels(pydir: Path) -> Path:
    wh = BUILD / "wheelhouse"
    wh.mkdir(parents=True, exist_ok=True)
    log("collecting wheels for Python 3.12 (only new ones are downloaded)")
    r = subprocess.run([str(pydir / "python.exe"), "-m", "pip", "download", "--only-binary=:all:",
                        "-d", str(wh), "-r", str(ROOT / "backend" / "requirements.txt"), *EXTRA_PACKAGES],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("pip download failed:\n" + (r.stderr or r.stdout)[-2000:])
    return wh


# ---------------------------------------------------------------------------------------------
# THIS PC's settings, minus what only means something on this PC.
_DROP_KEYS = {"workspace_roots", "chrome_path", "services"}
_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")


def seed_settings() -> tuple[dict, list]:
    src = ROOT / "data" / "settings.json"
    if not src.is_file():
        return {}, ["no data/settings.json on this PC - the new PC starts with the defaults"]
    data = json.loads(src.read_text(encoding="utf-8"))
    dropped: list = []

    def clean(obj, path=""):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                p = (path + "." + k) if path else k
                if not path and k in _DROP_KEYS:
                    dropped.append(p)
                    continue
                if isinstance(v, str) and _ABS.match(v.strip()):
                    dropped.append(p)        # a program or folder path on this PC
                    continue
                out[k] = clean(v, p)
            return out
        if isinstance(obj, list):            # a list of folders is as much this PC's as one folder
            keep = [x for x in obj if not (isinstance(x, str) and _ABS.match(x.strip()))]
            if len(keep) != len(obj):
                dropped.append(path + "[paths]")
            return [clean(x, path) for x in keep]
        return obj

    seed = clean(data)
    if isinstance(seed.get("ui_prefs"), dict) and seed["ui_prefs"]:
        # Tells the new PC's window that these preferences come from the installer, so they win
        # over the defaults its first load wrote (see seedUiPrefs in frontend/src/uiPrefs.ts).
        seed["ui_prefs_install_seed"] = True
    return seed, dropped


# ---------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=_dt.date.today().strftime("%Y.%-m.%-d") if os.name != "nt"
                    else "%d.%d.%d" % (_dt.date.today().year, _dt.date.today().month, _dt.date.today().day))
    ap.add_argument("--no-compile", action="store_true")
    args = ap.parse_args()

    check_ui_fresh()
    if STAGE.exists():
        log("clearing the old stage")
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    log("staging the app as git would carry it")
    n = stage_source()
    log("  %d files" % n)
    log("staging the built UI and node_modules")
    robocopy(ROOT / "frontend" / "dist", STAGE / "frontend" / "dist")
    robocopy(ROOT / "frontend" / "node_modules", STAGE / "frontend" / "node_modules")

    pydir = ensure_python()
    wh = ensure_wheels(pydir)
    log("staging the private Python and %d wheels" % len(list(wh.glob("*.whl"))))
    robocopy(pydir, STAGE / "runtime" / "python")
    robocopy(wh, STAGE / "runtime" / "wheelhouse")

    seed, dropped = seed_settings()
    (STAGE / "runtime" / "seed-settings.json").write_text(json.dumps(seed, indent=2), encoding="utf-8")
    log("settings seed: %d keys; left out as this PC's own: %s" % (len(seed), ", ".join(dropped) or "none"))
    if "ui_prefs" not in seed:
        log("  NOTE: no ui_prefs yet - the theme, fonts and status bar come with it once the desktop app "
            "has run the new UI for a few seconds")

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                                text=True).stdout.strip())
    (STAGE / "VERSION.txt").write_text(
        "Asset Studio %s\nbuilt %s from commit %s%s\n" % (
            args.version, _dt.datetime.now().isoformat(timespec="seconds"), commit,
            " plus uncommitted changes" if dirty else ""), encoding="utf-8")

    if args.no_compile:
        log("staged at %s (not compiled)" % STAGE)
        return
    iscc = next((p for p in ISCC_CANDIDATES if p.is_file()), None)
    if not iscc:
        sys.exit("Inno Setup 6 is not installed: winget install JRSoftware.InnoSetup")
    OUT.mkdir(parents=True, exist_ok=True)
    log("compiling with %s (several minutes: about a gigabyte goes into one file)" % iscc)
    r = subprocess.run([str(iscc), "/Q", "/DStageDir=" + str(STAGE), "/DAppVersion=" + args.version,
                        "/DOutDir=" + str(OUT), str(ROOT / "installer" / "AssetStudio.iss")])
    if r.returncode != 0:
        sys.exit("ISCC failed (%d)" % r.returncode)
    exe = OUT / ("AssetStudio-Setup-%s.exe" % args.version)
    size = exe.stat().st_size
    digest = sha256(exe)
    (OUT / (exe.name + ".sha256")).write_text("%s  %s\n" % (digest, exe.name), encoding="utf-8")
    log("built %s (%.0f MB)\n    sha256 %s" % (exe, size / 1e6, digest))


if __name__ == "__main__":
    main()

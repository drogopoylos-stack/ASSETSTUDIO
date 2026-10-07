# -*- coding: utf-8 -*-
"""A new PC, held to what it needs: every fault here was found by installing on an empty profile.

  1. CLAUDE CODE'S FOLDER is CLAUDE_CONFIG_DIR when that is set, else ~/.claude - for every module.
  2. AN INSTALL COUNTS ONLY WHEN IT IS COMPLETE. A venv folder exists the moment `python -m venv`
     runs; graphify and the web tools used to count as installed from then on, forever.
  3. THE WEB WORKER LOOKS WHERE ITS BROWSER IS, and for the build its stealth tier needs.
  4. THE /graphify SKILL is written whichever graphify the PC ended up with.
  5. CLAUDE CODE'S OWN INSTALLER does not put claude.exe on PATH; the Studio finds it anyway.
  6. A NEW PC STARTS WITH THE SETTINGS IT WAS GIVEN - once, and never overwrites its own.
  7. The scripts stop THIS copy only, install offline from bundled wheels, and keep the PATH's type.

Run:  python install_test.py     (from backend/)
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
ok = fail = 0


def check(name, cond, got=None):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + name)
    else:
        fail += 1
        print("  FAIL  " + name + ("   " + repr(got)[:300] if got is not None else ""))


def read(rel):
    return io.open(ROOT / rel, encoding="utf-8", errors="replace").read()


TMP = Path(tempfile.mkdtemp(prefix="studio-install-test-"))
_env_saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "ASSET_STUDIO_WHEELHOUSE",
                                              "ASSET_STUDIO_SEED_SETTINGS", "USERPROFILE", "HOME", "PATH")}


def restore_env():
    for k, v in _env_saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


from asset_studio import config                                            # noqa: E402

# ---------------------------------------------------------------------------------------------
print("\nThe Claude folder")
os.environ.pop("CLAUDE_CONFIG_DIR", None)
check("without CLAUDE_CONFIG_DIR it is ~/.claude", config.claude_home() == Path.home() / ".claude")
os.environ["CLAUDE_CONFIG_DIR"] = str(TMP / "cc")
check("with it, it is that folder", config.claude_home() == TMP / "cc")
check("...and ~/.claude.json stays the registry until the folder holds its own",
      config.claude_json() == Path.home() / ".claude.json")
(TMP / "cc").mkdir(parents=True, exist_ok=True)
(TMP / "cc" / ".claude.json").write_text("{}", encoding="utf-8")
check("...then the folder's own .claude.json is read", config.claude_json() == TMP / "cc" / ".claude.json")
os.environ.pop("CLAUDE_CONFIG_DIR", None)
for rel in ("backend/asset_studio/autolearn.py", "backend/asset_studio/claude_auth.py",
            "backend/asset_studio/usage.py", "backend/asset_studio/workspace.py"):
    t = read(rel)
    check("%s no longer assumes ~/.claude" % rel.split("/")[-1],
          'Path.home() / ".claude"' not in t and 'Path.home() / ".claude.json"' not in t)

# ---------------------------------------------------------------------------------------------
print("\nAn install counts only when it is complete")
v = TMP / "venv"
(v / "Lib" / "site-packages").mkdir(parents=True)
check("an empty venv does not have scrapling", not config.venv_has(v, "scrapling"))
(v / "Lib" / "site-packages" / "scrapling").mkdir()
(v / "Lib" / "site-packages" / "scrapling" / "__init__.py").write_text("", encoding="utf-8")
check("...a venv with the package does", config.venv_has(v, "scrapling"))

from asset_studio import web_tools as wt                                   # noqa: E402
from asset_studio import graphify_index as gi                              # noqa: E402

_saved_venv = wt._VENV
_saved_browsers = wt._BROWSERS
try:
    half = TMP / "half-scrapling-venv"
    (half / "Scripts").mkdir(parents=True)
    (half / "Scripts" / "python.exe").write_bytes(b"")
    wt._VENV = half
    check("the web tools are NOT available from a venv pip never finished", not wt.available())
    (half / "Lib" / "site-packages" / "scrapling").mkdir(parents=True)
    (half / "Lib" / "site-packages" / "scrapling" / "__init__.py").write_text("", encoding="utf-8")
    check("...and are once scrapling is in it", wt.available())

    # The browser the stealth tier runs is patchright's own build, read from its browsers.json.
    pr = half / "Lib" / "site-packages" / "patchright" / "driver" / "package"
    pr.mkdir(parents=True)
    (pr / "browsers.json").write_text(json.dumps({"browsers": [
        {"name": "chromium", "revision": "1234", "installByDefault": True},
        {"name": "chromium-headless-shell", "revision": "1234", "installByDefault": True},
        {"name": "firefox", "revision": "9", "installByDefault": False}]}), encoding="utf-8")
    wt._BROWSERS = TMP / "ms-playwright"
    check("the builds it needs are read from patchright",
          wt._needed_browsers() == ["chromium-1234", "chromium_headless_shell-1234"], wt._needed_browsers())
    (wt._BROWSERS / "chromium-1228").mkdir(parents=True)
    (wt._BROWSERS / "chromium-1228" / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    check("Playwright's OTHER build is not enough (the fault that hid on this PC)", not wt.browsers_ok())
    for d in ("chromium-1234", "chromium_headless_shell-1234"):
        (wt._BROWSERS / d).mkdir(parents=True)
    check("a download that never finished is not enough either", not wt.browsers_ok())
    for d in ("chromium-1234", "chromium_headless_shell-1234"):
        (wt._BROWSERS / d / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    check("...the complete builds are", wt.browsers_ok())
    check("the worker runs with PLAYWRIGHT_BROWSERS_PATH set to that folder",
          wt._env().get("PLAYWRIGHT_BROWSERS_PATH") == str(wt._BROWSERS)
          and "env=_env()" in read("backend/asset_studio/web_tools.py"))
    check("status says whether the browser is there", "browsers" in wt.status())
finally:
    wt._VENV = _saved_venv
    wt._BROWSERS = _saved_browsers

_saved_gvenv = gi._VENV_DIR
_saved_gpy = gi._GPY
try:
    gh = TMP / "half-graphify-venv"
    (gh / "Scripts").mkdir(parents=True)
    (gh / "Scripts" / "python.exe").write_bytes(b"")
    gi._VENV_DIR = gh
    gi._GPY = None
    got = gi._graphify_python()
    check("graphify's own venv is skipped while graphify is not in it", got != str(gh / "Scripts" / "python.exe"), got)
finally:
    gi._VENV_DIR = _saved_gvenv
    gi._GPY = _saved_gpy
check("the backend's installers try the bundled wheels first",
      "pip_attempts()" in read("backend/asset_studio/web_tools.py")
      and "pip_attempts()" in read("backend/asset_studio/graphify_index.py"))

# ---------------------------------------------------------------------------------------------
print("\nOffline wheels")
os.environ.pop("ASSET_STUDIO_WHEELHOUSE", None)
check("with no wheelhouse pip goes to PyPI", config.pip_attempts() == [["--prefer-binary"]] or config.wheelhouse())
wh = TMP / "wheels"
wh.mkdir()
(wh / "x-1.0-py3-none-any.whl").write_bytes(b"")
os.environ["ASSET_STUDIO_WHEELHOUSE"] = str(wh)
at = config.pip_attempts()
check("with one, pip tries it alone first, then PyPI with the wheels preferred",
      at[0][:1] == ["--no-index"] and str(wh) in at[0] and at[1][0] == "--prefer-binary", at)
os.environ.pop("ASSET_STUDIO_WHEELHOUSE", None)

# ---------------------------------------------------------------------------------------------
print("\nThe /graphify skill")
check("the skill is looked for in the Claude folder", "claude_home()" in read("backend/asset_studio/graphify_index.py"))
check("an install that finds graphify already there still writes the skill",
      "if not skill_installed():" in read("backend/asset_studio/graphify_index.py"))

# ---------------------------------------------------------------------------------------------
print("\nClaude Code where its own installer puts it")
from asset_studio import cc_session                                        # noqa: E402
home = TMP / "home"
(home / ".local" / "bin").mkdir(parents=True)
(home / ".local" / "bin" / "claude.exe").write_bytes(b"")
os.environ["USERPROFILE"] = str(home)
os.environ["HOME"] = str(home)
os.environ["PATH"] = str(TMP / "nothing-here")
try:
    found = cc_session._find_claude_build(None)
    check("claude.exe in ~/.local/bin is found with no PATH entry for it",
          found == str(home / ".local" / "bin" / "claude.exe"), found)
finally:
    restore_env()

# ---------------------------------------------------------------------------------------------
print("\nTools installed after the launcher started")
_saved_reg = config.registry_path
try:
    have = TMP / "on-path-already"
    new = TMP / "installed-a-minute-ago"
    have.mkdir()
    new.mkdir()
    os.environ["PATH"] = str(have)
    config.registry_path = lambda: [str(have), str(new), str(TMP / "uninstalled-long-ago")]
    os.environ.pop("ASSET_STUDIO_REGISTRY_PATH", None)
    added = config.refresh_path()
    parts = os.environ["PATH"].split(os.pathsep)
    check("a folder Windows has on PATH and this process lacks is added", added == [str(new)], added)
    check("...at the END, so nothing already on PATH changes its order", parts == [str(have), str(new)], parts)
    check("...and a folder that no longer exists is not", str(TMP / "uninstalled-long-ago") not in parts)
    check("a second call adds nothing", config.refresh_path() == [])
    os.environ["PATH"] = str(have)
    os.environ["ASSET_STUDIO_REGISTRY_PATH"] = "0"
    check("ASSET_STUDIO_REGISTRY_PATH=0 turns it off (the rig's registry is this PC's)",
          config.refresh_path() == [] and os.environ["PATH"] == str(have))
finally:
    config.registry_path = _saved_reg
    restore_env()
    os.environ.pop("ASSET_STUDIO_REGISTRY_PATH", None)
if os.name == "nt":
    check("the real registry PATH is read and expanded", config.registry_path()
          and not any("%" in p for p in config.registry_path()), config.registry_path()[:3])
mp = read("backend/asset_studio/main.py")
check("the backend refreshes its PATH before it serves (agents inherit it)",
      "refresh_path()" in mp and mp.index("refresh_path()") < mp.index("uvicorn.run("))

# ---------------------------------------------------------------------------------------------
print("\nThe settings a new PC starts with")
seed = TMP / "seed.json"
seed.write_text(json.dumps({"cc_live": True, "ui_prefs": {"asset-studio-theme": "light"}}), encoding="utf-8")
os.environ["ASSET_STUDIO_SEED_SETTINGS"] = str(seed)
try:
    s = config.Settings(TMP / "s1" / "settings.json")
    (TMP / "s1").mkdir()
    d = s.all()
    check("the first start takes the seed", d.get("cc_live") is True and d.get("ui_prefs", {}).get("asset-studio-theme") == "light")
    check("...keeps every default the seed does not name", d.get("cc_forge") is True)
    check("...and writes it down", (TMP / "s1" / "settings.json").is_file())
    seed.write_text(json.dumps({"cc_live": False}), encoding="utf-8")
    check("a later start never reads the seed again", config.Settings(TMP / "s1" / "settings.json").all().get("cc_live") is True)
    s2 = TMP / "s2"
    s2.mkdir()
    (s2 / "settings.json").write_text(json.dumps({"cc_live": False}), encoding="utf-8")
    check("a PC that has settings keeps its own", config.Settings(s2 / "settings.json").all().get("cc_live") is False)
finally:
    os.environ.pop("ASSET_STUDIO_SEED_SETTINGS", None)

sys.path.insert(0, str(ROOT / "installer"))
import build_installer as bi                                               # noqa: E402
_saved_root = bi.ROOT
try:
    fake = TMP / "fakeroot"
    (fake / "data").mkdir(parents=True)
    (fake / "data" / "settings.json").write_text(json.dumps({
        "workspace_roots": ["C:\\Users\\me\\Desktop\\game"], "chrome_path": "E:/Chrome", "services": {"x": {}},
        "tools": {"claude_path": "C:\\Users\\me\\.local\\bin\\claude.exe", "blender_path": "blender",
                  "comfyui_url": "http://127.0.0.1:8188"},
        "cc_live": True, "ui_prefs": {"ws-font": "15"},
        "pinned": ["C:\\Users\\me\\Desktop\\game", "rot-rush"]}), encoding="utf-8")
    bi.ROOT = fake
    sd, dropped = bi.seed_settings()
    check("the seed leaves out this PC's folders, browser and services",
          "workspace_roots" not in sd and "chrome_path" not in sd and "services" not in sd, dropped)
    check("...and any path to a program here, but keeps names and URLs",
          "claude_path" not in sd["tools"] and sd["tools"]["blender_path"] == "blender"
          and sd["tools"]["comfyui_url"].startswith("http"), sd.get("tools"))
    check("...and keeps the switches and the UI's own preferences",
          sd.get("cc_live") is True and sd.get("ui_prefs") == {"ws-font": "15"})
    check("...and takes the paths out of a list, keeping the rest", sd.get("pinned") == ["rot-rush"], sd.get("pinned"))
    check("...and marks its UI preferences as the installer's, so they win over first-load defaults",
          sd.get("ui_prefs_install_seed") is True)
finally:
    bi.ROOT = _saved_root

ui = read("frontend/src/uiPrefs.ts")
check("the UI mirrors its preferences from the desktop app only (never the headless browser)",
      "studioBridge" in ui and "updateSettings({ ui_prefs" in ui)
check("...and a first start writes them back before reloading once", "ui-prefs-seeded" in ui and "location.reload()" in ui)
check("...started with the app", "seedUiPrefs();" in read("frontend/src/main.tsx") and "mirrorUiPrefs();" in read("frontend/src/main.tsx"))

# ---------------------------------------------------------------------------------------------
print("\nThe Python the installer ships")
# The installer runs Python 3.12; this PC's venv may be older. 3.12 prints a SyntaxWarning for an
# invalid escape like "\m" in any string - a docstring's "%LOCALAPPDATA%\ms-playwright" was one,
# and its warning stood where setup prints the route count. A "\t" in a path would be a real TAB.
import warnings                                                             # noqa: E402
_bad = []
for _p in sorted((ROOT / "backend" / "asset_studio").rglob("*.py")):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            compile(_p.read_text(encoding="utf-8", errors="replace"), str(_p), "exec")
        except (SyntaxWarning, DeprecationWarning, SyntaxError) as _e:
            _bad.append("%s: %s" % (_p.name, str(_e)[:100]))
check("no backend file has an invalid escape (Python 3.12 warns, a stray \\t is a TAB)", not _bad, _bad)
check("setup prints its own probe line, not what Python wrote to stderr", "IMPORT_OK (\\d+) routes" in read("setup.ps1"))

# ---------------------------------------------------------------------------------------------
print("\nThe scripts")
sp = read("setup.ps1")
check("setup stops THIS copy only, found through its own venv",
      "StartsWith($venvDir" in sp and 'Filter "Name=\'python.exe\'"' not in sp)
check("setup installs Claude Code with Anthropic's installer, npm only as the fallback",
      "claude.ai/install.ps1" in sp and sp.index("claude.ai/install.ps1") < sp.index("@anthropic-ai/claude-code"))
check("...and puts ~/.local/bin on PATH, as that installer asks", 'Join-Path $env:USERPROFILE ".local\\bin"' in sp and "Add-UserPath $lb" in sp)
check("the user PATH is written raw and stays REG_EXPAND_SZ",
      "DoNotExpandEnvironmentNames" in sp and "RegistryValueKind]::ExpandString" in sp
      and '[Environment]::SetEnvironmentVariable("Path"' not in sp)
check("setup installs the web tools and checks the Engine's browser",
      "web_tools as w" in sp and "browser_install.ensure_installed" in sp)
check("setup writes the /graphify skill and a graphify command", "ensure_skill()" in sp and '"graphify.exe"' in sp)
check("setup installs gltf-transform, which opens a game's compressed models",
      "npm install -g \"@gltf-transform/cli\"" in sp and "npm config get prefix" in sp)
check("setup takes the bundled runtime and installs offline",
      all(x in sp for x in ("-PythonExe", "-Wheelhouse", "-SkipUiBuild", "--no-index", "runtime\\python\\python.exe")))
check("every setup run leaves a log", "Start-Transcript" in sp and "data\\logs" in sp)
check("setup still copies the bundled skills into the Claude folder", ".claude\\skills" in sp and "CLAUDE_CONFIG_DIR" in sp)
pr = read("studio-procs.ps1")
check("the stop/restart scripts share one own-copy rule",
      all(". (Join-Path" in read(f) and "Get-StudioProcesses" in read(f)
          for f in ("stop-studio.ps1", "restart-backend.ps1", "start.ps1")))
check("...and a backend-only restart leaves the session keepers alone",
      '$needle = "asset_studio.main"' in pr and "Get-StudioProcesses $PSScriptRoot -BackendOnly" in read("restart-backend.ps1"))

iss = read("installer/AssetStudio.iss")
check("the installer needs no administrator (the app writes data\\ beside itself)",
      "PrivilegesRequired=lowest" in iss and "{localappdata}\\Programs\\Asset Studio" in iss)
check("it runs setup offline with the UI it ships, and reports a failure with the log",
      "-SkipUiBuild" in iss and "ResultCode <> 0" in iss and "data\\logs" in iss)
check("uninstall removes the bin folder from PATH and keeps data\\",
      "RemoveFromUserPath" in iss and 'Name: "{app}\\data"' not in iss)
check("...and the code graph graphify made of the app's own folder", 'Name: "{app}\\graphify-out"' in iss)
check("the uninstaller stops this copy first", "stop-studio.ps1" in iss)
# A brace comment in the [Code] section ends at the first closing brace - so one that names
# {app} ended there, and the compiler read the rest of the line as code. Found by the compiler,
# minutes into a build; this finds it in seconds, compiling with no output against a tiny stage.
_iscc = next((p for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
                          Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
                          Path(r"C:\Program Files\Inno Setup 6\ISCC.exe")) if p.is_file()), None)
if _iscc:
    _st = TMP / "stage"
    (_st / "frontend" / "electron").mkdir(parents=True)
    shutil.copy2(ROOT / "frontend" / "electron" / "icon.ico", _st / "frontend" / "electron" / "icon.ico")
    _r = subprocess.run([str(_iscc), "/O-", "/Q", "/DStageDir=" + str(_st), "/DAppVersion=0.0.1",
                         str(ROOT / "installer" / "AssetStudio.iss")], capture_output=True, text=True, timeout=180)
    check("Inno Setup compiles the installer script", _r.returncode == 0, (_r.stdout + _r.stderr)[-500:])
else:
    print("  SKIP  Inno Setup is not installed here: the installer script is not compiled")

shutil.rmtree(TMP, ignore_errors=True)
print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

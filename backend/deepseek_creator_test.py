"""DeepSeek Creator mode: does the Studio start the official app the right way, and refuse cleanly?

Creator mode is a preset of the full DeepSeek Harness app (`cordis`), which the chat box's SDK runtime
does not have, so the Studio runs `dsh --profile web` for one folder. Everything that decides what that
app does is in ONE patch overlay, so the overlay is checked line by line here: new sessions start in
Creator mode, the folder is the workspace, a RIVER.AI key goes through the OpenAI-compatible gateway
(RIVER has no Messages endpoint, which DSH's DeepSeek route needs), and nothing lands in the user's
Documents.

No app is installed or started here: the install root is an empty temporary folder.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
TMP = Path(tempfile.mkdtemp())
os.environ["STUDIO_DSH_CREATOR_DIR"] = str(TMP / "creator")

from asset_studio import deepseek_creator as dc   # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s %s" % (name, extra))


print("\n-- the patch the app starts with --")
folder = str(TMP / "My Game")
p = dc.patch_text(folder, "deepseek")
check("new sessions start in Creator mode", "- id: agent-preset-registry\n  config:\n    default: cordis" in p, p)
check("optional session-log uploads stay off", "- id: session-log-deepseek\n  config:\n    enabled: false" in p)
check("DSH's first-use folder is the Studio's, not the user's Documents",
      "documentsDirectory: '%s'" % dc.DOCS in p)
check("the folder is the workspace, through the Studio's host plugin",
      "id: studio-workspace" in p and "          - '%s'" % folder in p and dc.PLUGIN.resolve().as_uri() in p)
check("a DeepSeek key needs no gateway rows", "llm-pi-ai" not in p and "agent-default-model" not in p)
r = dc.patch_text(folder, "river", "deepseek-ai/DeepSeek-V4.1-Flash", "https://api.river.ai/v1")
check("a RIVER.AI key goes through the OpenAI-compatible gateway",
      "api: openai-completions" in r and "baseURL: 'https://api.river.ai/v1'" in r
      and "apiKeyEnv: RIVER_API_KEY" in r, r)
check("...with RIVER's model, able to see images", "- id: 'deepseek-ai/DeepSeek-V4.1-Flash'" in r
      and "input: [text, image]" in r)
check("...and that model chosen for new sessions",
      "- id: agent-default-model\n  config:\n    provider: river\n    model: 'deepseek-ai/DeepSeek-V4.1-Flash'" in r)
q = dc.patch_text(r"C:\it's\here", "deepseek")
check("a quote in a path is doubled, a backslash kept", r"'C:\it''s\here'" in q, q)
check("no key is ever written into the patch", "rv_" not in r and "sk-" not in r)

print("\n-- the host plugin --")
src = dc.PLUGIN_SOURCE
check("it waits for the workspace registry", "inject = ['workspaceRegistry']" in src)
check("it does not wait for `ready`, which never fires for a patched plugin", "'ready'" not in src)
check("one bad folder is reported, not thrown", "catch (error)" in src)

print("\n-- the URL the app prints --")
m = dc.URL_RE.search("noise\ndsh web: http://127.0.0.1:52937/?token=nEyz-J3d_W8\nmore")
check("the URL with its token is read", m and m.group(1) == "http://127.0.0.1:52937/?token=nEyz-J3d_W8")
check("a URL on another host is not taken", not dc.URL_RE.search("dsh web: http://10.0.0.7:3080/?token=x"))

print("\n-- refusing cleanly --")
check("nothing installed in an empty root", dc.installed_version() == "" and not dc.ready())
got = dc.open_(str(TMP / "missing"))
check("a missing folder is refused by name", not got["ok"] and "no such folder" in got["error"], got)
(TMP / "game").mkdir()
got = dc.open_(str(TMP / "game"))
check("an existing folder without the app asks for the install", not got["ok"] and got.get("needs_install"), got)
st = dc.status()
check("the status says what is missing", st["ok"] and not st["ready"] and st["version"] == dc.VERSION
      and st["servers"] == [], st)
check("stopping with nothing running is not an error", dc.stop() == {"ok": True, "stopped": []})

print("\n-- servers that died are forgotten, and a stranger's pid is never stopped --")
dc._write_state({"x": {"folder": str(TMP / "game"), "pid": 4194303, "url": "http://127.0.0.1:1/?token=t"}})
check("a dead server is dropped from the list", dc.servers() == [] and json.loads(dc.STATE.read_text()) == {})
check("a live process that is not the app is not ours", not dc._ours(os.getpid()))

print("\n-- wiring --")
main = (Path("asset_studio") / "main.py").read_text(encoding="utf-8")
check("the routes are mounted", "deepseek_creator_router.router" in main)
check("the app's own pnpm is pinned and installed beside it", dc.PNPM and "pnpm@" in
      (Path("asset_studio") / "deepseek_creator.py").read_text(encoding="utf-8"))

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

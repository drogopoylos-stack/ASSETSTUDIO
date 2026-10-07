"""Does "New game" make a game every Studio tool can use, and does it refuse what it should?

Five parts.

The NAMES: a person's name becomes a title, a folder and an npm package without losing a game to
a device name, a dot, or a control character in the page title.

The TEMPLATES: what `template.json` claims (builders, targets, the import map) is what the files
really hold, both engines, so the manifest a new game ships with cannot drift from its code.

The GAME: both engines scaffolded for real, every placeholder filled, every JavaScript file parsed
by Node, the dev server started and asked for what the Studio will ask it for, and the asset index
shown the game to check it lists the two builders and nothing else.

The LIVE RELOAD: serve.mjs, run by real Node, says `reload` on /__studio_live within a second of
a change to the game's code, and says nothing for studio.edits.json, node_modules, the Studio's
own folders, a file that was only READ (this PC's disk rewrites access times, and fs.watch fires
for that) or a changed attribute. And the page script from index.html, run in Node against Node's
own EventSource, reloads on it, stays silent on a static host, and survives a dev-server restart.

The INSTALL: runs in the background (the request returns at once), reports running, done, failed
and timed out, survives a backend that forgot it, and never starts a second npm over the first.

Nothing here touches the real settings file: the one test that opens a workspace points the
settings object at a temporary file first, and checks the real one did not change.
"""
import collections
import http.server
import io
import json
import os
import queue
import re
import shutil
import socket
import stat as statmod
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import game_scaffold as gs         # noqa: E402
from asset_studio.config import DATA_DIR, SETTINGS_PATH, settings   # noqa: E402

ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def skipped(name, why):
    global skip
    skip += 1
    print("  SKIP  %s: %s" % (name, why))


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception as e:                           # noqa: BLE001
        print("        (raised %s: %s)" % (type(e).__name__, e))
        return False
    return False


NODE = shutil.which("node") or ""
ROOT = DATA_DIR / "tmp" / ("newgame-test-%d" % int(time.time()))
ROOT.mkdir(parents=True, exist_ok=True)

# The games are made in this test's own folder under data\. A game may only be made in a project
# or a standard folder, and data\ is one only while the Studio sits under the home folder - a copy
# on another drive (or the fresh-PC rig) refused the very first game and the suite stopped there.
from asset_studio import workspace as _ws             # noqa: E402
_real_bases = _ws._create_bases
_ws._create_bases = lambda: _real_bases() + [ROOT.resolve()]

# Every settings write this process makes, and the file it went to. Watching the real file's mtime
# instead would blame this test for a toggle the user made in the running Studio meanwhile.
WRITES: list = []
_real_update = settings.update


def _spy_update(patch):
    WRITES.append(Path(settings.path))
    return _real_update(patch)


settings.update = _spy_update


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def live_script(page: str) -> str:
    """The body of index.html's live-reload script: the attribute-less <script> naming the stream."""
    for body in re.findall(r"<script>(.*?)</script>", page, re.S):
        if "/__studio_live" in body:
            return body
    return ""


def get(url, method="GET", headers=None):
    import urllib.request
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ---------------------------------------------------------------------------
print("\nNAMES")
check("title keeps the name, drops control characters, collapses spaces",
      gs.title_of("  My\tCrate\n Game  ") == "My Crate Game")
check("title is capped at 64 characters", len(gs.title_of("x" * 200)) == 64)
check("a name with no letter or digit is refused", raises(lambda: gs.title_of("  !!! ~~ "), ValueError))
check("folder drops what Windows cannot keep", gs.folder_of('Sky <Crates>: "2"?') == "Sky Crates 2")
check("folder never ends in a dot or a space", gs.folder_of("Game. ") == "Game")
check("a device name gets a suffix (a folder called con cannot be made)", gs.folder_of("con") == "con game"
      and gs.folder_of("LPT1") == "LPT1 game" and gs.folder_of("console") == "console")
check("package name is what npm accepts", gs.slug_of("My Cool Game!") == "my-cool-game"
      and gs.slug_of("..._Hidden") == "hidden" and gs.slug_of("???") == "game")
p1, p2 = gs.port_for("crate-jumper"), gs.port_for("crate-jumper")
check("preferred port is stable per game and inside 5200-5899", p1 == p2 and 5200 <= p1 < 5900, p1)
check("two names spread over different ports", gs.port_for("a") != gs.port_for("b"))
check("engine aliases", gs.engine_of("three.js") == "three" and gs.engine_of("PC") == "playcanvas"
      and gs.engine_of("PlayCanvas") == "playcanvas")
check("an unknown engine is refused by name", raises(lambda: gs.engine_of("unity"), ValueError))
check('"" means the new_game_engine setting (three by default)', gs.engine_of("") in gs.ENGINES)

# ---------------------------------------------------------------------------
print("\nTEMPLATES")
BUILD_RE = re.compile(r"^export function (build\w+)\s*\(", re.M)
for eng in gs.ENGINES:
    meta = gs.template(eng)
    d = gs.TEMPLATES / eng
    assets = (d / "src" / "assets.js").read_text(encoding="utf-8")
    found = BUILD_RE.findall(assets)
    check("%s: template.json lists exactly the builders assets.js exports" % eng,
          found == [b["export"] for b in meta["builders"]], (found, meta["builders"]))
    targets_js = (d / "review" / "targets.js").read_text(encoding="utf-8")
    names = re.findall(r"name:\s*'([^']+)'", targets_js)
    check("%s: review targets match template.json" % eng, names == meta["targets"], names)
    page = (d / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<script type="importmap">\s*(\{.*?\})\s*</script>', page, re.S)
    imap = json.loads(m.group(1))["imports"] if m else None
    check("%s: index.html's import map is template.json's" % eng, imap == meta["import_map"], imap)
    live = live_script(page)
    check("%s: index.html carries the live-reload script, inline and classic (a module would wait for "
          "the whole page)" % eng, bool(live) and "EventSource('/__studio_live')" in live)
    check("%s: ...before the game's own module, after the import map" % eng,
          page.index('type="importmap"') < page.index("/__studio_live") < page.index('src="./src/main.js"'))
    main = (d / "src" / "main.js").read_text(encoding="utf-8")
    check("%s: main.js sets window.__game" % eng, "window.__game = {" in main)
    check("%s: main.js MERGES window.__review, never replaces it" % eng,
          "window.__review = window.__review || {}" in main and "review.actions = Object.assign(" in main)
    check("%s: every action template.json names is registered" % eng,
          all(("%s: {" % a) in main for a in meta["actions"]))
    # The LAST call is the one that starts the loop; the loop also re-arms itself inside `frame`.
    check("%s: saved edits applied before the loop starts" % eng,
          main.rindex("await within(4000, applySavedEdits())")
          < main.rindex("app.start()" if eng == "playcanvas" else "requestAnimationFrame(frame);"))
    tokens = set()
    for f in d.rglob("*"):
        if f.is_file():
            tokens |= set(re.findall(r"__GAME_([A-Z_]+)__", f.read_text(encoding="utf-8", errors="replace")))
    check("%s: every placeholder is one the scaffolder fills" % eng,
          tokens <= {"TITLE", "SLUG", "PORT", "STUDIO_ORIGIN"}, tokens)
LIVE_JS = {e: live_script((gs.TEMPLATES / e / "index.html").read_text(encoding="utf-8")) for e in gs.ENGINES}
check("both engines carry the very same live-reload script", len(set(LIVE_JS.values())) == 1)
SERVE_SRC = (gs.TEMPLATES / "common" / "serve.mjs").read_text(encoding="utf-8")
check("serve.mjs marks its pages with the Server-Timing entry the script looks for",
      "COMMON['Server-Timing'] = 'studio-live'" in SERVE_SRC and "m.name === 'studio-live'" in LIVE_JS["three"])
check("the heartbeat is every 25 s unless a test shortens it",
      re.search(r"STUDIO_LIVE_HEARTBEAT_MS\)\s*\|\|\s*25000\b", SERVE_SRC) is not None)
check("the live stream answers from the server's own route list, not from a file on disk",
      "if (rel === LIVE_PATH)" in SERVE_SRC and "const LIVE_PATH = '/__studio_live'" in SERVE_SRC)
common = gs._template_files("three")
check("serve.mjs and .gitignore come from the shared folder, template.json is never copied",
      "serve.mjs" in common and ".gitignore" in common and "gitignore" not in common
      and not any(k.endswith("template.json") for k in common))
stub = (gs.TEMPLATES / "runtime-stub.js").read_text(encoding="utf-8")
check("the stub has every runtime export", not gs._runtime_missing(stub))

# ---------------------------------------------------------------------------
print("\nTHE GAME")
games = {}
for eng in gs.ENGINES:
    t0 = time.perf_counter()
    r = gs.create("Test %s <Game> & Co" % eng, parent=str(ROOT), engine=eng, install=False, open_=False)
    dt = time.perf_counter() - t0
    games[eng] = Path(r["path"])
    g = games[eng]
    want = {".gitignore", "README.md", "index.html", "package.json", "review/targets.js", "serve.mjs",
            "src/assets.js", "src/main.js", "src/studio-runtime.js", "studio.game.json"}
    check("%s: made in %.2fs with exactly the contract's files" % (eng, dt), set(r["files"]) == want,
          sorted(set(r["files"]) ^ want))
    check("%s: the answer says where, what and what next" % eng,
          r["ok"] and r["engine"] == eng and r["install"] == {"state": "skipped"} and r["root"] is None
          and r["dev"]["script"] == "dev" and len(r["next"]) >= 5)
    left = [str(p.relative_to(g)) for p in g.rglob("*") if p.is_file()
            and "__GAME_" in p.read_text(encoding="utf-8", errors="replace")]
    check("%s: no placeholder left anywhere" % eng, not left, left)
    html = (g / "index.html").read_text(encoding="utf-8")
    check("%s: the title is escaped in the page" % eng, "<title>Test %s &lt;Game&gt; &amp; Co</title>" % eng in html)
    check("%s: the page the game ships listens for live reloads, the template's script unchanged" % eng,
          live_script(html) == LIVE_JS[eng] != "")
    pkg = json.loads((g / "package.json").read_text(encoding="utf-8"))
    check("%s: package.json runs serve.mjs and depends on the engine" % eng,
          pkg["scripts"]["dev"] == "node serve.mjs" and eng in pkg["dependencies"]
          and pkg["name"] == "test-%s-game-co" % eng and pkg["type"] == "module")
    man = json.loads((g / "studio.game.json").read_text(encoding="utf-8"))
    check("%s: studio.game.json names engine, entry, builders and runtime" % eng,
          man["engine"] == eng and man["entry"] == "index.html" and len(man["builders"]) == 2
          and man["runtime"]["file"] == "src/studio-runtime.js" and man["runtime"]["source"] in ("studio", "stub")
          and man["dev"]["port"] == r["dev"]["port"] and man["review"]["actions"] == ["jump"])
    check("%s: studio.game.json says this game reloads by itself (template %s, %s)"
          % (eng, man["made"]["template"], man["dev"].get("live_reload")),
          man["dev"].get("live_reload") == "/__studio_live" and man["made"]["template"] == gs.TEMPLATE_VERSION >= 2)
    check("%s: the next steps tell the agent a code edit reloads the page and a saved move does not" % eng,
          any("/__studio_live" in s and "studio.edits.json" in s for s in r["next"]), r["next"][-1])
    rt = (g / "src" / "studio-runtime.js").read_text(encoding="utf-8", errors="replace")
    check("%s: the runtime copy has every export (%s, %d bytes)" % (eng, r["runtime"]["source"], len(rt)),
          not gs._runtime_missing(rt))
    check("%s: the source the answer reports is the file on disk" % eng,
          (r["runtime"]["source"] == "stub") == ("A STUB" in rt[:200]))
    if NODE:
        bad = []
        for f in ["serve.mjs", "src/main.js", "src/assets.js", "src/studio-runtime.js", "review/targets.js"]:
            cp = subprocess.run([NODE, "--check", f], cwd=str(g), capture_output=True, text=True)
            if cp.returncode:
                bad.append((f, (cp.stderr or cp.stdout).strip()[:300]))
        check("%s: Node parses every JavaScript file as a module" % eng, not bad, bad)
    else:
        skipped("%s: node --check" % eng, "node is not on PATH")
    from asset_studio import assets_index
    rows = assets_index.scan(str(g))["items"]
    check("%s: the Library lists the two builders and nothing else" % eng,
          sorted((x["type"], x["name"]) for x in rows) == [("code", "buildCrate"), ("code", "buildTree")],
          [(x["type"], x["name"], x["file"]) for x in rows])

check("the same name again is refused as existing and not empty",
      raises(lambda: gs.create("Test three <Game> & Co", parent=str(ROOT), engine="three", install=False, open_=False),
             FileExistsError))
empty = ROOT / "Empty Already"
empty.mkdir()
r = gs.create("Empty Already", parent=str(ROOT), engine="three", install=False, open_=False)
check("an existing EMPTY folder is used", r["path"] == str(empty) and (empty / "index.html").is_file())
check("a bad engine is refused before anything is written",
      raises(lambda: gs.create("Never Made", parent=str(ROOT), engine="godot", install=False, open_=False), ValueError)
      and not (ROOT / "Never Made").exists())
check("a folder outside home and the open projects is refused",
      raises(lambda: gs.create("Nope", parent="C:/Windows/Temp", engine="three", install=False, open_=False),
             PermissionError))
(ROOT / "a-file.txt").write_text("x", encoding="utf-8")
check("a parent that is a file is refused",
      raises(lambda: gs.create("Nope", parent=str(ROOT / "a-file.txt"), engine="three", install=False, open_=False),
             NotADirectoryError))

real = gs._install_runtime


def broken(*a, **k):
    raise OSError("disk full (test)")


gs._install_runtime = broken
try:
    check("a failure halfway raises", raises(lambda: gs.create("Half Made", parent=str(ROOT), engine="three",
                                                              install=False, open_=False), OSError))
finally:
    gs._install_runtime = real
check("...and removes what it wrote, so the same name works next time", not (ROOT / "Half Made").exists()
      and gs.create("Half Made", parent=str(ROOT), engine="three", install=False, open_=False)["ok"])

# where a game goes when nobody says
orig_get = settings.get


def with_setting(key, value):
    def g(k, default=None):
        return value if k == key else orig_get(k, default)
    return g


settings.get = with_setting("new_game_parent", str(ROOT / "from-setting"))
try:
    p, why = gs.default_parent(str(games["three"]))
    check("the new_game_parent setting wins", why == "setting" and p == ROOT / "from-setting")
finally:
    settings.get = orig_get
settings.get = with_setting("new_game_parent", "")
try:
    p, why = gs.default_parent(str(games["three"]))
    check("else beside the project the caller is in", why == "beside" and p == ROOT)
    p, why = gs.default_parent("")
    check("else the Desktop", why == "home" and p.name in ("Desktop", Path.home().name))
    d = gs.defaults(str(games["three"]))
    check("defaults: parent, engine, both engines, npm and runtime facts",
          d["parent"] == str(ROOT) and d["engine"] in gs.ENGINES and [e["id"] for e in d["engines"]] == list(gs.ENGINES)
          and "found" in d["npm"] and "ready" in d["runtime"])
finally:
    settings.get = orig_get

# ---------------------------------------------------------------------------
print("\nTHE DEV SERVER")
if not NODE:
    skipped("serve.mjs", "node is not on PATH")
else:
    from asset_studio import dev_server
    g = games["three"]
    port = free_port()
    procs = []

    def serve(p):
        pr = subprocess.Popen([NODE, "serve.mjs"], cwd=str(g), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env=dict(os.environ, PORT=str(p)))
        procs.append(pr)
        box = {}

        def pump():
            for raw in pr.stdout:
                m = dev_server._URL_RE.search(raw)
                if m and "url" not in box:
                    box["url"] = m.group(0).decode()
        threading.Thread(target=pump, daemon=True).start()
        t = time.time()
        while "url" not in box and time.time() - t < 8:
            time.sleep(0.05)
        return box.get("url", "")

    try:
        url = serve(port)
        check("prints its URL the way dev_server.py reads it", url == "http://127.0.0.1:%d/" % port, url)
        base = url.rstrip("/")
        st, h, body = get(base + "/")
        check("/ is index.html as text/html", st == 200 and h.get("Content-Type", "").startswith("text/html")
              and b"importmap" in body)
        st, h, _ = get(base + "/src/main.js")
        check("a module is text/javascript, never cached, readable cross-origin",
              st == 200 and h.get("Content-Type", "").startswith("text/javascript")
              and h.get("Cache-Control") == "no-store" and h.get("Access-Control-Allow-Origin") == "*")
        st, _, body = get(base + "/__studio_review__")
        check("the isolate review's page carries the game's import map",
              st == 200 and b'"three": "./node_modules/three/build/three.module.js"' in body)
        st, h, body = get(base + "/index.html", headers={"Range": "bytes=0-9"})
        check("a byte range answers 206 with ten bytes", st == 206 and len(body) == 10
              and h.get("Content-Range", "").startswith("bytes 0-9/"))
        st, h, body = get(base + "/src/assets.js", method="HEAD")
        check("HEAD sends the headers and no body", st == 200 and body == b"" and int(h.get("Content-Length", 0)) > 1000)
        st, _, _ = get(base + "/studio.edits.json")
        check("a missing file is 404 (the runtime reads that as no edits)", st == 404)
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/..%5c..%5c..%5cWindows%5cwin.ini")
        check("a path that climbs out of the game is refused", c.getresponse().status == 403)
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/src")
        resp = c.getresponse()
        check("a folder without its slash is redirected to it", resp.status == 301 and resp.getheader("Location") == "/src/")
        c.close()
        url2 = serve(port)
        check("a taken port walks to the next one and says which", url2 == "http://127.0.0.1:%d/" % (port + 1), url2)
    finally:
        for pr in procs:
            try:
                pr.kill()
                pr.wait(timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# The live reload's instruments: a listener on the stream, index.html's script run by Node, and a
# static host that has never heard of serve.mjs.
# ---------------------------------------------------------------------------
# Runs index.html's live-reload script the way a page runs it, with Node's own EventSource (the
# WHATWG one, from undici: a 404 or a text/html answer closes it, a refused connection retries,
# as in a browser), and prints what the script did as JSON lines.
HARNESS_JS = r"""
import fs from 'node:fs';
import vm from 'node:vm';

// argv: the page file, the URL it is 'at', and a mode. The mode says what the page's navigation
// entry holds for Server-Timing: served (serve.mjs's mark), static (none: a page nobody marked),
// unknown (no Server-Timing at all: an insecure context, an older browser). "+review" adds the
// review harness's window.__review; "+file" makes it a file:// page.
const [, , file, pageUrl, mode] = process.argv;
const say = (ev, extra = {}) => console.log(JSON.stringify({ ev, ...extra }));
const body = [...fs.readFileSync(file, 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1]).find((s) => s.includes('/__studio_live'));
if (!body) { say('no-script'); process.exit(2); }
const Real = globalThis.EventSource;
let made = 0;
class Watched extends Real {
  constructor(url, init) {
    const abs = new URL(url, pageUrl).href;
    super(abs, init);
    made += 1;
    say('constructed', { url: abs });
    this.addEventListener('open', () => say('open'));
    this.addEventListener('error', () => say('error', { readyState: this.readyState }));
  }
  close() { super.close(); say('closed', { readyState: this.readyState }); }
}
const kind = mode.split('+')[0];
const serverTiming = kind === 'served' ? [{ name: 'studio-live', duration: 0, description: '' }]
  : kind === 'static' ? [] : undefined;
const window = {};
if (mode.includes('+review')) window.__review = { ready: true, step() { return 0; } };
vm.runInContext(body, vm.createContext({
  window,
  location: {
    href: pageUrl,
    protocol: mode.includes('+file') ? 'file:' : new URL(pageUrl).protocol,
    reload() { say('reload'); process.exit(0); },
  },
  performance: { getEntriesByType: (t) => (t === 'navigation' ? [{ serverTiming }] : []) },
  EventSource: Watched,
}));
say('ran', { made });
setTimeout(() => { say('done', { made }); process.exit(0); }, Number(process.env.LIVEPAGE_MS || 8000));
"""


def fmt(secs):
    return "never" if secs is None else "%d ms" % round(secs * 1000)


class Stream:
    """One listener on /__studio_live: every line it is sent, with when it came, read on its own thread."""

    def __init__(self, port, path="/__studio_live"):
        import http.client
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        self.conn.request("GET", path, headers={"Accept": "text/event-stream"})
        self.resp = self.conn.getresponse()
        self.status = self.resp.status
        self.headers = {k.lower(): v for k, v in self.resp.getheaders()}
        self.q = queue.Queue()
        if self.status == 200:
            threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            for raw in self.resp:
                self.q.put((time.perf_counter(), raw.decode("utf-8", "replace").rstrip("\r\n")))
        except Exception:                            # noqa: BLE001 — closed under us: the end
            pass
        self.q.put((time.perf_counter(), None))

    def lines(self, secs):
        """Every line sent within secs from now."""
        out, end = [], time.perf_counter() + secs
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                return out
            try:
                line = self.q.get(timeout=left)[1]
            except queue.Empty:
                return out
            if line is None:
                return out
            out.append(line)

    def close(self):
        try:
            self.conn.sock.shutdown(socket.SHUT_RDWR)
        except Exception:                            # noqa: BLE001
            pass
        self.conn.close()


def reloads(s, act, quiet=1.5):
    """Do act (however long it takes); every `data: reload` from its start until `quiet` seconds
    after it returned, as (seconds after act returned, the `: changed` comment before it)."""
    while not s.q.empty():
        s.q.get_nowait()
    act()
    done = time.perf_counter()
    out, note, end = [], "", done + quiet
    while True:
        left = end - time.perf_counter()
        if left <= 0:
            return out
        try:
            t, line = s.q.get(timeout=left)
        except queue.Empty:
            return out
        if line is None:
            return out
        if line.startswith(": changed"):
            note = line[2:]
        elif line == "data: reload":
            out.append((round(t - done, 3), note))


def after(s, act, window=1.0):
    """Do act; the seconds until `data: reload` came (None if not within window), and the
    `: changed ...` comment that named the files."""
    while not s.q.empty():
        s.q.get_nowait()
    t0 = time.perf_counter()
    act()
    note, end = "", t0 + window
    while True:
        left = end - time.perf_counter()
        if left <= 0:
            return None, note
        try:
            t, line = s.q.get(timeout=left)
        except queue.Empty:
            return None, note
        if line is None:
            return None, "the stream closed"
        if line.startswith(": changed"):
            note = line[2:]
        elif line == "data: reload":
            return t - t0, note


class Page:
    """index.html's live-reload script, run by the harness: what it did, as it happened."""

    def __init__(self, html, url, mode, ms=8000):
        self.p = subprocess.Popen([NODE, "--no-warnings", "--experimental-eventsource", str(HARNESS),
                                   str(html), url, mode], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  env=dict(os.environ, LIVEPAGE_MS=str(ms)))
        LIVE_PROCS.append(self.p)
        self.q = queue.Queue()
        self.seen: list = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for raw in self.p.stdout:
            text = raw.decode("utf-8", "replace").strip()
            try:
                ev = json.loads(text)
            except ValueError:
                ev = {"ev": "text", "text": text[:200]}
            self.q.put((time.perf_counter(), ev))
        self.q.put((time.perf_counter(), {"ev": "exit"}))

    def until(self, name, secs, since=None, **match):
        """Seconds from `since` (default now) until the event `name` with these fields; None if not
        within secs of now."""
        t0 = time.perf_counter()
        start = t0 if since is None else since
        end = t0 + secs
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                return None
            try:
                t, ev = self.q.get(timeout=left)
            except queue.Empty:
                return None
            self.seen.append(ev)
            if ev.get("ev") == name and all(ev.get(k) == v for k, v in match.items()):
                return t - start
            if ev.get("ev") == "exit":
                return None

    def names(self):
        return [e.get("ev") for e in self.seen]


class Host(http.server.BaseHTTPRequestHandler):
    """A static host: the page at /, and a 404 for anything else, or (spa) the page again, 200."""
    page = b""
    spa = False
    hits: collections.Counter = collections.Counter()

    def do_GET(self):                                # noqa: N802 — the base class's name
        path = self.path.split("?")[0]
        Host.hits[path] += 1
        if path == "/" or Host.spa:
            body, code, ctype = Host.page, 200, "text/html; charset=utf-8"
        else:
            body, code, ctype = b"not found", 404, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


LIVE_PROCS: list = []
HARNESS = ROOT / "livepage.mjs"


def start_serve(game, port, args=(), env=None):
    """serve.mjs in `game`, preferring `port`: (process, the URL it printed)."""
    from asset_studio import dev_server
    pr = subprocess.Popen([NODE, "serve.mjs", *args], cwd=str(game), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, env=dict(os.environ, PORT=str(port), **(env or {})))
    LIVE_PROCS.append(pr)
    box = {}
    pr.lines = []                                    # everything it printed, for the checks

    def pump():
        for raw in pr.stdout:
            pr.lines.append(raw.decode("utf-8", "replace").rstrip())
            m = dev_server._URL_RE.search(raw)
            if m and "url" not in box:
                box["url"] = m.group(0).decode()
    threading.Thread(target=pump, daemon=True).start()
    t = time.time()
    while "url" not in box and time.time() - t < 8:
        time.sleep(0.05)
    return pr, box.get("url", "")


def stop(pr):
    try:
        pr.kill()
        pr.wait(timeout=5)
    except Exception:                                # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
print("\nTHE LIVE RELOAD")
if not NODE:
    skipped("live reload", "node is not on PATH")
else:
    # A game of its own: the sections before and after this one change the other two.
    LG = Path(gs.create("Live Reload", parent=str(ROOT), engine="three", install=False, open_=False)["path"])

    def write(rel, text="x\n", append=False):
        p = LG / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(text)

    host = None
    try:
        port = free_port()
        pr, url = start_serve(LG, port)
        base = url.rstrip("/")
        check("serve.mjs starts with live reload on and prints its URL", url == "http://127.0.0.1:%d/" % port, url)
        st, h, _ = get(base + "/")
        check("the page it serves carries the mark (Server-Timing: studio-live)",
              st == 200 and h.get("Server-Timing") == "studio-live", h)
        st, h, body = get(base + "/__studio_live", method="HEAD")
        check("HEAD /__studio_live: 200, an event stream, no body",
              st == 200 and h.get("Content-Type", "").startswith("text/event-stream") and body == b"", (st, h))
        s = Stream(port)
        hello = s.lines(0.5)
        check("GET /__studio_live: 200 text/event-stream, never cached, open to the Studio's origin, "
              "and a hello that asks for a 1 s reconnect",
              s.status == 200 and s.headers.get("content-type", "").startswith("text/event-stream")
              and s.headers.get("cache-control") == "no-store" and s.headers.get("access-control-allow-origin") == "*"
              and "retry: 1000" in hello, (s.status, s.headers, hello))

        assets = LG / "src" / "assets.js"
        lat, note = after(s, lambda: write("src/assets.js", "\n// an edit\n", append=True))
        check("an edit to src/assets.js: `reload` in %s (within 1 s), naming the file" % fmt(lat),
              lat is not None and lat < 1.0 and "src/assets.js" in note, note)

        def touch():
            st_ = os.stat(assets)
            os.utime(assets, ns=(st_.st_atime_ns, st_.st_mtime_ns + 2 * 10**9))
        lat, note = after(s, touch)
        check("a bare touch (the same bytes, a newer modified time): `reload` in %s" % fmt(lat),
              lat is not None and lat < 1.0, note)
        lat, note = after(s, lambda: write("studio.edits.json",
                                           json.dumps({"version": 1, "parts": {"crate-1": {"pos": [1, 0, 1]}}})))
        check("studio.edits.json saved: nothing within 1 s (the page already shows that move)", lat is None, note)
        lat, note = after(s, lambda: write("node_modules/three/build/three.module.js", "// installed\n"))
        check("a file in node_modules: nothing within 1 s", lat is None, note)
        OWN = ["studio.edits.json.bak", "studio.edits.json.tmp-4242", "src/assets.buildCrate.edits.json",
               ".studio/install.log", ".git/HEAD", ".vscode/settings.json", "src/.assets.js.swp",
               "graphify-out/graph.json", "dist/main.js", "debug.log", "src/assets.js.bak", "notes.tmp",
               "src/assets.js~", "src/node_modules/x/index.js"]
        lat, note = after(s, lambda: [write(r) for r in OWN])
        check("the Studio's own files, dot-folders, a build, logs, backups, swap files (%d paths): nothing "
              "within 1 s" % len(OWN), lat is None, note)

        # READ, not written. This PC's disk rewrites a file's access time when it is read (last-access
        # updates on) once the old one is an hour stale, and fs.watch fires for it: counted blindly, a
        # page would reload because it had just loaded. Two hours back, then the page's own requests.
        loaded = ["index.html", "src/main.js", "src/assets.js", "src/studio-runtime.js", "review/targets.js"]
        for rel in loaded:
            st_ = os.stat(LG / rel)
            os.utime(LG / rel, ns=(st_.st_atime_ns - 2 * 3600 * 10**9, st_.st_mtime_ns))
        time.sleep(0.4)
        was = {rel: os.stat(LG / rel).st_atime_ns for rel in loaded}
        lat, note = after(s, lambda: [get(base + ("/" if rel == "index.html" else "/" + rel)) for rel in loaded])
        rewrote = sum(os.stat(LG / rel).st_atime_ns > was[rel] + 3600 * 10**9 for rel in loaded)
        check("the page's files only READ (the disk rewrote %d of %d access times): nothing within 1 s"
              % (rewrote, len(loaded)), lat is None, note)

        def attrs():
            os.chmod(assets, statmod.S_IREAD)
            time.sleep(0.05)
            os.chmod(assets, statmod.S_IREAD | statmod.S_IWRITE)
        lat, note = after(s, attrs)
        check("an attribute changed and changed back (read-only): nothing within 1 s", lat is None, note)

        def burst():
            for i in range(5):
                write("src/assets.js", "// burst %d\n" % i, append=True)
                time.sleep(0.01)
        lat, note = after(s, burst)
        more = [ln for ln in s.lines(0.6) if ln == "data: reload"]
        check("five writes in 50 ms: one `reload` (in %s), then quiet" % fmt(lat),
              lat is not None and lat < 1.0 and not more, (note, more))
        lat, note = after(s, lambda: (LG / "assets").mkdir())
        check("an empty new folder: nothing (nothing in it can have changed)", lat is None, note)
        lat, note = after(s, lambda: write("assets/level.json", '{"size": 3}\n'))
        check("a new file in it: `reload` in %s" % fmt(lat), lat is not None and lat < 1.0
              and "assets/level.json" in note, note)
        lat, note = after(s, lambda: os.replace(LG / "assets" / "level.json", LG / "assets" / "level2.json"))
        check("a rename: `reload` in %s" % fmt(lat), lat is not None and lat < 1.0, note)
        lat, note = after(s, lambda: os.remove(LG / "assets" / "level2.json"))
        check("a delete: `reload` in %s" % fmt(lat), lat is not None and lat < 1.0, note)

        # A BURST overflows the watcher's buffer: its events stop naming files, one comes about every
        # 50 ms until the burst ends, and serve.mjs compares every file instead. npm install is such
        # a burst inside node_modules, and must reload nothing; a folder of new assets must reload
        # once, when it has all landed, not once per file and not halfway.
        # Written by Node in a tight loop, as fast as npm writes: from Python the files trickle in
        # slowly enough that the watcher sometimes keeps up, and then the comparison is never tested.
        def burst(folder, n=3000):
            subprocess.run([NODE, "-e", "const fs = require('fs'), path = require('path');"
                            "const [d, n] = process.argv.slice(1); fs.mkdirSync(d, {recursive: true});"
                            "for (let i = 0; i < +n; i++) fs.writeFileSync(path.join(d, "
                            "'file-with-a-longish-name-' + String(i).padStart(4, '0') + '.js'), 'x');",
                            str(LG / folder), str(n)], check=True, timeout=60)
        got = reloads(s, lambda: burst("node_modules/burst"))
        check("3000 files written into node_modules at once (an npm install): no reload", not got, got)
        def how(got):
            if not got:
                return "no reload"
            return "the watcher overflowed, so every file was compared" if "comparing every file" in got[-1][1] \
                else "the watcher named the files"
        got = reloads(s, lambda: burst("assets/burst"))
        # Negative would mean it fired while files were still landing: a page loaded halfway.
        check("3000 new files in the game at once: one reload, %s after the last landed (%s)"
              % (fmt(got[0][0]) if got else "never", how(got)), len(got) == 1 and 0 <= got[0][0] < 1.0, got)
        got = reloads(s, lambda: shutil.rmtree(LG / "assets" / "burst"))
        check("...and all 3000 deleted at once: one reload (%s)" % how(got), len(got) == 1, got)

        # AN EDIT DURING npm install. Measured here: with another process flooding node_modules, an
        # edit's own event is lost in an overflow about one time in five, and without the comparison
        # that edit would never reload the page. Ten edits through one flood: every one must be told.
        edits = ["src/during-%d.js" % i for i in range(10)]

        def flood_and_edit():
            w = subprocess.Popen([NODE, "-e", "const fs = require('fs'), path = require('path');"
                                  "const d = process.argv[1]; fs.mkdirSync(d, {recursive: true});"
                                  "for (let i = 0; i < 12000; i++) fs.writeFileSync(path.join(d, "
                                  "'file-with-a-longish-name-' + i + '.js'), 'x');",
                                  str(LG / "node_modules" / "flood")])
            time.sleep(0.3)
            for i, rel in enumerate(edits):
                write(rel, "export const n = %d;\n" % i)
                time.sleep(0.25)
            w.wait(timeout=60)
        got = reloads(s, flood_and_edit)
        told = {f.strip() for _, note in got for f in note.replace("changed ", "", 1)
                .replace(" (found by comparing every file)", "").split(",")}
        missed = [e for e in edits if e not in told]
        found_by_scan = sum(1 for _, note in got if "comparing every file" in note)
        check("10 edits while 12000 files flood node_modules: every edit reloads the page (%d reloads, %d of "
              "them found by comparing every file, missed %s)" % (len(got), found_by_scan, missed or "none"),
              not missed, got)

        def listening():
            return get(base + "/__studio_live", method="HEAD")[1].get("X-Studio-Listeners")
        s2 = Stream(port)
        s2.lines(0.3)
        n_two = listening()
        lat, note = after(s, lambda: write("src/main.js", "\n// two listening\n", append=True))
        both = "data: reload" in s2.lines(0.5)
        check("two pages listening (HEAD counts %s): both told (%s)" % (n_two, fmt(lat)),
              n_two == "2" and lat is not None and both)
        s2.close()
        time.sleep(0.3)
        n_one = listening()
        lat, note = after(s, lambda: write("src/main.js", "// one left\n", append=True))
        st, _, _ = get(base + "/")
        check("one page leaves: the server forgets it (HEAD counts %s), the other is still told, and the "
              "server still serves" % n_one, n_one == "1" and lat is not None and st == 200, note)
        s.close()
        time.sleep(0.3)
        check("the last page leaves: nobody is listening (%s)" % listening(), listening() == "0")

        port2 = free_port()
        pr2, url2 = start_serve(LG, port2, env={"STUDIO_LIVE_HEARTBEAT_MS": "250"})
        s3 = Stream(port2)
        got = s3.lines(1.2)
        check("the heartbeat: a `: ping` comment on schedule (%d in 1.2 s at 250 ms)" % got.count(": ping"),
              got.count(": ping") >= 3, got)
        s3.close()
        stop(pr2)

        # WHERE fs.watch NEVER FIRES (some network drives), STUDIO_LIVE_POLL=1 compares every file
        # every 750 ms: the same comparison an overflowing burst falls back to, run on a clock.
        port4 = free_port()
        pr4, url4 = start_serve(LG, port4, env={"STUDIO_LIVE_POLL": "1"})
        s4 = Stream(port4)
        s4.lines(0.3)
        check("STUDIO_LIVE_POLL=1: it says it is comparing files on a clock",
              any("STUDIO_LIVE_POLL=1" in ln and "750 ms" in ln for ln in pr4.lines), pr4.lines)
        lat, note = after(s4, lambda: write("src/assets.js", "// polled\n", append=True), window=1.6)
        check("...an edit: `reload` in %s (a 750 ms clock), found by comparing every file" % fmt(lat),
              lat is not None and lat < 1.6 and "src/assets.js" in note and "comparing every file" in note, note)
        lat, note = after(s4, lambda: write("studio.edits.json", '{"version": 1, "parts": {}}'), window=1.6)
        check("...studio.edits.json saved: nothing within 1.6 s", lat is None, note)
        for rel in loaded:
            st_ = os.stat(LG / rel)
            os.utime(LG / rel, ns=(st_.st_atime_ns - 2 * 3600 * 10**9, st_.st_mtime_ns))
        lat, note = after(s4, lambda: [get(url4.rstrip("/") + ("/" if rel == "index.html" else "/" + rel))
                                       for rel in loaded], window=1.6)
        check("...the page's files only read: nothing within 1.6 s", lat is None, note)
        lat, note = after(s4, lambda: os.remove(LG / "src" / "assets.js.bak"), window=1.6)
        check("...an ignored file deleted (src/assets.js.bak): nothing", lat is None, note)
        write("src/extra.js", "export const extra = 1;\n")
        s4.lines(1.6)
        lat, note = after(s4, lambda: os.remove(LG / "src" / "extra.js"), window=1.6)
        check("...a game file deleted: `reload` in %s" % fmt(lat), lat is not None and "src/extra.js" in note, note)
        s4.close()
        stop(pr4)
        for args, env, how in ((["--no-live"], {}, "--no-live"), ([], {"STUDIO_LIVE": "0"}, "STUDIO_LIVE=0")):
            pr3, url3 = start_serve(LG, free_port(), args=args, env=env)
            st1, h1, _ = get(url3.rstrip("/") + "/")
            st2, _, _ = get(url3.rstrip("/") + "/__studio_live")
            check("%s: no mark on the page, and /__studio_live is 404" % how,
                  st1 == 200 and "Server-Timing" not in h1 and st2 == 404, (url3, h1, st2))
            stop(pr3)

        # THE PAGE SCRIPT, the very text of the scaffolded index.html.
        es_ok = subprocess.run([NODE, "--no-warnings", "--experimental-eventsource", "-e",
                                "process.exit(typeof EventSource === 'function' ? 0 : 3)"],
                               capture_output=True).returncode == 0
        if not es_ok:
            skipped("the page script", "this Node has no EventSource (22.3+ has it behind --experimental-eventsource)")
        else:
            HARNESS.write_text(HARNESS_JS, encoding="utf-8")
            page = LG / "index.html"
            pg = Page(page, url, "served")
            opened = pg.until("open", 4)
            t_edit = time.perf_counter()
            write("src/assets.js", "// the page is listening\n", append=True)
            lat = pg.until("reload", 1.0, since=t_edit)
            check("the page script where serve.mjs served it: connects, and reloads %s after an edit" % fmt(lat),
                  opened is not None and lat is not None and lat < 1.0, pg.names())
            stop(pg.p)

            Host.page = page.read_bytes()
            host = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Host)
            threading.Thread(target=host.serve_forever, daemon=True).start()
            hbase = "http://127.0.0.1:%d/" % host.server_address[1]

            Host.hits.clear()
            pg = Page(page, hbase, "static", ms=1500)
            pg.until("done", 5)
            check("a static host, in a browser that shows Server-Timing (https, localhost): no mark, so no request "
                  "at all (%d)" % Host.hits["/__studio_live"],
                  pg.names()[-1:] == ["done"] and pg.seen[-1].get("made") == 0 and Host.hits["/__studio_live"] == 0,
                  (pg.names(), dict(Host.hits)))
            Host.hits.clear()
            pg = Page(page, hbase, "unknown", ms=3000)
            closed = pg.until("closed", 3)
            pg.until("done", 5)
            check("a static host that answers 404, in a browser with no Server-Timing: one try, closed at once "
                  "(readyState 2), no reload, no retry (%d request in 3 s)" % Host.hits["/__studio_live"],
                  closed is not None and "reload" not in pg.names() and Host.hits["/__studio_live"] == 1
                  and any(e.get("ev") == "error" and e.get("readyState") == 2 for e in pg.seen),
                  (pg.names(), dict(Host.hits)))
            Host.hits.clear()
            Host.spa = True
            pg = Page(page, hbase, "unknown", ms=3000)
            closed = pg.until("closed", 3)
            pg.until("done", 5)
            Host.spa = False
            check("a single-page host that answers every path with the page (200 text/html): closed, no reload, "
                  "one request", closed is not None and "reload" not in pg.names()
                  and Host.hits["/__studio_live"] == 1, (pg.names(), dict(Host.hits)))
            pg = Page(page, "http://127.0.0.1:%d/" % free_port(), "unknown", ms=3500)
            pg.until("done", 6)
            errs = [e for e in pg.seen if e.get("ev") == "error"]
            check("nothing listening at all: closed after the first refusal, not retried every second (%d error)"
                  % len(errs), "closed" in pg.names() and len(errs) == 1, pg.names())
            for mode, why in (("served+review", "inside a Studio review capture (its window.__review is there first)"),
                              ("served+file", "on a file:// page")):
                pg = Page(page, url, mode, ms=800)
                pg.until("done", 4)
                check("the page script %s: never connects" % why,
                      pg.names()[-1:] == ["done"] and pg.seen[-1].get("made") == 0, pg.names())

            pg = Page(page, url, "served", ms=20000)
            opened = pg.until("open", 4)
            stop(pr)
            dropped = pg.until("error", 3, readyState=0)
            pr, url_again = start_serve(LG, port)
            back = pg.until("open", 8)
            t_edit = time.perf_counter()
            write("src/assets.js", "// after a restart\n", append=True)
            lat = pg.until("reload", 1.0, since=t_edit)
            check("the dev server restarts under an open page: it keeps trying (readyState 0, never closed), is "
                  "back in %s, and the next edit reloads it in %s" % (fmt(back), fmt(lat)),
                  opened is not None and dropped is not None and "closed" not in pg.names() and url_again == url
                  and back is not None and lat is not None and lat < 1.0, (pg.names(), url_again))
    finally:
        for p_ in LIVE_PROCS:
            stop(p_)
        if host:
            host.shutdown()
            host.server_close()

# ---------------------------------------------------------------------------
print("\nTHE INSTALL")
if not NODE:
    skipped("install", "node is not on PATH")
else:
    g = games["playcanvas"]
    fake_ok = [NODE, "-e", "setTimeout(()=>{require('fs').mkdirSync('node_modules/fake',{recursive:true});"
                           "console.log('added 1 package in 0.4s')},400)"]
    t0 = time.perf_counter()
    r = gs.start_install(g, cmd=fake_ok)
    dt = time.perf_counter() - t0
    check("returns at once, running (%.3fs)" % dt, r["state"] == "running" and dt < 1.0, r)
    s = gs.install_status(str(g))
    check("status says installing while it runs", s["installing"] and not s["done"] and not s["ok"], s)
    again = gs.start_install(g, cmd=fake_ok)
    check("a second install while one runs answers with the first", again.get("already") and again["pid"] == r["pid"])
    t = time.time()
    while gs.install_status(str(g))["installing"] and time.time() - t < 20:
        time.sleep(0.1)
    s = gs.install_status(str(g))
    check("done, ok, node_modules there, the log's tail says what npm said",
          s["done"] and s["ok"] and s["node_modules"] and s["state"] == "done" and "added 1 package" in s["tail"], s)
    st = json.loads((g / ".studio" / "install.json").read_text(encoding="utf-8"))
    check(".studio/install.json records the end", st["state"] == "done" and st["ok"] and st["code"] == 0)
    gs._installs.clear()
    s = gs.install_status(str(g))
    check("a backend that never saw the install reads the record", s["done"] and s["ok"] and s["state"] == "done", s)

    g2 = games["three"]
    r = gs.start_install(g2, cmd=[NODE, "-e", "console.error('npm ERR! code E404'); process.exit(3)"])
    t = time.time()
    while gs.install_status(str(g2))["installing"] and time.time() - t < 20:
        time.sleep(0.1)
    s = gs.install_status(str(g2))
    check("a failing install: done, not ok, the exit code and the error in the tail",
          s["done"] and not s["ok"] and s["code"] == 3 and s["state"] == "failed" and "E404" in s["tail"], s)
    r = gs.start_install(g2, cmd=[NODE, "-e", "setTimeout(()=>{},30000)"], timeout=0.8)
    t = time.time()
    while gs.install_status(str(g2))["installing"] and time.time() - t < 20:
        time.sleep(0.1)
    s = gs.install_status(str(g2))
    check("a stalled install is killed at its timeout and says so", s["done"] and not s["ok"] and s["timed_out"]
          and time.time() - t < 15, s)

    gs._installs.clear()
    (g2 / ".studio" / "install.json").write_text(json.dumps({"state": "running", "pid": 999999, "started": time.time() - 5}),
                                                 encoding="utf-8")
    s = gs.install_status(str(g2))
    check("an install whose backend died is 'stopped', judged by node_modules", s["state"] == "stopped" and s["done"]
          and not s["installing"] and not s["ok"], s)
    fresh = gs.create("Never Installed", parent=str(ROOT), engine="three", install=False, open_=False)
    s = gs.install_status(fresh["path"])
    check("a game never installed says so", s["state"] == "never" and not s["done"] and not s["installing"], s)
    (Path(fresh["path"]) / "node_modules").mkdir()
    s = gs.install_status(fresh["path"])
    check("node_modules put there by hand counts as installed", s["state"] == "present" and s["ok"] and s["done"], s)
    check("status of a folder outside home is refused",
          raises(lambda: gs.install_status("C:/Windows"), PermissionError))
    check("status of a folder that is not there is 404", raises(lambda: gs.install_status(str(ROOT / "nope")),
                                                              FileNotFoundError))

    # The whole path through create(), with npm replaced by a script that behaves like it.
    fake = ROOT / "fake-npm.cmd"
    fake.write_text("@echo off\r\nmkdir node_modules\\fake 2>nul\r\necho added 1 package in 0.1s\r\nexit /b 0\r\n",
                    encoding="utf-8")
    real_npm = gs._npm
    gs._npm = lambda: str(fake) if os.name == "nt" else ""
    try:
        if os.name == "nt":
            t0 = time.perf_counter()
            r = gs.create("Installs Itself", parent=str(ROOT), engine="three", install=True, open_=False)
            dt = time.perf_counter() - t0
            check("create with install returns in %.2fs with the install running" % dt,
                  r["install"]["state"] == "running" and dt < 3.0, r["install"])
            t = time.time()
            while gs.install_status(r["path"])["installing"] and time.time() - t < 20:
                time.sleep(0.1)
            s = gs.install_status(r["path"])
            check("...and the install finishes ok on its own", s["ok"] and s["state"] == "done", s)
        else:
            skipped("create with install", "the fake npm is a Windows .cmd")
        gs._npm = lambda: ""
        r = gs.create("No Npm Here", parent=str(ROOT), engine="three", install=True, open_=False)
        check("no npm on the PC: the game is made, the install says why it failed",
              r["ok"] and r["install"]["state"] == "failed" and "npm was not found" in r["install"]["error"])
    finally:
        gs._npm = real_npm

# ---------------------------------------------------------------------------
print("\nOPEN AS A WORKSPACE (on a temporary settings file)")
tmp_settings = ROOT / "settings.test.json"
old_path, old_cache = settings.path, settings._cache
settings.path, settings._cache = tmp_settings, None
try:
    from asset_studio import workspace
    workspace.invalidate_roots()
    r = gs.create("Opens Itself", parent=str(ROOT), engine="playcanvas", install=False, open_=True)
    roots = json.loads(tmp_settings.read_text(encoding="utf-8")).get("workspace_roots") or []
    check("open adds it as a workspace root", r["root"] and r["root"]["path"] == r["path"] and r["path"] in roots,
          (r.get("root"), roots))
finally:
    settings.path, settings._cache = old_path, old_cache
    from asset_studio import workspace
    workspace.invalidate_roots()

# ---------------------------------------------------------------------------
print("\nTHE ROUTES")
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from asset_studio.routers import workspace as ws_router
    app = FastAPI()
    app.include_router(ws_router.router)
    client = TestClient(app)
    res = client.post("/api/workspace/new-game", json={"name": "Route Game", "parent": str(ROOT), "engine": "three",
                                                       "install": False, "open": False})
    body = res.json()
    check("POST /new-game -> 200 {ok, path, engine, files, install, dev, next}",
          res.status_code == 200 and body["ok"] and body["engine"] == "three" and body["install"]["state"] == "skipped"
          and "serve.mjs" in body["files"] and body["dev"]["command"] == "node serve.mjs" and body["next"], body)
    res = client.post("/api/workspace/new-game", json={"name": "Route Game", "parent": str(ROOT), "install": False,
                                                       "open": False})
    check("the same name again -> 409", res.status_code == 409, res.text)
    res = client.post("/api/workspace/new-game", json={"name": "X", "parent": str(ROOT), "engine": "godot",
                                                       "install": False, "open": False})
    check("an unknown engine -> 400 naming the two", res.status_code == 400 and "playcanvas" in res.text, res.text)
    res = client.get("/api/workspace/new-game/status", params={"path": body["path"]})
    s = res.json()
    check("GET /new-game/status -> {installing, done, ok, tail}", res.status_code == 200
          and {"installing", "done", "ok", "tail"} <= set(s) and s["state"] == "never", s)
    res = client.get("/api/workspace/new-game/status", params={"path": "C:/Windows"})
    check("status outside the allowed places -> 403", res.status_code == 403, res.text)
    res = client.get("/api/workspace/new-game/defaults", params={"beside": body["path"]})
    d = res.json()
    check("GET /new-game/defaults -> parent beside the project, two engines",
          res.status_code == 200 and d["parent"] == str(ROOT) and len(d["engines"]) == 2, d)
except ImportError as e:
    skipped("routes", "fastapi test client missing: %s" % e)

# ---------------------------------------------------------------------------
check("the real settings.json was never written (%d write(s), all to the temporary file)" % len(WRITES),
      WRITES and all(w.resolve() != SETTINGS_PATH.resolve() for w in WRITES), WRITES)

shutil.rmtree(ROOT, ignore_errors=True)
print("\n%d passed, %d failed, %d skipped" % (ok, fail, skip))
sys.exit(1 if fail else 0)

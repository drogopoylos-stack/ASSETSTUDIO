// serve.mjs: this game's dev server. Static files with the right MIME types, and nothing else.
// `npm run dev` runs it, and it needs nothing installed: only Node's own modules.
//
// WHY NOT ANY STATIC SERVER. On Windows, Python's http.server and many others take MIME types from
// the registry, and on a machine where `.js` is registered as text/plain every module is served
// with a type the browser refuses to run: a black page, with no error in it that names the cause.
// This table cannot drift. Every answer also carries `Cache-Control: no-store`, so a reload always
// runs the file on disk, and `Access-Control-Allow-Origin: *`, because the Studio's editor imports
// this game's modules from its own origin and a module import across origins needs it.
//
// THE URL LINE IS LOAD-BEARING. The Studio starts a dev server by reading the first URL it prints,
// so this prints `Local:   http://127.0.0.1:<port>/` the way Vite does. When the port is taken it
// tries the next one, and the line always names the port it really got.
//
// /__studio_review__ is the page the Studio's isolate review mounts one builder on. A bare page
// has no import map, and `import 'three'` in the game's own modules cannot resolve there, so this
// one carries the map index.html declares.
//
// /__studio_live tells the open page to reload when a file of the game changes, so an agent that
// edits src/assets.js sees the new code in its live tab without asking for a reload. The LIVE
// RELOAD section below says which files count and why. `--no-live` or STUDIO_LIVE=0 turns it off.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const HOST = process.env.HOST || '127.0.0.1';
const flag = (name) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] || '' : '';
};
// The preferred port is chosen per game when the Studio makes it, so two new games are not both
// fighting for the same one; --port and PORT still win.
const FIRST = Number(flag('--port') || process.env.PORT || '__GAME_PORT__') || 5300;
const TRIES = 40;
const LIVE = !process.argv.includes('--no-live') && process.env.STUDIO_LIVE !== '0';

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.cjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.bmp': 'image/bmp',
  '.ktx2': 'image/ktx2',
  '.exr': 'image/x-exr',
  '.hdr': 'application/octet-stream',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.bin': 'application/octet-stream',
  '.obj': 'text/plain; charset=utf-8',
  '.mtl': 'text/plain; charset=utf-8',
  '.fbx': 'application/octet-stream',
  '.drc': 'application/octet-stream',
  '.basis': 'application/octet-stream',
  '.wasm': 'application/wasm',
  '.mp3': 'audio/mpeg',
  '.ogg': 'audio/ogg',
  '.oga': 'audio/ogg',
  '.opus': 'audio/ogg',
  '.wav': 'audio/wav',
  '.m4a': 'audio/mp4',
  '.aac': 'audio/aac',
  '.flac': 'audio/flac',
  '.weba': 'audio/webm',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
};

const COMMON = {
  'Cache-Control': 'no-store',
  'Access-Control-Allow-Origin': '*',
  'X-Content-Type-Options': 'nosniff',
};
// The mark index.html's live-reload script reads off its own navigation entry before it connects.
// A page on a static host carries no mark, so it never makes a request that could only 404.
if (LIVE) COMMON['Server-Timing'] = 'studio-live';

function reply(res, status, body, type = 'text/plain; charset=utf-8', head = false, extra = {}) {
  const buf = Buffer.from(body);
  res.writeHead(status, { ...COMMON, 'Content-Type': type, 'Content-Length': buf.length, ...extra });
  res.end(head ? undefined : buf);
}

function reviewPage(res, head) {
  let map = '';
  try {
    const page = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
    map = (page.match(/<script[^>]*type\s*=\s*["']?importmap["']?[^>]*>[\s\S]*?<\/script>/i) || [''])[0];
  } catch {
    // No index.html: a bare page is still a page to mount on.
  }
  const page = '<!doctype html><html><head><meta charset="utf-8"><title>studio review</title>'
    + map + '</head><body></body></html>';
  reply(res, 200, page, TYPES['.html'], head);
}

function sendFile(req, res, file, st, head) {
  const type = TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream';
  const headers = { ...COMMON, 'Content-Type': type, 'Accept-Ranges': 'bytes' };
  let start = 0;
  let end = st.size - 1;
  let status = 200;
  // One byte range, because a media element asks for one and seeks badly without it.
  const range = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range || '').trim());
  if (range && st.size && (range[1] || range[2])) {
    if (range[1]) {
      start = Number(range[1]);
      end = range[2] ? Math.min(Number(range[2]), st.size - 1) : st.size - 1;
    } else {
      start = Math.max(0, st.size - Number(range[2]));
    }
    if (start > end || start >= st.size) {
      res.writeHead(416, { ...COMMON, 'Content-Range': `bytes */${st.size}` });
      res.end();
      return;
    }
    status = 206;
    headers['Content-Range'] = `bytes ${start}-${end}/${st.size}`;
  }
  headers['Content-Length'] = st.size ? end - start + 1 : 0;
  res.writeHead(status, headers);
  if (head || !st.size) {
    res.end();
    return;
  }
  fs.createReadStream(file, { start, end }).on('error', () => res.destroy()).pipe(res);
}

// ---------------------------------------------------------------------------
// LIVE RELOAD
// ---------------------------------------------------------------------------
// GET /__studio_live is a Server-Sent-Events stream. When a file of the game changes it sends
// `data: reload`, and the small script in index.html reloads the page. Without it the page an
// agent has open keeps running the code it loaded, and nothing in its picture says so.
//
// WHICH FILES. Everything under this folder except node_modules; any name that starts with a dot
// (.studio, .git, editor swap files); dist, where a build writes; graphify-out, the Studio's code
// graph, rewritten after every edit, so it would reload the page a second time; *.log, *.bak,
// *.tmp and *~; and every *.edits.json. A move saved in the Studio is already applied in the page,
// and a reload for it would throw away the agent's unsaved tries.
//
// WHICH EVENTS. On Windows fs.watch also fires when a file is only READ (the volume rewrites its
// access time: on the Studio's own PC every file the page loaded fired once), and when an indexer,
// antivirus or OneDrive changes an attribute. Counted blindly, the page would reload because it
// had just loaded. So an event counts only when the file's size or modified time really moved.
//
// Many events make one reload, 150 ms after the last. A comment every 25 s keeps the stream open
// through anything in between. When a burst overflows the watcher (npm install writes thousands
// of files at once) its events name no file, about one every 50 ms until the burst ends, and
// every file is compared instead, at most once per 100 ms. Where fs.watch cannot run at all, or
// runs and never fires (some network drives: set STUDIO_LIVE_POLL=1), the comparison runs every
// 750 ms.
const LIVE_PATH = '/__studio_live';
const QUIET_MS = 150;
const BEAT_MS = Number(process.env.STUDIO_LIVE_HEARTBEAT_MS) || 25000;   // a test shortens it
const POLL_MS = 750;
const SCAN_CAP = 5000;
const listeners = new Set();
const seen = new Map();              // path -> "mtime:size", what the file was when it last counted
const changed = new Set();
let quiet = null;
let scanning = null;
let scanned = false;                 // the next reload was found by comparing every file
let polling = false;

function ignored(rel) {
  const parts = rel.toLowerCase().split('/').filter(Boolean);
  if (!parts.length || parts[0] === 'dist') return true;
  if (parts.some((p) => p === 'node_modules' || p === 'graphify-out' || p.startsWith('.'))) return true;
  const name = parts[parts.length - 1];
  return name.includes('.edits.json') || /(\.log|\.bak|\.tmp|~)$/.test(name);
}

// What a file is now: "mtime:size", '' when it is not there, and null for a folder, whose own
// time moves whenever a file in it does (that file has an event of its own).
function sig(rel) {
  try {
    const st = fs.statSync(path.join(ROOT, rel));
    return st.isDirectory() ? null : `${st.mtimeMs}:${st.size}`;
  } catch {
    return '';
  }
}

// Did the file really change since it last counted? Remembers what it is now, either way.
function moved(rel) {
  const now = sig(rel);
  if (now === null) return false;
  const before = seen.get(rel) || '';
  if (now) seen.set(rel, now);
  else seen.delete(rel);
  return now !== before;
}

function walk(dir, rel, out) {
  let list;
  try {
    list = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const d of list) {
    if (out.size >= SCAN_CAP) return;
    const r = rel ? `${rel}/${d.name}` : d.name;
    if (ignored(r)) continue;
    if (d.isDirectory()) {
      walk(path.join(dir, d.name), r, out);
    } else {
      const s = sig(r);
      if (s) out.set(r, s);
    }
  }
}

// Every file against what it last was: the baseline at start, and the answer whenever an event
// could not say which file it was about.
function rescan(report) {
  const now = new Map();
  walk(ROOT, '', now);
  let found = 0;
  if (report) {
    for (const [rel, s] of now) if (seen.get(rel) !== s) found += changed.add(rel) ? 1 : 0;
    if (now.size < SCAN_CAP) for (const rel of seen.keys()) if (!now.has(rel)) found += changed.add(rel) ? 1 : 0;
  }
  // A walk that stopped at the cap saw part of the folder: what it did not reach is kept, or a
  // read of one of those files would count as a change.
  if (now.size < SCAN_CAP) seen.clear();
  for (const [rel, s] of now) seen.set(rel, s);
  if (found) {
    scanned = true;
    later();
  }
}

// A burst's nameless events, one full comparison per 100 ms: always one after the last event.
function rescanSoon() {
  if (scanning) return;
  scanning = setTimeout(() => {
    scanning = null;
    rescan(true);
  }, 100);
}

function later() {
  clearTimeout(quiet);
  quiet = setTimeout(() => {
    // The comment line names what changed, for a person reading the stream; the page ignores it.
    const what = [...changed].slice(0, 5).join(', ').replace(/[\r\n]+/g, ' ');
    const how = scanned ? ' (found by comparing every file)' : '';
    changed.clear();
    scanned = false;
    send(`: changed ${what}${how}\ndata: reload\n\n`);
  }, QUIET_MS);
}

function send(text) {
  for (const res of listeners) {
    try {
      res.write(text);
    } catch {
      listeners.delete(res);
    }
  }
}

function onEvent(type, name) {
  if (!name) {
    rescanSoon();
    return;
  }
  const rel = String(name).split(path.sep).join('/');
  if (ignored(rel) || !moved(rel)) return;
  changed.add(rel);
  later();
}

function poll(why) {
  if (polling) return;
  polling = true;
  console.log(`  live reload: ${why}; comparing the files every ${POLL_MS} ms instead`);
  setInterval(() => rescan(true), POLL_MS);
}

function watch() {
  if (process.env.STUDIO_LIVE_POLL === '1') {
    poll('STUDIO_LIVE_POLL=1');
  } else {
    try {
      const w = fs.watch(ROOT, { recursive: true }, onEvent);
      w.on('error', (e) => {
        try {
          w.close();
        } catch {
          // already closed
        }
        poll(`the folder watcher stopped (${e && e.message})`);
      });
    } catch (e) {
      poll(`this folder cannot be watched (${e && e.message})`);
    }
  }
  rescan(false);
  setInterval(() => send(': ping\n\n'), BEAT_MS).unref();
}

function liveStream(req, res, head) {
  if (!LIVE) {
    reply(res, 404, 'live reload is off (--no-live or STUDIO_LIVE=0)\n', undefined, head);
    return;
  }
  // HEAD answers how many pages are listening now: "is my page connected?" without joining in.
  res.writeHead(200, { ...COMMON, 'Content-Type': 'text/event-stream; charset=utf-8',
                       'X-Accel-Buffering': 'no', 'X-Studio-Listeners': String(listeners.size) });
  if (head) {
    res.end();
    return;
  }
  // A stream that stays quiet for minutes is not a dead one.
  req.socket.setTimeout(0);
  req.socket.setNoDelay(true);
  req.socket.setKeepAlive(true, BEAT_MS);
  res.write('retry: 1000\n: serve.mjs live reload\n\n');
  listeners.add(res);
  const drop = () => listeners.delete(res);
  req.on('close', drop);
  res.on('close', drop);
  res.on('error', drop);
}

const server = http.createServer((req, res) => {
  const head = req.method === 'HEAD';
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { ...COMMON, 'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
                         'Access-Control-Allow-Headers': '*' });
    res.end();
    return;
  }
  if (req.method !== 'GET' && !head) {
    reply(res, 405, 'GET and HEAD only\n', undefined, false, { Allow: 'GET, HEAD, OPTIONS' });
    return;
  }
  let rel;
  try {
    rel = decodeURIComponent(new URL(req.url || '/', 'http://localhost').pathname);
  } catch {
    reply(res, 400, 'bad path\n', undefined, head);
    return;
  }
  if (rel.includes('\0')) {
    reply(res, 400, 'bad path\n', undefined, head);
    return;
  }
  if (rel === '/__studio_review__') {
    reviewPage(res, head);
    return;
  }
  if (rel === LIVE_PATH) {
    liveStream(req, res, head);
    return;
  }
  // Resolved, then checked: a decoded `..` or a backslash can point anywhere, and only what is
  // still inside this folder is served.
  const file = path.resolve(ROOT, '.' + rel);
  if (file !== ROOT && !file.startsWith(ROOT + path.sep)) {
    reply(res, 403, 'outside the game folder\n', undefined, head);
    return;
  }
  fs.stat(file, (err, st) => {
    if (!err && st.isDirectory()) {
      // A folder without its trailing slash would resolve every relative URL in its page one
      // level too high.
      if (!rel.endsWith('/')) {
        const q = (req.url || '').includes('?') ? (req.url || '').slice((req.url || '').indexOf('?')) : '';
        reply(res, 301, 'moved\n', undefined, head, { Location: encodeURI(rel + '/') + q });
        return;
      }
      const index = path.join(file, 'index.html');
      fs.stat(index, (e2, s2) => {
        if (e2 || !s2.isFile()) reply(res, 404, `no index.html in ${rel}\n`, undefined, head);
        else sendFile(req, res, index, s2, head);
      });
      return;
    }
    if (err || !st.isFile()) {
      reply(res, 404, `not found: ${rel}\n`, undefined, head);
      return;
    }
    sendFile(req, res, file, st, head);
  });
});

let port = FIRST;
let tries = 0;
server.on('error', (e) => {
  if (e && e.code === 'EADDRINUSE' && ++tries < TRIES) {
    port += 1;
    server.listen(port, HOST);
    return;
  }
  console.error(`could not serve ${ROOT}: ${e && e.message}`);
  process.exit(1);
});
server.on('listening', () => {
  const shown = HOST === '0.0.0.0' || HOST === '::' ? '127.0.0.1' : HOST;
  console.log(`\n  ${path.basename(ROOT)} (serve.mjs)\n\n  Local:   http://${shown}:${port}/\n`);
  // After the URL line, which the Studio waits for; before any request, which cannot come sooner.
  if (LIVE) watch();
});
server.listen(port, HOST);

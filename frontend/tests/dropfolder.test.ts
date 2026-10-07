// Dropping a folder on the Workspaces list.
//
// The gesture is obvious, so the failures have to be too: a browser tab that cannot know a path,
// a file dropped where a folder belongs, the Studio's own drags passing over the target. Each of
// those is a sentence a person can act on, and this file is where they are held.
//
// Run: npm run test:dropfolder

import { carriesFiles, openedMessage, planOpen } from "../src/components/dropFolder";

let pass = 0;
const fails: string[] = [];
function ok(name: string, cond: boolean, extra = "") {
  if (cond) { pass++; return; }
  fails.push(name + (extra ? "  <- " + extra : ""));
}
function eq(name: string, got: unknown, want: unknown) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  ok(name, a === b, "got " + a + ", want " + b);
}

const dir = (name: string, path: string) => ({ name, path, isDir: true });
const file = (name: string, path: string) => ({ name, path, isDir: false });
const unknown = (name: string, path: string) => ({ name, path, isDir: null });

// ---------------------------------------------------------------- the ordinary case

eq("one folder is one workspace to open",
   planOpen([dir("rot-rush", "C:/games/rot-rush")]),
   { ok: true, paths: ["C:/games/rot-rush"], skipped: 0 });

eq("several folders at once are all opened",
   planOpen([dir("a", "C:/a"), dir("b", "C:/b"), dir("c", "C:/c")]),
   { ok: true, paths: ["C:/a", "C:/b", "C:/c"], skipped: 0 });

// ---------------------------------------------------------------- what the page cannot know

// webkitGetAsEntry is not always there. The backend checks is_dir before it opens anything, so
// an unknown is worth trying — the cost of being wrong is one refusal.
eq("an item the page could not classify is still tried",
   planOpen([unknown("maybe", "C:/maybe")]),
   { ok: true, paths: ["C:/maybe"], skipped: 0 });

// A browser tab has no File.path and no Electron bridge. No inspection of the File will ever
// produce the disk path, so this must be its own message and not "that is a file".
{
  const p = planOpen([{ name: "rot-rush", path: "", isDir: true }]);
  ok("a browser tab is told the real reason", !p.ok && /browser tab/.test((p as any).why), JSON.stringify(p));
  ok("...and pointed at the two things that do work",
     !p.ok && /Open a folder/.test((p as any).why) && /Asset Studio window/.test((p as any).why));
}
{
  // ORDER MATTERS. A browser drop of a FILE must still say "a browser tab cannot see the path",
  // because "that is a file" is unactionable there — dropping the folder would fail identically.
  const p = planOpen([{ name: "notes.txt", path: "", isDir: false }]);
  ok("a browser drop is diagnosed before the thing dropped is judged",
     !p.ok && /browser tab/.test((p as any).why), JSON.stringify(p));
}

// ---------------------------------------------------------------- a file is not a folder

{
  const p = planOpen([file("notes.txt", "C:/x/notes.txt")]);
  ok("one file is refused by name", !p.ok && /“notes.txt” is a file/.test((p as any).why), JSON.stringify(p));
  ok("...and told what to drop instead", !p.ok && /Drop the folder that holds it/.test((p as any).why));
}
{
  const p = planOpen([file("a.png", "C:/a.png"), file("b.png", "C:/b.png")]);
  ok("two files are refused together, in the plural",
     !p.ok && /are files, not folders/.test((p as any).why), JSON.stringify(p));
}
{
  const p = planOpen([file("a.png", "C:/a.png"), file("b.png", "C:/b.png"),
                      file("c.png", "C:/c.png"), file("d.png", "C:/d.png")]);
  ok("a long selection is summarised rather than listed",
     !p.ok && /and 2 more/.test((p as any).why), JSON.stringify(p));
}

// A mixed drop opens what it can and says how much it left.
eq("folders are opened and the files are counted, not fatal",
   planOpen([dir("game", "C:/game"), file("readme.md", "C:/readme.md")]),
   { ok: true, paths: ["C:/game"], skipped: 1 });

// ---------------------------------------------------------------- nothing, and duplicates

ok("an empty drop says so", !planOpen([]).ok);
eq("the same folder twice is opened once",
   planOpen([dir("a", "C:/games/a"), dir("a", "C:/games/a")]),
   { ok: true, paths: ["C:/games/a"], skipped: 0 });
eq("...however Windows spelled the separator or the case",
   planOpen([dir("a", "C:/games/a"), dir("a", "C:\\Games\\A\\")]),
   { ok: true, paths: ["C:/games/a"], skipped: 0 });

// ---------------------------------------------------------------- whose drag is this
//
// A project row dragged out of the explorer, and a tab dragged between panes, are drags too.
// Lighting the folder target up for them would put "drop to open a workspace" over a gesture
// that means something else entirely. `types` is the only readable part during a dragover.

ok("a folder from Windows is ours to take", carriesFiles(["Files"]));
ok("...even with the OS's own extra flavours alongside",
   carriesFiles(["Files", "text/plain", "application/x-moz-file"]));
ok("a project row being dragged to a pane is not", !carriesFiles(["application/x-studio-root"], ["application/x-studio-root"]));
ok("...nor a tab moving between panes", !carriesFiles(["application/x-studio-pane"], ["application/x-studio-pane"]));
ok("a drag carrying only text is not a folder drop", !carriesFiles(["text/plain"]));
ok("an empty dragover is not either", !carriesFiles([]));
ok("...and neither is a missing one", !carriesFiles(undefined));
// The Studio's own payload wins even when the OS attaches Files as well, which is what happens
// when a row is dragged over a target that would otherwise import.
ok("our own drag wins over a stray Files flavour",
   !carriesFiles(["Files", "application/x-studio-root"], ["application/x-studio-root"]));

// ---------------------------------------------------------------- what it says afterwards

eq("one folder opened", openedMessage(["rot-rush"], [], [], 0), { text: "Opened rot-rush", tone: "ok" });
eq("several opened are counted", openedMessage(["a", "b"], [], [], 0), { text: "Opened 2 folders", tone: "ok" });
eq("a folder already open is not an error",
   openedMessage([], ["rot-rush"], [], 0), { text: "rot-rush is already a workspace", tone: "ok" });
eq("opened and already-open read as one line",
   openedMessage(["a"], ["b"], [], 0), { text: "Opened a · 1 already open", tone: "ok" });
eq("a skipped file makes it a warning, not a success",
   openedMessage(["a"], [], [], 1), { text: "Opened a · 1 not a folder", tone: "warn" });
eq("nothing opened and something failed is a failure",
   openedMessage([], [], ["C:/nope: no such folder"], 0),
   { text: "C:/nope: no such folder", tone: "danger" });
eq("something opened and something failed is a warning",
   openedMessage(["a"], [], ["b broke"], 0), { text: "Opened a · b broke", tone: "warn" });
ok("an empty outcome still says something", openedMessage([], [], [], 0).text.length > 0);

// ---------------------------------------------------------------- the shape, in the source
//
// Two things that cannot be checked without a DOM, asserted where they live instead.

import { readFileSync } from "node:fs";
import { join } from "node:path";

const ws = readFileSync(join(process.cwd(), "src", "pages", "Workspace.tsx"), "utf8");

// 1. THE EXPLORER'S EMPTY STATE. With a project open, a dropped folder is imported INTO it. With
//    no project open there was nothing to import into, and the drop was wired to `undefined` —
//    so the one moment a person most wants to open a folder was the one moment nothing happened.
ok("the explorer drop is not switched off when no project is open",
   !/onDrop=\{activeRoot \? /.test(ws), (ws.match(/onDrop=\{activeRoot \?[^\n]*/) || ["?"])[0]);
ok("...it opens the folder as a workspace instead", /openDroppedFolders\(/.test(ws));

// 2. THE STRAY DROP. Electron navigates to file:// on a drop no handler claimed, which replaces
//    the Studio with the contents of whatever was dragged. A window-level guard is the only
//    thing that covers the gaps between targets.
ok("a drop that lands on nothing is swallowed at the window",
   /addEventListener\("drop"/.test(ws) && /addEventListener\("dragover"/.test(ws), "no window guard");

// 2b. ...and it must not answer for a target that DID want the drag. The guard is last in the
//     bubble chain, so without this check it would overwrite the cursor every real target set,
//     and every folder drop would show the same "no" sign whether it worked or not.
ok("the guard stands aside for a target that claimed the drag",
   /if \(e\.defaultPrevented\) return;/.test(ws));
ok("...and tells the truth where nothing wants it", /dropEffect = "none"/.test(ws));

// 3. THE PINNED STRIP. Every WorkspaceTree row calls preventDefault on dragover so one row can
//    be dropped on another to reorder it. Claiming EVERY drag that way is what made a folder
//    unusable over the pinned strip: the cursor said yes, and the row's drop handler - which
//    only knows how to reorder - had no row to move and did nothing at all.
const tree = readFileSync(join(process.cwd(), "src", "components", "WorkspaceTree.tsx"), "utf8");
ok("a row claims a drag only while a row is being dragged",
   /onDragOver=\{d\?\.path \?/.test(tree),
   (tree.match(/onDragOver=\{[^\n]*/) || ["?"])[0]);
ok("...and the same for its drop", /onDrop=\{d\?\.path \?/.test(tree),
   (tree.match(/onDrop=\{[^\n]*/) || ["?"])[0]);

// 4. ONE TARGET FOR THE WHOLE COLUMN, so the pinned strip, the header, the workspace button and
//    the gaps between them all behave the same instead of being four separate discoveries.
ok("the explorer column takes a folder", /folderTarget\("explorer"\)/.test(ws));

// 5. ...and the file tree inside it keeps its narrower meaning to ITSELF. Without this the
//    column would light up too and, on release, open the folder as a workspace at the same
//    moment the tree imported it - one gesture doing both things it is allowed to mean.
{
  // The whole file-tree element, from the class that rings it to the hint it draws: both its
  // dragover and its drop have to stop there, or the column behind opens a workspace at the
  // same moment the tree imports into one.
  const from = ws.indexOf('wsDrag && "ring-2');
  const to = ws.indexOf("{wsDrag && (", from);
  ok("the file tree element can be found", from > 0 && to > from, from + ".." + to);
  const el = ws.slice(from, to);
  const stops = (el.match(/e\.stopPropagation\(\)/g) || []).length;
  ok("the file tree stops the drag reaching the column behind it", stops >= 2,
     stops + " stopPropagation calls in it");
}

// ---------------------------------------------------------------- report
if (fails.length) {
  console.error("\nFAILED " + fails.length + " of " + (pass + fails.length));
  for (const f of fails) console.error("  x " + f);
  process.exit(1);
}
console.log("drop folder: " + pass + " checks pass");

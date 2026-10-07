"""Does the browser-massacre guard block the right things and nothing else?

The offending strings are assembled at run time. Written as a literal, this file could not be
handled by any tool whose own command line the guard inspects — including the one that wrote it.
"""
import io
import json
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio.graph_hook import _mass_kill_target as target   # noqa: E402

TK = "task" + "kill"          # never a literal, see the docstring
ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + label)
    else:
        fail += 1
        print("  FAIL  " + label + "   " + str(extra))


print("BLOCKED — these kill every copy on the machine")
for label, cmd, want in [
    ("the exact line that killed the browser", TK + " //F //IM chrome.exe //T >/dev/null 2>&1", "chrome"),
    ("windows slash form", TK + " /F /IM chrome.exe", "chrome"),
    ("quoted image name", TK + ' /f /im "chrome.exe"', "chrome"),
    ("edge", TK + " //F //IM msedge.exe", "msedge"),
    ("buried in a longer line", "cd /tmp && " + TK + " //F //IM chrome.exe //T; sleep 2; echo go", "chrome"),
    ("pkill", "pkill chrome", "chrome"),
    ("killall with a signal", "killall -9 chromium", "chromium"),
    ("powershell by name", "Stop-Process -Name chrome -Force", "chrome"),
    ("powershell pipeline", "Get-Process chrome | Stop-Process -Force", "chrome"),
    ("node — this one kills the CLI itself", TK + " /F /IM node.exe", "node"),
    ("python — kills the backend", TK + " /F /IM python.exe", "python"),
]:
    check(label, target(cmd) == want, "got %r" % target(cmd))

print()
print("ALLOWED — each names one process the caller started")
for label, cmd in [
    ("kill by pid", TK + " /F /PID 12345"),
    ("kill by pid, whole tree", TK + " //F //T //PID 4242"),
    ("posix kill by pid", "kill -9 8123"),
    ("its own dev server, by name", TK + " //F //IM forge-api.exe"),
    ("a grep that merely mentions it", 'grep -rn "' + TK + '" --include=*.py .'),
    ("psutil by pid", 'python -c "import psutil; psutil.Process(99).kill()"'),
    ("an ordinary build", "npm run build && node shot.mjs http://localhost:5181 out.png"),
    ("a screenshot through the Studio", "curl -s -X POST http://127.0.0.1:8777/api/review/render"),
]:
    check(label, target(cmd) == "", "got %r" % target(cmd))

print()
print("END TO END — what the hook actually returns")
payload = {"tool_name": "Bash", "cwd": r"C:\x",
           "tool_input": {"command": TK + " //F //IM chrome.exe //T"}}
r = subprocess.run([sys.executable, "-m", "asset_studio.graph_hook"],
                   input=json.dumps(payload), capture_output=True, text=True, cwd=".")
try:
    d = json.loads((r.stdout or "").strip())["hookSpecificOutput"]
    check("the hook denies it", d.get("permissionDecision") == "deny", d)
    check("...and names the victim", "chrome" in d.get("permissionDecisionReason", ""))
    check("...and offers the review endpoint", "/api/review/render" in d.get("permissionDecisionReason", ""))
    check("...and says to kill by pid", "BY PID" in d.get("permissionDecisionReason", ""))
except Exception as e:
    check("the hook denies it", False, "%s :: %r" % (e, (r.stdout or "")[:200]))

payload["tool_input"]["command"] = TK + " /F /PID 4242"
r = subprocess.run([sys.executable, "-m", "asset_studio.graph_hook"],
                   input=json.dumps(payload), capture_output=True, text=True, cwd=".")
check("a pid kill passes straight through", "permissionDecision" not in (r.stdout or ""),
      (r.stdout or "")[:120])

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

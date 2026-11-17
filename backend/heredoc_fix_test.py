"""heredoc_fix: a long quoted heredoc moved to a file gives bash the SAME input.

Each case runs twice under the same bash, from a script file (so length is no problem here): the
original command and the rewritten one. Their output must be identical, byte for byte.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "asset_studio"))
import heredoc_fix  # noqa: E402

failed = 0


def check(label, cond, extra=""):
    global failed
    print(("  PASS  " if cond else "  FAIL  ") + label + (("   " + str(extra)) if extra and not cond else ""))
    if not cond:
        failed += 1


BASH = shutil.which("bash") or r"C:\Program Files\Git\usr\bin\bash.exe"
PAD = "# " + "x" * 120 + "\n"


def run(cmd: str) -> bytes:
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as f:
        f.write(cmd.encode("utf-8"))
    try:
        return subprocess.run([BASH, f.name], capture_output=True, stdin=subprocess.DEVNULL, timeout=60).stdout
    finally:
        os.unlink(f.name)


TRICKY = ("it's a 'quote' and \"double\" `backtick` $HOME ${X:-y} $(echo no) \\n \\\\ \\'\n"
          "unicode: Μοντέλα — ✓\n\n  indented line\n\ttabbed line\nEOFX not the end\n")
body = TRICKY + PAD * 40

cases = {
    "single quoted delimiter": "cat <<'EOF' | od -c | tail -3\n" + body + "EOF\necho after\n",
    "double quoted delimiter": 'cat <<"PYEOF"\n' + body + "PYEOF\n",
    "two heredocs on one line": "paste <(cat) /dev/fd/3 <<'A' 3<<'B'\n" + body + "A\n" + body + "B\n",
    "heredoc then more commands": "cat > \"$TMPF\" <<'EOF'\n" + body + "EOF\nwc -c < \"$TMPF\"; head -2 \"$TMPF\"\n",
    "tab-stripping <<-": "cat <<-'EOF'\n" + "".join("\t\t" + l + "\n" for l in body.split("\n")) + "\tEOF\n",
    "python stdin": "python - <<'EOF'\nprint(\"it's ok\", len('`$x`'))\n" + PAD * 40 + "EOF\n",
}
tmpf = tempfile.mktemp()
for name, cmd in cases.items():
    cmd = cmd.replace('"$TMPF"', "'" + tmpf.replace("\\", "/") + "'")
    new = heredoc_fix.rewrite_command(cmd)
    check(f"{name}: rewritten", bool(new))
    if new:
        check(f"{name}: shorter than the original", len(new) < len(cmd), (len(new), len(cmd)))
        a, b = run(cmd), run(new)
        check(f"{name}: same output", a == b, (a[:120], b[:120]))
try:
    os.unlink(tmpf)
except OSError:
    pass

# left alone
short = "cat <<'EOF'\nhello\nEOF\n"
check("a short command is left alone", heredoc_fix.rewrite_command(short) is None)
check("an empty body in a long command is left alone (no gain)",
      heredoc_fix.rewrite_command("cat <<'EOF'\nEOF\necho " + "y" * heredoc_fix.MIN_CHARS + "\n") is None)
check("an unquoted heredoc is left alone (its body is expanded)",
      heredoc_fix.rewrite_command("cat <<EOF\n" + body + "EOF\n") is None)
check("a mix of quoted and unquoted is left alone",
      heredoc_fix.rewrite_command("cat <<'A' <<B\n" + body + "A\n" + body + "B\n") is None)
check("no terminator: left alone", heredoc_fix.rewrite_command("cat <<'EOF'\n" + body) is None)
check("a here-string <<< is not a heredoc",
      heredoc_fix.rewrite_command("cat <<<'x'\n" + PAD * 40) is None)
check("a shift operator is not a heredoc", heredoc_fix.rewrite_command("echo $((1<<3))\n" + PAD * 40) is None)

# the payload gate
long_cmd = cases["single quoted delimiter"]
mk = lambda **kw: {"tool_name": "Bash", "permission_mode": "bypassPermissions", "tool_input": {"command": long_cmd, "description": "d"}, **kw}
got = heredoc_fix.redirect(mk())
check("bypassPermissions: rewritten, other fields kept", bool(got) and got.get("description") == "d" and got["command"] != long_cmd)
check("acceptEdits: untouched", heredoc_fix.redirect(mk(permission_mode="acceptEdits")) is None)
check("default mode: untouched", heredoc_fix.redirect(mk(permission_mode="default")) is None)
check("not Bash: untouched", heredoc_fix.redirect(mk(tool_name="PowerShell")) is None)

# through the real hook script, as the CLI calls it
hook = os.path.join(os.path.dirname(__file__), "asset_studio", "btw_hook.py")
with tempfile.TemporaryDirectory() as d:
    note = Path(d) / "btw" / "p.note"
    note.parent.mkdir()
    payload = {**mk(), "hook_event_name": "PreToolUse", "session_id": "s", "transcript_path": ""}
    r = subprocess.run([sys.executable, hook, str(note)], input=json.dumps(payload).encode(), capture_output=True, timeout=60)
    try:
        out = json.loads(r.stdout or b"{}").get("hookSpecificOutput", {})
    except Exception:
        out = {}
    check("hook: allow + updatedInput", out.get("permissionDecision") == "allow" and "updatedInput" in out, r.stdout[:300] + r.stderr[:300])
    check("hook: event echoed", out.get("hookEventName") == "PreToolUse")
    new = (out.get("updatedInput") or {}).get("command", "")
    check("hook: the rewritten command gives the same output", bool(new) and run(new) == run(long_cmd))

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)

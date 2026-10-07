"""Does the picker learn the right thing from a turn, and forget it at the right time?

Runs against a throwaway store so it never touches the real one.
"""
import io
import sys
import time

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import model_access as ma   # noqa: E402

ma._FILE = ma._FILE.with_name("model_access_test.json")
ma._FILE.unlink(missing_ok=True)

REFUSAL = ("There's an issue with the selected model (claude-fable-5-1). It may not exist or you "
           "may not have access to it. Run --model to pick a different model.")

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + label)
    else:
        fail += 1
        print("  FAIL  " + label + "   " + str(extra))


print("A refusal is recorded, under the id the CLI named")
check("the turn reports which model was refused", ma.note_turn("claude-fable-5-1", REFUSAL) == "claude-fable-5-1")
check("it is blocked", ma.is_blocked("claude-fable-5-1"))
check("the reason is not empty", bool(ma.blocked()["claude-fable-5-1"]["reason"]))
check("a model NOT named stays available", not ma.is_blocked("claude-opus-5"))

print()
print("An alias is blocked under the id it resolved to")
ma.forget()
ma.note_turn("fable", REFUSAL)
check("the resolved id is blocked", ma.is_blocked("claude-fable-5-1"))
check("...and so is the alias the user picked", ma.is_blocked("fable"))

print()
print("A context selector is the same model")
ma.forget()
ma.note_turn("claude-fable-5-1", REFUSAL)
check("[1m] resolves to the same record", ma.is_blocked("claude-fable-5-1[1m]"))

print()
print("Ordinary turns are not misread as refusals")
ma.forget()
for label, text in [
    ("a normal reply", "OK"),
    ("a reply that merely quotes the wording", "I changed the model selector; there's no issue."),
    ("empty output", ""),
    ("a reply about a DIFFERENT problem", "There's an issue with the selected file (a.py). It may not exist."),
]:
    ma.note_turn("claude-opus-5", text)
    check(label, not ma.blocked(), ma.blocked())

print()
print("A clean turn clears a stale record — this is how access arriving later is found")
ma.forget()
ma.note_turn("claude-fable-5-1", REFUSAL)
check("blocked first", ma.is_blocked("claude-fable-5-1"))
ma.note_turn("claude-fable-5-1", "OK")
check("...and cleared by a turn that worked", not ma.is_blocked("claude-fable-5-1"))

print()
print("A record expires on its own")
ma.forget()
ma.note_turn("claude-fable-5-1", REFUSAL)
ma._cache["claude-fable-5-1"]["at"] = time.time() - ma.RETRY_AFTER - 1
check("an expired record is not reported", not ma.is_blocked("claude-fable-5-1"))

print()
print("It survives a restart")
ma.forget()
ma.note_turn("claude-fable-5-1", REFUSAL)
ma._cache.clear()
ma._loaded = False
check("read back from disk", ma.is_blocked("claude-fable-5-1"))

ma._FILE.unlink(missing_ok=True)
print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

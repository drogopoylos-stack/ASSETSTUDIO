"""Opus 5.5 in the Studio: offered in the picker, priced from the real rate card, given the 1M
window, and never mistaken for Opus 5.

Every number here was read out of the model catalog baked into Claude Code 2.1.280 — the entry
`id:"claude-opus-5-5"` and its pricing tier `tier_4_20_cache_read_0_20` — rather than recalled.
See the `claude-cli-model-catalog` memory for how to read it again when the next model lands.

Reads settings but never writes them, so running it cannot change the Studio's own state.
Run from backend/:  python models_test.py
"""
import io
import sys

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session, mission, pricing   # noqa: E402

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + label)
    else:
        fail += 1
        print("  FAIL  " + label + "   " + str(extra))


print("The picker offers it, and still offers Opus 5")
M = cc_session.MODELS
check("Opus 5.5 is on the list", "claude-opus-5-5" in M)
check("Opus 5 is still on the list", "claude-opus-5" in M)
check("Opus 4.8 and the alias survive", "claude-opus-4-8" in M and "opus" in M)
check("the newest Opus comes first", M.index("claude-opus-5-5") < M.index("claude-opus-5"), M)
check("no duplicate ids", len(M) == len(set(M)), M)

print()
print("The rate card is the CLI's own")
check("its own tier", pricing.tier_for("claude-opus-5-5") == "tier_4_20_cr020", pricing.tier_for("claude-opus-5-5"))
check("the [1m] suffix does not change it", pricing.tier_for("claude-opus-5-5[1m]") == "tier_4_20_cr020")
check("a dated id still matches", pricing.tier_for("claude-opus-5-5-20260601") == "tier_4_20_cr020")
check("so does the Bedrock prefix", pricing.tier_for("us.anthropic.claude-opus-5-5") == "tier_4_20_cr020")
check("Opus 5 keeps its own tier", pricing.tier_for("claude-opus-5") == "tier_5_25", pricing.tier_for("claude-opus-5"))
check("and Opus 4.8 keeps it too", pricing.tier_for("claude-opus-4-8") == "tier_5_25")

MILLION = {"input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000}
five_five = pricing.cost("claude-opus-5-5", MILLION)
five = pricing.cost("claude-opus-5", MILLION)
check("a million of each line costs $29.20", abs(five_five - 29.2) < 1e-9, five_five)
check("the same work on Opus 5 costs $36.75", abs(five - 36.75) < 1e-9, five)
check("so 5.5 is the cheaper of the two", five_five < five, (five_five, five))
check("cache reads are $0.20 a million", abs(pricing.cost("claude-opus-5-5", {"cache_read": 1_000_000}) - 0.2) < 1e-9)
check("an hour-long cache write is $8 a million",
      abs(pricing.cost("claude-opus-5-5", {"cache_write": 1_000_000, "cache_write_1h": 1_000_000}) - 8.0) < 1e-9)

print()
print("The 1M window reaches it")
# Read, never written: whichever way cc_1m is set on this machine, both answers must agree with it.
on_1m = bool(cc_session.settings.get("cc_1m", True))
want = "claude-opus-5-5[1m]" if on_1m else "claude-opus-5-5"
check("the suffix follows the cc_1m setting", cc_session._apply_1m("claude-opus-5-5") == want,
      (on_1m, cc_session._apply_1m("claude-opus-5-5")))
check("never added twice", cc_session._apply_1m("claude-opus-5-5[1m]") == "claude-opus-5-5[1m]")
check("the context meter agrees",
      mission.model_window("claude-opus-5-5") == (1_000_000 if on_1m else 200_000),
      mission.model_window("claude-opus-5-5"))
check("a turn past 200k proves the big window either way",
      mission.model_window("claude-opus-5-5", used=400_000) == 1_000_000)

print()
print("Effort and fast mode, as the catalog lists them for this model")
# The catalog gives Opus 5.5 effort + xhigh_effort + max_effort, so every level the Studio offers
# is legal for it; the picker's own list is what must not be short.
check("all five levels are accepted", cc_session.EFFORT_LEVELS == ["low", "medium", "high", "xhigh", "max"],
      cc_session.EFFORT_LEVELS)
st = cc_session.status() if hasattr(cc_session, "status") else {}
if isinstance(st, dict) and st.get("efforts"):
    check("the API offers default, five levels and ultracode",
          st["efforts"] == ["default", "low", "medium", "high", "xhigh", "max", "ultracode"], st["efforts"])
    check("and the API's model list carries Opus 5.5", "claude-opus-5-5" in (st.get("models") or []), st.get("models"))
# Fast mode is a CLI config flag, not a per-model argument: the pair below is what the CLI needs,
# and it is written the same way whichever Opus is picked.
src = open("asset_studio/cc_session.py", encoding="utf-8").read()
check("fast mode writes the flag pair the CLI needs", '"flagSettings"' in src and '"fastMode"' in src)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

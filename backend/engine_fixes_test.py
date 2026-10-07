# -*- coding: utf-8 -*-
"""Four fixes that each had a real failure behind them, checked without a vendor call.

  * fsutil.replace — a Codex send returned HTTP 500 because `os.replace` hit [WinError 5] while a
    poll was reading the same JSON. A held file must be waited for, not turned into an error.
  * settings.update — two threads saving at once shared one ".tmp" file.
  * pricing — every DeepSeek turn was banked at $0, so the monthly cap could never stop it.
  * BOOST — the prefetch block went IN FRONT of the message, so "/btw …" and "/review x" stopped
    being commands; and the directive promised a code graph when the graph switch was off.

Run:  backend/.venv/Scripts/python.exe backend/engine_fixes_test.py
"""
import calendar
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="engine-fixes-test-")

from asset_studio import boost, fsutil, pricing  # noqa: E402
from asset_studio.config import settings  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  -- " + detail) if detail else ""))


# --- fsutil.replace ---------------------------------------------------------------------------
print("fsutil.replace")
tmp_dir = Path(tempfile.mkdtemp(prefix="fsutil-"))
src, dst = tmp_dir / "a.tmp", tmp_dir / "a.json"
src.write_text("new", encoding="utf-8")
dst.write_text("old", encoding="utf-8")
real = os.replace
calls = {"n": 0}


def flaky(a, b):
    calls["n"] += 1
    if calls["n"] <= 2:
        raise PermissionError(5, "Access is denied")
    return real(a, b)


with patch.object(fsutil.os, "replace", side_effect=flaky), patch.object(fsutil.os, "name", "nt"), \
        patch.object(fsutil.time, "sleep"):
    try:
        fsutil.replace(src, dst)
        check("a file held for a moment is replaced after a retry", dst.read_text() == "new")
    except PermissionError as e:
        check("a file held for a moment is replaced after a retry", False, repr(e))
check("...and it took exactly the retries it needed", calls["n"] == 3, str(calls["n"]))

src.write_text("x", encoding="utf-8")
with patch.object(fsutil.os, "replace", side_effect=PermissionError(5, "denied")), \
        patch.object(fsutil.os, "name", "nt"), patch.object(fsutil.time, "sleep"):
    try:
        fsutil.replace(src, dst)
        check("a file that stays locked still raises in the end", False, "no error")
    except PermissionError:
        check("a file that stays locked still raises in the end", True)

# --- settings.update under two threads ---------------------------------------------------------
print("settings.update")
errors: list = []


def writer(k: int) -> None:
    try:
        for i in range(40):
            settings.update({f"race_{k}": i})
    except Exception as e:  # noqa: BLE001
        errors.append(e)


threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("four threads saving at once raise nothing", not errors, repr(errors[:1]))
settings.reload()
check("...and every thread's last value is on disk",
      all(settings.get(f"race_{k}") == 39 for k in range(4)),
      str({k: settings.get(f"race_{k}") for k in range(4)}))

# --- DeepSeek pricing -------------------------------------------------------------------------
print("pricing")
# 2026-10-07 is a Wednesday. 08:00 UTC is peak; 12:00 UTC is off-peak; Saturday is off-peak all day.
peak = calendar.timegm((2026, 10, 7, 8, 0, 0))
off = calendar.timegm((2026, 10, 7, 12, 0, 0))
sat = calendar.timegm((2026, 10, 10, 8, 0, 0))
u = {"input": 1_000_000, "cache_read": 1_000_000, "output": 1_000_000}
check("Flash at peak: $0.30 miss + $0.006 hit + $1.20 out",
      abs(pricing.deepseek_cost("deepseek-flash", u, peak) - 1.506) < 1e-9,
      str(pricing.deepseek_cost("deepseek-flash", u, peak)))
check("off-peak is exactly half",
      abs(pricing.deepseek_cost("deepseek-flash", u, off) - 0.753) < 1e-9)
check("a Saturday is off-peak even in a peak hour",
      abs(pricing.deepseek_cost("deepseek-flash", u, sat) - 0.753) < 1e-9)
check("the legacy Flash ids are billed as Flash",
      pricing.deepseek_cost("deepseek-v4-flash", u, peak) == pricing.deepseek_cost("deepseek-flash", u, peak))
check("V4 Pro has its own, higher price",
      abs(pricing.deepseek_cost("deepseek-v4-pro", u, peak) - (1.32 + 0.044 + 3.96)) < 1e-9)
c, basis = pricing.cost_with_basis("deepseek-flash", {"input": 1000, "output": 1000})
check("a DeepSeek turn is no longer banked as $0 'unknown'", c > 0 and basis == "list", f"{c} {basis}")
settings.update({"pricing_overrides": {"deepseek-flash": {"input": 9, "output": 9}}})
c, basis = pricing.cost_with_basis("deepseek-flash", {"input": 1_000_000, "output": 0})
check("a rate the user typed in still wins", basis == "override" and abs(c - 9) < 1e-9, f"{c} {basis}")
settings.update({"pricing_overrides": None})
check("a Claude model is untouched", pricing.cost_with_basis("claude-opus-5-5", {"input": 1_000_000})[1] == "list")
check("an unknown model is still 'unknown', not free",
      pricing.cost_with_basis("kimi-k3", {"input": 10})[1] == "unknown")

# --- BOOST ------------------------------------------------------------------------------------
print("BOOST")
graph = {"block": "[BOOST local index] x", "lookups": 1, "chars": 20, "tokens": 5}
settings.update({"boost": True})
with patch.object(boost, "prefetch", return_value=graph) as pf:
    for msg in ("/btw also check `_send_streaming`", "/review _send_codex", "  /compact"):
        out = boost.prepare("p", msg, engine="claude", cwd=str(tmp_dir))
        check(f"{msg.strip()!r} still starts with its command",
              out["message"].lstrip().startswith("/"), out["message"][:60])
    check("...and no lookup was spent on a command", pf.call_count == 0, str(pf.call_count))
    out = boost.prepare("p", "fix `_send_codex` please", engine="claude", cwd=str(tmp_dir))
    check("a plain message still gets the local-index block",
          out["message"].startswith("[BOOST local index]"), out["message"][:60])

settings.update({"cc_graphify": False})
check("graph switch OFF: the directive does not promise a code graph", "code graph" not in boost.directive())
settings.update({"cc_graphify": True})
check("graph switch ON: the directive names it", "code graph" in boost.directive())
check("...and the text is the same on every call (cache-safe)", boost.directive() == boost.directive())
settings.update({"boost": False})

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)

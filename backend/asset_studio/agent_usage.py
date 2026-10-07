"""What an agent's tokens really were: every API call counted ONCE.

Three places in the Studio said how many tokens an agent used, and all three were wrong, each in
its own way:

  * THE LINES. Claude Code writes one transcript line per block of an answer - the thinking, the
    text, each tool call - and every one of those lines repeats the usage of the whole API call.
    Adding the lines counted a call two or three times. The subagent card did that.
  * THE LAST CALL. A Task agent's own record (`toolUseResult.usage`, `totalTokens`) holds the
    usage of its LAST call: where the agent ended, not what it spent. The card let that record
    replace its own figures, so for those agents it showed one call.
  * THE CONTEXT. A Workflow journal's per-agent `tokens` is the size of the agent's context at the
    end. The Workflows tab showed that for a finished run and output tokens for a live one.

Measured on the v3.1 tree agent: 103 calls written as 254 assistant lines. The line sum said
81.6M, the calls say 33.6M, and 32.9M of those are the same context read again from the cache on
every call. The number worth a headline is the NEW tokens - input, cache writes and output: 747k.

So: one row per call, keyed by the message id; a later line of the same call replaces what an
earlier one said (its output count is partial until the call's last block).
"""
from __future__ import annotations

KEYS = ("input", "output", "cache_read", "cache_write", "cache_write_1h")


def of(u: dict) -> dict:
    """One API call's usage, in the Studio's own names."""
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    return {
        "input": int(u.get("input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "cache_write": int(u.get("cache_creation_input_tokens") or 0),
        # The 1-hour cache bills at twice the 5-minute one, and the split is only in this object.
        # A subset of cache_write, never an addition to it.
        "cache_write_1h": int(cc.get("ephemeral_1h_input_tokens") or 0),
    }


def call_key(o: dict) -> str:
    """The id every line of one API call shares."""
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    return str(m.get("id") or o.get("requestId") or "")


def context(row: dict) -> int:
    """How big the request was: everything the model was sent for this call."""
    return int(row.get("input", 0)) + int(row.get("cache_read", 0)) + int(row.get("cache_write", 0))


def new_tokens(t: dict) -> int:
    """What the agent really added: input it had not sent before, and what it wrote."""
    return int(t.get("input", 0)) + int(t.get("cache_write", 0)) + int(t.get("output", 0))


def wire_tokens(t: dict) -> int:
    """Everything that crossed the wire, cache reads included."""
    return new_tokens(t) + int(t.get("cache_read", 0))


class Calls:
    """The usage of one transcript, one row per API call."""

    __slots__ = ("rows", "first", "peak", "_anon")

    def __init__(self) -> None:
        self.rows: dict = {}
        self.first = 0          # the context of the first call: what the agent was handed
        self.peak = 0           # the largest request it made
        self._anon = 0

    def add(self, key: str, usage: dict) -> None:
        row = of(usage)
        if not key:
            # No id at all (an old or foreign transcript): each line is its own call, as before.
            self._anon += 1
            key = "#%d" % self._anon
        old = self.rows.get(key)
        if old is None:
            if not self.rows:
                self.first = context(row)
            self.rows[key] = row
        else:
            # The same call again. Input and cache are the same on every line; the output count
            # grows to its final value on the call's last line. The larger is the true one.
            self.rows[key] = {k: max(old[k], row[k]) for k in KEYS}
            row = self.rows[key]
        self.peak = max(self.peak, context(row))

    def totals(self) -> dict:
        out = {k: 0 for k in KEYS}
        for r in self.rows.values():
            for k in KEYS:
                out[k] += r[k]
        return out

    def __len__(self) -> int:
        return len(self.rows)

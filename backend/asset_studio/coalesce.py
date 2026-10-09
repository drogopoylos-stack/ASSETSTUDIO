"""Single-flight: identical calls that arrive together are computed ONCE.

Measured on 2026-10-09 against a secondary backend: 60 feed requests for one large project at the
same moment kept the event loop from answering /api/health for 5-7 seconds. Each request ran the
same transcript parse in its own worker thread, and 60 threads doing Python work starve the one
thread that serves the loop of the GIL. The desktop shell kills a backend whose health goes quiet,
so a burst of polls — two panes, the rail, the context meter, all on one big project — could take
the whole app down while it was only busy.

With `@coalesced(ttl)`:
  * the first caller computes; callers with the same arguments that arrive meanwhile WAIT for that
    result (waiting on an Event releases the GIL) instead of computing it again;
  * a FINISHED result is reused for `ttl` seconds. 0 = never: only calls that overlap the
    computation share it, so nobody is ever handed a result computed before their call arrived.

Results are handed out as shallow copies, so one caller adding a key cannot change another's.
"""
from __future__ import annotations

import functools
import threading
import time
from typing import Any, Callable

_MAX_SLOTS = 256


class _Slot:
    __slots__ = ("at", "val", "err", "event", "busy")

    def __init__(self) -> None:
        self.at = 0.0
        self.val: Any = None
        self.err: BaseException | None = None
        self.event = threading.Event()
        self.busy = True


def _copy(v: Any) -> Any:
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, list):
        return list(v)
    return v


def coalesced(ttl: float) -> Callable:
    def deco(fn: Callable) -> Callable:
        slots: dict = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            while True:
                with lock:
                    slot = slots.get(key)
                    now = time.monotonic()
                    if slot is not None and not slot.busy and slot.err is None and now - slot.at < ttl:
                        return _copy(slot.val)
                    if slot is not None and slot.busy:
                        waiting = slot
                    else:
                        slot = _Slot()
                        slots[key] = slot
                        waiting = None
                        if len(slots) > _MAX_SLOTS:
                            for k in [k for k, s in slots.items() if not s.busy][: len(slots) - _MAX_SLOTS]:
                                slots.pop(k, None)
                if waiting is None:
                    break
                # Someone is computing exactly this. Wait for it (no GIL held) and take THAT result,
                # whatever the ttl: it finished after this call arrived, which is as fresh as a call
                # racing it could ever have been. A failed computation (or a timed-out wait) is
                # retried by this caller.
                waiting.event.wait(timeout=60)
                with lock:
                    if not waiting.busy and waiting.err is None:
                        return _copy(waiting.val)
            try:
                val, err = fn(*args, **kwargs), None
            except BaseException as e:      # noqa: BLE001 — re-raised below, after waking the waiters
                val, err = None, e
            with lock:
                slot.val, slot.err, slot.at, slot.busy = val, err, time.monotonic(), False
                slot.event.set()
                # ttl 0: nothing will reuse it, and a feed result can be megabytes. The waiters hold
                # the slot itself, so dropping it from the table loses nobody their answer.
                if ttl <= 0 and slots.get(key) is slot:
                    slots.pop(key, None)
            if err is not None:
                raise err
            return _copy(val)

        wrapper.coalesce_slots = slots          # for tests
        return wrapper
    return deco

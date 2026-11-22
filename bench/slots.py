"""CPU-slot limiter shared by every bench process: the total budget is CAP cores (default: the worker count).

  with slots.slot(n):   # n slots; blocks until available
Each holder is a file in slots/ named <pid>-<n>-<rand>; dead pids are ignored. A holder that wants more
than one slot (a timed rival run, 2 cores) posts slots/_want so labelers stop taking new slots until it gets in.
Cap: env RWX_SLOT_CAP, the number in $VCV_DATA/slot_cap, else the worker count.
"""
from __future__ import annotations

import contextlib
import os
import random
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcv  # noqa: E402

DIR = vcv.DATA / "slots"


def cap() -> int:
    try:
        return int((DIR.parent / "slot_cap").read_text().strip())
    except (OSError, ValueError):
        return int(os.environ.get("RWX_SLOT_CAP", "0")) or vcv.workers()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _used() -> int:
    total = 0
    for f in DIR.glob("*-*-*"):
        pid, n, _ = f.name.split("-", 2)
        if _alive(int(pid)):
            total += int(n)
        else:
            f.unlink(missing_ok=True)
    return total


@contextlib.contextmanager
def slot(n: int = 1, wait_s: float = 86400):
    DIR.mkdir(parents=True, exist_ok=True)
    me = DIR / f"{os.getpid()}-{n}-{random.randrange(10**9)}"
    want = DIR / f"_want-{os.getpid()}-{random.randrange(10**9)}"
    deadline = time.time() + wait_s
    if n > 1:
        want.write_text("")
    try:
        while True:
            others_want = any(f.name.startswith("_want") and f != want for f in DIR.glob("_want*")) if n == 1 else False
            if not others_want and _used() + n <= max(cap(), n):
                me.write_text("")
                if _used() <= max(cap(), n):
                    break
                me.unlink(missing_ok=True)
            if time.time() > deadline:
                raise TimeoutError("no slot")
            time.sleep(2 + random.random())
        want.unlink(missing_ok=True)
        yield
    finally:
        me.unlink(missing_ok=True)
        want.unlink(missing_ok=True)

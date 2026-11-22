"""Shared settings for every bench script (macOS and Linux).

  VCV_DATA     data directory            (default ~/vcv-data/rwx)
  VCV_RIVALS   rival bare repos           (default ~/vcv-rivals/rwx)
  VCV_WORKERS  parallel labeling workers  (default nproc-2, at least 1, at most what Docker's memory allows)
  VCV_HOST     name written into result rows (default hostname)
"""
from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("VCV_DATA", HOME / "vcv-data/rwx")).expanduser()
RIVALS = Path(os.environ.get("VCV_RIVALS", HOME / "vcv-rivals/rwx")).expanduser()
REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "kit"
HOST = os.environ.get("VCV_HOST") or socket.gethostname().split(".")[0]
BENCH_LOCK = RIVALS.parent / "bench.lock"
PINNED = Path(os.environ.get("RWX_PINNED", DATA / "sqlglot-pinned"))
SMOKE = bool(os.environ.get("VCV_SMOKE"))  # tiny sets, for testing the plumbing


def docker_mem_gb() -> float:
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True, timeout=30).stdout
        return int(out.strip()) / 2**30
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 16.0


def workers() -> int:
    if os.environ.get("VCV_WORKERS"):
        return max(1, int(os.environ["VCV_WORKERS"]))
    by_cpu = (os.cpu_count() or 4) - 2
    by_mem = int((docker_mem_gb() - 1.5) // 2)  # about 2 GB per labeling container
    return max(1, min(by_cpu, by_mem, 5))


def ensure_dirs():
    for d in ("tmp", "runs", "builds", "results"):
        (DATA / d).mkdir(parents=True, exist_ok=True)

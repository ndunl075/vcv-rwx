"""The line map (maps/lines.json.gz, built by tools/linemap from the pinned commit).

For every line of the checkout's Python files: the tests that execute it when run alone in a fresh
pytest process (import-time code and pool workers included), and the tests that execute it only
inside the whole-suite session (after earlier tests warmed caches).  Also: the files that Python
code of a test opens as data.
"""
import gzip
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "lines.json.gz")


class LineMap:
    def __init__(self, path=PATH):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.ids = d["ids"]
        self._sets = d["sets"]
        self.lines = d["lines"]      # file -> [[first line, last line, cold set, session-only set or -1], ...]
        self.reads = d["reads"]      # path -> set
        self.cpu = d["cpu"]
        self.unknown = set(d["unknown"])
        self.pool = set(d["pool"])   # tests that start worker processes
        self.spawn = set(d.get("spawn", range(len(self.ids))))  # tests whose child processes may run checkout code
        self.collect = d.get("collect")  # file -> [[first, last], ...]: lines run while the suite is collected

    def ran_at_collection(self, path, lines):
        """True if any of `lines` ran while the whole suite was imported and collected (or if unknown)."""
        if self.collect is None:
            return True
        rows = self.collect.get(path, ())
        return any(lo <= ln <= hi for ln in lines for lo, hi in rows)

    def executors(self, path, lines):
        """(cold, session-only): indexes of the tests that execute any of `lines` of `path`."""
        cold, sess = set(self.unknown), set()
        want = sorted(lines)
        rows = self.lines.get(path, ())
        k = 0
        for lo, hi, cs, es in rows:
            while k < len(want) and want[k] < lo:
                k += 1
            if k == len(want):
                break
            if want[k] <= hi:
                cold.update(self._sets[cs])
                if es >= 0:
                    sess.update(self._sets[es])
        return cold, sess - cold

    def readers(self, path):
        s = self.reads.get(path)
        return (set(self._sets[s]) if s is not None else set()) | self.unknown

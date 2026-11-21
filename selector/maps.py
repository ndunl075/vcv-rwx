"""Loading and querying the maps in maps/map.json.gz (format: see tools/pack_maps.py).

Test sets are Python ints used as bit masks over the map's test index, so unions are one OR.
"""
import gzip
import json
import os

MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "map.json.gz")


class Maps:
    def __init__(self, path=MAP_PATH):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.ids = d["ids"]
        self.n = len(self.ids)
        self._sets = d["sets"]
        self._mask = {}
        self.lines = d["lines"]
        self.codes = d["codes"]
        self.reads = d["reads"]
        self.listed = d["listed"]
        self.cold = d["cold"]
        self.extra = d.get("extra", {})
        self.all_mask = (1 << self.n) - 1

    def mask(self, sid):
        m = self._mask.get(sid)
        if m is None:
            m = 0
            for i in self._sets[sid]:
                m |= 1 << i
            self._mask[sid] = m
        return m

    def lines_mask(self, path, lo, hi):
        """Tests that executed any line in [lo, hi] of `path`."""
        m = 0
        for a, b, sid in self.lines.get(path, ()):
            if b < lo:
                continue
            if a > hi:
                break
            m |= self.mask(sid)
        return m

    def file_mask(self, path):
        """Tests that executed any line of `path`."""
        m = 0
        for _, _, sid in self.lines.get(path, ()):
            m |= self.mask(sid)
        return m

    def loaders_mask(self, path):
        """Tests that started the module code of `path` (loaded the module)."""
        sid = self.codes.get(path, {}).get("1:<module>")
        m = self.mask(sid) if sid is not None else 0
        # an empty module has no lines; also any test that ran a line of it loaded it
        return m | self.file_mask(path)

    def code_mask(self, path, firstlineno, name):
        sid = self.codes.get(path, {}).get("%d:%s" % (firstlineno, name))
        return self.mask(sid) if sid is not None else 0

    def readers_mask(self, path):
        sid = self.reads.get(path)
        return self.mask(sid) if sid is not None else 0

    def listers_mask(self, path):
        sid = self.listed.get(path)
        return self.mask(sid) if sid is not None else 0

    def indexes(self, mask):
        out = []
        i = 0
        while mask:
            if mask & 1:
                out.append(i)
            mask >>= 1
            i += 1
        return out

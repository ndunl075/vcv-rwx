"""The file map: for every file and directory of the pinned checkout, the tests whose process
tree opens it when the test runs alone in a fresh pytest process (maps/files.json.gz, built by
tools/filemap from the pinned commit).  Importing a module opens its source or its bytecode, and
both are recorded as the source path, so "opens sqlglot/x.py" covers "imports sqlglot.x".
"""
import gzip
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "files.json.gz")


class FileMap:
    def __init__(self, path=PATH):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.ids = d["ids"]                # test ids; a set below holds indexes into this list
        self._sets = d["sets"]
        self.files = d["files"]            # path -> set index
        self.dirs = d["dirs"]              # path -> set index
        self.cpu = d["cpu"]                # CPU seconds of the test alone in a fresh process
        self.base_cpu = d["base_cpu"]      # the same for a test that does nothing
        self.unknown = set(d["unknown"])   # indexes of tests whose trace is not trusted

    def openers(self, path):
        """Indexes of the tests that open `path` (plus those with an untrusted trace)."""
        s = self.files.get(path)
        out = set(self._sets[s]) if s is not None else set()
        return out | self.unknown

"""The slot map (maps/slots.json.gz, built by tools/slotmap from the pinned commit): for every data
attribute of a sqlglot class and every global of a sqlglot module, the tests that read it after
import (selector/state.py says which slots a patch changes).
"""
import gzip
import json
import os

from .linemap import decode

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "slots.json.gz")


class SlotMap:
    def __init__(self, path=PATH):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.ids = d["ids"]
        self._sets = d["sets"]
        self.slots = d["slots"]        # key -> set index, -1: tracked and never read
        self.unknown = set(d["unknown"])
        self.complete = bool(d.get("session"))

    def class_readers(self, key):
        """Tests that read a class slot, or None if the tracer never saw the slot."""
        s = self.slots.get(key)
        if s is None:
            return None
        return (set(decode(self._sets[s])) if s >= 0 else set()) | self.unknown

    def module_readers(self, key):
        """Tests that read a module global from outside the module's own code."""
        s = self.slots.get(key)
        return (set(decode(self._sets[s])) if s is not None and s >= 0 else set()) | self.unknown

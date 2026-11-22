"""selector.main.slot_readers: from the slots a patch changes to the tests that can see them."""
import gzip
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from selector import main  # noqa: E402
from selector.slotmap import SlotMap  # noqa: E402

IDS = ["a", "b", "c", "d", "e"]


class Lines:
    """Stand-in for the line map: which tests run which lines of a file."""

    lines = {"m.py": []}

    def executors(self, path, lines):
        lines = set(lines)
        cold = {0} if lines & {10, 11} else set()
        sess = {3} if lines & {20} else set()
        return cold, sess


class Ctx:
    def __init__(self, tmp):
        slots = {"C\tm\tK\tX": 0, "C\tm\tK\tUNREAD": -1, "MOD\tm": -1, "M\tm\tG": 2}
        out = {"ids": IDS, "sets": ["1,2,4", "4", "2"], "slots": slots, "only": {"C\tm\tK\tX": 1}, "unknown": [], "session": True}
        path = os.path.join(tmp, "slots.json.gz")
        with gzip.open(path, "wt") as f:
            json.dump(out, f)
        self.sm = SlotMap(path)
        self.snum_at = list(range(len(IDS)))
        self.lm = Lines()

    def nums(self, indexes):
        return set(indexes)


def f(path, extra=""):
    return "f 0123456789abcdef0123@%s%s" % (path, extra)


class SlotReaders(unittest.TestCase):
    def setUp(self):
        self.c = Ctx(tempfile.mkdtemp())

    def readers(self, changed):
        return main.slot_readers(self.c, changed)

    def test_class_slot(self):
        r, only, _ = self.readers({"C\tm\tK\tX": ("d aaa", "d bbb")})
        self.assertEqual(r, {1, 2, 4})
        self.assertEqual(only, {4})                       # seen only inside the whole-suite session: never run alone

    def test_read_by_nobody(self):
        r, only, _ = self.readers({"C\tm\tK\tUNREAD": ("d aaa", "d bbb")})
        self.assertEqual((r, only), (set(), set()))

    def test_slot_the_tracer_never_saw_does_not_narrow(self):
        r, _, why = self.readers({"C\tm\tK\tOTHER": ("d aaa", "d bbb")})
        self.assertIsNone(r)
        self.assertIn("not traced", why)

    def test_appearing_slot_or_other_kind_or_structure_does_not_narrow(self):
        for changed in ({"C\tm\tK\tX": (None, "d bbb")}, {"C\tm\tK\tX": ("d aaa", None)},
                        {"C\tm\tK\tX": ("d aaa", "x bbb")}, {"C\tm\tK\tX": ("x aaa", "x bbb")},
                        {"C\tm\tK\tX": ("d aaa", "f bbb@x:1-2")}, {"C\tm\tK\t<structure>": ("x a", "x b")},
                        {"E\tm": ("x a", "x b")}):
            self.assertIsNone(self.readers(changed)[0], changed)

    def test_function_slot_uses_the_line_map(self):
        r, only, _ = self.readers({"C\tm\tK\tFN": ("f aaa@m.py:9-12", "f bbb")})
        self.assertEqual((r, only), ({0}, set()))
        r, only, _ = self.readers({"C\tm\tK\tFN": ("f aaa@m.py:19-21", "f bbb")})
        self.assertEqual((r, only), ({3}, {3}))
        self.assertIsNone(self.readers({"C\tm\tK\tFN": ("f aaa", "f bbb")})[0])        # code of unknown place

    def test_function_the_line_map_does_not_know_does_not_narrow(self):
        # code of the standard library keeps an absolute path, and "no test runs it" is not an answer
        for where in ("/usr/lib/python3.13/textwrap.py:10-20", "sqlglot/unknown.py:1-5"):
            self.assertIsNone(self.readers({"C\tm\tK\tFN": ("f aaa@" + where, "f bbb")})[0], where)
        self.assertIsNone(self.readers({"C\tm\tK\tFN": ("f aaa", "f bbb@m.py:1-2")})[0])

    def test_unsure_value_or_dynamic_classes_do_not_narrow(self):
        for changed in ({"C\tm\tK\tX": ("? aaa", "? aaa")}, {"C\tm\tK\tX": ("d aaa", "? aaa")},
                        {"O\tm\tK": ("o aaa", "o bbb")}, {"E\tm": ("x ImportError", "x ImportError")}):
            self.assertIsNone(self.readers(changed)[0], changed)

    def test_too_many_slots(self):
        changed = {"C\tm\tK\tX%d" % i: ("d a", "d b") for i in range(main.MAX_CHANGED_SLOTS + 1)}
        self.assertIsNone(self.readers(changed)[0])

    def test_module_slot_of_an_unwatched_module(self):
        saved = main.global_readers
        main.global_readers = lambda c, mod, name: ({0}, {3})
        try:
            r, only, _ = self.readers({"M\tm\tG": ("d aaa", "d bbb")})
            self.assertEqual(only, {3})
            self.assertEqual(r, {0, 2, 3})
            self.assertIsNone(self.readers({"M\tunwatched\tG": ("d aaa", "d bbb")})[0])
        finally:
            main.global_readers = saved


if __name__ == "__main__":
    unittest.main()


class Resolve(unittest.TestCase):
    """resolve_slots: "no slot differs" is not a reason to narrow."""

    def run_with(self, changed):
        c = Ctx(tempfile.mkdtemp())
        p = main.Plan("x")
        p.base, p.importers = {1}, {1, 2, 3}
        p.cand = {1, 2, 3}
        c.always = set()
        saved = main.changed_slots
        main.changed_slots = lambda *a: changed
        try:
            main.resolve_slots(c, p, 0, 10)
        finally:
            main.changed_slots = saved
        return p

    def test_no_changed_slot_keeps_every_importer(self):
        p = self.run_with({})
        self.assertEqual(p.cand, {1, 2, 3})
        self.assertIn("no slot differs", p.notes[-1])

    def test_changed_slot_narrows(self):
        p = self.run_with({"C\tm\tK\tUNREAD": ("d aaa", "d bbb")})
        self.assertEqual(p.cand, {1})

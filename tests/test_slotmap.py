"""The slot-read tracer (tools/slotmap/sitecustomize.py), the packer (tools/slotmap/pack.py) and the
loader (selector/slotmap.py), on a small stand-in for sqlglot."""
import glob
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import slotkit  # noqa: E402
from selector.slotmap import SlotMap  # noqa: E402

TOOLS = os.path.join(ROOT, "tools", "slotmap")


def traced(code, files=None):
    tree = slotkit.make_tree(files or slotkit.BASE)
    d = tempfile.mkdtemp(prefix="slotdir-")
    slotkit.run(code, tree, env={"RWX_SLOT_DIR": d}, pythonpath=[TOOLS])
    reads = set()
    for p in glob.glob(os.path.join(d, "*.slots")):
        reads.update(x for x in open(p, "rb").read().decode().split("\n") if x)
    return {r.replace("\t", " ") for r in reads}, tree


class Tracer(unittest.TestCase):
    def test_reads_after_import_only(self):
        reads, _ = traced("""
            import sqlglot.core as c, sqlglot.other as o
            b = c.Child()
            b.run()          # self.LIMIT: a class slot; LEVEL is a global read by the module's own code
            b.args()         # self.ARGS
            o.core.LEVEL     # a global read from another module
        """)
        self.assertIn("C sqlglot.core Base LIMIT", reads)
        self.assertIn("C sqlglot.core Base ARGS", reads)
        self.assertIn("M sqlglot.core LEVEL", reads)
        self.assertNotIn("C sqlglot.core Base NAME", reads)        # never read after import
        self.assertNotIn("C sqlglot.core Base DERIVED", reads)
        self.assertNotIn("C sqlglot.core Child NAME", reads)
        # what import-time code reads (the metaclass reads ARGS, other.py reads core.LEVEL) is state, not a read
        self.assertNotIn("M sqlglot.other COPY", reads)

    def test_inherited_slot_is_read_where_it_lives(self):
        reads, _ = traced("""
            import sqlglot.core as c
            c.Child().args()
            c.Child.NAME
        """)
        self.assertIn("C sqlglot.core Base ARGS", reads)
        self.assertIn("C sqlglot.core Child NAME", reads)

    def test_collection_time_reads_count(self):
        files = dict(slotkit.BASE)
        files["tests_mod.py"] = "from sqlglot import core\nVALUE = core.Base.NAME\n"
        reads, _ = traced("import tests_mod", files)
        self.assertIn("C sqlglot.core Base NAME", reads)

    def test_replaced_slot_counts_as_read(self):
        reads, _ = traced("""
            import sqlglot.core as c
            c.Base.LIMIT = 5      # replaced without being read first
        """)
        self.assertIn("C sqlglot.core Base LIMIT", reads)

    def test_enums_are_not_traced(self):
        reads, _ = traced("import sqlglot.core as c\nc.Color.RED\n")
        self.assertFalse([r for r in reads if r.startswith("C ") and "Color" in r])

    def test_same_values(self):
        out = slotkit.run("""
            import sqlglot.core as c
            b = c.Child()
            print(b.run(), b.args(), c.Base.LIMIT, c.TABLE, c.Child.Inner.FLAG)
        """, slotkit.make_tree(slotkit.BASE), env={"RWX_SLOT_DIR": tempfile.mkdtemp()}, pythonpath=[TOOLS])
        self.assertEqual(out.strip(), "13 {'this': True, 'expression': False} 10 {'a': 1, 'b': (1, 2)} False")


def write_cold(d, rows):
    with gzip.open(os.path.join(d, "cold-0.jsonl.gz"), "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class Pack(unittest.TestCase):
    def test_roundtrip(self):
        d = tempfile.mkdtemp(prefix="pack-")
        ids = ["t.py::A::a", "t.py::A::b", "t.py::A::c", "t.py::A::d"]
        tj = os.path.join(d, "tests.json")
        json.dump({"tests": [{"id": i, "number": n} for n, i in enumerate(ids)]}, open(tj, "w"))
        write_cold(d, [
            {"id": ids[0], "rc": 0, "reads": ["C\tm\tK\tX", "M\tm\tG"]},
            {"id": ids[1], "rc": 0, "reads": ["C\tm\tK\tX"]},
            {"id": ids[2], "rc": 1, "reads": []},                    # failed under the tracer: reads everything
        ])                                                            # ids[3] was never traced: also everything
        os.makedirs(os.path.join(d, "session"))
        with open(os.path.join(d, "session", "1.slots"), "w") as f:
            f.write("T\t%s\nC\tm\tK\tY\nC\tm\tK\tX\nT\t%s\nC\tm\tK\tY\n" % (ids[0], ids[1]))
        with open(os.path.join(d, "tracked.txt"), "w") as f:
            f.write("C\tm\tK\tX\nC\tm\tK\tY\nC\tm\tK\tNEVER\n")
        out = os.path.join(d, "slots.json.gz")
        subprocess.run([sys.executable, os.path.join(TOOLS, "pack.py"), tj, out, d], check=True, stderr=subprocess.DEVNULL)
        sm = SlotMap(out)
        self.assertEqual(sm.ids, ids)
        self.assertTrue(sm.complete)
        self.assertEqual(sm.class_readers("C\tm\tK\tX"), {0, 1, 2, 3})        # a, b, and the two unknown ones
        self.assertEqual(sm.class_readers("C\tm\tK\tY"), {0, 1, 2, 3})
        self.assertEqual(sm.session_only("C\tm\tK\tY"), {0, 1})                # seen only inside the session
        self.assertEqual(sm.session_only("C\tm\tK\tX"), set())                 # a cold run saw a and b read it
        self.assertEqual(sm.class_readers("C\tm\tK\tNEVER"), {2, 3})           # tracked, read by nobody
        self.assertIsNone(sm.class_readers("C\tm\tK\tUNSEEN"))                 # the tracer never saw it: no answer
        self.assertEqual(sm.module_readers("M\tm\tG"), {0, 2, 3})
        self.assertEqual(sm.module_readers("M\tm\tNONE"), {2, 3})


if __name__ == "__main__":
    unittest.main()

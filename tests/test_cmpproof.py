"""The comparison-operator proof: what it proves (selector/cmpproof.py), what it refuses, what the tracer records.

    python3 -m unittest discover -s tests -p 'test_cmp*.py'
"""
import gzip
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from selector import cmpproof  # noqa: E402

P = "sqlglot/x.py"


def swap(old, new, path=P):
    return cmpproof.single_compare_swap(textwrap.dedent(old).lstrip("\n"), textwrap.dedent(new).lstrip("\n"), path)


class SwapTests(unittest.TestCase):
    def test_function_body_swap(self):
        got = swap("""
            def f(a):
                return a is None
            """, """
            def f(a):
                return a == None
            """)
        self.assertEqual(got, ("sqlglot/x.py:2:11:2:20", "Is", "Eq"))

    def test_method_and_lambda_and_comprehension_bodies(self):
        for body_old, body_new in [
            ("class A:\n    def f(self, a):\n        return [x for x in a if x < 3]\n",
             "class A:\n    def f(self, a):\n        return [x for x in a if x <= 3]\n"),
            ("def f(a):\n    return sorted(a, key=lambda x: x > 2)\n", "def f(a):\n    return sorted(a, key=lambda x: x >= 2)\n"),
            ("async def f(a, b):\n    return a != b\n", "async def f(a, b):\n    return a is not b\n"),
        ]:
            self.assertIsNotNone(cmpproof.single_compare_swap(body_old, body_new, P), body_old)

    def test_is_with_a_literal_is_refused(self):
        for lit in ("1", "'a'", "b'a'", "(1, 2)", "[]", "-1", "1.5", "f'{a}'"):
            old = "def f(a):\n    return a == %s\n" % lit
            new = "def f(a):\n    return a is %s\n" % lit
            self.assertIsNone(cmpproof.single_compare_swap(old, new, P), lit)
            self.assertIsNone(cmpproof.single_compare_swap(new, old.replace("==", "is not"), P), lit)
        for ok in ("None", "True", "a.b", "a[0]", "g(a)", "b"):
            old = "def f(a, b):\n    return a == %s\n" % ok
            self.assertIsNotNone(cmpproof.single_compare_swap(old, old.replace("==", "is"), P), ok)
        # the other direction compiles without warnings
        self.assertIsNotNone(cmpproof.single_compare_swap("def f(a):\n    return a is None\n", "def f(a):\n    return a == None\n", P))

    def test_multiline_comparison(self):
        old = "def f(a, b):\n    return (a\n            < b)\n"
        new = "def f(a, b):\n    return (a\n            <= b)\n"
        self.assertEqual(cmpproof.single_compare_swap(old, new, P)[1:], ("Lt", "LtE"))

    def test_import_time_code_is_refused(self):
        for old, new in [
            ("X = 1 if A is None else 2\n", "X = 1 if A == None else 2\n"),                      # module level
            ("class C:\n    X = 1 if A is None else 2\n", "class C:\n    X = 1 if A == None else 2\n"),   # class body
            ("def f(a=(B is None)):\n    pass\n", "def f(a=(B == None)):\n    pass\n"),         # default argument
            ("def f():\n    class C:\n        X = A is None\n", "def f():\n    class C:\n        X = A == None\n"),  # class body in a function
            ("@d(A is None)\ndef f():\n    pass\n", "@d(A == None)\ndef f():\n    pass\n"),       # decorator
            ("Y = [x for x in z if x < 3]\n", "Y = [x for x in z if x <= 3]\n"),                  # comprehension at module level
            ("class C(B if A is None else D):\n    pass\n", "class C(B if A == None else D):\n    pass\n"),   # base class
            ("def f(a: (A is None)):\n    pass\n", "def f(a: (A == None)):\n    pass\n"),         # annotation
            ("f = lambda a=(A is None): a\n", "f = lambda a=(A == None): a\n"),                    # lambda default at module level
        ]:
            self.assertIsNone(swap(old, new), old)

    def test_chained_membership_and_cross_family_are_refused(self):
        for old, new in [
            ("def f(a, b, c):\n    return a < b < c\n", "def f(a, b, c):\n    return a < b <= c\n"),
            ("def f(a, b, c):\n    return a < b\n", "def f(a, b, c):\n    return a < b < c\n"),
            ("def f(a, b):\n    return a in b\n", "def f(a, b):\n    return a not in b\n"),
            ("def f(a, b):\n    return a == b\n", "def f(a, b):\n    return a in b\n"),
            ("def f(a, b):\n    return a < b\n", "def f(a, b):\n    return a == b\n"),           # another family
            ("def f(a, b):\n    return a < b\n", "def f(a, b):\n    return a >= b\n"),           # the complement
            ("def f(a, b):\n    return a == b\n", "def f(a, b):\n    return not a == b\n"),
            ("def f(a, b):\n    return a is not b\n", "def f(a, b):\n    return not a is b\n"),
        ]:
            self.assertIsNone(cmpproof.single_compare_swap(old, new, P), old)

    def test_anything_else_changed_is_refused(self):
        base = "def f(a, b):\n    # c\n    x = 1\n    return a < b\n"
        for new in [
            "def f(a, b):\n    # d\n    x = 1\n    return a <= b\n",       # a comment
            "def f(a, b):\n    # c\n    x = 2\n    return a <= b\n",       # a constant
            "def f(a, b):\n    # c\n    x = 1\n    return a <= b\n\n",     # a line more
            "def f(a, b):\n    # c\n    x = 1\n\n    return a <= b\n",
            "def f(a, b):\n    # c\n    y = 1\n    return a <= b\n",       # a name
            "def f(a, c):\n    # c\n    x = 1\n    return a <= c\n",
            "def f(a, b):\n    # c\n    x = 1\n    return a <= b + 0\n",
            "def f(a, b):\n    # c\n    x = 1\n    return a <= b  # c\n",  # a comment on the same line is the same line: fine only if on the comparison's line
        ]:
            got = cmpproof.single_compare_swap(base, new, P)
            if new.endswith("# c\n"):
                self.assertIsNotNone(got)
            else:
                self.assertIsNone(got, new)

    def test_two_swaps_are_refused(self):
        old = "def f(a, b):\n    return a < b and a is b\n"
        new = "def f(a, b):\n    return a <= b and a == b\n"
        self.assertIsNone(cmpproof.single_compare_swap(old, new, P))
        old = "def f(a, b):\n    return (a < b) is True\n"
        new = "def f(a, b):\n    return (a <= b) == True\n"
        self.assertIsNone(cmpproof.single_compare_swap(old, new, P))

    def test_constants_compare_by_value_and_type(self):
        for a, b in [("0.0", "-0.0"), ("1", "True"), ("1", "1.0"), ("'a'", "b'a'"), ("2", "3")]:
            old = "def f(a):\n    return (a < 5, %s)\n" % a
            new = "def f(a):\n    return (a <= 5, %s)\n" % b
            self.assertIsNone(cmpproof.single_compare_swap(old, new, P), (a, b))
        old = "def f(a):\n    return (a < 5, 0.0)\n"
        new = "def f(a):\n    return (a <= 5, 0.0)\n"
        self.assertIsNotNone(cmpproof.single_compare_swap(old, new, P))

    def test_paths_and_bad_input(self):
        old = "def f(a):\n    return a is None\n"
        new = "def f(a):\n    return a == None\n"
        for path in ("tests/test_x.py", "sqlglot/data.txt", "sqlglotc/x.py", "x.py"):
            self.assertIsNone(cmpproof.single_compare_swap(old, new, path), path)
        self.assertIsNone(cmpproof.single_compare_swap(old, "def f(a:\n", P))
        self.assertIsNone(cmpproof.single_compare_swap(old, old, P))
        self.assertIsNone(cmpproof.single_compare_swap(old.encode(), b"def f(a):\n    return a == None\xff\n", P))
        self.assertIsNone(cmpproof.single_compare_swap(old.replace("\n", "\r\n"), new.replace("\n", "\r\n"), P))
        self.assertIsNotNone(cmpproof.single_compare_swap(old.encode(), new.encode(), P))


def make_map(sites, bad=(), sets=()):
    return {"ids": ["t::a", "t::b", "t::c", "t::d"], "bad": list(bad), "sets": [list(s) for s in sets], "sites": sites}


class MapTests(unittest.TestCase):
    def load(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cmp.json.gz")
            with gzip.open(path, "wt") as f:
                json.dump(make_map(**kw), f)
            return cmpproof.CmpMap(path)

    def test_proof_is_per_test_and_per_operator(self):
        m = self.load(sites={"sqlglot/x.py": {"2:11:2:20": ["Is", 0], "5:1:5:9": ["Lt", 0, -1], "7:1:7:9": ["Eq", 0]}},
                      bad=[3], sets=[[1], []])
        site = "sqlglot/x.py:2:11:2:20"
        self.assertTrue(m.same_outcome("t::a", site, "Is", "Eq"))
        self.assertFalse(m.same_outcome("t::b", site, "Is", "Eq"))        # the alternative disagreed in t::b
        self.assertTrue(m.same_outcome("t::c", site, "Is", "Eq"))
        self.assertFalse(m.same_outcome("t::d", site, "Is", "Eq"))        # no clean profile
        self.assertFalse(m.same_outcome("t::zzz", site, "Is", "Eq"))      # not a test of the map
        self.assertFalse(m.same_outcome("t::a", site, "Is", "IsNot"))     # not an alternative of Is
        self.assertFalse(m.same_outcome("t::a", site, "Eq", "Is"))        # the recorded operator is Is
        self.assertFalse(m.same_outcome("t::a", "sqlglot/x.py:3:1:3:9", "Is", "Eq"))   # a comparison never recorded
        self.assertFalse(m.same_outcome("t::a", "sqlglot/y.py:2:11:2:20", "Is", "Eq"))
        lt = "sqlglot/x.py:5:1:5:9"
        self.assertTrue(m.same_outcome("t::a", lt, "Lt", "LtE"))
        self.assertFalse(m.same_outcome("t::a", lt, "Lt", "Gt"))          # -1: not proven for any test
        self.assertTrue(m.same_outcome("t::c", "sqlglot/x.py:7:1:7:9", "Eq", "Is"))
        self.assertFalse(m.same_outcome("t::b", "sqlglot/x.py:7:1:7:9", "Eq", "Is"))

    def test_tests_attribute_lists_covered_tests(self):
        m = self.load(sites={})
        self.assertEqual(set(m.tests), {"t::a", "t::b", "t::c", "t::d"})


TRACE = r"""
import os, sys, builtins, enum, json
sys.path.insert(0, %(tools)r)
os.environ["RWX_CMP_DIR"] = %(out)r
os.environ["RWX_CMP_ROOT"] = %(out)r
import sitecustomize
cmp = builtins.__rwx_cmp__
calls = []

class Loud:
    def __eq__(self, other):
        calls.append("eq"); return self is other
    def __hash__(self): return 1

class Quiet:
    pass

class Mode(str, enum.Enum):
    A = "a"
    B = "b"

x, y = Quiet(), Quiet()
cmp("s1", x, x, "Is"); cmp("s1", x, y, "Is"); cmp("s1", None, x, "Is")       # Is -> Eq agrees everywhere
cmp("s2", Mode.A, Mode.A, "Eq"); cmp("s2", Mode.A, Mode.B, "Eq")             # enum members: Eq -> Is agrees
cmp("s3", Mode.A, "a", "Eq")                                                  # member against a plain str: not identical
l = Loud(); cmp("s4", l, l, "Eq"); cmp("s4", l, 3, "Eq")                      # own __eq__: never asked twice, no proof
cmp("s5", 1, 1, "Lt"); cmp("s5", 1, 2, "Lt")                                  # equal operands: Lt -> LtE and Gt differ
cmp("s6", 1, 2, "Lt"); cmp("s6", 3, 4, "Lt")                                  # Lt -> LtE agrees; Gt differs
cmp("s7", 1.5, 2.5, "Lt")                                                     # floats: no proof
cmp("s8", "a", "b", "Lt")                                                     # strings order: LtE agrees
try:
    cmp("s9", None, 1, "Lt")                                                  # the program's own exception: nothing recorded
except TypeError:
    pass
cmp("s10", 1, 1, "Eq"); cmp("s10", 1, 1, "Eq")                                # equal ints: Eq -> Is depends on identity: no proof
cmp("s11", "a", "a", "Eq")
cmp("s12", None, None, "Is"); cmp("s12", True, True, "Is"); cmp("s12", True, 1, "Is")   # bool against int: not stable
class Arr:
    def __lt__(self, o): return Arr()
    def __bool__(self): return True
calls.append(type(cmp("s13", Arr(), Arr(), "Lt")).__name__)
sys.stdout.write(json.dumps(calls))
"""


class TracerTests(unittest.TestCase):
    def test_recorded_disagreements(self):
        with tempfile.TemporaryDirectory() as out:
            code = TRACE % {"tools": os.path.join(ROOT, "tools", "cmpmap"), "out": out}
            p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            calls = json.loads(p.stdout)
            self.assertEqual(calls.count("eq"), 2)    # only the program's own two evaluations: the tracer never called Loud.__eq__
            self.assertEqual(calls[-1], "Arr")
            drops = {}
            counts = {}
            for name in os.listdir(out):
                for line in open(os.path.join(out, name)):
                    rec = line.rstrip("\n").split("\t")
                    if rec[0] == "D":
                        drops.setdefault(rec[4], set()).add(rec[6])
                    elif rec[0] == "C":
                        counts[rec[3]] = counts.get(rec[3], 0) + int(rec[5])
            alive = lambda s: s not in drops and s in counts   # noqa: E731
            self.assertTrue(alive("s1"))
            self.assertTrue(alive("s2"))
            self.assertEqual(drops["s3"], {"Is"})
            self.assertEqual(drops["s4"], {"Is"})
            self.assertEqual(drops["s5"], {"LtE", "Gt"})
            self.assertEqual(drops["s6"], {"Gt"})
            self.assertEqual(drops["s7"], {"LtE", "Gt"})
            self.assertEqual(drops["s8"], {"Gt"})
            self.assertEqual(drops["s9"], {"LtE", "Gt"})                      # the original raised: every alternative disagrees
            self.assertEqual(counts["s9"], 1)
            self.assertEqual(counts["s5"], 2)
            self.assertEqual(drops["s10"], {"Is"})
            self.assertEqual(drops["s11"], {"Is"})
            self.assertEqual(drops["s12"], {"Eq"})
            self.assertEqual(drops["s13"], {"LtE", "Gt"})

    def test_rewriter_skips_chained_and_membership(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "sqlglot"))
            with open(os.path.join(out, "sqlglot", "m.py"), "w") as f:
                f.write("def f(a, b, c, d):\n    return (a < b < c, a in d, a is b, a not in d)\n")
            code = ("import os, sys\nsys.path.insert(0, %r)\nsys.path.insert(0, %r)\nos.environ['RWX_CMP_DIR'] = %r\n"
                    "os.environ['RWX_CMP_ROOT'] = %r\nimport sitecustomize\nimport sqlglot.m as m\nprint(m.f(1, 2, 3, (1, 2)))\n"
                    % (os.path.join(ROOT, "tools", "cmpmap"), out, out, out))
            p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.strip(), "(True, True, False, False)")
            col = len("    return (a < b < c, a in d, ")
            counts = [l.split("\t") for n in os.listdir(out) if n.endswith(".cmp") for l in open(os.path.join(out, n)) if l[0] in "CD"]
            self.assertEqual({c[4] if c[0] == "D" else c[3] for c in counts}, {"sqlglot/m.py:2:%d:2:%d" % (col, col + 6)})   # only `a is b`


TEST_BOUNDS = r"""
import os, sys, builtins, json
sys.path.insert(0, %(tools)r)
os.environ["RWX_CMP_DIR"] = %(out)r
os.environ["RWX_CMP_ROOT"] = %(out)r
import sitecustomize
cmp = builtins.__rwx_cmp__
sys.rwx_cmp_mark("t::one")
cmp("s1", 1, 1, "Lt")                       # LtE disagrees in test one
cmp("s1", 1, 1, "Lt")
sys.rwx_cmp_mark(None)
sys.rwx_cmp_mark("t::two")
cmp("s1", 1, 1, "Lt")                       # ... and again in test two: recorded again
cmp("s2", 1, 2, "Lt")                       # Gt only
sys.rwx_cmp_mark(None)
sys.rwx_cmp_mark("t::three")
cmp("s2", 1, 3, "Lt")                       # Gt differs: recorded for three
sys.rwx_cmp_mark("t::four")                 # no end marker: the next start flushes
pid = os.fork()
if pid == 0:
    cmp("s1", 2, 2, "Lt")                   # a forked child records its own disagreement (LtE), as test four
    os._exit(0)
os.waitpid(pid, 0)
cmp("s1", 5, 6, "Lt")                       # the parent: Gt differs
sys.rwx_cmp_mark(None)
try:
    cmp("s3", None, 1, "Lt")
except TypeError:
    pass
sys.stdout.write(str(sys.getrecursionlimit()))
"""


class TracerPerTestTests(unittest.TestCase):
    def records(self, code):
        with tempfile.TemporaryDirectory() as out:
            p = subprocess.run([sys.executable, "-c", code % {"tools": os.path.join(ROOT, "tools", "cmpmap"), "out": out}],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            recs = [l.rstrip("\n").split("\t") for n in sorted(os.listdir(out)) for l in open(os.path.join(out, n))]
            return p, recs

    def test_disagreements_are_recorded_per_test_and_per_forked_child(self):
        p, recs = self.records(TEST_BOUNDS)
        drops = {}
        for r in recs:
            if r[0] == "D":
                drops.setdefault((r[3], r[4]), []).append(r[6])
        self.assertEqual(drops[("t::one", "s1")], ["LtE"])
        self.assertEqual(drops[("t::two", "s1")], ["LtE"])                     # not hidden by test one
        self.assertEqual(drops[("t::two", "s2")], ["Gt"])
        self.assertEqual(drops[("t::three", "s2")], ["Gt"])
        self.assertEqual(sorted(drops[("t::four", "s1")]), ["Gt", "LtE"])      # the child (inherits the current test) and the parent
        self.assertNotIn(("t::four", "s2"), drops)
        self.assertEqual(len({r[1] for r in recs if r[0] == "D" and r[3] == "t::four"}), 2)
        counts = {}
        for r in recs:
            if r[0] == "C":
                counts[(r[2], r[3])] = counts.get((r[2], r[3]), 0) + int(r[5])
        self.assertEqual(counts[("t::one", "s1")], 2)
        self.assertEqual(counts[("t::two", "s1")], 1)
        self.assertEqual(counts[("t::four", "s1")], 2)                         # child 1 + parent 1
        self.assertEqual(counts[("-", "s3")], 1)
        self.assertEqual([r[3:] for r in recs if r[0] == "R"], [["-", "s3", "Lt"]])   # the original raised
        self.assertEqual(int(p.stdout), sys.getrecursionlimit() + 4)           # the extra frame of the rewrite is paid for


class PackTests(unittest.TestCase):
    IDS = ["a.py::A::t0", "a.py::A::t1", "a.py::A::t2", "b.py::B::u"]

    def pack(self, d, scope, session_rows, seed4_rows, cold=None, executed=None):
        tests = {"tests": [{"id": i, "number": n, "file": i.split("::")[0], "seconds": 0.1} for n, i in enumerate(self.IDS)]}
        json.dump(tests, open(os.path.join(d, "tests.json"), "w"))
        cold = cold or [
            {"id": self.IDS[0], "rc": [0, 0], "procs": [1, 1], "drops": {"sqlglot/x.py:1:1:1:9": ["Lt", ["Gt"]]}, "nondet": [], "untraced": False, "spawn": []},
            {"id": self.IDS[1], "rc": [0, 0], "procs": [1, 1], "drops": {}, "nondet": ["sqlglot/x.py:2:1:2:9", "sqlglot/x.py:99:1:99:9"], "untraced": False, "spawn": []},
            {"id": self.IDS[2], "rc": [0, 1], "procs": [1, 1], "drops": {}, "nondet": [], "untraced": False, "spawn": []},
            {"id": self.IDS[3], "rc": [0, 0], "procs": [2, 2], "drops": {}, "nondet": [], "untraced": False, "spawn": ["os.fork"]},
        ]
        with open(os.path.join(d, "cold-0.jsonl"), "w") as f:
            for r in cold:
                f.write(json.dumps(r) + "\n")
        json.dump(executed or {"sqlglot/x.py:%d:1:%d:9" % (n, n): "Lt" for n in (1, 2, 3, 4, 6, 7, 8, 9, 10)}
                  | {"sqlglot/x.py:5:1:5:9": "Is"}, open(os.path.join(d, "executed-0.json"), "w"))
        for seed, rows in (("3", session_rows), ("4", seed4_rows)):
            s = os.path.join(d, "session-" + seed)
            os.makedirs(s, exist_ok=True)
            with open(os.path.join(s, "10.cmp"), "w") as f:
                f.write("\n".join(rows) + "\n")
            open(os.path.join(s, "rc.txt"), "w").write("0\n")
        out = os.path.join(d, "cmp-%s.json.gz" % scope)
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "cmpmap", "pack.py"), "--scope", scope,
                            os.path.join(d, "tests.json"), out, d], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return cmpproof.CmpMap(out)

    def rows(self):
        x = "sqlglot/x.py:"
        a, b = self.IDS[0], self.IDS[3]
        base = [
            "P\t10\t", "S\t10\t1\t0\tpython",
            "D\t10\t0\t%s\t%s3:1:3:9\tLt\tLtE" % (a, x),          # in the session while a test of a.py ran
            "D\t10\t1\t%s\t%s4:1:4:9\tLt\tLtE" % (b, x),         # while a module was imported
            "D\t10\t0\t-\t%s5:1:5:9\tIs\tEq" % x,                # outside any test
            "D\t11\t0\t-\t%s3:1:3:9\tLt\tGt" % x,                # in a process a test started
            "D\t10\t0\t%s\t%s8:1:8:9\tLt\tLtE" % (self.IDS[1], x),   # a later test of the file (not the first one to run the site)
            "R\t10\t0\t%s\t%s9:1:9:9\tLt" % (b, x),              # the original raised
            "D\t10\t0\t%s\t%s9:1:9:9\tLt\tLtE" % (b, x), "D\t10\t0\t%s\t%s9:1:9:9\tLt\tGt" % (b, x),
        ]
        for site, n in ((6, 5), (7, 5), (10, 3)):
            base.append("C\t10\t%s\t%s%d:1:%d:9\tLt\t%d" % (a, x, site, site, n))
        return base

    def test_pack_attribution_file_scope(self):
        with tempfile.TemporaryDirectory() as d:
            seed4 = [r for r in self.rows() if r[0] != "C"] + [
                     "C\t10\t%s\tsqlglot/x.py:6:1:6:9\tLt\t6" % self.IDS[0],   # site 6: another count under another seed
                     "C\t10\t%s\tsqlglot/x.py:7:1:7:9\tLt\t5" % self.IDS[0],
                     "C\t10\t%s\tsqlglot/x.py:10:1:10:9\tLt\t3" % self.IDS[1],              # site 10: the same count in another test
                     "D\t10\t0\t%s\tsqlglot/x.py:7:1:7:9\tLt\tGt" % self.IDS[2],            # site 7: another disagreement set
                     ]
            m = self.pack(d, "file", self.rows(), seed4)
            ids = self.IDS
            self.assertEqual(sorted(m.bad), [2])                              # failed in one profile run
            ok = lambda n, site, new: m.same_outcome(ids[n], "sqlglot/x.py:" + site, "Lt", new)   # noqa: E731
            self.assertFalse(ok(0, "1:1:1:9", "Gt"))                           # disagreed in its own cold run
            self.assertTrue(ok(1, "1:1:1:9", "Gt"))
            self.assertTrue(ok(0, "1:1:1:9", "LtE"))
            self.assertFalse(ok(1, "2:1:2:9", "LtE"))                          # different counts under two hash seeds (cold)
            self.assertTrue(ok(0, "2:1:2:9", "LtE"))
            self.assertFalse(ok(0, "3:1:3:9", "LtE"))                          # session: every test of the file
            self.assertFalse(ok(1, "3:1:3:9", "LtE"))
            self.assertTrue(ok(3, "3:1:3:9", "LtE"))                           # a test of another file is not affected
            self.assertFalse(ok(3, "3:1:3:9", "Gt"))                           # a process a test started: the tests that start processes
            self.assertFalse(ok(0, "4:1:4:9", "LtE"))                          # during an import: everyone
            self.assertFalse(ok(3, "4:1:4:9", "LtE"))
            self.assertFalse(m.same_outcome(ids[3], "sqlglot/x.py:5:1:5:9", "Is", "Eq"))   # outside any test: everyone
            self.assertFalse(ok(0, "6:1:6:9", "LtE"))                          # executed another number of times under another seed
            self.assertFalse(ok(3, "7:1:7:9", "LtE"))                          # another disagreement set under another seed: everyone
            self.assertFalse(ok(3, "7:1:7:9", "Gt"))
            self.assertFalse(ok(0, "8:1:8:9", "LtE"))                          # a later test's disagreement is charged: its file
            self.assertTrue(ok(3, "8:1:8:9", "LtE"))
            for alt in ("LtE", "Gt"):                                          # the original raised: unproven for everyone
                self.assertFalse(ok(0, "9:1:9:9", alt))
                self.assertFalse(ok(3, "9:1:9:9", alt))
            self.assertFalse(ok(0, "10:1:10:9", "LtE"))                        # the same total, other test: counts per test differ
            self.assertTrue(ok(3, "99:1:99:9", "LtE") is False)                # a cold nondeterministic site with no operator: not in the map

    def test_pack_attribution_all_scope(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.pack(d, "all", self.rows(), self.rows())
            ok = lambda n, site, new: m.same_outcome(self.IDS[n], "sqlglot/x.py:" + site, "Lt", new)   # noqa: E731
            self.assertFalse(ok(3, "3:1:3:9", "LtE"))                          # a disagreement in a test of another file counts too
            self.assertFalse(ok(3, "8:1:8:9", "LtE"))
            self.assertTrue(ok(3, "1:1:1:9", "LtE"))                           # the cold record is still per test
            self.assertFalse(ok(0, "1:1:1:9", "Gt"))
            self.assertTrue(ok(1, "1:1:1:9", "Gt"))

    def test_unknown_operator_of_a_cold_nondeterministic_site_is_not_proven(self):
        with tempfile.TemporaryDirectory() as d:
            # the operator of the site is only known from the session: it must still be charged
            cold = [{"id": i, "rc": [0, 0], "procs": [1, 1], "drops": {}, "nondet": ["sqlglot/x.py:1:1:1:9"] if i == self.IDS[1] else [],
                     "untraced": False, "spawn": []} for i in self.IDS]
            m = self.pack(d, "file", ["P\t10\t", "C\t10\t-\tsqlglot/x.py:1:1:1:9\tLt\t4"], ["P\t10\t", "C\t10\t-\tsqlglot/x.py:1:1:1:9\tLt\t4"],
                          cold=cold, executed={})
            self.assertFalse(m.same_outcome(self.IDS[1], "sqlglot/x.py:1:1:1:9", "Lt", "LtE"))
            self.assertTrue(m.same_outcome(self.IDS[0], "sqlglot/x.py:1:1:1:9", "Lt", "LtE"))


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "tools", "cmpmap"))
        import validate
        self.v = validate

    class CM:
        tests = {"tests/a.py::A::t": 0, "tests/a.py::A::u": 1, "tests/b.py::B::v": 2}

    def test_failing_tests_reads_subtests_and_file_level_errors(self):
        txt = ("FAILED tests/b.py::B::v - x\nERROR tests/a.py - boom\n"
               "SUBFAILED[431, DATE_TRUNC(x, WEEK) > CAST('2008' AS DATE)] tests/test_optimizer.py::TestOptimizer::test_simplify\n"
               "SUBFAILED(x=1) tests/z.py::Z::t - err\n")
        got = self.v.failing_tests(txt, self.CM())
        self.assertIn("tests/a.py::A::t", got)      # a collection error of the file fails every test of it
        self.assertIn("tests/a.py::A::u", got)
        self.assertIn("tests/b.py::B::v", got)
        self.assertIn("tests/test_optimizer.py::TestOptimizer::test_simplify", got)
        self.assertIn("tests/z.py::Z::t", got)

    def test_check_goes_through_single_compare_swap_and_survives_empty_output(self):
        old = "def f(a, b):\n    return a < b\n"
        with tempfile.TemporaryDirectory() as d:
            root, out = os.path.join(d, "root"), os.path.join(d, "out")
            os.makedirs(os.path.join(root, "sqlglot"))
            open(os.path.join(root, "sqlglot", "x.py"), "w").write(old)
            site = "sqlglot/x.py:2:11:2:16"
            mp = os.path.join(d, "m.json.gz")
            with gzip.open(mp, "wt") as f:
                json.dump({"ids": list(self.CM.tests), "bad": [], "sets": [[]], "sites": {"sqlglot/x.py": {"2:11:2:16": ["Lt", 0, 0]}}}, f)

            def swap(name, new, pytest_txt, **meta):
                os.makedirs(os.path.join(out, name))
                open(os.path.join(out, name, "file.py"), "w").write(new)
                json.dump(dict({"site": site, "old": "Lt", "new": "LtE", "path": "sqlglot/x.py"}, **meta), open(os.path.join(out, name, "meta.json"), "w"))
                open(os.path.join(out, name, "pytest.txt"), "w").write(pytest_txt)

            swap("empty", "def f(a, b):\n    return a <= b\n", "")                               # no IndexError
            swap("miss", "def f(a, b):\n    return a <= b\n", "ERROR tests/a.py - boom\n== 1 error in 1.0s ==\n")   # file level error: MISSED
            swap("ok", "def f(a, b):\n    return a <= b\n", "== 3 passed in 1.0s ==\n")
            swap("moved", "def f(a, b):\n    return a <= b\n", "== 3 passed in 1.0s ==\n", site="sqlglot/x.py:2:12:2:17")   # meta disagrees
            swap("refused", "def f(a, b):\n    return a <= b + 1\n", "== 3 passed in 1.0s ==\n")
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.v.check(mp, out, root)
            text = buf.getvalue()
            self.assertIn("empty", text)
            self.assertRegex(text, r"miss .* MISSED")
            self.assertRegex(text, r"ok .* ok")
            self.assertRegex(text, r"moved .* MISMATCH")
            self.assertRegex(text, r"refused .* refuses")
            self.assertIn("missed swaps: 2", text)


if __name__ == "__main__":
    unittest.main()

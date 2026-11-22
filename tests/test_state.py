"""Import-state snapshots and their comparison (selector/state.py), on a small stand-in for sqlglot."""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slotkit  # noqa: E402
from selector import state  # noqa: E402


def changed(old_files, new_files):
    a = slotkit.snap(slotkit.make_tree(old_files))
    b = slotkit.snap(slotkit.make_tree(new_files))
    return state.compare(a, b)


def patch(files, path, old, new):
    out = dict(files)
    assert old in out[path]
    out[path] = out[path].replace(old, new)
    return out


class Compare(unittest.TestCase):
    def test_same_tree_is_stable(self):
        a = slotkit.snap(slotkit.make_tree(slotkit.BASE))
        b = slotkit.snap(slotkit.make_tree(slotkit.BASE))
        self.assertEqual(a, b)
        self.assertEqual(state.compare(a, b), {})

    def test_class_value_and_derived_slot(self):
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", '"expression": False', '"expression": True'))
        keys = {k.replace("\t", " ") for k in ch}
        # the edited slot, and what the metaclass derived from it in the same class
        self.assertIn("C sqlglot.core Base ARGS", keys)
        self.assertNotIn("C sqlglot.core Base NAME", keys)

    def test_module_value_and_copy_in_another_module(self):
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", "LEVEL = 3", "LEVEL = 4"))
        keys = {k.replace("\t", " ") for k in ch}
        self.assertIn("M sqlglot.core LEVEL", keys)
        self.assertIn("M sqlglot.other COPY", keys)      # derived at import
        self.assertIn("M sqlglot.other DOUBLE", keys)
        fn = [k for k in ch if k.endswith("\tfn")]
        self.assertTrue(fn, "a default argument that took the value is part of the function")

    def test_nested_class_and_dict_keys(self):
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", "FLAG = False", "FLAG = True"))
        self.assertEqual({k.replace("\t", " ") for k in ch}, {"C sqlglot.core Child.Inner FLAG"})
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", '"b": (1, 2)', '"b": (1, 3)'))
        self.assertEqual({k.replace("\t", " ") for k in ch}, {"M sqlglot.core TABLE"})

    def test_reordered_dict_is_a_change(self):
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", '{"x": 1, "y": 2}', '{"y": 2, "x": 1}'))
        self.assertEqual({k.replace("\t", " ") for k in ch}, {"M sqlglot.core ORDERED"})

    def test_function_body_is_not_a_slot_change_with_translation(self):
        files = dict(slotkit.BASE)
        files["sqlglot/other.py"] = files["sqlglot/other.py"].replace("return x + 1", "return x + 2")
        ch = changed(slotkit.BASE, files)
        self.assertTrue([k for k in ch if k.endswith("\tfn")])   # without a translation the code differs

    def test_new_and_removed_slots(self):
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", 'NAME = "base"', 'NAME = "base"\n    EXTRA = 1'))
        self.assertTrue(any(v[0] is None for v in ch.values()))
        ch = changed(patch(slotkit.BASE, "sqlglot/core.py", 'NAME = "base"', 'NAME = "base"\n    EXTRA = 1'), slotkit.BASE)
        self.assertTrue(any(v[1] is None for v in ch.values()))

    def test_enum_member_value(self):
        ch = changed(slotkit.BASE, patch(slotkit.BASE, "sqlglot/core.py", "RED = 1", "RED = 2"))
        self.assertTrue([k for k in ch if "Color" in k], ch)

    def test_import_error_is_a_slot(self):
        files = dict(slotkit.BASE)
        files["sqlglot/broken.py"] = "raise ValueError('x')\n"
        ch = changed(slotkit.BASE, files)
        self.assertTrue([k for k in ch if k[0] == "E"])


DYN = dict(slotkit.BASE)
DYN.update({
    "sqlglot/tok.py": """
        class Meta(type):
            def __new__(mcs, name, bases, ns):
                k = super().__new__(mcs, name, bases, ns)
                k._Q = tuple(k.QUOTES)
                return k


        class Tok(metaclass=Meta):
            QUOTES = ["'"]
            OTHER = 1
    """,
    "sqlglot/dyn.py": """
        def make(base):
            return type("Dyn", (base,), {})
    """,
    "sqlglot/use.py": """
        from sqlglot.dyn import make
        from sqlglot.tok import Tok


        class Holder:
            cls = make(Tok)
    """,
})


class Dynamic(unittest.TestCase):
    """Classes built by `type(...)` in one module and stored on a class of another are owned by no namespace."""

    def test_derived_class_content_is_compared(self):
        ch = changed(DYN, patch(DYN, "sqlglot/tok.py", """QUOTES = ["'"]""", """QUOTES = ["'", '"']"""))
        keys = {k.replace("\t", " ") for k in ch}
        self.assertIn("C sqlglot.tok Tok QUOTES", keys)
        self.assertIn("O sqlglot.tok Tok", keys)           # Dyn._Q was derived from it: not a slot of the snapshot

    def test_unrelated_edit_leaves_it_alone(self):
        ch = changed(DYN, patch(DYN, "sqlglot/tok.py", "OTHER = 1", "OTHER = 2"))
        self.assertEqual({k.replace("\t", " ") for k in ch}, {"C sqlglot.tok Tok OTHER"})

    def test_class_reached_from_a_value_is_covered(self):
        files = dict(DYN)
        files["sqlglot/loose.py"] = """
            from sqlglot.dyn import make
            from sqlglot.tok import Tok
            K = [make(Tok)]
        """
        a = slotkit.snap(slotkit.make_tree(files))
        self.assertIn("O\tsqlglot.tok\tTok", a)


    def test_dropped_classes_do_not_count(self):
        # a temporary class is cyclic garbage until the collector runs: whether it is still a subclass must not
        # depend on when that was
        files = dict(DYN)
        del files["sqlglot/use.py"]
        files["sqlglot/temp.py"] = """
            import gc
            from sqlglot.dyn import make
            from sqlglot.tok import Tok

            gc.disable()
            _t = make(Tok)
            del _t
        """
        snap = slotkit.snap(slotkit.make_tree(files))
        self.assertEqual([k for k in snap if k[0] == "O"], [])


class Blind(unittest.TestCase):
    """What the fingerprints cannot vouch for is never "unchanged"."""

    def test_defaultdict_factory(self):
        files = dict(slotkit.BASE)
        files["sqlglot/dd.py"] = """
            from collections import defaultdict
            D = defaultdict(set, {"a": {1}})
        """
        ch = changed(files, patch(files, "sqlglot/dd.py", "defaultdict(set,", "defaultdict(list,"))
        self.assertIn("M\tsqlglot.dd\tD", ch)

    def test_builtin_subclasses_by_type(self):
        files = dict(slotkit.BASE)
        files["sqlglot/sub.py"] = """
            import collections


            class D(dict):
                pass


            class S(set):
                pass


            X = D(a=1)
            Y = S({1})
            Z = collections.OrderedDict(a=1)
            W = collections.Counter("ab")
        """
        for old, new, name in (("X = D(a=1)", "X = dict(a=1)", "X"), ("Y = S({1})", "Y = {1}", "Y"),
                               ("Z = collections.OrderedDict(a=1)", "Z = dict(a=1)", "Z"),
                               ("W = collections.Counter(\"ab\")", "W = dict(a=1, b=1)", "W")):
            self.assertIn("M\tsqlglot.sub\t" + name, changed(files, patch(files, "sqlglot/sub.py", old, new)), name)

    def test_instance_dict_of_a_subclass(self):
        files = dict(slotkit.BASE)
        files["sqlglot/sub.py"] = """
            class D(dict):
                pass


            X = D(a=1)
            X.tag = "one"
        """
        self.assertIn("M\tsqlglot.sub\tX", changed(files, patch(files, "sqlglot/sub.py", '"one"', '"two"')))

    def test_function_from_outside_the_tree_has_no_place(self):
        files = dict(slotkit.BASE)
        files["sqlglot/fn.py"] = """
            import textwrap
            from sqlglot.other import fn as inside

            X = textwrap.dedent
            Y = inside
        """
        snap = slotkit.snap(slotkit.make_tree(files))
        self.assertTrue(snap["M\tsqlglot.fn\tX"].startswith("f ") and "@" not in snap["M\tsqlglot.fn\tX"], snap["M\tsqlglot.fn\tX"])
        self.assertIn("@sqlglot/other.py:", snap["M\tsqlglot.fn\tY"])

    def test_cycle_does_not_hide_a_change(self):
        # B holds A, and A holds B: the fingerprint of B must still follow A's content
        files = dict(slotkit.BASE)
        files["sqlglot/cyc.py"] = """
            A = {"v": 1}
            B = {"a": A}
            A["b"] = B
        """
        ch = changed(files, patch(files, "sqlglot/cyc.py", '"v": 1', '"v": 2'))
        self.assertEqual({k.split("\t")[2] for k in ch}, {"A", "B"})

    def test_cycles_of_another_shape_differ(self):
        files = dict(slotkit.BASE)
        files["sqlglot/cyc.py"] = """
            a = []
            b = [a]
            a.append(b)
            X = a
        """
        other = patch(files, "sqlglot/cyc.py", "a.append(b)", "a.append(a)")
        self.assertIn("M\tsqlglot.cyc\tX", changed(files, other))

    def test_import_error_in_both_trees_still_counts(self):
        files = dict(slotkit.BASE)
        files["sqlglot/broken.py"] = "raise ValueError('x')\n"
        ch = changed(files, patch(files, "sqlglot/broken.py", "'x'", "'y'"))
        self.assertEqual({k for k in ch if k[0] == "E"}, {"E\tsqlglot.broken"})

    def test_opaque_value_counts_as_changed(self):
        files = dict(slotkit.BASE)
        files["sqlglot/op.py"] = "X = object()\nY = 1\n"
        ch = changed(files, files)
        self.assertEqual(set(ch), {"M\tsqlglot.op\tX"})
        self.assertEqual(ch["M\tsqlglot.op\tX"][0][0], "?")

    def test_known_native_objects_are_not_opaque(self):
        files = dict(slotkit.BASE)
        files["sqlglot/nat.py"] = """
            import threading
            import typing

            LOCK = threading.RLock()
            REF = typing.ForwardRef("int")
        """
        self.assertEqual(changed(files, files), {})

    def test_copy_and_alias_differ(self):
        files = dict(slotkit.BASE)
        files["sqlglot/al.py"] = """
            D = {"a": [1]}
            X = D
            Y = {"inner": D}
        """
        ch = changed(files, patch(files, "sqlglot/al.py", "X = D", "X = {**D}"))
        self.assertIn("M\tsqlglot.al\tX", ch)
        ch = changed(files, patch(files, "sqlglot/al.py", 'Y = {"inner": D}', 'Y = {"inner": {**D}}'))
        self.assertIn("M\tsqlglot.al\tY", ch)

    def test_aliasing_is_not_noise(self):
        files = dict(slotkit.BASE)
        files["sqlglot/al.py"] = """
            D = {"a": [1]}
            X = D
            LIMIT = 3
        """
        self.assertEqual(set(changed(files, patch(files, "sqlglot/al.py", "LIMIT = 3", "LIMIT = 4"))), {"M\tsqlglot.al\tLIMIT"})


class Unstable(unittest.TestCase):
    def test_order_that_varies_between_processes(self):
        # a dict built from a set of classes has no fixed order: the pinned state flags the slot, and a
        # reordering alone is then no change, while a changed entry still is
        files = dict(slotkit.BASE)
        files["sqlglot/sets.py"] = """
            class A: pass
            class B: pass
            class C: pass
            class D: pass
            class E: pass
            class F: pass
            ALL = {A, B, C, D, E, F}
            META = dict.fromkeys(ALL, 1)
            TYPES = tuple(ALL)
        """
        tree = slotkit.make_tree(files)
        out = os.path.join(tree, "state.json.gz")
        slotkit.run("""
            import sys
            sys.argv = ["state", %r, %r]
            from selector import state
            state.main()
        """ % (tree, out), tree)
        pinned = state.load(out)
        # the same tree, observed in another process: nothing changed
        self.assertEqual(state.compare(pinned, slotkit.snap(tree)), {})
        # an entry that differs is still a change
        files["sqlglot/sets.py"] = files["sqlglot/sets.py"].replace("META = dict.fromkeys(ALL, 1)", "META = dict.fromkeys(ALL, 2)")
        ch = state.compare(pinned, slotkit.snap(slotkit.make_tree(files)))
        self.assertIn("M\tsqlglot.sets\tMETA", ch)


class Body(unittest.TestCase):
    def test_location_suffix_only_for_functions(self):
        self.assertEqual(state.body("f abc@sqlglot/x.py:1-2"), ("f", "abc", "abc"))
        # a string value that contains an @ is still compared whole
        self.assertEqual(state.body("d str:'a@b'"), ("d", "str:'a@b'", "str:'a@b'"))
        self.assertNotEqual(state.body("d str:'a@b'"), state.body("d str:'a@c'"))
        pair = "0123456789abcdef0123~fedcba9876543210fedc"
        self.assertEqual(state.body("d " + pair), ("d", "0123456789abcdef0123", "fedcba9876543210fedc"))

    def test_compare_flags(self):
        a = {"M\tm\tX": "d 0123456789abcdef0123~aaaaaaaaaaaaaaaaaaaa", "U\tM\tm\tX": "u"}
        b = {"M\tm\tX": "d 9999999999999999999a~aaaaaaaaaaaaaaaaaaaa"}
        self.assertEqual(state.compare(a, b), {})
        b = {"M\tm\tX": "d 9999999999999999999a~bbbbbbbbbbbbbbbbbbbb"}
        self.assertIn("M\tm\tX", state.compare(a, b))
        del a["U\tM\tm\tX"]
        b = {"M\tm\tX": "d 9999999999999999999a~aaaaaaaaaaaaaaaaaaaa"}
        self.assertIn("M\tm\tX", state.compare(a, b))
        a["N\tM\tm\tX"] = "n"
        b = {"M\tm\tX": a["M\tm\tX"]}
        self.assertIn("M\tm\tX", state.compare(a, b))


if __name__ == "__main__":
    unittest.main()

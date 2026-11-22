"""codediff.values_only: which import-time edits the comparison of import states may answer for."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from selector import codediff  # noqa: E402

SRC = '''"""Module doc."""
import os
from x import y


class A(B, metaclass=M):
    """Class doc."""
    X = 1
    Y: int = 2
    __slots__ = ("a",)

    def f(self, k=1):
        """Function doc."""
        return k

    @property
    def g(self):
        return 1


Z, W = 1, 2
TABLE = {"a": lambda x: x + 1}
if os.name:
    V = 1
__all__ = ["A"]
'''


def only(new):
    return codediff.values_only(SRC, new)


class ValuesOnly(unittest.TestCase):
    def test_values_and_function_bodies(self):
        self.assertTrue(only(SRC.replace("X = 1", "X = 3")))
        self.assertTrue(only(SRC.replace("Y: int = 2", "Y: int = 5")))
        self.assertTrue(only(SRC.replace("Z, W = 1, 2", "Z, W = 1, 3")))
        self.assertTrue(only(SRC.replace("x + 1", "x + 2")))
        self.assertTrue(only(SRC.replace("return k", "return k + 1")))
        self.assertTrue(only(SRC.replace("return 1", "return 2")))

    def test_everything_else_is_not_covered(self):
        self.assertFalse(only(SRC.replace("Module doc.", "Other doc.")))
        self.assertFalse(only(SRC.replace("Class doc.", "Other doc.")))
        self.assertFalse(only(SRC.replace("Function doc.", "Other doc.")))
        self.assertFalse(only(SRC.replace("from x import y", "from x import z")))
        self.assertFalse(only(SRC.replace("class A(B,", "class A(C,")))
        self.assertFalse(only(SRC.replace("metaclass=M", "metaclass=N")))
        self.assertFalse(only(SRC.replace("def f(self, k=1)", "def f(self, k=2)")))
        self.assertFalse(only(SRC.replace("    @property\n", "")))
        self.assertFalse(only(SRC.replace("if os.name:", "if not os.name:")))
        self.assertFalse(only(SRC.replace("    V = 1\n", "    V = 2\n")))          # inside a condition
        self.assertFalse(only(SRC.replace('__slots__ = ("a",)', '__slots__ = ("b",)')))   # dunder
        self.assertFalse(only(SRC.replace('__all__ = ["A"]', '__all__ = []')))
        self.assertFalse(only(SRC.replace("    X = 1\n", "")))                     # a deleted statement
        self.assertFalse(only(SRC + "Q = 1\n"))                                    # an added one
        self.assertFalse(only(SRC.replace("X = 1", "X: int = 1")))                 # another kind of statement
        self.assertFalse(only(SRC.replace("Z, W = 1, 2", "Z = 1\nW = 2")))
        self.assertFalse(only(SRC.replace("Z, W", "W, Z")))                        # other targets

    def test_targets_that_reach_other_objects(self):
        base = SRC + "A.attr = 1\nTABLE['k'] = 1\n"
        self.assertTrue(codediff.values_only(base, base.replace("A.attr = 1", "A.attr = 1")))
        self.assertFalse(codediff.values_only(base, base.replace("A.attr = 1", "A.attr = 2")))
        self.assertFalse(codediff.values_only(base, base.replace("TABLE['k'] = 1", "TABLE['k'] = 2")))

    def test_broken_source(self):
        self.assertFalse(only(SRC + "def (:\n"))
        self.assertFalse(codediff.values_only("x = (", SRC))


if __name__ == "__main__":
    unittest.main()

"""A proof that a slow test runs exactly as before when a patch only replaces one comparison operator.

maps/cmp.json.gz (built by tools/cmpmap from the pinned commit) holds, for each slow test and each
single comparison `a OP b` it executes, the comparison operators that returned the very same bool as
OP on the same operands at every execution, in a fresh process and inside the whole-suite session.

If the patched module is the pinned module with the operator of exactly one such comparison replaced
(the two syntax trees are otherwise identical), and the new operator is among those, then by induction
over the test's execution every comparison yields the value it yielded before: the test executes the
same instructions on the same values, so its outcome cannot change.
"""
import ast
import gzip
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "cmp.json.gz")
ORDER = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"]


class CmpMap:
    """Two layouts of maps/cmp.json.gz:

    by test   {"tests": {test id: {site: [op, mask]}}}
    by site   {"ids": [test ids], "skipped": [indexes without a profile],
               "sites": {site: [op, {mask: "index ranges"}]}}        (every recorded test, see tools/cmpmap_alt)

    A profiled test that never executes a comparison cannot notice which operator stands there."""

    def __init__(self, path=PATH):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.sites = d.get("sites")
        if self.sites is None:
            self.by_test = d["tests"]
            self.tests = self.by_test          # the tests that have a profile
        else:
            skipped = set(d.get("skipped", ()))
            self.index = {t: i for i, t in enumerate(d["ids"]) if i not in skipped}
            self.tests = self.index
            self._cache = {}

    def _site(self, site):
        got = self._cache.get(site)
        if got is None:
            rec = self.sites.get(site)
            masks = {}
            if rec is not None:
                for mask, ranges in rec[1].items():
                    for part in ranges.split(","):
                        a, _, b = part.partition("-")
                        for i in range(int(a), int(b or a) + 1):
                            masks[i] = int(mask)
            got = self._cache[site] = (rec[0] if rec is not None else None, masks)
        return got

    def same_outcome(self, test_id, site, old_op, new_op):
        """True if replacing `old_op` by `new_op` at `site` cannot change what `test_id` does: the test
        never executes the comparison, or `new_op` gave the same bool at every execution it makes."""
        if new_op not in ORDER:
            return False
        if self.sites is None:
            got = self.by_test.get(test_id, {}).get(site)
            return bool(got and got[0] == old_op and got[1] >> ORDER.index(new_op) & 1)
        i = self.index.get(test_id)
        if i is None:
            return False
        op, masks = self._site(site)
        if i not in masks:
            return True
        return op == old_op and bool(masks[i] >> ORDER.index(new_op) & 1)


class _Different(Exception):
    pass


def _walk(a, b, found):
    if type(a) is not type(b):
        raise _Different()
    if isinstance(a, ast.Compare) and len(a.ops) == 1 and len(b.ops) == 1 and type(a.ops[0]) is not type(b.ops[0]):
        found.append((a, type(a.ops[0]).__name__, type(b.ops[0]).__name__))
        if len(found) > 1:
            raise _Different()
        _walk(a.left, b.left, found)
        _walk(a.comparators[0], b.comparators[0], found)
        return
    for name in a._fields:
        x, y = getattr(a, name, None), getattr(b, name, None)
        if isinstance(x, list):
            if not isinstance(y, list) or len(x) != len(y):
                raise _Different()
            for p, q in zip(x, y):
                if isinstance(p, ast.AST):
                    _walk(p, q, found)
                elif p != q or type(p) is not type(q):
                    raise _Different()
        elif isinstance(x, ast.AST):
            if not isinstance(y, ast.AST):
                raise _Different()
            _walk(x, y, found)
        elif x != y or type(x) is not type(y):
            raise _Different()


def single_compare_swap(old_source, new_source, path):
    """(site, old operator, new operator) if the patch replaces the operator of one comparison and
    nothing else; else None."""
    try:
        a, b = ast.parse(old_source, path), ast.parse(new_source, path)
        found = []
        _walk(a, b, found)
    except (_Different, SyntaxError, ValueError, RecursionError):
        return None
    if len(found) != 1:
        return None
    node, old_op, new_op = found[0]
    return ("%s:%d:%d:%d:%d" % (path, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset), old_op, new_op)

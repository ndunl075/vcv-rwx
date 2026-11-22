"""A proof that a test runs exactly as before when a patch only replaces the operator of one comparison.

maps/cmp.json.gz (built by tools/cmpmap from the pinned commit) holds, for every single comparison `a OP b`
(OP not `in`/`not in`) that the whole suite executes and for every operator ALT that a patch could replace OP
by without always flipping the value, the tests for which ALT was not shown to be a no-op:

  * a test where, on the very same operands, ALT answered another bool, raised, or was evaluated on operands
    whose comparison could not be evaluated without side effects or depends on object identity (a float,
    an object with its own __eq__, ...), in the test's own fresh process, in the whole-suite session
    (the failing tests are labeled in one whole-suite session, so the record must hold there too, with
    earlier tests having run) or in any process they started;
  * every test of the file, if that happened in the session while the test ran (class and module fixtures
    are shared by the file), every test, if it happened in the session while a module was being imported or
    outside any test, the tests that start processes, if it happened in a process one of them started;
  * a test whose runs were not clean (failed, were not profiled, started a process that does not trace,
    gave different execution counts under two hash seeds).

If the patched module is the pinned module with exactly one comparison operator replaced (the two syntax
trees are otherwise identical, the text differs only on the lines of that comparison, the comparison sits in
the body of a function), and the new operator is an ALT recorded as a no-op for a test, then by induction over
the test's execution every comparison yields the value it yielded before: the test executes the same
instructions on the same values (in the same session, for the state it shares with other tests), so its
outcome cannot change.  The map is only evidence about the pinned commit's runs; the patch is queried at run time.
"""
import ast
import gzip
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps", "cmp.json.gz")
# the replacements that can keep a comparison's value (the others return the complement of the original)
ALTS = {"Eq": ["Is"], "NotEq": ["IsNot"], "Is": ["Eq"], "IsNot": ["NotEq"],
        "Lt": ["LtE", "Gt"], "LtE": ["Lt", "GtE"], "Gt": ["GtE", "Lt"], "GtE": ["Gt", "LtE"]}


class CmpMap:
    def __init__(self, path=PATH):
        with gzip.open(path, "rt") as f:
            d = json.load(f)
        self.tests = {t: i for i, t in enumerate(d["ids"])}   # test id -> index; the tests the map covers
        self.bad = set(d["bad"])                               # tests with no usable profile: never proven
        self._sets = d["sets"]
        self._decoded = {}
        self.sites = d["sites"]                                # path -> {"line:col:endline:endcol": [op, set id per ALTS[op]]}

    def _set(self, sid):
        got = self._decoded.get(sid)
        if got is None:
            got = self._decoded[sid] = frozenset(self._sets[sid])
        return got

    def same_outcome(self, test_id, site, old_op, new_op):
        """True if the test provably runs as before when the comparison at `site` (path:line:col:endline:endcol)
        changes from `old_op` to `new_op`."""
        t = self.tests.get(test_id)
        if t is None or t in self.bad or old_op not in ALTS or new_op not in ALTS[old_op]:
            return False
        path, _, key = site.partition(":")
        entry = self.sites.get(path, {}).get(key)
        if entry is None or entry[0] != old_op:
            return False
        sid = entry[1 + ALTS[old_op].index(new_op)]
        return sid >= 0 and t not in self._set(sid)


class _Different(Exception):
    pass


def _walk(a, b, found, in_func):
    """Compare two syntax trees; `found` collects the single-operator comparisons whose operator differs."""
    if type(a) is not type(b):
        raise _Different()
    if isinstance(a, ast.Compare):
        if len(a.ops) != len(b.ops):
            raise _Different()
        if len(a.ops) == 1 and type(a.ops[0]) is not type(b.ops[0]):
            found.append((a, in_func, type(a.ops[0]).__name__, type(b.ops[0]).__name__))
            if len(found) > 1:
                raise _Different()
            _walk(a.left, b.left, found, in_func)
            _walk(a.comparators[0], b.comparators[0], found, in_func)
            return
    for name in a._fields:
        x, y = getattr(a, name, None), getattr(b, name, None)
        inner = in_func
        # decorators, defaults, annotations and base classes run when the def or class statement runs;
        # a class body runs at class creation: neither is the body of a function
        if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and name == "body":
            inner = True
        elif isinstance(a, ast.ClassDef) and name == "body":
            inner = False
        if isinstance(x, list):
            if not isinstance(y, list) or len(x) != len(y):
                raise _Different()
            for p, q in zip(x, y):
                if isinstance(p, ast.AST):
                    _walk(p, q, found, inner)
                elif type(p) is not type(q) or repr(p) != repr(q):
                    raise _Different()
        elif isinstance(x, ast.AST):
            if not isinstance(y, ast.AST):
                raise _Different()
            _walk(x, y, found, inner)
        elif type(x) is not type(y) or repr(x) != repr(y):   # repr: -0.0 and 0.0, nan
            raise _Different()


def _not_literal(n):
    if isinstance(n, ast.Constant):
        return any(n.value is s for s in (None, True, False, Ellipsis))
    return isinstance(n, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))


def single_compare_swap(old_source, new_source, path):
    """(site, old operator, new operator) if the patch replaces the operator of one comparison in the body of
    a function and changes nothing else (not even a comment or the line count); else None."""
    if not (path.startswith("sqlglot/") and path.endswith(".py")):
        return None
    try:
        old = old_source.decode("utf-8") if isinstance(old_source, bytes) else old_source
        new = new_source.decode("utf-8") if isinstance(new_source, bytes) else new_source
        if "\r" in old or "\r" in new or "\x0c" in old or "\x0c" in new:
            return None
        ol, nl = old.split("\n"), new.split("\n")
        if len(ol) != len(nl):
            return None
        a, b = ast.parse(old, path), ast.parse(new, path)
        found = []
        _walk(a, b, found, False)
    except (_Different, SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    if len(found) != 1:
        return None
    node, in_func, old_op, new_op = found[0]
    if not in_func or old_op not in ALTS or new_op not in ALTS[old_op]:
        return None
    if new_op in ("Is", "IsNot") and not all(_not_literal(x) for x in (node.left, node.comparators[0])):
        return None   # `is` with a literal compiles with a SyntaxWarning: the patched module would not load the same way
    changed = [i + 1 for i, (x, y) in enumerate(zip(ol, nl)) if x != y]
    if not changed or changed[0] < node.lineno or changed[-1] > node.end_lineno:
        return None
    return ("%s:%d:%d:%d:%d" % (path, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset), old_op, new_op)

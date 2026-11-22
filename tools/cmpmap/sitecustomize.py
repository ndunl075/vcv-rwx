"""Comparison profiler, loaded by every Python process of a traced run (active when RWX_CMP_DIR is set).

Every single comparison `a OP b` in sqlglot's source is compiled as a call that evaluates the same
comparison once and, on the same two operands, also asks the other comparison operators of its family
what they would have answered.  Per comparison site the profile keeps the operators that returned the
very same bool as the original on every execution.  If a patch replaces the operator of one comparison
by one of those, every execution recorded here takes the same path with the same values.

Writes $RWX_CMP_DIR/<pid>.cmp at exit:  <path>:<line>:<col>:<end line>:<end col> <op> <mask> <count>
(mask bit i set: operator ORDER[i] always agreed with the original one).
"""
import os
import sys

_dir = os.environ.get("RWX_CMP_DIR")
if _dir:
    import ast
    import builtins
    import importlib.machinery
    import operator

    _root = os.path.join(os.environ["RWX_CMP_ROOT"], "")
    ORDER = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"]
    FUNC = {"Eq": operator.eq, "NotEq": operator.ne, "Lt": operator.lt, "LtE": operator.le, "Gt": operator.gt,
            "GtE": operator.ge, "Is": operator.is_, "IsNot": operator.is_not,
            "In": lambda a, b: a in b, "NotIn": lambda a, b: a not in b}
    IDENTITY = ("Eq", "NotEq", "Is", "IsNot")
    ORDERING = ("Lt", "LtE", "Gt", "GtE")
    PRIMITIVE = (int, float, str, bytes, bool, type(None))
    BIT = {name: 1 << i for i, name in enumerate(ORDER)}
    _state = {}

    def _cmp(site, a, b, op):
        r = FUNC[op](a, b)
        st = _state.get(site)
        if st is None:
            st = _state[site] = [op, sum(BIT[n] for n in ORDER if n != op and op not in ("In", "NotIn")
                                         and n not in ("In", "NotIn")), 0]
        st[2] += 1
        m = st[1]
        if m:
            if type(r) is not bool:
                st[1] = 0
                return r
            prim = type(a) in PRIMITIVE and type(b) in PRIMITIVE
            for name in ORDER:
                bit = BIT[name]
                if not m & bit:
                    continue
                if name in ORDERING and not prim:
                    m &= ~bit   # asking an ordering of arbitrary objects is not done
                    continue
                try:
                    x = FUNC[name](a, b)
                    ok = x is r
                except Exception:
                    ok = False
                if not ok:
                    m &= ~bit
            st[1] = m
        return r

    builtins.__rwx_cmp__ = _cmp

    class _Rewrite(ast.NodeTransformer):
        def __init__(self, rel):
            self.rel = rel

        def visit_Compare(self, node):
            self.generic_visit(node)
            if len(node.ops) != 1:
                return node
            site = "%s:%d:%d:%d:%d" % (self.rel, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
            call = ast.Call(func=ast.Name(id="__rwx_cmp__", ctx=ast.Load()),
                            args=[ast.Constant(site), node.left, node.comparators[0],
                                  ast.Constant(type(node.ops[0]).__name__)], keywords=[])
            return ast.copy_location(call, node)

    _s2c = importlib.machinery.SourceFileLoader.source_to_code

    def source_to_code(self, data, path, *, _optimize=-1):
        p = os.fspath(path)
        if p.startswith(_root + "sqlglot" + os.sep):
            tree = ast.parse(data, p)
            tree = ast.fix_missing_locations(_Rewrite(p[len(_root):]).visit(tree))
            return compile(tree, p, "exec", dont_inherit=True, optimize=_optimize)
        return _s2c(self, data, path, _optimize=_optimize)

    importlib.machinery.SourceFileLoader.source_to_code = source_to_code

    def _end(*a):
        try:
            with open(os.path.join(_dir, "%d.cmp" % os.getpid()), "w") as f:
                for site, (op, m, n) in _state.items():
                    f.write("%s %s %d %d\n" % (site, op, m, n))
        except Exception:
            pass

    import atexit

    atexit.register(_end)
    _os_exit = os._exit

    def _exit(code):
        _end()
        _os_exit(code)

    os._exit = _exit

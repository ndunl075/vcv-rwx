"""Comparison profiler, loaded by every Python process of a traced run (it sits on PYTHONPATH, so spawned
processes load it too).  Active only when RWX_CMP_DIR is set.

Every single comparison `a OP b` in sqlglot's source is compiled as a call that evaluates the comparison
exactly as before and, on the same two operands, also asks the operators a patch could replace OP by
(ALTS) what they would have answered.  An alternative that returned the very same bool counts as
"agreeing" for that execution; every other outcome counts against it: another bool, an exception, a
result that is not a bool, or operands whose types are not known to be free of side effects and
independent of object identity (see _cls).  Alternatives are only evaluated for such types, so the
extra evaluation cannot change the state of the traced program.

Appends tab separated records to $RWX_CMP_DIR/<pid>.cmp, each written at once (a process that is killed or
ends with os._exit loses nothing but its counts):
    S pid ppid time exe            process start
    D pid imp cur site op alt      alternative `alt` of the comparison `site` (operator `op`) disagreed in the
                                   test `cur` (the whole-suite run marks it; "-" otherwise); imp is 1 if the
                                   execution happened while a module was being imported
    C pid site op count live       at exit: executions seen while an alternative was alive, and the bit mask
                                   of the alternatives still alive
    A pid event detail             a process was started (os.fork, subprocess.Popen, ...): detail says whether
                                   the child inherits this tracer
    P pid                          the whole-suite pytest process (written by rwx_cmpmarks)
    F pid test                     the test failed (written by rwx_cmpmarks)
Site: <path relative to $RWX_CMP_ROOT>:<line>:<col>:<end line>:<end col>, the position of the Compare node.
"""
import os
import sys

_dir = os.environ.get("RWX_CMP_DIR")
if _dir:
    import ast
    import builtins
    import enum
    import importlib.machinery
    import operator

    _root = os.path.join(os.environ["RWX_CMP_ROOT"], "")
    ORDER = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"]
    FUNC = {"Eq": operator.eq, "NotEq": operator.ne, "Lt": operator.lt, "LtE": operator.le, "Gt": operator.gt,
            "GtE": operator.ge, "Is": operator.is_, "IsNot": operator.is_not,
            "In": lambda a, b: a in b, "NotIn": lambda a, b: a not in b}
    # the replacements a patch can make that can ever keep a comparison's value (the others return the
    # complement of the original on every pair of operands)
    ALTS = {"Eq": ["Is"], "NotEq": ["IsNot"], "Is": ["Eq"], "IsNot": ["NotEq"],
            "Lt": ["LtE", "Gt"], "LtE": ["Lt", "GtE"], "Gt": ["GtE", "Lt"], "GtE": ["Gt", "LtE"]}
    BIT = {name: 1 << i for i, name in enumerate(ORDER)}
    ALTMASK = {op: sum(BIT[a] for a in alts) for op, alts in ALTS.items()}
    ORDERING = ("Lt", "LtE", "Gt", "GtE")
    ORD_TYPES = (int, str, bytes, bool)   # exact types whose ordering is a pure function of the values
    _OBJ_EQ = (object.__eq__, str.__eq__, int.__eq__)
    _OBJ_NE = (object.__ne__, str.__ne__, int.__ne__)
    _state = {}
    _cur = [None]
    _types = {}

    def _cls(t):
        """bit 1: `==` and `!=` of two instances run no user code; bit 2: `a == b` is `a is b` for it
        (None, bool, enum members, classes, objects that compare by identity), and identity is stable."""
        v = _types.get(t)
        if v is None:
            v = 0
            if type(t).__getattribute__ is not type.__getattribute__:
                v = 0     # a metaclass that runs code on attribute access
            elif t is type(None) or t is bool:
                v = 3
            elif t in (int, str, bytes):
                v = 1
            elif t is float or t is complex:
                v = 0     # values that come from clocks and arithmetic: no proof
            elif issubclass(t, enum.Enum) and not issubclass(t, enum.Flag):
                v = 3 if (t.__eq__ in _OBJ_EQ and t.__ne__ in _OBJ_NE) else 0
            elif t.__eq__ is object.__eq__ and t.__ne__ is object.__ne__:
                v = 3
            _types[t] = v
        return v

    def _agree(name, a, b, r):
        ta, tb = type(a), type(b)
        if name in ORDERING:
            if ta in ORD_TYPES and tb in ORD_TYPES:
                try:
                    return FUNC[name](a, b) is r
                except Exception:
                    return False
            return False
        ca, cb = _cls(ta), _cls(tb)
        if not (ca & 2 and cb & 2):
            return False
        if name == "Is" or name == "IsNot":
            return FUNC[name](a, b) is r
        if not (ca & 1 and cb & 1):
            return False
        try:
            return FUNC[name](a, b) is r
        except Exception:
            return False

    _fd = os.open(os.path.join(_dir, "%d.cmp" % os.getpid()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    _write = os.write
    _getpid = os.getpid

    def _rec(s):
        try:
            _write(_fd, s.encode("utf-8", "replace"))
        except OSError:
            pass

    def _importing():
        f = sys._getframe(2)
        while f is not None:
            if f.f_code.co_filename.startswith("<frozen importlib"):
                return 1
            f = f.f_back
        return 0

    def _drop(site, op, name, st):
        st[1] &= ~BIT[name]
        _rec("D\t%d\t%d\t%s\t%s\t%s\t%s\n" % (_getpid(), _importing(), (_cur[0] or "-").replace("\t", " "), site, op, name))

    def _cmp(site, a, b, op):
        r = FUNC[op](a, b)
        st = _state.get(site)
        if st is None:
            st = _state[site] = [op, ALTMASK.get(op, 0), 0]
        m = st[1]
        if m:
            st[2] += 1
            if type(r) is not bool:
                for name in ALTS[op]:
                    if m & BIT[name]:
                        _drop(site, op, name, st)
            else:
                for name in ALTS[op]:
                    if m & BIT[name] and not _agree(name, a, b, r):
                        _drop(site, op, name, st)
        return r

    builtins.__rwx_cmp__ = _cmp

    def _mark(nodeid):
        _cur[0] = nodeid

    def _note(kind, text):
        _rec("%s\t%d\t%s\n" % (kind, _getpid(), text.replace("\t", " ").replace("\n", " ")))

    sys.rwx_cmp_mark = _mark
    sys.rwx_cmp_note = _note

    class _Rewrite(ast.NodeTransformer):
        def __init__(self, rel):
            self.rel = rel

        def visit_Compare(self, node):
            self.generic_visit(node)
            if len(node.ops) != 1 or isinstance(node.ops[0], (ast.In, ast.NotIn)):
                return node   # chained comparisons and membership tests are never proven
            site = "%s:%d:%d:%d:%d" % (self.rel, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
            call = ast.Call(func=ast.Name(id="__rwx_cmp__", ctx=ast.Load()),
                            args=[ast.Constant(site), node.left, node.comparators[0],
                                  ast.Constant(type(node.ops[0]).__name__)], keywords=[])
            return ast.copy_location(call, node)

    _s2c = importlib.machinery.SourceFileLoader.source_to_code

    _cache = os.environ.get("RWX_CMP_CACHE")

    def source_to_code(self, data, path, *, _optimize=-1):
        p = os.fspath(path)
        if p.startswith(_root + "sqlglot" + os.sep):
            key = None
            if _cache:
                import hashlib
                import marshal

                key = os.path.join(_cache, hashlib.sha1(p.encode() + b"\0" + bytes(data)).hexdigest() + ".%d" % _optimize)
                try:
                    with open(key, "rb") as f:
                        return marshal.loads(f.read())
                except (OSError, ValueError, EOFError, TypeError):
                    pass
            tree = ast.parse(data, p)
            tree = ast.fix_missing_locations(_Rewrite(p[len(_root):]).visit(tree))
            code = compile(tree, p, "exec", dont_inherit=True, optimize=_optimize)
            if key:
                try:
                    tmp = "%s.%d.tmp" % (key, os.getpid())
                    with open(tmp, "wb") as f:
                        f.write(marshal.dumps(code))
                    os.replace(tmp, key)
                except OSError:
                    pass
            return code
        return _s2c(self, data, path, _optimize=_optimize)

    importlib.machinery.SourceFileLoader.source_to_code = source_to_code

    _rec("S\t%d\t%d\t%.3f\t%s\n" % (_getpid(), os.getppid(), __import__("time").time(), sys.executable))

    def _audit(event, args):
        if event in ("os.fork", "os.forkpty"):
            _note("A", "%s inherits" % event)
        elif event in ("subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn", "os.system"):
            # the child traces only if it is this interpreter (or a `python`) with an environment that keeps RWX_CMP_DIR
            try:
                exe = os.fsdecode(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else repr(args[0])
                env = args[3] if event == "subprocess.Popen" else (args[2] if event in ("os.exec", "os.posix_spawn") else None)
                if event == "os.system":
                    ok = False
                else:
                    keeps = env is None or any(os.fsdecode(k) == "RWX_CMP_DIR" for k in (env.keys() if hasattr(env, "keys") else []))
                    ok = keeps and os.path.basename(exe).startswith("python")
                _note("A", "%s %s %s" % (event, "traced" if ok else "UNTRACED", exe))
            except Exception as e:
                _note("A", "%s UNTRACED %r" % (event, e))

    sys.addaudithook(_audit)

    def _end(*a):
        try:
            pid = _getpid()
            out = []
            for site, (op, m, n) in _state.items():
                if m:
                    out.append("C\t%d\t%s\t%s\t%d\t%d\n" % (pid, site, op, n, m))
            _write(_fd, "".join(out).encode())
        except Exception:
            pass

    import atexit

    atexit.register(_end)
    _os_exit = os._exit

    def _exit(code):
        _end()
        _os_exit(code)

    os._exit = _exit

"""Where does a change to a Python source file take effect?

`analyze(old_src, new_src)` compares the two syntax trees and returns an Effect:

  import_level  True when something that runs when the module is imported changed
                (class bodies, decorators, defaults, module-level statements, docstrings,
                a new function/class, ...).  Every test that loads the module is a candidate.
  regions       old-file line ranges (lo, hi) inside function bodies that changed; every test
                that executed a line in a range is a candidate.
  codes         (firstlineno, name) of code objects (lambdas) whose body changed; every test that
                started that code object is a candidate.
  text_only     the trees are identical (comment / whitespace change)
  syntax_error  the new source does not compile (import of the module fails)
  ...
Everything unknown ends in import_level=True, which is the conservative answer.
"""
import ast


class Effect:
    def __init__(self):
        self.import_level = False
        self.regions = []
        self.codes = []
        self.syntax_error = False
        self.text_only = False
        self.reason = ""

    def __repr__(self):
        return "<Effect import=%s regions=%s codes=%s syn=%s text=%s %s>" % (
            self.import_level, self.regions, self.codes, self.syntax_error, self.text_only, self.reason)


_SKIP_FIELDS = {"lineno", "col_offset", "end_lineno", "end_col_offset", "type_comment"}
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _fields(node):
    for name in node._fields:
        if name in _SKIP_FIELDS:
            continue
        yield name, getattr(node, name, None)


def _dump(node):
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _is_docstring(stmt):
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _is_type_checking(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _span(node):
    return (node.lineno, getattr(node, "end_lineno", node.lineno))


def _stmts_span(stmts):
    return (stmts[0].lineno, max(getattr(s, "end_lineno", s.lineno) for s in stmts))


def _first_lineno(fn):
    if getattr(fn, "decorator_list", None):
        return min([fn.lineno] + [d.lineno for d in fn.decorator_list])
    return fn.lineno


class _Walker:
    """Parallel walk of the old and new tree; records effects in old-file coordinates."""

    def __init__(self, eff, is_test_file=False):
        self.eff = eff
        self.is_test_file = is_test_file
        self.skipped = False

    # ctx: None at import level, or ("body", region_node_old) inside a function body
    def mismatch(self, old, ctx, whole=None):
        """A difference we cannot localise below `old` (an old-tree node)."""
        if ctx is None:
            self.eff.import_level = True
            self.eff.reason += "mismatch@%s;" % getattr(old, "lineno", "?")
        else:
            kind, stmt_span, fn = ctx
            if stmt_span is not None:
                self.eff.regions.append(stmt_span)
            if fn is not None and isinstance(fn, ast.Lambda):
                self.eff.codes.append((fn.lineno, "<lambda>"))

    def walk_stmt_lists(self, olds, news, ctx, container_span, in_class_or_module):
        """Align two statement lists by equality, recurse into pairs of the same shape."""
        od = [_dump(s) for s in olds]
        nd = [_dump(s) for s in news]
        # strip common prefix / suffix
        i = 0
        while i < len(od) and i < len(nd) and od[i] == nd[i]:
            i += 1
        j = 0
        while j < len(od) - i and j < len(nd) - i and od[len(od) - 1 - j] == nd[len(nd) - 1 - j]:
            j += 1
        o_mid = olds[i:len(olds) - j]
        n_mid = news[i:len(news) - j]
        if not o_mid and not n_mid:
            return
        if len(o_mid) == len(n_mid):
            for o, n in zip(o_mid, n_mid):
                if _dump(o) != _dump(n):
                    self.walk_stmt(o, n, ctx)
            return
        # statements added / removed
        if ctx is None:
            # import-level insertion / deletion
            if (self.is_test_file and not o_mid and all(
                    isinstance(s, _FUNCS) and s.name.startswith("test_") and not s.decorator_list for s in n_mid)
                    and not any(isinstance(o, _FUNCS) and o.name in {s.name for s in n_mid} for o in olds)):
                return  # a new test method cannot change other tests
            self.eff.import_level = True
            self.eff.reason += "stmtlist@%s;" % (olds[0].lineno if olds else "?")
            return
        kind, stmt_span, fn = ctx
        # inside a function body: the whole enclosing block's lines are the region
        if olds:
            self.eff.regions.append(_stmts_span(olds))
        elif container_span:
            self.eff.regions.append(container_span)
        else:
            self.eff.regions.append(stmt_span)
        if fn is not None and isinstance(fn, ast.Lambda):
            self.eff.codes.append((fn.lineno, "<lambda>"))

    def walk_stmt(self, o, n, ctx):
        """o and n are statements at the same position that differ."""
        if type(o) is not type(n):
            return self.mismatch(o, ctx)
        if _is_docstring(o) or _is_docstring(n):
            self.eff.import_level = True
            self.eff.reason += "docstring@%s;" % o.lineno
            return
        if isinstance(o, _FUNCS):
            # header (name, args incl. defaults, decorators, returns) must be equal: else import-level
            hdr = lambda f: (f.name, _dump(f.args), [_dump(d) for d in f.decorator_list], _dump(f.returns) if f.returns else None, [_dump(x) for x in getattr(f, 'type_params', [])])
            if hdr(o) != hdr(n):
                if ctx is None:
                    self.eff.import_level = True
                    self.eff.reason += "defheader@%s;" % o.lineno
                else:
                    # nested function re-defined inside a body: its def runs when the outer body runs
                    self.mismatch(o, ctx)
                return
            fctx = ("body", None, o)
            self.walk_stmt_lists(o.body, n.body, fctx, _stmts_span(o.body), False)
            return
        if isinstance(o, ast.ClassDef):
            hdr = lambda c: (c.name, [_dump(b) for b in c.bases], [_dump(k) for k in c.keywords], [_dump(d) for d in c.decorator_list], [_dump(x) for x in getattr(c, 'type_params', [])])
            if hdr(o) != hdr(n):
                return self.mismatch(o, ctx)
            if ctx is None:
                self.walk_stmt_lists(o.body, n.body, None, None, True)
            else:
                self.mismatch(o, ctx)
            return
        if isinstance(o, ast.If) and _is_type_checking(o.test) and _dump(o.test) == _dump(n.test):
            # the body of `if TYPE_CHECKING:` never runs; only the else branch can matter
            self.skipped = True
            if [_dump(x) for x in o.orelse] != [_dump(x) for x in n.orelse]:
                self.walk_stmt_lists(o.orelse, n.orelse, ctx, None, ctx is None)
            return
        # compound statements and simple statements: compare field by field
        sctx = ctx
        if ctx is not None:
            sctx = ("body", _span(o), ctx[2])
        self.walk_node(o, n, sctx, stmt=True)

    def walk_node(self, o, n, ctx, stmt=False):
        if type(o) is not type(n):
            return self.mismatch(o, ctx)
        if isinstance(o, ast.Lambda):
            # arguments and defaults are evaluated where the lambda appears; the body when it is called
            if _dump(o.args) != _dump(n.args):
                return self.mismatch(o, ctx)
            lctx = ("body", _span(o), o)
            if _dump(o.body) != _dump(n.body):
                self.walk_node(o.body, n.body, lctx)
            return
        if isinstance(o, _FUNCS):
            return self.walk_stmt(o, n, ctx)
        for name in o._fields:
            if name in _SKIP_FIELDS:
                continue
            ov, nv = getattr(o, name, None), getattr(n, name, None)
            if isinstance(ov, list):
                if not isinstance(nv, list):
                    return self.mismatch(o, ctx)
                if ov and isinstance(ov[0], ast.stmt) or nv and isinstance(nv[0], ast.stmt):
                    if ov and nv and not all(isinstance(x, ast.stmt) for x in ov + nv):
                        return self.mismatch(o, ctx)
                    if [_dump(x) for x in ov] != [_dump(x) for x in nv]:
                        self.walk_stmt_lists(ov, nv, ctx, None, False)
                    continue
                if len(ov) != len(nv):
                    self.mismatch(o, ctx)
                    continue
                for a, b in zip(ov, nv):
                    if isinstance(a, ast.AST) and isinstance(b, ast.AST):
                        if _dump(a) != _dump(b):
                            self.walk_node(a, b, ctx)
                    elif a != b:
                        self.mismatch(o, ctx)
            elif isinstance(ov, ast.AST):
                if not isinstance(nv, ast.AST):
                    self.mismatch(o, ctx)
                elif _dump(ov) != _dump(nv):
                    self.walk_node(ov, nv, ctx)
            else:
                if ov != nv:
                    self.mismatch(o, ctx)


def analyze(old_src, new_src, filename="<file>", is_test_file=False):
    eff = Effect()
    try:
        # bytes in: encoding cookies and BOMs are honoured, NUL / bad UTF-8 raise
        new_tree = ast.parse(new_src, filename)
        compile(new_tree, filename, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, RecursionError, MemoryError, UnicodeError):
        eff.syntax_error = True
        eff.import_level = True
        eff.reason = "syntax"
        return eff
    try:
        old_tree = ast.parse(old_src, filename)
    except Exception:
        eff.import_level = True
        eff.reason = "old unparseable"
        return eff
    if _dump(old_tree) == _dump(new_tree):
        eff.text_only = True
        return eff
    w = _Walker(eff, is_test_file)
    try:
        w.walk_stmt_lists(old_tree.body, new_tree.body, None, None, True)
    except RecursionError:
        eff.import_level = True
        eff.reason = "recursion"
    if not (eff.import_level or eff.regions or eff.codes):
        if w.skipped:
            eff.text_only = True  # only code that never runs (TYPE_CHECKING) differs
        else:
            eff.import_level = True  # could not localise anything: be conservative
            eff.reason = "nolocalisation"
    return eff

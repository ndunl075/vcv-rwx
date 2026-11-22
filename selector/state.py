"""The import state of sqlglot as *slots* with fingerprints, and the comparison of two such states.

A slot is a global of a sqlglot module ("M", module, name) or an attribute in the body of a sqlglot
class ("C", module, class qualname, attribute).  Its fingerprint is a digest of the value, followed
through containers and objects; classes, modules and functions count by identity (name, and for
functions what the code does, defaults and closure), so that a slot changes only when what it holds
changes.  Importing every module of the patched tree and comparing with the pinned state gives the
slots an import-time edit really changes, derived tables in other modules included.

    python -m selector.state <tree> <out.json.gz>          snapshot of a tree (run when the image is built)
    python -m selector.state --compare <job.json>          slots a patched tree changes (run per patch)
"""
import enum
import functools
import gzip
import hashlib
import importlib
import json
import logging
import os
import re
import sys
import types

from .codediff import code_hash

STRUCTURE = ("__slots__", "__match_args__", "__annotations__")


def is_data(name, value):
    """The slots whose reads the slot map records (tools/slotmap/sitecustomize.py, same test)."""
    if name[:2] == "__" or name.startswith("_abc_"):
        return False
    if isinstance(value, type):
        return False
    return not hasattr(type(value), "__get__")


class Snap:
    """Fingerprints are pairs (ordered, unordered): the second does not depend on the order of dict
    entries.  The order of some dicts depends on the process (they are built from sets of classes)."""

    def __init__(self, translate=None):
        self.memo = {}
        self.keep = []
        self.open = set()
        self.codes = {}
        self.translate = translate or {}   # digest of patched function code -> digest of the pinned code it replaces

    @staticmethod
    def hexdigest(parts):
        h = hashlib.blake2b(digest_size=10)
        for p in parts:
            h.update(p.encode("utf-8", "backslashreplace"))
            h.update(b"\0")
        return h.hexdigest()

    def digest(self, *parts):
        """Digest of strings and fingerprint pairs."""
        return (self.hexdigest([p if isinstance(p, str) else p[0] for p in parts]),
                self.hexdigest([p if isinstance(p, str) else p[1] for p in parts]))

    def code(self, code):
        h = code_hash(code, self.codes)
        return self.translate.get(h, h)

    def fp(self, v):
        t = type(v)
        if v is None or t is bool or t is int or t is float:
            s = "%s:%r" % (t.__name__, v)
            return (s, s)
        if t is str or t is bytes:
            if len(v) < 40:
                s = "%s:%r" % (t.__name__, v)
                return (s, s)
            return self.digest(t.__name__, repr(v))
        i = id(v)
        got = self.memo.get(i)
        if got is not None:
            return got
        if i in self.open:
            return ("<cycle>", "<cycle>")
        self.open.add(i)
        try:
            got = self._fp(v, t)
        finally:
            self.open.discard(i)
        if isinstance(got, str):
            got = (got, got)
        self.memo[i] = got
        self.keep.append(v)
        return got

    def _fp(self, v, t):
        fp = self.fp
        digest = self.digest
        if isinstance(v, type):
            return "class:%s.%s" % (v.__module__, v.__qualname__)
        if t is types.ModuleType or isinstance(v, types.ModuleType):
            return "module:%s" % v.__name__
        if t is types.FunctionType:
            cells = []
            for c in v.__closure__ or ():
                try:
                    cells.append(fp(c.cell_contents))
                except ValueError:
                    cells.append("<empty>")
            extra = fp(v.__dict__) if v.__dict__ else ""
            return digest("func", v.__module__ or "", v.__qualname__, self.code(v.__code__), fp(v.__defaults__),
                          fp(v.__kwdefaults__), self.join(cells), extra)
        if isinstance(v, logging.Logger):
            return "logger:%s" % v.name   # its handlers and manager are process-wide state, not sqlglot's
        if t is types.MethodType:
            return digest("method", fp(v.__func__), fp(v.__self__))
        if t is classmethod or t is staticmethod:
            return digest(t.__name__, fp(v.__func__))
        if t is property:
            return digest("property", fp(v.fget), fp(v.fset), fp(v.fdel))
        if t is functools.partial:
            return digest("partial", fp(v.func), fp(v.args), fp(v.keywords))
        if t is types.BuiltinFunctionType or t is types.MethodDescriptorType or t is types.WrapperDescriptorType:
            return "builtin:%s.%s" % (getattr(v, "__module__", None), getattr(v, "__qualname__", repr(v)))
        if isinstance(v, enum.Enum):
            return digest("enum", "%s.%s" % (t.__module__, t.__qualname__), v._name_ or "", fp(v._value_))
        if t is tuple or t is list:
            return digest(t.__name__, *[fp(x) for x in v])
        if isinstance(v, dict):
            return self.mapping("dict:" + t.__name__, v.items())
        if isinstance(v, (set, frozenset)):
            return self.unordered("set:" + t.__name__, [fp(x) for x in v])
        if isinstance(v, (tuple, list)):
            return digest("seq:%s.%s" % (t.__module__, t.__qualname__), *[fp(x) for x in v])
        if t is re.Pattern:
            return digest("re", v.pattern if isinstance(v, str) else repr(v.pattern), str(v.flags))
        if t is types.MemberDescriptorType or t is types.GetSetDescriptorType:
            return "slot:%s" % v.__name__
        if t is types.MappingProxyType:
            return self.mapping("proxy", v.items())
        parts = []
        d = getattr(v, "__dict__", None)
        if isinstance(d, dict):
            parts.extend(digest(str(k), fp(x)) for k, x in sorted(d.items(), key=lambda kv: str(kv[0])))
        seen = set()
        for klass in t.__mro__:
            names = klass.__dict__.get("__slots__", ())
            for name in (names,) if isinstance(names, str) else names:
                if name not in seen and name not in ("__dict__", "__weakref__"):
                    seen.add(name)
                    try:
                        parts.append(digest(name, fp(getattr(v, name))))
                    except AttributeError:
                        parts.append(digest(name, "<unset>"))
        if parts or isinstance(d, dict):
            return digest("obj:%s.%s" % (t.__module__, t.__qualname__), *parts)
        r = repr(v)
        if " at 0x" in r:
            return "opaque:%s.%s" % (t.__module__, t.__qualname__)
        return digest("repr:%s.%s" % (t.__module__, t.__qualname__), r)

    def slot_fp(self, v):
        """The fingerprint of a slot's value.  A slot that is a plain tuple or list gets an order-independent
        second digest too (a tuple made from a set has no fixed order); nested sequences keep their order."""
        got = self.fp(v)
        if type(v) is tuple or type(v) is list:
            return (got[0], self.hexdigest([type(v).__name__ + "-as-set"] + sorted(self.fp(x)[1] for x in v)))
        return got

    def join(self, items):
        return self.digest(*items) if items else ""

    def mapping(self, tag, items):
        pairs = [(self.fp(k), self.fp(x)) for k, x in items]
        ordered = self.hexdigest([tag] + [a[0] + "=" + b[0] for a, b in pairs])
        unordered = self.hexdigest([tag] + sorted(a[1] + "=" + b[1] for a, b in pairs))
        return (ordered, unordered)

    def unordered(self, tag, fps):
        h = self.hexdigest([tag] + sorted(f[1] for f in fps))
        return (self.hexdigest([tag] + sorted(f[0] for f in fps)), h)


def text(pair):
    """A fingerprint pair as the text stored in a state: the ordered and the order-independent digest."""
    return pair[0] if pair[0] == pair[1] else "%s~%s" % pair


_PAIR = re.compile(r"[0-9a-f]{20}~[0-9a-f]{20}")


def body(value):
    """(kind, ordered, order-independent) of a stored value; the location suffix of function-like slots is not part of it."""
    kind, text_ = value[0], value[2:]
    if kind == "f":
        text_ = text_.split("@", 1)[0]
    if _PAIR.fullmatch(text_):
        return kind, text_[:20], text_[21:]
    return kind, text_, text_


def module_names(tree):
    out = []
    for dp, dn, fn in os.walk(os.path.join(tree, "sqlglot")):
        dn.sort()
        for f in sorted(fn):
            if f.endswith(".py") and f != "__main__.py":
                rel = os.path.relpath(os.path.join(dp, f), tree)[:-3].replace(os.sep, ".")
                out.append(rel[:-9] if rel.endswith(".__init__") else rel)
    return out


def where(snap, v):
    """Function-like slots carry where their code lives, for the line map: file:first-last."""
    f = v
    for _ in range(4):
        if isinstance(f, (classmethod, staticmethod)):
            f = f.__func__
        elif isinstance(f, property):
            f = f.fget
        elif isinstance(f, functools.partial):
            f = f.func
        elif isinstance(f, types.MethodType):
            f = f.__func__
    code = getattr(f, "__code__", None)
    if code is None:
        return ""
    last = max((ln for _, _, ln in code.co_lines() if ln is not None), default=code.co_firstlineno)
    return "@%s:%d-%d" % (code.co_filename, code.co_firstlineno, last)


def snapshot(tree, translate=None):
    """Import every sqlglot module from `tree` (already first on sys.path) -> {slot: "<kind> <fingerprint>"}.
    kind: d data, f function-like, x other.  "E\\t<module>" slots hold import errors."""
    out = {}
    for name in module_names(tree):
        try:
            importlib.import_module(name)
        except BaseException as e:
            out["E\t%s" % name] = "x %s" % type(e).__name__
    snap = Snap(translate)
    base = os.path.join(tree, "")
    for modname in sorted(m for m in sys.modules if m == "sqlglot" or m.startswith("sqlglot.")):
        mod = sys.modules[modname]
        if mod is None:
            continue
        classes = []

        def collect(ns):
            for v in list(ns.values()):
                if isinstance(v, type) and v.__module__ == modname and v not in classes:
                    classes.append(v)
                    collect(vars(v))

        ns = vars(mod)
        collect(ns)
        for k, v in list(ns.items()):
            if k[:2] == "__":
                continue
            if isinstance(v, (type, types.ModuleType)):
                out["M\t%s\t%s" % (modname, k)] = "x " + text(snap.slot_fp(v))
            elif is_data(k, v):
                out["M\t%s\t%s" % (modname, k)] = "d " + text(snap.slot_fp(v))
            else:
                out["M\t%s\t%s" % (modname, k)] = "f %s%s" % (text(snap.slot_fp(v)), where(snap, v).replace(base, ""))
        for cls in classes:
            head = "C\t%s\t%s\t" % (modname, cls.__qualname__)
            struct = [text(snap.fp(cls.__bases__)), "%s.%s" % (type(cls).__module__, type(cls).__qualname__)]
            for k, v in list(vars(cls).items()):
                if k in STRUCTURE:
                    struct.append(k + "=" + text(snap.slot_fp(v)))
                    continue
                if k[:2] == "__" and not callable(v) and not hasattr(type(v), "__get__"):
                    continue   # __doc__, __module__, __firstlineno__, __static_attributes__ ...
                if is_data(k, v):
                    out[head + k] = "d " + text(snap.slot_fp(v))
                elif isinstance(v, type):
                    out[head + k] = "x " + text(snap.slot_fp(v))
                else:
                    out[head + k] = "f %s%s" % (text(snap.slot_fp(v)), where(snap, v).replace(base, ""))
            out[head + "<structure>"] = "x " + text(snap.digest(*struct))
    return out


def compare(pinned, patched):
    """-> {slot: (pinned value or None, patched value or None)} for the slots that differ.
    The location suffix of function-like slots moves with the lines above it: only the digest counts.
    A slot whose dict order varies from one process to the next ("U" entries of the pinned state)
    is compared without regard to that order."""
    changed = {}
    for k in set(pinned) | set(patched):
        if k[:2] in ("U\t", "N\t"):
            continue
        a, b = pinned.get(k), patched.get(k)
        if a is None or b is None:
            changed[k] = (a, b)
            continue
        ka, oa, ua = body(a)
        kb, ob, ub = body(b)
        if ka != kb or ua != ub or (oa != ob and "U\t" + k not in pinned) or "N\t" + k in pinned:
            changed[k] = (a, b)
    return changed


def load(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def compare_main(job_path):
    """Import all of sqlglot from a patched tree and write the slots that differ from the pinned state.
    job: {"root": patched tree, "out": result file, "translate": {...}, "pinned": state file}"""
    with open(job_path) as f:
        job = json.load(f)
    out = {}
    try:
        import unittest  # noqa: F401  what the build-time state had loaded (sqlglot.expressions.UNITTEST)
        root = job["root"]
        os.chdir(root)
        sys.path.insert(0, root)
        snap = snapshot(root, job["translate"])
        out["changed"] = {k: list(v) for k, v in compare(load(job["pinned"]), snap).items()}
    except BaseException as e:  # noqa
        out = {"error": "%s: %s" % (type(e).__name__, e)}
    with open(job["out"], "w") as f:
        json.dump(out, f)


def calibrate(tree, runs=3):
    """Slots whose fingerprint differs from one process to the next on the same tree: -> (order varies, content varies)."""
    import subprocess
    import tempfile

    snaps = []
    with tempfile.TemporaryDirectory() as d:
        for n in range(runs - 1):
            out = os.path.join(d, "s%d.json.gz" % n)
            subprocess.run([sys.executable, "-m", "selector.state", "--raw", tree, out], check=True,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           env=dict(os.environ, PYTHONHASHSEED=os.environ.get("PYTHONHASHSEED", "0")))
            snaps.append(load(out))
    return snaps


def main():
    if sys.argv[1] == "--compare":
        return compare_main(sys.argv[2])
    raw = sys.argv[1] == "--raw"
    args = sys.argv[2:] if raw else sys.argv[1:]
    tree, out = args
    import unittest  # noqa: F401  the comparison has it loaded too (sqlglot.expressions.UNITTEST)
    os.chdir(tree)
    sys.path.insert(0, tree)
    snap = snapshot(tree)
    if not raw:
        order, content = set(), set()
        for other in calibrate(tree):
            for k in set(snap) | set(other):
                a, b = snap.get(k), other.get(k)
                if a is None or b is None:
                    content.add(k)
                    continue
                ka, oa, ua = body(a)
                kb, ob, ub = body(b)
                if ka != kb or ua != ub:
                    content.add(k)
                elif oa != ob:
                    order.add(k)
        for k in order:
            snap["U\t" + k] = "u"
        for k in content:
            snap["N\t" + k] = "n"
        print("state: %d slots, order varies in %d, content varies in %d" % (len(snap), len(order), len(content)),
              file=sys.stderr)
    with gzip.GzipFile(out, "wb", mtime=0) as f:
        f.write(json.dumps(snap, separators=(",", ":"), sort_keys=True).encode())
    print("state: %d slots, %d import errors" % (len(snap), sum(k[0] == "E" for k in snap)), file=sys.stderr)


if __name__ == "__main__":
    main()

"""The import state of sqlglot as *slots* with fingerprints, and the comparison of two such states.

A slot is a global of a sqlglot module ("M", module, name) or an attribute in the body of a sqlglot
class ("C", module, class qualname, attribute).  Its fingerprint is a digest of the value, followed
through containers and objects; classes, modules and functions count by identity (name, and for
functions what the code does, defaults and closure), so that a slot changes only when what it holds
changes.  Importing every module of the patched tree and comparing with the pinned state gives the
slots an import-time edit really changes, derived tables in other modules included.

    python -m selector.state <tree> <out.json.gz>          snapshot of a tree (run when the image is built)
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
    def __init__(self, translate=None, canonical=0):
        self.canonical = canonical   # 1: mappings count without their order, 2: sequences too (see `main`)
        self.memo = {}
        self.keep = []
        self.open = set()
        self.codes = {}
        self.translate = translate or {}   # digest of patched function code -> digest of the pinned code it replaces

    def digest(self, *parts):
        h = hashlib.blake2b(digest_size=10)
        for p in parts:
            h.update(p.encode("utf-8", "backslashreplace"))
            h.update(b"\0")
        return h.hexdigest()

    def code(self, code):
        h = code_hash(code, self.codes)
        return self.translate.get(h, h)

    def fp(self, v):
        t = type(v)
        if v is None or t is bool or t is int or t is float:
            return "%s:%r" % (t.__name__, v)
        if t is str or t is bytes:
            return "%s:%r" % (t.__name__, v) if len(v) < 40 else self.digest(t.__name__, repr(v))
        i = id(v)
        got = self.memo.get(i)
        if got is not None:
            return got
        if i in self.open:
            return "<cycle>"
        self.open.add(i)
        try:
            got = self._fp(v, t)
        finally:
            self.open.discard(i)
        self.memo[i] = got
        self.keep.append(v)
        return got

    def _fp(self, v, t):
        fp = self.fp
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
            return self.digest("func", v.__module__ or "", v.__qualname__, self.code(v.__code__), fp(v.__defaults__),
                               fp(v.__kwdefaults__), ",".join(cells), extra)
        if t is types.MethodType:
            return self.digest("method", fp(v.__func__), fp(v.__self__))
        if t is classmethod or t is staticmethod:
            return self.digest(t.__name__, fp(v.__func__))
        if t is property:
            return self.digest("property", fp(v.fget), fp(v.fset), fp(v.fdel))
        if t is functools.partial:
            return self.digest("partial", fp(v.func), fp(v.args), fp(v.keywords))
        if t is types.BuiltinFunctionType or t is types.MethodDescriptorType or t is types.WrapperDescriptorType:
            return "builtin:%s.%s" % (getattr(v, "__module__", None), getattr(v, "__qualname__", repr(v)))
        if isinstance(v, enum.Enum):
            return self.digest("enum", "%s.%s" % (t.__module__, t.__qualname__), v._name_ or "", fp(v._value_))
        if isinstance(v, (logging.Logger, logging.Handler)):
            return "logging:%s.%s:%s" % (t.__module__, t.__qualname__, getattr(v, "name", ""))   # set up by whoever runs us
        if t is tuple or t is list:
            items = [fp(x) for x in v]
            return self.digest(t.__name__, *(sorted(items) if self.canonical > 1 else items))
        if isinstance(v, dict):
            items = [fp(k) + "=" + fp(x) for k, x in v.items()]
            return self.digest("dict:" + t.__name__, *(sorted(items) if self.canonical else items))
        if isinstance(v, (set, frozenset)):
            return self.digest("set:" + t.__name__, *sorted(fp(x) for x in v))
        if isinstance(v, (tuple, list)):
            return self.digest("seq:%s.%s" % (t.__module__, t.__qualname__), *[fp(x) for x in v])
        if t is re.Pattern:
            return self.digest("re", v.pattern if isinstance(v, str) else repr(v.pattern), str(v.flags))
        if t is types.MemberDescriptorType or t is types.GetSetDescriptorType:
            return "slot:%s" % v.__name__
        if t is types.MappingProxyType:
            items = [fp(k) + "=" + fp(x) for k, x in v.items()]
            return self.digest("proxy", *(sorted(items) if self.canonical else items))
        parts = []
        d = getattr(v, "__dict__", None)
        if isinstance(d, dict):
            parts.extend("%s=%s" % (k, fp(x)) for k, x in sorted(d.items(), key=lambda kv: str(kv[0])))
        seen = set()
        for klass in t.__mro__:
            names = klass.__dict__.get("__slots__", ())
            for name in (names,) if isinstance(names, str) else names:
                if name not in seen and name not in ("__dict__", "__weakref__"):
                    seen.add(name)
                    try:
                        parts.append("%s=%s" % (name, fp(getattr(v, name))))
                    except AttributeError:
                        parts.append("%s=<unset>" % name)
        if parts or isinstance(d, dict):
            return self.digest("obj:%s.%s" % (t.__module__, t.__qualname__), *parts)
        r = repr(v)
        if " at 0x" in r:
            return "opaque:%s.%s" % (t.__module__, t.__qualname__)
        return self.digest("repr:%s.%s" % (t.__module__, t.__qualname__), r)


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


class _Pick:
    """Fingerprints that keep the order of mappings and sequences for most slots, drop the order of
    mappings for the slots in canon[0] and of sequences too for those in canon[1]."""

    def __init__(self, translate, canon):
        canon = canon or ((), ())
        self.snaps = (Snap(translate), Snap(translate, canonical=1), Snap(translate, canonical=2))
        self.plain = self.snaps[0]
        self.level = dict.fromkeys(canon[0], 1)
        self.level.update(dict.fromkeys(canon[1], 2))
        self.key = None

    def fp(self, v):
        return self.snaps[self.level.get(self.key, 0)].fp(v)

    def digest(self, *parts):
        return self.plain.digest(*parts)


def snapshot(tree, translate=None, canon=()):
    """Import every sqlglot module from `tree` (already first on sys.path) -> {slot: "<kind> <fingerprint>"}.
    kind: d data, f function-like, x other.  "E\\t<module>" slots hold import errors.
    canon: (slots whose mappings are compared without their order, slots whose sequences are too)."""
    out = {}
    for name in module_names(tree):
        try:
            importlib.import_module(name)
        except BaseException as e:
            out["E\t%s" % name] = "x %s" % type(e).__name__
    snap = _Pick(translate, canon)
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
            snap.key = "M\t%s\t%s" % (modname, k)
            if isinstance(v, (type, types.ModuleType)):
                out["M\t%s\t%s" % (modname, k)] = "x " + snap.fp(v)
            elif is_data(k, v):
                out["M\t%s\t%s" % (modname, k)] = "d " + snap.fp(v)
            else:
                out["M\t%s\t%s" % (modname, k)] = "f %s%s" % (snap.fp(v), where(snap, v).replace(base, ""))
        for cls in classes:
            head = "C\t%s\t%s\t" % (modname, cls.__qualname__)
            snap.key = head + "<structure>"
            struct = [snap.fp(cls.__bases__), "%s.%s" % (type(cls).__module__, type(cls).__qualname__)]
            for k, v in list(vars(cls).items()):
                snap.key = head + "<structure>" if k in STRUCTURE else head + k
                if k in STRUCTURE:
                    struct.append(k + "=" + snap.fp(v))
                    continue
                if k[:2] == "__" and not callable(v) and not hasattr(type(v), "__get__"):
                    continue   # __doc__, __module__, __firstlineno__, __static_attributes__ ...
                if is_data(k, v):
                    out[head + k] = "d " + snap.fp(v)
                elif isinstance(v, type):
                    out[head + k] = "x " + snap.fp(v)
                else:
                    out[head + k] = "f %s%s" % (snap.fp(v), where(snap, v).replace(base, ""))
            out[head + "<structure>"] = "x " + snap.digest(*struct)
    return out


def compare(pinned, patched, volatile=()):
    """-> {slot: (pinned value or None, patched value or None)} for the slots that differ.
    The location suffix of function-like slots moves with the lines above it: only the digest counts.
    volatile: slots whose value differs from one import of the pinned tree to the next; they say nothing."""
    changed = {}
    for k in (set(pinned) | set(patched)) - set(volatile):
        a, b = pinned.get(k), patched.get(k)
        if a is None or b is None or a.split("@")[0] != b.split("@")[0]:
            changed[k] = (a, b)
    return changed


META = "!meta"


def load(path):
    """-> (slots, canon, volatile) of a stored state."""
    with gzip.open(path, "rt") as f:
        d = json.load(f)
    meta = d.pop(META, {})
    return d, (meta.get("canon", []), meta.get("loose", [])), meta.get("volatile", [])


def _one(tree, out, canon_path=None):
    """One snapshot the way the selector takes it: pytest loaded, sqlglot not yet.  Objects are put
    on the heap first, another number in every run, so that orders which follow memory addresses show."""
    import random

    import pytest  # noqa: F401

    rnd = random.Random(os.urandom(8))
    ballast = [type("B%d" % i, (), {}) for i in range(rnd.randrange(1, 4000))]
    del ballast[::rnd.randrange(2, 7)]
    canon = None
    if canon_path:
        with open(canon_path) as f:
            canon = json.load(f)
    os.chdir(tree)
    sys.path.insert(0, tree)
    with open(out, "w") as f:
        json.dump(snapshot(tree, None, canon), f)


def main():
    """Snapshot the tree several times, each in its own process.

    Some slots hold mappings or sequences filled while iterating over a set of classes: their order
    follows memory addresses and differs from process to process.  Slots whose fingerprint is not the
    same in every run are fingerprinted without the order of mappings (`canon`); those that still
    differ, without the order of sequences either (`loose`); what differs even then (a loop variable
    left in a class body, say) is `volatile` and is left out of every comparison."""
    import subprocess
    import tempfile

    if sys.argv[1] == "--one":
        return _one(*sys.argv[2:])
    tree, out = sys.argv[1], sys.argv[2]
    runs = int(os.environ.get("RWX_STATE_RUNS", "8"))
    work = tempfile.mkdtemp()
    canon_path = os.path.join(work, "canon.json")

    def many(tag, canon):
        with open(canon_path, "w") as f:
            json.dump(canon, f)
        snaps = []
        for i in range(runs):
            p = os.path.join(work, "%s%d.json" % (tag, i))
            subprocess.run([sys.executable, "-m", "selector.state", "--one", tree, p, canon_path],
                           check=True, env=dict(os.environ, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1"))
            with open(p) as f:
                snaps.append(json.load(f))
        keys = set().union(*snaps)
        return snaps, sorted(k for k in keys if len({s.get(k) for s in snaps}) > 1)

    _, canon = many("a", [[], []])
    _, loose = many("b", [canon, []])
    canon = [k for k in canon if k not in loose]
    snaps, volatile = many("c", [canon, loose])
    snap = dict(snaps[0])
    snap[META] = {"canon": canon, "loose": loose, "volatile": volatile}
    with gzip.GzipFile(out, "wb", mtime=0) as f:
        f.write(json.dumps(snap, separators=(",", ":"), sort_keys=True).encode())
    print("state: %d slots, %d import errors; order-free mappings %d, sequences %d %s, volatile %d %s" % (
        len(snap) - 1, sum(k[0] == "E" for k in snap), len(canon), len(loose), loose[:6], len(volatile), volatile[:6]), file=sys.stderr)


if __name__ == "__main__":
    main()

"""The import state of sqlglot as *slots* with fingerprints, and the comparison of two such states.

A slot is a global of a sqlglot module ("M", module, name) or an attribute in the body of a sqlglot
class ("C", module, class qualname, attribute).  Its fingerprint is a digest of the value, followed
through containers and objects; classes, modules and functions count by identity (name, and for
functions what the code does, defaults and closure), so that a slot changes only when what it holds
changes.  Importing every module of the patched tree and comparing with the pinned state gives the
slots an import-time edit really changes, derived tables in other modules included.

What the fingerprints cannot vouch for is marked, never taken as "unchanged": a slot with a component the
fingerprint cannot look into has kind "?" and always counts as changed; the classes that sqlglot builds with
`type(...)` somewhere else than where they are stored (not owned by any module's namespace, e.g. the per-dialect
JSON path tokenizers) are fingerprinted together under the owned class they derive from ("O" entries); two slots that
hold the same mutable object are told apart from two that hold equal copies; an import error always counts.

    python -m selector.state <tree> <out.json.gz>          snapshot of a tree (run when the image is built)
    python -m selector.state --compare <job.json>          slots a patched tree changes (run per patch)
"""
import enum
import functools
import gc
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


_BIG = 1 << 30
_blake2b = hashlib.blake2b


def mutable(v):
    """Objects whose identity matters: two slots holding the same one see each other's later changes."""
    if isinstance(v, (dict, list, set, bytearray)):
        return True
    return (isinstance(getattr(v, "__dict__", None), dict)
            and not isinstance(v, (type, types.ModuleType, types.FunctionType, types.MethodType, types.BuiltinFunctionType)))


def _hexdigest(parts):
    return _blake2b("\0".join(parts).encode("utf-8", "backslashreplace"), digest_size=10).hexdigest()


class Snap:
    """Fingerprints are pairs (ordered, unordered): the second does not depend on the order of dict
    entries.  The order of some dicts depends on the process (they are built from sets of classes)."""

    def __init__(self, translate=None):
        self.memo = {}          # id -> (fingerprint, touched something unsure)
        self.keep = []
        self.open = {}          # id -> depth of the objects being fingerprinted
        self.depth = 0
        self.low = _BIG         # the shallowest open object that a back-reference met (while its subtree is walked)
        self.unsure = False     # the walk met something its fingerprint cannot see into
        self.codes = {}
        self.translate = translate or {}   # digest of patched function code -> digest of the pinned code it replaces
        self.mut = set()        # ids of mutable objects met
        self.owner = {}         # id of a mutable object -> the slot that walked it first
        self.current = None     # the slot being fingerprinted
        self.shared = set()     # slots earlier than the current one that hold some object of it
        self.strays = {}        # id -> class of sqlglot met in a value that no module namespace owns

    hexdigest = staticmethod(_hexdigest)

    def digest(self, *parts):
        """Digest of strings and fingerprint pairs (one hash when no part tells the two apart)."""
        a, b = [], []
        same = True
        for p in parts:
            if p.__class__ is str:
                a.append(p)
                b.append(p)
            else:
                x, y = p
                a.append(x)
                b.append(y)
                if x != y:
                    same = False
        h = _hexdigest(a)
        return (h, h) if same else (h, _hexdigest(b))

    def code(self, code):
        h = code_hash(code, self.codes)
        return self.translate.get(h, h)

    def touch(self, i):
        """A mutable object met again: if another slot walked it first, this slot shares it."""
        o = self.owner.get(i)
        if o is not None and o != self.current and self.current is not None:
            self.shared.add("%s@%d" % (o, self.depth))   # how deep it sits tells the object itself from one inside it

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
            if got[1]:
                self.unsure = True
            if i in self.mut:
                self.touch(i)
            return got[0]
        d = self.open.get(i)
        if d is not None:
            # a back-reference: where it points (levels up) is part of the shape
            self.low = min(self.low, d)
            if i in self.mut:
                self.touch(i)
            s = "<cycle:%d>" % (self.depth - d)
            return (s, s)
        mine = self.depth
        self.open[i] = mine
        self.depth += 1
        if mutable(v):
            self.mut.add(i)
            self.keep.append(v)
            if self.current is not None:
                self.owner.setdefault(i, self.current)
        outer_low, self.low = self.low, _BIG
        outer_unsure, self.unsure = self.unsure, False
        try:
            got = self._fp(v, t)
        finally:
            self.depth = mine
            del self.open[i]
        if isinstance(got, str):
            got = (got, got)
        inner_low, unsure = self.low, self.unsure
        self.unsure = outer_unsure or unsure
        # a result that depends on where the walk entered (a back-reference above this object) is not
        # reusable: another entry point would see another shape
        self.low = outer_low if inner_low >= mine else min(outer_low, inner_low)
        if inner_low >= mine:
            self.memo[i] = (got, unsure)
            self.keep.append(v)
        return got

    def _fp(self, v, t):
        fp = self.fp
        digest = self.digest
        # exact builtin containers: the checks below all fail for them, skip those
        if t is dict:
            return self.mapping("dict:dict", v.items())
        if t is tuple or t is list:
            return digest(t.__name__, *[fp(x) for x in v])
        if t is set or t is frozenset:
            return self.unordered("set:" + t.__name__, [fp(x) for x in v])
        if isinstance(v, type):
            if (v.__module__ or "").startswith("sqlglot"):
                self.strays[id(v)] = v
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
            got = self.mapping("dict:" + t.__name__, v.items())
            return got if t is dict else digest("subdict", self.subclass(v, t), got)
        if isinstance(v, (set, frozenset)):
            got = self.unordered("set:" + t.__name__, [fp(x) for x in v])
            return got if t is set or t is frozenset else digest("subset", self.subclass(v, t), got)
        if isinstance(v, (tuple, list)):
            return digest("seq:%s.%s" % (t.__module__, t.__qualname__), self.subclass(v, t), *[fp(x) for x in v])
        if t is types.CodeType:
            return digest("code", self.code(v))
        if t.__module__ == "_thread" or t.__module__ == "_abc":
            return "builtin-state:%s.%s" % (t.__module__, t.__qualname__)   # locks, ABC caches: no content
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
            self.unsure = True   # its content is invisible to us: whatever it holds, it may have changed
            return "opaque:%s.%s" % (t.__module__, t.__qualname__)
        return digest("repr:%s.%s" % (t.__module__, t.__qualname__), r)

    def subclass(self, v, t):
        """What tells an instance of a subclass of a builtin container from a plain one: its class, the factory of a
        defaultdict, the attributes of the instance."""
        out = [self.fp(t)]
        if hasattr(v, "default_factory"):
            out.append(self.fp(v.default_factory))
        d = getattr(v, "__dict__", None)
        if isinstance(d, dict) and d:
            out.append(self.fp(d))
        return self.digest(*out)

    def orphans(self, classes):
        """One fingerprint, independent of order, for classes that are not slots of any module: bases, metaclass
        and everything the class body and the metaclass put in them."""
        self.current, self.shared = None, set()
        fps = []
        for c in classes:
            items = sorted(((str(k), self.fp(x)) for k, x in vars(c).items()
                            if k not in ("__dict__", "__weakref__") and not k.startswith("_abc_")), key=lambda kv: kv[0])
            fps.append(self.digest(c.__module__, c.__qualname__, self.fp(c.__bases__), self.fp(type(c)),
                                   *[self.digest(k, x) for k, x in items]))
        return self.unordered("orphans", fps)

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
        fp = self.fp
        ordered, unordered = [tag], []
        for k, x in items:
            a, b = fp(k), fp(x)
            e = a[0] + "=" + b[0]
            ordered.append(e)
            unordered.append(e if a[0] == a[1] and b[0] == b[1] else a[1] + "=" + b[1])
        unordered.sort()
        unordered.insert(0, tag)
        return (_hexdigest(ordered), _hexdigest(unordered))

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


def where(v, base):
    """Function-like slots carry where their code lives, for the line map: file:first-last, relative to the tree.
    Code from outside the tree (the standard library, a dependency) has no place the line map knows: empty."""
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
    if code is None or not isinstance(code.co_filename, str) or not code.co_filename.startswith(base):
        return ""
    last = max((ln for _, _, ln in code.co_lines() if ln is not None), default=code.co_firstlineno)
    return "@%s:%d-%d" % (code.co_filename[len(base):], code.co_firstlineno, last)


def orphans(owned, strays):
    """The classes of the import state that no module namespace owns, grouped under the owned classes they derive
    from: {owned class or None: [class, ...]}.  Classes built with `type(...)` and stored on another class
    (`Dialect.jsonpath_tokenizer_class`) are such: their derived attributes are never slots of the snapshot, and
    the slot tracer never watches them."""
    found = {}
    for cls in owned.values():
        seen, todo, got = set(), [cls], []
        while todo:
            try:
                subs = type.__subclasses__(todo.pop())
            except TypeError:
                continue
            for sub in subs:
                if id(sub) not in seen:
                    seen.add(id(sub))
                    todo.append(sub)
                    if id(sub) not in owned:
                        got.append(sub)
        if got:
            found[cls] = got
    under = {id(c) for got in found.values() for c in got}
    loose = [c for i, c in strays.items() if i not in owned and i not in under]
    return found, loose


def snapshot(tree, translate=None):
    """Import every sqlglot module from `tree` (already first on sys.path) -> {slot: "<kind> <fingerprint>"}.
    kind: d data, f function-like, x other, ? a value the fingerprint cannot look into (always changed).
    "E<TAB>module" slots hold import errors, "O<TAB>module<TAB>class" the classes derived dynamically from an owned class."""
    out = {}
    for name in module_names(tree):
        try:
            importlib.import_module(name)
        except BaseException as e:
            out["E\t%s" % name] = "x %s" % type(e).__name__
    snap = Snap(translate)
    base = os.path.join(tree, "")
    mods = []
    owned = {}
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
        mods.append((modname, ns, classes))
        for c in classes:
            owned.setdefault(id(c), c)

    def entry(label, kind, v, loc=""):
        snap.current, snap.shared, snap.unsure = label, set(), False
        got = snap.slot_fp(v)
        if snap.shared:
            got = snap.digest(got, "alias", *sorted(snap.shared))
        return "%s %s%s" % ("?" if snap.unsure else kind, text(got), loc)

    for modname, ns, classes in mods:
        for k, v in list(ns.items()):
            if k[:2] == "__":
                continue
            key = "M\t%s\t%s" % (modname, k)
            if isinstance(v, (type, types.ModuleType)):
                out[key] = entry(key, "x", v)
            elif is_data(k, v):
                out[key] = entry(key, "d", v)
            else:
                out[key] = entry(key, "f", v, where(v, base))
        for cls in classes:
            head = "C\t%s\t%s\t" % (modname, cls.__qualname__)
            snap.current, snap.shared, snap.unsure = head, set(), False
            struct = [text(snap.fp(cls.__bases__)), "%s.%s" % (type(cls).__module__, type(cls).__qualname__)]
            for k, v in list(vars(cls).items()):
                if k in STRUCTURE:
                    struct.append(k + "=" + text(snap.slot_fp(v)))
                    continue
                if k[:2] == "__" and not callable(v) and not hasattr(type(v), "__get__"):
                    continue   # __doc__, __module__, __firstlineno__, __static_attributes__ ...
                if is_data(k, v):
                    out[head + k] = entry(head + k, "d", v)
                elif isinstance(v, type):
                    out[head + k] = entry(head + k, "x", v)
                else:
                    out[head + k] = entry(head + k, "f", v, where(v, base))
            out[head + "<structure>"] = "x " + text(snap.digest(*struct))
    # what no namespace owns.  Classes are cyclic garbage once dropped: only the live ones count, not whichever
    # temporary class this process has not yet collected
    gc.collect()
    found, loose = orphans(owned, snap.strays)
    for cls, got in found.items():
        out["O\t%s\t%s" % (cls.__module__, cls.__qualname__)] = "o " + text(snap.orphans(got))
    if loose:
        out["O\t*\t*"] = "o " + text(snap.orphans(loose))
    return out


def compare(pinned, patched):
    """-> {slot: (pinned value or None, patched value or None)} for the slots that differ.
    The location suffix of function-like slots moves with the lines above it: only the digest counts.
    A slot whose dict order varies from one process to the next ("U" entries of the pinned state)
    is compared without regard to that order.  Equal fingerprints are not proof for what they cannot see into:
    a slot of kind "?" and an import error count as changed even when both states agree."""
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
        if (ka != kb or ua != ub or (oa != ob and "U\t" + k not in pinned) or "N\t" + k in pinned
                or "?" in (ka, kb) or k[0] == "E"):
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

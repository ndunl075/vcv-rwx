"""Slot tracer: which test reads which data attribute of a sqlglot class, or which data global of a
sqlglot module from outside that module's own code, after import.

A slot is what selector/state.py fingerprints: "C\\t<module>\\t<class qualname>\\t<attribute>" for an
entry of a class body, "M\\t<module>\\t<name>" for a module global; "data" is the same test as there.

When a sqlglot module has been executed, every data entry of the classes it defines is replaced by a
descriptor that hands out the very same object and notes the read, and the module gets a class whose
attribute lookup notes reads of its data globals.  Reads made while a sqlglot module is being
imported are not noted: what import-time code computes from a slot is itself part of the import
state, which the selector compares directly.

RWX_SLOT_OUT   directory for the logs (one file per process; a line "<slot id>\\t<test index>")
RWX_SLOT_KEYS  JSON list of all slot keys (ids are positions); absent in the pass that makes the list
RWX_SLOT_TEST  index of the test being traced (for the whole process, or set by the plugin per test)
"""
import enum
import importlib.abc
import json
import os
import sys
import types

STRUCTURE = ("__slots__", "__match_args__", "__annotations__")
OUT = os.environ["RWX_SLOT_OUT"]
CUR = os.environ.get("RWX_SLOT_TEST") or None
SEEN = set()
DEPTH = 0
WRAPPED = []          # every key given a tracer, in order (the key list pass dumps this)
_fd = None
_ids = None
if os.environ.get("RWX_SLOT_KEYS"):
    with open(os.environ["RWX_SLOT_KEYS"]) as _f:
        _ids = {k: i for i, k in enumerate(json.load(_f))}


def is_data(name, value):
    if name[:2] == "__" or name.startswith("_abc_"):
        return False
    if isinstance(value, type):
        return False
    return not hasattr(type(value), "__get__")


def set_test(index):
    global CUR, SEEN
    CUR = index
    SEEN = set()
    if index is None:
        os.environ.pop("RWX_SLOT_TEST", None)
    else:
        os.environ["RWX_SLOT_TEST"] = index


def _note(key):
    global _fd
    SEEN.add(key)
    if _fd is None:
        _fd = os.open(os.path.join(OUT, "%d.%s.log" % (os.getpid(), os.urandom(4).hex())), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    i = _ids.get(key) if _ids is not None else None
    os.write(_fd, ("%s\t%s\n" % (key.replace("\t", "\x1f") if i is None else i, CUR)).encode("utf-8", "backslashreplace"))


class Slot:
    __slots__ = ("v", "key")

    def __init__(self, v, key):
        self.v, self.key = v, key

    def __get__(self, inst, owner=None):
        if CUR is not None and not DEPTH and self.key not in SEEN:
            _note(self.key)
        return self.v


def _module_class(modname, data):
    base_get = types.ModuleType.__getattribute__

    class TracedModule(types.ModuleType):
        def __getattribute__(self, name):
            if name in data and CUR is not None and not DEPTH:
                key = "M\t%s\t%s" % (modname, name)
                if key not in SEEN:
                    _note(key)
            return base_get(self, name)

    return TracedModule


def wrap_module(mod):
    name = mod.__name__
    classes = []

    def collect(ns):
        for v in list(ns.values()):
            if isinstance(v, type) and v.__module__ == name and v not in classes:
                classes.append(v)
                collect(vars(v))

    collect(vars(mod))
    for cls in classes:
        if isinstance(cls, enum.EnumMeta):
            continue
        head = "C\t%s\t%s\t" % (name, cls.__qualname__)
        for k, v in list(vars(cls).items()):
            if k in STRUCTURE or type(v) is Slot or not is_data(k, v):
                continue
            try:
                type.__setattr__(cls, k, Slot(v, head + k))
            except (AttributeError, TypeError):
                continue
            WRAPPED.append(head + k)
    data = set()
    for k, v in list(vars(mod).items()):
        if k[:2] != "__" and not isinstance(v, (type, types.ModuleType)) and is_data(k, v):
            data.add(k)
    if type(mod) is types.ModuleType:
        try:
            mod.__class__ = _module_class(name, frozenset(data))
        except TypeError:
            data = set()
    else:
        data = set()
    WRAPPED.extend("M\t%s\t%s" % (name, k) for k in sorted(data))


class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self.inner = inner

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        global DEPTH
        DEPTH += 1
        try:
            self.inner.exec_module(module)
        finally:
            DEPTH -= 1
        wrap_module(module)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != "sqlglot" and not name.startswith("sqlglot."):
            return None
        for f in sys.meta_path:
            if f is self or not hasattr(f, "find_spec"):
                continue
            spec = f.find_spec(name, path, target)
            if spec is not None:
                if spec.loader is not None and not isinstance(spec.loader, _Loader):
                    spec.loader = _Loader(spec.loader)
                return spec
        return None


sys.meta_path.insert(0, _Finder())

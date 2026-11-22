"""Slot-read tracer, loaded by every Python process of a traced run.  Active only when
RWX_SLOT_DIR is set.

A *slot* is a data attribute in the body of a sqlglot class, or a global of a sqlglot module: the
state that importing sqlglot builds.  The tracer records which slots a test reads after import:

  * every data attribute of a sqlglot class is replaced, once its module is imported, by a non-data
    descriptor that returns the same object and notes the read (instances, subclasses, getattr and
    super() all resolve through it);
  * every sqlglot module gets a module type whose attribute lookup notes the name.

Reads made while a module is being imported are not noted: what import-time code derives from a slot
is itself state, and a changed slot shows up there when two import states are compared.

Appends to $RWX_SLOT_DIR/<pid>.slots:
    C <module> <class qualname> <attribute>     read of a class slot
    M <module> <name>                           read of a module global from outside its own code
    T <id>                                      (whole-suite mode) test <id> starts
"""
import os
import sys


class Traced:
    def __init__(self, fn, nodeid):
        self.fn, self.nodeid = fn, nodeid

    def __call__(self, *args, **kwargs):
        mark = getattr(sys, "rwx_mark", None)
        if mark and self.nodeid:
            mark(self.nodeid)
        return self.fn(*args, **kwargs)


_dir = os.environ.get("RWX_SLOT_DIR")
if _dir:
    import enum
    import importlib.machinery
    import types

    _fd = os.open(os.path.join(_dir, "%d.slots" % os.getpid()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    _write = os.write
    _depth = [0]
    _seen = set()
    _wrapped = []   # (class, name, descriptor)

    class Slot:
        __slots__ = ("key", "value")

        def __init__(self, key, value):
            self.key = key
            self.value = value

        def __get__(self, obj, objtype=None):
            if not _depth[0]:
                key = self.key
                if key not in _seen:
                    _seen.add(key)
                    _write(_fd, key)
            return self.value

    class Module(types.ModuleType):
        def __getattribute__(self, name):
            if not _depth[0] and name[:2] != "__":
                key = (types.ModuleType.__getattribute__(self, "__name__"), name)
                if key not in _seen:
                    _seen.add(key)
                    _write(_fd, ("M\t%s\t%s\n" % key).encode())
            return types.ModuleType.__getattribute__(self, name)

    def is_slot_value(name, value):
        if name[:2] == "__" or name.startswith("_abc_"):
            return False
        if isinstance(value, (type, Slot)):
            return False
        return not hasattr(type(value), "__get__")   # functions, properties, classmethods... are code, not data

    def _classes(ns, modname, out):
        for v in list(ns.values()):
            if isinstance(v, type) and v.__module__ == modname and v not in out:
                out.append(v)
                _classes(vars(v), modname, out)
        return out

    def _instrument(module):
        modname = module.__name__
        for cls in _classes(vars(module), modname, []):
            if isinstance(cls, enum.EnumMeta) or issubclass(cls, tuple):
                continue
            for name, value in list(vars(cls).items()):
                if is_slot_value(name, value):
                    slot = Slot(("C\t%s\t%s\t%s\n" % (modname, cls.__qualname__, name)).encode(), value)
                    try:
                        type.__setattr__(cls, name, slot)
                    except (TypeError, AttributeError):
                        continue
                    _wrapped.append((cls, name, slot))
        try:
            module.__class__ = Module
        except TypeError:
            pass

    _exec = importlib.machinery.SourceFileLoader.exec_module

    def exec_module(self, module):
        _depth[0] += 1
        try:
            return _exec(self, module)
        finally:
            _depth[0] -= 1
            name = module.__name__
            if name == "sqlglot" or name.startswith("sqlglot."):
                _depth[0] += 1
                try:
                    _instrument(module)
                finally:
                    _depth[0] -= 1

    importlib.machinery.SourceFileLoader.exec_module = exec_module

    def _end(*a):
        # a test that put another object into a slot touched it
        _depth[0] += 1
        try:
            for cls, name, slot in _wrapped:
                if cls.__dict__.get(name) is not slot and slot.key not in _seen:
                    _seen.add(slot.key)
                    _write(_fd, slot.key)
        except Exception:
            pass

    import atexit

    atexit.register(_end)
    _os_exit = os._exit

    def _exit(code):
        _end()
        _os_exit(code)

    os._exit = _exit

    def rwx_mark(nodeid):
        _end()
        _seen.clear()
        _write(_fd, ("T\t%s\n" % nodeid).encode("utf-8", "surrogateescape"))
        sys.rwx_current = nodeid

    sys.rwx_mark = rwx_mark
    sys.rwx_slots = _wrapped

"""Candidate tests for one patch: a superset of every test that can fail on it.

plan(patch_bytes, maps, read_old) -> Plan
  Plan.mask         candidate tests (bit mask over the map's test index)
  Plan.everything   True when nothing narrower can be proven (patch does not apply, unknown kind, ...)
  Plan.changes      parsed file changes, Plan.new_files {path: bytes} patched contents (for running)
  Plan.notes        human readable reasons (stderr logging)
Every doubt widens the mask; nothing here narrows on a guess.
"""
import os

from . import astdiff, patch as P

# Files whose change can alter how every test is collected, started or configured.
GLOBAL_NAMES = {
    "conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "sitecustomize.py",
    "usercustomize.py", ".coveragerc", "mypy.ini", "requirements.txt", "tox.toml", "pytest.toml",
    ".pytest.ini", "noxfile.py", "__main__.py_",
}
GLOBAL_SUFFIXES = (".pth",)
SOURCE_PREFIXES = ("sqlglot/", "tests/", "sqlglotc/")


class Plan:
    def __init__(self):
        self.mask = 0
        self.everything = False
        self.changes = []
        self.new_files = {}  # path -> bytes (None = deleted)
        self.notes = []
        self.text_only_shift = []  # (path) files with a comment-only change that moves line numbers

    def note(self, s):
        self.notes.append(s)


def _is_py(path):
    return path.endswith(".py")


def _count_lines(b):
    return b.count(b"\n")


def plan_patch(data, maps, read_old, suspects_mask=0):
    pl = Plan()
    try:
        changes = P.parse(data)
    except P.Unparseable as e:
        pl.everything = True
        pl.note("unparseable patch: %s" % e)
        return pl
    pl.changes = changes
    for c in changes:
        try:
            _plan_change(pl, c, maps, read_old, suspects_mask)
        except P.Unparseable as e:
            pl.everything = True
            pl.note("%s: cannot apply: %s" % (c.path, e))
        except Exception as e:  # any bug in the analysis must not become a miss
            pl.everything = True
            pl.note("%s: analysis error %r" % (c.path, e))
    if pl.everything:
        pl.mask = maps.all_mask
    return pl


def _global(path):
    base = os.path.basename(path)
    return base in GLOBAL_NAMES or path.endswith(GLOBAL_SUFFIXES)


def _plan_change(pl, c, maps, read_old, suspects_mask):
    m = maps
    path = c.path
    old_path, new_path = c.old_path, c.new_path
    for p in (old_path, new_path):
        if p and (p.startswith("/") or ".." in p.split("/") or p.startswith(".git/")):
            pl.everything = True
            pl.note("odd path %s" % p)
            return
        if p and _global(p):
            pl.everything = True
            pl.note("global config file %s" % p)
            return
    if c.kind == "symlink":
        pl.everything = True
        pl.note("symlink %s" % path)
        return
    mask = 0
    # structural: files created, removed, moved
    if c.kind in ("add", "rename", "copy", "delete"):
        for p in (old_path, new_path):
            if not p:
                continue
            d = os.path.dirname(p) or "."
            mask |= m.listers_mask(d)
            mask |= m.readers_mask(p)
            if _is_py(p):
                mask |= m.loaders_mask(p)
                if "/" not in p:
                    # a new top-level module can shadow a library the tests import
                    pl.everything = True
                    pl.note("top-level python file %s" % p)
                    return
        if c.kind == "add" and new_path and _is_py(new_path):
            data = _new_content(c)
            try:
                compile(data, new_path, "exec", dont_inherit=True)
            except (SyntaxError, ValueError, UnicodeError, RecursionError):
                if new_path.startswith("tests/"):
                    pl.everything = True  # collection error in a new test file: unknown reporting
                    pl.note("new test file does not compile %s" % new_path)
                    return
                mask |= m.all_mask if not new_path.startswith(SOURCE_PREFIXES) else 0
        if c.kind == "copy":
            pl.everything = True
            pl.note("copy %s" % path)
            return
    if c.binary:
        for p in (old_path, new_path):
            if p:
                mask |= m.readers_mask(p)
                if _is_py(p):
                    pl.everything = True
                    pl.note("binary python file")
                    return
        pl.mask |= mask
        return
    if c.kind == "mode":
        mask |= m.readers_mask(path)
        pl.mask |= mask
        return
    if c.kind in ("add",):
        pl.new_files[new_path] = _new_content(c)
        pl.mask |= mask
        return
    if c.kind == "delete":
        pl.new_files[old_path] = None
        pl.mask |= mask
        return
    # modify / rename: need the old content
    old = read_old(old_path)
    if old is None:
        raise P.Unparseable("file %s not in the pinned tree" % old_path)
    new = P.apply_hunks(old, c.hunks) if c.hunks else old
    if c.kind == "rename":
        pl.new_files[old_path] = None
    pl.new_files[new_path] = new
    # tests that read the file as data / text
    mask |= m.readers_mask(old_path)
    if _is_py(old_path) or _is_py(new_path):
        is_test = old_path.startswith("tests/")
        if _is_py(old_path) and _is_py(new_path) and c.kind == "modify":
            eff = astdiff.analyze(old, new, old_path, is_test_file=is_test)
            pl.note("%s: %r" % (old_path, eff))
            if eff.import_level:
                mask |= m.loaders_mask(old_path)
            for lo, hi in eff.regions:
                mask |= m.lines_mask(old_path, lo, hi)
            for fl, name in eff.codes:
                mask |= m.code_mask(old_path, fl, name)
            if eff.text_only:
                if _count_lines(old) != _count_lines(new):
                    mask |= suspects_mask & m.loaders_mask(old_path)
            # any change moves lines after it: observable only through tracebacks etc.
            elif _count_lines(old) != _count_lines(new):
                mask |= suspects_mask & m.loaders_mask(old_path)
        else:
            mask |= m.loaders_mask(old_path)
            mask |= m.loaders_mask(new_path) if new_path else 0
    pl.mask |= mask


def _new_content(c):
    out = []
    for h in c.hunks:
        for op, body in h.lines:
            if op in "+":
                out.append(body)
    data = b"\n".join(out)
    if out and not (c.hunks and c.hunks[-1].new_noeol):
        data += b"\n"
    return data

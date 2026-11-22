"""Which lines of the pinned source can behave differently after a patch?

Both versions of a module are compiled and their code objects are compared, parent to child.
Two code objects with the same instructions, names and constants run the same way, whatever their
line numbers.  Where they differ, the instructions are aligned; an old instruction is *marked* when

  * it has no counterpart in the new code,
  * it is a jump, or lies under an exception handler, whose target has no counterpart or another one,
  * it sits right before or after a stretch of instructions that exists in only one version, or
  * it creates a function or class whose signature-level properties changed.

An execution that never runs a marked instruction runs the same instructions with the same operands
in both versions.  The result is the set of old line numbers that hold a marked instruction: a test
can only be affected through this file's code if it executes one of those lines.
"""
import difflib
import dis
import sys
import types

_JUMPS = set(dis.hasjrel) | set(dis.hasjabs)
_PROLOGUE = {"RESUME", "COPY_FREE_VARS", "MAKE_CELL", "RETURN_GENERATOR"}
_BY_REPR = set(dis.hasconst) | set(dis.hasname) | set(dis.haslocal) | set(dis.hasfree) | set(dis.hascompare)


class Broken(Exception):
    """The patched source does not compile."""


def compile_source(source, path):
    try:
        return compile(source, path, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, OverflowError, RecursionError, MemoryError) as e:
        raise Broken("%s: %s" % (type(e).__name__, e))


def _doc(code):
    """The docstring of function code.  Up to Python 3.13 it is the first constant when that is a
    string (the compiler puts None there otherwise); later versions flag it."""
    if not code.co_flags & 0x01 or not code.co_consts:  # module and class bodies assign __doc__ themselves
        return None
    if sys.version_info >= (3, 14):
        return code.co_consts[0] if code.co_flags & 0x4000000 else None
    return code.co_consts[0] if isinstance(code.co_consts[0], str) else None


def _props(code):
    """What the code that creates a function sees of it, and what callers bind to."""
    nargs = code.co_argcount + code.co_kwonlyargcount + bool(code.co_flags & 0x04) + bool(code.co_flags & 0x08)
    return (code.co_name, code.co_qualname, code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_flags, code.co_freevars, code.co_varnames[:nargs], _doc(code))


def _const_key(c):
    if isinstance(c, types.CodeType):
        return ("code",)
    if isinstance(c, (tuple, frozenset)):
        return (type(c).__name__,) + tuple(sorted((_const_key(x) for x in c), key=repr) if isinstance(c, frozenset)
                                             else (_const_key(x) for x in c))
    return (type(c).__name__, repr(c))


def _same_const(o, n, a, b):
    if _const_key(a) == _const_key(b):
        return True
    # a class body stores its own first line number (__firstlineno__): a shifted class is the same class
    return (type(a) is int and type(b) is int and "__firstlineno__" in o.co_names
            and a == o.co_firstlineno and b == n.co_firstlineno)


def _same_body(o, n):
    return (o.co_code == n.co_code and o.co_names == n.co_names and o.co_varnames == n.co_varnames
            and o.co_cellvars == n.co_cellvars and o.co_freevars == n.co_freevars
            and o.co_exceptiontable == n.co_exceptiontable and len(o.co_consts) == len(n.co_consts)
            and all(_same_const(o, n, a, b) for a, b in zip(o.co_consts, n.co_consts)))


def all_lines(code, out):
    """Every line of `code` and of the code nested in it."""
    for _, _, line in code.co_lines():
        if line is not None:
            out.add(line)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            all_lines(c, out)


def _keys(code, instructions):
    keys = [_key(ins) for ins in instructions]
    for i in range(len(instructions) - 1):
        nxt = instructions[i + 1]
        if (nxt.opname == "STORE_NAME" and nxt.argval == "__firstlineno__" and instructions[i].opname == "LOAD_CONST"
                and instructions[i].argval == code.co_firstlineno):
            keys[i] = ("LOAD_CONST", "__firstlineno__")
    return keys


def _matching_blocks(a, b):
    """Like difflib's matching blocks, after setting the common head and tail aside (edits are local)."""
    head = 0
    limit = min(len(a), len(b))
    while head < limit and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < limit - head and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]:
        tail += 1
    blocks = []
    if head:
        blocks.append((0, 0, head))
    ma, mb = a[head:len(a) - tail], b[head:len(b) - tail]
    if ma and mb:
        for i, j, size in difflib.SequenceMatcher(None, ma, mb, autojunk=False).get_matching_blocks():
            if size:
                blocks.append((head + i, head + j, size))
    if tail:
        blocks.append((len(a) - tail, len(b) - tail, tail))
    blocks.append((len(a), len(b), 0))
    return blocks


def _key(ins):
    if ins.opcode in _JUMPS:
        return (ins.opname,)
    if isinstance(ins.argval, types.CodeType):
        return (ins.opname, "code", ins.argval.co_name)
    if ins.opcode in _BY_REPR:
        return (ins.opname, ins.argrepr)
    return (ins.opname, ins.arg)


def _handlers(code, index_of):
    """instruction index -> (handler index, depth, lasti) for the instructions under a handler."""
    out = {}
    for e in dis._parse_exception_table(code):
        tgt = index_of.get(e.target)
        for off in range(e.start, e.end, 2):
            i = index_of.get(off)
            if i is not None:
                out[i] = (tgt, e.depth, e.lasti)
    return out


def _diff(o, n, lines):
    """Add to `lines` the old lines of `o` (and of nested code) that hold marked instructions."""
    if _same_body(o, n):
        for a, b in zip(o.co_consts, n.co_consts):
            if isinstance(a, types.CodeType):
                if _props(a) != _props(b):
                    # cannot tell which instruction creates it without disassembling: do so below
                    break
        else:
            for a, b in zip(o.co_consts, n.co_consts):
                if isinstance(a, types.CodeType):
                    _diff(a, b, lines)
            return
    old = list(dis.get_instructions(o))
    new = list(dis.get_instructions(n))
    oi = {ins.offset: i for i, ins in enumerate(old)}
    ni = {ins.offset: j for j, ins in enumerate(new)}
    match = {}
    marked = set()
    prev_i = prev_j = 0
    for i0, j0, size in _matching_blocks(_keys(o, old), _keys(n, new)):
        if i0 > prev_i or j0 > prev_j:
            # a stretch that exists in one version only: its neighbours lead into or out of it
            marked.update(range(prev_i, i0))
            if prev_i > 0:
                marked.add(prev_i - 1)
            elif old:
                marked.add(-1)  # the stretch is at the entry of the code: every execution meets it
            if i0 < len(old):
                marked.add(i0)
        for k in range(size):
            match[i0 + k] = j0 + k
        prev_i, prev_j = i0 + size, j0 + size
    oh, nh = _handlers(o, oi), _handlers(n, ni)
    for i, j in match.items():
        a, b = old[i], new[j]
        if a.opcode in _JUMPS:
            ti, tj = oi.get(a.argval), ni.get(b.argval)
            if ti is None or match.get(ti) != tj:
                marked.add(i)
        ha, hb = oh.get(i), nh.get(j)
        if (ha is None) != (hb is None) or (ha is not None and (match.get(ha[0]) != hb[0] or ha[1:] != hb[1:])):
            marked.add(i)
        if isinstance(a.argval, types.CodeType):
            if _props(a.argval) != _props(b.argval):
                marked.add(i)
                all_lines(a.argval, lines)
            else:
                _diff(a.argval, b.argval, lines)
    # Entering a function runs its prologue, which reports no line of its own: a marked prologue
    # instruction (or a new stretch right at the entry) stands for "any execution of this code".
    is_func = bool(o.co_flags & 0x01)

    def prologue(i):
        ins = old[i]
        return is_func and (ins.opname in _PROLOGUE or (ins.opname == "POP_TOP" and i > 0 and old[i - 1].opname == "RETURN_GENERATOR"))

    whole = -1 in marked
    for i in marked:
        if i < 0:
            continue
        ins = old[i]
        if ins.line_number is None or prologue(i):
            whole = True
        else:
            lines.add(ins.line_number)
        if isinstance(ins.argval, types.CodeType):
            all_lines(ins.argval, lines)
    if whole:
        for i, ins in enumerate(old):
            if ins.line_number is not None and not prologue(i):
                lines.add(ins.line_number)


def pinned_code(pinned_root, path, source):
    """The code object of a pinned module: from the bytecode made when the image was built, if it
    belongs to exactly this source (size and mtime in its header), else compiled now."""
    import importlib.util
    import marshal
    import os

    full = os.path.join(pinned_root, path)
    try:
        cache = importlib.util.cache_from_source(full)
        st = os.stat(full)
        with open(cache, "rb") as f:
            data = f.read()
        flags = int.from_bytes(data[4:8], "little")
        if (data[:4] == importlib.util.MAGIC_NUMBER and flags == 0
                and int.from_bytes(data[8:12], "little") == int(st.st_mtime) & 0xFFFFFFFF
                and int.from_bytes(data[12:16], "little") == st.st_size & 0xFFFFFFFF == len(source) & 0xFFFFFFFF):
            code = marshal.loads(data[16:])
            if isinstance(code, types.CodeType):
                return code
    except (OSError, ValueError, EOFError, TypeError, NotImplementedError):
        pass
    return compile_source(source, path)


def changed_lines(old_source, new_source, path, old_code=None):
    """Old line numbers a test must execute to be affected.  Raises Broken if the new source does not compile."""
    new = compile_source(new_source, path)
    old = old_code if old_code is not None else compile_source(old_source, path)
    lines = set()
    if _props(old) != _props(new):
        all_lines(old, lines)
        return lines
    _diff(old, new, lines)
    return lines

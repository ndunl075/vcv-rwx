"""Recorder behind the instrumented comparisons (tools/cmpmap_alt/instrument.py).

rec(site, a, b) returns exactly what `a OP b` returns.  While a test is being recorded it also keeps,
per site, the set of operators that gave the very same bool as OP on the same operands at every
execution so far.  Another operator is only tried where trying it cannot change what the program does
afterwards: identity always; equality when an operand is a plain value or both compare by identity;
ordering between plain values.  Anything else counts as "differs".

RWX_CMP_OUT   directory for the logs (one file per process, a line per change: "<site> <mask>\t<test>")
RWX_CMP_TEST  the test being recorded (set for a whole process, or by the plugin around each test;
              worker processes started by a test inherit it)
"""
import enum
import json
import operator
import os

OPS = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"]
_F = [operator.eq, operator.ne, operator.lt, operator.le, operator.gt, operator.ge, operator.is_, operator.is_not,
      lambda a, b: a in b, lambda a, b: a not in b]
EQ, NE, LT, LE, GT, GE, IS, ISNOT, IN, NOTIN = range(10)
# operators worth trying next to each operator (its negation never agrees; membership is never tried)
_TRY = {EQ: (IS, LT, LE, GT, GE), NE: (ISNOT, LT, LE, GT, GE), LT: (LE, NE, GT, GE, EQ), LE: (LT, EQ, NE, GT, GE),
        GT: (GE, NE, LT, LE, EQ), GE: (GT, EQ, NE, LT, LE), IS: (EQ, LT, LE, GT, GE), ISNOT: (NE, LT, LE, GT, GE),
        IN: (), NOTIN: ()}
_PLAIN = (bool, int, float, str, bytes)

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rwx_cmp_sites.json")) as _f:
    _SITES = json.load(_f)
_OP = [OPS.index(op) for _, op in _SITES]
_FN = [_F[i] for i in _OP]
_START = [(1 << o) | sum(1 << j for j in _TRY[o]) for o in _OP]

CUR = os.environ.get("RWX_CMP_TEST") or None
STATE = {}
_fd = None


def set_test(test):
    global CUR, STATE
    CUR = test or None
    STATE = {}
    if test:
        os.environ["RWX_CMP_TEST"] = test
    else:
        os.environ.pop("RWX_CMP_TEST", None)


def _emit(i, mask):
    global _fd
    if _fd is None:
        d = os.environ["RWX_CMP_OUT"]
        _fd = os.open(os.path.join(d, "%d.%s.log" % (os.getpid(), os.urandom(4).hex())), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.write(_fd, ("%d %d\t%s\n" % (i, mask, CUR)).encode("utf-8", "backslashreplace"))


def _plain(x):
    return x is None or type(x) in _PLAIN or isinstance(x, (enum.Enum, type))


def _ordered(x, depth=0):
    t = type(x)
    if t in _PLAIN:
        return True
    return t in (tuple, list) and depth < 2 and len(x) <= 8 and all(_ordered(y, depth + 1) for y in x)


def _agrees(j, a, b, r):
    if j == IS:
        return (a is b) is r
    if j == ISNOT:
        return (a is not b) is r
    if j == EQ or j == NE:
        if not (_plain(a) or _plain(b) or (type(a).__eq__ is object.__eq__ and type(b).__eq__ is object.__eq__)):
            return False
    elif not (_ordered(a) and _ordered(b)):
        return False
    try:
        return _F[j](a, b) is r
    except Exception:
        return False


def rec(i, a, b):
    if CUR is None:
        return _FN[i](a, b)
    o = _OP[i]
    only = 1 << o
    m = STATE.get(i)
    try:
        r = _FN[i](a, b)
    except BaseException:
        if m != only:
            STATE[i] = only
            _emit(i, only)
        raise
    if m == only:
        return r
    if m is None:
        m = _START[i]
        new = True
    else:
        new = False
    if r is True or r is False:
        nm = only
        for j in _TRY[o]:
            if m >> j & 1 and _agrees(j, a, b, r):
                nm |= 1 << j
    else:
        nm = only
    if new or nm != m:
        STATE[i] = nm
        _emit(i, nm)
    return r

#!/usr/bin/env python3
"""Deterministic patch generator against sqlglot at the pinned commit.

  mutate.py --seed 1 --count 300 --out DIR --mix dev|odd|traps [--only kind,kind]

Writes DIR/<id>.patch (git diff --binary -M, the kit's format) and appends
DIR/kinds.json: {id: {"kind":..., "sub":..., "desc":...}}.
Needs python >= 3.9; run with python3.12 for the tokenizer. Reads the pinned
checkout at $RWX_PINNED (default ~/Developer/vcv-data/rwx/sqlglot-pinned) and
generates in a private clone (DIR/.work) that is reset after every patch.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import io
import json
import os
import random
import re
import string
import subprocess
import sys
import tokenize
from pathlib import Path

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
PINNED = Path(os.environ.get("RWX_PINNED", Path.home() / "Developer/vcv-data/rwx/sqlglot-pinned"))

COMMENTS = [
    "Cheap check first", "Intentionally not cached", "Keep in sync with the generator",
    "Kept for backwards compatibility", "TODO: revisit", "See the dialect docs", "noqa",
    "Order matters here", "Handled by the caller", "Fast path", "type: ignore", "pragma: no cover",
    "Mirrors the upstream behaviour", "Do not reorder", "Lazily evaluated",
]


# ----------------------------------------------------------------------------- repo helpers
def git(*args, cwd=None, check=True, text=True, inp=None):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=text, input=inp).stdout


class Universe:
    def __init__(self):
        self.files = git("ls-files", cwd=PINNED).split("\n")
        self.files = [f for f in self.files if f]
        self._cache = {}
        self.src_py = [f for f in self.files if f.startswith("sqlglot/") and f.endswith(".py")]
        self.test_py = [f for f in self.files if f.startswith("tests/") and f.endswith(".py")]
        self.fix_sql = [f for f in self.files if f.startswith("tests/fixtures/") and f.endswith(".sql")]
        self.fix_gz = [f for f in self.files if f.endswith(".gz")]
        self.fix_json = [f for f in self.files if f.startswith("tests/fixtures/") and f.endswith(".json")]
        self.nonpy = [f for f in self.files if f in (
            "pyproject.toml", "setup.cfg", "README.md", "Makefile", "CHANGELOG.md", "MANIFEST.in",
            ".gitignore", "CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", ".pre-commit-config.yaml",
            "sqlglotc/pyproject.toml", "sqlglotc/MANIFEST.in", "sqlglotc/setup.py", "setup.py",
            ".gitpod.yml", "pdoc/templates/module.html.jinja2", "posts/ast_primer.md",
            "posts/onboarding.md", "posts/sql_diff.md", "posts/python_sql_engine.md",
            ".github/workflows/package-test.yml", "tests/fixtures/jsonpath/LICENSE", "LICENSE",
            "sqlglot/py.typed")]

    def read(self, path) -> bytes:
        if path not in self._cache:
            self._cache[path] = (PINNED / path).read_bytes()
        return self._cache[path]

    def text(self, path) -> str:
        return self.read(path).decode("utf-8")


U = None


class Edit:
    def __init__(self, kind, sub, desc):
        self.kind, self.sub, self.desc = kind, sub, desc
        self.files = {}    # path -> bytes | None (delete)
        self.renames = []  # (old, new)
        self.modes = {}    # path -> bool executable

    def merged(self, other):
        for p in other.files:
            if p in self.files:
                return None
        for a, b in other.renames:
            if a in self.files or b in self.files:
                return None
        self.files.update(other.files)
        self.renames += other.renames
        self.modes.update(other.modes)
        self.desc += " + " + other.desc
        return self


class Gen:
    """Applies an Edit to the private clone, takes the diff, resets."""

    def __init__(self, work: Path):
        self.work = work
        if not (work / ".git").exists():
            subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(PINNED), str(work)], check=True)
            git("checkout", "-q", PIN, cwd=work)
        git("config", "core.fileMode", "true", cwd=work)
        git("config", "core.autocrlf", "false", cwd=work)
        git("config", "user.email", "x@x", cwd=work)
        git("config", "user.name", "x", cwd=work)

    def diff(self, e: Edit) -> str:
        w = self.work
        try:
            for a, b in e.renames:
                (w / b).parent.mkdir(parents=True, exist_ok=True)
                git("mv", a, b, cwd=w)
            for p, data in e.files.items():
                fp = w / p
                if data is None:
                    if fp.exists():
                        fp.unlink()
                else:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(data)
            for p, ex in e.modes.items():
                os.chmod(w / p, 0o755 if ex else 0o644)
            git("add", "-A", cwd=w)
            return git("diff", "--cached", "--binary", "-M", "HEAD", cwd=w, text=False).decode("latin-1")
        finally:
            git("reset", "-q", "--hard", "HEAD", cwd=w)
            git("clean", "-fdqx", cwd=w)


# ----------------------------------------------------------------------------- python sites
class Site:
    __slots__ = ("line", "c0", "c1", "new", "op", "depth", "lam", "klass")

    def __init__(self, line, c0, c1, new, op):
        self.line, self.c0, self.c1, self.new, self.op = line, c0, c1, new, op
        self.depth = 0
        self.lam = False
        self.klass = False


SWAP = {
    "==": "!=", "!=": "==", "<": "<=", "<=": "<", ">": ">=", ">=": ">", "and": "or", "or": "and",
    "True": "False", "False": "True", "+": "-", "-": "+", "|": "&", "&": "|", "//": "/",
    "min": "max", "max": "min", "any": "all", "all": "any", "startswith": "endswith",
    "endswith": "startswith", "lstrip": "rstrip", "rstrip": "lstrip", "break": "continue",
    "continue": "break", "in": "not in", "first": "last", "last": "first", "left": "right",
    "right": "left", "append": "extend", "pop": "get", "sorted": "list", "reversed": "list",
}


def ctx_by_line(tree):
    info = {}

    def visit(node, depth, lam, klass):
        for ch in ast.iter_child_nodes(node):
            d, l, k = depth, lam, klass
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                d += 1
                k = False
            elif isinstance(ch, ast.Lambda):
                l = True
            elif isinstance(ch, ast.ClassDef):
                k = True
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                for ln in range(ch.lineno, (ch.end_lineno or ch.lineno) + 1):
                    info[ln] = (d, l, k)
            visit(ch, d, l, k)

    visit(tree, 0, False, False)
    return info


def py_sites(text: str):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    info = ctx_by_line(tree)
    sites = []
    toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    for i, t in enumerate(toks):
        if t.start[0] != t.end[0]:
            continue
        s = t.string
        ln, c0 = t.start
        c1 = t.end[1]
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        site = None
        if t.type in (tokenize.OP, tokenize.NAME):
            if s == "is":
                if nxt and nxt.string == "not" and nxt.start[0] == ln:
                    site = Site(ln, c0, nxt.end[1], "is", "op")
                else:
                    site = Site(ln, c0, c1, "is not", "op")
            elif s == "not":
                if nxt and nxt.start[0] == ln:
                    site = Site(ln, c0, nxt.start[1], "", "op")
            elif s in ("+", "-"):
                if prev and (prev.type in (tokenize.NAME, tokenize.NUMBER, tokenize.STRING) or prev.string in ")]"):
                    if prev.string not in ("return", "in", "and", "or", "not", "if", "else", "lambda"):
                        site = Site(ln, c0, c1, SWAP[s], "op")
            elif s in SWAP:
                if t.type == tokenize.NAME and prev and prev.string == ".":
                    if s in ("first", "last", "left", "right", "append", "pop", "any", "all", "min", "max", "sorted", "reversed"):
                        if s in ("startswith", "endswith", "lstrip", "rstrip"):
                            pass
                    site = Site(ln, c0, c1, SWAP[s], "op") if s in (
                        "startswith", "endswith", "lstrip", "rstrip", "append", "pop") else None
                elif s in ("first", "last", "left", "right", "append", "pop", "sorted", "reversed"):
                    site = None
                else:
                    site = Site(ln, c0, c1, SWAP[s], "op")
        elif t.type == tokenize.NUMBER:
            try:
                if re.fullmatch(r"\d+", s):
                    v = int(s)
                    site = Site(ln, c0, c1, str(v + 1 if v == 0 or random.Random(ln * 7 + c0).random() < 0.5 else v - 1), "num")
                elif re.fullmatch(r"\d*\.\d+", s):
                    site = Site(ln, c0, c1, repr(float(s) + 1.0), "num")
            except ValueError:
                pass
        elif t.type == tokenize.STRING:
            m = re.fullmatch(r"([rRuU]?)(['\"])(.*)\2", s, re.S)
            if m and m.group(3) and len(m.group(3)) <= 80 and "{" not in m.group(3) and "\\" not in m.group(3):
                inner = m.group(3)
                h = (ln * 31 + c0) % 4
                if h == 0:
                    new = inner + "x"
                elif h == 1 and len(inner) > 1:
                    new = inner[:-1]
                elif h == 2:
                    new = inner[0].swapcase() + inner[1:] if inner[0].isalpha() else "x" + inner
                else:
                    mid = len(inner) // 2
                    new = inner[:mid] + "_" + inner[mid:]
                site = Site(ln, c0, c1, m.group(1) + m.group(2) + new + m.group(2), "str")
        if site:
            d, l, k = info.get(ln, (0, False, False))
            site.depth, site.lam, site.klass = d, l, k
            sites.append(site)
    return sites


def apply_site(text: str, site: Site) -> str:
    lines = text.split("\n")
    ln = lines[site.line - 1]
    lines[site.line - 1] = ln[:site.c0] + site.new + ln[site.c1:]
    return "\n".join(lines)


def compiles(text: str) -> bool:
    try:
        compile(text, "<x>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


# ----------------------------------------------------------------------------- ast statement-level mutations
def stmt_sites(text: str):
    """Statement-level mutations: (kind, line_start, line_end, new_lines, depth info)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    info = ctx_by_line(tree)
    lines = text.split("\n")
    out = []
    for node in ast.walk(tree):
        if not hasattr(node, "lineno"):
            continue
        ln = node.lineno
        end = node.end_lineno or ln
        d = info.get(ln, (0, False, False))
        if isinstance(node, (ast.Expr, ast.Assign, ast.AugAssign, ast.Raise)) and ln == end:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            ind = re.match(r"\s*", lines[ln - 1]).group(0)
            if lines[ln - 1].strip().endswith(("\\", ",")):
                continue
            out.append(("stmt_del", ln, ln, [ind + "pass"], d))
        elif isinstance(node, ast.Return) and node.value is not None and ln == end:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                ind = re.match(r"\s*", lines[ln - 1]).group(0)
                out.append(("ret_none", ln, ln, [ind + "return None"], d))
        elif isinstance(node, (ast.If, ast.While)) and node.test.lineno == node.test.end_lineno:
            t = node.test
            line = lines[t.lineno - 1]
            b = line.encode("utf-8")
            seg = b[t.col_offset:t.end_col_offset].decode("utf-8")
            if seg.startswith("not "):
                new = seg[4:]
            else:
                new = f"not ({seg})"
            nl = b[:t.col_offset].decode("utf-8") + new + b[t.end_col_offset:].decode("utf-8")
            out.append(("neg", t.lineno, t.lineno, [nl], d))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if dec.lineno == (dec.end_lineno or dec.lineno):
                    out.append(("decorator_del", dec.lineno, dec.lineno, [], d))
            args = node.args
            allargs = list(args.defaults) + [x for x in args.kw_defaults if x is not None]
            for df in allargs:
                if df.lineno == df.end_lineno and isinstance(df, ast.Constant):
                    line = lines[df.lineno - 1]
                    b = line.encode("utf-8")
                    seg = b[df.col_offset:df.end_col_offset].decode("utf-8")
                    new = {"True": "False", "False": "True", "None": "0", "0": "1", "1": "0"}.get(seg)
                    if new is None:
                        if seg.isdigit():
                            new = str(int(seg) + 1)
                        elif seg[:1] in "'\"":
                            new = seg[:-1] + "x" + seg[-1]
                    if new is not None:
                        nl = b[:df.col_offset].decode("utf-8") + new + b[df.end_col_offset:].decode("utf-8")
                        out.append(("default", df.lineno, df.lineno, [nl], d))
    return out


def container_sites(text: str):
    """Remove one entry of a module/class level dict/set/list/tuple literal (import-time tables)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.split("\n")
    out = []
    bodies = [tree.body]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bodies.append(node.body)
    for body in bodies:
        for st in body:
            val = st.value if isinstance(st, (ast.Assign, ast.AnnAssign)) else None
            if val is None:
                continue
            elems = []
            if isinstance(val, ast.Dict):
                elems = [(k, v) for k, v in zip(val.keys, val.values) if k is not None]
                spans = [(k.lineno, v.end_lineno) for k, v in elems]
            elif isinstance(val, (ast.Set, ast.List, ast.Tuple)):
                spans = [(e.lineno, e.end_lineno) for e in val.elts]
            else:
                continue
            for (a, b) in spans:
                seg = lines[a - 1:b]
                if seg and seg[-1].rstrip().endswith(",") and len(seg) <= 6 and a > st.lineno:
                    out.append(("table_entry_del", a, b, [], (0, False, True)))
    return out


def apply_lines(text: str, a: int, b: int, new: list) -> str:
    lines = text.split("\n")
    lines[a - 1:b] = new
    return "\n".join(lines)


# ----------------------------------------------------------------------------- recipes
class Ctx:
    def __init__(self, rng):
        self.rng = rng


def pick_file(rng, files, need_sites=True, kinds=None):
    for _ in range(40):
        f = rng.choice(files)
        if f.endswith(".py"):
            text = U.text(f)
            if need_sites and not py_sites(text):
                continue
        return f
    return rng.choice(files)


def r_code(rng, files=None, kind="code", sub_filter=None, tests=False):
    """Operator / constant / string / statement mutation in a python file."""
    files = files or U.src_py
    for _ in range(60):
        f = pick_file(rng, files)
        text = U.text(f)
        r = rng.random()
        if r < 0.78 or sub_filter:
            sites = py_sites(text)
            if sub_filter:
                sites = [s for s in sites if sub_filter(s)]
            if not sites:
                continue
            # prefer logic over strings
            w = {"op": 4, "num": 3, "str": 2}
            s = rng.choices(sites, [w[x.op] for x in sites])[0]
            new = apply_site(text, s)
            sub = s.op
            desc = f"{f}:{s.line} {s.op}"
            if s.depth >= 2 or s.lam:
                sub += "_nested"
        else:
            ss = stmt_sites(text)
            if sub_filter:
                ss = [s for s in ss if sub_filter(s)]
            if not ss:
                continue
            k, a, b, nl, d = rng.choice(ss)
            new = apply_lines(text, a, b, nl)
            sub, desc = k, f"{f}:{a} {k}"
        if new == text or not compiles(new):
            continue
        e = Edit(kind, sub, desc)
        e.files[f] = new.encode()
        return e
    return None


def r_importtime(rng):
    for _ in range(60):
        f = pick_file(rng, U.src_py)
        text = U.text(f)
        if rng.random() < 0.5:
            cs = container_sites(text)
            if not cs:
                continue
            k, a, b, nl, d = rng.choice(cs)
            new = apply_lines(text, a, b, nl)
            sub = k
            desc = f"{f}:{a} {k}"
        else:
            sites = [s for s in py_sites(text) if s.depth == 0 and not s.lam and s.klass]
            if not sites:
                continue
            s = rng.choice(sites)
            new = apply_site(text, s)
            sub = "classattr_" + s.op
            desc = f"{f}:{s.line} {sub}"
        if new == text or not compiles(new):
            continue
        e = Edit("importtime", sub, desc)
        e.files[f] = new.encode()
        return e
    return None


def r_stmt(rng, which, kind):
    for _ in range(80):
        f = pick_file(rng, U.src_py, need_sites=False)
        text = U.text(f)
        ss = [s for s in stmt_sites(text) if s[0] in which]
        if not ss:
            continue
        k, a, b, nl, d = rng.choice(ss)
        new = apply_lines(text, a, b, nl)
        if new == text or not compiles(new):
            continue
        e = Edit(kind, k, f"{f}:{a} {k}")
        e.files[f] = new.encode()
        return e
    return None


def logical_line_starts(text):
    """Lines (1-based) where a logical line starts, with their indentation, outside strings."""
    starts = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return [], set(), []
    at_start = True
    in_string_lines = set()
    ends = []
    for t in toks:
        if t.type == tokenize.STRING and t.start[0] != t.end[0]:
            in_string_lines.update(range(t.start[0] + 1, t.end[0] + 1))
        if t.type in (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING):
            continue
        if t.type == tokenize.NEWLINE:
            at_start = True
            ends.append((t.start[0], t.start[1]))
            continue
        if at_start and t.type != tokenize.ENDMARKER:
            starts.append((t.start[0], t.start[1]))
        at_start = False
    return starts, in_string_lines, ends


def r_comment(rng, files=None, kind="comment", sub=None):
    files = files or U.src_py
    for _ in range(40):
        f = rng.choice(files)
        text = U.text(f)
        starts, instr, ends = logical_line_starts(text)
        if not starts:
            continue
        lines = text.split("\n")
        mode = rng.random()
        if mode < 0.7:
            ln, col = rng.choice(starts)
            if ln in instr:
                continue
            ind = " " * col
            c = rng.choice(COMMENTS)
            lines.insert(ln - 1, f"{ind}# {c}")
            s = sub or "line"
        else:
            ln, col = rng.choice(ends)
            lines[ln - 1] = lines[ln - 1][:col] + f"  # {rng.choice(COMMENTS)}" + lines[ln - 1][col:]
            s = sub or "trailing"
        e = Edit(kind, s, f"{f}:{ln} comment")
        e.files[f] = "\n".join(lines).encode()
        return e
    return None


def r_whitespace(rng, files=None):
    files = files or U.src_py
    for _ in range(40):
        f = rng.choice(files)
        text = U.text(f)
        starts, instr, ends = logical_line_starts(text)
        if not starts:
            continue
        lines = text.split("\n")
        m = rng.random()
        if m < 0.4:
            ln, col = rng.choice(ends)
            lines[ln - 1] = lines[ln - 1].rstrip("\n") + "   "
            sub = "trailing_ws"
        elif m < 0.8:
            ln, col = rng.choice(starts)
            if ln in instr or col == 0 and rng.random() < 0.5:
                continue
            lines.insert(ln - 1, "")
            sub = "blank_line"
        else:
            cand = [i for i, l in enumerate(lines) if l == "" and (i + 1) not in instr and i + 1 < len(lines) and lines[i - 1] == ""]
            if not cand:
                continue
            i = rng.choice(cand)
            del lines[i]
            sub = "blank_removed"
        e = Edit("whitespace", sub, f"{f} {sub}")
        e.files[f] = "\n".join(lines).encode()
        return e
    return None


def r_docstring(rng):
    for _ in range(60):
        f = rng.choice(U.src_py)
        text = U.text(f)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        docs = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
                b = n.body[0]
                if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                    docs.append(b)
        if not docs:
            continue
        lines = text.split("\n")
        cand_out = []
        cand_txt = []
        for d in docs:
            for ln in range(d.lineno, d.end_lineno + 1):
                s = lines[ln - 1]
                if re.match(r"\s*>>> ", s):
                    nxt = lines[ln] if ln < len(lines) else ""
                    if nxt.strip() and not nxt.strip().startswith((">>>", "...", '"""')):
                        cand_out.append(ln + 1)
                elif re.search(r"[A-Za-z]{4,}", s) and not s.strip().startswith((">>>", "...")):
                    cand_txt.append(ln)
        if cand_out and rng.random() < 0.6:
            ln = rng.choice(cand_out)
            s = lines[ln - 1]
            s2 = re.sub(r"\w", lambda m: m.group(0), s, count=0)
            m = list(re.finditer(r"[A-Za-z0-9]", s))
            if not m:
                continue
            mm = rng.choice(m)
            s2 = s[:mm.start()] + ("z" if not s[mm.start()].isdigit() else str((int(s[mm.start()]) + 1) % 10)) + s[mm.end():]
            sub = "doctest_output"
        elif cand_txt:
            ln = rng.choice(cand_txt)
            s = lines[ln - 1]
            m = list(re.finditer(r"[a-z]{4,}", s))
            if not m:
                continue
            mm = rng.choice(m)
            s2 = s[:mm.start()] + mm.group(0)[::-1] + s[mm.end():]
            sub = "doc_text"
        else:
            continue
        lines[ln - 1] = s2
        new = "\n".join(lines)
        if not compiles(new):
            continue
        e = Edit("docstring", sub, f"{f}:{ln} {sub}")
        e.files[f] = new.encode()
        return e
    return None


# ---- fixtures
KW_SWAPS = [("JOIN", "LEFT JOIN"), ("AND", "OR"), ("ASC", "DESC"), ("SELECT", "SELECT DISTINCT"),
            ("=", "<>"), ("UNION", "UNION ALL"), ("WHERE", "HAVING"), ("INNER", "LEFT"), ("COUNT", "SUM"),
            ("NOT", ""), ("TRUE", "FALSE"), ("1", "2"), ("a", "b"), ("x", "y")]


def mutate_sql_line(rng, line):
    toks = list(re.finditer(r"\w+|[^\w\s]", line))
    if not toks:
        return None
    t = rng.choice(toks)
    m = rng.random()
    if m < 0.15:
        return line[:t.start()] + t.group(0) + t.group(0) + line[t.end():], "dup"
    if m < 0.3:
        return line[:t.start()] + line[t.end():], "del"
    if m < 0.5 and t.group(0).isalpha():
        k = rng.randrange(len(t.group(0)))
        w = t.group(0)
        return line[:t.start()] + w[:k] + "q" + w[k:] + line[t.end():], "typo"
    if m < 0.65 and t.group(0).isdigit():
        return line[:t.start()] + str(int(t.group(0)) + 1) + line[t.end():], "digit"
    if m < 0.9:
        for a, b in rng.sample(KW_SWAPS, len(KW_SWAPS)):
            mm = re.search(r"\b" + re.escape(a) + r"\b" if a.isalnum() else re.escape(a), line)
            if mm:
                return line[:mm.start()] + b + line[mm.end():], "kw"
    return line[:t.start()] + " " + line[t.start():], "space"


def r_fixture(rng, files=None, kind="fixture"):
    files = files or U.fix_sql
    for _ in range(40):
        f = rng.choice(files)
        lines = U.text(f).split("\n")
        cand = [i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(("#", "--"))]
        if not cand:
            continue
        i = rng.choice(cand)
        r = rng.random()
        if r < 0.1:
            new = lines[:i] + lines[i + 1:]
            sub = "line_del"
        elif r < 0.15 and i + 1 < len(lines):
            new = list(lines)
            new[i], new[i + 1] = new[i + 1], new[i]
            sub = "line_swap"
        else:
            mm = mutate_sql_line(rng, lines[i])
            if not mm:
                continue
            new = list(lines)
            new[i] = mm[0]
            sub = mm[1]
        e = Edit(kind, sub, f"{f}:{i + 1} {sub}")
        e.files[f] = "\n".join(new).encode()
        if e.files[f] == U.read(f):
            continue
        return e
    return None


def r_fixture_comment(rng):
    for _ in range(40):
        f = rng.choice(U.fix_sql)
        lines = U.text(f).split("\n")
        i = rng.randrange(len(lines))
        r = rng.random()
        if r < 0.4:
            lines.insert(i, rng.choice(["-- note", "# note", "# title: renamed", "-- " + rng.choice(COMMENTS)]))
            sub = "comment_line"
        elif r < 0.7:
            cand = [j for j, l in enumerate(lines) if l.startswith("# title:")]
            if not cand:
                continue
            j = rng.choice(cand)
            lines[j] = lines[j] + " (edited)"
            sub = "title_edit"
        elif r < 0.85:
            lines.insert(i, "")
            sub = "blank_line"
        else:
            cand = [j for j, l in enumerate(lines) if l.strip() and not l.startswith(("#", "--"))]
            if not cand:
                continue
            j = rng.choice(cand)
            lines[j] += "  "
            sub = "trailing_ws"
        e = Edit("fixture_comment", sub, f"{f} {sub}")
        e.files[f] = "\n".join(lines).encode()
        return e
    return None


def r_fixture_bin(rng):
    f = rng.choice(U.fix_gz)
    data = gzip.decompress(U.read(f)).decode("utf-8", "replace")
    lines = data.split("\n")
    i = rng.randrange(1, max(2, len(lines) - 1))
    cells = lines[i].split("|") if "|" in lines[i] else lines[i].split(",")
    if not cells or not cells[0]:
        return None
    j = rng.randrange(len(cells))
    cells[j] = cells[j] + "9" if cells[j] else "x"
    sep = "|" if "|" in lines[i] else ","
    lines[i] = sep.join(cells)
    raw = "\n".join(lines).encode()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(raw)
    e = Edit("fixture", "csv_gz", f"{f} row {i}")
    e.files[f] = buf.getvalue()
    return e


def r_fixture_json(rng):
    f = rng.choice(U.fix_json)
    text = U.text(f)
    ms = list(re.finditer(r'"[a-z]{3,}"|\d+', text))
    m = rng.choice(ms)
    rep = m.group(0)[:-1] + 'x"' if m.group(0).startswith('"') else str(int(m.group(0)) + 1)
    e = Edit("fixture", "json", f"{f} {m.start()}")
    e.files[f] = (text[:m.start()] + rep + text[m.end():]).encode()
    return e


# ---- test files / helpers / non-python
def r_testfile(rng):
    files = [f for f in U.test_py if f.startswith("tests/") and "test_" in f]
    e = r_code(rng, files=files, kind="testfile", sub_filter=lambda s: s.op in ("str", "num", "op"))
    return e


def r_helper(rng):
    helpers = ["tests/helpers.py", "tests/gen_fixtures.py", "tests/leakcheck.py", "tests/__init__.py"]
    helpers = [h for h in helpers if h in U.files]
    r = rng.random()
    if r < 0.5:
        return r_code(rng, files=["tests/helpers.py"], kind="helper")
    if r < 0.8:
        return r_comment(rng, files=helpers, kind="helper", sub="comment")
    return r_code(rng, files=helpers, kind="helper")


def r_testcomment(rng):
    files = [f for f in U.test_py if "test_" in f]
    return r_comment(rng, files=files, kind="testfile", sub="comment")


def r_nonpy(rng):
    f = rng.choice(U.nonpy)
    if f == "sqlglot/py.typed":
        e = Edit("nonpy", "py_typed", f)
        e.files[f] = U.read(f) + b"# marker\n"
        return e
    text = U.text(f)
    lines = text.split("\n")
    r = rng.random()
    i = rng.randrange(len(lines))
    if r < 0.4:
        lines.insert(i, "# " + rng.choice(COMMENTS))
        sub = "comment"
    elif r < 0.7:
        cand = [j for j, l in enumerate(lines) if re.search(r"[a-zA-Z]{4,}", l)]
        j = rng.choice(cand)
        m = rng.choice(list(re.finditer(r"[a-z]{4,}", lines[j])) or [None])
        if m is None:
            return None
        lines[j] = lines[j][:m.start()] + m.group(0)[::-1] + lines[j][m.end():]
        sub = "text"
    elif r < 0.85:
        lines.insert(i, "")
        sub = "blank"
    else:
        cand = [j for j, l in enumerate(lines) if re.search(r"\d", l)]
        if not cand:
            return None
        j = rng.choice(cand)
        m = rng.choice(list(re.finditer(r"\d", lines[j])))
        lines[j] = lines[j][:m.start()] + str((int(m.group(0)) + 1) % 10) + lines[j][m.end():]
        sub = "digit"
    e = Edit("nonpy", sub, f"{f} {sub}")
    e.files[f] = "\n".join(lines).encode()
    return e


def r_init(rng):
    inits = [f for f in U.src_py if f.endswith("__init__.py")]
    if rng.random() < 0.3:
        return r_comment(rng, files=inits, kind="init", sub="comment")
    return r_code(rng, files=inits, kind="init")


def r_noreach(rng):
    # code that is probably never executed: error branches and exotic helpers
    for _ in range(60):
        e = r_code(rng, kind="noreach", files=[f for f in U.src_py if any(x in f for x in (
            "dataframe", "executor/python", "planner", "diff", "lineage", "jsonpath", "trie", "time.py",
            "anonymize", "errors", "serde", "schema", "dialects/", "transforms", "executor"))])
        if e:
            return e
    return None


# ---- structural edits
def r_newfile(rng):
    r = rng.random()
    if r < 0.3:
        name = rng.choice(["sqlglot/_extra.py", "sqlglot/dialects/extra.py", "sqlglot/optimizer/extra_rule.py", "sqlglot/util_new.py"])
        body = '"""New helper module."""\n\n\ndef helper(x):\n    return x\n'
        sub = "src_module"
    elif r < 0.55:
        name = "tests/test_new_%s.py" % "".join(rng.choices(string.ascii_lowercase, k=4))
        body = "import unittest\n\n\nclass TestNew(unittest.TestCase):\n    def test_new(self):\n        self.assertEqual(1, 1)\n"
        sub = "test_module"
    elif r < 0.7:
        name = "tests/fixtures/optimizer/extra_%s.sql" % "".join(rng.choices(string.ascii_lowercase, k=3))
        body = "SELECT 1;\nSELECT 1;\n"
        sub = "fixture"
    elif r < 0.85:
        name = rng.choice(["sqlglot/expressions.pyi", "sqlglot/parser.pyi", "sqlglot/helper.pyi"])
        body = "from typing import Any\n\ndef f(*args: Any) -> Any: ...\n"
        sub = "stub"
    else:
        name = rng.choice(["NOTES.md", "docs.txt", "tests/README.md"])
        body = "# Notes\n\nSome notes.\n"
        sub = "doc"
    e = Edit("newfile", sub, name)
    e.files[name] = body.encode()
    return e


def r_delete(rng):
    r = rng.random()
    if r < 0.35:
        f = rng.choice([x for x in U.src_py if not x.endswith("__init__.py")])
        sub = "src"
    elif r < 0.6:
        f = rng.choice(U.test_py)
        sub = "test"
    elif r < 0.85:
        f = rng.choice(U.fix_sql)
        sub = "fixture"
    else:
        f = rng.choice(["README.md", "Makefile", "CHANGELOG.md", "setup.cfg", "MANIFEST.in", "pyproject.toml", "CLAUDE.md"])
        sub = "nonpy"
    e = Edit("deleted", sub, f)
    e.files[f] = None
    return e


def r_rename(rng):
    r = rng.random()
    if r < 0.4:
        f = rng.choice([x for x in U.src_py if not x.endswith("__init__.py")])
        new = f[:-3] + "_old.py"
        sub = "src"
    elif r < 0.6:
        f = rng.choice(U.test_py)
        new = f[:-3] + "_old.py"
        sub = "test"
    elif r < 0.85:
        f = rng.choice(U.fix_sql)
        new = f[:-4] + "_old.sql"
        sub = "fixture"
    else:
        f = rng.choice(["README.md", "Makefile", "CHANGELOG.md"])
        new = f + ".bak"
        sub = "nonpy"
    e = Edit("renamed", sub, f"{f} -> {new}")
    e.renames.append((f, new))
    if rng.random() < 0.4 and f.endswith((".py", ".sql")):
        text = U.text(f)
        e.files[new] = (text + ("# renamed\n" if f.endswith(".py") else "-- renamed\n")).encode()
        e.sub += "_edited"
    return e


def r_move(rng):
    f = rng.choice([x for x in U.src_py if not x.endswith("__init__.py")])
    new = "sqlglot/moved_" + Path(f).name
    e = Edit("renamed", "src_move", f"{f} -> {new}")
    e.renames.append((f, new))
    return e


def r_crlf(rng):
    r = rng.random()
    if r < 0.4:
        f = rng.choice(U.src_py)
        sub = "src"
    elif r < 0.6:
        f = rng.choice(U.test_py)
        sub = "test"
    elif r < 0.9:
        f = rng.choice(U.fix_sql)
        sub = "fixture"
    else:
        f = rng.choice(U.nonpy[:6])
        sub = "nonpy"
    data = U.read(f)
    if b"\r\n" in data or f.endswith(".py") and False:
        return None
    e = Edit("crlf", sub, f)
    e.files[f] = data.replace(b"\n", b"\r\n")
    return e


def r_bom(rng):
    r = rng.random()
    f = rng.choice(U.src_py if r < 0.5 else (U.fix_sql if r < 0.85 else U.test_py))
    data = U.read(f)
    e = Edit("bom", "py" if f.endswith(".py") else "fixture", f)
    e.files[f] = b"\xef\xbb\xbf" + data
    return e


def r_modeonly(rng):
    f = rng.choice(U.src_py + U.test_py + U.fix_sql[:5] + U.nonpy[:4])
    e = Edit("modeonly", "chmod", f)
    e.modes[f] = True
    e.files[f] = U.read(f)
    return e


def r_nul(rng):
    f = rng.choice(U.src_py)
    text = U.text(f)
    starts, instr, ends = logical_line_starts(text)
    if not starts:
        return None
    lines = text.split("\n")
    ln, col = rng.choice(starts)
    if ln in instr:
        return None
    r = rng.random()
    if r < 0.4:
        lines.insert(ln - 1, " " * col + "# note\x00")
        sub = "nul_comment"
        data = "\n".join(lines).encode()
    elif r < 0.8:
        data = ("\n".join(lines[:ln - 1] + [" " * col + "# café"] + lines[ln - 1:])).encode().replace(b"\xc3\xa9", b"\xe9")
        sub = "latin1_comment"
    else:
        lines.insert(ln - 1, " " * col + "# note \xff")
        data = "\n".join(lines).encode("latin-1")
        sub = "invalid_utf8"
    e = Edit("comment_hard", sub, f"{f}:{ln}")
    e.files[f] = data
    return e


def r_cookie(rng):
    f = rng.choice(U.src_py)
    data = U.read(f)
    cookie = rng.choice([b"# -*- coding: latin-1 -*-\n", b"# coding: ascii\n", b"# -*- coding: utf-8 -*-\n",
                         b"# coding: nosuchcodec\n", b"# vim: set fileencoding=utf-8 :\n"])
    e = Edit("comment_hard", "cookie", f"{f} {cookie.decode().strip()}")
    e.files[f] = cookie + data
    return e


def r_syntax(rng, files=None, kind="syntax"):
    files = files or U.src_py
    for _ in range(60):
        f = rng.choice(files)
        text = U.text(f)
        lines = text.split("\n")
        r = rng.random()
        starts, instr, ends = logical_line_starts(text)
        if not starts:
            continue
        ln, col = rng.choice(starts)
        if ln in instr:
            continue
        s = lines[ln - 1]
        if r < 0.25:
            lines[ln - 1] = s.replace(")", "", 1) if ")" in s else s + " )"
            sub = "paren"
        elif r < 0.45:
            lines[ln - 1] = s + " $"
            sub = "stray"
        elif r < 0.6:
            lines[ln - 1] = s.replace(":", "", 1) if s.rstrip().endswith(":") else s + " ="
            sub = "colon"
        elif r < 0.75:
            lines[ln - 1] = (" " * (col + 1)) + s.strip()
            sub = "indent"
        elif r < 0.9:
            lines.insert(ln - 1, " " * col + "print 'x'")
            sub = "py2print"
        else:
            lines[ln - 1] = s + ' "unterminated'
            sub = "string"
        new = "\n".join(lines)
        if compiles(new):
            continue
        e = Edit(kind, sub, f"{f}:{ln} {sub}")
        e.files[f] = new.encode()
        return e
    return None


def r_removed_name(rng, files=None, kind="importbreak"):
    files = files or U.src_py
    for _ in range(80):
        f = rng.choice(files)
        text = U.text(f)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.split("\n")
        tops = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        imps = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.end_lineno == n.lineno]
        r = rng.random()
        if r < 0.45 and tops:
            n = rng.choice(tops)
            sub = "rename_def"
            ln = n.lineno
            s = lines[ln - 1]
            lines[ln - 1] = re.sub(r"\b(def|class) (\w+)", lambda m: f"{m.group(1)} {m.group(2)}_", s, count=1)
        elif r < 0.65 and tops:
            n = rng.choice(tops)
            a = min([d.lineno for d in n.decorator_list] + [n.lineno])
            lines[a - 1:n.end_lineno] = []
            sub = "delete_def"
        elif imps:
            n = rng.choice(imps)
            ln = n.lineno
            names = [a.name for a in n.names]
            if len(names) > 1 and rng.random() < 0.5:
                drop = rng.choice(names)
                lines[ln - 1] = lines[ln - 1].replace(f"{drop}, ", "").replace(f", {drop}", "")
                sub = "drop_import_name"
            else:
                lines[ln - 1] = "pass  # " + lines[ln - 1]
                sub = "drop_import_line"
        else:
            continue
        new = "\n".join(lines)
        if new == text or not compiles(new):
            continue
        e = Edit(kind, sub, f"{f} {sub}")
        e.files[f] = new.encode()
        return e
    return None


def r_shadow(rng):
    name = rng.choice(["json.py", "re.py", "dataclasses.py", "enum.py", "typing.py", "textwrap.py", "decimal.py",
                       "statistics.py", "pytest.py", "numpy.py", "pandas.py", "duckdb.py", "tests/sqlglot.py",
                       "sqlglot/json.py", "sqlglot/time_.py", "sqlglot/dialects/re.py", "sqlglot/optimizer/typing.py",
                       "sqlglot/enum.py", "sqlglot/logging.py"])
    body = rng.choice(["", "raise ImportError('shadowed')\n", "x = 1\n", "# shadow\n"])
    e = Edit("shadow", "newfile_shadow", name)
    e.files[name] = body.encode()
    return e


def r_conftest(rng):
    where = rng.choice(["conftest.py", "tests/conftest.py", "tests/dialects/conftest.py"])
    body = rng.choice([
        "import pytest\n",
        "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _boom():\n    raise RuntimeError('boom')\n",
        "import os\nos.environ['SQLGLOT_X'] = '1'\n",
        "def pytest_collection_modifyitems(config, items):\n    items[:] = items[::-1]\n",
        "import sqlglot\nsqlglot.Dialect.get_or_raise('duckdb')\n",
        "raise SystemExit(1)\n",
    ])
    e = Edit("conftest", "conftest", where)
    e.files[where] = body.encode()
    return e


def r_pytestcfg(rng):
    r = rng.random()
    if r < 0.5:
        f, text = "setup.cfg", U.text("setup.cfg")
        add = rng.choice(["\n[tool:pytest]\naddopts = -x\n", "\n[tool:pytest]\nfilterwarnings = error\n",
                          "\n[tool:pytest]\ntestpaths = tests/dialects\n", "\n[tool:pytest]\ntimeout = 1\n"])
        sub = "setup_cfg"
    elif r < 0.8:
        f, text = "pyproject.toml", U.text("pyproject.toml")
        add = rng.choice(["\n[tool.pytest.ini_options]\naddopts = \"-x\"\n", "\n[tool.pytest.ini_options]\nfilterwarnings = [\"error\"]\n",
                          "\n[tool.pytest.ini_options]\nminversion = \"99\"\n", "\n[tool.pytest.ini_options]\ntimeout = 1\n"])
        sub = "pyproject"
    else:
        f, text = "pytest.ini", ""
        add = rng.choice(["[pytest]\naddopts = -x\n", "[pytest]\nfilterwarnings = error\n", "[pytest]\n"])
        sub = "pytest_ini"
    e = Edit("pytestcfg", sub, f)
    e.files[f] = (text + add).encode()
    return e


def r_slowdown(rng):
    """Insert a sleep in a function so tests get slower, not failing."""
    for _ in range(40):
        f = rng.choice(["sqlglot/parser.py", "sqlglot/tokens.py", "sqlglot/generator.py", "sqlglot/tokenizer_core.py",
                        "sqlglot/optimizer/optimizer.py", "sqlglot/helper.py", "sqlglot/expressions/core.py",
                        "sqlglot/optimizer/scope.py", "sqlglot/schema.py", "sqlglot/dialects/dialect.py"])
        if f not in U.files:
            continue
        text = U.text(f)
        tree = ast.parse(text)
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.body and
               n.body[0].lineno > n.lineno and not (isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant))]
        if not fns:
            continue
        n = rng.choice(fns)
        b = n.body[0]
        lines = text.split("\n")
        ind = " " * b.col_offset
        ms = rng.choice([1, 2, 5, 20])
        lines.insert(b.lineno - 1, f"{ind}__import__('time').sleep({ms / 1000})")
        new = "\n".join(lines)
        if not compiles(new):
            continue
        e = Edit("slowdown", f"sleep_{ms}ms", f"{f}:{n.name}")
        e.files[f] = new.encode()
        return e
    return None


# ----------------------------------------------------------------------------- multi
def r_multi(rng):
    parts = [lambda: r_code(rng), lambda: r_comment(rng), lambda: r_fixture(rng), lambda: r_code(rng),
             lambda: r_docstring(rng), lambda: r_code(rng, files=[f for f in U.src_py if "typing" in f or "dialects" in f])]
    n = rng.choice([2, 2, 3])
    for _ in range(20):
        e = None
        for p in rng.sample(parts, n):
            x = p()
            if x is None:
                e = None
                break
            e = x if e is None else e.merged(x)
            if e is None:
                break
        if e:
            e.kind, e.sub = "multi", f"{n}files"
            return e
    return None


# ----------------------------------------------------------------------------- mixes
RECIPES = {
    "code": r_code, "fixture": r_fixture, "comment": r_comment, "multi": r_multi,
    "nested": lambda rng: r_code(rng, kind="nested", sub_filter=lambda s: getattr(s, "depth", 0) >= 2 or getattr(s, "lam", False)),
    "importtime": r_importtime,
    "decorator": lambda rng: r_stmt(rng, ("decorator_del",), "decorator"),
    "default": lambda rng: r_stmt(rng, ("default",), "default"),
    "errmsg": lambda rng: r_code(rng, kind="errmsg", sub_filter=lambda s: s.op == "str" and s.depth >= 1),
    "whitespace": r_whitespace, "docstring": r_docstring, "testfile": lambda rng: r_testfile(rng) if rng.random() < 0.7 else r_testcomment(rng),
    "helper": r_helper, "nonpy": r_nonpy, "newfile": r_newfile, "deleted": r_delete, "renamed": lambda rng: r_rename(rng) if rng.random() < 0.7 else r_move(rng),
    "syntax": r_syntax, "init": r_init, "noreach": r_noreach, "crlf": r_crlf, "bom": r_bom, "modeonly": r_modeonly,
    "comment_hard": lambda rng: r_nul(rng) if rng.random() < 0.6 else r_cookie(rng),
    "shadow": r_shadow, "conftest": r_conftest, "pytestcfg": r_pytestcfg, "importbreak": r_removed_name,
    "fixture_comment": r_fixture_comment,
    "fixture_bin": lambda rng: r_fixture_bin(rng) if rng.random() < 0.7 else r_fixture_json(rng),
    "slowdown": r_slowdown,
    "stmt": lambda rng: r_stmt(rng, ("stmt_del", "ret_none", "neg"), "code"),
}

MIXES = {
    # like the sample: 53% code, 27% fixture, 10% multi, 10% comment
    "dev": {"code": 53, "fixture": 27, "multi": 10, "comment": 10},
    # only kinds the sample lacks
    "odd": {k: 1 for k in ["nested", "importtime", "decorator", "default", "errmsg", "whitespace", "docstring",
                           "testfile", "helper", "nonpy", "newfile", "deleted", "renamed", "syntax", "init", "noreach",
                           "crlf", "bom", "modeonly", "comment_hard", "shadow", "conftest", "pytestcfg", "importbreak",
                           "fixture_comment", "fixture_bin", "slowdown"]},
}


def make_id(rng, used):
    while True:
        i = "".join(rng.choices(string.ascii_lowercase + string.digits, k=4))
        if i not in used:
            used.add(i)
            return i


def main():
    global U
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mix", default="dev")
    ap.add_argument("--only", default="")
    ap.add_argument("--work", default="")
    a = ap.parse_args()
    U = Universe()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(a.work) if a.work else out.parent / f".gen-work-{out.name}"
    gen = Gen(work)
    rng = random.Random(a.seed)
    if a.mix == "traps":
        import mutate_traps
        mix = mutate_traps.register(sys.modules[__name__])
    else:
        mix = MIXES[a.mix]
    if a.only:
        mix = {k: mix.get(k, 1) for k in a.only.split(",")}
    kinds = json.loads((out / "kinds.json").read_text()) if (out / "kinds.json").exists() else {}
    used = set(kinds)
    names, weights = list(mix), list(mix.values())
    # deterministic schedule: the i-th patch uses rng seeded by (seed, i)
    made = 0
    i = 0
    seen_diffs = set()
    while made < a.count and i < a.count * 30:
        r = random.Random(f"{a.seed}-{i}")
        i += 1
        k = r.choices(names, weights)[0]
        recipe = RECIPES[k]
        try:
            e = recipe(r)
        except Exception as ex:  # a recipe bug should not kill the run
            print(f"recipe {k} failed: {ex!r}", file=sys.stderr)
            continue
        if e is None:
            continue
        d = gen.diff(e)
        if not d.strip() or d in seen_diffs:
            continue
        seen_diffs.add(d)
        pid = make_id(r, used)
        (out / f"{pid}.patch").write_bytes(d.encode("latin-1"))
        kinds[pid] = {"kind": e.kind, "sub": e.sub, "desc": e.desc, "require": getattr(e, "require", None)}
        made += 1
        (out / "kinds.json").write_text(json.dumps(kinds, indent=1, sort_keys=True))
    print(f"made {made} patches in {out}", file=sys.stderr)


if __name__ == "__main__":
    main()

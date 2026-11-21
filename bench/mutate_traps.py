"""Trap recipes for the traps150 set (weak spots listed in the rival digest, section 4).

register(mutate) adds recipes to mutate.RECIPES and returns {recipe: weight}.
Sub-kinds: a_* only slow tests can fail, b_* order/state, c_* lazy tables, d_* core syntax/removed names,
e_* helpers/config, f_* hard comments, g_* odd patches, h_* slowdown.
`require` is checked by build_set after labeling (only_slow / order_dependent / nonempty); candidates the
labeler does not confirm are dropped.
"""
from __future__ import annotations

import ast
import re


def register(m):
    U = lambda: m.U  # noqa: E731

    def tag(e, kind, sub, require=None):
        if e is None:
            return None
        e.kind, e.sub, e.require = kind, sub, require
        return e

    def near(rng, files, regex, window=3, ops=("op", "num", "str")):
        for _ in range(60):
            f = rng.choice(files)
            if f not in U().files:
                continue
            text = U().text(f)
            lines = text.split("\n")
            hot = {i + 1 + d for i, l in enumerate(lines) if re.search(regex, l) for d in range(-window, window + 1)}
            sites = [s for s in m.py_sites(text) if s.line in hot and s.op in ops]
            if not sites:
                continue
            s = rng.choice(sites)
            new = m.apply_site(text, s)
            if new == text or not m.compiles(new):
                continue
            e = m.Edit("trap", s.op, f"{f}:{s.line} near /{regex[:20]}/")
            e.files[f] = new.encode()
            return e
        return None

    def insert_in(rng, f, funcname, code_fn):
        text = U().text(f)
        tree = ast.parse(text)
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == funcname]
        if not fns:
            return None
        n = fns[0]
        body = n.body
        first = body[1] if len(body) > 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) else body[0]
        ind = " " * first.col_offset
        lines = text.split("\n")
        ins = [ind + l for l in code_fn(rng).split("\n")]
        lines[first.lineno - 1:first.lineno - 1] = ins
        new = "\n".join(lines)
        if not m.compiles(new):
            return None
        e = m.Edit("trap", "guard", f"{f}:{funcname}")
        e.files[f] = new.encode()
        return e

    def a_nodes(rng):
        if rng.random() < 0.5:
            e = near(rng, ["sqlglot/parser.py"], r"max_nodes|_node_count", 3)
        else:
            e = insert_in(rng, "sqlglot/parser.py", "_advance",
                          lambda r: f"if self._index > {r.choice([20000, 60000, 150000, 400000])}:\n    self.raise_error('input too large')")
        return tag(e, "trap", "a_slow_nodes", "only_slow")

    def a_tokenizer(rng):
        e = insert_in(rng, "sqlglot/tokens.py", "tokenize",
                      lambda r: f"if len(sql) > {r.choice([50000, 200000, 1000000])}:\n    sql = sql[:{r.choice([40000, 150000, 900000])}]")
        return tag(e, "trap", "a_slow_tokenizer_size", "only_slow")

    def a_memory(rng):
        if rng.random() < 0.6:
            e = insert_in(rng, "sqlglot/dialects/dialect.py", "generator",
                          lambda r: f"globals().setdefault('_g', []).append(bytearray({r.choice([16, 64, 256])}))")
        else:
            e = near(rng, ["sqlglot/generator.py", "sqlglot/dialects/dialect.py"] + [f for f in U().src_py if f.startswith("sqlglot/generators/")],
                     r"SELECT_KINDS|__init__|lru_cache|_cache|ClassVar", 2)
        return tag(e, "trap", "a_slow_memory", "only_slow")

    def a_executor(rng):
        files = [f for f in U().src_py if f.startswith("sqlglot/executor/") or f == "sqlglot/planner.py"]
        e = m.r_code(rng, files=files, kind="trap")
        return tag(e, "trap", "a_slow_executor", "only_slow")

    def a_bigconst(rng):
        for _ in range(60):
            f = rng.choice(U().src_py)
            text = U().text(f)
            sites = [s for s in m.py_sites(text) if s.op == "num" and s.new.isdigit() and abs(int(s.new)) >= 50]
            if not sites:
                continue
            s = rng.choice(sites)
            old = text.split("\n")[s.line - 1][s.c0:s.c1]
            s.new = str(int(old) * rng.choice([2, 10]) if rng.random() < 0.5 else max(1, int(old) // 2))
            new = m.apply_site(text, s)
            if new == text or not m.compiles(new):
                continue
            e = m.Edit("trap", "a_slow_bigconst", f"{f}:{s.line}")
            e.files[f] = new.encode()
            e.require = "only_slow"
            return e
        return None

    CORE_STATE = ["sqlglot/dialects/dialect.py", "sqlglot/helper.py", "sqlglot/schema.py", "sqlglot/parser.py",
                  "sqlglot/tokens.py", "sqlglot/generator.py", "sqlglot/optimizer/scope.py", "sqlglot/expressions/core.py",
                  "sqlglot/optimizer/optimizer.py", "sqlglot/dialects/__init__.py", "sqlglot/__init__.py"]

    def b_state(rng):
        if rng.random() < 0.5:
            e = near(rng, CORE_STATE, r"cache|_registry|classes|lru|_instance|global |setdefault|\bsingleton|_memo", 4)
        else:
            e = m.r_code(rng, files=[f for f in CORE_STATE if f in U().files], kind="trap")
        return tag(e, "trap", "b_order_state", "order_dependent")

    def c_lazy(rng):
        e = m.r_importtime(rng)
        return tag(e, "trap", "c_lazy_table", "nonempty")

    CORE = ["sqlglot/expressions/core.py", "sqlglot/helper.py", "sqlglot/dialects/dialect.py", "sqlglot/optimizer/optimizer.py",
            "sqlglot/optimizer/scope.py", "sqlglot/typing/__init__.py", "sqlglot/expressions/__init__.py",
            "sqlglot/optimizer/qualify.py", "sqlglot/parser.py", "sqlglot/generator.py", "sqlglot/tokens.py", "sqlglot/errors.py"]
    CORE = [f for f in CORE]

    def d_core_syntax(rng):
        f = [x for x in CORE if x in U().files]
        if rng.random() < 0.5:
            return tag(m.r_syntax(rng, files=f, kind="trap"), "trap", "d_core_syntax")
        return tag(m.r_removed_name(rng, files=f, kind="trap"), "trap", "d_core_removed_name")

    def e_helper(rng):
        r = rng.random()
        if r < 0.4:
            e = m.r_helper(rng)
        elif r < 0.7:
            e = m.r_pytestcfg(rng)
        else:
            e = m.r_conftest(rng)
        return tag(e, "trap", "e_" + (e.kind if e else "x"))

    def f_hard_comment(rng):
        r = rng.random()
        if r < 0.75:
            e = m.r_nul(rng) if rng.random() < 0.6 else m.r_cookie(rng)
            return tag(e, "trap", "f_" + (e.sub if e else "x"))
        return tag(m.r_fixture_comment(rng), "trap", "f_fixture_comment")

    def g_odd(rng):
        fn = rng.choice([m.r_crlf, m.r_bom, m.r_modeonly, m.r_shadow, m.r_newfile, m.r_delete, m.r_rename, m.r_move, m.r_crlf, m.r_bom])
        e = fn(rng)
        return tag(e, "trap", "g_" + (e.kind if e else "x"))

    def h_slow(rng):
        return tag(m.r_slowdown(rng), "trap", "h_slowdown")

    m.RECIPES.update({"a_nodes": a_nodes, "a_tokenizer": a_tokenizer, "a_memory": a_memory, "a_executor": a_executor,
                      "a_bigconst": a_bigconst, "b_state": b_state, "c_lazy": c_lazy, "d_core": d_core_syntax,
                      "e_helper": e_helper, "f_hard": f_hard_comment, "g_odd": g_odd, "h_slow": h_slow})
    return {"a_nodes": 20, "a_tokenizer": 12, "a_memory": 25, "a_executor": 40, "a_bigconst": 25, "b_state": 60,
            "c_lazy": 25, "d_core": 22, "e_helper": 18, "f_hard": 18, "g_odd": 30, "h_slow": 12}

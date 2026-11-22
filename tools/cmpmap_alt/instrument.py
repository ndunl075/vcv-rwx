"""Copy a sqlglot checkout and route every single comparison `a OP b` of its sqlglot/ modules through
the recorder (_rwx_cmp.rec), so that a test run can note which other operators would have given the
same result.  The site of a comparison is "path:line:col:endline:endcol" of the *original* source,
the key selector/cmpproof.py computes for a patch.

usage: instrument.py <checkout> <copy>      (writes <copy>/rwx_cmp_sites.json)
"""
import ast
import json
import os
import shutil
import sys

OPS = ["Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"]


class Rewrite(ast.NodeTransformer):
    def __init__(self, path, sites):
        self.path, self.sites = path, sites

    def visit_Compare(self, node):
        site = "%s:%d:%d:%d:%d" % (self.path, node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
        self.generic_visit(node)
        if len(node.ops) != 1:
            return node
        self.sites.append([site, type(node.ops[0]).__name__])
        call = ast.Call(func=ast.Name(id="__rwx_cmp__", ctx=ast.Load()),
                        args=[ast.Constant(len(self.sites) - 1), node.left, node.comparators[0]], keywords=[])
        return ast.copy_location(call, node)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    sites = []
    for d, _, names in sorted(os.walk(os.path.join(src, "sqlglot"))):
        for n in sorted(names):
            if not n.endswith(".py"):
                continue
            full = os.path.join(d, n)
            rel = os.path.relpath(full, src)
            with open(full, encoding="utf-8") as f:
                tree = ast.parse(f.read(), rel)
            before = len(sites)
            tree = Rewrite(rel, sites).visit(tree)
            if len(sites) == before:
                continue
            at = 0
            body = tree.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                at = 1
            while at < len(body) and isinstance(body[at], ast.ImportFrom) and body[at].module == "__future__":
                at += 1
            body.insert(at, ast.ImportFrom(module="_rwx_cmp", names=[ast.alias(name="rec", asname="__rwx_cmp__")], level=0))
            ast.fix_missing_locations(tree)
            with open(os.path.join(dst, rel), "w", encoding="utf-8") as f:
                f.write(ast.unparse(tree) + "\n")
    with open(os.path.join(dst, "rwx_cmp_sites.json"), "w") as f:
        json.dump(sites, f)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_rwx_cmp.py"), os.path.join(dst, "_rwx_cmp.py"))
    print("%d comparison sites" % len(sites))


if __name__ == "__main__":
    main()

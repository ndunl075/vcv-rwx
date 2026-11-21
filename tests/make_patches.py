"""Generate the hand-written adversarial patches in tests/patches/ from the pinned sqlglot checkout.

    python3 tests/make_patches.py <sqlglot checkout at eb11aec>

Each case is one unusual kind of change.  tools/label.py labels them by really running the suite.
"""
import difflib
import os
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", ".work", "sqlglot-src")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patches")


def read(p):
    return open(os.path.join(SRC, p), "rb").read()


def udiff(path, old, new, a=None, b=None):
    a = a or path
    b = b or path
    ol = old.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    nl = new.decode("utf-8", "surrogateescape").splitlines(keepends=True)
    out = ["diff --git a/%s b/%s\n" % (a, b)]
    body = list(difflib.unified_diff(ol, nl, "a/" + a, "b/" + b, n=3))
    for i, l in enumerate(body):
        if not l.endswith("\n"):
            body[i] = l + "\n\\ No newline at end of file\n"
    return "".join(out + body)


def edit(path, old_text, new_text, count=1):
    src = read(path)
    s = src.decode()
    assert old_text in s, (path, old_text)
    return src, s.replace(old_text, new_text, count).encode()


CASES = {}


def case(name):
    def deco(fn):
        CASES[name] = fn
        return fn
    return deco


@case("syntax_core")
def _():
    old, new = edit("sqlglot/helper.py", "    try:\n        return seq[index]", "    try\n        return seq[index]")
    return udiff("sqlglot/helper.py", old, new)


@case("syntax_leaf_dialect")
def _():
    old = read("sqlglot/dialects/doris.py")
    return udiff("sqlglot/dialects/doris.py", old, old + b"\ndef broken(:\n")


@case("syntax_typing_module")
def _():
    old = read("sqlglot/typing/snowflake.py")
    return udiff("sqlglot/typing/snowflake.py", old, old + b"\nx = (\n")


@case("comment_nul")
def _():
    old, new = edit("sqlglot/transforms.py", "from __future__ import annotations\n", "from __future__ import annotations\n# bad \x00 byte\n")
    return udiff("sqlglot/transforms.py", old, new)


@case("comment_invalid_utf8")
def _():
    old = read("sqlglot/transforms.py")
    new = old.replace(b"from __future__ import annotations\n", b"from __future__ import annotations\n# caf\xe9\n", 1)
    return udiff("sqlglot/transforms.py", old, new)


@case("comment_shift_core")
def _():
    old, new = edit("sqlglot/expressions/core.py", "from __future__ import annotations\n", "from __future__ import annotations\n# a harmless comment\n")
    return udiff("sqlglot/expressions/core.py", old, new)


@case("encoding_cookie")
def _():
    old = read("sqlglot/helper.py")
    new = b"# -*- coding: latin-1 -*-\n" + old
    return udiff("sqlglot/helper.py", old, new)


@case("docstring_change")
def _():
    old, new = edit("sqlglot/helper.py", '"""Returns the value in `seq` at position `index`, or `None` if `index` is out of bounds."""', '"""Changed docstring."""')
    return udiff("sqlglot/helper.py", old, new)


@case("class_constant_core")
def _():
    old = read("sqlglot/generator.py").decode()
    import re
    m = re.search(r"\n    LIMIT_FETCH = \"ALL\"", old)
    assert m
    new = old.replace('    LIMIT_FETCH = "ALL"', '    LIMIT_FETCH = "FETCH"', 1)
    return udiff("sqlglot/generator.py", old.encode(), new.encode())


@case("default_arg_change")
def _():
    old, new = edit("sqlglot/helper.py", "def ensure_list(value", "def ensure_list(value", 1)
    s = read("sqlglot/helper.py").decode()
    import re
    m = re.search(r"def (\w+)\([^)]*=\s*(\d+)[^)]*\)", s)
    assert m, "no default"
    seg = m.group(0)
    new = s.replace(seg, seg.replace("= %s" % m.group(2), "= %d" % (int(m.group(2)) + 1), 1), 1)
    return udiff("sqlglot/helper.py", s.encode(), new.encode())


@case("function_start_insert")
def _():
    old, new = edit("sqlglot/helper.py", "    try:\n        return seq[index]", "    if index == 0:\n        return None\n    try:\n        return seq[index]")
    return udiff("sqlglot/helper.py", old, new)


@case("new_method_dynamic_dispatch")
def _():
    old, new = edit("sqlglot/generators/drill.py", "def _str_to_date(", "def _str_to_date2(", 1)
    s = read("sqlglot/generators/drill.py").decode()
    new = s + "\n\nclass _Extra:\n    pass\n"
    return udiff("sqlglot/generators/drill.py", s.encode(), new.encode())


@case("new_file_module")
def _():
    body = b"X = 1\n"
    return "diff --git a/sqlglot/dialects/zzz.py b/sqlglot/dialects/zzz.py\nnew file mode 100644\n--- /dev/null\n+++ b/sqlglot/dialects/zzz.py\n@@ -0,0 +1 @@\n+X = 1\n"


@case("new_file_toplevel_shadow")
def _():
    return "diff --git a/json.py b/json.py\nnew file mode 100644\n--- /dev/null\n+++ b/json.py\n@@ -0,0 +1 @@\n+raise ImportError('shadow')\n"


@case("new_test_file_syntax")
def _():
    return "diff --git a/tests/test_zzz.py b/tests/test_zzz.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_zzz.py\n@@ -0,0 +1,2 @@\n+def test_x(:\n+    pass\n"


@case("new_test_file_ok")
def _():
    return "diff --git a/tests/test_zzz.py b/tests/test_zzz.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_zzz.py\n@@ -0,0 +1,3 @@\n+def test_x():\n+    assert True\n+\n"


@case("delete_dialect_module")
def _():
    old = read("sqlglot/generators/drill.py").decode().splitlines(keepends=True)
    h = "diff --git a/sqlglot/generators/drill.py b/sqlglot/generators/drill.py\ndeleted file mode 100644\n--- a/sqlglot/generators/drill.py\n+++ /dev/null\n@@ -1,%d +0,0 @@\n" % len(old)
    return h + "".join("-" + l for l in old)


@case("rename_module")
def _():
    return "diff --git a/sqlglot/parsers/dremio.py b/sqlglot/parsers/dremio2.py\nsimilarity index 100%\nrename from sqlglot/parsers/dremio.py\nrename to sqlglot/parsers/dremio2.py\n"


@case("mode_only")
def _():
    return "diff --git a/sqlglot/helper.py b/sqlglot/helper.py\nold mode 100644\nnew mode 100755\n"


@case("readme_only")
def _():
    old = read("README.md")
    return udiff("README.md", old, old + b"\nmore docs\n")


@case("pyproject_pytest_config")
def _():
    old = read("pyproject.toml")
    return udiff("pyproject.toml", old, old + b'\n[tool.pytest.ini_options]\naddopts = "-k nothing_matches"\n')


@case("conftest_new")
def _():
    return "diff --git a/conftest.py b/conftest.py\nnew file mode 100644\n--- /dev/null\n+++ b/conftest.py\n@@ -0,0 +1,3 @@\n+import pytest\n+def pytest_collection_modifyitems(items):\n+    items[:] = [i for i in items if 'test_parser' not in i.nodeid]\n"


@case("helpers_break")
def _():
    old, new = edit("tests/helpers.py", "def string_to_bool(string):", "def string_to_bool(string):\n    raise RuntimeError('x')", 1)
    return udiff("tests/helpers.py", old, new)


@case("helpers_comment")
def _():
    old, new = edit("tests/helpers.py", "import os\n", "import os\n# comment\n", 1)
    return udiff("tests/helpers.py", old, new)


@case("test_assertion_change")
def _():
    old, new = edit("tests/test_helper.py", "assertEqual", "assertNotEqual", 1)
    return udiff("tests/test_helper.py", old, new)


@case("fixture_new_line")
def _():
    old = read("tests/fixtures/identity.sql")
    return udiff("tests/fixtures/identity.sql", old, old + b"SELECT 1 +\n")


@case("fixture_dir_new_file")
def _():
    return "diff --git a/tests/fixtures/optimizer/zzz.sql b/tests/fixtures/optimizer/zzz.sql\nnew file mode 100644\n--- /dev/null\n+++ b/tests/fixtures/optimizer/zzz.sql\n@@ -0,0 +1,2 @@\n+SELECT 1;\n+SELECT 2;\n"


@case("fixture_delete")
def _():
    old = read("tests/fixtures/optimizer/simplify.sql").decode().splitlines(keepends=True)
    return "diff --git a/tests/fixtures/optimizer/isolate_table_selects.sql b/tests/fixtures/optimizer/isolate_table_selects.sql\ndeleted file mode 100644\n--- a/tests/fixtures/optimizer/isolate_table_selects.sql\n+++ /dev/null\n@@ -1,%d +0,0 @@\n" % len(read("tests/fixtures/optimizer/isolate_table_selects.sql").decode().splitlines()) + "".join("-" + l if l.endswith("\n") else "-" + l + "\n" for l in read("tests/fixtures/optimizer/isolate_table_selects.sql").decode().splitlines(keepends=True))


@case("not_applying")
def _():
    old, new = edit("sqlglot/helper.py", "    try:\n        return seq[index]", "    try:\n        return seq[index+1]")
    p = udiff("sqlglot/helper.py", old, new)
    return p.replace(" return seq[index]\n", " return seq[index] # drifted\n", 1)


@case("indent_only")
def _():
    old, new = edit("sqlglot/helper.py", "        return seq[index]\n    except IndexError:\n        return None", "        return seq[index]\n    except IndexError:\n     return None")
    return udiff("sqlglot/helper.py", old, new)


@case("whitespace_only")
def _():
    old, new = edit("sqlglot/helper.py", "        return seq[index]\n", "        return seq[index]   \n")
    return udiff("sqlglot/helper.py", old, new)


@case("remove_import")
def _():
    old, new = edit("sqlglot/helper.py", "from itertools import count\n", "", 1)
    return udiff("sqlglot/helper.py", old, new)


@case("init_change")
def _():
    old = read("sqlglot/dialects/__init__.py")
    return udiff("sqlglot/dialects/__init__.py", old, old + b"\nimport sqlglot.dialects.doris\n")


@case("typing_checking_block")
def _():
    old, new = edit("sqlglot/__init__.py", "if t.TYPE_CHECKING:\n", "if t.TYPE_CHECKING:\n    import os\n", 1)
    return udiff("sqlglot/__init__.py", old, new)


@case("slow_patch")
def _():
    old, new = edit("sqlglot/helper.py", "    try:\n        return seq[index]", "    import time\n    time.sleep(0.01)\n    try:\n        return seq[index]")
    return udiff("sqlglot/helper.py", old, new)


@case("multi_mixed")
def _():
    a = read("sqlglot/helper.py")
    a2 = a.replace(b"from itertools import count\n", b"from itertools import count\n# c\n", 1)
    b = read("tests/fixtures/identity.sql")
    return udiff("sqlglot/helper.py", a, a2) + udiff("tests/fixtures/identity.sql", b, b + b"SELECT 1 +\n")


@case("crlf_line")
def _():
    old = read("tests/fixtures/pretty.sql")
    new = old.replace(b"\n", b"\r\n", 1)
    return udiff("tests/fixtures/pretty.sql", old, new)


@case("binary_fixture")
def _():
    return "diff --git a/tests/fixtures/optimizer/tpc-h/lineitem.csv.gz b/tests/fixtures/optimizer/tpc-h/lineitem.csv.gz\nindex 1234567..89abcde 100644\nBinary files a/tests/fixtures/optimizer/tpc-h/lineitem.csv.gz and b/tests/fixtures/optimizer/tpc-h/lineitem.csv.gz differ\n"


@case("sqlglotc_setup")
def _():
    old = read("sqlglotc/setup.py")
    return udiff("sqlglotc/setup.py", old, old + b"\n# c\n")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn in CASES.items():
        with open(os.path.join(OUT, name + ".patch"), "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(fn())
    print(len(CASES), "patches in", OUT)

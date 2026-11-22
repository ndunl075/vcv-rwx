"""Helpers for the slot tests: a tiny stand-in for sqlglot, written to a temporary directory, and a way to
run code against it in a fresh interpreter (the modules are named `sqlglot`, so they cannot share a process)."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_tree(files):
    """{relative path: source} -> path of a new tree."""
    root = tempfile.mkdtemp(prefix="slots-")
    for rel, src in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(textwrap.dedent(src))
    return root


def run(code, tree, env=None, pythonpath=()):
    """Run `code` with `tree` as the working directory and first on the path; -> stdout (JSON lines are the caller's)."""
    e = dict(os.environ, PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1")
    e["PYTHONPATH"] = os.pathsep.join([tree, *pythonpath, ROOT])
    e.update(env or {})
    p = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=tree, env=e, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout


BASE = {
    "sqlglot/__init__.py": "",
    "sqlglot/core.py": """
        import enum

        LEVEL = 3
        TABLE = {"a": 1, "b": (1, 2)}
        ORDERED = {"x": 1, "y": 2}


        class Meta(type):
            def __new__(mcs, name, bases, ns):
                k = super().__new__(mcs, name, bases, ns)
                k.DERIVED = tuple(sorted(k.ARGS))
                return k


        class Base(metaclass=Meta):
            ARGS = {"this": True, "expression": False}
            NAME = "base"
            LIMIT = 10

            def run(self):
                return self.LIMIT + LEVEL

            def args(self):
                return self.ARGS


        class Child(Base):
            NAME = "child"

            class Inner:
                FLAG = False


        class Color(enum.Enum):
            RED = 1
    """,
    "sqlglot/other.py": """
        from sqlglot import core

        COPY = core.LEVEL
        DOUBLE = core.LEVEL * 2


        def fn(x=core.LEVEL):
            return x + 1
    """,
}


def snapshot_code(patched_files=None):
    """Code that snapshots the tree in the working directory and prints it as JSON."""
    return """
        import json, os, sys
        from selector import state
        sys.path.insert(0, os.getcwd())
        print(json.dumps(state.snapshot(os.getcwd())))
    """


def snap(tree):
    return json.loads(run(snapshot_code(), tree))

# State of the selector (branch `core`)

## Design (stage A)

Per patch, `selector/main.py`:

1. **Parse and apply strictly** (`selector/patch.py`, our own unified-diff reader: the image has no git).
   Anything that is not "existing file modified, every hunk applies exactly" selects every test:
   new, deleted, renamed, copied files, mode changes, binary patches, symlinks, odd paths, a hunk that does not apply.
2. **Candidates** from the file map (`maps/files.json.gz`): the tests whose process tree opens the changed file
   when the test runs alone in a fresh pytest process. Importing a module opens its source or bytecode, so this is
   "every test whose process imports the module" for code and "every test that reads it" for fixtures.
   Only `sqlglot/**/*.py`, `tests/**/test_*.py` and `tests/fixtures/**` are classified; any other path
   (helpers, `__init__`, config, docs), a NUL byte or invalid UTF-8 in a Python file selects every test.
3. **Run or list.** A candidate is either listed unrun or run with real pytest (fresh process, patched tree,
   `SKIP_INTEGRATION=0`, the labeling environment's packages). The answer is the candidates that did not get a
   complete clean verdict plus every candidate that was not run. Tests over 2.5 s, and tests whose own process costs
   much more CPU than their recorded time (spawn pools, class set-up), are never run.
4. **Pack** in whole milliseconds over 1–8 workers; idle time on paid-for workers is filled with other tests (free).

Time: no verification starts after limit − 70 s, running ones are killed at limit − 45 s, and a guard thread
answers every open patch with its unrun candidates and exits at limit − 37 s (limit 180 s, 270 s above 50 patches).

## The file map

`tools/filemap/trace.py` runs inside the labeling image (pinned base digest, `requirements.lock`, sqlglot at the pin):
each test alone, cold, with an inotify watch on a private copy of the checkout, so opens by native code (DuckDB
reading CSVs) and by spawned pool workers are recorded too. `tools/filemap/pack.py` writes the map.
A test whose trace failed or overflowed is `unknown` and is a candidate of every patch.

## Import-time edits: the slot map (`maps/slots.json.gz`, `maps/state.json.gz`)

Before: an edit of module- or class-level code selected every test that imports the file (`ktz3`: 1617 tests, 0 fail).
Now, when the only import-time change is the *value* of an assignment (`codediff.values_only`: every other statement
of module and class bodies is identical, no dunder target, no attribute or item target, function headers and
docstrings identical), the candidates are the readers of the slots the edit changes.

- A **slot** is a data attribute in the body of a sqlglot class (`C module Class.Nested attr`) or a global of a sqlglot
  module (`M module name`).  `selector/state.py` imports every module of a tree in a fresh process and fingerprints every
  slot (containers followed through; classes, modules by name; functions by what the code does, defaults and closure;
  loggers by name).  `maps/state.json.gz` is the pinned tree's state, built by the Dockerfile from the image's own tree
  (`python -m selector.state`, three processes: slots whose dict order differs between processes get a `U` entry and are
  then compared without regard to order (47 today); content that differs gets an `N` entry and always counts as changed (none)).  It is not committed.  Per patch, `python -m selector.state --compare` imports the
  patched tree (`verify.build_tree`) and lists the slots that differ, derived ones included (a class attribute
  copied into a table at import, metaclass-built tries and tables, a default argument that took the value).
- `maps/slots.json.gz` (`tools/run_slotmap.sh`, 1620 cold single-test runs and one whole-suite session in the labeling
  image, tracer `tools/slotmap/sitecustomize.py`): for every slot, the tests that read it *after import* (class data
  attributes are replaced by a non-data descriptor that notes the read; sqlglot modules get a module type that notes
  attribute reads from other code).  Reads while a sqlglot module is being imported are not recorded (that is derived state,
  the comparison sees it); reads while a test module or conftest is imported are.  Readers = union of the cold runs and
  the session; readers seen only inside the session are selected but never run (a run alone proves nothing).
  A test that failed under the tracer, or has no trace, reads everything (none: 1620 of 1620 passed).
- Module globals: readers are the tests that run a line of the module's own functions that names the global (AST scan,
  line map) plus the outside readers the tracer saw.  A module that uses `globals()`, `vars`, `locals`, `eval`, `exec`
  or `__dict__` has no such scan: no narrowing.

**No narrowing, every importer as before:** any other import-time change (docstring, import, decorator, base class,
call statement, condition, added or deleted statement, dunder, attribute or item assignment); `__main__.py`; a slot that
appears or disappears; a slot whose value is a class, enum, module or other "x" kind, or whose kind changed; a class
change (`<structure>`: bases, metaclass, `__slots__`); a slot the tracer never saw (enums, tuple subclasses, anything
added after import); a function-like slot of unknown place; a module the tracer did not watch; an import error; more
than 400 changed slots; a comparison that fails or times out; no private copy of the pinned tree.  The comparison is also
skipped when settling every importer already costs less than `SLOT_COMPARE_S` (2 s).

Since the comparison runs before the servers start, the hot server is still used for the readers (modules the collection
did not import).

### Proven (slot map)

- Kit sample (30 patches, amd64 gate): 172.7 simulated against 226.0 at `99dc0ff`, 0 misses, no patch dearer;
  `ktz3` 36.8 -> 2.1 s (3 readers of 1617 importers), `535z` 13.0 -> 2.7 s, `4d63` 15.0 -> 9.1 s (273 readers).
- dev300 labels (71 patches at the time): 18 patches narrowed by the comparison; every failing test of every one is
  among the readers of the changed slots (`checkreaders`, not committed), 6jez 34 of 34.  The only failing test outside
  is `test_parser.py::test_max_nodes` (28.8 s), which the labeler failed on 7 of the newest patches whatever they
  edited (load-induced); `99dc0ff` misses it the same way.
- Synthetic check: 6 single-slot edits (bool constants of generators and dialects, 1 to 5 readers), each labeled with the
  whole suite in one session: the failing tests are exactly the readers (readers 1,1,1,1,2,5; failing 1,1,1,1,1,2).
- Fresh runs of an unpatched tree give an empty change list (with and without pytest, numpy, pandas, duckdb loaded).
- Bug found by comparing the two traces: the first session trace lost every read after the first test (the mark left
  the tracer's depth counter raised); fixed, test added, map rebuilt.  Session reads add few readers beyond the cold
  runs (1432 test-slot pairs of 343704, 1312 of them one slot a test replaced and left replaced).

### Not proven (slot map)

- Order effects: a test that fails because an earlier test ran under the patch (shared registries) and never reads the
  changed slot itself is not a reader.  Same gap as for the line map.
- Reads the tracer cannot see: class attributes read through `vars(cls)` / `cls.__dict__` after import (sqlglot does this
  only in metaclasses at import), module globals read through `globals()` from another module's dict, reads by native code.
  A test that went through such a path would be missed.  Whole-slot granularity only: a changed key of a dict slot selects
  every reader of the dict.
- A slot a test replaces (monkeypatch) is untraced afterwards: every later test of the session counts as a reader.
- Import-time side effects outside sqlglot's state (logging, warnings, environment) are not compared.  Assignment values
  with calls that have such effects are not excluded.
- CPU: one comparison is about 1.3 s native (imports every module, fingerprints 17.7k slots); the sample's CPU went from
  13.3 to 17.7 s native and 32.8 to 38.4 s amd64 (emulated, machine heavily loaded: noisy).  Under heavy load the
  comparisons of many patches can run into the time guard, then those patches select every importer.
- `maps/state.json.gz` is built by the Dockerfile (and `tools/Dockerfile.native`), not committed; it depends on the
  interpreter that builds it.

## Proven

- Verification runs reproduce the kit's failing sets on the sample patches tried (native arm64 and emulated amd64).

## Not proven / known gaps

- Order and state effects: the labels come from one whole-suite session; the map comes from cold single-test
  processes and verification runs only the candidates. A test that fails only because an earlier test ran under
  the patch (shared registries, caches) is outside the candidate set.
- A failing teardown is charged to the last test of its class and file (those are added), not proven on labels.
- The labeler's per-test timeout is unknown: a test that got much slower is treated as unverified.

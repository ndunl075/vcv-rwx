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
  then compared without regard to order, content that differs gets an `N` entry and always counts as changed; none today
  besides 47 order-unstable ones).  It is not committed.  Per patch, `python -m selector.state --compare` imports the
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
than 400 changed slots; a comparison that fails or times out; no private copy of the pinned tree.
Added after review (each one a place where "the fingerprints agree" is not proof):

- **No slot differs** (`resolve_slots`): never narrows.  An edit that passed `values_only` and changed the compiled
  import-level code but no fingerprint is exactly what a blind spot looks like; every importer stays a candidate.
  No zero-change case is kept as provably safe (an edit with equal code objects never reaches the comparison: `d.import_level` is false).
- **Dynamic classes**: classes built with `type(...)` in one module and stored on a class of another (the per-dialect
  `JSONPathTokenizer`, Druid's `Tokenizer`) belong to no namespace, so neither the snapshot nor the tracer sees their derived
  attributes.  The snapshot fingerprints them in `O` entries, one per owned class they derive from (and `O * *` for
  classes met in values that derive from nothing owned); a changed `O` entry falls back.  Only live classes count
  (`gc.collect()` first: a temporary class is cyclic garbage and its presence depended on the collector).
- **Builtin container subclasses** are fingerprinted with their class, `default_factory` and instance attributes
  (`defaultdict(set, ..)` vs `defaultdict(list, ..)`).
- **Cycles**: a back-reference is `<cycle:k>` (levels up), and a result that depends on where the walk entered is not memoised.
- **Code from outside the tree** (`X = textwrap.dedent`) has no place the line map knows; `where` gives none, and a place the
  line map has no file for gives `None`: both fall back instead of "no reader".
- **Import errors** (`E` entries) always count as changed, also when the module fails in both trees.
- **Opaque values** (an object whose repr is an address) make the slot kind `?`: always changed, falls back.  Known
  native state is not opaque: code objects (`ForwardRef`), locks, ABC caches.  The pinned tree has none left.
- **Aliasing**: a slot that holds an object another slot walked first carries that slot's name and the depth at which it
  met it in its fingerprint, so `X = Base.D` and `X = {**Base.D}` differ.  Slot order decides the owner; the calibration
  (three processes) sees an unstable order as `N`/`U` (none today).  The comparison is also
skipped when settling every importer already costs less than `SLOT_COMPARE_S` (2 s).

Since the comparison runs before the servers start, the hot server is still used for the readers (modules the collection
did not import).

### Proven (slot map)

- On the 30 kit patches and the dev300 labels available at the time (see the report in the branch's last commit message),
  every failing test of every narrowed patch is among the readers of the changed slots (`.work/scratch/checkreaders.py`,
  not committed), and the selector has 0 misses.
- Fresh runs of an unpatched tree give an empty change list (three environments: with and without pytest, numpy, pandas,
  duckdb loaded).
- Synthetic check: 18 single-slot edits (bool/int class constants, readers from 1 to 226) labeled with the whole suite,
  failing tests against the readers (`.work/mut`, not committed): see below.

### Not proven (slot map)

- Order effects: a test that fails because an earlier test ran under the patch (shared registries) and never reads the
  changed slot itself is not a reader.  Same gap as for the line map.
- Reads the tracer cannot see: class attributes read through `vars(cls)` / `cls.__dict__` after import (sqlglot does this
  only in metaclasses at import), module globals read through `globals()` from another module's dict, reads by native code.
  A test that went through such a path would be missed.  Whole-slot granularity only: a changed key of a dict slot selects
  every reader of the dict.
- Aliasing is recorded per slot, not per class body statement; an object shared between a slot and something the
  snapshot does not reach (a default argument of a native object, a registry outside sqlglot) stays invisible.
- The tracer's `_depth` counter is shared by all threads (a read in another thread while a module imports is lost);
  the slot map was not rebuilt for it.
- Import-time side effects outside sqlglot's state (logging, warnings, environment) are not compared.  Assignment values
  with calls that have such effects are not excluded.
- `maps/state.json.gz` depends on the interpreter that builds it; the Dockerfile builds it, `tools/Dockerfile.native` too.

## Proven

- Verification runs reproduce the kit's failing sets on the sample patches tried (native arm64 and emulated amd64).

## Not proven / known gaps

- Order and state effects: the labels come from one whole-suite session; the map comes from cold single-test
  processes and verification runs only the candidates. A test that fails only because an earlier test ran under
  the patch (shared registries, caches) is outside the candidate set.
- A failing teardown is charged to the last test of its class and file (those are added), not proven on labels.
- The labeler's per-test timeout is unknown: a test that got much slower is treated as unverified.

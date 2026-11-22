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

## Proven

- Verification runs reproduce the kit's failing sets on the sample patches tried (native arm64 and emulated amd64).

## Not proven / known gaps

- Order and state effects: the labels come from one whole-suite session; the map comes from cold single-test
  processes and verification runs only the candidates. A test that fails only because an earlier test ran under
  the patch (shared registries, caches) is outside the candidate set.
- A failing teardown is charged to the last test of its class and file (those are added), not proven on labels.
- The labeler's per-test timeout is unknown: a test that got much slower is treated as unverified.

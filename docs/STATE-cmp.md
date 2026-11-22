# State of the comparison-operator proof (branch `cmp`)

## What it does

A patch that replaces the operator of exactly one comparison in the body of a function (`is` -> `==`, `<` -> `<=`,
`== None`, ...) and changes nothing else (not a comment, not the line count) cannot change the outcome of a test that
executed that comparison only on operands where the new operator answers what the old one did.
`selector/cmpproof.py` proves it per test from `maps/cmp.json.gz`; `selector/main.py: prove_slow` drops the proven tests
from the candidates (it needs no change: it applies to every test the map covers, and the map covers all 1620).

## The map (`tools/cmpmap`, built from the pinned commit only)

* `sitecustomize.py` compiles every single-operator comparison of `sqlglot/**/*.py` (not `in`/`not in`, not chained) as a
  call that evaluates it as before and, on the same operands, the operators a patch could replace it by (`ALTS`: `==`
  <-> `is`, `!=` <-> `is not`, `<` <-> `<=`, `<` <-> `>` ...; `==` <-> `!=` or `<` <-> `>=` flip the value on every input).
  Alternatives are evaluated only on operand types that cannot run user code (None, bool, int/str/bytes for
  ordering and `==`, enum members and instances that compare by identity for `==`/`is`): the trace cannot change the
  state of the traced program. Floats and everything with its own `__eq__`/`__lt__` count as a disagreement.
  Each disagreement is written at once (a killed pool worker loses nothing).
  The per-site state (which alternatives are still alive, the execution count) is reset at every test boundary
  (`rwx_cmpmarks.py`) and in every forked child, so a disagreement is recorded once PER TEST and process (it used to be
  once per process: in the whole-suite session only the first test to disagree was written). Execution counts are written
  per test (`C pid test site op n`). If the original comparison raises, a `R` record and a `D` record for every
  alternative are written. The tracer raises the recursion limit by 4 (the frames the rewrite adds at a comparison).
* `trace.py cold`: every test alone in a fresh pytest process, twice (hash seeds 1 and 2). A site whose execution count
  differs between the two seeds is nondeterministic and unproven for that test. One test per process: the per-test reset
  changes nothing for the cold records (the single test is the single epoch), so they were not rerun.
  `trace.py session`: the whole suite in one pytest process (the labels come from one such session), the plugin
  `rwx_cmpmarks.py` says which test is running.
* `pack.py [--scope file|all]` -> `maps/cmp.json.gz`: per comparison and alternative, the tests for which it was not shown
  to be a no-op: cold disagreement -> that test; session disagreement -> `--scope file`: every test of the file of the
  running test, `--scope all`: every test (the test that ran may have changed state another test consumes);
  session disagreement while a module was being imported or outside a test -> every test (both); in a process a test
  started -> the tests that start processes. A site whose original comparison raised in a session -> every test.
  The two sessions are compared per test and per site (execution counts, disagreement sets) and by site totals: any
  difference makes the site unproven for every test; the disagreements of both are charged (their union).
  A cold nondeterministic site is charged after the sessions are read (its operator comes from them); a site with no
  operator is not in the map, i.e. unproven. A test that failed in any profile run, was not profiled, or started a
  process that does not trace is never proven (`bad`).

Rebuild: `tools/cmpmap/run.sh session .work/cmp 3` and `... 4`, `tools/cmpmap/run.sh cold .work/cmp 0 2 - 1,2` (and worker 1),
then `python3 tools/cmpmap/pack.py --scope all kit/sample/tests.json maps/cmp.json.gz .work/cmp`.
The committed map is `--scope all`.

## Refusals (selector/cmpproof.py, `single_compare_swap`)

Each has a test in `tests/test_cmpproof.py` (`python3 -m unittest discover -s tests -p 'test_cmp*.py'`).

* path must be `sqlglot/**.py`; both sources must decode as UTF-8, contain no `\r` or form feed, and have the same number of lines;
* the two syntax trees are equal except one `Compare` with ONE operator; any other difference (a constant, `-0.0` vs `0.0`, a
  name, a comment's line, a second swap, a nested swap) refuses; the text may differ only on that comparison's lines;
* the swap must stay inside a family of `ALTS` (never `in`/`not in`, never `<` -> `==`, never the complement `<` -> `>=`);
* chained comparisons (`a < b < c`) never match (and the tracer does not record them);
* the comparison must sit in the body of a def or lambda: module level, class bodies (also inside a function), decorators,
  default arguments, annotations, base classes refuse; comprehensions count as the scope they sit in;
* a new `is`/`is not` with a literal or any non Name/Attribute/Subscript/Call operand refuses (SyntaxWarning at import).

Tracer side (a disagreement, i.e. "unproven"): another bool, an exception, a non-bool result, a float/complex operand, an
operand type with its own `__eq__`/`__ne__`/`__lt__`, `==` against `is` on non-singleton str/int/bytes (identity of equal
values is not stable), mixed int/str ordering, ordering on anything but int/str/bytes/bool.

## Measured

* Kit sample (native arm64 image): 223.689 -> 173.448 simulated, CPU 6.18 -> 5.44 s, 0 misses both; 2haz, 66ks, 2qn5 cost 0.
  No patch costs more than at 99dc0ff. dev300 labeled subset (58 patches): 659.414 both, 0 misses both (no function-body comparison swap
  among them: the proof is inactive there).
* Attribution scope of a session disagreement, kit sample, native: `file` 173.448 and `all` 173.448 simulated, 0 misses both,
  identical selector output; 2haz, 66ks, 2qn5 cost 0 in both. `all` is committed (no loss on the sample); `file` unproves
  148 (site, alt) for every test, `all` 1683 (maps: 56 KB vs 29 KB).
* `tools/cmpmap/validate.py`: real whole-suite runs of swapped comparisons chosen from the map (full, partial and no proof);
  `check` takes the pinned source root and derives the swap with `single_compare_swap` from the real patched file.
  A file-level `ERROR tests/x.py` fails every test of the file; `SUBFAILED[...]` lines are parsed (the old regex never saw
  them); empty output no longer raises.
* The map covers all 1620 tests (all profiled cleanly).

## Build facts

Native (arm64) labeling image `rwx-label:native`, not the amd64 one: the traces depend on operand values, not on the architecture;
the whole-suite sessions (per-test records, hash seeds 3 and 4; 21 min each on the loaded machine, 7 min quiet) passed 1620 tests
under tracing. Cold runs (old tracer, not rerun): seeds 1 and 2 for the first 352 tests, seed 1 for the rest.
Session facts: 36 sites differ between the two sessions (per-test counts or disagreement sets): unproven for every test;
no site where the original comparison raised (0 `R` records), so the cold gap below did not show up; 0 `bad` tests.
The `executed-*.json` of the first cold run were overwritten by the restart; the session lists every executed site anyway.

Known gap of the cold traces: with the old tracer an original comparison that raised left no record at all (no site, no
alternative, no count), so a test whose every execution of a site raised looked as if it never executed it. The new
sessions record raises and saw none, which closes it for everything the session executes in the same way.
Recursion: sqlglot and its tests never set the recursion limit or catch `RecursionError`; the tracer raises the limit by 4.

## Not proven / open

* Order and state effects between tests of the whole-suite session are covered only through the attribution above (`all`: a
  disagreement anywhere kills the alternative for every test).
* `908fd05` (candidates reached only in the whole-suite session, `p.unrun`, proven from the session record) is kept: the
  session record is now per test and per forked child, so it is complete for those tests.
* Operand values that depend on the clock, on identity or on hash order: floats are excluded; counts are compared across
  two hash seeds; nothing else detects them.

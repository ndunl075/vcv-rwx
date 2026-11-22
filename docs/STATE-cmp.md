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
* `trace.py cold`: every test alone in a fresh pytest process, twice (hash seeds 1 and 2). A site whose execution count
  differs between the two seeds is nondeterministic and unproven for that test.
  `trace.py session`: the whole suite in one pytest process (the labels come from one such session), the plugin
  `rwx_cmpmarks.py` says which test is running.
* `pack.py` -> `maps/cmp.json.gz`: per comparison and alternative, the tests for which it was not shown to be a no-op:
  cold disagreement -> that test; session disagreement -> every test of the file (class/module fixtures are shared);
  session disagreement while a module was being imported or outside a test -> every test; in a process a test started
  -> the tests that start processes. A test that failed in any profile run, was not profiled, or started a process
  that does not trace is never proven (`bad`).

Rebuild: `tools/cmpmap/run.sh session .work/cmp 3`, `tools/cmpmap/run.sh cold .work/cmp 0 2 - 1,2` (and worker 1),
then `python3 tools/cmpmap/pack.py kit/sample/tests.json maps/cmp.json.gz .work/cmp`.

## Refusals (selector/cmpproof.py, `single_compare_swap`)

See the list in the final report of the branch; each one has a test in `tests/test_cmpproof.py`.

## Not proven / open

* Order and state effects between tests of the whole-suite session are only covered through the attribution above.
* Operand values that depend on the clock, on identity or on hash order: floats are excluded; counts are compared across
  two hash seeds; nothing else detects them.

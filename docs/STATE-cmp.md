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
* `tools/cmpmap/validate.py`: real whole-suite runs of swapped comparisons chosen from the map (full, partial and no proof);
  no failing test was ever claimed as running as before.
* The map covers all 1620 tests (all profiled cleanly), 62 KB.

## Build facts

Native (arm64) labeling image `rwx-label:native`, not the amd64 one: the traces depend on operand values, not on the architecture;
the whole-suite sessions passed 1620 tests under tracing (hash seeds 3 and 4, ~7 min each). Cold runs: seeds 1 and 2 for the first 352
tests, seed 1 for the rest. A site whose execution count differs between the two sessions is unproven for every test (68 sites).
The `executed-*.json` of the first cold run were overwritten by the restart; the session lists every executed site anyway.

## Not proven / open

* Order and state effects between tests of the whole-suite session are only covered through the attribution above.
* Operand values that depend on the clock, on identity or on hash order: floats are excluded; counts are compared across
  two hash seeds; nothing else detects them.

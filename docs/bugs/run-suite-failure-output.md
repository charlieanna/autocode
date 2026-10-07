# A failure printed mid-run borrowed later modules' output (#545)

#596 made `tools/run_suite.py` print a failed module's output as soon as the
module finishes, so a slow or hung module still running cannot hide it. It
also dropped the copy printed at the end of the run. Two consequences showed up
on master, both at `--verbosity 2`, which CI uses and which
[saved-verification-commands.md](saved-verification-commands.md) names as a
declared suite command. Both still reproduced on master at 0175e89, with
`run_suite.py --jobs 5` run as a subprocess and its output read by that
commit's verifier.

**The verifier misread tracebacks.** AutoCode reads a declared suite command's
output with `autocode_verify.per_test_results`, and
`autocode_test_setup.failure_details` took each failure's traceback as
everything up to the next `FAIL:`/`ERROR:` header. A mid-run failure block is
followed by later modules' output, so that output became part of the traceback.
Five fixture modules finished in this order: a missing mock target, a passing
module that logs an application error it caught, a real assertion failure, a
passing module that logs a caught `ModuleNotFoundError` from its own test code,
and a slow module. On master:

| | before #596 | master | now |
| --- | --- | --- | --- |
| missing mock read as a setup-only failure | yes | **no** | yes |
| real assertion failure | failure | **"could not import its dependency on base"** | failure |
| tracebacks after the last module's row | 2 | **0** | 2 |

The second row means a regression proof would discount a real failure on base.
At `--verbosity 1` passing modules print only their row, so it read correctly.

**Failures were far from the summary.** An early failure's traceback ended up
above every later module's output (at `--verbosity 1`, above every later
module's row), with only module names beside the final line, where a CI log
opens.

## Fix

- `failure_details` ends a section at the next failure or at the footer that
  closes a failed unittest report (`----`, `Ran N tests in ...`, `FAILED`),
  whichever comes first. A footer that ends `OK` closes no failure, so a test
  that quotes a passing run's output in its failure message keeps the rest of
  its traceback. Ending at that quoted footer would drop the application frame
  quoted after it and leave a quoted, caught import error: a real failure would
  read as "could not import its dependency on base". A quoted failed run is
  still split at its own `FAIL:`/`ERROR:` lines, as before.
- Stock unittest output reads differently only where text follows the footer,
  and then correctly. AutoCode captures a test command's stdout and stderr in
  one file, so a test's `print()` to a block-buffered stdout (Python's default
  when `PYTHONUNBUFFERED` is unset) lands after the footer. That text was read
  as part of the last traceback: a printed, caught import error made a real
  assertion failure "could not import its dependency on base", and a printed
  application frame hid a missing mock, which then counted as a reproduction.
- `run_parallel` still prints each failed module's output when it finishes, and
  after the last module it prints `The failures again:` and each failed module's
  unittest report again (from its first `FAIL`, `ERROR` or `UNEXPECTED SUCCESS`
  on, or the whole output when there is none). The repeat has no per-test result
  lines. The per-module rows, the counts and the final
  `Ran N tests in M modules ...` line are unchanged.

`--jobs 1` was never changed and still prints unittest's own report.
`tests/test_run_suite_output.py` runs real modules through `run_parallel`, with
the test choosing the order they finish in, and checks that the verifier reads the
same results as from one stock unittest run.

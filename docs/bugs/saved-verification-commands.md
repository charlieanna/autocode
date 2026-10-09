# Saved runs silently ignored verification command repairs

A run created with automatic test detection kept that selection even when an
operator supplied `--test-command` on resume. Root unittest discovery can be
wrong for a repository with its own suite runner: during a test-directory
migration it can execute both copies and disregard declared exclusions.

Existing `--test-command` and `--regression-command` options now accept an
explicit correction with `--resume-paused` at a reconciled pause immediately
before the Validator or combined checkpoint. Other boundaries reject the
change, so a passing proof from the old command cannot reach acceptance.
Changes are recorded as `verification_commands_changed` user events. The
contract, model routes, limits, test timeout and past proof receipts remain.

The next proof compares its selected interpreter, detected framework and
explicit commands with the saved execution context and reruns when they differ.
The baseline cache also compares the actual suite command. Changed-test
selection and criterion matching continue independently of the suite command.

For the older AutoCode task checkout, the declared suite command is
`.venv/bin/python tools/run_suite.py --verbosity 2`. Its runner selects the
original `tools/` test tree and documented exclusions and emits each unittest
result. Newer AutoCode checkouts use `--jobs 1 --verbosity 2` when a single
per-test result stream is needed. Do not choose a test directory solely because
it is called `tests`, discard unique tests, or replace the runner's proof with
an exit-zero command. The current runner's parallel run prints each failed
module's report when the module finishes and again before its summary; the
verifier ends each traceback at its module's `Ran N tests ... FAILED` footer,
so neither copy takes in later modules' output
([run-suite-failure-output.md](run-suite-failure-output.md)).

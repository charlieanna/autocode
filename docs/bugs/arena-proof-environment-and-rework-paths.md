# Arena proof environment and serial repair paths

The October 6 hard Arena campaign exposed two runner failures. SymPy and Django
passed their case oracles but stopped in recovery because regression proof used
the system Python instead of the configured virtual environment. The explicit
test commands began with literal `env NAME=value` assignments, which collector
detection did not recognize. Derived checks also discarded pytest configuration
and bootstrap options. Both revisions failed before collecting behavioral tests.

Python collector detection now recognizes literal `env` assignments and retains
the interpreter, environment and pytest options when selecting changed test
files. Unsupported shell programs remain unrecognized. Unknown pytest option
arity uses the complete configured suite rather than guessing its arguments.
Environment-wrapped commands still run fresh; this does not grant them reusable
dependency identities or relax the original-fails/candidate-passes proof gate.

The pytest case exposed a separate contradiction. Its Completion Reviewer and
Resolver requested `src/_pytest/fixtures.py`, but assignment replaced those paths
with the original milestone list. The Builder therefore received a requirement
to fix a file it was forbidden to edit. A serial REWORK assignment for the same
single milestone now adds the reviewed repair paths to its existing paths. A
later narrower report retains that admitted scope so recovery evidence stays
bound to the same source. Moving to another
milestone, parallel worker ownership and progressive planning keep their existing
scope rules. The contract and its acceptance criteria do not change.

Regression coverage executes a real original/candidate proof requiring an
environment variable, an external pytest plugin and a configuration override,
including a still-broken candidate control. Handoff coverage checks the Builder's
received paths and retains the worker-ownership restriction. Historical Arena
attempts remain final; qualification uses new attempts.

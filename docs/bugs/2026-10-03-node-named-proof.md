# Node named-test proof (#295)

A live build approved named criteria for a Node project, then wrote a custom
assertion script that printed passing test names. The targeted command failed
on the original source and passed on the candidate, but neither invocation
produced machine-readable per-test results. AutoCode correctly withheld
completion; another Builder/Reviewer cycle could not fix the missing adapter.

The runner now supports Node's built-in `node:test` through an owned custom
reporter, detects named Node test files, and derives their targeted command.
Planner and Builder instructions explain the supported protocol before tests
are written. Unsupported summaries receive actionable guidance. Existing suite
commands and named-case requirements remain intact.

Regression coverage executes the real Node runner for named before/after proof,
preserved cases, nested suites, skips/todos, aborted tests, import/hook errors,
empty files, forged stdout and ambiguous names. Truncated, malformed and
inconsistent event reports cannot supply proof.

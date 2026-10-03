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

The resumed live OpenCode workflow reached `TASK_COMPLETE` on 2026-10-03. A real
Builder converted its assertions to registered `node:test` cases without
weakening them. The runner's new proof recorded seven fail-to-pass results and
matched all six required named criteria. The original browser suite and fresh
independent Validator checks passed. Node 22 and 25 were also checked locally,
including an installed-wheel reporter check. This was a recovered campaign;
it also needed the separate retained-work handoff fix.

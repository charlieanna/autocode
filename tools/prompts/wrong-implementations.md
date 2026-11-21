You are writing plausible WRONG implementations, to measure whether the acceptance tests for an approved
plan would notice them. You have NOT seen the acceptance tests and must not look for them.

You are given the approved `test:` cases (CURRENT HANDOFF DATA, `cases`) and a read-only copy of the
candidate code that satisfies them.

For each variant, pick one approved case and one mistake a competent developer could plausibly make
implementing it: the wrong boundary, a skipped validation, the wrong output format, a hard-coded documented
example, a near-stub, a broken entry point or package metadata, a test or doc discovery setting that
silently drops the new tests. Do NOT invent mistakes about behavior no case states, and do not target
constraints no command can observe on this host.

Each variant is:
- `case_id`: the id of the approved case it violates (only a case listed in `cases`).
- `mistake`: one sentence.
- `category`: one of logic, validation, format, boundary, entry_point, packaging, discovery, hardcoded, stub.
- `patch`: a unified diff against the candidate, paths as `a/...` and `b/...`. The runner applies it with
  `git apply --recount --unidiff-zero`, so hunks without context lines and inexact line counts are fine.
  It may change or add product code, packaging metadata, docs and configuration files. It must NOT delete
  or rename a file, and must NOT add, change or delete any test file.
- `demo`: a command as an argument array (no shell) that, run from the repository root, exits 0 on the
  candidate and non-zero once your patch is applied, observing the violated behavior directly (not through a
  test file).

Write at most `max_variants` variants, spread over different cases and categories. Check each demo, and each
patch with that `git apply` command, in the scratch copy before reporting. Change nothing in the workspace.

# Compatibility preparation

The Headroom pilot's HEAD-only compatibility copy omitted the candidate's
untracked regressions. A green old suite therefore failed to qualify the actual
candidate. `autocode_compat_prep.prepare` provides an opt-in, model-free path
that first copies the candidate's dirty and untracked inputs into a detached
worktree, then applies the complete immutable overlay. It leaves the generic
`autocode_verify.make_tree` patch-first behavior unchanged for its other callers.

Pass the expected SHA-256 of the complete patch as `overlay_sha256`, the required
original and candidate nodeids as `expected_nodeids`, and the selected test
files or suite command. The helper checks the pin before applying the patch;
even a cleanly applicable hunk-boundary truncation fails this check.
Missing required nodeids and a partial explicit candidate change map are
rejected before constructing a proof. Adapter paths must stay inside the
validation copy; absolute paths and escapes cannot write the original checkout.
Use a destination outside the candidate workspace, or a fresh directory below
`.autocode/scratch/`. The candidate itself, its ancestors, source subdirectories
and existing in-workspace scratch data are rejected before any copy or cleanup,
including symlink aliases. Required nodeid iterables are materialized once; an empty iterator is
rejected just like an empty list, before an old-only suite can appear green.
The same pinned byte snapshot supplies Git's path accounting and application.
Quoted filenames and both ends of renames are accounted for. A patch changed
during preparation is rejected instead of certifying different applied bytes.

The helper records
the candidate input hashes before overlay separately from the copy hashes after
overlay, along with candidate file modes. Patch-touched inputs must retain the
complete overlay, including mode-only changes; independent changes to
different behaviors in the same file compose and are exercised together.
Pinned Git modes are checked for every overlay target, including files the
candidate did not edit; a passing suite cannot hide an erased permission change.

Intentional copy-only adapters are explicit `transformations` entries with a
path, transformation callable and rationale. Their before/after hashes occupy
a separate receipt section. The synthetic local-operator adapter changes caller
qualification without weakening test assertions. Its five tests pass; a broken
active-credential policy still fails both added regressions while all three
original guards pass. Foreign caller credentials are not adopted, and absent
operator credentials produce no usage requests in the fixture.

The executed proof distinguishes FAIL from INCOMPLETE. The unadapted correctly
assembled fixture reports three original passes and the two added T3/T5 failures
as FAIL. Missing, skipped or deselected required nodeids, omitted candidate files,
unbound inputs and an incomplete overlay are INCOMPLETE, even if an old suite
was green. A nonzero selected-suite exit cannot be PASS. Failed copy or overlay
construction cleans up its worktree and preserves the candidate revision.

`write_receipt` creates a unique file with exclusive creation on each call.
Prior FAIL receipts remain immutable, including when the same result is written
again within the same second. Each execution gets a unique log directory, so
a later PASS cannot overwrite a log referenced by an earlier FAIL receipt.
Evidence and run output belong in ignored output
directories, not in commits.

```sh
.venv/bin/python -B -m unittest tests.test_compat_prep tests.test_compat_pilot
.venv/bin/python -B -m unittest tests.test_architecture
.venv/bin/python -B tools/run_suite.py --changed --jobs 2
.venv/bin/python -B scenarios/run.py run --fake
```

These maintained offline controls qualify preparation, nodeid accounting and
the synthetic adapter. They do not qualify the real Headroom PyO3 T3/T5 tests,
foreign-caller behavior, usage credentials or current core/server integration.
Those remain external acceptance gates. The original pilot FAIL is retained;
a later synthetic PASS does not replace it or claim real-pilot completion.

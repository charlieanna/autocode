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
the synthetic adapter. Real application and live-model evidence is separate.

## Real application qualification, October 3, 2026

The follow-up for #223 used the original Headroom source
`8538a831531bb277a6083ed2a010312a849c3050`, its historical candidate production
and test changes, and the complete #3863 overlay at
`2d764f3eb6b0e558ebd71c0222d15dac53910fcc`. Candidate hashes and the complete
overlay pin are recorded separately from the two-line validation-copy adapter.
The original candidate remains unchanged.

The actual application checks produced these distinct results:

- The unchanged T3/T5 tests pass on the pre-overlay API.
- The complete unadapted compatibility copy executes all 258 required cases:
  256 pass and exactly T3/T5 fail.
- Adding explicit local-operator provenance to those two synthetic calls,
  without changing assertions or other callers, passes all 258 cases with no
  skips or missing cases.
- Deliberately disabling learned-token fallback fails T3/T5 and an existing
  tracker success test; the other 255 cases pass. The adapter does not hide
  that real behavior defect.
- Omitting candidate tests and truncating the pinned overlay are each rejected
  as INCOMPLETE before they can earn compatibility credit.
- The selected suite includes foreign/forwarded-caller guards, absent-operator
  behavior, and actual Uvicorn/current PyO3 integration. The loaded compiled
  core is hash-bound, with its 228 selected native build inputs matched against
  the original qualified build. Ruff and format checks pass on all eight
  changed Python files.

A fresh AutoCode review run using OpenCode and `openai/gpt-6-sol` independently
inspected the adapter and executed the same 258-case native application suite.
Its report approved the adapter with no findings. The executed receipt has
exact case accounting, no skips, the pinned loaded core, and zero refused
network attempts. All pinned qualification inputs remained unchanged.

This qualification uses synthetic application credentials, fresh per-test
state, pinned local tokenizer assets, and the documented opt-out from upstream
health probes. It makes no real vendor API calls. An initial invalid setup let
an upstream fixture clear isolation settings and changed the local savings
ledger's modification time; without a pre-run copy, its content change cannot
be established. That run is excluded and retained. Corrected runs guard access
to real application/account files and preserve both ledger bytes and modification
time relative to the post-error pin. Intermediate environment failures and
blocked background-network attempts are also retained, not relabeled PASS.

The original pilot FAIL and every subsequent result remain separate in ignored
`.scenario-runs/remaining-defect-proof/headroom-223/` evidence. These results
qualify the compatibility preparation and adapter for this pinned application
case. The review task's completion does not establish completion or acceptance
of the whole Headroom #3913 implementation pilot or reliability across all
models and projects.

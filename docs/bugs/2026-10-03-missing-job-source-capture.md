# Failed job recovery with missing source artifacts (#313)

A stopped workflow job could be permanently blocked even when the checkout
matched its saved original identity. Recovery tried to load both the source
manifest and stopped witness before checking the checkout, and exact retry
rejected the resulting historical `unrestored` list without rechecking source.
An operator restoring the original source could therefore remain blocked too.

Recovery now compares the current checkout with the identity saved before
provider admission: Git HEAD, path set/types, contents, full file modes and
symlink targets. If they match, no restoration writes or capture blobs are
needed. Exact retry independently repeats this check, then checks configuration
and limits; admission captures and checks the identity again. Failed attempts,
usage and restoration diagnoses remain archived. This does not construct an
original identity from a later checkout or bypass stopped-process verification.

Attempts predating source capture are different. Their original full identity
is unavailable, and an older content snapshot does not establish all permission
bits. Status schema 2 preserves their archive, token and diagnosis but publishes
`needs.kind = recover_source`, `action = null` and an explanation. An exact
retry still refuses before provider dispatch. Inspect retained work and current
changes before starting a new run; no automatic restart or budget reset occurs.
The unavailable original run reported in #313 has not been inspected, so its
specific capture-loss cause remains unconfirmed.

## Fresh live qualification

Evidence is retained under
`.scenario-runs/remaining-defect-proof/source-recovery-313/` (ignored).
These tests use actual GPT-6 Luna calls through a registered Codex command
provider, medium effort, Codex CLI 0.160.0 on macOS. A fixture wrapper terminates
only its owned provider after the first real completed command; it forwards real
provider events unchanged and never supplies a model response. Artifact-loss
faults move our fixture's files aside and retain them; no run state is edited.

- **Legacy upgrade reproduction:** runtime `0a36c1e8`, before captures were
  introduced, ran a real Investigator and persisted its controlled exit42.
  Its first command had failed to find `capture_command`. On `cd02b793`, the
  current status offered `retry_job`, but that exact action failed for missing
  original source capture. The source bytes and modes were unchanged, and the
  model-call count stayed one. With the candidate, the same retained run exposes
  `recover_source`, no executable retry action, and the precise refusal.
- **Current-run reproduction:** on `cd02b793`, a second real Investigator was
  interrupted after a successful source inspection. The fixture retained but
  removed the original manifest from its expected location. Although the full
  current identity matched the saved original identity, exact retry was rejected
  for `unrestored` source. No extra model call occurred.
- **Same-run recovery:** the candidate rejected a wrong token, changed source
  bytes, a non-executable permission change, an added untracked file and changed
  stage limits, all without model dispatch. After undoing those fixture-only
  changes, the exact retry succeeded on the same run. Luna's second attempt
  finished in 111 seconds, exit0, with an accepted `reproduced` diagnosis and
  runner probe verification. AutoCode reached the requested checkpoint before
  planning. The original 600-second run / 240-second stage / 120-second idle /
  three-iteration limits, failed archive and original source pins stayed intact.
  There were two actual model calls total. This qualifies Investigator recovery,
  not completion of the subsequent build.
- **Fresh candidate recovery:** another real interrupted Investigator had its
  stopped witness moved aside. Recovery reported
  `original_identity_verified: true` and no unrestored paths, retained the failed
  transcript, and offered the exact retry without making an automatic second
  model call. Source bytes and modes stayed unchanged.

The successful retry separately exposed a configured-provider prompt defect:
it required `capture_command` but the specialized Investigator handoff lacked
that command. The model tried a non-executable implementation module and
reported that it produced no receipt. Its diagnosis was accepted through the
separate runner probe; this is not receipt validation. That defect is tracked
in [#327](https://github.com/charlieanna/autocode/issues/327).

## Regression coverage

CLI tests cover missing manifest/witness, corrupt manifest, exact manual
restoration and rejection of changed bytes, modes, tokens and limits. Existing
checks for missing original bytes with changed source, later edits, process
cleanup uncertainty and restoration failures remain enforced. The new positive
recovery tests and legacy view test failed against `cd02b793` before the fix.
The focused 51-test check passed. The 219-test affected-module run exposed one
stale schema-version assertion; it was updated from 1 to the documented version
2, and that six-test module passed. No behavioral assertion or timeout was
weakened. The full suite passed 3,282 tests across 236 modules in 531 seconds;
the supplementary catalog passed 54 scenarios, with one existing NOT_EXERCISED
and one live-Investigator SKIPPED. After removing misleading retired-path mocks,
the corrected cleanup tests and process-discovery tests separately passed 52
tests (one existing browser skip). Runtime files still match the live source pins.

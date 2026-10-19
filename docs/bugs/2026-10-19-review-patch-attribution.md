# Patch-relative review findings (#1602)

A failing review probe shows a defect on the candidate. It does not establish
that the reviewed patch caused it. The runner now captures explicit patch bytes,
source inputs and delivered tests, and runs identical probes in separate base
and changed copies. Source, patch and test changes during proof refuse attribution.
The existing launch-input and supervised command boundaries still apply.

Saved executable blocking findings carry a runner-derived `disposition`:

- `introduced`: a matching probe passed on base and failed on the change.
- `pre_existing`: every relevant changed-side failure has equivalent complete
  Python failure details on base, including site, type and diagnostic content.
- `inconclusive`: missing, skipped, incomplete, unsupported or different evidence;
  the finding remains blocking. Untestable blockers are explicitly unverified.

Only known scratch-root paths are normalized. Full unittest traceback sections
or pytest failure XML are compared, never truncated tails or exit codes alone.
Setup and teardown errors cannot establish equivalence. A demonstrated regression
wins when a finding has several probes.

The saved verdict and counts follow effective blockers after classification.
Pre-existing observations remain in the report, status evidence and follow-ups,
but are not automatic repair requirements. The submitted verdict and any runner
adjustment are visible. Unanchored findings remain in the PR review summary.
Legacy findings retain their blocking behavior.

An empty `change_patch` has no baseline attribution; this includes current
`review-pr` and external-verification inputs. No base is inferred from HEAD or
prose. Capture currently admits bounded ordinary Git text patches and regular
source files (64 MiB total); unsupported captures retain blockers with a reason.
This does not change task acceptance criteria or the Validator prompt.

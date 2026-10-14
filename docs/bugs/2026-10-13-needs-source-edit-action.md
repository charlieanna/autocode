# Public stale-source repair action (#1010)

`tests.test_needs_actions_cli` covers one remaining public-action gap from #1168.
It reaches a genuine rejected-review handoff with the existing fake provider,
pauses before the Resolver, and edits the owned project's source. The real CLI
then exposes `needs.kind=resume` and `--resume-paused --accept-source-edit`.

A plain public resume holds the same pause, launches nothing and preserves the
attempt ledger. `TaskRun.accept_source_edit()` executes the advertised action:
only the read-only Resolver runs, the edited source and approved contract stay,
and subsequent correction completes with fresh passing checks. No synthetic
checkpoint or production patch is used.

The one focused test passed in 43.019 seconds on base `0bf4a8a56`, under isolated
Python 3.12 in an owned Linux container with networking disabled. Its retained
log is `autocode-1010-needs-actions-evidence-20261014/focused-needs-actions-01.log`
outside Git. This qualifies this recovery action; #1010's full per-kind action
and transition matrix remains open. No live models or routing changes were made.

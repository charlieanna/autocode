# A criterion that names a command which must fail can never be validated

Open; found by the live run of `program-notes-cli` for #22 and #23 on 2026-10-06
(Claude models, run directory `20261006T034404Z-program-notes-cli-claude-tiers-45uzedfo`).
The cause is not in the program code: any run whose plan words a criterion this way
meets it.

`autocode_verification_plan.obligations` turns every command in backticks in a
criterion's `verification_method` into an `approved_acceptance` obligation, and clean
replay (`autocode_check_replay`) re-runs each one as a `mandatory_approved_execution`
that must exit 0. A usage-error criterion is naturally written the other way round. In
the live run, the skeleton's S4 said that `python3 -m notes add` (no text) and
`python3 -m notes frobnicate` each exit 2 with one usage line. Both commands did exactly
that. The replay then reported them as failed checks, and a report that cites a failing
check cannot pass.

The Tester's own checks all passed in replay: both unittest runs (5 of 5, including
the usage-error test) and its wrapped `sh -c '...; test $? -eq 2'` checks. Three report
repairs got the identical rejection, the Investigator traced it to the obligation, and
the run stopped at `PAUSED_INVALID_OUTPUT`.

## Options

- Leave out of the obligations a backticked command the criterion says must fail
  (exit non-zero, print usage), or record the exit status it must have.
- Have the Plan Reviewer refuse a verification method that names a bare command
  expected to fail, and ask for a test or a wrapped check instead.

# Nested coverage collection (#4039)

Status: **blocked; do not merge this draft head.** Independent review demonstrated that its fresh nested-file approach is not an authenticated observation. A proving test can copy old coverage data to the injected `COVERAGE_FILE`; the candidate then accepts the old lines even though the target function never executed. This was reproduced under real supervised commands for both unittest and active pytest-cov. Baseline master kept the corresponding lines unexecuted. Logs: `/private/tmp/autocode-cov-forgery-v2-master.log`, `/private/tmp/autocode-cov-forgery-v2-candidate.log` and `/private/tmp/autocode-cov-forgery-pytest.log`.

The original collision is real: the supplementary outer coverage tracer sees no data when python-semver's pytest configuration starts pytest-cov. Its actual proof-03 and an isolated comparison are recorded in #4039. The original unmeasured fallback is required until a replacement authenticates the producer and preserves the proving-command contract.

A documented pytest-cov `--no-cov` option can leave the outer tracer active, but it changes the effective pytest invocation and disables plugin behavior. The repository's named-proof contract says the proving-test command runs once more for coverage, so an adjusted extra measurement is not yet established as equivalent. Do not infer it is safe from matching test outcomes alone.

The local scoped SemVer M1 reconstruction reached real reference-failure feedback, repaired the test while retaining archived assertions, and publicly reports all six named cases pass on the candidate and fail on the reference. The run remains paused on changed-line gaps and two separate surviving mutants. It is not completed, and neither current #3981 nor any actual VS Code editor outcome is qualified by that run.

Pending: an independently authenticated collector that preserves the proving command and test semantics, controls showing the stale-data forgery remains rejected, actual VS Code Extension Host behavior, and exact-head required CI. No issue is fixed or ready for closure.

# Explicit review overhead and misleading TUI completion (#2113, #2167)

The retained GLM-5.3 Low/Kilo 7.8.3 TUI review on master `0b7c5a73`
completed with an approved report, but the terminal used build-delivery fields
and showed delivery unknown/report not recorded. The public workflow result
already held the review facts; it lacked a source binding for freshness.

The same trace had 63.070 seconds in Requirements, 51.137 in its report repair,
and 21.710 probing an unused Codex Builder: 135.917 seconds of avoidable work
for an explicitly selected review. The reached GLM probe (17.203 seconds) is
retained. The failed review (471.069 seconds) and successful retry (409.484
seconds) are separate: backend logs showed an API call error/internal retry,
then core inactivity, but did not establish the underlying HTTP cause.

An explicitly user-pinned review now starts its Reviewer directly. Only its
Validator route is due for preflight; successful tool/model/effort receipts are
still reused. Unpinned Requirements intake and build approval are unchanged.
The core records a post-report source revision and status checks that revision
and report digest. Terminal header, panel and details render the public review
result. Older unbound reviews stay unknown, and changed evidence stays stale.
No model timeout, model route, verification gate or budget was weakened.

Three new regressions fail against unchanged master. The supported Linux run
passes 253 tests in 59.886 seconds (two terminal checks skipped because that
image lacks tmux), covering the public spec-first review path, canonical export,
source/report tampering, legacy binding, probe receipt reuse and build controls.
Five prompt-rendering checks pass without changed fingerprints. The new export
regression caught the evidence schema consumer; its optional source binding is
now declared. Four existing report-identity state parameters were annotated to
repair master's already-exceeded type ratchet; behavior is unchanged.

Live TUI qualification and a fresh equal-task Kilo comparison follow installation
of the merged revision. Deterministic overhead reduction does not establish
faster model completion. Preserve the prior comparison and failed attempts;
original public bundle manifest:
`320462e1c64168c107df4d78c824743e30cc1bbcd961ece021caa57bab286175`.

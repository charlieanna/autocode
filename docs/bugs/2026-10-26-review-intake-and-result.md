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
and report digest in a separate `review_result` inspection. The authenticated
`evidence.workflow_result` remains stable for downstream exact comparisons.
Terminal header, panel and details render the public review
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

## Follow-on cause exposed by the matched live review

The first narrowed-intake TUI review completed without retries in 329.710 seconds
plus a 25.207-second reached-model probe (370.943 seconds observed start to result).
It made 23 requests and 30 tool calls; reported request intervals covered 193.934
seconds and tool execution 28.499 seconds. Direct Kilo on byte-identical inputs
finished in 97.243 seconds, with 12 requests and 14 tool calls. Both passed the
same external 210 checks with all 3,962 source hashes intact. This remains a slow
review and the difference is not all attributed to one mechanism.

One concrete mismatch is in provider framing: standalone Reviewer's schema has
`tests_run` and findings, but no build acceptance checks, criterion evidence or
receipt fields. It nevertheless received the Validator's capture/receipt and
PASS-report instructions, and made extra capture invocations. OpenCode/Kilo now
select a review-only output contract from the actual report capabilities; any
schema declaring build evidence retains the full capture contract. The complete
review schema, filesystem/event-log boundary and core-run blocking-finding
proofs remain. No time cap, effort, model, approval or completion gate changes.
The original 192 prompt fingerprints remain byte-identical; a new compiled
review case pins this narrow branch. Compiling the real Review schema proves the formatter selects this branch;
existing public fake-provider CLI checks preserve patch attribution and
source/report tampering refusals. A new real-provider qualification must establish
the observed effect; removing irrelevant instructions cannot guarantee fewer
model requests or faster service responses.

The wider suite also exposed a terminal-test race: it sampled as soon as an old
entry label appeared, before curses finished drawing the new list footer. The
check now waits for both intended list content and its footer, then binds Enter
to the visibly populated composer after asynchronous tab setup. It retains every
open-tab/key/help/quit assertion and adds no fixed sleep.

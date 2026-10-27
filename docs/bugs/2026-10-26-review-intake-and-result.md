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
check now waits for both intended list content and its footer, retaining every
open-tab/key/help/quit assertion and adding no fixed sleep.


## Remaining report-only overhead (#2113 follow-up)

The next native run, bundle manifest
`3228d6abe7df96c3094ddc74f81a719ba807d5ad3b84c36ebb8cbb922c7c42d6`,
completed in 265.255 seconds including a 24.696-second access probe. Its original
review took 164.434 seconds and produced `advisary` instead of `advisory` in
`findings[1].severity`. The runner then spent another 26.696 seconds preparing a
fresh report repair and 32.468 seconds executing it. Launch metadata confirms
that this report-only repair unnecessarily configured a kernel verification
shell, including native conformance probes, despite needing no tools.

The loader now corrects a unique single insertion, deletion, substitution or
adjacent transposition in the two standalone-review severity labels. This is
restricted to that stage and its exact closed schema vocabulary. It never
normalizes decisions, approvals, finding IDs, evidence, supplied exits or other
stage fields. Unknown or multi-edit severities and all other schema defects
still reject. Full validation precedes persistence, and the existing
`.reported.json` artifact retains the original response. A misspelled blocking
label remains blocking and still needs the unchanged independent finding proof.

Ordinary Kilo report-only repairs now select the existing tool-free report-recovery
policy. Original review stages retain their tools; model, effort, accounting,
attempt limits, fresh-session isolation and original-evidence binding are
unchanged. A standalone review also uses its real, accounted review request to
establish model access instead of a separate paid ping immediately beforehand.
Local transport, billing, model and effort admission remain. Build workflows
retain their access probes; a review grants them no successful probe receipt.

The retained native message timestamps explain the earlier timing gap more
precisely: the 13 review messages contain 73.868 seconds between message creation
and the first step part, plus 6.560 seconds after the reported step intervals.
The corresponding direct Kilo run contains 35.751 seconds before its first step
parts across 12 messages. These intervals occur inside Kilo's message loop and
include native preparation/response waiting; they are **not measured HTTP or
inference time**. AutoCode's controller does not dispatch or sleep between these
native messages. The stage-start-to-first-message interval was 11.758 seconds.
Provider queueing, prefill and snapshot costs cannot be separated from this
retained trace, so none is asserted to explain the whole remaining difference.

Deterministic regressions cover the live typo, other single-edit spellings,
unknown and malformed negative controls, raw preservation, atomic refusal,
unchanged blocking proof, one-call public CLI completion, and tool-free repair
selection. A matched native TUI/Kilo run checks actual elapsed improvement;
one sample does not establish a provider latency guarantee.


## Live result after #2178: remaining verification-policy mismatch

The green, merged `b9d6b404` runtime completed the matched TUI review in
286.136 seconds (267.473 seconds in its single native review stage), with no
access ping, report repair, recovery or operator reply. Both products passed the
same 210 external checks with all 3,962 frozen source hashes unchanged. Fresh
Kilo delivered in 160.425 seconds. The new AutoCode result was slower than the
previous 265.255-second sample; this is not evidence of an end-to-end speedup.
Retained bundle manifest: `ca9c2e92fb9857f8d3ec45d8a4e20bd547740207f1d5ca0e1deed60728c39511`.

The current trace has 15 native requests / 17 tools versus Kilo's 9 / 9.
AutoCode's review spent 20.823 seconds in tool execution, including a 16.543-second
architecture/prompt test invocation in addition to the requested 44 checks;
Kilo's tool execution covered 1.403 seconds. These intervals overlap native step
intervals and must not be summed as separate inference and execution totals.
The broad native step intervals were 118.479 seconds versus 94.981 seconds.
Native response wait, generation, tool choice and cache behavior vary; neither
one changed prompt nor one sample establishes a general latency guarantee.

The runtime Reviewer still said to apply the change in a scratch copy and
"run the test suite", even for an already-applied change with named focused
checks and a separately assigned external verifier. This conflicts with the
repository's task-focused verification policy and encourages unrelated extra
suite work. The follow-up establishes the reviewed source first, runs requested
checks early and once on that version, adds focused probes for concrete gaps,
and broadens when requested or justified by an affected consumer. It also asks
for batched independent context reads without discarding truncated content.
Fresh evidence, every requested check, failure reporting and the complete
independent blocking-finding proof remain. No Tester/Completion Reviewer stages
are combined. Only one compiled review rendering changes; the other 192 remain
byte-identical. Native qualification must measure the effect before claiming it
reduces model turns or elapsed time.


The changed review prompt's public CLI consumer was listed in the non-resource
CLI mapping, whose lookup excludes `tools/prompts/`. Move it to the resource
mapping and include the literal resource in the existing slow-consumer regression;
otherwise a prompt-only PR could skip the blocking-proof and canonical-result
CLI checks. The selection control fails before this correction.

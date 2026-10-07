# Node cancellations must retain complete named results (#556)

A native Node test timeout reports `testTimeoutFailure` and increments the
cancelled counter. The named-result parser counted it as an ordinary failure,
so its otherwise complete stream failed the final counter balance and returned
no named results. An asynchronous abort with an ordinary Error reason similarly
reports `testAborted`; the default DOMException abort can instead remain an
ordinary native failure and must follow its actual reported counters.

Recognize `cancelledByParent`, `testAborted` and `testTimeoutFailure` as native
cancellation counter categories. All remain failed cases and collection errors,
never passing cases or ordinary fail-to-pass evidence for a bugfix. Header,
footer, identity, outcome and final-count checks are unchanged. Feature proof
has separate semantics; this does not impose a new universal transition policy.

A fresh native OpenCode 1.18.33 BEFORE run on `c7904176` used a Sol Builder and
Astra Tester under the original 1800/360/120/120-second and four-iteration caps.
It reached the actual derived Node proof: the repaired candidate passed all
three cases, while original code with the new tests had one healthy pass, one
ordinary assertion failure and one native timeout cancellation. Named base
results were missing, so proof was UNVERIFIED and the run paused. The independent
product/original-test oracle and terminal raw-stream, source and native-process
audits passed. This verifies the narrow defect, not general model reliability.

Native local controls and regression tests cover restored accounting, healthy
guards, genuine ordinary bugfix restoration and rejection of cancellation-only
restoration. The affected gate passed 15 tests and architecture passed four.
The full fake catalog retained 59 PASS, two ERROR, one existing NOT_EXERCISED
and one live-Investigator SKIPPED. Separate targeted reruns of both error cases
passed with unchanged source and caps; these are not a clean full-catalog run.

A fresh native AFTER run on `3fb84405` completed in iteration one with no stop
reason: nine actual calls and 600.650 active seconds, using the same brief,
fixture source, model routes and caps. Its current derived Node proof passed,
crediting only the ordinary restoration assertion as fail-to-pass and the
healthy existing test as pass-to-pass. The cancelled base case remained a
nonpassing collection error. The candidate passed three native tests; replay
of the protected original tests passed two. An independent oracle checked five
inputs, actual native Promises and immediate fulfillment. Explicit public
evidence inspection confirmed all five criteria and current full completion.
All 33 terminal audit checks passed, including raw-stream and process ownership
checks: all 465 recorded valid native identities were gone, with no unknown
identities or survivors.

The initial AFTER launch failed before any task or model call because an
external `inspect.py` observer shadowed the standard library. That failed case
is preserved; a fresh case renamed the helper and passed import/help checks
before the qualified run. It did not require an application change.

The implementation starts from newer master `eb004089`, rather than the BEFORE
runtime. Separate integration changes include nested provider ownership,
invocation interrupt handling, OpenCode 2.x support, recovery/replan prompts,
unittest counts and ignored-input proof plumbing. Unchanged newer-master native
controls reproduced the defect before the parser fix. The fixture trees and
blobs match, while fresh seed commits differ only in timestamps. The AFTER
environment was fully pinned; historical BEFORE dependency bytes were not, so
complete dependency equality is unproven. This four-file fixture uses pinned
OpenCode 1.18.33 and no ignored inputs. The result qualifies this cancellation
accounting defect, not every Node failure or general model reliability.

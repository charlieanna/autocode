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
restoration. Fresh live AFTER completion is still pending. The implementation
starts from newer master `eb004089`. Its separate integration changes include
nested provider ownership, invocation interrupt handling, OpenCode 2.x support,
recovery/replan prompts, unittest counts and ignored-input proof plumbing. The
AFTER protocol records these differences; this four-file Node fixture uses the
pinned 1.x provider and has no ignored inputs.

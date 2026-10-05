# Recovering after bounded report failures

Issue #446: a serialization correction contributes to the repeated-failure
streak without spending a full repair attempt. Three matching failures can
therefore pause with only one of two full repairs used, while the old public
retry path required both. An answered operational request could also hide the
existing recovery path for source edited during that pause.

A fresh mixed-model run exposed a second defect: repairing the Investigator's
own report restored the interrupted Tester repair, then unconditional cleanup
in `accept_repaired_report` deleted that restored record. Cleanup now removes
only the accepted repair. Public retry recognizes the authenticated failure
bound, retains the actual attempts and evidence, and starts fresh validation.
Source-only recovery stays behind the operational-request and evidence checks.

## Evidence (2026-10-05)

The final runtime was pinned to candidate tree
`a2a541c2777b437fe193846ada661fb598867915` before this documentation note.
Both fresh campaigns used native OpenCode 1.18.33 with GLM 5.3, GPT-6 Luna and
GPT-6 Sol. An external relay retained original native streams while deliberately
truncating Tester report delivery and omitting one required field from the
Investigator report. These were controlled delivery faults, not claims that
OpenCode spontaneously produced either fault.

| Fresh campaign | Native calls | Active seconds / original cap | Result |
| --- | ---: | ---: | --- |
| Exact retry, unchanged source (`final-native-r2`) | 12 | 1325.53 / 1800 | PASS |
| Resume after a comment-only source edit (`final-source-r4`) | 14 | 1413.99 / 1800 | PASS |

Both reached and repaired the nested Investigator failure, retained the Tester
repair and its original count, exercised the public recovery action, and
completed with current independent validation and 729/729 oracle cases.
Original tests, failure counts, repair archives and limits were preserved;
no counters or budgets were reset. Native session exports confirmed the models;
no owned workers remained after completion.

Earlier failed campaigns remain retained: source R2 exhausted its original
budget after a rejected evidence path; source R3 lost the outer repair record.
They are not counted as passes. Final source R4 also needed two initial
Investigator report repairs before planning, both retained in its accounting.
Both final campaigns needed a completion correction and reproduced the separate
stale `stop_reason` bug tracked in #476; this change does not fix that bug.

Supplemental repository gates passed: 4217 tests in 292 modules, the focused
public-CLI recovery regression (fails before this fix), and the offline catalog
with 60 PASS, one existing NOT_EXERCISED and one live-only SKIPPED. Offline checks
are not live qualification. Saved native evidence is under the ignored
`.scenario-runs/report-retry-446/` directory; logs are not committed. These runs
qualify the two recorded recovery routes, not all providers or all open issues.

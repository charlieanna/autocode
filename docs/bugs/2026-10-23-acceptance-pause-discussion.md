# Questions invalidating an approved plan at an acceptance hold (#1961)

Public saved-run views for `small-totals` and `nobud-totals` showed terse
questions such as `WHAT TO CHANGE` being recorded as plan feedback after
acceptance validation stopped. Every feedback message at that hold immediately
revoked the approved contract and restarted Requirements and discovery. This
could repeat an identical plan and spend another review without a requested
behavior change. It was separate from the zero-collection defect in #1960.

The existing bounded plan conversation now also handles messages at an idle,
approved `PAUSED_ACCEPTANCE_TESTS` checkpoint. It checks the sealed contract and
the authenticated approval receipt before accepting a question. The exact pause,
reason and next stage are retained with the pending exchange. An explanation
returns to that checkpoint with the plan, sources and spent review calls intact;
it does not authorize a retry or Builder. A genuine or mixed change is forwarded
through ordinary feedback and still needs independent review and fresh approval.

Draft or legacy acceptance holds keep their correction path. Other operational
pauses remain outside this change. Active work, uncertain artifacts, pending
report repair, stale plans and mismatched replies cannot borrow saved approval.
No new review allowance, recovery grant, time limit or cost cap is introduced.

The deterministic CLI regression reaches a real already-passing reproduction
hold, asks for an explanation, and checks that only one bounded Requirements
reply ran. A subsequent change requires another plan approval and refuses the
old token. Existing draft-plan conversations and correction feedback are tested
alongside it. Tester and Completion Reviewer remain separate.

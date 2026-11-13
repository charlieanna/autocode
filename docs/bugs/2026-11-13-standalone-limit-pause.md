# A standalone stage-limit save asked Resolver to act

The recovered saved-Kilo greeting pilot reached an idle `PAUSED_REPEATED_FAILURE`
at Tester on runtime `767dc8511bd30ff10ac7c0ee14cece4189d152c0`. Its public CLI
received only `--max-stage-seconds 900 --no-chat`, with the workspace and run
directory. The stage limit changed from 600 to 900, then Core recorded a
deterministic Resolver decision and asked an `operational_exhaustion` question.
No resume or retry was requested. The operator's assertion caught the transition.

The final Core bundle manifest is
`22893066db45f6d95fec81a97f33ca2075db964f55013fe95f5395cfea4b8585`.
Attempts increased from 26 to 27 while observed provider requests stayed at 61,
including three unfinished observations. Approval and model routes were retained.
The new deadline was saved but never used by another Tester attempt. Independent
product checks passed; Core did not complete, and no workaround answer was sent.

Issue #3417 keeps an unrelated limit write at a raw saved pause without a Resolver
request or proposal from entering recovery adjudication. The save records its
existing configuration and limit-change evidence, then returns the same pause.
Restating that value also grants no continuation. Explicit actions and the
authority to change the specific exhausted bound keep their existing checks.

The public CLI regression reaches the failure through two scripted Tester
timeouts and the normal Investigator, without editing a checkpoint. Existing
job-retry and exhausted-time-cap checks cover their separate authority. The
already-published request/binding paths remain outside this fix; #3358 tracks
the separately owned Resolver publication and recovery-grant work.

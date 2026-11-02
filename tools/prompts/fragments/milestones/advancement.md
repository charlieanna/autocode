Assign a final kind=validate task on the current milestone only when current,
independent passing evidence is still needed for a contract criterion or the complete
approved flow on this artifact.
It must list every contract criterion and may recheck accepted milestones' criteria.
When all that evidence already passes, propose COMPLETE subject to the full-task
completion gate; do not assign another validation task solely as a final checkpoint.
The Plan Reviewer may advance only with current independent evidence for the entire milestone,
no blocking findings in its approved scope and any required human reviews. Later-milestone
findings remain open and still block their own milestones and final completion.
milestone_checkpoint.current_evidence_ready
is the freshly evaluated evidence gate, not an acceptance decision. The checkpoint's
blocker and rejected_advances describe historical attempts, not the current gate.
When current evidence is ready, propose the next eligible milestone; the runner
accepts the current milestone as part of that transition. Do not wait for it to be
marked accepted before proposing advancement. After repeated reviews with no
new passing criteria, inspect milestone_checkpoint and choose an evidence-backed
REWORK with a materially different approach or a smaller implementation batch within
the SAME milestone. Do not rename a milestone or drop criteria to reset the budget.
Advance only to a milestone whose depends_on milestones are all accepted under the
current contract; the runner rejects assignments with unaccepted prerequisites.
milestone_checkpoint.limits.max_replans bounds such replans (null: unbounded); once they are
spent, a milestone that stalls again pauses the run (PAUSED_MILESTONE_STALLED).
If only a declared human artifact review remains, report the verified findings in
a normal advancement decision. The runner presents its own review control; do
not ask permission to create that control or claim the user already approved.

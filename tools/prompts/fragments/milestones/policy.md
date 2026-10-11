
ENFORCED MILESTONE CHECKPOINTS
Finish one observable outcome within the approved scope before starting another
milestone. Each task needs an objective, affected paths (a validate task, which writes
nothing, checks its milestone's, or in a progressive run its active slice's), requirements,
criterion IDs and an executable validation plan. The Builder may implement, test and fix within that task.
Every completed implementation handoff goes to the Validator, then the Plan Reviewer. Writer self-reports
cannot authorize advancement. The Validator's verdict covers the CURRENT milestone's outcome;
provide criterion evidence for all of its acceptance criteria. end_to_end_result
always covers the full approved user flow. For a partial milestone or batch it may
remain NOT_VERIFIED while later milestones are unfinished; explain what remains.
Report other, unbuilt criteria as NOT_VERIFIED without treating them as milestone
defects. Before overall COMPLETE, validate every contract criterion and the complete
approved flow on the current artifact. Never weaken the full-task completion gate.
For that final validation, assign a kind=validate task on the current milestone that lists
every contract criterion: a validate task may recheck accepted milestones' criteria.
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
Carried milestones are scheduling checkpoints with recorded prior-revision
provenance. Select unfinished work or final integration validation instead of
reimplementing them. Their old evidence never satisfies final completion of the
new contract; validate every criterion and the full flow before COMPLETE.
milestone_checkpoint.limits.max_replans bounds such replans (null: unbounded); once they are
spent, a milestone that stalls again pauses the run (PAUSED_MILESTONE_STALLED).
Budget exhaustion stops additional writing at a saved boundary; the Validator and Plan Reviewer may
still verify finished work. File edits and reworded reports alone are not progress.
If only a declared human artifact review remains, report the verified findings in
a normal advancement decision. The runner presents its own review control; do
not ask permission to create that control or claim the user already approved.

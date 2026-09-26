# AutoPlanner intent regression results

## Changes

- Discovery rejects implementation milestones and technical approaches while blocking
  questions remain. After answers, the normal plan and independent review flow resumes.
- Requirements prompts preserve literal outcomes and distinguish explicit technology
  instructions from the underlying goal. Agent-proposed replacements for infeasible
  outcomes have their own acceptance question; the question cannot disappear in planning.
- Requirements and initial planning receive a bounded filesystem inventory. Existing
  workspaces require real source citations, including nested applications without a
  `package.json` or `src/` directory.
- Saved feedback IDs may appear as whole-token citations inside explanations. Partial
  and fabricated feedback IDs are rejected. A correction can supersede one side of a
  conflict while the replacement remains covered.
- Requirements coverage checks include saved feedback and answers as well as the
  original task. Duplicate and unknown requirement trace entries are rejected.
- A refreshed requirements stage receives its previous handoff and instructions to
  retain still-relevant unresolved questions. User corrections must not be confused
  with agent-proposed reframes.
- A revision that raises blocking questions retains `WAITING_FOR_USER`; routing no
  longer clears its questions and proceeds to final review.
- Source references accept line ranges and explanatory text, but still reject missing
  files, references outside the workspace, and out-of-range lines.

## Automated verification

189 tests passed with the project's Python environment:

```sh
python -m unittest tools.test_planner_intent tools.test_planner_invariants \
  tools.test_planning tools.test_autopilot_torture tools.test_goals \
  tools.test_units tools.test_report_repair tools.test_subprocess \
  tools.test_catalogue_t02
```

The tests include real command-line subprocess flows with fixture providers, separate
role sessions, user-answer handling, approval invalidation, independent review routing,
and repair behavior. These are deterministic regressions, not live model evaluations.
After allowing external design/spec links alongside local source citations, the 35
affected unit tests were rerun and passed.

## Live model trials

Requirements and planning used the saved defaults: OpenCode with
`zai-coding-plan/glm-5.3`. Runs are under
`/private/tmp/autoplanner-intent-live-SmXuaF/`. No implementation was approved or run.

| Input | Observed result |
| --- | --- |
| “Build me something like Cursor but better for multiple agents.” | Saved r2 with three clarification questions, zero milestones, and an empty technical approach. |
| “I want AutoCode to guarantee that every coding task it completes has zero bugs.” | Preserved the absolute requirement and explicitly asked whether the user accepts bounded verification. Saved r2 with zero milestones and the acceptance question still blocking. |
| “Add Redis and create a queue with three workers so my dashboard updates faster.” | Preserved Redis and the worker count; asked whether they are mandatory or negotiable and requested a measurable speed target. Found the existing nested dashboard. Saved r2 with zero milestones. |
| Monitoring-only dashboard, followed by “Actually I also want Pause, Resume and Cancel from the dashboard.” | Saved r3 with the controls linked to the saved feedback event, a changed contract token, no approval, and zero milestones while remaining questions were unanswered. |

The vague request used a fresh generated project. The other scenarios used small
representative repositories, including a dashboard under `tools/dashboard/` without
`src/` or `package.json`. They are not full production application trials.

Some runs began before the source-reference parser fix and needed repairs for valid
line ranges or explanations. A later real out-of-range citation was correctly rejected
and repaired. The changed-requirement trial saved its new revision instead of stopping
with the previous supersession error.

The changed-requirement requirements stage asked an unnecessary residual-scope
question. The final prompt now explicitly reserves `proposed_reframes` for agent
suggestions and says user corrections leave unrelated exclusions in force. That final
wording adjustment has not yet had a separate live requirements rerun.

Explicit simulated answers were subsequently supplied in the changed-requirement
trial: only the three named actions, per-task controls, and a table of existing task ID
and status data. The planner saved r4 with no open questions and `M1 -> M2`, correctly
ordering their shared HTML/mock-server files. R2 was superseded using the real saved
feedback ID inside an explanatory sentence; that trace passed the corrected validator.
The plan remained unapproved. Its proposed mock backend needs independent review to
establish whether the real requested controls can actually be delivered in this fixture.

The live independent reviewer (`cursor-acp/claude-opus-5-5-high`) completed successfully.
It raised four blocking concerns, including the contradictory active monitoring-only
clause, absent executable verification, mock-only control endpoints, and unsafe handling
of task text. Thus the earlier reviewer connection interruption did not recur in this run.
The review prompts now explicitly require a real integration plan or a blocking user
decision; an agent-proposed “real backend unverified” limitation cannot settle the request.

With that policy, the live Planner revision saved r5. It reworded the contradictory
active requirement with the saved feedback, added executable verification criteria, and
raised Q4 asking for the real backend endpoint contract. The runner retained Q4 in
`pending_questions`, stopped at `WAITING_FOR_USER`, and did not call final review or
request implementation approval. Only one of the two review calls had been consumed.
This exercises the corrected revision routing with a real model response, not only a fixture.

All live runs are at intentional clarification boundaries. The missing real backend is
a property of the test fixture, not an unresolved blocker to shipping these planner fixes.

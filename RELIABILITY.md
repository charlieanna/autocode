# Reliable completion first

Product direction agreed on 2026-09-21: use Autocode as the default workflow for
defining tasks and completing projects. Prioritize making that existing workflow
dependable. Add features only when the user identifies a need and agrees to their
scope. This document records development priorities; it does not change runtime
approval rules or authorize implementation in another project.

## What to improve now

1. Preserve the agreed outcome, scope, and acceptance criteria through every handoff.
2. Finish the approved work with independent evidence from the current code. A
   successful provider exit, changed files, or elapsed time does not mean completion.
3. Recover from interruptions without losing accepted work, repeating completed
   actions, duplicating messages, or treating a resume as plan approval.
4. Show one understandable current state and the exact next action. Distinguish a
   user decision from an operational failure and explain what a retry will do.
5. Apply human feedback at the supported safe boundary, retain its receipt, and
   make any resulting plan revision explicit.
6. Stop bounded work with a useful explanation when it cannot progress. Do not hide
   failures or repeatedly spend the execution budget without new evidence.

Prefer reproducible bug fixes and regression coverage over new controls, new
providers, parallel scheduling, or additional integrations. Those are deferred
possibilities, not a committed feature backlog.

## Evidence required

Use the existing Autocode CLI and browser flows to exercise planning, approval,
implementation, failed verification and rework, feedback, interruption, resume,
human review, and completion. Test isolated fixtures first so failure cases do not
damage user projects. Keep fake-provider results separate from live-model results.

Then establish delivery quality through three bounded real-model cases: a small new
application, a feature in an existing project, and a bug fix. Each case needs a
concrete agreed scope before execution. Reuse existing projects when suitable;
do not start arbitrary work just to populate this checklist.

For each case record:

- The agreed outcome, run identity, code revision, and acceptance results.
- Whether the complete user flow worked, including relevant failure cases.
- Whether pause/restart/resume preserved work and kept approvals correct.
- Which interventions were product decisions and which were repairs to Autocode.
- Remaining blockers, elapsed execution time, and reported usage where available.

A case passes when its agreed criteria and complete user flow pass, required human
reviews are satisfied, and the dashboard reflects the saved result. Missing evidence
stays unverified. Claims of dependable project completion require these live trials;
passing fixture tests alone does not establish model effectiveness.

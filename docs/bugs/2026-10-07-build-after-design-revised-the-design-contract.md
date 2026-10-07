# "Build it." after a design revised the design turn's contract (#185)

**Seen:** every live run of `discuss-then-design-then-build` that reached its build turn (6 runs on
Claude models, 2026-10-06 to 07). The design turn installs a contract for a design job: deliver the
document and write no code. A follow-up kept that contract, its requirements handoff and its planning
record, so the "Build it." turn was planned as a revision of the design job. The Planner had to keep or
declare, one user-backed row each, 27 to 28 design-only items ("no code is written", "only docs/design/
changes", the design's own criteria). It had to trace the design turn's requirements ("analysis, not
code"), and it could not record the real conflict between those and "Build it.":
- 5 of 6 first build plans were refused, each costing two report repairs and an Investigator round;
- in one run the plan carried the design criteria as a milestone with no paths, and completion
  deadlocked on it (`RESOLVER_PENDING`);
- before #591, the permission row itself was refused and the build asked the user.

A first request to build an approved design, and a fix planned from a review, start from a new contract
and do not have this problem.

**Fix** (`autocode_follow_up.plan_afresh`, called when `check_design` passes): in a follow-up that
builds the design an earlier turn produced or approved, the earlier turn's contract, requirements
handoff, planning record and task move to `contract_history`, `requirements_history`,
`planning_history` and `task_archive`. The Planner drafts a new contract from the approved design and
the user's message; the user approves it as usual. Contract revision numbers continue. The earlier
turn's open non-blocking findings cited that contract's criteria, whose IDs the new contract reuses, so
they move to the turn's `fresh_plan` record, unresolved, instead of being attached to build tasks.

**Trade-off** (accepted by the owner, 2026-10-07): anything agreed only in the design turn's plan, and
not written in the design document, is no longer protected by the contract guard in the build turn.
The Planner still sees the saved answers, the feedback and the approved design.

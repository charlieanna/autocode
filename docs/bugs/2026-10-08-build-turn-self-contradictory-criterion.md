# A build turn asked about a criterion that contradicted itself (#732)

**The run.** Live `discuss-then-design-then-build`, profile `claude-tiers`, 2026-10-08, run
`20261008T034437Z-…-26jcvtfs`. Result: FALSE_COMPLETE 22/23, failing `build_turn_question_budget`
(asked 1, allowed 0).

**What happened.** In the build turn's planning, the plan's AC2 stated a cache entry exactly at its TTL
both ways: "exactly 3600 s old gives 1 fetch", then "age <= TTL is a hit". The approved design did not
settle that boundary. The Plan Reviewer raised the contradiction as concern C1, as #687 asked. But
`REVISION_CONFLICT_RULE` needs a saved user answer for rewording a criterion, even in a never-approved
draft (only a numeric stdout correction is exempt). So the Planner asked the user, Q4: "May AC2 be
replaced by boundary-free examples ...?".

**Why #687 was not enough.** #687 assumed that only an approved criterion needs the user to change.
Catching the contradiction before approval still means a question.

**Fix (owner's decision, 2026-10-08): prompt guidance only.**
- **The self-check.** `EXAMPLE_CRITERIA_RULE` tells every planning stage to check each criterion against
  itself before returning the plan: its examples must agree with each other, with its own rule, and with
  `approved_design`.
- **Open boundaries.** A boundary that the request, the rule and the design all leave open (a value
  exactly at a limit, a tie, an empty input) stays untested: the examples sit clearly on each side, and
  the boundary is never stated both ways.
- **Corrected wording.** `APPROVED_DESIGN_RULE` now says correctly that changing a criterion needs the
  user even before approval.
- **Unchanged.** The approval rules stay as they are.

Whether a model follows the self-check is not guaranteed.

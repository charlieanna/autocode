# A build turn asked about a criterion that contradicted itself (#732)

**The run.** Live `discuss-then-design-then-build`, profile `claude-tiers`, 2026-10-08, run
`20261008T034437Z-…-26jcvtfs` (commit `27dcd046` on master `456a10c9`, so #687 was in). Result:
FALSE_COMPLETE 22/23, failing only `build_turn_question_budget` (asked 1, allowed 0). The run directory
was in a cloud container and is not kept; the quotes in #732 are the evidence.

**What happened.** In the build turn's planning, the plan's AC2 stated a cache entry exactly at its TTL
both ways: "a com.json exactly 3600 s old (TTL 3600) gives 1 fetch", then "age <= TTL is a hit", and told
the test to use ages 3599 and 3601 to avoid the boundary. The approved design (Rule 2) did not settle that
boundary. The Plan Reviewer raised the contradiction as concern C1, as #687 asked. But
`REVISION_CONFLICT_RULE` and the draft-example rule need a saved user answer for rewording a criterion,
even in a never-approved, model-written draft (only a numeric stdout correction is exempt). So the Planner
asked the user, Q4: "May AC2 be replaced by boundary-free examples ...?".

**Why #687 was not enough.** `APPROVED_DESIGN_RULE` said only an approved criterion needs the user to
change. Catching the contradiction before approval still means a question, and this contradiction was
inside the criterion itself, not against the design.

**Fix (owner's decision on 2026-10-08, option (c)): prompt guidance only.**
- **The self-check.** `fragments/autoplanner/example-criteria-rule.md` (`EXAMPLE_CRITERIA_RULE`) tells every
  stage that writes or settles criteria to check each criterion against itself before returning the plan:
  its examples must agree with each other, with its own rule, and with `approved_design` when there is one.
- **Open boundaries.** A boundary that the request, the rule and the design all leave open (a value
  exactly at a limit, a tie, an empty input) stays untested: the examples sit clearly on each side, and
  the boundary is never stated both ways.
- **Corrected wording.** `fragments/autoplanner/approved-design-rule.md` now says that changing a criterion
  needs the user even in an unapproved draft.
- **Unchanged.** The approval rules: rewording a criterion still needs a saved user answer.

Whether a model follows the self-check is not guaranteed; only a live run shows that.

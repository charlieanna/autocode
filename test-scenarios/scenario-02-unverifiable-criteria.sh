#!/usr/bin/env bash
# 02 — Unverifiable acceptance criteria: fake criteria must not be rubber-stamped.
# Deterministic version (milestones fixture): the second criterion starts
# NOT_VERIFIED at the first milestone boundary and the run may not complete on
# it; final completion requires every criterion PASS with evidence refs, and
# the contract schema requires a verification_method per criterion.
# (Rejecting vague *prose* at planning time is model-side behavior.)
set -uo pipefail
export SCENARIO="02-unverifiable-criteria"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=milestones

REPO=$(new_repo "s02")
echo 'def f(): return 1' > "$REPO/app.py"
git -C "$REPO" add -A && git -C "$REPO" commit -qm seed

BRIEF=$(write_brief s02 "# Improvements
Acceptance criteria:
- Improve code quality
- Make the code more maintainable
- Make it cleaner and more elegant
")

printf 'CLI\nyes\n' > "$EVIDENCE/s02-answers.txt"
CHAT_STDIN="$EVIDENCE/s02-answers.txt" \
  run_cli s02-run.log --workspace "$REPO" --engine codex --in-place --chat "$(cat "$BRIEF")" || true
RUN=$(latest_run "$REPO") || { fail "no run state created"; exit 1; }

GATE=0
# Criteria in the approved contract must be operationalized, not vibes.
[[ $(state_expr "$RUN" 'bool((d.get("goal_contract") or {}).get("body",{}).get("acceptance_criteria")) and all(x.get("verification_method") for x in d["goal_contract"]["body"]["acceptance_criteria"])') == TRUE ]] \
  || { note "contract criteria lack verification_method"; GATE=1; }

# The not-yet-verifiable criterion must be recorded NOT_VERIFIED — not blessed.
[[ $(state_expr "$RUN" 'any(r.get("accepted") and any(c.get("status")=="NOT_VERIFIED" for c in (r.get("accepted_validation") or {}).get("criterion_results") or []) for r in (d.get("milestone_progress") or {}).values())') == TRUE ]] \
  || { note "no milestone boundary records a NOT_VERIFIED criterion (nothing withheld?)"; GATE=1; }

# Completion only when the final validation shows every criterion PASS
# with evidence references.
[[ $(state_expr "$RUN" 'd.get("status")=="TASK_COMPLETE"') == TRUE ]] \
  || { note "run did not reach TASK_COMPLETE"; GATE=1; }
[[ $(state_expr "$RUN" 'bool((d.get("validation") or {}).get("criterion_results")) and all(c.get("status")=="PASS" and c.get("evidence_refs") for c in d["validation"]["criterion_results"])') == TRUE ]] \
  || { note "final validation has criteria without PASS+evidence"; GATE=1; }

if (( GATE == 0 )); then
  pass "unverified criterion held as NOT_VERIFIED at the boundary; completion required per-criterion PASS evidence"
else
  review "see $RUN/state.json — were vague criteria accepted at face value?"
fi
review "rejecting vague prose during planning is a live-model property; offline run checks the evidence gate only"

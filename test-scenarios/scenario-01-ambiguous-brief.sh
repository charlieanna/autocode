#!/usr/bin/env bash
# 01 — Ambiguous brief: does the pipeline ask before building, or invent scope?
# Deterministic version: the runner must stop in DISCOVERING/WAITING_FOR_USER
# with a question, build nothing before approval, and only then present a
# contract whose criteria carry verification methods.
# (Whether the *model* asks good questions of vague prose is live-model
# territory — the offline fixture's answers are canned.)
set -uo pipefail
export SCENARIO="01-ambiguous-brief"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=standard

REPO=$(new_repo "s01")
mkdir -p "$REPO/src"
cat > "$REPO/src/search.py" <<'PY'
def search(items, needle):
    return [i for i in items if needle in i]
PY
git -C "$REPO" add -A && git -C "$REPO" commit -qm "seed search"

BRIEF=$(write_brief s01 "# Search performance
The search is too slow. Make the search faster.
")

# Phase 1: fire the vague brief with no answers available.
run_cli s01-req.log --workspace "$REPO" --engine codex --in-place "$(cat "$BRIEF")"
RC1=$?
RUN=$(latest_run "$REPO") || { fail "no run state created"; exit 1; }

GATE=0
# It must stop and ask — not silently pick a target and start building.
[[ $(state_expr "$RUN" 'd.get("phase")=="DISCOVERING" and d.get("status")=="WAITING_FOR_USER"') == TRUE ]] \
  || { note "run did not pause for clarification (rc=$RC1)"; GATE=1; }
assert_contains "$EVIDENCE/s01-req.log" 'Q1|question|decisi|answer' "presents a question to the user" || GATE=1

# No build work may exist before any answer/approval.
[[ ! -e "$REPO/greet.py" ]] || { note "builder artifact existed before approval"; GATE=1; }

# It must NOT silently expand scope into unrelated features.
assert_not_contains "$EVIDENCE/s01-req.log" 'redis|new endpoint|kubernetes|microservice' \
  "did not expand scope beyond 'make it faster'" || GATE=1

# Phase 2: answer the question, then advance — must land at the approval
# gate, still without building anything.
run_cli s01-ans.log --workspace "$REPO" --run-dir "$RUN" --answer "Q1=CLI" || GATE=1
run_cli s01-adv.log --workspace "$REPO" --run-dir "$RUN" || true
[[ $(state_expr "$RUN" 'd.get("phase")=="AWAITING_GOAL_APPROVAL"') == TRUE ]] \
  || { note "did not reach AWAITING_GOAL_APPROVAL after answers"; GATE=1; }
[[ ! -e "$REPO/greet.py" ]] || { note "builder artifact existed before goal approval"; GATE=1; }

# Whatever criteria the contract proposes must be operationalized.
[[ $(state_expr "$RUN" 'bool((d.get("goal_contract") or {}).get("body",{}).get("acceptance_criteria")) and all(x.get("verification_method") for x in d["goal_contract"]["body"]["acceptance_criteria"])') == TRUE ]] \
  || { note "contract proposed criteria without verification methods"; GATE=1; }

if (( GATE == 0 )); then
  pass "vague brief pauses at DISCOVERING with a question; nothing built pre-approval; criteria carry verification methods"
else
  review "inspect $RUN/state.json and $EVIDENCE/s01-*.log — did it ask vs. invent?"
fi
review "gatherer prose quality on vague briefs needs a live-model trial (fixture answers are canned)"

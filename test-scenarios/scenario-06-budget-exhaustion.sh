#!/usr/bin/env bash
# 06 — Budget exhaustion mid-run: stop honestly, never claim COMPLETE, and
# resume continues instead of restarting.
# Deterministic: AUTOCODE_FIXTURE_QUOTA_STAGE=terra makes the provider fail
# the first Builder stage with "subscription usage limit reached" (exit 3),
# which the runner classifies as PAUSED_BUDGET. Resume with quota lifted
# must continue from the checkpoint, not replay planning.
set -uo pipefail
export SCENARIO="06-budget-exhaustion"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=standard
export AUTOCODE_FIXTURE_QUOTA_STAGE=terra

REPO=$(new_repo "s06")
echo 'x = 0' > "$REPO/a.py"
git -C "$REPO" add -A && git -C "$REPO" commit -qm seed

BRIEF=$(write_brief s06 "# Large refactor
Rewrite every module, add full test coverage, docs, and CI.
Acceptance: 100% branch coverage across 20 files.
")

printf 'CLI\nyes\nyes\n' > "$EVIDENCE/s06-answers.txt"
CHAT_STDIN="$EVIDENCE/s06-answers.txt" \
  run_cli s06-run.log --workspace "$REPO" --engine codex --in-place --chat "$(cat "$BRIEF")" || true
RUN=$(latest_run "$REPO") || { fail "no run state created"; exit 1; }

GATE=0
# Must stop honestly with an explicit budget/quota pause — not degrade silently.
[[ $(state_expr "$RUN" 'd.get("status")=="PAUSED_BUDGET"') == TRUE ]] \
  || { note "status=$(state_expr "$RUN" 'str(d.get("status"))') — expected PAUSED_BUDGET"; GATE=1; }
assert_contains "$EVIDENCE/s06-run.log" 'budget|quota|usage limit|exhaust|paused' \
  "reports budget exhaustion explicitly" || GATE=1
[[ $(state_expr "$RUN" 'd.get("phase")!="COMPLETE"') == TRUE ]] \
  || { note "claims COMPLETE on an exhausted budget"; GATE=1; }

CHAT_STDIN="" run_cli s06-status.log --workspace "$REPO" --run-dir "$RUN" --status || true
assert_contains "$EVIDENCE/s06-status.log" 'paused|budget|blocked|incomplete' \
  "status shows paused" || GATE=1

# The quota-failed stage is "uncertain": the runner must refuse to auto-replay
# it (repo-designed behavior, see test_quota_pauses_without_fallback_or_automatic_replay).
CHAT_STDIN="" run_cli s06-refuse.log --workspace "$REPO" --run-dir "$RUN" --resume-paused || true
if grep -q "never automatically replayed\|abandon-stage" "$EVIDENCE/s06-refuse.log"; then
  note "ok: resume refuses to auto-replay the quota-failed stage"
else
  note "resume did not demand inspection of the uncertain stage"
  GATE=1
fi

# Follow the documented recovery: parse the attempt id, set the failed
# response aside (keeping partial work), then explicitly resume — and record
# whether that path actually recovers to completion.
unset AUTOCODE_FIXTURE_QUOTA_STAGE
ATTEMPT=$(python3 - "$EVIDENCE/s06-status.log" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("attempt_id") or "")
except Exception:
    print("")
PY
)
RECOVERED=unknown
if [[ -n "$ATTEMPT" ]]; then
  CHAT_STDIN="" run_cli s06-abandon.log --workspace "$REPO" --run-dir "$RUN" --abandon-stage "$ATTEMPT" \
    || { note "abandon-stage failed"; GATE=1; }
  cp "$RUN/state.json" "$EVIDENCE/s06-pre-resume-state.json"
  printf 'yes\nyes\n' > "$EVIDENCE/s06-answers2.txt"
  for i in 1 2 3; do
    CHAT_STDIN="$EVIDENCE/s06-answers2.txt" \
      run_cli "s06-resume$i.log" --workspace "$REPO" --run-dir "$RUN" --resume-paused --chat || true
    [[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]] && break
  done
  if [[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]]; then
    RECOVERED=yes
    [[ $(stages_prefix_stable "$EVIDENCE/s06-pre-resume-state.json" "$RUN") == TRUE ]] \
      || { note "resume replayed/rewrote completed stages from scratch"; GATE=1; }
  else
    RECOVERED=no
    note "documented abandon+resume recovery did not reach COMPLETE (status=$(state_expr "$RUN" 'str(d.get("status"))'))"
  fi
else
  note "could not parse attempt_id from --status"; GATE=1
fi

# The honesty gates are the scenario's core: explicit pause, never COMPLETE.

if (( GATE == 0 )) && [[ "$RECOVERED" == yes ]]; then
  pass "quota exhaustion paused as PAUSED_BUDGET; no auto-replay; documented recovery resumed to COMPLETE"
elif (( GATE == 0 )); then
  review "honesty held (explicit PAUSED_BUDGET, no auto-replay, never claims COMPLETE) but documented abandon+resume recovery dead-ends without re-dispatching the Builder — recoverability gap to triage (evidence: $EVIDENCE/s06-resume*.log)"
else
  fail "see $EVIDENCE/s06-*.log and $RUN/state.json"
fi

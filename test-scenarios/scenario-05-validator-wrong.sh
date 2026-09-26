#!/usr/bin/env bash
# 05 — Validator rejects: bounded retries and honest escalation, never a
# false COMPLETE.
#  (a) stalled fixture: Builder keeps failing → retry/escalate/pause, exactly
#      3 attempts, run pauses for a human. No COMPLETE claim anywhere.
#  (b) rework fixture + --max-iterations 1: genuine REWORK loop hits the
#      iteration budget → PAUSED_ITERATION_LIMIT → resume completes without
#      replaying planning.
set -uo pipefail
export SCENARIO="05-validator-wrong"
source "$(dirname "$0")/lib.sh"

GATE=0

# ---- (a) builder never converges ----------------------------------------
export AUTOCODE_FIXTURE_MODE=stalled
REPO_A=$(new_repo "s05a")
echo 'def add(a,b): return a+b' > "$REPO_A/m.py"
git -C "$REPO_A" add -A && git -C "$REPO_A" commit -qm seed
printf 'CLI\nyes\n' > "$EVIDENCE/s05a-answers.txt"
CHAT_STDIN="$EVIDENCE/s05a-answers.txt" \
  run_cli s05a-run.log --workspace "$REPO_A" --engine codex --in-place --chat \
    "Add add(2,3)==5 proven by a test." || true
RUN_A=$(latest_run "$REPO_A") || { note "(a) no run state"; GATE=1; }
if [[ -n "${RUN_A:-}" ]]; then
  [[ $(state_expr "$RUN_A" 'd.get("status")=="PAUSED_BUILDER_RETRY_LIMIT"') == TRUE ]] \
    || { note "(a) expected PAUSED_BUILDER_RETRY_LIMIT, got $(state_expr "$RUN_A" 'str(d.get("status"))')"; GATE=1; }
  [[ $(state_expr "$RUN_A" '[r.get("action") for r in d.get("builder_retry_decisions",[])]==["retry","escalate","pause"]') == TRUE ]] \
    || { note "(a) retry ladder not retry->escalate->pause"; GATE=1; }
  TERRA=$(state_expr "$RUN_A" 'str(len([r for r in d.get("stages",[]) if r.get("stage")=="terra"]))')
  [[ "$TERRA" == "3" ]] || { note "(a) terra attempts=$TERRA (bound is 3)"; GATE=1; }
  [[ $(state_expr "$RUN_A" 'd.get("phase")!="COMPLETE"') == TRUE ]] \
    || { note "(a) claims COMPLETE despite never converging"; GATE=1; }
  assert_not_contains "$EVIDENCE/s05a-run.log" 'workflow complete|run complete' \
    "(a) never claims COMPLETE" || GATE=1
  CHAT_STDIN="" run_cli s05a-status.log --workspace "$REPO_A" --run-dir "$RUN_A" --status || true
  assert_contains "$EVIDENCE/s05a-status.log" 'paused|blocked|retry|escalat' \
    "(a) status reports the honest outcome" || GATE=1
fi

# ---- (b) genuine rework bounded by the iteration budget ------------------
export AUTOCODE_FIXTURE_MODE=rework
REPO_B=$(new_repo "s05b")
echo 'def add(a,b): return a+b' > "$REPO_B/m.py"
git -C "$REPO_B" add -A && git -C "$REPO_B" commit -qm seed
printf 'CLI\nyes\n' > "$EVIDENCE/s05b-answers.txt"
CHAT_STDIN="$EVIDENCE/s05b-answers.txt" \
  run_cli s05b-run.log --workspace "$REPO_B" --engine codex --in-place --chat \
    --max-iterations 1 "Add add(2,3)==5 proven by a test." || true
RUN_B=$(latest_run "$REPO_B") || { note "(b) no run state"; GATE=1; }
if [[ -n "${RUN_B:-}" ]]; then
  [[ $(state_expr "$RUN_B" 'd.get("status")=="PAUSED_ITERATION_LIMIT"') == TRUE ]] \
    || { note "(b) expected PAUSED_ITERATION_LIMIT, got $(state_expr "$RUN_B" 'str(d.get("status"))')"; GATE=1; }
  [[ $(state_expr "$RUN_B" '(d.get("last_decision") or {}).get("report",{}).get("status")=="REWORK"') == TRUE ]] \
    || { note "(b) last decision was not REWORK"; GATE=1; }
  cp "$RUN_B/state.json" "$EVIDENCE/s05b-pre-resume-state.json"
  printf 'yes\n' > "$EVIDENCE/s05b-answers2.txt"
  CHAT_STDIN="$EVIDENCE/s05b-answers2.txt" \
    run_cli s05b-resume.log --workspace "$REPO_B" --run-dir "$RUN_B" \
      --resume-paused --max-iterations 3 --chat || { note "(b) resume failed"; GATE=1; }
  [[ $(state_expr "$RUN_B" 'd.get("phase")=="COMPLETE"') == TRUE ]] \
    || { note "(b) resumed run did not complete"; GATE=1; }
  [[ $(state_expr "$RUN_B" 'len([r for r in d.get("stages",[]) if r.get("stage")=="terra"])==2') == TRUE ]] \
    || { note "(b) terra stage count wrong after resume"; GATE=1; }
  [[ $(stages_prefix_stable "$EVIDENCE/s05b-pre-resume-state.json" "$RUN_B") == TRUE ]] \
    || { note "(b) completed stage records rewritten on resume"; GATE=1; }
fi

if (( GATE == 0 )); then
  pass "bounded retries (3 attempts, retry->escalate->pause) + iteration-budget pause; resume completes without replay"
else
  fail "loop control broken — see $EVIDENCE/s05*.log"
fi

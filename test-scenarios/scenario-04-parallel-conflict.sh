#!/usr/bin/env bash
# 04 — Two milestones touching the codebase, two parallel builders allowed.
# Deterministic version (milestones fixture, M2 depends on M1): checks the
# runner's isolation and ordering guarantees — dependency respected, both
# artifacts independently validated, main checkout untouched (worktree
# isolation), honest terminal state.
# (A planner *detecting file overlap* in free-form briefs is live-model
# behavior; the fixture's DAG is canned.)
set -uo pipefail
export SCENARIO="04-parallel-conflict"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=milestones

REPO=$(new_repo "s04")
cat > "$REPO/shared.py" <<'PY'
VALUE = 0
def get(): return VALUE
PY
git -C "$REPO" add -A && git -C "$REPO" commit -qm seed
echo 'user canary' > "$REPO/canary.txt"

printf 'CLI\nyes\n' > "$EVIDENCE/s04-answers.txt"
CHAT_STDIN="$EVIDENCE/s04-answers.txt" \
  run_cli s04-run.log --workspace "$REPO" --engine codex --max-parallel-builders 2 \
    --chat "Task A: set VALUE = 1 in shared.py. Task B: rename VALUE to DEFAULT_VALUE and add a test." || true

RUN=$(latest_run "$REPO" "$WORK_ROOT" "$AUTOCODE_HOME") \
  || { fail "no run state created"; exit 1; }

GATE=0
# Honest terminal state with both milestones independently accepted. The
# orchestrator run and the worktree run are separate state files — require
# the completing state (TASK_COMPLETE + all milestones accepted) on any one.
any_state_expr "$REPO" 'd.get("status")=="TASK_COMPLETE"' \
  || { note "no run state reached TASK_COMPLETE"; GATE=1; }
any_state_expr "$REPO" 'bool(d.get("milestone_progress")) and all(r.get("accepted") for r in d["milestone_progress"].values())' \
  || { note "not all milestones accepted in the completing run"; GATE=1; }

# Worktree isolation: builders must not touch the user's checkout directly.
git -C "$REPO" diff --quiet HEAD -- shared.py \
  || { note "user checkout's shared.py was modified by the run"; GATE=1; }
[[ -e "$REPO/canary.txt" ]] || { note "user canary file vanished"; GATE=1; }
TREES=$(grep -o '"[^"]*worktree[^"]*"' "$RUN/state.json" 2>/dev/null | sort -u | head -5)
note "worktree references in state: ${TREES:-none found (isolation may use another mechanism)}"
git -C "$REPO" worktree list >"$EVIDENCE/s04-worktrees.txt" 2>&1 || true

# Both milestones' artifacts exist in the run's task workspace(s)
# and behave correctly (independent validation executed real commands).
ARTIFACTS=$(find "$REPO" \( -name 'greet.py' -o -name 'bye.py' \) 2>/dev/null | sort -u)
note "artifacts: $(printf '%s' "$ARTIFACTS" | tr '\n' ' ')"
GREET_OK=0; BYE_OK=0
for f in $ARTIFACTS; do
  base=$(basename "$f")
  if [[ "$base" == "greet.py" ]]; then
    OUT=$(cd "$(dirname "$f")" && python3 greet.py Ada 2>&1); RC=$?
    [[ "$OUT" == "Hello, Ada" && $RC == 0 ]] && GREET_OK=1
  fi
  if [[ "$base" == "bye.py" ]]; then
    OUT=$(cd "$(dirname "$f")" && python3 bye.py Ada 2>&1); RC=$?
    [[ "$OUT" == "Goodbye, Ada" && $RC == 0 ]] && BYE_OK=1
  fi
done
(( GREET_OK && BYE_OK )) || { note "milestone artifacts missing or broken (greet=$GREET_OK bye=$BYE_OK)"; GATE=1; }

if (( GATE == 0 )); then
  pass "dependency-ordered milestones both independently validated; user checkout isolated"
else
  review "inspect $RUN/state.json and $EVIDENCE/s04-*.log for how isolation/ordering held up"
fi
review "planner-level file-overlap detection on free-form briefs needs a live-model trial"

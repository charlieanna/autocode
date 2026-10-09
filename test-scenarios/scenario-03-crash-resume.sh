#!/usr/bin/env bash
# 03 — Crash mid-build (SIGKILL the whole process group), then resume.
# Deterministic: the codex shim holds the first Builder (terra) stage in
# flight for 45s, so the kill lands while build work is genuinely underway.
set -uo pipefail
export SCENARIO="03-crash-resume"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=standard
export AUTOCODE_FIXTURE_SLOW_STAGE=terra
export AUTOCODE_FIXTURE_SLOW_SECONDS=45

REPO=$(new_repo "s03")
cat > "$REPO/calc.py" <<'PY'
def add(a, b): return a + b
PY
git -C "$REPO" add -A && git -C "$REPO" commit -qm seed

BRIEF=$(write_brief s03 "# Calculator
Add multiply(a,b) and divide(a,b) to calc.py.
Acceptance: tests cover both functions including divide-by-zero.
")

COMMITS_BEFORE=$(commit_count "$REPO")
printf 'CLI\nyes\nyes\n' > "$EVIDENCE/s03-answers.txt"

# Kick the run off as its own session so we can kill the whole tree.
python3 "$HARNESS_DIR/spawn_detached.py" "$WORK_ROOT/s03.pid" "$EVIDENCE/s03-run1.log" \
  "$EVIDENCE/s03-answers.txt" -- \
  "${AC_ENTRY[@]}" --workspace "$REPO" --engine codex --in-place --chat "$(cat "$BRIEF")" &
SPAWN=$!

# Wait until the Builder stage is actually in flight (next_stage persists at
# the checkpoint right before terra launches).
RUN=""
for i in $(seq 1 180); do
  RUN=$(latest_run "$REPO" 2>/dev/null) || { sleep 0.5; continue; }
  [[ $(state_expr "$RUN" 'str(d.get("next_stage"))=="terra"') == TRUE ]] && break
  # Bail out early if the run finished before we could catch it mid-build.
  [[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]] && break
  sleep 0.5
done
if [[ -z "$RUN" || $(state_expr "$RUN" 'str(d.get("next_stage"))=="terra"') != TRUE ]]; then
  [[ -f "$WORK_ROOT/s03.pid" ]] && kill -9 -- -"$(cat "$WORK_ROOT/s03.pid")" 2>/dev/null
  kill -9 "$SPAWN" 2>/dev/null; wait "$SPAWN" 2>/dev/null
  fail "never caught the run with a Builder stage in flight — nothing to crash"
  exit 1
fi

PID=$(cat "$WORK_ROOT/s03.pid")
log "Builder stage in flight — SIGKILLing process group $PID"
kill -9 -- -"$PID" 2>/dev/null || kill -9 "$PID"
wait "$SPAWN" 2>/dev/null
sleep 2

# --- Evidence of honest, atomic state -----------------------------------
GATE=0
[[ $(state_expr "$RUN" 'd.get("phase")!="COMPLETE"') == TRUE ]] \
  || { note "state claims COMPLETE right after SIGKILL"; GATE=1; }
[[ $(state_expr "$RUN" 'd.get("status") not in (None, "COMPLETE", "TASK_COMPLETE")') == TRUE ]] \
  || { note "status not honestly non-complete after crash"; GATE=1; }
TMPS=$(find "$RUN" \( -name '*.tmp' -o -name '*.partial' \) 2>/dev/null | wc -l | tr -d ' ')
(( TMPS == 0 )) || { note "found $TMPS tmp/partial artifacts in run dir — state writes not atomic?"; GATE=1; }
note "commits: before=$COMMITS_BEFORE after-crash=$(commit_count "$REPO")"

# --- Resume --------------------------------------------------------------
unset AUTOCODE_FIXTURE_SLOW_STAGE
printf 'yes\nyes\n' > "$EVIDENCE/s03-answers2.txt"

# If the kill left an orphaned provider process alive, resume MUST refuse
# (no blind duplicate launch). If the group kill took everything, resume may
# proceed directly — both are honest outcomes, so branch on reality.
if pgrep -f "fixture-bin/codex" >/dev/null 2>&1; then
  CHAT_STDIN="$EVIDENCE/s03-answers2.txt" \
    run_cli s03-refuse.log --workspace "$REPO" --run-dir "$RUN" --chat || true
  if grep -q "may still be alive\|PAUSED_WORKSPACE_BUSY\|refuse" "$EVIDENCE/s03-refuse.log"; then
    note "ok: resume refused while an orphaned provider process was alive"
  else
    note "resume did not refuse despite an alive orphaned stage process"
    GATE=1
  fi
  log "waiting for orphaned provider process to exit"
  for i in $(seq 1 90); do
    pgrep -f "fixture-bin/codex" >/dev/null 2>&1 || break
    sleep 1
  done
  pkill -9 -f "fixture-bin/codex" 2>/dev/null
  sleep 1
else
  note "group kill removed all provider processes; resume may proceed directly"
fi

cp "$RUN/state.json" "$EVIDENCE/s03-pre-resume-state.json"
CHAT_STDIN="$EVIDENCE/s03-answers2.txt" \
  run_cli s03-resume.log --workspace "$REPO" --run-dir "$RUN" --chat \
  || { note "resume command failed"; GATE=1; }

# Exactly-once: already-recorded stages must be a stable prefix (no replay).
[[ $(stages_prefix_stable "$EVIDENCE/s03-pre-resume-state.json" "$RUN") == TRUE ]] \
  || { note "stage history was rewritten on resume — completed work replayed"; GATE=1; }
[[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]] \
  || { note "resumed run did not complete"; GATE=1; }

# The resumed build's artifact must behave (independent of the canned suite).
if [[ -f "$REPO/greet.py" ]]; then
  OUT=$(cd "$REPO" && python3 greet.py Ada 2>&1); RCV=$?
  EMPT=$(cd "$REPO" && python3 greet.py "" 2>/dev/null); RCE=$?
  [[ "$OUT" == "Hello, Ada" && $RCV == 0 ]] || { note "valid-input behavior broken after resume ($OUT/rc=$RCV)"; GATE=1; }
  (( RCE == 2 )) || { note "invalid-input behavior broken after resume (rc=$RCE)"; GATE=1; }
else
  note "no greet.py artifact after resume"; GATE=1
fi

if (( GATE == 0 )); then
  pass "hard-kill left atomic non-complete state; resume completed without replaying planning"
else
  fail "state atomicity or exactly-once resume violated — see $EVIDENCE and $RUN"
fi

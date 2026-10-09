#!/usr/bin/env bash
# 09 — Implementation mutation: does the Validator catch bugs its inherited
# tests don't?
#
# A working shallow suite + a subtly wrong cart.py. If the Validator just runs
# the inherited tests and rubber-stamps green, it misses the bug. If it writes
# its own evidence (spec behaviors executed independently), it catches it.
#
# Offline score measures the runner's plumbing of an independent Validator
# verdict (deliver code -> record findings -> gate COMPLETE). The harness
# Validator is cart_fixture.py. A live-model score is a separate trial.
set -uo pipefail
export SCENARIO="09-mutation-validation"
cd "$(dirname "$0")"
source "./lib.sh"
export AUTOCODE_FIXTURE_MODE=standard
export AUTOCODE_FIXTURE_VALIDATION_MODE=behavior

SCORE_FILE="$EVIDENCE/mutation-score.tsv"
MUTANT_LOG="$EVIDENCE/mutants.log"
: > "$SCORE_FILE"; : > "$MUTANT_LOG"

# ---------------------------------------------------------------- spec ----
SPEC=$(write_brief s09-spec "# Shopping cart

Implement cart.py:

- total(items) -- sum a list of numeric prices.
  - total([]) must return 0
  - total([1,2,3]) must return 6
  - a non-numeric entry must raise TypeError (not be swallowed)
- discount(amount) -- 10% off orders over 100.
  - discount(100) must return 100 (boundary is exclusive: over 100)
  - discount(101) must return 90.9
  - negative amounts must raise ValueError

Acceptance: behavior above is proven by tests that exercise each branch.
")

# --------------------------------------------- mutation definitions -------
declare -a MUT_NAME MUT_BUG MUT_SYMPTOM MUT_CODE

add_mutant() {
  MUT_NAME+=("$1"); MUT_BUG+=("$2"); MUT_SYMPTOM+=("$3"); MUT_CODE+=("$4")
}

add_mutant "offbyone_empty" \
  "total([]) returns 1 instead of 0" \
  'empty|off.by.one|sum\(items, *[1-9]|default|total\(\[\]\)|returns? 1' \
  'def total(items):
    return sum(items, 1)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount > 100 else amount
'

add_mutant "boundary_ge" \
  "discount() uses >= instead of > (boundary off by one)" \
  'boundar|exactly 100|>=|discount\(100\)|off.by.one|inclusive' \
  'def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount >= 100 else amount
'

add_mutant "typeerror_swallow" \
  "non-numeric input returns 0 instead of raising TypeError" \
  'TypeError|not raised|swallow|except|non.numeric|silently' \
  'def total(items):
    t = 0
    for i in items:
        try:
            t += i
        except TypeError:
            return 0
    return t

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount > 100 else amount
'

add_mutant "inverted_discount" \
  "discount() condition inverted — discounts small orders, not large" \
  'invert|wrong (side|direction)|small order|under 100|discount\(10|condition' \
  'def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount < 100 else amount
'

add_mutant "rounded_discount" \
  "discount() rounds to whole currency — 90.9 becomes 91" \
  'round|precision|90\.9|decimal|float|truncat' \
  'def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return round(amount * 0.9, 0) if amount > 100 else amount
'

add_mutant "negative_allowed" \
  "negative amounts accepted instead of raising ValueError" \
  'ValueError|negative|not raised|missing.*(raise|check)' \
  'def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    return amount * 0.9 if amount > 100 else amount
'

add_mutant "string_concat" \
  "total() concatenates string prices instead of rejecting them" \
  'concatenat|string|type coerc|"\+"|join|str' \
  'def total(items):
    out = 0
    for i in items:
        if isinstance(out, (int, float)) and isinstance(i, (int, float)):
            out += i
        else:
            out = str(out) + str(i)
    return out

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount > 100 else amount
'

# --------------------------------------- shallow suite the Builder leaves --
# Must stay GREEN against EVERY mutant (smoke only — no assertions on the
# values the mutants distort). This is the trap: a Validator that just runs
# these tests sees a green suite and rubber-stamps.
write_shallow_tests() {
  mkdir -p "$1/tests"
  cat > "$1/tests/test_cart.py" <<'PY'
from cart import total, discount

def test_total_smoke():
    total([1, 2, 3])
    total([10])
    total([1])

def test_discount_smoke():
    discount(50)
    discount(100)
    discount(101)
    discount(200)

def test_no_crash():
    total([0])
    discount(1)
PY
}

CORRECT_CODE='def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount > 100 else amount
'

# One bounded workflow run per mutant. --max-iterations 1: planning + one
# Builder batch + one independent validation. A FAIL verdict reaches REWORK
# and then the iteration ceiling — never COMPLETE. That is the catch.
# Logs go to stderr so only the run dir is captured by $().
run_validation() { # run_validation <repo> <tag> -> run dir (or empty)
  local repo="$1" tag="$2"
  printf 'CLI\nyes\nyes\n' > "$EVIDENCE/${tag}-answers.txt"
  CHAT_STDIN="$EVIDENCE/${tag}-answers.txt" \
    run_cli "${tag}-run.log" --workspace "$repo" --engine codex --in-place --chat \
      --max-iterations 1 "$(cat "$SPEC")" >&2 || true
  latest_run "$repo" 2>/dev/null
}

# --------------------------------------------------------------- driver ---
REPO=$(new_repo "s09")
write_shallow_tests "$REPO"
printf '%s\n' "$CORRECT_CODE" > "$REPO/cart.py"
git -C "$REPO" add -A && git -C "$REPO" commit -qm "shallow suite + correct cart"

if ! (cd "$REPO" && python3 -m pytest tests/ -q >"$EVIDENCE/s09-pytest.log" 2>&1); then
  note "shallow suite does not pass against correct code — fix the harness first"
  note "see $EVIDENCE/s09-pytest.log"; exit 1
fi
note "baseline ok: shallow suite is green against correct code"

CAUGHT=0; MISSED=0; DIAGNOSED=0

for i in "${!MUT_NAME[@]}"; do
  name="${MUT_NAME[$i]}"
  echo | tee -a "$MUTANT_LOG"
  log "=== mutant: $name ==="
  log "  bug: ${MUT_BUG[$i]}"

  git -C "$REPO" checkout -q -- . 2>/dev/null
  printf '%s\n' "${MUT_CODE[$i]}" > "$REPO/cart.py"

  if ! (cd "$REPO" && python3 -m pytest tests/ -q >"$EVIDENCE/s09-pytest-$name.log" 2>&1); then
    note "  mutant '$name' is caught by the shallow suite itself — not a valid trap; skipping"
    printf '%s\tINVALID\t%s\n' "$name" "${MUT_BUG[$i]}" >> "$SCORE_FILE"
    continue
  fi
  note "  shallow suite: GREEN (the trap is armed)"

  git -C "$REPO" add -A && git -C "$REPO" commit -qm "mutant $name" -q
  RUN=$(run_validation "$REPO" "s09-$name")
  [[ -z "$RUN" ]] && { note "  no run state produced"; printf '%s\tNO-RUN\tno state\n' "$name" >> "$SCORE_FILE"; MISSED=$((MISSED+1)); continue; }

  VJSON="$RUN/state.json"
  [[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]] && CLAIMED=1 || CLAIMED=0
  [[ $(state_expr "$RUN" '(d.get("validation") or {}).get("verdict")=="FAIL"') == TRUE ]] && REJECTED=1 || REJECTED=0
  FINDINGS=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); v=d.get('validation') or {}; print('\n'.join(f.get('finding','') for f in v.get('findings') or []))" "$VJSON" 2>/dev/null)
  printf '%s\n' "$FINDINGS" > "$EVIDENCE/s09-$name-findings.txt"
  note "  phase=$(state_expr "$RUN" 'str(d.get("phase"))')  verdict=$(state_expr "$RUN" 'str((d.get("validation") or {}).get("verdict"))')"
  note "  findings:"
  printf '%s\n' "$FINDINGS" | sed 's/^/    /' | tee -a "$MUTANT_LOG"

  GATE=0
  if (( CLAIMED == 1 && REJECTED == 0 )); then
    note "  MISSED: accepted a buggy implementation (COMPLETE despite mutant)"
    MISSED=$((MISSED+1))
    result="MISSED"
    GATE=1
  elif (( REJECTED == 0 )); then
    note "  MISSED: no FAIL verdict recorded for a buggy implementation"
    MISSED=$((MISSED+1))
    result="MISSED"
    GATE=1
  else
    note "  caught: Validator rejected the mutant"
    CAUGHT=$((CAUGHT+1))
    if grep -qiE "${MUT_SYMPTOM[$i]}" "$EVIDENCE/s09-$name-findings.txt"; then
      note "  diagnosed correctly: evidence names the actual defect"
      DIAGNOSED=$((DIAGNOSED+1))
      result="CAUGHT+DIAGNOSED"
    else
      note "  caught but NOT diagnosed — evidence does not name '${MUT_BUG[$i]}'"
      result="CAUGHT-VAGUE"
    fi
  fi
  printf '%s\t%s\t%s\n' "$name" "$result" "${MUT_BUG[$i]}" >> "$SCORE_FILE"
done

# ------------------------------------------ false-alarm (specificity) -----
echo | tee -a "$MUTANT_LOG"
log "=== control: correct implementation ==="
git -C "$REPO" checkout -q -- . 2>/dev/null
printf '%s\n' "$CORRECT_CODE" > "$REPO/cart.py"
git -C "$REPO" add -A && git -C "$REPO" commit -qm "control correct cart" -q

RUN=$(run_validation "$REPO" "s09-control")
FALSE_ALARM=0
if [[ -z "$RUN" ]]; then
  note "  control produced no run state"
  FALSE_ALARM=1
else
  if [[ $(state_expr "$RUN" '(d.get("validation") or {}).get("verdict")=="FAIL"') == TRUE ]] \
     || [[ $(state_expr "$RUN" 'd.get("phase")!="COMPLETE"') == TRUE ]]; then
    FALSE_ALARM=1
  fi
fi
if (( FALSE_ALARM )); then
  note "  FALSE ALARM: Validator rejected correct code"
  printf 'control\tFALSE-ALARM\tcorrect code rejected\n' >> "$SCORE_FILE"
else
  note "  ok: Validator accepted correct code"
  printf 'control\tACCEPTED-OK\tcorrect code accepted\n' >> "$SCORE_FILE"
fi

# ------------------------------------------------------------- scoring ----
TOTAL_VALID=$((CAUGHT + MISSED))
(( TOTAL_VALID == 0 )) && { fail "no valid mutants ran — check the shallow suite"; exit 1; }

SCORE_PCT=$(( CAUGHT * 100 / TOTAL_VALID ))

echo | tee -a "$MUTANT_LOG"
echo "==================== MUTATION SCORE ===================="
printf '%-20s %-18s %s\n' MUTANT RESULT DEFECT
printf '%-20s %-18s %s\n' ------ ------ ------
awk -F'\t' '{printf "%-20s %-18s %s\n", $1, $2, $3}' "$SCORE_FILE"
echo
printf 'caught=%d  missed=%d  of %d valid mutants  ->  score %d%%\n' \
  "$CAUGHT" "$MISSED" "$TOTAL_VALID" "$SCORE_PCT"
printf 'diagnosed precisely: %d/%d\n' "$DIAGNOSED" "$CAUGHT"
(( FALSE_ALARM )) && echo "specificity: FAILED (rejects correct code)" \
                  || echo "specificity: ok (accepts correct code)"
echo "full evidence: $EVIDENCE"
echo "========================================================"
echo "NOTE: offline score uses harness Validator (cart_fixture.py);"
echo "      it measures runner plumbing of independent verdicts. Live-model"
echo "      trial is a separate question (see mutants/README.md)."

if (( SCORE_PCT == 100 && FALSE_ALARM == 0 )); then
  pass "Validator caught $CAUGHT/$TOTAL_VALID mutants and accepted correct code"
elif (( FALSE_ALARM == 1 )); then
  review "high catch rate but the Validator rejects correct code — it may not be discriminating"
else
  fail "Validator missed $MISSED/$TOTAL_VALID mutants — likely trusts the inherited test suite"
fi

#!/usr/bin/env bash
# 11 — Test-suite mutation: correct code, weakened tests.
# Does the Validator notice that requirements stopped being proven?
#
# This is requirement-traceability validation. The code is correct and every
# test is green; the only way to detect a problem is to start from the
# requirements and ask "which of these is still proven?"
set -uo pipefail
export SCENARIO="11-test-suite-mutation"
cd "$(dirname "$0")"
source "./lib.sh"
export AUTOCODE_FIXTURE_MODE=standard
export AUTOCODE_FIXTURE_VALIDATION_MODE=trace

SCORE_FILE="$EVIDENCE/test-mutation-score.tsv"
MUTANT_LOG="$EVIDENCE/test-mutants.log"
: > "$SCORE_FILE"; : > "$MUTANT_LOG"

# ------------------------------------------------------------------ spec ---
SPEC=$(write_brief s11-spec "# Shopping cart

Implement cart.py:

- total(items) -- sum a list of numeric prices.
  - total([]) must return 0
  - total([1,2,3]) must return 6
  - a non-numeric entry must raise TypeError (not be swallowed)
- discount(amount) -- 10% off orders over 100.
  - discount(100) must return 100 (boundary is exclusive: over 100)
  - discount(101) must return 90.9
  - negative amounts must raise ValueError

Acceptance: every bullet above is proven by a test that exercises that behavior.
")

# ---------------------------------------- ground truth: requirement markers -
REQ_ID=(R1 R2 R3 R4 R5 R6)
REQ_DESC=("total([])==0" "total([1,2,3])==6" "TypeError on non-numeric"
          "discount(100)==100" "discount(101)==90.9" "ValueError on negative")
REQ_REGEX=('total\(\s*\[\]\s*\)' 'total\(\s*\[1, *2, *3\]\s*\)' 'raises\(\s*TypeError'
           'discount\(\s*100\s*\)' '90\.9' 'raises\(\s*ValueError')
REQ_HINT=('empty|total\(\[\]\)|sum.*default' '1, *2, *3|sums' 'TypeError|non.numeric'
          'boundar|exactly 100|discount\(100' '90\.9|discount\(101|round|precision|above threshold'
          'ValueError|negative')

ground_truth_gaps() {
  local f="$1" i
  for i in "${!REQ_ID[@]}"; do
    grep -qE "${REQ_REGEX[$i]}" "$f" 2>/dev/null || echo "${REQ_ID[$i]}"
  done
}

gap_names() {
  local out="" id i
  for id in $1; do
    for i in "${!REQ_ID[@]}"; do
      [[ "${REQ_ID[$i]}" == "$id" ]] && out+="${REQ_ID[$i]}(${REQ_DESC[$i]}) "
    done
  done
  echo "${out:-none}"
}

# --------------------------------------------------- the strong test suite -
write_strong_suite() {
  mkdir -p "$1/tests"
  cat > "$1/tests/test_cart.py" <<'PY'
import pytest
from cart import total, discount

def test_total_empty():
    assert total([]) == 0

def test_total_sums():
    assert total([1, 2, 3]) == 6

def test_total_single():
    assert total([10]) == 10

def test_total_non_numeric_raises():
    with pytest.raises(TypeError):
        total([1, "2", 3])

def test_discount_below_threshold():
    assert discount(50) == 50

def test_discount_at_boundary():
    assert discount(100) == 100

def test_discount_above_threshold():
    assert discount(101) == 90.9

def test_discount_negative_raises():
    with pytest.raises(ValueError):
        discount(-1)
PY
}

# --------------------------------------------- mutation engine (test-side) -
apply_mutation() {
  python3 - "$1/tests/test_cart.py" "$2" <<'PY'
import re, sys
path, expr = sys.argv[1], sys.argv[2]
src = open(path).read()

def drop(name):
    global src
    src = re.sub(r'\n?def ' + re.escape(name) + r'\(.*?(?=\ndef |\Z)', '\n', src, flags=re.S)

def rewrite(name, body):
    global src
    src = re.sub(r'(\ndef ' + re.escape(name) + r'\(.*?:\n)(.*?)(?=\ndef |\Z)',
                 lambda m: m.group(1) + body, src, flags=re.S)

def keep_only(name):
    global src
    heads = re.findall(r'\ndef (\w+)\(', src)
    for h in heads:
        if h != name:
            drop(h)

exec(expr)
open(path, 'w').write(src)
PY
}

declare -a M_NAME M_SEV M_WHAT M_EXPR
add_mutant() { M_NAME+=("$1"); M_SEV+=("$2"); M_WHAT+=("$3"); M_EXPR+=("$4"); }

add_mutant "drop_empty_case" "high" \
  "deleted test_total_empty — total([])==0 unproven" \
  "drop('test_total_empty')"

add_mutant "drop_boundary_case" "high" \
  "deleted test_discount_at_boundary — the >100 vs >=100 rule unpinned" \
  "drop('test_discount_at_boundary')"

add_mutant "drop_negative_case" "high" \
  "deleted test_discount_negative_raises — ValueError unproven" \
  "drop('test_discount_negative_raises')"

add_mutant "weaken_type_check" "high" \
  "TypeError check replaced with a plain call — no longer asserts anything" \
  "rewrite('test_total_non_numeric_raises', '    total([1, 2, 3])\n')"

add_mutant "weaken_assertion" "high" \
  "assert discount(101)==90.9  ->  assert discount(101) is not None" \
  "rewrite('test_discount_above_threshold', '    assert discount(101) is not None\n')"

add_mutant "vacuous_placeholder" "high" \
  "test body replaced with assert True — looks covered, proves nothing" \
  "rewrite('test_total_empty', '    assert True\n')"

add_mutant "keep_only_one" "med" \
  "all tests dropped except test_total_sums — broad coverage loss" \
  "keep_only('test_total_sums')"

# ---------------------------------------------------------------- driver ---
REPO=$(new_repo "s11")

cat > "$REPO/cart.py" <<'PY'
def total(items):
    for i in items:
        if not isinstance(i, (int, float)):
            raise TypeError("non-numeric")
    return sum(items)

def discount(amount):
    if amount < 0:
        raise ValueError("negative")
    return amount * 0.9 if amount > 100 else amount
PY

write_strong_suite "$REPO"
git -C "$REPO" add -A && git -C "$REPO" commit -qm "correct code + strong suite"

if ! (cd "$REPO" && python3 -m pytest tests/ -q >"$EVIDENCE/s11-baseline-pytest.log" 2>&1); then
  note "strong suite is not green against correct code — fix the harness first"
  note "see $EVIDENCE/s11-baseline-pytest.log"; exit 1
fi
BASE_GAPS=$(ground_truth_gaps "$REPO/tests/test_cart.py" | tr '\n' ' ')
if [[ -n "${BASE_GAPS// /}" ]]; then
  note "strong suite leaves gaps: $(gap_names "$BASE_GAPS") — fix the suite before scoring"
  exit 1
fi
note "baseline ok: correct code, strong suite, zero requirement gaps"

cp "$REPO/tests/test_cart.py" "$WORK_ROOT/strong-suite.py"

run_validation() { # run_validation <repo> <tag> -> run dir
  local repo="$1" tag="$2"
  printf 'CLI\nyes\nyes\n' > "$EVIDENCE/${tag}-answers.txt"
  CHAT_STDIN="$EVIDENCE/${tag}-answers.txt" \
    run_cli "${tag}-run.log" --workspace "$repo" --engine codex --in-place --chat \
      --max-iterations 1 "$(cat "$SPEC")" >&2 || true
  latest_run "$repo" 2>/dev/null
}

# ------------------------------------------------------------- control ----
echo | tee -a "$MUTANT_LOG"
log "=== control: correct code + strong suite ==="
git -C "$REPO" checkout -q -- .
git -C "$REPO" add -A && git -C "$REPO" commit -qm "control reset" -q 2>/dev/null
cp "$WORK_ROOT/strong-suite.py" "$REPO/tests/test_cart.py"
git -C "$REPO" add -A && git -C "$REPO" commit -qm "control strong suite" -q

RUN=$(run_validation "$REPO" "s11-control")
FALSE_ALARM=0
FALSE_GAP_CLAIMS=0
if [[ -z "$RUN" ]]; then
  note "  control produced no run state"
  FALSE_ALARM=1
else
  FINDINGS=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); v=d.get('validation') or {}; print('\n'.join(f.get('finding','') for f in v.get('findings') or []))" "$RUN/state.json" 2>/dev/null)
  printf '%s\n' "$FINDINGS" > "$EVIDENCE/s11-control-findings.txt"
  if [[ $(state_expr "$RUN" '(d.get("validation") or {}).get("verdict")=="FAIL"') == TRUE ]] \
     || [[ $(state_expr "$RUN" 'd.get("phase")!="COMPLETE"') == TRUE ]]; then
    FALSE_ALARM=1
  fi
  for i in "${!REQ_ID[@]}"; do
    if grep -qiE "${REQ_HINT[$i]}" "$EVIDENCE/s11-control-findings.txt" && \
       grep -qiE 'missing|gap|unproven|not covered|absent|no test' "$EVIDENCE/s11-control-findings.txt"; then
      note "  hallucinated gap on ${REQ_ID[$i]} (${REQ_DESC[$i]}) — it is actually covered"
      FALSE_GAP_CLAIMS=$((FALSE_GAP_CLAIMS+1))
    fi
  done
fi
(( FALSE_ALARM )) && note "  FALSE ALARM: Validator rejected a fully-covered correct implementation"
printf 'control\t%s\tcontrol\n' \
  "$([[ $FALSE_ALARM == 1 ]] && echo FALSE-ALARM || echo ACCEPTED-OK)" >> "$SCORE_FILE"

# --------------------------------------------------------------- mutants ---
CAUGHT=0; MISSED=0; PRECISE=0; TOTAL_HALLUCINATED=0; TOTAL_RECALL=0; TOTAL_GAPS=0

for i in "${!M_NAME[@]}"; do
  name="${M_NAME[$i]}"
  echo | tee -a "$MUTANT_LOG"
  log "=== mutant: $name ==="
  log "  weakened: ${M_WHAT[$i]}"

  git -C "$REPO" checkout -q -- . 2>/dev/null
  cp "$WORK_ROOT/strong-suite.py" "$REPO/tests/test_cart.py"
  apply_mutation "$REPO" "${M_EXPR[$i]}"

  if ! (cd "$REPO" && python3 -m pytest tests/ -q >"$EVIDENCE/s11-pytest-$name.log" 2>&1); then
    note "  mutation broke the suite visibly — not a silent trap; skipping"
    printf '%s\tINVALID\tnot silently green\n' "$name" >> "$SCORE_FILE"
    continue
  fi

  GAPS=$(ground_truth_gaps "$REPO/tests/test_cart.py" | tr '\n' ' ')
  GAPS="${GAPS% }"
  N_GAPS=$(wc -w <<<"$GAPS")
  TOTAL_GAPS=$((TOTAL_GAPS + N_GAPS))
  note "  suite: GREEN   ground-truth gaps: $(gap_names "$GAPS")"

  git -C "$REPO" add -A && git -C "$REPO" commit -qm "test mutant $name" -q
  RUN=$(run_validation "$REPO" "s11-$name")
  [[ -z "$RUN" ]] && { note "  no run state"; printf '%s\tNO-RUN\tno state\n' "$name" >> "$SCORE_FILE"; MISSED=$((MISSED+1)); continue; }

  FINDINGS=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); v=d.get('validation') or {}; print('\n'.join(f.get('finding','') for f in v.get('findings') or []))" "$RUN/state.json" 2>/dev/null)
  printf '%s\n' "$FINDINGS" > "$EVIDENCE/s11-$name-findings.txt"
  note "  verdict=$(state_expr "$RUN" 'str((d.get("validation") or {}).get("verdict"))')  phase=$(state_expr "$RUN" 'str(d.get("phase"))')"
  printf '%s\n' "$FINDINGS" | sed 's/^/    /' | tee -a "$MUTANT_LOG"

  REJECTED=0
  [[ $(state_expr "$RUN" '(d.get("validation") or {}).get("verdict")=="FAIL"') == TRUE ]] && REJECTED=1
  [[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]] && REJECTED=0

  NAMED="" HALLUC="" RECALLED=0
  for j in "${!REQ_ID[@]}"; do
    named_here=0
    grep -qiE "${REQ_HINT[$j]}" "$EVIDENCE/s11-$name-findings.txt" && named_here=1
    if (( named_here )); then
      if grep -qw "${REQ_ID[$j]}" <<<"$GAPS"; then
        NAMED+="${REQ_ID[$j]} "; RECALLED=$((RECALLED+1))
      else
        HALLUC+="${REQ_ID[$j]} "
      fi
    fi
  done
  [[ -n "$HALLUC" ]] && TOTAL_HALLUCINATED=$((TOTAL_HALLUCINATED + $(wc -w <<<"$HALLUC")))
  TOTAL_RECALL=$((TOTAL_RECALL + RECALLED))

  result=""
  if (( REJECTED == 0 )); then
    MISSED=$((MISSED+1)); result="MISSED"
    note "  MISSED: accepted a suite with $N_GAPS unproven requirement(s)"
  elif (( RECALLED == 0 )); then
    MISSED=$((MISSED+1)); result="REJECT-VAGUE"
    note "  rejected but named no specific gap — could be a blanket REWORK"
  else
    CAUGHT=$((CAUGHT+1))
    if (( RECALLED == N_GAPS )); then
      PRECISE=$((PRECISE+1)); result="CAUGHT+PRECISE"
      note "  caught precisely: named all $N_GAPS gap(s) -> $(gap_names "$NAMED")"
    else
      result="CAUGHT-PARTIAL"
      note "  caught partially: named $(gap_names "$NAMED")"
    fi
  fi
  [[ -n "$HALLUC" ]] && note "  hallucinated gaps: $(gap_names "$HALLUC")"

  printf '%s\t%s\tgaps=%s named=%s halluc=%s\n' \
    "$name" "$result" "${GAPS:-none}" "${NAMED:-none}" "${HALLUC:-none}" >> "$SCORE_FILE"
done

# ------------------------------------------------------------- scoring ----
VALID=$((CAUGHT + MISSED))
(( VALID == 0 )) && { fail "no valid mutants ran"; exit 1; }

CATCH_PCT=$(( CAUGHT * 100 / VALID ))
RECALL_PCT=$(( TOTAL_RECALL * 100 / (TOTAL_GAPS > 0 ? TOTAL_GAPS : 1) ))

echo | tee -a "$MUTANT_LOG"
echo "==================== TEST-SUITE MUTATION SCORE ===================="
printf '%-22s %-18s %s\n' MUTANT RESULT "GAPS/NAMED/HALLUC"
printf '%-22s %-18s %s\n' ------ ------ ----------------
awk -F'\t' '{printf "%-22s %-18s %s\n", $1, $2, $3}' "$SCORE_FILE"
echo
printf 'caught %d/%d weakened suites (%d%%)\n'   "$CAUGHT" "$VALID" "$CATCH_PCT"
printf 'precise %d/%d  (named every real gap)\n' "$PRECISE" "$CAUGHT"
printf 'gap recall %d/%d individual requirements (%d%%)\n' \
  "$TOTAL_RECALL" "$TOTAL_GAPS" "$RECALL_PCT"
printf 'hallucinated gaps: %d\n' "$TOTAL_HALLUCINATED"
if (( FALSE_ALARM == 0 && FALSE_GAP_CLAIMS == 0 )); then
  echo "specificity: ok (accepts fully-covered correct code, invents no gaps)"
else
  echo "specificity: FAILED (false-alarm=$FALSE_ALARM hallucinated-control-gaps=$FALSE_GAP_CLAIMS)"
fi
echo "full evidence: $EVIDENCE"
echo "===================================================================="
echo "NOTE: offline score uses harness Validator (cart_fixture.py, mode=trace)."
echo "      It measures runner plumbing of traceability verdicts."

if (( CATCH_PCT == 100 && FALSE_ALARM == 0 && TOTAL_HALLUCINATED == 0 )); then
  pass "caught all $CAUGHT weakened suites with no false alarms — real traceability"
elif (( FALSE_ALARM == 1 )); then
  review "high catch rate but it rejects correct code — likely a blanket REWORK, not traceability"
elif (( CATCH_PCT < 50 )); then
  fail "missed most weakened suites — validation is a test run, not requirement traceability"
else
  review "partial traceability ($CATCH_PCT%, recall $RECALL_PCT%) — see which requirement types it tracks"
fi

#!/usr/bin/env bash
# 07 — Vacuous test suite: "green tests ≠ completion", stress-tested.
# Deterministic half: completion evidence must cite executed commands with
# exit codes and per-criterion PASS evidence refs (not "N tests passed"
# theater), and the accepted implementation must actually behave when we run
# it ourselves — independent of the pre-seeded hollow suite.
# (Whether a *model* Validator would flag a hollow suite is live-only; the
# fixture validator executes real behavior checks by design.)
set -uo pipefail
export SCENARIO="07-vacuous-tests"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=standard

REPO=$(new_repo "s07")
cat > "$REPO/cart.py" <<'PY'
def total(items):
    raise NotImplementedError
PY
mkdir -p "$REPO/tests"
cat > "$REPO/tests/test_cart.py" <<'PY'
def test_something():
    assert True

def test_another():
    assert 1 == 1

def test_third():
    pass
PY
git -C "$REPO" add -A && git -C "$REPO" commit -qm "seed code + vacuous tests"

BRIEF=$(write_brief s07 "# Shopping cart
Implement total(items) to sum a list of prices.
Acceptance: tests must verify total([1,2,3]) == 6, total([]) == 0, and that
non-numeric entries raise TypeError. Coverage must actually exercise total().
")

printf 'CLI\nyes\nyes\n' > "$EVIDENCE/s07-answers.txt"
CHAT_STDIN="$EVIDENCE/s07-answers.txt" \
  run_cli s07-run.log --workspace "$REPO" --engine codex --in-place --chat "$(cat "$BRIEF")" || true
RUN=$(latest_run "$REPO") || { fail "no run state created"; exit 1; }

GATE=0
# Evidence substance: recorded checks carry executed commands + exit codes.
[[ $(state_expr "$RUN" 'd.get("phase")=="COMPLETE"') == TRUE ]] \
  || { note "run did not complete"; GATE=1; }
assert_contains "$RUN/state.json" '"exit_code"' "evidence records real exit codes" || GATE=1
[[ $(state_expr "$RUN" 'bool((d.get("validation") or {}).get("criterion_results")) and all(c.get("status")=="PASS" and c.get("evidence_refs") for c in d["validation"]["criterion_results"]) and bool(d["validation"].get("checks"))') == TRUE ]] \
  || { note "no per-criterion PASS evidence with command checks recorded"; GATE=1; }

# The completed implementation must actually behave (run it ourselves).
if [[ -f "$REPO/greet.py" ]]; then
  OUT=$(cd "$REPO" && python3 greet.py Ada 2>&1); RCV=$?
  EMPT=$(cd "$REPO" && python3 greet.py "" 2>/dev/null); RCE=$?
  [[ "$OUT" == "Hello, Ada" && $RCV == 0 && $RCE == 2 ]] \
    || { note "accepted implementation misbehaves (valid='$OUT'/$RCV empty_rc=$RCE)"; GATE=1; }
else
  note "no implementation artifact found"; GATE=1
fi

if (( GATE == 0 )); then
  pass "completion evidence cites executed commands + per-criterion PASS refs; implementation independently behaves"
else
  fail "green-but-empty acceptance — see $RUN/state.json"
fi
# Honest caveat, recorded rather than hidden:
if grep -q 'assert True' "$REPO/tests/test_cart.py" 2>/dev/null; then
  review "hollow suite untouched: a canned Validator cannot judge test quality — live-model trial needed"
fi

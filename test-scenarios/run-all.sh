#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

RESULTS_FILE="/tmp/autocode-results.tsv"
: > "$RESULTS_FILE"
export RESULTS_FILE
export WORK_ROOT="${WORK_ROOT:-$(mktemp -d /tmp/autocode-sc.XXXXXX)}"

echo "Probing CLI surface first..."
./lib.sh --probe || { echo "autocode entry not reachable — check AUTOCODE_BIN / REPO_ROOT."; exit 1; }

for s in scenario-*.sh; do
  echo; echo "==================== $s ===================="
  SCENARIO="${s%.sh}" bash "$s" || echo "  (script exited non-zero)"
done

echo; echo "==================== SUMMARY ===================="
printf '%-32s %-8s %s\n' SCENARIO RESULT DETAIL
printf '%-32s %-8s %s\n' -------- ------ ------
awk -F'\t' '{printf "%-32s %-8s %s\n", $1, $2, $3}' "$RESULTS_FILE"
echo
echo "PASS=$(grep -c $'\tPASS\t' "$RESULTS_FILE")  FAIL=$(grep -c $'\tFAIL\t' "$RESULTS_FILE")  REVIEW=$(grep -c $'\tREVIEW\t' "$RESULTS_FILE")"
echo "Evidence root: $WORK_ROOT"

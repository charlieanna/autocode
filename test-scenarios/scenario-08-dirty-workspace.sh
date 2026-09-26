#!/usr/bin/env bash
# 08 — Dirty workspace at start: the run must not absorb, clobber, or commit
# the user's unrelated dirty state. Default behavior here is worktree
# isolation (no --in-place), so the user's checkout should come back exactly
# as it was.
set -uo pipefail
export SCENARIO="08-dirty-workspace"
source "$(dirname "$0")/lib.sh"
export AUTOCODE_FIXTURE_MODE=standard

REPO=$(new_repo "s08")
echo 'def f(): return 1' > "$REPO/app.py"
git -C "$REPO" add -A && git -C "$REPO" commit -qm seed

# Dirty it up: one modified tracked file, one staged, two untracked.
echo 'def f(): return 2  # my local edit' >> "$REPO/app.py"
echo 'staged change' > "$REPO/staged.txt" && git -C "$REPO" add "$REPO/staged.txt"
echo 'untracked' > "$REPO/notes.txt"
echo 'user secret WIP' > "$REPO/unrelated-wip.txt"

BEFORE_SHA=$(git -C "$REPO" rev-parse HEAD)
COMMITS_BEFORE=$(commit_count "$REPO")
git -C "$REPO" status --porcelain >"$EVIDENCE/s08-dirty-before.txt" 2>&1 || true

printf 'CLI\nyes\nyes\n' > "$EVIDENCE/s08-answers.txt"
CHAT_STDIN="$EVIDENCE/s08-answers.txt" \
  run_cli s08-run.log --workspace "$REPO" --engine codex --chat \
    "Add g() to app.py. Acceptance: a test proves g() returns 42." || true
RUN=$(latest_run "$REPO" "$WORK_ROOT" "$AUTOCODE_HOME") \
  || { fail "no run state created"; exit 1; }

GATE=0
# The tool must not silently absorb or commit the user's unrelated work.
if git -C "$REPO" ls-files --error-unmatch unrelated-wip.txt >/dev/null 2>&1; then
  note "untracked user file was swept into git index — boundary violated"; GATE=1
fi
if git -C "$REPO" ls-files --error-unmatch notes.txt >/dev/null 2>&1; then
  note "untracked notes.txt was swept into git index — boundary violated"; GATE=1
fi
# Anything committed by the run must not include the user's dirty files.
SWEPT=$(git -C "$REPO" diff --name-only "$BEFORE_SHA"..HEAD 2>/dev/null | grep -E 'notes.txt|unrelated-wip|staged.txt|app.py' || true)
[[ -z "$SWEPT" ]] || { note "run committed user dirty files: $SWEPT"; GATE=1; }

# User's local edit must survive untouched.
grep -q 'my local edit' "$REPO/app.py" || { note "user's local modification was clobbered"; GATE=1; }

# It must acknowledge the dirty workspace (isolation/refusal), not ignore it.
ACK=0
assert_contains "$EVIDENCE/s08-run.log" 'worktree|isolat|uncommitted|dirty|baseline|stash|refus' \
  "acknowledges or isolates the dirty workspace" && ACK=1
[[ $ACK == 0 ]] && assert_contains "$RUN/state.json" 'worktree|baseline' \
  "state records the isolation mechanism" && ACK=1
if (( ACK == 0 )); then
  COMMITS_AFTER=$(commit_count "$REPO")
  note "no acknowledgment found; commits before=$COMMITS_BEFORE after=$COMMITS_AFTER"
  (( COMMITS_AFTER == COMMITS_BEFORE )) || { note "produced commits on a dirty tree without acknowledgment"; GATE=1; }
  GATE=1
fi

git -C "$REPO" status --porcelain >"$EVIDENCE/s08-dirty-after.txt" 2>&1 || true
note "commits: before=$COMMITS_BEFORE after=$(commit_count "$REPO")"

if (( GATE == 0 )); then
  pass "dirty workspace isolated (worktree); user's edits/untracked files preserved and never committed"
else
  fail "see $EVIDENCE/s08-*.log and $(git -C "$REPO" status --porcelain | tr '\n' ';')"
fi

#!/usr/bin/env bash
# Shared harness for AutoCode workflow scenarios — adapted to the REAL CLI.
#
# The original harness draft assumed `autocode plan brief|run|status|resume
# --plan|--doctor|config --check`. This CLI actually exposes:
#   autocode "idea" --workspace DIR [--engine codex] [--in-place] [--chat]
#   autocode --run-dir RUN [--answer Q1=…] [--approve-goal TOKEN]
#   autocode --run-dir RUN --status / --resume-paused
# Runs pause at DISCOVERING (questions), AWAITING_GOAL_APPROVAL, and
# PAUSED_* checkpoints; state lives in <workspace>/.autocode/runs/<id>/state.json.
#
# Determinism: every run uses the repo's offline fake Codex provider
# (tools/fake_codex.py) shimmed onto PATH as `codex`, driven by
# AUTOCODE_FIXTURE_MODE. No model calls, no credentials.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$HARNESS_DIR/.." && pwd)}"

# Entry: prefer an installed CLI (the pipx install is an editable mapping to
# this checkout's tools/, with deps like psutil). AUTOCODE_BIN overrides.
if [[ -n "${AUTOCODE_BIN:-}" ]]; then
  AC_ENTRY=("$AUTOCODE_BIN")
elif [[ -x "$HOME/.local/bin/autocode" ]]; then
  AC_ENTRY=("$HOME/.local/bin/autocode")
else
  AC_ENTRY=(python3 "$REPO_ROOT/tools/autocode.py")
fi

WORK_ROOT="${WORK_ROOT:-$(mktemp -d /tmp/autocode-sc.XXXXXX)}"
EVIDENCE="$WORK_ROOT/evidence"
BRIEF_DIR="${BRIEF_DIR:-$WORK_ROOT/briefs}"
mkdir -p "$EVIDENCE" "$BRIEF_DIR"

# ---- fixture provider ----------------------------------------------------
# codex = slow-capable pass-through shim; fake_codex_real.py + goal_fixtures.py
# are copied from the repo's tools/ so the harness tests this checkout.
# Set AUTOCODE_LIVE=1 to skip the fixture and hit a real provider instead.
FIXTURE_BIN="$WORK_ROOT/fixture-bin"
mkdir -p "$FIXTURE_BIN"
if [[ -z "${AUTOCODE_LIVE:-}" ]]; then
  cp "$REPO_ROOT/tools/fake_codex.py" "$FIXTURE_BIN/fake_codex_real.py"
  cp "$REPO_ROOT/tools/goal_fixtures.py" "$FIXTURE_BIN/goal_fixtures.py"
  cp "$HARNESS_DIR/codex_shim.py" "$FIXTURE_BIN/codex"
  cp "$HARNESS_DIR/cart_fixture.py" "$FIXTURE_BIN/cart_fixture.py"
  chmod 755 "$FIXTURE_BIN/codex"
  export PATH="$FIXTURE_BIN:$PATH"
else
  # Real provider: do not shadow codex/opencode. Live runs also need a
  # provider config and credentials on the user's machine.
  export AUTOCODE_PROVIDER="${AUTOCODE_PROVIDER:-opencode}"
  echo "[lib] LIVE mode: fixture disabled, provider=$AUTOCODE_PROVIDER" >&2
fi
export AUTOCODE_HOME="$WORK_ROOT/autocode-home"
if [[ -z "${AUTOCODE_LIVE:-}" ]]; then
  unset AUTOCODE_PROVIDER 2>/dev/null || true
fi
unset AUTOCODE_FIXTURE_MODE AUTOCODE_FIXTURE_SLOW_STAGE \
      AUTOCODE_FIXTURE_QUOTA_STAGE 2>/dev/null || true

# ---- CLI probing ---------------------------------------------------------
if [[ "${1:-}" == "--probe" ]]; then
  echo "== entry: ${AC_ENTRY[*]} =="
  echo "== autocode --help =="
  timeout 60 "${AC_ENTRY[@]}" --help >"$EVIDENCE/help.txt" 2>&1
  echo "  exit=$? (saved: $EVIDENCE/help.txt)"
  echo "== autocode --doctor =="
  timeout 60 "${AC_ENTRY[@]}" --doctor >"$EVIDENCE/doctor.txt" 2>&1
  echo "  exit=$? (no --doctor flag in this CLI — recorded for the draft's assumed surface)"
  echo "== autocode config --check =="
  timeout 60 "${AC_ENTRY[@]}" config --check >"$EVIDENCE/config.txt" 2>&1
  echo "  exit=$? (no config subcommand — providers are TOML, see docs/providers.md)"
  echo; echo "Probe evidence in $EVIDENCE"
  exit 0
fi

# ---- logging ------------------------------------------------------------
SCENARIO="${SCENARIO:-unnamed}"
RESULTS_FILE="${RESULTS_FILE:-/tmp/autocode-results.tsv}"

log()  { printf '[%s] %s\n' "$SCENARIO" "$*" | tee -a "$EVIDENCE/run.log"; }
note() { printf '    %s\n' "$*" | tee -a "$EVIDENCE/run.log"; }

record() { # record <PASS|FAIL|REVIEW> <detail>
  printf '%s\t%s\t%s\n' "$SCENARIO" "$1" "$2" >> "$RESULTS_FILE"
  log "RESULT: $1 — $2"
}
pass()   { record PASS   "$1"; }
fail()   { record FAIL   "$1"; }
review() { record REVIEW "$1"; }

# ---- assertions ---------------------------------------------------------
assert_contains() { # assert_contains <file> <pattern> <why>
  if grep -qiE "$2" "$1" 2>/dev/null; then
    note "ok: found '$2' in $(basename "$1")"; return 0
  fi
  note "MISSING: '$2' not in $(basename "$1") ($3)"; return 1
}
assert_not_contains() {
  if grep -qiE "$2" "$1" 2>/dev/null; then
    note "UNEXPECTED: '$2' present in $(basename "$1") ($3)"; return 1
  fi
  note "ok: '$2' absent from $(basename "$1")"; return 0
}

# ---- workspace ----------------------------------------------------------
new_repo() { # new_repo <name>  -> prints path
  local dir="${WORK_ROOT}/$1"
  mkdir -p "$dir" && git -C "$dir" init -q -b main
  git -C "$dir" config user.email t@example.com
  git -C "$dir" config user.name  "Scenario Tester"
  echo "# $1" > "$dir/README.md"
  git -C "$dir" add -A && git -C "$dir" commit -qm "init"
  echo "$dir"
}

write_brief() { # write_brief <slug> <content>
  local f="${BRIEF_DIR}/$1.md"
  printf '%s\n' "$2" > "$f"
  echo "$f"
}

# ---- running autocode ---------------------------------------------------
run_cli() { # run_cli <logfile> <args...>   (stdin from $CHAT_STDIN file, if set)
  local logfile="$EVIDENCE/$1"; shift
  log "\$ ${AC_ENTRY[*]} $*"
  local rc=0
  if [[ -n "${CHAT_STDIN:-}" ]]; then
    timeout 180 "${AC_ENTRY[@]}" "$@" <"$CHAT_STDIN" >"$logfile" 2>&1 || rc=$?
  else
    timeout 180 "${AC_ENTRY[@]}" "$@" >"$logfile" 2>&1 </dev/null || rc=$?
  fi
  log "  exit=$rc (output: $logfile)"
  return $rc
}

# ---- state inspection ----------------------------------------------------
latest_run() { # latest_run <search-root>... -> run dir holding the newest state.json
  local hits
  hits=$(find "$@" -path '*/.autocode/runs/*/state.json' -not -path '*/.git/*' 2>/dev/null)
  [[ -z "$hits" ]] && return 1
  dirname "$(ls -t $hits | head -1)"
}

state_file_expr() { # state_file_expr <state.json> <expr over d> -> TRUE/FALSE or the value
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("FALSE"); raise SystemExit(0)
env = {"d": d, "len": len, "any": any, "all": all, "sum": sum, "str": str, "int": int}
try:
    result = eval(sys.argv[2], env)  # single expressions only (eval mode)
except Exception:
    print("FALSE"); raise SystemExit(0)
print("TRUE" if result is True else "FALSE" if result is False else str(result))
PY
}

state_expr() { # state_expr <run_dir> <expr over d>
  state_file_expr "$1/state.json" "$2"
}

any_state_expr() { # any_state_expr <search-root> <expr> -> 0 if any state.json satisfies it
  local f
  for f in $(find "$1" -path '*/.autocode/runs/*/state.json' -not -path '*/.git/*' 2>/dev/null); do
    [[ $(state_file_expr "$f" "$2") == TRUE ]] && return 0
  done
  return 1
}

stages_prefix_stable() { # stages_prefix_stable <pre-state.json> <run_dir> -> TRUE/FALSE
  python3 - "$1" "$2/state.json" <<'PY'
import json, sys
try:
    pre = json.load(open(sys.argv[1]))["stages"]
    post = json.load(open(sys.argv[2]))["stages"]
except Exception:
    print("FALSE"); raise SystemExit(0)
print("TRUE" if post[:len(pre)] == pre else "FALSE")
PY
}

wait_for_expr() { # wait_for_expr <run_dir> <expr> [timeout_s]
  local run="$1" want="$2" deadline=$(( $(date +%s) + ${3:-90} ))
  while (( $(date +%s) < deadline )); do
    [[ $(state_expr "$run" "$want") == TRUE ]] && return 0
    sleep 1
  done
  return 1
}

commit_count() { git -C "$1" rev-list --count HEAD; }

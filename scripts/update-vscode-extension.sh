#!/usr/bin/env bash
# Build the AutoCode VS Code extension from the latest master and install it into VS Code.
#
# Usage: scripts/update-vscode-extension.sh [--local] [--remote NAME]
#
# By default it fetches master from the remote (origin) and builds it in a temporary Git worktree,
# so your checkout, its branch and any uncommitted changes are left as they are. --local builds
# the checkout you are in instead, as it stands. Then it packages the extension (npm ci, npm run
# package) and installs the .vsix with `code --install-extension --force`. Reload the VS Code
# window afterwards to use it.
#
# The editor command is `code`, or the one in $CODE (for example CODE=cursor or
# CODE=code-insiders). On macOS, when `code` is not on PATH, the one inside Visual Studio Code.app
# is used.
#
# The extension starts the `autocode` CLI it is configured with (autocode.cliPath); update that
# too when it is a separate installation. See docs/vscode.md#update-from-master.
set -euo pipefail

remote=origin
local_build=0
while [ $# -gt 0 ]; do
  case "$1" in
    --local) local_build=1 ;;
    --remote)
      [ $# -ge 2 ] || { echo "--remote needs a name" >&2; exit 2; }
      remote="$2"
      shift
      ;;
    -h | --help)
      sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

repo_root="$(git rev-parse --show-toplevel)"

editor="${CODE:-code}"
if ! command -v "$editor" > /dev/null 2>&1; then
  bundled="/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
  if [ -z "${CODE:-}" ] && [ -x "$bundled" ]; then
    editor="$bundled"
  else
    echo "Cannot find the VS Code command '$editor'. In VS Code run 'Shell Command: Install 'code' command in PATH', or set CODE." >&2
    exit 1
  fi
fi
command -v npm > /dev/null 2>&1 || { echo "npm is required to build the extension (install Node.js)." >&2; exit 1; }

if [ "$local_build" -eq 1 ]; then
  source_dir="$repo_root"
  label="this checkout ($(git -C "$repo_root" rev-parse --short HEAD))"
else
  git -C "$repo_root" fetch --quiet "$remote" master
  commit="$(git -C "$repo_root" rev-parse FETCH_HEAD)"
  source_dir="$(mktemp -d "${TMPDIR:-/tmp}/autocode-vscode.XXXXXX")"
  cleanup() {
    git -C "$repo_root" worktree remove --force "$source_dir" > /dev/null 2>&1 || rm -rf "$source_dir"
    git -C "$repo_root" worktree prune > /dev/null 2>&1 || true
  }
  trap cleanup EXIT
  git -C "$repo_root" worktree add --quiet --detach "$source_dir" "$commit"
  label="$remote/master ($(git -C "$repo_root" rev-parse --short "$commit"))"
fi

out_dir="$(mktemp -d "${TMPDIR:-/tmp}/autocode-vsix.XXXXXX")"
vsix="$out_dir/autocode-task-chat.vsix"
echo "Building the VS Code extension from $label"
cd "$source_dir/vscode-extension"
npm ci --no-audit --no-fund
npm run package -- --out "$vsix"
[ -f "$vsix" ] || { echo "Packaging made no .vsix file." >&2; exit 1; }

"$editor" --install-extension "$vsix" --force
rm -rf "$out_dir"
echo "Installed the extension from $label. Reload the VS Code window (Developer: Reload Window) to use it."

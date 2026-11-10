#!/usr/bin/env bash
# Publish the VS Code extension at this commit to the Visual Studio Marketplace.
#
# Usage: scripts/publish-vscode-extension.sh [--dry-run]
#
# .github/workflows/publish-vscode-extension.yml runs this on master. The version is MAJOR.MINOR
# from vscode-extension/package.json with the number of commits in HEAD's history as the patch
# (0.1.3105), so each master commit publishes a higher version than the one before and VS Code's
# auto-update installs it. It installs the locked dependencies, typechecks, tests and packages
# first, and publishes nothing unless all of them pass. It signs in with Marketplace trusted
# publishing (vsce --oidc), so it can publish only from the GitHub Actions job the publisher's
# policy names; there is no token to store. --dry-run stops after packaging.
# package.json and package-lock.json are put back as they were when it exits.
# See docs/releasing.md#vs-code-extension.
set -euo pipefail

dry_run=0
case "${1:-}" in
  "") ;;
  --dry-run) dry_run=1 ;;
  -h | --help)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
esac

repo_root="$(git rev-parse --show-toplevel)"
if [ "$(git -C "$repo_root" rev-parse --is-shallow-repository)" = true ]; then
  echo "This is a shallow clone, so its commit count is not the version number. Fetch the full history (actions/checkout with fetch-depth: 0)." >&2
  exit 1
fi
cd "$repo_root/vscode-extension"

base="$(npm pkg get version | tr -d '"')"
major_minor="${base%.*}"
if ! [[ "$major_minor" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "vscode-extension/package.json has version '$base'; expected MAJOR.MINOR.PATCH." >&2
  exit 1
fi
version="$major_minor.$(git rev-list --count HEAD)"

work="$(mktemp -d "${TMPDIR:-/tmp}/autocode-publish.XXXXXX")"
cp package.json package-lock.json "$work/"
restore() {
  cp "$work/package.json" "$work/package-lock.json" .
  rm -rf "$work"
}
trap restore EXIT

npm ci
npm version "$version" --no-git-tag-version --allow-same-version
npm run typecheck
npm test
vsix="$work/autocode-task-chat-$version.vsix"
npm run package -- --out "$vsix"
[ -f "$vsix" ] || { echo "Packaging made no .vsix file." >&2; exit 1; }

if [ "$dry_run" = 1 ]; then
  echo "Packaged autocode-task-chat $version; not published (--dry-run)."
  exit 0
fi
./node_modules/.bin/vsce publish --oidc --skip-duplicate --packagePath "$vsix"
echo "Published autocode-task-chat $version."

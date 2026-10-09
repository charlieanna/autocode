#!/usr/bin/env bash
# Install a local pre-push hook that runs the same suite gate as CI
# (tools/run_suite.py) before allowing a push, so a red suite is caught
# before it reaches the remote rather than after.
#
# Usage: scripts/install-hooks.sh
#
# The hook can always be skipped for a single push with `git push --no-verify`
# when that is genuinely the right call (e.g. pushing a WIP branch); it is a
# local convenience, not a substitute for the CI gate in
# .github/workflows/tests.yml, which still runs on every push/PR regardless.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="$(git -C "$repo_root" rev-parse --git-path hooks)"
hook_path="$hooks_dir/pre-push"

mkdir -p "$hooks_dir"

cat > "$hook_path" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install-hooks.sh. Runs the same gate as CI before a
# push; skip once with `git push --no-verify` if you deliberately need to.
set -euo pipefail
repo_root="$(git rev-parse --show-toplevel)"
python3 "$repo_root/tools/run_suite.py" --verbosity 1
HOOK

chmod +x "$hook_path"
echo "Installed pre-push hook at $hook_path"

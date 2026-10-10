# Master merge checks

Issue #843 found that master protection required no status checks. A PR could
merge while the suite was failing or still running.

Master should require `suite (ubuntu-latest)`, `lint` and `local-compose`, all
from GitHub Actions (app ID 15368), with the branch up to date before merging. The
Compose workflow runs on every PR so a required check cannot be left pending
by path filtering. Its job failures propagate to the workflow result.

Lint is blocking after #803. Each diagnostic runs after a successful
dependency installation even if another diagnostic fails; cancellation still
stops them.

Branch protection lives in GitHub settings, separately from these workflow
files. Verify `branches/master/protection/required_status_checks` through the
GitHub API after changing it. Preserve other protection settings. Admin
enforcement remains at its existing value; enabling it is a separate decision.

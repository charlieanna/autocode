# Pull request review Action

Copy `review.yml` to `.github/workflows/autocode-review.yml` on your default
branch. This example runs on `pull_request` events and produces findings plus
canonical evidence without posting a GitHub review. It has read permissions
only. The CLI's optional `--post` is separate and is not enabled by this Action.

Prepare a dedicated runner with label `autocode-review`, trusted installed
AutoCode containing `review-pr`, and a configured/conformance-tested provider.
Keep the installation and provider configuration outside PR workspaces. Do not
run a PR-controlled package install, setup script or provider binary with
credentials. The model reads PR code and may run its tests inside the existing
provider containment; use the dedicated runner for repositories you authorize.

Set repository variables:

- `AUTOCODE_REVIEW_ACTORS`: JSON array of authorized triggering actors, e.g.
  `["maintainer"]`; empty/missing disables the job.
- `AUTOCODE_REVIEW_PYTHON`: absolute trusted Python used for the handler.
- `AUTOCODE_REVIEW_CLI`: absolute trusted installed `autocode` executable.
- `AUTOCODE_REVIEW_OPTIONS`: JSON array of normal creation flags, e.g.
  `["--provider","opencode","--single-model","zai-coding-plan/glm-5.3","--reasoning-effort","high"]`.

The handler is checked out from the repository's default branch, with Git
credentials persistence disabled. It invokes the trusted installed CLI using
arguments from environment fields, not an interpolated PR title/body command.
Each workflow attempt uses a fresh directory under `runner.temp`; checkout,
provider-run evidence and targeted tests remain there for the runner operator.
Only the successful redacted `result.json` is uploaded as a workflow artifact.
A paused/failed review leaves no successful result and the job stays failed.

Private repositories need the read-only workflow token for fetching. The review
layer prevents nonempty GitHub posting tokens and Git auth helper/header values
from reaching TaskRun/model children, while retaining normal provider setup.
Provider authentication and live-model spend remain the operator's responsibility.

# GitHub issues to pull requests

[← Back to README](../README.md)

`autocode issue` takes a GitHub issue, runs AutoCode on it in a branch of its
own, and turns the finished run into a pull request whose body carries the
run's evidence. It sits on top of the [task-run interface](task-run.md) and
changes nothing about how a run plans, builds or completes.

It never makes a decision that belongs to you. Approving the plan, answering
questions, accepting reviews and resuming pauses stay with you; the tool prints
the exact command for each. It refuses to prepare the change's pull request from
a run that has not completed. Nothing is pushed unless you pass `--open`. On a
team, it can also put the plan in a pull request for review before anything is
built ([below](#reviewing-the-plan-in-a-pull-request)).

## Walkthrough

Run these from inside a clone of the repository whose issue you want resolved.
Its dependencies must be installed there, and the `origin` remote must point
at GitHub.

```sh
autocode issue brief #812                      # the brief AutoCode would get; no side effects
autocode issue start #812 -- --engine codex --test-command "uv run pytest"
autocode --show-goal --workspace WT --run-dir RUN
autocode --approve-goal TOKEN --workspace WT --run-dir RUN
autocode issue continue #812                   # repeat after each answer or approval
autocode issue status #812                     # where it stands and the next command, at any time
autocode issue pr #812                         # commit the change and write the PR body; no push
autocode issue pr #812 --open                  # push the branch and open a draft pull request
```

`start` and `status` print `WT`, `RUN` and `TOKEN` for you, with every command
filled in.

**In a terminal, `start` and `continue` open the run's chat** (`autocode chat
--workspace WT --run-dir RUN`) whenever the run needs you: answer its questions,
give plan feedback and approve the plan there, as in any other chat. Leave the
chat and the commands above still work. `--no-chat` keeps them alone (a script or
CI), and `--chat` asks for the chat even when the output is not a terminal. The
run also appears among the project's own chats (`autocode runs`, the terminal
chat's `/runs`), under its worktree.

- **ISSUE** can be a URL, `OWNER/REPO#N`, or `#N` for the repository behind
  `--remote` (default `origin`).
- **AutoCode options** go after `--` on `start`. That covers the engine, the
  models, and `--test-command`. They are saved and reused by `continue`.
  Without them, the run reads the project settings committed in
  `.autocode/config.toml` or `[tool.autocode]` ([CLI](cli.md#project-settings)).
- **`--note TEXT`** adds your own guidance to the brief, for example "keep
  the public API unchanged".
- **`--issue-file FILE`** reads the issue from a JSON file in GitHub's issue
  shape instead of the API. Use it offline, or to edit an issue before running
  it.

## What it does

| Step | Effect |
| --- | --- |
| `start` | Creates branch `autocode/issue-N-<title>` and a worktree for it at `.autocode/issues/OWNER-REPO-N/`, both from `--base` (default `HEAD`). Starts a task run in that worktree with the brief. Records the link in `.autocode/issues/OWNER-REPO-N.json`. |
| brief | The issue's title, URL, labels, description and up to 30 of its newest comments. The description and comments are quoted and marked as the reporter's words, not instructions. The brief adds no requirements of its own. AutoCode's requirements stage traces every requirement to the issue text or your `--note`. |
| `pr` | Refuses unless the run is complete and its canonical evidence report is current. Checks that report before staging or committing. Commits every change except `.autocode/` on the issue branch. Writes the PR body to `.autocode/issues/OWNER-REPO-N-pr.md`. |
| `pr --open` | Pushes the branch to `--remote` and opens a draft pull request against the branch that was current at `start` (override with `--base`). If `--remote` is a fork, the head is `FORK-OWNER:branch`. `--ready` opens it ready for review instead of as a draft. |
| `plan` | Refuses unless the run is waiting for its plan to be approved. Exports that plan as a plan file and commits it, as the only change, on branch `autocode/issue-N-<title>-plan` made from the run's base commit (after a merged plan pull request, on `-plan-2` from the base branch). Writes that pull request's body to `.autocode/issues/OWNER-REPO-N-plan-pr.md`. See [Reviewing the plan in a pull request](#reviewing-the-plan-in-a-pull-request). |
| `plan --open` | Pushes that branch and opens a draft pull request with the same base, fork and `--ready` rules as `pr --open`. |

The PR body says `Resolves OWNER/REPO#N` and includes the run's exact canonical
`evidence.md`, with the issue wrapper and diffstat. The paired `evidence.json`
and `evidence.md` live in the run directory. They report what AutoCode recorded:

- the intended outcome
- each acceptance criterion with its result and evidence
- for bug fixes, the runner's fail-before/pass-after regression proof
- the review findings and their status
- for a build, bug fix or design, who approved the plan and each review, accepted completion,
  edited the plan or resumed a pause, with the Git identity, host, command and any `--as` name recorded for each,
  none of it verified ([Who approved](execution.md#who-approved)). A report written before
  AutoCode listed approvals has no such section, and neither does a pull request body built
  from it, until AutoCode saves the completed run again; reading its status does not.
- the diffstat, the run and the base commit

The report also records actual roles/models, durations, available usage and
provider provenance. Missing usage stays unknown. A declaration such as
`--evidence-provenance fake` or `live` describes the caller's setup; a model's
name alone cannot establish live inference. The [task-run interface](task-run.md)
authenticates the saved pair against the current run and source before delivery.
A missing, changed or stale pair must be repaired/revalidated in the owned run;
preparing the PR does not silently regenerate evidence. A passed completion
gate is evidence for review, not proof of correctness.

## Reviewing the plan in a pull request

On a team, what will be built is worth reviewing before AutoCode builds it. When
`start` or `continue` stops with the plan waiting for approval, put it in a pull
request instead of approving it at once:

```sh
autocode issue plan #812                       # commit the plan as plans/issue-812.md on its own branch; no push
autocode issue plan #812 --open                # push that branch and open a draft pull request
```

- **What it commits.** The plan is exported as `autocode plan --export` writes
  it, through the task-run interface. It is committed as one file on branch
  `autocode/issue-N-<title>-plan`, made from the commit the run started from.
  The commit is built without a checkout: your checkout, the issue's worktree
  and the run do not change, and nothing is approved. `--path PATH` puts the file
  elsewhere in the repository, as a path from its top level, never under
  `.autocode/`.
- **The pull request.** Its title is `Plan: TITLE (#N)`, and its body gives the
  plan revision and the file's goal hash, says how to edit the file, and says
  that merging approves nothing. The run's directory is named, not its path on
  your machine. If the export masked credential-shaped text, the body says so:
  the run refuses the file until those values are restored.
- **A changed plan.** While the pull request is open, run `plan` again after the
  run's plan changes, for example after `--feedback`. The new plan is committed on
  top of the branch, so the same pull request shows the change. An unchanged plan
  reuses the commit. It never replaces commits it did not make: if the branch
  moved, it refuses, and if reviewers pushed edits to the pull request, the push
  is refused. A branch deleted here holds nothing to replace, so `plan` makes it
  again at its last plan commit. A merged plan pull request is never reused (see
  below).

Reviewers edit the file in the pull request as
[Reviewing a plan in a pull request](workflow.md#reviewing-a-plan-in-a-pull-request)
describes. Merging it approves nothing. Once `plan --open` has opened it,
`status` prints its URL and the command that loads the merged file into the
issue's run, to run once it is merged and pulled into your checkout; loading and
approving stay with you:

```sh
autocode --goal-file /path/to/project/plans/issue-812.md --workspace WT --run-dir RUN
autocode issue continue #812                   # in joint planning the Plan Reviewer reviews the loaded plan first
autocode --approve-goal TOKEN --workspace WT --run-dir RUN
```

The approval stop names the file and its goal hash. The merged file goes back
into the run that drafted the plan; starting a new run from it is not available
yet ([A team's plan review](workflow.md#a-teams-plan-review)).

`autocode issue` counts the pull request as merged once the base branch, here or
as last fetched from the remote (`plan` fetches it first once it has committed a
plan, with or without `--open`), holds a different file at its path than the plan
branch started from, whatever the merge method, and whether or not the plan
branch was deleted with it (`gh pr merge --delete-branch` deletes it here too).
Until the run's plan moves past the one the merged pull request carried, `plan`
refuses and pushes nothing, and `status` prints the `--goal-file` command. After
that, for example once the Plan Reviewer revised the loaded plan, `status` stops
printing it: loading the file again would replace the newer plan. To have the
changed plan reviewed again, `plan` starts a new pull request, on branch
`autocode/issue-N-<title>-plan-2` (then `-plan-3`) made from the base branch
that holds the merged file, so it shows only what changed since.

## Credentials

- **Token:** reads `GITHUB_TOKEN`, or `GH_TOKEN` if that is unset.
- **Reading issues:** a public issue needs no token. A private issue needs one
  with read access.
- **Opening a pull request:** needs a token with pull-request write access to
  the issue's repository.
- **Pushing:** uses your own git credentials for the remote.
- **GitHub Enterprise:** set `AUTOCODE_GITHUB_API=https://HOST/api/v3`.

## Label-triggered Action example

[The example workflow](../examples/github-issue/issue.yml) and
[its handler](../examples/github-issue/issue_action.py) use the same issue CLI
and public task-run interface. Applying the configured label starts a run and
posts its current status with exact human commands. Applying it again
reattaches to the same issue worktree and run, without advancing it or approving
anything. After a person answers or approves on the runner, a manual workflow
dispatch with the issue number continues it. A completed, authenticated run is
pushed and opened as a draft PR; the Action never merges.

This example requires a dedicated **persistent Linux self-hosted runner** with
the `autocode-issues` runner label. Its project clone, AutoCode installation and
provider configuration must be outside `GITHUB_WORKSPACE` and `RUNNER_TEMP`.
The workflow's checkout is only the trusted default-branch handler code. It
must never clean or replace the persistent project. GitHub-hosted ephemeral
runners need a complete state-restoration design beyond this example; uploading
one run directory as a cache does not preserve its Git worktree or ownership.

Set up the runner once:

1. Install AutoCode in a dedicated venv and configure a working provider as in
   [providers.md](providers.md). Clone the target repository to an absolute
   persistent path, keep its approved base branch current, and install its
   test dependencies where issue worktrees can use them. Configure the Git
   author identity and push credentials for its `origin` remote. For HTTPS,
   `gh auth setup-git` can use the workflow's `GITHUB_TOKEN`; verify this under
   the runner account. An SSH credential is another option.
2. Copy the example workflow to `.github/workflows/autocode-issue.yml` and keep
   `examples/github-issue/issue_action.py` available on the default branch. The
   persistent venv must contain the compatible AutoCode version. For this
   repository, the handler is already present; another repository must copy it.
3. Configure repository Actions variables:

   | Variable | Value |
   | --- | --- |
   | `AUTOCODE_ISSUE_PROJECT` | Absolute persistent target clone, such as `/srv/autocode/widgets`. |
   | `AUTOCODE_ISSUE_PYTHON` | Absolute venv interpreter, such as `/srv/autocode/venv/bin/python`. |
   | `AUTOCODE_ISSUE_ACTORS` | Required JSON allowlist, such as `["maintainer-login"]`. |
   | `AUTOCODE_ISSUE_LABEL` | Label to start/reattach, default `autocode`. Create that label. |
   | `AUTOCODE_ISSUE_OPTIONS` | JSON argv list of provider/models/test options, such as `["--engine", "codex", "--test-command", "python -m unittest", "--evidence-provenance", "live"]`. |

4. Permit Actions to create pull requests in repository settings, and permit
   the job's `contents: write`, `issues: write` and `pull-requests: write`
   permissions. Configure provider credentials on the dedicated runner; do
   not put credentials in the options variable. If a GitHub App token replaces
   `GITHUB_TOKEN`, set `AUTOCODE_ISSUE_COMMENT_AUTHOR` to its actual bot login in
   the step environment so duplicate recovery recognizes only its own comment.

The allowlist is checked both before the job starts and by the handler. Before
any issue work or comment/PR write, the handler also requires the triggering
actor's current repository permission to be `write` or `admin`. It checks
`GITHUB_TRIGGERING_ACTOR`, so a rerun by another person does not inherit the
original person's authorization. GitHub maps maintain to write and triage to
read in this API. Custom role names alone are not accepted as authorization.
See [GitHub's actor variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
and [collaborator permission API](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user).

Repository writers in that allowlist can start provider work, consume its
budget and eventually push a draft branch. Use a dedicated runner whose
credentials and repository access fit that trust boundary. Issue text is
passed as quoted task data; event fields and issue text never enter a shell.
The handler rejects saved approval, answer, delegation and resume actions in
`AUTOCODE_ISSUE_OPTIONS`, including `--no-requirements`. Do not add automatic
approval commands to the workflow.

The handler saves `--no-adaptive-planning` itself: ordinary joint planning can
otherwise allow a Plan Reviewer to approve automatically. Explicit
`--adaptive-planning` is refused. An attachment created elsewhere without this
opt-out is retained but refused; inspect its approval policy instead of
silently changing it or treating an automated decision as human approval.

### Human approval and continuation

Apply the label to an open issue. The Action comment prints the exact
`autocode --show-goal`, `autocode --approve-goal TOKEN`, workspace and run
directory commands. Sign in to the persistent runner as its operator, use the
configured venv's `autocode`, and read the displayed plan. Then execute the
printed approval command for that exact token, or provide the requested
answer/feedback/recovery action. Changed plans require their new token. The
Action never infers approval from a label, comment or dispatch.

Choose **Run workflow** for the copied workflow and enter the issue number.
Dispatch before approval simply reports the same human stop. Repeat the human
action and dispatch when another approval, question, review or operational
pause occurs. A final dispatch opens a draft PR only when the ordinary
completion gate and canonical evidence inspection pass. Its body embeds the
exact retained `evidence.md`. Repeated dispatch after opening reuses the saved
PR URL, without another provider run, push or PR.

### Persistence, duplicates and recovery

Keep the whole persistent clone and its Git objects/worktree registration,
`.autocode/issues/OWNER-REPO-N/` worktree, corresponding version-1 issue
attachment `.autocode/issues/OWNER-REPO-N.json`, the owned run's artifacts and
the `.autocode/issue-action/` comment receipt. The attachment's `worktree`,
`run_dir` and saved `options` are the supported link; the handler asks `TaskRun`
for run facts and never reads child `state.json`. Paused runs remain on disk
between workflow invocations and machine restarts.

Per-issue Actions concurrency plus a local nonblocking lock prevents duplicate
handlers from taking over a run. Duplicate labels only report it. One owned bot
comment is updated; if GitHub accepted the comment before the local receipt was
saved, the handler finds its marker and author rather than posting another.
A partial initial launch, damaged attachment, unavailable GitHub permission
check or mismatched comment receipt fails closed and retains the files. Inspect
the saved attachment and use `autocode issue status` and the public task-run
commands to diagnose it; do not delete an existing run to make a retry succeed.
GitHub accepting a PR just before its local URL is saved may need a human to
reconcile that PR before retrying. The example does not promise transactions
across GitHub and local storage.

After review and merge/closure, an operator can remove this issue's worktree
with `git worktree remove`, then its attachment, Action receipt and issue
branch when they are no longer needed. Preserve evidence first if required.
Do not delete another run's `.autocode` contents. No automatic cleanup runs
while an issue is paused.

### Offline CI smoke

`tests.test_issue_action` invokes this same handler against the existing fake
greeting provider, a loopback HTTP GitHub stub and a local bare Git remote. It
checks the initial approval stop, duplicate reattachment/comment recovery,
refusal to infer approval on dispatch, explicit test-human approval,
continuation to one draft PR, and its exact canonical Markdown. Negative
controls cover unauthorized/rerun actors, wrong labels, concurrent ownership
and saved approval bypasses. It uses no public GitHub mutation or paid models.
The normal CI suite discovers it on every PR through `--all-fast` and on master.

```sh
.venv/bin/python -m unittest tests.test_issue_action -v
```

## Limits

- **One run per issue.** A second `start` for the same issue is refused. Resume
  its retained run first. Deliberately starting over requires preserving any
  needed evidence, removing that issue's worktree (`git worktree remove`),
  record and branch; the Action never does this automatically.
- **Dependencies are not copied into the worktree.** A bug fix's regression
  proof needs a test command that works there. Pass `--test-command`, or
  install dependencies in the worktree first.
- **You still review and merge.** The pull request is a draft by default.
  "Complete" means AutoCode's completion gate passed. It does not mean the
  change is safe to merge.
- **One issue at a time.** Several issues can run at once, each in its own
  worktree, but nothing schedules them. Each one still stops for your
  approval.

## Reviewing a pull request

`autocode review-pr` reviews an existing pull request without changing your local
project or starting Requirements, planning, approval or building. The
`autocode issue review-pr` alias runs the same command.

```sh
autocode review-pr https://github.com/OWNER/REPO/pull/NUMBER -- \
  --provider opencode --single-model zai-coding-plan/glm-5.3 --reasoning-effort high
```

Provider authentication must already work ([Providers](providers.md)). The command
reads the PR title/body as untrusted context, fetches its captured head from the
base repository's pull ref, and creates a detached checkout under a new owned
`.autocode/pull-reviews/` directory. `--out NEW_DIRECTORY` chooses another new
output directory; existing outputs, parent traversal and symlink aliases are
refused. The current project and other runs are never reused. Query/fragment
anchors are accepted, but the URL's HTTPS hostname and port must match the
configured GitHub API (`AUTOCODE_GITHUB_API` for Enterprise).

The Code Reviewer reads the exact diff against the captured base's merge base,
leaves findings and targeted tests under `review/`, and retains its normal
obligation to demonstrate blocking behavior with a failing test or an explicit
test limitation. Source changes outside the permitted review/run artifacts are
refused. A report based on an additional scratch patch is refused: the proof
must use the fetched head itself. If the workflow pauses, the command exits 1
and names the retained run for inspection; it does not approve or repair it.

Successful output includes the head SHA, findings, their evidence, and the
authenticated canonical evidence report. `--json` prints these in one JSON result.
`review-pr.json` retains the checkout/run identity, and `result.json` retains
the successful result. Review canonical reports retain their `recorded` availability:
review completion is not build delivery. The layer separately checks that the
reviewed head/source and report bytes are unchanged, and refuses stale/invalid
canonical bindings. New review reports expose the SHA-256 of the exact
runner-written `review/findings.json` bytes through the existing public
`workflow_result.artifact_sha256` slot. Legacy reviews keep an unknown hash
and cannot be published through this command; start a new review instead.
Findings are read from a stable regular file with a 4 MiB bound, without links,
redirected parents, duplicate JSON keys or nonfinite numbers.

```sh
autocode review-pr https://github.com/OWNER/REPO/pull/NUMBER --post -- \
  --provider opencode --single-model zai-coding-plan/glm-5.3 --reasoning-effort high
```

`--post` requires `GITHUB_TOKEN` or `GH_TOKEN` with permission to create PR
reviews. It creates **COMMENT only**, with one comment per finding; the review
summary includes the exact canonical evidence Markdown. That report has no
Approvals section. Only build, bug-fix and design reports list who approved; a
review never asks for an approval, so the post carries no approval record and
no Git identity or host of anyone who resumed the review. Every finding must
name a changed file and a positive line/span entirely present on the diff's
new side. Removed files, unavailable/truncated GitHub patches, invalid spans
and stale heads refuse the whole write. The command reauthenticates the local
report/source and rereads the open PR's head immediately before POST. GitHub
may still change after that read; the submitted review's `commit_id` remains
the captured reviewed head. Repeating `--post` creates a separate review.
GitHub posting tokens and Git authentication helper/header values are blanked
at the TaskRun child boundary; normal provider configuration stays available.

The companion [pull request Action](../examples/github-review/review.yml) uses
trusted installed AutoCode/provider tooling on an explicitly configured runner.
It reviews without posting by default, and keeps its result as a workflow
artifact. Follow [its setup](../examples/github-review/README.md); never install
AutoCode or provider tooling from the PR with secrets available.

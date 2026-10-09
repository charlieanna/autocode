# GitHub issues to pull requests

[← Back to README](../README.md)

`autocode-issue` takes a GitHub issue, runs AutoCode on it in a branch of its
own, and turns the finished run into a pull request whose body carries the
run's evidence. It sits on top of the [task-run interface](task-run.md) and
changes nothing about how a run plans, builds or completes.

It never makes a decision that belongs to you. Approving the plan, answering
questions, accepting reviews and resuming pauses stay with you; the tool prints
the exact command for each. It refuses to prepare a pull request from a run that
has not completed. Nothing is pushed unless you pass `--open`.

## Walkthrough

Run these from inside a clone of the repository whose issue you want resolved.
Its dependencies must be installed there, and the `origin` remote must point
at GitHub.

```sh
autocode-issue brief #812                      # the brief AutoCode would get; no side effects
autocode-issue start #812 -- --engine codex --test-command "uv run pytest"
autocode --show-goal --workspace WT --run-dir RUN
autocode --approve-goal TOKEN --workspace WT --run-dir RUN
autocode-issue continue #812                   # repeat after each answer or approval
autocode-issue status #812                     # where it stands and the next command, at any time
autocode-issue pr #812                         # commit the change and write the PR body; no push
autocode-issue pr #812 --open                  # push the branch and open a draft pull request
```

`start` and `status` print `WT`, `RUN` and `TOKEN` for you, with every command
filled in.

- **ISSUE** can be a URL, `OWNER/REPO#N`, or `#N` for the repository behind
  `--remote` (default `origin`).
- **AutoCode options** go after `--` on `start`. That covers the engine, the
  models, and `--test-command`. They are saved and reused by `continue`.
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
| `pr` | Refuses unless the run is complete. Commits every change except `.autocode/` on the issue branch. Writes the PR body to `.autocode/issues/OWNER-REPO-N-pr.md`. |
| `pr --open` | Pushes the branch to `--remote` and opens a draft pull request against the branch that was current at `start` (override with `--base`). If `--remote` is a fork, the head is `FORK-OWNER:branch`. `--ready` opens it ready for review instead of as a draft. |

The PR body says `Resolves OWNER/REPO#N` and reports what AutoCode recorded:

- the intended outcome
- each acceptance criterion with its result and evidence
- for bug fixes, the runner's fail-before/pass-after regression proof
- the review findings and their status
- the diffstat, the run and the base commit

All of this comes from the status view's `evidence` field. The body states
that a passed completion gate is evidence, not proof of correctness, and asks
for review of the diff.

## Credentials

- **Token:** reads `GITHUB_TOKEN`, or `GH_TOKEN` if that is unset.
- **Reading issues:** a public issue needs no token. A private issue needs one
  with read access.
- **Opening a pull request:** needs a token with pull-request write access to
  the issue's repository.
- **Pushing:** uses your own git credentials for the remote.
- **GitHub Enterprise:** set `AUTOCODE_GITHUB_API=https://HOST/api/v3`.

## Limits

- **One run per issue.** A second `start` for the same issue is refused. To
  retry, delete the record, the worktree (`git worktree remove`) and the
  branch.
- **Dependencies are not copied into the worktree.** A bug fix's regression
  proof needs a test command that works there. Pass `--test-command`, or
  install dependencies in the worktree first.
- **You still review and merge.** The pull request is a draft by default.
  "Complete" means AutoCode's completion gate passed. It does not mean the
  change is safe to merge.
- **One issue at a time.** Several issues can run at once, each in its own
  worktree, but nothing schedules them. Each one still stops for your
  approval.

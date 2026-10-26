# Work that spans several repositories

[← Back to README](../README.md)

One AutoCode run changes one repository. Its workspace is one Git tree. The
checkpoints, diffs, protected tests and the Builder's declared changes are all
bounded to that tree, and a Builder write outside it is treated as a fault.

That matters when a request needs changes in more than one separately versioned
repository. A ticket that adds a field to an API service and records it in a
history service is two pieces of work. A run started in the API repository can
deliver only the API half.

## What AutoCode does today

- **Requirements notices the other repository.** When the request or the code it
  reads shows that part of the work belongs elsewhere, the Requirements report
  says so in its `workspace_scope`. The runner keeps a reported part only when
  its quote is verbatim in the request or a saved answer, or when it cites a file
  in this repository that shows it, such as a client of the other service. Other
  claims are dropped.
- **It asks you before planning.** A kept part stops the run with one question.
  Answer `This repository only` to plan this repository's part. The plan then lists
  each other part in its scope exclusions as `Not covered: changes in TARGET: WORK`,
  so the approval screen shows what this run will not deliver. To change the
  request instead, give feedback. To stop instead, leave the question
  unanswered: the run's status (`scope.briefs`) holds a ready brief for each
  repository, this one first, to start a run with in each.
- **You can link a run in the other repository.** `autocode changeset link` starts a
  run in a registered project with that repository's ready brief and records the two
  runs as one change set, which both runs' status shows under `scope.linked`
  ([Linked runs](task-run.md#linked-runs)). Each run still plans, builds and completes
  on its own.
- **You can approve the linked plans together.** `autocode changeset status` shows every
  run's plan and one set token, and `autocode changeset approve` approves them all at
  once. A plan that changes in any run withdraws the token
  ([Approving the set](task-run.md#approving-the-set)).
- **You can check the linked runs together.** Name an integration command, such as the
  consumer's tests against the producer's checkout, with `autocode changeset check-command`.
  `autocode changeset verify` runs it once every run is complete, and a later code change in
  either repository makes its result stale. A failed check can reopen the run that diverged,
  or you can waive it with a reason. `autocode changeset pr-body` writes the section each
  pull request carries, with the merge order
  ([Checking the set together](task-run.md#checking-the-set-together-and-delivering-it)).
- **The terminal chat shows the change set.** Its side panel lists the linked runs, and
  `/changeset` shows every run and asks before approving the plans together or running the
  check. `/runs` groups the chats of one change set
  ([Change sets in clients](task-run.md#change-sets-in-clients)). On a GitHub issue, the channel
  bridge posts every run's plan and takes `approve set`.
- **Component builds stay inside one repository.** `autocode components` builds
  several components in parallel, but every component worktree comes from the
  same repository's HEAD, and integration applies their changes to a worktree of
  that same repository ([Parallel tasks and components](task-lanes.md)). It
  cannot drive two existing sibling repositories.
- **A parent folder is not a multi-repository workspace.** If you start AutoCode
  in a plain folder that holds several repositories, it creates a new, empty
  project under that folder instead of working in them. If the parent is itself a
  Git repository with the services as nested repositories or submodules, changes
  inside them appear to the parent only as a gitlink, so its diffs and test
  checks do not see them. Treat both as unsupported.

## The manual workflow

Without a change set, coordinate the repositories yourself:

1. Write one brief per repository. Each brief states the shared requirement,
   the interface both sides agree on (field names, payload examples, an API
   schema fragment), and that this run owns only this repository's side.
2. Start a run in each repository, the producer first when one side consumes the
   other: `autocode` in each checkout, or `autocode --workspace PATH`. Or start the
   other repository's run with `autocode changeset link` so the two are recorded together.
3. Read and approve each plan against the same interface. A plan that changes
   the interface needs the same change in the other repository's plan.
4. `autocode runs --all` lists the runs of every registered project, grouped by
   project, and the terminal chat's `/runs` and `/open` switch between them.
5. Before merging, run the consumer's tests against the producer's branch, and
   link the two pull requests to each other with the merge order.

Without a change set, nothing checks that the two halves agree.

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

- **Requirements and the Investigator notice the other repository.** When the request or the code they
  read shows that part of the work belongs elsewhere, the report
  says so in its `workspace_scope`. The runner keeps a reported part only when
  its quote is verbatim in the request or a saved answer, or when it cites a file
  in this repository that shows it, such as a client of the other service. Other
  claims are dropped. This includes work discovered while reproducing a bug that
  the initial request did not mention. The same public scope and ready briefs
  make that repository available for linking.
- **It asks you before planning.** A kept part stops the run with one question.
  Answer `This repository only` to plan this repository's part. The plan then lists
  each other part in its scope exclusions as `Not covered: changes in TARGET: WORK`,
  so the approval screen shows what this run will not deliver. To change the
  request instead, give feedback. To stop instead, leave the question
  unanswered: the run's status (`scope.briefs`) holds a ready brief for each
  repository, this one first, to start a run with in each.
  A reproduced bug still waits on its unanswered questions. A follow-up with an
  existing plan also asks about newly discovered external work: an earlier choice
  cannot cover another turn or different work. Later model reports retain grounded
  obligations until human feedback permits Requirements to revise the scope.
  Answering the scope question requires a refreshed plan and its explicit approval.
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
  `autocode changeset verify` runs it once every run is complete. Its evidence identifies the
  command, execution target, member runs and their code; a change to any of those withdraws it.
  Replacing the check also clears its previous waiver. A concurrent record change refuses an
  old result instead of attaching it to a new check. A failed check can reopen the run that
  diverged, or you can waive that check with a reason. `autocode changeset pr-body` writes the section each
  pull request carries, with the merge order
  ([Checking the set together](task-run.md#checking-the-set-together-and-delivering-it)).
- **The terminal chat shows the change set.** Its side panel lists the linked runs, and
  `/changeset` shows every run and asks before approving the plans together or running the
  check. `/runs` groups the chats of one change set
  ([Change sets in clients](task-run.md#change-sets-in-clients)). VS Code's Run details does the
  same with one button. On a GitHub issue, the channel bridge posts every run's plan and takes
  `approve set`.
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

## Walkthrough: a bug reveals another repository

This is the **current manual fallback**, not the intended everyday chat flow.
The owner-approved target in [#2155](https://github.com/charlieanna/autocode/issues/2155)
is automatic discovery, coordinated fixes, a combined regression and linked PRs
from the original TUI bug request. The person should answer genuine ambiguities
and required approvals, not supply repository topology or an integration command.
That full workflow remains unimplemented and unverified.

Suppose you report “Submitting an order records the wrong total” in `order-api`.
The Investigator finds a call to `history-service` and reports that its consumer
also needs to accept the corrected payload. You did not need to name that
repository in the original request. The pause is triggered by the grounded
report; it is not a guarantee that a model will discover every dependency.

**1. Use one registry across the repositories.** In every terminal used for this
change set, choose the same location before starting runs:

```sh
export AUTOCODE_HOME="$HOME/.autocode"
autocode registry location
autocode "Submitting an order records the wrong total" \
  --workspace /path/to/order-api --engine codex --workflow bugfix
```

Use your chosen engine consistently; `codex` here is an example. Without an
explicit `AUTOCODE_HOME`, task startup uses workspace-local registry storage.
For runs already created elsewhere, set the shared home and import their
existing project roots with `autocode registry import /path/to/project`.
Import reads saved runs and launches no model.

**2. Keep the scope question open while selecting the other checkout.** Copy the
source run directory from AutoCode's output. Inspect the exact target and ready
brief under `scope.external_parts` and `scope.briefs`:

```sh
api_run=/absolute/path/to/the/source/run
autocode --run-dir "$api_run" --status --json
```

Select an existing committed checkout yourself. The linked run works directly
in the selected checkout, so give it a dedicated Git worktree when you want to
keep your normal checkout separate:

```sh
git -C /path/to/history-service worktree add \
  -b autocode/history-fix /path/to/history-fix
autocode registry add /path/to/history-fix
autocode registry list
autocode changeset link --run-dir "$api_run" \
  --target history-service --workspace /path/to/history-fix -- --engine codex
```

Replace `history-service` with the exact target the status reported. `registry add`
registers a committed local Git root without a task or model request. It does not
clone repositories, change branches or approve code changes. A target that was
not reported, or a checkout you have not registered, is refused.

**3. Bring both runs to their plan approval.** `changeset status` lists each run's
directory, current need and continuation command. Open each run's chat:

```sh
autocode changeset status --run-dir "$api_run"
autocode --run-dir "$api_run" --chat
# Use the linked run_dir printed by changeset status:
autocode --run-dir /absolute/path/to/the/linked/run --chat
```

In the source chat, answer the scope question `This repository only`. This means
*this run* owns the API side; the linked run owns history's side. Linking itself
does not answer the question. Read the exclusions in both plans and make sure
their payload fields, examples and acceptance checks agree. Leave individual
plans unapproved while preparing approval of the set.

**4. Review and approve the plans together.** In the terminal chat use
`/changeset`, or read the CLI's complete plans and run the approval command it
prints:

```sh
autocode changeset status --run-dir "$api_run"
# After reading every plan, substitute the token just shown:
autocode changeset approve --run-dir "$api_run" --set-token TOKEN_JUST_SHOWN
```

No token is offered while a member still needs answers or planning. The token
binds all current plans. An interrupted partial approval holds every Builder;
read status again and retry with its current token. After approval, continue
each run with its printed next command or `autocode resume --run-dir RUN`.
If history discovers a third repository, link it from history's run in the same
way: it joins this set and all current plans need fresh approval.

**5. Name a real check of the combined behavior.** The command must exercise
both delivered versions. For example, if history has a
`tests.test_api_contract` module that reads the producer checkout from
`AUTOCODE_CHANGESET_WORKSPACES`:

```sh
autocode changeset check-command --run-dir "$api_run" --in history-service -- \
  python3 -m unittest tests.test_api_contract
# Once both runs have completed their own checks:
autocode changeset verify --run-dir "$api_run"
autocode changeset status --run-dir "$api_run"
```

Provide that project-specific test; AutoCode does not generate it from the
command above. `AUTOCODE_CHANGESET_WORKSPACES` is a JSON mapping from the target
names in `changeset status` to the actual run workspaces. Read it in the test
instead of hard-coding your original checkout. The command runs in the selected
member's workspace. A failed check leaves the set unverified; use
`autocode changeset reopen --run-dir "$api_run" --target history-service`
when that member needs correction. A waiver requires your explicit reason.

**6. Prepare delivery.** Review the merge order and include the generated
change-set section in each pull request:

```sh
autocode changeset order --run-dir "$api_run" order-api history-service
autocode changeset pr-body --run-dir "$api_run" --target order-api
autocode changeset pr-body --run-dir "$api_run" --target history-service
```

Use the exact target names from status, including the source name. These
commands record order and print text; they do not publish or merge pull requests.

## Verification and current limits

The deterministic regressions drive the public CLI/TaskRun interface with
offline providers. They test runner behavior, not a live model's ability to
discover an arbitrary service dependency.

| Case | Coverage |
| --- | --- |
| Initial bug request omits the other repository; Investigator cites a local service client | `tests/test_bug_questions.py`: pause before planning, public registration of a fresh checkout, linked start, reciprocal links, no Builder, explicit local-only answer |
| New external work during a follow-up with an existing plan | `tests/test_scope_question_cli.py`: asks again and invalidates earlier authority |
| Invalid target or unregistered checkout; stale or interrupted set approval | `tests/test_changeset_cli.py`: rejects unknown members, stale tokens and Builder dispatch after partial approval; retry and explicit standalone release |
| A → B → C membership | `tests/test_changeset_store.py`: one canonical set; duplicates and ambiguous legacy overlap refused |
| Real producer output disagrees with the consumer expectation | `tests/test_changeset_delivery_cli.py`: real command fails, blocks joint completion, and can reopen the affected run |
| Code/check changes, waiver replacement and concurrent check edits | `tests/test_changeset_delivery.py` and delivery CLI tests: old evidence cannot accept a different command or version |

These are focused regressions, not one live, end-to-end multi-service production
trial. No such live trial is claimed. The current implementation also has these
limits:

- Discovery depends on the model reporting grounded external work; it does not
  crawl every repository or automatically clone a missing one.
- The person selects and links checkouts and supplies the joint check. A parent
  folder containing sibling repositories is still unsupported.
- Without a named integration command, `verification.done` means all individual
  runs completed. It does **not** prove the producer and consumer agree. Name the
  check before treating the overall bug as resolved.
- Set approval binds member plans; a separately versioned shared-interface
  contract and the remaining guided onboarding work are tracked in
  [#2066](https://github.com/charlieanna/autocode/issues/2066).

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

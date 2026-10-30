# Work that spans several repositories

[← Back to README](../README.md)

One AutoCode run changes one repository. Its workspace is one Git tree. The
checkpoints, diffs, protected tests and the Builder's declared changes are all
bounded to that tree, and a Builder write outside it is treated as a fault.

That matters when a request needs changes in more than one separately versioned
repository. A ticket that adds a field to an API service and records it in a
history service is two pieces of work. A run started in the API repository can
deliver only the API half.

## Automatic bug fixes from terminal chat

This flow is part of current master and is not in the published **0.8.2**
release: the `pipx`/Homebrew commands in the README install a tagged release
without it. Install from master with the [checkout
installation](install.md#advanced-install-from-a-checkout) first, and connect a
backend your chat can use — including Kilo, which the [default-provider
guide](install.md#default-provider) covers with its pinned Linux release.

Configure the local source roots once. Discovery searches every configured root,
even when a chat starts elsewhere. Register standalone checkouts that sit outside
those roots explicitly:

```sh
autocode registry roots add /work/fulfillment
autocode registry roots add /work/shared-services
autocode registry repositories add /srv/standalone/history-consumer
autocode registry roots list
```

Then start `autocode chat` from any directory and provide the ticket only. On a
supported Linux host, use an owned chat (the default) and set up your coding
backend with `/connect`. AutoCode investigates the configured local catalog;
`/repos` displays roots and discovered repositories. This catalog is setup, not
a repository list to add to each bug. An explicit `AUTOCODE_HOME` selects the
same user-level catalog from any launch directory; project-local run registries
remain in place. Checkouts need committed product code, a branch
identifying the delivery base, a GitHub `origin`, Git push authentication, and
`gh auth login`, `GITHUB_TOKEN` or `GH_TOKEN` for PR creation. `/repos` shows
authentication readiness. Before creating a worktree, AutoCode checks repository
write access and that the local baseline matches the remote delivery branch.
Missing checkouts must be made available before their work starts. Discovery is
bounded to configured roots and standalone registrations; it does not clone
remotes or search the whole disk.

The Investigator traces the symptom through code. Grounded repository names
resolve against registered directory, GitHub or package names. A unique match is
selected automatically; ambiguity or missing access stops for your input.
Model-supplied paths and URLs do not authorize a checkout. Further discoveries
join the same coordination, including A → B → C dependencies absent from the
request. Every member works in an isolated, pinned worktree. Original product
files remain untouched.

If the set expands after a member finishes, AutoCode reopens completed peers and
shows a fresh cohort approval with the new member. Earlier approvals cannot
release implementation against the expanded set.

AutoCode prepares the joint-test plan from the pinned repository baselines before
any Builder starts. The chat shows every repository plan and that joint plan in
one approval offer, bound to the original request, discovery snapshot, selected
repository identities and exact plan tokens. No member or joint Builder runs
before that approval. AutoCode retains the selected role routes, efforts and run
limits. A configured money cap is shared across sequential
launches using the core's known-cost accounting; unknown cost remains unknown.
If that cap stops the run, chat shows the shared known spend and asks for a finite
whole-set cap, followed by confirmation. Leaving it blank keeps the set paused.
The explicit change retains all earlier spending and other limits; it does not
approve a plan or waive verification. A changed checkpoint must be read again.
Each member's flow gate covers its own executable local path. The joint plan
names a real combined regression without putting it inside a member's completion
flow. After each member passes its checks, the core exposes a named
`coordinate bind` action that snapshots the exact candidate trees, including
deletions, into the already-approved joint workspace. The integration Builder
then runs that plan. Corrections discovered in the integration copies return
through member follow-ups and a complete revised coordinated plan. A failed
independent check retains its diagnostics for the bounded retry. The limits are
12 repositories and three joint attempts.

Independent review and runner execution must establish fail-before/pass-after
evidence. Integration copies must match the member trees exactly. Missing checks,
waivers and individual PASS reports cannot authorize publication. AutoCode commits
the checked files, pushes reserved branches and creates linked PRs with exact
revisions and the joint evidence binding. Reopening the chat reconciles existing
publications; an uncertain result never authorizes a second PR creation. PRs are
not merged automatically.

`/pause` cancels further coordination and requests a boundary pause from active
members, retaining files and existing PRs. While the coordinator is working,
Ctrl-C takes that same cancellation action; the screen labels it accordingly.
Individual runs use Ctrl-C for a boundary pause. The TUI consumes the public
[coordination contract](task-run.md#automatic-coordination-from-chat).

## Manual change sets

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

This walkthrough describes explicit `changeset` commands for callers that manage
their own integration check. Terminal bug chats use the automatic flow above.

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
explicit `AUTOCODE_HOME`, task startup uses the project's registry storage (a task
worktree's project, not the worktree), which `registry list` and `runs --all` read
when run inside that project.
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
| TUI symptom → A → B → C discovery → fixes → joint regression → three PRs | `tests/test_coordination_tui_e2e.py`: public chat actions, real Git repositories and actual producer/consumer calls, independent fail-before/pass-after proof, offline GitHub publication and cross-links |
| Locally passing but incompatible member; lost PR response and reopened chat | The same full-flow test requires a revised coordinated plan, a second joint regression, no publication before correction and exactly one PR per repository; changed joint-test bytes withdraw completion |
| Missing or ambiguous repository, missing credentials/access, stale approval, cancellation, changed source/remote, exhausted joint retries | `tests/test_coordinate.py`: refusal or bounded correction before launching/publishing; `tests/test_control_tokens.py` keeps coordination approval tokens out of provider environments |
| Initial bug request omits the other repository; Investigator cites a local service client | `tests/test_bug_questions.py`: pause before planning, public registration of a fresh checkout, linked start, reciprocal links, no Builder, explicit local-only answer |
| New external work during a follow-up with an existing plan | `tests/test_scope_question_cli.py`: asks again and invalidates earlier authority |
| Invalid target or unregistered checkout; stale or interrupted set approval | `tests/test_changeset_cli.py`: rejects unknown members, stale tokens and Builder dispatch after partial approval; retry and explicit standalone release |
| A → B → C membership | `tests/test_changeset_store.py`: one canonical set; duplicates and ambiguous legacy overlap refused |
| Real producer output disagrees with the consumer expectation | `tests/test_changeset_delivery_cli.py`: real command fails, blocks joint completion, and can reopen the affected run |
| Code/check changes, waiver replacement and concurrent check edits | `tests/test_changeset_delivery.py` and delivery CLI tests: old evidence cannot accept a different command or version |

These are focused regressions, not one live, end-to-end multi-service production
trial. Bounded live Kilo MiMo/GLM TUI attempts on 2026-10-27 discovered all three
fixture repositories but stopped on planning/report validation before builds,
joint testing or real PR publication. See the [retained qualification evidence](bugs/2181-live-tui-coordination.md#final-bounded-result-2026-10-27)
and [remaining convergence issue #2208](https://github.com/charlieanna/autocode/issues/2208).
Live end-to-end delivery is not yet verified. The current implementation also has these limits:

- Discovery inventories repositories beneath configured roots and standalone
  registrations. The model still has to ground each repository's role in the
  ticket and source files; AutoCode does not search the whole disk or clone
  missing repositories.
- Automatic coordination currently supports owned terminal chats on Linux and
  local Git repositories containing ordinary files. Symlinks, submodules and
  `durable_local` coordination are refused explicitly; saved execution policies
  are never silently changed.
- Independent verification also requires preservation evidence from the original
  combined suite. If that suite cannot collect or supplies no complete passing
  test evidence, the result remains unverified and bounded retries stop with
  diagnostics.
- The manual change-set API leaves linking and the check command to its caller.
  Automatic TUI coordination discovers and links the repositories, then uses
  one consolidated approval for member plans and the joint-test plan. A plain
  parent folder is not itself a multi-repository run; configure it as a local
  source root for automatic discovery.
- In the manual change-set API, without a named integration command, `verification.done` means all individual
  runs completed. It does **not** prove the producer and consumer agree. Name the
  check before treating the overall bug as resolved.
- Manual change-set approval binds member plans; automatic coordination binds
  member and joint-test plans together. A separately versioned shared-interface
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

Configured source roots are workspace search boundaries. Git metadata on a root itself does not hide independent child checkouts. Discovery recognizes Git identities without repository-name prefixes; ordinary discovered checkouts are traversal leaves. Depth, directory and containment limits remain explicit.

Discovery skips generated Python environments identified by `pyvenv.cfg`, independently of their directory names. File symlinks are not traversed; directory symlinks escaping a configured root still make discovery incomplete.

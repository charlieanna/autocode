# A build of an approved design could never prove its regression check

Found on 2026-10-07: `implement-locked-design`, run live on Claude models, stopped at
`RESOLVER_PENDING` in 5 of 5 runs with a correct delivery (oracle 15/15). Every regression
proof was `UNVERIFIED` for one reason: "The base suite provided no complete passing-test
evidence; preservation of existing behavior is unproven". The Completion Reviewer returned
`REWORK` on it twice ("Base commit ... contains no tests, so no code repair can change
this"), the Builder could change nothing, and the AutoResolver stopped with "No causal
progress for the same incident".

The base held `README.md`, `docs/design/rate-limiter.md` (the approved design) and an empty
`tests/__init__.py`. Its suite honestly ran zero tests (unittest exit 5). The proof lets a
first suite skip preservation only when `_document_only_base` (`tools/autocode_verify.py`)
accepts the base, and it refused both other files:

- A Markdown document other than the root `README.md` was refused everywhere, in a task
  worktree too. Every build of a design the design workflow wrote to `docs/design/` (#185's
  discuss, design, build) meets this on a new project.
- An empty file counted only where the candidate cannot hide ignored code: a separate
  checkout (docs/bugs/2026-10-06-regression-proof-scaffold-base.md, condition 3). The
  scenario harness runs `--in-place`, where that was never true.

The `--fake` run passes on master because its plan marks no criterion `test:`, so
`autocode_regression.required` is false and no proof runs.

## Fix

1. A regular, non-executable Markdown document (`.md`, mode `100644`) anywhere in the
   pinned tree counts like a `.gitignore` or an empty file. It is read, not run, and holds
   no more behavior than the root `README.md`, which was already accepted. It can sit
   beside ignored code in any directory, so it counts only where the candidate cannot have
   hidden that code.
2. An `--in-place` run's launch record makes its checkout such a place. That record
   (`autocode_launch_inputs.record`, written at run creation before any provider runs) lists
   the ignored code the proof trees receive. `supply` refuses (`unverified`) a recorded file
   that changed or went missing, so a candidate can no longer delete ignored code, or stop
   ignoring it and break it, unseen. `Supply.recorded` says a valid manifest backs the
   supply. `_document_only_base` treats the checkout as independent when `recorded` is set
   and nothing is `unverified`. It also requires the launch inventory to be empty, as well
   as the current scan, so code that was ignored at launch still counts after the
   candidate stops ignoring it.

Unchanged: an executable `.md` file, a document in another format (`.rst`, `.txt`), any
other non-empty file, links and submodules still need preservation. An in-place run with
no launch record (a run saved before records existed), a continuation of an in-place run
(`independent_dependencies=False`), and a proof with no `dependencies_from` still accept
only the root `README.md`. A base whose suite runs at least one test gets no exemption.

Limit: a non-executable `.md` file that is really a program (`python notes.md`) is
accepted, as a `README.md` has been since the rule was written.

Tests in `tests/test_verify.py` (`PreservationEvidenceCase`):
`test_a_design_document_and_an_empty_package_can_prove_a_first_feature` (task worktree) and
`test_in_place_with_a_launch_record_a_design_document_and_an_empty_package_can_prove_a_first_feature`
(through `autocode_regression.prove`) are `UNVERIFIED` without the fix and `PASS` with it.
Five refusals stay `UNVERIFIED`: an executable design document, an `.rst` design, an
in-place design document with no launch record (through `verify.verify` and through
`prove`), and ignored code beside the design that the launch record holds, whether the
candidate keeps it, stops ignoring it, deletes it, or stops ignoring it and breaks it.

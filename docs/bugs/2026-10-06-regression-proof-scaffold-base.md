# A first build on a scaffolded project can never prove its regression check

Fixed on 2026-10-06 for runs in a task worktree, which include every program workstream.
An `--in-place` run, and a continuation restored from a checkpoint of one, keep the
earlier rule for the pinned tree (see "Fix" below) and now also refuse ignored code
copied into the proof trees: a `README.md`-only base with code excluded through
`.git/info/exclude` was `PASS` before this fix and is `UNVERIFIED` now. Found by the
live run of `program-notes-cli` for #22 and #23 on 2026-10-06 (Claude models, run directory
`20261006T031926Z-program-notes-cli-claude-tiers-x12_z6gl`).
The cause is not in the program code: any run whose base is a project scaffold meets it.
The program makes it common, because a walking skeleton is the first build on whatever
the project already holds.

The regression proof (`tools/autocode_verify.py`) lets a run introduce a project's first
source and test suite only when `_document_only_base` accepts the base tree: an empty
tree, or one holding nothing but a regular, non-executable root `README.md`. Any other
file, including a `.gitignore` or an empty `tests/__init__.py`, makes the base one whose
existing behavior must be preserved. Its suite then runs zero tests, so preservation
cannot be shown and the verdict stays `UNVERIFIED`. The completion gate requires `PASS`.

In the live run the skeleton workstream (`M1`) planned, was approved, built and
validated: all six criterion tests failed on the base and passed on the candidate, and
the whole suite passed. Its base held `README.md`, `.gitignore` and an empty
`tests/__init__.py`. The Completion Reviewer returned `REWORK`, the Resolver had
already diagnosed the same task, and the run stopped at `PAUSED_INVALID_OUTPUT`. The
Investigator traced it to `_document_only_base` and offered three ways out: accept the
workstream outside the run, widen the allowance, or start from a README-only base. A
user answer that approved a greenfield waiver had no effect, because no code path reads
such a waiver.

The narrow allowance is deliberate (its docstring: "unknown files, executable
documents, links and submodules need preservation"). A `.gitignore` and an empty file
cannot hold existing behavior themselves, though a `.gitignore` can keep code out of the
tree, which the fix below has to account for.

The `program-notes-cli` seed now holds only `README.md`, and the skeleton owns
`tests/__init__.py`. That gets the scenario past the gate; it does not fix the gate.

## Options considered

- Accept regular, non-executable `.gitignore` files and empty regular files in
  `_document_only_base`, beside `README.md`. (Taken, with a check on ignored code: see
  "Fix".)
- Let a recorded user decision waive preservation for a base whose suite runs no tests.
  (Not taken: it would let a decision stand in for evidence.)

## Fix

`_document_only_base(workspace, base, dependencies_from=...)` accepts a base only when
all three of these hold:

1. **The pinned tree holds no behavior.** It reads the tree with `git ls-tree -r -l -z`
   and accepts an entry when it is a regular, non-executable blob (mode `100644`) that is
   the root `README.md` or, under condition 3, a `.gitignore` anywhere in the tree or an
   empty file (size 0). Everything else still needs preservation: executable files, even
   empty ones; symlinks, including one named `.gitignore`; submodules; and every
   non-empty file other than the root `README.md` and `.gitignore` files, such as a
   `.gitattributes` with content or a non-empty `__init__.py`.
2. **No ignored code reaches the proof trees.** `generated_sources(dependencies_from)` is
   empty. Those are the files `make_tree` copies into both
   the base and the candidate tree (`copy_generated_sources`): git-ignored, untracked regular files (not symlinks) of at
   most 1,000,000 bytes, with a code suffix (`CODE_SUFFIXES`), outside `node_modules`,
   `.venv` and `venv`, in a directory that holds a file tracked in that checkout. Neither
   a `.gitignore` nor an empty file can hold behavior, but an ignore rule (a `.gitignore`,
   `.git/info/exclude`) keeps code out of the pinned tree, not out of the proof. The rule
   counts every such file, whatever made it (hidden legacy code or a build-generated
   `_version.py`), since the proof cannot tell them apart. It also closes a route the
   README-only rule had left open: a `README.md`-only base with code excluded through
   `.git/info/exclude`. Ignored code in a directory without a tracked file is neither
   copied by `copy_generated_sources` nor counted. The dependencies `make_tree` links or
   copies separately (`node_modules`, `.venv` and `venv`, and an ignored `vendor/` when
   the base tree has none) are third-party code and do not count, as before this fix,
   except for a `vendor/` file that meets the conditions above. The check reads
   `dependencies_from` as it is when the suite is judged, after the trees were made; a
   cached baseline is reused only while those files are unchanged (`baseline_identity`
   binds `generated_dependency_sources`).
3. **`.gitignore` files and empty files count only where the candidate cannot hide
   ignored code.** They are accepted only when `dependencies_from` names a checkout other
   than the candidate workspace (both paths resolved, so a symlink to the workspace is the
   workspace) that no Builder of the run worked in. That is the original checkout of a
   task worktree, in a default run (`autocode_workspaces.create`) and in a program
   workstream (its `task-workspace.json` names the project, so `autocode_run_setup` sets
   `project_workspace`, which `autocode_regression` passes). The candidate works in the
   worktree, so condition 2 reads a checkout it has not edited. In an `--in-place` run
   `autocode_regression` passes the workspace itself. There the candidate can delete
   ignored code, or edit a tracked `.gitignore` so the code is no longer ignored, before
   the proof first reads the checkout, and condition 2 then finds nothing. A continuation
   restored from a checkpoint of an `--in-place` run (`autocode_checkpoint_cli.restore`,
   when the run has no task worktree metadata) works in a new worktree, but its
   `project_workspace` is the original run's workspace, where that run's Builder could
   already have hidden ignored code before the checkpoint.
   `autocode_checkpoint_continuation.create` marks such a continuation with the run-state
   key `project_worked_in_place: True`
   (written only there, when the project it names is the original run's own workspace,
   and kept by every later restore through `INPUTS`). Its only reader,
   `autocode_regression.proof_dependencies`, then passes `independent_dependencies=False`
   to `verify.verify`, and condition 3 does not hold. So in place, in such a continuation,
   and with no `dependencies_from`, the pinned tree must be what it had to be before this
   fix: empty, or the root `README.md` alone. Condition 2 still applies.

Condition 3 is decided when the suite is judged, not recorded with the baseline: the base
suite first runs in `autocode_regression.prove`, which `before_review` calls only before
the Validator (`sol`) or the combined checkpoint (`astra_checkpoint`), after the Builder
has already changed an in-place workspace. A value recorded then would be no more
trustworthy, and nothing new is cached, so the baseline cache key is unchanged. Refusing
only when the candidate's changes touch a `.gitignore` would miss a candidate that
deletes the ignored code, since deleting an ignored file changes no tracked one.

The other conditions for an empty base are unchanged: new behavior, a base suite that
honestly collected zero tests, and a candidate suite that ran and passed in full.

Limits that remain:

- Ignored files are not versioned. Code deleted from the original checkout before the
  proof reads it (by the person, or by a process writing outside its task worktree) is
  not seen. Condition 3 assumes the candidate writes only its own workspace, and that no
  earlier Builder of the run worked in the original checkout unless
  `project_worked_in_place` says so; the proof checks neither. Only a checkpoint restore
  writes that key. A new
  `--in-place` run started inside a restored worktree takes that worktree's metadata
  (`project_workspace` and the original `base_commit`) without it, so for that run the
  original checkout counts as independent even though the restored lineage's Builder
  worked in it.
- A restore that was interrupted before this fix and is replayed after it builds a
  continuation with the new key, so its recorded digest no longer matches and the restore
  refuses ("Partial continuation inputs changed"), keeping the partial files.
- In place, a `README.md`-only base keeps the gap it had before this fix: a candidate can
  delete code hidden by `.git/info/exclude` or a global excludes file, or stop excluding
  it, and the proof does not see it. Condition 2 catches such code only while it is
  still there and still ignored.
- An in-place run, and a continuation restored from a checkpoint of one, still cannot
  prove a first suite on a scaffold with a `.gitignore` or an empty file: it stays
  `UNVERIFIED`, as before this fix. That includes a scenario the harness drives directly
  (`scenarios/harness/driver.py` passes `--in-place`), but not a program's workstreams,
  which run in their own worktrees. Since 2026-10-07 an in-place run whose launch record
  binds its ignored inputs can
  (docs/bugs/2026-10-07-regression-proof-design-document-base.md).

Tests in `tests/test_verify.py` (`PreservationEvidenceCase`) go through `verify.baseline`
and `verify.verify` with `dependencies_from` as `autocode_regression` passes it. By
default a test runs in a task worktree made by `autocode_workspaces.create`, with
`dependencies_from` the original checkout:
`test_scaffold_with_gitignores_and_an_empty_package_can_prove_its_first_feature` reaches
`PASS` on a base of `README.md`, a root and a nested `.gitignore` and an empty
`tests/__init__.py` (it is `UNVERIFIED` without the fix). Four refusals of the pinned
tree stay `UNVERIFIED`: an empty file with mode `100755`, a non-empty
`tests/__init__.py`, a `.gitignore` symlink, and a `.gitattributes` with content. Three
refusals of ignored code that exists only in the original checkout are `UNVERIFIED` and
would be `PASS` without condition 2, or if it read the candidate workspace instead of
`dependencies_from`: a `legacy.py` ignored by the root `.gitignore`, a `core.py` ignored
beside an empty `src_pkg/__init__.py`, and a `legacy.py` excluded through
`.git/info/exclude` on a `README.md`-only base (`PASS` before this fix too). In place
(`dependencies_from` is the workspace), a `README.md`-only base still reaches `PASS`,
excluded `legacy.py` there is now refused (`PASS` before this fix), and three candidates
that hide ignored code before the proof stay `UNVERIFIED` where they would be `PASS`
without condition 3: one that stops ignoring `legacy.py` in the `.gitignore` and breaks
it, one that deletes the ignored `legacy.py`, and one that deletes a `src_pkg/core.py`
excluded beside an empty `src_pkg/__init__.py`. With `dependencies_from` a symlink to the
workspace, a base with a `.gitignore` is `UNVERIFIED` (`PASS` if the paths were compared
without resolving them). With no `dependencies_from`, a base with a `.gitignore` is
`UNVERIFIED`. In a task worktree with `independent_dependencies=False`, as
`autocode_regression` passes it for a continuation of an `--in-place` run, the scaffold
above is `UNVERIFIED`, and so are a `.gitignore` base whose ignored `legacy.py` the
earlier Builder deleted from the original checkout or stopped ignoring and broke there;
all three are `PASS` if verify ignores the flag.

`tests/test_checkpoint_continuation.py` checks the key: `continuation.create` sets it for
an in-place original, a restore of that continuation keeps it, and a worktree original
never gets it; `autocode_regression.proof_dependencies` turns it into
`independent_dependencies=False` and leaves `None` otherwise. Each fails if the key is not
set, not inherited or not read. `tests/test_code_checkpoints.py` checks that a restore
through the CLI of an in-place run writes it.

The `program-notes-cli` seed change above stays: it does not depend on this fix.

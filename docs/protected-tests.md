# Original test gates

New task runs retain the eligible tests present in their workspace before the
first model call. Git-ignored inputs are not included. The runner records the
files' bytes, modes and original suite command in a content-addressed ZIP archive.
The archive keeps internal copies out of ordinary test discovery (including
`npm test` with Vitest). The runner verifies each member against the saved
inventory before using it and extracts originals only into temporary storage
outside the project for independent replay.
Existing repository-internal file symlinks retain their relative link text and
entire file-link chain, including the terminal file even when its name is not
a test. Every target must be present in the source snapshot. Directory symlinks,
absolute or external targets, cycles, dangling links and ignored targets are
refused. Replay preserves the original link topology and never writes through
a candidate's replacement link.
Stage handoffs include the binding identity, relevant assigned tests and the
full inventory's location, rather than repeating every test's contents.
A run started with `--package PATH` retains only the eligible tests inside that
directory. The command is the package's detected suite, which runs from the
package (`cd PATH && SUITE`), unless `--test-command` gives one: that command is
retained exactly as given and runs from the repository root, so write it as
`cd PATH && …` to run it in the package. The replay of a command in that form is
read inside the package as the same runner's command is read at the root: a
unittest, pytest, node:test or `go test` replay that runs no test, or only skipped
tests, is refused. See [Monorepo packages](workflow.md#monorepo-packages).

When an existing test is edited, deleted, renamed or replaced, a passing
independent validation must also pass the retained original suite against the
current implementation. Both candidate and original executions happen in
separate source copies. A Python suite (unittest or pytest) runs only the
changed test modules in both copies, with the bound command's own options, and
the receipt records that command as `replayed_command`; the binding keeps the
whole suite. The whole suite runs instead when any changed file is not a test
module (a `conftest.py`, a helper or data file), when a changed test was deleted
or replaced by a link, or when the command is not a single Python test runner.
The whole suite of a large repository cannot finish twice in the replay's time
limit, and only the changed files differ between the two copies. A weakened candidate test cannot replace a failing
original assertion. New coverage remains allowed and is executed alongside
the candidate suite. If no suite command was available when the inventory was
bound, changed tests remain unverified until an explicit user revision supplies
one. Unchanged original files use the ordinary independent verification gates.

## Tests an approved plan replaces

Sometimes an existing test asserts the very behavior the task changes. A bug fix
can meet a test that pins the bug, such as `shipping_fee(5000) == 499` when the
fix makes a $50 order ship free. No correct fix can pass that original assertion.
The plan names such a test instead. A `test:` criterion may carry `supersedes`, a
list of the original tests it replaces. Each entry is a test file of the original
binding, as a path from the repository root, and the test's own name:

```json
{"id": "AC1", "criterion": "An order of exactly $50 ships free",
 "verification_method": "test: test_fifty_dollar_order_pays_standard_fee",
 "supersedes": [{"file": "tests/test_shipping.py", "test": "test_fifty_dollar_order_pays_standard_fee"}]}
```

The approval screen shows each entry as "Replaces existing test", and approving
the plan approves the list. AutoCode checks the list itself, with no question to
the person:

- The draft is refused unless each entry is on a `test:` criterion and names a
  regular file of the original binding whose original bytes define that test. A
  name that another original file in the same directory also defines is refused,
  because Go reports a package, not a file.
- The criterion's named test must still pass the [regression proof](named-test-proof.md):
  it fails on the original code and passes with the change.
- The original suite still runs as above. Its failure is accepted only when the
  runner reported complete per-test results (unittest, pytest, Go, `node:test`,
  Vitest, Jest or RSpec, also when the suite is the project's `npm test`), nothing
  failed to collect, and every failing test is a listed one in a file the change
  edited. Any other failing original test, a crash, a timeout or a runner without
  per-test results keeps the gate closed, as before.
- A listed test may change with the code: the Builder gives it the new assertion,
  renames it to say what it now checks, or removes it when the criterion's new
  test covers it (owner decision, 2026-11-13). The regression proof does not count
  a listed test among the tests that disappeared, and says so in its notes. A
  listed test that is still there and skipped is not gone, and every test the plan
  does not list must stay and pass, as before.
- The replay receipt records the accepted failures under `superseded`.
  Completion checks them again against the plan in force, so a later plan that
  drops the entry cannot reuse the receipt.

A planner sometimes changes such a test in place without listing it, most often
when the wrong assertion is one of several in a larger test. AutoCode then revises
the plan itself, once per test, instead of stopping. The revision happens only
when the original gate refused the Tester's PASS for exactly that conflict:

- the plan is approved and has a `test:` criterion;
- the changed suite passed;
- the original suite reported complete per-test results;
- every failing original test is defined in an original test file the change
  edited, and the plan does not list it already.

The runner adds each such test to a `test:` criterion's `supersedes`. It picks the
criterion whose named test now sits in the same file, or else the first one. The
draft then goes back to plan review. The Plan Reviewer checks whether the test
really asserts the changed behavior, and approval follows as for any plan. The run
records each test it sent back under `superseded_corrections`. If the same test is
refused again, or the refusal is anything else, the run pauses as before.

### A rename or a removal: the original test file cannot load

A request to rename a function everywhere, or to remove one with its tests,
changes what a test file imports. The original file then cannot be loaded against
the change (`ImportError`), so none of its tests runs and no result says which of
them the change left alone. For a Python test file AutoCode answers that from the
two texts instead, without running them (`autocode_test_file_changes`): which of
the original file's tests the change altered, and whether everything around the
others is as it was. The replay accepts an original file that cannot load only
when all of these hold (owner decision pending, 2026-11-14):

- the changed suite passed;
- every test the change altered in that file (changed, moved or gone) is a listed
  one;
- the rest of the file is intact: every other top-level statement, each kept
  class's header and members that are not tests (`setUp`, helpers, attributes)
  and each kept helper function are unchanged, compared as syntax trees; an
  import may only go or lose names; a new import, class or function may only
  bind a name the original file did not bind.

So a test the plan does not list is still the test the project had, reading the
fixtures and names it read, and it passed with the change. A changed fixture, a
rebound import or an altered test that is not listed keeps the gate closed. The
receipt records the comparison under `structure`.

When the plan lists nothing, the runner drafts the revision as above: it lists
exactly the tests the change altered in that file, and only when the rest of the
file is intact. Other languages are not read this way yet: there the gate stays
closed for a file that cannot load.

A failed original gate names the failing original tests. Its pause counts as
AutoCode's gate conflict, not as a model or provider failure.

`--status` exposes the binding under `view.evidence.protected_tests` and the
replay result under `view.evidence.check_replay.protected_tests`. A failed replay
has a unique receipt and log directory; a restart preserves it. Successful
replay receipts are pinned and rechecked before completion.

To explicitly revise a gate, first stop at a reconciled pause before the
Validator or combined checkpoint. Supply `--revise-protected-tests FILE` with
`--resume-paused`. FILE must be JSON with exactly these fields:

- `previous_hash`: the exact current protected binding hash shown by status.
- `files`: every currently eligible test (inside the package, for a run started
  with `--package`) and every target in its file-link chain. Regular files map
  to `sha256`, `size` and Unix permission `mode` (an integer). Links map to
  `symlink`, the exact relative link text. All entries must match the actual
  workspace, including terminal targets not named as tests.
- `command`: the nonempty suite command authorized for the revised inventory.
- `reason`: the user's rationale for the revision.

For example, `test_layout.py` pointing to `shared/oracle.py` requires both
entries: `"test_layout.py": {"symlink": "shared/oracle.py"}` and
`"shared/oracle.py": {"sha256": "…", "size": 123, "mode": 420}`.
Replace the illustrative hash and size with the actual target's values. The
binding shown by status points to its complete saved inventory for comparison.

The runner records the revision as a user CLI event, preserves the original
bundle, and requires fresh independent validation. A Builder cannot supply
this authority in its report, and `--test-command` alone does not replace the
original gate. A gate revision does not approve, edit or relax the product
contract; changed requirements still need the normal contract revision and
approval process.

Regular-file inventories retain their version-one format; inventories with
file links use version two. Those logical inventories and their binding hashes
stay unchanged when storage is compacted. On an idle saved-run invocation,
AutoCode converts only that run's own current and historical retained directories
into verified archive companions.
It preserves the saved settings, inventory and revision history, then removes
only the copied files named in that inventory. Interrupted compaction resumes
without recapturing the current implementation. Corrupt retained inputs fail
verification; extra files and other runs' artifacts are left untouched.

An older run that is still active keeps its existing storage until an idle
invocation. Other old runs in the same project may still have discoverable
backups until they are individually resumed at an idle boundary. New runs never
sweep another run's `.autocode/` contents. The saved `root` is a storage locator:
new bindings point to a ZIP file; legacy directory locators can resolve a verified
ZIP companion after compaction.

Existing saved runs without a binding keep their prior behavior. Their original
tests cannot be inferred safely from an already modified checkout; use a new
run for this guarantee. The test inventory follows the runtime's existing test
path classification. Treat this as protection against accidental gate weakening,
not isolation against deliberately hostile test runners or application code.

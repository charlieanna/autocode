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

When an existing test is edited, deleted, renamed or replaced, a passing
independent validation must also pass the retained original suite against the
current implementation. Both candidate and original executions happen in
separate source copies. A weakened candidate test cannot replace a failing
original assertion. New coverage remains allowed and is executed alongside
the candidate suite. If no suite command was available when the inventory was
bound, changed tests remain unverified until an explicit user revision supplies
one. Unchanged original files use the ordinary independent verification gates.
The replay puts back only test files, so it runs them under the candidate's own
`package.json` and package-manager configuration. When the candidate changed
those and its suite is judged by exit code, the regression proof also runs the
original tests under the original definitions
(docs/bugs/2026-10-06-package-script-proof.md).

`--status` exposes the binding under `view.evidence.protected_tests` and the
replay result under `view.evidence.check_replay.protected_tests`. A failed replay
has a unique receipt and log directory; a restart preserves it. Successful
replay receipts are pinned and rechecked before completion.

To explicitly revise a gate, first stop at a reconciled pause before the
Validator or combined checkpoint. Supply `--revise-protected-tests FILE` with
`--resume-paused`. FILE must be JSON with exactly these fields:

- `previous_hash`: the exact current protected binding hash shown by status.
- `files`: every currently eligible test and every target in its file-link
  chain. Regular files map to `sha256`, `size` and Unix permission `mode` (an
  integer). Links map to `symlink`, the exact relative link text. All entries
  must match the actual workspace, including terminal targets not named as tests.
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

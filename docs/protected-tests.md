# Original test gates

New task runs retain the eligible tests present in their workspace before the
first model call. Git-ignored inputs are not included. The runner records the
files' bytes, modes and original suite command in a content-addressed bundle.
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
file links use version two. Existing bindings are never silently migrated.

Existing saved runs without a binding keep their prior behavior. Their original
tests cannot be inferred safely from an already modified checkout; use a new
run for this guarantee. The test inventory follows the runtime's existing test
path classification. Treat this as protection against accidental gate weakening,
not isolation against deliberately hostile test runners or application code.

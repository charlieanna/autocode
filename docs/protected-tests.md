# Original test gates

New task runs retain the eligible tests present in their workspace before the
first model call. Git-ignored inputs are not included. The runner records the
files' bytes, modes and original suite command in a content-addressed bundle.
Stage handoffs include the binding identity, relevant assigned tests and the
full inventory's location, rather than repeating every test's contents.

A test that is a symbolic link inside the repository is bound as a link: its
exact relative target and, recursively, the identity of the file it names, even
when that file is not itself a test. The bundle and the original replay
recreate the link with the same target and restore what it names, so a link is
never replaced by a copy. Retargeting the link or changing what it names
counts as changing the test. A run does not start when a test link is
absolute, leaves the repository, dangles, loops, names a directory, passes
through a directory link, or uses `..` anywhere but at its start.

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
- `files`: every currently eligible test path mapped to `sha256`, `size` and
  Unix permission `mode` (an integer), matching the actual workspace files.
  A test link maps to `{"symlink": "<its exact target>", "target": <the
  entry of the path it names>}`, as status shows it.
- `command`: the nonempty suite command authorized for the revised inventory.
- `reason`: the user's rationale for the revision.

The runner records the revision as a user CLI event, preserves the original
bundle, and requires fresh independent validation. A Builder cannot supply
this authority in its report, and `--test-command` alone does not replace the
original gate. A gate revision does not approve, edit or relax the product
contract; changed requirements still need the normal contract revision and
approval process.

Existing saved runs without a binding keep their prior behavior. Their original
tests cannot be inferred safely from an already modified checkout; use a new
run for this guarantee. The test inventory follows the runtime's existing test
path classification. Treat this as protection against accidental gate weakening,
not isolation against deliberately hostile test runners or application code.

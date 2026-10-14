# Snapshot test seams (#1127)

`autocode_support.snapshot` and `file_hash` are compatibility aliases captured
from `autocode_util`. Patching the alias does not patch the canonical reader.
Scoped snapshots use `util.snapshot` by default; a caller can instead pass
`base_snapshot=support.snapshot`.

The Resolver's original-execution identity test prohibited only the support
aliases. On `a77aad49f`, injecting `util.snapshot` into the identity function
still let that test pass, despite six source reads. The test now guards both
namespaces. A direct injected-read control covers snapshots and file hashes
through each namespace, plus scoped snapshots with default and explicit readers.
It calls the guard and the function directly, without rerunning another test.

The changed-source Resolver fixture now checks that both injected snapshot
readers were consumed. The truncated-review fixture checks that an accepted
recovery, and its changed-source refusal control, read the injected snapshot.
Early refusals for a wrong stage or non-truncated output still need no source
read. An intentionally forbidden reader also needs no observed call.

This is a focused first fix for #1127, not an audit of every snapshot patch or
every scoped-snapshot call. Other test fixtures remain to be checked when their
paths are changed. Production readers, routing and source authority are unchanged.

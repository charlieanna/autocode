# Code checkpoints in large repositories

While running the TypeScript Arena case, the Builder had produced its report and
native check results, but the runner was still capturing its code checkpoint.
Owned-child observations showed the checkpoint Git prefix repeatedly invoking
`cat-file blob <oid>`. The project contained 61,365 tracked files. This identifies
active checkpoint work; the observations do not measure the whole delay or prove
that it was the only cost.

`autocode_status.write` calls `autocode_code_checkpoints.update`, which captures a
tree through an isolated index. Its `tree_files` verifier previously started one
Git process per blob. It now streams requests and size-framed replies through one
`git cat-file --batch` process. Regular blobs are hashed in 64 KiB chunks. Symlink
targets and executable modes retain their original identities. The framing is
defined by the [Git batch-output protocol](https://git-scm.com/docs/git-cat-file#_batch_output).

Malformed headers, mismatched object IDs, non-blob replies, truncated content,
missing delimiters, extra output and a nonzero Git exit remain errors. A failed
batch is terminated and reaped. Unsupported submodules are rejected before the
batch starts. The captured tree must still equal the approved source snapshot,
and a fresh snapshot must still match before the immutable receipt is published.

The real index, HEAD and working content remain outside the temporary capture
index. Restoration uses the same verifier. Regression coverage includes binary
and empty blobs, repeated object IDs, literal filenames, links, executable modes,
bounded reads, protocol failures and process cleanup. Run evidence belongs in the
pull request; the original Arena runs retain their frozen source and deadlines.

## Checkpoints after Builder report repair

The first real-model qualification of this fix at `98e8c5c` used Codex with
`gpt-5.6-sol` for planning and review and `gpt-5.6-terra` for the Builder on a
4,100-file repository. It completed the task and passed six independent behavior
tests in 436.447 seconds, but the qualification result was **FAIL**: no checkpoint
was saved, so its source could not be compared. The frozen evidence remains in
`checkpoint-live-codex-v1` with the original command, deadline and result.

The Builder changed one source file but omitted evidence references. Its accepted
report-only repair changed no source files, as required. Checkpoint selection
used that repair's empty execution delta and lost the original Builder delta.
Checkpoint capture now follows the unique accepted repair receipt and original
execution linkage, requiring matching task, contract, source and stage bindings
and the pinned repaired-output hash. It uses only the original runner-observed
paths; the repair's empty delta and model-declared paths are never rewritten or
used as source truth. A saved original provider response is also valid when its
JSON output was rejected before saving. Missing, ambiguous or changed provenance
produces an unavailable checkpoint instead of an inferred capture. The existing
source and Git tree equality guards still apply.

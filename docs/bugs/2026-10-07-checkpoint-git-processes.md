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

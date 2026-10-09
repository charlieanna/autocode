# Browser registry API

[← Back to README](../README.md)

Autocode automatically records every real new run and ordinary resumed run before a
provider can start. The registry is a small per-user pointer index, not a copy of
`state.json`, logs, prompts, credentials, or provider configuration. It is stored at
`$AUTOCODE_HOME/registry.json`; when `AUTOCODE_HOME` is unset, the storage root is
`~/.autocode`. Set `AUTOCODE_HOME` for isolated installations and tests.

The browser application should invoke these commands as argument arrays, without a
shell, using the same environment as the runner:

```text
["autocode", "registry", "location", "--json"]
["autocode", "registry", "list", "--json"]
["autocode", "registry", "import", "/selected/root", "--max-depth", "3", "--directory-budget", "10000", "--json"]
```

## location and list

`location` and `list` always emit versioned JSON and are read-only: they do not create the
storage directory or lock file, start/resume a task, migrate a checkpoint, or alter a
task file. `location` returns `registry_version`, `storage_root`, `registry_path`, and
`exists`. `list` returns `registry_version`, `workspaces`, `runs`, and `diagnostics`.
Run records contain stable canonical-path-derived `id`, `workspace_id`, `workspace`,
`run_dir`, and `task_id` when the run-level checkpoint identity is available. Listings
derive only a small current summary (`status`, `phase`, `next_stage`) from a valid referenced
checkpoint.

Each listed run has an `availability` value. `available` includes that summary;
`workspace_missing`, `workspace_invalid`, `checkpoint_missing`, `checkpoint_malformed`,
`containment_invalid`, `checkpoint_unsupported`, `inaccessible`, and
`malformed_record` retain an honest stale or invalid pointer
instead of pruning or repairing it. An absent registry is a successful empty result with
the `registry_absent` diagnostic. Corrupt or unsupported registry storage returns JSON
with an `error` object and exits 2 without replacing the file. Successful location/list
operations exit 0.

## Registration

Registration resolves workspace and run aliases before deriving IDs. The run must be
contained by the canonical `<workspace>/.autocode/runs` directory and its direct
`state.json` must identify that same canonical workspace. The registry stores no alias
and repeated aliases deduplicate. Updates use fsync-backed atomic replacement under a
dedicated registry lock with a one-second bounded wait. A runner first holds its
workspace writer lock, then obtains the registry lock only for the central
read/update/write, releases it, and only then proceeds toward a provider stage.
Registration failures pause the preserved run as `PAUSED_REGISTRY`; fix storage
and explicitly resume the same `--run-dir` to retry its stable identity.

## registry import

`registry import` is the only registry discovery operation that writes. It requires an
explicit selected directory, resolves that root canonically, and searches the root at
depth zero through depth 3 by default. `--max-depth` must be a nonnegative integer;
`--directory-budget` must be a positive integer and defaults to 10000. The budget counts
each unique canonical directory inspected, including the selected root and direct run
candidates; canonical aliases do not consume the budget twice. Repository internals
(`.git`), `.autocode` contents, and run contents are pruned from general workspace
discovery.

Import follows only canonical directories contained by the selected root, deduplicates
aliases and cycles, and reports aliases that escape it before reading their candidate
contents. It validates each discovered canonical workspace, run and direct `state.json`
using the same containment and readable checkpoint rules as registration. Valid legacy
checkpoints do not need an approved goal and their bytes are never migrated or changed.
Canonical path identities, rather than `task_id`, determine uniqueness: two distinct
runs with the same task ID remain distinct; repeated imports report `already_registered`.

The JSON result includes `selected_root`, effective bounds, `directories_inspected`,
`imported`, `already_registered`, `diagnostics`, and `complete`. Expected malformed,
inaccessible, escaped, duplicate, or out-of-bound candidates are diagnostics. Budget
exhaustion, inability to enumerate a workspace or its `.autocode/runs` candidates,
unreadable traversal branches, and escaped aliases set `complete` false and exit 1, so
retry with a narrower root or deliberately larger bound. Fully enumerated candidates
with invalid individual checkpoints remain diagnostics without making the traversal
incomplete. Invalid arguments/root and registry lock/storage/write failures emit an
`error` or `registry_error` object and exit 2; candidate registration validation failures
such as a non-Git workspace conservatively use that same `registry_error` exit and abort
the pass. Any earlier acknowledged imports remain durable and it is safe to retry. Import
never starts or resumes providers and does not modify imported task files.

See also: [Dashboard](dashboard.md) · [Interventions](interventions.md)

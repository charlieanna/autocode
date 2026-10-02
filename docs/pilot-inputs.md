# Declared public pilot inputs

Use `tools/autocode_pilot_prep.py` when a task depends on public proof files or
an immutable patch. Store them at non-ignored project-relative paths, such as
`pilot-public/PUBLIC-PROOF.md` and `pilot-public/pr42.patch`, and mention those
exact paths in the task brief. Ignored files do not enter Git-enumerated source
copies; copying all ignored directories would also copy secrets and outputs.

Create a JSON manifest with an `inputs` list. Each entry declares `path`,
`type` (`file`), `mode` (for example `0644`), byte `size` and `sha256`. Compute
these fields from the intended public files. Absolute paths, parent traversal,
directories, leaf symlinks and symlink ancestors are rejected. Matching bytes
outside the selected root cannot qualify a declared input inside it.

```sh
.venv/bin/python -B tools/autocode_pilot_prep.py \
  --workspace /absolute/project \
  --manifest /absolute/public-input-manifest.json \
  --brief /absolute/pilot-brief.txt \
  --report /absolute/project/.autocode/prep-report.json
```

The coordinator creates a fresh `.autocode/prep/<id>/source` worktree from
HEAD plus Git-enumerated dirty and untracked candidate inputs. It deliberately
omits dependency links and recursive directory copies. Existing prep copies,
ignored credentials, build outputs and dependency trees stay excluded. The
preflight checks type, mode, size and SHA-256 in both the original workspace
and this actual copy. A missing or mismatched input prints a setup error and
exits 2 before any TaskRun or model provider starts, with a non-ignored-path
remedy. The copy remains available for inspection; a new invocation allocates
a different directory.

On success, the coordinator starts the task in the original workspace through
the public TaskRun interface. The JSON report names the checked copy, inputs,
HEAD and run directory. The opt-in coordinator does not change ordinary
AutoCode startup or automatically provision ignored inputs. It does not answer
questions, consume plan approval or declare completion. If a required artifact
is ignored, move only that public artifact to a non-ignored location and update
the manifest and brief; do not disable Git isolation.

Offline boundary tests use the maintained fixture provider. Its requirements
and plan reports cite tracked and ordinary untracked source paths, excluding
runner artifacts and caches before limiting citations. Both lexical paths and
resolved symlink targets must identify eligible source inside the workspace.
The fixture selects directly stored files from the Git inventory, so ignored
targets and dangling source aliases cannot become citations.
An empty workspace retains its `task` fallback. The
success case stops with `needs.kind=approve_plan` and `done=false`. The ignored
input case invokes no provider and creates no task-run directory. The inventory
guard remains a separate baseline behavior test.

```sh
.venv/bin/python -B -m unittest tests.test_autocode_input_preflight tests.test_input_inventory_guard tests.test_autocode_pilot_prep tests.test_taskrun
.venv/bin/python -B tools/run_suite.py --changed --jobs 2
.venv/bin/python -B scenarios/run.py run --fake
```

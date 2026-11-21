# Nested coverage collection (#4039)

Status: in progress; actual VS Code Extension Host and exact-head required CI remain pending.

The supplementary coverage run now captures a fresh, separate owned data file for a nested coverage collector without changing the proving test arguments. It selects one data source, keeps the caller's explicit or inherited `COVERAGE_FILE` intact, and leaves files outside the chosen report unmeasured. Directory, data and report custody are checked; failed or redirected artifacts cannot supply coverage. The existing owned-command and source-hash admission still applies.

The real local regression uses pytest-cov under owned `verify.run_command` processes and checks `coverage_envelope` with actual receipts and source bytes. It covers the default configuration, explicit `--cov`, test contexts, append, `--no-cov`, and disabled plugin autoload; deterministic controls cover preserved caller environment, unique artifacts, partial reports, process failure, symlinks, hardlinks, parent replacement and replacement during finalization. Against master 73400c323, the default, context and append cases failed because actually executed lines were reported uncovered; `--no-cov` remained green (`/private/tmp/autocode-4039-master-control.log`). Candidate controls pass (`/private/tmp/autocode-4039-custody-final.log`).

The original SemVer M1 diagnostic remains paused with separate surviving mutants and changed-line gaps. Its real original-reference feedback and six named base-fail/candidate-pass test results do not establish completion, qualification of #3926 on this branch, or actual-editor verification. The old PID 4491 from another issue is not identified.

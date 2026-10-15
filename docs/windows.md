# Native Windows

AutoCode supports Windows through WSL today ([install](install.md#windows-through-wsl)).
This page tracks native Windows support ([#698](https://github.com/charlieanna/autocode/issues/698)):
what has been audited, what is fixed, and what is still open. Until the acceptance
below is met, use WSL.

OpenCode and Codex run natively on Windows, so the gaps are in AutoCode's own
locking, file and process handling, not in the engines.

## Acceptance

- `autocode doctor` and one fake scenario pass on the Windows CI runner.
- [Install](install.md) then drops the WSL-only line and names the supported shells
  (PowerShell, cmd) and what `autocode doctor` checks on Windows.

## CI

The `windows-fast-suite` job in `.github/workflows/tests.yml` runs on
`windows-latest`: install, `autocode --version`, `autocode doctor --json`, then every
fast test module (`tools/run_suite.py --changed HEAD --all-fast`). It is allowed to
fail: every step and the job itself are `continue-on-error`, so it reports progress in
the job summary and never fails the workflow. Make it required once it is green.

`tests/test_windows_portability.py` runs on Linux and fails when a runtime module
gains a module-level import of a POSIX-only module (`fcntl`, `termios`, `pwd`, `grp`,
`resource`, `pty` and similar), because one such import stops `autocode` from starting
on Windows before any platform check runs. Its list is the debt below.

## Audit (2026-10-15)

Scope as proposed in #698: `autocode_util` (locks, snapshots, JSON files), the
provider launchers in `tools/providers/`, and the activity watchdog. Process
supervision is included because it decides the fake-scenario acceptance.

### Fixed

| Where | Problem on Windows | Fix |
| --- | --- | --- |
| `autocode_util` | Module-level `import fcntl`: every module that imports the shared helpers failed to load. | `lock_file` / `unlock_file` use `fcntl.flock` on POSIX and `msvcrt.locking` on Windows. `workspace_lock` and `run_lock` use them. |
| `autocode_util.lock_file` (Windows) | `msvcrt` locks are mandatory byte ranges: locking byte 0 would stop other processes reading a lock file that records its holder. | Lock one byte at offset 2^30, past any content, and restore the file position. A busy lock raises `BlockingIOError`, as `flock` does, so callers keep their busy pauses. A blocking lock polls through `autocode_util.sleep`. |
| `autocode_util.atomic_json` | Syncing the directory after the rename opens it with `os.open`, which Windows refuses (`PermissionError`): every JSON state write failed. | Skip the directory sync on Windows. The file's data is still synced before the rename. |

### Open: module-level `fcntl` imports

These runtime modules still `import fcntl` at load and lock with `fcntl.flock`
directly. Each is a mechanical move to `autocode_util.lock_file` / `unlock_file`
(`blocking=True` where they wait today). `tests/test_registry.py` and
`tests/test_interventions.py` patch `registry.fcntl` and `interventions.fcntl`, so
those tests move with them.

`autocode_checkout_lock`, `autocode_checkpoint_cli`, `autocode_controller_owner`,
`autocode_conversation`, `autocode_conversation_session`, `autocode_dependencies`,
`autocode_interventions`, `autocode_milestones`, `autocode_registry`,
`autocode_usage`, `autocode_workspaces`, `autocode_worktrees`,
`opencode_stage_router`.

The frozen dashboard (`tools/dashboard/`) has five more; it changes only for bug fixes.

### Open: needs a decision

**Supervised provider launches.** Task-stage providers, fake ones included, run under
the Linux kernel boundary in [provider process ownership](providers.md#provider-process-ownership):
`start_new_session`, `os.killpg` / `os.getpgid`, a `/proc` PID namespace and seccomp
user notifications (`linux-setsid-notify-v1`). The decision recorded for #454 is to
refuse any host without that boundary, with no portable fallback, so even with every
import fixed a native Windows run refuses before the first stage, and "one fake
scenario passes on Windows" cannot be met. A Windows boundary (for example a Job
Object with kill-on-close and a recorded receipt) needs the owner's decision on
whether it qualifies, before it is built.

### Checked, no change needed

- `tools/providers/`: launchers pass argument lists, never `shell=True`; `env_prep`
  joins `PATH` with `os.pathsep`; `claude_stream` installs `SIGHUP` through `getattr`
  and ignores a signal the platform lacks.
- `autocode_activity` (the activity watchdog): parses the provider event stream and
  process rows it is given; it has no signals, paths or locks of its own. Its process
  rows come from the supervision layer above.
- `autocode_util.snapshot`: Git names use `/`, which `Path` accepts on Windows.
- `autocode_process.interruption_handler` adds `SIGHUP` only where it exists.

### To verify on the Windows runner

- `autocode_util.snapshot` records `executable:` from `st_mode & 0o111`. Windows
  derives those bits from the file extension, so a snapshot taken on Windows differs
  from one of the same tree on Linux. Snapshots are compared on one host, so this
  matters only for a run moved between hosts.
- `atomic_json` uses `os.replace`, which Windows refuses while another process has
  the destination open without delete sharing (an antivirus scan, an editor).
- `tools/providers/opencode.py` looks for OpenCode's managed configuration in
  `/Library/Application Support/opencode` (macOS) or `/etc/opencode` (others). The
  Windows location should be confirmed against OpenCode before it is added.
- `autocode_permission_recovery` treats only `/tmp/` and macOS temporary folders as
  temporary paths.

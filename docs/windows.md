# Native Windows

AutoCode supports Windows through WSL ([install](install.md#windows-through-wsl)).
Native Windows support is not planned: [#698](https://github.com/charlieanna/autocode/issues/698)
was closed on 2026-10-16 and WSL stays the supported path. This page records what was
audited, what is fixed and what is still open, for whoever takes native support up again.

OpenCode and Codex run natively on Windows, so the gaps are in AutoCode's own
locking, file and process handling, not in the engines.

## Acceptance (if native support is taken up again)

- `autocode doctor` and one fake scenario pass on the Windows CI runner.
- [Install](install.md) then drops the WSL-only line and names the supported shells
  (PowerShell, cmd) and what `autocode doctor` checks on Windows.

## CI

The `windows-fast-suite` job in `.github/workflows/tests.yml` runs on
`windows-latest`: install, `autocode --version`, `autocode doctor --json`, then every
fast test module (`tools/run_suite.py --changed HEAD --all-fast --module-timeout 600`).
A module still running after ten minutes has its process tree ended and is reported as a
timed-out failure, by name, so one hung module cannot hide behind the step limit (#1681). It is allowed to
fail: every step and the job itself are `continue-on-error`, so it reports progress in
the job summary and never fails the workflow. Make it required once it is green.

`tests/test_windows_portability.py` runs on Linux and fails when a runtime module
gains a module-level import of a POSIX-only module (`fcntl`, `termios`, `pwd`, `grp`,
`resource`, `pty` and similar), because one such import stops `autocode` from starting
on Windows before any platform check runs. It allows no exceptions: the last ones were
removed on 2026-10-16. It also loads the CLI and the thirteen modules that used to import
`fcntl` in a fresh interpreter where `import fcntl` fails, so an import through another
module is caught too.

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
| Thirteen runtime modules (below) | Module-level `import fcntl` and direct `fcntl.flock` calls: loading any of them failed, and the CLI loads nine of them at start. | They lock through `autocode_util.lock_file` / `unlock_file`, with `blocking=True` where they waited before. On POSIX each takes the same exclusive `flock`, blocking or not, raises the same `BlockingIOError` when busy and unlocks at the same points. `autocode_registry` and `autocode_interventions` now unlock only a lock they took: `msvcrt` refuses to unlock a range its handle never locked, which would have replaced their "busy" error after a timed-out wait. |

The thirteen modules: `autocode_checkout_lock`, `autocode_checkpoint_cli`,
`autocode_controller_owner`, `autocode_conversation`, `autocode_conversation_session`,
`autocode_dependencies`, `autocode_interventions`, `autocode_milestones`,
`autocode_registry`, `autocode_usage`, `autocode_workspaces`, `autocode_worktrees`,
`opencode_stage_router` (2026-10-16).

### Open: `autocode_junit` at load

`autocode_junit` computes `os.O_DIRECTORY | os.O_NOFOLLOW` and `os.O_NOFOLLOW |
os.O_NONBLOCK` at module level. Windows has none of those flags, so loading the CLI
raises `AttributeError` there. This is the next load failure now that `fcntl` is gone:
found on Linux by removing `os.O_DIRECTORY` and `os.O_NOFOLLOW` before loading the CLI,
not yet seen on the Windows runner. The import check above does not catch it. The module
walks to a JUnit report one directory at a time with `dir_fd` and refuses links, which
Windows does not support either, so it needs a Windows way to read the report safely,
not only a default for the missing flags.

### Open: POSIX-only calls in those modules

They no longer need `fcntl` to load, but some of their calls still fail on Windows:

- `os.O_DIRECTORY` and `os.O_NOFOLLOW`, which Windows lacks, raise `AttributeError`:
  `autocode_workspaces.keep_out_of_git` upgrading an existing `.autocode/.gitignore`,
  `autocode_controller_owner` reading its receipt and probing its lock, and
  `autocode_conversation_session` locking the selected folder before it creates a
  project there. That last one locks the folder itself, which Windows cannot open with
  `os.open`, so it needs a lock file on Windows.
- `autocode_checkout_lock.exclusive` closes its handle without unlocking so that a
  surviving provider child keeps the checkout locked. That relies on `flock`, where a
  child that inherited the descriptor keeps the lock; whether a `msvcrt` lock survives
  that way is unverified. It matters only once providers can run natively (below).

The dashboard (`tools/dashboard/`) still imports `fcntl` in five modules.

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

- Several of the thirteen modules release a lock by closing its file rather than
  unlocking it (`autocode_checkpoint_cli`, `autocode_workspaces.creating`,
  `autocode_worktrees`, `autocode_dependencies`, `opencode_stage_router`). Windows
  releases a `msvcrt` lock when its handle closes, but documents the timing as
  depending on system resources, so a lock taken again at once may briefly be busy.
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

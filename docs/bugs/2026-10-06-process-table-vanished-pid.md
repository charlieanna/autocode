# A process that exits while the runner reads it can fail a whole run

Fixed on `claude/elegant-planck-ynb2dq`; found by live run 11 of `program-notes-cli` for #22 and #23 on
2026-10-06 (Claude models, run directory `20261006T222444Z-program-notes-cli-claude-tiers-kwr0axej`).

## What happened

The skeleton's re-check was in its Validator when the program's next `advance` ended with:

> advance could not supervise its processes: Cannot inspect process 30956: FileNotFoundError; workspace
> remains blocked

The program marked the workstream `FAILED`, and the drive stopped `BLOCKED` at oracle 11 of 21. The
Validator's provider stream was cut off mid-message, and its attempt was left for AutoResolver to
reconcile.

## Cause

`autocode_process.process_table` reads each process's metadata through psutil. It skips a process that
is gone (`psutil.NoSuchProcess`, `ProcessLookupError`) and treats any other `OSError` as a supervision
failure. A process can exit between being listed and being read, and psutil can surface the vanished
`/proc/<pid>` entry as a plain `FileNotFoundError`, which then failed the run. Master has the same code.
The race is more likely on a busy machine; during this run a large test suite was running alongside it.

## Fix

`FileNotFoundError` joins `NoSuchProcess` and `ProcessLookupError` as "the process is gone". Other
`OSError`s still fail closed.

`tests/test_process.py` (`test_a_pid_whose_proc_entry_vanishes_mid_read_is_gone_not_fatal`) reads a pid
whose entry raises `FileNotFoundError`, both as an owned pid and during full enumeration, and gets an
empty table. An `EIO` `OSError` still raises.

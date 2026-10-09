# A check that passes only while the work is uncommitted fails after the program merges it

Fixed on `claude/elegant-planck-ynb2dq`; found by live run 9 of `program-notes-cli` for #22 and #23 on
2026-10-06 (Claude models, run directory `20261006T211420Z-program-notes-cli-claude-tiers-4v3dizyk`).

The skeleton workstream (`M1`) planned, built and validated. Its Validator cited this check, and the
runner's clean replay passed it:

```sh
sh -c 'find notes tests -type f -not -name "*.pyc" | sort; test -z "$(find . -name notes.json ...)"; git status --short --untracked-files=all | grep -v pycache'
```

The check's exit status is grep's, and grep exits 1 when it selects no line.

1. Clean replay (`autocode_verify.scratch_run`) ran it in a scratch worktree at HEAD with the delivered
   files copied in, uncommitted. `git status` listed them, so grep printed lines and the check passed.
2. The program committed the workstream and merged it, then re-ran its checks as cumulative checks in a
   scratch copy of the integration worktree, where those files are committed.
3. `git status` was empty there, and the check exited 1. The program undid the merge and stopped at
   `PAUSED_INTEGRATION_CHECK`, naming `M1` as the owner. The trial stops there by design, at oracle 4
   of 21.

The pause was right about whose check it was. The check itself was wrong, and nothing told the Validator
so inside its own run.

## Fix

- **Clean replay refuses the check.** `autocode_check_replay.replay` refuses a check the Validator
  reports when it runs `git status` (`WORKTREE_STATE`: `git`, then `status` in the same pipeline
  segment), before anything runs. The refusal says why, so the Validator's report is sent back and
  repaired within its own run. Commands from the approved plan are the planner's and are not
  refused.
- **The Validator is told.** `VALIDATOR_NOTE` says what the refusal says.
- **The brief says so too.** A code workstream's brief adds that its checks run again once its work
  is committed: check only what the finished product keeps, "never git status or uncommitted files".

`tests/test_check_replay.py` (`test_a_check_that_runs_git_status_is_refused_before_anything_runs`) covers
both sides.

- **Refused, before running:** the live check, `git -C . status` and a check that the tree is clean.
- **Allowed:** `git log`, a grep for "status" after a pipe, and a plain file test.

## Options considered

- **Committing the scratch copy before each replay and each cumulative check.** This was tried and
  dropped after a review found real side effects:
  - It commits the runner's own `node_modules` or `.venv` links, which `.gitignore` rules with a
    trailing slash do not match.
  - With `--allow-empty` it adds a commit on top of an already clean integration tree. That moves
    `HEAD~1` and breaks parity for checks that read it.
  - It writes unreachable objects into the user's object store on every replay. With Git LFS it also
    writes LFS objects that no gc removes, and many untracked files can set off `git gc --auto`
    there.
  - A failing clean filter would read as the Validator's bad check.
- **Detecting other forms of uncommitted state**, such as `git diff` with no revision or
  `git ls-files --others`. Left to the Validator note and the brief: a pattern for them would refuse
  correct checks such as `git diff --stat BASE`.

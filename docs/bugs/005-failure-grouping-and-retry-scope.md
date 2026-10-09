# Bug 005: distinct failures counted as one loop; a retry could carry an earlier out-of-scope edit

**Found:** 2026-09-29, while AutoCode built the dashboard (milestone MF1)

## 1. Different failures looked like one repeated failure

`autocode_failures` grouped failures by stage, source revision and exception class, and
its count never reset. Almost every rejected report raises `ValueError`, and planning
stages are read-only, so the source never changes during planning. "Missing response to
concern P1", "... P2" and "Malformed test receipt P3" shared one entry, and the third
paused the run as `PAUSED_REPEATED_FAILURE` while the loop was still producing new
information. `failures.repeated(state, {stage, source_revision})` also ignored the error
class, and it gates Resolver diagnosis (`autopilot.py`) and completion recovery.

Fix: the ledger keeps every attempt, grouped as before, and also records each attempt's
signature (error text with only per-attempt paths and long digests normalized, plus the
kind of saved output) and the length of the current run of consecutive identical failures
for that stage and source. A success, a different error or a changed source ends the run.
The repeated-failure decision reads that run length (`failures.stalled`). Ledger entries
saved before this change keep their old cumulative count.
Tests: `tests/test_failures.py`.

## 2. A retry could carry an earlier out-of-scope edit to validation

The serial Builder's assignment gate checked only the current attempt's own tree delta.
A rejected attempt's out-of-scope edit stays in the tree for inspection, so the next
attempt passed the gate if it changed nothing, or only files inside its assignment; the
second case went straight to the Validator with the out-of-scope file still in place.
Partial work kept by timeout and capacity recovery was never gated at all.

Fix: `autocode_assignment` compares the tree the attempt left with the before-snapshot of
the task's earliest Builder attempt, and every changed or deleted path must be inside the
assignment. An earlier attempt whose snapshot cannot be read pauses the run rather than
counting as an empty delta. Restoring the stray file lets the next attempt through. The
saved no-progress route to validation (`recover_retained_candidate`) uses the same check.
Tests: the `test_serial_retry_*` cases in `tests/test_assignment_scenarios.py`.

## 3. A retained new file from an empty starting tree triggered another Builder retry

The assignment's starting snapshot can legitimately contain `files={}`. If an earlier
Builder left an in-scope new file and a later retry made no further edits, the saved
snapshots showed the retained file, but the no-diff path required an earlier Validator
PASS before it would send the file to validation. A new file from an interrupted or
rejected attempt has no such PASS, so the run spent another Builder retry.

Fix: for a no-diff retry, compare the earliest assignment snapshot with the current
saved after-snapshot. When the current source revision matches and the retained paths
are nonempty and entirely inside the approved assignment, send the actual retained
paths to the independent Validator as an unverified candidate. Preserve the Builder's
empty reported path list in the handoff receipt. An empty or missing candidate still
takes the existing no-progress path; the assignment scope gate still pauses on stray
edits or missing starting evidence. This route never declares the work accepted.

Test: `test_empty_starting_tree_retains_new_file_for_validation` in
`tests/test_assignment_scenarios.py` drives two fake Builder attempts through the
runner, including an initially empty source tree and a retained new file.

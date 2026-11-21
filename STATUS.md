# Recovery issues on Kilo: status (2026-11-20 23:20 UTC)

Thread "Fix recovery issues on Kilo". Issues: #3887, #3938, #3879, #3872, #3870, #3892, #3890, escalation half of #3954.

## Clusters
- **D, run identity (#3892, #3890)**: IN PROGRESS. Branch `claude/project-thread-j0d3hi-runs`. M1 and the M2 polling changes are implemented; the remaining broader review, final verification and CI are pending. Issues labelled `in progress`.
- **A, role labels in stop/rejection text (#3879, #3872)**: not started.
- **B, re-observe recovery causes (#3887, #3938, #3954 escalation half)**: not started.
- **C, report-repair freshness view (#3870)**: not started.
A, B, C wait until #3868 (group 3b) and group 4a (#3852/#3880/#3869 + view half of #3954) merge; they share recovery/report-repair files.

## Cluster D run (on Ankur's Mac, Kilo engine, saved stage models; uses no Claude)
- Controller: `/Users/ankurkothari/Documents/workspace/autocode-recov-controller` (detached at master 38e247b23, own .venv)
- Task worktree: `/Users/ankurkothari/Documents/workspace/autocode-recov-runs` (branch above, `--in-place`)
- Run dir: `autocode-recov-runs/.autocode/runs/20261120-120044-fix-github-issues-3892-and-3890-in-this-reposito-5147cd67`
- Brief: `/private/tmp/ac-recov/brief-runs.md`; log `/private/tmp/ac-recov/runs.log`; tmux session `ac-recov-runs6`
- Plan r7 approved (agent review under the AGENTS.md delegation): new `tools/autocode_run_identity.py`; capture refuses `--output` under `.autocode/runs/<id>/` with no checkpoint before mkdir/exec and names the expected owner; rework_policy rejection names the expected run; events --follow / wait re-resolve the moved run on every poll. 15 criteria, milestones M1 (capture) and M2 (events/wait).
- At 23:20 UTC: M1 built, regression checks running locally (no model active).
- The Completion Reviewer later found that an existing run-shaped directory without a checkpoint was still accepted and that the new identity unit module could not collect on the original source. After reviewing the fixture request, the operating agent authorized writing the canonical fixture checkpoint while keeping every assertion. The correction refuses checkpointless output directories before execution, lets candidate-only unit coverage skip cleanly on master, and adds a public CLI regression. The three-module focused suite passed 88 tests; three public CLI regressions fail on master.
- The paused run could not resume after those reviewed source edits: the public resume returned "AutoResolver diagnosis requires a current approved task, source and saved source report" and the run stayed at `RESOLVER_PENDING`. The source change invalidated the diagnosis. Do not blindly retry or alter its evidence. The original worktree and attempt remain preserved.
- Ruff and mypy for the changed runtime modules now pass; the missing type annotations in the inherited WIP were corrected.
- Events and wait now resolve the run through the same authenticated identity helper at each poll. Two public CLI-entry regressions using a real Git worktree fail on master and pass on the branch, including the copied event history and the actual destination in the wait result. The six-module focused suite passed 135 tests.
- Capture also rejects malformed checkpoint files. Its new CLI regression fails on master and the 27 targeted capture and identity tests pass on the branch.

## Resume / continue
Status: `autocode-recov-controller/.venv/bin/autocode --run-dir <RUN> --status`
Continue after any saved action: `/private/tmp/ac-recov/cont-runs.sh` (= `autocode --run-dir <RUN> --workspace <WS> --no-chat`)
Approve a plan: `AUTOCODE_APPROVE_GOAL_TOKEN=<token> autocode --run-dir <RUN> --workspace <WS> --approve-goal -`, then continue.
When the run completes: commit in the task worktree (explicit paths), push `claude/project-thread-j0d3hi-runs` (fast-forward over this snapshot or force-with-lease if it diverges), open a draft PR, merge when `ci-required` is green, remove the `in progress` labels.

## Notes
- Kilo's OpenAI login is an API key; works since #3961. After upgrading the controller the run paused PAUSED_TRANSPORT_CHANGED; resumed with `--resume-paused --accept-transport-change`.

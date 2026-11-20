# Recovery issues on Kilo: status (2026-11-20 23:20 UTC)

Thread "Fix recovery issues on Kilo". Issues: #3887, #3938, #3879, #3872, #3870, #3892, #3890, escalation half of #3954.

## Clusters
- **D, run identity (#3892, #3890)**: IN PROGRESS. Branch `claude/project-thread-j0d3hi-runs` (this commit is a WIP snapshot of the Builder's milestone M1 files; M2 events/wait not built yet). Issues labelled `in progress`.
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

## Resume / continue
Status: `autocode-recov-controller/.venv/bin/autocode --run-dir <RUN> --status`
Continue after any saved action: `/private/tmp/ac-recov/cont-runs.sh` (= `autocode --run-dir <RUN> --workspace <WS> --no-chat`)
Approve a plan: `AUTOCODE_APPROVE_GOAL_TOKEN=<token> autocode --run-dir <RUN> --workspace <WS> --approve-goal -`, then continue.
When the run completes: commit in the task worktree (explicit paths), push `claude/project-thread-j0d3hi-runs` (fast-forward over this snapshot or force-with-lease if it diverges), open a draft PR, merge when `ci-required` is green, remove the `in progress` labels.

## Notes
- Kilo's OpenAI login is an API key; works since #3961. After upgrading the controller the run paused PAUSED_TRANSPORT_CHANGED; resumed with `--resume-paused --accept-transport-change`.

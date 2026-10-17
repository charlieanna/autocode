# Kilo live qualification after #1504

**Not fully qualified.** The owner requested an actual VS Code flow and live
qualification on the existing Kilo token-plan routes before #1504 became ready.
GitHub records #1504 merged at 2026-10-17 06:28:32 UTC (October 16, 11:28 PM PDT)
into the parent branch, followed by #1491 merging into master. Those merges
were performed outside this qualification operation, before these checks passed.
A merged PR cannot be restored to draft. This follow-up stays draft.

## Scope and host

The live checkout remained pinned to PR head
`123763894f0fd904b94760b1f52595eb6630ee5c`; a separate worktree holds this help
and evidence update so an active provider never observes edited runtime files.
The new Linux host is `autocode-pr1504-live`, image
`autocode-pr1425-qualification:20261016`, Kilo **7.8.3**, uid 501, host PID
visibility for the existing supervisor, and the existing private Kilo auth file
mounted read-only. Only `zai-coding-plan` and `xiaomi-token-plan-sgp` were enabled.
No credentials were changed or migrated into AutoCode's direct API setup.

This is an actual Kilo attempt, not a fixture or a qualification of the new direct
API transports. The launch omitted `--evidence-provenance live`, so the run's
launch-fixed provenance disclosure remains `unknown`; it was not edited after
launch. The wiring and native request evidence are described here separately.

## Command and routes

An explicit qualification driver uses only `autocode_taskrun.TaskRun` and the
public CLI. The command is:

```sh
docker exec --workdir "$PROJECT" autocode-pr1504-live \
  python3 "$QUALIFICATION/qualify.py" --i-authorize-live-model-spend start
```

It calls `TaskRun.start(PROJECT, "hi", ...)`, equivalent to
`autocode "hi" --in-place --no-chat --spec-first --joint-planning`, with
`--provider kilocode` and these saved role choices:

| Role | Model | Effort |
| --- | --- | --- |
| Requirements / recognizer | `zai-coding-plan/glm-5.3` | medium |
| Requirements revision | `zai-coding-plan/glm-5.3` | high |
| Planner / Plan Reviewer | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | high |
| Builder | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | medium |
| Tester | `zai-coding-plan/glm-5.3` | high |
| Completion Reviewer | `zai-coding-plan/glm-5.3` | medium |
| Resolver | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | high |
| Investigator | `zai-coding-plan/glm-5.3` | saved default |

The exact role flags and pins are those in the
[earlier command](2026-10-16-vscode-chat-qualification.md#earlier-full-task-token-plan-attempt).
Bounds for this attempt: `--max-seconds 3600 --max-stage-seconds 900
--max-tool-seconds 900 --max-idle-seconds 300 --max-cost-usd 3`; Planner, Builder,
Tester and Completion Reviewer models remain pinned. Historical comparison
costs are not provider-reported billing or token-plan charges.

## Observed result

Run: `20261017-063138-hi-0d5a466f`.

- Both model access preflights passed on the requested routes.
- **Greeting intake passed in 124.979 seconds.** `hi` returned a Requirements
  question asking what to build/change, `WAITING_FOR_USER`, and no displayed
  plan or approval. Only recognizer and requirements-gather task events existed.
- The driver answered with a small deterministic standard-library `greet.py`,
  unittest regression tests and README. The saved answer took 14.891 seconds;
  continuing refreshed Requirements in 122.595 seconds and produced the
  spec-first requirements review. The operator read that public question and
  answered `plan` (8.091 seconds), then continued planning.
- Repository discovery completed. Plan Reviewer reached MiMo/high through Kilo.
  Its first completed native step made two tool calls and reported 20,232
  reasoning tokens. The final report did not arrive before the 900-second
  stage deadline. Receipt: `cause=stage_deadline`, `phase=stopped`,
  `monotonic_elapsed_seconds=900.134002`, `cleanup_error=null`, with no indicated
  host/VM suspension. The runner retained the partial attempt before starting
  one configured fresh recovery on the same route.
- A public `intervention submit --kind pause` request,
  `pr1504-inspect-review-timeout`, was accepted to stop at the next saved
  boundary before another stage/retry.
- The recovery returned a native response in 715.654 seconds with clean provider
  shutdown. Post-stage capture then crashed because Git reported dubious
  ownership of the Docker-mounted workspace: uid 501 saw workspace uid 0 while
  its `.git` directory remained uid 501. This is a qualification-host setup fault.
  The correction added only this test repository's absolute path to
  `safe.directory` in the disposable container's Git config; no wildcard or
  user-host Git configuration was changed.
- The next public advance reconciled the already-finished response, accepted
  `plan-challenge-02.json` and applied the queued pause in 14.561 seconds,
  selecting `glm_revise`. No model was called to recover that report. The
  accepted reviewer identified two fixable plan gaps (the small-implementation
  constraint's source trace, and tests for the usage line's expected invocation).
  After inspection, the operator resumed the existing run with the same bounds.
  The remainder of qualification is pending.

No plan has been approved. Builder, Tester, Completion Reviewer, independent
acceptance checks and same-run live follow-up have not passed in this attempt.
Run output and the independent black-box oracle remain ignored under
`.scenario-runs/pr1425-real-requirements-20261016/pr1504-live/`.
Bundle manifest: pending final boundary.

## Native VS Code

The Mac was unlocked. Automation opened the actual Extension Development Host
and the fresh `pr1504-kilo-live` folder. The folder was in Restricted Mode.
Automatic approval review rejected clicking **Trust** because it enables tasks,
debugging, workspace settings and extensions and requires action-time
confirmation. Explicit approval was requested for this folder only and remains
pending; no alternative path was used to bypass that rejection. The full native
flow is therefore unverified. Existing chats and credentials were preserved.

## Focused correction

`autocode connections --help` still advertised Bedrock after the owner
removed it from scope. The follow-up corrects that help to the implemented
MiMo/Z.ai/OpenAI/Claude API choices. It changes no transport, model routing,
budget, approval or runtime behavior. Focused checks: `python -m unittest tests.test_connections
 tests.test_cli_documentation` passed 13 tests in 2.266 seconds; direct CLI help
checks passed. No further model run was needed for this help-only correction.

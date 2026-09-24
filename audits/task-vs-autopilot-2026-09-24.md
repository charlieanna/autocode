# Live task vs Autopilot — 2026-09-24

Two real runs of the same greeting task, from checkout `153d89c`. No fixture Builder was used. OpenCode 1.18.32 called the saved providers.

The `autocode` and `autopilot` commands both enter the same controller. This comparison is two independent live executions, not two different engines.

Task, given to each command in its own empty Git repository:

> Build greet.py, a Python 3 CLI in this repository. With no arguments it prints exactly Hello, World! and exits 0. With one argument NAME it prints exactly Hello, NAME! and exits 0. Add test_greet.py using only unittest for both cases. No third-party packages, no network, and no other product files.

```sh
.venv/bin/python tools/autocode.py "<task>" --workspace /tmp/autocode-live-compare/autocode --in-place --no-chat --reasoning-effort medium --max-iterations 8 --max-parallel-builders 1 --max-seconds 2400
.venv/bin/python tools/autopilot.py "<task>" --workspace /tmp/autocode-live-compare/autopilot --in-place --no-chat --reasoning-effort medium --max-iterations 8 --max-parallel-builders 1 --max-seconds 2400
```

Roles were the defaults: requirements and planner `zai-coding-plan/glm-5.3`, plan reviewer `cursor-acp/claude-opus-5-5-high`, builder `openai/gpt-5.6-terra`, validator and completion owner `openai/gpt-5.6-sol`, reasoning effort medium where the command set it.

## What each run produced

| | `autocode` | `autopilot` |
| --- | --- | --- |
| Run | `20260924-153110-build-greet-py-a-python-3-cli-in-this-repository-6a71d210` | `20260924-153110-build-greet-py-a-python-3-cli-in-this-repository-960ebded` |
| Elapsed to stop | 317s | 377s |
| Requirements | Saved. Outcome is greet.py plus test_greet.py, with R1–R4 covering the file, both exact prints, and unittest. | Saved. Same outcome and the same four requirements, worded independently. |
| Planner draft | Rejected. Report repair ran twice. | Rejected. Report repair ran twice. |
| Status | `PAUSED_REPEATED_FAILURE` | `PAUSED_REPEATED_FAILURE` |
| Next stage | `astra_discovery` | `astra_discovery` |
| Rejection | Requirement R1 is not covered by a behavior or criterion | `$.requirement_trace[0]` is missing `requirement_id` |
| `greet.py` | Not written | Not written |
| `test_greet.py` | Not written | Not written |

Neither run reached approval, the Builder, or validation. The runner archived the rejected planner output and refused another automatic repair of the same error.

The requested product was a CLI that prints `Hello, World!` and `Hello, NAME!`. Both runs produced a requirements report for that CLI and then stopped because the planner's draft did not satisfy the report schema.

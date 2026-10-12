# Product fixes from the gocode-cross scorecard (2026-10-12)

Evidence: `.scenario-runs/gocode-cross-scorecard/` (not committed). Partial 3×3;
0 FALSE_COMPLETE. Fixes below are universal stage/provider boundaries, not
scenario-specific patches.

## 1. Plan Reviewer schema rejected by OpenAI/gocode — **fixed**

**Seen on:** greenfield-todo-cli ×3 → `WAITING_FOR_USER` after Plan Reviewer
(`astra_challenge` / `plan-challenge-01`) exit 1; Resolver escalates
`operational_recovery` with no automatic replay.

**Cause:** provider jsonl ends with:

```text
Invalid schema for response_format 'codex_output_schema': In context=(
  'properties', 'brief_observations', 'items', 'properties', 'criterion_ids'
), 'uniqueItems' is not permitted.
```

`brief_observations` proposals carry `"uniqueItems": True` (and `minItems`).
OpenAI Structured Outputs (via LiteLLM/gocode) rejects `uniqueItems`. Autocode
still validates uniqueness locally after the reply.

**Fix:** strip only `uniqueItems` in `model_output_schema`
(`tools/autocode_util.py`) before `--output-schema` is written. Keep
`minItems`/`maxItems`/`pattern` (supported on the wire and used in Autocode
validation of the same file). Unit test: `tests/test_model_output_schema.py`.
Always-on fake rejection when a schema still carries `uniqueItems`
(`scenarios/harness/fake_codex.py`).

## 2. Report-delivery prompt contradicts final-message persistence — **fixed**

**Seen on:** every gocode-cross run that reached Requirements — empty or
blocker-heavy contracts with `REPORT_WRITE` / `report_write` access blockers;
often followed by literal-gate `PAUSED_INVALID_OUTPUT` or Plan Reviewer failure.

**Cause:** `~/.config/autocode/providers/gocode.toml` ran `codex exec … -o …`,
so Codex persists the final message, but `-o {report}` was buried inside a
`sh -c` string. Autocode only switched to "return it as your final response"
when `sandbox_adapter = "codex_artifacts"` was set. Otherwise the prompt said
"Use a permitted tool to write that JSON file" under `--sandbox read-only`,
which the sandbox rejects. Luna then returned an empty contract plus a
permission blocker about the report path.

**Fix:** treat argv-visible `-o {report}` / `--output-last-message {report}` as
final-message persistence (`codex_sandbox.persists_final_message`) and use the
final-response wording in `command.prompt_for_schema`. Document and rewrite the
GoCode provider config so `-o {report}` is a top-level argv slot. CLI proof:
`tests/test_report_delivery_cli.py` with `SCENARIO_FAKE_READONLY_REPORT`.

## 3. Investigator auth timeout reported as a bare exit — **fixed**

**Seen on:** bugfix-iso-weeks #3 → `PAUSED_JOB_FAILURE` on `investigate_bug`
exit 1.

**Cause:** provider jsonl trailing lines:
`provider auth command …/gocode timed out after 5000 ms` (reconnect 5/5), no
terminal report. The helper's `timeout_ms` was unset in `~/.codex/config.toml`
(the other gocode provider uses 45000).

**Fix:** `tools/autocode_provider_auth.py` names the stop from trailing error
lines; `autocode_job_failure` kind `provider_auth`; stage-exit message prefixes
the same reason. No automatic replay (uncertain attempt). Docs recommend
`timeout_ms = 45000` under `[model_providers.gocode.auth]`. CLI proof:
`tests/test_provider_auth_cli.py` with `SCENARIO_FAKE_LOGIN_TIMEOUT` (named
without the word AUTH so `autocode_agent_env.scrubbed` does not withhold it).

## Verification

| Check | Result |
| --- | --- |
| Unit / CLI (`test_model_output_schema`, `test_command_provider`, `test_provider_auth`, `test_provider_auth_cli`, `test_report_delivery_cli`, `test_architecture`) | PASS |
| Fake `greenfield-todo-cli`, `feature-timesheet-by-project`, `bugfix-iso-weeks` | PASS |
| `--changed --all-fast` (isolated `CODEX_HOME` + `XDG_CONFIG_HOME`) | 2 modules FAILED on ambient pyenv shims (`tests.test_toolchain`, `tests.test_verification_copy`: `Selected tool is not a native Mach-O executable: ~/.pyenv/shims/python`) — unrelated to these fixes |

### Live confirmation (gocode-cross)

Commands (billing keys unset; local `gocode.toml` exposes `-o {report}`):

```sh
.venv/bin/python scenarios/run.py run greenfield-todo-cli --profile gocode-cross \
  --i-authorize-live-model-spend --out .scenario-runs/gocode-cross-product-fixes
.venv/bin/python scenarios/run.py run feature-timesheet-by-project --profile gocode-cross \
  --i-authorize-live-model-spend --out .scenario-runs/gocode-cross-product-fixes
```

| Scenario | Verdict | Duration | Evidence dir | Fix evidence |
| --- | --- | --- | --- | --- |
| greenfield-todo-cli (change) | PASS — TASK_COMPLETE; oracle 11/11 | ~36 min | `20261012T235203Z-greenfield-todo-cli-gocode-cross-_c2a3w5c` | discovery `access_blockers=[]`, 6 criteria; prompt uses final-response wording; `astra_challenge` exit 0 |
| feature-timesheet-by-project (change, 1st) | HONEST_BLOCKER — oracle 5/6 (`project_tests_pass`) | ~10 min | `20261013T002847Z-feature-timesheet-by-project-gocode-cross-49qrurb7` | product-fix path clean (blockers `[]`, `astra_challenge` exit 0); later model variance |
| feature-timesheet-by-project (change, retry) | PASS — TASK_COMPLETE; oracle 7/7 | ~9 min | `20261013T010700Z-feature-timesheet-by-project-gocode-cross-e2oa0w4i` | discovery blockers `[]`; `astra_challenge` exit 0 |
| feature-timesheet-by-project (`origin/master`) | PASS — TASK_COMPLETE; oracle 7/7 | ~27 min | `gocode-cross-product-fixes-master/20261013T003926Z-feature-timesheet-by-project-gocode-cross-r4pz35pv` | same local `gocode.toml` with argv-visible `-o {report}`, but master still produced a `REPORT_WRITE` access_blocker on discovery report-repair — confirms fix A is the change that removes that contradiction |

Provider: gocode. Models: profile `gocode-cross` (Luna requirements/planner, Sol reviewer/validator/completion, Terra builder, Astra resolver).

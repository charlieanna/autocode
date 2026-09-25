# Reliable completion baseline — 2026-09-21

The installed `autocode` command passed **10 workflow tests** in 80.909 seconds:

```sh
AUTOCODE_TEST_CLI=/Users/ankurkothari/.local/bin/autocode python3 -m unittest tools.test_planning.JointFlow tools.test_opencode.OpenCodeFlow
```

These exercise default joint planning, exact-plan approval, independent verification,
rework, unresolved decisions, saved-stage recovery, explicit plan revision, provider
quota handling, and completion. They use temporary Git projects and fake providers;
no live application run or real model request was started. This is a mechanics
baseline, not evidence of model-driven product delivery. Development priorities and
the remaining live-trial evidence are recorded in [RELIABILITY.md](RELIABILITY.md).

A separate source regression pass also passed **74 tests** in 117.777 seconds:

```sh
env -u AUTOCODE_TEST_CLI PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tools.test_subprocess tools.test_milestone_checkpoints tools.test_intervention_ordering tools.test_report_repair tools.test_final_workflow
```

It covers completion gates, milestone evidence, intervention ordering, invalid-report
recovery, and saved workflow routing. The first sandboxed attempt could not inspect
worker processes; the rerun passed with process inspection enabled. No guard was
disabled. All projects and provider responses were isolated fixtures; no runtime fix
was needed from these checks.

# Activity-aware timeout validation — 2026-09-20

Separate inactivity/tool deadlines, preserved hard caps, live activity diagnostics
and bounded recovery are implemented. The final offline suite passed **323 tests**;
three installed-wheel workflows also passed. See [the validation record](audits/activity-timeouts-2026-09-20/RESULTS.md)
for coverage, source fingerprints, deployment behavior and transport limitations.

# Milestone checkpoint validation — 2026-09-20

Enforced milestone evidence, bounded replanning, milestone budgets, status and
safe adoption are implemented. The full offline suite passed 261 tests; the final
targeted milestone suite passed 19 tests, and three installed-wheel workflow tests
passed. See [the validation and rollout record](audits/milestone-checkpoints-2026-09-20/RESULTS.md)
for exact coverage, limits, and the IdleCampus/ddia-tutor migration state.

# Repair validation — Autocode 0.5.4 — 2026-09-19

All five audited OpenCode integration defects are fixed. The runner also rechecks
completed work, detects executable-mode and submodule changes, preserves recovery
evidence across checkpoint failures, accounts for rejected/recovered stage time,
checks resumed session identity, and supports explicit stopped-stage abandonment.

- Full source suite: **120 tests passed**, 73.570 seconds.
  `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py tools/test_process.py`
- Offline wheel build and isolated install: passed; packaged runner sources matched
  the checkout byte-for-byte. **3 installed-wheel workflow tests passed**, 13.725 seconds.
- Global editable installation refreshed to **0.5.4**. Its complete OpenCode fixture
  workflow passed from an unrelated project: **1 test**, 3.752 seconds.
- Compilation, CLI help and changed-source whitespace checks: passed.
- Native OpenCode **1.18.31** checks confirmed preserved inline denies, detected
  custom-directory config drift and target-project model lookup. A real native bash
  worker was stopped on timeout and could not write after the workspace lock released.
- Native malformed-report recovery and an archive-checkpoint failure regression
  confirmed evidence remains recoverable and retries require explicit resumption.

The tests used temporary Git repositories, fake providers and a loopback server;
there were no hosted model requests in this repair pass. Process inspection was
enabled for the supervision tests. OpenCode permissions and process supervision
remain distinct from an OS sandbox. Existing application runs were not resumed.

Details and raw evidence: [repair resolution](audits/opencode-repair-2026-09-19/RESOLUTION.md).

# OpenCode integration validation — 2026-09-19 (historical)

OpenCode is the default engine for new runs; `--engine opencode` remains available
as an explicit selection. Its default role mapping is `openai/gpt-6-astra` for the
Plan Reviewer (Astra route), `openai/gpt-5.6-terra` for the Builder (Terra route),
and `zai-coding-plan/glm-5.3` for the Validator (Sol route), with separate
persisted sessions for each role.

Executed against the final source:

- `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py`:
  **100 tests passed**, 29.246 seconds. OpenCode subprocess flows omitted the
  engine flag to verify the new default; Codex flows selected `--engine codex`.
  The suite also verifies that OpenCode role prompts prohibit reading or searching
  parent directories, sibling projects, and ancestor instruction files.
- `python3 -m compileall -q tools`, direct syntax checks and CLI `--help`: passed.
- Offline wheel build with `pip wheel --no-deps --no-build-isolation --no-index`:
  passed. The wheel was installed into a temporary virtual environment, where
  **5 OpenCode CLI workflow tests passed**, 12.612 seconds.
- The opt-in `python3 tools/opencode_smoke.py --run-live` check passed against
  installed OpenCode **1.18.31**, using the existing provider authentication.

The live check made two small requests in a temporary empty Git workspace. The
Plan Reviewer request returned a valid JSON object through OpenAI. The Validator
request returned a valid JSON object through
Z.ai after executing a harmless `printf` command; its native event contained the
matching command, output and exit status zero. These checks verify the two provider
connections and native event/report handling. They do not constitute a complete
model-driven product build.

Offline coverage includes native command evidence and token accounting, malformed
or incomplete output rejection, terminal-message parsing, permission configuration,
config drift, metadata timeouts, completed-stage recovery, explicit session reuse,
distinct Builder/Validator sessions and refusal to change engines on an existing run. The
subprocess workflows cover approval, implementation, independent validation, rework,
limits and resumption using a fake OpenCode provider.

OpenCode review roles deny native edit tools, and the runner checks for source changes
after review stages. OpenCode does not provide Codex's OS sandbox; native tool
permissions remain responsible for shell and custom-tool access. No global OpenCode
configuration or authentication files were changed. This adapter supports OpenCode
1.x; it refuses 2.x rather than assuming protocol compatibility.

# Build-brief workflow validation — 2026-09-19 (historical)

The runner now follows the rough idea → Requirements conversation → approved build brief →
bounded Builder task → independent Validator validation → Completion Owner decision workflow.

Executed against the final source:

- `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py`:
  **82 tests passed**, 18.754 seconds.
- `python3 -m compileall -q tools` and CLI `--help`: passed. Final changed Python
  sources also passed direct syntax and trailing-whitespace checks.

The offline subprocess tests cover brief feedback and renewed approval, an intentionally
broken implementation followed by a Validator FAIL and a Completion Owner REWORK, successful revalidation,
artifact review in chat, pause before approval, an iteration-limit pause with the
correction task retained, and resuming without replaying completed work. They inspect
saved prompts to verify that execution roles receive the identical approved brief and
that the Validator receives the current task, full Builder report and matching workspace revision.
The fake Validator provider executes the greeting program's success and failure cases.

Unit coverage additionally checks complete brief fields and milestone coverage,
feedback provenance, stale task rejection, explicit end-to-end evidence, forged
evidence rejection, stale milestone evidence, no human-review request before passing
automated evidence, preservation of legacy v3 briefs and completed discovery output,
and honoring `--pause-after-stage` after chat answers.

The subprocess suite requires process inspection for the runner's duplicate-process
guard. It passed with approved escalation after the outer sandbox denied that check.
Tests use temporary Git workspaces and a fake Codex provider. No live model execution,
real project run or migration was performed for this change. These checks establish
runner behavior, not the quality of a real model's product questions or code review.

# Standalone validation — 2026-09-18 (historical)

The standalone source was extracted into an isolated temporary folder and extended
there. No task in the original project was launched, migrated, paused or restarted
by this work. No learner data, course material or application code was edited.

## Executed checks

- `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py`:
  **52 tests passed**, 6.191 seconds in the final full source run.
- `python3 -m compileall -q tools`: passed.
- Offline wheel build with `pip wheel --no-build-isolation --no-deps --no-index`:
  passed; Python 3.14.6 / setuptools 81.0.0 in this environment.
- Wheel installed into an isolated virtual environment with `--no-index --no-deps`.
- The installed `autocode` console entry point exercised from an unrelated Git
  workspace using `AUTOCODE_TEST_CLI` and `tools/test_subprocess.py`: passed.
- Installed `codex exec --help` and `codex exec resume --help` inspected for the
  model, sandbox, resume, schema, JSONL and output flags. Official
  [non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
  was also consulted. Help inspection is not a live provider compatibility test.

The subprocess test uses a deliberately fake Codex executable. It runs the actual
runner and role subprocesses, validates actual JSON schemas, writes a tiny greeting
program, executes valid/invalid-input checks, and uses actual CLI user events through
discovery, approval, implementation, Validator validation, human review and completion.
The same test runs against both source and the installed wheel. No network, provider
inference, credentials, paid model calls or production workspaces are involved.

The process guard correctly refused to run under the outer sandbox because `ps`
was denied. The offline subprocess tests were then run with approved escalation,
preserving the guard rather than disabling it.

## Gate coverage

Regression tests cover: initial read-only discovery; persisted answers; no repeat of
an answered question ID; answer/approval separation; exact displayed revision tokens;
edit invalidation; tampered/stale goal refusal; explicit delegation provenance;
material ambiguity/permission/contradiction/infeasibility/scope pauses; contract deltas;
stable criteria; all-criterion evidence; forged event references; source/evidence
drift; artifact-specific human review; blocking findings; deferred optional work;
refusal of extra implementation after current completion evidence; iteration/time/
usage/no-progress limits; atomic checkpoints; writer locks; reload after lock
acquisition; active/uncertain migration refusal; original task/session/artifact/answer
preservation; completed-stage recovery without replay; model/session/sandbox command
mapping; transport refusal; full command evidence and failure capture.

Two inherited tests were updated for intentional behavior changes: the old full-loop
test now records an explicit displayed-goal approval before execution, and the launch
test expects workspace-write sandboxing instead of `--approve-for-me`. Existing
assertions for independent evidence, checkpoint recovery and model/session identity
remain. Additional tests exposed and addressed fabricated criterion event references
and stale state reads before acquiring the lock.

## Limits

Live Requirements interviewing, Builder implementation and Validator semantic validation have not
been exercised against a real model in this extraction. Model output quality and
completeness of behavioral evidence still require an actual bounded trial. Headroom
is still disabled and unverified. No production migration or adoption was performed.

This is a local trusted-workspace tool. It enforces state transitions and approval
gates; it cannot prove that every relevant ambiguity was noticed or compile arbitrary
natural-language permissions into OS policy. Codex sandbox and connector settings
remain responsible for individual tool permissions. Windows-native support was not
tested; inherited POSIX locking/process inspection requires macOS/Linux or WSL.

## Extraction provenance

Initial source SHA-256 values recorded when copying the original runner:

| Original file | SHA-256 |
| --- | --- |
| `tools/autocode.py` | `6af6a4c7da274d59c317afa3e1789c550da417dd316f360fc4887c0c0b8b0d13` |
| `tools/autocode_support.py` | `d52ec563336856c375cf3d8c82ee0e67c6d233777b0f888b8d9714b0fd90f419` |
| `tools/test_autocode.py` | `cb2886f87f5e6527a1edde911d0c52dcc04e0a76c7c55fbf2525b3c090ea9f7d` |
| `tools/README-autocode.md` | `4dce80a64e6b4e6e6db923e22b373975cf21f258cca19c118b8b87291308a29b` |

The original project files changed independently during this work, including new
discovery-related code and schemas. Those changes were not overwritten, imported
into this extraction or claimed as this task's work. Consequently, the extraction
is based on the recorded starting snapshot, not byte-identical to the current
project runner. All patches in this task targeted the separate extraction folder.

# Public proof inputs in the actual Investigator copy

Issue #224 already had an opt-in preparation coordinator and identity preflight.
Its maintained positive test inspected the preparation copy, then started a
build. It did not read inputs from the bug Investigator's actual copy.

That copy used a different recursive copy rule. A new public TaskRun regression
reached investigate_bug and ran a checker in its supplied investigation_workspace.
Before the fix the checker failed because an ignored secrets.env fixture was
present. After the fix it reads one tracked proof file and an ordinary untracked
patch with exact regular-file type, mode, size and SHA-256; preserves dirty
application source; and finds no ignored credentials, dependencies, outputs,
Git metadata or runner state.

Actual Git-backed Investigator copies now select tracked and ordinary untracked
paths and their ancestors. Nested initialized Git components use their own
inventory. A tracked input beneath an ignore rule remains included without
copying its ignored siblings. A nonignored symlink cannot materialize an ignored
target. Existing non-Git helper behavior and virtualenv interpreter reuse remain
available. Every attempt receives a fresh copy; previous attempts remain intact.

The existing coordinator rejects ignored declared inputs before TaskRun or any
provider starts and still leaves a successful build awaiting plan approval.
Input access establishes no source-fix or product acceptance credit.

Reproduce offline:

```sh
.venv/bin/python -B -m unittest tests.test_pilot_investigator_cli \
  tests.test_investigation_workspace tests.test_autocode_input_preflight \
  tests.test_input_inventory_guard tests.test_autocode_pilot_prep
```

Live qualification used three fresh native Investigator runs through public
TaskRun.start with OpenCode: GLM-5.3, MiMo-2.6-Pro and GPT-6 Sol. Each ran the
checker in its supplied copy and passed a separate execution of the unchanged
original checker against that copy. Original source and protected fixture files
stayed unchanged. These are input-access investigations, not complete builds.

An initial GLM route was refused before any paid stage because its Planner and
Plan Reviewer were identical. The corrected GLM run executed the check but
returned malformed JSON; normal report-only recovery supplied the accepted
report. The qualification script initially selected the archived unrepaired
report, so a fresh offline receipt verified the accepted repaired report and
original command events without further model spend. Original attempt receipts
remain unchanged. Each live run capped active provider time at 180 seconds.

Ignored qualification receipts: pilot-input-copy/b040cfd6b5 (MiMo and OpenAI),
pilot-input-copy/3d2371646a (GLM plus report repair), and
pilot-input-copy/qualified-6feecc098e.json (separate qualification: 3/3 pass).

# Approved test scope omits a required package initializer

## Fix (2026-09-30)

Planning validation now checks executable unittest discovery commands before
approval. A missing package marker required by an explicit top-level directory
must already be present or belong to the milestone and initial task scope.
The rejected plan names the exact missing path and command so bounded report
repair can assign it before approval. Task reassignment applies the same check.
The assignment guard is unchanged. Commands without `-t` do not gain an
unnecessary initializer requirement. Focused plan/scope controls pass.

Historical reproduction on the unfixed revision follows.

Two live builds produced correct applications but stopped because the generated
plan required `python3 -m unittest discover -s tests -t .` while assigning only
one file inside `tests/`. The Builder added `tests/__init__.py` to make that
command work, and the assignment guard correctly rejected the extra source file.
This is a planning/prerequisite omission exposed by scope enforcement, not an
application correctness failure or evidence that the scope guard should be removed.

## Temperature rerun evidence

The ignored evidence root is
`.scenario-runs/20260929-codex-campaign/live/20260929T231421Z-ladder-01-temperature-cli-codex-only-guuwirde/`.
Its runner artifacts are under
`project/.autocode/runs/20260929-161422-build-a-python-standard-library-cli-invoked-as-p-adb7d26a/`.

- `result.json`: `HONEST_BLOCKER`, `PAUSED_INVALID_OUTPUT`, all seven oracle
  checks pass; the delivered project has 19 passing tests.
- `state.json`, `current_task`: affected paths are `temperature/`,
  `tests/test_temperature.py`, and `README.md`; validation explicitly requires
  unittest discovery with `-t .`. The original temperature brief requires that
  command, so this is not an oracle adding an undocumented requirement.
- `iterations/001/astra_finalize-01.json`: the approved plan contains the same
  path list and command. The package initializer was omitted during planning.
- `iterations/001/archived-terra-01-1c566d/terra-01.json`: the Builder reports
  adding `tests/__init__.py` and a successful 19-test run. The recorded tree delta
  confirms the added file.
- `iterations/001/investigate_stuck-02.json`: the Investigator returns an accepted
  `cause=needs_user`, `recommendation=pause` diagnosis and asks to authorize the
  missing path. It submits no probe; the run restores the original pause.

The first Investigator attempt also stopped after incorrectly reconstructing
artifact paths outside the workspace. Its archived `.jsonl` file records
`external_directory` auto-rejections for paths beginning
`.scenario-runs/20260929-161422-.../evidence/` instead of using the actual
project/run prefix above. One bounded retry recovered from that interruption.
It is a model path-construction error, not the final cause of this pause.

## Code boundary and independent check

`tools/units/autoplanner.py:595` already asks the Planner to include tests and
shared files in each milestone's affected paths. The final-review prompt at
line 631 asks for an executable milestone including tests and local fixes.
These plans still missed a prerequisite of their validation command.

`tools/autopilot.py:606` (`assert_within_assignment`) compares retained changes
against the declared assignment. `tools/autocode_assignment.py:21` and `:104`
match exact files or descendants of assigned directories: assigning
`tests/test_temperature.py` does not assign its sibling `tests/__init__.py`.
The rejection preserves edits for inspection and does not claim completion.

A scratch copy of the delivered temperature project independently reproduced the
conflict using the repository virtualenv interpreter: removing only
`tests/__init__.py` makes the required discovery command exit 1 with
`ImportError: Start directory is not importable`; restoring it makes all 19 tests
pass. Hashes confirmed the original delivered project was unchanged. The scope
predicate likewise excludes this marker under the saved assignment and includes
it when `tests/` is assigned.

## Related CSV failure and follow-up

The same initial scope defect appears in
`.scenario-runs/20260929-codex-campaign/live/20260929T224726Z-ladder-03-csv-validation-cli-codex-only-js99wmri/`:
the generated assignment names `csvcheck/__main__.py`, `tests/test_csvcheck.py`,
and `README.md`, selects discovery with `-t .`, and rejects the Builder's added
`tests/__init__.py`. All seven oracle checks pass. Its Investigator recovery
subsequently exhausts the budget after three external-directory interruptions;
unlike temperature, it never saves an accepted final diagnosis.

Before approval, check that the chosen test command is compatible with existing
and planned test-package scaffolding. Assign the marker explicitly or assign the
test directory when the milestone owns it. Preserve ownership checks, especially
for parallel work. A bounded plan-repair route could present a concrete minimal
scope correction without making the user diagnose an internal packaging omission.
Regression coverage should include a greenfield nonpackage test directory and
verify that the approved command and assigned paths can both be satisfied.

This is distinct from the
[Investigator evidence-staging defect](2026-09-29-investigator-evidence-staging.md):
that defect drops cited sibling run files from probe scratch trees. Temperature's
accepted diagnosis has an empty probe and succeeds without invoking that path.
No runtime fix or further live rerun was performed for this investigation.

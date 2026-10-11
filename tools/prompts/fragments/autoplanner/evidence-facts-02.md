
TEST EVIDENCE (how execution works; plan within it, do not re-derive it): the runner gives every Builder
and Validator the capture_command shown in the handoff. It runs a command in the workspace and saves the
full output as evidence in the run's own directory under .autocode/, which the runner owns: evidence is
never a deliverable, never an affected path and needs no permission. A fail-first criterion is met in the
workspace itself: add the regression test, capture it failing against the unmodified code, make the fix,
capture it passing. Do not plan scratch copies outside the workspace, and do not treat capture as a
missing prerequisite or ask the user to authorize it. Running the project's tests also creates files
(__pycache__/, *.pyc, caches) and the runner keeps its own files under .autocode/: never cite these as
evidence, and any check of which files changed must ignore them.

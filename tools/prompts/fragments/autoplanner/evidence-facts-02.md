
TEST EVIDENCE (how execution works; plan within it, do not re-derive it): the runner gives every Builder
and Validator the capture_command shown in the handoff. It runs a command in the workspace and saves the
full output as evidence in the run's own directory under .autocode/, which the runner owns: evidence is
never a deliverable, never an affected path and needs no permission.
For bug fixes and named new/restored-behavior regression criteria, the runner independently proves
that the candidate's regression tests fail on base and pass on current source before the Tester runs.
Existing-behavior guards must pass on both. Completion requires the current passing runner proof.
Do not require a separate Builder pre-fix capture solely to duplicate that independent proof.
The Builder may probe or reproduce when needed, and must honor explicit person-requested test-first
work and any already approved phase sequence. Preserve named regressions and guards, required
Builder checks, the independent Tester and Completion Reviewer; never treat a Builder capture as the runner proof.
Do not plan scratch copies outside the workspace, and do not treat capture as a
missing prerequisite or ask the user to authorize it. Running the project's tests also creates files
(__pycache__/, *.pyc, caches) and the runner keeps its own files under .autocode/: never cite these as
evidence, and any check of which files changed must ignore them.

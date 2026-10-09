# Goal fixtures claimed a passing suite without any tests

The GoalTests fixture reported `python3 -m unittest` as passing while its Git
workspace contained no tests. The runner's independent check replay correctly
rejected that claim: discovery ran zero tests and exited 5. TortureBase inherited
the same empty workspace and additionally wrote a changing counter as its
supposed greeting implementation.

This failure reproduces on clean master `b0e8d8ead272bb61b83971082cc8800ef1609335`
and the dashboard/Planner integration branch. The representative case is
`tests.test_goals.GoalTests.test_accept_completion_probe_carries_the_current_task_identity`.
Both used the same Python 3.14 installed environment and failed in
`autocode_check_replay.replay`; the fixture and checker were identical before
the repair. Empty discovery also exits 5 on the available Python 3.13 runtime.

The shared fixture now commits a real greeting CLI and two discoverable tests:
one checks a valid name and one checks empty and whitespace-only rejection.
TortureBase changes a source revision comment while preserving those behaviors.
Its source-invalidation tests can still distinguish revisions, and negative
test cases retain their explicit broken edits. Production replay, evidence
checks and completion gates are unchanged. No zero-test result is accepted.

The findings-controller and catalogue validation harnesses had the same empty
repository assumption and now use that executable fixture as well. A scenario
that explicitly exercises an empty starting tree removes the greeting fixture
before its assignment, keeping that boundary test meaningful.

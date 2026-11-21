# Activity status batch

The original handoff is absent from the branch and accessible project files. The branch begins at `ffcccd188` and contains unverified work for #3882 and #3883. Its commit explicitly says #3876, #3878 and #3885 remain unfinished.

The Requirements handoff now publishes all constraints and the editor keeps its full disclosure available even after a question when there are no assumptions or conflicts (#3878, #3885). Regression tests fail on master and pass here. A type conflict in the activity journal's NamedTuple was also corrected.

Run inventory now shows a matching, live launch record as starting without offering an action before the first checkpoint (#3876). Dead, unidentifiable and foreign launch records remain unreadable, as do symlinked checkpoints. The live-launch regression fails on master and passes here. The extension suite passed (751 passed, 22 skipped). Ruff, format and mypy passed. The broad suite revealed a noncanonical macOS temporary-path fixture, now corrected.

Regressions for #3882 and #3883 also failed on master and passed on the branch. After the fixture correction, 49 controller and failure-routing tests passed, as did 54 focused activity, setup, inventory and Requirements tests. All 34 architecture and run-state tests passed. Draft PR #3973 is open. Follow `ci-required` through a verified merge and close only resolved issues.

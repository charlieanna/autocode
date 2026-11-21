# Activity status batch

The original handoff is absent from the branch and accessible project files. The branch begins at `ffcccd188` and contains unverified work for #3882 and #3883. Its commit explicitly says #3876, #3878 and #3885 remain unfinished.

The Requirements handoff now publishes all constraints and the editor keeps its full disclosure available even after a question when there are no assumptions or conflicts (#3878, #3885). Regression tests fail on master and pass here. A type conflict in the activity journal's NamedTuple was also corrected.

Run inventory now shows a matching, live launch record as starting without offering an action before the first checkpoint (#3876). Dead, unidentifiable and foreign launch records remain unreadable. The live-launch regression fails on master and passes here. Focused suites, extension tests and static checks are in progress.

Review the existing activity changes against their issues and verify every batch change. Open a PR, follow `ci-required` through a verified merge and close only resolved issues.

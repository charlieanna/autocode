# Pytest Arena missed exceptions from one fixture

The original nine-check oracle tested failing finalizers on separate fixtures.
That exercises teardown routing, but does not detect `FixtureDef.finish`
discarding all but the first exception from callbacks on the same fixture.
A live candidate completed and passed all nine checks while still dropping one
such exception. A separate offline probe reproduced the missing exception.

The case now explicitly includes multiple `request.addfinalizer` callbacks on
one higher-scoped fixture. Three additional checks cover maxfail, stepwise and
setup failure. They assert callback execution order/counts, terminal teardown,
report ownership, exit status and every rendered exception. Matching exception
lines avoids accepting a message merely because it appears in traceback source.

The positive control is pinned at upstream commit
`4c5298c3954efa71436364efaf531d3851a6aeb4`, which includes finalizer aggregation
([#12048](https://github.com/pytest-dev/pytest/pull/12048)) and the reapplication
of early-stop routing ([#12279](https://github.com/pytest-dev/pytest/pull/12279)).
The original early-stop reference predates aggregation; the aggregation merge
alone also fails because early-stop routing had temporarily been reverted.

The problem description and oracle changed, so this requires a freshly prepared
Arena and cohort. Historical nine-check passes and timed-out attempts retain
their original verdicts; they are not twelve-check qualifications.

# Why the first build failed its tests

The first build kept the original guard in `greet.py`, which accepted an empty
or whitespace-only name. `test_greet.py` expects `usage: greet.py NAME` on
stderr and exit 2 for those inputs, so the Tester's run of the unchanged tests
failed. The rework rejected names that are empty after stripping whitespace,
and the same tests then passed.

Fix interval coalescing at touching boundaries

Fix intervalset.coalesce: it currently turns [(1,3),(3,5)] into [(1,5)], although
these half-open intervals merely touch. coalesce(intervals) must return a sorted
list of (start,end) tuples, merging intervals only when they overlap with positive
length, transitively. Touching boundaries stay separate. Accept any iterable of
pairs, do not mutate input data, and return [] for no intervals. Each endpoint
must be an integer (not bool), with start < end; any malformed pair or invalid
endpoint raises ValueError. Existing overlap behavior must remain correct for
unsorted, nested and repeated intervals. Add a regression test, preserve existing
tests and use only the Python standard library. Tests run with unittest discovery.

Run tests: `python3 -m unittest discover -s tests -t .`.

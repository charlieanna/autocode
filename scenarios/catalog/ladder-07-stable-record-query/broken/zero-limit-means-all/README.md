Add stable filtering sorting and pagination to a record API

Extend recordquery.query(records, *, status=None, sort_by="score", descending=False,
offset=0, limit=None). Existing calls return all records sorted ascending by score.
Add status filtering by exact case-sensitive equality when status is not None;
allow sort_by score (numeric) or name (case-sensitive string); sort stably so ties
keep input order, including in descending mode. Apply operations in this order:
filter, sort, then slice by offset and limit. Missing or None sort values always
come last in both directions, preserving their original order. offset must be a
nonnegative int excluding bool; limit must be None or a nonnegative int excluding
bool; invalid values and unsupported sort keys raise ValueError even on empty
input. limit=0 returns [], and offsets past the end return []. Do not mutate the
input list or its record dictionaries. Return a new list of matching records;
copying the dictionaries is optional. Preserve existing tests and add coverage.
Use the Python standard library only; unittest discovery is the test command.

Run tests: `python3 -m unittest discover -s tests -t .`.

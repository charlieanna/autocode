Fix CSV export escaping while preserving field contents

Fix csvexport.export(records): a contact named `Doe, Jane` currently produces an
extra column. This public function accepts an iterable of dictionaries and returns
a CSV string with header name,email,note in that order and CRLF record terminators,
including after the last record. Use standard CSV double-quote escaping so commas,
quotes, CR and LF round-trip in any field. Preserve all whitespace and Unicode
in field values. Missing fields or None become empty fields; ignore extra keys.
Empty input still returns the header. Values are strings or None. Do not mutate
records. Keep current ordinary output, preserve existing tests, add a regression
test, and use only the Python standard library. No files or network are involved.

Run tests: `python3 -m unittest discover -s tests -t .`.

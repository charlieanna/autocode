Merge JSON configurations recursively without aliasing

Build a standard-library package configmerge exposing `merge(base, override)`
and a CLI `python3 -m configmerge BASE_JSON OVERRIDE_JSON`. Both inputs must be
JSON objects. For keys present in both: recursively merge when both values are
objects, otherwise replace the base value with the override value. Preserve keys
only in base, add keys only in override; arrays replace as a whole and null is a
literal replacement, not deletion. merge must not modify either input, and its
output must share no mutable dict/list objects with either input, including
untouched and newly added subtrees. Invalid top-level values raise ValueError.
The CLI reads UTF-8 files and prints JSON with recursively sorted keys plus a
newline, exit 0, empty stderr. Missing/invalid files and nonobject roots exit 2,
nonempty stderr, empty stdout. Include README and unittest tests.

Run tests: `python3 -m unittest discover -s tests -t .`.

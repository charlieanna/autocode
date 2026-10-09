Build `python3 -m wordfreq [--limit N]`, a Python standard-library CLI that reads
all text from stdin and prints a JSON array followed by a newline. A token is a
maximal run of ASCII letters A-Z or a-z; lowercase tokens, count them, and emit
objects with exactly `word` and `count`. Sort by descending count then ascending
word. All whitespace, punctuation, digits and non-ASCII characters separate
tokens: `can't` becomes `can` and `t`. Empty input prints []. Optional --limit
must be a positive integer and truncates after sorting; omitted means all.
Invalid options/limits exit 2, diagnostic stderr, empty stdout. Success exits 0
with empty stderr. Include README and at least three unittest tests.

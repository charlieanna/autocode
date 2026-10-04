Implement greet.py using only the standard library. Exactly one nonempty,
non-whitespace name argument prints `Hello, NAME` followed by one newline on
stdout, with empty stderr and exit 0. No argument, more than one argument,
an empty name or a whitespace-only name must print exactly
`usage: greet.py NAME` followed by one newline on stderr, with empty stdout
and exit 2. Preserve the original test_greet.py unchanged. Execute the full
canonical suite `python3 -m unittest discover -v` before acceptance. Independent
validation and the final completion gate remain required.

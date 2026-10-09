Convert temperatures with explicit input validation

Build a Python standard-library CLI invoked as `python3 -m temperature VALUE UNIT`,
where UNIT is exactly C or F and the output converts to the other unit. Print one
line with one digit after the decimal point, a space, and the target unit: e.g.
`0 C` prints `32.0 F` and `212 F` prints `100.0 C`. Accept negative and fractional
finite numbers, including absolute zero (-273.15 C or -459.67 F). Reject values
below absolute zero, NaN/infinity, invalid numbers, unknown units and wrong
argument counts with exit code 2, a nonempty diagnostic on stderr, and empty
stdout. Successful calls exit 0 with empty stderr. Include README usage and
unittest tests runnable with `python3 -m unittest discover -s tests -t .`.

Run tests: `python3 -m unittest discover -s tests -t .`.

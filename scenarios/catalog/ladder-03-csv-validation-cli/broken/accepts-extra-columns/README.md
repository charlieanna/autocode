Validate CSV records with structured row errors

Build `python3 -m csvcheck PATH` using only the Python standard library. Read a
UTF-8 CSV with exactly the header `name,age,email` in that order. CSV quoting,
embedded commas/newlines, CRLF, and blank lines must work. Strip surrounding
whitespace from each data field. A valid name is nonempty; age is one or more
ASCII digits with numeric value 0 through 130; email has exactly one @, nonempty
text on both sides and no whitespace anywhere. Print one JSON object plus newline:
{"rows": number_of_data_records, "errors": [{"row": 2, "fields": ["age","email"]}]}.
Row numbers count logical records, with header 1 and first data record 2. Ignore
blank lines. Report every invalid row in input order and fields in header order.
Wrong-width rows have only ["columns"] as their fields. Exit 0 when errors is
empty, 1 for row validation errors, both with empty stderr. Missing files,
invalid UTF-8, malformed CSV quoting, a missing/wrong header or invalid CLI usage
exit 2 with nonempty stderr and empty stdout. Header-only CSV is valid with zero
rows. Include README usage and unittest tests.

Run tests: `python3 -m unittest discover -s tests -t .`.

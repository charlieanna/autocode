Extend calendardates with parse_date(text) and inclusive_dates(start,end).
parse_date accepts only a string containing exactly ten ASCII characters in
YYYY-MM-DD format and returns datetime.date for a valid Gregorian calendar date,
years 0001 through 9999. Reject whitespace, compact dates, week dates, unpadded
components, Unicode digits, impossible dates, and nonstrings with ValueError.
inclusive_dates accepts two strings under the same rules and returns every date
from start through end inclusive in order; reversed bounds raise ValueError.
Handle leap years and boundaries including a same-day range on 9999-12-31 without
overflow. Keep existing format_date(date) behavior and existing tests. Use only
the Python standard library. Add regression tests and README usage; run tests
with `python3 -m unittest discover -s tests -t .`.

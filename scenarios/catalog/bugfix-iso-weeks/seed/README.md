# timesheet

Summarizes a time-tracking CSV export into total hours per week.

```sh
python3 -m timesheet report examples/march.csv
python3 -m timesheet report examples/march.csv --project ops
```

```text
2025-W10     7.50h
2025-W11     8.00h
total       15.50h
```

## Weeks

Weeks follow **ISO 8601**: they start on Monday, and week 1 of a year is the
week containing that year's first Thursday. Days near New Year can therefore
belong to a week of the previous or the next year, and some years have a week
53. Rows are labelled `YYYY-Www` using the ISO week-numbering year.

## Export format

A header row with `date` (`YYYY-MM-DD`), `hours` (0–24) and an optional
`project`. A malformed row stops the report with exit status 1 and a message
naming the CSV line.

## Development

Standard library only. Run the tests with:

```sh
python3 -m unittest discover -s tests -t .
```

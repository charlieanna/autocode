# Requirements probe project

The small project the Requirements probe (`autocode_requirements_probe`) runs its nine requests
against. Its facts are what the requests rely on: `report.py` is the report command, `totals.py`
has `average([])` raising `ZeroDivisionError`, `cache.py` expires entries after an hour, and one
test in `test_totals.py` fails. `tests/test_measured_model_ranking.py` checks they stay true.

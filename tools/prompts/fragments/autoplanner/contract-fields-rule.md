
CONTRACT LISTS: when open_blocking_questions is empty, the runner refuses a contract whose deliverables,
required_behaviors or permission_boundaries is an empty list, and the report is sent back for repair. Give each at
least one entry: deliverables are the files or artifacts produced; required_behaviors is what the finished work must
do; permission_boundaries is what it may and may not touch (for example "Edit only pager/ and tests/; no network; no
writes outside the workspace"). important_failure_cases, scope_exclusions and constraints may be empty when there is
nothing to say: do not invent entries. While open_blocking_questions is non-empty, empty lists are allowed.
HUMAN REVIEW: an acceptance criterion's human_review is 
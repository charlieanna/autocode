# Listings monorepo

Two independent Python packages, each with its own tests and its own owners:

- `packages/invoices/`: the paged invoice list.
- `packages/search/`: paged search results, owned by another team.

Each package keeps its own copy of the small `pager` module. Run a package's
tests from its own directory, for example
`cd packages/invoices && python3 -m unittest discover -v`.

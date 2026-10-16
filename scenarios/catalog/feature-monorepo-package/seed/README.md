# Greetings monorepo

Two independent Python packages, each with its own tests and its own owners:

- `packages/greet/`: the greeting command line.
- `packages/farewell/`: the farewell command line, owned by another team.

Run a package's tests from its own directory, for example
`cd packages/greet && python3 -m unittest discover -v`.

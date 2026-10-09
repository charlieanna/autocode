# stock.py

A warehouse stock ledger kept in `stock.json` in the current directory: `{location: {sku: quantity}}`,
each quantity a positive JSON integer.

## Commands

- `stock.py receive SKU QTY LOCATION` adds QTY units of SKU at LOCATION.
- `stock.py show` prints every holding as `LOCATION SKU QTY`, sorted, one per line.

## Exit codes

- `0`: the command succeeded.
- `2`: the command was refused: a usage error, or a rule of the command was broken
  (for example a quantity that is not a positive integer in ASCII digits, or a malformed `stock.json`).
  A refused command prints an explanation to stderr and leaves `stock.json` byte-for-byte unchanged.

Tests: `python3 -m unittest discover -s tests -t .`
